"""中标事件触发后的超级管理员开工草稿与决定流程。"""
from sqlalchemy import select

from domain_packs.mold.authorization import predicate

from agent_core.host_ports import host_ports
from domain_packs.mold import models as m
from domain_packs.mold.erp.commercial.bid_start_workflow import confirmed_bid_event_snapshot, consume_confirmed_bid_notice
from domain_packs.mold.ports.db import now
from domain_packs.mold.ports.errors import DomainError
from domain_packs.mold.ports.events import record
from domain_packs.mold.erp.commercial.admin_start_schemas import material_values
from domain_packs.mold.erp.commercial.contract_intake_models import DocumentRecognizedPage
from domain_packs.mold.erp.commercial.bid_field_candidates import validate_bid_fields
from domain_packs.mold.erp.project.admin_start_workflow_models import ADMIN_DEPARTMENT_KEYS


ADMIN_DECISIONS = {"INTERNAL_ACCEPTED", "FULL_OUTSOURCE_ACCEPTED", "REJECTED"}
ACCEPTED_DECISIONS = {"INTERNAL_ACCEPTED", "FULL_OUTSOURCE_ACCEPTED"}
ACTIVE_DRAFT_STATUSES = {"ADMIN_PENDING_INPUT", "ADMIN_CONFIRMED", "NEEDS_REVIEW", "DEPARTMENTS_NOTIFIED"}
DECISION_ELIGIBLE_STATUSES = {"ADMIN_PENDING_INPUT", "NEEDS_REVIEW", "DEPARTMENTS_NOTIFIED"}


def _normalized(value):
    return "".join(str(value or "").split()).casefold()


def _plain_value(value):
    if isinstance(value, dict) and set(value) == {"value"}:
        return value["value"]
    return value


def _source_pages(db, draft):
    intake_file = db.scalar(select(m.DocumentIntakeFile).where(
        m.DocumentIntakeFile.file_id == draft.source_file_id,
        m.DocumentIntakeFile.intake_id == draft.inbound_record_id,
    ))
    if not intake_file:
        raise DomainError("BID_SOURCE_MISSING", "中标邮件不属于原文档接收记录", 409)
    pages = list(db.scalars(select(DocumentRecognizedPage).where(
        DocumentRecognizedPage.intake_file_id == intake_file.id,
    ).order_by(DocumentRecognizedPage.page_number, DocumentRecognizedPage.created_at.desc())))
    latest = {}
    for page in pages:
        latest.setdefault(page.page_number, page)
    return intake_file, list(latest.values())


def _source_field(pages, value, field_key, *, confidence=None):
    value = _plain_value(value)
    if value in (None, ""):
        return None
    if isinstance(value, (list, tuple)):
        values = value
    else:
        values = [value]
    result = []
    for item in values:
        text_value = str(item).strip()
        needle = _normalized(text_value)
        if not needle:
            continue
        for page in pages:
            for block in page.blocks or []:
                if not isinstance(block, dict):
                    continue
                block_text = str(block.get("text") or "")
                if needle not in _normalized(block_text):
                    continue
                result.append({
                    "field_key": field_key, "value": item,
                    "confidence": str(confidence) if confidence is not None else None,
                    "page_number": page.page_number,
                    "source_block_ids": [block.get("block_id")],
                    "source_text": block_text[:500],
                })
                break
            if result and result[-1]["value"] == item:
                break
    return result or None


def _classification_source(db, draft):
    event = db.get(m.AuditEvent, draft.source_event_id)
    if not event:
        raise DomainError("BID_SOURCE_MISSING", "中标确认事件不存在", 409)
    classification_id = (event.detail or {}).get("classification_id")
    classification_event = db.get(m.AuditEvent, classification_id) if classification_id else None
    classification = (classification_event.detail or {}).get("classification") if classification_event else None
    if not isinstance(classification, dict):
        raise DomainError("BID_CLASSIFICATION_SOURCE_INVALID", "中标邮件的确认分类版本不存在", 409)
    _intake_file, pages = _source_pages(db, draft)
    extracted = classification.get("extracted") if isinstance(classification.get("extracted"), dict) else {}
    blocks = {}
    for page in pages:
        for block in page.blocks or []:
            if isinstance(block, dict) and block.get("block_id"):
                blocks[block["block_id"]] = (page.page_number, str(block.get("text") or ""))
    try:
        bid_fields = validate_bid_fields(classification.get("bid_fields", []), blocks)
    except DomainError:
        raise
    fields = {}
    references = {}
    for row in bid_fields:
        target = references if row["field_key"] in {"bid_amount", "bid_currency"} else fields
        key = "customer_mold_numbers" if row["field_key"] == "customer_mold_number" else row["field_key"]
        target.setdefault(key, []).append(row)
    # 兼容已完成但尚未保存 bid_fields 的旧分类：仍必须回到同一文件页级文字核对，不能直接信任旧 extracted。
    if not bid_fields:
        for field_key in ("project_name", "project_number", "customer_name",
                          "external_order_number", "customer_due_date"):
            source = _source_field(pages, extracted.get(field_key), field_key)
            if source:
                fields[field_key] = source
        mold_source = _source_field(pages, extracted.get("customer_mold_number"), "customer_mold_number")
        if mold_source:
            fields["customer_mold_numbers"] = mold_source
        for field_key in ("amount", "currency"):
            source = _source_field(pages, extracted.get(field_key), field_key)
            if source:
                references[field_key] = source
    blob = db.get(m.FileObject, draft.source_file_id)
    return {
        "source_file": {
            "id": blob.id, "filename": blob.filename, "media_type": blob.media_type,
            "size": blob.size, "sha256": blob.sha256,
        } if blob else None,
        "classification_id": classification_event.id,
        "fields": fields,
        "references": references,
        "warnings": ["中标金额和币种仅作为邮件参考，不能直接视为正式合同金额。"] if references.get("bid_amount") else [],
    }


def _project_candidates(db, user, source, project_query=None):
    values = {key: row[0]["value"] for key, row in source["fields"].items() if row}
    query = _normalized(project_query)
    rows = []
    for project in db.scalars(select(m.Project).where(
        m.Project.status.not_in({"CLOSED", "TERMINATED"}),
        predicate(db, user, "project.read", {"project_id": m.Project.id}),
        predicate(db, user, "project.dossier.read", {"project_id": m.Project.id}),
    ).order_by(m.Project.code).limit(500)):
        profile = db.get(m.ProjectProfile, project.id)
        customer = db.get(m.Customer, profile.customer_id) if profile and profile.customer_id else None
        molds = list(db.scalars(select(m.Mold).join(m.ProjectMold, m.ProjectMold.mold_id == m.Mold.id)
            .where(m.ProjectMold.project_id == project.id).order_by(m.Mold.internal_number)))
        haystack = [project.code, project.name, customer.name if customer else ""] + [m.internal_number for m in molds]
        if query and not any(query in _normalized(value) for value in haystack):
            continue
        score = 0
        matched = []
        project_name = values.get("project_name")
        customer_name = values.get("customer_name")
        mold_number = values.get("customer_mold_numbers")
        if project_name and _normalized(project_name) in _normalized(project.name):
            score, matched = max(score, 50), matched + ["项目名称"]
        if customer_name and customer and _normalized(customer_name) in _normalized(customer.name):
            score, matched = max(score, 50), matched + ["客户名称"]
        if mold_number and any(_normalized(mold_number) in _normalized(row.internal_number) for row in molds):
            score, matched = max(score, 90), matched + ["客户模号"]
        if project_query:
            matched.append("人工检索")
        if score or query:
            rows.append({"id": project.id, "code": project.code, "name": project.name,
                "status": project.status, "row_version": project.row_version,
                "score": score, "matched_by": sorted(set(matched)),
                "customer": {"id": customer.id, "name": customer.name} if customer else None,
                "molds": [{"id": row.id, "internal_number": row.internal_number, "name": row.name} for row in molds]})
    rows.sort(key=lambda row: (-row["score"], row["code"], row["id"]))
    return rows[:20]


def admin_start_source_context(db, user, draft_id, *, project_query=None):
    _is_super_admin(user)
    draft = db.get(m.AdminStartNoticeDraft, str(draft_id))
    if not draft:
        raise DomainError("NOT_FOUND", "内部开工通知草稿不存在", 404)
    host_ports().uploaded_file(db, user, draft.source_file_id)
    source = _classification_source(db, draft)
    source["projects"] = _project_candidates(db, user, source, project_query)
    source["project_confirmation_required"] = True
    return source


def _is_super_admin(user):
    if not user or not getattr(user, "super_admin", False) or not getattr(user, "active", True):
        raise DomainError("SUPER_ADMIN_REQUIRED", "只有超级管理员可以处理内部开工通知", 403)


def _source_snapshot(event):
    snapshot = confirmed_bid_event_snapshot(event)
    return {
        "source_event_id": event.id,
        "source_file_id": event.resource_id,
        "inbound_record_id": event.detail.get("inbound_record_id"),
        "classification": snapshot,
    }


def _required_material_fields(decision, material_snapshot):
    # 承接决定先于项目建立/选择；项目、模具编号和开工日期在决定后补齐。
    return []


def _project_material_complete(material_snapshot):
    required = ("project_id", "project_version", "internal_mold_numbers", "effective_date")
    return all(key in material_snapshot and material_snapshot[key] not in (None, "")
               for key in required)


def validate_admin_start_decision(draft, user, *, expected_row_version, decision,
                                  reason, material_snapshot):
    _is_super_admin(user)
    if getattr(draft, "status", None) not in DECISION_ELIGIBLE_STATUSES:
        raise DomainError("ADMIN_START_ALREADY_DECIDED", "内部开工通知已经形成最终决定", 409)
    if getattr(draft, "row_version", None) != expected_row_version:
        raise DomainError("STALE_VERSION", "内部开工通知草稿已变化，请重新读取", 409)
    if decision not in ADMIN_DECISIONS:
        raise DomainError("ADMIN_DECISION_INVALID", "内部开工决定不受支持")
    if not isinstance(reason, str) or not reason.strip():
        raise DomainError("ADMIN_DECISION_REASON_REQUIRED", "内部开工决定必须填写依据", 409)
    if not isinstance(material_snapshot, dict):
        raise DomainError("ADMIN_MATERIAL_INVALID", "内部开工资料必须是结构化对象", 400)
    missing = _required_material_fields(decision, material_snapshot)
    if missing:
        raise DomainError(
            "ADMIN_MATERIAL_REQUIRED",
            "内部开工资料缺少：" + "、".join(missing),
            409,
        )


def create_admin_start_notice_draft(db, user, event_id):
    """Skill 自动调用的 Tool：创建草稿；可预期业务失败会留下 NEEDS_REVIEW。"""
    event = db.scalar(select(m.AuditEvent).where(m.AuditEvent.id == str(event_id)).with_for_update())
    if not event:
        raise DomainError("BID_EVENT_NOT_FOUND", "中标确认事件不存在", 404)
    host_ports().uploaded_file(db, user, event.resource_id)
    existing = db.scalar(select(m.AdminStartNoticeDraft).where(
        m.AdminStartNoticeDraft.source_event_id == event.id,
    ))
    if existing:
        return existing
    source = _source_snapshot(event)
    draft = m.AdminStartNoticeDraft(
        source_event_id=event.id,
        source_file_id=event.resource_id,
        inbound_record_id=source["inbound_record_id"],
        status="ADMIN_PENDING_INPUT",
        material_snapshot=source,
        current_revision=1,
        row_version=1,
        created_by=event.user_id or user.id,
    )
    db.add(draft)
    db.flush()
    db.add(m.AdminStartNoticeRevision(
        draft_id=draft.id,
        revision=1,
        payload=source,
        reason="系统根据已确认的中标事件自动生成超级管理员待处理草稿",
        created_by=event.user_id or user.id,
    ))
    try:
        consume_confirmed_bid_notice(db, user, event.id)
    except DomainError as error:
        draft.status = "NEEDS_REVIEW"
        draft.row_version += 1
        failed = {**source, "failure_code": error.code}
        draft.material_snapshot = failed
        draft.current_revision += 1
        db.add(m.AdminStartNoticeRevision(
            draft_id=draft.id,
            revision=draft.current_revision,
            payload=failed,
            reason="系统自动触发未完成，待超级管理员核查",
            created_by=event.user_id or user.id,
        ))
        record(db, user, "admin_start_notice.failed", draft.id, {
            "draft_id": draft.id, "source_event_id": event.id,
            "failure_code": error.code, "status": draft.status,
        })
        db.flush()
        return draft
    recipients = list(db.scalars(select(m.User.id).where(
        m.User.active.is_(True), m.User.super_admin.is_(True),
    )))
    record(db, user, "admin_start_notice.pending", draft.id, {
        "draft_id": draft.id,
        "source_event_id": event.id,
        "source_file_id": event.resource_id,
        "status": draft.status,
        "recipient_count": len(recipients),
    }, recipients)
    db.flush()
    return draft


def checked_material(db, draft, patch):
    patch = material_values(patch)
    merged = {**(draft.material_snapshot or {}), **patch}
    pid = merged.get('project_id')
    if pid:
        project = db.scalar(select(m.Project).where(m.Project.id == pid).with_for_update())
        if not project or project.row_version != merged.get('project_version'):
            raise DomainError('VERSION_CONFLICT', '项目不存在或版本已变化', 409)
        if project.status in {'CLOSED', 'TERMINATED'}:
            raise DomainError('PROJECT_BLOCKED', '项目已关闭或终止', 409)
        profile = db.get(m.ProjectProfile, project.id)
        customer = db.get(m.Customer, profile.customer_id) if profile and profile.customer_id else None
        identity = {
            'project_name': project.name,
            'project_number': project.code,
            'customer_name': customer.name if customer else None,
        }
        for key, expected in identity.items():
            if merged.get(key) not in (None, '') and _normalized(merged[key]) != _normalized(expected):
                raise DomainError('PROJECT_IDENTITY_CONFLICT', f'所选项目与{key}资料不一致', 409)
        merged.update({key: value for key, value in identity.items() if value is not None})
    elif merged.get('internal_mold_numbers') or merged.get('project_version'):
        raise DomainError('PROJECT_REQUIRED', '指定模具或项目版本前须选择项目', 409)
    return merged


def update_admin_start_notice_draft(db, user, draft_id, *, expected_row_version,
                                    material_snapshot, reason, operation_id):
    draft = db.scalar(select(m.AdminStartNoticeDraft).where(
        m.AdminStartNoticeDraft.id == str(draft_id),
    ).with_for_update())
    if not draft:
        raise DomainError("NOT_FOUND", "内部开工通知草稿不存在", 404)
    _is_super_admin(user)
    existing = db.scalar(select(m.AuditEvent).where(
        m.AuditEvent.action == "admin_start_notice.updated",
        m.AuditEvent.resource_id == draft.id,
        m.AuditEvent.detail["operation_id"].as_string() == operation_id,
    ).limit(1))
    if existing:
        return dict(existing.detail or {})
    if draft.status not in ACTIVE_DRAFT_STATUSES:
        raise DomainError("ADMIN_START_ALREADY_DECIDED", "内部开工通知已经形成最终决定", 409)
    if draft.row_version != expected_row_version:
        raise DomainError("STALE_VERSION", "内部开工通知草稿已变化，请重新读取", 409)
    if not isinstance(material_snapshot, dict):
        raise DomainError("ADMIN_MATERIAL_INVALID", "内部开工资料必须是结构化对象", 400)
    if not isinstance(reason, str) or not reason.strip():
        raise DomainError("ADMIN_UPDATE_REASON_REQUIRED", "补充内部开工资料必须填写依据", 409)
    revision = draft.current_revision + 1
    merged = checked_material(db, draft, material_snapshot)
    if draft.decision and _project_material_complete(merged):
        draft.status = "READY_FOR_CONTRACT_MATCH"
    draft.project_id = merged.get('project_id')
    draft.project_version = merged.get('project_version')
    draft.material_snapshot = merged
    draft.current_revision = revision
    draft.row_version += 1
    db.add(m.AdminStartNoticeRevision(
        draft_id=draft.id, revision=revision, payload=merged,
        reason=reason.strip(), created_by=user.id,
    ))
    detail = {
        "draft_id": draft.id, "revision": revision,
        "row_version": draft.row_version, "operation_id": operation_id,
        "status": draft.status,
    }
    record(db, user, "admin_start_notice.updated", draft.id, detail)
    db.flush()
    return detail


def _department_ack_state(db, draft_id):
    acks = list(db.scalars(select(m.AdminStartDepartmentAck).where(
        m.AdminStartDepartmentAck.draft_id == draft_id,
    ).order_by(m.AdminStartDepartmentAck.department_key)))
    acknowledged = [a.department_key for a in acks if a.status == "ACKNOWLEDGED"]
    pending = [a.department_key for a in acks if a.status != "ACKNOWLEDGED"]
    return acks, acknowledged, pending


def dispatch_admin_start_notice(db, user, draft_id, *, expected_row_version,
                                department_keys, reason, operation_id):
    """项目部分发内部通知单给选定部门：只创建 SENT 回执，不代填确认。"""
    _is_super_admin(user)
    draft = db.scalar(select(m.AdminStartNoticeDraft).where(
        m.AdminStartNoticeDraft.id == str(draft_id),
    ).with_for_update())
    if not draft:
        raise DomainError("NOT_FOUND", "内部开工通知草稿不存在", 404)
    existing = db.scalar(select(m.AuditEvent).where(
        m.AuditEvent.action == "admin_start_notice.dispatched",
        m.AuditEvent.resource_id == draft.id,
        m.AuditEvent.detail["operation_id"].as_string() == operation_id,
    ).limit(1))
    if existing:
        return dict(existing.detail or {})
    if draft.status not in ACTIVE_DRAFT_STATUSES:
        raise DomainError("ADMIN_START_ALREADY_DECIDED", "内部开工通知已经形成最终决定", 409)
    if draft.row_version != expected_row_version:
        raise DomainError("STALE_VERSION", "内部开工通知草稿已变化，请重新读取", 409)
    if not isinstance(reason, str) or not reason.strip():
        raise DomainError("ADMIN_DISPATCH_REASON_REQUIRED", "分发内部通知单必须填写依据", 409)
    if not isinstance(department_keys, list) or not department_keys:
        raise DomainError("ADMIN_DEPARTMENT_INVALID", "必须至少选择一个接收部门", 409)
    if len(department_keys) != len(set(department_keys)):
        raise DomainError("ADMIN_DEPARTMENT_INVALID", "接收部门不能重复", 409)
    invalid = [key for key in department_keys if key not in ADMIN_DEPARTMENT_KEYS]
    if invalid:
        raise DomainError("ADMIN_DEPARTMENT_INVALID", "未知部门：" + "、".join(invalid), 409)
    dispatched = []
    for key in department_keys:
        row = db.scalar(select(m.AdminStartDepartmentAck).where(
            m.AdminStartDepartmentAck.draft_id == draft.id,
            m.AdminStartDepartmentAck.department_key == key,
        ))
        if row:
            continue
        db.add(m.AdminStartDepartmentAck(
            draft_id=draft.id, department_key=key, status="SENT",
            notified_by=user.id, notified_at=now(), row_version=1,
        ))
        dispatched.append(key)
    if not dispatched:
        raise DomainError("ADMIN_DEPARTMENT_ALREADY_SENT", "所选部门均已分发", 409)
    draft.status = "DEPARTMENTS_NOTIFIED"
    draft.row_version += 1
    detail = {
        "draft_id": draft.id, "dispatched": sorted(dispatched),
        "row_version": draft.row_version, "status": draft.status,
        "operation_id": operation_id,
    }
    record(db, user, "admin_start_notice.dispatched", draft.id, detail)
    db.flush()
    return detail


def acknowledge_admin_start_department(db, user, draft_id, *, expected_row_version,
                                       department_key, note, operation_id):
    """记录单个部门确认收到；重复回执幂等，不改变业务决定。"""
    _is_super_admin(user)
    draft = db.scalar(select(m.AdminStartNoticeDraft).where(
        m.AdminStartNoticeDraft.id == str(draft_id),
    ).with_for_update())
    if not draft:
        raise DomainError("NOT_FOUND", "内部开工通知草稿不存在", 404)
    existing = db.scalar(select(m.AuditEvent).where(
        m.AuditEvent.action == "admin_start_notice.department_acked",
        m.AuditEvent.resource_id == draft.id,
        m.AuditEvent.detail["operation_id"].as_string() == operation_id,
    ).limit(1))
    if existing:
        return dict(existing.detail or {})
    if draft.status != "DEPARTMENTS_NOTIFIED":
        raise DomainError("ADMIN_DEPARTMENTS_NOT_NOTIFIED", "内部通知单尚未分发部门", 409)
    if draft.row_version != expected_row_version:
        raise DomainError("STALE_VERSION", "内部开工通知草稿已变化，请重新读取", 409)
    if department_key not in ADMIN_DEPARTMENT_KEYS:
        raise DomainError("ADMIN_DEPARTMENT_INVALID", "未知部门", 409)
    ack = db.scalar(select(m.AdminStartDepartmentAck).where(
        m.AdminStartDepartmentAck.draft_id == draft.id,
        m.AdminStartDepartmentAck.department_key == department_key,
    ).with_for_update())
    if not ack:
        raise DomainError("ADMIN_DEPARTMENT_NOT_DISPATCHED", "该部门未收到内部通知单分发", 409)
    if ack.status == "ACKNOWLEDGED":
        detail = {
            "draft_id": draft.id, "department_key": department_key,
            "status": ack.status, "row_version": draft.row_version,
            "operation_id": operation_id, "replayed": True,
        }
        record(db, user, "admin_start_notice.department_acked", draft.id, detail)
        db.flush()
        return detail
    if not isinstance(note, str) or not note.strip():
        raise DomainError("ADMIN_ACK_NOTE_REQUIRED", "部门回执必须填写核对依据", 409)
    ack.status = "ACKNOWLEDGED"
    ack.acked_by = user.id
    ack.acked_at = now()
    ack.ack_note = note.strip()
    ack.row_version += 1
    draft.row_version += 1
    detail = {
        "draft_id": draft.id, "department_key": department_key,
        "status": ack.status, "row_version": draft.row_version,
        "operation_id": operation_id,
    }
    record(db, user, "admin_start_notice.department_acked", draft.id, detail)
    db.flush()
    return detail


def confirm_admin_start_notice(db, user, draft_id, *, expected_row_version,
                               decision, reason, material_snapshot, operation_id):
    _is_super_admin(user)
    draft = db.scalar(select(m.AdminStartNoticeDraft).where(
        m.AdminStartNoticeDraft.id == str(draft_id),
    ).with_for_update())
    if not draft:
        raise DomainError("NOT_FOUND", "内部开工通知草稿不存在", 404)
    existing = db.scalar(select(m.AuditEvent).where(
        m.AuditEvent.action == "admin_start_notice.decision",
        m.AuditEvent.resource_id == draft.id,
        m.AuditEvent.detail["operation_id"].as_string() == operation_id,
    ).limit(1))
    if existing:
        return dict(existing.detail or {})
    merged = checked_material(db, draft, material_snapshot)
    validate_admin_start_decision(
        draft, user, expected_row_version=expected_row_version,
        decision=decision, reason=reason, material_snapshot=merged,
    )
    revision = draft.current_revision + 1
    draft.project_id = merged.get('project_id')
    draft.project_version = merged.get('project_version')
    draft.material_snapshot = merged
    draft.current_revision = revision
    draft.row_version += 1
    draft.decision = decision
    draft.status = (
        "REJECTED" if decision == "REJECTED"
        else "READY_FOR_CONTRACT_MATCH" if _project_material_complete(merged)
        else "ADMIN_CONFIRMED"
    )
    draft.confirmed_by = user.id
    draft.confirmed_at = now()
    db.add(m.AdminStartNoticeRevision(
        draft_id=draft.id, revision=revision,
        payload={**merged, "decision": decision},
        reason=reason.strip(), created_by=user.id,
    ))
    _acks, acknowledged, pending = _department_ack_state(db, draft.id)
    detail = {
        "draft_id": draft.id, "revision": revision,
        "row_version": draft.row_version, "decision": decision,
        "status": draft.status, "operation_id": operation_id,
        "acknowledged_departments": acknowledged,
        "pending_departments": pending,
    }
    record(db, user, "admin_start_notice.decision", draft.id, detail)
    db.flush()
    return detail
