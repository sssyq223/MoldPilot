from types import SimpleNamespace

from domain_packs.mold.erp.commercial import bid_start_workflow


class FakeDb:
    def __init__(self, event, intake_file, existing=None):
        self.event = event
        self.values = iter([existing, intake_file])
        self.added = []

    def get(self, model, key):
        return self.event

    def scalar(self, query):
        return next(self.values)

    def add(self, row):
        self.added.append(row)

    def flush(self):
        self.added[0].id = "match-1"


def _event():
    return SimpleNamespace(
        id="event-1",
        action="bid_notice.confirmed",
        resource_id="file-1",
        detail={
            "file_version_id": "file-1",
            "inbound_record_id": "intake-1",
            "classification_id": "classification-1",
            "confirmed_document_type": "BID_NOTICE",
            "classifier_version": "bid-classifier-v1",
            "operation_id": "operation-1",
            "evidence_snapshot": {
                "document_type": "BID_NOTICE",
                "event_type": "BID_WON",
                "confidence": 0.9,
                "classifier_version": "bid-classifier-v1",
            },
        },
    )


def test_consume_confirmed_bid_notice_creates_only_pending_match(monkeypatch):
    db = FakeDb(_event(), SimpleNamespace(intake_id="intake-1", file_id="file-1", confirmed_type="BID_NOTICE"))
    monkeypatch.setattr(
        bid_start_workflow,
        "host_ports",
        lambda: SimpleNamespace(uploaded_file=lambda db, user, file_id: object()),
    )

    row = bid_start_workflow.consume_confirmed_bid_notice(db, SimpleNamespace(id="u1"), "event-1")

    assert row.id == "match-1"
    assert row.status == "PENDING_MATCH"
    assert row.project_id is None
    assert row.bid_intake_revision_id is None
    assert row.evidence_snapshot == {
        "classification_id": "classification-1",
        "confirmed_document_type": "BID_NOTICE",
        "document_type": "BID_NOTICE",
        "event_type": "BID_WON",
        "confidence": 0.9,
        "classifier_version": "bid-classifier-v1",
    }


def test_consume_confirmed_bid_notice_replay_returns_existing_match(monkeypatch):
    existing = SimpleNamespace(id="match-1", status="PENDING_MATCH")
    db = FakeDb(_event(), None, existing=existing)
    monkeypatch.setattr(
        bid_start_workflow,
        "host_ports",
        lambda: SimpleNamespace(uploaded_file=lambda db, user, file_id: object()),
    )

    row = bid_start_workflow.consume_confirmed_bid_notice(db, SimpleNamespace(id="u1"), "event-1")

    assert row is existing
    assert db.added == []
