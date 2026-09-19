"""Contract version lineage and confirmed-cash allocation rules."""
from collections import defaultdict
from decimal import Decimal

from sqlalchemy import select

from domain_packs.mold import models as m
from domain_packs.mold.ports.errors import DomainError


ACTIVE_REPLACEMENT_STATUSES = {
    "DRAFT", "SUBMITTED", "RETURNED", "APPLY_BLOCKED", "EFFECTIVE",
}


def lineage_ids(db, predecessor):
    """Return only the version chain without folding additions into their base.

    ``ADDITION`` uses ``replaces_id`` as its commercial parent, but it remains an
    independently effective contract.  Following that edge as if it were a
    replacement would incorrectly move the base contract's cash history when an
    addition is later replaced.
    """
    result = []
    current = predecessor
    seen = set()
    while current and current.id not in seen:
        result.append(current.id)
        seen.add(current.id)
        detail = db.get(m.ContractDetail, current.id)
        current = (
            db.get(m.BusinessSubject, detail.replaces_id)
            if detail and detail.relation_type == "REPLACEMENT" and detail.replaces_id
            else None
        )
        if len(result) > 50:
            raise DomainError("CONTRACT_LINEAGE_INVALID", "合同版本链超过安全上限，请先核对历史关系", 409)
    if current:
        raise DomainError("CONTRACT_LINEAGE_CYCLE", "合同版本关系存在循环，不能继续", 409)
    return result


def settlement_records(db, predecessor):
    """Read every signed cash fact in the predecessor lineage exactly once."""
    contract_ids = lineage_ids(db, predecessor)
    result = []
    if predecessor.kind == "sales_contract":
        for row in db.scalars(
            select(m.CustomerReceiptConfirmation)
            .where(m.CustomerReceiptConfirmation.contract_subject_id.in_(contract_ids))
            .order_by(m.CustomerReceiptConfirmation.received_date, m.CustomerReceiptConfirmation.id)
        ):
            result.append({
                "record_type": "CUSTOMER_RECEIPT",
                "source_record_id": row.id,
                "source_contract_id": row.contract_subject_id,
                "amount": Decimal(row.amount),
                "currency": row.currency,
                "date": row.received_date.isoformat(),
                "reference": row.reference,
            })
    else:
        query = (
            select(m.PaymentConfirmation, m.PaymentStage.contract_id)
            .join(m.PaymentRequestDetail, m.PaymentRequestDetail.subject_id == m.PaymentConfirmation.request_id)
            .join(m.PaymentStage, m.PaymentStage.id == m.PaymentRequestDetail.stage_id)
            .where(m.PaymentStage.contract_id.in_(contract_ids))
            .order_by(m.PaymentConfirmation.paid_date, m.PaymentConfirmation.id)
        )
        for row, contract_id in db.execute(query):
            result.append({
                "record_type": "SUPPLIER_PAYMENT",
                "source_record_id": row.id,
                "source_contract_id": contract_id,
                "amount": Decimal(row.amount),
                "currency": row.currency,
                "date": row.paid_date.isoformat(),
                "reference": row.reference,
            })
    return result


def allocation_cards(db, contract_id):
    rows = db.scalars(
        select(m.ContractSettlementAllocation)
        .where(m.ContractSettlementAllocation.target_contract_id == contract_id)
        .order_by(m.ContractSettlementAllocation.created_at, m.ContractSettlementAllocation.id)
    )
    return [{
        "id": row.id,
        "target_contract_id": row.target_contract_id,
        "source_contract_id": row.source_contract_id,
        "target_stage_id": row.target_stage_id,
        "record_type": row.record_type,
        "source_record_id": row.source_record_id,
        "amount": str(row.amount),
        "currency": row.currency,
        "evidence": row.evidence,
    } for row in rows]


def _predecessor(db, subject, detail, *, lock=False):
    if detail.relation_type == "ORIGINAL":
        return None
    query = select(m.BusinessSubject).where(m.BusinessSubject.id == detail.replaces_id)
    predecessor = db.scalar(query.with_for_update() if lock else query)
    if (
        not predecessor
        or predecessor.id == subject.id
        or predecessor.project_id != subject.project_id
        or predecessor.kind != subject.kind
        or predecessor.status != "EFFECTIVE"
    ):
        raise DomainError(
            "CONTRACT_PREDECESSOR_INVALID",
            "前序合同不存在、类型不符或已不是当前有效合同，请重新查询",
            409,
        )
    old = db.get(m.ContractDetail, predecessor.id)
    if not old or old.currency != detail.currency:
        raise DomainError("CONTRACT_CURRENCY_MISMATCH", "新旧合同币种必须一致", 409)
    if subject.kind == "sales_contract" and old.customer_id != detail.customer_id:
        raise DomainError("CONTRACT_PARTY_MISMATCH", "销售合同替代或追加必须保持同一客户", 409)
    if subject.kind == "full_outsource_contract" and old.supplier_id != detail.supplier_id:
        raise DomainError("CONTRACT_PARTY_MISMATCH", "委外合同替代或追加必须保持同一供应商", 409)
    return predecessor


def validate_relation(db, subject, *, lock=False):
    """Revalidate lineage and every cash allocation before submit/apply."""
    detail = db.get(m.ContractDetail, subject.id)
    if not detail:
        raise DomainError("CONTRACT_DETAIL_MISSING", "合同明细不存在", 404)
    allocations = list(db.scalars(select(m.ContractSettlementAllocation).where(
        m.ContractSettlementAllocation.target_contract_id == subject.id
    )))
    if detail.relation_type == "ORIGINAL":
        if detail.replaces_id or detail.settlement_allocation_evidence or allocations:
            raise DomainError("CONTRACT_RELATION_INVALID", "原始合同不能包含替代、追加或历史收付款分配", 409)
        return {"relation_type": "ORIGINAL", "allocation_total": Decimal(0), "allocation_count": 0}

    predecessor = _predecessor(db, subject, detail, lock=lock)
    if detail.relation_type == "ADDITION":
        if allocations:
            raise DomainError("CONTRACT_ALLOCATION_INVALID", "追加合同不迁移历史收付款", 409)
        return {"relation_type": "ADDITION", "predecessor": predecessor, "allocation_total": Decimal(0), "allocation_count": 0}

    sibling = db.scalar(
        select(m.BusinessSubject)
        .join(m.ContractDetail, m.ContractDetail.subject_id == m.BusinessSubject.id)
        .where(
            m.ContractDetail.replaces_id == predecessor.id,
            m.ContractDetail.relation_type == "REPLACEMENT",
            m.BusinessSubject.id != subject.id,
            m.BusinessSubject.status.in_(ACTIVE_REPLACEMENT_STATUSES),
        )
        .limit(1)
    )
    if sibling:
        raise DomainError("CONTRACT_REPLACEMENT_EXISTS", "前序合同已有在途或生效替代版本，不能重复替代", 409)

    actual = settlement_records(db, predecessor)
    actual_by_key = {(row["record_type"], row["source_record_id"]): row for row in actual}
    allocated_by_key = {(row.record_type, row.source_record_id): row for row in allocations}
    if len(allocated_by_key) != len(allocations) or set(allocated_by_key) != set(actual_by_key):
        raise DomainError(
            "CONTRACT_SETTLEMENT_ALLOCATION_INCOMPLETE",
            "替代合同必须逐条分配前序版本链中的全部历史实收实付；不能遗漏或重复",
            409,
        )

    stage_totals = defaultdict(Decimal)
    allocation_total = Decimal(0)
    lineage = set(lineage_ids(db, predecessor))
    for key, allocation in allocated_by_key.items():
        source = actual_by_key[key]
        if (
            allocation.source_contract_id != source["source_contract_id"]
            or Decimal(allocation.amount) != source["amount"]
            or allocation.currency != source["currency"]
            or allocation.evidence != detail.settlement_allocation_evidence
        ):
            raise DomainError("CONTRACT_SETTLEMENT_CHANGED", "历史收付款金额、币种或分配依据已变化，请重新准备", 409)
        stage = db.get(m.PaymentStage, allocation.target_stage_id)
        if not stage or stage.contract_id != subject.id or stage.currency != allocation.currency:
            raise DomainError("CONTRACT_TARGET_STAGE_INVALID", "历史收付款目标节点不存在或币种不符", 409)
        stage_totals[stage.id] += Decimal(allocation.amount)
        allocation_total += Decimal(allocation.amount)

        conflict = db.scalar(
            select(m.ContractSettlementAllocation)
            .join(m.BusinessSubject, m.BusinessSubject.id == m.ContractSettlementAllocation.target_contract_id)
            .where(
                m.ContractSettlementAllocation.record_type == allocation.record_type,
                m.ContractSettlementAllocation.source_record_id == allocation.source_record_id,
                m.ContractSettlementAllocation.target_contract_id != subject.id,
                m.BusinessSubject.status.in_(ACTIVE_REPLACEMENT_STATUSES),
                m.ContractSettlementAllocation.target_contract_id.not_in(lineage),
            )
            .limit(1)
        )
        if conflict:
            raise DomainError("CONTRACT_SETTLEMENT_ALREADY_ALLOCATED", "同一历史收付款已分配给其他在途替代合同", 409)

    for stage_id, amount in stage_totals.items():
        stage = db.get(m.PaymentStage, stage_id)
        if amount < 0 or amount > Decimal(stage.amount):
            raise DomainError("CONTRACT_STAGE_ALLOCATION_OVERFLOW", "历史收付款分配后节点金额小于零或超过节点金额", 409)
    if allocation_total < 0 or allocation_total > Decimal(detail.amount):
        raise DomainError("CONTRACT_ALLOCATION_OVERFLOW", "历史收付款净额小于零或超过替代合同金额", 409)
    return {
        "relation_type": "REPLACEMENT",
        "predecessor": predecessor,
        "allocation_total": allocation_total,
        "allocation_count": len(allocations),
    }


def apply_relation(db, subject):
    context = validate_relation(db, subject, lock=True)
    if context["relation_type"] == "REPLACEMENT":
        context["predecessor"].status = "CLOSED"
    return context
