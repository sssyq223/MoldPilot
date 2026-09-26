"""Document intake service for conversation-owned PDF uploads."""
from collections import defaultdict
from datetime import date
from decimal import Decimal, InvalidOperation

from sqlalchemy import and_, select, text, func

from agent_core.host_ports import host_ports
from domain_packs.mold import models as m
from domain_packs.mold.authorization import predicate, require
from domain_packs.mold.ports.errors import DomainError


MAX_INTAKE_FILES = 10
DOCUMENT_PROCESSING_MEDIA_TYPES = {
    "application/pdf", "image/png", "image/jpeg",
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
}
ENGINEERING_CONTACT_TYPE = "ENGINEERING_CONTACT"


def _intake_files(db, intake_id):
    return list(db.scalars(
        select(m.DocumentIntakeFile)
        .where(m.DocumentIntakeFile.intake_id == intake_id)
        .order_by(m.DocumentIntakeFile.created_at, m.DocumentIntakeFile.id)
    ))


def _job(db, intake_file_id, phase="PRECLASSIFY"):
    return db.scalar(select(m.DocumentOcrJob).where(
        m.DocumentOcrJob.intake_file_id == intake_file_id,
        m.DocumentOcrJob.phase == phase,
    ))


def _duplicate_sha_groups(db, owner_id, blobs):
    digests = {blob.sha256 for blob in blobs}
    grouped = defaultdict(list)
    if digests:
        rows = db.scalars(
            select(m.FileObject)
            .where(m.FileObject.owner_id == owner_id, m.FileObject.sha256.in_(digests))
            .order_by(m.FileObject.created_at, m.FileObject.id)
            .limit(500)
        )
        for blob in rows:
            grouped[blob.sha256].append(blob)
    return [
        {
            "sha256": digest,
            "file_ids": [blob.id for blob in rows],
            "filenames": [blob.filename for blob in rows],
        }
        for digest, rows in sorted(grouped.items())
        if len(rows) > 1
    ]


def serialize(db, intake, *, include_admin_start_drafts=False, user=None, project_query=None):
    files = []
    blobs = []
    for intake_file in _intake_files(db, intake.id):
        blob = db.get(m.FileObject, intake_file.file_id)
        phase = "FULL_CONTRACT" if intake_file.confirmed_type == "SALES_CONTRACT" else "PRECLASSIFY"
        job = _job(db, intake_file.id, phase)
        classification = None
        conversion = None
        completion = None
        if job and phase == "PRECLASSIFY":
            completion = db.scalar(select(m.AuditEvent).where(
                m.AuditEvent.action == "document.ocr.completed",
                m.AuditEvent.resource_id == job.id,
            ).order_by(m.AuditEvent.created_at.desc(), m.AuditEvent.id.desc()).limit(1))
            if completion and isinstance(completion.detail, dict):
                classification = completion.detail.get("classification")
                conversion = completion.detail.get("conversion")
        if not blob:
            continue
        blobs.append(blob)
        files.append({
            "id": intake_file.id,
            "file_id": blob.id,
            "filename": blob.filename,
            "media_type": blob.media_type,
            "size": blob.size,
            "sha256": blob.sha256,
            "suggested_type": (classification or {}).get('document_type') or intake_file.suggested_type,
            "classification_id": completion.id if completion else None,
            "suggested_confidence": (
                str(intake_file.suggested_confidence)
                if intake_file.suggested_confidence is not None else None
            ),
            "classification": classification,
            "conversion": conversion,
            "confirmed_type": ('ENGINEERING_CONTACT' if db.scalar(select(m.AuditEvent.id).where(
                m.AuditEvent.action == 'engineering_contact.document.confirmed',
                m.AuditEvent.resource_id == blob.id,
                m.AuditEvent.user_id == intake.created_by,
            ).limit(1)) else intake_file.confirmed_type),
            "confirmed_role": intake_file.confirmed_role,
            "contract_group_id": intake_file.contract_group_id,
            "ocr_status": job.status if job else None,
            "ocr_error": job.last_error if job and job.status == "FAILED" else None,
            "ocr_job": {
                "id": job.id, "phase": job.phase, "status": job.status,
                "attempts": job.attempts, "created_at": job.created_at,
                "current_attempt": job.attempts + 1 if job.status == 'PROCESSING' else None,
                "previous_error": job.last_error if job.status in {'PROCESSING','RETRY_WAIT','QUEUED'} else None,
                "error_code": job.last_error if job.status == 'FAILED' else None,
                "started_at": job.started_at, "finished_at": job.finished_at,
                "retry_at": job.retry_at, "last_error": job.last_error,
                "cached_pages": db.scalar(select(func.count(func.distinct(m.DocumentRecognizedPage.page_number))).where(
                    m.DocumentRecognizedPage.intake_file_id == intake_file.id)),
            } if job else None,
        })
    file_ids = [file["file_id"] for file in files]
    admin_drafts = []
    if file_ids and include_admin_start_drafts:
        drafts = db.scalars(select(m.AdminStartNoticeDraft).where(
            m.AdminStartNoticeDraft.source_file_id.in_(file_ids),
        ).order_by(m.AdminStartNoticeDraft.created_at, m.AdminStartNoticeDraft.id)).all()
        for draft in drafts:
            departments = list(db.scalars(select(m.AdminStartDepartmentAck).where(
                m.AdminStartDepartmentAck.draft_id == draft.id,
            ).order_by(m.AdminStartDepartmentAck.department_key)))
            context = None
            if user:
                from domain_packs.mold.erp.commercial.admin_start_workflow import admin_start_source_context
                context = admin_start_source_context(db, user, draft.id, project_query=project_query)
            admin_drafts.append({
                "id": draft.id,
                "source_file_id": draft.source_file_id,
                "status": draft.status,
                "decision": draft.decision,
                "project_id": draft.project_id,
                "project_version": draft.project_version,
                "current_revision": draft.current_revision,
                "row_version": draft.row_version,
                "material_snapshot": draft.material_snapshot,
                "departments": [{
                    "department_key": ack.department_key,
                    "status": ack.status,
                    "notified_at": ack.notified_at,
                    "acked_at": ack.acked_at,
                } for ack in departments],
                "created_at": draft.created_at,
                "confirmed_at": draft.confirmed_at,
                "source_context": context,
            })
    return {
        "id": intake.id,
        "conversation_id": intake.conversation_id,
        "status": intake.status,
        "row_version": intake.row_version,
        "created_at": intake.created_at,
        "files": files,
        "admin_start_drafts": admin_drafts,
        "duplicate_sha_groups": _duplicate_sha_groups(db, intake.created_by, blobs),
    }


def _owned_conversation(db, user, conversation_id):
    conversation = db.get(m.Conversation, conversation_id)
    if not conversation or conversation.user_id != user.id:
        raise DomainError("NOT_FOUND", "会话不存在或无权访问", 404)
    return conversation


def load(db, user, intake_id, *, lock=False):
    query = select(m.DocumentIntake).where(
        m.DocumentIntake.id == intake_id,
        m.DocumentIntake.created_by == user.id,
    )
    if lock:
        query = query.with_for_update()
    intake = db.scalar(query)
    if not intake:
        raise DomainError("NOT_FOUND", "文档接收记录不存在或无权访问", 404)
    _owned_conversation(db, user, intake.conversation_id)
    return intake


def create(db, user, *, conversation_id, file_ids, request_key):
    if not file_ids or len(file_ids) > MAX_INTAKE_FILES or len(file_ids) != len(set(file_ids)):
        raise DomainError("DOCUMENT_FILES_INVALID", "每个接收批次须包含1至10个不重复文档")
    _owned_conversation(db, user, conversation_id)
    db.execute(text("SELECT pg_advisory_xact_lock(hashtextextended(:key,0))"), {
        "key": f"document-intake:{user.id}:{request_key}",
    })
    existing = db.scalar(select(m.DocumentIntake).where(
        m.DocumentIntake.created_by == user.id,
        m.DocumentIntake.request_key == request_key,
    ))
    if existing:
        existing_ids = {row.file_id for row in _intake_files(db, existing.id)}
        if existing.conversation_id != conversation_id or existing_ids != set(file_ids):
            raise DomainError("IDEMPOTENCY_CONFLICT", "同一次文档接收的文件或会话已变化", 409)
        return existing

    ports = host_ports()
    blobs = []
    for file_id in file_ids:
        blob = ports.uploaded_file(db, user, file_id)
        if blob.conversation_id != conversation_id:
            raise DomainError("FILE_CONTEXT_INVALID", "PDF 必须属于当前会话", 403)
        if blob.media_type not in DOCUMENT_PROCESSING_MEDIA_TYPES:
            raise DomainError("DOCUMENT_MEDIA_TYPE_UNSUPPORTED", "文档识别只接受 PDF、PNG、JPG 或 DOCX 文件")
        linked = db.scalar(select(m.DocumentIntakeFile).where(
            m.DocumentIntakeFile.file_id == blob.id,
        ))
        if linked:
            raise DomainError("DOCUMENT_ALREADY_INGESTED", "该 PDF 已进入文档识别流程", 409)
        blobs.append(blob)

    intake = m.DocumentIntake(
        conversation_id=conversation_id,
        created_by=user.id,
        request_key=request_key,
        status="PRECLASSIFYING",
    )
    db.add(intake)
    db.flush()
    for blob in blobs:
        intake_file = m.DocumentIntakeFile(intake_id=intake.id, file_id=blob.id)
        db.add(intake_file)
        db.flush()
        db.add(m.DocumentOcrJob(
            intake_file_id=intake_file.id,
            phase="PRECLASSIFY",
            status="QUEUED",
        ))
    ports.record(db, user, "document.intake.created", intake.id, {
        "conversation_id": conversation_id,
        "file_ids": [blob.id for blob in blobs],
        "sha256": [blob.sha256 for blob in blobs],
    })
    db.flush()
    return intake


def list_for_conversation(db, user, conversation_id):
    _owned_conversation(db, user, conversation_id)
    return list(db.scalars(
        select(m.DocumentIntake)
        .where(
            m.DocumentIntake.conversation_id == conversation_id,
            m.DocumentIntake.created_by == user.id,
        )
        .order_by(m.DocumentIntake.created_at.desc(), m.DocumentIntake.id.desc())
        .limit(100)
    ))


def _validate_preclassification_confirmation(db, user, intake, confirmations):
    intake_files = _intake_files(db, intake.id)
    by_id = {row.id: row for row in intake_files}
    provided_ids = [row["intake_file_id"] for row in confirmations]
    if len(provided_ids) != len(set(provided_ids)) or set(provided_ids) != set(by_id):
        raise DomainError("DOCUMENT_CONFIRMATION_INCOMPLETE", "必须一次确认本批次全部文档类型")
    jobs = [_job(db, row.id) for row in intake_files]
    if any(job is None or job.status != "SUCCEEDED" for job in jobs):
        raise DomainError("PRECLASSIFICATION_INCOMPLETE", "文档预分类尚未全部完成", 409)
    return by_id


def confirm_engineering_contact(db, user, intake_id, *, expected_version, confirmations, operation_id):
    intake = load(db, user, intake_id, lock=True)
    if not user.active:
        raise DomainError('FORBIDDEN', '账号不可用', 403)
    if _owned_conversation(db, user, intake.conversation_id).archived:
        raise DomainError('CONVERSATION_ARCHIVED', '归档会话只可查看', 409)
    if not isinstance(operation_id, str) or not operation_id.strip():
        raise DomainError('IDEMPOTENCY_KEY_REQUIRED', '分类确认缺少操作标识', 400)
    # 首版明确采用单文件门禁，不能吞掉同批合同或中标文件的后续处理。
    if (len(confirmations) != 1 or confirmations[0]['document_type'] != ENGINEERING_CONTACT_TYPE
            or confirmations[0].get('contract_group_key')):
        raise DomainError('ENGINEERING_CONTACT_FILE_REQUIRED', '请将工程联络单单独上传并确认', 409)
    item = db.get(m.DocumentIntakeFile, confirmations[0]['intake_file_id'])
    if not item or item.intake_id != intake.id or len(_intake_files(db, intake.id)) != 1:
        raise DomainError('ENGINEERING_CONTACT_FILE_REQUIRED', '请将工程联络单单独上传并确认', 409)
    blob = host_ports().uploaded_file(db, user, item.file_id)
    existing = db.scalar(select(m.AuditEvent).where(
        m.AuditEvent.action == 'engineering_contact.document.confirmed',
        m.AuditEvent.resource_id == blob.id,
    ).order_by(m.AuditEvent.created_at.desc()).limit(1))
    if existing:
        if existing.user_id == user.id and existing.detail.get('operation_id') == operation_id:
            return intake, item
        raise DomainError('DOCUMENT_TYPES_ALREADY_CONFIRMED', '工程联络单分类已经确认', 409)
    if intake.row_version != expected_version:
        raise DomainError('STALE_VERSION', '文档接收记录已变化', 409)
    if intake.status != 'AWAITING_TYPE_CONFIRMATION':
        raise DomainError('PRECLASSIFICATION_INCOMPLETE', '文档尚不可确认', 409)
    _validate_preclassification_confirmation(db, user, intake, confirmations)
    completion = db.scalar(select(m.AuditEvent).where(
        m.AuditEvent.action == 'document.ocr.completed',
        m.AuditEvent.resource_id == _job(db, item.id).id,
    ).order_by(m.AuditEvent.created_at.desc(), m.AuditEvent.id.desc()).limit(1))
    classification = (completion.detail or {}).get('classification') if completion else None
    if not isinstance(classification, dict) or not classification.get('classifier_version'):
        raise DomainError('CLASSIFICATION_NOT_FOUND', '缺少可追溯的分类结果', 409)
    item.confirmed_by = user.id
    item.confirmed_at = host_ports().now()
    # 旧枚举列保持空值；查询通过事件投影出真实类型，不伪装成 OTHER。
    intake.status = 'CLASSIFIED_ARCHIVED'
    intake.row_version += 1
    detail = {
        'document_intake_id': intake.id, 'intake_file_id': item.id, 'file_id': blob.id,
        'file_sha256': blob.sha256, 'classification_id': completion.id,
        'classifier_version': classification['classifier_version'],
        'document_type': ENGINEERING_CONTACT_TYPE, 'operation_id': operation_id,
        'row_version': intake.row_version,
    }
    # 只引用已有识别快照，不复制正文或未经验证的模型字段到确认审计。
    host_ports().record(db, user, 'document.classification.confirmed', blob.id, detail)
    host_ports().record(db, user, 'engineering_contact.document.confirmed', blob.id, detail)
    db.flush()
    return intake, item


def confirm_types(db, user, intake_id, *, expected_version, confirmations):
    intake = load(db, user, intake_id, lock=True)
    if intake.row_version != expected_version:
        raise DomainError("STALE_VERSION", "文档接收记录已被其他操作更新", 409)
    if intake.status != "AWAITING_TYPE_CONFIRMATION":
        if intake.status in {"CLASSIFIED_ARCHIVED", "FULL_OCR_QUEUED", "FULL_OCR_PROCESSING", "AWAITING_FIELD_CONFIRMATION"}:
            raise DomainError("DOCUMENT_TYPES_ALREADY_CONFIRMED", "文档类型已经确认", 409)
        raise DomainError("PRECLASSIFICATION_INCOMPLETE", "PDF 预分类尚未全部完成", 409)

    intake_files = _intake_files(db, intake.id)
    by_id = {row.id: row for row in intake_files}
    provided_ids = [row["intake_file_id"] for row in confirmations]
    if len(provided_ids) != len(set(provided_ids)) or set(provided_ids) != set(by_id):
        raise DomainError("DOCUMENT_CONFIRMATION_INCOMPLETE", "必须一次确认本批次全部 PDF 类型")
    jobs = [_job(db, row.id) for row in intake_files]
    if any(job is None or job.status != "SUCCEEDED" for job in jobs):
        raise DomainError("PRECLASSIFICATION_INCOMPLETE", "PDF 预分类尚未全部完成", 409)

    group_keys = []
    for item in confirmations:
        is_contract = item["document_type"] == "SALES_CONTRACT"
        group_key = (item.get("contract_group_key") or "").strip()
        if is_contract and not group_key:
            raise DomainError("CONTRACT_GROUP_REQUIRED", "销售合同 PDF 必须选择合同分组")
        if not is_contract and group_key:
            raise DomainError("CONTRACT_GROUP_INVALID", "非销售合同 PDF 不能加入合同分组")
        if is_contract and group_key not in group_keys:
            group_keys.append(group_key)

    groups = {}
    for group_key in group_keys:
        group = m.ContractIntakeGroup(
            intake_id=intake.id,
            group_key=group_key,
            status="FULL_OCR_QUEUED",
        )
        db.add(group)
        db.flush()
        groups[group_key] = group

    assigned_roles = set()
    current = host_ports().now()
    for item in confirmations:
        intake_file = by_id[item["intake_file_id"]]
        intake_file.confirmed_type = item["document_type"]
        intake_file.confirmed_by = user.id
        intake_file.confirmed_at = current
        if item["document_type"] == "SALES_CONTRACT":
            group = groups[item["contract_group_key"].strip()]
            intake_file.contract_group_id = group.id
            intake_file.confirmed_role = "MAIN" if group.id not in assigned_roles else "ATTACHMENT"
            assigned_roles.add(group.id)
            db.add(m.DocumentOcrJob(
                intake_file_id=intake_file.id,
                phase="FULL_CONTRACT",
                status="QUEUED",
            ))

    intake.status = "FULL_OCR_QUEUED" if groups else "CLASSIFIED_ARCHIVED"
    intake.row_version += 1
    host_ports().record(db, user, "document.type.confirmed", intake.id, {
        "status": intake.status,
        "types": [
            {"intake_file_id": row["intake_file_id"], "document_type": row["document_type"]}
            for row in confirmations
        ],
        "contract_group_ids": [group.id for group in groups.values()],
    })
    db.flush()
    return intake


def retry_failed_ocr(db, user, intake_id, *, expected_version):
    intake = load(db, user, intake_id, lock=True)
    if intake.row_version != expected_version:
        raise DomainError("STALE_VERSION", "文档接收记录已被其他操作更新", 409)
    if intake.status != "OCR_FAILED":
        raise DomainError("OCR_RETRY_NOT_ALLOWED", "当前没有可人工重试的失败 OCR 任务", 409)
    intake_files = _intake_files(db, intake.id)
    jobs = list(db.scalars(
        select(m.DocumentOcrJob)
        .where(
            m.DocumentOcrJob.intake_file_id.in_([row.id for row in intake_files]),
            m.DocumentOcrJob.status == "FAILED",
        )
        .with_for_update()
    ))
    if not jobs:
        raise DomainError("OCR_RETRY_NOT_ALLOWED", "未找到失败 OCR 任务", 409)
    phases = {job.phase for job in jobs}
    if len(phases) != 1:
        raise DomainError("OCR_RETRY_AMBIGUOUS", "失败任务阶段不一致，需人工排查", 409)
    phase = next(iter(phases))
    for job in jobs:
        job.status = "QUEUED"
        job.attempts = 0
        job.retry_at = None
        job.lease_id = None
        job.lease_until = None
        job.finished_at = None
    if phase == "PRECLASSIFY":
        intake.status = "PRECLASSIFYING"
    else:
        intake.status = "FULL_OCR_QUEUED"
        group_ids = {row.contract_group_id for row in intake_files if row.contract_group_id}
        for group in db.scalars(select(m.ContractIntakeGroup).where(
            m.ContractIntakeGroup.id.in_(group_ids or [""])
        ).with_for_update()):
            if group.status == "OCR_FAILED":
                group.status = "FULL_OCR_QUEUED"
                group.row_version += 1
    intake.row_version += 1
    host_ports().record(db, user, "document.ocr.retry_confirmed", intake.id, {
        "phase": phase,
        "job_ids": [job.id for job in jobs],
    })
    db.flush()
    return intake


def load_group(db, user, group_id, *, lock=False):
    query = (
        select(m.ContractIntakeGroup)
        .join(m.DocumentIntake, m.DocumentIntake.id == m.ContractIntakeGroup.intake_id)
        .where(
            m.ContractIntakeGroup.id == group_id,
            m.DocumentIntake.created_by == user.id,
        )
    )
    if lock:
        query = query.with_for_update()
    group = db.scalar(query)
    if not group:
        raise DomainError("NOT_FOUND", "合同识别分组不存在或无权访问", 404)
    return group


def _group_fields(db, group_id):
    return list(db.scalars(
        select(m.DocumentExtractedField)
        .join(m.DocumentOcrJob, m.DocumentOcrJob.id == m.DocumentExtractedField.job_id)
        .join(m.DocumentIntakeFile, m.DocumentIntakeFile.id == m.DocumentOcrJob.intake_file_id)
        .where(m.DocumentIntakeFile.contract_group_id == group_id, m.DocumentOcrJob.status == 'SUCCEEDED')
        .order_by(
            m.DocumentExtractedField.scope,
            m.DocumentExtractedField.row_key,
            m.DocumentExtractedField.field_key,
            m.DocumentExtractedField.id,
        )
    ))


def _json_value(value):
    return value.get("value") if isinstance(value, dict) else None


def _field_map(fields, *, confirmed=False):
    result = {}
    for field in fields:
        source = field.confirmed_value if confirmed and field.confirmed_value is not None else field.normalized_value
        result[(field.scope, field.row_key, field.field_key)] = _json_value(source)
    return result


def _normalized_text(value):
    return "".join(str(value or "").split()).casefold()


def _project_matches(db, fields):
    values = _field_map(fields)
    project_number = values.get(("HEADER", "header", "project_number"))
    order_number = values.get(("HEADER", "header", "external_order_number"))
    project_name = values.get(("HEADER", "header", "project_name"))
    customer_name = values.get(("HEADER", "header", "customer_name"))
    mold_numbers = {
        str(value) for (scope, _row, key), value in values.items()
        if scope == "MOLD" and key == "customer_mold_number" and value
    }
    normalized_molds = {_normalized_text(value) for value in mold_numbers}
    matches = {}
    projects = list(db.scalars(select(m.Project).order_by(m.Project.code).limit(501)))
    for project in projects[:500]:
        score = 0
        matched_by = []
        if project_number and _normalized_text(project.code) == _normalized_text(project_number):
            score = max(score, 100)
            matched_by.append("项目编号")
        if order_number and _normalized_text(project.code) == _normalized_text(order_number):
            score = max(score, 100)
            matched_by.append("订单号")
        if project_name and (
            _normalized_text(project_name) in _normalized_text(project.name)
            or _normalized_text(project.name) in _normalized_text(project_name)
        ):
            score = max(score, 50)
            matched_by.append("项目名称")
        profile = db.get(m.ProjectProfile, project.id)
        customer = db.get(m.Customer, profile.customer_id) if profile and profile.customer_id else None
        if customer_name and customer and (
            _normalized_text(customer_name) in _normalized_text(customer.name)
            or _normalized_text(customer.name) in _normalized_text(customer_name)
        ):
            score = max(score, 50)
            matched_by.append("客户名称")
        molds = list(db.scalars(
            select(m.Mold)
            .join(m.ProjectMold, m.ProjectMold.mold_id == m.Mold.id)
            .where(m.ProjectMold.project_id == project.id)
            .order_by(m.Mold.internal_number)
        ))
        if normalized_molds and any(_normalized_text(mold.internal_number) in normalized_molds for mold in molds):
            score = max(score, 90)
            matched_by.append("客户模号")
        if score:
            matches[project.id] = {
                "project": project,
                "profile": profile,
                "customer": customer,
                "molds": molds,
                "score": score,
                "matched_by": sorted(set(matched_by)),
            }
    return matches


def project_candidates(db, user, group_id):
    group = load_group(db, user, group_id)
    if group.status not in {"AWAITING_FIELD_CONFIRMATION", "READY_FOR_DRAFT"}:
        raise DomainError("CONTRACT_OCR_NOT_READY", "合同 OCR 结果尚不可复核", 409)
    matches = _project_matches(db, _group_fields(db, group.id))
    visible_ids = set(db.scalars(select(m.Project.id).where(and_(
        predicate(db, user, "project.read", {"project_id": m.Project.id}),
        predicate(db, user, "project.dossier.read", {"project_id": m.Project.id}),
    ))))
    visible = [row for project_id, row in matches.items() if project_id in visible_ids]
    visible.sort(key=lambda row: (-row["score"], row["project"].code, row["project"].id))
    top_score = visible[0]["score"] if visible else None
    top_count = sum(1 for row in visible if row["score"] == top_score)
    if not visible:
        resolution = "NOT_FOUND_OR_FORBIDDEN" if matches else "NOT_FOUND"
    else:
        resolution = "RESOLVED" if top_count == 1 else "MULTIPLE_CANDIDATES"
    projects = []
    for row in visible[:20]:
        project = row["project"]
        projects.append({
            "id": project.id,
            "code": project.code,
            "name": project.name,
            "status": project.status,
            "row_version": project.row_version,
            "score": row["score"],
            "matched_by": row["matched_by"],
            "customer": ({"id": row["customer"].id, "name": row["customer"].name}
                         if row["customer"] else None),
            "molds": [{"id": mold.id, "internal_number": mold.internal_number, "name": mold.name}
                      for mold in row["molds"]],
        })
    return {"resolution": resolution, "projects": projects, "project_confirmation_required": True}


def serialize_group(db, group):
    fields = _group_fields(db, group.id)
    jobs = {row.id: row for row in db.scalars(select(m.DocumentOcrJob).where(
        m.DocumentOcrJob.id.in_({field.job_id for field in fields})
    ))} if fields else {}
    intake_files = {row.id: row for row in db.scalars(select(m.DocumentIntakeFile).where(
        m.DocumentIntakeFile.id.in_({job.intake_file_id for job in jobs.values()})
    ))} if jobs else {}
    blobs = {row.id: row for row in db.scalars(select(m.FileObject).where(
        m.FileObject.id.in_({row.file_id for row in intake_files.values()})
    ))} if intake_files else {}
    matches = list(db.scalars(
        select(m.ContractIntakeMoldMatch)
        .where(m.ContractIntakeMoldMatch.group_id == group.id)
        .order_by(m.ContractIntakeMoldMatch.row_key)
    ))
    return {
        "id": group.id,
        "intake_id": group.intake_id,
        "group_key": group.group_key,
        "status": group.status,
        "row_version": group.row_version,
        "project_id": group.project_id,
        "project_version": group.project_version,
        "customer_id": group.customer_id,
        "relationship": {
            "relation_type": group.relation_type,
            "target_contract_id": group.relation_target_contract_id,
            "reason": group.relation_reason,
        } if group.relation_type else None,
        "review_warnings": group.review_warnings,
        "fields": [{
            "id": field.id,
            "scope": field.scope,
            "row_key": field.row_key,
            "field_key": field.field_key,
            "raw_value": field.raw_value,
            "normalized_value": field.normalized_value,
            "confidence": str(field.confidence),
            "page_number": field.page_number,
            "bbox": field.bbox,
            "confirmed_value": field.confirmed_value,
            "source": {
                "file_id": blobs[intake_files[jobs[field.job_id].intake_file_id].file_id].id,
                "filename": blobs[intake_files[jobs[field.job_id].intake_file_id].file_id].filename,
                "page_number": field.page_number,
                "block_ids": list(field.source_block_ids),
            },
        } for field in fields],
        "mold_mappings": [{
            "row_key": row.row_key,
            "mold_id": row.mold_id,
            "customer_mold_number": row.customer_mold_number,
            "machine_model": row.machine_model,
            "material_number": row.material_number,
            "amount": str(row.amount) if row.amount is not None else None,
            "currency": row.currency,
            "due_date": row.due_date.isoformat() if row.due_date else None,
        } for row in matches],
    }


def _decimal_value(value, label, *, required=False):
    if value in (None, ""):
        if required:
            raise DomainError("CONFIRMED_FIELD_INVALID", f"{label}不能为空")
        return None
    try:
        result = Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError):
        raise DomainError("CONFIRMED_FIELD_INVALID", f"{label}不是有效金额") from None
    if result < 0:
        raise DomainError("CONFIRMED_FIELD_INVALID", f"{label}不能为负数")
    return result


def _date_value(value, label):
    if value in (None, ""):
        return None
    try:
        return date.fromisoformat(str(value))
    except ValueError:
        raise DomainError("CONFIRMED_FIELD_INVALID", f"{label}不是有效日期") from None


def review_group(
    db, user, group_id, *, expected_version, project_id, project_version,
    confirmed_fields, mold_mappings, relationship,
):
    group = load_group(db, user, group_id, lock=True)
    if group.row_version != expected_version:
        raise DomainError("STALE_VERSION", "合同复核记录已被其他操作更新", 409)
    if group.status != "AWAITING_FIELD_CONFIRMATION":
        raise DomainError("CONTRACT_OCR_NOT_READY", "合同 OCR 结果尚不可复核", 409)
    project = db.scalar(select(m.Project).where(m.Project.id == project_id).with_for_update(read=True))
    if not project:
        raise DomainError("PROJECT_NOT_FOUND_OR_FORBIDDEN", "项目不存在或无权访问", 404)
    require(db, user, "sales_contract.create", {"project_id": project.id})
    require(db, user, "sales_contract.submit", {"project_id": project.id})
    if project.row_version != project_version:
        raise DomainError("PROJECT_VERSION_CHANGED", "项目版本已变化，请重新确认", 409)
    profile = db.get(m.ProjectProfile, project.id)
    if not profile or not profile.customer_id:
        raise DomainError("CUSTOMER_MAPPING_REQUIRED", "项目档案必须先维护客户关系", 409)
    customer = db.get(m.Customer, profile.customer_id)
    if not customer:
        raise DomainError("CUSTOMER_MAPPING_REQUIRED", "项目客户不存在，请先修复项目档案", 409)

    fields = _group_fields(db, group.id)
    field_by_id = {field.id: field for field in fields}
    supplied = {row["field_id"]: row["confirmed_value"] for row in confirmed_fields}
    if len(supplied) != len(confirmed_fields) or set(supplied) != set(field_by_id):
        raise DomainError("FIELD_CONFIRMATION_INCOMPLETE", "必须逐项确认全部 OCR 字段")
    if any(not isinstance(value, dict) or set(value) != {"value"} for value in supplied.values()):
        raise DomainError("CONFIRMED_FIELD_INVALID", "确认值必须使用 value 对象")
    values = {
        (field.scope, field.row_key, field.field_key): _json_value(supplied[field.id])
        for field in fields
    }
    ocr_customer = values.get(("HEADER", "header", "customer_name"))
    if ocr_customer and _normalized_text(ocr_customer) != _normalized_text(customer.name):
        raise DomainError("CUSTOMER_CONFLICT", "合同客户与项目客户不一致", 409)

    mold_rows = sorted({field.row_key for field in fields if field.scope == "MOLD"})
    mapping_by_row = {row["row_key"]: row["mold_id"] for row in mold_mappings}
    if len(mapping_by_row) != len(mold_mappings) or set(mapping_by_row) != set(mold_rows):
        raise DomainError("MOLD_MAPPING_REQUIRED", "每一条合同模具明细都必须关联正式模具", 409)
    linked_molds = set(db.scalars(select(m.ProjectMold.mold_id).where(
        m.ProjectMold.project_id == project.id,
        m.ProjectMold.mold_id.in_(list(mapping_by_row.values()) or [""]),
    )))
    if linked_molds != set(mapping_by_row.values()):
        raise DomainError("MOLD_NOT_IN_PROJECT", "选择的模具不属于目标项目", 409)

    relation_type = relationship["relation_type"]
    target_id = relationship.get("target_contract_id")
    reason = relationship["reason"].strip()
    if relation_type == "NEW":
        if target_id:
            raise DomainError("CONTRACT_RELATION_INVALID", "新合同不能选择历史合同")
    else:
        target = db.get(m.BusinessSubject, target_id) if target_id else None
        if not target or target.kind != "sales_contract" or target.project_id != project.id:
            raise DomainError("CONTRACT_RELATION_INVALID", "关联合同必须是同项目销售合同", 409)

    contract_amount = _decimal_value(values.get(("HEADER", "header", "amount")), "合同总额", required=True)
    mold_amounts = [_decimal_value(values.get(("MOLD", row_key, "amount")), "模具金额") for row_key in mold_rows]
    payment_rows = sorted({field.row_key for field in fields if field.scope == "PAYMENT"})
    payment_amounts = [
        _decimal_value(values.get(("PAYMENT", row_key, "amount")), "付款节点金额")
        for row_key in payment_rows
    ]
    payment_total = sum((value for value in payment_amounts if value is not None), Decimal("0"))
    if payment_total > contract_amount:
        raise DomainError("PAYMENT_TOTAL_EXCEEDS_CONTRACT", "付款节点合计超过合同总额", 409)
    warnings = []
    mold_total = sum((value for value in mold_amounts if value is not None), Decimal("0"))
    if mold_amounts and mold_total != contract_amount:
        warnings.append({
            "code": "MOLD_TOTAL_MISMATCH",
            "message": "模具明细金额合计与合同总额不一致，需由财务复核",
            "contract_amount": str(contract_amount),
            "mold_total": str(mold_total),
        })
    if payment_amounts and payment_total != contract_amount:
        warnings.append({
            "code": "PAYMENT_TOTAL_MISMATCH",
            "message": "付款节点金额合计与合同总额不一致，需由财务复核",
            "contract_amount": str(contract_amount),
            "payment_total": str(payment_total),
        })

    current = host_ports().now()
    for field in fields:
        field.confirmed_value = supplied[field.id]
        field.confirmed_by = user.id
        field.confirmed_at = current
    for row_key, mold_id in mapping_by_row.items():
        db.add(m.ContractIntakeMoldMatch(
            group_id=group.id,
            row_key=row_key,
            customer_mold_number=values.get(("MOLD", row_key, "customer_mold_number")),
            machine_model=values.get(("MOLD", row_key, "machine_model")),
            material_number=values.get(("MOLD", row_key, "material_number")),
            amount=_decimal_value(values.get(("MOLD", row_key, "amount")), "模具金额"),
            currency=values.get(("MOLD", row_key, "currency")),
            due_date=_date_value(values.get(("MOLD", row_key, "due_date")), "模具交期"),
            mold_id=mold_id,
            confirmed_by=user.id,
            confirmed_at=current,
        ))
    group.project_id = project.id
    group.project_version = project.row_version
    group.customer_id = customer.id
    group.relation_type = relation_type
    group.relation_target_contract_id = target_id
    group.relation_reason = reason
    group.confirmed_by = user.id
    group.confirmed_at = current
    group.review_warnings = warnings
    group.status = "READY_FOR_DRAFT"
    group.row_version += 1
    host_ports().record(db, user, "contract.intake.reviewed", group.id, {
        "project_id": project.id,
        "mold_ids": sorted(mapping_by_row.values()),
        "field_ids": sorted(field_by_id),
        "relationship": {"relation_type": relation_type, "target_contract_id": target_id},
        "warnings": [row["code"] for row in warnings],
    })
    db.flush()
    return group
