from sqlalchemy import select

from app.models import ApprovalAction, ApprovalInstance, ApprovalSeat, AuditEvent, User
from conftest import draft, sign_in, submit
from test_core import confirm_decision, decision_payload


def publish_add_sign_flow(client, ids, *, timings=("PRE", "POST"), reject_rules=None):
    config = {
        "business_type": "purchase_request",
        "nodes": [{
            "key": "review",
            "name": "可加签审批",
            "users": [ids["reviewer"]],
            "mode": "ALL",
            "reject_rules": reject_rules or [],
            "add_sign_policy": {"timings": list(timings), "users": [ids["admin"]]},
        }],
    }
    response = client.post("/api/workflows", json={
        "process_key": "add_sign_test",
        "name": "审批加签合成测试",
        "config": config,
    })
    assert response.status_code == 200, response.text
    definition_id = response.json()["id"]
    response = client.post(f"/api/workflows/{definition_id}/publish")
    assert response.status_code == 200, response.text
    return definition_id


def add_sign_payload(detail, target_user_id, timing, reason="增加独立复核，核对本轮审批依据"):
    return {
        "instance_id": detail["id"],
        "seat_id": detail["seat_id"],
        "seat_version": detail["seat_version"],
        "version": detail["version"],
        "snapshot_hash": detail["snapshot_hash"],
        "target_user_id": target_user_id,
        "timing": timing,
        "reason": reason,
    }


def confirm_add_sign(client, detail, target_user_id, timing):
    response = client.post(
        "/api/approval-seat-additions/intent",
        json=add_sign_payload(detail, target_user_id, timing),
    )
    assert response.status_code == 200, response.text
    intent = response.json()
    confirmed = client.post(
        f"/api/human-actions/{intent['id']}/confirm",
        json={"challenge": intent["challenge"]},
    )
    assert confirmed.status_code == 200, confirmed.text
    return intent, confirmed.json()


def start_add_sign_approval(client, ids, definition_id):
    sign_in(client, "test_buyer")
    return submit(client, {**ids, "definition": definition_id}, draft(client, ids))


def test_pre_add_sign_blocks_original_then_returns_same_stage(client, data):
    ids, factory = data
    sign_in(client)
    definition_id = publish_add_sign_flow(client, ids)
    instance_id = start_add_sign_approval(client, ids, definition_id)

    sign_in(client, "test_reviewer")
    before = client.get(f"/api/approvals/{instance_id}").json()
    assert before["add_sign_allowed"] is True
    assert before["add_sign_timings"] == ["PRE", "POST"]
    assert [item["id"] for item in before["add_sign_options"]] == [ids["admin"]]
    _, receipt = confirm_add_sign(client, before, ids["admin"], "PRE")
    assert receipt["timing"] == "PRE"
    assert client.get("/api/approvals").json() == []

    sign_in(client)
    added_detail = client.get(f"/api/approvals/{instance_id}").json()
    assert added_detail["seat_id"] == receipt["added_seat_id"]
    _, response = confirm_decision(client, instance_id)
    assert response.json()["status"] == "RUNNING"

    sign_in(client, "test_reviewer")
    resumed = client.get(f"/api/approvals/{instance_id}").json()
    assert resumed["seat_id"] == before["seat_id"]
    # Waiting for the pre-signer and becoming actionable again are two
    # separately versioned seat-state transitions.
    assert resumed["seat_version"] == before["seat_version"] + 2
    assert resumed["add_sign_history"][0]["timing"] == "PRE"
    _, response = confirm_decision(client, instance_id)
    assert response.json()["status"] == "COMPLETED"

    with factory() as db:
        seats = list(db.scalars(select(ApprovalSeat).where(
            ApprovalSeat.instance_id == instance_id
        ).order_by(ApprovalSeat.countersign_sequence)))
        assert len(seats) == 2
        assert seats[0].parent_seat_id is None and seats[0].status == "APPROVE"
        assert seats[1].parent_seat_id == seats[0].id
        assert seats[1].countersign_timing == "PRE" and seats[1].status == "APPROVE"
        assert seats[1].countersign_initiated_by == ids["reviewer"]
        assert db.scalar(select(AuditEvent).where(
            AuditEvent.resource_id == instance_id,
            AuditEvent.action == "approval.seat.added",
        )).detail["added_seat_id"] == seats[1].id
        assert len(list(db.scalars(select(ApprovalAction).where(
            ApprovalAction.instance_id == instance_id
        )))) == 2


def test_post_add_sign_waits_until_original_approves(client, data):
    ids, factory = data
    sign_in(client)
    definition_id = publish_add_sign_flow(client, ids, timings=("POST",))
    instance_id = start_add_sign_approval(client, ids, definition_id)

    sign_in(client, "test_reviewer")
    original = client.get(f"/api/approvals/{instance_id}").json()
    _, receipt = confirm_add_sign(client, original, ids["admin"], "POST")
    assert receipt["timing"] == "POST"
    sign_in(client)
    assert client.get("/api/approvals").json() == []

    sign_in(client, "test_reviewer")
    _, response = confirm_decision(client, instance_id)
    assert response.json()["status"] == "RUNNING"
    assert client.get("/api/approvals").json() == []

    sign_in(client)
    added = client.get(f"/api/approvals/{instance_id}").json()
    assert added["seat_id"] == receipt["added_seat_id"]
    _, response = confirm_decision(client, instance_id)
    assert response.json()["status"] == "COMPLETED"
    with factory() as db:
        seat = db.get(ApprovalSeat, receipt["added_seat_id"])
        assert seat.countersign_timing == "POST" and seat.status == "APPROVE"


def test_add_sign_confirmation_revalidates_target_and_stage(client, data):
    ids, factory = data
    sign_in(client)
    definition_id = publish_add_sign_flow(client, ids)
    instance_id = start_add_sign_approval(client, ids, definition_id)
    sign_in(client, "test_reviewer")
    detail = client.get(f"/api/approvals/{instance_id}").json()
    intent_response = client.post(
        "/api/approval-seat-additions/intent",
        json=add_sign_payload(detail, ids["admin"], "PRE"),
    )
    assert intent_response.status_code == 200, intent_response.text
    intent = intent_response.json()
    with factory.begin() as db:
        db.get(User, ids["admin"]).active = False
    response = client.post(
        f"/api/human-actions/{intent['id']}/confirm",
        json={"challenge": intent["challenge"]},
    )
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "ADD_SIGN_TARGET_INVALID"
    with factory() as db:
        seats = list(db.scalars(select(ApprovalSeat).where(ApprovalSeat.instance_id == instance_id)))
        assert len(seats) == 1 and seats[0].status == "PENDING"


def test_add_sign_cannot_bypass_mandatory_rejection(client, data):
    ids, _ = data
    sign_in(client)
    definition_id = publish_add_sign_flow(client, ids, reject_rules=[{
        "condition": {"field": "quantity", "op": "gt", "value": "0"},
        "reason": "测试资料命中必须驳回",
    }])
    instance_id = start_add_sign_approval(client, ids, definition_id)
    sign_in(client, "test_reviewer")
    detail = client.get(f"/api/approvals/{instance_id}").json()
    assert detail["allowed_actions"] == ["REJECT"]
    assert detail["add_sign_allowed"] is False
    response = client.post(
        "/api/approval-seat-additions/intent",
        json=add_sign_payload(detail, ids["admin"], "PRE"),
    )
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "ADD_SIGN_TARGET_INVALID"


def test_confirmed_add_sign_invalidates_an_older_decision_intent(client, data):
    ids, factory = data
    sign_in(client)
    definition_id = publish_add_sign_flow(client, ids)
    instance_id = start_add_sign_approval(client, ids, definition_id)

    sign_in(client, "test_reviewer")
    detail = client.get(f"/api/approvals/{instance_id}").json()
    decision_intent = client.post(
        "/api/approvals/decision-intent",
        json=decision_payload(detail),
    ).json()
    confirm_add_sign(client, detail, ids["admin"], "PRE")

    stale_decision = client.post(
        f"/api/human-actions/{decision_intent['id']}/confirm",
        json={"challenge": decision_intent["challenge"]},
    )
    assert stale_decision.status_code == 409
    assert stale_decision.json()["error"]["code"] == "VERSION_CONFLICT"
    with factory() as db:
        assert db.scalar(select(ApprovalAction).where(
            ApprovalAction.instance_id == instance_id
        )) is None
        seats = list(db.scalars(select(ApprovalSeat).where(
            ApprovalSeat.instance_id == instance_id
        ).order_by(ApprovalSeat.countersign_sequence)))
        assert [seat.status for seat in seats] == ["WAITING_COUNTERSIGN", "PENDING"]
