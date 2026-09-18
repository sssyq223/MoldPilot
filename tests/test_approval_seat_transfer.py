from sqlalchemy import select

from app.models import ApprovalInstance, ApprovalSeat, AuditEvent, HumanIntent, User
from conftest import draft, sign_in, submit


def publish_transfer_flow(client, ids):
    config = {
        "business_type": "purchase_request",
        "nodes": [
            {
                "key": "transferable_review",
                "name": "可转交审批",
                "users": [ids["reviewer"]],
                "mode": "ALL",
                "reject_rules": [],
                "allow_transfer": True,
            }
        ],
    }
    response = client.post("/api/workflows", json={
        "process_key": "transfer_test",
        "name": "审批转交合成测试",
        "config": config,
    })
    assert response.status_code == 200, response.text
    definition_id = response.json()["id"]
    response = client.post(f"/api/workflows/{definition_id}/publish")
    assert response.status_code == 200, response.text
    return definition_id


def transfer_payload(detail, target_user_id, reason="原审批人本轮无法处理，转交具备权限的授权人"):
    return {
        "instance_id": detail["id"],
        "seat_id": detail["seat_id"],
        "seat_version": detail["seat_version"],
        "version": detail["version"],
        "snapshot_hash": detail["snapshot_hash"],
        "target_user_id": target_user_id,
        "reason": reason,
    }


def test_transfer_requires_enabled_node_and_qualified_target(client, data):
    ids, _ = data
    sign_in(client, "test_buyer")
    ordinary_instance = submit(client, ids, draft(client, ids))
    sign_in(client, "test_reviewer")
    ordinary = client.get(f"/api/approvals/{ordinary_instance}").json()
    assert ordinary["transfer_allowed"] is False
    assert ordinary["transfer_options"] == []
    blocked = client.post("/api/approval-seat-transfers/intent", json=transfer_payload(ordinary, ids["admin"]))
    assert blocked.status_code == 409
    assert blocked.json()["error"]["code"] == "TRANSFER_TARGET_INVALID"

    sign_in(client)
    definition_id = publish_transfer_flow(client, ids)
    sign_in(client, "test_buyer")
    instance_id = submit(client, {**ids, "definition": definition_id}, draft(client, ids))
    sign_in(client, "test_reviewer")
    detail = client.get(f"/api/approvals/{instance_id}").json()
    assert detail["transfer_allowed"] is True
    assert [item["id"] for item in detail["transfer_options"]] == [ids["admin"]]
    invalid = client.post("/api/approval-seat-transfers/intent", json=transfer_payload(detail, ids["buyer"]))
    assert invalid.status_code == 409
    assert invalid.json()["error"]["code"] == "TRANSFER_TARGET_INVALID"


def test_confirmed_transfer_moves_same_seat_and_keeps_audit_history(client, data):
    ids, factory = data
    sign_in(client)
    definition_id = publish_transfer_flow(client, ids)
    sign_in(client, "test_buyer")
    instance_id = submit(client, {**ids, "definition": definition_id}, draft(client, ids))

    sign_in(client, "test_reviewer")
    before = client.get(f"/api/approvals/{instance_id}").json()
    intent_response = client.post(
        "/api/approval-seat-transfers/intent",
        json=transfer_payload(before, ids["admin"]),
    )
    assert intent_response.status_code == 200, intent_response.text
    intent = intent_response.json()
    confirmed = client.post(
        f"/api/human-actions/{intent['id']}/confirm",
        json={"challenge": intent["challenge"]},
    )
    assert confirmed.status_code == 200, confirmed.text
    receipt = confirmed.json()
    assert receipt["seat_id"] == before["seat_id"]
    assert receipt["transferred_to"]["id"] == ids["admin"]
    assert client.get("/api/approvals").json() == []
    assert client.post(
        f"/api/human-actions/{intent['id']}/confirm",
        json={"challenge": intent["challenge"]},
    ).json() == receipt

    with factory() as db:
        seat = db.get(ApprovalSeat, before["seat_id"])
        instance = db.get(ApprovalInstance, instance_id)
        assert seat.user_id == ids["admin"] and seat.version == before["seat_version"] + 1
        assert instance.version == before["version"] + 1
        assert instance.assignment_snapshots["0"]["transfers"][0]["to_user"]["id"] == ids["admin"]
        event = db.scalar(select(AuditEvent).where(
            AuditEvent.resource_id == instance_id,
            AuditEvent.action == "approval.seat.transferred",
        ))
        assert event.detail["from_user"]["id"] == ids["reviewer"]
        assert event.detail["to_user"]["id"] == ids["admin"]

    sign_in(client)
    after = client.get(f"/api/approvals/{instance_id}").json()
    assert after["seat_id"] == before["seat_id"]
    assert after["transfer_history"][0]["reason"] == intent["payload"]["reason"]
    assert after["allowed_actions"] == ["APPROVE", "REJECT", "RETURN"]


def test_transfer_revalidates_version_and_permissions_at_confirmation(client, data):
    ids, factory = data
    sign_in(client)
    definition_id = publish_transfer_flow(client, ids)
    sign_in(client, "test_buyer")
    instance_id = submit(client, {**ids, "definition": definition_id}, draft(client, ids))
    sign_in(client, "test_reviewer")
    detail = client.get(f"/api/approvals/{instance_id}").json()
    intent = client.post(
        "/api/approval-seat-transfers/intent",
        json=transfer_payload(detail, ids["admin"]),
    ).json()
    with factory.begin() as db:
        db.get(ApprovalInstance, instance_id).version += 1
    response = client.post(
        f"/api/human-actions/{intent['id']}/confirm",
        json={"challenge": intent["challenge"]},
    )
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "VERSION_CONFLICT"
    with factory() as db:
        assert db.get(HumanIntent, intent["id"]).receipt is None
        assert db.get(ApprovalSeat, detail["seat_id"]).user_id == ids["reviewer"]


def test_transfer_revalidates_target_active_state_at_confirmation(client, data):
    ids, factory = data
    sign_in(client)
    definition_id = publish_transfer_flow(client, ids)
    sign_in(client, "test_buyer")
    instance_id = submit(client, {**ids, "definition": definition_id}, draft(client, ids))
    sign_in(client, "test_reviewer")
    detail = client.get(f"/api/approvals/{instance_id}").json()
    intent = client.post(
        "/api/approval-seat-transfers/intent",
        json=transfer_payload(detail, ids["admin"]),
    ).json()
    with factory.begin() as db:
        db.get(User, ids["admin"]).active = False
    response = client.post(
        f"/api/human-actions/{intent['id']}/confirm",
        json={"challenge": intent["challenge"]},
    )
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "TRANSFER_TARGET_INVALID"
    with factory() as db:
        assert db.get(ApprovalSeat, detail["seat_id"]).user_id == ids["reviewer"]
