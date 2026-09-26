"""Loopback-only single-page PaddleOCR service."""
from contextlib import asynccontextmanager
from hmac import compare_digest
from importlib.metadata import version
import json
import os
import struct
from threading import Lock
from uuid import UUID

from fastapi import Body, FastAPI, Header, HTTPException, Query, Request
from starlette.concurrency import run_in_threadpool


ENGINE_NAME = "PaddleOCR"
ENGINE_VERSION = "3.7.0"
MODEL_VERSION = "PP-OCRv5"
PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"


class PaddleEngine:
    def __init__(self):
        import paddle
        from paddleocr import PaddleOCR

        self._paddle = paddle
        self._device = os.environ.get("PADDLE_OCR_DEVICE", "gpu:0")
        self._lock = Lock()
        self._ocr = PaddleOCR(
            ocr_version=MODEL_VERSION,
            lang="ch",
            device=self._device,
            use_doc_orientation_classify=True,
            use_doc_unwarping=False,
            use_textline_orientation=True,
            text_detection_model_name="PP-OCRv5_mobile_det",
            text_recognition_model_name="PP-OCRv5_mobile_rec",
            text_recognition_batch_size=1,
        )

    def health(self):
        return {
            "device": self._device,
            "cuda_available": bool(self._paddle.device.is_compiled_with_cuda()),
            "paddle_version": version("paddlepaddle-gpu") if self._device.startswith("gpu") else version("paddlepaddle"),
            "paddleocr_version": version("paddleocr"),
            "model_version": MODEL_VERSION,
        }

    @staticmethod
    def _payload(result):
        value = getattr(result, "json", result)
        if callable(value):
            value = value()
        if isinstance(value, str):
            value = json.loads(value)
        if not isinstance(value, dict):
            raise ValueError("PaddleOCR result is not an object")
        data = value.get("res", value)
        if not isinstance(data, dict):
            raise ValueError("PaddleOCR result payload is invalid")
        return data

    def recognize(self, image):
        import cv2
        import numpy as np

        decoded = cv2.imdecode(np.frombuffer(image, dtype=np.uint8), cv2.IMREAD_COLOR)
        if decoded is None:
            raise ValueError("PNG decode failed")
        with self._lock:
            results = self._ocr.predict(decoded)
        blocks = []
        for result in results:
            data = self._payload(result)
            texts = data.get("rec_texts") or []
            scores = data.get("rec_scores") or []
            polygons = data.get("rec_polys") or data.get("dt_polys") or []
            for text, score, polygon in zip(texts, scores, polygons):
                blocks.append({
                    "text": str(text),
                    "confidence": float(score),
                    "bbox": polygon.tolist() if hasattr(polygon, "tolist") else polygon,
                })
        return blocks


def _safe_blocks(rows, page_number):
    if not isinstance(rows, list) or len(rows) > 5000:
        raise ValueError("OCR block list is invalid")
    blocks = []
    for row in rows:
        if not isinstance(row, dict):
            raise ValueError("OCR block is invalid")
        text = row.get("text")
        confidence = row.get("confidence")
        bbox = row.get("bbox")
        if not isinstance(text, str) or not text.strip() or len(text) > 10000:
            raise ValueError("OCR block text is invalid")
        if not isinstance(confidence, (int, float)) or not 0 <= float(confidence) <= 1:
            raise ValueError("OCR block confidence is invalid")
        if (
            not isinstance(bbox, (list, tuple))
            or len(bbox) != 4
            or any(not isinstance(point, (list, tuple)) or len(point) != 2 for point in bbox)
            or any(not isinstance(value, (int, float)) for point in bbox for value in point)
        ):
            raise ValueError("OCR block bbox is invalid")
        blocks.append({
            "block_id": f"p{page_number}-b{len(blocks) + 1:04d}",
            "text": text.strip(),
            "confidence": float(confidence),
            "bbox": [[float(value) for value in point] for point in bbox],
        })
    return blocks


def _validate_png(image, max_image_bytes, max_pixels):
    if len(image) > max_image_bytes:
        raise HTTPException(413, "OCR_IMAGE_TOO_LARGE")
    if len(image) < 24 or not image.startswith(PNG_SIGNATURE):
        raise HTTPException(415, "OCR_IMAGE_TYPE_INVALID")
    width, height = struct.unpack(">II", image[16:24])
    if width < 1 or height < 1 or width * height > max_pixels:
        raise HTTPException(413, "OCR_IMAGE_DIMENSIONS_INVALID")


def create_app(
    engine=None,
    *,
    token=None,
    max_image_bytes=15 * 1024 * 1024,
    max_pixels=100_000_000,
):
    configured_token = token if token is not None else os.environ.get("PADDLE_OCR_TOKEN", "")

    @asynccontextmanager
    async def lifespan(app):
        if not configured_token:
            raise RuntimeError("PADDLE_OCR_TOKEN is required")
        if app.state.engine is None:
            app.state.engine = await run_in_threadpool(PaddleEngine)
        yield

    application = FastAPI(title="MoldPilot PaddleOCR", docs_url=None, redoc_url=None, lifespan=lifespan)
    application.state.engine = engine

    @application.get("/health")
    async def health(request: Request):
        active = request.app.state.engine
        if active is None:
            raise HTTPException(503, "OCR_ENGINE_NOT_READY")
        details = await run_in_threadpool(active.health)
        return {"status": "ok", **details}

    @application.post("/v1/ocr/page")
    async def recognize_page(
        request: Request,
        page_number: int = Query(ge=1, le=500),
        request_id: UUID = Query(),
        authorization: str | None = Header(default=None),
        content_type: str | None = Header(default=None),
        image: bytes = Body(),
    ):
        del request_id
        expected = f"Bearer {configured_token}"
        if not authorization or not compare_digest(authorization, expected):
            raise HTTPException(401, "OCR_UNAUTHORIZED")
        if (content_type or "").split(";", 1)[0].strip().lower() != "image/png":
            raise HTTPException(415, "OCR_IMAGE_TYPE_INVALID")
        _validate_png(image, max_image_bytes, max_pixels)
        active = request.app.state.engine
        if active is None:
            raise HTTPException(503, "OCR_ENGINE_NOT_READY")
        try:
            rows = await run_in_threadpool(active.recognize, image)
            blocks = _safe_blocks(rows, page_number)
            details = await run_in_threadpool(active.health)
        except (ValueError, TypeError, KeyError):
            raise HTTPException(502, "OCR_ENGINE_OUTPUT_INVALID") from None
        return {
            "page_number": page_number,
            "engine": ENGINE_NAME,
            "engine_version": details["paddleocr_version"],
            "model_version": details["model_version"],
            "device": details["device"],
            "blocks": blocks,
        }

    return application


app = create_app()
