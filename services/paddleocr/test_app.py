from base64 import b64decode
import sys
from types import SimpleNamespace

from fastapi.testclient import TestClient

from services.paddleocr import app as service_app
from services.paddleocr.app import create_app


PNG = b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk+A8AAQUBAScY42YAAAAASUVORK5CYII="
)
REQUEST_ID = "11111111-1111-4111-8111-111111111111"


class FakeEngine:
    def health(self):
        return {
            "device": "gpu:0",
            "cuda_available": True,
            "paddle_version": "3.2.0",
            "paddleocr_version": "3.7.0",
            "model_version": "PP-OCRv5",
        }

    def recognize(self, _image):
        return [
            {
                "text": "销售合同",
                "confidence": 0.99,
                "bbox": [[1, 2], [3, 2], [3, 4], [1, 4]],
            }
        ]


def _client(*, max_bytes=1024):
    return TestClient(create_app(FakeEngine(), token="test-token", max_image_bytes=max_bytes))


def test_engine_pins_ppocrv5_mobile_detection_and_recognition_models(monkeypatch):
    captured = {}

    class FakePaddleOCR:
        def __init__(self, **kwargs):
            captured.update(kwargs)

    monkeypatch.setitem(sys.modules, "paddle", SimpleNamespace())
    monkeypatch.setitem(sys.modules, "paddleocr", SimpleNamespace(PaddleOCR=FakePaddleOCR))
    service_app.PaddleEngine()

    assert captured["text_detection_model_name"] == "PP-OCRv5_mobile_det"
    assert captured["text_recognition_model_name"] == "PP-OCRv5_mobile_rec"
    assert captured["text_recognition_batch_size"] == 1


def test_health_reports_gpu_and_fixed_versions():
    response = _client().get("/health")
    assert response.status_code == 200
    assert response.json() == {
        "status": "ok",
        "device": "gpu:0",
        "cuda_available": True,
        "paddle_version": "3.2.0",
        "paddleocr_version": "3.7.0",
        "model_version": "PP-OCRv5",
    }


def test_page_ocr_requires_bearer_token():
    response = _client().post(
        f"/v1/ocr/page?page_number=1&request_id={REQUEST_ID}",
        content=PNG,
        headers={"content-type": "image/png"},
    )
    assert response.status_code == 401
    assert response.json()["detail"] == "OCR_UNAUTHORIZED"


def test_page_ocr_rejects_non_png_and_oversized_body():
    wrong_type = _client().post(
        f"/v1/ocr/page?page_number=1&request_id={REQUEST_ID}",
        content=b"not-an-image",
        headers={"authorization": "Bearer test-token", "content-type": "text/plain"},
    )
    assert wrong_type.status_code == 415
    assert wrong_type.json()["detail"] == "OCR_IMAGE_TYPE_INVALID"

    oversized = _client(max_bytes=16).post(
        f"/v1/ocr/page?page_number=1&request_id={REQUEST_ID}",
        content=PNG,
        headers={"authorization": "Bearer test-token", "content-type": "image/png"},
    )
    assert oversized.status_code == 413
    assert oversized.json()["detail"] == "OCR_IMAGE_TOO_LARGE"


def test_page_ocr_normalizes_blocks_without_echoing_image():
    response = _client().post(
        f"/v1/ocr/page?page_number=2&request_id={REQUEST_ID}",
        content=PNG,
        headers={"authorization": "Bearer test-token", "content-type": "image/png"},
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body == {
        "page_number": 2,
        "engine": "PaddleOCR",
        "engine_version": "3.7.0",
        "model_version": "PP-OCRv5",
        "device": "gpu:0",
        "blocks": [{
            "block_id": "p2-b0001",
            "text": "销售合同",
            "confidence": 0.99,
            "bbox": [[1.0, 2.0], [3.0, 2.0], [3.0, 4.0], [1.0, 4.0]],
        }],
    }
    assert "iVBOR" not in response.text
