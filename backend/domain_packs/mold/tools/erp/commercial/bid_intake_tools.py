"""Bid intake context plus confirmed, append-only intake draft registration."""
from datetime import date
from decimal import Decimal
from typing import Literal

from fastapi import APIRouter, Depends
from pydantic import Field, ValidationError, field_validator, model_validator
from sqlalchemy import select

from domain_packs.mold import domains, models as m
from domain_packs.mold.authorization import access, fingerprint, require, select_fields
from domain_packs.mold.ports.bpm import content_hash
from domain_packs.mold.ports.confirmation_policy import proposal_confirmation_policy
from domain_packs.mold.ports.db import get_db, now
from domain_packs.mold.ports.errors import DomainError
from domain_packs.mold.ports.events import record
from domain_packs.mold.ports.files import uploaded_file
from domain_packs.mold.ports.schemas import StrictModel
from domain_packs.mold.ports.security import current_user
from domain_packs.mold.tools.erp.commercial.quotation_tools import QUOTE_MEDIA_TYPES
from domain_packs.mold.tools.erp.commercial.quote_tools import QuoteContextInput, _project_card, _resolve, _subjects


class BidIntakeAttachmentInput(StrictModel):
    file_id: str = Field(min_length=1, max_length=36)
    role: Literal[
        "BID_NOTICE", "EXTERNAL_START_NOTICE", "CONTRACT_REFERENCE", "MOLD_IMAGE", "OTHER"
    ]


class BidIntakeProposalInput(StrictModel):
    project_id: str = Field(min_length=1, max_length=36)
    project_version: int = Field(ge=1)
    previous_revision_id: str | None = Field(default=None, max_length=36)
    version: int = Field(ge=1)
    source_kind: Literal["UPLOAD", "EMAIL", "CUSTOMER_PLATFORM", "OTHER"]
    source_ref: str = Field(min_length=1, max_length=200)
    received_date: date
    customer_classification: Literal["HISENSE", "HAIER", "OTHER"]
    classification_evidence: str = Field(min_length=1, max_length=4000)
    customer_company: str = Field(min_length=1, max_length=200)
    customer_contact: str = Field(min_length=1, max_length=200)
    customer_mold_number: str | None = Field(default=None, min_length=1, max_length=120)
    customer_model_or_material: str | None = Field(default=None, min_length=1, max_length=200)
    project_name_snapshot: str = Field(min_length=1, max_length=200)
    amount: Decimal | None = Field(default=None, gt=0, max_digits=18, decimal_places=2)
    currency: str | None = Field(default=None, pattern=r"^[A-Z]{3}$")
    our_recipient: str = Field(min_length=1, max_length=200)
    external_order_number: str | None = Field(default=None, min_length=1, max_length=120)
    external_start_date: date | None = None
    customer_due_date: date | None = None
    customer_process_confirmed: bool = False
    customer_process_confirmation_evidence: str | None = Field(
        default=None, min_length=1, max_length=4000
    )
    matched_quotation_subject_id: str | None = Field(default=None, max_length=36)
    historical_mold_number: str | None = Field(default=None, min_length=1, max_length=120)
    historical_relation_kind: Literal["BACKUP", "REFERENCE"] | None = None
    match_result: Literal["MATCHED", "PARTIAL", "UNMATCHED", "MANUAL"]
    match_evidence: str = Field(min_length=1, max_length=4000)
    notes: str = Field(default="", max_length=4000)
    attachments: list[BidIntakeAttachmentInput] = Field(min_length=1, max_length=20)

    @field_validator("attachments")
    @classmethod
    def unique_attachments(cls, value):
        pairs = [(item.file_id, item.role) for item in value]
        if len(pairs) != len(set(pairs)):
            raise ValueError("中标接收附件及其用途不能重复")
        return value

    @model_validator(mode="after")
    def consistent_fields(self):
        if bool(self.amount) != bool(self.currency):
            raise ValueError("金额与币种须同时填写")
        if bool(self.historical_mold_number) != bool(self.historical_relation_kind):
            raise ValueError("历史模号与备份/参考关系须同时填写")
        if self.match_result == "MATCHED" and not (
            self.matched_quotation_subject_id or self.historical_mold_number
        ):
            raise ValueError("完全匹配时至少填写匹配报价或历史模号")
        if self.match_result == "UNMATCHED" and (
            self.matched_quotation_subject_id or self.historical_mold_number
        ):
            raise ValueError("未匹配时历史报价与历史模具字段必须留空")
        if self.customer_process_confirmed and not self.customer_process_confirmation_evidence:
            raise ValueError("确认客户工艺方案时必须填写人工确认依据")
        if not self.customer_process_confirmed and self.customer_process_confirmation_evidence:
            raise ValueError("客户工艺方案未确认时不能填写确认依据")
        return self


def bid_intake_schema():
    return BidIntakeProposalInput.model_json_schema()


def parse_bid_intake(arguments):
    try:
        return BidIntakeProposalInput.model_validate(arguments or {})
    except ValidationError as error:
        raise DomainError(
            "INVALID_TOOL_INPUT", "中标接收草稿参数不完整或不符合要求：" + error.errors()[0]["msg"]
        ) from None


def _profile(db, user, project_id: str):
    profile = db.get(m.ProjectProfile, project_id)
    if not profile:
        return None
    fields = access(db, user, "project.read", {"project_id": project_id}).fields
    customer = db.get(m.Customer, profile.customer_id) if profile.customer_id else None
    return select_fields(
        {
            "customer_id": profile.customer_id,
            "customer_name": customer.name if customer else None,
            "customer_rule_key": customer.rule_key if customer else None,
            "owner_user_id": profile.owner_user_id,
            "execution_mode": profile.execution_mode,
            "customer_due_date": profile.customer_due_date.isoformat() if profile.customer_due_date else None,
            "settlement_status": profile.settlement_status,
        },
        fields | {
            "customer_id", "customer_name", "customer_rule_key", "owner_user_id",
            "execution_mode", "customer_due_date", "settlement_status",
        },
    )


def _has_dossier_access(db, user, project_id: str):
    return access(db, user, "project.dossier.read", {"project_id": project_id}).allowed


def _molds(db, user, project_id: str):
    if not _has_dossier_access(db, user, project_id):
        return [], False
    rows = []
    for _, mold in db.execute(
        select(m.ProjectMold, m.Mold)
        .join(m.Mold, m.Mold.id == m.ProjectMold.mold_id)
        .where(m.ProjectMold.project_id == project_id)
        .order_by(m.Mold.internal_number)
        .limit(21)
    ):
        rows.append({"id": mold.id, "internal_number": mold.internal_number,
                     "name": mold.name, "status": mold.status})
    return rows[:20], len(rows) > 20


def _limited_subjects(db, user, project_id: str, kind: str, allowed_tools: set[str]):
    context_tools = {
        "quote_acceptance": {
            "query_bid_intake_context", "query_quote_acceptance_context", "query_quote_evaluation_context"
        },
        "quotation": {
            "query_bid_intake_context", "query_quote_acceptance_context", "query_quote_evaluation_context"
        },
        "sales_contract": {"query_contract_context"},
        "internal_start": {"query_internal_start_readiness"},
    }
    direct_tool = "query_" + kind
    if direct_tool not in allowed_tools and not (context_tools.get(kind, set()) & allowed_tools):
        return [], False
    return _subjects(db, user, project_id, kind, allowed_tools | {direct_tool})


def _decision(row: dict):
    detail = row.get("detail") or {}
    return {
        "id": row.get("id"), "number": row.get("number"), "status": row.get("status"),
        "decision": detail.get("decision"), "execution_mode": detail.get("execution_mode"),
        "effective_date": detail.get("effective_date"), "amount": detail.get("amount"),
        "currency": detail.get("currency"), "evidence": detail.get("evidence"),
        "source_subject_id": detail.get("source_subject_id"),
    }


def _contract(row: dict):
    detail = row.get("detail") or {}
    return {
        "id": row.get("id"), "number": row.get("number"), "status": row.get("status"),
        "contract_number": detail.get("contract_number"), "amount": detail.get("amount"),
        "currency": detail.get("currency"), "expected_date": detail.get("expected_date"),
        "received_date": detail.get("received_date"),
        "stages_count": len(detail.get("stages") or []),
    }


def _latest(rows: list[dict], decision: str | None = None):
    for row in rows:
        detail = row.get("detail") if isinstance(row.get("detail"), dict) else {}
        if row.get("status") == "EFFECTIVE" and (decision is None or detail.get("decision") == decision):
            return row
    return None


def _attachments(db, revision_ids: list[str], visible: bool):
    if not visible or not revision_ids:
        return {}
    grouped = {revision_id: [] for revision_id in revision_ids}
    rows = db.execute(
        select(m.BidIntakeAttachment, m.FileObject)
        .join(m.FileObject, m.FileObject.id == m.BidIntakeAttachment.file_id)
        .where(m.BidIntakeAttachment.revision_id.in_(revision_ids))
        .order_by(m.BidIntakeAttachment.created_at, m.BidIntakeAttachment.id)
    )
    for attachment, blob in rows:
        grouped.setdefault(attachment.revision_id, []).append({
            "id": attachment.id, "file_id": attachment.file_id, "role": attachment.role,
            "title": attachment.title, "media_type": blob.media_type,
            "size": blob.size, "content_sha256": attachment.content_sha256,
        })
    return grouped


def _revision_card(row, attachments):
    return {
        "id": row.id, "version": row.version, "previous_revision_id": row.previous_revision_id,
        "source_kind": row.source_kind, "source_ref": row.source_ref,
        "received_date": row.received_date.isoformat(),
        "customer_classification": row.customer_classification,
        "classification_evidence": row.classification_evidence,
        "customer_company": row.customer_company, "customer_contact": row.customer_contact,
        "customer_mold_number": row.customer_mold_number,
        "customer_model_or_material": row.customer_model_or_material,
        "project_name_snapshot": row.project_name_snapshot,
        "amount": str(row.amount) if row.amount is not None else None, "currency": row.currency,
        "our_recipient": row.our_recipient, "external_order_number": row.external_order_number,
        "external_start_date": row.external_start_date.isoformat() if row.external_start_date else None,
        "customer_due_date": row.customer_due_date.isoformat() if row.customer_due_date else None,
        "customer_process_confirmed": row.customer_process_confirmed,
        "customer_process_confirmation_evidence": row.customer_process_confirmation_evidence,
        "matched_quotation_subject_id": row.matched_quotation_subject_id,
        "historical_mold_number": row.historical_mold_number,
        "historical_relation_kind": row.historical_relation_kind,
        "match_result": row.match_result, "match_evidence": row.match_evidence,
        "notes": row.notes, "recorded_by": row.recorded_by,
        "created_at": row.created_at.isoformat(), "attachments": attachments,
    }


def _intake_context(db, user, project_id, quote_rows, start_rows):
    case = db.scalar(select(m.BidIntakeCase).where(m.BidIntakeCase.project_id == project_id))
    if not case:
        return None
    revision_rows = list(db.scalars(select(m.BidIntakeRevision).where(
        m.BidIntakeRevision.case_id == case.id
    ).order_by(m.BidIntakeRevision.version.desc()).limit(21)))
    truncated = len(revision_rows) > 20
    revision_rows = revision_rows[:20]
    dossier_visible = _has_dossier_access(db, user, project_id)
    attachments = _attachments(db, [row.id for row in revision_rows], dossier_visible)
    visible_subjects = {row.get("id"): row for row in quote_rows + start_rows}
    links = []
    for link in db.scalars(select(m.BidIntakeLifecycleLink).where(
        m.BidIntakeLifecycleLink.case_id == case.id
    ).order_by(m.BidIntakeLifecycleLink.created_at, m.BidIntakeLifecycleLink.id)):
        subject = visible_subjects.get(link.subject_id)
        if subject:
            links.append({
                "id": link.id, "link_kind": link.link_kind, "subject_id": link.subject_id,
                "source_revision_id": link.source_revision_id,
                "number": subject.get("number"), "status": subject.get("status"),
                "decision": (subject.get("detail") or {}).get("decision"),
                "created_at": link.created_at.isoformat(),
            })
    return {
        "id": case.id, "project_id": case.project_id, "created_by": case.created_by,
        "created_at": case.created_at.isoformat(),
        "current_revision": (
            _revision_card(revision_rows[0], attachments.get(revision_rows[0].id, []))
            if revision_rows else None
        ),
        "revisions": [
            _revision_card(row, attachments.get(row.id, [])) for row in revision_rows
        ],
        "revisions_truncated": truncated,
        "attachments_visible": dossier_visible,
        "lifecycle_links": links,
    }


def _analysis(project, profile, quote_rows, contracts, starts, molds, intake):
    latest_accept = _latest(quote_rows, "ACCEPT")
    latest_reject = _latest(quote_rows, "REJECT")
    latest_start = _latest(starts, "START")
    effective_contracts = [row for row in contracts if row.get("status") == "EFFECTIVE"]
    revision = intake.get("current_revision") if intake else None
    attachment_roles = {
        item["role"]
        for recorded_revision in (intake or {}).get("revisions", [])
        for item in recorded_revision.get("attachments", [])
    } if intake and intake.get("attachments_visible") else set()
    gaps = []
    warnings = []
    if not profile or not profile.get("customer_id"):
        gaps.append("未见已关联并人工确认的客户分类。")
    if profile and not profile.get("customer_rule_key"):
        gaps.append("未见客户规则键，不能区分海信、海尔或其他客户处理规则。")
    if not molds:
        gaps.append("未见当前可见的内部模具关联。")
    if not revision:
        gaps.append("未见已确认的中标接收草稿；客户邮件、平台文件或人工上传来源、收件人和匹配结果尚未落库。")
    else:
        if not revision.get("external_order_number") and not revision.get("external_start_date"):
            warnings.append("中标草稿尚未记录外部订单号或客户开工日期，可在同一接收记录上追加下一版。")
        if intake.get("attachments_visible") and "MOLD_IMAGE" not in attachment_roles:
            gaps.append("中标接收草稿未见模具或 UG 图片附件。")
        if not intake.get("attachments_visible"):
            warnings.append("未具备项目业务档案读取权限，附件名称与用途未返回。")
    if not (latest_accept or latest_reject):
        gaps.append("未见有效承接或有效拒单决定。")
    if latest_accept and not latest_accept.get("detail", {}).get("execution_mode"):
        warnings.append("有效承接缺少最终加工方式，不能判断内部生产或整套委外。")
    if effective_contracts and not latest_accept:
        warnings.append("当前可见销售合同不等于已人工确认承接。")
    if latest_start and not latest_accept:
        warnings.append("当前可见正式开工缺少可见承接依据，需要核对历史权限或资料。")
    if project.status == "ACTIVE" and not latest_start:
        warnings.append("项目已进行中但当前未见有效正式开工通知，请核对开工依据。")
    lifecycle_state = (
        "NOT_CREATED" if not intake else
        "REJECTED" if latest_reject else
        "FORMALLY_STARTED" if latest_start else
        "ACCEPTED_AWAITING_FORMAL_START" if latest_accept else
        "DRAFT_AWAITING_ACCEPTANCE"
    )
    return {
        "customer_classification": {
            "customer_id": (profile or {}).get("customer_id"),
            "customer_name": (profile or {}).get("customer_name"),
            "customer_rule_key": (profile or {}).get("customer_rule_key"),
            "confirmed_intake_classification": (revision or {}).get("customer_classification"),
            "is_confirmed_from_current_facts": bool(profile and profile.get("customer_id")),
        },
        "latest_effective_acceptance": _decision(latest_accept) if latest_accept else None,
        "latest_effective_rejection": _decision(latest_reject) if latest_reject else None,
        "latest_effective_internal_start": _decision(latest_start) if latest_start else None,
        "effective_sales_contracts": [_contract(row) for row in effective_contracts[:20]],
        "known_molds": molds, "gaps": gaps, "warnings": warnings,
        "derived_status": {
            "bid_intake_lifecycle_state": lifecycle_state,
            "has_customer_classification": bool(profile and profile.get("customer_id")),
            "has_effective_acceptance": bool(latest_accept),
            "has_effective_rejection": bool(latest_reject),
            "has_sales_contract": bool(effective_contracts),
            "has_internal_start": bool(latest_start), "has_mold_relation": bool(molds),
            "has_source_document_record": bool(revision),
            "has_mold_image_evidence": (
                "MOLD_IMAGE" in attachment_roles if intake and intake.get("attachments_visible") else None
            ),
            "can_continue_same_intake_case": bool(intake and not latest_reject and not latest_start),
        },
    }


def query(db, user, data: QuoteContextInput, allowed_tools: set[str]):
    project, alternatives, truncated = _resolve(db, user, data, allowed_tools)
    limitations = [
        "只读取当前用户可见且具备报价与承接读取权限的项目。",
        "客户邮件和客户平台仍由人工接收或外部适配器提供；本工具只读取已确认落库的来源和附件元数据。",
        "合同、中标接收草稿、承接决定、外部开工资料和内部正式开工分别管理，不能互相替代。",
    ]
    if truncated:
        limitations.append("最多检查前500个可见项目，结果可能未覆盖全部可见范围。")
    if project:
        profile = _profile(db, user, project.id)
        molds, molds_truncated = _molds(db, user, project.id)
        quote_rows, quote_truncated = _limited_subjects(db, user, project.id, "quote_acceptance", allowed_tools)
        quotation_rows, quotation_truncated = _limited_subjects(db, user, project.id, "quotation", allowed_tools)
        contracts, contracts_truncated = _limited_subjects(db, user, project.id, "sales_contract", allowed_tools)
        starts, starts_truncated = _limited_subjects(db, user, project.id, "internal_start", allowed_tools)
        intake = _intake_context(db, user, project.id, quote_rows, starts)
        if not _has_dossier_access(db, user, project.id):
            limitations.append("未具备项目业务档案读取权限，不能返回模具关系或中标附件元数据。")
        if molds_truncated:
            limitations.append("模具关联最多返回前20条。")
        if quote_truncated:
            limitations.append("承接/拒单记录最多返回最新20条。")
        if quotation_truncated:
            limitations.append("报价版本最多返回最新20条。")
        if contracts_truncated:
            limitations.append("销售合同最多返回最新20条。")
        if starts_truncated:
            limitations.append("正式开工通知最多返回最新20条。")
        if intake and intake["revisions_truncated"]:
            limitations.append("中标接收草稿最多返回最新20版。")
        return {
            "resolution": "RESOLVED",
            "data": [{
                "project": _project_card(db, user, project, alternatives or ("项目定位",)),
                "project_profile": profile, "bid_intake": intake,
                "quotation_versions": quotation_rows,
                "quote_acceptance": [_decision(row) for row in quote_rows],
                "sales_contracts": [_contract(row) for row in contracts],
                "internal_starts": [_decision(row) for row in starts],
                "analysis": _analysis(project, profile, quote_rows, contracts, starts, molds, intake),
            }],
            "source": "agent_db", "as_of": now().isoformat(), "limitations": limitations,
        }
    if alternatives is None:
        return {"resolution": "NOT_FOUND_OR_FORBIDDEN", "data": [], "source": "agent_db",
                "as_of": now().isoformat(), "limitations": limitations}
    if alternatives:
        return {"resolution": "MULTIPLE_CANDIDATES", "data": alternatives, "source": "agent_db",
                "as_of": now().isoformat(),
                "limitations": limitations + ["线索命中多个候选项目，请使用项目 ID 或更完整编号后再查询。"]}
    return {"resolution": "NOT_FOUND", "data": [], "source": "agent_db",
            "as_of": now().isoformat(), "limitations": limitations}


def _expected_classification(rule_key):
    key = (rule_key or "").strip().casefold()
    if "hisense" in key or "海信" in key:
        return "HISENSE"
    if "haier" in key or "海尔" in key:
        return "HAIER"
    return "OTHER" if key else None


def _validate_files(db, user, data, run):
    if not run or run.user_id != user.id:
        raise DomainError("FILE_CONTEXT_INVALID", "中标资料必须来自当前本人会话任务", 403)
    bound = set(db.scalars(select(m.RunFile.file_id).where(m.RunFile.run_id == run.id)))
    blobs = {}
    for item in data.attachments:
        if item.file_id not in bound:
            raise DomainError("FILE_CONTEXT_INVALID", "中标资料必须在本轮任务中明确发送", 403)
        blob = uploaded_file(db, user, item.file_id)
        if blob.conversation_id != run.conversation_id:
            raise DomainError("FILE_CONTEXT_INVALID", "中标资料不属于当前会话", 403)
        if blob.media_type not in QUOTE_MEDIA_TYPES:
            raise DomainError("BID_INTAKE_FILE_TYPE", "中标资料文件类型不受支持", 409)
        blobs[item.file_id] = blob
    return blobs


def _current_case_revision(db, project_id):
    case = db.scalar(select(m.BidIntakeCase).where(m.BidIntakeCase.project_id == project_id))
    latest = None
    if case:
        latest = db.scalar(select(m.BidIntakeRevision).where(
            m.BidIntakeRevision.case_id == case.id
        ).order_by(m.BidIntakeRevision.version.desc()).limit(1))
    return case, latest


def _intake_fingerprint(data, blobs):
    payload = data.model_dump(mode="json", exclude={
        "project_version", "previous_revision_id", "version", "attachments",
    })
    payload["files"] = sorted(
        [{"sha256": blobs[item.file_id].sha256, "role": item.role} for item in data.attachments],
        key=lambda item: (item["sha256"], item["role"]),
    )
    return content_hash(payload)


def preview_bid_intake(db, user, data, run):
    project = db.get(m.Project, data.project_id)
    if not project:
        raise DomainError("NOT_FOUND", "项目不存在", 404)
    scope = {"project_id": project.id}
    for permission in ("project.read", "project.dossier.read", "quote_acceptance.read", "quote_acceptance.create"):
        require(db, user, permission, scope)
    if project.row_version != data.project_version:
        raise DomainError("VERSION_CONFLICT", "项目状态已变化，请重新查询后准备", 409)
    if project.status != "DRAFT":
        raise DomainError("BID_INTAKE_STATE", "只有正式开工前的项目可以登记或补充中标接收草稿", 409)
    profile = db.get(m.ProjectProfile, project.id)
    customer = db.get(m.Customer, profile.customer_id) if profile and profile.customer_id else None
    expected = _expected_classification(customer.rule_key if customer else None)
    if expected and expected != data.customer_classification:
        raise DomainError(
            "CUSTOMER_CLASSIFICATION_CONFLICT",
            "人工确认的客户分类与当前项目客户规则冲突，请先核对项目档案", 409,
        )
    case, latest = _current_case_revision(db, project.id)
    if latest:
        if data.version != latest.version + 1 or data.previous_revision_id != latest.id:
            raise DomainError("BID_INTAKE_VERSION_CONFLICT", "必须在同一中标接收记录的最新版本上继续补充", 409)
    elif data.version != 1 or data.previous_revision_id:
        raise DomainError("BID_INTAKE_VERSION_INVALID", "首个中标接收草稿版本必须为1且无前序版本", 409)
    if case:
        linked = list(db.execute(
            select(m.BidIntakeLifecycleLink, m.BusinessSubject)
            .join(m.BusinessSubject, m.BusinessSubject.id == m.BidIntakeLifecycleLink.subject_id)
            .where(m.BidIntakeLifecycleLink.case_id == case.id)
        ))
        if any(link.link_kind == "REJECTION" and subject.status == "EFFECTIVE" for link, subject in linked):
            raise DomainError("BID_INTAKE_REJECTED", "项目已形成有效拒单决定，不能继续补充执行草稿", 409)
        if any(link.link_kind == "INTERNAL_START" and subject.status == "EFFECTIVE" for link, subject in linked):
            raise DomainError("BID_INTAKE_STARTED", "项目已正式开工，中标接收草稿已结束", 409)
    quote = None
    if data.matched_quotation_subject_id:
        quote = domains.require_source(
            db, data.matched_quotation_subject_id, project.id, {"quotation"},
            statuses=("EFFECTIVE", "CLOSED"),
        )
    blobs = _validate_files(db, user, data, run)
    fingerprint_value = _intake_fingerprint(data, blobs)
    if case and db.scalar(select(m.BidIntakeRevision.id).where(
        m.BidIntakeRevision.case_id == case.id,
        m.BidIntakeRevision.source_fingerprint == fingerprint_value,
    ).limit(1)):
        raise DomainError("BID_INTAKE_DUPLICATE", "相同来源和内容的中标资料已经登记", 409)
    display = {
        "操作": "登记中标接收草稿" if not latest else "补充同一中标接收草稿",
        "项目": project.code + " · " + project.name,
        "项目版本": project.row_version,
        "草稿版本": "V" + str(data.version),
        "前序版本": data.previous_revision_id or "首版",
        "客户分类": {"HISENSE": "海信", "HAIER": "海尔", "OTHER": "其他客户"}[data.customer_classification],
        "分类依据": data.classification_evidence,
        "客户与联系人": data.customer_company + " · " + data.customer_contact,
        "客户模号/机型物料": (data.customer_mold_number or "未匹配") + " · " + (data.customer_model_or_material or "未提供"),
        "项目名称": data.project_name_snapshot,
        "金额": (str(data.amount) + " " + data.currency) if data.amount is not None else "资料未提供，留空待人工补充",
        "我方收件人": data.our_recipient,
        "外部订单与开工": (data.external_order_number or "未提供") + " · " + (
            data.external_start_date.isoformat() if data.external_start_date else "未提供"
        ),
        "客户交期": data.customer_due_date.isoformat() if data.customer_due_date else "未提供",
        "客户工艺方案": (
            "已人工确认 · " + data.customer_process_confirmation_evidence
            if data.customer_process_confirmed else "尚未确认"
        ),
        "报价匹配": quote.number if quote else "未匹配，保留为空",
        "历史模具": (
            data.historical_mold_number + " · " + data.historical_relation_kind
            if data.historical_mold_number else "未匹配，保留为空"
        ),
        "匹配结论": data.match_result + " · " + data.match_evidence,
        "资料来源": data.source_kind + " · " + data.source_ref + " · " + data.received_date.isoformat(),
        "资料附件": [blobs[item.file_id].filename + " · " + item.role for item in data.attachments],
        "说明": "本人确认后把资料写入同一中标接收记录的新版本；不会自动承接、拒单、登记正式合同或下达内部开工。",
    }
    return project, case, latest, blobs, fingerprint_value, display


def execute_bid_intake_tool(db, user, key, arguments, run=None):
    if key != "prepare_bid_intake_draft":
        raise DomainError("TOOL_UNKNOWN", "工具未实现", 403)
    data = parse_bid_intake(arguments)
    _, _, _, _, _, display = preview_bid_intake(db, user, data, run)
    proposal = {
        "kind": "bid_intake", "action": "record_bid_intake", "requires_approval": False,
        "input": data.model_dump(mode="json"), "display": display,
        "confirmation_policy": proposal_confirmation_policy(run, requires_approval=False),
    }
    return {
        "data": [], "source": "agent_proposal", "as_of": now().isoformat(),
        "proposal": proposal,
        "limitations": [
            "仅准备中标接收资料登记；本人确认后追加同一草稿版本，不自动承接、拒单、形成正式合同或下达开工。"
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
    if step.tool not in available_tools(db, user) or step.tool != "prepare_bid_intake_draft" or not proposal:
        raise DomainError("TOOL_FORBIDDEN", "操作能力不可用", 403)
    return proposal, run


def validate_intent(db, user, payload):
    proposal, run = source(db, user, payload["step_id"])
    if content_hash(proposal) != payload["proposal_hash"]:
        raise DomainError("CONFIRMATION_INVALID", "操作建议内容已变化", 409)
    data = parse_bid_intake(proposal["input"])
    _, _, _, _, _, display = preview_bid_intake(db, user, data, run)
    if content_hash(display) != content_hash(proposal["display"]):
        raise DomainError("VERSION_CONFLICT", "项目、前序草稿或资料已变化，请重新准备", 409)
    return proposal, data, run


def start_condition_snapshot(db, user, project_id):
    """Return authoritative start prerequisites without exposing attachment names."""
    case, revision = _current_case_revision(db, project_id)
    dossier_visible = _has_dossier_access(db, user, project_id)
    blockers = []
    if not case or not revision:
        blockers.append("尚无已确认的中标接收记录。")
        return {
            "visible": dossier_visible,
            "case_id": case.id if case else None,
            "current_revision_id": None,
            "current_version": None,
            "customer_process_confirmed": False,
            "customer_process_confirmation_evidence": None,
            "external_order_number": None,
            "external_start_date": None,
            "customer_due_date": None,
            "has_external_start_notice": False,
            "complete": False,
            "blockers": blockers,
        }
    if not dossier_visible:
        blockers.append("当前人员无权核对中标接收附件和客户开工条件。")
        return {
            "visible": False,
            "case_id": None,
            "current_revision_id": None,
            "current_version": None,
            "customer_process_confirmed": None,
            "customer_process_confirmation_evidence": None,
            "external_order_number": None,
            "external_start_date": None,
            "customer_due_date": None,
            "has_external_start_notice": None,
            "complete": False,
            "blockers": blockers,
        }
    revision_ids = select(m.BidIntakeRevision.id).where(
        m.BidIntakeRevision.case_id == case.id
    )
    has_external_notice = bool(db.scalar(select(m.BidIntakeAttachment.id).where(
        m.BidIntakeAttachment.revision_id.in_(revision_ids),
        m.BidIntakeAttachment.role == "EXTERNAL_START_NOTICE",
    ).limit(1)))
    if not revision.customer_process_confirmed:
        blockers.append("客户工艺方案尚未由人工确认并留存依据。")
    if not revision.external_order_number:
        blockers.append("尚未记录客户外部订单号。")
    if not revision.external_start_date:
        blockers.append("尚未记录客户开工日期。")
    if not revision.customer_due_date:
        blockers.append("尚未记录客户交期。")
    if not has_external_notice:
        blockers.append("尚未见客户外部开工通知附件。")
    return {
        "visible": True,
        "case_id": case.id,
        "current_revision_id": revision.id,
        "current_version": revision.version,
        "customer_process_confirmed": revision.customer_process_confirmed,
        "customer_process_confirmation_evidence": revision.customer_process_confirmation_evidence,
        "external_order_number": revision.external_order_number,
        "external_start_date": revision.external_start_date.isoformat() if revision.external_start_date else None,
        "customer_due_date": revision.customer_due_date.isoformat() if revision.customer_due_date else None,
        "has_external_start_notice": has_external_notice,
        "complete": not blockers,
        "blockers": blockers,
    }


def link_lifecycle_subject(db, user, project_id, subject, link_kind, source_revision_id=None):
    """Link a newly created decision/start material when an intake case exists."""
    case = db.scalar(select(m.BidIntakeCase).where(m.BidIntakeCase.project_id == project_id))
    if not case:
        return None
    existing = db.scalar(select(m.BidIntakeLifecycleLink).where(
        m.BidIntakeLifecycleLink.subject_id == subject.id
    ))
    if existing:
        return existing
    if source_revision_id:
        revision = db.get(m.BidIntakeRevision, source_revision_id)
        if not revision or revision.case_id != case.id:
            raise DomainError(
                "BID_INTAKE_REVISION_MISMATCH",
                "正式开工引用的中标接收版本不属于当前项目",
                409,
            )
    link = m.BidIntakeLifecycleLink(
        case_id=case.id, subject_id=subject.id, source_revision_id=source_revision_id,
        link_kind=link_kind, linked_by=user.id,
    )
    db.add(link)
    db.flush()
    return link


def _backfill_lifecycle_links(db, user, case):
    subjects = db.scalars(select(m.BusinessSubject).where(
        m.BusinessSubject.project_id == case.project_id,
        m.BusinessSubject.kind.in_(["quote_acceptance", "internal_start"]),
    ))
    for subject in subjects:
        detail = db.get(m.BusinessDecisionDetail, subject.id)
        if not detail:
            continue
        link_kind = (
            "INTERNAL_START" if subject.kind == "internal_start" else
            "ACCEPTANCE" if detail.decision == "ACCEPT" else
            "REJECTION" if detail.decision == "REJECT" else None
        )
        if link_kind:
            link_lifecycle_subject(db, user, case.project_id, subject, link_kind)


def confirm(db, user, payload):
    proposal, data, run = validate_intent(db, user, payload)
    project, case, _, blobs, fingerprint_value, _ = preview_bid_intake(db, user, data, run)
    if not case:
        case = m.BidIntakeCase(project_id=project.id, created_by=user.id)
        db.add(case)
        db.flush()
    revision = m.BidIntakeRevision(
        case_id=case.id, version=data.version, previous_revision_id=data.previous_revision_id,
        source_kind=data.source_kind, source_ref=data.source_ref,
        source_fingerprint=fingerprint_value, received_date=data.received_date,
        customer_classification=data.customer_classification,
        classification_evidence=data.classification_evidence,
        classification_confirmed_by=user.id, customer_company=data.customer_company,
        customer_contact=data.customer_contact, customer_mold_number=data.customer_mold_number,
        customer_model_or_material=data.customer_model_or_material,
        project_name_snapshot=data.project_name_snapshot, amount=data.amount, currency=data.currency,
        our_recipient=data.our_recipient, external_order_number=data.external_order_number,
        external_start_date=data.external_start_date, customer_due_date=data.customer_due_date,
        customer_process_confirmed=data.customer_process_confirmed,
        customer_process_confirmation_evidence=data.customer_process_confirmation_evidence,
        matched_quotation_subject_id=data.matched_quotation_subject_id,
        historical_mold_number=data.historical_mold_number,
        historical_relation_kind=data.historical_relation_kind, match_result=data.match_result,
        match_evidence=data.match_evidence, notes=data.notes, recorded_by=user.id,
    )
    db.add(revision)
    db.flush()
    for item in data.attachments:
        blob = blobs[item.file_id]
        db.add(m.BidIntakeAttachment(
            revision_id=revision.id, file_id=blob.id, role=item.role,
            content_sha256=blob.sha256, title=blob.filename,
        ))
    _backfill_lifecycle_links(db, user, case)
    record(db, user, "bid_intake.revision.recorded", case.id, {
        "project_id": project.id, "revision_id": revision.id, "version": revision.version,
        "source_kind": revision.source_kind, "source_ref": revision.source_ref,
        "customer_classification": revision.customer_classification,
        "match_result": revision.match_result,
    })
    return {
        "project_id": project.id, "case_id": case.id, "revision_id": revision.id,
        "version": revision.version, "action": proposal["action"], "status": "RECORDED",
    }


router = APIRouter()


@router.get("/api/bid-intake-proposals/{step_id}")
def proposal_status(step_id: str, user=Depends(current_user), db=Depends(get_db)):
    source(db, user, step_id)
    intent = db.scalar(select(m.HumanIntent).where(
        m.HumanIntent.user_id == user.id,
        m.HumanIntent.action == "bid_intake.execute",
        m.HumanIntent.resource_id == step_id,
        m.HumanIntent.receipt["status"].as_string() == "RECORDED",
    ).order_by(m.HumanIntent.created_at.desc()))
    return {"receipt": intent.receipt if intent else None}


@router.post("/api/bid-intake-proposals/{step_id}/intent")
def intent(step_id: str, user=Depends(current_user), db=Depends(get_db)):
    from domain_packs.mold.erp.core.business import create_intent

    proposal, _ = source(db, user, step_id)
    payload = {"step_id": step_id, "proposal_hash": content_hash(proposal)}
    result = create_intent(db, user, "bid_intake.execute", step_id, payload)
    result["display"] = proposal["display"]
    result["confirmation_policy"] = proposal.get("confirmation_policy")
    db.commit()
    return result
