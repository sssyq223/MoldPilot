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


def project(db, code, name="客户设变项目", status="ACTIVE"):
    row = m.Project(code=code, name=name, status=status)
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
            reason="unit test",
            granted_by=admin.id,
        )
    )


def capability(db, target, key, kind="TOOL"):
    db.add(m.Capability(user_id=target.id, kind=kind, key=key, enabled=True))


def customer(db):
    row = m.Customer(code="HAIER", name="海尔", rule_key="haier")
    db.add(row)
    db.flush()
    return row


def project_profile_and_mold(db, project, owner):
    cust = customer(db)
    db.add(
        m.ProjectProfile(
            project_id=project.id,
            customer_id=cust.id,
            owner_user_id=owner.id,
            execution_mode="INTERNAL",
            customer_due_date=date.today() + timedelta(days=30),
            settlement_status="OPEN",
        )
    )
    mold = m.Mold(internal_number="MOLD-INT-001", name="冰箱门胆模具", status="ACTIVE")
    db.add(mold)
    db.flush()
    db.add(m.ProjectMold(project_id=project.id, mold_id=mold.id))
    return mold


def acceptance_contract_and_start(db, project, creator):
    today = date.today()
    quote = m.BusinessSubject(kind="quote_acceptance", number="QA-" + project.code, project_id=project.id, created_by=creator.id, status="EFFECTIVE")
    db.add(quote)
    db.flush()
    db.add(
        m.BusinessDecisionDetail(
            subject_id=quote.id,
            source_subject_id=None,
            decision="ACCEPT",
            execution_mode="INTERNAL",
            effective_date=today,
            evidence="客户邮件确认可承接改模",
            amount=Decimal("12000.00"),
            currency="CNY",
        )
    )
    contract = m.BusinessSubject(kind="sales_contract", number="SC-" + project.code, project_id=project.id, created_by=creator.id, status="EFFECTIVE")
    db.add(contract)
    db.flush()
    db.add(
        m.ContractDetail(
            subject_id=contract.id,
            customer_id=None,
            supplier_id=None,
            amount=Decimal("12000.00"),
            currency="CNY",
            contract_number="CHANGE-SC-" + project.code,
            expected_date=today + timedelta(days=20),
            replaces_id=None,
        )
    )
    start = m.BusinessSubject(kind="internal_start", number="START-" + project.code, project_id=project.id, created_by=creator.id, status="EFFECTIVE")
    db.add(start)
    db.flush()
    db.add(
        m.BusinessDecisionDetail(
            subject_id=start.id,
            source_subject_id=quote.id,
            decision="START",
            execution_mode="INTERNAL",
            effective_date=today,
            evidence="小设变正式开工通知",
            amount=None,
            currency=None,
        )
    )
    return quote, contract, start


def plan_and_change(db, project, creator):
    today = date.today()
    plan = m.BusinessSubject(kind="project_plan", number="PLAN-" + project.code, project_id=project.id, created_by=creator.id, status="EFFECTIVE")
    db.add(plan)
    db.flush()
    db.add(m.PlanDetail(subject_id=plan.id, reason="改模影响计划"))
    design = m.PlanTask(
        plan_id=plan.id,
        key="design_revision",
        name="图纸改版确认",
        owner_user_id=creator.id,
        planned_start=today,
        planned_end=today + timedelta(days=2),
        status="RUNNING",
    )
    machining = m.PlanTask(
        plan_id=plan.id,
        key="machining_rework",
        name="加工返工",
        owner_user_id=creator.id,
        planned_start=today + timedelta(days=3),
        planned_end=today + timedelta(days=7),
        status="PLANNED",
    )
    db.add_all([design, machining])
    db.flush()
    change = m.BusinessSubject(
        kind="engineering_change",
        number="EC-" + project.code,
        project_id=project.id,
        category="customer_change",
        created_by=creator.id,
        status="EFFECTIVE",
        remark="客户要求改尺寸，继续复用原内部模具号",
    )
    db.add(change)
    db.flush()
    db.add(
        m.EngineeringChangeDetail(
            subject_id=change.id,
            problem="客户邮件提出尺寸设变，原客户模号版本变化",
            solution="复用内部模具号，设计改版后加工返工，免费小改但仍保留开工依据",
            customer_due_affected=True,
            customer_evidence="客户邮件 EC-001",
        )
    )
    db.add(m.ChangeImpact(change_id=change.id, task_id=design.id, action="KEEP", implemented_by=creator.id, implementation_evidence="设计已出图", rechecked_by=creator.id, recheck_passed=True))
    db.add(m.ChangeImpact(change_id=change.id, task_id=machining.id, action="REWORK", implemented_by=None, implementation_evidence=None, rechecked_by=None, recheck_passed=None))
    return plan, change


def contact_flow(db, project, creator):
    group = m.AssignmentGroup(kind="DEPARTMENT", name="工程部")
    db.add(group)
    db.flush()
    case = m.ContactCase(
        project_id=project.id,
        category="hardware",
        title="客户设变导致加工返工",
        description="客户确认改尺寸，涉及图纸和加工任务",
        mode="ONLINE",
        created_by=creator.id,
        request_key="change-" + project.code,
        request_hash="hash",
        customer_ref="客户邮件 EC-001",
        customer_name="海尔",
        mold_number="MOLD-INT-001 / 客户模号 H-OLD->H-NEW",
        product_ref="HAIER-MAT-001",
        application_date=date.today(),
        problem_source="CUSTOMER_CHANGE",
        current_stage="加工",
        change_type="CHANGE",
        urgency="URGENT",
    )
    db.add(case)
    db.flush()
    task = m.ContactTask(
        case_id=case.id,
        department_id=group.id,
        title="加工返工并复验",
        created_by=creator.id,
        status="RESPONDED",
        affected_type="WIP_TASK",
        affected_ref="machining_rework",
        impact_description="加工任务需返工，交期影响 2 天",
        planned_action="REWORK",
        delivery_impact_days=2,
        estimated_amount=Decimal("0.00"),
        currency="CNY",
        actual_hours=Decimal("3.50"),
        actual_amount=Decimal("0.00"),
        actual_currency="CNY",
        response="已返工，待复验",
        execution_evidence="返工记录",
        execution_source_system="MANUAL",
    )
    db.add(task)
    db.flush()
    resolution = m.BusinessSubject(kind="contact_resolution", number="CR-" + project.code, project_id=project.id, category="hardware", created_by=creator.id, status="EFFECTIVE")
    db.add(resolution)
    db.flush()
    db.add(
        m.ContactResolution(
            subject_id=resolution.id,
            case_id=case.id,
            case_revision=case.revision,
            solution="设计改版，加工返工，品质复验后关闭",
            customer_evidence="客户邮件 EC-001",
            customer_due_affected=True,
            material_snapshot={"tasks": [{"id": task.id}], "attachments": []},
        )
    )
    return case


def test_change_intake_schema_and_context_summary():
    engine, Session = factory()
    try:
        with Session.begin() as db:
            admin = user(db, "admin", True)
            p = project(db, "CHG-M001")
            project_profile_and_mold(db, p, admin)
            acceptance_contract_and_start(db, p, admin)
            plan_and_change(db, p, admin)
            contact_flow(db, p, admin)
        schema = tool_schema("query_change_intake_context")["function"]["parameters"]
        assert {"project_id", "identifier"} <= set(schema["properties"])
        with Session() as db:
            admin = db.query(m.User).filter_by(username="admin").one()
            result = execute(db, admin, "query_change_intake_context", {"identifier": "CHG-M001"})
            assert result["resolution"] == "RESOLVED"
            analysis = result["data"][0]["analysis"]
            status = analysis["derived_status"]
            assert status["has_change_record"] is True
            assert status["has_customer_change_signal"] is True
            assert status["has_customer_written_evidence"] is True
            assert status["has_internal_mold_identity"] is True
            assert status["has_effective_start_notice"] is True
            assert status["has_effective_contract"] is True
            assert status["has_plan_impact_context"] is True
            assert status["has_approved_solution"] is True
            assert status["has_open_execution_or_recheck_items"] is True
            assert status["has_plan_adjustment_candidate"] is True
            assert status["plan_adjustment_candidate_count"] == 1
            assert analysis["known_molds"][0]["internal_number"] == "MOLD-INT-001"
            assert analysis["engineering_changes"][0]["source_classification"] == "CUSTOMER_CHANGE_INTERNAL_OR_UNSPECIFIED"
            candidate = analysis["plan_adjustment_candidates"][0]
            assert candidate["candidate_status"] == "READY_FOR_PLAN_CHANGE_PREPARE"
            assert candidate["affected_ref"] == "machining_rework"
            assert candidate["delivery_impact_days"] == 2
            assert candidate["matched_plan_tasks"][0]["key"] == "machining_rework"
            assert candidate["evidence_gaps"] == []
            assert candidate["recommended_next_tools"] == ["query_project_plan_context", "prepare_project_plan_change"]
            assert "不会自动改计划" in candidate["guardrail"]
            assert "不能把方案审批" in "".join(analysis["warnings"])
            assert "不同事实" in "".join(result["limitations"])
    finally:
        engine.dispose()


def test_change_intake_does_not_leak_contacts_without_contact_permission():
    engine, Session = factory()
    try:
        with Session.begin() as db:
            admin = user(db, "admin", True)
            operator = user(db)
            p = project(db, "CHG-LIMITED")
            project_profile_and_mold(db, p, admin)
            plan_and_change(db, p, admin)
            contact_flow(db, p, admin)
            grant(db, admin, operator, "project.read", project_id=p.id)
            grant(db, admin, operator, "engineering_change.read", project_id=p.id, category="customer_change")
            capability(db, operator, "query_change_intake_context")
        with Session() as db:
            operator = db.query(m.User).filter_by(username="operator").one()
            result = execute(db, operator, "query_change_intake_context", {"identifier": "CHG-LIMITED"})
            analysis = result["data"][0]["analysis"]
            assert analysis["derived_status"]["has_change_record"] is True
            assert analysis["engineering_contact_cases"] == []
            assert analysis["plan_adjustment_candidates"] == []
            assert "客户设变导致加工返工" not in str(result)
            assert "工程联络协作事项" in "".join(result["limitations"])
    finally:
        engine.dispose()


def test_change_intake_marks_plan_adjustment_candidate_context_gaps():
    engine, Session = factory()
    try:
        with Session.begin() as db:
            admin = user(db, "admin", True)
            p = project(db, "CHG-GAP")
            project_profile_and_mold(db, p, admin)
            plan_and_change(db, p, admin)
            case = contact_flow(db, p, admin)
            resolution = db.query(m.BusinessSubject).filter_by(kind="contact_resolution", project_id=p.id).one()
            resolution.status = "DRAFT"
            contact_task = db.query(m.ContactTask).filter_by(case_id=case.id).one()
            contact_task.affected_ref = "unknown-plan-node"
            capability(db, admin, "query_project_plan_context")
            capability(db, admin, "prepare_project_plan_change")
        with Session() as db:
            admin = db.query(m.User).filter_by(username="admin").one()
            result = execute(db, admin, "query_change_intake_context", {"identifier": "CHG-GAP"})
            candidate = result["data"][0]["analysis"]["plan_adjustment_candidates"][0]
            assert candidate["candidate_status"] == "NEEDS_CONTEXT"
            assert candidate["matched_plan_tasks"] == []
            assert "已审批生效的联络单处理方案" in "".join(candidate["evidence_gaps"])
            assert "未精确匹配当前可见计划任务" in "".join(candidate["evidence_gaps"])
            assert candidate["recommended_next_tools"] == ["query_project_plan_context", "prepare_project_plan_change"]
    finally:
        engine.dispose()


def test_change_intake_reports_multiple_candidates_without_deciding():
    engine, Session = factory()
    try:
        with Session.begin() as db:
            admin = user(db, "admin", True)
            project(db, "CHG-A", "共同设变项目A")
            project(db, "CHG-B", "共同设变项目B")
        with Session() as db:
            admin = db.query(m.User).filter_by(username="admin").one()
            result = execute(db, admin, "query_change_intake_context", {"identifier": "共同设变项目"})
            assert result["resolution"] == "MULTIPLE_CANDIDATES"
            assert {row["code"] for row in result["data"]} == {"CHG-A", "CHG-B"}
            assert "请使用项目 ID" in "".join(result["limitations"])
    finally:
        engine.dispose()
