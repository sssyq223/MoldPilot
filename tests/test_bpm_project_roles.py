from sqlalchemy import select

from app.models import (
    ApprovalInstance,
    ApprovalSeat,
    ProjectProfile,
    ProjectRoleConfig,
    ProjectRoleMember,
)
from conftest import draft, sign_in, submit
from test_core import confirm_decision


def project_role_node(role_key="PURCHASE_OWNER", key="project_review"):
    return {
        "key": key,
        "name": "项目角色审批",
        "users": [],
        "assignment": {
            "roles": [],
            "departments": [],
            "department_heads_only": False,
            "domain_roles": [role_key],
        },
        "mode": "ALL",
        "reject_rules": [],
    }


def save_roles(client, project_id, entries, version=0, reason="合成项目角色调整"):
    return client.put(f"/api/organization/domain-role-bindings/{project_id}", json={
        "entries": entries,
        "expected_version": version,
        "reason": reason,
    })


def publish(client, nodes, process_key="project_role_assignment"):
    response = client.post("/api/workflows", json={
        "process_key": process_key,
        "name": "项目角色动态选人流程",
        "config": {"business_type": "purchase_request", "nodes": nodes},
    })
    assert response.status_code == 200, response.text
    definition_id = response.json()["id"]
    response = client.post(f"/api/workflows/{definition_id}/publish")
    assert response.status_code == 200, response.text
    return definition_id


def test_project_role_preview_and_each_stage_resolve_latest_members(client, data):
    ids, factory = data
    sign_in(client)
    catalog = client.get("/api/workflows/assignment-catalog")
    assert catalog.status_code == 200, catalog.text
    domain = catalog.json()["domain"]
    assert domain["label"] == "项目角色"
    assert {role["key"] for role in domain["roles"]} >= {"PROJECT_OWNER", "PURCHASE_OWNER"}
    assert ids["project"] in {scope["id"] for scope in domain["scopes"]}

    saved = save_roles(client, ids["project"], [{
        "role_key": "PURCHASE_OWNER", "user_id": ids["reviewer"],
    }])
    assert saved.status_code == 200, saved.text
    assert saved.json()["version"] == 1
    preview = client.post("/api/workflows/assignment-preview", json={
        "assignment": project_role_node()["assignment"],
        "context": {"project_id": ids["project"]},
    })
    assert preview.status_code == 200, preview.text
    assert [person["id"] for person in preview.json()["users"]] == [ids["reviewer"]]
    assert preview.json()["sources"][0]["kind"] == "DOMAIN_ROLE"
    other = client.post("/api/workflows/assignment-preview", json={
        "assignment": project_role_node()["assignment"],
        "context": {"project_id": ids["other_project"]},
    })
    assert other.status_code == 200 and other.json()["count"] == 0

    definition_id = publish(client, [
        project_role_node(key="project_review_1"),
        project_role_node(key="project_review_2"),
    ])
    sign_in(client, "test_buyer")
    instance_id = submit(client, {**ids, "definition": definition_id}, draft(client, ids))
    with factory() as db:
        assert list(db.scalars(select(ApprovalSeat.user_id).where(
            ApprovalSeat.instance_id == instance_id,
            ApprovalSeat.stage_index == 0,
        ))) == [ids["reviewer"]]
        snapshot = db.get(ApprovalInstance, instance_id).assignment_snapshots["0"]
        assert snapshot["sources"][0]["version"] == 1
        assert snapshot["sources"][0]["scope"]["id"] == ids["project"]

    sign_in(client)
    changed = save_roles(client, ids["project"], [{
        "role_key": "PURCHASE_OWNER", "user_id": ids["admin"],
    }], version=1)
    assert changed.status_code == 200, changed.text
    assert save_roles(client, ids["project"], [], version=1).status_code == 409
    with factory() as db:
        # Already-created stage seats are frozen when the project role changes.
        assert list(db.scalars(select(ApprovalSeat.user_id).where(
            ApprovalSeat.instance_id == instance_id,
            ApprovalSeat.stage_index == 0,
        ))) == [ids["reviewer"]]

    sign_in(client, "test_reviewer")
    _, response = confirm_decision(client, instance_id)
    assert response.status_code == 200, response.text
    with factory() as db:
        assert list(db.scalars(select(ApprovalSeat.user_id).where(
            ApprovalSeat.instance_id == instance_id,
            ApprovalSeat.stage_index == 1,
        ))) == [ids["admin"]]
        snapshot = db.get(ApprovalInstance, instance_id).assignment_snapshots["1"]
        assert snapshot["sources"][0]["version"] == 2


def test_project_role_membership_does_not_grant_approval_permission(client, data):
    ids, factory = data
    sign_in(client)
    saved = save_roles(client, ids["project"], [{
        "role_key": "PURCHASE_OWNER", "user_id": ids["buyer"],
    }])
    assert saved.status_code == 200, saved.text
    definition_id = publish(client, [project_role_node()], "project_role_no_grant")
    sign_in(client, "test_buyer")
    instance_id = submit(client, {**ids, "definition": definition_id}, draft(client, ids))
    with factory() as db:
        instance = db.get(ApprovalInstance, instance_id)
        snapshot = instance.assignment_snapshots["0"]
        assert instance.incident == "ASSIGNMENT_BLOCKED"
        assert snapshot["candidates"] == [ids["buyer"]]
        assert snapshot["eligible"] == []
        assert not list(db.scalars(select(ApprovalSeat).where(
            ApprovalSeat.instance_id == instance_id,
        )))


def test_project_owner_binding_stays_synchronized_with_project_profile(client, data):
    ids, factory = data
    with factory.begin() as db:
        db.add(ProjectProfile(
            project_id=ids["project"], owner_user_id=ids["reviewer"], execution_mode="INTERNAL",
        ))
    sign_in(client)
    current = client.get(f"/api/organization/domain-role-bindings/{ids['project']}")
    assert current.status_code == 200, current.text
    assert current.json()["version"] == 0
    assert {tuple(entry.values()) for entry in current.json()["entries"]} == {
        ("PROJECT_OWNER", ids["reviewer"]),
    }
    saved = save_roles(client, ids["project"], [{
        "role_key": "PROJECT_OWNER", "user_id": ids["admin"],
    }])
    assert saved.status_code == 200, saved.text
    with factory() as db:
        assert db.get(ProjectProfile, ids["project"]).owner_user_id == ids["admin"]
        assert db.get(ProjectRoleConfig, ids["project"]).version == 1
        assert db.scalar(select(ProjectRoleMember.user_id).where(
            ProjectRoleMember.project_id == ids["project"],
            ProjectRoleMember.role_key == "PROJECT_OWNER",
        )) == ids["admin"]
