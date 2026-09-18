from sqlalchemy import select

from app import bpm
from app.authorization import PERMISSIONS
from app.errors import DomainError
from app.models import ApprovalAction, ApprovalInstance, ApprovalSeat, Grant, User
from app.security import hasher
from conftest import PASSWORD, draft, sign_in, submit
from test_approval_seat_add_sign import confirm_add_sign
from test_core import confirm_decision


def _third_approver(data):
    ids, factory = data
    with factory.begin() as db:
        user = User(
            username="test_quorum",
            display_name="测试比例会签人",
            department="采购",
            password_hash=hasher.hash(PASSWORD),
        )
        db.add(user)
        db.flush()
        for permission in ("project.read", "purchase.read", "purchase.approve"):
            scope = {"project_id": [ids["project"]]}
            if permission.startswith("purchase."):
                scope["category"] = ["hardware"]
            db.add(Grant(
                user_id=user.id,
                permission=permission,
                effect="ALLOW",
                scope=scope,
                fields=PERMISSIONS[permission],
                reason="比例会签合成测试授权",
                granted_by=ids["admin"],
            ))
        return user.id


def _publish_quorum(client, users, required=2):
    config = {
        "business_type": "purchase_request",
        "nodes": [{
            "key": "quorum_review",
            "name": "比例会签复核",
            "users": users,
            "mode": "QUORUM",
            "required_approvals": required,
            "reject_rules": [],
        }],
    }
    response = client.post("/api/workflows", json={
        "process_key": "quorum_review",
        "name": "比例会签合成测试",
        "config": config,
    })
    assert response.status_code == 200, response.text
    definition_id = response.json()["id"]
    response = client.post(f"/api/workflows/{definition_id}/publish")
    assert response.status_code == 200, response.text
    return definition_id


def _start(client, ids, definition_id):
    sign_in(client, "test_buyer")
    return submit(client, {**ids, "definition": definition_id}, draft(client, ids))


def test_quorum_waits_for_k_approvals_and_freezes_n(client, data):
    ids, factory = data
    third = _third_approver(data)
    sign_in(client)
    definition_id = _publish_quorum(client, [ids["admin"], ids["reviewer"], third], 2)
    instance_id = _start(client, ids, definition_id)

    sign_in(client, "test_reviewer")
    detail = client.get(f"/api/approvals/{instance_id}").json()
    assert detail["stage_completion"] == {
        "mode": "QUORUM", "required_approvals": 2, "total_seats": 3,
        "approved": 0, "rejected": 0, "pending": 3,
    }
    _, response = confirm_decision(client, instance_id, "REJECT")
    assert response.json()["status"] == "RUNNING"

    sign_in(client)
    detail = client.get(f"/api/approvals/{instance_id}").json()
    assert detail["stage_completion"]["rejected"] == 1
    assert detail["stage_completion"]["pending"] == 2
    _, response = confirm_decision(client, instance_id)
    assert response.json()["status"] == "RUNNING"

    sign_in(client, "test_quorum")
    detail = client.get(f"/api/approvals/{instance_id}").json()
    assert detail["stage_completion"]["approved"] == 1
    _, response = confirm_decision(client, instance_id)
    assert response.json()["status"] == "COMPLETED"

    with factory() as db:
        instance = db.get(ApprovalInstance, instance_id)
        assert instance.assignment_snapshots["0"]["required_approvals"] == 2
        assert instance.assignment_snapshots["0"]["total_seats"] == 3
        statuses = sorted(db.scalars(select(ApprovalSeat.status).where(
            ApprovalSeat.instance_id == instance_id
        )))
        assert statuses == ["APPROVE", "APPROVE", "REJECT"]
        assert len(list(db.scalars(select(ApprovalAction).where(
            ApprovalAction.instance_id == instance_id
        )))) == 3


def test_quorum_rejects_only_when_remaining_votes_cannot_reach_k(client, data):
    ids, factory = data
    third = _third_approver(data)
    sign_in(client)
    definition_id = _publish_quorum(client, [ids["admin"], ids["reviewer"], third], 2)
    instance_id = _start(client, ids, definition_id)

    sign_in(client, "test_reviewer")
    _, first = confirm_decision(client, instance_id, "REJECT")
    assert first.json()["status"] == "RUNNING"
    sign_in(client)
    _, second = confirm_decision(client, instance_id, "REJECT")
    assert second.json()["status"] == "REJECTED"

    with factory() as db:
        statuses = sorted(db.scalars(select(ApprovalSeat.status).where(
            ApprovalSeat.instance_id == instance_id
        )))
        assert statuses == ["CANCELLED", "REJECT", "REJECT"]


def test_quorum_post_addition_still_requires_the_added_vote(client, data):
    ids, factory = data
    third = _third_approver(data)
    sign_in(client)
    config = {
        "business_type": "purchase_request",
        "nodes": [{
            "key": "quorum_with_addition",
            "name": "比例会签与加签",
            "users": [ids["admin"], ids["reviewer"]],
            "mode": "QUORUM",
            "required_approvals": 1,
            "reject_rules": [],
            "add_sign_policy": {"timings": ["POST"], "users": [third]},
        }],
    }
    response = client.post("/api/workflows", json={
        "process_key": "quorum_addition",
        "name": "比例会签加签测试",
        "config": config,
    })
    assert response.status_code == 200, response.text
    definition_id = response.json()["id"]
    assert client.post(f"/api/workflows/{definition_id}/publish").status_code == 200
    instance_id = _start(client, ids, definition_id)

    sign_in(client, "test_reviewer")
    detail = client.get(f"/api/approvals/{instance_id}").json()
    _, receipt = confirm_add_sign(client, detail, third, "POST")
    _, response = confirm_decision(client, instance_id)
    assert response.json()["status"] == "RUNNING"

    sign_in(client, "test_quorum")
    detail = client.get(f"/api/approvals/{instance_id}").json()
    assert detail["seat_id"] == receipt["added_seat_id"]
    _, response = confirm_decision(client, instance_id)
    assert response.json()["status"] == "COMPLETED"
    with factory() as db:
        statuses = sorted(db.scalars(select(ApprovalSeat.status).where(
            ApprovalSeat.instance_id == instance_id
        )))
        assert statuses == ["APPROVE", "APPROVE", "CANCELLED"]


def test_quorum_configuration_and_publish_candidate_count_are_validated(client, data):
    ids, _ = data
    base = {
        "business_type": "purchase_request",
        "nodes": [{"key": "review", "name": "比例会签", "users": [ids["admin"]],
                   "mode": "QUORUM", "required_approvals": 1, "reject_rules": []}],
    }
    bpm.validate(base)
    for invalid in (None, 0, True, 51):
        config = {**base, "nodes": [{**base["nodes"][0], "required_approvals": invalid}]}
        try:
            bpm.validate(config)
        except DomainError:
            pass
        else:
            raise AssertionError(f"invalid quorum threshold accepted: {invalid!r}")
    non_quorum = {**base, "nodes": [{**base["nodes"][0], "mode": "ALL"}]}
    try:
        bpm.validate(non_quorum)
    except DomainError:
        pass
    else:
        raise AssertionError("non-quorum node accepted required_approvals")

    sign_in(client)
    config = {**base, "nodes": [{**base["nodes"][0], "required_approvals": 2}]}
    response = client.post("/api/workflows", json={
        "process_key": "quorum_too_small",
        "name": "候选人数不足",
        "config": config,
    })
    assert response.status_code == 200, response.text
    published = client.post(f"/api/workflows/{response.json()['id']}/publish")
    assert published.status_code == 400
    assert published.json()["error"]["code"] == "ASSIGNMENT_BLOCKED"
