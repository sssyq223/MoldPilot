from datetime import date, timedelta
from decimal import Decimal

from app import models as m
from app.authorization import PERMISSIONS
from app.tool_gateway import execute, tool_schema
from domain_packs.mold.tool_gateway import SKILLS, TOOLS, skill_context
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


def project(db, code, status="DRAFT"):
    row = m.Project(code=code, name=f"{code} 项目", status=status)
    db.add(row)
    db.flush()
    return row


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


def capability(db, target, key, kind="TOOL"):
    db.add(m.Capability(user_id=target.id, kind=kind, key=key, enabled=True))


def decision(db, project_row, creator, kind, number, decision_value, source_subject_id=None):
    subject = m.BusinessSubject(
        kind=kind,
        number=number,
        project_id=project_row.id,
        created_by=creator.id,
        status="EFFECTIVE",
    )
    db.add(subject)
    db.flush()
    db.add(m.BusinessDecisionDetail(
        subject_id=subject.id,
        source_subject_id=source_subject_id,
        decision=decision_value,
        execution_mode="INTERNAL",
        effective_date=date.today(),
        evidence="人工确认依据",
        amount=Decimal("100.00"),
        currency="CNY",
    ))
    return subject


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
    db.add(m.PlanDetail(subject_id=subject.id, reason="全生命周期测试基线"))
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


def lifecycle(result):
    return result["data"][0]["analysis"]["project_lifecycle"]


def segments(result):
    return {row["key"]: row for row in lifecycle(result)["segments"]}


def test_lifecycle_schema_skill_and_fresh_project_use_one_hierarchical_coordinator():
    engine, Session = factory()
    try:
        with Session.begin() as db:
            admin = user(db, "admin", True)
            project(db, "LIFECYCLE-FRESH")

        schema = tool_schema("query_project_lifecycle_context")["function"]["parameters"]
        assert {"project_id", "identifier"} <= set(schema["properties"])
        assert TOOLS["query_project_lifecycle_context"]["permission"] == "project.read"
        skill = SKILLS["project_lifecycle_orchestration"]
        assert skill["tools"] == ["query_project_lifecycle_context"]
        assert skill["activation_tools"] == ["query_project_lifecycle_context"]
        assert set(skill["optional_tools"]) == {
            "query_project_kickoff_context",
            "query_project_execution_context",
            "query_project_completion_context",
            "query_project_control_context",
        }
        assert "项目全生命周期" in skill["auto_activation_queries"]
        assert skill["suppress_tool_search_on_auto_activation"] is True

        with Session() as db:
            admin = db.query(m.User).filter_by(username="admin").one()
            runtime_skill = next(
                item for item in skill_context(db, admin)
                if item["key"] == "project_lifecycle_orchestration"
            )
            assert runtime_skill["activation_tools"] == ["query_project_lifecycle_context"]
            assert runtime_skill["suppress_tool_search_on_auto_activation"] is True
            assert "项目全生命周期" in runtime_skill["auto_activation_queries"]

            result = execute(db, admin, "query_project_lifecycle_context", {"identifier": "LIFECYCLE-FRESH"})
            data = lifecycle(result)
            assert result["resolution"] == "RESOLVED"
            assert data["kind"] == "project_lifecycle_overview_v1"
            assert [row["key"] for row in data["segments"]] == ["kickoff", "execution", "completion"]
            assert data["current_segment"]["key"] == "kickoff"
            assert data["current_segment"]["focus"]["key"] == "quotation"
            assert data["recommended_next_steps"][0]["tool"] == "query_project_kickoff_context"
    finally:
        engine.dispose()


def test_lifecycle_enters_execution_after_core_kickoff_without_hiding_parallel_contract_gap():
    engine, Session = factory()
    try:
        with Session.begin() as db:
            admin = user(db, "admin", True)
            project_row = project(db, "LIFECYCLE-ACTIVE", "ACTIVE")
            acceptance = decision(db, project_row, admin, "quote_acceptance", "QA-LIFECYCLE", "ACCEPT")
            decision(db, project_row, admin, "internal_start", "START-LIFECYCLE", "START", acceptance.id)
            baseline(db, project_row, admin)

        with Session() as db:
            admin = db.query(m.User).filter_by(username="admin").one()
            result = execute(db, admin, "query_project_lifecycle_context", {"identifier": "LIFECYCLE-ACTIVE"})
            data = lifecycle(result)
            by_key = segments(result)
            assert data["current_segment"]["key"] == "execution"
            assert data["recommended_next_steps"][0]["tool"] == "query_project_execution_context"
            assert by_key["kickoff"]["state"] == "COMPLETED"
            assert by_key["kickoff"]["progress"]["completed_count"] == 3
            assert by_key["kickoff"]["progress"]["stage_count"] == 5
            assert by_key["kickoff"]["progress"]["not_applicable_count"] == 1
            assert by_key["execution"]["focus"]["key"] == "design_route"
    finally:
        engine.dispose()


def test_lifecycle_paused_project_routes_to_project_control_before_normal_execution():
    engine, Session = factory()
    try:
        with Session.begin() as db:
            admin = user(db, "admin", True)
            project(db, "LIFECYCLE-PAUSED", "PAUSED")
        with Session() as db:
            admin = db.query(m.User).filter_by(username="admin").one()
            result = execute(db, admin, "query_project_lifecycle_context", {"identifier": "LIFECYCLE-PAUSED"})
            data = lifecycle(result)
            assert data["current_segment"]["key"] == "project_control"
            assert data["current_segment"]["state"] == "PAUSED"
            assert data["recommended_next_steps"][0]["tool"] == "query_project_control_context"
    finally:
        engine.dispose()


def test_lifecycle_marks_active_project_with_missing_kickoff_evidence_as_consistency_gap():
    engine, Session = factory()
    try:
        with Session.begin() as db:
            admin = user(db, "admin", True)
            project(db, "LIFECYCLE-CONFLICT", "ACTIVE")
        with Session() as db:
            admin = db.query(m.User).filter_by(username="admin").one()
            result = execute(db, admin, "query_project_lifecycle_context", {"identifier": "LIFECYCLE-CONFLICT"})
            data = lifecycle(result)
            assert data["current_segment"]["key"] == "execution"
            assert any("启动链路尚未证明" in item for item in data["consistency_warnings"])
            assert segments(result)["kickoff"]["state"] != "COMPLETED"
    finally:
        engine.dispose()


def test_lifecycle_keeps_unassigned_segment_capabilities_unread_and_secret_facts_hidden():
    engine, Session = factory()
    try:
        with Session.begin() as db:
            admin = user(db, "admin", True)
            operator = user(db, "operator")
            project_row = project(db, "LIFECYCLE-LIMITED")
            secret = m.BusinessSubject(
                kind="project_plan",
                number="SECRET-LIFECYCLE-PLAN",
                project_id=project_row.id,
                created_by=admin.id,
                status="EFFECTIVE",
            )
            db.add(secret)
            db.flush()
            db.add(m.PlanDetail(subject_id=secret.id, reason="SECRET-LIFECYCLE-DETAIL"))
            grant(db, admin, operator, "project.read", project_row.id)
            capability(db, operator, "query_project_lifecycle_context")
            capability(db, operator, "project_lifecycle_orchestration", "SKILL")

        with Session() as db:
            operator = db.query(m.User).filter_by(username="operator").one()
            result = execute(db, operator, "query_project_lifecycle_context", {"identifier": "LIFECYCLE-LIMITED"})
            data = lifecycle(result)
            assert {row["state"] for row in data["segments"]} == {"UNAVAILABLE"}
            assert data["current_segment"]["key"] == "kickoff"
            assert data["recommended_next_steps"] == []
            assert len(data["access_gaps"]) == 3
            assert "SECRET-LIFECYCLE" not in str(result)
    finally:
        engine.dispose()


def test_lifecycle_reports_multiple_projects_without_merging_segment_facts():
    engine, Session = factory()
    try:
        with Session.begin() as db:
            admin = user(db, "admin", True)
            project(db, "LIFECYCLE-A")
            project(db, "LIFECYCLE-B")
        with Session() as db:
            admin = db.query(m.User).filter_by(username="admin").one()
            result = execute(db, admin, "query_project_lifecycle_context", {"identifier": "LIFECYCLE"})
            assert result["resolution"] == "MULTIPLE_CANDIDATES"
            assert {row["code"] for row in result["data"]} == {"LIFECYCLE-A", "LIFECYCLE-B"}
            assert all("analysis" not in row for row in result["data"])
    finally:
        engine.dispose()
