from datetime import timedelta


from app import models as m
from app.authorization import PERMISSIONS
from app.db import now
from pg_db import factory as pg_factory
from app.tool_gateway import execute, tool_schema


def factory():
    return pg_factory()


def user(db, username="operator", super_admin=False):
    row = m.User(username=username, display_name=username, password_hash="test", super_admin=super_admin)
    db.add(row)
    db.flush()
    return row


def grant(db, admin, target, permission, project_id=None, category=None, fields=None):
    scope = {}
    if project_id:
        scope["project_id"] = [project_id]
    if category:
        scope["category"] = [category]
    db.add(
        m.Grant(
            user_id=target.id,
            permission=permission,
            effect="ALLOW",
            scope=scope or {"all": True},
            fields=fields or PERMISSIONS[permission],
            reason="治理核对测试授权",
            granted_by=admin.id,
        )
    )


def capability(db, target, key, kind="TOOL"):
    db.add(m.Capability(user_id=target.id, kind=kind, key=key, enabled=True))


def seed_governance_project(db):
    admin = user(db, "admin", True)
    operator = user(db)
    project = m.Project(code="GOV-M001", name="治理核对项目", status="ACTIVE")
    group = m.AssignmentGroup(kind="DEPARTMENT", name="工程部")
    db.add_all([project, group])
    db.flush()
    grant(db, admin, operator, "project.read", project.id)
    grant(db, admin, operator, "audit.read")
    grant(db, admin, operator, "file.upload")
    capability(db, operator, "query_governance_context")
    case = m.ContactCase(
        project_id=project.id,
        category="outsource",
        title="治理联络单",
        description="核对附件、通知和来源",
        mode="ONLINE",
        created_by=admin.id,
        request_key="gov-case",
        request_hash="hash",
        current_stage="执行",
    )
    db.add(case)
    db.flush()
    task = m.ContactTask(
        case_id=case.id,
        department_id=group.id,
        title="核对 ERP 来源",
        created_by=admin.id,
        status="RESPONDED",
        affected_type="OTHER",
        affected_ref="ERP-GOV-M001",
        impact_description="ERP 来源状态核对",
        planned_action="CONTINUE",
        source_system="ERP",
        source_ref="ERP-TASK-GOV-M001",
        source_as_of=now(),
        execution_source_system="ERP",
        execution_source_ref="ERP-FINISH-GOV-M001",
        execution_source_as_of=now(),
    )
    db.add(task)
    conversation = m.Conversation(user_id=admin.id, title="治理证据附件")
    db.add(conversation)
    db.flush()
    blob = m.FileObject(
        owner_id=admin.id,
        conversation_id=conversation.id,
        request_key="file-gov",
        filename="治理证据.pdf",
        media_type="application/pdf",
        size=12,
        sha256="a" * 64,
        backend="s3",
        storage_namespace="private-test",
        object_key="gov/key",
        storage_version="v1",
    )
    db.add(blob)
    db.flush()
    link = m.ContactAttachment(case_id=case.id, file_id=blob.id, document_id="doc-gov", version=1, title="治理证据", previous_id=None, created_by=admin.id)
    db.add(link)
    event = m.Outbox(kind="contact.assigned", resource_id=case.id, payload={"recipients": [operator.id], "action": "contact.assigned"}, published_at=now())
    db.add(event)
    db.flush()
    db.add(m.Inbox(event_id=event.id))
    db.add(m.Notification(event_id=event.id, user_id=operator.id, title="有新的工程联络事项待处理", resource_id=case.id, read=False))
    db.add(m.AuditEvent(user_id=admin.id, action="contact.assigned", resource_id=case.id, detail={"before": "OPEN", "after": "ASSIGNED", "reason": "测试分派"}))
    intent = m.HumanIntent(
        user_id=admin.id,
        action="erp.dispatch",
        resource_id=case.id,
        payload={"project_id": project.id},
        payload_hash="p" * 64,
        challenge_hash="c" * 64,
        expires_at=now() + timedelta(minutes=5),
        receipt=None,
    )
    db.add(intent)
    db.flush()
    db.add(
        m.ERPOperation(
            user_id=admin.id,
            intent_id=intent.id,
            action="update_contact_task",
            native_id="ERP-GOV-M001",
            state="UNKNOWN",
            request_hash="r" * 64,
            erp_user_id="ERP-ADMIN",
            response=None,
            error_code="TIMEOUT",
        )
    )
    return admin, operator, project, case


def test_governance_context_schema_and_evidence():
    engine, Session = factory()
    try:
        with Session.begin() as db:
            admin, operator, project, case = seed_governance_project(db)
            operator_id = operator.id
        schema = tool_schema("query_governance_context")["function"]["parameters"]
        assert {"project_id", "identifier", "target_user_id"} <= set(schema["properties"])
        with Session() as db:
            admin = db.query(m.User).filter_by(username="admin").one()
            result = execute(db, admin, "query_governance_context", {"identifier": "GOV-M001", "target_user_id": operator_id})
            assert result["resolution"] == "RESOLVED"
            data = result["data"][0]
            assert data["project"]["code"] == "GOV-M001"
            assert data["permission_boundary"]["summary_must_not_bypass_fields"] is True
            assert data["permission_boundary"]["attachment_links_require_business_read_after_linking"] is True
            assert any(grant["permission"] == "audit.read" for grant in data["target_authorization"]["active_grants"])
            assert any(row["permission"] == "project.read" and row["allowed"] for row in data["permission_matrix"])
            assert data["attachments"][0]["file"]["filename"] == "治理证据.pdf"
            assert data["attachments"][0]["file"]["storage_versioned"] is True
            assert data["notifications"][0]["delivered"] is True
            assert data["notifications"][0]["unread_count"] == 1
            assert data["audit_trail"][0]["action"] == "contact.assigned"
            assert data["source_responsibility"]["erp_operations"][0]["state"] == "UNKNOWN"
            assert data["source_responsibility"]["pending_or_failed_source_operations"][0]["error_code"] == "TIMEOUT"
            assert "不维护 ERP 镜像" in data["source_responsibility"]["authority_strategy"]
            assert "不下载附件" in "".join(result["limitations"])
    finally:
        engine.dispose()


def test_governance_context_does_not_expose_contact_files_without_contact_read():
    engine, Session = factory()
    try:
        with Session.begin() as db:
            _, operator, _, _ = seed_governance_project(db)
        with Session() as db:
            operator = db.query(m.User).filter_by(username="operator").one()
            result = execute(db, operator, "query_governance_context", {"identifier": "GOV-M001"})
            assert result["resolution"] == "RESOLVED"
            data = result["data"][0]
            assert data["attachments"] == []
            assert data["visible_resources"]["contact_cases"] == []
            assert "治理证据.pdf" not in str(result)
    finally:
        engine.dispose()

