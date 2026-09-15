from datetime import date, timedelta
from decimal import Decimal

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app import models as m
from app.authorization import PERMISSIONS
from app.models import Base
from app.tool_gateway import execute, tool_schema


def factory():
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)
    Session = sessionmaker(engine, expire_on_commit=False)
    return engine, Session


def user(db, username="operator", super_admin=False):
    row = m.User(username=username, display_name=username, password_hash="test", super_admin=super_admin)
    db.add(row)
    db.flush()
    return row


def project(db, code, name="制造质检项目", status="ACTIVE"):
    row = m.Project(code=code, name=name, status=status)
    db.add(row)
    db.flush()
    return row


def grant(db, admin, target, permission, project_id, fields=None):
    db.add(
        m.Grant(
            user_id=target.id,
            permission=permission,
            effect="ALLOW",
            scope={"project_id": [project_id]},
            fields=fields or PERMISSIONS[permission],
            reason="unit test",
            granted_by=admin.id,
        )
    )


def capability(db, target, key, kind="TOOL"):
    db.add(m.Capability(user_id=target.id, kind=kind, key=key, enabled=True))


def plan(db, project, creator):
    today = date.today()
    subject = m.BusinessSubject(kind="project_plan", number="PLAN-" + project.code, project_id=project.id, created_by=creator.id, status="EFFECTIVE")
    db.add(subject)
    db.flush()
    db.add(m.PlanDetail(subject_id=subject.id, reason="制造质检计划"))
    machining = m.PlanTask(
        plan_id=subject.id,
        key="machining",
        name="CNC加工工序",
        owner_user_id=creator.id,
        planned_start=today,
        planned_end=today + timedelta(days=2),
        actual_start=today,
        status="RUNNING",
    )
    assembly = m.PlanTask(
        plan_id=subject.id,
        key="assembly",
        name="装配",
        owner_user_id=creator.id,
        planned_start=today + timedelta(days=3),
        planned_end=today + timedelta(days=4),
        status="PLANNED",
    )
    db.add_all([machining, assembly])
    db.flush()
    return subject, machining, assembly


def material(db, code="STEEL-SECRET", name="保密钢料"):
    row = m.Material(code=code, name=name, category="raw_material", unit="KG")
    db.add(row)
    db.flush()
    return row


def design(db, project, creator, task, mat):
    subject = m.BusinessSubject(kind="design_route", number="DESIGN-" + project.code, project_id=project.id, created_by=creator.id, status="EFFECTIVE")
    db.add(subject)
    db.flush()
    db.add(m.DesignDetail(subject_id=subject.id, design_type="NEW_MOLD", drawing_revision="R1", drawing_evidence="图纸依据", reviewer_id=creator.id))
    db.add(m.DesignItem(design_id=subject.id, material_id=mat.id, quantity=Decimal("2"), route="INTERNAL", task_id=task.id))
    return subject


def contact_issue(db, project, creator, task):
    group = m.AssignmentGroup(kind="DEPARTMENT", name="质检部")
    db.add(group)
    db.flush()
    case = m.ContactCase(
        project_id=project.id,
        category="raw_material",
        title="加工尺寸异常",
        description="尺寸超差",
        mode="ONLINE",
        created_by=creator.id,
        request_key="case-" + project.code,
        request_hash="hash",
        problem_source="QUALITY_ISSUE",
        current_stage="加工",
        change_type="EXCEPTION",
        urgency="URGENT",
    )
    db.add(case)
    db.flush()
    db.add(
        m.ContactTask(
            case_id=case.id,
            department_id=group.id,
            title="整改并复检",
            created_by=creator.id,
            status="ASSIGNED",
            affected_type="PLAN_NODE",
            affected_ref=task.id,
            impact_description="加工节点需要返工复检",
            planned_action="REWORK",
        )
    )
    return case


def test_manufacturing_quality_schema_and_context_summary():
    engine, Session = factory()
    try:
        with Session.begin() as db:
            admin = user(db, "admin", True)
            p = project(db, "MFQ-M001")
            _, machining, _ = plan(db, p, admin)
            mat = material(db)
            design(db, p, admin, machining, mat)
            contact_issue(db, p, admin, machining)
        schema = tool_schema("query_manufacturing_quality_context")["function"]["parameters"]
        assert {"project_id", "identifier"} <= set(schema["properties"])
        with Session() as db:
            admin = db.query(m.User).filter_by(username="admin").one()
            result = execute(db, admin, "query_manufacturing_quality_context", {"identifier": "MFQ-M001"})
            assert result["resolution"] == "RESOLVED"
            analysis = result["data"][0]["analysis"]
            assert analysis["derived_status"]["has_process_task"] is True
            assert analysis["derived_status"]["has_start_report"] is True
            assert analysis["derived_status"]["has_finish_report"] is False
            assert analysis["derived_status"]["has_independent_quality_report"] is False
            assert analysis["derived_status"]["has_open_quality_or_rework_contact"] is True
            assert analysis["design_internal_route_items"][0]["material_code"] == "STEEL-SECRET"
            assert "检测报告" in "".join(analysis["gaps"])
    finally:
        engine.dispose()


def test_manufacturing_quality_does_not_leak_design_without_design_tool():
    engine, Session = factory()
    try:
        with Session.begin() as db:
            admin = user(db, "admin", True)
            operator = user(db)
            p = project(db, "MFQ-LIMITED")
            _, machining, _ = plan(db, p, admin)
            mat = material(db, "SECRET-MAT", "秘密物料")
            design(db, p, admin, machining, mat)
            grant(db, admin, operator, "project.read", p.id)
            grant(db, admin, operator, "project_plan.read", p.id)
            capability(db, operator, "query_manufacturing_quality_context")
        with Session() as db:
            operator = db.query(m.User).filter_by(username="operator").one()
            result = execute(db, operator, "query_manufacturing_quality_context", {"identifier": "MFQ-LIMITED"})
            analysis = result["data"][0]["analysis"]
            assert analysis["derived_status"]["has_process_task"] is True
            assert analysis["design_internal_route_items"] == []
            assert "SECRET-MAT" not in str(result)
            assert "设计BOM" in "".join(result["limitations"])
    finally:
        engine.dispose()


def test_manufacturing_quality_reports_multiple_candidates_without_deciding():
    engine, Session = factory()
    try:
        with Session.begin() as db:
            admin = user(db, "admin", True)
            p1 = project(db, "MFQ-A", "共同制造项目A")
            p2 = project(db, "MFQ-B", "共同制造项目B")
            plan(db, p1, admin)
            plan(db, p2, admin)
        with Session() as db:
            admin = db.query(m.User).filter_by(username="admin").one()
            result = execute(db, admin, "query_manufacturing_quality_context", {"identifier": "共同制造项目"})
            assert result["resolution"] == "MULTIPLE_CANDIDATES"
            assert {row["code"] for row in result["data"]} == {"MFQ-A", "MFQ-B"}
            assert "请使用项目 ID" in "".join(result["limitations"])
    finally:
        engine.dispose()
