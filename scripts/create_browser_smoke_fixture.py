"""Seed PostgreSQL data for built-in-browser smoke tests.

This script intentionally refuses SQLite. Browser validation must exercise the
same PostgreSQL/Navicat baseline used by local delivery, not a disposable file
database.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import date, timedelta
from decimal import Decimal
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
from domain_packs.mold.tools.erp.commercial import quotation_tools
from domain_packs.mold.tools.erp.finance import finance_context_tools
from domain_packs.mold.tools.erp.project import project_closure_tools as closure_tools
from domain_packs.mold.tools.erp.project import project_control_tools as pause_tools
from domain_packs.mold.tools.erp.project import plan_tools
from domain_packs.mold.erp.core import domains


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
        "plan_change": "工程联络影响计划变更审批（浏览器验收）",
        "contract": "销售合同审批（浏览器验收）",
        "contract_relation": "销售合同替代审批（浏览器验收）",
        "quotation": "客户报价版本审批（浏览器验收）",
        "bid_intake": "正式开工审批（浏览器验收）",
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
            else "plan_change" if scenario == "plan_change"
            else "sales_contract" if scenario in {"contract", "contract_relation"}
            else "project_close" if scenario == "closure"
            else "pause_resume"
        )
        workflow = _build_workflow(db, user.id, business_type, scenario)
        conversation = m.Conversation(
            user_id=user.id,
            title=(
                "工程联络影响项验收" if scenario == "contact"
                else "工程联络影响计划变更验收" if scenario == "plan_change"
                else "销售合同替代与历史回款验收" if scenario == "contract_relation"
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
                else f"请根据工程联络单对 {project_code} 准备计划变更审批建议。"
                if scenario == "plan_change"
                else f"请替代 {project_code} 的原销售合同，并把历史回款明确归属到新合同节点。"
                if scenario == "contract_relation"
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
        elif scenario == "plan_change":
            tool = "prepare_project_plan_change"
            baseline_tasks = list(db.scalars(select(m.PlanTask).where(m.PlanTask.plan_id == plan.id).order_by(m.PlanTask.key)))
            task_payload = [
                {
                    "key": row.key,
                    "name": row.name,
                    "owner_user_id": row.owner_user_id,
                    "planned_start": row.planned_start.isoformat(),
                    "planned_end": (
                        (row.planned_end + timedelta(days=2)).isoformat()
                        if row.key == "manufacture" else row.planned_end.isoformat()
                    ),
                    "prerequisites": [],
                }
                for row in baseline_tasks
            ]
            task_payload.append({
                "key": "trial",
                "name": "试模验证",
                "owner_user_id": user.id,
                "planned_start": (now().date() + timedelta(days=27)).isoformat(),
                "planned_end": (now().date() + timedelta(days=29)).isoformat(),
                "prerequisites": ["manufacture"],
            })
            arguments = {
                "project_id": project.id,
                "project_version": project.row_version,
                "previous_id": plan.id,
                "reason": "工程联络单确认图纸返工，制造节点顺延并新增试模验证节点",
                "workflow_definition_id": workflow.id,
                "tasks": task_payload,
            }
            result = plan_tools.execute_plan_tool(db, user, tool, arguments, run=run)
            summary = "已根据工程联络单影响准备项目计划变更审批建议，请核对顺延节点、新增试模节点和受影响部门后确认。"
            suggestions = [
                "确认后只提交 Agent BPM；审批生效前原计划、执行任务和客户承诺交期不会改变。",
                "审批生效后再由受影响部门确认执行影响，不能把计划变更建议当成已生效。",
            ]
        elif scenario in {"contract", "contract_relation"}:
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
            profile = db.get(m.ProjectProfile, project.id)
            if profile is None:
                profile = m.ProjectProfile(
                    project_id=project.id,
                    customer_id=customer.id,
                    owner_user_id=user.id,
                    execution_mode="INTERNAL",
                    settlement_status="OPEN",
                )
                db.add(profile)
            else:
                profile.customer_id = customer.id
            internal_number = f"{project.code}-MOLD-001"
            mold = db.scalar(
                select(m.Mold).where(m.Mold.internal_number == internal_number).limit(1)
            )
            if mold is None:
                mold = m.Mold(
                    internal_number=internal_number,
                    name=f"{project.name} 内部模具",
                    status="ACTIVE",
                )
                db.add(mold)
                db.flush()
            if not db.scalar(select(m.ProjectMold).where(
                m.ProjectMold.project_id == project.id,
                m.ProjectMold.mold_id == mold.id,
            )):
                db.add(m.ProjectMold(project_id=project.id, mold_id=mold.id))
            db.flush()
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
            predecessor = None
            receipt = None
            if scenario == "contract_relation":
                predecessor = m.BusinessSubject(
                    kind="sales_contract",
                    number=f"{project_code}-SC-OLD-SUBJECT",
                    project_id=project.id,
                    created_by=user.id,
                    status="EFFECTIVE",
                )
                db.add(predecessor)
                db.flush()
                db.add(m.ContractDetail(
                    subject_id=predecessor.id,
                    customer_id=customer.id,
                    supplier_id=None,
                    amount=Decimal("100000.00"),
                    currency="CNY",
                    contract_number=f"{project_code}-SC-OLD",
                    expected_date=date.today(),
                    replaces_id=None,
                    relation_type="ORIGINAL",
                    settlement_allocation_evidence=None,
                ))
                predecessor_stage = m.PaymentStage(
                    contract_id=predecessor.id,
                    name="原合同首款",
                    amount=Decimal("100000.00"),
                    currency="CNY",
                    condition="原合同审批生效",
                )
                db.add(predecessor_stage)
                db.flush()
                receipt = m.CustomerReceiptConfirmation(
                    project_id=project.id,
                    contract_subject_id=predecessor.id,
                    stage_id=predecessor_stage.id,
                    amount=Decimal("30000.00"),
                    currency="CNY",
                    received_date=date.today(),
                    reference=f"{project_code}-RCPT-001",
                    evidence="浏览器验收合成银行回单",
                    confirmed_by=user.id,
                    source_system="MANUAL",
                    source_ref=f"{project_code}-BANK-001",
                    note="浏览器验收合成历史回款",
                )
                db.add(receipt)
                db.flush()
            tool = "prepare_contract_record"
            arguments = {
                "project_id": project.id,
                "project_version": project.row_version,
                "contract_kind": "sales_contract",
                "customer_id": customer.id,
                "supplier_id": None,
                "amount": "120000.00" if scenario == "contract_relation" else "128000.00",
                "currency": "CNY",
                "contract_number": f"{project_code}-SC-{run_key}",
                "signed_date": date.today().isoformat(),
                "received_date": date.today().isoformat(),
                "delivery_due_date": (date.today() + timedelta(days=60)).isoformat(),
                "payment_method": "浏览器验收合同按结构化节点收款",
                "mapping_evidence": "浏览器验收已核对项目、内部模具号和客户合同原件",
                "expected_date": date.today().isoformat(),
                "replaces_id": predecessor.id if predecessor else None,
                "relation_type": "REPLACEMENT" if predecessor else "ORIGINAL",
                "settlement_allocation_evidence": (
                    "浏览器验收：财务按原银行回单确认历史回款归属" if predecessor else None
                ),
                "settlement_allocations": ([{
                    "source_record_id": receipt.id,
                    "target_stage_name": "替代合同首款",
                }] if receipt else []),
                "stages": ([
                    {"name": "替代合同首款", "amount": "50000.00", "condition": "替代合同审批生效"},
                    {"name": "替代合同验收款", "amount": "70000.00", "condition": "客户验收完成"},
                ] if predecessor else [
                    {"name": "预付款", "amount": "38400.00", "condition": "合同审批生效"},
                    {"name": "验收款", "amount": "89600.00", "condition": "客户验收完成"},
                ]),
                "remark": (
                    "浏览器验收合成替代合同；仅用于验证历史回款归属。"
                    if predecessor else "浏览器验收合成合同；仅用于验证附件随审批快照展示。"
                ),
                "workflow_definition_id": workflow.id,
                "file_ids": [blob.id],
                "document_source": "ELECTRONIC",
            }
            result = contract_tools.execute_contract_tool(db, user, tool, arguments, run=run)
            summary = (
                "替代合同已审批生效；原合同保留为历史版本，历史回款仍在原凭证并按新合同节点计入一次。"
                if predecessor else
                "销售合同登记已由本人确认并提交审批；确认卡保留合同字段和附件快照供后续追溯。"
            )
            suggestions = ([
                "这是浏览器验收合成数据；可查询合同关系、当前有效金额与历史回款归属。"
            ] if predecessor else [
                "确认后合同原件会冻结进审批快照；审批生效前不视为正式合同。"
            ])
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
            submitted = business.confirm_intent(db, user, intent["id"], intent["challenge"])
            if scenario == "contract_relation":
                instance = db.get(m.ApprovalInstance, submitted["instance_id"])
                seat = db.scalar(select(m.ApprovalSeat).where(
                    m.ApprovalSeat.instance_id == instance.id,
                    m.ApprovalSeat.user_id == user.id,
                    m.ApprovalSeat.status == "PENDING",
                ))
                decision = {
                    "instance_id": instance.id,
                    "seat_id": seat.id,
                    "seat_version": seat.version,
                    "version": instance.version,
                    "snapshot_hash": instance.snapshot_hash,
                    "decision": "APPROVE",
                    "comment": "浏览器验收合成审批：合同替代及历史回款归属已核对",
                }
                approval_intent = business.create_intent(
                    db, user, "approval.decide", instance.id, decision
                )
                business.confirm_intent(
                    db, user, approval_intent["id"], approval_intent["challenge"]
                )
        call_id = f"browser-smoke-{step.id}"
        run.checkpoint = {
            **(run.checkpoint or {}),
            "agent_permission_mode": "ask",
            "completed_at": now().isoformat(),
            "messages": [
                {
                    "role": "assistant",
                    "content": "",
                    "tool_calls": [{
                        "id": call_id,
                        "type": "function",
                        "function": {
                            "name": tool,
                            "arguments": json.dumps(arguments, ensure_ascii=False),
                        },
                    }],
                },
                {
                    "role": "tool",
                    "tool_call_id": call_id,
                    "content": json.dumps({"evidence_id": step.id}, ensure_ascii=False),
                },
            ],
        }
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


def build_quotation(
    database_url: str,
    password: str,
    project_code: str | None = None,
    username: str = "admin",
):
    """Create one real quotation proposal, confirmation and approval for browser QA."""
    _require_postgresql(database_url)
    engine = make_engine(database_url)
    parsed = urlsplit(database_url)
    run_key = now().strftime("%m%d%H%M%S")
    project_code = project_code or f"SMOKE-QUOTATION-{run_key}"
    factory = sessionmaker(engine, expire_on_commit=False)
    try:
        with factory.begin() as db:
            user = db.scalar(select(m.User).where(m.User.username == username).limit(1))
            if user is None or not user.active:
                raise SystemExit(
                    f"Smoke user {username!r} is missing or inactive. "
                    "Run database/init_moldpilot_admin.sql first."
                )
            project = _upsert_one(
                db,
                m.Project,
                [m.Project.code == project_code],
                {"code": project_code, "name": "浏览器报价版本验收项目", "status": "DRAFT"},
            )
            workflow = _build_workflow(db, user.id, "quotation", "quotation")
            conversation = m.Conversation(user_id=user.id, title="客户报价版本与反馈验收")
            db.add(conversation)
            db.flush()
            run = m.Run(
                conversation_id=conversation.id,
                user_id=user.id,
                security_version=user.security_version,
                prompt=f"请根据本轮客户资料为 {project_code} 准备内部加工报价版本并提交审批。",
                status="SUCCEEDED",
                checkpoint={
                    "authorization_hash": fingerprint(db, user),
                    "agent_permission_mode": "ask",
                },
            )
            db.add(run)
            db.flush()
            pdf = (
                b"%PDF-1.4\n1 0 obj<</Type/Catalog/Pages 2 0 R>>endobj\n"
                b"2 0 obj<</Type/Pages/Count 0>>endobj\ntrailer<</Root 1 0 R>>\n%%EOF\n"
                + f"% quotation browser smoke {run_key}\n".encode()
            )
            digest = sha256(pdf).hexdigest()
            key = uuid4().hex + "/" + digest
            storage = object_storage.put(key, pdf, "application/pdf")
            blob = m.FileObject(
                owner_id=user.id,
                conversation_id=conversation.id,
                request_key=str(uuid4()),
                filename=f"{project_code}-客户报价资料.pdf",
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
            tool = "prepare_quotation_version"
            arguments = {
                "project_id": project.id,
                "project_version": project.row_version,
                "previous_id": None,
                "quotation_number": f"{project_code}-QUOTE",
                "version": 1,
                "preliminary_execution_mode": "INTERNAL",
                "quoted_amount": "128000.00",
                "currency": "CNY",
                "promised_delivery_date": (date.today() + timedelta(days=90)).isoformat(),
                "payment_terms": "合同生效30%，首轮试模40%，终验30%",
                "cost_amount": "92000.00",
                "cost_evidence": "客户二维图、产品数据与浏览器验收合成成本清单",
                "process_analysis": "结构设计、钢材粗精加工、热处理、电极、装配和两轮试模",
                "duration_days": 75,
                "duration_evidence": "按设计10天、采购15天、制造35天、装配试模15天估算",
                "supplier_quote_amount": None,
                "supplier_delivery_date": None,
                "supplier_requirements": None,
                "supplier_quote_evidence": None,
                "customer_company_snapshot": "浏览器验收客户有限公司",
                "customer_contact_snapshot": "报价负责人 张工",
                "owner_user_id": user.id,
                "source_summary": {
                    "mold_number": project_code,
                    "mold_type": "注塑模",
                    "cavity": "1x2",
                },
                "source_kind": "UPLOAD",
                "source_ref": f"browser-quotation-{run_key}",
                "file_ids": [blob.id],
                "workflow_definition_id": workflow.id,
            }
            result = quotation_tools.execute_quotation_tool(db, user, tool, arguments, run=run)
            step = m.Step(
                run_id=run.id,
                sequence=0,
                tool=tool,
                request_hash=content_hash({"key": tool, "arguments": arguments}),
                result=result,
            )
            db.add(step)
            db.flush()
            payload = {"step_id": step.id, "proposal_hash": content_hash(result["proposal"])}
            intent = business.create_intent(db, user, "quotation.execute", step.id, payload)
            submitted = business.confirm_intent(db, user, intent["id"], intent["challenge"])
            instance = db.get(m.ApprovalInstance, submitted["instance_id"])
            seat = db.scalar(select(m.ApprovalSeat).where(
                m.ApprovalSeat.instance_id == instance.id,
                m.ApprovalSeat.user_id == user.id,
                m.ApprovalSeat.status == "PENDING",
            ))
            decision = {
                "instance_id": instance.id,
                "seat_id": seat.id,
                "seat_version": seat.version,
                "version": instance.version,
                "snapshot_hash": instance.snapshot_hash,
                "decision": "APPROVE",
                "comment": "浏览器验收合成审批：报价资料、成本、工艺、工期、价格和交期已核对",
            }
            approval_intent = business.create_intent(
                db, user, "approval.decide", instance.id, decision
            )
            business.confirm_intent(
                db, user, approval_intent["id"], approval_intent["challenge"]
            )
            run.result = {
                "response_kind": "BUSINESS",
                "summary": (
                    f"{project_code} 的客户报价 {project_code}-QUOTE V1 已审批生效。"
                    "报价金额 128000 CNY，内部成本 92000 CNY，初步方式为内部加工，"
                    "工期估算 75 天；客户资料已按来源和文件摘要冻结。"
                ),
                "evidence_ids": [step.id],
                "suggestions": [
                    "该记录是浏览器验收合成数据；客户接受或要求修改应另行登记反馈，承接决定仍须独立审批。"
                ],
                "evidence": [{"id": step.id, "tool": step.tool, **result}],
            }
            return {
                "database": parsed.path.lstrip("/"),
                "host": parsed.hostname,
                "port": parsed.port,
                "username": user.username,
                "password": password,
                "project_code": project_code,
                "conversation_id": conversation.id,
            }
    finally:
        engine.dispose()


def build_customer_receipt(
    database_url: str,
    password: str,
    project_code: str | None = None,
    username: str = "admin",
):
    """Create a pending customer-receipt confirmation card for browser QA.

    The fixture intentionally stops before confirmation.  The browser must
    display the proposal, let the user confirm it, and then wake the Agent
    resume path; the confirm endpoint is the only code path that may create
    the receipt ledger row.
    """
    _require_postgresql(database_url)
    engine = make_engine(database_url)
    parsed = urlsplit(database_url)
    run_key = now().strftime("%m%d%H%M%S")
    project_code = project_code or f"SMOKE-CUSTOMER-RECEIPT-{run_key}"
    factory = sessionmaker(engine, expire_on_commit=False)
    try:
        with factory.begin() as db:
            user = db.scalar(select(m.User).where(m.User.username == username).limit(1))
            if user is None or not user.active:
                raise SystemExit(f"Smoke user {username!r} is missing or inactive.")
            project = _upsert_one(
                db,
                m.Project,
                [m.Project.code == project_code],
                {"code": project_code, "name": "浏览器回款确认验收项目", "status": "ACTIVE"},
            )
            customer = _upsert_one(
                db,
                m.Customer,
                [m.Customer.code == f"{project_code}-CUSTOMER"],
                {"code": f"{project_code}-CUSTOMER", "name": "浏览器回款验收客户", "rule_key": "standard", "active": True},
            )
            profile = db.get(m.ProjectProfile, project.id)
            if profile is None:
                db.add(m.ProjectProfile(
                    project_id=project.id,
                    customer_id=customer.id,
                    owner_user_id=user.id,
                    execution_mode="INTERNAL",
                    customer_due_date=date.today() + timedelta(days=45),
                    settlement_status="OPEN",
                ))
            else:
                profile.customer_id = customer.id
                profile.owner_user_id = user.id
                profile.execution_mode = "INTERNAL"
                profile.settlement_status = "OPEN"

            contract = _upsert_one(
                db,
                m.BusinessSubject,
                [m.BusinessSubject.number == f"{project_code}-SC"],
                {
                    "kind": "sales_contract",
                    "number": f"{project_code}-SC",
                    "project_id": project.id,
                    "created_by": user.id,
                    "status": "EFFECTIVE",
                },
            )
            contract_detail = db.get(m.ContractDetail, contract.id)
            if contract_detail is None:
                db.add(m.ContractDetail(
                    subject_id=contract.id,
                    customer_id=customer.id,
                    supplier_id=None,
                    amount=Decimal("100000.00"),
                    currency="CNY",
                    contract_number=f"{project_code}-SC-001",
                    expected_date=date.today(),
                    replaces_id=None,
                ))
            stage = db.scalar(select(m.PaymentStage).where(
                m.PaymentStage.contract_id == contract.id,
                m.PaymentStage.name == "首付款",
            ).limit(1))
            if stage is None:
                stage = m.PaymentStage(
                    contract_id=contract.id,
                    name="首付款",
                    amount=Decimal("30000.00"),
                    currency="CNY",
                    condition="合同生效后客户回款",
                    condition_confirmed=True,
                    condition_evidence="浏览器验收已核对合同付款节点",
                )
                db.add(stage)
                db.flush()

            conversation = m.Conversation(user_id=user.id, title="客户实际回款确认验收")
            db.add(conversation)
            db.flush()
            run = m.Run(
                conversation_id=conversation.id,
                user_id=user.id,
                security_version=user.security_version,
                prompt=f"请登记 {project_code} 客户已到账的首付款。",
                status="SUCCEEDED",
                checkpoint={
                    "authorization_hash": fingerprint(db, user),
                    "agent_permission_mode": "ask",
                },
            )
            db.add(run)
            db.flush()
            arguments = {
                "project_id": project.id,
                "project_version": project.row_version,
                "contract_subject_id": contract.id,
                "stage_id": stage.id,
                "amount": "12000.00",
                "currency": "CNY",
                "received_date": date.today().isoformat(),
                "reference": f"{project_code}-RCPT-001",
                "evidence": "浏览器验收合成银行回单",
                "source_ref": f"{project_code}-BANK-001",
                "note": "首付款分次到账，本次登记第二笔回款",
            }
            tool = "prepare_customer_receipt_confirmation"
            result = finance_context_tools.execute_finance_tool(db, user, tool, arguments, run=run)
            step = m.Step(
                run_id=run.id,
                sequence=0,
                tool=tool,
                request_hash=content_hash({"key": tool, "arguments": arguments}),
                result=result,
            )
            db.add(step)
            db.flush()
            run.result = {
                "response_kind": "BUSINESS",
                "summary": "已准备客户首付款回款登记确认卡。本人确认后才写入实际回款台账，本次只登记回款，不代表开票、结算或项目关闭。",
                "evidence_ids": [step.id],
                "suggestions": ["请核对合同、收款节点、金额、到账日期和银行凭证号；确认后再写入回款事实。"],
                "evidence": [{"id": step.id, "tool": step.tool, **result}],
            }
            return {
                "database": parsed.path.lstrip("/"),
                "host": parsed.hostname,
                "port": parsed.port,
                "username": user.username,
                "password": password,
                "project_code": project_code,
                "conversation_id": conversation.id,
                "step_id": step.id,
            }
    finally:
        engine.dispose()


def build_bid_intake(
    database_url: str,
    password: str,
    project_code: str | None = None,
    username: str = "admin",
):
    """Create a six-stage kickoff fixture with complete customer start conditions."""
    _require_postgresql(database_url)
    engine = make_engine(database_url)
    parsed = urlsplit(database_url)
    run_key = now().strftime("%m%d%H%M%S")
    project_code = project_code or f"SMOKE-BID-INTAKE-{run_key}"
    factory = sessionmaker(engine, expire_on_commit=False)
    try:
        with factory.begin() as db:
            user = db.scalar(select(m.User).where(m.User.username == username).limit(1))
            if user is None or not user.active:
                raise SystemExit(f"Smoke user {username!r} is missing or inactive.")
            project = _upsert_one(
                db, m.Project, [m.Project.code == project_code],
                {"code": project_code, "name": "浏览器中标接收与开工条件验收项目", "status": "DRAFT"},
            )
            customer = _upsert_one(
                db, m.Customer, [m.Customer.code == f"{project_code}-CUSTOMER"],
                {"code": f"{project_code}-CUSTOMER", "name": "浏览器验收客户", "rule_key": "standard", "active": True},
            )
            profile = db.get(m.ProjectProfile, project.id)
            if profile is None:
                db.add(m.ProjectProfile(
                    project_id=project.id, customer_id=customer.id, owner_user_id=user.id,
                    execution_mode="INTERNAL", customer_due_date=date.today() + timedelta(days=90),
                ))
            else:
                profile.customer_id = customer.id
                profile.owner_user_id = user.id
                profile.execution_mode = "INTERNAL"
                profile.customer_due_date = date.today() + timedelta(days=90)

            quotation = m.BusinessSubject(
                kind="quotation", number=f"{project_code}-QUOTE-SUBJECT", project_id=project.id,
                created_by=user.id, status="EFFECTIVE",
            )
            db.add(quotation); db.flush()
            db.add(m.QuotationDetail(
                subject_id=quotation.id, previous_id=None, quotation_number=f"{project_code}-QUOTE",
                version=1, preliminary_execution_mode="INTERNAL", quoted_amount=Decimal("128000.00"),
                currency="CNY", promised_delivery_date=date.today() + timedelta(days=90),
                payment_terms="合同生效30%，首轮试模40%，终验30%", cost_amount=Decimal("92000.00"),
                cost_evidence="浏览器验收合成成本清单", process_analysis="设计、采购、制造、装配和试模",
                duration_days=75, duration_evidence="浏览器验收合成工期评估",
                supplier_quote_amount=None, supplier_delivery_date=None, supplier_requirements=None,
                supplier_quote_evidence=None, customer_company_snapshot=customer.name,
                customer_contact_snapshot="客户项目经理", owner_user_id=user.id, source_summary={},
            ))

            conversation = m.Conversation(user_id=user.id, title=f"{project_code} 中标接收与开工条件验收")
            db.add(conversation); db.flush()
            pdf = (
                b"%PDF-1.4\n1 0 obj<</Type/Catalog/Pages 2 0 R>>endobj\n"
                b"2 0 obj<</Type/Pages/Count 0>>endobj\ntrailer<</Root 1 0 R>>\n%%EOF\n"
                + f"% bid intake browser smoke {run_key}\n".encode()
            )
            digest = sha256(pdf).hexdigest()
            key = uuid4().hex + "/" + digest
            storage = object_storage.put(key, pdf, "application/pdf")
            blob = m.FileObject(
                owner_id=user.id, conversation_id=conversation.id, request_key=str(uuid4()),
                filename=f"{project_code}-客户开工通知.pdf", media_type="application/pdf",
                size=len(pdf), sha256=digest, object_key=key, **storage,
            )
            db.add(blob); db.flush()

            case = m.BidIntakeCase(project_id=project.id, created_by=user.id)
            db.add(case); db.flush()
            revision = m.BidIntakeRevision(
                case_id=case.id, version=1, previous_revision_id=None, source_kind="EMAIL",
                source_ref=f"browser-bid-intake-{run_key}", source_fingerprint=sha256(
                    f"{project.id}:{run_key}".encode()
                ).hexdigest(), received_date=date.today(), customer_classification="OTHER",
                classification_evidence="销售人员已按客户档案人工确认", classification_confirmed_by=user.id,
                customer_company=customer.name, customer_contact="客户项目经理",
                customer_mold_number=f"{project_code}-MOLD", customer_model_or_material="MODEL-SMOKE",
                project_name_snapshot=project.name, amount=Decimal("128000.00"), currency="CNY",
                our_recipient="项目负责人", external_order_number=f"{project_code}-ORDER",
                external_start_date=date.today(), customer_due_date=date.today() + timedelta(days=90),
                customer_process_confirmed=True,
                customer_process_confirmation_evidence="客户工艺方案已由双方项目负责人人工确认",
                matched_quotation_subject_id=quotation.id, historical_mold_number=None,
                historical_relation_kind=None, match_result="MATCHED",
                match_evidence="项目编号、客户模号和报价版本均已人工核对一致",
                notes="仅用于浏览器验收", recorded_by=user.id,
            )
            db.add(revision); db.flush()
            db.add(m.BidIntakeAttachment(
                revision_id=revision.id, file_id=blob.id, role="EXTERNAL_START_NOTICE",
                content_sha256=blob.sha256, title=blob.filename,
            ))

            acceptance = m.BusinessSubject(
                kind="quote_acceptance", number=f"{project_code}-ACCEPT", project_id=project.id,
                created_by=user.id, status="EFFECTIVE",
            )
            db.add(acceptance); db.flush()
            db.add(m.BusinessDecisionDetail(
                subject_id=acceptance.id, source_subject_id=quotation.id, decision="ACCEPT",
                execution_mode="INTERNAL", effective_date=date.today(),
                evidence="中标资料与客户开工条件已由项目负责人核对",
                amount=Decimal("128000.00"), currency="CNY",
            ))
            db.add(m.BidIntakeLifecycleLink(
                case_id=case.id, subject_id=acceptance.id, source_revision_id=revision.id,
                link_kind="ACCEPTANCE", linked_by=user.id,
            ))
            _build_workflow(db, user.id, "internal_start", "bid_intake")

            run = m.Run(
                conversation_id=conversation.id, user_id=user.id, security_version=user.security_version,
                prompt=f"只读核对 {project_code} 的项目启动链路和正式开工条件。",
                status="SUCCEEDED", checkpoint={"authorization_hash": fingerprint(db, user)},
                result={
                    "response_kind": "BUSINESS",
                    "summary": (
                        f"{project_code} 已有生效报价、中标接收 V1、有效承接和完整客户开工条件；"
                        "尚未正式下达内部开工，销售合同可并行补齐。"
                    ),
                    "evidence_ids": [],
                    "suggestions": ["可在新会话中只读查询项目启动链路，验证六阶段投影与开工门禁。"],
                    "evidence": [],
                },
            )
            db.add(run)
            return {
                "database": parsed.path.lstrip("/"), "host": parsed.hostname, "port": parsed.port,
                "username": user.username, "password": password, "project_code": project_code,
                "conversation_id": conversation.id,
            }
    finally:
        engine.dispose()


def build_internal_start_handoff(
    database_url: str,
    password: str,
    project_code: str | None = None,
    username: str = "admin",
):
    """Create an effective formal start with all five role handoffs."""
    output = build_bid_intake(
        database_url, password, project_code=project_code, username=username
    )
    engine = make_engine(database_url)
    factory = sessionmaker(engine, expire_on_commit=False)
    try:
        with factory.begin() as db:
            user = db.scalar(select(m.User).where(m.User.username == username).limit(1))
            project = db.scalar(
                select(m.Project).where(m.Project.code == output["project_code"]).limit(1)
            )
            acceptance = db.scalar(
                select(m.BusinessSubject)
                .where(
                    m.BusinessSubject.project_id == project.id,
                    m.BusinessSubject.kind == "quote_acceptance",
                    m.BusinessSubject.status == "EFFECTIVE",
                )
                .order_by(m.BusinessSubject.created_at.desc())
                .limit(1)
            )
            case = db.scalar(
                select(m.BidIntakeCase).where(m.BidIntakeCase.project_id == project.id)
            )
            revision = db.scalar(
                select(m.BidIntakeRevision)
                .where(m.BidIntakeRevision.case_id == case.id)
                .order_by(m.BidIntakeRevision.version.desc())
                .limit(1)
            )
            for role_key in (
                "DESIGN_OWNER",
                "PURCHASE_OWNER",
                "MANUFACTURING_OWNER",
                "ASSEMBLY_OWNER",
                "FINANCE_OWNER",
                "BUSINESS_OWNER",
                "MARKETING_OWNER",
            ):
                existing = db.scalar(select(m.ProjectRoleMember).where(
                    m.ProjectRoleMember.project_id == project.id,
                    m.ProjectRoleMember.role_key == role_key,
                    m.ProjectRoleMember.user_id == user.id,
                ))
                if not existing:
                    db.add(m.ProjectRoleMember(
                        project_id=project.id, role_key=role_key, user_id=user.id
                    ))
            internal_number = f"{project.code}-MOLD-001"
            mold = db.scalar(
                select(m.Mold).where(m.Mold.internal_number == internal_number).limit(1)
            )
            if mold is None:
                mold = m.Mold(
                    internal_number=internal_number,
                    name=f"{project.name} 内部模具",
                    status="ACTIVE",
                )
                db.add(mold)
                db.flush()
            project_mold = db.scalar(
                select(m.ProjectMold).where(
                    m.ProjectMold.project_id == project.id,
                    m.ProjectMold.mold_id == mold.id,
                )
            )
            if project_mold is None:
                db.add(m.ProjectMold(project_id=project.id, mold_id=mold.id))
                db.flush()
            start = m.BusinessSubject(
                kind="internal_start",
                number=f"{project.code}-START",
                project_id=project.id,
                created_by=user.id,
                status="APPROVED",
            )
            db.add(start)
            db.flush()
            db.add(m.BusinessDecisionDetail(
                subject_id=start.id,
                source_subject_id=acceptance.id,
                decision="START",
                execution_mode="INTERNAL",
                effective_date=date.today(),
                evidence="浏览器验收：客户开工通知、工艺方案和项目负责人正式下达均已核对",
                amount=None,
                currency=None,
            ))
            db.add(m.BidIntakeLifecycleLink(
                case_id=case.id,
                subject_id=start.id,
                source_revision_id=revision.id,
                link_kind="INTERNAL_START",
                linked_by=user.id,
            ))
            db.flush()
            from domain_packs.mold.erp.project import start_materials

            expected_contract_date = date.today() - timedelta(days=2)
            material = start_materials.build(
                db,
                project,
                revision.id,
                date.today(),
                expected_contract_date=expected_contract_date,
                contract_visibility=True,
            )
            start_materials.create(
                db,
                user,
                start,
                revision.id,
                material,
                expected_contract_date=expected_contract_date,
            )
            domains.apply(db, user, start)
            output["start_subject_id"] = start.id
        return output
    finally:
        engine.dispose()


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--database-url", default="", help="PostgreSQL SQLAlchemy DSN. Defaults to AGENT_DATABASE_URL from env/.env.")
    parser.add_argument("--env-file", default=".env")
    parser.add_argument("--url-key", default="AGENT_DATABASE_URL")
    parser.add_argument("--password", required=True)
    parser.add_argument("--username", default="admin", help="Existing PostgreSQL-backed MoldPilot user. Defaults to admin.")
    parser.add_argument("--scenario", choices=["pause", "closure", "contact", "plan_change", "contract", "contract_relation", "quotation", "bid_intake", "internal_start_handoff", "customer_receipt"], default="pause")
    parser.add_argument("--project-code", default="", help="Optional fixed smoke project code. Omit to generate a unique SMOKE-* code.")
    parser.add_argument("--pdf-file", default="", help="Optional real PDF used by the contract smoke scenario.")
    args = parser.parse_args()
    url = _database_url(args.env_file, args.url_key, args.database_url)
    if args.scenario == "quotation":
        print(build_quotation(url, args.password, args.project_code or None, args.username))
    elif args.scenario == "bid_intake":
        print(build_bid_intake(url, args.password, args.project_code or None, args.username))
    elif args.scenario == "internal_start_handoff":
        print(build_internal_start_handoff(url, args.password, args.project_code or None, args.username))
    elif args.scenario == "customer_receipt":
        print(build_customer_receipt(url, args.password, args.project_code or None, args.username))
    else:
        print(build(url, args.password, args.scenario, args.project_code or None, args.username, args.pdf_file or None))
