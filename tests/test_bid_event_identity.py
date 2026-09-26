from types import SimpleNamespace
from uuid import uuid4

import pytest

from domain_packs.mold.erp.commercial import document_workflow


class FakeDb:
    def __init__(self):
        self.events = []
        self.outbox = []

    def add(self, row):
        if row.__class__.__name__ == "AuditEvent":
            row.id = "audit-1"
            self.events.append(row)
        else:
            row.id = "outbox-1"
            self.outbox.append(row)

    def flush(self):
        pass

    def scalar(self, query):
        return None if not self.events else self.events[-1]


def test_confirmed_bid_event_requires_classifier_version(monkeypatch):
    db = FakeDb()

    def record_both(db, user, action, resource_id, detail):
        audit = SimpleNamespace(id="audit-1", action=action, resource_id=resource_id, detail=detail)
        outbox = SimpleNamespace(id="outbox-1")
        db.events.append(audit)
        db.outbox.append(outbox)
        return outbox

    monkeypatch.setattr(document_workflow, "record", record_both)

    from domain_packs.mold.ports.errors import DomainError
    with pytest.raises(DomainError) as error:
        document_workflow.record_confirmed_bid_event(
            db, SimpleNamespace(id="user-1"), SimpleNamespace(id="intake-1"),
            SimpleNamespace(file_id="file-1"), "classification-1",
            {"document_type": "BID_NOTICE"}, "BID_NOTICE", str(uuid4()),
        )

    assert error.value.code == "BID_EVENT_METADATA_MISSING"
    assert "classifier_version" in error.value.message


def test_confirmed_bid_event_returns_audit_event_identity(monkeypatch):
    db = FakeDb()
    monkeypatch.setattr(document_workflow, "record", lambda db, *args, **kwargs: (
        db.add(SimpleNamespace(__class__=None))
    ))
    # use the real event contract instead of the helper's internal fake return
    def record_both(db, user, action, resource_id, detail):
        audit = SimpleNamespace(id="audit-1", action=action, resource_id=resource_id, detail=detail)
        outbox = SimpleNamespace(id="outbox-1")
        db.events.append(audit)
        db.outbox.append(outbox)
        return outbox
    monkeypatch.setattr(document_workflow, "record", record_both)
    intake = SimpleNamespace(id="intake-1")
    intake_file = SimpleNamespace(file_id="file-1")
    result = document_workflow.record_confirmed_bid_event(
        db, SimpleNamespace(id="user-1"), intake, intake_file,
        "classification-1", {"document_type": "BID_NOTICE", "classifier_version": "bid-classifier-v1"}, "BID_NOTICE", str(uuid4()),
    )

    assert result.id == "audit-1"
