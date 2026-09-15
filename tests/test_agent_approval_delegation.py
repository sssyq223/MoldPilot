from datetime import date, timedelta
from decimal import Decimal

import pytest
from sqlalchemy import create_engine, select, func
from sqlalchemy.orm import sessionmaker

from app import bpm, business, models as m, schemas as s
from app.authorization import PERMISSIONS, fingerprint
from app.db import Base, now
from app.security import hasher


@pytest.fixture()
def sqlite_session():
    engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False})
    Base.metadata.create_all(engine)
    factory = sessionmaker(engine, expire_on_commit=False)
    with factory() as db:
        yield db
    engine.dispose()


def grant(db, user, admin, permission, project, category="hardware"):
    scope = {"project_id": [project.id]}
    if permission.startswith("purchase."):
        scope["category"] = [category]
    db.add(m.Grant(user_id=user.id, permission=permission, effect="ALLOW", scope=scope,
                   fields=PERMISSIONS[permission], reason="自动审批测试授权", granted_by=admin.id))


def fixture_data(db, *, auto_node=True):
    admin = m.User(username="admin", display_name="管理员", password_hash=hasher.hash("SyntheticPassword-2026!"), super_admin=True)
    buyer = m.User(username="buyer", display_name="采购员", password_hash=hasher.hash("SyntheticPassword-2026!"))
    reviewer = m.User(username="reviewer", display_name="采购主管", password_hash=hasher.hash("SyntheticPassword-2026!"))
    project = m.Project(code="AUTO-M001", name="自动审批测试项目", status="ACTIVE")
    material = m.Material(code="AUTO-H001", name="螺丝", category="hardware", unit="件")
    db.add_all([admin, buyer, reviewer, project, material])
    db.flush()
    for permission in ("project.read", "purchase.read", "purchase.create", "purchase.submit"):
        grant(db, buyer, admin, permission, project)
    for permission in ("project.read", "purchase.read", "purchase.approve"):
        grant(db, reviewer, admin, permission, project)
    node = {"key": "review", "name": "采购主管审批", "users": [reviewer.id], "mode": "ALL", "reject_rules": []}
    if auto_node:
        node["agent_auto_approval"] = True
    config = {"business_type": "purchase_request", "nodes": [node]}
    definition = m.WorkflowDefinition(process_key="auto_purchase", version=1, name="自动审批测试", config=config,
                                      bpmn_xml=bpm.compile_bpmn(config), status="PUBLISHED",
                                      package_hash=bpm.content_hash({"config": config}))
    db.add(definition)
    db.add(m.AgentApprovalDelegation(user_id=reviewer.id, process_key="auto_purchase", node_key="review",
                                     decision="APPROVE", reason="低风险采购节点允许 Agent 自动同意",
                                     valid_from=None, valid_to=now() + timedelta(days=1), created_by=reviewer.id))
    db.flush()
    return admin, buyer, reviewer, project, material, definition


def draft(db, buyer, project, material):
    return business.create_request(db, buyer, s.PurchaseInput(
        project_id=project.id,
        remark="自动审批测试",
        lines=[s.LineInput(material_id=material.id, quantity=Decimal("2"), due_date=date(2026, 9, 16))],
    ))


def test_agent_delegation_auto_approves_explicitly_enabled_node(sqlite_session):
    db = sqlite_session
    _admin, buyer, reviewer, project, material, definition = fixture_data(db, auto_node=True)
    req = draft(db, buyer, project, material)
    result = business.submit_request(db, buyer, req.id, req.revision, definition.id)
    assert result["status"] == "APPROVED"
    assert result["agent_auto_approved"][0]["user_id"] == reviewer.id
    instance = db.get(m.ApprovalInstance, result["instance_id"])
    assert instance.status == "COMPLETED"
    assert db.scalar(select(func.count()).select_from(m.ApprovalAction)) == 1
    action = db.scalar(select(m.ApprovalAction))
    assert action.user_id == reviewer.id
    assert action.decision == "APPROVE"
    assert action.user_snapshot["actor_type"] == "AGENT_DELEGATED"
    assert action.user_snapshot["delegation_id"]
    audit = db.scalar(select(m.AuditEvent).where(m.AuditEvent.action == "approval.decided"))
    assert audit.detail["actor_type"] == "AGENT_DELEGATED"
    assert audit.detail["delegation_id"] == action.user_snapshot["delegation_id"]


def test_agent_delegation_does_not_bypass_force_human_node(sqlite_session):
    db = sqlite_session
    _admin, buyer, reviewer, project, material, definition = fixture_data(db, auto_node=False)
    req = draft(db, buyer, project, material)
    result = business.submit_request(db, buyer, req.id, req.revision, definition.id)
    assert result["status"] == "SUBMITTED"
    assert result["agent_auto_approved"] == []
    instance = db.get(m.ApprovalInstance, result["instance_id"])
    assert instance.status == "RUNNING"
    seat = db.scalar(select(m.ApprovalSeat).where(m.ApprovalSeat.instance_id == instance.id))
    assert seat.user_id == reviewer.id
    assert seat.status == "PENDING"
    assert db.scalar(select(func.count()).select_from(m.ApprovalAction)) == 0


def test_delegation_changes_authorization_fingerprint(sqlite_session):
    db = sqlite_session
    _admin, _buyer, reviewer, _project, _material, _definition = fixture_data(db, auto_node=True)
    before = fingerprint(db, reviewer)
    delegation = db.scalar(select(m.AgentApprovalDelegation).where(m.AgentApprovalDelegation.user_id == reviewer.id))
    delegation.active = False
    delegation.revoked_at = now()
    reviewer.security_version += 1
    assert fingerprint(db, reviewer) != before
