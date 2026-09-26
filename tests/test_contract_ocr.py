from base64 import b64decode
import importlib
import json
import struct
from decimal import Decimal
from types import SimpleNamespace

import httpx
import pytest
from sqlalchemy import select

from app import models as m
from app.config import settings
from agent_core.host_ports import host_ports
from pg_db import factory as pg_factory


def _modules():
    pdf = importlib.import_module("domain_packs.mold.erp.commercial.pdf_analysis")
    ocr = importlib.import_module("domain_packs.mold.erp.commercial.ocr_provider")
    return pdf, ocr


def _text_pdf(text="销售合同 SC-001\n" + "\n".join(f"合同条款 {index}" for index in range(10))):
    fitz = importlib.import_module("fitz")
    document = fitz.open()
    page = document.new_page()
    page.insert_text((72, 72), text, fontname="china-s")
    data = document.tobytes()
    document.close()
    return data


def test_text_pdf_prefers_embedded_text_and_preserves_page_number():
    pdf, _ = _modules()
    document = pdf.inspect_pdf(_text_pdf(), max_pages=20)
    assert document.encrypted is False
    assert document.page_count == 1
    assert "SC-001" in document.pages[0].text
    assert document.pages[0].page_number == 1
    assert document.pages[0].needs_ocr is False
    assert document.pages[0].image_png is None
    assert document.pages[0].blocks[0].source == "TEXT_LAYER"
    assert document.pages[0].blocks[0].block_id == "p1-t0001"


def test_blank_pdf_page_is_rendered_for_paddleocr():
    pdf, _ = _modules()
    fitz = importlib.import_module("fitz")
    source = fitz.open()
    source.new_page()
    data = source.tobytes()
    source.close()

    document = pdf.inspect_pdf(data, max_pages=20)
    assert document.pages[0].text == ""
    assert document.pages[0].needs_ocr is True
    assert document.pages[0].image_png.startswith(b"\x89PNG")


def _image_pdf(*, text=""):
    fitz = importlib.import_module("fitz")
    source = fitz.open()
    page = source.new_page(width=300, height=400)
    png = b64decode(
        "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk+A8AAQUBAScY42YAAAAASUVORK5CYII="
    )
    page.insert_image(page.rect, stream=png)
    if text:
        page.insert_text((20, 20), text, fontname="china-s")
    data = source.tobytes()
    source.close()
    return data


def test_image_pdf_renders_page_for_paddleocr():
    pdf, _ = _modules()
    page = pdf.inspect_pdf(_image_pdf(), max_pages=20).pages[0]
    assert page.needs_ocr is True
    assert page.image_coverage > 0.99
    assert page.blocks == ()
    assert page.image_png.startswith(b"\x89PNG")


def test_large_pdf_page_render_stays_within_ocr_service_pixel_limit():
    pdf, _ = _modules()
    page = pdf.inspect_pdf(
        _image_pdf(),
        max_pages=20,
        max_render_pixels=120_000,
    ).pages[0]
    width, height = struct.unpack(">II", page.image_png[16:24])
    assert width * height <= 120_000


def test_hybrid_pdf_keeps_text_blocks_and_renders_image_content():
    pdf, _ = _modules()
    page = pdf.inspect_pdf(
        _image_pdf(text="销售合同文本层"),
        max_pages=20,
        min_text_chars=1,
        image_coverage_threshold=0.25,
    ).pages[0]
    assert page.needs_ocr is True
    assert page.blocks[0].text == "销售合同文本层"
    assert page.blocks[0].source == "TEXT_LAYER"
    assert page.image_png.startswith(b"\x89PNG")


def test_merge_blocks_deduplicates_overlapping_text_in_favor_of_text_layer():
    pdf, _ = _modules()
    embedded = pdf.PageTextBlock("p1-t0001", "合同编号 SC-001", (10, 10, 100, 30), "TEXT_LAYER", None)
    recognized = pdf.PageTextBlock("p1-b0001", "合同编号 SC-001", (11, 10, 101, 30), "PADDLE_OCR", 0.99)
    other = pdf.PageTextBlock("p1-b0002", "客户名称", (10, 40, 100, 60), "PADDLE_OCR", 0.95)
    merged = pdf.merge_page_blocks((embedded,), (recognized, other))
    assert [block.block_id for block in merged] == ["p1-t0001", "p1-b0002"]


def _openai_response(content):
    return {
        "choices": [{
            "finish_reason": "stop",
            "message": {"role": "assistant", "content": json.dumps(content, ensure_ascii=False)},
        }],
        "usage": {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15},
    }


@pytest.fixture(autouse=True)
def model_stream_transport(monkeypatch):
    """既有候选样本保持不变，仅把模型 HTTP 替身切到真实 SSE 协议。"""
    original = httpx.MockTransport
    class StreamingTransport(original):
        def handle_request(self, request):
            response = super().handle_request(request)
            if response.status_code != 200 or not json.loads(request.content).get('stream'):
                return response
            data = response.json()
            if 'choices' not in data:
                return response
            choice = data['choices'][0]
            events = [
                {'choices':[{'delta':choice['message'],'finish_reason':None}]},
                {'choices':[{'delta':{},'finish_reason':choice.get('finish_reason','stop')}], 'usage':data.get('usage',{})},
            ]
            return httpx.Response(200,headers={'content-type':'text/event-stream'},
                content=''.join('data: '+json.dumps(e)+'\n\n' for e in events)+'data: [DONE]\n\n')
    monkeypatch.setattr(httpx,'MockTransport',StreamingTransport)


def _document_model_settings():
    return SimpleNamespace(
        document_model_base_url="https://qwen.example.test/v1",
        document_model_api_key="synthetic-key",
        document_model="Qwen3-30B-A3B-Instruct",
        document_model_trusted_http_origin="",
        document_model_connect_timeout=5,
        document_model_read_timeout=30,
    )


def _recognized_document(pdf, ocr):
    return ocr.RecognizedDocument(pages=(ocr.RecognizedPage(
        page_number=1,
        blocks=(pdf.PageTextBlock(
            "p1-t0001", "合同编号 SC-001", (10, 20, 180, 45), "TEXT_LAYER", None,
        ),),
    ),))


def _recognized_from_pdf(document, ocr):
    return ocr.RecognizedDocument(pages=tuple(
        ocr.RecognizedPage(page_number=page.page_number, blocks=page.blocks)
        for page in document.pages
    ))


def test_text_provider_sends_only_numbered_text_blocks_to_qwen():
    pdf, ocr = _modules()

    def handler(request):
        payload = json.loads(request.content)
        serialized = json.dumps(payload, ensure_ascii=False)
        assert payload["model"] == "Qwen3-30B-A3B-Instruct"
        assert "image_url" not in serialized
        assert "data:image" not in serialized
        assert "base64" not in serialized
        assert "p1-t0001" in serialized
        return httpx.Response(200, json=_openai_response({
            "document_type": "SALES_CONTRACT",
            "confidence": 0.98,
        }))

    provider = ocr.DocumentTextProvider(
        _document_model_settings(), transport=httpx.MockTransport(handler)
    )
    result = provider.classify(_recognized_document(pdf, ocr))
    assert result.document_type == "SALES_CONTRACT"


def test_text_provider_computes_machine_source_from_valid_block_ids():
    pdf, ocr = _modules()

    def handler(_request):
        return httpx.Response(200, json=_openai_response({
            "fields": [{
                "scope": "HEADER",
                "row_key": "header",
                "field_key": "contract_number",
                "normalized_value": "SC-001",
                "confidence": 0.97,
                "page_number": 1,
                "source_block_ids": ["p1-t0001"],
            }],
        }))

    provider = ocr.DocumentTextProvider(
        _document_model_settings(), transport=httpx.MockTransport(handler)
    )
    field = provider.extract_sales_contract(_recognized_document(pdf, ocr)).fields[0]
    assert field.raw_value == {"value": "合同编号 SC-001"}
    assert field.source_block_ids == ("p1-t0001",)
    assert field.bbox == {
        "x0": 10.0, "y0": 20.0, "x1": 180.0, "y1": 45.0,
        "source_block_ids": ["p1-t0001"],
    }


def test_text_provider_ignores_blank_candidates_without_source_blocks():
    pdf, ocr = _modules()

    def handler(_request):
        return httpx.Response(200, json=_openai_response({
            "fields": [{
                "scope": "MOLD",
                "row_key": "mold-1",
                "field_key": "machine_model",
                "normalized_value": "",
                "confidence": 0.50,
                "page_number": 1,
                "source_block_ids": [],
            }],
        }))

    provider = ocr.DocumentTextProvider(
        _document_model_settings(), transport=httpx.MockTransport(handler)
    )
    result = provider.extract_sales_contract(_recognized_document(pdf, ocr))
    assert result.fields == ()


def test_text_provider_rejects_field_source_outside_supplied_page_blocks():
    pdf, ocr = _modules()

    def handler(_request):
        return httpx.Response(200, json=_openai_response({
            "fields": [{
                "scope": "HEADER",
                "row_key": "header",
                "field_key": "contract_number",
                "normalized_value": "SC-001",
                "confidence": 0.97,
                "page_number": 1,
                "source_block_ids": ["p9-b9999"],
            }],
        }))

    provider = ocr.DocumentTextProvider(
        _document_model_settings(), transport=httpx.MockTransport(handler)
    )
    with pytest.raises(Exception) as error:
        provider.extract_sales_contract(_recognized_document(pdf, ocr))
    assert getattr(error.value, "code", None) == "DOCUMENT_FIELD_SOURCE_INVALID"


def test_text_provider_rejects_unregistered_contract_field_key():
    pdf, ocr = _modules()

    def handler(_request):
        return httpx.Response(200, json=_openai_response({
            "fields": [{
                "scope": "HEADER",
                "row_key": "header",
                "field_key": "invented_field",
                "normalized_value": "invented",
                "confidence": 0.90,
                "page_number": 1,
                "source_block_ids": ["p1-t0001"],
            }],
        }))

    provider = ocr.DocumentTextProvider(
        _document_model_settings(), transport=httpx.MockTransport(handler)
    )
    with pytest.raises(Exception) as error:
        provider.extract_sales_contract(_recognized_document(pdf, ocr))
    assert getattr(error.value, "code", None) == "DOCUMENT_MODEL_OUTPUT_INVALID"


def test_provider_returns_registered_document_classification():
    pdf, ocr = _modules()

    def handler(request):
        payload = json.loads(request.content)
        assert payload["model"] == "Qwen3-30B-A3B-Instruct"
        assert payload["response_format"] == {"type": "json_object"}
        return httpx.Response(200, json=_openai_response({
            "document_type": "SALES_CONTRACT",
            "confidence": 0.98,
        }))

    provider = ocr.DocumentTextProvider(
        _document_model_settings(), transport=httpx.MockTransport(handler)
    )
    document = _recognized_from_pdf(pdf.inspect_pdf(_text_pdf(), max_pages=20), ocr)
    result = provider.classify(document)
    assert result.document_type == "SALES_CONTRACT"
    assert str(result.confidence) == "0.98"


def test_provider_rejects_unknown_document_type():
    pdf, ocr = _modules()

    def handler(_request):
        return httpx.Response(200, json=_openai_response({
            "document_type": "INVOICE",
            "confidence": 0.99,
        }))

    provider = ocr.DocumentTextProvider(
        _document_model_settings(), transport=httpx.MockTransport(handler)
    )
    document = _recognized_from_pdf(pdf.inspect_pdf(_text_pdf(), max_pages=20), ocr)
    with pytest.raises(Exception) as error:
        provider.classify(document)
    assert getattr(error.value, "code", None) == "DOCUMENT_MODEL_OUTPUT_INVALID"


def test_provider_extracts_contract_fields_with_source_page():
    pdf, ocr = _modules()

    def handler(_request):
        return httpx.Response(200, json=_openai_response({
            "fields": [{
                "scope": "HEADER",
                "row_key": "header",
                "field_key": "contract_number",
                "normalized_value": "SC-001",
                "confidence": 0.97,
                "page_number": 1,
                "source_block_ids": ["p1-t0001"],
            }],
        }))

    provider = ocr.DocumentTextProvider(
        _document_model_settings(), transport=httpx.MockTransport(handler)
    )
    document = _recognized_from_pdf(pdf.inspect_pdf(_text_pdf(), max_pages=20), ocr)
    result = provider.extract_sales_contract(document)
    assert len(result.fields) == 1
    field = result.fields[0]
    assert field.scope == "HEADER"
    assert field.field_key == "contract_number"
    assert field.normalized_value == {"value": "SC-001"}
    assert field.page_number == 1


def test_provider_deduplicates_repeated_fields_by_highest_confidence():
    pdf, ocr = _modules()
    fitz = importlib.import_module("fitz")
    source = fitz.open()
    for index in range(5):
        page = source.new_page()
        page.insert_text((72, 72), f"Contract SC-001 page {index + 1}")
    document = _recognized_from_pdf(pdf.inspect_pdf(source.tobytes(), max_pages=20), ocr)
    source.close()
    calls = 0

    def handler(_request):
        nonlocal calls
        calls += 1
        confidence = 0.80 if calls == 1 else 0.95
        page_number = 1 if calls == 1 else 5
        return httpx.Response(200, json=_openai_response({
            "fields": [{
                "scope": "HEADER",
                "row_key": "header",
                "field_key": "contract_number",
                "normalized_value": "SC-001",
                "confidence": confidence,
                "page_number": page_number,
                "source_block_ids": [f"p{page_number}-t0001"],
            }],
        }))

    provider = ocr.DocumentTextProvider(
        _document_model_settings(), transport=httpx.MockTransport(handler)
    )
    result = provider.extract_sales_contract(document)
    assert calls == 2
    assert len(result.fields) == 1
    assert result.fields[0].page_number == 5
    assert str(result.fields[0].confidence) == "0.95"


def test_provider_keeps_same_local_row_key_from_different_pages_separate():
    pdf, ocr = _modules()
    fitz = importlib.import_module("fitz")
    source = fitz.open()
    for index in range(5):
        page = source.new_page()
        page.insert_text((72, 72), f"Mold row page {index + 1}")
    document = _recognized_from_pdf(pdf.inspect_pdf(source.tobytes(), max_pages=20), ocr)
    source.close()
    calls = 0

    def handler(_request):
        nonlocal calls
        calls += 1
        value = "MOLD-A" if calls == 1 else "MOLD-B"
        page_number = 1 if calls == 1 else 5
        return httpx.Response(200, json=_openai_response({
            "fields": [{
                "scope": "MOLD",
                "row_key": "mold-1",
                "field_key": "customer_mold_number",
                "normalized_value": value,
                "confidence": 0.90,
                "page_number": page_number,
                "source_block_ids": [f"p{page_number}-t0001"],
            }],
        }))

    provider = ocr.DocumentTextProvider(
        _document_model_settings(), transport=httpx.MockTransport(handler)
    )
    result = provider.extract_sales_contract(document)
    assert [(field.row_key, field.normalized_value["value"]) for field in result.fields] == [
        ("p1:mold-1", "MOLD-A"),
        ("p5:mold-1", "MOLD-B"),
    ]


def _job_fixture():
    engine, Session = pg_factory()
    with Session.begin() as db:
        user = m.User(username="ocr-worker", display_name="OCR Worker", password_hash="test")
        db.add(user)
        db.flush()
        conversation = m.Conversation(user_id=user.id, title="OCR Worker")
        db.add(conversation)
        db.flush()
        blob = m.FileObject(
            owner_id=user.id,
            conversation_id=conversation.id,
            request_key="11111111-1111-4111-8111-111111111111",
            filename="合同.pdf",
            media_type="application/pdf",
            size=10,
            sha256="a" * 64,
            backend="local",
            storage_namespace="local",
            object_key="objects/contract",
            storage_version=None,
        )
        db.add(blob)
        db.flush()
        intake = m.DocumentIntake(
            conversation_id=conversation.id,
            created_by=user.id,
            request_key="22222222-2222-4222-8222-222222222222",
            status="PRECLASSIFYING",
        )
        db.add(intake)
        db.flush()
        intake_file = m.DocumentIntakeFile(intake_id=intake.id, file_id=blob.id)
        db.add(intake_file)
        db.flush()
        job = m.DocumentOcrJob(intake_file_id=intake_file.id, phase="PRECLASSIFY")
        db.add(job)
        db.flush()
        ids = {"job": job.id, "file": intake_file.id, "intake": intake.id}
    return engine, Session, ids


def _image_pages_pdf(count):
    fitz = importlib.import_module("fitz")
    source = fitz.open()
    png = b64decode(
        "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk+A8AAQUBAScY42YAAAAASUVORK5CYII="
    )
    for _index in range(count):
        page = source.new_page(width=300, height=400)
        page.insert_image(page.rect, stream=png)
    data = source.tobytes()
    source.close()
    return data


class _FakePaddleOCR:
    def __init__(self, client_module):
        self.client_module = client_module
        self.calls = []

    def recognize_page(self, _image_png, *, page_number, request_id=None):
        del request_id
        self.calls.append(page_number)
        return self.client_module.PaddleOCRPage(
            page_number=page_number,
            engine="PaddleOCR",
            engine_version="3.7.0",
            model_version="PP-OCRv5",
            device="gpu:0",
            blocks=(self.client_module.PaddleOCRBlock(
                block_id=f"p{page_number}-b0001",
                text=f"第{page_number}页销售合同",
                confidence=0.98,
                bbox=((10, 20), (200, 20), (200, 60), (10, 60)),
            ),),
        )


def test_document_worker_reuses_page_cache_when_qwen_retry_succeeds(monkeypatch):
    worker = importlib.import_module("app.document_worker")
    _, ocr = _modules()
    client_module = importlib.import_module("domain_packs.mold.erp.commercial.paddleocr_client")
    engine, Session, ids = _job_fixture()
    paddle = _FakePaddleOCR(client_module)
    seen_documents = []

    class FailingProvider:
        name = "synthetic-text"

        def classify(self, document, **kwargs):
            seen_documents.append(document)
            raise RuntimeError("private qwen response")

    class SuccessfulProvider:
        name = "synthetic-text"

        def classify(self, document, **kwargs):
            seen_documents.append(document)
            return ocr.Classification("SALES_CONTRACT", Decimal("0.96"))

    monkeypatch.setattr(host_ports().object_storage, "read", lambda _blob: _image_pages_pdf(1))
    monkeypatch.setattr(settings(), "ocr_max_pages", 20)
    monkeypatch.setattr(settings(), "ocr_max_attempts", 5)
    try:
        claimed = worker.claim(Session)
        worker.process_claim(Session, claimed[0], claimed[1], FailingProvider(), paddle)
        with Session.begin() as db:
            job = db.get(m.DocumentOcrJob, ids["job"])
            assert job.status == "RETRY_WAIT"
            job.status = "QUEUED"
            job.retry_at = None
            pages = list(db.scalars(select(m.DocumentRecognizedPage)))
            assert len(pages) == 1
            assert pages[0].blocks[0]["block_id"] == "p1-b0001"
        retried = worker.claim(Session)
        worker.process_claim(Session, retried[0], retried[1], SuccessfulProvider(), paddle)
        assert paddle.calls == [1]
        assert len(seen_documents) == 2
        assert seen_documents[1].pages[0].blocks[0].block_id == "p1-b0001"
        with Session() as db:
            assert db.get(m.DocumentOcrJob, ids["job"]).status == "SUCCEEDED"
            assert len(list(db.scalars(select(m.DocumentRecognizedPage)))) == 1
    finally:
        engine.dispose()


def test_document_worker_preclassifies_three_pages_then_reuses_them_for_full_contract(monkeypatch):
    worker = importlib.import_module("app.document_worker")
    _, ocr = _modules()
    client_module = importlib.import_module("domain_packs.mold.erp.commercial.paddleocr_client")
    engine, Session, ids = _job_fixture()
    paddle = _FakePaddleOCR(client_module)

    class Provider:
        name = "synthetic-text"

        def classify(self, document, **kwargs):
            assert [page.page_number for page in document.pages] == [1, 2, 3]
            return ocr.Classification("SALES_CONTRACT", Decimal("0.96"))

        def extract_sales_contract(self, document, **kwargs):
            assert [page.page_number for page in document.pages] == [1, 2, 3, 4]
            return ocr.ContractExtraction(())

    monkeypatch.setattr(host_ports().object_storage, "read", lambda _blob: _image_pages_pdf(4))
    monkeypatch.setattr(settings(), "ocr_max_pages", 20)
    try:
        claimed = worker.claim(Session)
        worker.process_claim(Session, claimed[0], claimed[1], Provider(), paddle)
        assert paddle.calls == [1, 2, 3]
        with Session.begin() as db:
            group = m.ContractIntakeGroup(
                intake_id=ids["intake"], group_key="contract-1", status="FULL_OCR_QUEUED"
            )
            db.add(group)
            db.flush()
            intake_file = db.get(m.DocumentIntakeFile, ids["file"])
            intake_file.contract_group_id = group.id
            intake_file.confirmed_type = "SALES_CONTRACT"
            db.get(m.DocumentIntake, ids["intake"]).status = "FULL_OCR_QUEUED"
            full_job = m.DocumentOcrJob(intake_file_id=ids["file"], phase="FULL_CONTRACT")
            db.add(full_job)
            db.flush()
            full_job_id = full_job.id
        full_claim = worker.claim(Session)
        assert full_claim[0] == full_job_id
        worker.process_claim(Session, full_claim[0], full_claim[1], Provider(), paddle)
        assert paddle.calls == [1, 2, 3, 4]
        with Session() as db:
            assert len(list(db.scalars(select(m.DocumentRecognizedPage)))) == 4
            assert db.get(m.DocumentOcrJob, full_job_id).status == "SUCCEEDED"
    finally:
        engine.dispose()


def test_document_worker_claims_and_completes_preclassification(monkeypatch):
    worker = importlib.import_module("app.document_worker")
    _, ocr = _modules()
    engine, Session, ids = _job_fixture()

    class Provider:
        name = "synthetic"

        def classify(self, _document, **kwargs):
            return ocr.Classification("SALES_CONTRACT", Decimal("0.96"))

        def extract_sales_contract(self, _document, **kwargs):
            raise AssertionError("预分类不能调用完整合同提取")

    monkeypatch.setattr(host_ports().object_storage, "read", lambda _blob: _text_pdf())
    monkeypatch.setattr(settings(), "ocr_max_pages", 20)
    try:
        claimed = worker.claim(Session)
        assert claimed and claimed[0] == ids["job"]
        worker.process_claim(Session, claimed[0], claimed[1], Provider())
        with Session() as db:
            job = db.get(m.DocumentOcrJob, ids["job"])
            intake_file = db.get(m.DocumentIntakeFile, ids["file"])
            intake = db.get(m.DocumentIntake, ids["intake"])
            assert job.status == "SUCCEEDED"
            assert intake_file.suggested_type == "SALES_CONTRACT"
            assert str(intake_file.suggested_confidence) == "0.9600"
            assert intake.status == "AWAITING_TYPE_CONFIRMATION"
    finally:
        engine.dispose()


def _full_contract_fixture(file_count=2):
    engine, Session = pg_factory()
    with Session.begin() as db:
        user = m.User(username="full-ocr-worker", display_name="Full OCR Worker", password_hash="test")
        db.add(user)
        db.flush()
        conversation = m.Conversation(user_id=user.id, title="Full OCR Worker")
        db.add(conversation)
        db.flush()
        intake = m.DocumentIntake(
            conversation_id=conversation.id,
            created_by=user.id,
            request_key="33333333-3333-4333-8333-333333333333",
            status="FULL_OCR_QUEUED",
        )
        db.add(intake)
        db.flush()
        group = m.ContractIntakeGroup(
            intake_id=intake.id,
            group_key="contract-1",
            status="FULL_OCR_QUEUED",
        )
        db.add(group)
        db.flush()
        jobs = []
        for index in range(file_count):
            blob = m.FileObject(
                owner_id=user.id,
                conversation_id=conversation.id,
                request_key=f"44444444-4444-4444-8444-44444444444{index}",
                filename=f"合同-{index + 1}.pdf",
                media_type="application/pdf",
                size=10,
                sha256=str(index + 1) * 64,
                backend="local",
                storage_namespace="local",
                object_key=f"objects/contract-{index}",
                storage_version=None,
            )
            db.add(blob)
            db.flush()
            intake_file = m.DocumentIntakeFile(
                intake_id=intake.id,
                file_id=blob.id,
                contract_group_id=group.id,
                confirmed_type="SALES_CONTRACT",
            )
            db.add(intake_file)
            db.flush()
            job = m.DocumentOcrJob(intake_file_id=intake_file.id, phase="FULL_CONTRACT")
            db.add(job)
            db.flush()
            jobs.append(job.id)
        ids = {"intake": intake.id, "group": group.id, "jobs": jobs}
    return engine, Session, ids


def test_document_worker_marks_group_ready_only_after_all_full_ocr_jobs(monkeypatch):
    worker = importlib.import_module("app.document_worker")
    _, ocr = _modules()
    engine, Session, ids = _full_contract_fixture(2)

    class Provider:
        name = "synthetic"

        def extract_sales_contract(self, _document, **kwargs):
            return ocr.ContractExtraction((ocr.ExtractedField(
                "HEADER", "header", "contract_number", {"value": "SC-001"},
                {"value": "SC-001"}, Decimal("0.95"), 1,
                {"source_block_ids": ["p1-t0001"]}, ("p1-t0001",),
            ),))

    monkeypatch.setattr(host_ports().object_storage, "read", lambda _blob: _text_pdf())
    monkeypatch.setattr(settings(), "ocr_max_pages", 20)
    try:
        first = worker.claim(Session)
        worker.process_claim(Session, first[0], first[1], Provider())
        with Session() as db:
            assert db.get(m.ContractIntakeGroup, ids["group"]).status == "FULL_OCR_PROCESSING"
        second = worker.claim(Session)
        worker.process_claim(Session, second[0], second[1], Provider())
        with Session() as db:
            assert db.get(m.ContractIntakeGroup, ids["group"]).status == "AWAITING_FIELD_CONFIRMATION"
            assert db.get(m.DocumentIntake, ids["intake"]).status == "AWAITING_FIELD_CONFIRMATION"
            assert db.scalar(select(m.DocumentExtractedField).where(
                m.DocumentExtractedField.job_id == ids["jobs"][0])) is not None
    finally:
        engine.dispose()


def test_document_worker_scopes_repeated_mold_row_keys_by_source_file(monkeypatch):
    worker = importlib.import_module("app.document_worker")
    _, ocr = _modules()
    engine, Session, ids = _full_contract_fixture(2)

    class Provider:
        name = "synthetic"

        def extract_sales_contract(self, _document, **kwargs):
            return ocr.ContractExtraction((ocr.ExtractedField(
                "MOLD", "p1:mold-1", "customer_mold_number", {"value": "M-1"},
                {"value": "M-1"}, Decimal("0.95"), 1,
                {"source_block_ids": ["p1-t0001"]}, ("p1-t0001",),
            ),))

    monkeypatch.setattr(host_ports().object_storage, "read", lambda _blob: _text_pdf())
    monkeypatch.setattr(settings(), "ocr_max_pages", 20)
    try:
        for _index in range(2):
            claimed = worker.claim(Session)
            worker.process_claim(Session, claimed[0], claimed[1], Provider())
        with Session() as db:
            rows = list(db.scalars(
                select(m.DocumentExtractedField)
                .join(m.DocumentOcrJob, m.DocumentOcrJob.id == m.DocumentExtractedField.job_id)
                .join(m.DocumentIntakeFile, m.DocumentIntakeFile.id == m.DocumentOcrJob.intake_file_id)
                .where(
                    m.DocumentIntakeFile.contract_group_id == ids["group"],
                    m.DocumentExtractedField.scope == "MOLD",
                )
                .order_by(m.DocumentExtractedField.row_key)
            ))
            assert len(rows) == 2
            assert len({row.row_key for row in rows}) == 2
            assert all(row.row_key.endswith(":p1:mold-1") for row in rows)
    finally:
        engine.dispose()


def test_document_worker_recovers_group_after_failed_full_ocr_retry(monkeypatch):
    worker = importlib.import_module("app.document_worker")
    _, ocr = _modules()
    engine, Session, ids = _full_contract_fixture(1)

    class FailingProvider:
        name = "synthetic"

        def extract_sales_contract(self, _document, **kwargs):
            raise RuntimeError("private upstream response")

    class SuccessfulProvider:
        name = "synthetic"

        def extract_sales_contract(self, _document, **kwargs):
            return ocr.ContractExtraction(())

    monkeypatch.setattr(host_ports().object_storage, "read", lambda _blob: _text_pdf())
    monkeypatch.setattr(settings(), "ocr_max_pages", 20)
    monkeypatch.setattr(settings(), "ocr_max_attempts", 1)
    try:
        claimed = worker.claim(Session)
        worker.process_claim(Session, claimed[0], claimed[1], FailingProvider())
        with Session.begin() as db:
            assert db.get(m.ContractIntakeGroup, ids["group"]).status == "OCR_FAILED"
            job = db.get(m.DocumentOcrJob, ids["jobs"][0])
            job.status = "QUEUED"
            job.finished_at = None
        retried = worker.claim(Session)
        worker.process_claim(Session, retried[0], retried[1], SuccessfulProvider())
        with Session() as db:
            assert db.get(m.ContractIntakeGroup, ids["group"]).status == "AWAITING_FIELD_CONFIRMATION"
    finally:
        engine.dispose()


def test_document_worker_retries_with_stable_error_code(monkeypatch):
    worker = importlib.import_module("app.document_worker")
    engine, Session, ids = _job_fixture()

    class Provider:
        name = "synthetic"

        def classify(self, _document, **kwargs):
            raise RuntimeError("secret upstream response")

    monkeypatch.setattr(host_ports().object_storage, "read", lambda _blob: _text_pdf())
    monkeypatch.setattr(settings(), "ocr_max_pages", 20)
    monkeypatch.setattr(settings(), "ocr_max_attempts", 5)
    try:
        job_id, lease_id = worker.claim(Session)
        worker.process_claim(Session, job_id, lease_id, Provider())
        with Session() as db:
            job = db.get(m.DocumentOcrJob, ids["job"])
            assert job.status == "RETRY_WAIT"
            assert job.attempts == 1
            assert job.last_error == "RuntimeError"
            assert "secret" not in job.last_error
            assert job.retry_at is not None
    finally:
        engine.dispose()
