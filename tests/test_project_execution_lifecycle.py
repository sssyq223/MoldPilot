from datetime import date, timedelta

from app import models as m
from app.authorization import PERMISSIONS
from app.tool_gateway import execute, tool_schema
from domain_packs.mold.tool_gateway import SKILLS, TOOLS
from domain_packs.mold.tools.erp.project.execution_lifecycle_tools import (
    _assembly_stage,
    _delivery_stage,
    _plan_stage,
    _procurement_stage,
)
from pg_db import factory as pg_factory


def factory():
    return pg_factory()


def test_execution_stage_keeps_latest_failed_recheck_blocking_delivery():
    derived = {
        "has_customer_signature": True,
        "has_customer_acceptance": False,
        "has_failed_customer_acceptance": True,
        "has_customer_recheck_passed": False,
        "has_unresolved_customer_acceptance_failure": True,
    }
    stage = _delivery_stage({"analysis": {"derived_status": derived}})
    assert stage["state"] == "NEEDS_ATTENTION"
    assert stage["facts"]["has_unresolved_customer_acceptance_failure"] is True
    assert "历史通过" in "".join(stage["blockers"])


def test_execution_stage_treats_erp_fulfillment_as_active_and_erp_exception_as_blocking():
    stage = _delivery_stage({"analysis": {"derived_status": {
        "has_erp_fulfillment_record": True,
        "has_erp_delivery_exception": False,
    }}})
    assert stage["state"] == "ACTIVE"
    assert stage["facts"]["has_erp_fulfillment_record"] is True

    blocked = _delivery_stage({"analysis": {"derived_status": {
        "has_erp_fulfillment_record": True,
        "has_erp_delivery_exception": True,
    }}})
    assert blocked["state"] == "NEEDS_ATTENTION"
    assert "ERP 原系统" in "".join(blocked["blockers"])


def test_execution_plan_stage_exposes_draft_project_as_upstream_kickoff_blocker():
    stage = _plan_stage({"analysis": {"derived_status": {
        "project_status": "DRAFT",
        "has_effective_plan": False,
    }, "milestone_coverage": {"missing": []}}})
    assert stage["state"] == "BLOCKED"
    assert "正式开工尚未生效" in "".join(stage["blockers"])


def test_execution_plan_stage_keeps_incomplete_effective_plan_at_baseline_handoff():
    stage = _plan_stage({"analysis": {
        "derived_status": {
            "project_status": "ACTIVE",
            "has_effective_plan": True,
        },
        "active_plan": {"id": "plan-1", "status": "EFFECTIVE"},
        "milestone_coverage": {
            "missing": ["trial"],
            "missing_labels": ["试模/调试"],
        },
    }, "workflow_options": [{"id": "plan-change-flow"}]}, {"prepare_project_plan_change"})
    assert stage["state"] == "NEEDS_ATTENTION"
    assert stage["facts"]["missing_milestones"] == ["trial"]
    assert "试模/调试" in "".join(stage["blockers"])
    assert stage["action_tool"] == "prepare_project_plan_change"


def test_execution_stage_blocks_assembly_when_plan_prerequisite_is_open():
    stage = _assembly_stage(
        {"analysis": {"derived_status": {
            "has_effective_plan": True,
            "has_design_route": True,
            "has_blocked_plan_dependency": True,
        }}},
        "INTERNAL",
    )
    assert stage["state"] == "BLOCKED"
    assert stage["facts"]["has_blocked_plan_dependency"] is True
    assert "未完成前置依赖" in "".join(stage["blockers"])


def test_execution_procurement_stage_treats_erp_purchase_facts_as_active():
    stage = _procurement_stage(
        {"analysis": {"derived_status": {
            "has_erp_purchase_order": True,
            "has_erp_supplier_delivery": False,
            "has_erp_inbound": False,
            "has_erp_stock_flow": False,
        }}},
        None,
    )
    assert stage["state"] == "ACTIVE"
    assert stage["facts"]["has_erp_purchase_order"] is True


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
    names = [
        ("design", "结构设计及出图"),
        ("purchase", "五金采购"),
        ("machining", "工序加工"),
        ("assembly", "装配"),
        ("trial", "试模"),
        ("delivery", "最终交付"),
    ]
    previous = None
    for index, (key, name) in enumerate(names):
        row = m.PlanTask(
            plan_id=subject.id,
            key=key,
            name=name,
            owner_user_id=creator.id,
            planned_start=date.today() + timedelta(days=index * 3),
            planned_end=date.today() + timedelta(days=index * 3 + 2),
            status="PLANNED",
        )
        db.add(row)
        db.flush()
        if previous:
            db.add(m.TaskDependency(task_id=row.id, prerequisite_id=previous.id))
        previous = row
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
            "query_project_kickoff_context",
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
            assert result["scope_boundary"]["complete"] is True
            assert result["scope_boundary"]["scope_key"] == "project_execution"
            assert "prepare_project_plan_baseline" in result["scope_boundary"]["write_tools"]
            assert "prepare_customer_acceptance" in result["scope_boundary"]["write_tools"]
            assert result["scope_boundary"]["read_tools"] == ["query_project_plan_context"]
            assert result["model_context"]["project"]["code"] == "EXEC-EMPTY"
            assert result["model_context"]["execution_lifecycle"]["current_focus"]["key"] == "baseline_plan"
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
            assert lifecycle["entry_handoff"]["state"] == "WAITING"
            handoffs = {row["key"]: row for row in lifecycle["handoffs"]}
            assert handoffs["plan_to_design"]["state"] == "BLOCKED"
            assert handoffs["design_to_procurement"]["state"] in {"WAITING", "NOT_APPLICABLE"}
            assert handoffs["plan_to_manufacturing"]["state"] == "BLOCKED"
    finally:
        engine.dispose()


def test_execution_draft_routes_the_next_step_back_to_kickoff_chain():
    engine, Session = factory()
    try:
        with Session.begin() as db:
            admin = user(db, "admin", True)
            project(db, "EXEC-DRAFT", status="DRAFT")
        with Session() as db:
            admin = db.query(m.User).filter_by(username="admin").one()
            result = execute(
                db,
                admin,
                "query_project_execution_context",
                {"identifier": "EXEC-DRAFT"},
            )
            lifecycle = result["data"][0]["analysis"]["execution_lifecycle"]
            assert lifecycle["current_focus"] == {
                "key": "kickoff_handoff",
                "name": "启动段交接",
                "state": "BLOCKED",
            }
            assert lifecycle["entry_handoff"] == {
                "key": "kickoff_to_execution",
                "from": "project_kickoff",
                "to": "baseline_plan",
                "state": "BLOCKED",
                "reason": "项目仍处于 DRAFT，正式开工尚未生效；启动链路尚未交接到执行链路。",
                "next_query_tool": "query_project_kickoff_context",
            }
            assert lifecycle["recommended_next_steps"][0]["tool"] == "query_project_kickoff_context"
            assert result["scope_boundary"]["read_tools"] == ["query_project_kickoff_context"]
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
            handoffs = {row["key"]: row for row in lifecycle["handoffs"]}
            assert handoffs["plan_to_design"]["state"] == "NOT_APPLICABLE"
            assert handoffs["plan_to_manufacturing"]["state"] == "NOT_APPLICABLE"
            assert handoffs["outsource_to_delivery"]["state"] == "BLOCKED"
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
            assert result["scope_boundary"] == {
                "complete": True,
                "scope_key": "project_execution",
                "write_tools": [],
            }
            assert result["model_context"]["execution_lifecycle"]["current_focus"]["key"] == "visibility"
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
