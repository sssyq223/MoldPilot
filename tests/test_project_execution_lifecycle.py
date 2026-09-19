from datetime import date, timedelta

from app import models as m
from app.authorization import PERMISSIONS
from app.tool_gateway import execute, tool_schema
from domain_packs.mold.tool_gateway import SKILLS, TOOLS
from pg_db import factory as pg_factory


def factory():
    return pg_factory()


def user(db, username="operator", super_admin=False):
    row = m.User(
        username=username,
        display_name=username,
        password_hash="test",
        super_admin=super_admin,
    )
    db.add(row)
    db.flush()
    return row


def project(db, code, status="ACTIVE"):
    row = m.Project(code=code, name=f"{code} 项目", status=status)
    db.add(row)
    db.flush()
    return row


def capability(db, target, key, kind="TOOL"):
    db.add(m.Capability(user_id=target.id, kind=kind, key=key, enabled=True))


def grant(db, admin, target, permission, project_id):
    db.add(m.Grant(
        user_id=target.id,
        permission=permission,
        effect="ALLOW",
        scope={"project_id": [project_id]},
        fields=PERMISSIONS[permission],
        reason="unit test",
        granted_by=admin.id,
    ))


def baseline(db, project_row, creator):
    subject = m.BusinessSubject(
        kind="project_plan",
        number="PLAN-" + project_row.code,
        project_id=project_row.id,
        created_by=creator.id,
        status="EFFECTIVE",
    )
    db.add(subject)
    db.flush()
    db.add(m.PlanDetail(subject_id=subject.id, reason="执行链路基线"))
    db.add(m.PlanTask(
        plan_id=subject.id,
        key="design",
        name="结构设计",
        owner_user_id=creator.id,
        planned_start=date.today(),
        planned_end=date.today() + timedelta(days=3),
        status="PLANNED",
    ))
    return subject


def stages(result):
    rows = result["data"][0]["analysis"]["execution_lifecycle"]["stages"]
    return {row["key"]: row for row in rows}


def test_execution_schema_skill_and_empty_project_are_registered_as_one_coordinator():
    engine, Session = factory()
    try:
        with Session.begin() as db:
            admin = user(db, "admin", True)
            project(db, "EXEC-EMPTY")
        schema = tool_schema("query_project_execution_context")["function"]["parameters"]
        assert {"project_id", "identifier"} <= set(schema["properties"])
        assert TOOLS["query_project_execution_context"]["permission"] == "project.read"
        skill = SKILLS["project_execution_orchestration"]
        assert skill["tools"] == ["query_project_execution_context"]
        assert skill["activation_tools"] == ["query_project_execution_context"]
        assert "项目执行链路" in skill["auto_activation_queries"]
        assert skill["suppress_tool_search_on_auto_activation"] is True
        assert {
            "query_project_plan_context",
            "query_design_route_context",
            "query_procurement_price_context",
            "query_full_outsource_context",
            "query_manufacturing_quality_context",
            "query_assembly_trial_context",
            "query_delivery_logistics_context",
        } == set(skill["optional_tools"])

        with Session() as db:
            admin = db.query(m.User).filter_by(username="admin").one()
            result = execute(db, admin, "query_project_execution_context", {"identifier": "EXEC-EMPTY"})
            lifecycle = result["data"][0]["analysis"]["execution_lifecycle"]
            assert result["resolution"] == "RESOLVED"
            assert lifecycle["kind"] == "project_execution_lifecycle_v1"
            assert [row["key"] for row in lifecycle["stages"]] == [
                "baseline_plan",
                "design_route",
                "procurement",
                "full_outsource",
                "manufacturing_quality",
                "assembly_trial",
                "delivery_acceptance",
            ]
            assert lifecycle["current_focus"]["key"] == "baseline_plan"
            assert lifecycle["recommended_next_steps"][0]["tool"] == "query_project_plan_context"
    finally:
        engine.dispose()


def test_execution_full_outsource_mode_replaces_internal_execution_branches_without_hiding_delivery():
    engine, Session = factory()
    try:
        with Session.begin() as db:
            admin = user(db, "admin", True)
            project_row = project(db, "EXEC-OUTSOURCE")
            db.add(m.ProjectProfile(
                project_id=project_row.id,
                owner_user_id=admin.id,
                execution_mode="FULL_OUTSOURCE",
            ))
            baseline(db, project_row, admin)
        with Session() as db:
            admin = db.query(m.User).filter_by(username="admin").one()
            result = execute(db, admin, "query_project_execution_context", {"identifier": "EXEC-OUTSOURCE"})
            lifecycle = result["data"][0]["analysis"]["execution_lifecycle"]
            by_key = stages(result)
            assert lifecycle["execution_mode"] == "FULL_OUTSOURCE"
            assert by_key["baseline_plan"]["state"] == "ACTIVE"
            assert by_key["full_outsource"]["state"] == "BLOCKED"
            assert by_key["manufacturing_quality"]["state"] == "NOT_APPLICABLE"
            assert by_key["assembly_trial"]["state"] == "NOT_APPLICABLE"
            assert by_key["delivery_acceptance"]["state"] != "NOT_APPLICABLE"
            assert lifecycle["current_focus"]["key"] == "full_outsource"
    finally:
        engine.dispose()


def test_execution_keeps_unassigned_stages_unread_and_does_not_leak_stage_facts():
    engine, Session = factory()
    try:
        with Session.begin() as db:
            admin = user(db, "admin", True)
            operator = user(db, "operator")
            project_row = project(db, "EXEC-LIMITED")
            secret = m.BusinessSubject(
                kind="project_plan",
                number="SECRET-EXECUTION-PLAN",
                project_id=project_row.id,
                created_by=admin.id,
                status="EFFECTIVE",
            )
            db.add(secret)
            db.flush()
            db.add(m.PlanDetail(subject_id=secret.id, reason="SECRET-EXECUTION-DETAIL"))
            grant(db, admin, operator, "project.read", project_row.id)
            capability(db, operator, "query_project_execution_context")
            capability(db, operator, "project_execution_orchestration", "SKILL")
        with Session() as db:
            operator = db.query(m.User).filter_by(username="operator").one()
            result = execute(db, operator, "query_project_execution_context", {"identifier": "EXEC-LIMITED"})
            lifecycle = result["data"][0]["analysis"]["execution_lifecycle"]
            assert {row["state"] for row in lifecycle["stages"]} == {"UNAVAILABLE"}
            assert lifecycle["current_focus"] == {
                "key": "visibility",
                "name": "执行链路可见性",
                "state": "UNAVAILABLE",
            }
            assert lifecycle["access_gaps"] == [
                "基线计划",
                "设计/BOM/路线",
                "采购/价格/订单",
                "整套委外",
                "制造/质检",
                "装配/试模",
                "交付/签收/验收",
            ]
            assert "SECRET-EXECUTION" not in str(result)
    finally:
        engine.dispose()


def test_execution_reports_multiple_projects_without_merging_stage_facts():
    engine, Session = factory()
    try:
        with Session.begin() as db:
            admin = user(db, "admin", True)
            project(db, "EXEC-A")
            project(db, "EXEC-B")
        with Session() as db:
            admin = db.query(m.User).filter_by(username="admin").one()
            result = execute(db, admin, "query_project_execution_context", {"identifier": "EXEC"})
            assert result["resolution"] == "MULTIPLE_CANDIDATES"
            assert {row["code"] for row in result["data"]} == {"EXEC-A", "EXEC-B"}
            assert all("analysis" not in row for row in result["data"])
    finally:
        engine.dispose()
