from sqlalchemy import select

from app.models import ApprovalAction, ApprovalInstance, ApprovalSeat, AuditEvent, HumanIntent, PurchaseRequest
from conftest import draft, sign_in, submit
from test_core import confirm_decision


def withdrawal_payload(detail, reason="申请资料需要由本人补充后重新提交"):
    return {
        "instance_id": detail["id"],
        "version": detail["version"],
        "snapshot_hash": detail["snapshot_hash"],
        "reason": reason,
    }


def test_applicant_withdraws_running_round_without_erasing_approval_history(client, data):
    ids, factory = data
    sign_in(client, "test_buyer")
    request_id = draft(client, ids)
    instance_id = submit(client, ids, request_id)

    initiated = client.get("/api/approvals/initiated")
    assert initiated.status_code == 200, initiated.text
    assert [item["id"] for item in initiated.json()] == [instance_id]
    applicant_detail = initiated.json()[0]
    assert applicant_detail["withdraw_allowed"] is True
    assert applicant_detail["withdrawal"] is None

    prepared = client.post(
        "/api/approvals/withdraw-intent",
        json=withdrawal_payload(applicant_detail),
    )
    assert prepared.status_code == 200, prepared.text
    intent = prepared.json()
    with factory() as db:
        assert db.get(ApprovalInstance, instance_id).status == "RUNNING"
        assert db.get(PurchaseRequest, request_id).status == "SUBMITTED"
        assert db.get(HumanIntent, intent["id"]).receipt is None

    sign_in(client, "test_reviewer")
    _, approval = confirm_decision(client, instance_id)
    assert approval.json()["status"] == "RUNNING"

    sign_in(client, "test_buyer")
    stale = client.post(
        f"/api/human-actions/{intent['id']}/confirm",
        json={"challenge": intent["challenge"]},
    )
    assert stale.status_code == 409
    assert stale.json()["error"]["code"] == "VERSION_CONFLICT"

    current = client.get(f"/api/approvals/{instance_id}").json()
    assert current["withdraw_allowed"] is True
    replacement = client.post(
        "/api/approvals/withdraw-intent",
        json=withdrawal_payload(current, "首节点通过后发现申请数量需重新核对"),
    )
    assert replacement.status_code == 200, replacement.text
    withdrawal = replacement.json()
    confirmed = client.post(
        f"/api/human-actions/{withdrawal['id']}/confirm",
        json={"challenge": withdrawal["challenge"]},
    )
    assert confirmed.status_code == 200, confirmed.text
    assert confirmed.json() == {
        "instance_id": instance_id,
        "status": "CANCELLED",
        "business_status": "DRAFT",
        "business_revision": 2,
        "cancelled_seats": 1,
    }
    repeated = client.post(
        f"/api/human-actions/{withdrawal['id']}/confirm",
        json={"challenge": withdrawal["challenge"]},
    )
    assert repeated.json() == confirmed.json()

    with factory() as db:
        instance = db.get(ApprovalInstance, instance_id)
        request = db.get(PurchaseRequest, request_id)
        seats = list(db.scalars(select(ApprovalSeat).where(ApprovalSeat.instance_id == instance_id)))
        actions = list(db.scalars(select(ApprovalAction).where(ApprovalAction.instance_id == instance_id)))
        event = db.scalar(select(AuditEvent).where(
            AuditEvent.resource_id == instance_id,
            AuditEvent.action == "approval.withdrawn",
        ))
        assert instance.status == "CANCELLED"
        assert request.status == "DRAFT"
        assert request.revision == 2
        assert [action.decision for action in actions] == ["APPROVE"]
        assert any(seat.status == "APPROVE" for seat in seats)
        assert any(seat.status == "CANCELLED" for seat in seats)
        assert event.detail["reason"] == "首节点通过后发现申请数量需重新核对"
        assert event.detail["withdrawn_revision"] == 1
        assert event.detail["draft_revision"] == 2

    completed = client.get(f"/api/approvals/{instance_id}").json()
    assert completed["withdraw_allowed"] is False
    assert completed["withdrawal"]["reason"] == "首节点通过后发现申请数量需重新核对"
    assert completed["history"][0]["decision"] == "APPROVE"


def test_non_applicant_and_terminal_round_cannot_be_withdrawn(client, data):
    ids, _ = data
    sign_in(client, "test_buyer")
    instance_id = submit(client, ids, draft(client, ids))

    sign_in(client, "test_reviewer")
    reviewer_detail = client.get(f"/api/approvals/{instance_id}").json()
    assert reviewer_detail["withdraw_allowed"] is False
    denied = client.post(
        "/api/approvals/withdraw-intent",
        json=withdrawal_payload(reviewer_detail),
    )
    assert denied.status_code == 409
    assert denied.json()["error"]["code"] == "WITHDRAW_NOT_ALLOWED"

    confirm_decision(client, instance_id, "REJECT")
    sign_in(client, "test_buyer")
    terminal = client.get(f"/api/approvals/{instance_id}").json()
    assert terminal["withdraw_allowed"] is False
    denied = client.post(
        "/api/approvals/withdraw-intent",
        json=withdrawal_payload(terminal),
    )
    assert denied.status_code == 409
    assert denied.json()["error"]["code"] == "WITHDRAW_NOT_ALLOWED"
