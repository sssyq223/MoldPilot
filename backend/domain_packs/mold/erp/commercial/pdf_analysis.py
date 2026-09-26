"""Safe PDF inspection before local PaddleOCR and text-model processing."""
from dataclasses import dataclass
import math
from typing import Literal

import pymupdf

from domain_packs.mold.ports.errors import DomainError


BlockSource = Literal["TEXT_LAYER", "PADDLE_OCR"]


@dataclass(frozen=True)
class PageTextBlock:
    block_id: str
    text: str
    bbox: tuple[float, float, float, float]
    source: BlockSource
    confidence: float | None


@dataclass(frozen=True)
class PDFPage:
    page_number: int
    text: str
    needs_ocr: bool
    image_png: bytes | None
    blocks: tuple[PageTextBlock, ...]
    image_coverage: float
    width: float
    height: float


@dataclass(frozen=True)
class PDFDocument:
    encrypted: bool
    page_count: int
    pages: tuple[PDFPage, ...]


def _normalized_text(value: str) -> str:
    return "".join(value.split()).casefold()


def _intersection_over_union(
    left: tuple[float, float, float, float],
    right: tuple[float, float, float, float],
) -> float:
    x0 = max(left[0], right[0])
    y0 = max(left[1], right[1])
    x1 = min(left[2], right[2])
    y1 = min(left[3], right[3])
    intersection = max(0.0, x1 - x0) * max(0.0, y1 - y0)
    left_area = max(0.0, left[2] - left[0]) * max(0.0, left[3] - left[1])
    right_area = max(0.0, right[2] - right[0]) * max(0.0, right[3] - right[1])
    union = left_area + right_area - intersection
    return intersection / union if union else 0.0


def merge_page_blocks(
    embedded: tuple[PageTextBlock, ...],
    recognized: tuple[PageTextBlock, ...],
) -> tuple[PageTextBlock, ...]:
    """Merge one page while preferring its authoritative embedded text."""
    merged: list[PageTextBlock] = []
    for block in (*embedded, *recognized):
        duplicate_index = next((
            index
            for index, current in enumerate(merged)
            if _normalized_text(current.text) == _normalized_text(block.text)
            and _intersection_over_union(current.bbox, block.bbox) >= 0.5
        ), None)
        if duplicate_index is None:
            merged.append(block)
            continue
        if block.source == "TEXT_LAYER" and merged[duplicate_index].source != "TEXT_LAYER":
            merged[duplicate_index] = block
    return tuple(sorted(merged, key=lambda item: (
        round(item.bbox[1], 2), round(item.bbox[0], 2), item.block_id,
    )))


def _text_blocks(page, page_number: int) -> tuple[PageTextBlock, ...]:
    blocks = []
    for row in page.get_text("blocks"):
        if len(row) < 7 or row[6] != 0:
            continue
        text = str(row[4] or "").strip()
        if not text:
            continue
        blocks.append(PageTextBlock(
            block_id=f"p{page_number}-t{len(blocks) + 1:04d}",
            text=text,
            bbox=tuple(float(value) for value in row[:4]),
            source="TEXT_LAYER",
            confidence=None,
        ))
    return tuple(blocks)


def _image_coverage(page) -> float:
    page_area = float(page.rect.width * page.rect.height)
    if page_area <= 0:
        return 0.0
    area = 0.0
    for image in page.get_image_info():
        bbox = pymupdf.Rect(image.get("bbox") or ())
        clipped = bbox & page.rect
        if not clipped.is_empty:
            area += float(clipped.width * clipped.height)
    return min(1.0, area / page_area)


def inspect_document(
    data: bytes,
    media_type: str,
    *,
    max_pages: int,
    render_dpi: int = 300,
    max_render_pixels: int = 95_000_000,
    min_text_chars: int = 1,
    image_coverage_threshold: float = 0.25,
) -> PDFDocument:
    if media_type == "application/pdf":
        return inspect_pdf(
            data, max_pages=max_pages, render_dpi=render_dpi,
            max_render_pixels=max_render_pixels, min_text_chars=min_text_chars,
            image_coverage_threshold=image_coverage_threshold,
        )
    if media_type not in {"image/png", "image/jpeg"}:
        raise DomainError("DOCUMENT_MEDIA_TYPE_UNSUPPORTED", "只支持 PDF、PNG 或 JPG 文件")
    try:
        image_document = pymupdf.open(stream=data, filetype=media_type.removeprefix("image/"))
        if image_document.page_count != 1:
            raise ValueError
        pdf_data = image_document.convert_to_pdf()
        image_document.close()
    except Exception:
        raise DomainError("IMAGE_INVALID", "图片文件损坏或无法读取") from None
    return inspect_pdf(
        pdf_data, max_pages=max_pages, render_dpi=render_dpi,
        max_render_pixels=max_render_pixels, min_text_chars=min_text_chars,
        image_coverage_threshold=image_coverage_threshold,
    )


def inspect_pdf(
    data: bytes,
    *,
    max_pages: int,
    render_dpi: int = 300,
    max_render_pixels: int = 95_000_000,
    min_text_chars: int = 1,
    image_coverage_threshold: float = 0.25,
) -> PDFDocument:
    if not data.startswith(b"%PDF-"):
        raise DomainError("PDF_INVALID", "文件不是有效 PDF")
    try:
        document = pymupdf.open(stream=data, filetype="pdf")
    except Exception:
        raise DomainError("PDF_INVALID", "PDF 文件损坏或无法读取") from None
    try:
        if document.needs_pass:
            raise DomainError("PDF_ENCRYPTED", "加密 PDF 无法识别，请上传可读取版本")
        page_count = document.page_count
        if page_count < 1:
            raise DomainError("PDF_EMPTY", "PDF 没有可识别页面")
        if page_count > max_pages:
            raise DomainError("PDF_PAGE_LIMIT", f"PDF 页数超过允许上限 {max_pages} 页")
        pages = []
        for index in range(page_count):
            page = document.load_page(index)
            page_number = index + 1
            blocks = _text_blocks(page, page_number)
            text = "\n".join(block.text for block in blocks)
            text_chars = len(_normalized_text(text))
            image_coverage = _image_coverage(page)
            needs_ocr = (
                not blocks
                or text_chars < min_text_chars
                or image_coverage >= image_coverage_threshold
            )
            image = None
            if needs_ocr:
                if max_render_pixels < 1:
                    raise DomainError("OCR_RENDER_LIMIT_INVALID", "OCR 渲染像素限制无效")
                target_scale = render_dpi / 72
                safe_scale = math.sqrt(
                    max_render_pixels
                    / ((float(page.rect.width) + 1) * (float(page.rect.height) + 1))
                )
                scale = min(target_scale, safe_scale)
                image = page.get_pixmap(
                    matrix=pymupdf.Matrix(scale, scale), alpha=False
                ).tobytes("png")
            pages.append(PDFPage(
                page_number=page_number,
                text=text,
                needs_ocr=needs_ocr,
                image_png=image,
                blocks=blocks,
                image_coverage=image_coverage,
                width=float(page.rect.width),
                height=float(page.rect.height),
            ))
        return PDFDocument(encrypted=False, page_count=page_count, pages=tuple(pages))
    finally:
        document.close()
