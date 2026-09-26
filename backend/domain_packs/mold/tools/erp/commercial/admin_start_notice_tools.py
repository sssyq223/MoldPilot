"""超级管理员内部开工通知草稿的 Skill Tools。"""
from uuid import uuid4

from pydantic import Field, ValidationError
from sqlalchemy import select

from domain_packs.mold import models as m
from domain_packs.mold.authorization import fingerprint
from domain_packs.mold.erp.commercial.admin_start_workflow import (
    acknowledge_admin_start_department,
    confirm_admin_start_notice,
    dispatch_admin_start_notice,
    update_admin_start_notice_draft,
)
from domain_packs.mold.erp.commercial.contract_match_workflow import confirm_contract_match_candidate
from domain_packs.mold.ports.bpm import content_hash
from domain_packs.mold.ports.confirmation_policy import proposal_confirmation_policy
from domain_packs.mold.ports.db import now
from domain_packs.mold.ports.errors import DomainError
from domain_packs.mold.ports.schemas import StrictModel


class AdminStartNoticeQueryInput(StrictModel):
    draft_id: str | None = Field(default=None, min_length=1, max_length=36)
    project_query: str | None = Field(default=None, max_length=120)


class AdminStartNoticeUpdateInput(StrictModel):
    draft_id: str = Field(min_length=1, max_length=36)
    expected_row_version: int = Field(ge=1)
    material_snapshot: dict
    reason: str = Field(min_length=1, max_length=4000)


class AdminStartNoticeDecisionInput(AdminStartNoticeUpdateInput):
    decision: str = Field(pattern=r"^(INTERNAL_ACCEPTED|FULL_OUTSOURCE_ACCEPTED|REJECTED)$")


class AdminStartDepartmentDispatchInput(StrictModel):
    draft_id: str = Field(min_length=1, max_length=36)
    expected_row_version: int = Field(ge=1)
    department_keys: list[str] = Field(min_length=1, max_length=5)
    reason: str = Field(min_length=1, max_length=2000)


class AdminStartDepartmentAckInput(StrictModel):
    draft_id: str = Field(min_length=1, max_length=36)
    expected_row_version: int = Field(ge=1)
    department_key: str = Field(min_length=1, max_length=60)
    note: str = Field(min_length=1, max_length=2000)


class ContractMatchConfirmationInput(StrictModel):
    candidate_id: str = Field(min_length=1, max_length=36)
    expected_target_version: int = Field(ge=1)


INPUTS = {
    "query_admin_start_notices": AdminStartNoticeQueryInput,
    "prepare_admin_start_notice_update": AdminStartNoticeUpdateInput,
    "prepare_admin_start_notice_decision": AdminStartNoticeDecisionInput,
    "prepare_admin_start_department_dispatch": AdminStartDepartmentDispatchInput,
    "prepare_admin_start_department_ack": AdminStartDepartmentAckInput,
    "prepare_contract_match_confirmation": ContractMatchConfirmationInput,
}


def _require_admin(user):
    if not user or not user.super_admin:
        raise DomainError("SUPER_ADMIN_REQUIRED", "只有超级管理员可以处理内部开工通知", 403)


def query_admin_start_notices(db, user, *, draft_id=None, project_query=None):
    _require_admin(user)
    if draft_id:
        from domain_packs.mold.erp.commercial.admin_start_workflow import admin_start_source_context
        return {
            "data": [admin_start_source_context(db, user, draft_id, project_query=project_query)],
            "source": "admin_start_notice_source_context",
            "as_of": now().isoformat(),
        }
    rows = db.scalars(select(m.AdminStartNoticeDraft).where(
        m.AdminStartNoticeDraft.status.in_({
            "ADMIN_PENDING_INPUT", "NEEDS_REVIEW", "DEPARTMENTS_NOTIFIED",
            "READY_FOR_CONTRACT_MATCH",
        }),
    ).order_by(m.AdminStartNoticeDraft.created_at.desc()).limit(100))
    data = []
    for row in rows:
        departments = list(db.scalars(select(m.AdminStartDepartmentAck).where(
            m.AdminStartDepartmentAck.draft_id == row.id,
        ).order_by(m.AdminStartDepartmentAck.department_key)))
        data.append({
            "draft_id": row.id,
            "source_event_id": row.source_event_id,
            "source_file_id": row.source_file_id,
            "status": row.status,
            "decision": row.decision,
            "material_snapshot": row.material_snapshot,
            "current_revision": row.current_revision,
            "row_version": row.row_version,
            "departments": [{
                "department_key": ack.department_key,
                "status": ack.status,
                "notified_at": ack.notified_at,
                "acked_at": ack.acked_at,
            } for ack in departments],
        })
    return {
        "data": data,
        "source": "admin_start_notice_draft",
        "as_of": now().isoformat(),
    }


def _parse(key, arguments):
    try:
        return INPUTS[key].model_validate(arguments or {})
    except (KeyError, ValidationError) as error:
        message = error.errors()[0]["msg"] if hasattr(error, "errors") else "参数无效"
        raise DomainError("INVALID_TOOL_INPUT", f"内部开工工具参数无效：{message}") from None


def _draft(db, user, draft_id):
    _require_admin(user)
    row = db.get(m.AdminStartNoticeDraft, str(draft_id))
    if not row:
        raise DomainError("NOT_FOUND", "内部开工通知草稿不存在", 404)
    return row


def execute_tool(db, user, key, arguments, run=None):
    if key == "query_admin_start_notices":
        data = _parse(key, arguments)
        return query_admin_start_notices(
            db, user, draft_id=data.draft_id, project_query=data.project_query,
        )
    data = _parse(key, arguments)
    draft = None if key == 'prepare_contract_match_confirmation' else _draft(db, user, data.draft_id)
    if key == 'prepare_contract_match_confirmation':
        _require_admin(user)
        candidate = db.get(m.StartContractMatchCandidate, data.candidate_id)
        if not candidate:
            raise DomainError('NOT_FOUND', '合同匹配候选不存在', 404)
    display = {
        "draft_id": draft.id if draft else None,
        "candidate_id": getattr(data, 'candidate_id', None),
        "status": draft.status if draft else candidate.status,
        "current_revision": draft.current_revision if draft else None,
        "expected_row_version": getattr(data, 'expected_row_version', None),
        "material_snapshot": data.material_snapshot if hasattr(data, 'material_snapshot') else None,
        "department_keys": getattr(data, 'department_keys', None),
        "department_key": getattr(data, 'department_key', None),
        "note": getattr(data, 'note', None),
        "decision": getattr(data, "decision", None),
        "reason": data.reason if hasattr(data, "reason") else None,
    }
    proposal = {
        "tool": key,
        "action": key.removeprefix("prepare_"),
        "input": data.model_dump(mode="json"),
        "display": display,
        "operation_id": str(uuid4()),
        "security_version": user.security_version,
        "authorization_hash": fingerprint(db, user),
        "requires_approval": False,
        "confirmation_policy": proposal_confirmation_policy(run, requires_approval=False),
    }
    return {"data": [], "source": "agent_proposal", "as_of": now().isoformat(), "proposal": proposal}


def _source_context(db, user, step_id):
    from domain_packs.mold.tool_gateway import available_tools
    step = db.get(m.Step, step_id)
    run = db.get(m.Run, step.run_id) if step else None
    if not run or run.user_id != user.id or run.status not in {"RUNNING", "SUCCEEDED"}:
        raise DomainError("NOT_FOUND", "操作建议不存在或无权访问", 404)
    proposal = step.result.get("proposal") if isinstance(step.result, dict) else None
    if not proposal or proposal.get("tool") not in available_tools(db, user):
        raise DomainError("TOOL_FORBIDDEN", "操作能力不可用", 403)
    if run.security_version != user.security_version or proposal.get("authorization_hash") != fingerprint(db, user):
        raise DomainError("AUTHORIZATION_CHANGED", "授权已变化，请重新准备操作", 403)
    return proposal, run


def source(db, user, step_id):
    """确认卡只接收不可变建议内容，Run 留在内部执行上下文。"""
    proposal, _run = _source_context(db, user, step_id)
    return proposal


def validate_intent(db, user, payload):
    proposal, run = _source_context(db, user, payload["step_id"])
    if content_hash(proposal) != payload.get("proposal_hash"):
        raise DomainError("CONFIRMATION_INVALID", "操作建议内容已变化", 409)
    data = _parse(proposal["tool"], proposal["input"])
    if proposal["tool"] == 'prepare_contract_match_confirmation':
        _require_admin(user)
        if not db.get(m.StartContractMatchCandidate, data.candidate_id):
            raise DomainError('NOT_FOUND', '合同匹配候选不存在', 404)
    else:
        _draft(db, user, data.draft_id)
    return proposal, data, run


def confirm(db, user, payload):
    proposal, data, _run = validate_intent(db, user, payload)
    operation_id = proposal["operation_id"]
    if proposal["action"] == "admin_start_notice_update":
        return update_admin_start_notice_draft(
            db, user, data.draft_id, expected_row_version=data.expected_row_version,
            material_snapshot=data.material_snapshot, reason=data.reason,
            operation_id=operation_id,
        )
    if proposal["action"] == "admin_start_department_dispatch":
        return dispatch_admin_start_notice(
            db, user, data.draft_id, expected_row_version=data.expected_row_version,
            department_keys=data.department_keys, reason=data.reason,
            operation_id=operation_id,
        )
    if proposal["action"] == "admin_start_department_ack":
        return acknowledge_admin_start_department(
            db, user, data.draft_id, expected_row_version=data.expected_row_version,
            department_key=data.department_key, note=data.note,
            operation_id=operation_id,
        )
    if proposal["action"] == "admin_start_notice_decision":
        return confirm_admin_start_notice(
            db, user, data.draft_id, expected_row_version=data.expected_row_version,
            decision=data.decision, reason=data.reason,
            material_snapshot=data.material_snapshot, operation_id=operation_id,
        )
    if proposal["action"] == "contract_match_confirmation":
        return confirm_contract_match_candidate(
            db, user, data.candidate_id,
            expected_target_version=data.expected_target_version,
            operation_id=operation_id,
        )
    raise DomainError("ACTION_UNKNOWN", "内部开工动作未登记")
