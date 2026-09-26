"""中标确认事件到后续项目匹配的事务边界。"""
from sqlalchemy import select

from domain_packs.mold import models as m
from agent_core.host_ports import host_ports
from domain_packs.mold.authorization import require
from domain_packs.mold.ports.db import now
from domain_packs.mold.ports.errors import DomainError
from domain_packs.mold.ports.events import record


def _value(event, name, default=None):
    if isinstance(event, dict):
        return event.get(name, default)
    return getattr(event, name, default)


def safe_classification_metadata(classification, confirmed_document_type):
    """从分类结果提取允许进入审计/领域表的标量元数据。"""
    if not isinstance(classification, dict):
        classification = {}
    values = {
        "classification_id": classification.get("classification_id"),
        "confirmed_document_type": confirmed_document_type,
        "document_type": classification.get("document_type"),
        "event_type": classification.get("event_type"),
        "confidence": classification.get("confidence"),
        "classifier_version": classification.get("classifier_version"),
        "decision": classification.get("decision"),
    }
    return {key: value for key, value in values.items() if value is not None}


def confirmed_bid_event_snapshot(event):
    """校验确认事件的完整协议并只提取不含正文的安全元数据。"""
    if _value(event, "action") != "bid_notice.confirmed":
        raise DomainError("BID_CONFIRMATION_REQUIRED", "只有人工确认的中标事件才能进入后续流程")
    detail = _value(event, "detail")
    if not isinstance(detail, dict):
        raise DomainError("BID_CONFIRMATION_REQUIRED", "中标确认事件缺少安全快照")
    if detail.get("confirmed_document_type") != "BID_NOTICE":
        raise DomainError("BID_DOCUMENT_REQUIRED", "当前事件未确认文档为中标通知")

    evidence_snapshot = detail.get("evidence_snapshot")
    legacy_classifier_version = (
        evidence_snapshot.get("classifier_version")
        if isinstance(evidence_snapshot, dict) else None
    )
    classifier_version = detail.get("classifier_version") or legacy_classifier_version
    required_values = {
        "file_version_id": detail.get("file_version_id"),
        "classification_id": detail.get("classification_id"),
        "inbound_record_id": detail.get("inbound_record_id"),
        "evidence_snapshot": evidence_snapshot,
        "classifier_version": classifier_version,
        "operation_id": detail.get("operation_id"),
    }
    missing = [
        key for key, value in required_values.items()
        if value is None or (isinstance(value, str) and not value.strip())
    ]
    if missing:
        raise DomainError(
            "BID_EVENT_METADATA_MISSING",
            "中标确认事件缺少必需字段：" + "、".join(missing),
            409,
        )
    if not isinstance(evidence_snapshot, dict):
        raise DomainError("BID_EVENT_METADATA_INVALID", "中标确认事件的证据快照格式无效", 409)
    if detail["file_version_id"] != _value(event, "resource_id"):
        raise DomainError("BID_SOURCE_MISMATCH", "中标确认事件的文件版本与事件资源不一致", 409)

    snapshot = safe_classification_metadata(
        evidence_snapshot, detail["confirmed_document_type"],
    )
    snapshot["classification_id"] = detail["classification_id"]
    snapshot["classifier_version"] = classifier_version
    return snapshot


def validate_project_match(match, *, expected_row_version, project_id, project_version):
    """校验一次人工项目匹配的状态和版本边界。"""
    if _value(match, "status") != "PENDING_MATCH":
        raise DomainError("BID_MATCH_ALREADY_RESOLVED", "中标项目匹配已经处理，不能覆盖原决定", 409)
    if _value(match, "row_version") != expected_row_version:
        raise DomainError("STALE_VERSION", "中标项目匹配记录已变化，请重新读取", 409)
    existing_project = _value(match, "project_id")
    if existing_project and existing_project != project_id:
        raise DomainError("PROJECT_MATCH_CONFLICT", "中标通知已经关联其他项目", 409)
    if not project_id or project_version < 1:
        raise DomainError("PROJECT_MATCH_INVALID", "项目和项目版本不能为空")


def validate_intake_revision_confirmation(match, revision, *, expected_row_version, revision_id):
    """校验已匹配项目与中标接收版本的一致性。"""
    if _value(match, "status") != "MATCHED":
        raise DomainError("BID_MATCH_REQUIRED", "中标通知尚未完成项目匹配", 409)
    if _value(match, "row_version") != expected_row_version:
        raise DomainError("STALE_VERSION", "中标项目匹配记录已变化，请重新读取", 409)
    if not revision or _value(revision, "id") != revision_id:
        raise DomainError("BID_REVISION_NOT_FOUND", "中标接收版本不存在或已变化", 404)
    if _value(revision, "case_project_id") != _value(match, "project_id"):
        raise DomainError("BID_REVISION_PROJECT_CONFLICT", "中标接收版本不属于已匹配项目", 409)


def require_intake_confirmed(match):
    """检查待匹配记录是否已经人工确认项目和中标接收版本。"""
    status = _value(match, "status")
    if status not in {"MATCHED", "INTAKE_CONFIRMED"}:
        raise DomainError("BID_MATCH_REQUIRED", "中标通知尚未完成项目匹配")
    if not _value(match, "bid_intake_revision_id"):
        raise DomainError("BID_INTAKE_REVISION_REQUIRED", "中标接收版本尚未确认")
    if not _value(match, "project_id"):
        raise DomainError("PROJECT_REQUIRED", "中标通知尚未关联项目")


def confirm_intake_revision(db, user, match_id, *, expected_row_version, revision_id, operation_id):
    """将已匹配的项目与已有中标接收版本绑定，不代填或新建接收内容。"""
    match = db.scalar(select(m.BidNoticeMatch).where(
        m.BidNoticeMatch.id == str(match_id),
    ).with_for_update())
    if not match:
        raise DomainError("NOT_FOUND", "中标项目匹配记录不存在", 404)
    host_ports().uploaded_file(db, user, match.source_file_id)
    if not match.project_id:
        raise DomainError("PROJECT_REQUIRED", "中标通知尚未匹配项目", 409)
    require(db, user, "project.read", {"project_id": match.project_id})
    existing = db.scalar(select(m.AuditEvent).where(
        m.AuditEvent.action == "bid_notice.intake_confirmed",
        m.AuditEvent.resource_id == match.id,
        m.AuditEvent.detail["operation_id"].as_string() == operation_id,
    ).limit(1))
    if existing:
        return dict(existing.detail or {})
    revision = db.get(m.BidIntakeRevision, str(revision_id))
    case = db.get(m.BidIntakeCase, revision.case_id) if revision else None
    revision_view = {
        "id": revision.id if revision else revision_id,
        "case_project_id": case.project_id if case else None,
    }
    validate_intake_revision_confirmation(
        match,
        revision_view,
        expected_row_version=expected_row_version,
        revision_id=str(revision_id),
    )
    project = db.get(m.Project, match.project_id)
    if not project:
        raise DomainError("PROJECT_NOT_FOUND", "项目不存在", 404)
    require(db, user, "project.read", {"project_id": project.id})
    if project.row_version != match.project_version:
        raise DomainError("VERSION_CONFLICT", "项目资料已变化，请重新匹配中标通知", 409)

    match.bid_intake_case_id = case.id
    match.bid_intake_revision_id = revision.id
    match.status = "INTAKE_CONFIRMED"
    match.row_version += 1
    detail = {
        "match_id": match.id,
        "project_id": match.project_id,
        "project_version": match.project_version,
        "bid_intake_case_id": case.id,
        "bid_intake_revision_id": revision.id,
        "operation_id": operation_id,
        "status": match.status,
    }
    record(db, user, "bid_notice.intake_confirmed", match.id, detail)
    db.flush()
    return detail


def summarize_department_gate(acks, required_keys):
    """只汇总部门状态，不把通知送达误算成部门承接。"""
    required = set(required_keys)
    accepted, pending, returned = [], [], []
    for ack in acks:
        key = ack.get("department_key")
        if key not in required:
            continue
        status = ack.get("status")
        if status == "ACCEPTED":
            accepted.append(key)
        elif status in {"RETURNED", "NEED_INFO"}:
            returned.append(key)
        else:
            pending.append(key)
    present = set(accepted) | set(pending) | set(returned)
    pending.extend(sorted(required - present))
    return {
        "ready": not pending and not returned and set(accepted) == required,
        "accepted": sorted(accepted),
        "pending": sorted(pending),
        "returned": sorted(returned),
    }


def confirm_project_match(db, user, match_id, *, expected_row_version, project_id,
                          project_version, match_evidence, operation_id):
    """人工确认中标通知对应项目；不创建中标接收版本或正式开工。"""
    match = db.scalar(select(m.BidNoticeMatch).where(
        m.BidNoticeMatch.id == str(match_id),
    ).with_for_update())
    if not match:
        raise DomainError("NOT_FOUND", "中标项目匹配记录不存在", 404)
    host_ports().uploaded_file(db, user, match.source_file_id)
    # 幂等回放也必须先通过项目读取权限，不能凭历史操作号越权读取匹配结果。
    require(db, user, "project.read", {"project_id": project_id})
    existing = db.scalar(select(m.AuditEvent).where(
        m.AuditEvent.action == "bid_notice.project_matched",
        m.AuditEvent.resource_id == match.id,
        m.AuditEvent.detail["operation_id"].as_string() == operation_id,
    ).limit(1))
    if existing:
        return dict(existing.detail or {})
    validate_project_match(
        match,
        expected_row_version=expected_row_version,
        project_id=project_id,
        project_version=project_version,
    )
    project = db.get(m.Project, project_id)
    if not project:
        raise DomainError("PROJECT_NOT_FOUND", "项目不存在", 404)
    require(db, user, "project.read", {"project_id": project_id})
    if project.row_version != project_version:
        raise DomainError("VERSION_CONFLICT", "项目资料已变化，请重新查询", 409)
    match.project_id = project_id
    match.project_version = project_version
    match.status = "MATCHED"
    match.match_evidence = match_evidence
    match.row_version += 1
    match.matched_by = user.id
    match.matched_at = now()
    detail = {
        "match_id": match.id,
        "project_id": project_id,
        "project_version": project_version,
        "match_evidence": match_evidence,
        "operation_id": operation_id,
        "status": match.status,
    }
    record(db, user, "bid_notice.project_matched", match.id, detail)
    db.flush()
    return detail


def consume_confirmed_bid_notice(db, user, event_id):
    """将已确认中标事件登记为待项目匹配记录，不自动匹配或开工。"""
    event = db.get(m.AuditEvent, str(event_id))
    if not event:
        raise DomainError("BID_EVENT_NOT_FOUND", "中标确认事件不存在", 404)
    snapshot = confirmed_bid_event_snapshot(event)
    detail = event.detail
    file_id = event.resource_id
    # 幂等重放也必须重新检查当前文件权限，不能凭历史事件越权读取结果。
    host_ports().uploaded_file(db, user, file_id)
    intake_id = detail.get("inbound_record_id") or detail.get("document_intake_id")
    if not intake_id:
        raise DomainError("BID_SOURCE_MISSING", "中标确认事件缺少文档接收记录")

    existing = db.scalar(select(m.BidNoticeMatch).where(
        m.BidNoticeMatch.confirmation_event_id == event.id,
    ))
    if existing:
        return existing

    intake_file = db.scalar(select(m.DocumentIntakeFile).where(
        m.DocumentIntakeFile.intake_id == str(intake_id),
        m.DocumentIntakeFile.file_id == file_id,
    ))
    if not intake_file:
        raise DomainError("BID_SOURCE_MISSING", "中标确认文件不属于原文档接收记录", 409)
    if intake_file.confirmed_type != "BID_NOTICE":
        raise DomainError("BID_DOCUMENT_REQUIRED", "文件当前未确认成中标通知", 409)
    row = m.BidNoticeMatch(
        confirmation_event_id=event.id,
        source_file_id=file_id,
        inbound_record_id=str(intake_id),
        status="PENDING_MATCH",
        evidence_snapshot=snapshot,
        row_version=1,
    )
    db.add(row)
    db.flush()
    return row
