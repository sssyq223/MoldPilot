from datetime import date, timedelta

from sqlalchemy import select

from app import models as m
from app.authorization import PERMISSIONS
from app.tool_gateway import execute, tool_schema
from domain_packs.mold.erp.project.project_closure import create_case
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


def completed_plan(db, project_row, creator):
    subject = m.BusinessSubject(
        kind="project_plan",
        number="PLAN-" + project_row.code,
        project_id=project_row.id,
        created_by=creator.id,
        status="EFFECTIVE",
    )
    db.add(subject)
    db.flush()
    db.add(m.PlanDetail(subject_id=subject.id, reason="收尾测试基线"))
    db.add(m.PlanTask(
        plan_id=subject.id,
        key="delivery",
        name="交付完成",
        owner_user_id=creator.id,
        planned_start=date.today() - timedelta(days=3),
        planned_end=date.today(),
        status="DONE",
    ))


def lifecycle(result):
    return result["data"][0]["analysis"]["completion_lifecycle"]


def stages(result):
    return {row["key"]: row for row in lifecycle(result)["stages"]}


def test_completion_schema_skill_and_empty_project_are_registered_as_one_coordinator():
    engine, Session = factory()
    try:
        with Session.begin() as db:
            admin = user(db, "admin", True)
            project(db, "CLOSE-EMPTY")
        schema = tool_schema("query_project_completion_context")["function"]["parameters"]
        assert {"project_id", "identifier"} <= set(schema["properties"])
        assert TOOLS["query_project_completion_context"]["permission"] == "project.read"
        skill = SKILLS["project_completion_orchestration"]
        assert skill["tools"] == ["query_project_completion_context"]
        assert skill["activation_tools"] == ["query_project_completion_context"]
        assert "项目收尾链路" in skill["auto_activation_queries"]
        assert skill["suppress_tool_search_on_auto_activation"] is True

        with Session() as db:
            admin = db.query(m.User).filter_by(username="admin").one()
            runtime_skill = next(
                item for item in skill_context(db, admin)
                if item["key"] == "project_completion_orchestration"
            )
            assert "项目收尾链路" in runtime_skill["auto_activation_queries"]
            assert runtime_skill["suppress_tool_search_on_auto_activation"] is True
            assert runtime_skill["activation_tools"] == ["query_project_completion_context"]
            assert "收尾链路" in runtime_skill["route_terms"]

        with Session() as db:
            admin = db.query(m.User).filter_by(username="admin").one()
            result = execute(db, admin, "query_project_completion_context", {"identifier": "CLOSE-EMPTY"})
            data = lifecycle(result)
            assert result["resolution"] == "RESOLVED"
            assert data["kind"] == "project_completion_lifecycle_v1"
            assert [row["key"] for row in data["stages"]] == [
                "delivery_acceptance",
                "customer_finance",
                "supplier_settlement",
                "issue_resolution",
                "archive",
                "final_close",
            ]
            assert data["closure_mode"] == "NORMAL"
            assert data["current_focus"]["key"] == "delivery_acceptance"
            assert data["recommended_next_steps"][0]["tool"] == "query_delivery_logistics_context"
            final_close = stages(result)["final_close"]
            assert final_close["state"] == "NOT_STARTED"
            assert final_close["action_tool"] == "prepare_project_closure_checklist"
            assert "不代表项目已经可以关闭" in final_close["blockers"][0]
    finally:
        engine.dispose()


def test_completion_normal_path_requires_each_stage_then_offers_normal_close():
    engine, Session = factory()
    try:
        with Session.begin() as db:
            admin = user(db, "admin", True)
            project_row = project(db, "CLOSE-NORMAL")
            completed_plan(db, project_row, admin)
            db.flush()
            case = create_case(db, admin, project_row, "NORMAL", "项目收尾")
            for item in db.scalars(select(m.ProjectClosureItem).where(m.ProjectClosureItem.case_id == case.id)):
                item.status = "DONE"
                item.result = "测试依据已核对"
                item.evidence = "unit-test"

        with Session() as db:
            admin = db.query(m.User).filter_by(username="admin").one()
            result = execute(db, admin, "query_project_completion_context", {"identifier": "CLOSE-NORMAL"})
            data = lifecycle(result)
            by_key = stages(result)
            assert by_key["delivery_acceptance"]["state"] == "COMPLETED"
            assert by_key["customer_finance"]["state"] == "COMPLETED"
            assert by_key["supplier_settlement"]["state"] == "COMPLETED"
            assert by_key["issue_resolution"]["state"] == "COMPLETED"
            assert by_key["archive"]["state"] == "COMPLETED"
            assert by_key["final_close"]["state"] == "READY"
            assert by_key["final_close"]["action_tool"] == "prepare_project_normal_close"
            assert data["current_focus"]["key"] == "final_close"
            assert data["recommended_next_steps"][0]["tool"] == "prepare_project_normal_close"
            assert data["recommended_next_steps"][0]["requires_user_confirmation"] is True
    finally:
        engine.dispose()


def test_completion_termination_path_allows_explicit_delivery_disposition_but_not_finance_inference():
    engine, Session = factory()
    try:
        with Session.begin() as db:
            admin = user(db, "admin", True)
            project_row = project(db, "CLOSE-TERMINATED", "TERMINATED")
            case = create_case(
                db,
                admin,
                project_row,
                "TERMINATION",
                "采购执行",
                seed={
                    "evidence": "客户终止函",
                    "current_stage": "采购执行",
                    "completed_work_summary": "设计已完成",
                    "incurred_cost_summary": "已发生设计成本",
                },
            )
            for item in db.scalars(select(m.ProjectClosureItem).where(m.ProjectClosureItem.case_id == case.id)):
                if item.item_key in {"DELIVERY_DISPOSITION", "ACCEPTANCE_DISPOSITION"}:
                    item.status = "NOT_APPLICABLE"
                    item.result = "终止时尚未进入交付验收"
                    item.evidence = "终止申请记录"

        with Session() as db:
            admin = db.query(m.User).filter_by(username="admin").one()
            result = execute(db, admin, "query_project_completion_context", {"identifier": "CLOSE-TERMINATED"})
            data = lifecycle(result)
            by_key = stages(result)
            assert data["closure_mode"] == "TERMINATION"
            assert by_key["delivery_acceptance"]["state"] == "NOT_APPLICABLE"
            assert by_key["customer_finance"]["state"] == "BLOCKED"
            assert data["current_focus"]["key"] == "customer_finance"
            assert by_key["final_close"]["state"] == "BLOCKED"
            assert by_key["final_close"]["action_tool"] is None
    finally:
        engine.dispose()


def test_completion_keeps_unassigned_stages_unread_and_does_not_leak_stage_facts():
    engine, Session = factory()
    try:
        with Session.begin() as db:
            admin = user(db, "admin", True)
            operator = user(db, "operator")
            project_row = project(db, "CLOSE-LIMITED")
            completed_plan(db, project_row, admin)
            grant(db, admin, operator, "project.read", project_row.id)
            capability(db, operator, "query_project_completion_context")
            capability(db, operator, "project_completion_orchestration", "SKILL")
        with Session() as db:
            operator = db.query(m.User).filter_by(username="operator").one()
            result = execute(db, operator, "query_project_completion_context", {"identifier": "CLOSE-LIMITED"})
            data = lifecycle(result)
            assert {row["state"] for row in data["stages"]} == {"UNAVAILABLE"}
            assert data["current_focus"] == {
                "key": "visibility",
                "name": "收尾链路可见性",
                "state": "UNAVAILABLE",
            }
            assert data["access_gaps"] == [
                "交付/签收/客户验收",
                "发票/收付款/供应商结算",
                "结项清单/归档/最终关闭",
            ]
            assert "PLAN-CLOSE-LIMITED" not in str(result)
    finally:
        engine.dispose()


def test_completion_reports_multiple_projects_without_merging_stage_facts():
    engine, Session = factory()
    try:
        with Session.begin() as db:
            admin = user(db, "admin", True)
            project(db, "CLOSE-A")
            project(db, "CLOSE-B")
        with Session() as db:
            admin = db.query(m.User).filter_by(username="admin").one()
            result = execute(db, admin, "query_project_completion_context", {"identifier": "CLOSE"})
            assert result["resolution"] == "MULTIPLE_CANDIDATES"
            assert {row["code"] for row in result["data"]} == {"CLOSE-A", "CLOSE-B"}
            assert all("analysis" not in row for row in result["data"])
    finally:
        engine.dispose()
