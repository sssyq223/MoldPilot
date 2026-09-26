from io import BytesIO
from types import SimpleNamespace

import pymupdf
import pytest

from domain_packs.mold.erp.commercial.pdf_analysis import inspect_document
from domain_packs.mold.ports.errors import DomainError


def _png():
    document = pymupdf.open()
    page = document.new_page(width=240, height=120)
    page.insert_text((20, 60), "ENGINEERING CONTACT P260001", fontsize=14)
    pdf = document.tobytes()
    document.close()
    source = pymupdf.open(stream=pdf, filetype="pdf")
    image = source.load_page(0).get_pixmap().tobytes("png")
    source.close()
    return image


def test_image_document_is_normalized_to_one_ocr_page():
    result = inspect_document(_png(), "image/png", max_pages=5, min_text_chars=40)

    assert result.page_count == 1
    assert result.pages[0].needs_ocr is True
    assert result.pages[0].image_png


def test_unsupported_document_media_type_is_rejected():
    with pytest.raises(DomainError) as error:
        inspect_document(b"not-a-doc", "application/octet-stream", max_pages=5)
    assert error.value.code == "DOCUMENT_MEDIA_TYPE_UNSUPPORTED"
