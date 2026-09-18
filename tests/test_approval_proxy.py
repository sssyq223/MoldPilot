from sqlalchemy import select

from app.models import ApprovalAction, ApprovalInstance, ApprovalProxyDelegation, ApprovalSeat, AuditEvent
from conftest import draft, sign_in, submit
from test_core import decision_payload


def publish_proxy_flow(client, ids):
    response = client.post("/api/workflows", json={
        "process_key": "human_proxy_test",
        "name": "人工审批代理测试",
        "config": {
            "business_type": "purchase_request",
            "nodes": [{
                "key": "review",
                "name": "代理审批节点",
                "users": [ids["reviewer"]],
                "mode": "ALL",
                "reject_rules": [],
                "allow_proxy": True,
            }],
        },
    })
    assert response.status_code == 200, response.text
    definition_id = response.json()["id"]
    assert client.post(f"/api/workflows/{definition_id}/publish").status_code == 200
    return definition_id


def create_proxy(client, ids, *, principal=None, proxy=None, decisions=None):
    response = client.post("/api/approval-proxies", json={
        "principal_user_id": principal or ids["reviewer"],
        "proxy_user_id": proxy or ids["admin"],
        "process_key": "human_proxy_test",
        "node_key": "review",
        "allowed_decisions": decisions or ["APPROVE"],
        "reason": "审批责任人请假，由已授权代理人临时办理",
        "valid_from": None,
        "valid_to": None,
    })
    assert response.status_code == 200, response.text
    return response.json()


def start_proxy_approval(client, ids, definition_id, submitter="test_buyer"):
    sign_in(client, submitter)
    request_id = draft(client, ids)
    instance_id = submit(client, {**ids, "definition": definition_id}, request_id)
    return request_id, instance_id


def test_proxy_acts_on_principal_seat_and_preserves_both_identities(client, data):
    ids, factory = data
    sign_in(client)
    definition_id = publish_proxy_flow(client, ids)
    proxy = create_proxy(client, ids, decisions=["APPROVE", "RETURN"])
    _, instance_id = start_proxy_approval(client, ids, definition_id)

    sign_in(client)
    pending = client.get("/api/approvals").json()
    detail = next(item for item in pending if item["id"] == instance_id)
    assert detail["proxy_delegation"]["id"] == proxy["id"]
    assert detail["proxy_delegation"]["principal_user"]["id"] == ids["reviewer"]
    assert detail["allowed_actions"] == ["APPROVE", "RETURN"]
    assert detail["transfer_allowed"] is False
    assert detail["add_sign_allowed"] is False

    intent = client.post("/api/approvals/decision-intent", json=decision_payload(detail)).json()
    confirmed = client.post(
        f"/api/human-actions/{intent['id']}/confirm",
        json={"challenge": intent["challenge"]},
    )
    assert confirmed.status_code == 200, confirmed.text
    assert confirmed.json()["status"] == "COMPLETED"

    with factory() as db:
        seat = db.scalar(select(ApprovalSeat).where(ApprovalSeat.instance_id == instance_id))
        action = db.scalar(select(ApprovalAction).where(ApprovalAction.instance_id == instance_id))
        assert seat.user_id == ids["reviewer"] and seat.status == "APPROVE"
        assert action.user_id == ids["admin"]
        assert action.user_snapshot["actor_type"] == "HUMAN_PROXY"
        assert action.user_snapshot["principal_user"]["id"] == ids["reviewer"]
        assert action.user_snapshot["delegation_id"] == proxy["id"]
        event = db.scalar(select(AuditEvent).where(
            AuditEvent.resource_id == instance_id,
            AuditEvent.action == "approval.decided",
        ))
        assert event.detail["principal_user_id"] == ids["reviewer"]
        assert event.detail["actor_user_id"] == ids["admin"]


def test_proxy_revocation_invalidates_prepared_decision(client, data):
    ids, factory = data
    sign_in(client)
    definition_id = publish_proxy_flow(client, ids)
    proxy = create_proxy(client, ids)
    _, instance_id = start_proxy_approval(client, ids, definition_id)

    sign_in(client)
    detail = client.get(f"/api/approvals/{instance_id}").json()
    intent = client.post("/api/approvals/decision-intent", json=decision_payload(detail)).json()
    revoked = client.post(
        f"/api/approval-proxies/{proxy['id']}/revoke",
        json={"reason": "原责任人已恢复工作，撤销临时代理"},
    )
    assert revoked.status_code == 200 and revoked.json()["active"] is False
    stale = client.post(
        f"/api/human-actions/{intent['id']}/confirm",
        json={"challenge": intent["challenge"]},
    )
    assert stale.status_code == 409
    assert stale.json()["error"]["code"] == "RULE_BLOCKED"
    assert not any(item["id"] == instance_id for item in client.get("/api/approvals").json())

    sign_in(client, "test_reviewer")
    assert any(item["id"] == instance_id for item in client.get("/api/approvals").json())
    with factory() as db:
        assert db.scalar(select(ApprovalAction).where(ApprovalAction.instance_id == instance_id)) is None


def test_proxy_cannot_approve_a_request_created_by_the_proxy(client, data):
    ids, _ = data
    sign_in(client)
    definition_id = publish_proxy_flow(client, ids)
    create_proxy(client, ids)
    _, instance_id = start_proxy_approval(client, ids, definition_id, submitter="admin")

    detail = client.get(f"/api/approvals/{instance_id}").json()
    assert detail["seat_id"] is None
    assert detail["proxy_delegation"] is None
    assert detail["allowed_actions"] == []
    assert not any(item["id"] == instance_id for item in client.get("/api/approvals").json())


def test_proxy_configuration_rejects_cycles_and_non_admin_changes(client, data):
    ids, factory = data
    sign_in(client)
    publish_proxy_flow(client, ids)
    create_proxy(client, ids)
    cycle = client.post("/api/approval-proxies", json={
        "principal_user_id": ids["admin"],
        "proxy_user_id": ids["reviewer"],
        "process_key": "human_proxy_test",
        "node_key": "review",
        "allowed_decisions": ["APPROVE"],
        "reason": "该配置应因形成循环而被拒绝",
        "valid_from": None,
        "valid_to": None,
    })
    assert cycle.status_code == 409
    assert cycle.json()["error"]["code"] == "APPROVAL_PROXY_CYCLE"
    with factory() as db:
        assert len(list(db.scalars(select(ApprovalProxyDelegation)))) == 1

    sign_in(client, "test_buyer")
    forbidden = client.post("/api/approval-proxies", json={
        "principal_user_id": ids["reviewer"],
        "proxy_user_id": ids["admin"],
        "process_key": "human_proxy_test",
        "node_key": "review",
        "allowed_decisions": ["APPROVE"],
        "reason": "普通用户不能配置他人的人工审批代理",
        "valid_from": None,
        "valid_to": None,
    })
    assert forbidden.status_code == 403
