"""Contract customer mapping, immutable terms and cash timing projections."""
from collections import defaultdict
from decimal import Decimal, InvalidOperation

from sqlalchemy import select

from domain_packs.mold import models as m
from domain_packs.mold.ports.db import now
from domain_packs.mold.ports.errors import DomainError


def customer_classification(customer):
    if not customer:
        return None
    value = (str(customer.rule_key or "") + " " + str(customer.name or "")).casefold()
    if "hisense" in value or "海信" in value:
        return "HISENSE"
    if "haier" in value or "海尔" in value or "海达诚" in value:
        return "HAIER"
    return "OTHER"


def _molds(db, project_id):
    return [
        {
            "id": mold.id,
            "internal_number": mold.internal_number,
            "name": mold.name,
        }
        for _, mold in db.execute(
            select(m.ProjectMold, m.Mold)
            .join(m.Mold, m.Mold.id == m.ProjectMold.mold_id)
            .where(m.ProjectMold.project_id == project_id)
            .order_by(m.Mold.internal_number, m.Mold.id)
        )
    ]


def _intake_orders(db, project_id):
    rows = list(
        db.execute(
            select(m.BidIntakeRevision, m.BidIntakeCase)
            .join(m.BidIntakeCase, m.BidIntakeCase.id == m.BidIntakeRevision.case_id)
            .where(m.BidIntakeCase.project_id == project_id)
            .order_by(
                m.BidIntakeRevision.created_at.desc(),
                m.BidIntakeRevision.version.desc(),
            )
        )
    )
    result = []
    seen = set()
    for revision, case in rows:
        number = revision.external_order_number
        if not number or number in seen:
            continue
        seen.add(number)
        result.append(
            {
                "number": number,
                "revision_id": revision.id,
                "revision_version": revision.version,
                "case_id": case.id,
            }
        )
    return result


def _effective_starts(db, project_id):
    rows = db.scalars(
        select(m.BusinessSubject)
        .where(
            m.BusinessSubject.project_id == project_id,
            m.BusinessSubject.kind == "internal_start",
            m.BusinessSubject.status == "EFFECTIVE",
        )
        .order_by(m.BusinessSubject.created_at.desc(), m.BusinessSubject.id)
    )
    return [
        {"id": row.id, "number": row.number, "revision": row.revision}
        for row in rows
    ]


def preview(db, project, data):
    """Validate customer-specific references and freeze cross-object links."""
    profile = db.get(m.ProjectProfile, project.id)
    customer = db.get(m.Customer, data.customer_id) if data.customer_id else None
    if data.contract_kind == "sales_contract":
        if profile and profile.customer_id and profile.customer_id != data.customer_id:
            raise DomainError(
                "CONTRACT_CUSTOMER_MISMATCH",
                "销售合同客户与项目档案客户不一致，请先核对项目关联",
                409,
            )
        classification = customer_classification(customer)
    else:
        classification = None

    if data.signed_date and data.signed_date > now().date():
        raise DomainError("CONTRACT_SIGNED_DATE_INVALID", "合同签订日期不能在未来", 409)
    if data.signed_date and data.delivery_due_date < data.signed_date:
        raise DomainError(
            "CONTRACT_DELIVERY_DATE_INVALID",
            "合同交付日期不能早于合同签订日期",
            409,
        )
    if data.received_date and data.signed_date and data.received_date < data.signed_date:
        raise DomainError(
            "CONTRACT_RECEIPT_DATE_INVALID",
            "合同原件实际到达日期不能早于合同签订日期",
            409,
        )
    if data.received_date and data.received_date > now().date():
        raise DomainError(
            "CONTRACT_RECEIPT_DATE_INVALID",
            "合同原件实际到达日期不能在未来",
            409,
        )

    orders = _intake_orders(db, project.id)
    known_orders = {row["number"] for row in orders}
    if data.contract_kind == "full_outsource_contract":
        if data.customer_project_number or data.customer_order_number:
            raise DomainError(
                "CONTRACT_CUSTOMER_REFERENCE_NOT_APPLICABLE",
                "整套委外采购合同不应填写客户项目号或客户订单号",
                409,
            )
        reference_type = "SUPPLIER_CONTRACT"
    elif classification == "HISENSE":
        if not data.customer_project_number:
            raise DomainError(
                "HISENSE_PROJECT_NUMBER_REQUIRED",
                "海信销售合同必须填写合同中的客户项目编号并保留核对依据",
                409,
            )
        reference_type = "PROJECT_NUMBER"
    elif classification == "HAIER" and data.document_source == "ELECTRONIC":
        if not data.customer_order_number:
            raise DomainError(
                "HAIER_ORDER_NUMBER_REQUIRED",
                "海尔电子合同必须填写客户订单编号并与中标接收记录核对",
                409,
            )
        if known_orders and data.customer_order_number not in known_orders:
            raise DomainError(
                "CONTRACT_ORDER_MISMATCH",
                "海尔电子合同订单编号与当前项目已确认的客户订单不一致",
                409,
            )
        reference_type = "ORDER_NUMBER"
    elif classification == "HAIER":
        reference_type = "CONTRACT_NUMBER"
    else:
        reference_type = "MANUAL_CONFIRMED"

    molds = _molds(db, project.id)
    if not molds:
        raise DomainError(
            "CONTRACT_MOLD_LINK_REQUIRED",
            "合同提交前必须先核对项目关联的内部模具号",
            409,
        )
    starts = _effective_starts(db, project.id)
    return {
        "customer_rule_key": customer.rule_key if customer else None,
        "customer_classification": classification,
        "customer_reference_type": reference_type,
        "customer_project_number": data.customer_project_number,
        "customer_order_number": data.customer_order_number,
        "known_customer_orders": orders,
        "project": {"id": project.id, "code": project.code, "name": project.name},
        "customer": (
            {"id": customer.id, "code": customer.code, "name": customer.name}
            if customer
            else None
        ),
        "internal_molds": molds,
        "effective_internal_starts": starts,
    }


def create(db, user, subject, data, association_snapshot):
    row = m.ContractBusinessTerms(
        material_version=1,
        contract_subject_id=subject.id,
        signed_date=data.signed_date,
        delivery_due_date=data.delivery_due_date,
        payment_method=data.payment_method,
        customer_rule_key=association_snapshot.get("customer_rule_key"),
        customer_reference_type=association_snapshot["customer_reference_type"],
        customer_project_number=data.customer_project_number,
        customer_order_number=data.customer_order_number,
        mapping_evidence=data.mapping_evidence,
        association_snapshot=association_snapshot,
        recorded_by=user.id,
    )
    db.add(row)
    db.flush()
    return row


def card(db, contract_subject_id):
    row = db.scalar(
        select(m.ContractBusinessTerms)
        .where(m.ContractBusinessTerms.contract_subject_id == contract_subject_id)
        .order_by(m.ContractBusinessTerms.material_version.desc())
        .limit(1)
    )
    if not row:
        return None
    return {
        "signed_date": row.signed_date.isoformat() if row.signed_date else None,
        "delivery_due_date": row.delivery_due_date.isoformat(),
        "payment_method": row.payment_method,
        "customer_rule_key": row.customer_rule_key,
        "customer_reference_type": row.customer_reference_type,
        "customer_project_number": row.customer_project_number,
        "customer_order_number": row.customer_order_number,
        "mapping_evidence": row.mapping_evidence,
        "association_snapshot": row.association_snapshot,
        "recorded_by": row.recorded_by,
        "material_version": row.material_version,
        "attachment_selection": row.attachment_selection,
    }


def _decimal(value):
    try:
        return Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError):
        return None


def _events(records, direction):
    events = []
    for record in records:
        if record.get("status") != "EFFECTIVE":
            continue
        detail = record.get("detail") if isinstance(record.get("detail"), dict) else {}
        for stage in detail.get("stages") or []:
            amount = _decimal(stage.get("amount"))
            currency = stage.get("currency") or detail.get("currency")
            if amount is None or not currency:
                continue
            # A provisional date can exist before finance has confirmed the
            # schedule.  It remains useful audit context, but must not be used
            # to claim a projected cash shortfall until that confirmation is
            # present.
            due_date = (
                stage.get("expected_due_date") or stage.get("trigger_date")
            ) if stage.get("schedule_confirmed") else None
            events.append(
                {
                    "direction": direction,
                    "contract_id": record.get("id"),
                    "contract_number": detail.get("contract_number"),
                    "stage_id": stage.get("id"),
                    "stage_name": stage.get("name"),
                    "amount": str(amount),
                    "currency": currency,
                    "due_date": due_date,
                    "condition": stage.get("condition"),
                    "schedule_confirmed": bool(stage.get("schedule_confirmed")),
                }
            )
    return events


def cash_timing_analysis(sales_records, outsource_records, fallback_payment_terms=None):
    """Compare structured receipts and payments without changing contract facts."""
    inflows = _events(sales_records, "INFLOW")
    outflows = _events(outsource_records, "OUTFLOW")
    all_events = inflows + outflows
    by_currency = defaultdict(list)
    for event in all_events:
        by_currency[event["currency"]].append(event)

    currencies = []
    for currency in sorted(by_currency):
        events = by_currency[currency]
        dated = [event for event in events if event.get("due_date")]
        undated = [event for event in events if not event.get("due_date")]
        daily = defaultdict(lambda: {"inflow": Decimal(0), "outflow": Decimal(0), "events": []})
        for event in dated:
            bucket = daily[str(event["due_date"])]
            bucket["events"].append(event)
            if event["direction"] == "INFLOW":
                bucket["inflow"] += Decimal(event["amount"])
            else:
                bucket["outflow"] += Decimal(event["amount"])
        cumulative = Decimal(0)
        timeline = []
        first_shortfall = None
        for day in sorted(daily):
            bucket = daily[day]
            net = bucket["inflow"] - bucket["outflow"]
            cumulative += net
            item = {
                "date": day,
                "inflow": str(bucket["inflow"]),
                "outflow": str(bucket["outflow"]),
                "net": str(net),
                "cumulative_net": str(cumulative),
                "events": bucket["events"],
            }
            timeline.append(item)
            if cumulative < 0 and first_shortfall is None:
                first_shortfall = {
                    "date": day,
                    "amount": str(-cumulative),
                    "currency": currency,
                }
        currencies.append(
            {
                "currency": currency,
                "timeline": timeline,
                "undated_events": undated,
                "first_projected_shortfall": first_shortfall,
                "has_projected_shortfall": first_shortfall is not None,
            }
        )

    warnings = []
    if outflows and not inflows:
        warnings.append("已有整套委外付款节点，但没有可见的生效销售合同结构化收款节点。")
    if any(item["undated_events"] for item in currencies):
        warnings.append("部分付款节点缺少已确认到期日，仅展示条件，不能据此断言实际资金缺口。")
    if any(item["has_projected_shortfall"] for item in currencies):
        warnings.append("按已确认日期投影，存在供应商付款早于客户收款形成的资金缺口。")
    if not inflows and fallback_payment_terms:
        warnings.append("销售合同收款节点尚未结构化，当前仅保留已确认报价或承接依据中的收款条件文本。")
    if inflows and outflows:
        state = "DATED_RISK" if any(item["has_projected_shortfall"] for item in currencies) else "NO_DATED_RISK"
        if any(item["undated_events"] for item in currencies):
            state = "DATES_INCOMPLETE"
    elif outflows and fallback_payment_terms:
        state = "UNSTRUCTURED_INFLOW_TERMS"
    elif outflows:
        state = "INFLOW_TERMS_MISSING"
    elif inflows:
        state = "NO_OUTSOURCE_PAYMENT_TERMS"
    else:
        state = "NO_STRUCTURED_TERMS"
    return {
        "state": state,
        "currencies": currencies,
        "fallback_customer_payment_terms": fallback_payment_terms,
        "warnings": warnings,
        "semantics": "仅比较经权限过滤的合同节点时序；不改变条款、不确认收付款、不代替采购或财务审批。",
    }
