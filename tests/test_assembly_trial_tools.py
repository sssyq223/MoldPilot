from datetime import date, timedelta
from decimal import Decimal

from sqlalchemy import select


from app import models as m
from app.authorization import PERMISSIONS
from pg_db import factory as pg_factory
from app.tool_gateway import execute, tool_schema
from domain_packs.mold.erp.core.domain_commands import execute_command
from domain_packs.mold.erp.core import business
from domain_packs.mold.ports.bpm import content_hash
from domain_packs.mold.authorization import fingerprint


def factory():
    return pg_factory()


def user(db, username="operator", super_admin=False):
    row = m.User(username=username, display_name=username, password_hash="test", super_admin=super_admin)
    db.add(row)
    db.flush()
    return row


def project(db, code, name="装配试模项目", status="ACTIVE"):
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
    db.add(m.PlanDetail(subject_id=subject.id, reason="装配试模计划"))
    assembly = m.PlanTask(
        plan_id=subject.id,
        key="assembly",
        name="装配齐套与装配",
        owner_user_id=creator.id,
        planned_start=today,
        planned_end=today + timedelta(days=1),
        status="DONE",
        actual_start=today,
        actual_end=today + timedelta(days=1),
    )
    trial = m.PlanTask(
        plan_id=subject.id,
        key="trial",
        name="T0试模调试",
        owner_user_id=creator.id,
        planned_start=today + timedelta(days=2),
        planned_end=today + timedelta(days=3),
        status="RUNNING",
        actual_start=today + timedelta(days=2),
    )
    db.add_all([assembly, trial])
    db.flush()
    db.add(m.TaskDependency(task_id=trial.id, prerequisite_id=assembly.id))
    return subject, assembly, trial


def material(db, code="CORE-001", name="型芯"):
    row = m.Material(code=code, name=name, category="raw_material", unit="PCS")
    db.add(row)
    db.flush()
    return row


def design(db, project, creator, mat, task):
    subject = m.BusinessSubject(kind="design_route", number="DESIGN-" + project.code, project_id=project.id, created_by=creator.id, status="EFFECTIVE")
    db.add(subject)
    db.flush()
    db.add(m.DesignDetail(subject_id=subject.id, design_type="NEW_MOLD", drawing_revision="A1", drawing_evidence="正式图纸A1", reviewer_id=creator.id))
    db.add(m.DesignItem(design_id=subject.id, material_id=mat.id, quantity=Decimal("1"), route="INTERNAL", task_id=task.id))
    return subject


def assembly(db, project, creator, design_subject, status="DONE", number=None):
    today = date.today()
    subject = m.BusinessSubject(kind="assembly_issue", number=number or "ASM-" + project.code, project_id=project.id, created_by=creator.id, status="EFFECTIVE")
    db.add(subject)
    db.flush()
    db.add(
        m.AssemblyDetail(
            subject_id=subject.id,
            design_id=design_subject.id,
            supervisor_id=creator.id,
            prerequisites_evidence="ERP齐套检查与关键件确认",
            planned_date=today,
            execution_status=status,
        )
    )
    if status in {"RUNNING", "DONE"}:
        db.add(m.AssemblyExecution(assembly_id=subject.id, action="START", actual_date=today, evidence="装配开工回执", confirmed_by=creator.id))
    if status == "DONE":
        db.add(m.AssemblyExecution(assembly_id=subject.id, action="DONE", actual_date=today + timedelta(days=1), evidence="装配完工回执", confirmed_by=creator.id))
    return subject


def trial(db, project, creator, assembly_subject, passed=False, number=None):
    today = date.today()
    subject = m.BusinessSubject(kind="trial_request", number=number or "TRIAL-" + project.code, project_id=project.id, created_by=creator.id, status="EFFECTIVE")
    db.add(subject)
    db.flush()
    db.add(
        m.TrialDetail(
            subject_id=subject.id,
            assembly_id=assembly_subject.id,
            planned_date=today + timedelta(days=2),
            location="内部试模机台A",
            acceptance_criteria="试模报告与尺寸记录",
            responsible_id=creator.id,
        )
    )
    db.add(
        m.TrialResult(
            trial_id=subject.id,
            passed=passed,
            actual_date=today + timedelta(days=3),
            evidence="SECRET-TRIAL-REPORT",
            findings="拉伤，需要整改" if not passed else "动作与尺寸合格",
            change_id=None,
            confirmed_by=creator.id,
        )
    )
    return subject


def contact_issue(db, project, creator, trial_subject):
    group = m.AssignmentGroup(kind="DEPARTMENT", name="工程部")
    db.add(group)
    db.flush()
    case = m.ContactCase(
        project_id=project.id,
        category="raw_material",
        title="试模拉伤整改",
        description="试模发现产品拉伤",
        mode="ONLINE",
        created_by=creator.id,
        request_key="trial-case-" + project.code,
        request_hash="hash",
        problem_source="TRIAL_ISSUE",
        current_stage="试模阶段",
        change_type="EXCEPTION",
        urgency="URGENT",
    )
    db.add(case)
    db.flush()
    db.add(
        m.ContactTask(
            case_id=case.id,
            department_id=group.id,
            title="整改后重新试模",
            created_by=creator.id,
            status="ASSIGNED",
            affected_type="PLAN_NODE",
            affected_ref=trial_subject.id,
            impact_description="试模未通过，需要整改复验",
            planned_action="REWORK",
        )
    )
    return case


def test_assembly_trial_schema_and_context_summary():
    engine, Session = factory()
    try:
        with Session.begin() as db:
            admin = user(db, "admin", True)
            p = project(db, "ASM-M001")
            _, assembly_task, _ = plan(db, p, admin)
            d = design(db, p, admin, material(db), assembly_task)
            asm = assembly(db, p, admin, d)
            tr = trial(db, p, admin, asm, passed=False)
            contact_issue(db, p, admin, tr)
        schema = tool_schema("query_assembly_trial_context")["function"]["parameters"]
        assert {"project_id", "identifier"} <= set(schema["properties"])
        with Session() as db:
            admin = db.query(m.User).filter_by(username="admin").one()
            result = execute(db, admin, "query_assembly_trial_context", {"identifier": "ASM-M001"})
            assert result["resolution"] == "RESOLVED"
            analysis = result["data"][0]["analysis"]
            assert analysis["derived_status"]["has_assembly_order"] is True
            assert analysis["derived_status"]["has_assembly_done"] is True
            assert analysis["derived_status"]["has_trial_request"] is True
            assert analysis["derived_status"]["has_trial_result"] is True
            assert analysis["derived_status"]["has_trial_failed"] is True
            assert analysis["derived_status"]["has_open_assembly_or_trial_issue"] is True
            assert analysis["erp_manufacturing_execution"]["status"] in {"NOT_CONFIGURED", "LOGIN_REQUIRED"}
            assert analysis["derived_status"]["has_erp_assembly_or_trial_fact"] is False
            assert analysis["design_bom_routes"][0]["material_code"] == "CORE-001"
            assert "重新验证" in "".join(analysis["warnings"])
            handoffs = {row["key"]: row for row in analysis["assembly_trial_handoffs"]}
            assert handoffs["design_to_assembly"]["state"] == "CONNECTED"
            assert handoffs["assembly_to_trial"]["state"] == "NEEDS_ATTENTION"
            assert handoffs["trial_to_delivery_release"]["state"] == "NEEDS_ATTENTION"
            readiness = analysis["assembly_readiness"]
            assert readiness["readiness_status"] in {"UNVERIFIED", "UNAVAILABLE"}
            assert readiness["key_part_policy"]["state"] == "UNCONFIGURED"
            assert readiness["trial_resources"][0]["availability_state"] == "UNVERIFIED"
            assert readiness["trial_report_evidence"][0]["attachment_state"] == "UNAVAILABLE"
    finally:
        engine.dispose()


def test_assembly_trial_does_not_leak_trial_without_trial_permission():
    engine, Session = factory()
    try:
        with Session.begin() as db:
            admin = user(db, "admin", True)
            operator = user(db)
            p = project(db, "ASM-LIMITED")
            _, assembly_task, _ = plan(db, p, admin)
            d = design(db, p, admin, material(db, "SECRET-PART", "秘密零件"), assembly_task)
            asm = assembly(db, p, admin, d)
            trial(db, p, admin, asm, passed=True, number="TRIAL-SECRET")
            grant(db, admin, operator, "project.read", p.id)
            grant(db, admin, operator, "assembly_issue.read", p.id)
            capability(db, operator, "query_assembly_trial_context")
        with Session() as db:
            operator = db.query(m.User).filter_by(username="operator").one()
            result = execute(db, operator, "query_assembly_trial_context", {"identifier": "ASM-LIMITED"})
            analysis = result["data"][0]["analysis"]
            assert analysis["derived_status"]["has_assembly_order"] is True
            assert analysis["derived_status"]["has_trial_request"] is False
            assert "TRIAL-SECRET" not in str(result)
            assert "SECRET-TRIAL-REPORT" not in str(result)
            assert "SECRET-PART" not in str(result)
            assert "试模申请" in "".join(result["limitations"])
            assert analysis["assembly_readiness"]["readiness_status"] == "NO_EFFECTIVE_BOM"
    finally:
        engine.dispose()


def test_assembly_readiness_uses_visible_stock_but_keeps_key_parts_and_resources_unverified():
    engine, Session = factory()
    try:
        with Session.begin() as db:
            admin = user(db, "admin", True)
            p = project(db, "ASM-READINESS")
            _, assembly_task, _ = plan(db, p, admin)
            mat = material(db, "READY-PART", "已到位零件")
            d = design(db, p, admin, mat, assembly_task)
            asm = assembly(db, p, admin, d, status="DONE")
            trial(db, p, admin, asm, passed=True)
            wh = m.Warehouse(
                code="WH-READINESS",
                name="齐套测试仓",
                active=True,
                scope_confirmed=True,
                opening_evidence="齐套测试",
            )
            db.add(wh)
            db.flush()
            db.add(
                m.StockBalance(
                    warehouse_id=wh.id,
                    material_id=mat.id,
                    project_id=p.id,
                    quantity=Decimal("1"),
                )
            )

        with Session() as db:
            admin = db.query(m.User).filter_by(username="admin").one()
            result = execute(
                db, admin, "query_assembly_trial_context", {"identifier": "ASM-READINESS"}
            )
            readiness = result["data"][0]["analysis"]["assembly_readiness"]
            assert readiness["readiness_status"] == "READY_PENDING_KEY_PART_RULE"
            assert readiness["items"][0]["quantity_state"] == "READY"
            assert readiness["items"][0]["stock_quantity"] == "1.000000"
            assert readiness["key_part_policy"]["state"] == "UNCONFIGURED"
            assert readiness["trial_resources"][0]["declaration_state"] == "DECLARED"
            assert readiness["trial_resources"][0]["availability_state"] == "UNVERIFIED"
            assert readiness["trial_report_evidence"][0]["report_state"] == "TEXT_EVIDENCE_ONLY"
            assert "关键件" in "".join(result["data"][0]["analysis"]["gaps"])
    finally:
        engine.dispose()


def test_trial_confirmation_links_report_original_and_context_marks_attachment_verified():
    engine, Session = factory()
    try:
        with Session.begin() as db:
            admin = user(db, "admin", True)
            p = project(db, "ASM-REPORT")
            _, assembly_task, _ = plan(db, p, admin)
            d = design(db, p, admin, material(db, "REPORT-PART"), assembly_task)
            asm = assembly(db, p, admin, d, status="DONE")
            trial_subject = m.BusinessSubject(
                kind="trial_request",
                number="TRIAL-REPORT-ASM-REPORT",
                project_id=p.id,
                created_by=admin.id,
                status="EFFECTIVE",
            )
            db.add(trial_subject)
            db.flush()
            db.add(
                m.TrialDetail(
                    subject_id=trial_subject.id,
                    assembly_id=asm.id,
                    planned_date=date.today(),
                    location="内部试模机台A",
                    acceptance_criteria="报告原件",
                    responsible_id=admin.id,
                )
            )
            conversation = m.Conversation(user_id=admin.id, title="试模报告")
            db.add(conversation)
            db.flush()
            blob = m.FileObject(
                owner_id=admin.id,
                conversation_id=conversation.id,
                request_key="trial-report-file",
                filename="trial-report.pdf",
                media_type="application/pdf",
                size=12,
                sha256="a" * 64,
                backend="local",
                storage_namespace="test",
                object_key="trial-report-object",
            )
            db.add(blob)
            db.flush()
            receipt = execute_command(
                db,
                admin,
                "trial.confirm",
                trial_subject.id,
                {
                    "passed": True,
                    "actual_date": date.today().isoformat(),
                    "findings": "试模通过，报告已归档",
                    "change_id": None,
                    "file_ids": [blob.id],
                    "evidence": "报告原件已上传",
                },
            )
            assert receipt["trial_report_attachment_count"] == 1
            assert db.scalar(
                select(m.TrialResultAttachment.id).where(
                    m.TrialResultAttachment.trial_result_id == receipt["trial_result_id"]
                )
            )

        with Session() as db:
            admin = db.query(m.User).filter_by(username="admin").one()
            result = execute(
                db,
                admin,
                "query_assembly_trial_context",
                {"identifier": "ASM-REPORT"},
            )
            evidence = result["data"][0]["analysis"]["assembly_readiness"][
                "trial_report_evidence"
            ][0]
            assert evidence["attachment_state"] == "VERIFIED"
            assert evidence["attachment_count"] == 1
            assert evidence["report_state"] == "ATTACHED"
            assert evidence["attachments"][0]["filename"] == "trial-report.pdf"
    finally:
        engine.dispose()


def test_prepare_assembly_execution_requires_confirmation_then_records_agent_receipt():
    engine, Session = factory()
    try:
        with Session.begin() as db:
            admin = user(db, "admin", True)
            p = project(db, "ASM-PREPARE")
            _, assembly_task, _ = plan(db, p, admin)
            d = design(db, p, admin, material(db, "PREPARE-PART"), assembly_task)
            asm = assembly(db, p, admin, d, status="RUNNING")
            conversation = m.Conversation(user_id=admin.id, title="装配执行")
            db.add(conversation)
            db.flush()
            run = m.Run(
                conversation_id=conversation.id,
                user_id=admin.id,
                security_version=admin.security_version,
                prompt="登记装配完工",
                status="RUNNING",
                checkpoint={"authorization_hash": fingerprint(db, admin)},
            )
            db.add(run)
            db.flush()
            args = {
                "project_id": p.id,
                "project_version": p.row_version,
                "assembly_id": asm.id,
                "action": "DONE",
                "actual_date": date.today().isoformat(),
                "evidence": "钳工主管现场确认装配完工",
            }
            prepared = execute(db, admin, "prepare_assembly_execution", args, run=run)
            assert prepared["proposal"]["kind"] == "assembly_execution"
            assert db.scalar(select(m.AssemblyExecution.id).where(
                m.AssemblyExecution.assembly_id == asm.id,
                m.AssemblyExecution.action == "DONE",
            )) is None
            step = m.Step(
                run_id=run.id,
                sequence=0,
                tool="prepare_assembly_execution",
                request_hash="prepare-assembly",
                result=prepared,
            )
            db.add(step)
            db.flush()
            intent = business.create_intent(
                db,
                admin,
                "assembly_trial.execute",
                step.id,
                {"step_id": step.id, "proposal_hash": content_hash(prepared["proposal"])},
            )
            receipt = business.confirm_intent(db, admin, intent["id"], intent["challenge"])
            assert receipt["status"] == "CONFIRMED"
            assert receipt["action"] == "assembly_execution"
            assert db.scalar(select(m.AssemblyExecution.id).where(
                m.AssemblyExecution.assembly_id == asm.id,
                m.AssemblyExecution.action == "DONE",
            )) is not None
            assert db.get(m.AssemblyDetail, asm.id).execution_status == "DONE"
    finally:
        engine.dispose()


def test_prepare_trial_result_requires_confirmation_and_preserves_failed_follow_up_boundary():
    engine, Session = factory()
    try:
        with Session.begin() as db:
            admin = user(db, "admin", True)
            p = project(db, "TRIAL-PREPARE")
            _, assembly_task, _ = plan(db, p, admin)
            d = design(db, p, admin, material(db, "TRIAL-PART"), assembly_task)
            asm = assembly(db, p, admin, d, status="DONE")
            trial_subject = m.BusinessSubject(
                kind="trial_request",
                number="TRIAL-PREPARE-001",
                project_id=p.id,
                created_by=admin.id,
                status="EFFECTIVE",
            )
            db.add(trial_subject)
            db.flush()
            db.add(
                m.TrialDetail(
                    subject_id=trial_subject.id,
                    assembly_id=asm.id,
                    planned_date=date.today(),
                    location="内部试模机台B",
                    acceptance_criteria="尺寸及动作验证",
                    responsible_id=admin.id,
                )
            )
            db.flush()
            conversation = m.Conversation(user_id=admin.id, title="试模结论")
            db.add(conversation)
            db.flush()
            run = m.Run(
                conversation_id=conversation.id,
                user_id=admin.id,
                security_version=admin.security_version,
                prompt="登记试模通过",
                status="RUNNING",
                checkpoint={"authorization_hash": fingerprint(db, admin)},
            )
            db.add(run)
            db.flush()
            args = {
                "project_id": p.id,
                "project_version": p.row_version,
                "trial_id": trial_subject.id,
                "passed": True,
                "actual_date": date.today().isoformat(),
                "findings": "尺寸、动作及外观检查通过",
                "file_ids": [],
            }
            prepared = execute(db, admin, "prepare_trial_result", args, run=run)
            assert prepared["proposal"]["kind"] == "trial_result"
            assert db.scalar(select(m.TrialResult.id).where(
                m.TrialResult.trial_id == trial_subject.id
            )) is None
            step = m.Step(
                run_id=run.id,
                sequence=0,
                tool="prepare_trial_result",
                request_hash="prepare-trial",
                result=prepared,
            )
            db.add(step)
            db.flush()
            intent = business.create_intent(
                db,
                admin,
                "assembly_trial.execute",
                step.id,
                {"step_id": step.id, "proposal_hash": content_hash(prepared["proposal"])},
            )
            receipt = business.confirm_intent(db, admin, intent["id"], intent["challenge"])
            assert receipt["status"] == "CONFIRMED"
            assert receipt["action"] == "trial_result"
            result = db.scalar(select(m.TrialResult).where(m.TrialResult.trial_id == trial_subject.id))
            assert result is not None
            assert result.passed is True
            assert db.get(m.TrialDetail, trial_subject.id).responsible_id == admin.id
    finally:
        engine.dispose()


def test_assembly_trial_reports_multiple_candidates_without_deciding():
    engine, Session = factory()
    try:
        with Session.begin() as db:
            admin = user(db, "admin", True)
            p1 = project(db, "ASM-A", "共同装配项目A")
            p2 = project(db, "ASM-B", "共同装配项目B")
            for p in (p1, p2):
                _, assembly_task, _ = plan(db, p, admin)
                d = design(db, p, admin, material(db, "MAT-" + p.code, "测试零件"), assembly_task)
                assembly(db, p, admin, d, number="ASM-" + p.code)
        with Session() as db:
            admin = db.query(m.User).filter_by(username="admin").one()
            result = execute(db, admin, "query_assembly_trial_context", {"identifier": "共同装配项目"})
            assert result["resolution"] == "MULTIPLE_CANDIDATES"
            assert {row["code"] for row in result["data"]} == {"ASM-A", "ASM-B"}
            assert "请使用项目 ID" in "".join(result["limitations"])
    finally:
        engine.dispose()


def test_assembly_trial_exposes_unfinished_plan_prerequisite():
    engine, Session = factory()
    try:
        with Session.begin() as db:
            admin = user(db, "admin", True)
            p = project(db, "ASM-DEPENDENCY")
            _, assembly_task, _ = plan(db, p, admin)
            assembly_task.status = "RUNNING"
            d = design(db, p, admin, material(db, "DEPENDENCY-PART"), assembly_task)
            asm = assembly(db, p, admin, d)
            trial(db, p, admin, asm, passed=True)
        with Session() as db:
            admin = db.query(m.User).filter_by(username="admin").one()
            result = execute(db, admin, "query_assembly_trial_context", {"identifier": "ASM-DEPENDENCY"})
            analysis = result["data"][0]["analysis"]
            assert analysis["derived_status"]["has_blocked_plan_dependency"] is True
            assert analysis["dependency_blocked_tasks"][0]["waiting_for"] == ["assembly"]
            assert "未完成前置依赖" in "".join(analysis["warnings"])
            assert analysis["derived_status"]["assembly_trial_handoff_states"]["assembly_to_trial"] == "BLOCKED"
            assert analysis["derived_status"]["assembly_trial_handoff_states"]["trial_to_delivery_release"] == "READY"
    finally:
        engine.dispose()

