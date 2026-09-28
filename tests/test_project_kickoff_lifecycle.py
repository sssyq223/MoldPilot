from datetime import date, timedelta
from decimal import Decimal

from app import bpm, models as m
from app.authorization import PERMISSIONS
from app.tool_gateway import execute, tool_schema
from pg_db import factory as pg_factory
from domain_packs.mold.tools.erp.project.kickoff_lifecycle_tools import (
    ProjectKickoffContextInput,
    _contract_stage,
    _plan_stage,
    _recommendations,
    query as kickoff_query,
)


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


def test_plan_recommendation_explains_missing_nodes_before_first_baseline():
    def stage(key, state, *, action_tool=None, facts=None):
        return {
            "key": key,
            "state": state,
            "query_tool": f"query_{key}",
            "action_tool": action_tool,
            "facts": facts or {},
        }

    stages = [
        stage("quotation", "COMPLETED"),
        stage("bid_intake", "COMPLETED"),
        stage("acceptance", "COMPLETED"),
        stage("contract", "COMPLETED"),
        stage("internal_start", "COMPLETED"),
        stage(
            "project_plan",
            "READY",
            action_tool="prepare_project_plan_baseline",
            facts={"active_plan": None, "missing_milestones": ["design", "delivery"]},
        ),
    ]

    result = _recommendations(stages, {"prepare_project_plan_baseline"})

    assert result[0]["tool"] == "prepare_project_plan_baseline"
    assert "尚无完整基线计划" in result[0]["reason"]
    assert "计划变更" not in result[0]["reason"]


def test_parallel_contract_gap_is_follow_up_not_a_kickoff_blocker():
    stage = _contract_stage(
        {"sales_contracts": [], "late_expected_contracts": [], "workflow_options": {}},
        set(),
    )
    assert stage["parallel"] is True
    assert stage["blockers"] == []
    assert stage["follow_ups"]


def test_plan_stage_routes_incomplete_effective_plan_to_plan_change():
    row = {
        "analysis": {
            "derived_status": {"has_effective_plan": True, "project_status": "ACTIVE"},
            "active_plan": {"id": "plan-1", "status": "EFFECTIVE"},
            "milestone_coverage": {
                "missing": ["trial"],
                "missing_labels": ["试模/调试"],
            },
        },
        "project_plans": [{"status": "EFFECTIVE"}],
        "workflow_options": [{"id": "plan-change-flow"}],
        "baseline_workflow_options": [],
    }
    stage = _plan_stage(
        row,
        {"prepare_project_plan_change"},
        "COMPLETED",
        "ACTIVE",
    )
    assert stage["state"] == "NEEDS_ATTENTION"
    assert stage["action_tool"] == "prepare_project_plan_change"
    assert stage["facts"]["plan_change_workflow_count"] == 1


def test_plan_stage_blocks_first_baseline_until_erp_mapping_is_confirmed():
    row = {
        "analysis": {
            "derived_status": {"has_effective_plan": False, "project_status": "ACTIVE"},
            "active_plan": None,
            "milestone_coverage": {"missing": ["design"], "missing_labels": []},
        },
        "project_plans": [],
        "workflow_options": [],
        "baseline_workflow_options": [{"id": "baseline-flow"}],
        "erp_execution_progress": {"status": "ERP_PROJECT_MAPPING_REQUIRED"},
    }
    stage = _plan_stage(
        row,
        {"prepare_project_plan_baseline"},
        "COMPLETED",
        "ACTIVE",
    )
    assert stage["state"] == "BLOCKED"
    assert stage["action_tool"] is None
    assert any("ERP 项目与模具尚未完成人工映射" in item for item in stage["blockers"])


def workflow(db, approver, business_type):
    config = {
        "business_type": business_type,
        "nodes": [{
            "key": "review",
            "name": f"{business_type} 审批",
            "mode": "ALL",
            "users": [approver.id],
            "reject_rules": [],
        }],
    }
    row = m.WorkflowDefinition(
        process_key=f"{business_type}_kickoff_test",
        version=1,
        name=f"{business_type} 测试流程",
        status="PUBLISHED",
        config=config,
        bpmn_xml=bpm.compile_bpmn(config),
        package_hash="test",
    )
    db.add(row)
    db.flush()
    return row


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
        execution_mode="INTERNAL" if decision_value != "REJECT" else None,
        effective_date=date.today(),
        evidence="人工确认依据",
        amount=Decimal("100.00"),
        currency="CNY",
    ))
    return subject


def sales_contract(db, project_row, creator, number="SC-KICKOFF"):
    subject = m.BusinessSubject(
        kind="sales_contract",
        number=f"SUBJECT-{number}",
        project_id=project_row.id,
        created_by=creator.id,
        status="EFFECTIVE",
    )
    db.add(subject)
    db.flush()
    db.add(m.ContractDetail(
        subject_id=subject.id,
        customer_id=None,
        supplier_id=None,
        amount=Decimal("100.00"),
        currency="CNY",
        contract_number=number,
        expected_date=date.today(),
    ))
    return subject


def quotation(db, project_row, creator, number="Q-KICKOFF"):
    subject = m.BusinessSubject(
        kind="quotation",
        number="SUBJECT-" + number,
        project_id=project_row.id,
        created_by=creator.id,
        status="EFFECTIVE",
    )
    db.add(subject)
    db.flush()
    db.add(m.QuotationDetail(
        subject_id=subject.id,
        previous_id=None,
        quotation_number=number,
        version=1,
        preliminary_execution_mode="INTERNAL",
        quoted_amount=Decimal("100000.00"),
        currency="CNY",
        promised_delivery_date=date.today() + timedelta(days=90),
        payment_terms="合同生效30%，T0后40%，终验30%",
        cost_amount=Decimal("70000.00"),
        cost_evidence="成本核算表",
        process_analysis="内部设计、加工、装配和试模路线",
        duration_days=75,
        duration_evidence="项目工期评估",
        supplier_quote_amount=None,
        supplier_delivery_date=None,
        supplier_requirements=None,
        supplier_quote_evidence=None,
        customer_company_snapshot="测试客户",
        customer_contact_snapshot="王经理",
        owner_user_id=creator.id,
        source_summary={},
    ))
    return subject


def plan(db, project_row, creator, number="PLAN-KICKOFF"):
    subject = m.BusinessSubject(
        kind="project_plan",
        number=number,
        project_id=project_row.id,
        created_by=creator.id,
        status="EFFECTIVE",
    )
    db.add(subject)
    db.flush()
    db.add(m.PlanDetail(subject_id=subject.id, reason="项目启动基线"))
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
    rows = result["data"][0]["analysis"]["kickoff_lifecycle"]["stages"]
    return {row["key"]: row for row in rows}


def test_kickoff_schema_and_fresh_project_recommend_acceptance_with_parallel_contract():
    engine, Session = factory()
    try:
        with Session.begin() as db:
            admin = user(db, "admin", True)
            project(db, "KICKOFF-FRESH")
            for business_type in ("quotation", "quote_acceptance", "sales_contract", "internal_start", "project_plan"):
                workflow(db, admin, business_type)
        schema = tool_schema("query_project_kickoff_context")["function"]["parameters"]
        assert {"project_id", "identifier"} <= set(schema["properties"])
        with Session() as db:
            admin = db.query(m.User).filter_by(username="admin").one()
            result = execute(db, admin, "query_project_kickoff_context", {"identifier": "KICKOFF-FRESH"})
            lifecycle = result["data"][0]["analysis"]["kickoff_lifecycle"]
            by_key = stages(result)
            assert result["resolution"] == "RESOLVED"
            assert result["scope_boundary"]["complete"] is True
            assert result["scope_boundary"]["scope_key"] == "project_kickoff"
            assert "prepare_quotation_version" in result["scope_boundary"]["write_tools"]
            assert "prepare_internal_start" in result["scope_boundary"]["write_tools"]
            assert "query_quote_evaluation_context" in result["scope_boundary"]["read_tools"]
            assert "query_contract_context" in result["scope_boundary"]["read_tools"]
            assert result["model_context"]["project"]["code"] == "KICKOFF-FRESH"
            assert result["model_context"]["kickoff_lifecycle"]["phase"] == "QUOTATION"
            assert lifecycle["kind"] == "project_kickoff_lifecycle_v2"
            assert lifecycle["phase"] == "QUOTATION"
            assert by_key["quotation"]["state"] == "READY"
            assert by_key["acceptance"]["state"] == "READY"
            assert by_key["contract"]["state"] == "READY"
            assert by_key["contract"]["parallel"] is True
            assert by_key["internal_start"]["state"] == "BLOCKED"
            assert by_key["project_plan"]["state"] == "BLOCKED"
            assert [(item["kind"], item["tool"]) for item in lifecycle["recommended_next_steps"]] == [
                ("PRIMARY", "prepare_quotation_version"),
                ("PARALLEL", "prepare_contract_record"),
            ]
    finally:
        engine.dispose()


def test_kickoff_moves_from_acceptance_to_start_then_plan_then_execution():
    engine, Session = factory()
    try:
        with Session.begin() as db:
            admin = user(db, "admin", True)
            project_row = project(db, "KICKOFF-LIFECYCLE")
            for business_type in ("quotation", "quote_acceptance", "sales_contract", "internal_start", "project_plan"):
                workflow(db, admin, business_type)
            acceptance = decision(db, project_row, admin, "quote_acceptance", "QA-KICKOFF", "ACCEPT")

        with Session() as db:
            admin = db.query(m.User).filter_by(username="admin").one()
            result = execute(db, admin, "query_project_kickoff_context", {"identifier": "KICKOFF-LIFECYCLE"})
            lifecycle = result["data"][0]["analysis"]["kickoff_lifecycle"]
            assert lifecycle["phase"] == "START_PREPARATION"
            assert stages(result)["quotation"]["state"] == "NOT_APPLICABLE"
            assert stages(result)["bid_intake"]["state"] == "NOT_APPLICABLE"
            assert stages(result)["internal_start"]["state"] == "NOT_STARTED"
            assert lifecycle["recommended_next_steps"][0]["tool"] == "prepare_bid_intake_draft"

        with Session.begin() as db:
            admin = db.query(m.User).filter_by(username="admin").one()
            project_row = db.query(m.Project).filter_by(code="KICKOFF-LIFECYCLE").one()
            project_row.status = "ACTIVE"
            acceptance = db.query(m.BusinessSubject).filter_by(number="QA-KICKOFF").one()
            decision(db, project_row, admin, "internal_start", "START-KICKOFF", "START", acceptance.id)

        with Session() as db:
            admin = db.query(m.User).filter_by(username="admin").one()
            result = execute(db, admin, "query_project_kickoff_context", {"identifier": "KICKOFF-LIFECYCLE"})
            lifecycle = result["data"][0]["analysis"]["kickoff_lifecycle"]
            assert lifecycle["phase"] == "PLAN_APPROVAL"
            assert stages(result)["project_plan"]["state"] == "READY"
            assert lifecycle["recommended_next_steps"][0]["tool"] == "prepare_project_plan_baseline"

        with Session.begin() as db:
            admin = db.query(m.User).filter_by(username="admin").one()
            project_row = db.query(m.Project).filter_by(code="KICKOFF-LIFECYCLE").one()
            sales_contract(db, project_row, admin)
            plan(db, project_row, admin)

        with Session() as db:
            admin = db.query(m.User).filter_by(username="admin").one()
            result = execute(db, admin, "query_project_kickoff_context", {"identifier": "KICKOFF-LIFECYCLE"})
            lifecycle = result["data"][0]["analysis"]["kickoff_lifecycle"]
            by_key = stages(result)
            assert lifecycle["phase"] == "EXECUTION"
            assert by_key["acceptance"]["state"] == "COMPLETED"
            assert by_key["contract"]["state"] == "COMPLETED"
            assert by_key["internal_start"]["state"] == "COMPLETED"
            assert by_key["project_plan"]["state"] == "ACTIVE"
            assert by_key["project_plan"]["facts"]["task_count"] == 6
            assert lifecycle["recommended_next_steps"] == []
    finally:
        engine.dispose()


def test_kickoff_context_aliases_preserve_acceptance_for_start_readiness():
    """Coordinator tool names must not hide facts owned by the start reader."""
    engine, Session = factory()
    try:
        with Session.begin() as db:
            admin = user(db, "admin", True)
            project_row = project(db, "KICKOFF-CONTEXT-ALIASES", status="ACTIVE")
            decision(db, project_row, admin, "quote_acceptance", "QA-CONTEXT-ALIASES", "ACCEPT")
            decision(db, project_row, admin, "internal_start", "START-CONTEXT-ALIASES", "START")
        with Session() as db:
            admin = db.query(m.User).filter_by(username="admin").one()
            allowed = {
                "query_project_kickoff_context",
                "query_quote_acceptance_context",
                "query_bid_intake_context",
                "query_contract_context",
                "query_internal_start_readiness",
                "query_project_plan_context",
            }
            result = kickoff_query(
                db, admin,
                ProjectKickoffContextInput(identifier="KICKOFF-CONTEXT-ALIASES"),
                allowed,
            )
            lifecycle = result["data"][0]["analysis"]["kickoff_lifecycle"]
            start = next(row for row in lifecycle["stages"] if row["key"] == "internal_start")
            assert start["state"] == "COMPLETED"
            assert "当前可见范围未见有效承接记录。" not in start["blockers"]
    finally:
        engine.dispose()


def test_kickoff_routes_effective_quotation_to_bid_intake_before_acceptance():
    engine, Session = factory()
    try:
        with Session.begin() as db:
            admin = user(db, "admin", True)
            project_row = project(db, "KICKOFF-BID-INTAKE")
            quotation(db, project_row, admin)
            for business_type in ("quote_acceptance", "sales_contract", "internal_start", "project_plan"):
                workflow(db, admin, business_type)

        with Session() as db:
            admin = db.query(m.User).filter_by(username="admin").one()
            result = execute(db, admin, "query_project_kickoff_context", {"identifier": "KICKOFF-BID-INTAKE"})
            lifecycle = result["data"][0]["analysis"]["kickoff_lifecycle"]
            by_key = stages(result)
            assert lifecycle["phase"] == "BID_INTAKE"
            assert by_key["quotation"]["state"] == "COMPLETED"
            assert by_key["bid_intake"]["state"] == "READY"
            assert lifecycle["recommended_next_steps"][0]["tool"] == "prepare_bid_intake_draft"
            assert lifecycle["recommended_next_steps"][0]["requires_user_confirmation"] is True
    finally:
        engine.dispose()


def test_kickoff_routes_unique_erp_mold_candidate_to_human_handoff_before_start():
    from domain_packs.mold.tools.erp.project.kickoff_lifecycle_tools import _start_stage

    row = {
        "readiness": {
            "has_effective_internal_start": False,
            "can_prepare_start_from_known_facts": True,
            "project_status": "DRAFT",
            "known_blockers": [],
        },
        "latest_internal_start": None,
        "open_start_requests": [],
        "workflow_options": [{"id": "start-flow"}],
        "customer_start_conditions": {"complete": True},
        "erp_mold_handoff": {
            "handoff_state": "ERP_CANDIDATE_REQUIRES_HANDOFF",
            "exact_project_records": [{
                "project_code": "P-001",
                "mold_code": "M-001",
                "source_ref": "scheduling/api/business/molds/:P-001:M-001",
            }],
            "local_internal_mold_numbers": [],
        },
    }

    stage = _start_stage(
        row,
        {
            "query_internal_start_readiness",
            "prepare_internal_start",
            "prepare_project_mold_handoff",
        },
        "COMPLETED",
    )

    assert stage["state"] == "READY"
    assert stage["action_tool"] == "prepare_project_mold_handoff"
    assert stage["facts"]["erp_mold_handoff"]["handoff_state"] == (
        "ERP_CANDIDATE_REQUIRES_HANDOFF"
    )


def test_kickoff_routes_confirmed_erp_handoff_to_formal_start():
    from domain_packs.mold.tools.erp.project.kickoff_lifecycle_tools import _start_stage

    row = {
        "readiness": {
            "has_effective_internal_start": False,
            "can_prepare_start_from_known_facts": True,
            "project_status": "DRAFT",
            "known_blockers": [],
        },
        "latest_internal_start": None,
        "open_start_requests": [],
        "workflow_options": [{"id": "start-flow"}],
        "customer_start_conditions": {"complete": True},
        "erp_mold_handoff": {
            "handoff_state": "LOCAL_ASSOCIATION_PRESENT",
            "project_mapping_state": "CONFIRMED",
            "erp_project_code": "ERP-P-001",
            "exact_project_records": [{
                "project_code": "ERP-P-001",
                "mold_code": "ERP-M-001",
                "source_ref": "scheduling/api/business/molds/:ERP-P-001:ERP-M-001",
            }],
            "records": [],
            "local_internal_mold_numbers": ["ERP-M-001"],
        },
    }

    stage = _start_stage(
        row,
        {"query_internal_start_readiness", "prepare_internal_start"},
        "COMPLETED",
    )

    assert stage["state"] == "READY"
    assert stage["action_tool"] == "prepare_internal_start"
    assert stage["facts"]["erp_mold_handoff"]["project_mapping_state"] == "CONFIRMED"


def test_kickoff_keeps_unassigned_stage_tools_unread_and_does_not_leak_numbers():
    engine, Session = factory()
    try:
        with Session.begin() as db:
            admin = user(db, "admin", True)
            operator = user(db, "operator")
            project_row = project(db, "KICKOFF-LIMITED")
            decision(db, project_row, admin, "quote_acceptance", "SECRET-QA", "ACCEPT")
            sales_contract(db, project_row, admin, "SECRET-CONTRACT")
            grant(db, admin, operator, "project.read", project_row.id)
            capability(db, operator, "query_project_kickoff_context")
            capability(db, operator, "project_kickoff_orchestration", "SKILL")
        with Session() as db:
            operator = db.query(m.User).filter_by(username="operator").one()
            result = execute(db, operator, "query_project_kickoff_context", {"identifier": "KICKOFF-LIMITED"})
            lifecycle = result["data"][0]["analysis"]["kickoff_lifecycle"]
            assert result["scope_boundary"] == {
                "complete": True,
                "scope_key": "project_kickoff",
                "write_tools": [],
            }
            assert result["model_context"]["kickoff_lifecycle"]["phase"] == lifecycle["phase"]
            assert {row["state"] for row in lifecycle["stages"]} == {"UNAVAILABLE"}
            assert lifecycle["access_gaps"] == ["客户报价", "承接确认", "中标接收", "销售合同", "正式开工", "项目计划"]
            assert "SECRET-QA" not in str(result)
            assert "SECRET-CONTRACT" not in str(result)
    finally:
        engine.dispose()


def test_kickoff_reports_multiple_projects_without_merging_lifecycle_facts():
    engine, Session = factory()
    try:
        with Session.begin() as db:
            admin = user(db, "admin", True)
            project(db, "KICKOFF-A")
            project(db, "KICKOFF-B")
        with Session() as db:
            admin = db.query(m.User).filter_by(username="admin").one()
            result = execute(db, admin, "query_project_kickoff_context", {"identifier": "KICKOFF"})
            assert result["resolution"] == "MULTIPLE_CANDIDATES"
            assert {row["code"] for row in result["data"]} == {"KICKOFF-A", "KICKOFF-B"}
            assert all("analysis" not in row for row in result["data"])
    finally:
        engine.dispose()
