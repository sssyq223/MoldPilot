"""MoldPilot procurement migration tools.

This module deliberately keeps the ERP as the authority for raw-material,
hardware and supplier execution facts.  Query tools return bounded ERP DTOs;
prepare tools create the normal MoldPilot confirmation card; the trusted
proposal handler below is the only path that can call an ERP write endpoint.
"""
from __future__ import annotations

from collections import defaultdict
from datetime import date
from decimal import Decimal
from typing import Literal

from pydantic import Field, ValidationError, model_validator
from sqlalchemy import select

from domain_packs.mold import models as m
from domain_packs.mold.authorization import access, fingerprint, predicate
from domain_packs.mold.config import settings
from domain_packs.mold.erp_adapter import ERPClient, decrypt, normalized
from domain_packs.mold.erp.core.project_locator import ProjectId
from domain_packs.mold.ports.bpm import content_hash
from domain_packs.mold.ports.confirmation_policy import proposal_confirmation_policy
from domain_packs.mold.ports.db import now
from domain_packs.mold.ports.errors import DomainError
from domain_packs.mold.ports.schemas import StrictModel


class ProcurementContextInput(StrictModel):
    project_id: ProjectId | None = Field(default=None)
    identifier: str | None = Field(
        default=None,
        min_length=1,
        max_length=200,
        description="项目编号、项目名称、模具号或采购线索。",
    )
    group_id: int | None = Field(default=None, ge=1)

    @model_validator(mode="after")
    def validate_locator(self):
        if bool(self.project_id) == bool(self.identifier):
            raise ValueError("project_id 和 identifier 须且只能填写一项")
        if self.identifier:
            self.identifier = self.identifier.strip()
            if not self.identifier:
                raise ValueError("业务线索不能为空")
        return self


class RawMaterialSplitProposalInput(StrictModel):
    request_id: int = Field(ge=1, description="ERP 采购申请 ID。")
    split_mode: Literal["AUTO", "NO_SPLIT"] = "AUTO"
    auto_route: bool = True
    expected_version: int | None = Field(default=None, ge=1)
    receive_site_id: int | None = Field(default=None, ge=1)
    mold_no: str = Field(min_length=1, max_length=80)
    reason: str = Field(min_length=1, max_length=2000)


class PurchaseDecisionProposalInput(StrictModel):
    group_id: int = Field(ge=1)
    decision: Literal["CONFIRM", "CLOSE_IDLE"] = "CONFIRM"
    expected_status: str | None = Field(default=None, max_length=80)
    expected_version: int | None = Field(default=None, ge=1)
    supplier_id: int | None = Field(default=None, ge=1)
    final_confirmed_price: str | None = Field(default=None, max_length=40)
    delivery_date: date | None = None
    remark: str = Field(default="", max_length=2000)


class HardwareQuoteProposalInput(StrictModel):
    group_id: int = Field(ge=1)
    supplier_id: int = Field(ge=1)
    unit_price: str = Field(min_length=1, max_length=40)
    delivery_date: date
    approver_id: int = Field(ge=1)
    expected_status: str | None = Field(default=None, max_length=80)
    expected_version: int | None = Field(default=None, ge=1)
    remark: str = Field(default="", max_length=2000)


class PurchaseOrderProposalInput(StrictModel):
    group_id: int = Field(ge=1)
    expected_status: str | None = Field(default=None, max_length=80)
    expected_version: int | None = Field(default=None, ge=1)
    material_category: Literal["raw_material", "hardware", "unknown"] = "unknown"
    reason: str = Field(min_length=1, max_length=2000)


class SupplierDeliveryChangeProposalInput(StrictModel):
    delivery_id: int = Field(ge=1)
    expected_version: int | None = Field(default=None, ge=1)
    expected_delivery_date: date | None = None
    expected_quantity: str | None = Field(default=None, max_length=40)
    reason: str = Field(min_length=1, max_length=2000)


class PurchaseWorkbenchContextInput(StrictModel):
    project_id: ProjectId | None = Field(default=None)
    identifier: str | None = Field(default=None, min_length=1, max_length=200)
    request_id: int | None = Field(default=None, ge=1)

    @model_validator(mode="after")
    def validate_locator(self):
        if not (self.project_id or self.identifier or self.request_id):
            raise ValueError("project_id、identifier 和 request_id 至少填写一项")
        if self.identifier:
            self.identifier = self.identifier.strip()
            if not self.identifier:
                raise ValueError("业务线索不能为空")
        return self


class PurchaseAdjustmentContextInput(StrictModel):
    request_id: int = Field(ge=1)
    adjustment_id: int | None = Field(default=None, ge=1)


class SupplierPortalContextInput(StrictModel):
    identifier: str | None = Field(default=None, min_length=1, max_length=200)
    order_id: int | None = Field(default=None, ge=1)
    task_id: int | None = Field(default=None, ge=1)
    mold_no: str | None = Field(default=None, min_length=1, max_length=80)

    @model_validator(mode="after")
    def validate_locator(self):
        if not (self.identifier or self.order_id or self.task_id or self.mold_no):
            raise ValueError("identifier、order_id、task_id 和 mold_no 至少填写一项")
        if self.identifier:
            self.identifier = self.identifier.strip()
        if self.mold_no:
            self.mold_no = self.mold_no.strip()
        return self


class PurchaseClaimProposalInput(StrictModel):
    request_id: int = Field(ge=1)
    expected_version: int | None = Field(default=None, ge=1)
    material_category: Literal["raw_material", "hardware", "unknown"] = "unknown"
    reason: str = Field(min_length=1, max_length=2000)


class HardwareInquiryProposalInput(StrictModel):
    group_id: int = Field(ge=1)
    expected_version: int | None = Field(default=None, ge=1)
    supplier_ids: list[int] = Field(default_factory=list, max_length=100)
    remark: str = Field(default="", max_length=2000)


class SupplierOrderDecisionProposalInput(StrictModel):
    order_id: int = Field(ge=1)
    decision: Literal["ACCEPT", "REJECT", "PARTIAL"]
    expected_version: int | None = Field(default=None, ge=1)
    detail_ids: list[int] = Field(default_factory=list, max_length=200)
    promised_delivery_date: date | None = None
    reason: str = Field(default="", max_length=2000)


class SupplierQuoteLineInput(StrictModel):
    material_id: int = Field(ge=1)
    unit_price: str = Field(min_length=1, max_length=40)
    tax_rate: str = Field(min_length=1, max_length=20)
    delivery_date: date
    quantity: str | None = Field(default=None, max_length=40)


class SupplierQuoteSubmitProposalInput(StrictModel):
    task_id: int = Field(ge=1)
    expected_version: int | None = Field(default=None, ge=1)
    lines: list[SupplierQuoteLineInput] = Field(min_length=1, max_length=500)
    remark: str = Field(default="", max_length=2000)


class SupplierDeliveryLineInput(StrictModel):
    detail_id: int = Field(ge=1)
    quantity: str = Field(min_length=1, max_length=40)
    delivery_date: date | None = None


class SupplierDeliveryCreateProposalInput(StrictModel):
    order_id: int = Field(ge=1)
    destination_id: int = Field(ge=1)
    lines: list[SupplierDeliveryLineInput] = Field(min_length=1, max_length=500)
    remark: str = Field(default="", max_length=2000)


class SupplierExceptionProposalInput(StrictModel):
    order_id: int = Field(ge=1)
    detail_id: int | None = Field(default=None, ge=1)
    exception_type: Literal["DELAY", "SHORTAGE", "QUALITY", "OTHER"]
    requested_delivery_date: date | None = None
    reason: str = Field(min_length=1, max_length=2000)


class PurchaseSplitAdjustAllocationInput(StrictModel):
    group_detail_id: int = Field(ge=1)
    quantity: Decimal = Field(gt=0, max_digits=18, decimal_places=3)


class PurchaseSplitAdjustGroupInput(StrictModel):
    group_key: str = Field(min_length=1, max_length=40, pattern=r"^[A-Za-z0-9][A-Za-z0-9_-]*$")
    group_name: str = Field(min_length=1, max_length=200)
    allocations: list[PurchaseSplitAdjustAllocationInput] = Field(min_length=1, max_length=500)

    @model_validator(mode="after")
    def unique_detail_ids(self):
        ids = [item.group_detail_id for item in self.allocations]
        if len(ids) != len(set(ids)):
            raise ValueError("同一目标采购组不能重复分配原组明细")
        self.group_name = self.group_name.strip()
        if not self.group_name:
            raise ValueError("目标采购组名称不能为空")
        return self


class PurchaseSplitAdjustmentPreviewProposalInput(StrictModel):
    split_group_id: int = Field(ge=1)
    expected_group_version: int = Field(ge=0)
    expected_snapshot_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    reason_type: Literal["recognition_error", "supplier_unavailable", "quantity_adjust", "other"]
    reason: str = Field(min_length=1, max_length=500)
    groups: list[PurchaseSplitAdjustGroupInput] = Field(min_length=2, max_length=100)

    @model_validator(mode="after")
    def unique_group_keys(self):
        keys = [item.group_key for item in self.groups]
        if len(keys) != len(set(keys)):
            raise ValueError("目标采购组标识不能重复")
        self.reason = self.reason.strip()
        if not self.reason:
            raise ValueError("拆组调整原因不能为空")
        return self


class PurchaseSplitAdjustmentSubmitProposalInput(StrictModel):
    adjustment_id: int = Field(ge=1)
    expected_version: int | None = Field(default=None, ge=0)
    expected_snapshot_hash: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    reason: str = Field(default="", max_length=500)


class PurchaseTemporaryGroupSaveProposalInput(StrictModel):
    request_id: int = Field(ge=1)
    client_group_key: str = Field(min_length=1, max_length=100, pattern=r"^[A-Za-z0-9][A-Za-z0-9_-]*$")
    display_name: str = Field(min_length=1, max_length=100)
    biz_type: str = Field(min_length=1, max_length=50)
    category_id: int | None = Field(default=None, ge=1)
    category_name: str | None = Field(default=None, max_length=200)
    applicable_detail_ids: list[int] = Field(min_length=1, max_length=500)
    supplier_ids: list[int] = Field(min_length=1, max_length=100)
    receive_site_id: int = Field(ge=1)
    remark: str = Field(default="", max_length=500)

    @model_validator(mode="after")
    def normalize_group(self):
        self.display_name = self.display_name.strip()
        self.category_name = self.category_name.strip() if self.category_name else None
        self.biz_type = self.biz_type.strip()
        if not self.display_name or not self.biz_type:
            raise ValueError("临时分组名称和业务类型不能为空")
        if self.category_id is None and not self.category_name:
            raise ValueError("临时分组必须提供 category_id 或 category_name")
        if len(set(self.applicable_detail_ids)) != len(self.applicable_detail_ids):
            raise ValueError("临时分组明细不能重复")
        if len(set(self.supplier_ids)) != len(self.supplier_ids):
            raise ValueError("临时分组候选供应商不能重复")
        return self


class PurchaseTemporaryGroupDeleteProposalInput(StrictModel):
    request_id: int = Field(ge=1)
    client_group_key: str = Field(min_length=1, max_length=100, pattern=r"^[A-Za-z0-9][A-Za-z0-9_-]*$")


class PurchaseRepurchaseContextInput(StrictModel):
    order_id: int | None = Field(default=None, ge=1)
    batch_id: int | None = Field(default=None, ge=1)

    @model_validator(mode="after")
    def require_locator(self):
        if bool(self.order_id) == bool(self.batch_id):
            raise ValueError("order_id 和 batch_id 须且只能填写一项")
        return self


class PurchaseRepurchaseLineInput(StrictModel):
    line_id: int = Field(ge=1)
    supplier_id: int = Field(ge=1)
    unit_price: str = Field(min_length=1, max_length=40)


class PurchaseApproverOverrideInput(StrictModel):
    node_code: str = Field(min_length=1, max_length=100)
    role_ids: list[int] = Field(default_factory=list, max_length=20)
    user_ids: list[int] = Field(default_factory=list, max_length=20)


class PurchaseRepurchaseSubmitProposalInput(StrictModel):
    batch_id: int = Field(ge=1)
    expected_version: int = Field(ge=0)
    lines: list[PurchaseRepurchaseLineInput] = Field(min_length=1, max_length=500)
    approver_overrides: list[PurchaseApproverOverrideInput] = Field(default_factory=list, max_length=20)
    comment: str = Field(default="", max_length=500)

    @model_validator(mode="after")
    def unique_repurchase_lines(self):
        line_ids = [item.line_id for item in self.lines]
        if len(set(line_ids)) != len(line_ids):
            raise ValueError("重采单不能重复指定同一批次行")
        return self


class PurchaseSupplierRankingContextInput(StrictModel):
    group_id: int = Field(ge=1)


class PurchaseSupplierRankAdjustmentProposalInput(StrictModel):
    split_group_id: int = Field(ge=1)
    target_candidate_id: int = Field(ge=1)
    expected_snapshot_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    to_rank: int = Field(ge=1, le=10000)
    reason: str = Field(min_length=1, max_length=500)

    @model_validator(mode="after")
    def normalize_reason(self):
        self.reason = self.reason.strip()
        if not self.reason:
            raise ValueError("供应商顺位调整原因不能为空")
        return self


INPUTS = {
    "prepare_raw_material_split": RawMaterialSplitProposalInput,
    "prepare_purchase_decision": PurchaseDecisionProposalInput,
    "prepare_hardware_quote": HardwareQuoteProposalInput,
    "prepare_raw_material_order": PurchaseOrderProposalInput,
    "prepare_hardware_order": PurchaseOrderProposalInput,
    "prepare_supplier_delivery_change": SupplierDeliveryChangeProposalInput,
    "prepare_purchase_claim": PurchaseClaimProposalInput,
    "prepare_hardware_inquiry": HardwareInquiryProposalInput,
    "prepare_supplier_order_decision": SupplierOrderDecisionProposalInput,
    "prepare_supplier_quote_submit": SupplierQuoteSubmitProposalInput,
    "prepare_supplier_delivery_create": SupplierDeliveryCreateProposalInput,
    "prepare_supplier_exception": SupplierExceptionProposalInput,
    "prepare_purchase_split_adjustment": PurchaseSplitAdjustmentPreviewProposalInput,
    "prepare_purchase_split_adjustment_submit": PurchaseSplitAdjustmentSubmitProposalInput,
    "prepare_purchase_temporary_group_save": PurchaseTemporaryGroupSaveProposalInput,
    "prepare_purchase_temporary_group_delete": PurchaseTemporaryGroupDeleteProposalInput,
    "prepare_purchase_repurchase_submit": PurchaseRepurchaseSubmitProposalInput,
    "prepare_purchase_supplier_rank_adjustment": PurchaseSupplierRankAdjustmentProposalInput,
}

PROPOSAL_TOOLS = frozenset(INPUTS)


def procurement_context_schema():
    return ProcurementContextInput.model_json_schema()


def proposal_schema(key: str):
    return INPUTS[key].model_json_schema()


def _project_card(db, user, project, matched_by=()):
    fields = access(db, user, "project.read", {"project_id": project.id}).fields
    card = {
        "id": project.id,
        "code": project.code,
        "name": project.name,
        "status": project.status,
        "row_version": project.row_version,
        "matched_by": sorted(set(matched_by)),
    }
    return {key: value for key, value in card.items() if key in fields or key == "matched_by"}


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


def _resolve_project(db, user, data: ProcurementContextInput):
    visible, truncated = _visible_projects(db, user)
    by_id = {row.id: row for row in visible}
    if data.project_id:
        return by_id.get(data.project_id), [], truncated

    needle = data.identifier.casefold()
    scores = defaultdict(int)
    reasons = defaultdict(list)
    for project in visible:
        for value, label in ((project.id, "项目ID"), (project.code, "项目编号"), (project.name, "项目名称")):
            value_text = str(value or "").casefold()
            score = 100 if value_text == needle else 50 if needle in value_text else 0
            if score:
                scores[project.id] = max(scores[project.id], score)
                reasons[project.id].append(label)
        # A local mold number is an explicit lookup clue, but never replaces
        # the confirmed Agent project-to-ERP mapping.
        mold_rows = db.execute(
            select(m.Mold.internal_number)
            .join(m.ProjectMold, m.ProjectMold.mold_id == m.Mold.id)
            .where(m.ProjectMold.project_id == project.id)
            .limit(20)
        )
        if any(str(row[0] or "").casefold() == needle for row in mold_rows):
            scores[project.id] = max(scores[project.id], 100)
            reasons[project.id].append("模具号")
    if not scores:
        return None, None, truncated
    best = max(scores.values())
    ids = [pid for pid, score in scores.items() if score == best]
    if len(ids) != 1:
        return None, [_project_card(db, user, by_id[pid], reasons[pid]) for pid in ids[:20]], truncated
    return by_id[ids[0]], reasons[ids[0]], truncated


def _mold_numbers(db, project_id):
    rows = db.execute(
        select(m.Mold.internal_number)
        .join(m.ProjectMold, m.ProjectMold.mold_id == m.Mold.id)
        .where(m.ProjectMold.project_id == project_id)
        .order_by(m.Mold.internal_number)
        .limit(20)
    )
    return [str(row[0]).strip() for row in rows if row[0]]


def _client_for_user(db, user):
    if not settings().erp_base_url:
        raise DomainError("ERP_NOT_CONFIGURED", "ERP 服务地址未配置，暂不能执行采购迁移工具", 503)
    identity = db.get(m.ERPIdentity, user.id)
    if not identity or not identity.token_ciphertext:
        raise DomainError("ERP_LOGIN_REQUIRED", "当前用户尚未绑定或验证 ERP 身份", 401)
    client = ERPClient(decrypt(identity.token_ciphertext))
    return client, identity


def _erp_context(db, user, project, *, category=None, group_id=None):
    result = {
        "status": "NOT_CONFIGURED",
        "project_code": project.code,
        "mold_numbers": _mold_numbers(db, project.id),
        "decision_groups": [],
        "execution": None,
        "quote_approval_preview": None,
        "source_system": "ERP",
        "as_of": now().isoformat(),
        "limitations": [],
    }
    try:
        client, _identity = _client_for_user(db, user)
    except DomainError as error:
        result["status"] = error.code
        result["limitations"].append(error.message)
        return result
    try:
        mold_no = result["mold_numbers"][0] if result["mold_numbers"] else None
        result["decision_groups"] = client.purchase_decision_groups(
            mold_no=mold_no,
            project_no=project.code,
            material_category=category,
        )
        result["execution"] = client.procurement_execution_context(
            mold_no=mold_no,
            project_no=project.code,
        )
        if group_id:
            selected = client.group(group_id)
            scope_values = {
                str(selected.get(key) or "").strip()
                for key in ("projectNo", "project_no", "projectId", "project_id", "moldNo", "mold_no")
            }
            expected_scope = {str(project.code).strip(), *result["mold_numbers"]}
            if not scope_values.intersection(expected_scope):
                raise DomainError("ERP_SCOPE_UNRESOLVED", "采购分组不属于当前项目或模具范围", 403)
            result["selected_group"] = selected
            result["quote_approval_preview"] = client.quote_approval_preview(group_id)
        result["status"] = "RESOLVED"
        result["as_of"] = now().isoformat()
        result["limitations"].append(
            "采购订单、供应商发货、入库、库存和质检事实来自 ERP；MoldPilot 不复制或覆盖 ERP 台账。"
        )
    except DomainError as error:
        result["status"] = error.code
        result["limitations"].append(error.message)
    finally:
        client.close()
    return normalized(result)


def _query(db, user, data: ProcurementContextInput, *, category=None, label="采购"):
    project, alternatives, truncated = _resolve_project(db, user, data)
    limitations = [
        "仅返回当前用户可见项目和 ERP 当前用户授权范围内的事实。",
        "本工具只读，不创建订单、不确认供应商接单、不修改发货、入库、库存或质检状态。",
    ]
    if truncated:
        limitations.append("最多检查前500个可见项目，结果可能未覆盖全部项目。")
    if not project:
        if alternatives is None:
            return {
                "resolution": "NOT_FOUND_OR_FORBIDDEN",
                "data": [],
                "source": "agent_db",
                "as_of": now().isoformat(),
                "limitations": limitations,
            }
        if alternatives:
            return {
                "resolution": "MULTIPLE_CANDIDATES",
                "data": alternatives,
                "source": "agent_db",
                "as_of": now().isoformat(),
                "limitations": limitations + ["线索命中多个项目，请使用项目 UUID 或完整编号。"],
            }
        return {
            "resolution": "NOT_FOUND",
            "data": [],
            "source": "agent_db",
            "as_of": now().isoformat(),
            "limitations": limitations,
        }

    erp = _erp_context(db, user, project, category=category, group_id=data.group_id)
    return {
        "resolution": "RESOLVED",
        "data": [{
            "project": _project_card(db, user, project, alternatives or ("项目定位",)),
            "business_kind": label,
            "erp": erp,
        }],
        "source": "erp",
        "as_of": erp.get("as_of", now().isoformat()),
        "limitations": limitations + erp.get("limitations", []),
    }


def query_raw_material_purchase_context(db, user, data, allowed_tools=None):
    return _query(db, user, data, category="steel", label="原材/钢料采购")


def query_hardware_purchase_context(db, user, data, allowed_tools=None):
    return _query(db, user, data, category="hardware", label="五金采购")


def query_supplier_procurement_context(db, user, data, allowed_tools=None):
    return _query(db, user, data, category=None, label="供应商采购履约")


def query_purchase_decision_context(db, user, data, allowed_tools=None):
    return _query(db, user, data, category=None, label="采购决策")


def _workbench_context(db, user, project=None, request_id=None):
    result = {
        "status": "NOT_CONFIGURED",
        "request_records": [],
        "split_records": [],
        "selected_request": None,
        "source_system": "ERP",
        "as_of": now().isoformat(),
        "limitations": [],
    }
    try:
        client, _identity = _client_for_user(db, user)
    except DomainError as error:
        result["status"] = error.code
        result["limitations"].append(error.message)
        return result
    try:
        params = {}
        if project:
            mold_numbers = _mold_numbers(db, project.id)
            params.update({"projectNo": project.code})
            if mold_numbers:
                params["moldNo"] = mold_numbers[0]
        if request_id:
            params["requestId"] = request_id
        result["request_records"] = client.purchase_request_list(params)
        result["split_records"] = client.purchase_workbench_split_list(params)
        if request_id:
            result["selected_request"] = client.purchase_request_detail(request_id)
        result["status"] = "RESOLVED"
        result["as_of"] = now().isoformat()
        result["limitations"].append(
            "采购申请、拆单工作台和认领状态来自 ERP；MoldPilot 不复制采购申请或拆组台账。"
        )
    except DomainError as error:
        result["status"] = error.code
        result["limitations"].append(error.message)
    finally:
        client.close()
    return normalized(result)


def _supplier_portal_context(db, user, data: SupplierPortalContextInput):
    result = {
        "status": "NOT_CONFIGURED",
        "quote_tasks": [],
        "purchase_orders": [],
        "deliveries": [],
        "selected_quote_task": None,
        "selected_order": None,
        "source_system": "ERP",
        "as_of": now().isoformat(),
        "limitations": [],
    }
    try:
        client, _identity = _client_for_user(db, user)
    except DomainError as error:
        result["status"] = error.code
        result["limitations"].append(error.message)
        return result
    try:
        params = {}
        if data.mold_no:
            params["moldNo"] = data.mold_no
        if data.order_id:
            params["orderId"] = data.order_id
        if data.task_id:
            params["taskId"] = data.task_id
        if data.identifier:
            params["keyword"] = data.identifier
        result["quote_tasks"] = client.supplier_quote_task_list(params)
        result["purchase_orders"] = client.supplier_purchase_order_list(params)
        result["deliveries"] = client.supplier_delivery_list(params)
        if data.task_id:
            result["selected_quote_task"] = client.supplier_quote_task_detail(data.task_id)
        if data.order_id:
            result["selected_order"] = client.supplier_purchase_order_detail(data.order_id)
        result["status"] = "RESOLVED"
        result["as_of"] = now().isoformat()
    except DomainError as error:
        result["status"] = error.code
        result["limitations"].append(error.message)
    finally:
        client.close()
    return normalized(result)


def query_purchase_workbench_context(db, user, data, allowed_tools=None):
    project = None
    if data.project_id or data.identifier:
        locator = ProcurementContextInput(project_id=data.project_id, identifier=data.identifier)
        project, alternatives, truncated = _resolve_project(db, user, locator)
        if not project:
            return {
                "resolution": "MULTIPLE_CANDIDATES" if alternatives else "NOT_FOUND",
                "data": alternatives or [],
                "source": "agent_db",
                "as_of": now().isoformat(),
                "limitations": ["采购工作台必须先定位唯一 Agent 项目。"],
                "truncated": truncated,
            }
    workbench = _workbench_context(db, user, project, data.request_id)
    return {
        "resolution": "RESOLVED",
        "data": [{
            "project": _project_card(db, user, project, ("项目定位",)) if project else None,
            "workbench": workbench,
        }],
        "source": "erp",
        "as_of": workbench.get("as_of", now().isoformat()),
        "limitations": [
            "本工具只读；认领、拆单、询价和正式下单必须单独生成确认提案。",
        ] + workbench.get("limitations", []),
    }


def query_supplier_portal_context(db, user, data, allowed_tools=None):
    context = _supplier_portal_context(db, user, data)
    return {
        "resolution": "RESOLVED" if context.get("status") == "RESOLVED" else context.get("status"),
        "data": [context],
        "source": "erp",
        "as_of": context.get("as_of", now().isoformat()),
        "limitations": [
            "供应商门户查询使用当前 ERP 身份；不能用采购员本地状态推断供应商已接单、已报价或已发货。",
        ] + context.get("limitations", []),
    }


def _purchase_split_adjustment_context(db, user, data):
    result = {
        "status": "NOT_CONFIGURED",
        "context": None,
        "history": [],
        "selected_adjustment": None,
        "source_system": "ERP",
        "as_of": now().isoformat(),
        "limitations": [],
    }
    try:
        client, _identity = _client_for_user(db, user)
    except DomainError as error:
        result["status"] = error.code
        result["limitations"].append(error.message)
        return result
    try:
        result["context"] = client.purchase_split_adjustment_context(data.request_id)
        result["history"] = client.purchase_split_adjustment_history({"requestId": data.request_id})
        if data.adjustment_id:
            result["selected_adjustment"] = client.purchase_split_adjustment_detail(data.adjustment_id)
        result["status"] = "RESOLVED"
        result["as_of"] = now().isoformat()
        result["limitations"].append(
            "拆组调整上下文、版本、快照和历史来自 ERP；MoldPilot 不复制原采购组或调整台账。"
        )
    except DomainError as error:
        result["status"] = error.code
        result["limitations"].append(error.message)
    finally:
        client.close()
    return normalized(result)


def query_purchase_adjustment_context(db, user, data, allowed_tools=None):
    context = _purchase_split_adjustment_context(db, user, data)
    return {
        "resolution": "RESOLVED" if context.get("status") == "RESOLVED" else context.get("status"),
        "data": [context],
        "source": "erp",
        "as_of": context.get("as_of", now().isoformat()),
        "limitations": [
            "本工具只读；拆组预览和提交必须分别生成确认提案，并以 ERP 当前版本和快照为准。",
        ] + context.get("limitations", []),
    }


def _purchase_repurchase_context(db, user, data):
    result = {
        "status": "NOT_CONFIGURED",
        "batch": None,
        "suppliers": [],
        "source_system": "ERP",
        "as_of": now().isoformat(),
        "limitations": [],
    }
    try:
        client, _identity = _client_for_user(db, user)
    except DomainError as error:
        result["status"] = error.code
        result["limitations"].append(error.message)
        return result
    try:
        result["batch"] = (
            client.manual_dispatch_detail(data.batch_id)
            if data.batch_id else client.manual_dispatch_by_order(data.order_id)
        )
        result["suppliers"] = client.manual_dispatch_suppliers()
        result["status"] = "RESOLVED"
        result["as_of"] = now().isoformat()
        result["limitations"].append(
            "无人接单重采批次、逐零件价格和供应商候选来自 ERP；MoldPilot 不本地计算正式金额或审批结果。"
        )
    except DomainError as error:
        result["status"] = error.code
        result["limitations"].append(error.message)
    finally:
        client.close()
    return normalized(result)


def query_purchase_repurchase_context(db, user, data, allowed_tools=None):
    context = _purchase_repurchase_context(db, user, data)
    return {
        "resolution": "RESOLVED" if context.get("status") == "RESOLVED" else context.get("status"),
        "data": [context],
        "source": "erp",
        "as_of": context.get("as_of", now().isoformat()),
        "limitations": [
            "本工具只读；逐零件重采提交必须以 ERP 当前批次、快照和两级审批人信息生成确认提案。",
        ] + context.get("limitations", []),
    }


def query_purchase_supplier_ranking_context(db, user, data, allowed_tools=None):
    try:
        client, _identity = _client_for_user(db, user)
    except DomainError as error:
        return {
            "resolution": error.code, "data": [], "source": "erp",
            "as_of": now().isoformat(), "limitations": [error.message],
        }
    try:
        ranking = client.purchase_supplier_ranking(data.group_id)
        return {
            "resolution": "RESOLVED", "data": [ranking], "source": "erp",
            "as_of": now().isoformat(),
            "limitations": ["候选顺位和快照来自 ERP；本工具只读，调整必须另行确认。"],
        }
    except DomainError as error:
        return {
            "resolution": error.code, "data": [], "source": "erp",
            "as_of": now().isoformat(), "limitations": [error.message],
        }
    finally:
        client.close()


def _group_snapshot(db, user, group_id):
    client, _identity = _client_for_user(db, user)
    try:
        group = client.group(group_id)
        return normalized(group or {})
    finally:
        client.close()


def _proposal_display(key, data, snapshot):
    names = {
        "prepare_raw_material_split": "提交原材拆单路径",
        "prepare_purchase_decision": "确认 ERP 采购决策",
        "prepare_hardware_quote": "提交五金报价审批材料",
        "prepare_raw_material_order": "生成原材采购订单",
        "prepare_hardware_order": "生成五金采购订单",
        "prepare_supplier_delivery_change": "提交供应商交期变更申请",
        "prepare_purchase_claim": "认领 ERP 采购申请",
        "prepare_hardware_inquiry": "发送五金询价",
        "prepare_supplier_order_decision": "处理供应商采购订单决定",
        "prepare_supplier_quote_submit": "提交供应商报价",
        "prepare_supplier_delivery_create": "创建供应商发货单",
        "prepare_supplier_exception": "提交供应商交付异常",
        "prepare_purchase_split_adjustment": "预览采购拆组调整",
        "prepare_purchase_split_adjustment_submit": "提交采购拆组调整",
        "prepare_purchase_temporary_group_save": "保存采购临时分组",
        "prepare_purchase_temporary_group_delete": "删除采购临时分组",
        "prepare_purchase_repurchase_submit": "提交无人接单重采审批",
        "prepare_purchase_supplier_rank_adjustment": "调整采购供应商候选顺位",
    }
    return {
        "操作": names[key],
        "工具": key,
        "参数": data.model_dump(mode="json"),
        "ERP 当前快照": snapshot,
        "说明": "本次只生成确认卡；本人确认后重新校验权限和 ERP 版本，再调用受控 ERP 接口。",
    }


def execute_tool(db, user, key, arguments, run=None):
    if key == "query_purchase_workbench_context":
        try:
            data = PurchaseWorkbenchContextInput.model_validate(arguments or {})
        except ValidationError as error:
            raise DomainError("INVALID_TOOL_INPUT", "采购工作台查询参数无效：" + error.errors()[0]["msg"]) from None
        return query_purchase_workbench_context(db, user, data)
    if key == "query_supplier_portal_context":
        try:
            data = SupplierPortalContextInput.model_validate(arguments or {})
        except ValidationError as error:
            raise DomainError("INVALID_TOOL_INPUT", "供应商门户查询参数无效：" + error.errors()[0]["msg"]) from None
        return query_supplier_portal_context(db, user, data)
    if key == "query_purchase_adjustment_context":
        try:
            data = PurchaseAdjustmentContextInput.model_validate(arguments or {})
        except ValidationError as error:
            raise DomainError("INVALID_TOOL_INPUT", "采购调整查询参数无效：" + error.errors()[0]["msg"]) from None
        return query_purchase_adjustment_context(db, user, data)
    if key == "query_purchase_repurchase_context":
        try:
            data = PurchaseRepurchaseContextInput.model_validate(arguments or {})
        except ValidationError as error:
            raise DomainError("INVALID_TOOL_INPUT", "重采查询参数无效：" + error.errors()[0]["msg"]) from None
        return query_purchase_repurchase_context(db, user, data)
    if key == "query_purchase_supplier_ranking_context":
        try:
            data = PurchaseSupplierRankingContextInput.model_validate(arguments or {})
        except ValidationError as error:
            raise DomainError("INVALID_TOOL_INPUT", "供应商候选顺位查询参数无效：" + error.errors()[0]["msg"]) from None
        return query_purchase_supplier_ranking_context(db, user, data)
    if key.startswith("query_"):
        try:
            data = ProcurementContextInput.model_validate(arguments or {})
        except ValidationError as error:
            raise DomainError("INVALID_TOOL_INPUT", "采购查询参数无效：" + error.errors()[0]["msg"]) from None
        if key == "query_raw_material_purchase_context":
            return query_raw_material_purchase_context(db, user, data)
        if key == "query_hardware_purchase_context":
            return query_hardware_purchase_context(db, user, data)
        if key == "query_supplier_procurement_context":
            return query_supplier_procurement_context(db, user, data)
        return query_purchase_decision_context(db, user, data)
    if key not in PROPOSAL_TOOLS:
        raise DomainError("TOOL_UNKNOWN", "采购迁移工具未登记", 403)
    if not isinstance(arguments, dict):
        raise DomainError("INVALID_TOOL_INPUT", "采购操作参数必须是对象")
    try:
        data = INPUTS[key].model_validate(arguments)
    except ValidationError as error:
        raise DomainError("INVALID_TOOL_INPUT", "采购操作参数无效：" + error.errors()[0]["msg"]) from None
    if key == "prepare_supplier_delivery_change":
        # Delivery modification is supported by the ERP contract, but the
        # preview must be checked again by the trusted confirmation handler.
        snapshot = {"delivery_id": data.delivery_id}
    elif key == "prepare_purchase_claim":
        snapshot = {"request_id": data.request_id}
    elif key in {
        "prepare_supplier_order_decision", "prepare_supplier_delivery_create",
        "prepare_supplier_exception", "prepare_supplier_quote_submit",
    }:
        snapshot = {
            "native_id": getattr(data, "order_id", None) or getattr(data, "task_id", None),
            "tool": key,
        }
    elif key == "prepare_purchase_split_adjustment_submit":
        snapshot = {"native_id": data.adjustment_id, "tool": key}
    elif key == "prepare_purchase_split_adjustment":
        snapshot = {"native_id": data.split_group_id, "tool": key}
    elif key in {"prepare_purchase_temporary_group_save", "prepare_purchase_temporary_group_delete"}:
        snapshot = {"request_id": data.request_id, "tool": key}
    elif key == "prepare_purchase_repurchase_submit":
        snapshot = {"native_id": data.batch_id, "tool": key}
    elif key == "prepare_purchase_supplier_rank_adjustment":
        snapshot = {"native_id": data.split_group_id, "tool": key}
    else:
        snapshot = _group_snapshot(db, user, getattr(data, "group_id", 0))
    proposal = {
        "kind": "erp_procurement_action",
        "action": "procurement_erp.execute",
        "tool": key,
        "requires_approval": True,
        "input": data.model_dump(mode="json"),
        "snapshot": snapshot,
        "display": _proposal_display(key, data, snapshot),
        "confirmation_policy": proposal_confirmation_policy(run, requires_approval=True),
    }
    return {
        "data": [],
        "source": "agent_proposal",
        "as_of": now().isoformat(),
        "proposal": proposal,
        "limitations": [
            "本轮未执行 ERP 正式动作；只有人工确认接口会调用 ERP。",
            "确认时会重新校验 ERP 当前对象版本、用户身份和业务权限。",
        ],
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
    proposal = step.result.get("proposal") if isinstance(step.result, dict) else None
    if step.tool not in available_tools(db, user) or step.tool not in PROPOSAL_TOOLS or not proposal:
        raise DomainError("TOOL_FORBIDDEN", "采购操作能力不可用", 403)
    return proposal


def _fresh_group_check(db, user, proposal):
    data = proposal.get("input") or {}
    group_id = data.get("group_id")
    tool = proposal.get("tool")
    if group_id:
        current = _group_snapshot(db, user, int(group_id))
    else:
        client, _identity = _client_for_user(db, user)
        try:
            if tool == "prepare_purchase_claim" and data.get("request_id"):
                current = client.purchase_request_detail(int(data["request_id"]))
            elif tool in {"prepare_supplier_quote_submit"} and data.get("task_id"):
                current = client.supplier_quote_task_detail(int(data["task_id"]))
            elif tool in {"prepare_supplier_order_decision", "prepare_supplier_delivery_create", "prepare_supplier_exception"} and data.get("order_id"):
                current = client.supplier_purchase_order_detail(int(data["order_id"]))
            elif tool == "prepare_purchase_split_adjustment_submit" and data.get("adjustment_id"):
                current = client.purchase_split_adjustment_detail(int(data["adjustment_id"]))
            elif tool in {"prepare_purchase_temporary_group_save", "prepare_purchase_temporary_group_delete"} and data.get("request_id"):
                current = client.purchase_split_adjustment_context(int(data["request_id"]))
            elif tool == "prepare_purchase_repurchase_submit" and data.get("batch_id"):
                current = client.manual_dispatch_detail(int(data["batch_id"]))
            elif tool == "prepare_purchase_supplier_rank_adjustment" and data.get("split_group_id"):
                current = client.purchase_supplier_ranking(int(data["split_group_id"]))
            else:
                return None
        finally:
            client.close()
    expected_status = data.get("expected_status")
    if expected_status and str(current.get("groupStatus") or current.get("status") or "") != str(expected_status):
        raise DomainError("VERSION_CONFLICT", "ERP 采购分组状态已变化，请重新查询并准备操作", 409)
    expected_version = data.get("expected_version")
    if expected_version is not None:
        actual = current.get("version") or current.get("versionNo") or current.get("rowVersion") or current.get("claim_version")
        if actual is not None and int(actual) != int(expected_version):
            raise DomainError("VERSION_CONFLICT", "ERP 采购分组版本已变化，请重新查询并准备操作", 409)
    expected_snapshot_hash = data.get("expected_snapshot_hash")
    if expected_snapshot_hash:
        actual_hash = current.get("snapshotHash") or current.get("snapshot_hash") or current.get("sourceSnapshotHash")
        if actual_hash and str(actual_hash) != str(expected_snapshot_hash):
            raise DomainError("VERSION_CONFLICT", "ERP 采购候选快照已变化，请重新查询并准备操作", 409)
    return current


def validate_intent(db, user, payload):
    proposal = source(db, user, payload["step_id"])
    if content_hash(proposal) != payload.get("proposal_hash"):
        raise DomainError("CONFIRMATION_INVALID", "操作建议内容已变化，请重新准备", 409)
    _fresh_group_check(db, user, proposal)
    return proposal


def _operation_record(db, user, payload, proposal, identity):
    intent = db.scalar(
        select(m.HumanIntent)
        .where(
            m.HumanIntent.user_id == user.id,
            m.HumanIntent.action == "procurement_erp.execute",
            m.HumanIntent.resource_id == payload["step_id"],
        )
        .order_by(m.HumanIntent.created_at.desc())
    )
    if not intent:
        raise DomainError("CONFIRMATION_INVALID", "找不到对应的人工确认意图", 409)
    operation = db.scalar(select(m.ERPOperation).where(m.ERPOperation.intent_id == intent.id))
    if operation:
        if operation.state == "SUCCEEDED" and operation.response:
            return operation, intent
        if operation.state in {"DISPATCHING", "UNKNOWN"}:
            raise DomainError("ERP_OUTCOME_UNKNOWN", "该确认动作已有未核对的 ERP 操作，请先对账", 409)
    operation = m.ERPOperation(
        user_id=user.id,
        intent_id=intent.id,
        action=proposal.get("tool", "procurement"),
        native_id=str(
            (proposal.get("input") or {}).get("group_id")
            or (proposal.get("input") or {}).get("delivery_id")
            or (proposal.get("input") or {}).get("request_id")
            or (proposal.get("input") or {}).get("task_id")
            or (proposal.get("input") or {}).get("order_id")
            or (proposal.get("input") or {}).get("split_group_id")
            or (proposal.get("input") or {}).get("adjustment_id")
            or (proposal.get("input") or {}).get("batch_id")
            or "-"
        ),
        state="DISPATCHING",
        request_hash=content_hash(proposal),
        erp_user_id=identity.erp_user_id,
    )
    db.add(operation)
    db.flush()
    return operation, intent


def confirm(db, user, payload):
    proposal = validate_intent(db, user, payload)
    client, identity = _client_for_user(db, user)
    operation, _intent = _operation_record(db, user, payload, proposal, identity)
    data = proposal.get("input") or {}
    tool = proposal.get("tool")
    try:
        if tool == "prepare_purchase_decision":
            request_payload = {
                key: value
                for key, value in {
                    "decision": data.get("decision"),
                    "supplierId": data.get("supplier_id"),
                    "finalConfirmedPrice": data.get("final_confirmed_price"),
                    "deliveryDate": data.get("delivery_date"),
                    "remark": data.get("remark"),
                }.items()
                if value not in (None, "")
            }
            result = client.confirm_purchase_decision(int(data["group_id"]), request_payload)
        elif tool == "prepare_hardware_quote":
            request_payload = {
                "supplierId": data["supplier_id"],
                "unitPrice": data["unit_price"],
                "deliveryDate": data["delivery_date"],
                "approverId": data["approver_id"],
                "remark": data.get("remark", ""),
            }
            result = client.submit_hardware_quote(int(data["group_id"]), request_payload)
        elif tool in {"prepare_raw_material_order", "prepare_hardware_order"}:
            result = client.create_purchase_order(int(data["group_id"]))
        elif tool == "prepare_raw_material_split":
            request_payload = {
                "requestId": data["request_id"],
                "splitMode": data["split_mode"],
                "autoRoute": data["auto_route"],
                "expectedVersion": data.get("expected_version"),
                "receiveSiteId": data.get("receive_site_id"),
            }
            result = client.confirm_split_workbench(request_payload)
        elif tool == "prepare_supplier_delivery_change":
            request_payload = {
                key: value
                for key, value in {
                    "expectedVersion": data.get("expected_version"),
                    "expectedDeliveryDate": data.get("expected_delivery_date"),
                    "expectedQuantity": data.get("expected_quantity"),
                    "reason": data.get("reason"),
                }.items()
                if value not in (None, "")
            }
            result = client.create_supplier_delivery_modify_request(
                int(data["delivery_id"]), request_payload
            )
        elif tool == "prepare_purchase_claim":
            result = client.claim_purchase_request(
                int(data["request_id"]),
                {
                    key: value for key, value in {
                        "expectedVersion": data.get("expected_version"),
                        "materialCategory": data.get("material_category"),
                    }.items() if value not in (None, "unknown", "")
                },
            )
        elif tool == "prepare_hardware_inquiry":
            result = client.send_group_inquiry(
                int(data["group_id"]),
                {
                    key: value for key, value in {
                        "expectedVersion": data.get("expected_version"),
                        "supplierIds": data.get("supplier_ids") or None,
                        "remark": data.get("remark"),
                    }.items() if value not in (None, "")
                },
            )
        elif tool == "prepare_supplier_order_decision":
            request_payload = {
                key: value for key, value in {
                    "expectedVersion": data.get("expected_version"),
                    "detailIds": data.get("detail_ids") or None,
                    "promisedDeliveryDate": data.get("promised_delivery_date"),
                    "reason": data.get("reason"),
                }.items() if value not in (None, "")
            }
            result = client.decide_supplier_purchase_order(
                int(data["order_id"]), data["decision"], request_payload
            )
        elif tool == "prepare_supplier_quote_submit":
            result = client.submit_supplier_quote(
                int(data["task_id"]),
                {
                    "lines": [
                        {
                            key: value for key, value in {
                                "materialId": line["material_id"],
                                "unitPrice": line["unit_price"],
                                "taxRate": line["tax_rate"],
                                "deliveryDate": line["delivery_date"],
                                "quantity": line.get("quantity"),
                            }.items() if value not in (None, "")
                        }
                        for line in data["lines"]
                    ],
                    "remark": data.get("remark", ""),
                },
            )
        elif tool == "prepare_supplier_delivery_create":
            result = client.create_supplier_delivery({
                "orderId": data["order_id"],
                "destinationId": data["destination_id"],
                "lines": [
                    {
                        key: value for key, value in {
                            "detailId": line["detail_id"],
                            "quantity": line["quantity"],
                            "deliveryDate": line.get("delivery_date"),
                        }.items() if value not in (None, "")
                    }
                    for line in data["lines"]
                ],
                "remark": data.get("remark", ""),
            })
        elif tool == "prepare_supplier_exception":
            result = client.submit_supplier_exception({
                key: value for key, value in {
                    "orderId": data["order_id"],
                    "detailId": data.get("detail_id"),
                    "exceptionType": data["exception_type"],
                    "requestedDeliveryDate": data.get("requested_delivery_date"),
                    "reason": data["reason"],
                }.items() if value not in (None, "")
            })
        elif tool == "prepare_purchase_split_adjustment":
            result = client.preview_purchase_split_adjustment({
                "splitGroupId": data["split_group_id"],
                "expectedGroupVersion": data["expected_group_version"],
                "expectedSnapshotHash": data["expected_snapshot_hash"],
                "reasonType": data["reason_type"],
                "reason": data["reason"],
                "groups": [
                    {
                        "groupKey": group["group_key"],
                        "groupName": group["group_name"],
                        "allocations": [
                            {
                                "groupDetailId": allocation["group_detail_id"],
                                "quantity": allocation["quantity"],
                            }
                            for allocation in group["allocations"]
                        ],
                    }
                    for group in data["groups"]
                ],
            })
        elif tool == "prepare_purchase_split_adjustment_submit":
            result = client.submit_purchase_split_adjustment(
                int(data["adjustment_id"]),
                {
                    key: value for key, value in {
                        "expectedVersion": data.get("expected_version"),
                        "expectedSnapshotHash": data.get("expected_snapshot_hash"),
                        "reason": data.get("reason"),
                    }.items() if value not in (None, "")
                },
            )
        elif tool == "prepare_purchase_temporary_group_save":
            result = client.save_purchase_temporary_group(
                int(data["request_id"]),
                {
                    "clientGroupKey": data["client_group_key"],
                    "displayName": data["display_name"],
                    "bizType": data["biz_type"],
                    "categoryId": data.get("category_id"),
                    "categoryName": data["category_name"],
                    "applicableDetailIds": data["applicable_detail_ids"],
                    "supplierIds": data["supplier_ids"],
                    "receiveMode": "company_warehouse",
                    "receiveSiteId": data["receive_site_id"],
                    "remark": data.get("remark", ""),
                },
            )
        elif tool == "prepare_purchase_temporary_group_delete":
            result = client.delete_purchase_temporary_group(
                int(data["request_id"]), data["client_group_key"]
            )
        elif tool == "prepare_purchase_repurchase_submit":
            result = client.save_manual_dispatch_draft(
                int(data["batch_id"]),
                {
                    "expectedVersion": data["expected_version"],
                    "lines": [
                        {
                            "lineId": line["line_id"],
                            "supplierId": line["supplier_id"],
                            "unitPrice": line["unit_price"],
                        }
                        for line in data["lines"]
                    ],
                },
            )
            submit_payload = {
                "expectedVersion": result.get("statusVersion", data["expected_version"]),
                "approverOverrides": data.get("approver_overrides") or [],
            }
            result = client.submit_manual_dispatch(int(data["batch_id"]), submit_payload)
        elif tool == "prepare_purchase_supplier_rank_adjustment":
            request_payload = {
                "splitGroupId": data["split_group_id"],
                "targetCandidateId": data["target_candidate_id"],
                "expectedSnapshotHash": data["expected_snapshot_hash"],
                "toRank": data["to_rank"],
                "reason": data["reason"],
            }
            client.preview_supplier_rank_adjustment(request_payload)
            operation_key = str(operation.id)
            result = client.create_supplier_rank_adjustment_proposal({
                **request_payload,
                "sourceSessionId": f"moldpilot:{operation_key}",
                "targetSessionId": f"moldpilot:{operation_key}",
                "sourceMessageId": operation_key,
                "traceId": operation_key,
                "idempotencyKey": operation_key,
            })
        else:
            raise DomainError("TOOL_UNKNOWN", "采购正式动作未实现", 403)
        receipt = {
            "action": "procurement_erp.execute",
            "tool": tool,
            "status": "SUCCEEDED",
            "operation_id": operation.id,
            "source_system": "ERP",
            "source_ref": f"{tool}:{operation.native_id}",
            "source_as_of": now().isoformat(),
            "result": normalized(result or {}),
        }
        operation.state = "SUCCEEDED"
        operation.response = receipt
        return receipt
    except DomainError as error:
        operation.state = "UNKNOWN" if error.code == "ERP_OUTCOME_UNKNOWN" else "REJECTED"
        operation.error_code = error.code
        raise
    finally:
        client.close()
