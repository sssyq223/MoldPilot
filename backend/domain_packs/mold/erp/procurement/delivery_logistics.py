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
from domain_packs.mold.erp.procurement.customer_acceptance_status import summarize_customer_acceptance
from domain_packs.mold.config import settings as mold_settings
from domain_packs.mold.ports.files import uploaded_file


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
DELIVERY_LOGISTICS_PROPOSAL_TOOLS = {
    "prepare_logistics_route", "prepare_logistics_quote", "prepare_customer_acceptance",
    "prepare_outbound_release", "prepare_customer_delivery_signature",
}


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


class CustomerAcceptanceProposalInput(StrictModel):
    project_id: str = Field(min_length=1, max_length=36)
    project_version: int = Field(ge=1)
    signature_id: str | None = Field(default=None, max_length=36)
    acceptance_type: Literal["INITIAL", "RECHECK"] = "INITIAL"
    previous_acceptance_id: str | None = Field(default=None, max_length=36,
        description='整改复验须引用查询返回的同一验收链当前末条记录 ID；初次验收不填写。')
    result: Literal["PASSED", "FAILED", "CONDITIONALLY_PASSED"]
    accepted_date: date
    issue_description: str = Field(default="", max_length=4000)
    responsibility: Literal["CUSTOMER", "SUPPLIER", "INTERNAL", "SHARED", "UNKNOWN"] = "UNKNOWN"
    corrective_due_date: date | None = None
    contact_case_id: str | None = Field(default=None, max_length=36)
    supplier_id: str | None = Field(default=None, max_length=36)
    deduction_amount: Decimal | None = Field(default=None, gt=0, max_digits=18, decimal_places=2)
    currency: str | None = Field(default=None, pattern=r"^[A-Z]{3}$")
    schedule_impact_days: int = Field(default=0, ge=0, le=3650)
    contract_change_required: bool = False
    evidence: str = Field(min_length=1, max_length=4000)
    file_ids: list[str] = Field(
        default_factory=list,
        max_length=20,
        description='当前任务中已上传的客户验收原件文件 ID；只关联本人确认过的原件。',
    )


class OutboundReleaseProposalInput(StrictModel):
    project_id: str = Field(min_length=1, max_length=36)
    project_version: int = Field(ge=1)
    inspection_type: Literal["SELF_INSPECTION", "OUTBOUND_ACCEPTANCE"] = "SELF_INSPECTION"
    trial_request_id: str | None = Field(default=None, max_length=36)
    previous_record_id: str | None = Field(default=None, max_length=36)
    result: Literal["PASSED", "FAILED", "CONDITIONALLY_PASSED"]
    inspected_date: date
    issue_description: str = Field(default="", max_length=4000)
    corrective_due_date: date | None = None
    evidence: str = Field(min_length=1, max_length=4000)
    source_ref: str = Field(min_length=1, max_length=120)
    file_ids: list[str] = Field(
        default_factory=list,
        max_length=20,
        description='当前任务中已上传的出厂检验/放行原件文件 ID。',
    )


class CustomerDeliverySignatureProposalInput(StrictModel):
    project_id: str = Field(min_length=1, max_length=36)
    project_version: int = Field(ge=1)
    signed_date: date
    shipment_reference: str = Field(min_length=1, max_length=120)
    signer_name: str = Field(min_length=1, max_length=120)
    logistics_route_id: str | None = Field(default=None, max_length=36)
    evidence: str = Field(min_length=1, max_length=4000)
    file_ids: list[str] = Field(
        default_factory=list,
        max_length=20,
        description='当前任务中已上传的客户签收原件文件 ID。',
    )


def customer_acceptance_schema():
    return CustomerAcceptanceProposalInput.model_json_schema()


def logistics_route_schema():
    return LogisticsRouteProposalInput.model_json_schema()


def logistics_quote_schema():
    return LogisticsQuoteProposalInput.model_json_schema()


def outbound_release_schema():
    return OutboundReleaseProposalInput.model_json_schema()


def customer_delivery_signature_schema():
    return CustomerDeliverySignatureProposalInput.model_json_schema()


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


def parse_customer_acceptance(arguments):
    try:
        data = CustomerAcceptanceProposalInput.model_validate(arguments or {})
    except ValidationError as error:
        raise DomainError("INVALID_TOOL_INPUT", "客户验收参数不完整或不符合要求：" + error.errors()[0]["msg"]) from None
    if data.accepted_date > now().date():
        raise DomainError("DATE_INVALID", "客户验收日期不能在未来")
    if data.result == "FAILED" and not data.issue_description.strip():
        raise DomainError("ACCEPTANCE_ISSUE_REQUIRED", "客户验收未通过时必须填写问题描述")
    if data.corrective_due_date and data.corrective_due_date < data.accepted_date:
        raise DomainError("DATE_INVALID", "整改期限不能早于客户验收日期")
    if data.deduction_amount is not None and not data.currency:
        raise DomainError("CURRENCY_REQUIRED", "验收扣款必须填写币种")
    if data.deduction_amount is not None and data.responsibility == "UNKNOWN":
        raise DomainError("RESPONSIBILITY_REQUIRED", "验收扣款必须明确责任归属")
    if data.responsibility == "SUPPLIER" and not data.supplier_id:
        raise DomainError("SUPPLIER_REQUIRED", "供应商责任必须关联供应商")
    return data


def _acceptance_files(db, user, file_ids, *, run=None, step_id=None):
    """Resolve acceptance originals against the exact current task boundary."""
    requested = [str(value) for value in (file_ids or [])]
    if len(requested) != len(set(requested)):
        raise DomainError("FILE_CONTEXT_INVALID", "客户验收附件不能重复", 409)
    if not requested:
        return []
    if run is None and step_id:
        step = db.get(m.Step, step_id)
        run = db.get(m.Run, step.run_id) if step else None
    if not run or run.user_id != user.id:
        raise DomainError("FILE_CONTEXT_INVALID", "客户验收附件须绑定当前本人任务", 403)
    blobs = []
    for file_id in requested:
        blob = uploaded_file(db, user, file_id)
        if blob.conversation_id != run.conversation_id:
            raise DomainError("FILE_CONTEXT_INVALID", "只能关联当前会话中的客户验收原件", 403)
        if not db.scalar(select(m.RunFile).where(
            m.RunFile.run_id == run.id,
            m.RunFile.file_id == blob.id,
        )):
            raise DomainError("FILE_CONTEXT_INVALID", "客户验收附件尚未绑定当前任务", 403)
        blobs.append(blob)
    return blobs


def _acceptance_display_files(display, blobs, *, label="客户验收原件"):
    if not blobs:
        return display
    return {
        **display,
        label: [
            {"id": blob.id, "filename": blob.filename, "sha256": blob.sha256}
            for blob in blobs
        ],
        "说明": display["说明"] + " 原件只作为不可变 Agent 附件登记，不替代业务事实。",
    }


def parse_outbound_release(arguments):
    try:
        data = OutboundReleaseProposalInput.model_validate(arguments or {})
    except ValidationError as error:
        raise DomainError("INVALID_TOOL_INPUT", "出厂自检/放行参数不完整或不符合要求：" + error.errors()[0]["msg"]) from None
    if data.inspected_date > now().date():
        raise DomainError("DATE_INVALID", "出厂自检/放行日期不能在未来")
    if data.result in {"FAILED", "CONDITIONALLY_PASSED"} and not data.issue_description.strip():
        raise DomainError("RELEASE_ISSUE_REQUIRED", "未通过或有条件通过时必须填写问题描述")
    if data.corrective_due_date and data.corrective_due_date < data.inspected_date:
        raise DomainError("DATE_INVALID", "整改期限不能早于出厂自检/放行日期")
    if data.previous_record_id and data.inspection_type != "SELF_INSPECTION":
        raise DomainError("INVALID_TOOL_INPUT", "当前只允许对出厂自检记录进行整改复验")
    return data


def parse_customer_delivery_signature(arguments):
    try:
        data = CustomerDeliverySignatureProposalInput.model_validate(arguments or {})
    except ValidationError as error:
        raise DomainError("INVALID_TOOL_INPUT", "客户签收参数不完整或不符合要求：" + error.errors()[0]["msg"]) from None
    if data.signed_date > now().date():
        raise DomainError("DATE_INVALID", "客户签收日期不能在未来")
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
                "attachments": [
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
                        .join(
                            m.FileObject,
                            m.FileObject.id == m.CustomerDeliverySignatureAttachment.file_id,
                        )
                        .where(
                            m.CustomerDeliverySignatureAttachment.signature_id == row.id
                        )
                        .order_by(
                            m.CustomerDeliverySignatureAttachment.version,
                            m.CustomerDeliverySignatureAttachment.id,
                        )
                    )
                ],
            }
        )

    can_read_acceptance = "query_project_closure_context" in allowed_tools and access(db, user, "project_close.read", {"project_id": project_id}).allowed
    acceptance_records = []
    acceptance_rows = []
    if can_read_acceptance:
        acceptance_rows = list(db.scalars(
            select(m.CustomerAcceptanceRecord)
            .where(m.CustomerAcceptanceRecord.project_id == project_id)
            .order_by(m.CustomerAcceptanceRecord.accepted_date.desc(), m.CustomerAcceptanceRecord.created_at.desc(), m.CustomerAcceptanceRecord.id)
        ))
        for row in acceptance_rows[:50]:
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
                    select(m.CustomerAcceptanceAttachment, m.FileObject)
                    .join(m.FileObject, m.FileObject.id == m.CustomerAcceptanceAttachment.file_id)
                    .where(
                        m.CustomerAcceptanceAttachment.customer_acceptance_id == row.id
                    )
                    .order_by(
                        m.CustomerAcceptanceAttachment.version,
                        m.CustomerAcceptanceAttachment.id,
                    )
                )
            ]
            acceptance_records.append(
                {
                    "id": row.id,
                    "signature_id": row.signature_id,
                    "previous_acceptance_id": row.previous_acceptance_id,
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
                    "attachments": attachments,
                }
            )

    signed = [row for row in signatures if row["sign_status"] == "SIGNED"]
    acceptance_status = summarize_customer_acceptance(acceptance_rows)
    return {
        "signatures": signatures,
        "acceptance_records": acceptance_records,
        "acceptance_record_count": len(acceptance_rows) if can_read_acceptance else None,
        "acceptance_records_truncated": len(acceptance_rows) > len(acceptance_records) if can_read_acceptance else None,
        "visibility": {
            "signature_records_visible": True,
            "acceptance_records_visible": can_read_acceptance,
        },
        "derived_status": {
            "has_customer_signature": bool(signed),
            **acceptance_status,
        },
    }


def _outbound_release_records(db, user, project_id, allowed_tools):
    can_read = (
        "query_project_closure_context" in allowed_tools
        and access(db, user, "project_close.read", {"project_id": project_id}).allowed
    )
    if not can_read:
        return {
            "records": [],
            "visibility": {"records_visible": False},
            "derived_status": {
                "has_outbound_release_evidence": None,
                "has_outbound_self_inspection_passed": None,
                "has_outbound_release_failure": None,
            },
        }
    rows = list(
        db.scalars(
            select(m.OutboundReleaseRecord)
            .where(m.OutboundReleaseRecord.project_id == project_id)
            .order_by(
                m.OutboundReleaseRecord.inspected_date.desc(),
                m.OutboundReleaseRecord.created_at.desc(),
                m.OutboundReleaseRecord.id,
            )
            .limit(100)
        )
    )
    records = []
    for row in rows:
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
                select(m.OutboundReleaseAttachment, m.FileObject)
                .join(m.FileObject, m.FileObject.id == m.OutboundReleaseAttachment.file_id)
                .where(m.OutboundReleaseAttachment.outbound_release_id == row.id)
                .order_by(
                    m.OutboundReleaseAttachment.version,
                    m.OutboundReleaseAttachment.id,
                )
            )
        ]
        records.append({
            "id": row.id,
            "project_version": row.project_version,
            "trial_request_id": row.trial_request_id,
            "previous_record_id": row.previous_record_id,
            "inspection_type": row.inspection_type,
            "result": row.result,
            "inspected_date": row.inspected_date.isoformat(),
            "issue_description": row.issue_description,
            "corrective_due_date": row.corrective_due_date.isoformat() if row.corrective_due_date else None,
            "evidence": row.evidence,
            "source_ref": row.source_ref,
            "confirmed_by": row.confirmed_by,
            "attachments": attachments,
        })
    latest = records[0] if records else None
    latest_self = next((row for row in records if row["inspection_type"] == "SELF_INSPECTION"), None)
    return {
        "records": records,
        "visibility": {"records_visible": True},
        "derived_status": {
            "has_outbound_release_evidence": bool(records),
            "has_outbound_self_inspection_passed": bool(
                latest_self and latest_self["result"] in {"PASSED", "CONDITIONALLY_PASSED"}
            ),
            "has_outbound_release_failure": bool(
                latest and latest["result"] == "FAILED"
            ),
            "latest_record": latest,
            "latest_self_inspection": latest_self,
        },
    }


def _delivery_handoffs(
    *,
    release_status,
    shipment_tracking,
    stock_movements,
    erp_product_shipments,
    customer_status,
    has_customer_acceptance,
):
    """Project the delivery responsibility boundaries without creating facts.

    Each row is an evidence boundary between two independently confirmed
    events.  A state such as READY or WAITING only says which next fact is
    missing; it never upgrades an ERP shipment into a signature or acceptance.
    """
    totals = shipment_tracking["totals"]
    has_local_shipment = bool(totals.get("supplier_shipments"))
    has_local_outbound = any(
        Decimal(str(row.get("quantity") or "0")) < 0 for row in stock_movements
    )
    has_erp_shipment = bool(erp_product_shipments)
    has_shipment = has_local_shipment or has_local_outbound or has_erp_shipment
    has_tracking = bool(
        any(
            str(row.get("tracking_no") or row.get("trackingNo") or "").strip()
            for row in erp_product_shipments
        )
    )
    release_visible = release_status.get("has_outbound_release_evidence") is not None
    release_passed = release_status.get("has_outbound_self_inspection_passed")
    release_failed = release_status.get("has_outbound_release_failure")
    has_signature = bool(customer_status.get("has_customer_signature"))
    unresolved_acceptance = bool(customer_status.get("has_unresolved_failure"))

    if not release_visible:
        release_to_shipment = {
            "state": "UNAVAILABLE",
            "reason": "出厂放行记录不可见，不能判断是否具备发运前置依据。",
        }
    elif release_failed or not release_passed:
        release_to_shipment = {
            "state": "BLOCKED",
            "reason": "出厂自检/放行尚未形成合格依据，不能把出库或发货记录当作已放行。",
        }
    elif has_shipment:
        release_to_shipment = {
            "state": "CONNECTED",
            "reason": "已形成出厂放行合格依据，并存在本地或 ERP 发运/出库事实。",
        }
    else:
        release_to_shipment = {
            "state": "READY",
            "reason": "出厂放行已通过，等待出库或发运事实回执。",
        }

    if not has_shipment:
        shipment_to_signature = {
            "state": "BLOCKED",
            "reason": "尚未见出库、供应商发货或 ERP 成品发货事实，不能进入客户签收核对。",
        }
    elif has_signature:
        shipment_to_signature = {
            "state": "CONNECTED",
            "reason": "发运/出库事实已与客户签收记录形成可核对交接。",
        }
    elif has_erp_shipment and not has_tracking:
        shipment_to_signature = {
            "state": "WAITING",
            "reason": "ERP 已有成品发货，但缺少物流单号或客户签收回执。",
        }
    else:
        shipment_to_signature = {
            "state": "READY",
            "reason": "已见发运/出库事实，等待客户签收回执。",
        }

    if not has_signature:
        signature_to_acceptance = {
            "state": "BLOCKED",
            "reason": "未见客户签收，不能进入客户质量验收结论。",
        }
    elif unresolved_acceptance:
        signature_to_acceptance = {
            "state": "NEEDS_ATTENTION",
            "reason": "客户验收链存在未解决失败或关联冲突，需整改复验。",
        }
    elif has_customer_acceptance:
        signature_to_acceptance = {
            "state": "CONNECTED",
            "reason": "客户签收已与客户质量验收通过或有条件通过依据关联。",
        }
    else:
        signature_to_acceptance = {
            "state": "READY",
            "reason": "已见客户签收，等待客户质量验收结果。",
        }

    has_impact = bool(
        customer_status.get("has_acceptance_deduction")
        or customer_status.get("has_contract_change_required")
        or customer_status.get("schedule_impact_days_total")
    )
    if not has_customer_acceptance:
        acceptance_to_follow_up = {
            "state": "BLOCKED",
            "reason": "客户验收尚未形成通过依据，不能进入财务、合同或计划影响核对。",
        }
    elif has_impact:
        acceptance_to_follow_up = {
            "state": "READY",
            "reason": "客户验收已形成结果，但存在扣款、合同变化或交期影响，需要交给对应责任线核对。",
        }
    else:
        acceptance_to_follow_up = {
            "state": "CONNECTED",
            "reason": "客户验收结果未产生待处理扣款、合同变化或交期影响信号。",
        }

    return [
        {
            "key": "outbound_release_to_shipment",
            "from": "outbound_release",
            "to": "shipment",
            **release_to_shipment,
            "next_query_tool": "query_delivery_logistics_context",
        },
        {
            "key": "shipment_to_customer_signature",
            "from": "shipment",
            "to": "customer_signature",
            **shipment_to_signature,
            "next_query_tool": "query_delivery_logistics_context",
        },
        {
            "key": "customer_signature_to_acceptance",
            "from": "customer_signature",
            "to": "customer_acceptance",
            **signature_to_acceptance,
            "next_query_tool": "query_delivery_logistics_context",
        },
        {
            "key": "customer_acceptance_to_follow_up",
            "from": "customer_acceptance",
            "to": "finance_contract_plan",
            **acceptance_to_follow_up,
            "next_query_tool": "query_finance_context" if has_impact else "query_project_completion_context",
        },
    ]


def _analysis(project, profile, active_plan, delivery_tasks, shipment_tracking, stock_movements, trials, closure_items, contacts, logistics_pricing, customer_delivery_acceptance, outbound_release, erp_delivery_execution):
    totals = shipment_tracking["totals"]
    trial_passed = [row for row in trials if row.get("passed") is True]
    trial_failed = [row for row in trials if row.get("passed") is False]
    customer_acceptance_done = [row for row in closure_items if "ACCEPTANCE" in row.get("item_key", "") and row.get("status") == "DONE"]
    delivery_done = [row for row in closure_items if "DELIVERY" in row.get("item_key", "") and row.get("status") == "DONE"]
    open_contacts = [row for row in contacts if row.get("collaboration_status") != "CLOSED"]
    stock_out = [row for row in stock_movements if Decimal(str(row.get("quantity") or "0")) < 0]
    customer_status = customer_delivery_acceptance["derived_status"]
    release_status = outbound_release["derived_status"]
    has_customer_acceptance = (bool(customer_acceptance_done) or customer_status["has_customer_acceptance"]) and not customer_status["has_unresolved_failure"]
    erp_records = (erp_delivery_execution or {}).get("records") or {}
    erp_fulfillment_records = erp_records.get("fulfillment_records") or []
    erp_product_shipments = erp_records.get("product_shipment_records") or []
    erp_exception_records = erp_records.get("exception_records") or []
    erp_quality_records = (
        erp_records.get("quality_inspection_records")
        or erp_records.get("quality_inspections")
        or []
    )
    erp_quality_totals = erp_records.get("quality_totals") or {}
    open_erp_exception_records = [
        row for row in erp_exception_records
        if str(row.get("status") or "").strip().lower() not in {"closed", "resolved", "done", "completed", "cancelled"}
    ]
    open_erp_quality_records = [
        row for row in erp_quality_records
        if str(row.get("status") or "").strip().lower() in {"pending", "inspecting"}
    ]
    failed_erp_quality_records = [
        row for row in erp_quality_records
        if (
            str(row.get("result") or "").strip().lower() in {"unqualified", "partial"}
            or str(row.get("status") or "").strip().lower() in {"partial", "reject_return"}
        )
    ]
    delivery_handoffs = _delivery_handoffs(
        release_status=release_status,
        shipment_tracking=shipment_tracking,
        stock_movements=stock_movements,
        erp_product_shipments=erp_product_shipments,
        customer_status=customer_status,
        has_customer_acceptance=has_customer_acceptance,
    )
    has_erp_fulfillment = bool(erp_fulfillment_records)
    has_erp_product_shipment = bool(erp_product_shipments)
    erp_tracking_rows = [
        row for row in erp_product_shipments
        if str(row.get("tracking_no") or row.get("trackingNo") or "").strip()
    ]
    erp_pending_receipt_shipments = [
        row for row in erp_product_shipments
        if str(row.get("arrival_status") or "").strip().lower()
        not in {"received", "completed", "confirmed", "done"}
    ]

    gaps = []
    warnings = []
    if not active_plan:
        warnings.append("当前可见范围未见有效项目计划，无法核对交付/出库/客户验收节点。")
    if not delivery_tasks:
        gaps.append("未见明确的交付、出库、发货、客户签收或验收计划节点。")
    if not totals.get("supplier_shipments"):
        gaps.append("未见当前可见正式订单的供应商发货记录；不能据此判断实物已交付。")
    if has_erp_fulfillment and not totals.get("supplier_shipments"):
        warnings.append("ERP 原系统已有履约记录，但 Agent 未见本地供应商发货明细；需按 ERP 原单据核对发货、物流和客户签收。")
    if has_erp_product_shipment and not totals.get("supplier_shipments"):
        warnings.append("ERP 原系统已有成品发货单；当前只作为出厂运输事实引用，仍需核对出库放行、物流单号、我方/客户收货和签收。")
    if has_erp_product_shipment and not erp_tracking_rows:
        warnings.append("ERP 原系统已有成品发货单但未见物流单号；不能把发货单存在当作运输已可追踪。")
    if erp_pending_receipt_shipments:
        warnings.append("ERP 原系统成品发货单仍有未确认到货状态；客户签收和验收不能由发货单自动推断。")
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
        if outbound_release["visibility"]["records_visible"] and not release_status["has_outbound_self_inspection_passed"]:
            gaps.append("试模已通过，但未见 Agent 出厂自检/放行合格依据；不能据此推进出库发运。")
    if release_status.get("has_outbound_release_failure"):
        warnings.append("最新出厂自检/放行记录未通过，需完成整改并登记复验后再继续交付。")
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
    if customer_status["has_unresolved_failure"]:
        warnings.append("当前客户验收未通过，尚未见对应验收链的后续复验通过；需继续跟踪问题、责任、整改期限和复验结果。")
    if customer_status["unlinked_recheck_ids"]:
        warnings.append("存在未关联前次验收的历史复验记录；未猜测其处理对象，不能据此消除其他验收失败。")
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
    if open_erp_exception_records:
        warnings.append("ERP 原系统存在未关闭的交付履约异常；需在原系统责任流程中处理并保留可核对回执。")
    if open_erp_quality_records:
        warnings.append("ERP 原系统存在待检或检验中的质量任务；在形成合格结论前不能认定交付具备放行依据。")
    if failed_erp_quality_records:
        warnings.append("ERP 原系统存在不合格或部分合格质量任务；需在原系统完成整改、复检或放行处置后再继续交付。")

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
        "outbound_release": outbound_release,
        "delivery_handoffs": delivery_handoffs,
        "erp_delivery_execution": erp_delivery_execution,
        "erp_product_shipments": erp_product_shipments[:200],
        "erp_quality_inspections": erp_quality_records[:200],
        "erp_quality_totals": erp_quality_totals,
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
            "has_outbound_release_evidence": release_status.get("has_outbound_release_evidence"),
            "has_outbound_self_inspection_passed": release_status.get("has_outbound_self_inspection_passed"),
            "has_outbound_release_failure": release_status.get("has_outbound_release_failure"),
            "has_customer_signature": customer_status["has_customer_signature"],
            "has_customer_acceptance": has_customer_acceptance,
            "has_failed_customer_acceptance": customer_status["has_failed_customer_acceptance"],
            "has_unresolved_customer_acceptance_failure": customer_status["has_unresolved_failure"],
            "has_customer_recheck_passed": customer_status["has_recheck_passed"],
            "has_customer_acceptance_deduction": customer_status["has_acceptance_deduction"],
            "has_customer_acceptance_contract_change": customer_status["has_contract_change_required"],
            "has_open_delivery_or_quality_issue": bool(
                open_contacts
                or open_erp_exception_records
                or open_erp_quality_records
                or failed_erp_quality_records
            ),
            "has_structured_logistics_price": pricing_status["has_effective_quote"],
            "has_project_logistics_settlement_price": pricing_status["has_project_settlement_price"],
            "has_erp_fulfillment_record": has_erp_fulfillment,
            "has_erp_product_shipment": has_erp_product_shipment,
            "has_erp_tracking_no": bool(erp_tracking_rows),
            "has_erp_pending_receipt": bool(erp_pending_receipt_shipments),
            "has_erp_delivery_exception": bool(open_erp_exception_records),
            "has_erp_quality_inspection": bool(erp_quality_records),
            "has_erp_quality_open": bool(open_erp_quality_records),
            "has_erp_quality_failure": bool(failed_erp_quality_records),
            "delivery_handoff_states": {
                row["key"]: row["state"] for row in delivery_handoffs
            },
        },
    }


def query(db, user, data: ProjectPlanContextInput, allowed_tools: set[str]):
    project, alternatives, truncated = _resolve(db, user, data, allowed_tools)
    limitations = [
        "只读取当前用户可见项目；订单、仓库、试模、结项和工程联络材料分别受对应工具与权限约束。",
        "本工具只核对交付、物流、质量、签收和验收上下文，不创建物流路线、不维护报价、不确认发货、不确认客户签收或验收。",
        "采购发货、仓库收货、入库检验、试模通过、Agent 出厂放行、客户签收和客户验收是不同事实，不能相互替代。",
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
        outbound_release = _outbound_release_records(db, user, project.id, allowed_tools)
        profile = _profile(db, user, project.id)
        from domain_packs.mold.erp.design.erp_progress import query_project_delivery_execution
        erp_delivery_execution = query_project_delivery_execution(db, user, project)
        skipped = []
        if not records["project_plan"] and not records["plan_change"]:
            skipped.append("项目计划/计划变更")
        if "query_purchase_orders" not in allowed_tools and "query_orders" not in allowed_tools:
            skipped.append("正式采购订单/供应商发货/收货跟踪")
        if "query_trial_request" not in allowed_tools and "query_assembly_trial_context" not in allowed_tools:
            skipped.append("试模结果")
        if not customer_delivery_acceptance["visibility"]["acceptance_records_visible"]:
            skipped.append("客户验收/复验/扣款记录")
        if not outbound_release["visibility"]["records_visible"]:
            skipped.append("Agent 出厂自检/放行记录")
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
                    "analysis": _analysis(
                        project,
                        profile,
                        active_plan,
                        delivery_tasks,
                        shipment_tracking,
                        stock_movements,
                        trials,
                        closure_items,
                        contacts,
                        logistics_pricing,
                        customer_delivery_acceptance,
                        outbound_release,
                        erp_delivery_execution,
                    ),
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


def preview_customer_delivery_signature(db, user, data: CustomerDeliverySignatureProposalInput):
    project = db.get(m.Project, data.project_id)
    if not project:
        raise DomainError("NOT_FOUND", "项目不存在", 404)
    scope = {"project_id": project.id}
    require(db, user, "project.read", scope)
    require(db, user, "project_close.execute", scope)
    if project.row_version != data.project_version:
        raise DomainError("VERSION_CONFLICT", "项目状态已变化，请重新查询后准备客户签收", 409)
    if project.status == "CLOSED":
        raise DomainError("PROJECT_CLOSED", "项目已关闭，不能追加客户签收记录", 409)
    route = None
    if data.logistics_route_id:
        route = db.get(m.LogisticsRoute, data.logistics_route_id)
        if not route or not route.active:
            raise DomainError("LOGISTICS_ROUTE_NOT_FOUND", "物流路线不存在或已停用", 404)
        if route.project_id and route.project_id != project.id:
            raise DomainError("LOGISTICS_ROUTE_PROJECT_MISMATCH", "物流路线不属于本次交付项目", 409)
    duplicate = db.scalar(
        select(m.CustomerDeliverySignature.id).where(
            m.CustomerDeliverySignature.project_id == project.id,
            m.CustomerDeliverySignature.shipment_reference == data.shipment_reference,
            m.CustomerDeliverySignature.signed_date == data.signed_date,
        )
    )
    if duplicate:
        raise DomainError("CUSTOMER_SIGNATURE_DUPLICATE", "该项目的客户签收依据已经登记", 409)
    display = {
        "操作": "登记客户签收事实",
        "项目": project.code + " · " + project.name,
        "项目版本": project.row_version,
        "客户签收日期": data.signed_date.isoformat(),
        "签收或交付单号": data.shipment_reference,
        "客户签收人": data.signer_name,
        "物流路线": (
            f"{route.route_code} · {route.origin} → {route.destination}"
            if route
            else "未关联 Agent 物流路线"
        ),
        "签收依据": data.evidence,
        "说明": "本人确认后仅登记发运后的 Agent 客户签收事实；不写回 ERP、不代表质量验收通过、移模时间、回款或项目关闭。",
    }
    return project, route, display


def create_customer_delivery_signature(db, user, data: CustomerDeliverySignatureProposalInput):
    return create_customer_delivery_signature_with_context(db, user, data)


def create_customer_delivery_signature_with_context(
    db,
    user,
    data: CustomerDeliverySignatureProposalInput,
    *,
    run=None,
    step_id=None,
):
    db.scalar(
        select(m.Project)
        .where(m.Project.id == data.project_id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    blobs = _acceptance_files(db, user, data.file_ids, run=run, step_id=step_id)
    project, route, _ = preview_customer_delivery_signature(db, user, data)
    row = m.CustomerDeliverySignature(
        project_id=project.id,
        logistics_route_id=route.id if route else None,
        shipment_reference=data.shipment_reference,
        signed_date=data.signed_date,
        signer_name=data.signer_name,
        sign_status="SIGNED",
        move_type="DELIVERY",
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
    return row


def preview_customer_acceptance(db, user, data: CustomerAcceptanceProposalInput):
    project = db.get(m.Project, data.project_id)
    if not project:
        raise DomainError("NOT_FOUND", "项目不存在", 404)
    scope = {"project_id": project.id}
    require(db, user, "project.read", scope)
    require(db, user, "project_close.execute", scope)
    if project.row_version != data.project_version:
        raise DomainError("VERSION_CONFLICT", "项目状态已变化，请重新查询后准备客户验收", 409)
    if project.status == 'CLOSED':
        raise DomainError('PROJECT_CLOSED', '项目已关闭，不能直接追加验收；请按关闭后更正流程核对', 409)

    signature = None
    if data.signature_id:
        signature = db.get(m.CustomerDeliverySignature, data.signature_id)
        if not signature or signature.project_id != project.id:
            raise DomainError("SIGNATURE_NOT_FOUND", "客户签收记录不存在或不属于该项目", 404)
        if signature.sign_status != "SIGNED":
            raise DomainError("SIGNATURE_NOT_CONFIRMED", "客户签收记录尚未确认，不能作为验收依据", 409)

    previous = None
    if data.acceptance_type == "RECHECK":
        previous = db.get(m.CustomerAcceptanceRecord, data.previous_acceptance_id) if data.previous_acceptance_id else None
        if not previous or previous.project_id != project.id or previous.signature_id != data.signature_id:
            raise DomainError("RECHECK_WITHOUT_FAILURE", "复验须明确引用同一项目及签收依据下的验收链当前记录")
        if previous.accepted_date > data.accepted_date:
            raise DomainError("DATE_INVALID", "复验日期不能早于所引用的验收记录")
        if db.scalar(select(m.CustomerAcceptanceRecord.id).where(m.CustomerAcceptanceRecord.previous_acceptance_id == previous.id)):
            raise DomainError("RECHECK_SOURCE_CHANGED", "所引用验收记录已有后续复验，请重新查询当前记录", 409)
        current, seen, has_failure = previous, set(), False
        while current:
            if current.id in seen or current.project_id != project.id or current.signature_id != data.signature_id:
                raise DomainError("ACCEPTANCE_CHAIN_INVALID", "验收链关联不一致，请先核对", 409)
            seen.add(current.id)
            has_failure |= current.result == 'FAILED'
            current = db.get(m.CustomerAcceptanceRecord, current.previous_acceptance_id) if current.previous_acceptance_id else None
        if not has_failure:
            raise DomainError("RECHECK_WITHOUT_FAILURE", "所引用验收链没有未通过记录，不能登记整改复验")
    elif data.previous_acceptance_id:
        raise DomainError("INVALID_TOOL_INPUT", "初次验收不能引用前次验收；整改后核对请选择复验")
    duplicate = db.scalar(select(m.CustomerAcceptanceRecord.id).where(
        m.CustomerAcceptanceRecord.project_id == project.id,
        m.CustomerAcceptanceRecord.acceptance_type == data.acceptance_type,
        m.CustomerAcceptanceRecord.accepted_date == data.accepted_date,
        m.CustomerAcceptanceRecord.evidence == data.evidence,
    ))
    if duplicate:
        raise DomainError("CUSTOMER_ACCEPTANCE_DUPLICATE", "相同客户验收依据已经登记", 409)

    supplier = None
    if data.supplier_id:
        supplier = db.get(m.Supplier, data.supplier_id)
        if not supplier or not supplier.active:
            raise DomainError("SUPPLIER_INVALID", "验收责任供应商不存在或已停用", 409)
    if data.contact_case_id:
        case = db.get(m.ContactCase, data.contact_case_id)
        if not case or case.project_id != project.id:
            raise DomainError("CONTACT_NOT_FOUND", "验收关联工程联络单不存在或不属于该项目", 404)

    display = {
        "操作": "登记客户质量验收结果",
        "项目": project.code + " · " + project.name,
        "项目版本": project.row_version,
        "客户签收依据": signature.shipment_reference if signature else "未关联签收记录",
        "验收类型": "初次验收" if data.acceptance_type == "INITIAL" else "整改复验",
        "验收结果": data.result,
        "验收日期": data.accepted_date.isoformat(),
        "问题描述": data.issue_description or "无",
        "责任归属": data.responsibility,
        "责任供应商": supplier.name if supplier else "不适用",
        "整改期限": data.corrective_due_date.isoformat() if data.corrective_due_date else "未设置",
        "验收扣款": (f"{data.deduction_amount:.2f} {data.currency}" if data.deduction_amount is not None else "无"),
        "计划影响天数": data.schedule_impact_days,
        "需要合同变化": "是" if data.contract_change_required else "否",
        "工程联络单": data.contact_case_id or "未关联",
        "验收依据": data.evidence,
        "说明": "本人确认后仅登记客户质量验收事实；不自动关闭项目、不自动扣款、不替代整改复验或合同变更审批。",
    }
    if previous:
        display['前次验收记录'] = previous.id
        display['前次验收结果'] = previous.result
        display['前次验收依据'] = previous.evidence
    return project, display


def create_customer_acceptance(
    db,
    user,
    data: CustomerAcceptanceProposalInput,
    *,
    run=None,
    step_id=None,
):
    # Serialize confirmation with other rechecks and final project close.
    db.scalar(select(m.Project).where(m.Project.id == data.project_id).with_for_update().execution_options(populate_existing=True))
    blobs = _acceptance_files(db, user, data.file_ids, run=run, step_id=step_id)
    project, _ = preview_customer_acceptance(db, user, data)
    row = m.CustomerAcceptanceRecord(
        project_id=project.id,
        signature_id=data.signature_id,
        acceptance_type=data.acceptance_type,
        previous_acceptance_id=data.previous_acceptance_id,
        result=data.result,
        accepted_date=data.accepted_date,
        issue_description=data.issue_description,
        responsibility=data.responsibility,
        corrective_due_date=data.corrective_due_date,
        contact_case_id=data.contact_case_id,
        supplier_id=data.supplier_id,
        deduction_amount=data.deduction_amount,
        currency=data.currency,
        schedule_impact_days=data.schedule_impact_days,
        contract_change_required=data.contract_change_required,
        evidence=data.evidence,
        confirmed_by=user.id,
    )
    db.add(row)
    db.flush()
    for version, blob in enumerate(blobs, start=1):
        db.add(
            m.CustomerAcceptanceAttachment(
                customer_acceptance_id=row.id,
                file_id=blob.id,
                role="ACCEPTANCE_EVIDENCE",
                version=version,
                title=blob.filename,
                content_sha256=blob.sha256,
                linked_by=user.id,
            )
        )
    db.flush()
    return row


def _resolve_release_trial(db, project, data):
    if data.trial_request_id:
        trial = db.get(m.BusinessSubject, data.trial_request_id)
        if not trial or trial.project_id != project.id or trial.kind != "trial_request":
            raise DomainError("TRIAL_NOT_FOUND", "指定试模记录不存在或不属于该项目", 404)
        result = db.scalar(select(m.TrialResult).where(m.TrialResult.trial_id == trial.id))
        if not result or result.passed is not True:
            raise DomainError("TRIAL_PREREQUISITE", "指定试模记录尚未形成通过结果，不能登记出厂放行", 409)
        return trial, result
    candidates = list(
        db.execute(
            select(m.BusinessSubject, m.TrialResult)
            .join(m.TrialResult, m.TrialResult.trial_id == m.BusinessSubject.id)
            .where(
                m.BusinessSubject.project_id == project.id,
                m.BusinessSubject.kind == "trial_request",
                m.TrialResult.passed.is_(True),
            )
            .order_by(m.TrialResult.actual_date.desc(), m.TrialResult.created_at.desc())
            .limit(20)
        )
    )
    if not candidates:
        raise DomainError("TRIAL_PREREQUISITE", "未见试模通过结果，不能登记出厂放行", 409)
    if len(candidates) > 1:
        raise DomainError("TRIAL_SOURCE_REQUIRED", "项目存在多个试模通过结果，请明确引用试模记录", 409)
    return candidates[0]


def preview_outbound_release(db, user, data: OutboundReleaseProposalInput):
    project = db.get(m.Project, data.project_id)
    if not project:
        raise DomainError("NOT_FOUND", "项目不存在", 404)
    scope = {"project_id": project.id}
    require(db, user, "project.read", scope)
    require(db, user, "project_close.execute", scope)
    if project.row_version != data.project_version:
        raise DomainError("VERSION_CONFLICT", "项目状态已变化，请重新查询后准备出厂自检/放行", 409)
    if project.status == "CLOSED":
        raise DomainError("PROJECT_CLOSED", "项目已关闭，不能追加出厂放行记录", 409)

    trial, trial_result = _resolve_release_trial(db, project, data)
    previous = None
    if data.previous_record_id:
        previous = db.get(m.OutboundReleaseRecord, data.previous_record_id)
        if (
            not previous
            or previous.project_id != project.id
            or previous.inspection_type != "SELF_INSPECTION"
            or previous.result not in {"FAILED", "CONDITIONALLY_PASSED"}
        ):
            raise DomainError("RELEASE_RECHECK_INVALID", "整改复验必须引用本项目当前未闭环的出厂自检记录", 409)
        if previous.inspected_date > data.inspected_date:
            raise DomainError("DATE_INVALID", "整改复验日期不能早于前次出厂自检日期")
        if db.scalar(select(m.OutboundReleaseRecord.id).where(
            m.OutboundReleaseRecord.previous_record_id == previous.id
        )):
            raise DomainError("RELEASE_RECHECK_SOURCE_CHANGED", "所引用的出厂自检记录已有后续复验，请重新查询当前记录", 409)
        if previous.trial_request_id and previous.trial_request_id != trial.id:
            raise DomainError("RELEASE_TRIAL_MISMATCH", "整改复验必须继续引用同一试模记录", 409)
    elif data.inspection_type == "SELF_INSPECTION":
        # Any new self-inspection must close the current open failure through
        # an explicit successor; otherwise a second sibling would hide it.
        latest = db.scalar(
            select(m.OutboundReleaseRecord)
            .where(
                m.OutboundReleaseRecord.project_id == project.id,
                m.OutboundReleaseRecord.inspection_type == "SELF_INSPECTION",
            )
            .order_by(
                m.OutboundReleaseRecord.inspected_date.desc(),
                m.OutboundReleaseRecord.created_at.desc(),
            )
            .limit(1)
        )
        if latest and latest.result in {"FAILED", "CONDITIONALLY_PASSED"}:
            raise DomainError("RELEASE_RECHECK_REQUIRED", "已有未闭环的出厂自检问题，必须引用前次记录登记整改复验", 409)

    if data.inspection_type == "OUTBOUND_ACCEPTANCE":
        has_self_pass = db.scalar(
            select(m.OutboundReleaseRecord.id).where(
                m.OutboundReleaseRecord.project_id == project.id,
                m.OutboundReleaseRecord.inspection_type == "SELF_INSPECTION",
                m.OutboundReleaseRecord.result.in_(("PASSED", "CONDITIONALLY_PASSED")),
            )
        )
        if not has_self_pass:
            raise DomainError("OUTBOUND_SELF_INSPECTION_REQUIRED", "出厂验收前必须先登记出厂自检合格或有条件通过依据", 409)

    if db.scalar(select(m.OutboundReleaseRecord.id).where(
        m.OutboundReleaseRecord.project_id == project.id,
        m.OutboundReleaseRecord.source_ref == data.source_ref,
    )):
        raise DomainError("RELEASE_SOURCE_DUPLICATE", "该出厂自检/放行依据已经登记", 409)

    display = {
        "操作": "登记出厂自检/放行结果",
        "项目": project.code + " · " + project.name,
        "项目版本": project.row_version,
        "检查类型": "出厂自检" if data.inspection_type == "SELF_INSPECTION" else "出厂验收",
        "试模记录": trial.number,
        "试模日期": trial_result.actual_date.isoformat(),
        "检查结果": data.result,
        "检查日期": data.inspected_date.isoformat(),
        "问题描述": data.issue_description or "无",
        "整改期限": data.corrective_due_date.isoformat() if data.corrective_due_date else "不适用",
        "前次记录": previous.id if previous else "无",
        "来源引用": data.source_ref,
        "检查依据": data.evidence,
        "说明": "本人确认后仅登记 Agent 出厂自检/放行事实；不写回 ERP、不自动出库、不自动发货或关闭项目。",
    }
    if previous:
        display["前次结果"] = previous.result
        display["前次依据"] = previous.evidence
    return project, trial, display


def create_outbound_release(db, user, data: OutboundReleaseProposalInput):
    return create_outbound_release_with_context(db, user, data)


def create_outbound_release_with_context(
    db,
    user,
    data: OutboundReleaseProposalInput,
    *,
    run=None,
    step_id=None,
):
    db.scalar(
        select(m.Project)
        .where(m.Project.id == data.project_id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    blobs = _acceptance_files(db, user, data.file_ids, run=run, step_id=step_id)
    project, trial, _ = preview_outbound_release(db, user, data)
    row = m.OutboundReleaseRecord(
        project_id=project.id,
        project_version=project.row_version,
        trial_request_id=trial.id,
        previous_record_id=data.previous_record_id,
        inspection_type=data.inspection_type,
        result=data.result,
        inspected_date=data.inspected_date,
        issue_description=data.issue_description,
        corrective_due_date=data.corrective_due_date,
        evidence=data.evidence,
        source_ref=data.source_ref,
        confirmed_by=user.id,
    )
    db.add(row)
    db.flush()
    for version, blob in enumerate(blobs, start=1):
        db.add(
            m.OutboundReleaseAttachment(
                outbound_release_id=row.id,
                file_id=blob.id,
                role="RELEASE_EVIDENCE",
                version=version,
                title=blob.filename,
                content_sha256=blob.sha256,
                linked_by=user.id,
            )
        )
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
    elif key == "prepare_customer_acceptance":
        data = parse_customer_acceptance(arguments)
        blobs = _acceptance_files(db, user, data.file_ids, run=run)
        _, display = preview_customer_acceptance(db, user, data)
        display = _acceptance_display_files(display, blobs, label="客户验收原件")
        kind = "customer_acceptance"
        action = "confirm_customer_acceptance"
        limitation = "仅准备客户质量验收登记建议；本人确认后才写入验收事实，不自动扣款、整改、改合同或关闭项目。"
    elif key == "prepare_outbound_release":
        data = parse_outbound_release(arguments)
        blobs = _acceptance_files(db, user, data.file_ids, run=run)
        _, _, display = preview_outbound_release(db, user, data)
        display = _acceptance_display_files(display, blobs, label="出厂放行原件")
        kind = "outbound_release"
        action = "confirm_outbound_release"
        limitation = "仅准备出厂自检/放行事实登记建议；本人确认后才写入 Agent 证据，不写回 ERP、不自动出库或发货。"
    elif key == "prepare_customer_delivery_signature":
        data = parse_customer_delivery_signature(arguments)
        blobs = _acceptance_files(db, user, data.file_ids, run=run)
        _, _, display = preview_customer_delivery_signature(db, user, data)
        display = _acceptance_display_files(display, blobs, label="客户签收原件")
        kind = "customer_delivery_signature"
        action = "confirm_customer_delivery_signature"
        limitation = "仅准备发运后的 Agent 客户签收事实登记建议；本人确认后才写入，不写回 ERP、不替代移模时间登记或客户质量验收。"
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
    elif proposal.get("kind") == "customer_acceptance":
        data = parse_customer_acceptance(proposal["input"])
        blobs = _acceptance_files(db, user, data.file_ids, step_id=payload["step_id"])
        _, display = preview_customer_acceptance(db, user, data)
        display = _acceptance_display_files(display, blobs, label="客户验收原件")
    elif proposal.get("kind") == "outbound_release":
        data = parse_outbound_release(proposal["input"])
        blobs = _acceptance_files(db, user, data.file_ids, step_id=payload["step_id"])
        _, _, display = preview_outbound_release(db, user, data)
        display = _acceptance_display_files(display, blobs, label="出厂放行原件")
    elif proposal.get("kind") == "customer_delivery_signature":
        data = parse_customer_delivery_signature(proposal["input"])
        blobs = _acceptance_files(db, user, data.file_ids, step_id=payload["step_id"])
        _, _, display = preview_customer_delivery_signature(db, user, data)
        display = _acceptance_display_files(display, blobs, label="客户签收原件")
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
    if proposal["kind"] == "customer_acceptance":
        row = create_customer_acceptance(db, user, data, step_id=payload["step_id"])
        return {
            "project_id": row.project_id,
            "customer_acceptance_record_id": row.id,
            "action": "customer_acceptance",
            "status": "CONFIRMED",
        }
    if proposal["kind"] == "outbound_release":
        row = create_outbound_release_with_context(db, user, data, step_id=payload["step_id"])
        return {
            "project_id": row.project_id,
            "outbound_release_record_id": row.id,
            "action": "outbound_release",
            "status": "CONFIRMED",
        }
    if proposal["kind"] == "customer_delivery_signature":
        row = create_customer_delivery_signature_with_context(db, user, data, step_id=payload["step_id"])
        return {
            "project_id": row.project_id,
            "customer_delivery_signature_id": row.id,
            "action": "customer_delivery_signature",
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
