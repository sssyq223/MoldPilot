from sqlalchemy import select

from app import bpm, business, models as m
from app.authorization import fingerprint
from app.tool_gateway import execute, tool_schema
from pg_db import factory as pg_factory


def factory():
    return pg_factory()


def user(db, username="admin", super_admin=True):
    row = m.User(username=username, display_name="设计主管", password_hash="test", super_admin=super_admin)
    db.add(row)
    db.flush()
    return row


def workflow(db, reviewer):
    config = {
        "business_type": "design_route",
        "applicability": {"design_types": ["NEW_MOLD", "MOLD_CHANGE"]},
        "nodes": [{"key": "design_review", "name": "设计复核", "mode": "ALL",
                   "users": [reviewer.id], "reject_rules": []}],
    }
    row = m.WorkflowDefinition(
        process_key="design_upload_test", version=1, name="新模改模设计审批",
        status="PUBLISHED", config=config, bpmn_xml=bpm.compile_bpmn(config), package_hash="test",
    )
    db.add(row)
    db.flush()
    return row


def uploaded_file(db, owner, conversation):
    row = m.FileObject(
        owner_id=owner.id, conversation_id=conversation.id, request_key="design-file-1",
        filename="M250238-P4设计清单.xlsx",
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        size=256, sha256="d" * 64, backend="local", storage_namespace="test",
        object_key="test/design/material.xlsx", storage_version=None,
    )
    db.add(row)
    db.flush()
    return row


def erp_order(version="approval:128:draft", quantity=2):
    return {
        "data": {
            "request": {
                "requestId": 128,
                "requestNo": "DESIGN-REQ-128",
                "moldNo": "M250238-P4",
                "status": "DRAFT",
                "approvalVersion": version,
            },
            "details": [{
                "seq": 1,
                "partNo": "P4-001",
                "partName": "型芯",
                "materialMark": "S136",
                "specification": "200x100x80",
                "quantity": quantity,
                "route": "PURCHASE",
                "status": "READY",
            }],
        }
    }


def setup_context(Session):
    with Session.begin() as db:
        admin = user(db)
        project = m.Project(code="DESIGN-APPROVAL-001", name="设计审批项目", status="ACTIVE")
        db.add(project)
        db.flush()
        definition = workflow(db, admin)
        conversation = m.Conversation(user_id=admin.id, title="设计订单审批")
        db.add(conversation)
        db.flush()
        run = m.Run(
            conversation_id=conversation.id, user_id=admin.id,
            security_version=admin.security_version, prompt="把 ERP 设计订单 128 提交审批",
            status="SUCCEEDED", checkpoint={
                "authorization_hash": fingerprint(db, admin), "agent_permission_mode": "ask",
            },
        )
        db.add(run)
        db.flush()
        blob = uploaded_file(db, admin, conversation)
        db.add(m.RunFile(run_id=run.id, file_id=blob.id))
        return {
            "admin_id": admin.id, "project_id": project.id, "project_version": project.row_version,
            "definition_id": definition.id, "run_id": run.id, "file_id": blob.id,
        }


def args(ids):
    return {
        "project_id": ids["project_id"], "project_version": ids["project_version"],
        "erp_order_id": 128, "design_type": "NEW_MOLD", "drawing_revision": "A1",
        "submission_note": "ERP 设计订单与本轮清单已由设计主管核对",
        "reviewer_id": ids["admin_id"], "workflow_definition_id": ids["definition_id"],
        "file_ids": [ids["file_id"]],
    }


def test_prepare_design_order_approval_freezes_erp_snapshot_and_attachment(monkeypatch):
    from domain_packs.mold.tools.erp.design import design_approval_tools

    calls = []

    def fake_call(name, arguments):
        calls.append((name, arguments))
        return erp_order()

    monkeypatch.setattr(design_approval_tools, "call_mcp", fake_call)
    engine, Session = factory()
    try:
        ids = setup_context(Session)
        parameters = tool_schema("prepare_design_order_approval")["function"]["parameters"]
        assert {"project_id", "project_version", "erp_order_id", "design_type", "drawing_revision",
                "reviewer_id", "workflow_definition_id"} <= set(parameters["properties"])
        with Session.begin() as db:
            admin = db.get(m.User, ids["admin_id"])
            run = db.get(m.Run, ids["run_id"])
            evidence = execute(db, admin, "prepare_design_order_approval", args(ids), run=run)
            assert evidence["proposal"]["display"]["ERP 设计订单"] == "DESIGN-REQ-128"
            assert evidence["proposal"]["display"]["说明"].startswith("本人确认后只冻结")
            assert db.scalar(select(m.BusinessSubject).where(m.BusinessSubject.kind == "design_route")) is None
            step = m.Step(run_id=run.id, sequence=0, tool="prepare_design_order_approval",
                          request_hash="hash", result=evidence)
            db.add(step)
            db.flush()
            payload = {"step_id": step.id, "proposal_hash": bpm.content_hash(evidence["proposal"])}
            intent = business.create_intent(db, admin, "design_approval.execute", step.id, payload)
            receipt = business.confirm_intent(db, admin, intent["id"], intent["challenge"])
            assert receipt["status"] == "SUBMITTED"
            subject = db.get(m.BusinessSubject, receipt["subject_id"])
            assert subject.status == "SUBMITTED"
            detail = db.get(m.DesignDetail, subject.id)
            assert detail.source_system == "management-system"
            assert detail.source_resource_type == "design_order"
            assert detail.source_resource_id == "128"
            assert detail.source_resource_version == "approval:128:draft"
            assert detail.source_snapshot["request"]["requestNo"] == "DESIGN-REQ-128"
            assert detail.source_summary["items"][0]["part_number"] == "P4-001"
            attachment = db.scalar(select(m.DesignAttachment).where(
                m.DesignAttachment.design_subject_id == subject.id
            ))
            assert attachment and attachment.file_id == ids["file_id"] and attachment.version == 1
            instance = db.get(m.ApprovalInstance, receipt["instance_id"])
            frozen = instance.snapshot["detail"]
            assert frozen["erp_order_material"]["resource_version"] == "approval:128:draft"
            assert frozen["erp_order_material"]["document_revision"] == 1
            assert frozen["erp_order_material"]["items"][0]["material"] == "S136"
            assert frozen["attachments"][0]["file_id"] == ids["file_id"]
            assert frozen["attachments"][0]["sha256"] == "d" * 64
        assert all(call == ("get_erp_design_record", {"resource": "design_order", "id": 128}) for call in calls)
        assert len(calls) >= 2
    finally:
        engine.dispose()


def test_design_order_change_invalidates_confirmation(monkeypatch):
    from domain_packs.mold.tools.erp.design import design_approval_tools

    values = [erp_order(), erp_order(version="approval:128:changed", quantity=3)]
    monkeypatch.setattr(design_approval_tools, "call_mcp", lambda _name, _arguments: values.pop(0))
    engine, Session = factory()
    try:
        ids = setup_context(Session)
        with Session.begin() as db:
            admin = db.get(m.User, ids["admin_id"])
            run = db.get(m.Run, ids["run_id"])
            evidence = execute(db, admin, "prepare_design_order_approval", args(ids), run=run)
            step = m.Step(run_id=run.id, sequence=0, tool="prepare_design_order_approval",
                          request_hash="hash", result=evidence)
            db.add(step)
            db.flush()
            payload = {"step_id": step.id, "proposal_hash": bpm.content_hash(evidence["proposal"])}
            intent = business.create_intent(db, admin, "design_approval.execute", step.id, payload)
            try:
                business.confirm_intent(db, admin, intent["id"], intent["challenge"])
                raise AssertionError("confirmation should fail after ERP order changes")
            except Exception as error:
                assert getattr(error, "code", None) == "VERSION_CONFLICT"
            assert db.scalar(select(m.BusinessSubject).where(m.BusinessSubject.kind == "design_route")) is None
    finally:
        engine.dispose()
