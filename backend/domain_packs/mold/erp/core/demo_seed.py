"""Synthetic cases, isolated development data only. No ERP import."""
from datetime import date
from sqlalchemy import select
from domain_packs.mold.models import User, Project, Material, Grant, WorkflowDefinition, Capability
from domain_packs.mold.ports.security import hasher
from domain_packs.mold.authorization import PERMISSIONS
from domain_packs.mold import bpm


def seed(db, password):
    if db.scalar(select(Project.id)): raise ValueError("Demo seed only supports an empty business database")
    admin = db.scalar(select(User).where(User.super_admin.is_(True)))
    buyer = User(username="test_buyer", display_name="测试五金采购员", department="采购", password_hash=hasher.hash(password))
    reviewer = User(username="test_reviewer", display_name="测试采购主管", department="采购", password_hash=hasher.hash(password))
    project = Project(code="TEST-M001", name="模拟模具项目 · M001", status="ACTIVE")
    other = Project(code="TEST-M002", name="模拟模具项目 · M002", status="ACTIVE")
    hardware = Material(code="TEST-H001", name="M6 内六角螺钉", category="hardware", unit="件")
    steel = Material(code="TEST-R001", name="P20 模具钢料", category="raw_material", unit="千克")
    db.add_all([buyer, reviewer, project, other, hardware, steel]); db.flush()
    for u, permissions in [(buyer, ["project.read", "purchase.read", "purchase.create", "purchase.submit"]), (reviewer, ["project.read", "purchase.read", "purchase.approve"])]:
        for p in permissions:
            scope = {"project_id": [project.id]}
            if p.startswith("purchase."): scope["category"] = ["hardware"]
            db.add(Grant(user_id=u.id, permission=p, effect="ALLOW", scope=scope, fields=PERMISSIONS[p], reason="合成测试授权", granted_by=admin.id))
        db.add(Capability(user_id=u.id, kind="TOOL", key="query_purchase_requests"))
        db.add(Capability(user_id=u.id, kind="SKILL", key="purchase_request_review"))
    config = {"business_type": "purchase_request", "nodes": [
        {"key": "purchase_review", "name": "采购主管审批", "users": [reviewer.id], "mode": "ALL", "reject_rules": []},
        {"key": "authorized_review", "name": "授权人审批", "users": [admin.id], "mode": "ALL", "reject_rules": []}]}
    xml = bpm.compile_bpmn(config)
    definition = WorkflowDefinition(process_key="purchase_request", version=1, name="采购申请审批（模拟配置）", config=config, bpmn_xml=xml, status="PUBLISHED", package_hash=bpm.content_hash({"config": config, "xml": xml}))
    db.add(definition); db.flush()
    return {"admin": admin.id, "buyer": buyer.id, "reviewer": reviewer.id, "project": project.id,
            "other_project": other.id, "hardware": hardware.id, "steel": steel.id, "definition": definition.id}
