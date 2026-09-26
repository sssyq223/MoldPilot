"""中标到开工通知的 Skill Tools 与人工确认 Handler。"""
from datetime import date
from uuid import uuid4

from pydantic import Field, ValidationError, model_validator
from sqlalchemy import select

from domain_packs.mold import models as m
from domain_packs.mold.authorization import fingerprint, require
from domain_packs.mold.erp.commercial.bid_start_workflow import (
    confirm_intake_revision,
    confirm_project_match,
    consume_confirmed_bid_notice,
)
from domain_packs.mold.erp.project.start_notice_workflow import (
    create_start_notice,
    decide_project_start,
    record_department_ack,
    validate_start_notice_creation,
)
from domain_packs.mold.erp.commercial.post_start_binding import bind_post_start
from domain_packs.mold.ports.bpm import content_hash
from domain_packs.mold.ports.confirmation_policy import proposal_confirmation_policy
from domain_packs.mold.ports.db import now
from domain_packs.mold.ports.errors import DomainError
from domain_packs.mold.ports.files import uploaded_file
from domain_packs.mold.ports.schemas import StrictModel


class MatchProposalInput(StrictModel):
    event_id: str = Field(min_length=1, max_length=36)


class ProjectMatchProposalInput(StrictModel):
    match_id: str = Field(min_length=1, max_length=36)
    expected_row_version: int = Field(ge=1)
    project_id: str = Field(min_length=1, max_length=36)
    project_version: int = Field(ge=1)
    match_evidence: str = Field(min_length=1, max_length=4000)


class IntakeConfirmationProposalInput(StrictModel):
    match_id: str = Field(min_length=1, max_length=36)
    expected_row_version: int = Field(ge=1)
    bid_intake_revision_id: str = Field(min_length=1, max_length=36)


class StartNoticeProposalInput(StrictModel):
    match_id: str = Field(min_length=1, max_length=36)
    expected_row_version: int = Field(ge=1)
    effective_date: date
    expected_contract_date: date | None = None


class DepartmentAckProposalInput(StrictModel):
    notice_id: str = Field(min_length=1, max_length=36)
    department_key: str = Field(min_length=1, max_length=60)
    expected_row_version: int = Field(ge=1)
    status: str = Field(pattern=r"^(ACCEPTED|RETURNED|NEED_INFO)$")
    evidence: str = Field(min_length=1, max_length=4000)


class ProjectDecisionProposalInput(StrictModel):
    notice_id: str = Field(min_length=1, max_length=36)
    decision: str = Field(pattern=r"^(PROJECT_ACCEPTED|FULL_OUTSOURCE_ACCEPTED|REJECTED|RETURNED)$")
    reason: str = Field(min_length=1, max_length=4000)


class PostStartBindingProposalInput(StrictModel):
    decision_id: str = Field(min_length=1, max_length=36)
    start_notice_id: str = Field(min_length=1, max_length=36)
    target_type: str = Field(pattern=r"^(SALES_CONTRACT|ACCOUNTING_CHECKLIST)$")
    target_id: str = Field(min_length=1, max_length=120)
    target_version: int = Field(ge=1)
    mold_rows: list[dict] = Field(min_length=1, max_length=100)


INPUTS = {
    "prepare_bid_notice_match": MatchProposalInput,
    "prepare_bid_project_match": ProjectMatchProposalInput,
    "prepare_bid_intake_confirmation": IntakeConfirmationProposalInput,
    "prepare_start_notice": StartNoticeProposalInput,
    "prepare_department_ack": DepartmentAckProposalInput,
    "prepare_project_start_decision": ProjectDecisionProposalInput,
    "prepare_post_start_binding": PostStartBindingProposalInput,
}


def _parse(key, arguments):
    try:
        return INPUTS[key].model_validate(arguments or {})
    except (KeyError, ValidationError) as error:
        message = error.errors()[0]["msg"] if hasattr(error, "errors") else "参数无效"
        raise DomainError("INVALID_TOOL_INPUT", f"中标到开工工具参数无效：{message}") from None


def _owned_match(db, user, match_id):
    match = db.get(m.BidNoticeMatch, str(match_id))
    if not match:
        raise DomainError("NOT_FOUND", "中标匹配记录不存在", 404)
    uploaded_file(db, user, match.source_file_id)
    return match


def _proposal(db, user, key, data, resource_id, display, run):
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


def query_confirmed_bid_notices(db, user):
    consumed = select(m.BidNoticeMatch.id).where(
        m.BidNoticeMatch.confirmation_event_id == m.AuditEvent.id,
    ).exists()
    rows = db.scalars(select(m.AuditEvent).where(
        m.AuditEvent.action == "bid_notice.confirmed",
        ~consumed,
    ).order_by(m.AuditEvent.created_at.desc(), m.AuditEvent.id.desc()).limit(100))
    items = []
    for event in rows:
        try:
            uploaded_file(db, user, event.resource_id)
        except DomainError:
            continue
        detail = event.detail if isinstance(event.detail, dict) else {}
        items.append({
            "event_id": event.id,
            "file_version_id": event.resource_id,
            "inbound_record_id": detail.get("inbound_record_id"),
            "classification_id": detail.get("classification_id"),
            "confirmed_document_type": detail.get("confirmed_document_type"),
            "operation_id": detail.get("operation_id"),
        })
    return {"data": items, "source": "audit_event", "as_of": now().isoformat(),
            "limitations": ["仅返回尚未消费、已人工确认且当前人员仍有文件权限的中标事件；不会自动消费或匹配项目。"]}


def _preview(key, db, user, data, run):
    if key == "prepare_bid_notice_match":
        event = db.get(m.AuditEvent, data.event_id)
        if not event:
            raise DomainError("BID_EVENT_NOT_FOUND", "中标确认事件不存在", 404)
        uploaded_file(db, user, event.resource_id)
        return {"event_id": event.id, "action": "consume_bid_notice",
                "message": "确认后仅登记 PENDING_MATCH，不自动匹配项目。"}
    if key == "prepare_bid_project_match":
        match = _owned_match(db, user, data.match_id)
        project = db.get(m.Project, data.project_id)
        if not project:
            raise DomainError("PROJECT_NOT_FOUND", "项目不存在", 404)
        from domain_packs.mold.erp.commercial.bid_start_workflow import validate_project_match
        validate_project_match(match, expected_row_version=data.expected_row_version,
                               project_id=data.project_id, project_version=data.project_version)
        require(db, user, "project.read", {"project_id": project.id})
        return {"match_id": match.id, "project": {"id": project.id, "code": project.code,
                "name": project.name, "row_version": project.row_version},
                "match_evidence": data.match_evidence}
    if key == "prepare_bid_intake_confirmation":
        match = _owned_match(db, user, data.match_id)
        revision = db.get(m.BidIntakeRevision, data.bid_intake_revision_id)
        case = db.get(m.BidIntakeCase, revision.case_id) if revision else None
        from domain_packs.mold.erp.commercial.bid_start_workflow import validate_intake_revision_confirmation
        validate_intake_revision_confirmation(
            match, {"id": revision.id if revision else data.bid_intake_revision_id,
                    "case_project_id": case.project_id if case else None},
            expected_row_version=data.expected_row_version,
            revision_id=data.bid_intake_revision_id,
        )
        return {"match_id": match.id, "bid_intake_revision_id": revision.id,
                "version": revision.version, "action": "confirm_intake_revision"}
    if key == "prepare_start_notice":
        match = _owned_match(db, user, data.match_id)
        validate_start_notice_creation(match, project_version=match.project_version)
        project = db.get(m.Project, match.project_id)
        from domain_packs.mold.tools.erp.commercial.bid_intake_tools import start_condition_snapshot
        conditions = start_condition_snapshot(db, user, project.id)
        return {"match_id": match.id, "project_id": project.id,
                "effective_date": data.effective_date.isoformat(),
                "expected_contract_date": data.expected_contract_date.isoformat() if data.expected_contract_date else None,
                "customer_start_conditions": conditions,
                "action": "create_start_notice"}
    if key == "prepare_department_ack":
        notice = db.get(m.StartNotice, data.notice_id)
        if not notice:
            raise DomainError("NOT_FOUND", "开工通知不存在", 404)
        if not user.super_admin:
            role_by_department = {
                "DESIGN": "DESIGN_OWNER", "PURCHASE": "PURCHASE_OWNER",
                "MANUFACTURING": "MANUFACTURING_OWNER", "ASSEMBLY": "ASSEMBLY_OWNER",
                "FINANCE": "FINANCE_OWNER",
            }
            allowed = db.scalar(select(m.ProjectRoleMember).where(
                m.ProjectRoleMember.project_id == notice.project_id,
                m.ProjectRoleMember.role_key == role_by_department.get(data.department_key),
                m.ProjectRoleMember.user_id == user.id,
            )) if data.department_key in role_by_department else None
            if not allowed:
                raise DomainError("FORBIDDEN", "当前人员不是该部门回执负责人", 403)
        ack = db.scalar(select(m.StartNoticeDepartmentAck).where(
            m.StartNoticeDepartmentAck.start_notice_id == notice.id,
            m.StartNoticeDepartmentAck.start_notice_version == notice.version,
            m.StartNoticeDepartmentAck.department_key == data.department_key,
        ))
        if not ack:
            raise DomainError("DEPARTMENT_NOT_REQUIRED", "当前部门不在该通知回执范围", 409)
        return {"notice_id": notice.id, "department_key": data.department_key,
                "current_row_version": ack.row_version, "status": data.status,
                "evidence": data.evidence, "action": "record_department_ack"}
    if key == "prepare_project_start_decision":
        notice = db.get(m.StartNotice, data.notice_id)
        if not notice:
            raise DomainError("NOT_FOUND", "开工通知不存在", 404)
        return {"notice_id": notice.id, "version": notice.version,
                "decision": data.decision, "reason": data.reason,
                "action": "decide_project_start"}
    if key == "prepare_post_start_binding":
        return {**data.model_dump(mode="json"), "action": "bind_post_start",
                "message": "确认后才写入正式后置绑定；本地合同或核算资料版本和模具关系会再次核验。"}
    raise DomainError("TOOL_UNKNOWN", "工具未实现", 403)


def execute_tool(db, user, key, arguments, run=None):
    if key == "query_confirmed_bid_notices":
        return query_confirmed_bid_notices(db, user)
    data = _parse(key, arguments)
    display = _preview(key, db, user, data, run)
    resource_id = getattr(data, "match_id", None) or getattr(data, "notice_id", None) \
        or getattr(data, "decision_id", None) or getattr(data, "event_id", None) or user.id
    return _proposal(db, user, key, data, resource_id, display, run)


def source(db, user, step_id):
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


def validate_intent(db, user, payload):
    proposal, run = source(db, user, payload["step_id"])
    if content_hash(proposal) != payload.get("proposal_hash"):
        raise DomainError("CONFIRMATION_INVALID", "操作建议内容已变化", 409)
    data = _parse(proposal["tool"], proposal["input"])
    _preview(proposal["tool"], db, user, data, run)
    return proposal, data, run


def confirm(db, user, payload):
    proposal, data, _ = validate_intent(db, user, payload)
    operation_id = proposal["operation_id"]
    action = proposal["action"]
    if action == "bid_notice_match":
        row = consume_confirmed_bid_notice(db, user, data.event_id)
        return {"action": action, "match_id": row.id, "status": row.status}
    if action == "bid_project_match":
        return confirm_project_match(db, user, data.match_id, expected_row_version=data.expected_row_version,
            project_id=data.project_id, project_version=data.project_version,
            match_evidence=data.match_evidence, operation_id=operation_id)
    if action == "bid_intake_confirmation":
        return confirm_intake_revision(db, user, data.match_id, expected_row_version=data.expected_row_version,
            revision_id=data.bid_intake_revision_id, operation_id=operation_id)
    if action == "start_notice":
        return create_start_notice(db, user, data.match_id, expected_row_version=data.expected_row_version,
            effective_date=data.effective_date, expected_contract_date=data.expected_contract_date,
            operation_id=operation_id)
    if action == "department_ack":
        return record_department_ack(db, user, data.notice_id, department_key=data.department_key,
            expected_row_version=data.expected_row_version, status=data.status,
            evidence=data.evidence, operation_id=operation_id)
    if action == "project_start_decision":
        return decide_project_start(db, user, data.notice_id, decision=data.decision,
            reason=data.reason, operation_id=operation_id)
    if action == "post_start_binding":
        return bind_post_start(db, user, decision_id=data.decision_id, start_notice_id=data.start_notice_id,
            target_type=data.target_type, target_id=data.target_id, target_version=data.target_version,
            mold_rows=data.mold_rows, operation_id=operation_id)
    raise DomainError("ACTION_UNKNOWN", "中标到开工动作未登记")
