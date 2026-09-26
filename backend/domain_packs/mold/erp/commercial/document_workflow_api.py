"""既有会话中的后台文档状态和本人确认入口。"""
from typing import Literal

from fastapi import APIRouter, Depends, Header
from pydantic import Field
from sqlalchemy import select

from agent_core.host_ports import host_ports
from domain_packs.mold import models as m
from domain_packs.mold.erp.commercial import contract_intake, document_workflow
from domain_packs.mold.ports.errors import DomainError
from domain_packs.mold.ports.events import record
from domain_packs.mold.ports.schemas import StrictModel
from domain_packs.mold.tools.erp.commercial.document_intake_tools import (
    PrepareDocumentTypeConfirmationInput, PrepareDocumentOcrRetryInput,
    PrepareSalesContractIntakeReviewInput,
)

ports = host_ports()
router = APIRouter(prefix='/api', tags=['conversation-documents'])


class DocumentClassificationConfirmInput(StrictModel):
    classification_id: str = Field(min_length=1, max_length=36)
    row_version: int = Field(ge=1)
    document_type: Literal[
        'BID_NOTICE', 'CUSTOMER_START_NOTICE', 'SALES_CONTRACT', 'MOLD_DRAWING', 'ENGINEERING_CONTACT', 'OTHER'
    ]
    reason: str = Field(min_length=1, max_length=2000)


@router.get('/files/{file_version_id}/document-classification')
def document_classification(file_version_id: str,
                            user=Depends(ports.current_user), db=Depends(ports.get_db)):
    """返回预分类完成事件中的证据快照；人工确认仍走批次确认入口。"""
    blob = db.get(m.FileObject, file_version_id)
    if not blob:
        raise DomainError('NOT_FOUND', '文件不存在或无权访问', 404)
    ports.uploaded_file(db, user, file_version_id)
    intake_file = db.scalar(select(m.DocumentIntakeFile).where(
        m.DocumentIntakeFile.file_id == file_version_id,
    ))
    if not intake_file:
        raise DomainError('CLASSIFICATION_NOT_FOUND', '文件尚未进入 PDF 预分类流程', 404)
    job = contract_intake._job(db, intake_file.id, 'PRECLASSIFY')
    if not job:
        raise DomainError('CLASSIFICATION_NOT_FOUND', '文件尚未登记预分类任务', 404)
    completion = db.scalar(select(m.AuditEvent).where(
        m.AuditEvent.action == 'document.ocr.completed',
        m.AuditEvent.resource_id == job.id,
    ).order_by(m.AuditEvent.created_at.desc(), m.AuditEvent.id.desc()).limit(1))
    classification = completion.detail.get('classification') if completion and isinstance(completion.detail, dict) else None
    if classification is None:
        return {
            'file_version_id': file_version_id,
            'classification_id': None,
            'status': 'NEEDS_REVIEW' if job.status == 'FAILED' else 'PENDING',
            'job_id': job.id,
            'job_status': job.status,
            'error_code': job.last_error if job.status == 'FAILED' else None,
        }
    return {
        'file_version_id': file_version_id,
        'classification_id': completion.id,
        'status': 'CLASSIFIED',
        'job_id': job.id,
        'job_status': job.status,
        'classification': classification,
        'confirmation_required': classification.get('needs_human_confirmation', True),
    }


@router.post('/files/{file_version_id}/document-classification/confirm')
def confirm_document_classification(
    file_version_id: str,
    data: DocumentClassificationConfirmInput,
    idempotency_key: str = Header(..., alias='Idempotency-Key', min_length=1, max_length=120),
    user=Depends(ports.current_user), db=Depends(ports.get_db),
):
    """人工确认单个文件分类，并在确认中标时只发布门禁事件。"""
    from domain_packs.mold.erp.commercial.document_workflow import record_confirmed_bid_event

    ports.uploaded_file(db, user, file_version_id)
    intake_file = db.scalar(select(m.DocumentIntakeFile).where(
        m.DocumentIntakeFile.file_id == file_version_id,
    ))
    if not intake_file:
        raise DomainError('CLASSIFICATION_NOT_FOUND', '文件尚未进入 PDF 预分类流程', 404)
    intake = contract_intake.load(db, user, intake_file.intake_id, lock=True)
    existing = db.scalar(select(m.AuditEvent).where(
        m.AuditEvent.action == 'document.classification.confirmed',
        m.AuditEvent.resource_id == file_version_id,
        m.AuditEvent.detail['operation_id'].as_string() == idempotency_key,
    ).order_by(m.AuditEvent.created_at.desc(), m.AuditEvent.id.desc()).limit(1))
    if existing:
        bid_event = db.scalar(select(m.AuditEvent).where(
            m.AuditEvent.action == 'bid_notice.confirmed',
            m.AuditEvent.resource_id == file_version_id,
            m.AuditEvent.detail['operation_id'].as_string() == idempotency_key,
        ).order_by(m.AuditEvent.created_at.desc(), m.AuditEvent.id.desc()).limit(1))
        draft = db.scalar(select(m.AdminStartNoticeDraft).where(
            m.AdminStartNoticeDraft.source_event_id == bid_event.id,
        )) if bid_event else None
        contact_run = db.scalar(select(m.Run).where(
            m.Run.conversation_id == intake.conversation_id,
            m.Run.checkpoint['engineering_contact_operation_id'].as_string() == idempotency_key,
        ).order_by(m.Run.created_at.desc(), m.Run.id.desc()).limit(1))
        return {**(existing.detail or {}), 'status': 'CONFIRMED', 'event_id': existing.id,
                'bid_notice_event_id': bid_event.id if bid_event else None,
                'admin_start_draft_id': draft.id if draft else None,
                'run_id': contact_run.id if contact_run else None}
    if intake.row_version != data.row_version:
        raise DomainError('STALE_VERSION', '文档接收记录已变化，请重新读取分类结果', 409)

    job = contract_intake._job(db, intake_file.id, 'PRECLASSIFY')
    completion = db.scalar(select(m.AuditEvent).where(
        m.AuditEvent.action == 'document.ocr.completed',
        m.AuditEvent.resource_id == job.id if job else False,
    ).order_by(m.AuditEvent.created_at.desc(), m.AuditEvent.id.desc()).limit(1)) if job else None
    if not completion or str(data.classification_id) != completion.id:
        raise DomainError('CLASSIFICATION_VERSION_CONFLICT', '分类结果已变化，请重新识别后确认', 409)
    snapshot = completion.detail.get('classification') if isinstance(completion.detail, dict) else None
    confirmed_snapshot = {
        **(snapshot or {}),
        'document_type': data.document_type,
        'decision': 'CONFIRMED',
        'needs_human_confirmation': False,
        'confirmation_reason': data.reason,
    }
    if data.document_type == 'ENGINEERING_CONTACT':
        if len(contract_intake._intake_files(db, intake.id)) != 1:
            raise DomainError('ENGINEERING_CONTACT_FILE_REQUIRED', '工程联络单类型确认需要单文件接收批次', 409)
        confirmed_intake, contact_file = contract_intake.confirm_engineering_contact(
            db, user, intake.id, expected_version=data.row_version,
            confirmations=[{'intake_file_id': intake_file.id, 'document_type': 'ENGINEERING_CONTACT'}],
            operation_id=idempotency_key,
        )
        run = document_workflow._trigger_engineering_contact_skill(
            db, user, confirmed_intake, contact_file, idempotency_key,
        )
        db.commit()
        return {
            'status': 'CONFIRMED',
            'event_id': None,
            'file_version_id': file_version_id,
            'classification_id': completion.id,
            'document_type': data.document_type,
            'operation_id': idempotency_key,
            'row_version': confirmed_intake.row_version,
            'run_id': run.id,
        }
    from domain_packs.mold.erp.commercial.bid_start_workflow import safe_classification_metadata
    safe_confirmation_snapshot = {
        **safe_classification_metadata(confirmed_snapshot, data.document_type),
        'confirmation_reason': data.reason,
    }
    intake_file.confirmed_type = data.document_type
    intake_file.confirmed_by = user.id
    from domain_packs.mold.ports.db import now
    intake_file.confirmed_at = now()
    intake.row_version += 1
    record(db, user, 'document.classification.confirmed', file_version_id, {
        'file_version_id': file_version_id,
        'classification_id': completion.id,
        'document_intake_id': intake.id,
        'document_type': data.document_type,
        'actor': user.id,
        'principal': user.id,
        'reason': data.reason,
        'operation_id': idempotency_key,
        'evidence_snapshot': safe_confirmation_snapshot,
    })
    db.flush()
    # 业务事件 ID 必须引用 AuditEvent；record() 的返回值是 Outbox 投递记录。
    confirmation = db.scalar(select(m.AuditEvent).where(
        m.AuditEvent.action == 'document.classification.confirmed',
        m.AuditEvent.resource_id == file_version_id,
        m.AuditEvent.detail['operation_id'].as_string() == idempotency_key,
    ).order_by(m.AuditEvent.created_at.desc(), m.AuditEvent.id.desc()).limit(1))
    bid_event = None
    if data.document_type == 'BID_NOTICE':
        bid_event = record_confirmed_bid_event(
            db, user, intake, intake_file, completion.id, confirmed_snapshot,
            data.document_type, idempotency_key,
        )
        from domain_packs.mold.skills.erp.commercial.bid_to_start_notice.orchestrator import (
            trigger_bid_to_start_notice,
        )
        admin_draft = trigger_bid_to_start_notice(db, user, bid_event.id)
    db.commit()
    return {
        'status': 'CONFIRMED',
        'event_id': confirmation.id,
        'bid_notice_event_id': bid_event.id if bid_event else None,
        'admin_start_draft_id': admin_draft.id if bid_event else None,
        'file_version_id': file_version_id,
        'classification_id': completion.id,
        'document_type': data.document_type,
        'operation_id': idempotency_key,
        'row_version': intake.row_version,
    }


@router.get('/conversations/{cid}/documents')
def documents(cid: str, user=Depends(ports.current_user), db=Depends(ports.get_db)):
    values = []
    for intake in contract_intake.list_for_conversation(db, user, cid):
        try:
            for row in contract_intake._intake_files(db,intake.id):
                ports.uploaded_file(db,user,row.file_id)
        except DomainError:
            continue
        value = contract_intake.serialize(
            db, intake, include_admin_start_drafts=bool(user.super_admin), user=user,
        )
        value['groups'] = [contract_intake.serialize_group(db,group)
            for group in db.scalars(select(m.ContractIntakeGroup).where(m.ContractIntakeGroup.intake_id==intake.id))
            if group.status in {'AWAITING_FIELD_CONFIRMATION','READY_FOR_DRAFT','CONTRACT_DRAFT_CREATED'}]
        values.append(value)
    return values


def _prepare(db, user, iid, operation, data):
    if str(data.document_intake_id) != iid:
        raise DomainError('FILE_CONTEXT_INVALID', '确认内容与接收批次不符', 400)
    value = document_workflow.prepare(db,user,operation,data.model_dump(mode='json'))
    db.commit()
    return value


@router.post('/document-intakes/{iid}/type-intent')
def type_intent(iid:str, data:PrepareDocumentTypeConfirmationInput,
                user=Depends(ports.current_user), db=Depends(ports.get_db)):
    return _prepare(db,user,iid,'types',data)


@router.post('/document-intakes/{iid}/retry-intent')
def retry_intent(iid:str, data:PrepareDocumentOcrRetryInput,
                 user=Depends(ports.current_user), db=Depends(ports.get_db)):
    return _prepare(db,user,iid,'retry',data)


@router.get('/contract-intakes/{group_id}/candidates')
def contract_candidates(group_id: str, user=Depends(ports.current_user), db=Depends(ports.get_db)):
    """返回当前用户可见的项目候选及项目内正式模具，只读。"""
    return contract_intake.project_candidates(db, user, group_id)


@router.post('/contract-intake-groups/{group_id}/review-intent')
def review_intent(group_id: str, data:PrepareSalesContractIntakeReviewInput,
                  user=Depends(ports.current_user), db=Depends(ports.get_db)):
    if str(data.contract_intake_group_id) != group_id:
        raise DomainError('FILE_CONTEXT_INVALID', '合同复核分组与请求内容不符', 400)
    result = document_workflow.prepare(db, user, 'review', data.model_dump(mode='json'))
    db.commit()
    return result
