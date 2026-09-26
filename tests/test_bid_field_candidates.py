"""中标邮件字段必须有同页文字来源，且保留多个候选，不产生业务事实。"""
import json
from decimal import Decimal

import httpx
import pytest

from domain_packs.mold.erp.commercial.ocr_provider import DocumentTextProvider, RecognizedDocument, RecognizedPage
from domain_packs.mold.erp.commercial.pdf_analysis import PageTextBlock
from domain_packs.mold.ports.errors import DomainError
from test_contract_ocr import _document_model_settings, _openai_response, model_stream_transport  # noqa: F401


def classify(fields):
    document = RecognizedDocument((RecognizedPage(1, (
        PageTextBlock('p1-t0001', '恭喜贵司中标项目 甲项目，外部订单 PO-001，客户交期 2026-10-30', (0, 0, 100, 20), 'TEXT_LAYER', None),
        PageTextBlock('p1-t0002', '项目 乙项目，客户模具号 CLIENT-02，中标金额 98000 CNY', (0, 20, 100, 40), 'TEXT_LAYER', None),
    )),))
    payload = {'document_type': 'BID_NOTICE', 'event_type': 'BID_WON', 'confidence': 0.95,
               'evidence': [{'page': 1, 'text': '恭喜贵司中标项目', 'rule': 'AWARD_RESULT'}],
               'bid_fields': fields}
    provider = DocumentTextProvider(_document_model_settings(), transport=httpx.MockTransport(
        lambda request: httpx.Response(200, json=_openai_response(payload))))
    return provider.classify(document)


def test_classifier_preserves_multiple_sourced_bid_fields():
    result = classify([
        {'field_key': 'project_name', 'value': '甲项目', 'confidence': 0.95, 'source_block_ids': ['p1-t0001']},
        {'field_key': 'project_name', 'value': '乙项目', 'confidence': 0.95, 'source_block_ids': ['p1-t0002']},
        {'field_key': 'external_order_number', 'value': 'PO-001', 'confidence': 0.96, 'source_block_ids': ['p1-t0001']},
    ])
    assert [row['value'] for row in result.bid_fields] == ['甲项目', '乙项目', 'PO-001']
    assert result.bid_fields[0]['page_number'] == 1
    assert result.bid_fields[0]['source_text'].startswith('恭喜贵司')


@pytest.mark.parametrize('field', [
    {'field_key': 'project_name', 'value': '甲项目', 'source_block_ids': ['p9-fake'], 'confidence': 0.9},
    {'field_key': 'project_name', 'value': '虚构项目', 'source_block_ids': ['p1-t0001'], 'confidence': 0.9},
    {'field_key': 'project_id', 'value': 'fake-id', 'source_block_ids': ['p1-t0001'], 'confidence': 0.9},
    {'field_key': 'amount', 'value': '98000', 'source_block_ids': ['p1-t0002'], 'confidence': 0.9},
    {'field_key': 'project_name', 'value': {'nested': '甲项目'}, 'source_block_ids': ['p1-t0001'], 'confidence': 0.9},
])
def test_invalid_or_untraceable_bid_field_is_rejected(field):
    with pytest.raises(DomainError):
        classify([field])
