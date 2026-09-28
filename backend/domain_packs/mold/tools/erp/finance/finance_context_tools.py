from collections import defaultdict
from datetime import date, timedelta
from decimal import Decimal, InvalidOperation
from typing import Literal

from fastapi import APIRouter, Depends
from pydantic import Field, ValidationError, model_validator
from sqlalchemy import select

from domain_packs.mold import models as m
from domain_packs.mold.authorization import access, fingerprint, predicate, require, select_fields
from domain_packs.mold.ports.bpm import content_hash
from domain_packs.mold.ports.confirmation_policy import proposal_confirmation_policy
from domain_packs.mold.ports.db import get_db, now
from domain_packs.mold.erp.core.domain_commands import CustomerReceipt, Payment, validate_customer_receipt, validate_supplier_payment
from domain_packs.mold.ports.errors import DomainError
from domain_packs.mold.ports.events import record
from domain_packs.mold.ports.files import uploaded_file
from domain_packs.mold.erp.project.project_dossier import ProjectDossierInput
from domain_packs.mold.ports.schemas import StrictModel
from domain_packs.mold.ports.security import current_user


FINANCE_KINDS = {"sales_contract", "full_outsource_contract", "supplier_payment", "finance_correction", "project_close", "internal_start"}
FINANCE_PROPOSAL_TOOLS = {
    "prepare_finance_correction", "prepare_supplier_payment_condition", "prepare_supplier_payment_request",
    "prepare_customer_receivable_schedule",
    "prepare_customer_receipt_confirmation",
    "prepare_supplier_payment_confirmation",
    "prepare_supplier_deduction_settlement",
    "prepare_mold_transfer_receipt",
}


class CustomerReceivableScheduleProposalInput(StrictModel):
    project_id: str = Field(min_length=1, max_length=36)
    project_version: int = Field(ge=1)
    contract_subject_id: str = Field(min_length=1, max_length=36)
    stage_id: str = Field(min_length=1, max_length=36)
    ratio_percent: Decimal | None = Field(default=None, gt=0, le=100, max_digits=7, decimal_places=4)
    trigger_event: str = Field(min_length=1, max_length=120)
    trigger_date: date | None = None
    credit_days: int | None = Field(default=None, ge=0, le=3650)
    expected_due_date: date | None = None
    schedule_evidence: str = Field(min_length=1, max_length=4000)
    trigger_evidence: str | None = Field(default=None, min_length=1, max_length=4000)
    special_mark: str | None = Field(default=None, min_length=1, max_length=200)

    @model_validator(mode="after")
    def validate_schedule(self):
        if self.credit_days is None and self.expected_due_date is None:
            raise ValueError("账期天数和预计到期日期至少填写一项")
        if self.trigger_date and not self.trigger_evidence:
            raise ValueError("填写触发日期时必须同时填写触发依据")
        if self.trigger_date and self.expected_due_date and self.expected_due_date < self.trigger_date:
            raise ValueError("预计到期日期不能早于触发日期")
        if self.trigger_date and self.credit_days is not None:
            derived = self.trigger_date + timedelta(days=self.credit_days)
            if self.expected_due_date is not None and self.expected_due_date != derived:
                raise ValueError("预计到期日期必须与触发日期加账期天数一致")
            self.expected_due_date = derived
        return self


class CustomerReceiptProposalInput(StrictModel):
    project_id: str = Field(min_length=1, max_length=36)
    project_version: int = Field(ge=1)
    contract_subject_id: str = Field(min_length=1, max_length=36)
    stage_id: str | None = Field(default=None, max_length=36)
    amount: Decimal = Field(gt=0, max_digits=18, decimal_places=2)
    currency: str = Field(pattern=r"^[A-Z]{3}$")
    received_date: date
    reference: str = Field(min_length=1, max_length=100)
    evidence: str = Field(min_length=1, max_length=4000)
    source_ref: str | None = Field(default=None, max_length=120)
    note: str | None = Field(default=None, max_length=4000)


class SupplierPaymentConfirmationProposalInput(StrictModel):
    project_id: str = Field(min_length=1, max_length=36)
    project_version: int = Field(ge=1)
    payment_subject_id: str = Field(min_length=1, max_length=36)
    amount: Decimal = Field(gt=0, max_digits=18, decimal_places=2)
    currency: str = Field(pattern=r"^[A-Z]{3}$")
    paid_date: date
    reference: str = Field(min_length=1, max_length=100)
    evidence: str = Field(min_length=1, max_length=4000)


class SupplierDeductionSettlementProposalInput(StrictModel):
    project_id: str = Field(min_length=1, max_length=36)
    project_version: int = Field(ge=1)
    supplier_id: str = Field(min_length=1, max_length=36)
    previous_deduction_id: str | None = Field(default=None, max_length=36,
        description='已有责任确认记录后登记结算时，明确引用该记录 ID；不得覆盖原记录或改用无关联的新来源。')
    customer_acceptance_id: str | None = Field(default=None, max_length=36,
        description='明确关联同项目的客户验收原记录；仅表示问题来源，不代表客户与供应商扣款金额相等或已对冲。')
    contract_subject_id: str | None = Field(default=None, max_length=36)
    contact_case_id: str | None = Field(default=None, max_length=36)
    contact_task_id: str | None = Field(default=None, max_length=36)
    reason: str = Field(min_length=1, max_length=4000)
    responsibility: Literal["CUSTOMER", "SUPPLIER", "INTERNAL", "SHARED"]
    deduction_amount: Decimal = Field(gt=0, max_digits=18, decimal_places=2)
    currency: str = Field(pattern=r"^[A-Z]{3}$")
    status: Literal["RESPONSIBILITY_CONFIRMED", "SETTLED"]
    responsibility_evidence: str = Field(min_length=1, max_length=4000)
    settlement_reference: str | None = Field(default=None, max_length=120)
    settlement_evidence: str | None = Field(default=None, max_length=4000)
    source_ref: str | None = Field(default=None, max_length=120,
        description='首次责任/结算登记的来源引用；引用 previous_deduction_id 后不再填写，以前次记录保留原来源。')


class MoldTransferReceiptProposalInput(StrictModel):
    project_id: str = Field(min_length=1, max_length=36)
    project_version: int = Field(ge=1)
    signed_date: date = Field(description="客户签收模具的日期，也是财务人工维护的移模时间。")
    shipment_reference: str = Field(min_length=1, max_length=120)
    signer_name: str = Field(min_length=1, max_length=120)
    evidence: str = Field(min_length=1, max_length=4000)
    logistics_route_id: str | None = Field(default=None, max_length=36)
    file_ids: list[str] = Field(
        default_factory=list,
        max_length=20,
        description='当前任务中已上传的客户签收/移模原件文件 ID。',
    )


def _strength(value, needle):
    if value is None:
        return 0
    value = str(value).casefold()
    needle = str(needle).casefold()
    return 100 if value == needle else 50 if needle in value else 0


def _project_card(db, user, project, matched_by=()):
    fields = access(db, user, "project.read", {"project_id": project.id}).fields
    card = select_fields({"id": project.id, "code": project.code, "name": project.name, "status": project.status}, fields)
    card["matched_by"] = sorted(set(matched_by))
    return card


def _can_read_kind(db, user, kind, project_id, category=None):
    return access(db, user, f"{kind}.read", {"project_id": project_id, "category": category}).allowed


def _subject_rows(db, user, project_id, kind, limit=50):
    from domain_packs.mold.erp.core.domains import data as subject_data

    rows = []
    for subject in db.scalars(
        select(m.BusinessSubject)
        .where(m.BusinessSubject.project_id == project_id, m.BusinessSubject.kind == kind)
        .order_by(m.BusinessSubject.created_at.desc(), m.BusinessSubject.id)
        .limit(limit)
    ):
        try:
            rows.append(subject_data(db, user, subject))
        except DomainError:
            continue
    return rows


def _visible_projects(db, user):
    gate = predicate(db, user, "project.read", {"project_id": m.Project.id})
    dossier_gate = predicate(db, user, "project.dossier.read", {"project_id": m.Project.id})
    rows = list(db.scalars(select(m.Project).where(gate, dossier_gate).order_by(m.Project.code).limit(501)))
    return rows[:500], len(rows) > 500


def _resolve(db, user, data: ProjectDossierInput):
    visible, truncated = _visible_projects(db, user)
    by_id = {project.id: project for project in visible}
    if data.project_id:
        project = by_id.get(data.project_id)
        return project, ([] if project else None), truncated

    scores = defaultdict(int)
    reasons = defaultdict(list)

    def add(project_id, value, label):
        if project_id not in by_id:
            return
        score = _strength(value, data.identifier)
        if score:
            scores[project_id] = max(scores[project_id], score)
            reasons[project_id].append(label)

    for project in visible:
        add(project.id, project.id, "项目ID")
        add(project.id, project.code, "项目编号")
        add(project.id, project.name, "项目名称")
    if by_id:
        for link, mold in db.execute(select(m.ProjectMold, m.Mold).join(m.Mold, m.Mold.id == m.ProjectMold.mold_id).where(m.ProjectMold.project_id.in_(list(by_id))).limit(501)):
            add(link.project_id, mold.internal_number, "模具号")
            add(link.project_id, mold.name, "模具名称")
        for case in db.scalars(select(m.ContactCase).where(m.ContactCase.project_id.in_(list(by_id))).limit(501)):
            add(case.project_id, case.title, "工程联络标题")
            add(case.project_id, case.customer_ref, "客户引用")
            add(case.project_id, case.mold_number, "联络模具号")
            add(case.project_id, case.product_ref, "产品/料品号")
        for subject in db.scalars(select(m.BusinessSubject).where(m.BusinessSubject.project_id.in_(list(by_id)), m.BusinessSubject.kind.in_(FINANCE_KINDS)).limit(501)):
            add(subject.project_id, subject.number, "财务相关业务单号")
            if subject.kind in {"sales_contract", "full_outsource_contract"} and _can_read_kind(db, user, subject.kind, subject.project_id, subject.category):
                detail = db.get(m.ContractDetail, subject.id)
                if detail:
                    add(subject.project_id, detail.contract_number, "合同编号")
    if not scores:
        return None, [], truncated
    best = max(scores.values())
    ids = [project_id for project_id, score in scores.items() if score == best]
    if len(ids) != 1:
        return None, [_project_card(db, user, by_id[project_id], reasons[project_id]) for project_id in ids[:20]], truncated
    return by_id[ids[0]], reasons[ids[0]], truncated


def _profile(db, user, project_id):
    profile = db.get(m.ProjectProfile, project_id)
    if not profile:
        return None
    fields = access(db, user, "project.dossier.read", {"project_id": project_id}).fields
    return select_fields(
        {
            "customer_id": profile.customer_id,
            "owner_user_id": profile.owner_user_id,
            "execution_mode": profile.execution_mode,
            "customer_due_date": profile.customer_due_date.isoformat() if profile.customer_due_date else None,
            "settlement_status": profile.settlement_status,
        },
        fields | {"customer_id", "owner_user_id", "execution_mode", "customer_due_date", "settlement_status"},
    )


def _as_decimal(value):
    if value is None:
        return None
    try:
        return Decimal(str(value))
    except InvalidOperation:
        return None


def _money_total(items, amount_key="amount", currency_key="currency"):
    totals = defaultdict(Decimal)
    for item in items:
        amount = _as_decimal(item.get(amount_key))
        currency = item.get(currency_key)
        if amount is not None and currency:
            totals[currency] += amount
    return [{"currency": currency, "amount": str(amount)} for currency, amount in sorted(totals.items())]


def _contract_context(rows, role):
    result = []
    payment_nodes = []
    for row in rows:
        detail = row.get("detail") if isinstance(row.get("detail"), dict) else {}
        stages = []
        for stage in detail.get("stages") or []:
            node = {
                "contract_id": row.get("id"),
                "contract_number": detail.get("contract_number"),
                "contract_role": role,
                "stage_id": stage.get("id"),
                "name": stage.get("name"),
                "amount": stage.get("amount"),
                "currency": stage.get("currency") or detail.get("currency"),
                "condition": stage.get("condition"),
                "condition_confirmed": stage.get("condition_confirmed"),
                "condition_evidence_present": bool(stage.get("condition_evidence")),
                "ratio_percent": stage.get("ratio_percent"),
                "trigger_event": stage.get("trigger_event"),
                "trigger_date": stage.get("trigger_date"),
                "credit_days": stage.get("credit_days"),
                "expected_due_date": stage.get("expected_due_date"),
                "schedule_confirmed": bool(stage.get("schedule_confirmed")),
                "schedule_evidence_present": bool(stage.get("schedule_evidence")),
                "trigger_evidence_present": bool(stage.get("trigger_evidence")),
                "special_mark": stage.get("special_mark"),
                "special_approval_reference": stage.get("special_approval_reference"),
                "status": (
                    "SCHEDULE_CONFIRMED"
                    if role == "CUSTOMER_RECEIVABLE" and stage.get("schedule_confirmed")
                    else "CONDITION_CONFIRMED" if stage.get("condition_confirmed") else "CONDITION_PENDING"
                ),
            }
            stages.append(node)
            if row.get("status") == "EFFECTIVE":
                payment_nodes.append(node)
        result.append(
            {
                "id": row.get("id"),
                "number": row.get("number"),
                "status": row.get("status"),
                "role": role,
                "contract_number": detail.get("contract_number"),
                "amount": detail.get("amount"),
                "currency": detail.get("currency"),
                "expected_date": detail.get("expected_date"),
                "received_date": detail.get("received_date"),
                "business_terms": detail.get("business_terms"),
                "replaces_id": detail.get("replaces_id"),
                "relation_type": detail.get("relation_type") or ("REPLACEMENT" if detail.get("replaces_id") else "ORIGINAL"),
                "settlement_allocation_evidence_present": bool(detail.get("settlement_allocation_evidence")),
                "settlement_allocations": detail.get("settlement_allocations") or [],
                "stage_count": len(stages),
                "stages": stages,
            }
        )
    return result, payment_nodes


def _supplier_payments(rows, sales_contracts, outsource_contracts):
    stage_contract = {}
    for contract in sales_contracts + outsource_contracts:
        for stage in contract.get("stages") or []:
            stage_contract[stage.get("stage_id")] = {
                "contract_id": contract.get("id"),
                "contract_number": contract.get("contract_number"),
                "contract_role": contract.get("role"),
                "contract_status": contract.get("status"),
                "stage_name": stage.get("name"),
                "stage_condition": stage.get("condition"),
            }
    requests = []
    all_confirmations = []
    outstanding_reservations = []
    for row in rows:
        detail = row.get("detail") if isinstance(row.get("detail"), dict) else {}
        payments = detail.get("payments") or []
        stage = stage_contract.get(detail.get("stage_id"), {})
        confirmations = [
            {
                "id": payment.get("id"),
                "amount": payment.get("amount"),
                "currency": payment.get("currency") or detail.get("currency"),
                "paid_date": payment.get("paid_date"),
                "reference": payment.get("reference"),
                "evidence_present": bool(payment.get("evidence")),
                "is_reversal": _as_decimal(payment.get("amount")) is not None and _as_decimal(payment.get("amount")) < 0,
                "reversal_of_id": payment.get("reversal_of_id"),
            }
            for payment in payments
        ]
        all_confirmations.extend(confirmations)
        reservation = _as_decimal(detail.get("reservation")) or Decimal(0)
        if reservation:
            outstanding_reservations.append({"id": row.get("id"), "amount": str(reservation), "currency": detail.get("currency")})
        requests.append(
            {
                "id": row.get("id"),
                "number": row.get("number"),
                "status": row.get("status"),
                "category": row.get("category"),
                "stage_id": detail.get("stage_id"),
                **stage,
                "revision": row.get("revision"),
                "created_by": row.get("created_by"),
                "requested_amount": detail.get("amount"),
                "currency": detail.get("currency"),
                "reservation": detail.get("reservation"),
                "approval_effect": "APPROVED_FOR_PAYMENT" if row.get("status") == "EFFECTIVE" else "NOT_APPROVED_OR_NOT_EFFECTIVE",
                "payment_execution_eligible": (row.get("status") == "EFFECTIVE" and stage["contract_status"] == "EFFECTIVE")
                    if stage.get("contract_status") is not None else None,
                "payment_confirmations": confirmations,
                "confirmed_total": _money_total(confirmations),
            }
        )
    return {
        "requests": requests,
        "confirmed_totals": _money_total(all_confirmations),
        "outstanding_reservations": outstanding_reservations,
        "outstanding_reservation_totals": _money_total(outstanding_reservations),
    }


def _customer_receipts(db, user, project_id, sales_contracts, customer_nodes):
    if not access(db, user, "customer_receipt.read", {"project_id": project_id}).allowed:
        return {"receipts": [], "confirmed_totals": [], "by_stage": [], "unallocated_receipts": []}, True

    visible_contract_ids = {contract.get("id") for contract in sales_contracts if contract.get("id")}
    all_nodes = [node for contract in sales_contracts for node in contract.get("stages") or []]
    stage_map = {
        node.get("stage_id"): {
            "contract_id": node.get("contract_id"),
            "contract_number": node.get("contract_number"),
            "stage_id": node.get("stage_id"),
            "stage_name": node.get("name"),
            "stage_condition": node.get("condition"),
            "stage_amount": node.get("amount"),
            "currency": node.get("currency"),
        }
        for node in all_nodes
        if node.get("stage_id")
    }
    receipts = []
    for receipt in db.scalars(
        select(m.CustomerReceiptConfirmation)
        .where(m.CustomerReceiptConfirmation.project_id == project_id)
        .order_by(m.CustomerReceiptConfirmation.received_date.desc(), m.CustomerReceiptConfirmation.created_at.desc(), m.CustomerReceiptConfirmation.id)
        .limit(100)
    ):
        if receipt.contract_subject_id not in visible_contract_ids:
            continue
        stage = stage_map.get(receipt.stage_id, {})
        contract = next((row for row in sales_contracts if row.get("id") == receipt.contract_subject_id), {})
        receipts.append(
            {
                "id": receipt.id,
                "contract_id": receipt.contract_subject_id,
                "contract_number": stage.get("contract_number") or contract.get("contract_number"),
                "stage_id": receipt.stage_id,
                "stage_name": stage.get("stage_name"),
                "amount": str(receipt.amount),
                "currency": receipt.currency,
                "received_date": receipt.received_date.isoformat(),
                "reference": receipt.reference,
                "evidence_present": bool(receipt.evidence),
                "confirmed_by": receipt.confirmed_by,
                "source_system": receipt.source_system,
                "source_ref": receipt.source_ref,
            }
        )

    by_stage = []
    for stage_id, stage in stage_map.items():
        stage_receipts = [row for row in receipts if row.get("stage_id") == stage_id]
        if stage_receipts:
            by_stage.append({**stage, "confirmed_totals": _money_total(stage_receipts), "receipt_count": len(stage_receipts)})
    unallocated = [row for row in receipts if not row.get("stage_id")]
    allocated_by_stage = []
    for contract in sales_contracts:
        if contract.get("status") != "EFFECTIVE":
            continue
        for allocation in contract.get("settlement_allocations") or []:
            if allocation.get("record_type") != "CUSTOMER_RECEIPT":
                continue
            allocated_by_stage.append({
                "target_contract_id": contract.get("id"),
                "target_stage_id": allocation.get("target_stage_id"),
                "source_contract_id": allocation.get("source_contract_id"),
                "source_record_id": allocation.get("source_record_id"),
                "amount": allocation.get("amount"),
                "currency": allocation.get("currency"),
            })
    return {
        "receipts": receipts,
        "confirmed_totals": _money_total(receipts),
        "by_stage": by_stage,
        "unallocated_receipts": unallocated,
        "allocated_to_current_stages": allocated_by_stage,
    }, False


def _receivable_schedule(customer_nodes, customer_receipts, as_of_date):
    """Project customer receivable state from structured terms and confirmed cash facts.

    No date or trigger is inferred from free-form condition text. Missing structured
    evidence remains an explicit state instead of being guessed by the Agent.
    """
    receipts = customer_receipts.get("receipts") or []
    allocations = customer_receipts.get("allocated_to_current_stages") or []
    rows = []
    counts = defaultdict(int)
    reminders = []
    for node in customer_nodes:
        stage_id = node.get("stage_id")
        currency = node.get("currency")
        stage_amount = _as_decimal(node.get("amount")) or Decimal(0)
        stage_receipts = [receipt for receipt in receipts if receipt.get("stage_id") == stage_id]
        matching_receipts = [receipt for receipt in stage_receipts if receipt.get("currency") == currency]
        stage_allocations = [allocation for allocation in allocations
            if allocation.get("target_stage_id") == stage_id and allocation.get("currency") == currency]
        direct_amount = sum((_as_decimal(receipt.get("amount")) or Decimal(0) for receipt in matching_receipts), Decimal(0))
        allocated_amount = sum((_as_decimal(item.get("amount")) or Decimal(0) for item in stage_allocations), Decimal(0))
        received_amount = direct_amount + allocated_amount
        outstanding_amount = max(stage_amount - received_amount, Decimal(0))
        currency_mismatches = sorted({str(receipt.get("currency")) for receipt in stage_receipts if receipt.get("currency") != currency})
        trigger_date = date.fromisoformat(node["trigger_date"]) if node.get("trigger_date") else None
        due_date = date.fromisoformat(node["expected_due_date"]) if node.get("expected_due_date") else None
        if due_date is None and trigger_date is not None and node.get("credit_days") is not None:
            due_date = trigger_date + timedelta(days=int(node["credit_days"]))

        partial = received_amount > 0 and received_amount < stage_amount
        if stage_amount > 0 and received_amount >= stage_amount:
            due_state = "RECEIVED"
        elif not node.get("schedule_confirmed"):
            due_state = "PARTIALLY_RECEIVED_SCHEDULE_PENDING" if partial else "SCHEDULE_PENDING"
        elif trigger_date is None:
            due_state = "PARTIALLY_RECEIVED_UNTRIGGERED" if partial else "UNTRIGGERED"
        elif due_date is None:
            due_state = "PARTIALLY_RECEIVED_DUE_DATE_UNKNOWN" if partial else "DUE_DATE_UNKNOWN"
        elif as_of_date < due_date:
            due_state = "PARTIALLY_RECEIVED_NOT_DUE" if partial else "NOT_DUE"
        elif as_of_date == due_date:
            due_state = "PARTIALLY_RECEIVED_DUE" if partial else "DUE_UNPAID"
        else:
            due_state = "PARTIALLY_RECEIVED_OVERDUE" if partial else "OVERDUE_UNPAID"

        days_until_due = (due_date - as_of_date).days if due_date and due_date >= as_of_date else None
        days_overdue = (as_of_date - due_date).days if due_date and due_date < as_of_date else None
        reminder = None
        if due_state in {"DUE_UNPAID", "PARTIALLY_RECEIVED_DUE"}:
            reminder = "DUE_TODAY"
        elif due_state in {"OVERDUE_UNPAID", "PARTIALLY_RECEIVED_OVERDUE"}:
            reminder = "OVERDUE"
        row = {
            **node,
            "effective_due_date": due_date.isoformat() if due_date else None,
            "due_state": due_state,
            "due_state_as_of": as_of_date.isoformat(),
            "confirmed_received_amount": str(received_amount),
            "outstanding_amount": str(outstanding_amount),
            "receipt_count": len(matching_receipts),
            "allocated_history_count": len(stage_allocations),
            "allocated_history_amount": str(allocated_amount),
            "currency_mismatches": currency_mismatches,
            "days_until_due": days_until_due,
            "days_overdue": days_overdue,
            "reminder": reminder,
        }
        rows.append(row)
        counts[due_state] += 1
        if reminder:
            reminders.append({
                "stage_id": stage_id,
                "contract_number": node.get("contract_number"),
                "stage_name": node.get("name"),
                "currency": currency,
                "outstanding_amount": str(outstanding_amount),
                "effective_due_date": row["effective_due_date"],
                "due_state": due_state,
                "days_overdue": days_overdue,
                "special_mark": node.get("special_mark"),
            })
    return {
        "as_of_date": as_of_date.isoformat(),
        "nodes": rows,
        "state_counts": dict(sorted(counts.items())),
        "reminders": reminders,
        "reminder_count": len(reminders),
    }


def _current_contract_balances(sales_contracts, customer_receipts, outsource_contracts, supplier_payments):
    receipt_rows = customer_receipts.get("receipts") or []
    payment_requests = supplier_payments.get("requests") or []

    def rows_for(contracts, role):
        result = []
        for contract in contracts:
            if contract.get("status") != "EFFECTIVE":
                continue
            amount = _as_decimal(contract.get("amount")) or Decimal(0)
            currency = contract.get("currency")
            if role == "CUSTOMER_RECEIVABLE":
                direct = sum((_as_decimal(row.get("amount")) or Decimal(0) for row in receipt_rows
                    if row.get("contract_id") == contract.get("id") and row.get("currency") == currency), Decimal(0))
                allocation_type = "CUSTOMER_RECEIPT"
            else:
                direct = sum((_as_decimal(payment.get("amount")) or Decimal(0)
                    for request in payment_requests if request.get("contract_id") == contract.get("id")
                    for payment in request.get("payment_confirmations") or [] if payment.get("currency") == currency), Decimal(0))
                allocation_type = "SUPPLIER_PAYMENT"
            allocated = sum((_as_decimal(row.get("amount")) or Decimal(0)
                for row in contract.get("settlement_allocations") or []
                if row.get("record_type") == allocation_type and row.get("currency") == currency), Decimal(0))
            settled = direct + allocated
            result.append({
                "contract_id": contract.get("id"),
                "contract_number": contract.get("contract_number"),
                "relation_type": contract.get("relation_type"),
                "currency": currency,
                "effective_contract_amount": str(amount),
                "direct_confirmed_amount": str(direct),
                "allocated_historical_amount": str(allocated),
                "confirmed_settlement_amount": str(settled),
                "outstanding_amount": str(max(amount-settled, Decimal(0))),
                "settlement_overflow": settled > amount,
            })
        return result

    sales = rows_for(sales_contracts, "CUSTOMER_RECEIVABLE")
    supplier = rows_for(outsource_contracts, "SUPPLIER_PAYABLE")
    return {
        "customer_receivable": sales,
        "supplier_payable": supplier,
        "customer_receivable_totals": _money_total(sales, "outstanding_amount"),
        "supplier_payable_totals": _money_total(supplier, "outstanding_amount"),
    }


def _corrections(rows):
    result = []
    for row in rows:
        detail = row.get("detail") if isinstance(row.get("detail"), dict) else {}
        result.append(
            {
                "id": row.get("id"),
                "number": row.get("number"),
                "status": row.get("status"),
                "original_payment_id": detail.get("original_payment_id"),
                "revision": row.get("revision"),
                "created_by": row.get("created_by"),
                "reversal_id": detail.get("reversal_id"),
                "reason": detail.get("reason"),
                "reversal_date": detail.get("reversal_date"),
                "reversal_evidence_present": bool(detail.get("reversal_evidence")),
                "original_payment": detail.get("original_payment"),
                "reversal_amount": detail.get("reversal_amount"),
                "allocation_effects": detail.get("allocation_effects") or [],
            }
        )
    return result


def _mold_transfer_receipts(db, project_id):
    result = []
    for row in db.scalars(
        select(m.CustomerDeliverySignature)
        .where(
            m.CustomerDeliverySignature.project_id == project_id,
            m.CustomerDeliverySignature.move_type == "MOLD_TRANSFER",
        )
        .order_by(
            m.CustomerDeliverySignature.signed_date.desc(),
            m.CustomerDeliverySignature.created_at.desc(),
            m.CustomerDeliverySignature.id,
        )
        .limit(100)
    ):
        attachments = [
            {
                "id": attachment.id,
                "file_id": attachment.file_id,
                "role": attachment.role,
                "version": attachment.version,
                "title": attachment.title,
                "content_sha256": attachment.content_sha256,
                "filename": blob.filename,
                "media_type": blob.media_type,
                "size": blob.size,
            }
            for attachment, blob in db.execute(
                select(m.CustomerDeliverySignatureAttachment, m.FileObject)
                .join(m.FileObject, m.FileObject.id == m.CustomerDeliverySignatureAttachment.file_id)
                .where(m.CustomerDeliverySignatureAttachment.signature_id == row.id)
                .order_by(
                    m.CustomerDeliverySignatureAttachment.version,
                    m.CustomerDeliverySignatureAttachment.id,
                )
            )
        ]
        result.append({
            "id": row.id,
            "shipment_reference": row.shipment_reference,
            "signed_date": row.signed_date.isoformat(),
            "move_time": row.signed_date.isoformat(),
            "signer_name": row.signer_name,
            "sign_status": row.sign_status,
            "move_type": row.move_type,
            "logistics_route_id": row.logistics_route_id,
            "evidence_present": bool(row.evidence),
            "recorded_by": row.recorded_by,
            "quality_acceptance_effect": "NONE",
            "attachments": attachments,
        })
    return result


def _cost_impacts(db, user, project_id, allowed_tools):
    contacts = []
    if "query_contact_cases" in allowed_tools:
        from domain_packs.mold.erp.change.contacts import permitted

        for case in db.scalars(select(m.ContactCase).where(m.ContactCase.project_id == project_id).order_by(m.ContactCase.created_at.desc(), m.ContactCase.id).limit(100)):
            if not permitted(db, user, "read", case):
                continue
            tasks = []
            for task in db.scalars(select(m.ContactTask).where(m.ContactTask.case_id == case.id).limit(100)):
                if task.estimated_amount is None and task.actual_amount is None and task.actual_hours is None:
                    continue
                tasks.append(
                    {
                        "id": task.id,
                        "title": task.title,
                        "status": task.status,
                        "affected_type": task.affected_type,
                        "affected_ref": task.affected_ref,
                        "planned_action": task.planned_action,
                        "estimated_amount": str(task.estimated_amount) if task.estimated_amount is not None else None,
                        "currency": task.currency,
                        "actual_hours": str(task.actual_hours) if task.actual_hours is not None else None,
                        "actual_amount": str(task.actual_amount) if task.actual_amount is not None else None,
                        "actual_currency": task.actual_currency,
                        "execution_evidence_present": bool(task.execution_evidence),
                    }
                )
            if tasks:
                contacts.append({"id": case.id, "title": case.title, "status": "CLOSED" if case.closed_at else "OPEN", "tasks": tasks})
    return contacts[:50]


def _closure_finance_items(db, user, project_id):
    if not access(db, user, "project_close.read", {"project_id": project_id}).allowed:
        return []
    keys = {"INVOICE", "CUSTOMER_RECEIPT", "SUPPLIER_SETTLEMENT", "ARCHIVE_FINANCE", "RECEIVABLE_PAYABLE", "CUSTOMER_SETTLEMENT", "SUPPLIER_SETTLEMENT"}
    rows = []
    for case in db.scalars(select(m.ProjectClosureCase).where(m.ProjectClosureCase.project_id == project_id).order_by(m.ProjectClosureCase.created_at.desc(), m.ProjectClosureCase.id).limit(20)):
        for item in db.scalars(select(m.ProjectClosureItem).where(m.ProjectClosureItem.case_id == case.id).limit(100)):
            if item.item_key in keys or any(word in ((item.label or "") + (item.result or "")) for word in ("财务", "回款", "付款", "发票", "结算")):
                rows.append(
                    {
                        "case_id": case.id,
                        "case_mode": case.mode,
                        "case_status": case.status,
                        "item_key": item.item_key,
                        "label": item.label,
                        "status": item.status,
                        "result": item.result,
                        "evidence_present": bool(item.evidence),
                        "source_system": item.source_system,
                        "source_ref": item.source_ref,
                        "source_as_of": item.source_as_of.isoformat() if item.source_as_of else None,
                    }
                )
    return rows


def _quality_finance_context(db, user, project_id, allowed_tools):
    # Reuse the source readers and their capability/data permissions. These
    # facts remain separate: no inferred mapping or settlement arithmetic.
    from domain_packs.mold.erp.procurement.delivery_logistics import _customer_delivery_acceptance
    from domain_packs.mold.tools.erp.procurement.full_outsource_tools import (
        _deduction_settlements, can_read_deduction_settlements,
    )
    delivery = _customer_delivery_acceptance(db, user, project_id, allowed_tools)
    acceptance_visible = delivery['visibility']['acceptance_records_visible']
    deduction_visible = can_read_deduction_settlements(db, user, project_id, allowed_tools)
    deductions = _deduction_settlements(db, user, project_id, allowed_tools)
    return {
        'customer_acceptance': {
            'visible': acceptance_visible,
            'records': delivery['acceptance_records'],
            'record_count': delivery['acceptance_record_count'],
            'records_truncated': delivery['acceptance_records_truncated'],
            'derived_status': delivery['derived_status'] if acceptance_visible else None,
        },
        'supplier_deductions': {
            'visible': deduction_visible, 'records': deductions,
            'possibly_truncated': len(deductions) == 100 if deduction_visible else None,
        },
        'accounting_effect': 'NONE',
        'relationship': 'EXPLICIT_REFERENCES_ONLY',
    }


def _erp_finance_flags(erp_finance_execution, sales_contracts, outsource_contracts):
    records = (erp_finance_execution or {}).get("records") or {}
    payment_plans = records.get("payment_plan_records") or []
    payment_records = records.get("payment_records") or []
    sales_numbers = {
        str(row.get("contract_number") or "").strip()
        for row in sales_contracts
        if str(row.get("contract_number") or "").strip()
    }
    outsource_numbers = {
        str(row.get("contract_number") or "").strip()
        for row in outsource_contracts
        if str(row.get("contract_number") or "").strip()
    }

    def contract_number(row):
        return str(row.get("contract_no") or row.get("contractNo") or "").strip()

    sales_plan = [row for row in payment_plans if contract_number(row) in sales_numbers]
    outsource_plan = [row for row in payment_plans if contract_number(row) in outsource_numbers]
    sales_records = [row for row in payment_records if contract_number(row) in sales_numbers]
    outsource_records = [row for row in payment_records if contract_number(row) in outsource_numbers]

    issued_invoice_states = {
        "issued", "invoiced", "completed", "complete", "done", "success",
        "已开票", "已开具", "开票完成", "已完成",
    }
    unresolved_invoice_states = {
        "pending", "unissued", "not_issued", "未开票", "待开票", "未开具",
        "待开具", "processing", "处理中",
    }

    def invoice_state(row):
        invoice_no = str(row.get("invoice_no") or row.get("invoiceNo") or "").strip()
        raw_status = str(row.get("invoice_status") or row.get("invoiceStatus") or "").strip()
        status = raw_status.lower()
        # An invoice number is direct ERP evidence that an invoice reference
        # exists. Unknown status values stay unresolved rather than being
        # guessed into an issued/pending state.
        if invoice_no or status in issued_invoice_states:
            return "ISSUED"
        if status in unresolved_invoice_states:
            return "PENDING"
        if raw_status:
            return "UNRESOLVED"
        return None

    def invoice_summary(rows):
        states = [invoice_state(row) for row in rows]
        visible = [state for state in states if state]
        return {
            "record_count": len(visible),
            "issued_count": sum(state == "ISSUED" for state in visible),
            "pending_count": sum(state == "PENDING" for state in visible),
            "unresolved_count": sum(state == "UNRESOLVED" for state in visible),
        }

    customer_invoice = invoice_summary(sales_records)
    supplier_invoice = invoice_summary(outsource_records)
    return {
        "has_erp_customer_payment_plan": bool(sales_plan),
        "has_erp_customer_payment_record": bool(sales_records),
        "has_erp_supplier_payment_plan": bool(outsource_plan),
        "has_erp_supplier_payment_record": bool(outsource_records),
        "customer_payment_plan_count": len(sales_plan),
        "customer_payment_record_count": len(sales_records),
        "supplier_payment_plan_count": len(outsource_plan),
        "supplier_payment_record_count": len(outsource_records),
        "has_erp_customer_invoice_record": bool(customer_invoice["record_count"]),
        "has_erp_supplier_invoice_record": bool(supplier_invoice["record_count"]),
        "erp_customer_invoice_record_count": customer_invoice["record_count"],
        "erp_customer_invoice_issued_count": customer_invoice["issued_count"],
        "erp_customer_invoice_pending_count": customer_invoice["pending_count"],
        "erp_customer_invoice_unresolved_count": customer_invoice["unresolved_count"],
        "erp_supplier_invoice_record_count": supplier_invoice["record_count"],
        "erp_supplier_invoice_issued_count": supplier_invoice["issued_count"],
        "erp_supplier_invoice_pending_count": supplier_invoice["pending_count"],
        "erp_supplier_invoice_unresolved_count": supplier_invoice["unresolved_count"],
    }


def _analysis(project, profile, starts, start_materials, contract_follow_up, department_handoffs, sales_contracts, customer_nodes, customer_receipts, receivable_schedule, outsource_contracts, supplier_payments, contract_balances, corrections, mold_transfer_receipts, cost_impacts, closure_items, quality_finance, erp_finance_execution):
    open_reservation = bool(supplier_payments["outstanding_reservations"])
    supplier_paid = bool(supplier_payments["confirmed_totals"])
    customer_received = bool(customer_receipts["confirmed_totals"])
    condition_pending = [node for node in customer_nodes if not node.get("condition_confirmed") and not node.get("schedule_confirmed")]
    schedule_pending = [node for node in receivable_schedule["nodes"] if node.get("due_state") in {"SCHEDULE_PENDING", "PARTIALLY_RECEIVED_SCHEDULE_PENDING"}]
    trigger_pending = [node for node in receivable_schedule["nodes"] if node.get("due_state") in {"UNTRIGGERED", "PARTIALLY_RECEIVED_UNTRIGGERED"}]
    currency_mismatches = [node for node in receivable_schedule["nodes"] if node.get("currency_mismatches")]
    settlement_overflows = [row for group in (
        contract_balances["customer_receivable"], contract_balances["supplier_payable"])
        for row in group if row.get("settlement_overflow")]
    invoice_done = any(item.get("item_key") == "INVOICE" and item.get("status") == "DONE" for item in closure_items)
    customer_receipt_done = any(item.get("item_key") == "CUSTOMER_RECEIPT" and item.get("status") == "DONE" for item in closure_items)
    supplier_settlement_done = any(item.get("item_key") == "SUPPLIER_SETTLEMENT" and item.get("status") == "DONE" for item in closure_items)
    cost_tasks = [task for case in cost_impacts for task in case.get("tasks") or []]
    acceptance = quality_finance['customer_acceptance']
    acceptance_status = acceptance['derived_status'] or {}
    acceptance_impact = bool(acceptance_status.get('has_acceptance_deduction') or acceptance_status.get('has_contract_change_required'))
    deductions = quality_finance['supplier_deductions']
    unsettled_deductions = [row for row in deductions['records'] if row.get('is_current', True)
        and (row['status'] in {'PROPOSED', 'RESPONSIBILITY_CONFIRMED'} or not row.get('lineage_valid', True))]
    erp_finance_flags = _erp_finance_flags(erp_finance_execution, sales_contracts, outsource_contracts)

    warnings = []
    gaps = []
    if not starts:
        gaps.append("未见正式开工通知上下文，无法证明已向财务形成开工交接。")
    elif not start_materials:
        gaps.append("正式开工通知缺少冻结的订单、客户、模具、机型/物料、合同和交期关联材料。")
    if contract_follow_up.get("state") == "OVERDUE":
        warnings.append("销售合同已超过正式开工材料中的预计到达日期；应提醒财务和项目负责人，并由业务/市场跟踪补充签订。")
    elif contract_follow_up.get("state") == "RECEIVED_DATE_MISSING":
        gaps.append("已见销售合同，但未登记合同原件实际到达日期。")
    elif contract_follow_up.get("state") == "RECEIVED_ATTACHMENT_MISSING":
        gaps.append("已见销售合同到达日期，但未见冻结的合同原件附件。")
    if not sales_contracts:
        gaps.append("未见可见销售合同及客户收款节点；不能判断客户应收条件。")
    if customer_nodes and not customer_received:
        warnings.append("当前只见客户合同收款节点或关闭清单，未见客户实际回款确认；不能把节点到期或清单核对当成实际回款。")
    if customer_received and customer_receipt_done:
        warnings.append("客户实际回款确认与关闭清单均存在；仍须按合同节点、发票和财务口径核对，不能用单次回款代表项目已结束。")
    if condition_pending:
        warnings.append("存在付款/收款节点条件未由财务确认，不能作为到期或付款依据。")
    if schedule_pending:
        gaps.append("存在客户收款节点尚未由财务确认结构化触发事件、账期或预计到期日；系统不会从条件文字猜测到期状态。")
    if trigger_pending:
        warnings.append("存在已配置但尚无触发日期依据的客户收款节点；仅保留未触发状态，不生成到期或逾期提醒。")
    if currency_mismatches:
        warnings.append("存在回款币种与合同节点币种不一致的记录；未纳入对应节点已收金额，需财务核对归属。")
    if settlement_overflows:
        warnings.append("存在当前有效合同的历史分配与直接实收实付合计超过合同金额，需暂停继续登记并核对合同替代关系。")
    if receivable_schedule["reminder_count"]:
        warnings.append("存在到期或逾期未收节点；提醒仅依据财务确认的触发日期、账期、到期日和实际回款记录，不代表自动催款或特殊认定。")
    if any(request.get("status") == "EFFECTIVE" for request in supplier_payments["requests"]):
        warnings.append("存在已审批供应商付款申请；审批通过不等于已付款或全部付清，仍须以财务实际付款确认汇总。")
    if open_reservation:
        warnings.append("存在未释放的供应商付款授权占用，项目关闭或结算前需核对。")
    if corrections:
        warnings.append("存在财务冲正/更正记录，汇总必须按有符号实付和冲正依据计算，不能覆盖原付款记录。")
    if cost_tasks:
        warnings.append("存在设变、异常或联络单费用/工时线索；收入、成本、利润需财务适配口径，不能用报价成本或回款金额直接替代。")
    if acceptance_impact:
        warnings.append("客户验收记录含扣款或合同变化要求；复验通过不会抹去历史费用依据，须关联对应财务及合同处理记录。验收登记本身不代表已结算或已收付款。")
    if deductions['records']:
        warnings.append("供应商扣款责任/结算记录与客户验收影响分别列示，仅沿明确记录引用追溯；不能根据金额或文字相同认定已对冲，也不自动减少应付余额或实际付款。")
    if unsettled_deductions:
        warnings.append("可见供应商扣款记录中存在尚未登记结算或前后关联不一致的事项；责任确认不等于结算完成。")
    if acceptance['records_truncated'] or deductions['possibly_truncated']:
        gaps.append("验收或扣款明细达到返回上限，不能把当前明细当成完整历史；验收影响标记仍按全部可见验收记录计算。")
    if erp_finance_flags["has_erp_customer_invoice_record"] or erp_finance_flags["has_erp_supplier_invoice_record"]:
        warnings.append("已读取 ERP 付款记录中的发票编号/状态；这些字段只证明 ERP 存在发票事实，不等同于 Agent 发票关闭、收入确认、银行回执或正式对账。")
    gaps.append("发票、收入确认、含税口径、工时计价、费用分摊和占用资金公式尚未完整接入；当前只做可见事实核对。")
    if (
        (erp_finance_execution or {}).get("status") == "RESOLVED"
        and any(erp_finance_flags.values())
    ):
        warnings.append("已读取 ERP 原系统合同付款计划/付款记录；这些记录只作为按合同角色核对的外部事实，不等同于 Agent 客户回款、供应商实付或项目结算完成。")

    return {
        "finance_handoff": {
            "effective_start_notices": starts,
            "formal_start_materials": start_materials,
            "contract_follow_up": contract_follow_up,
            "department_handoffs": department_handoffs,
            "profile_execution_mode": (profile or {}).get("execution_mode"),
            "settlement_status": (profile or {}).get("settlement_status"),
        },
        "sales_contracts": sales_contracts,
        "current_receivable_contract_totals": _money_total([
            contract for contract in sales_contracts if contract.get("status") == "EFFECTIVE"
        ]),
        "customer_receivable_nodes": customer_nodes,
        "customer_receivable_schedule": receivable_schedule,
        "customer_receipt_summary": customer_receipts,
        "full_outsource_contracts": outsource_contracts,
        "supplier_payment_summary": supplier_payments,
        "current_effective_contract_balances": contract_balances,
        "finance_corrections": corrections,
        "mold_transfer_receipts": mold_transfer_receipts,
        "cost_and_change_impacts": cost_impacts,
        "quality_finance_context": quality_finance,
        "closure_finance_items": closure_items,
        "erp_finance_execution": erp_finance_execution,
        "gaps": gaps,
        "warnings": warnings,
        "derived_status": {
            "project_status": project.status,
            "has_effective_start_notice": bool(starts),
            "has_complete_start_material": bool(start_materials),
            "contract_arrival_state": contract_follow_up.get("state"),
            "has_mold_transfer_time": bool(mold_transfer_receipts),
            "mold_transfer_is_quality_acceptance": False,
            "has_sales_contract_payment_nodes": bool(customer_nodes),
            "has_confirmed_receivable_schedule": any(node.get("schedule_confirmed") for node in customer_nodes),
            "customer_receivable_reminder_count": receivable_schedule["reminder_count"],
            "has_customer_actual_receipt_ledger": customer_received,
            "has_invoice_or_customer_receipt_closure_evidence": invoice_done or customer_receipt_done,
            "has_supplier_payment_request": bool(supplier_payments["requests"]),
            "has_confirmed_supplier_payment": supplier_paid,
            "has_open_supplier_payment_reservation": open_reservation,
            "has_contract_replacement_or_addition": any(
                row.get("relation_type") in {"REPLACEMENT", "ADDITION"}
                for row in sales_contracts + outsource_contracts
            ),
            "has_settlement_allocation_overflow": bool(settlement_overflows),
            "has_finance_correction": bool(corrections),
            "has_cost_or_deduction_signal": bool(cost_tasks or acceptance_impact or deductions['records']),
            "has_customer_acceptance_financial_impact": acceptance_impact if acceptance['visible'] else None,
            "has_supplier_deduction_record": bool(deductions['records']) if deductions['visible'] else None,
            "has_deduction_link_conflict": any(not row.get('lineage_valid', True) for row in deductions['records']) if deductions['visible'] else None,
            "has_unsettled_supplier_deduction": (True if unsettled_deductions else
                None if not deductions['visible'] or deductions['possibly_truncated'] else False),
            "has_supplier_settlement_closure_evidence": supplier_settlement_done,
            "has_erp_invoice_record": bool(
                erp_finance_flags["has_erp_customer_invoice_record"]
                or erp_finance_flags["has_erp_supplier_invoice_record"]
            ),
            **erp_finance_flags,
        },
    }


def customer_receivable_schedule_schema():
    return CustomerReceivableScheduleProposalInput.model_json_schema()


def customer_receipt_schema():
    return CustomerReceiptProposalInput.model_json_schema()


def supplier_payment_confirmation_schema():
    return SupplierPaymentConfirmationProposalInput.model_json_schema()


def supplier_deduction_settlement_schema():
    return SupplierDeductionSettlementProposalInput.model_json_schema()


def mold_transfer_receipt_schema():
    return MoldTransferReceiptProposalInput.model_json_schema()


def parse_customer_receivable_schedule(arguments):
    try:
        return CustomerReceivableScheduleProposalInput.model_validate(arguments or {})
    except ValidationError as error:
        raise DomainError("INVALID_TOOL_INPUT", "客户收款节点账期参数不完整或不符合要求："+error.errors()[0]["msg"]) from None


def parse_customer_receipt(arguments):
    try:
        return CustomerReceiptProposalInput.model_validate(arguments or {})
    except ValidationError as error:
        raise DomainError("INVALID_TOOL_INPUT", "客户回款确认参数不完整或不符合要求："+error.errors()[0]["msg"]) from None


def parse_supplier_payment_confirmation(arguments):
    try:
        return SupplierPaymentConfirmationProposalInput.model_validate(arguments or {})
    except ValidationError as error:
        raise DomainError("INVALID_TOOL_INPUT", "供应商实付确认参数不完整或不符合要求："+error.errors()[0]["msg"]) from None


def parse_supplier_deduction_settlement(arguments):
    try:
        data = SupplierDeductionSettlementProposalInput.model_validate(arguments or {})
    except ValidationError as error:
        raise DomainError("INVALID_TOOL_INPUT", "供应商扣款结算参数不完整或不符合要求："+error.errors()[0]["msg"]) from None
    if data.status == "SETTLED" and (not data.settlement_reference or not data.settlement_evidence):
        raise DomainError("INVALID_TOOL_INPUT", "供应商扣款已结算必须填写结算单号和结算依据")
    if data.status == "RESPONSIBILITY_CONFIRMED" and data.settlement_reference:
        raise DomainError("INVALID_TOOL_INPUT", "仅确认责任时不要填写结算单号；结算完成后再登记已结算依据")
    if data.previous_deduction_id and (data.status != 'SETTLED' or data.source_ref is not None):
        raise DomainError('INVALID_TOOL_INPUT', '引用原责任记录时须登记已结算，source_ref 留空；原来源由 previous_deduction_id 追溯')
    return data


def parse_mold_transfer_receipt(arguments):
    try:
        data = MoldTransferReceiptProposalInput.model_validate(arguments or {})
    except ValidationError as error:
        raise DomainError(
            "INVALID_TOOL_INPUT",
            "移模客户签收参数不完整或不符合要求："+error.errors()[0]["msg"],
        ) from None
    if data.signed_date > now().date():
        raise DomainError("DATE_INVALID", "客户签收日期不能在未来")
    return data


def _receipt_files(db, user, file_ids, *, run=None, step_id=None):
    requested = [str(value) for value in (file_ids or [])]
    if len(requested) != len(set(requested)):
        raise DomainError("FILE_CONTEXT_INVALID", "客户签收附件不能重复", 409)
    if not requested:
        return []
    if run is None and step_id:
        step = db.get(m.Step, step_id)
        run = db.get(m.Run, step.run_id) if step else None
    if not run or run.user_id != user.id:
        raise DomainError("FILE_CONTEXT_INVALID", "客户签收附件须绑定当前本人任务", 403)
    blobs = []
    for file_id in requested:
        blob = uploaded_file(db, user, file_id)
        if blob.conversation_id != run.conversation_id:
            raise DomainError("FILE_CONTEXT_INVALID", "只能关联当前会话中的客户签收原件", 403)
        if not db.scalar(select(m.RunFile).where(
            m.RunFile.run_id == run.id,
            m.RunFile.file_id == blob.id,
        )):
            raise DomainError("FILE_CONTEXT_INVALID", "客户签收附件尚未绑定当前任务", 403)
        blobs.append(blob)
    return blobs


def _receipt_display_files(display, blobs):
    if not blobs:
        return display
    return {
        **display,
        "客户签收原件": [
            {"id": blob.id, "filename": blob.filename, "sha256": blob.sha256}
            for blob in blobs
        ],
        "说明": display["说明"] + " 原件只作为不可变 Agent 附件登记，不替代客户签收或质量验收事实。",
    }


def _receipt_payload(data: CustomerReceiptProposalInput):
    return CustomerReceipt(amount=data.amount, currency=data.currency, received_date=data.received_date,
        reference=data.reference, evidence=data.evidence, stage_id=data.stage_id,
        source_ref=data.source_ref, note=data.note)


def preview_customer_receivable_schedule(db, user, data: CustomerReceivableScheduleProposalInput):
    project = db.get(m.Project, data.project_id)
    if not project:
        raise DomainError("NOT_FOUND", "项目不存在", 404)
    scope = {"project_id": project.id}
    require(db, user, "project.read", scope)
    require(db, user, "sales_contract.read", scope)
    require(db, user, "customer_receipt.confirm", scope)
    if project.row_version != data.project_version:
        raise DomainError("VERSION_CONFLICT", "项目状态已变化，请重新查询后准备", 409)
    contract = db.get(m.BusinessSubject, data.contract_subject_id)
    if not contract or contract.project_id != project.id or contract.kind != "sales_contract":
        raise DomainError("NOT_FOUND", "销售合同不存在或不属于该项目", 404)
    if contract.status != "EFFECTIVE":
        raise DomainError("CONTRACT_NOT_EFFECTIVE", "只能为已生效销售合同确认收款节点账期", 409)
    stage = db.get(m.PaymentStage, data.stage_id)
    if not stage or stage.contract_id != contract.id:
        raise DomainError("STAGE_UNKNOWN", "收款节点不存在或不属于该销售合同", 404)
    from domain_packs.mold.erp.commercial.contract_materials import require_current_stage
    require_current_stage(db,stage)
    detail = db.get(m.ContractDetail, contract.id)
    due_date = data.expected_due_date
    if due_date is None and data.trigger_date is not None and data.credit_days is not None:
        due_date = data.trigger_date + timedelta(days=data.credit_days)
    display = {
        "操作": "确认客户收款节点触发事件与账期",
        "项目": project.code+" · "+project.name,
        "项目版本": project.row_version,
        "销售合同": detail.contract_number if detail else contract.number,
        "收款节点": stage.name,
        "节点金额": str(stage.amount)+" "+stage.currency,
        "节点比例": (str(data.ratio_percent)+"%") if data.ratio_percent is not None else "按固定金额",
        "触发事件": data.trigger_event,
        "触发日期": data.trigger_date.isoformat() if data.trigger_date else "尚未触发",
        "账期": (str(data.credit_days)+" 天") if data.credit_days is not None else "按明确到期日",
        "预计到期日": due_date.isoformat() if due_date else "触发后按账期计算",
        "合同/账期依据": data.schedule_evidence,
        "触发依据": data.trigger_evidence or "尚未触发",
        "财务特殊标记": data.special_mark or "无",
        "说明": "本人确认后仅更新该合同收款节点的结构化触发事件、账期和到期依据；不会登记实际回款、自动催款或修改合同金额。",
    }
    return project, contract, stage, due_date, display


def confirm_customer_receivable_schedule(db, user, data: CustomerReceivableScheduleProposalInput):
    project, contract, stage, due_date, _ = preview_customer_receivable_schedule(db, user, data)
    stage.ratio_percent = data.ratio_percent
    stage.trigger_event = data.trigger_event
    stage.trigger_date = data.trigger_date
    stage.credit_days = data.credit_days
    stage.expected_due_date = due_date
    stage.schedule_confirmed = True
    stage.schedule_evidence = data.schedule_evidence
    stage.trigger_evidence = data.trigger_evidence
    stage.special_mark = data.special_mark
    record(db, user, "customer_receivable_schedule.confirm", stage.id, {
        "project_id": project.id,
        "contract_subject_id": contract.id,
        "stage_id": stage.id,
        "trigger_event": data.trigger_event,
        "trigger_date": data.trigger_date.isoformat() if data.trigger_date else None,
        "credit_days": data.credit_days,
        "expected_due_date": due_date.isoformat() if due_date else None,
        "special_mark": data.special_mark,
    }, [contract.created_by])
    db.flush()
    return stage


def _supplier_payment_payload(data: SupplierPaymentConfirmationProposalInput):
    return Payment(amount=data.amount, currency=data.currency, paid_date=data.paid_date,
        reference=data.reference, evidence=data.evidence)


def _deduction_scope(project_id):
    return {"project_id": project_id, "category": "outsource"}


def preview_supplier_deduction_settlement(db, user, data: SupplierDeductionSettlementProposalInput):
    project = db.get(m.Project, data.project_id)
    if not project:
        raise DomainError("NOT_FOUND", "项目不存在", 404)
    require(db, user, "project.read", {"project_id": project.id})
    if project.row_version != data.project_version:
        raise DomainError("VERSION_CONFLICT", "项目状态已变化，请重新查询后准备", 409)
    scope = _deduction_scope(project.id)
    require(db, user, "full_outsource_contract.read", scope)
    require(db, user, "finance.confirm", scope)
    supplier = db.get(m.Supplier, data.supplier_id)
    if not supplier:
        raise DomainError("NOT_FOUND", "供应商不存在或不属于当前可见范围", 404)
    contract_number = "未关联委外合同"
    if data.contract_subject_id:
        contract = db.get(m.BusinessSubject, data.contract_subject_id)
        if not contract or contract.project_id != project.id or contract.kind != "full_outsource_contract":
            raise DomainError("CONTRACT_NOT_FOUND", "整套委外合同不存在或不属于该项目", 404)
        if contract.status != "EFFECTIVE" and not (data.previous_deduction_id and contract.status == 'CLOSED'):
            raise DomainError("CONTRACT_NOT_EFFECTIVE", "首次扣款须关联生效委外合同；已有责任记录的结算可追溯原已关闭合同", 409)
        detail = db.get(m.ContractDetail, contract.id)
        if not detail or detail.supplier_id != supplier.id:
            raise DomainError("CONTRACT_SUPPLIER_MISMATCH", "整套委外合同供应商与扣款供应商不一致", 409)
        contract_number = detail.contract_number
    contact_title = "未关联工程联络单"
    task_title = "未关联事项"
    if data.contact_case_id:
        case = db.get(m.ContactCase, data.contact_case_id)
        if not case or case.project_id != project.id:
            raise DomainError("CONTACT_NOT_FOUND", "工程联络单不存在或不属于该项目", 404)
        require(db, user, "contact.read", {"project_id": project.id, "category": case.category})
        contact_title = case.title
        if data.contact_task_id:
            task = db.get(m.ContactTask, data.contact_task_id)
            if not task or task.case_id != case.id:
                raise DomainError("CONTACT_TASK_NOT_FOUND", "工程联络事项不存在或不属于该联络单", 404)
            task_title = task.title
    elif data.contact_task_id:
        raise DomainError("CONTACT_REQUIRED", "关联工程联络事项时必须同时提供工程联络单 ID")
    previous = None
    if data.previous_deduction_id:
        from domain_packs.mold.erp.finance.deduction_status import FROZEN_FIELDS
        previous = db.get(m.SupplierDeductionSettlement, data.previous_deduction_id)
        if not previous or previous.project_id != project.id or previous.supplier_id != supplier.id:
            raise DomainError('DEDUCTION_NOT_FOUND', '原责任确认记录不存在或不属于本项目及供应商', 404)
        if previous.status != 'RESPONSIBILITY_CONFIRMED' or previous.previous_deduction_id:
            raise DomainError('DEDUCTION_STATE_CHANGED', '只能针对尚未结算的责任确认记录登记后续结算', 409)
        if db.scalar(select(m.SupplierDeductionSettlement.id).where(
                m.SupplierDeductionSettlement.previous_deduction_id == previous.id)):
            raise DomainError('DEDUCTION_ALREADY_SETTLED', '原责任记录已有后续结算，请重新查询，不得重复确认', 409)
        if (any(getattr(previous, key) != getattr(data, key) for key in FROZEN_FIELDS)
                or (previous.customer_acceptance_id and previous.customer_acceptance_id != data.customer_acceptance_id)):
            raise DomainError('DEDUCTION_BASIS_CHANGED', '后续结算必须保持原供应商、合同、联络、责任、金额币种及已有验收关联；变更须另行核对更正依据', 409)
    acceptance = None
    if data.customer_acceptance_id:
        require(db, user, 'project_close.read', {'project_id': project.id})
        acceptance = db.get(m.CustomerAcceptanceRecord, data.customer_acceptance_id)
        if not acceptance or acceptance.project_id != project.id:
            raise DomainError('ACCEPTANCE_NOT_FOUND', '关联客户验收记录不存在或不属于本项目', 404)
    duplicate = None
    if data.source_ref:
        duplicate = db.scalar(select(m.SupplierDeductionSettlement.id).where(
            m.SupplierDeductionSettlement.project_id == project.id,
            m.SupplierDeductionSettlement.supplier_id == supplier.id,
            m.SupplierDeductionSettlement.reason == data.reason,
            m.SupplierDeductionSettlement.source_ref == data.source_ref,
        ))
    if duplicate:
        raise DomainError("DEDUCTION_DUPLICATE_SOURCE", "该供应商扣款来源已登记", 409)
    if data.settlement_reference and db.scalar(select(m.SupplierDeductionSettlement.id).where(
        m.SupplierDeductionSettlement.project_id == project.id,
        m.SupplierDeductionSettlement.settlement_reference == data.settlement_reference,
    )):
        raise DomainError("DEDUCTION_DUPLICATE_SETTLEMENT", "该供应商扣款结算单号已登记", 409)
    display = {
        "操作": "登记供应商扣款责任/结算依据",
        "项目": project.code+" · "+project.name,
        "项目版本": project.row_version,
        "供应商": supplier.name,
        "关联合同": contract_number,
        "工程联络单": contact_title,
        "工程联络事项": task_title,
        "扣款原因": data.reason,
        "责任归属": data.responsibility,
        "扣款金额": str(data.deduction_amount)+" "+data.currency,
        "状态": "已结算" if data.status == "SETTLED" else "责任已确认",
        "责任依据": data.responsibility_evidence,
        "结算单号": data.settlement_reference or "未结算",
        "结算依据": data.settlement_evidence or "未结算",
        "来源引用": data.source_ref or "未填写",
        "说明": "本人确认后仅登记供应商扣款责任和/或结算依据；不执行收付款，不自动抵扣供应商付款，不代表客户对我方扣款已完成。",
    }
    if previous:
        display['原责任确认记录'] = previous.id
        display['原责任来源引用'] = previous.source_ref or '未填写'
        display['原责任确认依据'] = previous.responsibility_evidence
    if acceptance:
        display['关联客户验收记录'] = acceptance.id
        display['客户验收结果'] = acceptance.result
        display['客户验收依据'] = acceptance.evidence
        display['验收关联含义'] = '问题来源追溯；客户扣款与供应商责任、金额及结算分别确认，不自动对冲'
    return display


def create_supplier_deduction_settlement(db, user, data: SupplierDeductionSettlementProposalInput):
    db.scalar(select(m.Project).where(m.Project.id == data.project_id).with_for_update().execution_options(populate_existing=True))
    preview_supplier_deduction_settlement(db, user, data)
    row = m.SupplierDeductionSettlement(
        project_id=data.project_id,
        supplier_id=data.supplier_id,
        previous_deduction_id=data.previous_deduction_id,
        customer_acceptance_id=data.customer_acceptance_id,
        contract_subject_id=data.contract_subject_id,
        contact_case_id=data.contact_case_id,
        contact_task_id=data.contact_task_id,
        reason=data.reason,
        responsibility=data.responsibility,
        deduction_amount=data.deduction_amount,
        currency=data.currency,
        status=data.status,
        settlement_reference=data.settlement_reference,
        responsibility_evidence=data.responsibility_evidence,
        settlement_evidence=data.settlement_evidence or "",
        confirmed_by=user.id,
        settled_by=user.id if data.status == "SETTLED" else None,
        settled_at=now() if data.status == "SETTLED" else None,
        source_system="MANUAL",
        source_ref=data.source_ref,
    )
    db.add(row)
    db.flush()
    return row


def preview_customer_receipt(db, user, data: CustomerReceiptProposalInput):
    project = db.get(m.Project, data.project_id)
    if not project:
        raise DomainError("NOT_FOUND", "项目不存在", 404)
    scope = {"project_id": project.id}
    require(db, user, "project.read", scope)
    require(db, user, "sales_contract.read", scope)
    require(db, user, "customer_receipt.confirm", scope)
    if project.row_version != data.project_version:
        raise DomainError("VERSION_CONFLICT", "项目状态已变化，请重新查询后准备", 409)
    contract = db.get(m.BusinessSubject, data.contract_subject_id)
    if not contract or contract.project_id != project.id:
        raise DomainError("NOT_FOUND", "销售合同不存在或不属于该项目", 404)
    payload = _receipt_payload(data)
    detail, stage = validate_customer_receipt(db, contract, payload)
    display = {
        "操作": "登记客户实际回款确认",
        "项目": project.code+" · "+project.name,
        "项目版本": project.row_version,
        "销售合同": detail.contract_number,
        "收款节点": stage.name if stage else "未指定节点",
        "回款金额": str(data.amount)+" "+data.currency,
        "回款日期": data.received_date.isoformat(),
        "银行流水/凭证号": data.reference,
        "来源引用": data.source_ref or "未填写",
        "依据": data.evidence,
        "备注": data.note or "无",
        "说明": "本人确认后仅登记财务已确认的客户实际回款事实；不代表开票、收入确认、项目关闭或 ERP 财务对账完成。",
    }
    return payload, display


def preview_supplier_payment_confirmation(db, user, data: SupplierPaymentConfirmationProposalInput):
    project = db.get(m.Project, data.project_id)
    if not project:
        raise DomainError("NOT_FOUND", "项目不存在", 404)
    scope = {"project_id": project.id}
    require(db, user, "project.read", scope)
    if project.row_version != data.project_version:
        raise DomainError("VERSION_CONFLICT", "项目状态已变化，请重新查询后准备", 409)
    payment = db.get(m.BusinessSubject, data.payment_subject_id)
    if not payment or payment.project_id != project.id:
        raise DomainError("NOT_FOUND", "供应商付款申请不存在或不属于该项目", 404)
    payment_scope = {"project_id": project.id, "category": payment.category}
    require(db, user, "supplier_payment.read", payment_scope)
    require(db, user, "finance.confirm", payment_scope)
    payload = _supplier_payment_payload(data)
    detail = validate_supplier_payment(db, payment, payload)
    stage = db.get(m.PaymentStage, detail.stage_id)
    contract = db.get(m.BusinessSubject, stage.contract_id) if stage else None
    contract_detail = db.get(m.ContractDetail, contract.id) if contract else None
    display = {
        "操作": "登记供应商实际付款确认",
        "项目": project.code+" · "+project.name,
        "项目版本": project.row_version,
        "付款申请": payment.number,
        "付款节点": stage.name if stage else "未找到节点",
        "关联合同": contract_detail.contract_number if contract_detail else "未找到合同",
        "本次实付": str(data.amount)+" "+data.currency,
        "付款日期": data.paid_date.isoformat(),
        "付款流水/凭证号": data.reference,
        "本次授权余额": str(detail.reservation)+" "+detail.currency,
        "依据": data.evidence,
        "说明": "本人确认后仅登记财务已确认的供应商实际付款事实，并扣减该申请授权余额；不执行银行转账，不代表全部付款完成或项目关闭。",
    }
    return payload, display


def preview_mold_transfer_receipt(db, user, data: MoldTransferReceiptProposalInput):
    project = db.get(m.Project, data.project_id)
    if not project:
        raise DomainError("NOT_FOUND", "项目不存在", 404)
    scope = {"project_id": project.id}
    require(db, user, "project.read", scope)
    require(db, user, "project.dossier.read", scope)
    require(db, user, "customer_receipt.confirm", scope)
    if project.row_version != data.project_version:
        raise DomainError("VERSION_CONFLICT", "项目状态已变化，请重新查询后准备", 409)
    if data.signed_date > now().date():
        raise DomainError("DATE_INVALID", "客户签收日期不能在未来")
    if data.logistics_route_id:
        route = db.get(m.LogisticsRoute, data.logistics_route_id)
        if not route:
            raise DomainError("LOGISTICS_ROUTE_NOT_FOUND", "物流路线不存在", 404)
    duplicate = db.scalar(
        select(m.CustomerDeliverySignature.id).where(
            m.CustomerDeliverySignature.project_id == project.id,
            m.CustomerDeliverySignature.shipment_reference == data.shipment_reference,
            m.CustomerDeliverySignature.signed_date == data.signed_date,
        )
    )
    if duplicate:
        raise DomainError("MOLD_TRANSFER_RECEIPT_DUPLICATE", "该移模客户签收记录已存在", 409)
    display = {
        "操作": "登记移模客户签收时间",
        "项目": project.code+" · "+project.name,
        "项目版本": project.row_version,
        "客户签收/移模时间": data.signed_date.isoformat(),
        "签收或交付单号": data.shipment_reference,
        "客户签收人": data.signer_name,
        "物流路线": data.logistics_route_id or "未关联",
        "签收依据": data.evidence,
        "说明": "本人确认后仅由财务登记客户签收日期作为移模时间；客户签收不等于质量验收通过，也不代表回款、结算或项目关闭。",
    }
    return project, display


def create_mold_transfer_receipt(db, user, data: MoldTransferReceiptProposalInput):
    return create_mold_transfer_receipt_with_context(db, user, data)


def create_mold_transfer_receipt_with_context(
    db,
    user,
    data: MoldTransferReceiptProposalInput,
    *,
    run=None,
    step_id=None,
):
    blobs = _receipt_files(db, user, data.file_ids, run=run, step_id=step_id)
    project, _ = preview_mold_transfer_receipt(db, user, data)
    row = m.CustomerDeliverySignature(
        project_id=project.id,
        logistics_route_id=data.logistics_route_id,
        shipment_reference=data.shipment_reference,
        signed_date=data.signed_date,
        signer_name=data.signer_name,
        sign_status="SIGNED",
        move_type="MOLD_TRANSFER",
        evidence=data.evidence,
        recorded_by=user.id,
    )
    db.add(row)
    db.flush()
    for version, blob in enumerate(blobs, start=1):
        db.add(
            m.CustomerDeliverySignatureAttachment(
                signature_id=row.id,
                file_id=blob.id,
                role="SIGNATURE_EVIDENCE",
                version=version,
                title=blob.filename,
                content_sha256=blob.sha256,
                linked_by=user.id,
            )
        )
    db.flush()
    profile = db.get(m.ProjectProfile, project.id)
    record(db, user, "mold_transfer.customer_receipt.recorded", row.id, {
        "project_id": project.id,
        "signed_date": data.signed_date.isoformat(),
        "shipment_reference": data.shipment_reference,
        "move_type": "MOLD_TRANSFER",
    }, [profile.owner_user_id] if profile and profile.owner_user_id else [])
    return row


def execute_finance_tool(db, user, key, arguments, run=None):
    if key in {'prepare_supplier_payment_condition','prepare_supplier_payment_request'}:
        from . import supplier_payment_tools as payment
        kind = key.removeprefix('prepare_')
        data = payment.parse(kind, arguments)
        display = payment.preview(db, user, kind, data)
        approval = kind == 'supplier_payment_request'
        return {'data':[], 'source':'agent_proposal', 'as_of':now().isoformat(),
            'proposal':{'kind':kind, 'action':kind, 'input':data.model_dump(mode='json'), 'display':display,
                'requires_approval':approval, 'confirmation_policy':proposal_confirmation_policy(run, requires_approval=approval)},
            'limitations':['仅准备本人确认建议；条件核验、付款审批授权和实际付款是独立事实，不操作银行或 ERP 对账。']}
    if key == 'prepare_finance_correction':
        from . import finance_correction_tools as correction
        data = correction.parse(arguments)
        _, display = correction.preview(db, user, data)
        return {'data':[], 'source':'agent_proposal', 'as_of':now().isoformat(),
            'proposal':{'kind':'finance_correction', 'action':'submit_finance_correction',
                'input':data.model_dump(mode='json'), 'display':display, 'requires_approval':True,
                'confirmation_policy':proposal_confirmation_policy(run, requires_approval=True)},
            'limitations':['本人确认只提交 Agent BPM；审批生效才登记反向付款事实，不执行银行退款或 ERP 对账。']}
    if key not in FINANCE_PROPOSAL_TOOLS:
        raise DomainError("TOOL_UNKNOWN", "工具未实现", 403)
    if key == "prepare_customer_receivable_schedule":
        data = parse_customer_receivable_schedule(arguments)
        _, _, _, _, display = preview_customer_receivable_schedule(db, user, data)
        proposal = {"kind": "customer_receivable_schedule", "action": "confirm_customer_receivable_schedule",
            "requires_approval": False, "input": data.model_dump(mode="json"), "display": display,
            "confirmation_policy": proposal_confirmation_policy(run, requires_approval=False)}
        limitations = ["仅准备客户收款节点结构化账期确认；本人确认后才更新节点，不登记实际回款、不自动催款、不修改合同金额。"]
    elif key == "prepare_customer_receipt_confirmation":
        data = parse_customer_receipt(arguments)
        _, display = preview_customer_receipt(db, user, data)
        proposal = {"kind": "customer_receipt", "action": "confirm_customer_receipt",
            "requires_approval": False, "input": data.model_dump(mode="json"), "display": display,
            "confirmation_policy": proposal_confirmation_policy(run, requires_approval=False)}
        limitations = ["仅准备客户实际回款登记建议；本人确认后才写入回款确认台账，不执行收款、不开票、不计算收入利润。"]
    elif key == "prepare_mold_transfer_receipt":
        data = parse_mold_transfer_receipt(arguments)
        blobs = _receipt_files(db, user, data.file_ids, run=run)
        _, display = preview_mold_transfer_receipt(db, user, data)
        display = _receipt_display_files(display, blobs)
        proposal = {"kind": "mold_transfer_receipt", "action": "confirm_mold_transfer_receipt",
            "requires_approval": False, "input": data.model_dump(mode="json"), "display": display,
            "confirmation_policy": proposal_confirmation_policy(run, requires_approval=False)}
        limitations = ["仅准备财务人工登记移模客户签收时间；本人确认后才写入，客户签收不等于质量验收、回款、结算或项目关闭。"]
    else:
        if key == "prepare_supplier_payment_confirmation":
            data = parse_supplier_payment_confirmation(arguments)
            _, display = preview_supplier_payment_confirmation(db, user, data)
            proposal = {"kind": "supplier_payment_confirmation", "action": "confirm_supplier_payment",
                "requires_approval": False, "input": data.model_dump(mode="json"), "display": display,
                "confirmation_policy": proposal_confirmation_policy(run, requires_approval=False)}
            limitations = ["仅准备供应商实际付款登记建议；本人确认后才写入付款确认记录，不执行银行转账，不代表全部付款完成。"]
        else:
            data = parse_supplier_deduction_settlement(arguments)
            display = preview_supplier_deduction_settlement(db, user, data)
            proposal = {"kind": "supplier_deduction_settlement", "action": "confirm_supplier_deduction_settlement",
                "requires_approval": False, "input": data.model_dump(mode="json"), "display": display,
                "confirmation_policy": proposal_confirmation_policy(run, requires_approval=False)}
            limitations = ["仅准备供应商扣款责任/结算依据登记建议；本人确认后才写入，不执行收付款或自动抵扣。"]
    return {"data": [], "source": "agent_proposal", "as_of": now().isoformat(), "proposal": proposal,
        "limitations": limitations}


def source(db, user, step_id, *, for_read=False):
    from domain_packs.mold.tool_gateway import available_tools
    step = db.get(m.Step, step_id)
    run = db.get(m.Run, step.run_id) if step else None
    if not run or run.user_id != user.id:
        raise DomainError("NOT_FOUND", "操作建议不存在或无权访问", 404)
    if run.status not in {"RUNNING", "RUNNING_SCOPED", "SUCCEEDED"}:
        resolved = for_read and db.scalar(select(m.HumanIntent.id).where(
            m.HumanIntent.user_id == user.id, m.HumanIntent.action == 'finance.execute',
            m.HumanIntent.resource_id == step_id,
            m.HumanIntent.receipt['status'].as_string().in_(['CONFIRMED','SUBMITTED'])).limit(1))
        if not resolved:
            raise DomainError("PROPOSAL_STOPPED", "任务已停止，请重新准备操作", 409)
    if run.security_version != user.security_version or run.checkpoint.get("authorization_hash") != fingerprint(db, user):
        raise DomainError("AUTHORIZATION_CHANGED", "授权已变化，请重新准备操作", 403)
    proposal = step.result.get("proposal")
    if step.tool not in available_tools(db, user) or step.tool not in FINANCE_PROPOSAL_TOOLS or not proposal:
        raise DomainError("TOOL_FORBIDDEN", "操作能力不可用", 403)
    return proposal


def validate_intent(db, user, payload):
    proposal = source(db, user, payload["step_id"])
    if content_hash(proposal) != payload["proposal_hash"]:
        raise DomainError("CONFIRMATION_INVALID", "操作建议内容已变化", 409)
    if proposal.get('kind') in {'supplier_payment_condition','supplier_payment_request'}:
        from . import supplier_payment_tools as payment
        data = payment.parse(proposal['kind'], proposal['input'])
        display = payment.preview(db, user, proposal['kind'], data)
    elif proposal.get('kind') == 'finance_correction':
        from . import finance_correction_tools as correction
        data = correction.parse(proposal['input'])
        _, display = correction.preview(db, user, data)
    elif proposal.get("kind") == "customer_receivable_schedule":
        data = parse_customer_receivable_schedule(proposal["input"])
        _, _, _, _, display = preview_customer_receivable_schedule(db, user, data)
    elif proposal.get("kind") == "customer_receipt":
        data = parse_customer_receipt(proposal["input"])
        _, display = preview_customer_receipt(db, user, data)
    elif proposal.get("kind") == "supplier_payment_confirmation":
        data = parse_supplier_payment_confirmation(proposal["input"])
        _, display = preview_supplier_payment_confirmation(db, user, data)
    elif proposal.get("kind") == "supplier_deduction_settlement":
        data = parse_supplier_deduction_settlement(proposal["input"])
        display = preview_supplier_deduction_settlement(db, user, data)
    elif proposal.get("kind") == "mold_transfer_receipt":
        data = parse_mold_transfer_receipt(proposal["input"])
        blobs = _receipt_files(db, user, data.file_ids, step_id=payload["step_id"])
        _, display = preview_mold_transfer_receipt(db, user, data)
        display = _receipt_display_files(display, blobs)
    else:
        raise DomainError("TOOL_FORBIDDEN", "操作建议类型不可用", 403)
    if content_hash(display) != content_hash(proposal["display"]):
        raise DomainError("VERSION_CONFLICT", "项目或财务资料已变化，请重新准备", 409)
    return proposal, data


def confirm(db, user, payload):
    from domain_packs.mold.erp.core.domain_commands import execute_command
    proposal, data = validate_intent(db, user, payload)
    if proposal.get('kind') in {'supplier_payment_condition','supplier_payment_request'}:
        from . import supplier_payment_tools as payment
        return payment.confirm(db, user, proposal['kind'], data, proposal)
    if proposal.get('kind') == 'finance_correction':
        from . import finance_correction_tools as correction
        return correction.confirm(db, user, data, proposal)
    if proposal.get("kind") == "customer_receivable_schedule":
        stage = confirm_customer_receivable_schedule(db, user, data)
        return {"project_id": data.project_id, "contract_subject_id": data.contract_subject_id,
            "stage_id": stage.id, "action": "customer_receivable_schedule_confirm", "status": "CONFIRMED"}
    if proposal.get("kind") == "customer_receipt":
        receipt = _receipt_payload(data)
        result = execute_command(db, user, "customer_receipt.confirm", data.contract_subject_id, receipt.model_dump(mode="json"))
        return {"project_id": data.project_id, "contract_subject_id": data.contract_subject_id,
            "customer_receipt_id": result["customer_receipt_id"], "action": "customer_receipt_confirm", "status": "CONFIRMED"}
    if proposal.get("kind") == "supplier_payment_confirmation":
        payment = _supplier_payment_payload(data)
        result = execute_command(db, user, "finance.confirm", data.payment_subject_id, payment.model_dump(mode="json"))
        return {"project_id": data.project_id, "payment_subject_id": data.payment_subject_id,
            "payment_confirmation_id": result["payment_confirmation_id"], "action": "supplier_payment_confirm", "status": "CONFIRMED"}
    if proposal.get("kind") == "mold_transfer_receipt":
        receipt = create_mold_transfer_receipt_with_context(
            db,
            user,
            data,
            step_id=payload["step_id"],
        )
        return {"project_id": data.project_id, "customer_delivery_signature_id": receipt.id,
            "action": "mold_transfer_receipt_confirm", "status": "CONFIRMED"}
    settlement = create_supplier_deduction_settlement(db, user, data)
    return {"project_id": data.project_id, "supplier_deduction_settlement_id": settlement.id,
        "action": "supplier_deduction_settlement_confirm", "status": "CONFIRMED"}


def query(db, user, data: ProjectDossierInput, allowed_tools: set[str]):
    project, alternatives, truncated = _resolve(db, user, data)
    limitations = [
        "只读取当前用户可见项目与财务相关业务事实；合同、付款、冲正、关闭清单仍受对应数据权限约束。",
        "合同收款节点、系统提醒、付款申请审批、实际付款确认、客户实际回款、发票和项目关闭是不同事实，不能相互替代。",
        "本工具不创建同义财务台账、不确认回款或付款、不执行银行转账、不计算正式收入成本利润；ERP/财务适配事实缺失时必须明确未知。",
    ]
    if truncated:
        limitations.append("最多检查前500个可见项目，结果可能未覆盖全部可见范围。")
    if project:
        profile = _profile(db, user, project.id)
        skipped = []
        rows = {}
        for kind, label in (
            ("internal_start", "正式开工通知"),
            ("sales_contract", "销售合同/客户收款节点"),
            ("customer_receipt", "客户实际回款确认"),
            ("full_outsource_contract", "整套委外合同/供应商付款节点"),
            ("supplier_payment", "供应商付款申请和实付"),
            ("finance_correction", "财务冲正"),
            ("project_close", "项目关闭财务清单"),
        ):
            if _can_read_kind(db, user, kind, project.id):
                rows[kind] = _subject_rows(db, user, project.id, kind)
            else:
                rows[kind] = []
                skipped.append(label)
        starts = []
        for row in rows["internal_start"]:
            detail = row.get("detail") if isinstance(row.get("detail"), dict) else {}
            if detail.get("decision") == "START" and row.get("status") == "EFFECTIVE":
                starts.append(
                    {
                        "id": row.get("id"),
                        "number": row.get("number"),
                        "effective_date": detail.get("effective_date"),
                        "source_subject_id": detail.get("source_subject_id"),
                        "evidence_present": bool(detail.get("evidence")),
                    }
                )
        from domain_packs.mold.erp.project import start_materials as start_material_service
        frozen_start_materials = [
            material
            for material in (
                start_material_service.card(db, row["id"]) for row in starts
            )
            if material
        ]
        sales_contracts, customer_nodes = _contract_context(rows["sales_contract"], "CUSTOMER_RECEIVABLE")
        latest_start_id = starts[0]["id"] if starts else None
        contract_follow_up = start_material_service.contract_follow_up(
            db, project.id, latest_start_id, rows["sales_contract"]
        )
        from domain_packs.mold.erp.project import start_dispatches
        department_handoffs = start_dispatches.summary(db, latest_start_id)
        customer_receipts, receipts_skipped = _customer_receipts(db, user, project.id, sales_contracts, customer_nodes)
        receivable_schedule = _receivable_schedule(customer_nodes, customer_receipts, now().date())
        outsource_contracts, _ = _contract_context(rows["full_outsource_contract"], "SUPPLIER_PAYABLE")
        supplier_payments = _supplier_payments(rows["supplier_payment"], sales_contracts, outsource_contracts)
        contract_numbers = list(dict.fromkeys(
            row.get("contract_number")
            for row in sales_contracts + outsource_contracts
            if row.get("contract_number")
        ))
        from domain_packs.mold.erp.design.erp_progress import query_project_finance_execution
        erp_finance_execution = query_project_finance_execution(
            db, user, project, contract_numbers=contract_numbers
        )
        contract_balances = _current_contract_balances(
            sales_contracts, customer_receipts, outsource_contracts, supplier_payments)
        corrections = _corrections(rows["finance_correction"])
        mold_transfer_receipts = _mold_transfer_receipts(db, project.id)
        cost_impacts = _cost_impacts(db, user, project.id, allowed_tools)
        quality_finance = _quality_finance_context(db, user, project.id, allowed_tools)
        closure_items = _closure_finance_items(db, user, project.id)
        if not quality_finance['customer_acceptance']['visible']:
            skipped.append("客户验收费用与合同变化依据")
        if not quality_finance['supplier_deductions']['visible']:
            skipped.append("供应商扣款责任/结算记录")
        if receipts_skipped:
            skipped.append("客户实际回款确认")
        if skipped:
            limitations.append("未授权或未分配对应财务事实读取范围，未返回：" + "、".join(dict.fromkeys(skipped)))
        if "query_contact_cases" not in allowed_tools:
            limitations.append("未分配工程联络查询工具，未汇总设变费用、扣款或额外工时线索。")
        if "prepare_customer_receipt_confirmation" in allowed_tools:
            limitations.append("可在取得真实销售合同和收款节点后准备客户实际回款确认；该操作仍需本人核对卡片后才写入。")
        if "prepare_customer_receivable_schedule" in allowed_tools:
            limitations.append("可在取得真实销售合同、收款节点和合同依据后准备结构化账期确认；该操作仍需本人核对卡片后才更新节点，不会从条件文字猜测日期。")
        if "prepare_supplier_payment_confirmation" in allowed_tools:
            limitations.append("可在取得已审批供应商付款申请和授权余额后准备供应商实际付款确认；该操作仍需本人核对卡片后才写入。")
        if "prepare_supplier_deduction_settlement" in allowed_tools:
            limitations.append("可在取得供应商、委外合同、工程联络扣款线索和责任/结算依据后准备供应商扣款结算确认；该操作仍需本人核对卡片后才写入。")
        if "prepare_mold_transfer_receipt" in allowed_tools:
            limitations.append("可由财务按客户签收日期准备移模时间登记；该操作仍需本人核对卡片后才写入，且不会形成质量验收结论。")
        analysis = _analysis(
            project, profile, starts, frozen_start_materials, contract_follow_up,
            department_handoffs, sales_contracts, customer_nodes,
            customer_receipts, receivable_schedule, outsource_contracts,
            supplier_payments, contract_balances, corrections,
            mold_transfer_receipts, cost_impacts, closure_items, quality_finance,
            erp_finance_execution,
        )
        correction_options = []
        if 'prepare_finance_correction' in allowed_tools:
            from .finance_correction_tools import workflow_options
            for payment in rows['supplier_payment']:
                request = db.get(m.BusinessSubject, payment['id']) if payment.get('id') else None
                if not request:
                    continue
                options = workflow_options(db, user, project.id, request.category)
                if options:
                    correction_options.append({'payment_request_id':request.id, 'workflows':options})
        analysis['finance_correction_options'] = correction_options
        payment_options = []
        if 'prepare_supplier_payment_request' in allowed_tools or 'prepare_supplier_payment_condition' in allowed_tools:
            from . import supplier_payment_tools as payment
            for contract_row in rows['full_outsource_contract']:
                contract = db.get(m.BusinessSubject, contract_row['id']) if contract_row.get('id') else None
                if not contract or contract.status != 'EFFECTIVE':
                    continue
                try:
                    payment.full_read(db, user, contract)
                    options = payment.workflow_options(db, user, project.id, contract.category) if 'prepare_supplier_payment_request' in allowed_tools else []
                    from domain_packs.mold.erp.commercial.contract_materials import stages
                    payment_options.append({'contract_id':contract.id, 'workflows':options,
                        'can_confirm_condition': 'prepare_supplier_payment_condition' in allowed_tools and access(db,user,'finance.condition',{'project_id':project.id,'category':contract.category}).allowed,
                        'stages':[{'stage_id':stage.id, 'name':stage.name, 'amount':str(stage.amount), 'currency':stage.currency,
                            'condition':stage.condition, 'condition_confirmed':stage.condition_confirmed,
                            'condition_evidence':stage.condition_evidence,
                            'condition_profile':stage.condition_profile or {},
                            'condition_evidence_map':stage.condition_evidence_map or {},
                            'special_approval_reference':stage.special_approval_reference} for stage in stages(db, contract.id)]})
                except DomainError:
                    continue
        analysis['supplier_payment_options'] = payment_options
        finance_delivery = next(
            (item for item in department_handoffs.get("items", [])
             if item.get("role_key") == "FINANCE_OWNER"),
            None,
        )
        model_context = {
            **analysis,
            "project": {
                "id": project.id,
                "row_version": project.row_version,
                "code": project.code,
                "name": project.name,
                "status": project.status,
            },
            "formal_start_materials": [
                start_material_service.frozen_material_model_context(row)
                for row in frozen_start_materials
            ],
            "contract_follow_up": (
                start_material_service.contract_follow_up_model_context(
                    contract_follow_up
                )
            ),
            "finance_handoff": {
                "overall_status": department_handoffs.get("status"),
                "delivery_state": finance_delivery.get("delivery_state") if finance_delivery else None,
                "notification_delivered": bool(
                    finance_delivery
                    and finance_delivery.get("delivery_state") == "DELIVERED"
                ),
                "explicit_receipt_acknowledged": None,
                "recipient_count": finance_delivery.get("recipient_count") if finance_delivery else 0,
                "gaps": department_handoffs.get("gaps", []),
            },
            "derived_status": analysis.get("derived_status", {}),
            "quality_finance_context": quality_finance,
            "gaps": analysis.get("gaps", []),
            "warnings": analysis.get("warnings", []),
        }
        return {
            "resolution": "RESOLVED",
            "data": [
                {
                    "project": _project_card(db, user, project, alternatives or ("项目定位",)),
                    "profile": profile,
                    "analysis": analysis,
                }
            ],
            "model_context": model_context,
            "scope_boundary": {
                "complete": True,
                "scope_key": "finance",
                "write_tools": sorted(
                    tool for tool in FINANCE_PROPOSAL_TOOLS if tool in allowed_tools
                ),
            },
            "source": "agent_db",
            "as_of": now().isoformat(),
            "limitations": limitations,
        }
    if alternatives is None:
        return {"resolution": "NOT_FOUND_OR_FORBIDDEN", "data": [], "source": "agent_db", "as_of": now().isoformat(), "limitations": limitations}
    if alternatives:
        return {
            "resolution": "MULTIPLE_CANDIDATES",
            "data": alternatives,
            "source": "agent_db",
            "as_of": now().isoformat(),
            "limitations": limitations + ["线索命中多个候选项目，请使用项目 ID 或更完整编号后再查询。"],
        }
    return {"resolution": "NOT_FOUND", "data": [], "source": "agent_db", "as_of": now().isoformat(), "limitations": limitations}


router = APIRouter()


@router.get("/api/finance-proposals/{step_id}")
def proposal_status(step_id: str, user=Depends(current_user), db=Depends(get_db)):
    source(db, user, step_id, for_read=True)
    intent = db.scalar(select(m.HumanIntent).where(m.HumanIntent.user_id == user.id,
        m.HumanIntent.action == "finance.execute", m.HumanIntent.resource_id == step_id,
        m.HumanIntent.receipt["status"].as_string().in_(["CONFIRMED", "SUBMITTED"])).order_by(m.HumanIntent.created_at.desc()))
    return {"receipt": intent.receipt if intent else None}


@router.post("/api/finance-proposals/{step_id}/intent")
def intent(step_id: str, user=Depends(current_user), db=Depends(get_db)):
    from domain_packs.mold.erp.core.business import create_intent
    proposal = source(db, user, step_id)
    payload = {"step_id": step_id, "proposal_hash": content_hash(proposal)}
    result = create_intent(db, user, "finance.execute", step_id, payload)
    result["display"] = proposal["display"]
    result["confirmation_policy"] = proposal.get("confirmation_policy")
    db.commit()
    return result
