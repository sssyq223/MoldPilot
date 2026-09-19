"""Seed PostgreSQL data for built-in-browser smoke tests.

This script intentionally refuses SQLite. Browser validation must exercise the
same PostgreSQL/Navicat baseline used by local delivery, not a disposable file
database.
"""
from __future__ import annotations

import argparse
import os
import sys
from datetime import date, timedelta
from hashlib import sha256
from pathlib import Path
from urllib.parse import urlsplit
from uuid import uuid4

from dotenv import dotenv_values
from sqlalchemy import select
from sqlalchemy.orm import sessionmaker

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))

from app import bpm, business, models as m, object_storage
from app.authorization import fingerprint
from app.bpm import content_hash
from app.db import make_engine, now
from domain_packs.mold.tools.erp.change import contact_tools
from domain_packs.mold.tools.erp.commercial import contract_tools
from domain_packs.mold.tools.erp.project import project_closure_tools as closure_tools
from domain_packs.mold.tools.erp.project import project_control_tools as pause_tools


def _database_url(env_file: str, url_key: str, explicit_url: str) -> str:
    if explicit_url:
        return explicit_url
    if os.environ.get(url_key):
        return os.environ[url_key]
    if url_key == "AGENT_DATABASE_URL" and os.environ.get("MOLD_DATABASE_URL"):
        return os.environ["MOLD_DATABASE_URL"]
    values = dotenv_values(env_file)
    return values.get(url_key) or (values.get("MOLD_DATABASE_URL") if url_key == "AGENT_DATABASE_URL" else "") or ""


def _require_postgresql(url: str) -> None:
    parsed = urlsplit(url)
    if not url:
        raise SystemExit("PostgreSQL DSN is required. Set AGENT_DATABASE_URL or pass --database-url.")
    if parsed.scheme.startswith("sqlite"):
        raise SystemExit("Refusing SQLite: browser smoke tests must use PostgreSQL.")
    if not parsed.scheme.startswith("postgresql"):
        raise SystemExit(f"Unsupported database scheme for browser smoke tests: {parsed.scheme}")


def _upsert_one(db, model, criteria, values):
    row = db.scalar(select(model).where(*criteria).limit(1))
    if row is None:
        row = model(**values)
        db.add(row)
        db.flush()
    else:
        for key, value in values.items():
            setattr(row, key, value)
    return row


def _build_workflow(db, user_id: str, business_type: str, scenario: str):
    config = {
        "business_type": business_type,
        "nodes": [
            {
                "key": "owner_review",
                "name": "项目负责人核对",
                "mode": "ALL",
                "users": [user_id],
                "reject_rules": [],
            }
        ],
    }
    xml = bpm.compile_bpmn(config)
    names = {
        "contact": "工程联络处理方案审批（浏览器验收）",
        "contract": "销售合同审批（浏览器验收）",
        "closure": "项目终止与关闭审批（浏览器验收）",
        "pause": "项目暂停恢复审批（浏览器验收）",
    }
    return _upsert_one(
        db,
        m.WorkflowDefinition,
        [m.WorkflowDefinition.process_key == f"browser_smoke_{business_type}", m.WorkflowDefinition.version == 1],
        {
            "process_key": f"browser_smoke_{business_type}",
            "version": 1,
            "name": names[scenario],
            "config": config,
            "bpmn_xml": xml,
            "status": "PUBLISHED",
            "package_hash": bpm.content_hash({"config": config, "xml": xml}),
        },
    )


def build(
    database_url: str,
    password: str,
    scenario: str = "pause",
    project_code: str | None = None,
    username: str = "admin",
    pdf_file: str | None = None,
):
    _require_postgresql(database_url)
    engine = make_engine(database_url)
    parsed = urlsplit(database_url)
    run_key = now().strftime("%m%d%H%M%S")
    project_code = project_code or f"SMOKE-{scenario.upper()}-{run_key}"
    factory = sessionmaker(engine, expire_on_commit=False)

    with factory.begin() as db:
        user = db.scalar(select(m.User).where(m.User.username == username).limit(1))
        if user is None or not user.active:
            raise SystemExit(f"Smoke user {username!r} is missing or inactive. Run database/init_moldpilot_admin.sql first.")
        project = _upsert_one(
            db,
            m.Project,
            [m.Project.code == project_code],
            {"code": project_code, "name": "浏览器验收模具项目", "status": "ACTIVE"},
        )
        profile = db.get(m.ProjectProfile, project.id)
        if profile is None:
            db.add(
                m.ProjectProfile(
                    project_id=project.id,
                    owner_user_id=user.id,
                    execution_mode="INTERNAL",
                    customer_due_date=now().date() + timedelta(days=45),
                )
            )
        else:
            profile.owner_user_id = user.id
            profile.execution_mode = "INTERNAL"
            profile.customer_due_date = now().date() + timedelta(days=45)

        plan = _upsert_one(
            db,
            m.BusinessSubject,
            [m.BusinessSubject.number == f"{project_code}-PLAN"],
            {
                "kind": "project_plan",
                "number": f"{project_code}-PLAN",
                "project_id": project.id,
                "created_by": user.id,
                "status": "EFFECTIVE",
            },
        )
        detail = db.get(m.PlanDetail, plan.id)
        if detail is None:
            db.add(m.PlanDetail(subject_id=plan.id, reason="浏览器验收基线"))
        else:
            detail.reason = "浏览器验收基线"
        for task in [
            {
                "key": "design",
                "name": "结构设计",
                "planned_start": now().date(),
                "planned_end": now().date() + timedelta(days=7),
                "status": "RUNNING",
                "actual_start": now().date(),
            },
            {
                "key": "manufacture",
                "name": "模具加工",
                "planned_start": now().date() + timedelta(days=8),
                "planned_end": now().date() + timedelta(days=25),
                "status": "PLANNED",
                "actual_start": None,
            },
        ]:
            _upsert_one(
                db,
                m.PlanTask,
                [m.PlanTask.plan_id == plan.id, m.PlanTask.key == task["key"]],
                {"plan_id": plan.id, "owner_user_id": user.id, **task},
            )

        business_type = (
            "contact_resolution" if scenario == "contact"
            else "sales_contract" if scenario == "contract"
            else "project_close" if scenario == "closure"
            else "pause_resume"
        )
        workflow = _build_workflow(db, user.id, business_type, scenario)
        conversation = m.Conversation(
            user_id=user.id,
            title=(
                "工程联络影响项验收" if scenario == "contact"
                else "销售合同附件审批验收" if scenario == "contract"
                else "项目终止业务流验收" if scenario == "closure"
                else "项目暂停业务流验收"
            ),
        )
        db.add(conversation)
        db.flush()
        run = m.Run(
            conversation_id=conversation.id,
            user_id=user.id,
            security_version=user.security_version,
            prompt=(
                "请将受影响图纸纳入工程联络单，明确返工、交期与费用影响。"
                if scenario == "contact"
                else f"请把本轮上传的销售合同原件绑定到 {project_code} 并提交审批。"
                if scenario == "contract"
                else f"请根据客户终止通知终止 {project_code} 项目，并转入处置和终止结算。"
                if scenario == "closure"
                else f"请根据客户通知暂停 {project_code} 项目，并保留客户承诺交期。"
            ),
            status="SUCCEEDED",
        )
        db.add(run)
        db.flush()
        run.checkpoint = {"authorization_hash": fingerprint(db, user)}

        confirm_contract = False
        if scenario == "contact":
            group = _upsert_one(
                db,
                m.AssignmentGroup,
                [m.AssignmentGroup.kind == "DEPARTMENT", m.AssignmentGroup.name == "设计部"],
                {"kind": "DEPARTMENT", "name": "设计部", "active": True, "version": 1},
            )
            member = db.scalar(select(m.AssignmentMember).where(m.AssignmentMember.group_id == group.id, m.AssignmentMember.user_id == user.id))
            if member is None:
                db.add(m.AssignmentMember(group_id=group.id, user_id=user.id, is_head=True))
            else:
                member.is_head = True
            case = _upsert_one(
                db,
                m.ContactCase,
                [m.ContactCase.created_by == user.id, m.ContactCase.request_key == f"contact-{run_key}"],
                {
                    "project_id": project.id,
                    "category": "hardware",
                    "title": "装配尺寸与图纸不一致",
                    "description": "试装时发现型腔关键尺寸与客户确认图纸不一致，需要评估返工。",
                    "mode": "ONLINE",
                    "created_by": user.id,
                    "request_key": f"contact-{run_key}",
                    "request_hash": "b" * 64,
                    "revision": 1,
                    "customer_ref": "ERP-CUSTOMER-008",
                    "customer_name": "浏览器验收客户",
                    "mold_number": project_code,
                    "product_ref": "PART-A100",
                    "application_date": date.today(),
                    "problem_source": "ASSEMBLY_ISSUE",
                    "current_stage": "装配阶段",
                    "change_type": "EXCEPTION",
                    "urgency": "URGENT",
                },
            )
            tool = "prepare_contact_task"
            arguments = {
                "case_id": case.id,
                "revision": case.revision,
                "department_id": group.id,
                "title": "按第二版图纸返工并复测",
                "affected_type": "DRAWING",
                "affected_ref": "DRAWING-A100-R2",
                "impact_description": "型腔尺寸须按客户确认第二版图纸返工并重新检测",
                "planned_action": "REWORK",
                "delivery_impact_days": 2,
                "estimated_amount": "3500.00",
                "currency": "CNY",
                "source_system": "ERP",
                "source_ref": "erp:drawing:DRAWING-A100-R2",
                "source_as_of": now().isoformat(),
            }
            result = contact_tools.execute_tool(db, user, tool, arguments)
            summary = "已按当前联络单资料准备结构化影响与责任事项，请核对对象、返工动作、交期、金额和来源后确认。"
            suggestions = ["确认后只新增联络协作事项；方案审批、实际执行和独立复验仍分别办理。"]
        elif scenario == "contract":
            customer = _upsert_one(
                db,
                m.Customer,
                [m.Customer.code == "BROWSER-CONTRACT-CUSTOMER"],
                {
                    "code": "BROWSER-CONTRACT-CUSTOMER",
                    "name": "浏览器合同验收客户",
                    "rule_key": "standard",
                    "active": True,
                },
            )
            pdf_path = Path(pdf_file).resolve() if pdf_file else None
            if pdf_path:
                if not pdf_path.is_file():
                    raise SystemExit(f"PDF file does not exist: {pdf_path}")
                pdf = pdf_path.read_bytes()
                if not pdf.startswith(b"%PDF-"):
                    raise SystemExit(f"File is not a PDF: {pdf_path}")
                pdf_filename = pdf_path.name
            else:
                pdf = (
                    b"%PDF-1.4\n1 0 obj<</Type/Catalog/Pages 2 0 R>>endobj\n"
                    b"2 0 obj<</Type/Pages/Count 0>>endobj\ntrailer<</Root 1 0 R>>\n%%EOF\n"
                ) + f"% browser-smoke {run_key}\n".encode()
                pdf_filename = f"{project_code}-销售合同原件.pdf"
            digest = sha256(pdf).hexdigest()
            key = uuid4().hex + "/" + digest
            storage = object_storage.put(key, pdf, "application/pdf")
            blob = m.FileObject(
                owner_id=user.id,
                conversation_id=conversation.id,
                request_key=str(uuid4()),
                filename=pdf_filename,
                media_type="application/pdf",
                size=len(pdf),
                sha256=digest,
                object_key=key,
                **storage,
            )
            db.add(blob)
            db.flush()
            db.add(m.RunFile(run_id=run.id, file_id=blob.id))
            db.flush()
            tool = "prepare_contract_record"
            arguments = {
                "project_id": project.id,
                "project_version": project.row_version,
                "contract_kind": "sales_contract",
                "customer_id": customer.id,
                "supplier_id": None,
                "amount": "128000.00",
                "currency": "CNY",
                "contract_number": f"{project_code}-SC-{run_key}",
                "expected_date": date.today().isoformat(),
                "stages": [
                    {"name": "预付款", "amount": "38400.00", "condition": "合同审批生效"},
                    {"name": "验收款", "amount": "89600.00", "condition": "客户验收完成"},
                ],
                "remark": "浏览器验收合成合同；仅用于验证附件随审批快照展示。",
                "workflow_definition_id": workflow.id,
                "file_ids": [blob.id],
                "document_source": "ELECTRONIC",
            }
            result = contract_tools.execute_contract_tool(db, user, tool, arguments, run=run)
            summary = "已按当前对话上传的合同原件准备销售合同登记，请核对合同字段和附件后确认提交审批。"
            suggestions = ["确认后合同原件会冻结进审批快照；审批生效前不视为正式合同。"]
            confirm_contract = True
        elif scenario == "closure":
            tool = "prepare_project_termination"
            arguments = {
                "project_id": project.id,
                "project_version": project.row_version,
                "effective_date": date.today().isoformat(),
                "current_stage": "制造加工阶段",
                "reason": "客户书面要求终止项目",
                "evidence": "客户终止通知（浏览器验收合成材料）",
                "completed_work_summary": "结构设计已完成，模具加工进行中",
                "incurred_cost_summary": "设计与当前加工费用已由项目负责人汇总，待财务在终止清单复核",
                "incurred_cost_amount": "128000.00",
                "currency": "CNY",
                "workflow_definition_id": workflow.id,
            }
            result = closure_tools.execute_tool(db, user, tool, arguments)
            summary = "已根据项目当前状态准备终止申请，请核对当前环节、完成工作、已发生费用和执行限制后确认提交。"
            suggestions = ["确认后仅提交审批；审批生效才会停止本地正常计划执行并建立终止处置清单。"]
        else:
            tool = "prepare_project_pause"
            arguments = {
                "project_id": project.id,
                "project_version": project.row_version,
                "effective_date": date.today().isoformat(),
                "expected_resume_date": (date.today() + timedelta(days=5)).isoformat(),
                "reason": "客户要求等待最终产品确认",
                "evidence": "客户暂停通知（浏览器验收合成材料）",
                "workflow_definition_id": workflow.id,
            }
            result = pause_tools.execute_tool(db, user, tool, arguments)
            summary = "已根据当前项目与计划资料准备整体暂停申请，请核对影响范围后确认提交审批。"
            suggestions = ["审批生效前项目仍处于执行中。"]

        step = m.Step(
            run_id=run.id,
            sequence=0,
            tool=tool,
            request_hash=content_hash({"key": tool, "arguments": arguments}),
            result=result,
        )
        db.add(step)
        db.flush()
        if confirm_contract:
            payload = {"step_id": step.id, "proposal_hash": content_hash(result["proposal"])}
            intent = business.create_intent(db, user, "contract.execute", step.id, payload)
            business.confirm_intent(db, user, intent["id"], intent["challenge"])
        run.result = {
            "response_kind": "BUSINESS",
            "summary": summary,
            "evidence_ids": [step.id],
            "suggestions": suggestions,
            "evidence": [{"id": step.id, "tool": step.tool, **result}],
        }
        output = {
            "database": parsed.path.lstrip("/"),
            "host": parsed.hostname,
            "port": parsed.port,
            "username": user.username,
            "password": password,
            "project_code": project_code,
            "conversation_id": conversation.id,
        }
    engine.dispose()
    return output


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--database-url", default="", help="PostgreSQL SQLAlchemy DSN. Defaults to AGENT_DATABASE_URL from env/.env.")
    parser.add_argument("--env-file", default=".env")
    parser.add_argument("--url-key", default="AGENT_DATABASE_URL")
    parser.add_argument("--password", required=True)
    parser.add_argument("--username", default="admin", help="Existing PostgreSQL-backed MoldPilot user. Defaults to admin.")
    parser.add_argument("--scenario", choices=["pause", "closure", "contact", "contract"], default="pause")
    parser.add_argument("--project-code", default="", help="Optional fixed smoke project code. Omit to generate a unique SMOKE-* code.")
    parser.add_argument("--pdf-file", default="", help="Optional real PDF used by the contract smoke scenario.")
    args = parser.parse_args()
    url = _database_url(args.env_file, args.url_key, args.database_url)
    print(build(url, args.password, args.scenario, args.project_code or None, args.username, args.pdf_file or None))
