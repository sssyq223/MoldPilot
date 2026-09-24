"""Prepare an ERP design-order snapshot for the local Agent BPM.

ERP remains the live design-data authority.  This adapter stores only the
immutable evidence package that a person actually submitted for one approval
round; it never mirrors ERP lists or calls ERP approval endpoints.
"""
from __future__ import annotations

import json
import secrets
from datetime import datetime
from typing import Any, Literal

from pydantic import Field, ValidationError, field_validator
from sqlalchemy import select

from domain_packs.mold import models as m, workflow_selection
from domain_packs.mold.authorization import fingerprint, require
from domain_packs.mold.ports.bpm import content_hash
from domain_packs.mold.ports.confirmation_policy import proposal_confirmation_policy
from domain_packs.mold.ports.db import now
from domain_packs.mold.ports.errors import DomainError
from domain_packs.mold.ports.events import record
from domain_packs.mold.ports.schemas import StrictModel
from domain_packs.mold.erp.design import design_documents
from domain_packs.mold.tools.erp.design.erp_design_mcp import call_mcp


TOOL_KEY = "prepare_design_order_approval"
MAX_SNAPSHOT_BYTES = 1024 * 1024


class DesignOrderApprovalInput(StrictModel):
    project_id: str = Field(min_length=1, max_length=36)
    project_version: int = Field(ge=1)
    erp_order_id: int = Field(ge=1, description="已通过 ERP 只读查询确认的设计订单 ID。")
    design_type: Literal["NEW_MOLD", "MOLD_CHANGE"]
    drawing_revision: str = Field(min_length=1, max_length=100)
    submission_note: str = Field(min_length=1, max_length=4000)
    reviewer_id: str = Field(min_length=1, max_length=36)
    workflow_definition_id: str = Field(min_length=1, max_length=36)
    file_ids: list[str] = Field(default_factory=list, max_length=20,
        description="本轮任务中明确上传的图纸、清单或说明附件 ID；审批将冻结精确文件版本。")
    material_review_id: str | None = Field(default=None, min_length=1, max_length=36,
        description="流程绑定资料模板时填写本人已确认的资料核对包 ID；否则保持 null。")

    @field_validator("file_ids")
    @classmethod
    def unique_file_ids(cls, value):
        if len(value) != len(set(value)):
            raise ValueError("设计审批附件不能重复")
        return value


def schema():
    return DesignOrderApprovalInput.model_json_schema()


def parse(arguments):
    try:
        return DesignOrderApprovalInput.model_validate(arguments or {})
    except ValidationError as error:
        raise DomainError("INVALID_TOOL_INPUT", "设计订单审批参数无效：" + error.errors()[0]["msg"]) from None


def _unwrap_record(value: Any) -> dict:
    if not isinstance(value, dict):
        raise DomainError("ERP_DESIGN_RECORD_INVALID", "ERP 设计订单详情结构无效", 502)
    current = value
    for _ in range(3):
        candidate = next((current.get(key) for key in ("data", "record", "result")
                          if isinstance(current.get(key), dict)), None)
        if candidate is None:
            break
        current = candidate
    encoded = json.dumps(current, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str).encode("utf-8")
    if len(encoded) > MAX_SNAPSHOT_BYTES:
        raise DomainError("ERP_DESIGN_RECORD_TOO_LARGE", "ERP 设计订单详情超过审批快照上限，请先按订单范围收窄材料", 409)
    return json.loads(encoded.decode("utf-8"))


def _first(mapping: dict, *keys):
    for key in keys:
        value = mapping.get(key)
        if value not in (None, ""):
            return value
    return None


def _header(record_value: dict):
    return next((record_value.get(key) for key in ("request", "order", "header")
                 if isinstance(record_value.get(key), dict)), record_value)


def _rows(record_value: dict):
    candidates = [record_value, _header(record_value)]
    data = record_value.get("data")
    if isinstance(data, dict):
        candidates.append(data)
    for candidate in candidates:
        for key in ("items", "details", "rows", "requestDetails", "orderDetails", "detailList", "list"):
            value = candidate.get(key)
            if isinstance(value, list):
                return [item for item in value if isinstance(item, dict)]
    return []


def _item_cards(record_value: dict):
    result = []
    for index, row in enumerate(_rows(record_value)[:200], 1):
        card = {
            "sequence": _first(row, "seq", "sequence", "lineNo", "rowNo") or index,
            "part_number": _first(row, "partNo", "partNumber", "materialCode", "code", "drawingNo"),
            "part_name": _first(row, "partName", "materialName", "name"),
            "material": _first(row, "material", "materialMark", "materialName", "grade"),
            "specification": _first(row, "specification", "spec", "size", "dimensions"),
            "quantity": _first(row, "quantity", "qty", "purchaseQuantity", "count"),
            "route": _first(row, "route", "processRoute", "purchaseType", "processingType"),
            "status": _first(row, "status", "state", "approvalStatus"),
        }
        result.append({key: value for key, value in card.items() if value not in (None, "")})
    return result


def _material(order_id: int):
    record_value = _unwrap_record(call_mcp("get_erp_design_record", {"resource": "design_order", "id": order_id}))
    header = _header(record_value)
    actual_id = _first(header, "requestId", "designOrderId", "orderId", "id")
    if actual_id not in (None, "") and str(actual_id) != str(order_id):
        raise DomainError("ERP_DESIGN_RECORD_MISMATCH", "ERP 返回的设计订单与本次选择不一致", 409)
    snapshot_hash = content_hash(record_value)
    resource_version = _first(
        header, "approvalVersion", "approval_version", "rowVersion", "row_version",
        "detailVersion", "detail_version", "version", "updatedAt", "updateTime",
    ) or "snapshot:" + snapshot_hash[:16]
    items = _item_cards(record_value)
    summary = {
        "order_number": _first(header, "requestNo", "orderNo", "orderNumber", "number", "code") or str(order_id),
        "mold_number": _first(header, "moldNo", "moldNumber", "mouldNo", "moduleNo"),
        "status": _first(header, "status", "orderStatus", "approvalStatus", "state"),
        "business_type": _first(header, "businessType", "requestType", "designType", "type"),
        "item_count": len(_rows(record_value)),
        "items": items,
        "items_truncated": len(_rows(record_value)) > len(items),
    }
    return {
        "source_system": "management-system",
        "resource_type": "design_order",
        "resource_id": str(order_id),
        "resource_version": str(resource_version),
        "as_of": now().isoformat(),
        "snapshot_hash": snapshot_hash,
        "summary": summary,
        "snapshot": record_value,
    }


def workflow_options(db, user, project, design_type):
    scope = {"project_id": project.id}
    require(db, user, "project.read", scope)
    for permission in ("design_route.read", "design_route.create", "design_route.submit"):
        require(db, user, permission, scope)
    definitions = db.scalars(select(m.WorkflowDefinition).where(
        m.WorkflowDefinition.status == "PUBLISHED"
    ).order_by(m.WorkflowDefinition.process_key, m.WorkflowDefinition.version.desc()))
    result = []
    for definition in definitions:
        if workflow_selection.matches(definition.config, {
            "business_type": "design_route", "categories": set(), "design_type": design_type,
        }):
            result.append(workflow_selection.metadata(definition, db))
    return result


def _preview(db, user, data: DesignOrderApprovalInput, run, *, source_material=None, verify_external=False):
    project = db.get(m.Project, data.project_id)
    if not project:
        raise DomainError("NOT_FOUND", "项目不存在", 404)
    if project.row_version != data.project_version:
        raise DomainError("VERSION_CONFLICT", "项目状态已变化，请重新查询后准备", 409)
    if project.status in {"CLOSED", "TERMINATED"}:
        raise DomainError("PROJECT_BLOCKED", "项目已关闭或终止，不能提交设计审批", 409)
    reviewer = db.get(m.User, data.reviewer_id)
    if not reviewer or not reviewer.active:
        raise DomainError("ASSIGNMENT_BLOCKED", "指定设计复核人员不存在或已停用", 409)
    options = workflow_options(db, user, project, data.design_type)
    selected = next((item for item in options if item["id"] == data.workflow_definition_id), None)
    if not selected:
        raise DomainError("WORKFLOW_MISMATCH", "所选审批模板不适用于本次新模/改模设计材料", 409)
    material = source_material or _material(data.erp_order_id)
    if verify_external:
        current = _material(data.erp_order_id)
        if current["snapshot_hash"] != material.get("snapshot_hash"):
            raise DomainError("VERSION_CONFLICT", "ERP 设计订单已变化，请重新读取并准备审批材料", 409)
    duplicate = db.scalar(select(m.DesignDetail.subject_id).join(
        m.BusinessSubject, m.BusinessSubject.id == m.DesignDetail.subject_id
    ).where(
        m.BusinessSubject.project_id == project.id,
        m.BusinessSubject.kind == "design_route",
        m.BusinessSubject.status.in_(["DRAFT", "SUBMITTED", "RETURNED", "APPLY_BLOCKED", "EFFECTIVE"]),
        m.DesignDetail.source_system == material["source_system"],
        m.DesignDetail.source_resource_type == material["resource_type"],
        m.DesignDetail.source_resource_id == material["resource_id"],
        m.DesignDetail.source_snapshot_hash == material["snapshot_hash"],
    ).limit(1))
    if duplicate:
        raise DomainError("DESIGN_APPROVAL_DUPLICATE", "该 ERP 设计订单版本已经进入 Agent 审批，请勿重复提交", 409)
    blobs = design_documents.validate_proposal_files(db, user, data.file_ids, run, project.id)
    summary = material["summary"]
    display = {
        "操作": "提交设计订单审批",
        "项目": project.code + " · " + project.name,
        "项目版本": project.row_version,
        "设计类型": "新模" if data.design_type == "NEW_MOLD" else "改模",
        "图纸版本": data.drawing_revision,
        "ERP 设计订单": summary.get("order_number") or material["resource_id"],
        "ERP 订单状态": summary.get("status") or "未提供",
        "模具号": summary.get("mold_number") or "未提供",
        "ERP 记录版本": material["resource_version"],
        "ERP 快照哈希": material["snapshot_hash"],
        "订单明细数量": summary.get("item_count", 0),
        "本轮附件": [blob.filename for blob in blobs] or ["无额外聊天附件"],
        "指定复核人": reviewer.display_name,
        "提交说明": data.submission_note,
        "审批流程": selected["name"] + " · 第" + str(selected["version"]) + "版",
        "说明": "本人确认后只冻结 ERP 设计订单证据与本轮附件并提交 Agent BPM；不调用 ERP 设计审批，也不把快照当作 ERP 当前状态。",
    }
    return project, reviewer, selected, material, blobs, display


def execute_tool(db, user, key, arguments, run=None):
    if key != TOOL_KEY:
        raise DomainError("TOOL_UNKNOWN", "设计审批工具未实现", 403)
    data = parse(arguments)
    _, _, _, material, _, display = _preview(db, user, data, run)
    proposal = {
        "kind": "design_route",
        "action": "design_order_approval",
        "requires_approval": True,
        "input": data.model_dump(mode="json"),
        "source_material": material,
        "display": display,
        "confirmation_policy": proposal_confirmation_policy(run, requires_approval=True),
    }
    return {
        "data": [],
        "source": "agent_proposal",
        "as_of": now().isoformat(),
        "proposal": proposal,
        "limitations": [
            "仅准备本次 Agent BPM 设计审批；ERP 仍是设计订单、图纸版本、材质密度和标准件的实时权威来源。",
            "本人确认后才创建审批实例；后续 ERP 写入仍需独立、明确的业务确认。",
        ],
    }


def source(db, user, step_id):
    from domain_packs.mold.tool_gateway import available_tools
    step = db.get(m.Step, step_id)
    run = db.get(m.Run, step.run_id) if step else None
    if not run or run.user_id != user.id:
        raise DomainError("NOT_FOUND", "操作建议不存在或无权访问", 404)
    if run.status not in {"RUNNING", "RUNNING_SCOPED", "SUCCEEDED", "FAILED"}:
        raise DomainError("PROPOSAL_STOPPED", "任务已停止，请重新准备操作", 409)
    if run.security_version != user.security_version or run.checkpoint.get("authorization_hash") != fingerprint(db, user):
        raise DomainError("AUTHORIZATION_CHANGED", "授权已变化，请重新准备操作", 403)
    proposal = step.result.get("proposal")
    if step.tool not in available_tools(db, user) or step.tool != TOOL_KEY or not proposal:
        raise DomainError("TOOL_FORBIDDEN", "操作能力不可用", 403)
    return proposal


def validate_intent(db, user, payload):
    proposal = source(db, user, payload["step_id"])
    if content_hash(proposal) != payload["proposal_hash"]:
        raise DomainError("CONFIRMATION_INVALID", "操作建议内容已变化", 409)
    data = parse(proposal["input"])
    step = db.get(m.Step, payload["step_id"])
    run = db.get(m.Run, step.run_id) if step else None
    _, _, _, material, _, display = _preview(
        db, user, data, run, source_material=proposal.get("source_material"), verify_external=False,
    )
    if material.get("snapshot_hash") != proposal.get("source_material", {}).get("snapshot_hash"):
        raise DomainError("VERSION_CONFLICT", "ERP 设计订单版本已变化，请重新准备", 409)
    if content_hash(display) != content_hash(proposal.get("display")):
        raise DomainError("VERSION_CONFLICT", "项目、人员、权限、附件或流程资料已变化，请重新准备", 409)
    return proposal, data, material


def _create_subject(db, user, data, material, blobs):
    subject = m.BusinessSubject(
        kind="design_route",
        number="DESIGN-" + secrets.token_hex(5).upper(),
        project_id=data.project_id,
        created_by=user.id,
        remark=data.submission_note,
    )
    db.add(subject)
    db.flush()
    db.add(m.DesignDetail(
        subject_id=subject.id,
        design_type=data.design_type,
        drawing_revision=data.drawing_revision,
        drawing_evidence=data.submission_note,
        reviewer_id=data.reviewer_id,
        source_system=material["source_system"],
        source_resource_type=material["resource_type"],
        source_resource_id=material["resource_id"],
        source_resource_version=material["resource_version"],
        source_as_of=datetime.fromisoformat(material["as_of"]),
        source_snapshot_hash=material["snapshot_hash"],
        source_summary=material["summary"],
        source_snapshot=material["snapshot"],
    ))
    design_documents.link_initial(db, user, subject, blobs)
    record(db, user, "business.draft.created", subject.id, {
        "kind": "design_route",
        "source_system": material["source_system"],
        "source_resource_type": material["resource_type"],
        "source_resource_id": material["resource_id"],
        "source_resource_version": material["resource_version"],
        "source_snapshot_hash": material["snapshot_hash"],
    })
    return subject


def confirm(db, user, payload):
    from domain_packs.mold.ports.confirmation_policy import agent_permission_mode_from_proposal
    from domain_packs.mold.erp.core.business import submit_subject
    proposal, data, material = validate_intent(db, user, payload)
    step = db.get(m.Step, payload["step_id"])
    run = db.get(m.Run, step.run_id) if step else None
    _, _, _, current_material, blobs, _ = _preview(
        db, user, data, run, source_material=material, verify_external=True,
    )
    subject = _create_subject(db, user, data, current_material, blobs)
    submitted = submit_subject(
        db, user, subject.id, subject.revision, data.workflow_definition_id,
        data.material_review_id,
        agent_permission_mode=agent_permission_mode_from_proposal(proposal),
    )
    return {
        "project_id": data.project_id,
        "subject_id": subject.id,
        "instance_id": submitted["instance_id"],
        "erp_order_id": data.erp_order_id,
        "action": "design_order_approval",
        "status": "SUBMITTED",
    }
