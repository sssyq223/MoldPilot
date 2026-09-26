import importlib
from uuid import UUID

import httpx
import pytest


PNG = b"\x89PNG\r\n\x1a\n" + b"synthetic-page"


def _module():
    return importlib.import_module("domain_packs.mold.erp.commercial.paddleocr_client")


def _response(*, page_number=2, blocks=None):
    return {
        "page_number": page_number,
        "engine": "PaddleOCR",
        "engine_version": "3.7.0",
        "model_version": "PP-OCRv5",
        "device": "gpu:0",
        "blocks": blocks if blocks is not None else [{
            "block_id": "p2-b0001",
            "text": "销售合同",
            "confidence": 0.98,
            "bbox": [[10, 20], [110, 20], [110, 50], [10, 50]],
        }],
    }


def _client(module, handler):
    return module.PaddleOCRClient(
        "http://127.0.0.1:18081",
        "synthetic-secret",
        connect_timeout=5,
        read_timeout=120,
        transport=httpx.MockTransport(handler),
    )


def test_document_pipeline_settings_separate_ocr_service_from_text_model():
    from app.config import Settings

    config = Settings(_env_file=None)
    assert config.ocr_service_url == "http://127.0.0.1:18081"
    assert config.ocr_service_connect_timeout == 5
    assert config.ocr_service_read_timeout == 120
    assert config.ocr_render_dpi == 300
    assert config.ocr_min_text_chars == 40
    assert config.ocr_image_coverage_threshold == 0.25
    assert config.document_model == "Qwen3-30B-A3B-Instruct"
    assert not hasattr(config, "ocr_base_url")
    assert not hasattr(config, "ocr_api_key")
    assert not hasattr(config, "ocr_model")


def test_client_sends_bearer_png_page_and_request_id():
    module = _module()
    seen = {}

    def handler(request):
        seen["request"] = request
        return httpx.Response(200, json=_response())

    client = _client(module, handler)
    request_id = UUID("11111111-1111-4111-8111-111111111111")
    result = client.recognize_page(PNG, page_number=2, request_id=request_id)

    request = seen["request"]
    assert request.method == "POST"
    assert request.url.path == "/v1/ocr/page"
    assert request.url.params["page_number"] == "2"
    assert request.url.params["request_id"] == str(request_id)
    assert request.headers["authorization"] == "Bearer synthetic-secret"
    assert request.headers["content-type"] == "image/png"
    assert request.content == PNG
    assert result.page_number == 2
    assert result.device == "gpu:0"
    assert result.blocks[0].block_id == "p2-b0001"
    assert result.blocks[0].bbox == ((10.0, 20.0), (110.0, 20.0), (110.0, 50.0), (10.0, 50.0))


@pytest.mark.parametrize("payload", [
    _response(blocks=[{
        "block_id": "p2-b0001", "text": "合同", "confidence": 0.9,
        "bbox": [[1, 2], [3, 4], [5, 6]],
    }]),
    _response(blocks=[
        {"block_id": "p2-b0001", "text": "合同", "confidence": 0.9,
         "bbox": [[1, 2], [3, 2], [3, 4], [1, 4]]},
        {"block_id": "p2-b0001", "text": "编号", "confidence": 0.8,
         "bbox": [[5, 2], [7, 2], [7, 4], [5, 4]]},
    ]),
    _response(blocks=[{
        "block_id": "p2-b0001", "text": "合同", "confidence": 1.01,
        "bbox": [[1, 2], [3, 2], [3, 4], [1, 4]],
    }]),
    _response(page_number=3),
])
def test_client_rejects_invalid_service_output(payload):
    module = _module()
    client = _client(module, lambda _request: httpx.Response(200, json=payload))
    with pytest.raises(Exception) as error:
        client.recognize_page(PNG, page_number=2)
    assert getattr(error.value, "code", None) == "OCR_SERVICE_OUTPUT_INVALID"


def test_client_maps_timeout_to_safe_unavailable_error():
    module = _module()

    def handler(request):
        raise httpx.ReadTimeout("private timeout details", request=request)

    client = _client(module, handler)
    with pytest.raises(Exception) as error:
        client.recognize_page(PNG, page_number=2)
    assert getattr(error.value, "code", None) == "OCR_SERVICE_UNAVAILABLE"
    assert "private timeout" not in str(error.value)
