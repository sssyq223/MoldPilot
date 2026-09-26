from decimal import Decimal
from types import SimpleNamespace

import pytest

from domain_packs.mold.erp.commercial.document_classification import classify_rules, merge_model_classification
from domain_packs.mold.erp.commercial.ocr_provider import (
    DocumentTextProvider,
    RecognizedDocument,
    RecognizedPage,
)


def _document(*texts):
    return RecognizedDocument(pages=tuple(
        RecognizedPage(
            page_number=index,
            blocks=(SimpleNamespace(block_id=f"p{index}b1", text=text, bbox=(0, 0, 10, 10), source="TEXT_LAYER", confidence=Decimal("1")),),
        ) for index, text in enumerate(texts, 1)
    ))


def test_rule_classifier_recognizes_engineering_contact_candidate():
    result = classify_rules(_document(
        "工程联络单",
        "项目编号 P260001 客户：甲方 模具号：M260001 当前环节：试模",
        "问题来源：客户设变 变更类别：设变 紧急程度：一般",
    ))

    assert result.document_type == "ENGINEERING_CONTACT"
    assert result.event_type == "ENGINEERING_CONTACT"
    assert result.confidence >= Decimal("0.80")
    assert any(item.rule == "ENGINEERING_CONTACT_TITLE" for item in result.evidence)


def test_engineering_contact_conflict_requires_human_review():
    result = classify_rules(_document(
        "工程联络单",
        "销售合同 合同编号 C-001 甲方：客户 乙方：我司 付款条款：30%",
    ))

    assert result.document_type == "ENGINEERING_CONTACT"
    assert result.conflicts
    merged = merge_model_classification(
        result,
        {"document_type": "ENGINEERING_CONTACT", "event_type": "ENGINEERING_CONTACT", "confidence": 0.99},
        classifier_version="engineering-contact-v1",
    )
    assert merged["decision"] == "NEEDS_REVIEW"
    assert merged["needs_human_confirmation"] is True


def test_classifier_rejects_evidence_not_present_in_page_text(monkeypatch):
    provider = object.__new__(DocumentTextProvider)
    provider.adapter = SimpleNamespace(model='test-model')
    result = {
        'document_type': 'OTHER', 'event_type': 'UNKNOWN', 'confidence': 0.20,
        'evidence': [{'page': 1, 'text': '模型编造的证据', 'rule': 'FAKE_RULE'}],
        'conflicts': [], 'extracted': {},
        'classifier_version': 'document-classifier-v1', 'needs_human_confirmation': True,
    }
    monkeypatch.setattr(provider, '_request', lambda *args, **kwargs: kwargs['validate'](result))
    with pytest.raises(Exception, match='文档证据'):
        provider.classify(_document('页面上的真实文字'))


def test_classifier_rejects_contact_field_without_source_blocks(monkeypatch):
    provider = object.__new__(DocumentTextProvider)
    provider.adapter = SimpleNamespace(model='test-model')
    result = {
        'document_type': 'ENGINEERING_CONTACT', 'event_type': 'ENGINEERING_CONTACT', 'confidence': 0.96,
        'evidence': [{'page': 1, 'text': '工程联络单', 'rule': 'ENGINEERING_CONTACT_TITLE'}],
        'conflicts': [], 'extracted': {'project_ref': 'P260001'},
        'classifier_version': 'engineering-contact-v1', 'needs_human_confirmation': True,
    }
    monkeypatch.setattr(provider, '_request', lambda *args, **kwargs: kwargs['validate'](result))
    with pytest.raises(Exception, match='工程联络字段来源'):
        provider.classify(_document('工程联络单 项目编号 P260001'))


def test_classifier_model_accepts_engineering_contact_and_extracts_whitelisted_fields(monkeypatch):
    provider = object.__new__(DocumentTextProvider)
    provider.adapter = SimpleNamespace(model="test-model")

    def field(value):
        return {"value": value, "confidence": 0.95, "source_block_ids": ["p1b1"]}

    result = {
        "document_type": "ENGINEERING_CONTACT",
        "event_type": "ENGINEERING_CONTACT",
        "confidence": 0.96,
        "evidence": [{"page": 1, "text": "工程联络单", "rule": "ENGINEERING_CONTACT_TITLE"}],
        "conflicts": [],
        "extracted": {
            "project_ref": field("P260001"),
            "customer_ref": field("CUST-001"),
            "customer_name": field("甲方"),
            "mold_number": field("M260001"),
            "product_ref": field("PART-001"),
            "title": field("试模尺寸异常"),
            "description": field("首件尺寸超差，需要工程确认。"),
            "current_stage": field("试模"),
            "problem_source": field("QUALITY_ISSUE"),
            "change_type": field("EXCEPTION"),
            "urgency": field("NORMAL"),
            "category": field("hardware"),
            "mode": field("ONLINE"),
            "application_date": field("2026-09-24"),
        },
        "classifier_version": "engineering-contact-v1",
        "needs_human_confirmation": True,
    }
    monkeypatch.setattr(provider, "_request", lambda *args, **kwargs: kwargs["validate"](result))

    recognized = _document("工程联络单 P260001 CUST-001 甲方 M260001 PART-001 试模尺寸异常 首件尺寸超差，需要工程确认。 试模 QUALITY_ISSUE EXCEPTION NORMAL hardware ONLINE 2026-09-24")
    classified = provider.classify(recognized)

    assert classified.document_type == "ENGINEERING_CONTACT"
    assert classified.extracted["project_ref"]["value"] == "P260001"
    assert classified.needs_human_confirmation is True
