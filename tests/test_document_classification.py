from decimal import Decimal
from types import SimpleNamespace

from domain_packs.mold.erp.commercial.document_classification import (
    classify_rules, merge_model_classification,
)


def _document(*pages):
    return SimpleNamespace(pages=tuple(
        SimpleNamespace(page_number=index, blocks=(SimpleNamespace(text=text),))
        for index, text in enumerate(pages, 1)
    ))


def test_bid_requires_award_and_project_context_and_source_metadata():
    result = classify_rules(_document("恭喜贵司中标项目", "客户：甲方，项目号 P-001，交期 30 天"), source_metadata={"kind": "EMAIL"})
    assert result.document_type == "BID_NOTICE"
    assert result.event_type == "BID_WON"
    assert {item.rule for item in result.evidence} >= {"AWARD_RESULT", "PROJECT_CONTEXT"}
    merged = merge_model_classification(result, {"document_type": "BID_NOTICE", "event_type": "BID_WON", "confidence": 0.99}, classifier_version="bid-classifier-v1", source_metadata={"kind": "EMAIL"})
    assert merged["decision"] == "CONFIRMED"


def test_bid_evidence_on_page_four_is_not_lost():
    result = classify_rules(_document("普通封面", "目录", "附件说明", "恭喜贵司中标项目 P-009，客户：甲方"), source_metadata={"kind": "CUSTOMER_PLATFORM"})
    assert result.document_type == "BID_NOTICE"
    assert {item.page for item in result.evidence} >= {4}


def test_bid_without_source_metadata_stays_in_review():
    result = classify_rules(_document("恭喜贵司中标项目 P-010，客户：甲方"))
    merged = merge_model_classification(result, {"document_type": "BID_NOTICE", "event_type": "BID_WON", "confidence": 0.99}, classifier_version="v1")
    assert merged["decision"] == "NEEDS_REVIEW"
    assert any(item["type"] == "SOURCE_METADATA_MISSING" for item in merged["conflicts"])


def test_contract_with_award_word_is_not_automatically_bid():
    result = classify_rules(_document("销售合同 合同编号 C-001", "甲方：客户，乙方：我司，付款条款：30%"))
    assert result.document_type == "SALES_CONTRACT"
    assert any(item.type == "MIXED_DOCUMENT" for item in result.conflicts) is False
    merged = merge_model_classification(result, {"document_type": "SALES_CONTRACT", "event_type": "CONTRACT_SIGNED", "confidence": 0.98}, classifier_version="v1")
    assert merged["decision"] == "CONFIRMED"


def test_mixed_bid_and_contract_requires_review():
    result = classify_rules(_document("恭喜贵司中标项目 P-001", "客户：甲方，合同编号 C-001，付款条款：30%"), source_metadata={"kind": "EMAIL"})
    merged = merge_model_classification(result, {"document_type": "BID_NOTICE", "event_type": "BID_WON", "confidence": 0.99}, classifier_version="v1", source_metadata={"kind": "EMAIL"})
    assert merged["decision"] == "NEEDS_REVIEW"
    assert any(item["type"] == "MIXED_DOCUMENT" for item in merged["conflicts"])
