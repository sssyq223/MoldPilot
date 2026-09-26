import pytest

from domain_packs.mold.erp.commercial.bid_start_workflow import (
    confirmed_bid_event_snapshot,
)
from domain_packs.mold.ports.errors import DomainError


def test_confirmed_bid_event_snapshot_keeps_only_safe_classification_metadata():
    snapshot = confirmed_bid_event_snapshot(
        {
            "action": "bid_notice.confirmed",
            "resource_id": "file-1",
            "detail": {
                "file_version_id": "file-1",
                "inbound_record_id": "intake-1",
                "classification_id": "audit-1",
                "confirmed_document_type": "BID_NOTICE",
                "classifier_version": "bid-classifier-v1",
                "operation_id": "operation-1",
                "evidence_snapshot": {
                    "document_type": "BID_NOTICE",
                    "event_type": "BID_WON",
                    "confidence": 0.91,
                    "decision": "NEEDS_REVIEW",
                    "classifier_version": "bid-classifier-v1",
                    "evidence": [{"page": 1, "text": "不得保存的原文"}],
                    "extracted": {"customer_name": "不得保存的客户"},
                },
            },
        }
    )
    assert snapshot == {
        "classification_id": "audit-1",
        "confirmed_document_type": "BID_NOTICE",
        "document_type": "BID_NOTICE",
        "event_type": "BID_WON",
        "confidence": 0.91,
        "decision": "NEEDS_REVIEW",
        "classifier_version": "bid-classifier-v1",
    }
    assert "evidence" not in snapshot
    assert "extracted" not in snapshot


def test_confirmed_bid_event_accepts_legacy_nested_classifier_version():
    snapshot = confirmed_bid_event_snapshot({
        "action": "bid_notice.confirmed",
        "resource_id": "file-1",
        "detail": {
            "file_version_id": "file-1",
            "inbound_record_id": "intake-1",
            "classification_id": "audit-1",
            "confirmed_document_type": "BID_NOTICE",
            "operation_id": "operation-1",
            "evidence_snapshot": {
                "document_type": "BID_NOTICE",
                "classifier_version": "bid-classifier-v1",
            },
        },
    })
    assert snapshot["classifier_version"] == "bid-classifier-v1"


def test_confirmed_bid_event_requires_complete_event_metadata():
    with pytest.raises(DomainError) as error:
        confirmed_bid_event_snapshot({
            "action": "bid_notice.confirmed",
            "resource_id": "file-1",
            "detail": {
                "confirmed_document_type": "BID_NOTICE",
                "evidence_snapshot": {},
            },
        })

    assert error.value.code == "BID_EVENT_METADATA_MISSING"
    assert "file_version_id" in error.value.message
    assert "classification_id" in error.value.message
    assert "classifier_version" in error.value.message
    assert "operation_id" in error.value.message


def test_confirmed_bid_event_rejects_file_version_mismatch():
    with pytest.raises(DomainError) as error:
        confirmed_bid_event_snapshot({
            "action": "bid_notice.confirmed",
            "resource_id": "file-1",
            "detail": {
                "file_version_id": "file-2",
                "classification_id": "audit-1",
                "inbound_record_id": "intake-1",
                "confirmed_document_type": "BID_NOTICE",
                "classifier_version": "bid-classifier-v1",
                "operation_id": "operation-1",
                "evidence_snapshot": {"document_type": "BID_NOTICE"},
            },
        })

    assert error.value.code == "BID_SOURCE_MISMATCH"


def test_unconfirmed_or_non_bid_event_cannot_create_match_snapshot():
    with pytest.raises(DomainError) as error:
        confirmed_bid_event_snapshot({"action": "document.type.confirmed", "detail": {}})
    assert error.value.code == "BID_CONFIRMATION_REQUIRED"

    with pytest.raises(DomainError) as error:
        confirmed_bid_event_snapshot({
            "action": "bid_notice.confirmed",
            "detail": {"confirmed_document_type": "SALES_CONTRACT"},
        })
    assert error.value.code == "BID_DOCUMENT_REQUIRED"
