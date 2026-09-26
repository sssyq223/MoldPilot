"""Validated loopback client for the single-page PaddleOCR service."""
from dataclasses import dataclass
import math
from uuid import UUID, uuid4

import httpx

from domain_packs.mold.ports.errors import DomainError


@dataclass(frozen=True)
class PaddleOCRBlock:
    block_id: str
    text: str
    confidence: float
    bbox: tuple[tuple[float, float], tuple[float, float], tuple[float, float], tuple[float, float]]


@dataclass(frozen=True)
class PaddleOCRPage:
    page_number: int
    engine: str
    engine_version: str
    model_version: str
    device: str
    blocks: tuple[PaddleOCRBlock, ...]


def _output_error() -> DomainError:
    return DomainError("OCR_SERVICE_OUTPUT_INVALID", "OCR 服务返回格式无效", 502)


def _number(value) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise _output_error()
    number = float(value)
    if not math.isfinite(number):
        raise _output_error()
    return number


def _text(value, *, maximum: int) -> str:
    if not isinstance(value, str) or not value.strip() or len(value) > maximum:
        raise _output_error()
    return value.strip()


class PaddleOCRClient:
    def __init__(
        self,
        base_url: str,
        token: str,
        *,
        connect_timeout: float = 5,
        read_timeout: float = 120,
        transport=None,
    ):
        url = httpx.URL(base_url)
        if (
            url.scheme != "http"
            or url.host not in {"127.0.0.1", "localhost", "::1"}
            or url.username
            or url.password
            or url.query
            or url.fragment
        ):
            raise ValueError("PaddleOCR service must use an uncredentialed loopback HTTP URL")
        if not token:
            raise ValueError("PaddleOCR service token is required")
        self._url = str(url).rstrip("/") + "/v1/ocr/page"
        self._token = token
        self._client = httpx.Client(
            timeout=httpx.Timeout(
                connect=connect_timeout,
                read=read_timeout,
                write=30,
                pool=5,
            ),
            transport=transport,
            trust_env=False,
            follow_redirects=False,
        )

    def close(self) -> None:
        self._client.close()

    def recognize_page(
        self,
        image_png: bytes,
        *,
        page_number: int,
        request_id: UUID | None = None,
    ) -> PaddleOCRPage:
        if not isinstance(image_png, bytes) or not image_png.startswith(b"\x89PNG\r\n\x1a\n"):
            raise DomainError("OCR_PAGE_IMAGE_INVALID", "OCR 页面必须是 PNG")
        if not isinstance(page_number, int) or isinstance(page_number, bool) or page_number < 1:
            raise DomainError("OCR_PAGE_NUMBER_INVALID", "OCR 页码无效")
        try:
            response = self._client.post(
                self._url,
                params={
                    "page_number": page_number,
                    "request_id": str(request_id or uuid4()),
                },
                headers={
                    "Authorization": f"Bearer {self._token}",
                    "Content-Type": "image/png",
                },
                content=image_png,
            )
            if response.status_code in {401, 403}:
                raise DomainError("OCR_SERVICE_AUTH_FAILED", "OCR 服务鉴权失败", 503)
            if not response.is_success:
                raise DomainError("OCR_SERVICE_FAILED", "OCR 服务调用失败", 503)
            return self._parse(response.json(), page_number)
        except DomainError:
            raise
        except (httpx.TimeoutException, httpx.RequestError):
            raise DomainError("OCR_SERVICE_UNAVAILABLE", "OCR 服务暂时不可用", 503) from None
        except (ValueError, TypeError):
            raise _output_error() from None

    @staticmethod
    def _parse(payload, expected_page_number: int) -> PaddleOCRPage:
        if not isinstance(payload, dict) or payload.get("page_number") != expected_page_number:
            raise _output_error()
        engine = _text(payload.get("engine"), maximum=80)
        engine_version = _text(payload.get("engine_version"), maximum=80)
        model_version = _text(payload.get("model_version"), maximum=120)
        device = _text(payload.get("device"), maximum=40)
        rows = payload.get("blocks")
        if not isinstance(rows, list) or len(rows) > 5000:
            raise _output_error()
        blocks = []
        seen = set()
        for row in rows:
            if not isinstance(row, dict):
                raise _output_error()
            block_id = _text(row.get("block_id"), maximum=120)
            if block_id in seen:
                raise _output_error()
            seen.add(block_id)
            text = _text(row.get("text"), maximum=10000)
            confidence = _number(row.get("confidence"))
            if confidence < 0 or confidence > 1:
                raise _output_error()
            bbox = row.get("bbox")
            if not isinstance(bbox, (list, tuple)) or len(bbox) != 4:
                raise _output_error()
            points = []
            for point in bbox:
                if not isinstance(point, (list, tuple)) or len(point) != 2:
                    raise _output_error()
                x, y = (_number(value) for value in point)
                if x < 0 or y < 0:
                    raise _output_error()
                points.append((x, y))
            blocks.append(PaddleOCRBlock(
                block_id=block_id,
                text=text,
                confidence=confidence,
                bbox=tuple(points),
            ))
        return PaddleOCRPage(
            page_number=expected_page_number,
            engine=engine,
            engine_version=engine_version,
            model_version=model_version,
            device=device,
            blocks=tuple(blocks),
        )
