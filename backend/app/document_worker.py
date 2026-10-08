"""Lease-based worker for PDF classification and sales-contract OCR jobs."""
from datetime import timedelta
from decimal import Decimal
from hashlib import sha256
import argparse
import json
import logging
import struct
import time
from uuid import uuid4

import pymupdf
from sqlalchemy import or_, select, delete
from sqlalchemy.dialects.postgresql import insert as pg_insert

from agent_core.host_ports import host_ports
from domain_packs.mold.erp.commercial.ocr_provider import (
    DocumentTextProvider,
    RecognizedDocument,
    RecognizedPage,
)
from domain_packs.mold.erp.commercial.paddleocr_client import PaddleOCRClient
from app.document_preview import docx_to_pdf
from domain_packs.mold.erp.commercial.pdf_analysis import (
    PageTextBlock,
    inspect_document,
    merge_page_blocks,
)
from domain_packs.mold.ports.errors import DomainError
from . import models as m
from .config import settings, document_model_settings
from .db import SessionLocal, now


log = logging.getLogger(__name__)


def claim(factory):
    current = now()
    with factory.begin() as db:
        job = db.scalar(
            select(m.DocumentOcrJob)
            .where(or_(
                m.DocumentOcrJob.status == "QUEUED",
                (m.DocumentOcrJob.status == "RETRY_WAIT") & (m.DocumentOcrJob.retry_at <= current),
                (m.DocumentOcrJob.status == "PROCESSING") & (m.DocumentOcrJob.lease_until <= current),
            ))
            .order_by(m.DocumentOcrJob.created_at, m.DocumentOcrJob.id)
            .with_for_update(skip_locked=True)
            .limit(1)
        )
        if not job:
            return None
        lease_id = str(uuid4())
        job.status = "PROCESSING"
        job.lease_id = lease_id
        job.lease_until = current + timedelta(minutes=5)
        job.started_at = current
        job.retry_at = None
        intake_file = db.get(m.DocumentIntakeFile,job.intake_file_id)
        if job.phase == 'FULL_CONTRACT' and intake_file and intake_file.contract_group_id:
            _refresh_full_ocr_status(db,intake_file.contract_group_id,intake_file.intake_id)
        return job.id, lease_id


def _load_work(factory, job_id, lease_id):
    with factory() as db:
        job = db.get(m.DocumentOcrJob, job_id)
        if not job or job.status != "PROCESSING" or job.lease_id != lease_id:
            return None
        intake_file = db.get(m.DocumentIntakeFile, job.intake_file_id)
        blob = db.get(m.FileObject, intake_file.file_id) if intake_file else None
        if not intake_file or not blob:
            raise DomainError("OCR_SOURCE_MISSING", "OCR 原始文件记录不存在")
        db.expunge(blob)
        return job.phase, intake_file.id, intake_file.intake_id, intake_file.contract_group_id, blob.media_type, blob


def _all_preclassifications_done(db, intake_id):
    statuses = list(db.scalars(
        select(m.DocumentOcrJob.status)
        .join(m.DocumentIntakeFile, m.DocumentIntakeFile.id == m.DocumentOcrJob.intake_file_id)
        .where(
            m.DocumentIntakeFile.intake_id == intake_id,
            m.DocumentOcrJob.phase == "PRECLASSIFY",
        )
    ))
    return bool(statuses) and all(status == "SUCCEEDED" for status in statuses)


def _refresh_full_ocr_status(db, group_id, intake_id):
    statuses = list(db.scalars(
        select(m.DocumentOcrJob.status)
        .join(m.DocumentIntakeFile, m.DocumentIntakeFile.id == m.DocumentOcrJob.intake_file_id)
        .where(
            m.DocumentIntakeFile.contract_group_id == group_id,
            m.DocumentOcrJob.phase == "FULL_CONTRACT",
        )
    ))
    group = db.get(m.ContractIntakeGroup, group_id)
    if not group or not statuses:
        return
    next_status = ('OCR_FAILED' if 'FAILED' in statuses else
        'AWAITING_FIELD_CONFIRMATION' if all(status == 'SUCCEEDED' for status in statuses)
        else 'FULL_OCR_PROCESSING')
    if group.status != next_status:
        group.status = next_status
        group.row_version += 1
    group_statuses = list(db.scalars(
        select(m.ContractIntakeGroup.status).where(m.ContractIntakeGroup.intake_id == intake_id)
    ))
    intake_status = ('OCR_FAILED' if 'OCR_FAILED' in group_statuses else
        'AWAITING_FIELD_CONFIRMATION' if group_statuses and all(s == 'AWAITING_FIELD_CONFIRMATION' for s in group_statuses)
        else 'FULL_OCR_PROCESSING')
    intake = db.get(m.DocumentIntake,intake_id)
    if intake and intake.status != intake_status:
        intake.status = intake_status
        intake.row_version += 1


def _finish_attachment_runs(db, intake, *, success, group_id=None, error_code=None):
    """Close the host-owned attachment Run only after document processing ends.

    Upload-triggered Runs are deliberately not sent through the general model
    worker.  The document worker owns their visible tool trace and final
    confirmation prompt, so the conversation never claims OCR is complete
    before the persisted intake reaches a terminal state.
    """
    if not intake:
        return
    file_ids = list(db.scalars(select(m.DocumentIntakeFile.file_id).where(
        m.DocumentIntakeFile.intake_id == intake.id,
    )))
    if not file_ids:
        return
    runs = db.scalars(select(m.Run).join(
        m.RunFile, m.RunFile.run_id == m.Run.id,
    ).where(
        m.Run.conversation_id == intake.conversation_id,
        m.Run.status == "WAITING_DOCUMENT",
        m.RunFile.file_id.in_(file_ids),
    ).distinct())
    for run in runs:
        checkpoint = run.checkpoint if isinstance(run.checkpoint, dict) else {}
        if checkpoint.get("run_trigger") != "ATTACHMENT_UPLOAD":
            continue
        if success:
            run.result = {
                "trace": [{
                    "type": "tool",
                    "tool": "document_ocr",
                    "data": [{
                        "status": "AWAITING_FIELD_CONFIRMATION",
                        "intake_id": intake.id,
                        "contract_group_id": group_id,
                        "fields_ready": True,
                    }],
                    "as_of": now().isoformat(),
                }],
                "summary": "合同 OCR 解析已完成，候选字段已提取。请打开右侧“文件识别”工作区，点击“二次确认合同字段”复核；确认后才会进入后续审批准备。未确认前仍可继续追问合同内容。",
                "suggestions": ["打开右侧“文件识别”工作区进行二次确认", "如暂不确认，可直接追问合同金额、编号或项目内容"],
            }
            run.status = "SUCCEEDED"
        else:
            run.result = {
                "trace": [{
                    "type": "tool_error",
                    "tool": "document_ocr",
                    "code": error_code or "DOCUMENT_OCR_FAILED",
                    "message": "合同 OCR 解析未完成，请在右侧文件识别工作区查看失败原因并重试。",
                }],
                "message": "合同解析未完成，尚未生成二次确认卡片。请先在右侧文件识别工作区重试解析。",
                "error_code": error_code or "DOCUMENT_OCR_FAILED",
            }
            run.status = "FAILED"
        run.lease_until = None
        run.checkpoint = {**checkpoint, "completed_at": now().isoformat()}
        from .run_events import publish_run_update
        publish_run_update(run.conversation_id, run.id, run.status)


def _auto_route_sales_contract(db, intake, intake_file, confidence):
    """Start full contract OCR after a high-confidence upload classification.

    Uploads are the user's explicit request to analyse the attachment.  A
    high-confidence SALES_CONTRACT classification therefore advances directly
    to field extraction; the human confirmation card is the field/project /
    relationship review after OCR, not a second, redundant type gate.  Other
    document types keep the existing type-confirmation gate.
    """
    if confidence is None or Decimal(str(confidence)) < Decimal('0.90'):
        return False
    existing = db.scalar(select(m.DocumentIntakeFile).where(
        m.DocumentIntakeFile.id == intake_file.id,
        m.DocumentIntakeFile.contract_group_id.is_not(None),
    ))
    if existing:
        return False
    group = m.ContractIntakeGroup(
        intake_id=intake.id,
        group_key='合同1',
        status='FULL_OCR_QUEUED',
    )
    db.add(group)
    db.flush()
    intake_file.confirmed_type = 'SALES_CONTRACT'
    intake_file.confirmed_role = 'MAIN'
    intake_file.contract_group_id = group.id
    db.add(m.DocumentOcrJob(
        intake_file_id=intake_file.id,
        phase='FULL_CONTRACT',
        status='QUEUED',
    ))
    intake.status = 'FULL_OCR_QUEUED'
    intake.row_version += 1
    host_ports().record(db, None, 'document.type.auto_routed', intake.id, {
        'document_intake_id': intake.id,
        'intake_file_id': intake_file.id,
        'document_type': 'SALES_CONTRACT',
        'confidence': str(confidence),
        'reason': 'UPLOAD_AUTO_ANALYSIS',
    })
    return True


def _succeed(factory, job_id, lease_id, phase, intake_file_id, intake_id, group_id, provider, result, conversion=None):
    with factory.begin() as db:
        job = db.scalar(select(m.DocumentOcrJob).where(
            m.DocumentOcrJob.id == job_id,
            m.DocumentOcrJob.status == "PROCESSING",
            m.DocumentOcrJob.lease_id == lease_id,
        ).with_for_update())
        if not job:
            return
        intake_file = db.get(m.DocumentIntakeFile, intake_file_id)
        if not intake_file:
            raise DomainError("OCR_SOURCE_MISSING", "OCR 接收文件不存在")
        if phase == "PRECLASSIFY":
            if result.document_type in {"BID_NOTICE", "CUSTOMER_START_NOTICE", "SALES_CONTRACT", "MOLD_DRAWING", "OTHER"}:
                intake_file.suggested_type = result.document_type
            else:
                # 工程联络类型不写入旧列的数据库约束，完整快照保存在审计事件。
                intake_file.suggested_type = None
            intake_file.suggested_confidence = result.confidence
        else:
            confirmed = db.scalar(select(m.DocumentExtractedField.id).where(
                m.DocumentExtractedField.job_id==job.id,m.DocumentExtractedField.confirmed_by.is_not(None)).limit(1))
            if confirmed:
                raise DomainError('OCR_REVIEW_ALREADY_STARTED','已有人工复核结果，不能覆盖',409)
            db.execute(delete(m.DocumentExtractedField).where(m.DocumentExtractedField.job_id==job.id))
            for field in result.fields:
                row_key = field.row_key
                if field.scope != "HEADER":
                    row_key = f"{intake_file_id}:{field.row_key}"
                    if len(row_key) > 80:
                        row_key = f"{intake_file_id}:{sha256(field.row_key.encode('utf-8')).hexdigest()[:32]}"
                db.add(m.DocumentExtractedField(
                    job_id=job.id,
                    scope=field.scope,
                    row_key=row_key,
                    field_key=field.field_key,
                    raw_value=field.raw_value,
                    normalized_value=field.normalized_value,
                    source_block_ids=list(field.source_block_ids),
                    confidence=field.confidence,
                    page_number=field.page_number,
                    bbox=field.bbox,
                ))
        job.status = "SUCCEEDED"
        job.attempts += 1
        job.provider = provider.name
        job.finished_at = now()
        job.lease_id = None
        job.lease_until = None
        job.last_error = None
        if phase == "PRECLASSIFY" and _all_preclassifications_done(db, intake_id):
            intake = db.get(m.DocumentIntake, intake_id)
            if intake:
                if result.document_type == 'SALES_CONTRACT' and _auto_route_sales_contract(
                    db, intake, intake_file, result.confidence,
                ):
                    # The newly queued FULL_CONTRACT job is claimed by the
                    # same worker loop on its next pass.  The UI will move
                    # from "正在解析" to the field confirmation card.
                    pass
                else:
                    intake.status = "AWAITING_TYPE_CONFIRMATION"
                    intake.row_version += 1
        elif phase == "FULL_CONTRACT" and group_id:
            _refresh_full_ocr_status(db, group_id, intake_id)
        intake = db.get(m.DocumentIntake,intake_id)
        completion_detail = {
            "phase": phase,
            "intake_id": intake_id,
            "intake_file_id": intake_file_id,
            "contract_group_id": group_id,
        }
        if conversion:
            completion_detail["conversion"] = conversion
        if phase == "PRECLASSIFY":
            # 分类证据作为不可变完成事件快照保留；业务状态仍以人工确认入口为准。
            completion_detail["classification"] = {
                "document_type": result.document_type,
                "event_type": result.event_type,
                "decision": result.decision,
                "confidence": str(result.confidence),
                "evidence": [dict(item) for item in result.evidence[:50]],
                "conflicts": [dict(item) for item in result.conflicts[:20]],
                "extracted": dict(result.extracted or {}),
                "bid_fields": [dict(field) for field in result.bid_fields[:200]],
                "classifier_version": result.classifier_version,
                "needs_human_confirmation": result.needs_human_confirmation,
            }
        host_ports().record(db, None, "document.ocr.completed", job.id, completion_detail,
                            recipients=[intake.created_by] if intake else [])
        if phase == "FULL_CONTRACT" and group_id:
            ready = db.get(m.ContractIntakeGroup, group_id)
            if ready and ready.status == "AWAITING_FIELD_CONFIRMATION":
                host_ports().record(db, None, "contract.ocr.ready", group_id, {
                    "contract_intake_group_id": group_id,
                    "contract_group_version": ready.row_version,
                    "source_job_id": job.id,
                })
                db.flush()
                ready_event = db.scalar(select(m.AuditEvent).where(
                    m.AuditEvent.action == "contract.ocr.ready",
                    m.AuditEvent.resource_id == group_id,
                ).order_by(m.AuditEvent.created_at.desc(), m.AuditEvent.id.desc()).limit(1))
                from domain_packs.mold.tools.erp.commercial.document_event_tools import execute_event_tool
                owner = db.get(m.User, intake.created_by) if intake else None
                if owner and ready_event:
                    execute_event_tool(
                        db, owner, 'propose_start_contract_matches',
                        event_kind='contract.ocr.ready', resource_id=ready_event.id,
                    )
        intake = db.get(m.DocumentIntake, intake_id)
        if intake and (
            (phase == "FULL_CONTRACT" and intake.status == "AWAITING_FIELD_CONFIRMATION")
            or (phase == "PRECLASSIFY" and intake.status == "AWAITING_TYPE_CONFIRMATION")
        ):
            _finish_attachment_runs(db, intake, success=True, group_id=group_id)


def _error_code(error):
    if isinstance(error, DomainError):
        return error.code[:120]
    text = str(error)
    if text and text.isupper() and len(text) <= 120:
        return text
    return type(error).__name__[:120]


def _fail(factory, job_id, lease_id, error):
    with factory.begin() as db:
        job = db.scalar(select(m.DocumentOcrJob).where(
            m.DocumentOcrJob.id == job_id,
            m.DocumentOcrJob.status == "PROCESSING",
            m.DocumentOcrJob.lease_id == lease_id,
        ).with_for_update())
        if not job:
            return
        job.attempts += 1
        job.last_error = _error_code(error)
        log.warning('Document job failed: job=%s phase=%s attempt=%s code=%s',
                    job.id, job.phase, job.attempts, job.last_error)
        job.lease_id = None
        job.lease_until = None
        maximum = settings().ocr_max_attempts
        terminal_budget_error = job.last_error in {'DOCUMENT_MODEL_BATCH_TOO_LARGE','DOCUMENT_MODEL_CALL_LIMIT'}
        if job.attempts >= maximum or terminal_budget_error:
            job.status = "FAILED"
            job.finished_at = now()
            intake_file = db.get(m.DocumentIntakeFile, job.intake_file_id)
            if intake_file:
                intake = db.get(m.DocumentIntake, intake_file.intake_id)
                if intake:
                    intake.status = "OCR_FAILED"
                    intake.row_version += 1
                if intake_file.contract_group_id:
                    group = db.get(m.ContractIntakeGroup, intake_file.contract_group_id)
                    if group:
                        group.status = "OCR_FAILED"
                        group.row_version += 1
            intake = db.get(m.DocumentIntake,intake_file.intake_id) if intake_file else None
            host_ports().record(db, None, "document.ocr.failed", job.id, {
                "phase": job.phase,
                "intake_file_id": job.intake_file_id,
                "error_code": job.last_error,
            }, recipients=[intake.created_by] if intake else [])
            _finish_attachment_runs(db, intake, success=False, error_code=job.last_error)
        else:
            job.status = "RETRY_WAIT"
            job.retry_at = now() + timedelta(seconds=min(300, 2 ** job.attempts))


def _renew_lease(factory, job_id, lease_id):
    with factory.begin() as db:
        job = db.scalar(select(m.DocumentOcrJob).where(
            m.DocumentOcrJob.id == job_id,
            m.DocumentOcrJob.status == "PROCESSING",
            m.DocumentOcrJob.lease_id == lease_id,
        ).with_for_update())
        if not job:
            return False
        job.lease_until = now() + timedelta(minutes=5)
        return True


def _pipeline_version(config):
    return (
        f"pymupdf-{pymupdf.VersionBind}|paddleocr-3.7.0|PP-OCRv5|"
        f"dpi-{config.ocr_render_dpi}|text-{config.ocr_min_text_chars}|"
        f"image-{config.ocr_image_coverage_threshold:g}"
    )


def _block_payload(block):
    return {
        "block_id": block.block_id,
        "text": block.text,
        "bbox": [float(value) for value in block.bbox],
        "source": block.source,
        "confidence": None if block.confidence is None else float(block.confidence),
    }


def _source_sha256(page):
    payload = {
        "page_number": page.page_number,
        "width": page.width,
        "height": page.height,
        "image_sha256": sha256(page.image_png).hexdigest() if page.image_png else None,
        "blocks": [_block_payload(block) for block in page.blocks],
    }
    return sha256(json.dumps(
        payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")).hexdigest()


def _cached_page(factory, intake_file_id, page_number, source_hash, pipeline_version):
    with factory() as db:
        row = db.scalar(select(m.DocumentRecognizedPage).where(
            m.DocumentRecognizedPage.intake_file_id == intake_file_id,
            m.DocumentRecognizedPage.page_number == page_number,
            m.DocumentRecognizedPage.source_sha256 == source_hash,
            m.DocumentRecognizedPage.pipeline_version == pipeline_version,
        ))
        return _recognized_page(row) if row else None


def _recognized_page(row):
    blocks = []
    for value in row.blocks:
        if not isinstance(value, dict):
            raise DomainError("OCR_PAGE_CACHE_INVALID", "OCR 页面缓存格式无效")
        bbox = value.get("bbox")
        if not isinstance(bbox, list) or len(bbox) != 4:
            raise DomainError("OCR_PAGE_CACHE_INVALID", "OCR 页面缓存坐标无效")
        source = value.get("source")
        if source not in {"TEXT_LAYER", "PADDLE_OCR"}:
            raise DomainError("OCR_PAGE_CACHE_INVALID", "OCR 页面缓存来源无效")
        blocks.append(PageTextBlock(
            block_id=str(value.get("block_id") or ""),
            text=str(value.get("text") or ""),
            bbox=tuple(float(item) for item in bbox),
            source=source,
            confidence=(
                None if value.get("confidence") is None
                else float(value["confidence"])
            ),
        ))
    return RecognizedPage(page_number=row.page_number, blocks=tuple(blocks))


def _ocr_blocks(page, result):
    if page.image_png is None or len(page.image_png) < 24:
        raise DomainError("OCR_PAGE_IMAGE_INVALID", "OCR 页面图像无效")
    pixel_width, pixel_height = struct.unpack(">II", page.image_png[16:24])
    if pixel_width < 1 or pixel_height < 1:
        raise DomainError("OCR_PAGE_IMAGE_INVALID", "OCR 页面图像尺寸无效")
    x_scale = page.width / pixel_width
    y_scale = page.height / pixel_height
    blocks = []
    for block in result.blocks:
        xs = [point[0] for point in block.bbox]
        ys = [point[1] for point in block.bbox]
        blocks.append(PageTextBlock(
            block_id=block.block_id,
            text=block.text,
            bbox=(
                min(xs) * x_scale,
                min(ys) * y_scale,
                max(xs) * x_scale,
                max(ys) * y_scale,
            ),
            source="PADDLE_OCR",
            confidence=block.confidence,
        ))
    return tuple(blocks)


def _recognize_page(page, ocr_client):
    recognized = ()
    if page.needs_ocr:
        if ocr_client is None:
            raise DomainError("OCR_SERVICE_CONFIGURATION_MISSING", "OCR 服务未配置", 503)
        result = ocr_client.recognize_page(
            page.image_png,
            page_number=page.page_number,
            request_id=uuid4(),
        )
        recognized = _ocr_blocks(page, result)
    blocks = merge_page_blocks(page.blocks, recognized)
    if recognized and page.blocks:
        source_kind = "HYBRID"
    elif recognized:
        source_kind = "PADDLE_OCR"
    else:
        source_kind = "TEXT_LAYER"
    confidences = [block.confidence for block in recognized if block.confidence is not None]
    average = Decimal(str(sum(confidences) / len(confidences))) if confidences else None
    text = "\n".join(block.text for block in blocks)
    return {
        "page_number": page.page_number,
        "source_kind": source_kind,
        "text": text,
        "blocks": [_block_payload(block) for block in blocks],
        "average_confidence": average,
        "text_sha256": sha256(text.encode("utf-8")).hexdigest(),
    }


def _store_page(factory, intake_file_id, source_hash, pipeline_version, values):
    record = {
        "intake_file_id": intake_file_id,
        "source_sha256": source_hash,
        "pipeline_version": pipeline_version,
        **values,
    }
    with factory.begin() as db:
        db.execute(
            pg_insert(m.DocumentRecognizedPage)
            .values(**record)
            .on_conflict_do_nothing(constraint="uq_document_recognized_page_version")
        )
        row = db.scalar(select(m.DocumentRecognizedPage).where(
            m.DocumentRecognizedPage.intake_file_id == intake_file_id,
            m.DocumentRecognizedPage.page_number == values["page_number"],
            m.DocumentRecognizedPage.source_sha256 == source_hash,
            m.DocumentRecognizedPage.pipeline_version == pipeline_version,
        ))
        if not row:
            raise DomainError("OCR_PAGE_CACHE_WRITE_FAILED", "OCR 页面缓存写入失败", 503)
        return _recognized_page(row)


def _build_recognized_document(
    factory,
    job_id,
    lease_id,
    phase,
    intake_file_id,
    document,
    ocr_client,
    config,
):
    pages = document.pages[:3] if phase == "PRECLASSIFY" else document.pages
    pipeline_version = _pipeline_version(config)
    recognized_pages = []
    for page in pages:
        source_hash = _source_sha256(page)
        cached = _cached_page(
            factory, intake_file_id, page.page_number, source_hash, pipeline_version
        )
        if cached is not None:
            recognized_pages.append(cached)
            continue
        if not _renew_lease(factory, job_id, lease_id):
            return None
        values = _recognize_page(page, ocr_client)
        if not _renew_lease(factory, job_id, lease_id):
            return None
        recognized_pages.append(_store_page(
            factory, intake_file_id, source_hash, pipeline_version, values
        ))
    return RecognizedDocument(pages=tuple(recognized_pages))


def _prepare_document(data, media_type, config):
    conversion = None
    if media_type == 'application/vnd.openxmlformats-officedocument.wordprocessingml.document':
        source_digest = sha256(data).hexdigest()
        try:
            processed = docx_to_pdf(data, source_digest)
        except DomainError as error:
            raise DomainError('WORD_TO_PDF_FAILED', 'Word 文档无法转换为 PDF，未开始识别', 503) from error
        conversion = {
            'source_media_type': media_type,
            'source_sha256': source_digest,
            'processed_media_type': 'application/pdf',
            'processed_sha256': sha256(processed).hexdigest(),
            'converter': 'local-office',
        }
        data, media_type = processed, 'application/pdf'
    return inspect_document(
        data, media_type,
        max_pages=config.ocr_max_pages,
        render_dpi=config.ocr_render_dpi,
        min_text_chars=config.ocr_min_text_chars,
        image_coverage_threshold=config.ocr_image_coverage_threshold,
    ), conversion


def process_claim(factory, job_id, lease_id, provider, ocr_client=None):
    recognized = None
    phase = intake_file_id = intake_id = group_id = None
    try:
        work = _load_work(factory, job_id, lease_id)
        if not work:
            return
        phase, intake_file_id, intake_id, group_id, media_type, blob = work
        config = settings()
        data = host_ports().object_storage.read(blob)
        document, conversion = _prepare_document(data, media_type, config)
        recognized = _build_recognized_document(
            factory,
            job_id,
            lease_id,
            phase,
            intake_file_id,
            document,
            ocr_client,
            config,
        )
        if recognized is None or not _renew_lease(factory, job_id, lease_id):
            return
        last_renewed = [0.0]
        def on_progress():
            current = time.monotonic()
            if current - last_renewed[0] >= 10:
                if not _renew_lease(factory, job_id, lease_id):
                    raise DomainError('OCR_LEASE_LOST','识别任务租约已失效',409)
                last_renewed[0] = current
        from .document_batch_cache import BatchCache
        batch_cache = BatchCache(factory,job_id,lease_id)
        result = (
            provider.classify(recognized,on_progress=on_progress,on_call=batch_cache.record_call)
            if phase == "PRECLASSIFY"
            else provider.extract_sales_contract(recognized,on_progress=on_progress,
                load_batch=batch_cache.load,save_batch=batch_cache.save,
                load_split=batch_cache.load_split,save_split=batch_cache.save_split,
                on_call=batch_cache.record_call)
        )
        _succeed(
            factory,
            job_id,
            lease_id,
            phase,
            intake_file_id,
            intake_id,
            group_id,
            provider,
            result,
            conversion=conversion,
        )
    except DomainError as error:
        # A malformed source-id response from an OpenAI-compatible document
        # endpoint should not strand an upload forever.  Recover only the
        # conservative, directly source-bound header fields and still leave
        # them explicitly unconfirmed in the review card.
        if (phase == 'FULL_CONTRACT' and recognized is not None
                and error.code in {'DOCUMENT_FIELD_SOURCE_INVALID', 'DOCUMENT_MODEL_TOTAL_TIMEOUT',
                                   'DOCUMENT_MODEL_READ_TIMEOUT', 'DOCUMENT_MODEL_NETWORK_ERROR',
                                   'DOCUMENT_MODEL_CONNECT_TIMEOUT'}
                and hasattr(provider, '_fallback_contract_fields')):
            try:
                fallback = provider._fallback_contract_fields(
                    recognized.pages, recognized.page_count,
                )
                from domain_packs.mold.erp.commercial.ocr_provider import ContractExtraction
                _succeed(
                    factory, job_id, lease_id, phase, intake_file_id, intake_id,
                    group_id, provider, ContractExtraction(tuple(fallback)),
                )
                return
            except Exception:
                pass
        _fail(factory, job_id, lease_id, error)
    except Exception as error:
        _fail(factory, job_id, lease_id, error)


def run_once(factory, provider=None, ocr_client=None):
    work = claim(factory)
    if not work:
        return False
    owned_provider = provider is None
    try:
        if owned_provider:
            try:
                provider = DocumentTextProvider(document_model_settings())
            except ValueError:
                _fail(factory,work[0],work[1],DomainError('DOCUMENT_MODEL_CONFIG_INVALID','文档模型配置不可用',503))
                return True
        process_claim(factory, work[0], work[1], provider, ocr_client)
    finally:
        if owned_provider and provider is not None:
            provider.adapter.close()
    return True


def main():
    argparse.ArgumentParser(description=__doc__).parse_args()
    config = settings()
    document_config = document_model_settings()
    if not document_config.document_model_base_url or not document_config.document_model:
        raise SystemExit("Document model configuration missing")
    ocr_client = None
    if config.ocr_service_token:
        ocr_client = PaddleOCRClient(
            config.ocr_service_url,
            config.ocr_service_token,
            connect_timeout=config.ocr_service_connect_timeout,
            read_timeout=config.ocr_service_read_timeout,
        )
    else:
        log.warning(
            "PaddleOCR is not configured; text-layer pages can still be processed, "
            "while image-only pages will fail with an explicit OCR configuration error"
        )
    while True:
        try:
            if not run_once(SessionLocal, ocr_client=ocr_client):
                time.sleep(1)
        except Exception as error:
            log.warning("Document worker retry: %s", type(error).__name__)
            time.sleep(2)


if __name__ == "__main__":
    main()
