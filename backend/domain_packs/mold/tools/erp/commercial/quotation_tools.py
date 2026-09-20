"""Conversation tools for versioned customer quotations and customer feedback."""
from datetime import date
from decimal import Decimal
from typing import Literal

from fastapi import APIRouter, Depends
from pydantic import Field, ValidationError, field_validator, model_validator
from sqlalchemy import select

from domain_packs.mold import domain_schemas as s, domains, models as m, workflow_selection
from domain_packs.mold.authorization import fingerprint, require
from domain_packs.mold.ports.bpm import content_hash
from domain_packs.mold.ports.confirmation_policy import proposal_confirmation_policy
from domain_packs.mold.ports.db import get_db, now
from domain_packs.mold.ports.errors import DomainError
from domain_packs.mold.ports.events import record
from domain_packs.mold.ports.files import uploaded_file
from domain_packs.mold.ports.schemas import StrictModel
from domain_packs.mold.ports.security import current_user


QUOTE_MEDIA_TYPES = {
    "application/pdf",
    "image/png",
    "image/jpeg",
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    "application/vnd.ms-excel",
    "text/csv",
    "application/zip",
    "application/x-zip-compressed",
    "application/dxf",
    "image/vnd.dwg",
    "application/acad",
}


class QuotationProposalInput(StrictModel):
    project_id: str = Field(min_length=1, max_length=36)
    project_version: int = Field(ge=1)
    previous_id: str | None = Field(default=None, max_length=36)
    quotation_number: str = Field(min_length=1, max_length=100)
    version: int = Field(ge=1)
    preliminary_execution_mode: Literal["INTERNAL", "FULL_OUTSOURCE"]
    quoted_amount: Decimal = Field(gt=0, max_digits=18, decimal_places=2)
    currency: str = Field(pattern=r"^[A-Z]{3}$")
    promised_delivery_date: date
    payment_terms: str = Field(min_length=1, max_length=4000)
    cost_amount: Decimal | None = Field(default=None, ge=0, max_digits=18, decimal_places=2)
    cost_evidence: str = Field(min_length=1, max_length=4000)
    process_analysis: str = Field(min_length=1, max_length=10000)
    duration_days: int = Field(gt=0, le=3650)
    duration_evidence: str = Field(min_length=1, max_length=4000)
    supplier_quote_amount: Decimal | None = Field(default=None, ge=0, max_digits=18, decimal_places=2)
    supplier_delivery_date: date | None = None
    supplier_requirements: str | None = Field(default=None, min_length=1, max_length=4000)
    supplier_quote_evidence: str | None = Field(default=None, min_length=1, max_length=4000)
    customer_company_snapshot: str = Field(min_length=1, max_length=200)
    customer_contact_snapshot: str = Field(min_length=1, max_length=200)
    owner_user_id: str = Field(min_length=1, max_length=36)
    source_summary: dict = Field(default_factory=dict)
    source_kind: Literal["UPLOAD", "EMAIL", "CUSTOMER_PLATFORM", "OTHER"]
    source_ref: str = Field(min_length=1, max_length=200)
    file_ids: list[str] = Field(min_length=1, max_length=20)
    workflow_definition_id: str = Field(min_length=1, max_length=36)

    @field_validator("file_ids")
    @classmethod
    def unique_files(cls, value):
        if len(value) != len(set(value)):
            raise ValueError("报价资料文件不能重复")
        return value

    @model_validator(mode="after")
    def structured_evaluation(self):
        s.QuotationInput.model_validate(self.model_dump(exclude={
            "project_id", "project_version", "source_kind", "source_ref",
            "file_ids", "workflow_definition_id",
        }))
        return self


class QuotationFeedbackProposalInput(StrictModel):
    project_id: str = Field(min_length=1, max_length=36)
    project_version: int = Field(ge=1)
    quotation_subject_id: str = Field(min_length=1, max_length=36)
    feedback_type: Literal["ACCEPTED", "REJECTED", "REVISION_REQUESTED", "NO_RESPONSE", "OTHER"]
    feedback_date: date
    evidence: str = Field(min_length=1, max_length=4000)
    source_ref: str = Field(min_length=1, max_length=200)


def workflow_options(db, user, project):
    scope = {"project_id": project.id}
    for permission in ("quotation.read", "quotation.create", "quotation.submit"):
        require(db, user, permission, scope)
    rows = db.scalars(select(m.WorkflowDefinition).where(
        m.WorkflowDefinition.status == "PUBLISHED"
    ).order_by(m.WorkflowDefinition.process_key, m.WorkflowDefinition.version.desc()))
    result = []
    for row in rows:
        if not workflow_selection.matches(row.config, {
            "business_type": "quotation", "categories": set(), "design_type": None,
        }):
            continue
        if row.config.get("material_contract") is not None:
            continue
        result.append(workflow_selection.metadata(row, db))
    return result


def quotation_rows(db, user, project_id):
    result = []
    rows = list(db.scalars(select(m.BusinessSubject).where(
        m.BusinessSubject.project_id == project_id,
        m.BusinessSubject.kind == "quotation",
    ).order_by(m.BusinessSubject.created_at.desc(), m.BusinessSubject.id).limit(51)))
    for subject in rows[:50]:
        try:
            result.append(domains.data(db, user, subject))
        except DomainError:
            continue
    return result, len(rows) > 50


def validate_files(db, user, file_ids, run, project_id, source_kind, source_ref):
    if not run or run.user_id != user.id:
        raise DomainError("FILE_CONTEXT_INVALID", "报价资料必须来自当前本人会话任务", 403)
    bound = set(db.scalars(select(m.RunFile.file_id).where(m.RunFile.run_id == run.id)))
    if any(file_id not in bound for file_id in file_ids):
        raise DomainError("FILE_CONTEXT_INVALID", "报价资料必须在本轮任务中明确发送", 403)
    blobs = []
    for file_id in file_ids:
        blob = uploaded_file(db, user, file_id)
        if blob.conversation_id != run.conversation_id:
            raise DomainError("FILE_CONTEXT_INVALID", "报价资料不属于当前会话", 403)
        if blob.media_type not in QUOTE_MEDIA_TYPES:
            raise DomainError("QUOTE_FILE_TYPE", "报价资料文件类型不受支持", 409)
        blobs.append(blob)
    return blobs


def quotation_schema():
    return QuotationProposalInput.model_json_schema()


def quotation_feedback_schema():
    return QuotationFeedbackProposalInput.model_json_schema()


def parse_quotation(arguments):
    try:
        return QuotationProposalInput.model_validate(arguments or {})
    except ValidationError as error:
        raise DomainError("INVALID_TOOL_INPUT", "报价版本参数不完整或不符合要求：" + error.errors()[0]["msg"]) from None


def parse_feedback(arguments):
    try:
        return QuotationFeedbackProposalInput.model_validate(arguments or {})
    except ValidationError as error:
        raise DomainError("INVALID_TOOL_INPUT", "客户反馈参数不完整或不符合要求：" + error.errors()[0]["msg"]) from None


def _project(db, user, project_id, project_version):
    project = db.get(m.Project, project_id)
    if not project:
        raise DomainError("NOT_FOUND", "项目不存在", 404)
    scope = {"project_id": project.id}
    require(db, user, "project.read", scope)
    if project.row_version != project_version:
        raise DomainError("VERSION_CONFLICT", "项目状态已变化，请重新查询后准备", 409)
    if project.status != "DRAFT":
        raise DomainError("QUOTE_STATE", "只有正式开工前的项目可以提交客户报价版本", 409)
    return project


def preview_quotation(db, user, data, run):
    project = _project(db, user, data.project_id, data.project_version)
    for permission in ("quotation.read", "quotation.create", "quotation.submit"):
        require(db, user, permission, {"project_id": project.id})
    active = list(db.scalars(select(m.BusinessSubject).where(
        m.BusinessSubject.project_id == project.id,
        m.BusinessSubject.kind == "quotation",
        m.BusinessSubject.status == "EFFECTIVE",
    )))
    pending = db.scalar(select(m.BusinessSubject.id).where(
        m.BusinessSubject.project_id == project.id,
        m.BusinessSubject.kind == "quotation",
        m.BusinessSubject.status.in_(["DRAFT", "SUBMITTED", "RETURNED", "APPLY_BLOCKED"]),
    ).limit(1))
    if pending:
        raise DomainError("QUOTE_VERSION_PENDING", "项目已有待处理报价版本，请先处理原申请", 409)
    if len(active) > 1:
        raise DomainError("QUOTE_VERSION_CONFLICT", "项目存在多个生效报价版本，请先核对版本链", 409)
    if active:
        prior = active[0]
        prior_detail = db.get(m.QuotationDetail, prior.id)
        if data.previous_id != prior.id:
            raise DomainError("QUOTE_VERSION_SOURCE_REQUIRED", "新报价必须关联当前生效报价版本", 409)
        if data.quotation_number != prior_detail.quotation_number or data.version != prior_detail.version + 1:
            raise DomainError("QUOTE_VERSION_INVALID", "报价编号必须沿用且版本号必须连续", 409)
    elif data.previous_id or data.version != 1:
        raise DomainError("QUOTE_VERSION_INVALID", "首个报价版本不得关联前序且版本必须为1", 409)
    owner = db.get(m.User, data.owner_user_id)
    if not owner or not owner.active:
        raise DomainError("ASSIGNMENT_BLOCKED", "报价责任人不存在或已停用", 409)
    blobs = validate_files(
        db, user, data.file_ids, run, project.id, data.source_kind, data.source_ref,
    )
    selected = next((item for item in workflow_options(db, user, project)
                     if item["id"] == data.workflow_definition_id), None)
    if not selected:
        raise DomainError("WORKFLOW_MISMATCH", "报价审批模板不可用，请重新查询流程选项", 409)
    display = {
        "操作": "提交客户报价版本审批",
        "项目": project.code + " · " + project.name,
        "项目版本": project.row_version,
        "报价编号与版本": data.quotation_number + " · V" + str(data.version),
        "前序报价": data.previous_id or "首版",
        "初步加工方式": "内部加工" if data.preliminary_execution_mode == "INTERNAL" else "整套委外",
        "报价金额": str(data.quoted_amount) + " " + data.currency,
        "承诺交期": data.promised_delivery_date.isoformat(),
        "收款条件": data.payment_terms,
        "成本核算": (str(data.cost_amount) + " " + data.currency) if data.cost_amount is not None else "不适用",
        "粗略工艺分析": data.process_analysis,
        "工期估算": str(data.duration_days) + " 天 · " + data.duration_evidence,
        "供应商评估": (
            str(data.supplier_quote_amount) + " " + data.currency + " · " +
            data.supplier_delivery_date.isoformat() + " · " + data.supplier_requirements
            if data.supplier_quote_amount is not None else "不适用"
        ),
        "客户与联系人": data.customer_company_snapshot + " · " + data.customer_contact_snapshot,
        "责任人": owner.display_name,
        "资料来源": data.source_kind + " · " + data.source_ref,
        "资料文件": [blob.filename for blob in blobs],
        "审批流程": selected["name"] + " · 第" + str(selected["version"]) + "版",
        "说明": "本人确认后冻结本轮资料并提交 Agent BPM；审批完成前不会发布报价、承接项目或改变加工方式。",
    }
    return project, blobs, display


def preview_feedback(db, user, data):
    project = _project(db, user, data.project_id, data.project_version)
    require(db, user, "quotation.read", {"project_id": project.id})
    require(db, user, "quotation.execute", {"project_id": project.id})
    quote = domains.require_source(db, data.quotation_subject_id, project.id, {"quotation"})
    detail = db.get(m.QuotationDetail, quote.id)
    duplicate = db.scalar(select(m.QuotationFeedback.id).where(
        m.QuotationFeedback.quotation_subject_id == quote.id,
        m.QuotationFeedback.source_ref == data.source_ref,
    ).limit(1))
    if duplicate:
        raise DomainError("QUOTE_FEEDBACK_DUPLICATE", "该客户反馈来源已登记，请勿重复累计", 409)
    display = {
        "操作": "登记客户报价反馈",
        "项目": project.code + " · " + project.name,
        "报价版本": detail.quotation_number + " · V" + str(detail.version),
        "反馈结果": data.feedback_type,
        "反馈日期": data.feedback_date.isoformat(),
        "反馈依据": data.evidence,
        "来源标识": data.source_ref,
        "说明": "确认后仅登记客户反馈事实，不自动承接、拒单、发布新报价或修改项目状态。",
    }
    return project, quote, display


def execute_quotation_tool(db, user, key, arguments, run=None):
    if key == "prepare_quotation_version":
        data = parse_quotation(arguments)
        _, _, display = preview_quotation(db, user, data, run)
        proposal = {
            "kind": "quotation", "action": "quotation_version", "requires_approval": True,
            "input": data.model_dump(mode="json"), "display": display,
            "confirmation_policy": proposal_confirmation_policy(run, requires_approval=True),
        }
        limitations = ["仅准备报价版本审批；本人确认后冻结资料并提交 BPM，审批生效前不形成正式报价版本。"]
    elif key == "prepare_quotation_feedback":
        data = parse_feedback(arguments)
        _, _, display = preview_feedback(db, user, data)
        proposal = {
            "kind": "quotation_feedback", "action": "quotation_feedback", "requires_approval": False,
            "input": data.model_dump(mode="json"), "display": display,
            "confirmation_policy": proposal_confirmation_policy(run, requires_approval=False),
        }
        limitations = ["仅登记已发生的客户反馈；不自动生成承接/拒单决定或后续报价版本。"]
    else:
        raise DomainError("TOOL_UNKNOWN", "工具未实现", 403)
    return {"data": [], "source": "agent_proposal", "as_of": now().isoformat(),
            "proposal": proposal, "limitations": limitations}


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
    if step.tool not in available_tools(db, user) or step.tool not in {
        "prepare_quotation_version", "prepare_quotation_feedback",
    } or not proposal:
        raise DomainError("TOOL_FORBIDDEN", "操作能力不可用", 403)
    return proposal, run


def validate_intent(db, user, payload):
    proposal, run = source(db, user, payload["step_id"])
    if content_hash(proposal) != payload["proposal_hash"]:
        raise DomainError("CONFIRMATION_INVALID", "操作建议内容已变化", 409)
    if proposal["action"] == "quotation_version":
        data = parse_quotation(proposal["input"])
        _, _, display = preview_quotation(db, user, data, run)
    else:
        data = parse_feedback(proposal["input"])
        _, _, display = preview_feedback(db, user, data)
    if content_hash(display) != content_hash(proposal["display"]):
        raise DomainError("VERSION_CONFLICT", "项目、资料或流程已变化，请重新准备", 409)
    return proposal, data, run


def confirm(db, user, payload):
    from domain_packs.mold.erp.core.business import submit_subject
    from domain_packs.mold.ports.confirmation_policy import agent_permission_mode_from_proposal

    proposal, data, run = validate_intent(db, user, payload)
    if proposal["action"] == "quotation_feedback":
        _, quote, _ = preview_feedback(db, user, data)
        feedback = m.QuotationFeedback(
            quotation_subject_id=quote.id, feedback_type=data.feedback_type,
            feedback_date=data.feedback_date, evidence=data.evidence,
            source_ref=data.source_ref, recorded_by=user.id,
        )
        db.add(feedback)
        db.flush()
        record(db, user, "quotation.feedback.recorded", quote.id, {
            "feedback_id": feedback.id, "feedback_type": data.feedback_type,
            "feedback_date": data.feedback_date.isoformat(), "source_ref": data.source_ref,
        })
        return {"project_id": data.project_id, "quotation_subject_id": quote.id,
                "feedback_id": feedback.id, "action": "quotation_feedback", "status": "RECORDED"}

    project, blobs, _ = preview_quotation(db, user, data, run)
    detail = s.QuotationInput.model_validate(data.model_dump(exclude={
        "project_id", "project_version", "source_kind", "source_ref",
        "file_ids", "workflow_definition_id",
    }))
    subject = domains.create(db, user, s.SubjectInput(
        kind="quotation", project_id=project.id,
        remark="报价版本 " + data.quotation_number + " V" + str(data.version),
        detail=detail.model_dump(mode="json"),
    ))
    for blob in blobs:
        inbound = db.scalar(select(m.QuoteInboundRecord).where(
            m.QuoteInboundRecord.project_id == project.id,
            m.QuoteInboundRecord.source_kind == data.source_kind,
            m.QuoteInboundRecord.source_ref == data.source_ref,
            m.QuoteInboundRecord.content_sha256 == blob.sha256,
        ).limit(1))
        if not inbound:
            inbound = m.QuoteInboundRecord(
                project_id=project.id, source_kind=data.source_kind,
                source_ref=data.source_ref, file_id=blob.id,
                content_sha256=blob.sha256, title=blob.filename, received_by=user.id,
            )
            db.add(inbound)
            db.flush()
        db.add(m.QuotationSourceLink(
            quotation_subject_id=subject.id, inbound_record_id=inbound.id,
        ))
    submitted = submit_subject(
        db, user, subject.id, subject.revision, data.workflow_definition_id,
        agent_permission_mode=agent_permission_mode_from_proposal(proposal),
    )
    return {"project_id": project.id, "subject_id": subject.id,
            "instance_id": submitted["instance_id"], "action": "quotation_version",
            "status": "SUBMITTED"}


router = APIRouter()


@router.get("/api/quotation-proposals/{step_id}")
def proposal_status(step_id: str, user=Depends(current_user), db=Depends(get_db)):
    source(db, user, step_id)
    intent = db.scalar(select(m.HumanIntent).where(
        m.HumanIntent.user_id == user.id,
        m.HumanIntent.action == "quotation.execute",
        m.HumanIntent.resource_id == step_id,
        m.HumanIntent.receipt["status"].as_string().in_(["SUBMITTED", "RECORDED"]),
    ).order_by(m.HumanIntent.created_at.desc()))
    return {"receipt": intent.receipt if intent else None}


@router.post("/api/quotation-proposals/{step_id}/intent")
def intent(step_id: str, user=Depends(current_user), db=Depends(get_db)):
    from domain_packs.mold.erp.core.business import create_intent

    proposal, _ = source(db, user, step_id)
    payload = {"step_id": step_id, "proposal_hash": content_hash(proposal)}
    result = create_intent(db, user, "quotation.execute", step_id, payload)
    result["display"] = proposal["display"]
    result["confirmation_policy"] = proposal.get("confirmation_policy")
    db.commit()
    return result
