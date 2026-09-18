from sqlalchemy import select

from app.models import ApprovalAction, ApprovalInstance, ApprovalSeat, AuditEvent, PurchaseRequest
from conftest import draft, sign_in, submit
from test_core import confirm_decision, decision_payload


def publish_return_flow(client, ids):
    response = client.post("/api/workflows", json={
        "process_key": "return_target_test",
        "name": "指定退回目标测试",
        "config": {
            "business_type": "purchase_request",
            "nodes": [
                {
                    "key": "business_review",
                    "name": "业务资料复核",
                    "users": [ids["reviewer"]],
                    "mode": "ALL",
                    "reject_rules": [],
                    "return_policy": {"targets": ["applicant"]},
                },
                {
                    "key": "final_review",
                    "name": "最终审批",
                    "users": [ids["admin"]],
                    "mode": "ALL",
                    "reject_rules": [],
                    "return_policy": {"targets": ["applicant", "business_review"]},
                },
            ],
        },
    })
    assert response.status_code == 200, response.text
    definition_id = response.json()["id"]
    assert client.post(f"/api/workflows/{definition_id}/publish").status_code == 200
    return definition_id


def test_template_scoped_return_target_is_frozen_and_resubmit_restarts_full_review(client, data):
    ids, factory = data
    sign_in(client)
    definition_id = publish_return_flow(client, ids)
    sign_in(client, "test_buyer")
    request_id = draft(client, ids)
    instance_id = submit(client, {**ids, "definition": definition_id}, request_id)

    sign_in(client, "test_reviewer")
    _, first_result = confirm_decision(client, instance_id)
    assert first_result.json()["status"] == "RUNNING"

    sign_in(client)
    detail = client.get(f"/api/approvals/{instance_id}").json()
    assert detail["stage_index"] == 1
    assert detail["return_options"] == [
        {"key": "applicant", "name": "申请人修改"},
        {"key": "business_review", "name": "业务资料复核"},
    ]
    missing = client.post(
        "/api/approvals/decision-intent",
        json=decision_payload(detail, "RETURN"),
    )
    assert missing.status_code == 409
    assert missing.json()["error"]["code"] == "RETURN_TARGET_INVALID"
    invalid_payload = decision_payload(detail, "RETURN") | {"return_target_node_key": "final_review"}
    invalid = client.post("/api/approvals/decision-intent", json=invalid_payload)
    assert invalid.status_code == 409
    assert invalid.json()["error"]["code"] == "RETURN_TARGET_INVALID"

    payload = decision_payload(detail, "RETURN") | {"return_target_node_key": "business_review"}
    intent = client.post("/api/approvals/decision-intent", json=payload)
    assert intent.status_code == 200, intent.text
    prepared = intent.json()
    assert prepared["payload"]["return_target_node_key"] == "business_review"
    returned = client.post(
        f"/api/human-actions/{prepared['id']}/confirm",
        json={"challenge": prepared["challenge"]},
    )
    assert returned.status_code == 200, returned.text
    assert returned.json()["status"] == "RETURNED"

    with factory() as db:
        instance = db.get(ApprovalInstance, instance_id)
        request = db.get(PurchaseRequest, request_id)
        action = db.scalar(select(ApprovalAction).where(
            ApprovalAction.instance_id == instance_id,
            ApprovalAction.decision == "RETURN",
        ))
        event = db.scalar(select(AuditEvent).where(
            AuditEvent.resource_id == instance_id,
            AuditEvent.action == "approval.decided",
        ).order_by(AuditEvent.created_at.desc()))
        assert instance.status == "RETURNED"
        assert request.status == "RETURNED"
        assert action.decision_context == {
            "return_target": {"key": "business_review", "name": "业务资料复核"}
        }
        assert event.detail["return_target"] == {
            "key": "business_review", "name": "业务资料复核"
        }

    completed_detail = client.get(f"/api/approvals/{instance_id}").json()
    return_history = next(item for item in completed_detail["history"] if item["decision"] == "RETURN")
    assert return_history["decision_context"]["return_target"]["key"] == "business_review"

    sign_in(client, "test_buyer")
    new_instance_id = submit(client, {**ids, "definition": definition_id}, request_id)
    assert new_instance_id != instance_id
    with factory() as db:
        new_instance = db.get(ApprovalInstance, new_instance_id)
        new_seat = db.scalar(select(ApprovalSeat).where(ApprovalSeat.instance_id == new_instance_id))
        request = db.get(PurchaseRequest, request_id)
        assert new_instance.round_no == 2
        assert new_instance.revision == 2
        assert new_instance.stage_index == 0
        assert new_seat.user_id == ids["reviewer"]
        assert request.status == "SUBMITTED"


def test_legacy_node_without_return_policy_defaults_to_applicant(client, data):
    ids, factory = data
    sign_in(client, "test_buyer")
    request_id = draft(client, ids)
    instance_id = submit(client, ids, request_id)
    sign_in(client, "test_reviewer")
    detail = client.get(f"/api/approvals/{instance_id}").json()
    assert detail["return_options"] == [{"key": "applicant", "name": "申请人修改"}]
    intent = client.post(
        "/api/approvals/decision-intent",
        json=decision_payload(detail, "RETURN"),
    )
    assert intent.status_code == 200, intent.text
    assert intent.json()["payload"]["return_target_node_key"] == "applicant"
