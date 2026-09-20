from collections import Counter, defaultdict
from datetime import date
from decimal import Decimal
from typing import Literal

from pydantic import Field, ValidationError
from sqlalchemy import or_, select

from agent_core.errors import DomainError
from agent_core.host_ports import host_ports
from agent_core.schemas import StrictModel
from domain_packs.mold.erp.core.contracts import ProjectPlanContextInput, match_strength as _strength
from domain_packs.mold.erp.core.legacy_read_ports import business_subject_data, contact_case_permitted, purchase_order_data
from domain_packs.mold.config import settings as mold_settings


_host = host_ports()
m = _host.models
access = _host.access
fingerprint = _host.fingerprint
predicate = _host.predicate
require = _host.require
select_fields = _host.select_fields
content_hash = _host.content_hash
proposal_confirmation_policy = _host.proposal_confirmation_policy
settings = _host.settings
now = _host.now


DELIVERY_KEYWORDS = ("交付", "出库", "发货", "物流", "签收", "验收", "delivery", "shipment", "acceptance", "logistics")
QUALITY_SOURCES = {"QUALITY_ISSUE", "TRIAL_ISSUE", "ASSEMBLY_ISSUE", "SUPPLIER_QUALITY"}
DELIVERY_LOGISTICS_PROPOSAL_TOOLS = {"prepare_logistics_route", "prepare_logistics_quote"}


class LogisticsRouteProposalInput(StrictModel):
    route_scope: Literal["FIXED", "PROJECT_ACTUAL"]
    project_id: str | None = Field(default=None, max_length=36)
    project_version: int | None = Field(default=None, ge=1)
    route_code: str = Field(min_length=1, max_length=80)
    origin: str = Field(min_length=1, max_length=200)
    destination: str = Field(min_length=1, max_length=200)
    carrier_name: str = Field(min_length=1, max_length=150)
    vehicle_type: str = Field(min_length=1, max_length=80)
    weight_kg: Decimal = Field(gt=0, max_digits=18, decimal_places=3)
    transport_mode: Literal["TRUCK", "EXPRESS", "SEA", "AIR", "RAIL", "OTHER"] = "TRUCK"
    price_unit: str = Field(min_length=1, max_length=40)
    tax_mode: Literal["TAX_INCLUDED", "TAX_EXCLUDED", "UNKNOWN"] = "TAX_INCLUDED"
    valid_from: date
    valid_to: date
    evidence: str = Field(min_length=1, max_length=4000)
    source_ref: str = Field(min_length=1, max_length=120)


class LogisticsQuoteProposalInput(StrictModel):
    route_id: str = Field(min_length=1, max_length=36)
    supplier_id: str | None = Field(default=None, max_length=36)
    unit_price: Decimal = Field(gt=0, max_digits=18, decimal_places=2)
    currency: str = Field(pattern=r"^[A-Z]{3}$")
    valid_from: date
    valid_to: date
    settlement_for_project_id: str | None = Field(default=None, max_length=36)
    project_version: int | None = Field(default=None, ge=1)
    pricing_method: Literal["FIXED_ROUTE", "COMPETITIVE", "NEGOTIATED", "SINGLE_SOURCE"]
    comparison_count: int = Field(ge=1, le=100)
    comparison_summary: str | None = Field(default=None, max_length=4000)
    quote_evidence: str = Field(min_length=1, max_length=4000)
    reconciliation_basis: str = Field(min_length=1, max_length=4000)
    source_ref: str = Field(min_length=1, max_length=120)
    supersedes_quote_id: str | None = Field(default=None, max_length=36)


def logistics_route_schema():
    return LogisticsRouteProposalInput.model_json_schema()


def logistics_quote_schema():
    return LogisticsQuoteProposalInput.model_json_schema()


def parse_logistics_route(arguments):
    try:
        data = LogisticsRouteProposalInput.model_validate(arguments or {})
    except ValidationError as error:
        raise DomainError("INVALID_TOOL_INPUT", "物流路线参数不完整或不符合要求：" + error.errors()[0]["msg"]) from None
    if data.route_scope == "FIXED" and (data.project_id or data.project_version):
        raise DomainError("INVALID_TOOL_INPUT", "固定物流路线不能绑定单个项目或项目版本")
    if data.route_scope == "PROJECT_ACTUAL" and (not data.project_id or not data.project_version):
        raise DomainError("INVALID_TOOL_INPUT", "模具实际路线必须填写项目和当前项目版本")
    if data.valid_to < data.valid_from:
        raise DomainError("INVALID_TOOL_INPUT", "物流路线失效日期不能早于生效日期")
    return data


def parse_logistics_quote(arguments):
    try:
        data = LogisticsQuoteProposalInput.model_validate(arguments or {})
    except ValidationError as error:
        raise DomainError("INVALID_TOOL_INPUT", "物流报价参数不完整或不符合要求：" + error.errors()[0]["msg"]) from None
    validity_days = (data.valid_to - data.valid_from).days
    if validity_days < 0:
        raise DomainError("INVALID_TOOL_INPUT", "物流报价失效日期不能早于生效日期")
    max_valid_days = mold_settings().logistics_quote_max_valid_days
    if validity_days > max_valid_days:
        raise DomainError("LOGISTICS_QUOTE_VALIDITY_TOO_LONG", f"物流报价有效期不能超过 {max_valid_days} 天")
    if data.pricing_method == "COMPETITIVE" and data.comparison_count < 2:
        raise DomainError("LOGISTICS_COMPARISON_REQUIRED", "多家比价必须至少登记两家报价依据")
    if data.pricing_method == "COMPETITIVE" and not (data.comparison_summary or "").strip():
        raise DomainError("LOGISTICS_COMPARISON_REQUIRED", "多家比价必须填写参与方与比较结论摘要")
    if bool(data.settlement_for_project_id) != bool(data.project_version):
        raise DomainError("INVALID_TOOL_INPUT", "本次项目结算价格必须同时填写项目和当前项目版本")
    return data


def _project_card(db, user, project, matched_by=()):
    fields = access(db, user, "project.read", {"project_id": project.id}).fields
    card = select_fields(
        {"id": project.id, "code": project.code, "name": project.name, "status": project.status, "row_version": project.row_version},
        fields,
    )
    card["matched_by"] = sorted(set(matched_by))
    return card


def _visible_projects(db, user):
    rows = list(
        db.scalars(
            select(m.Project)
            .where(predicate(db, user, "project.read", {"project_id": m.Project.id}))
            .order_by(m.Project.code)
            .limit(501)
        )
    )
    return rows[:500], len(rows) > 500


def _resolve(db, user, data: ProjectPlanContextInput, allowed_tools: set[str]):
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
    if "query_purchase_orders" in allowed_tools or "query_orders" in allowed_tools:
        for order in db.scalars(select(m.PurchaseOrder).where(m.PurchaseOrder.project_id.in_(list(by_id))).limit(501)):
            add(order.project_id, order.number, "采购/发货订单号")
            for line in db.scalars(select(m.OrderLine).where(m.OrderLine.order_id == order.id).limit(100)):
                for shipment in db.scalars(select(m.SupplierShipment).where(m.SupplierShipment.order_line_id == line.id).limit(100)):
                    add(order.project_id, shipment.reference, "供应商发货单号")
    if "query_contact_cases" in allowed_tools:
        for case in db.scalars(select(m.ContactCase).where(m.ContactCase.project_id.in_(list(by_id))).limit(501)):
            add(case.project_id, case.title, "工程联络标题")
            add(case.project_id, case.mold_number, "工程联络模具号")
            add(case.project_id, case.product_ref, "工程联络产品")
    if not scores:
        return None, [], truncated
    best = max(scores.values())
    ids = [pid for pid, score in scores.items() if score == best]
    if len(ids) != 1:
        return None, [_project_card(db, user, by_id[pid], reasons[pid]) for pid in ids[:20]], truncated
    return by_id[ids[0]], reasons[ids[0]], truncated


def _profile(db, user, project_id):
    profile = db.get(m.ProjectProfile, project_id)
    if not profile:
        return None
    fields = access(db, user, "project.read", {"project_id": project_id}).fields
    return select_fields(
        {
            "execution_mode": profile.execution_mode,
            "customer_due_date": profile.customer_due_date.isoformat() if profile.customer_due_date else None,
            "settlement_status": profile.settlement_status,
        },
        fields | {"execution_mode", "customer_due_date", "settlement_status"},
    )


def _visible_subjects(db, user, project_id, kind, allowed_tools):
    tool = "query_" + kind
    context_tools = {
        "project_plan": {"query_project_plan_context", "query_delivery_logistics_context"},
        "plan_change": {"query_project_plan_context", "query_delivery_logistics_context"},
        "trial_request": {"query_trial_request", "query_assembly_trial_context"},
        "project_close": {"query_project_closure_context"},
    }
    if tool not in allowed_tools and not (context_tools.get(kind, set()) & allowed_tools):
        return []
    result = []
    for subject in db.scalars(
        select(m.BusinessSubject)
        .where(m.BusinessSubject.project_id == project_id, m.BusinessSubject.kind == kind)
        .order_by(m.BusinessSubject.created_at.desc(), m.BusinessSubject.id)
        .limit(100)
    ):
        try:
            result.append(business_subject_data(db, user, subject))
        except DomainError:
            continue
    return result


def _plan_tasks(records):
    plans = records["project_plan"] + records["plan_change"]
    effective = [row for row in plans if row.get("status") == "EFFECTIVE"]
    active = max(effective, key=lambda row: row.get("created_at", "")) if effective else None
    detail = active.get("detail") if active and isinstance(active.get("detail"), dict) else {}
    result = []
    for task in detail.get("tasks") or []:
        text = (str(task.get("key") or "") + " " + str(task.get("name") or "")).casefold()
        if any(keyword.casefold() in text for keyword in DELIVERY_KEYWORDS):
            result.append(
                {
                    "id": task.get("id"),
                    "key": task.get("key"),
                    "name": task.get("name"),
                    "status": task.get("status"),
                    "planned_start": task.get("planned_start"),
                    "planned_end": task.get("planned_end"),
                    "actual_start": task.get("actual_start"),
                    "actual_end": task.get("actual_end"),
                    "prerequisites": task.get("prerequisites") or [],
                }
            )
    return active, result


def _plan_headers(records):
    return {
        key: [
            {
                "id": row.get("id"),
                "number": row.get("number"),
                "status": row.get("status"),
                "created_at": row.get("created_at"),
            }
            for row in rows
        ]
        for key, rows in records.items()
    }


def _order_rows(db, user, project_id, allowed_tools):
    if "query_purchase_orders" not in allowed_tools and "query_orders" not in allowed_tools:
        return []
    result = []
    q = select(m.PurchaseOrder).where(m.PurchaseOrder.project_id == project_id).order_by(m.PurchaseOrder.created_at.desc()).limit(100)
    for order in db.scalars(q):
        try:
            result.append(purchase_order_data(db, user, order))
        except DomainError:
            continue
    return result


def _order_headers(orders):
    return [
        {
            "id": order.get("id"),
            "number": order.get("number"),
            "status": order.get("status"),
            "currency": order.get("currency"),
            "line_count": len(order.get("lines") or []),
        }
        for order in orders
    ]


def _warehouse_name(db, warehouse_id):
    warehouse = db.get(m.Warehouse, warehouse_id)
    return warehouse.name if warehouse else None


def _augment_order_receipts(db, user, orders):
    for order in orders:
        for line in order.get("lines") or []:
            for shipment in line.get("shipments") or []:
                receipts = []
                for receipt in db.scalars(select(m.GoodsReceipt).where(m.GoodsReceipt.shipment_id == shipment.get("id")).limit(100)):
                    if not access(db, user, "warehouse.read", {"warehouse_id": receipt.warehouse_id}).allowed:
                        continue
                    inspection = db.scalar(select(m.ReceiptInspection).where(m.ReceiptInspection.receipt_id == receipt.id))
                    receipts.append(
                        {
                            "id": receipt.id,
                            "quantity": str(receipt.quantity),
                            "warehouse_id": receipt.warehouse_id,
                            "warehouse_name": _warehouse_name(db, receipt.warehouse_id),
                            "reference": receipt.reference,
                            "evidence": receipt.evidence,
                            "inspection": (
                                {
                                    "id": inspection.id,
                                    "accepted_quantity": str(inspection.accepted_quantity),
                                    "rejected_quantity": str(inspection.rejected_quantity),
                                    "evidence": inspection.evidence,
                                }
                                if inspection
                                else None
                            ),
                        }
                    )
                shipment["receipts"] = receipts


def _shipment_tracking(orders):
    totals = Counter()
    lines = []
    for order in orders:
        for line in order.get("lines") or []:
            quantity = Decimal(str(line.get("quantity") or "0"))
            shipped = Decimal(str(line.get("shipped_quantity") or "0"))
            receipts = [
                receipt
                for shipment in line.get("shipments") or []
                for receipt in shipment.get("receipts") or []
            ]
            inspections = [receipt.get("inspection") for receipt in receipts if receipt.get("inspection")]
            accepted = sum((Decimal(str(item.get("accepted_quantity") or "0")) for item in inspections), Decimal(0))
            rejected = sum((Decimal(str(item.get("rejected_quantity") or "0")) for item in inspections), Decimal(0))
            totals["order_lines"] += 1
            totals["supplier_shipments"] += len(line.get("shipments") or [])
            totals["goods_receipts"] += len(receipts)
            totals["receipt_inspections"] += len(inspections)
            if shipped < quantity:
                totals["unshipped_lines"] += 1
            if receipts and accepted + rejected < shipped:
                totals["uninspected_received_lines"] += 1
            if rejected > 0:
                totals["rejected_receipt_lines"] += 1
            if line.get("exceptions"):
                totals["exception_lines"] += 1
            lines.append(
                {
                    "order_id": order.get("id"),
                    "order_number": order.get("number"),
                    "order_status": order.get("status"),
                    "line_id": line.get("id"),
                    "material_id": line.get("material_id"),
                    "material_name": line.get("material_name"),
                    "category": line.get("category"),
                    "quantity": line.get("quantity"),
                    "unit": line.get("unit"),
                    "agreed_ship_date": line.get("agreed_ship_date"),
                    "shipped_quantity": str(shipped),
                    "accepted_quantity": str(accepted),
                    "rejected_quantity": str(rejected),
                    "shipments": line.get("shipments") or [],
                    "exceptions": line.get("exceptions") or [],
                }
            )
    return {"totals": dict(totals), "lines": lines[:100]}


def _stock_movements(db, user, project_id):
    rows = []
    q = (
        select(m.StockMovement, m.StockBalance, m.Warehouse, m.Material)
        .join(m.StockBalance, m.StockMovement.balance_id == m.StockBalance.id)
        .join(m.Warehouse, m.StockBalance.warehouse_id == m.Warehouse.id)
        .join(m.Material, m.StockBalance.material_id == m.Material.id)
        .where(m.StockBalance.project_id == project_id)
        .order_by(m.StockMovement.created_at.desc())
        .limit(100)
    )
    for movement, balance, warehouse, material in db.execute(q):
        if not access(db, user, "warehouse.read", {"warehouse_id": warehouse.id}).allowed:
            continue
        rows.append(
            {
                "id": movement.id,
                "warehouse_id": warehouse.id,
                "warehouse_name": warehouse.name,
                "material_id": material.id,
                "material_code": material.code,
                "material_name": material.name,
                "quantity": str(movement.quantity),
                "kind": movement.kind,
                "source_key": movement.source_key,
                "evidence": movement.evidence,
                "created_at": movement.created_at.isoformat() if movement.created_at else None,
            }
        )
    return rows


def _trial_results(trials):
    rows = []
    for trial in trials:
        detail = trial.get("detail") if isinstance(trial.get("detail"), dict) else {}
        for result in detail.get("results") or []:
            rows.append(
                {
                    "trial_id": trial.get("id"),
                    "trial_number": trial.get("number"),
                    "passed": result.get("passed"),
                    "actual_date": result.get("actual_date"),
                    "evidence": result.get("evidence"),
                    "findings": result.get("findings"),
                }
            )
    return rows


def _closure_items(db, user, project_id, allowed_tools):
    if "query_project_closure_context" not in allowed_tools:
        return []
    if not access(db, user, "project_close.read", {"project_id": project_id}).allowed:
        return []
    cases = list(
        db.scalars(
            select(m.ProjectClosureCase)
            .where(m.ProjectClosureCase.project_id == project_id)
            .order_by(m.ProjectClosureCase.created_at.desc(), m.ProjectClosureCase.id)
            .limit(20)
        )
    )
    rows = []
    for case in cases:
        for item in db.scalars(select(m.ProjectClosureItem).where(m.ProjectClosureItem.case_id == case.id).limit(100)):
            if any(token in (item.item_key + item.label) for token in ("DELIVERY", "ACCEPTANCE", "QUALITY", "发货", "交付", "验收", "质量")):
                rows.append(
                    {
                        "case_id": case.id,
                        "mode": case.mode,
                        "case_status": case.status,
                        "item_key": item.item_key,
                        "label": item.label,
                        "status": item.status,
                        "result": item.result,
                        "evidence": item.evidence,
                        "source_system": item.source_system,
                        "source_ref": item.source_ref,
                    }
                )
    return rows[:100]


def _contact_issues(db, user, project_id, allowed_tools):
    if "query_contact_cases" not in allowed_tools:
        return []
    issues = []
    q = (
        select(m.ContactCase)
        .where(m.ContactCase.project_id == project_id, predicate(db, user, "contact.read", {"project_id": m.ContactCase.project_id, "category": m.ContactCase.category}))
        .order_by(m.ContactCase.created_at.desc(), m.ContactCase.id)
        .limit(100)
    )
    for case in db.scalars(q):
        if not contact_case_permitted(db, user, "read", case):
            continue
        text = (case.title or "") + (case.current_stage or "") + (case.problem_source or "")
        tasks = []
        for task in db.scalars(select(m.ContactTask).where(m.ContactTask.case_id == case.id, m.ContactTask.status != "CANCELLED").limit(50)):
            task_text = (task.title or "") + (task.affected_type or "") + (task.affected_ref or "") + (task.impact_description or "")
            if any(keyword in task_text for keyword in ("发货", "物流", "签收", "验收", "出库", "质量")):
                tasks.append(
                    {
                        "id": task.id,
                        "title": task.title,
                        "status": task.status,
                        "affected_type": task.affected_type,
                        "affected_ref": task.affected_ref,
                        "planned_action": task.planned_action,
                        "actual_completed_at": task.actual_completed_at.isoformat() if task.actual_completed_at else None,
                        "delivery_impact_days": task.delivery_impact_days,
                    }
                )
        if case.problem_source in QUALITY_SOURCES or any(keyword in text for keyword in ("发货", "物流", "签收", "验收", "出库", "质量")) or tasks:
            issues.append(
                {
                    "id": case.id,
                    "title": case.title,
                    "collaboration_status": "CLOSED" if case.closed_at else "HISTORY_RECORD" if case.mode == "HISTORY" else "OPEN",
                    "problem_source": case.problem_source,
                    "current_stage": case.current_stage,
                    "change_type": case.change_type,
                    "urgency": case.urgency,
                    "tasks": tasks[:20],
                }
            )
    return issues[:20]


def _logistics_pricing(db, project_id):
    today = now().date()
    rows = []
    routes = []
    seen_routes = set()
    q = (
        select(m.LogisticsRoute, m.LogisticsQuote)
        .outerjoin(m.LogisticsQuote, m.LogisticsQuote.route_id == m.LogisticsRoute.id)
        .where(
            m.LogisticsRoute.active.is_(True),
            or_(m.LogisticsRoute.project_id.is_(None), m.LogisticsRoute.project_id == project_id),
        )
        .order_by(m.LogisticsQuote.valid_from.desc(), m.LogisticsQuote.created_at.desc(), m.LogisticsQuote.id)
        .limit(100)
    )
    for route, quote in db.execute(q):
        route_is_valid_now = (
            (route.valid_from is None or route.valid_from <= today)
            and (route.valid_to is None or route.valid_to >= today)
        )
        route_card = {
            "id": route.id,
            "project_id": route.project_id,
            "route_code": route.route_code,
            "origin": route.origin,
            "destination": route.destination,
            "carrier_name": route.carrier_name,
            "vehicle_type": route.vehicle_type,
            "weight_kg": str(route.weight_kg) if route.weight_kg is not None else None,
            "transport_mode": route.transport_mode,
            "price_unit": route.price_unit,
            "tax_mode": route.tax_mode,
            "valid_from": route.valid_from.isoformat() if route.valid_from else None,
            "valid_to": route.valid_to.isoformat() if route.valid_to else None,
            "evidence": route.evidence,
            "source_ref": route.source_ref,
            "confirmed_by": route.confirmed_by,
            "confirmed_at": route.confirmed_at.isoformat() if route.confirmed_at else None,
            "is_valid_now": route_is_valid_now,
        }
        if route.id not in seen_routes:
            routes.append(route_card)
            seen_routes.add(route.id)
        if quote is None:
            continue
        is_valid_now = route_is_valid_now and quote.status == "EFFECTIVE" and quote.valid_from <= today <= quote.valid_to
        is_settlement = quote.settlement_for_project_id == project_id
        rows.append(
            {
                "route": route_card,
                "quote": {
                    "id": quote.id,
                    "supplier_id": quote.supplier_id,
                    "unit_price": str(quote.unit_price),
                    "currency": quote.currency,
                    "valid_from": quote.valid_from.isoformat(),
                    "valid_to": quote.valid_to.isoformat(),
                    "status": quote.status,
                    "settlement_for_project_id": quote.settlement_for_project_id,
                    "quote_evidence": quote.quote_evidence,
                    "approved_by": quote.approved_by,
                    "approved_at": quote.approved_at.isoformat() if quote.approved_at else None,
                    "pricing_method": quote.pricing_method,
                    "comparison_count": quote.comparison_count,
                    "comparison_summary": quote.comparison_summary,
                    "reconciliation_basis": quote.reconciliation_basis,
                    "source_ref": quote.source_ref,
                    "created_by": quote.created_by,
                    "supersedes_quote_id": quote.supersedes_quote_id,
                    "is_valid_now": is_valid_now,
                    "is_settlement_price_for_project": is_settlement,
                },
            }
        )
    effective = [row for row in rows if row["quote"]["is_valid_now"]]
    settlement = [row for row in effective if row["quote"]["is_settlement_price_for_project"]]
    open_reviews = [row for row in rows if row["quote"]["status"] in {"DRAFT", "SUBMITTED"}]
    expired = [row for row in rows if row["quote"]["status"] == "EXPIRED" or row["quote"]["valid_to"] < today.isoformat()]
    current_routes = [route for route in routes if route["is_valid_now"]]
    return {
        "routes": routes[:50],
        "current_routes": current_routes[:50],
        "effective_quotes": effective[:20],
        "settlement_price_candidates": settlement[:20],
        "open_price_reviews": open_reviews[:20],
        "expired_quotes": expired[:20],
        "derived_status": {
            "has_route": bool(current_routes),
            "has_route_history": bool(routes),
            "has_effective_quote": bool(effective),
            "has_project_settlement_price": bool(settlement),
            "has_open_price_review": bool(open_reviews),
        },
    }


def _customer_delivery_acceptance(db, user, project_id, allowed_tools):
    signatures = []
    for row in db.scalars(
        select(m.CustomerDeliverySignature)
        .where(m.CustomerDeliverySignature.project_id == project_id)
        .order_by(m.CustomerDeliverySignature.signed_date.desc(), m.CustomerDeliverySignature.created_at.desc(), m.CustomerDeliverySignature.id)
        .limit(50)
    ):
        signatures.append(
            {
                "id": row.id,
                "logistics_route_id": row.logistics_route_id,
                "shipment_reference": row.shipment_reference,
                "signed_date": row.signed_date.isoformat(),
                "signer_name": row.signer_name,
                "sign_status": row.sign_status,
                "move_type": row.move_type,
                "evidence": row.evidence,
                "recorded_by": row.recorded_by,
            }
        )

    can_read_acceptance = "query_project_closure_context" in allowed_tools and access(db, user, "project_close.read", {"project_id": project_id}).allowed
    acceptance_records = []
    if can_read_acceptance:
        for row in db.scalars(
            select(m.CustomerAcceptanceRecord)
            .where(m.CustomerAcceptanceRecord.project_id == project_id)
            .order_by(m.CustomerAcceptanceRecord.accepted_date.desc(), m.CustomerAcceptanceRecord.created_at.desc(), m.CustomerAcceptanceRecord.id)
            .limit(50)
        ):
            acceptance_records.append(
                {
                    "id": row.id,
                    "signature_id": row.signature_id,
                    "acceptance_type": row.acceptance_type,
                    "result": row.result,
                    "accepted_date": row.accepted_date.isoformat(),
                    "issue_description": row.issue_description,
                    "responsibility": row.responsibility,
                    "corrective_due_date": row.corrective_due_date.isoformat() if row.corrective_due_date else None,
                    "contact_case_id": row.contact_case_id,
                    "supplier_id": row.supplier_id,
                    "deduction_amount": str(row.deduction_amount) if row.deduction_amount is not None else None,
                    "currency": row.currency,
                    "schedule_impact_days": row.schedule_impact_days,
                    "contract_change_required": row.contract_change_required,
                    "evidence": row.evidence,
                    "confirmed_by": row.confirmed_by,
                }
            )

    signed = [row for row in signatures if row["sign_status"] == "SIGNED"]
    passed = [row for row in acceptance_records if row["result"] in {"PASSED", "CONDITIONALLY_PASSED"}]
    failed = [row for row in acceptance_records if row["result"] == "FAILED"]
    rechecks = [row for row in acceptance_records if row["acceptance_type"] == "RECHECK"]
    recheck_passed = [row for row in rechecks if row["result"] in {"PASSED", "CONDITIONALLY_PASSED"}]
    deductions = [row for row in acceptance_records if row["deduction_amount"] is not None]
    return {
        "signatures": signatures,
        "acceptance_records": acceptance_records,
        "visibility": {
            "signature_records_visible": True,
            "acceptance_records_visible": can_read_acceptance,
        },
        "derived_status": {
            "has_customer_signature": bool(signed),
            "has_customer_acceptance": bool(passed),
            "has_failed_customer_acceptance": bool(failed),
            "has_recheck_record": bool(rechecks),
            "has_recheck_passed": bool(recheck_passed),
            "has_acceptance_deduction": bool(deductions),
            "has_contract_change_required": any(row["contract_change_required"] for row in acceptance_records),
            "schedule_impact_days_total": sum(int(row["schedule_impact_days"] or 0) for row in acceptance_records),
        },
    }


def _analysis(project, profile, active_plan, delivery_tasks, shipment_tracking, stock_movements, trials, closure_items, contacts, logistics_pricing, customer_delivery_acceptance):
    totals = shipment_tracking["totals"]
    trial_passed = [row for row in trials if row.get("passed") is True]
    trial_failed = [row for row in trials if row.get("passed") is False]
    customer_acceptance_done = [row for row in closure_items if "ACCEPTANCE" in row.get("item_key", "") and row.get("status") == "DONE"]
    delivery_done = [row for row in closure_items if "DELIVERY" in row.get("item_key", "") and row.get("status") == "DONE"]
    open_contacts = [row for row in contacts if row.get("collaboration_status") != "CLOSED"]
    stock_out = [row for row in stock_movements if Decimal(str(row.get("quantity") or "0")) < 0]
    customer_status = customer_delivery_acceptance["derived_status"]
    has_customer_acceptance = bool(customer_acceptance_done) or customer_status["has_customer_acceptance"]

    gaps = []
    warnings = []
    if not active_plan:
        warnings.append("当前可见范围未见有效项目计划，无法核对交付/出库/客户验收节点。")
    if not delivery_tasks:
        gaps.append("未见明确的交付、出库、发货、客户签收或验收计划节点。")
    if not totals.get("supplier_shipments"):
        gaps.append("未见当前可见正式订单的供应商发货记录；不能据此判断实物已交付。")
    if totals.get("supplier_shipments") and not totals.get("goods_receipts"):
        gaps.append("已有供应商发货记录，但未见仓库签收/收货回执。")
    if totals.get("goods_receipts") and not totals.get("receipt_inspections"):
        gaps.append("已有收货记录，但未见入库检验或出厂质量检验结论。")
    if totals.get("rejected_receipt_lines"):
        warnings.append("存在收货检验不合格数量，需关联退换货、扣款、整改或工程联络处理。")
    if trial_failed:
        warnings.append("存在试模未通过结果，不能进入出厂验收或客户验收结论。")
    if trial_passed:
        warnings.append("试模通过只代表试模结论，不等于客户签收、客户验收或项目关闭。")
    if stock_out:
        warnings.append("存在库存出库类移动记录；仍需核对对应发货物流单、客户签收和客户验收材料。")
    if open_contacts:
        warnings.append("存在未关闭质量、交付、物流或验收相关工程联络事项，不能认定整改闭环完成。")
    if not delivery_done:
        gaps.append("未见结项/归档清单中的交付或发货完成依据。")
    if not customer_status["has_customer_signature"]:
        gaps.append("未见客户签收记录；客户签收日期不能由供应商发货、仓库收货或出库移动推断。")
    if customer_status["has_customer_signature"] and not has_customer_acceptance:
        gaps.append("已有客户签收记录，但未见客户质量验收通过或有条件通过依据。")
    if not has_customer_acceptance:
        gaps.append("未见客户签收与客户质量验收分别确认的正式依据。")
    if customer_status["has_failed_customer_acceptance"] and not customer_status["has_recheck_passed"]:
        warnings.append("存在客户验收未通过记录，未见复验通过；需继续跟踪问题、责任、整改期限和复验结果。")
    if customer_status["has_acceptance_deduction"]:
        warnings.append("客户验收记录涉及费用扣款，需同步财务/合同/供应商结算依据。")
    if customer_status["has_contract_change_required"]:
        warnings.append("客户验收记录要求合同变化，需同步合同变更或客户确认材料。")
    if customer_status["schedule_impact_days_total"]:
        warnings.append("客户验收记录存在交期影响天数，需与计划变更或客户交期确认联动。")
    pricing_status = logistics_pricing["derived_status"]
    if not pricing_status["has_route"]:
        gaps.append("未见结构化固定物流路线、承运商车型、计价单位、含税方式和有效期。")
    elif not pricing_status["has_effective_quote"]:
        gaps.append("已登记物流路线，但未见当前有效的物流报价。")
    if pricing_status["has_effective_quote"] and not pricing_status["has_project_settlement_price"]:
        warnings.append("存在有效物流报价，但尚未明确本项目本次结算价格；费用对账仍需按项目确认。")
    if pricing_status["has_open_price_review"]:
        warnings.append("存在未完成的物流报价审批，正式结算价格可能即将变化。")

    return {
        "active_plan": (
            {
                "id": active_plan.get("id"),
                "number": active_plan.get("number"),
                "status": active_plan.get("status"),
                "created_at": active_plan.get("created_at"),
            }
            if active_plan
            else None
        ),
        "delivery_plan_tasks": delivery_tasks[:50],
        "shipment_tracking": shipment_tracking,
        "stock_movements": stock_movements[:100],
        "trial_results": trials[:50],
        "closure_delivery_acceptance_items": closure_items[:100],
        "delivery_quality_contacts": contacts,
        "logistics_pricing": logistics_pricing,
        "customer_delivery_acceptance": customer_delivery_acceptance,
        "gaps": gaps,
        "warnings": warnings,
        "derived_status": {
            "project_status": project.status,
            "execution_mode": (profile or {}).get("execution_mode"),
            "has_effective_plan": bool(active_plan),
            "has_delivery_plan_node": bool(delivery_tasks),
            "has_supplier_shipment": bool(totals.get("supplier_shipments")),
            "has_goods_receipt": bool(totals.get("goods_receipts")),
            "has_receipt_inspection": bool(totals.get("receipt_inspections")),
            "has_rejected_receipt": bool(totals.get("rejected_receipt_lines")),
            "has_stock_out_movement": bool(stock_out),
            "has_trial_passed": bool(trial_passed),
            "has_trial_failed": bool(trial_failed),
            "has_customer_signature": customer_status["has_customer_signature"],
            "has_customer_acceptance": has_customer_acceptance,
            "has_failed_customer_acceptance": customer_status["has_failed_customer_acceptance"],
            "has_customer_recheck_passed": customer_status["has_recheck_passed"],
            "has_customer_acceptance_deduction": customer_status["has_acceptance_deduction"],
            "has_customer_acceptance_contract_change": customer_status["has_contract_change_required"],
            "has_open_delivery_or_quality_issue": bool(open_contacts),
            "has_structured_logistics_price": pricing_status["has_effective_quote"],
            "has_project_logistics_settlement_price": pricing_status["has_project_settlement_price"],
        },
    }


def query(db, user, data: ProjectPlanContextInput, allowed_tools: set[str]):
    project, alternatives, truncated = _resolve(db, user, data, allowed_tools)
    limitations = [
        "只读取当前用户可见项目；订单、仓库、试模、结项和工程联络材料分别受对应工具与权限约束。",
        "本工具只核对交付、物流、质量、签收和验收上下文，不创建物流路线、不维护报价、不确认发货、不确认客户签收或验收。",
        "采购发货、仓库收货、入库检验、试模通过、客户签收和客户验收是不同事实，不能相互替代。",
    ]
    if truncated:
        limitations.append("最多检查前500个可见项目，结果可能未覆盖全部可见范围。")
    if project:
        records = {
            "project_plan": _visible_subjects(db, user, project.id, "project_plan", allowed_tools)[:20],
            "plan_change": _visible_subjects(db, user, project.id, "plan_change", allowed_tools)[:20],
        }
        active_plan, delivery_tasks = _plan_tasks(records)
        orders = _order_rows(db, user, project.id, allowed_tools)
        _augment_order_receipts(db, user, orders)
        shipment_tracking = _shipment_tracking(orders)
        stock_movements = _stock_movements(db, user, project.id)
        trials = _trial_results(_visible_subjects(db, user, project.id, "trial_request", allowed_tools))
        closure_items = _closure_items(db, user, project.id, allowed_tools)
        contacts = _contact_issues(db, user, project.id, allowed_tools)
        logistics_pricing = _logistics_pricing(db, project.id)
        customer_delivery_acceptance = _customer_delivery_acceptance(db, user, project.id, allowed_tools)
        profile = _profile(db, user, project.id)
        skipped = []
        if not records["project_plan"] and not records["plan_change"]:
            skipped.append("项目计划/计划变更")
        if "query_purchase_orders" not in allowed_tools and "query_orders" not in allowed_tools:
            skipped.append("正式采购订单/供应商发货/收货跟踪")
        if "query_trial_request" not in allowed_tools and "query_assembly_trial_context" not in allowed_tools:
            skipped.append("试模结果")
        if not customer_delivery_acceptance["visibility"]["acceptance_records_visible"]:
            skipped.append("客户验收/复验/扣款记录")
        if "query_project_closure_context" not in allowed_tools:
            skipped.append("项目结项/交付验收清单")
        if "query_contact_cases" not in allowed_tools:
            skipped.append("工程联络异常与整改")
        if skipped:
            limitations.append("未分配对应查询工具或权限，未返回：" + "、".join(skipped))
        return {
            "resolution": "RESOLVED",
            "data": [
                {
                    "project": _project_card(db, user, project, alternatives or ("项目定位",)),
                    "profile": profile,
                    "project_plans": _plan_headers(records)["project_plan"],
                    "plan_changes": _plan_headers(records)["plan_change"],
                    "purchase_orders": _order_headers(orders),
                    "analysis": _analysis(project, profile, active_plan, delivery_tasks, shipment_tracking, stock_movements, trials, closure_items, contacts, logistics_pricing, customer_delivery_acceptance),
                }
            ],
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


def preview_logistics_route(db, user, data: LogisticsRouteProposalInput):
    project = None
    scope = {}
    if data.project_id:
        project = db.get(m.Project, data.project_id)
        if not project:
            raise DomainError("NOT_FOUND", "项目不存在", 404)
        require(db, user, "project.read", {"project_id": project.id})
        scope = {"project_id": project.id}
        if project.row_version != data.project_version:
            raise DomainError("VERSION_CONFLICT", "项目状态已变化，请重新查询后准备物流路线", 409)
    require(db, user, "warehouse.configure", scope)
    if db.scalar(select(m.LogisticsRoute.id).where(m.LogisticsRoute.route_code == data.route_code)):
        raise DomainError("LOGISTICS_ROUTE_CODE_EXISTS", "物流路线编号已存在，请使用新的版本编号", 409)
    if db.scalar(select(m.LogisticsRoute.id).where(m.LogisticsRoute.source_ref == data.source_ref)):
        raise DomainError("LOGISTICS_ROUTE_SOURCE_DUPLICATE", "该物流路线来源已登记", 409)
    display = {
        "操作": "确认物流路线",
        "路线范围": "固定物流路线" if data.route_scope == "FIXED" else "模具项目实际路线",
        "项目": (project.code + " · " + project.name) if project else "全局固定路线",
        "项目版本": project.row_version if project else "不适用",
        "路线编号": data.route_code,
        "出发地": data.origin,
        "接收地": data.destination,
        "承运商": data.carrier_name,
        "车型": data.vehicle_type,
        "路线重量(kg)": str(data.weight_kg),
        "运输方式": data.transport_mode,
        "计价单位": data.price_unit,
        "含税方式": data.tax_mode,
        "有效期": data.valid_from.isoformat() + " 至 " + data.valid_to.isoformat(),
        "来源引用": data.source_ref,
        "确认依据": data.evidence,
        "说明": "本人确认后仅登记仓库已核对的物流路线；不会确认发货、客户签收、物流报价或费用结算。",
    }
    return project, display


def create_logistics_route(db, user, data: LogisticsRouteProposalInput):
    project, _ = preview_logistics_route(db, user, data)
    row = m.LogisticsRoute(
        project_id=project.id if project else None,
        route_code=data.route_code,
        origin=data.origin,
        destination=data.destination,
        carrier_name=data.carrier_name,
        vehicle_type=data.vehicle_type,
        weight_kg=data.weight_kg,
        transport_mode=data.transport_mode,
        price_unit=data.price_unit,
        tax_mode=data.tax_mode,
        valid_from=data.valid_from,
        valid_to=data.valid_to,
        active=True,
        evidence=data.evidence,
        source_ref=data.source_ref,
        confirmed_by=user.id,
        confirmed_at=now(),
    )
    db.add(row)
    db.flush()
    return row


def preview_logistics_quote(db, user, data: LogisticsQuoteProposalInput):
    route = db.get(m.LogisticsRoute, data.route_id)
    if not route or not route.active:
        raise DomainError("LOGISTICS_ROUTE_NOT_FOUND", "物流路线不存在或已停用", 404)
    today = now().date()
    if ((route.valid_from and route.valid_from > today)
            or (route.valid_to and route.valid_to < today)):
        raise DomainError("LOGISTICS_ROUTE_NOT_EFFECTIVE", "物流路线当前不在有效期内", 409)
    project = None
    scoped_project_id = data.settlement_for_project_id or route.project_id
    scope = {"project_id": scoped_project_id, "category": "logistics"} if scoped_project_id else {}
    require(db, user, "warehouse.read", {"project_id": scoped_project_id} if scoped_project_id else {})
    require(db, user, "purchase_price.approve", scope)
    if data.settlement_for_project_id:
        project = db.get(m.Project, data.settlement_for_project_id)
        if not project:
            raise DomainError("NOT_FOUND", "结算价格项目不存在", 404)
        require(db, user, "project.read", {"project_id": project.id})
        if project.row_version != data.project_version:
            raise DomainError("VERSION_CONFLICT", "项目状态已变化，请重新查询后准备本次结算价格", 409)
        if route.project_id and route.project_id != project.id:
            raise DomainError("LOGISTICS_ROUTE_PROJECT_MISMATCH", "项目实际路线不属于本次结算项目", 409)
    supplier = None
    if data.supplier_id:
        supplier = db.get(m.Supplier, data.supplier_id)
        if not supplier or not supplier.active:
            raise DomainError("SUPPLIER_INVALID", "物流报价供应商不存在或已停用", 409)
    if db.scalar(select(m.LogisticsQuote.id).where(
        m.LogisticsQuote.route_id == route.id,
        m.LogisticsQuote.source_ref == data.source_ref,
    )):
        raise DomainError("LOGISTICS_QUOTE_SOURCE_DUPLICATE", "该物流报价来源已登记", 409)
    overlaps = list(db.scalars(select(m.LogisticsQuote).where(
        m.LogisticsQuote.route_id == route.id,
        m.LogisticsQuote.status == "EFFECTIVE",
        m.LogisticsQuote.settlement_for_project_id == data.settlement_for_project_id,
        m.LogisticsQuote.valid_from <= data.valid_to,
        m.LogisticsQuote.valid_to >= data.valid_from,
    )))
    replaced_quote = None
    if overlaps:
        if len(overlaps) != 1 or overlaps[0].id != data.supersedes_quote_id:
            raise DomainError(
                "LOGISTICS_QUOTE_EFFECTIVE_OVERLAP",
                "该路线和结算范围已存在有效期重叠的生效报价；价格变化时必须明确替换的报价 ID",
                409,
            )
        replaced_quote = overlaps[0]
    elif data.supersedes_quote_id:
        raise DomainError("LOGISTICS_QUOTE_SUPERSEDES_INVALID", "指定的原报价不是当前范围内重叠的生效报价", 409)
    display = {
        "操作": "审批生效物流报价" if not project else "确认项目本次物流结算价格",
        "路线": route.route_code + " · " + route.origin + " → " + route.destination,
        "承运商": route.carrier_name,
        "车型/运输方式": route.vehicle_type + " / " + route.transport_mode,
        "计价单位": route.price_unit,
        "含税方式": route.tax_mode,
        "报价供应商": supplier.name if supplier else "未关联供应商主数据",
        "单价": f"{data.unit_price:.2f} {data.currency}",
        "有效期": data.valid_from.isoformat() + " 至 " + data.valid_to.isoformat(),
        "有效天数": (data.valid_to - data.valid_from).days + 1,
        "计价方式": data.pricing_method,
        "比价数量": data.comparison_count,
        "比价摘要": data.comparison_summary or "不适用",
        "结算项目": (project.code + " · " + project.name) if project else "通用有效报价",
        "项目版本": project.row_version if project else "不适用",
        "来源引用": data.source_ref,
        "替换原报价": replaced_quote.id if replaced_quote else "无",
        "报价/询比议价依据": data.quote_evidence,
        "对账依据": data.reconciliation_basis,
        "说明": "本人确认即以当前采购价格审批权限登记生效报价；仅形成物流价格依据，不确认发货、签收、付款或财务对账完成。",
    }
    return route, supplier, project, replaced_quote, display


def create_logistics_quote(db, user, data: LogisticsQuoteProposalInput):
    route, supplier, project, replaced_quote, _ = preview_logistics_quote(db, user, data)
    if replaced_quote:
        replaced_quote.status = "CANCELLED"
    row = m.LogisticsQuote(
        route_id=route.id,
        supplier_id=supplier.id if supplier else None,
        unit_price=data.unit_price,
        currency=data.currency,
        valid_from=data.valid_from,
        valid_to=data.valid_to,
        status="EFFECTIVE",
        settlement_for_project_id=project.id if project else None,
        quote_evidence=data.quote_evidence,
        approved_by=user.id,
        approved_at=now(),
        pricing_method=data.pricing_method,
        comparison_count=data.comparison_count,
        comparison_summary=data.comparison_summary,
        reconciliation_basis=data.reconciliation_basis,
        source_ref=data.source_ref,
        created_by=user.id,
        supersedes_quote_id=replaced_quote.id if replaced_quote else None,
    )
    db.add(row)
    db.flush()
    return row


def execute_delivery_logistics_tool(db, user, key, arguments, run=None):
    if key == "prepare_logistics_route":
        data = parse_logistics_route(arguments)
        _, display = preview_logistics_route(db, user, data)
        kind = "logistics_route"
        action = "confirm_logistics_route"
        limitation = "仅准备仓库核对后的物流路线登记；本人确认后才写入，不确认报价、发货、签收或验收。"
    elif key == "prepare_logistics_quote":
        data = parse_logistics_quote(arguments)
        _, _, _, _, display = preview_logistics_quote(db, user, data)
        kind = "logistics_quote"
        action = "confirm_logistics_quote"
        limitation = "仅准备物流报价或项目结算价格确认；本人确认后才登记生效价格，不执行发货、付款或财务对账。"
    else:
        raise DomainError("TOOL_UNKNOWN", "工具未实现", 403)
    proposal = {
        "kind": kind,
        "action": action,
        "requires_approval": False,
        "input": data.model_dump(mode="json"),
        "display": display,
        "confirmation_policy": proposal_confirmation_policy(run, requires_approval=False),
    }
    return {
        "data": [],
        "source": "agent_proposal",
        "as_of": now().isoformat(),
        "proposal": proposal,
        "limitations": [limitation],
    }


def source(db, user, step_id):
    from domain_packs.mold.tool_gateway import available_tools
    step = db.get(m.Step, step_id)
    run = db.get(m.Run, step.run_id) if step else None
    if not run or run.user_id != user.id:
        raise DomainError("NOT_FOUND", "操作建议不存在或无权访问", 404)
    if run.status not in {"RUNNING", "RUNNING_SCOPED", "SUCCEEDED"}:
        raise DomainError("PROPOSAL_STOPPED", "任务已停止，请重新准备操作", 409)
    if run.security_version != user.security_version or run.checkpoint.get("authorization_hash") != fingerprint(db, user):
        raise DomainError("AUTHORIZATION_CHANGED", "授权已变化，请重新准备操作", 403)
    proposal = step.result.get("proposal")
    if step.tool not in available_tools(db, user) or step.tool not in DELIVERY_LOGISTICS_PROPOSAL_TOOLS or not proposal:
        raise DomainError("TOOL_FORBIDDEN", "操作能力不可用", 403)
    return proposal


def validate_intent(db, user, payload):
    proposal = source(db, user, payload["step_id"])
    if content_hash(proposal) != payload["proposal_hash"]:
        raise DomainError("CONFIRMATION_INVALID", "操作建议内容已变化", 409)
    if proposal.get("kind") == "logistics_route":
        data = parse_logistics_route(proposal["input"])
        _, display = preview_logistics_route(db, user, data)
    elif proposal.get("kind") == "logistics_quote":
        data = parse_logistics_quote(proposal["input"])
        _, _, _, _, display = preview_logistics_quote(db, user, data)
    else:
        raise DomainError("TOOL_FORBIDDEN", "操作建议类型不可用", 403)
    if content_hash(display) != content_hash(proposal["display"]):
        raise DomainError("VERSION_CONFLICT", "项目、路线、报价或权限资料已变化，请重新准备", 409)
    return proposal, data


def confirm(db, user, payload):
    proposal, data = validate_intent(db, user, payload)
    if proposal["kind"] == "logistics_route":
        row = create_logistics_route(db, user, data)
        return {
            "project_id": row.project_id,
            "logistics_route_id": row.id,
            "action": "logistics_route",
            "status": "CONFIRMED",
        }
    row = create_logistics_quote(db, user, data)
    return {
        "project_id": row.settlement_for_project_id,
        "logistics_route_id": row.route_id,
        "logistics_quote_id": row.id,
        "action": "logistics_quote",
        "status": "CONFIRMED",
    }
