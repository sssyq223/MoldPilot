from sqlalchemy import select

from app import bpm, business, models as m
from app.authorization import fingerprint
from app.tool_gateway import execute, tool_schema
from domain_packs.mold.erp.project import mold_handoff
from domain_packs.mold.ports.errors import DomainError
from domain_packs.mold.tools.erp.project import mold_handoff_tools
from pg_db import factory


def _fake_evidence(*_args, **_kwargs):
    return {
        "status": "RESOLVED",
        "handoff_state": "ERP_CANDIDATE_REQUIRES_HANDOFF",
        "as_of": "2026-09-26T01:02:03+08:00",
        "exact_project_records": [
            {
                "project_code": "ERP-P-001",
                "mold_code": "ERP-M-001",
                "customer": "测试客户",
                "overall_progress": 0,
                "source_ref": "scheduling/api/business/molds/:ERP-P-001:ERP-M-001",
            }
        ],
        "records": [],
        "limitations": ["只读"],
    }


def test_project_mold_handoff_requires_confirmation_and_preserves_erp_provenance(
    monkeypatch,
):
    monkeypatch.setattr(mold_handoff, "query", _fake_evidence)
    engine, Session = factory()
    try:
        with Session.begin() as db:
            admin = m.User(
                username="handoff_admin",
                display_name="admin",
                password_hash="test",
                super_admin=True,
            )
            db.add(admin)
            db.flush()
            project = m.Project(code="ERP-P-001", name="ERP 交接项目", status="DRAFT")
            db.add(project)
            db.flush()
            conversation = m.Conversation(user_id=admin.id, title="模具交接")
            db.add(conversation)
            db.flush()
            run = m.Run(
                conversation_id=conversation.id,
                user_id=admin.id,
                security_version=admin.security_version,
                prompt="准备 ERP 模具交接",
                status="SUCCEEDED",
                checkpoint={
                    "authorization_hash": fingerprint(db, admin),
                    "agent_permission_mode": "ask",
                },
            )
            db.add(run)
            db.flush()
            args = {
                "project_id": project.id,
                "project_version": project.row_version,
                "erp_project_code": "ERP-P-001",
                "erp_mold_code": "ERP-M-001",
                "erp_source_ref": "scheduling/api/business/molds/:ERP-P-001:ERP-M-001",
                "internal_number": "ERP-M-001",
                "mold_name": "ERP-M-001 主模",
                "evidence": "ERP 项目与模具号已由项目负责人逐项核对",
            }

        schema = tool_schema("prepare_project_mold_handoff")["function"]["parameters"]
        assert {
            "project_id",
            "project_version",
            "erp_project_code",
            "erp_mold_code",
            "erp_source_ref",
            "internal_number",
        } <= set(schema["properties"])

        with Session.begin() as db:
            admin = db.query(m.User).filter_by(username="handoff_admin").one()
            run = db.scalar(select(m.Run).where(m.Run.user_id == admin.id))
            evidence = execute(
                db,
                admin,
                "prepare_project_mold_handoff",
                args,
                run=run,
            )
            assert evidence["proposal"]["kind"] == "project_mold_handoff"
            assert evidence["proposal"]["requires_approval"] is False
            contract = evidence["proposal"]["execution_contract"]
            assert contract["submits_agent_bpm"] is False
            assert contract["writes_erp"] is False
            assert "确认后效果" in evidence["proposal"]["display"]
            assert db.scalar(select(m.Mold.id)) is None
            step = m.Step(
                run_id=run.id,
                sequence=0,
                tool="prepare_project_mold_handoff",
                request_hash="hash",
                result=evidence,
            )
            db.add(step)
            db.flush()
            payload = {
                "step_id": step.id,
                "proposal_hash": bpm.content_hash(evidence["proposal"]),
            }
            intent = business.create_intent(
                db, admin, "mold_handoff.execute", step.id, payload
            )
            receipt = business.confirm_intent(db, admin, intent["id"], intent["challenge"])

            assert receipt["status"] == "CONFIRMED"
            assert receipt["erp_write"] is False
            mold = db.scalar(select(m.Mold).where(m.Mold.internal_number == "ERP-M-001"))
            assert mold is not None
            assert mold.source_system == "ERP"
            assert mold.source_ref.endswith("ERP-P-001:ERP-M-001")
            assert mold.source_as_of is not None
            link = db.scalar(select(m.ProjectMold).where(m.ProjectMold.mold_id == mold.id))
            assert link is not None
            assert link.project_id == args["project_id"]
            # A confirmed proposal may move the host Run into a waiting
            # resume state. Read-only receipt access remains available, while
            # a new confirmation attempt still fails closed.
            run.status = "WAITING_CONFIGURATION"
            assert mold_handoff_tools.source(db, admin, step.id, for_read=True)
            try:
                mold_handoff_tools.source(db, admin, step.id)
            except DomainError as error:
                assert error.code == "PROPOSAL_STOPPED"
            else:
                raise AssertionError("stopped proposal must reject a new confirmation")
    finally:
        engine.dispose()


def test_project_mold_handoff_rejects_a_different_internal_number(monkeypatch):
    monkeypatch.setattr(mold_handoff, "query", _fake_evidence)
    engine, Session = factory()
    try:
        with Session.begin() as db:
            admin = m.User(
                username="handoff_admin_mismatch",
                display_name="admin",
                password_hash="test",
                super_admin=True,
            )
            db.add(admin)
            db.flush()
            project = m.Project(code="ERP-P-001", name="ERP 交接项目", status="DRAFT")
            db.add(project)
            db.flush()
            args = {
                "project_id": project.id,
                "project_version": project.row_version,
                "erp_project_code": "ERP-P-001",
                "erp_mold_code": "ERP-M-001",
                "erp_source_ref": "scheduling/api/business/molds/:ERP-P-001:ERP-M-001",
                "internal_number": "WRONG-MOLD",
                "mold_name": "错误模具",
                "evidence": "不应通过",
            }
            from domain_packs.mold.tools.erp.project import mold_handoff_tools

            try:
                mold_handoff_tools.preview(db, admin, mold_handoff_tools.parse(args))
            except Exception as error:
                assert getattr(error, "code", None) == "MOLD_HANDOFF_NUMBER_MISMATCH"
            else:
                raise AssertionError("expected a strict ERP mold-number mismatch")
    finally:
        engine.dispose()


def test_project_mold_handoff_rejects_forged_project_source_reference(monkeypatch):
    """The proposal cannot replace the ERP project's source provenance."""
    monkeypatch.setattr(mold_handoff, "query", _fake_evidence)
    engine, Session = factory()
    try:
        with Session.begin() as db:
            admin = m.User(
                username="handoff_project_source_admin",
                display_name="admin",
                password_hash="test",
                super_admin=True,
            )
            db.add(admin)
            db.flush()
            project = m.Project(code="AGENT-P-001", name="Agent 项目", status="DRAFT")
            db.add(project)
            db.flush()
            args = {
                "project_id": project.id,
                "project_version": project.row_version,
                "erp_project_code": "ERP-P-001",
                "erp_project_source_ref": "untrusted://different-project",
                "erp_mold_code": "ERP-M-001",
                "erp_source_ref": "scheduling/api/business/molds/:ERP-P-001:ERP-M-001",
                "internal_number": "ERP-M-001",
                "mold_name": "ERP 模具",
                "evidence": "不应通过",
            }
            try:
                mold_handoff_tools.preview(db, admin, mold_handoff_tools.parse(args))
            except DomainError as error:
                assert error.code == "MOLD_HANDOFF_EVIDENCE_CHANGED"
            else:
                raise AssertionError("forged ERP project source reference must be rejected")
    finally:
        engine.dispose()


def test_project_mold_handoff_can_confirm_explicit_agent_to_erp_project_mapping(monkeypatch):
    """A different ERP project code is allowed only inside the human handoff."""
    monkeypatch.setattr(mold_handoff, "query", _fake_evidence)
    engine, Session = factory()
    try:
        with Session.begin() as db:
            admin = m.User(
                username="handoff_mapping_admin",
                display_name="admin",
                password_hash="test",
                super_admin=True,
            )
            db.add(admin)
            db.flush()
            project = m.Project(code="AGENT-P-001", name="Agent 项目", status="DRAFT")
            db.add(project)
            db.flush()
            conversation = m.Conversation(user_id=admin.id, title="项目映射")
            db.add(conversation)
            db.flush()
            run = m.Run(
                conversation_id=conversation.id,
                user_id=admin.id,
                security_version=admin.security_version,
                prompt="确认 ERP 项目映射",
                status="SUCCEEDED",
                checkpoint={
                    "authorization_hash": fingerprint(db, admin),
                    "agent_permission_mode": "ask",
                },
            )
            db.add(run)
            db.flush()
            args = {
                "project_id": project.id,
                "project_version": project.row_version,
                "erp_project_code": "ERP-P-001",
                "erp_project_source_ref": "scheduling/api/business/molds/:ERP-P-001",
                "erp_mold_code": "ERP-M-001",
                "erp_source_ref": "scheduling/api/business/molds/:ERP-P-001:ERP-M-001",
                "internal_number": "ERP-M-001",
                "mold_name": "ERP 模具",
                "evidence": "项目负责人已逐项核对 Agent 项目与 ERP 项目号",
            }

        with Session.begin() as db:
            admin = db.query(m.User).filter_by(username="handoff_mapping_admin").one()
            run = db.scalar(select(m.Run).where(m.Run.user_id == admin.id))
            evidence = execute(db, admin, "prepare_project_mold_handoff", args, run=run)
            assert evidence["proposal"]["display"]["项目映射"] == "本次确认后建立"
            step = m.Step(
                run_id=run.id,
                sequence=0,
                tool="prepare_project_mold_handoff",
                request_hash="mapping-hash",
                result=evidence,
            )
            db.add(step)
            db.flush()
            payload = {
                "step_id": step.id,
                "proposal_hash": bpm.content_hash(evidence["proposal"]),
            }
            intent = business.create_intent(db, admin, "mold_handoff.execute", step.id, payload)
            receipt = business.confirm_intent(db, admin, intent["id"], intent["challenge"])
            assert receipt["status"] == "CONFIRMED"
            mapping = db.scalar(select(m.ProjectERPMapping).where(
                m.ProjectERPMapping.project_id == args["project_id"]
            ))
            assert mapping is not None
            assert mapping.erp_project_code == "ERP-P-001"
            assert db.scalar(select(m.ProjectMold).where(
                m.ProjectMold.project_id == args["project_id"]
            )) is not None
            # The confirmed local association must be the handoff that
            # unlocks the formal-start readiness gate.  Contract/plan facts
            # remain separate inputs; the mapping blocker must not survive
            # merely because the project is still DRAFT.
            from domain_packs.mold.tools.erp.project import start_tools
            readiness = start_tools._readiness(
                project,
                {
                    "quote_acceptance": [{
                        "status": "EFFECTIVE",
                        "detail": {"decision": "ACCEPT"},
                    }],
                    "internal_start": [],
                    "sales_contract": [],
                    "project_plan": [],
                    "plan_change": [],
                },
                {
                    "query_quote_acceptance",
                    "query_sales_contract",
                    "query_project_plan",
                },
                {"complete": True, "blockers": []},
                {
                    "handoff_state": "LOCAL_ASSOCIATION_PRESENT",
                    "local_internal_mold_numbers": ["ERP-M-001"],
                },
            )
            assert not any("ERP" in item and "映射" in item
                           for item in readiness["known_blockers"])
            assert readiness["can_prepare_start_from_known_facts"] is True
    finally:
        engine.dispose()
