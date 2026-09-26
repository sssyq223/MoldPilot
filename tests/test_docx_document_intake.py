from hashlib import sha256
from types import SimpleNamespace
from uuid import uuid4

import pymupdf
import pytest
from sqlalchemy import select

from contact_document_db import contact_document_db
from domain_packs.mold import models as m
from domain_packs.mold import manifest


def test_docx_upload_is_registered_for_document_intake(contact_document_db, monkeypatch):
    db = contact_document_db
    owner = m.User(username='docx_' + uuid4().hex, display_name='合成测试人员',
                   password_hash='not-a-login', super_admin=True)
    db.add(owner); db.flush()
    conversation = m.Conversation(user_id=owner.id, title='Word 工程联络单')
    db.add(conversation); db.flush()
    blob = m.FileObject(owner_id=owner.id, conversation_id=conversation.id,
        request_key=str(uuid4()), filename='工程联络单.docx',
        media_type='application/vnd.openxmlformats-officedocument.wordprocessingml.document',
        size=4, sha256=sha256(b'docx').hexdigest(), backend='local',
        storage_namespace='test', object_key=uuid4().hex)
    db.add(blob); db.flush()

    manifest.after_files_uploaded(db, owner, [blob], str(uuid4()))

    intake = db.scalar(select(m.DocumentIntake))
    job = db.scalar(select(m.DocumentOcrJob))
    assert intake is not None
    assert job is not None and job.status == 'QUEUED'


def test_word_converter_ignores_com_quit_rpc_cleanup_error():
    from app.document_preview import _WORD_EXPORT_SCRIPT
    assert 'try { $word.Quit() } catch { }' in _WORD_EXPORT_SCRIPT


def test_prepare_document_rejects_word_when_converter_fails(monkeypatch):
    from app import document_worker
    from domain_packs.mold.ports.errors import DomainError
    monkeypatch.setattr(document_worker, 'docx_to_pdf',
                        lambda data, digest: (_ for _ in ()).throw(
                            DomainError('PREVIEW_RENDER_FAILED', 'converter unavailable', 503)))
    config = SimpleNamespace(ocr_max_pages=10, ocr_render_dpi=200,
                             ocr_min_text_chars=20, ocr_image_coverage_threshold=0.5)
    with pytest.raises(DomainError, match='Word 文档无法转换') as error:
        document_worker._prepare_document(
            b'editable-docx', 'application/vnd.openxmlformats-officedocument.wordprocessingml.document', config)
    assert error.value.code == 'WORD_TO_PDF_FAILED'


def test_prepare_document_converts_editable_word_to_pdf(monkeypatch):
    from app import document_worker

    pdf = pymupdf.open()
    pdf.new_page().insert_text((50, 50), '工程变更申请联络单')
    pdf_bytes = pdf.tobytes()
    pdf.close()
    monkeypatch.setattr(document_worker, 'docx_to_pdf', lambda data, digest: pdf_bytes)
    monkeypatch.setattr(document_worker, 'inspect_document',
                        lambda data, media_type, **kwargs: SimpleNamespace(
                            pages=(), page_count=1))
    config = SimpleNamespace(ocr_max_pages=10, ocr_render_dpi=200,
                             ocr_min_text_chars=20, ocr_image_coverage_threshold=0.5)

    document, conversion = document_worker._prepare_document(
        b'editable-docx', 'application/vnd.openxmlformats-officedocument.wordprocessingml.document', config)

    assert document.page_count == 1
    assert conversion['source_media_type'].endswith('wordprocessingml.document')
    assert conversion['processed_media_type'] == 'application/pdf'
    assert conversion['source_sha256'] == sha256(b'editable-docx').hexdigest()
    assert conversion['processed_sha256'] == sha256(pdf_bytes).hexdigest()
