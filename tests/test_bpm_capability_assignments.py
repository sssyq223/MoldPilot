from sqlalchemy import select

from app.models import ApprovalInstance, ApprovalSeat, Grant, User
from conftest import draft, sign_in, submit
from test_core import confirm_decision


def group(client, user_ids):
    response = client.post("/api/organization/groups", json={
        "kind": "ROLE",
        "name": "能力责任域候选池",
        "members": [{"user_id": user_id, "is_head": False} for user_id in user_ids],
        "reason": "合成能力责任域选人测试",
    })
    assert response.status_code == 200, response.text
    return response.json()["id"]


def capability_node(role_id, key="capability_review"):
    return {
        "key": key,
        "name": "业务能力与责任域审批",
        "users": [],
        "assignment": {
            "roles": [role_id],
            "departments": [],
            "department_heads_only": False,
            "domain_roles": [],
            "business_permissions": ["purchase.approve"],
            "responsibility_scope": ["project_id"],
        },
        "mode": "ALL",
        "reject_rules": [],
    }


def publish(client, nodes, process_key="capability_scope_assignment"):
    response = client.post("/api/workflows", json={
        "process_key": process_key,
        "name": "能力责任域动态选人流程",
        "config": {"business_type": "purchase_request", "nodes": nodes},
    })
    assert response.status_code == 200, response.text
    definition_id = response.json()["id"]
    response = client.post(f"/api/workflows/{definition_id}/publish")
    return definition_id, response


def test_catalog_and_preview_intersect_role_capability_and_project_scope(client, data):
    ids, factory = data
    sign_in(client)
    role_id = group(client, [ids["reviewer"], ids["buyer"]])
    catalog = client.get("/api/workflows/assignment-catalog")
    assert catalog.status_code == 200, catalog.text
    domain = catalog.json()["domain"]
    assert "purchase.approve" in {item["key"] for item in domain["capabilities"]}
    assert domain["responsibility_dimensions"] == [{"key": "project_id", "name": "当前项目"}]

    response = client.post("/api/workflows/assignment-preview", json={
        "assignment": capability_node(role_id)["assignment"],
        "context": {"project_id": ids["project"]},
    })
    assert response.status_code == 200, response.text
    result = response.json()
    assert [person["id"] for person in result["users"]] == [ids["reviewer"]]
    source = next(item for item in result["sources"] if item["kind"] == "BUSINESS_CAPABILITY")
    assert source["id"] == "purchase.approve"
    assert source["scope"] == {
        "keys": ["project_id"],
        "values": {"project_id": ids["project"]},
        "resolved": True,
    }
    assert len(source["version"]) == 64

    other = client.post("/api/workflows/assignment-preview", json={
        "assignment": capability_node(role_id)["assignment"],
        "context": {"project_id": ids["other_project"]},
    })
    assert other.status_code == 200, other.text
    assert other.json()["count"] == 0

    definition_id, published = publish(
        client, [capability_node(role_id)], "capability_scope_runtime",
    )
    assert published.status_code == 200, published.text
    instance_id = submit(
        client, {**ids, "definition": definition_id}, draft(client, ids),
    )
    with factory() as db:
        assert list(db.scalars(select(ApprovalSeat.user_id).where(
            ApprovalSeat.instance_id == instance_id,
            ApprovalSeat.stage_index == 0,
        ))) == [ids["reviewer"]]
        snapshot = db.get(ApprovalInstance, instance_id).assignment_snapshots["0"]
        capability_source = next(
            item for item in snapshot["sources"] if item["kind"] == "BUSINESS_CAPABILITY"
        )
        assert capability_source["scope"]["values"]["project_id"] == ids["project"]


def test_capability_assignment_requires_published_permission_scope_and_context(client, data):
    ids, _ = data
    sign_in(client)
    role_id = group(client, [ids["reviewer"]])
    assignment = capability_node(role_id)["assignment"]

    missing_context = client.post("/api/workflows/assignment-preview", json={
        "assignment": assignment,
        "context": {},
    })
    assert missing_context.status_code == 400
    assert missing_context.json()["error"]["code"] == "ASSIGNMENT_CONTEXT_REQUIRED"

    missing_scope = {**assignment, "responsibility_scope": []}
    response = client.post("/api/workflows/assignment-preview", json={
        "assignment": missing_scope,
        "context": {"project_id": ids["project"]},
    })
    assert response.status_code == 400
    assert response.json()["error"]["code"] == "INVALID_WORKFLOW"

    unknown_permission = {**assignment, "business_permissions": ["unknown.approve"]}
    response = client.post("/api/workflows/assignment-preview", json={
        "assignment": unknown_permission,
        "context": {"project_id": ids["project"]},
    })
    assert response.status_code == 400
    assert response.json()["error"]["code"] == "INVALID_WORKFLOW"

    unknown_scope = {**assignment, "responsibility_scope": ["customer_id"]}
    response = client.post("/api/workflows/assignment-preview", json={
        "assignment": unknown_scope,
        "context": {"customer_id": "synthetic"},
    })
    assert response.status_code == 400
    assert response.json()["error"]["code"] == "INVALID_WORKFLOW"


def test_next_stage_re_resolves_current_capability_grants(client, data):
    ids, factory = data
    sign_in(client)
    role_id = group(client, [ids["reviewer"]])
    first = {
        "key": "admin_review",
        "name": "管理员首审",
        "users": [ids["admin"]],
        "mode": "ALL",
        "reject_rules": [],
    }
    definition_id, response = publish(
        client, [first, capability_node(role_id)], "capability_scope_revoke",
    )
    assert response.status_code == 200, response.text
    request_id = draft(client, ids)
    instance_id = submit(client, {**ids, "definition": definition_id}, request_id)

    with factory.begin() as db:
        reviewer = db.get(User, ids["reviewer"])
        grant = db.scalar(select(Grant).where(
            Grant.user_id == ids["reviewer"],
            Grant.permission == "purchase.approve",
            Grant.active.is_(True),
        ))
        grant.active = False
        reviewer.security_version += 1

    _, response = confirm_decision(client, instance_id)
    assert response.status_code == 200, response.text
    with factory() as db:
        instance = db.get(ApprovalInstance, instance_id)
        assert instance.stage_index == 1
        assert instance.incident == "ASSIGNMENT_BLOCKED"
        assert instance.assignment_snapshots["1"]["candidates"] == []
        assert not list(db.scalars(select(ApprovalSeat).where(
            ApprovalSeat.instance_id == instance_id,
            ApprovalSeat.stage_index == 1,
        )))
