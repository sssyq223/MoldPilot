"""系统文档任务的人工确认适配器；不创建或模拟 Agent Run/Step。"""
from uuid import uuid4

from agent_core.host_ports import host_ports
from domain_packs.mold import models as m
from domain_packs.mold.authorization import fingerprint
from domain_packs.mold.erp.commercial import contract_intake
from domain_packs.mold.ports.bpm import content_hash
from domain_packs.mold.ports.events import record
from domain_packs.mold.ports.errors import DomainError

ACTION = 'document_workflow.execute'
OPERATIONS = {
    'types': 'prepare_document_type_confirmation',
    'retry': 'prepare_document_ocr_retry',
    'review': 'prepare_sales_contract_intake_review',
}


def _preview(db, user, operation, arguments):
    # 与对话工具共用同一输入、预览与领域确认规则，而不是第二套业务写入。
    from domain_packs.mold.tools.erp.commercial import document_intake_tools as tools
    key = OPERATIONS.get(operation)
    if not key:
        raise DomainError('ACTION_UNKNOWN', '未知文档确认操作')
    data = tools._parse(key, arguments)
    if operation == 'review':
        group = contract_intake.load_group(db, user, str(data.contract_intake_group_id))
        intake_id = group.intake_id
    else:
        intake_id = data.document_intake_id
    intake = contract_intake.load(db, user, str(intake_id))
    conversation = db.get(m.Conversation, intake.conversation_id)
    if conversation.archived:
        raise DomainError('CONVERSATION_ARCHIVED', '归档会话只可查看', 409)
    ports = host_ports()
    for row in contract_intake._intake_files(db, intake.id):
        ports.uploaded_file(db, user, row.file_id)
    return data, tools._preview(db, user, key, data, None)


def prepare(db, user, operation, arguments):
    from domain_packs.mold.erp.core import business
    data, display = _preview(db, user, operation, arguments)
    if operation == 'review':
        group = contract_intake.load_group(db, user, str(data.contract_intake_group_id))
        resource_id = group.intake_id
    else:
        resource_id = str(data.document_intake_id)
    payload = {'operation': operation, 'input': data.model_dump(mode='json'),
               'display_hash': content_hash(display), 'authorization_hash': fingerprint(db,user),
               'security_version': user.security_version, 'operation_id': str(uuid4())}
    result = business.create_intent(db, user, ACTION, resource_id, payload)
    result['display'] = display
    result['confirmation_policy'] = host_ports().proposal_confirmation_policy(None, requires_approval=False)
    return result


def validate_intent(db, user, payload):
    if (payload.get('security_version') != user.security_version
            or payload.get('authorization_hash') != fingerprint(db,user)):
        raise DomainError('AUTHORIZATION_CHANGED', '授权已变化，请重新核对文档', 403)
    data, display = _preview(db, user, payload.get('operation'), payload.get('input'))
    if payload.get('display_hash') != content_hash(display):
        raise DomainError('VERSION_CONFLICT', '文档或识别结果已变化，请重新核对', 409)
    return data


def confirm(db, user, payload):
    data = validate_intent(db, user, payload)
    if payload['operation'] == 'types':
        confirmations = [row.model_dump(mode='json') for row in data.files]
        if any(row['document_type'] == 'ENGINEERING_CONTACT' for row in confirmations):
            intake, contact_file = contract_intake.confirm_engineering_contact(
                db, user, str(data.document_intake_id), expected_version=data.expected_version,
                confirmations=confirmations, operation_id=payload.get('operation_id'),
            )
            run = _trigger_engineering_contact_skill(db, user, intake, contact_file, payload.get('operation_id'))
            action = 'engineering_contact_classification_confirmation'
        else:
            run = None
            intake = contract_intake.confirm_types(db, user, str(data.document_intake_id),
                expected_version=data.expected_version, confirmations=confirmations)
            _record_bid_confirmations(db, user, intake, data, payload.get('operation_id'))
            action = 'document_type_confirmation'
        return {'action':action, 'status':intake.status, 'document_intake_id':intake.id,
                'conversation_id':intake.conversation_id, 'row_version':intake.row_version,
                **({'run_id': run.id} if run else {})}
    if payload['operation'] == 'retry':
        intake = contract_intake.retry_failed_ocr(db, user, str(data.document_intake_id), expected_version=data.expected_version)
        action = 'document_ocr_retry'
        return {'action':action, 'status':intake.status, 'document_intake_id':intake.id,
                'conversation_id':intake.conversation_id, 'row_version':intake.row_version}
    if payload['operation'] == 'review':
        group = contract_intake.review_group(
            db, user, str(data.contract_intake_group_id),
            expected_version=data.expected_version,
            project_id=str(data.project_id),
            project_version=data.project_version,
            confirmed_fields=[row.model_dump(mode='json') for row in data.confirmed_fields],
            mold_mappings=[row.model_dump(mode='json') for row in data.mold_mappings],
            relationship=data.relationship.model_dump(mode='json'),
        )
        return {'action':'sales_contract_intake_review', 'status':group.status,
                'contract_intake_group_id':group.id, 'row_version':group.row_version}
    raise DomainError('ACTION_UNKNOWN', '未知文档确认操作')


def _trigger_engineering_contact_skill(db, user, intake, contact_file, operation_id):
    """分类确认后创建受信任 Agent Run，由 Skill/Tool 继续生成 Proposal。"""
    from app.config import model_settings
    from app.files import bind_run_files
    from app.run_model_selection import select_model
    from sqlalchemy import select

    existing = db.scalar(select(m.Run).where(
        m.Run.conversation_id == intake.conversation_id,
        m.Run.user_id == user.id,
        m.Run.checkpoint['engineering_contact_operation_id'].as_string() == str(operation_id),
    ).order_by(m.Run.created_at.desc(), m.Run.id.desc()).limit(1)) if operation_id else None
    if existing:
        return existing
    selection = select_model(user, default_enabled=model_settings().llm_enabled)
    run = m.Run(
        user_id=user.id,
        conversation_id=intake.conversation_id,
        security_version=user.security_version,
        prompt=("文档已由本人确认类型为工程联络单。请激活工程联络文档 Skill，先查询本次文档识别结果和本地项目候选，"
                "再根据识别字段准备 prepare_contact_create Proposal；不要直接写入业务事实。"),
        status="QUEUED" if selection else "WAITING_CONFIGURATION",
        checkpoint={
            "agent_permission_mode": "ask",
            "run_trigger": "DOCUMENT_CLASSIFICATION_CONFIRMED",
            "model_selection": selection,
            "engineering_contact_operation_id": str(operation_id),
        },
    )
    db.add(run)
    db.flush()
    bind_run_files(db, user, run, [contact_file.file_id])
    host_ports().record(db, user, "agent.run.created", run.id, {
        "run_trigger": "DOCUMENT_CLASSIFICATION_CONFIRMED",
        "source": "engineering_contact.document.confirmed",
        "document_intake_id": intake.id,
        "file_id": contact_file.file_id,
        "operation_id": operation_id,
        "model_selection": selection,
    })
    return run


def _record_bid_confirmations(db, user, intake, data, operation_id):
    """将人工确认的 BID_NOTICE 转成一次性事件，不自动创建承接或开工草稿。"""
    from sqlalchemy import select

    for item in data.files:
        if item.document_type != "BID_NOTICE":
            continue
        intake_file = db.get(m.DocumentIntakeFile, str(item.intake_file_id))
        if not intake_file or intake_file.intake_id != intake.id:
            raise DomainError("FILE_CONTEXT_INVALID", "中标分类确认文件不属于当前接收批次", 409)
        job = contract_intake._job(db, intake_file.id, "PRECLASSIFY")
        completion = db.scalar(select(m.AuditEvent).where(
            m.AuditEvent.action == "document.ocr.completed",
            m.AuditEvent.resource_id == job.id if job else False,
        ).order_by(m.AuditEvent.created_at.desc(), m.AuditEvent.id.desc()).limit(1)) if job else None
        detail = completion.detail if completion and isinstance(completion.detail, dict) else {}
        classification = detail.get("classification") if isinstance(detail, dict) else None
        classification_id = completion.id if completion else None
        event = record_confirmed_bid_event(
            db, user, intake, intake_file, classification_id, classification,
            item.document_type, operation_id,
        )
        from domain_packs.mold.skills.erp.commercial.bid_to_start_notice.orchestrator import (
            trigger_bid_to_start_notice,
        )
        trigger_bid_to_start_notice(db, user, event.id)


def record_confirmed_bid_event(db, user, intake, intake_file, classification_id,
                               classification, confirmed_document_type=None, operation_id=None):
    """发布一次人工确认后的中标事件；同一分类快照只发布一次。"""
    from sqlalchemy import select
    from domain_packs.mold.erp.commercial.bid_start_workflow import safe_classification_metadata

    confirmed_document_type = confirmed_document_type or (classification or {}).get("document_type")
    classifier_version = (classification or {}).get("classifier_version")
    missing = [
        name for name, value in (
            ("classification_id", classification_id),
            ("classifier_version", classifier_version),
            ("operation_id", operation_id),
        ) if value is None or (isinstance(value, str) and not value.strip())
    ]
    if missing:
        raise DomainError(
            "BID_EVENT_METADATA_MISSING",
            "中标确认事件缺少必需字段：" + "、".join(missing),
            409,
        )
    if confirmed_document_type != "BID_NOTICE":
        raise DomainError("BID_DOCUMENT_REQUIRED", "当前事件未确认文档为中标通知", 409)
    existing = db.scalar(select(m.AuditEvent).where(
        m.AuditEvent.action == "bid_notice.confirmed",
        m.AuditEvent.resource_id == intake_file.file_id,
        m.AuditEvent.detail["classification_id"].as_string() == str(classification_id),
    ).limit(1)) if classification_id else None
    if existing:
        return existing
    safe_snapshot = safe_classification_metadata(classification, confirmed_document_type)
    record(db, user, "bid_notice.confirmed", intake_file.file_id, {
        "file_version_id": intake_file.file_id,
        "classification_id": classification_id,
        "confirmed_document_type": confirmed_document_type,
        "classifier_version": classifier_version,
        # 当前系统的 DocumentIntake 是现有入库事实；独立 inbound_record
        # 表留待数据库协议批次，不在这里伪造第二条业务记录。
        "inbound_record_id": intake.id,
        "document_intake_id": intake.id,
        "evidence_snapshot": safe_snapshot,
        "operation_id": operation_id,
        "source_kind": "UPLOAD",
    })
    db.flush()
    # record() 返回 Outbox，但消费者必须引用同一事务中的 AuditEvent ID；
    # 两者是不同的持久化对象，不能把投递 ID 当作业务事件 ID。
    return db.scalar(select(m.AuditEvent).where(
        m.AuditEvent.action == "bid_notice.confirmed",
        m.AuditEvent.resource_id == intake_file.file_id,
        m.AuditEvent.detail["operation_id"].as_string() == operation_id,
    ).order_by(m.AuditEvent.created_at.desc(), m.AuditEvent.id.desc()).limit(1))
