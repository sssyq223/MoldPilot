from concurrent.futures import ThreadPoolExecutor
from threading import Barrier

from sqlalchemy import select

from app.errors import DomainError
from app.models import ApprovalAction, ApprovalCandidate, ApprovalInstance, ApprovalSeat, AuditEvent, User
from conftest import draft, sign_in, submit
from domain_packs.mold.erp.core import business
from test_bpm_assignments import group, node, publish
from test_core import confirm_decision


def claim_workflow(client, ids, *, agent_auto=False):
    candidate_group = group(client, [ids["admin"], ids["reviewer"]], name="候选领取审批组")
    claim_node = node(candidate_group["id"])
    claim_node["mode"] = "CLAIM"
    if agent_auto:
        claim_node["agent_auto_approval"] = True
    return publish(client, [claim_node])


def test_claim_node_creates_one_seat_and_preserves_claim_history(client, data):
    ids, factory = data
    sign_in(client)
    definition_id, published = claim_workflow(client, ids)
    assert published.status_code == 200, published.text

    sign_in(client, "test_buyer")
    instance_id = submit(client, {**ids, "definition": definition_id}, draft(client, ids))

    with factory() as db:
        candidates = list(db.scalars(select(ApprovalCandidate).where(
            ApprovalCandidate.instance_id == instance_id,
        )))
        assert {candidate.user_id for candidate in candidates} == {ids["admin"], ids["reviewer"]}
        assert {candidate.status for candidate in candidates} == {"AVAILABLE"}
        assert not list(db.scalars(select(ApprovalSeat).where(
            ApprovalSeat.instance_id == instance_id,
        )))

    sign_in(client)
    admin_detail = client.get(f"/api/approvals/{instance_id}").json()
    assert admin_detail["claim_allowed"] is True
    assert admin_detail["seat_id"] is None
    assert admin_detail["allowed_actions"] == []

    sign_in(client, "test_reviewer")
    reviewer_tasks = client.get("/api/approvals").json()
    assert [item["id"] for item in reviewer_tasks] == [instance_id]
    assert reviewer_tasks[0]["claim_status"] == "AVAILABLE"
    claimed = client.post(f"/api/approvals/{instance_id}/claim", json={
        "version": reviewer_tasks[0]["version"],
        "snapshot_hash": reviewer_tasks[0]["snapshot_hash"],
    })
    assert claimed.status_code == 200, claimed.text
    assert claimed.json()["claimed_by"]["id"] == ids["reviewer"]

    sign_in(client)
    stale = client.post(f"/api/approvals/{instance_id}/claim", json={
        "version": admin_detail["version"],
        "snapshot_hash": admin_detail["snapshot_hash"],
    })
    assert stale.status_code == 409
    assert stale.json()["error"]["code"] == "VERSION_CONFLICT"
    assert client.get("/api/approvals").json() == []

    with factory() as db:
        seats = list(db.scalars(select(ApprovalSeat).where(
            ApprovalSeat.instance_id == instance_id,
        )))
        candidates = list(db.scalars(select(ApprovalCandidate).where(
            ApprovalCandidate.instance_id == instance_id,
        )))
        assert len(seats) == 1 and seats[0].user_id == ids["reviewer"]
        assert sorted((item.user_id, item.status) for item in candidates) == sorted([
            (ids["admin"], "CLOSED"),
            (ids["reviewer"], "CLAIMED"),
        ])

    sign_in(client, "test_reviewer")
    detail = client.get(f"/api/approvals/{instance_id}").json()
    assert detail["claim_allowed"] is False
    assert detail["claimed_by"]["id"] == ids["reviewer"]
    assert detail["allowed_actions"] == ["APPROVE", "REJECT", "RETURN"]
    assert detail["claim_history"][0]["candidate_count"] == 2
    _, completed = confirm_decision(client, instance_id)
    assert completed.json()["status"] == "COMPLETED"
    terminal = client.get(f"/api/approvals/{instance_id}").json()
    assert terminal["claim_history"][0]["claimed_by"]["id"] == ids["reviewer"]
    assert terminal["history"][0]["decision"] == "APPROVE"


def test_two_candidates_concurrently_claim_only_one_seat(client, data):
    ids, factory = data
    sign_in(client)
    definition_id, published = claim_workflow(client, ids)
    assert published.status_code == 200, published.text
    sign_in(client, "test_buyer")
    instance_id = submit(client, {**ids, "definition": definition_id}, draft(client, ids))
    detail = client.get(f"/api/approvals/{instance_id}").json()
    payload = {"version": detail["version"], "snapshot_hash": detail["snapshot_hash"]}
    barrier = Barrier(2)

    def attempt(user_id):
        with factory.begin() as db:
            user = db.get(User, user_id)
            barrier.wait(timeout=5)
            try:
                result = business.claim_approval(db, user, instance_id, payload)
                return "OK", result["seat_id"]
            except DomainError as error:
                return error.code, None

    with ThreadPoolExecutor(max_workers=2) as pool:
        outcomes = list(pool.map(attempt, [ids["admin"], ids["reviewer"]]))
    assert sorted(code for code, _ in outcomes) == ["OK", "VERSION_CONFLICT"]
    with factory() as db:
        seats = list(db.scalars(select(ApprovalSeat).where(
            ApprovalSeat.instance_id == instance_id,
        )))
        assert len(seats) == 1
        assert db.scalar(select(ApprovalAction).where(
            ApprovalAction.instance_id == instance_id,
        )) is None
        event = db.scalar(select(AuditEvent).where(
            AuditEvent.resource_id == instance_id,
            AuditEvent.action == "approval.seat.claimed",
        ))
        assert event.detail["seat_id"] == seats[0].id


def test_withdraw_closes_unclaimed_pool_and_claim_cannot_be_automated(client, data):
    ids, factory = data
    sign_in(client)
    candidate_group = group(client, [ids["admin"], ids["reviewer"]], name="禁止自动领取组")
    invalid_node = node(candidate_group["id"])
    invalid_node.update({"mode": "CLAIM", "agent_auto_approval": True})
    invalid = client.post("/api/workflows", json={
        "process_key": "invalid_auto_claim",
        "name": "候选领取不得自动审批",
        "config": {"business_type": "purchase_request", "nodes": [invalid_node]},
    })
    assert invalid.status_code == 400
    assert invalid.json()["error"]["code"] == "INVALID_WORKFLOW"

    definition_id, published = claim_workflow(client, ids)
    assert published.status_code == 200, published.text
    sign_in(client, "test_buyer")
    instance_id = submit(client, {**ids, "definition": definition_id}, draft(client, ids))
    detail = client.get(f"/api/approvals/{instance_id}").json()
    prepared = client.post("/api/approvals/withdraw-intent", json={
        "instance_id": instance_id,
        "version": detail["version"],
        "snapshot_hash": detail["snapshot_hash"],
        "reason": "候选人领取前补充申请资料",
    })
    assert prepared.status_code == 200, prepared.text
    withdrawn = client.post(
        f"/api/human-actions/{prepared.json()['id']}/confirm",
        json={"challenge": prepared.json()["challenge"]},
    )
    assert withdrawn.status_code == 200, withdrawn.text
    with factory() as db:
        instance = db.get(ApprovalInstance, instance_id)
        candidates = list(db.scalars(select(ApprovalCandidate).where(
            ApprovalCandidate.instance_id == instance_id,
        )))
        event = db.scalar(select(AuditEvent).where(
            AuditEvent.resource_id == instance_id,
            AuditEvent.action == "approval.withdrawn",
        ))
        assert instance.status == "CANCELLED"
        assert {candidate.status for candidate in candidates} == {"CLOSED"}
        assert event.detail["closed_candidates"] == 2
        assert not list(db.scalars(select(ApprovalSeat).where(
            ApprovalSeat.instance_id == instance_id,
        )))
