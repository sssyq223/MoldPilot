from collections import defaultdict
from datetime import date
from decimal import Decimal, InvalidOperation

from fastapi import APIRouter, Depends
from pydantic import Field, ValidationError
from sqlalchemy import select

from . import models as m
from .authorization import access, fingerprint, predicate, require, select_fields
from .bpm import content_hash
from .confirmation_policy import proposal_confirmation_policy
from .db import get_db, now
from .domain_commands import CustomerReceipt, validate_customer_receipt
from .errors import DomainError
from .project_dossier import ProjectDossierInput
from .schemas import StrictModel
from .security import current_user


FINANCE_KINDS = {"sales_contract", "full_outsource_contract", "supplier_payment", "finance_correction", "project_close", "internal_start"}


class CustomerReceiptProposalInput(StrictModel):
    project_id: str = Field(min_length=1, max_length=36)
    project_version: int = Field(ge=1)
    contract_subject_id: str = Field(min_length=1, max_length=36)
    stage_id: str | None = Field(default=None, max_length=36)
    amount: Decimal = Field(gt=0, max_digits=18, decimal_places=2)
    currency: str = Field(pattern=r"^[A-Z]{3}$")
    received_date: date
    reference: str = Field(min_length=1, max_length=100)
    evidence: str = Field(min_length=1, max_length=4000)
    source_ref: str | None = Field(default=None, max_length=120)
    note: str | None = Field(default=None, max_length=4000)


def _strength(value, needle):
    if value is None:
        return 0
    value = str(value).casefold()
    needle = str(needle).casefold()
    return 100 if value == needle else 50 if needle in value else 0


def _project_card(db, user, project, matched_by=()):
    fields = access(db, user, "project.read", {"project_id": project.id}).fields
    card = select_fields({"id": project.id, "code": project.code, "name": project.name, "status": project.status}, fields)
    card["matched_by"] = sorted(set(matched_by))
    return card


def _can_read_kind(db, user, kind, project_id, category=None):
    return access(db, user, f"{kind}.read", {"project_id": project_id, "category": category}).allowed


def _subject_rows(db, user, project_id, kind, limit=50):
    from .domains import data as subject_data

    rows = []
    for subject in db.scalars(
        select(m.BusinessSubject)
        .where(m.BusinessSubject.project_id == project_id, m.BusinessSubject.kind == kind)
        .order_by(m.BusinessSubject.created_at.desc(), m.BusinessSubject.id)
        .limit(limit)
    ):
        try:
            rows.append(subject_data(db, user, subject))
        except DomainError:
            continue
    return rows


def _visible_projects(db, user):
    gate = predicate(db, user, "project.read", {"project_id": m.Project.id})
    dossier_gate = predicate(db, user, "project.dossier.read", {"project_id": m.Project.id})
    rows = list(db.scalars(select(m.Project).where(gate, dossier_gate).order_by(m.Project.code).limit(501)))
    return rows[:500], len(rows) > 500


def _resolve(db, user, data: ProjectDossierInput):
    visible, truncated = _visible_projects(db, user)
    by_id = {project.id: project for project in visible}
    if data.project_id:
        project = by_id.get(data.project_id)
        return project, ([] if project else None), truncated

    scores = defaultdict(int)
    reasons = defaultdict(list)

    def add(project_id, value, label):
        if project_id not in by_id:
            return
        score = _strength(value, data.identifier)
        if score:
            scores[project_id] = max(scores[project_id], score)
            reasons[project_id].append(label)

    for project in visible:
        add(project.id, project.id, "项目ID")
        add(project.id, project.code, "项目编号")
        add(project.id, project.name, "项目名称")
    if by_id:
        for link, mold in db.execute(select(m.ProjectMold, m.Mold).join(m.Mold, m.Mold.id == m.ProjectMold.mold_id).where(m.ProjectMold.project_id.in_(list(by_id))).limit(501)):
            add(link.project_id, mold.internal_number, "模具号")
            add(link.project_id, mold.name, "模具名称")
        for case in db.scalars(select(m.ContactCase).where(m.ContactCase.project_id.in_(list(by_id))).limit(501)):
            add(case.project_id, case.title, "工程联络标题")
            add(case.project_id, case.customer_ref, "客户引用")
            add(case.project_id, case.mold_number, "联络模具号")
            add(case.project_id, case.product_ref, "产品/料品号")
        for subject in db.scalars(select(m.BusinessSubject).where(m.BusinessSubject.project_id.in_(list(by_id)), m.BusinessSubject.kind.in_(FINANCE_KINDS)).limit(501)):
            add(subject.project_id, subject.number, "财务相关业务单号")
            if subject.kind in {"sales_contract", "full_outsource_contract"} and _can_read_kind(db, user, subject.kind, subject.project_id, subject.category):
                detail = db.get(m.ContractDetail, subject.id)
                if detail:
                    add(subject.project_id, detail.contract_number, "合同编号")
    if not scores:
        return None, [], truncated
    best = max(scores.values())
    ids = [project_id for project_id, score in scores.items() if score == best]
    if len(ids) != 1:
        return None, [_project_card(db, user, by_id[project_id], reasons[project_id]) for project_id in ids[:20]], truncated
    return by_id[ids[0]], reasons[ids[0]], truncated


def _profile(db, user, project_id):
    profile = db.get(m.ProjectProfile, project_id)
    if not profile:
        return None
    fields = access(db, user, "project.dossier.read", {"project_id": project_id}).fields
    return select_fields(
        {
            "customer_id": profile.customer_id,
            "owner_user_id": profile.owner_user_id,
            "execution_mode": profile.execution_mode,
            "customer_due_date": profile.customer_due_date.isoformat() if profile.customer_due_date else None,
            "settlement_status": profile.settlement_status,
        },
        fields | {"customer_id", "owner_user_id", "execution_mode", "customer_due_date", "settlement_status"},
    )


def _as_decimal(value):
    if value is None:
        return None
    try:
        return Decimal(str(value))
    except InvalidOperation:
        return None


def _money_total(items, amount_key="amount", currency_key="currency"):
    totals = defaultdict(Decimal)
    for item in items:
        amount = _as_decimal(item.get(amount_key))
        currency = item.get(currency_key)
        if amount is not None and currency:
            totals[currency] += amount
    return [{"currency": currency, "amount": str(amount)} for currency, amount in sorted(totals.items())]


def _contract_context(rows, role):
    result = []
    payment_nodes = []
    for row in rows:
        detail = row.get("detail") if isinstance(row.get("detail"), dict) else {}
        stages = []
        for stage in detail.get("stages") or []:
            node = {
                "contract_id": row.get("id"),
                "contract_number": detail.get("contract_number"),
                "contract_role": role,
                "stage_id": stage.get("id"),
                "name": stage.get("name"),
                "amount": stage.get("amount"),
                "currency": stage.get("currency") or detail.get("currency"),
                "condition": stage.get("condition"),
                "condition_confirmed": stage.get("condition_confirmed"),
                "condition_evidence_present": bool(stage.get("condition_evidence")),
                "status": "CONDITION_CONFIRMED" if stage.get("condition_confirmed") else "CONDITION_PENDING",
            }
            stages.append(node)
            payment_nodes.append(node)
        result.append(
            {
                "id": row.get("id"),
                "number": row.get("number"),
                "status": row.get("status"),
                "role": role,
                "contract_number": detail.get("contract_number"),
                "amount": detail.get("amount"),
                "currency": detail.get("currency"),
                "expected_date": detail.get("expected_date"),
                "replaces_id": detail.get("replaces_id"),
                "stage_count": len(stages),
                "stages": stages,
            }
        )
    return result, payment_nodes


def _supplier_payments(rows, sales_contracts, outsource_contracts):
    stage_contract = {}
    for contract in sales_contracts + outsource_contracts:
        for stage in contract.get("stages") or []:
            stage_contract[stage.get("stage_id")] = {
                "contract_id": contract.get("id"),
                "contract_number": contract.get("contract_number"),
                "contract_role": contract.get("role"),
                "stage_name": stage.get("name"),
                "stage_condition": stage.get("condition"),
            }
    requests = []
    all_confirmations = []
    outstanding_reservations = []
    for row in rows:
        detail = row.get("detail") if isinstance(row.get("detail"), dict) else {}
        payments = detail.get("payments") or []
        stage = stage_contract.get(detail.get("stage_id"), {})
        confirmations = [
            {
                "id": payment.get("id"),
                "amount": payment.get("amount"),
                "currency": payment.get("currency") or detail.get("currency"),
                "paid_date": payment.get("paid_date"),
                "reference": payment.get("reference"),
                "evidence_present": bool(payment.get("evidence")),
                "is_reversal": _as_decimal(payment.get("amount")) is not None and _as_decimal(payment.get("amount")) < 0,
                "reversal_of_id": payment.get("reversal_of_id"),
            }
            for payment in payments
        ]
        all_confirmations.extend(confirmations)
        reservation = _as_decimal(detail.get("reservation")) or Decimal(0)
        if reservation:
            outstanding_reservations.append({"id": row.get("id"), "amount": str(reservation), "currency": detail.get("currency")})
        requests.append(
            {
                "id": row.get("id"),
                "number": row.get("number"),
                "status": row.get("status"),
                "category": row.get("category"),
                "stage_id": detail.get("stage_id"),
                **stage,
                "requested_amount": detail.get("amount"),
                "currency": detail.get("currency"),
                "reservation": detail.get("reservation"),
                "approval_effect": "APPROVED_FOR_PAYMENT" if row.get("status") == "EFFECTIVE" else "NOT_APPROVED_OR_NOT_EFFECTIVE",
                "payment_confirmations": confirmations,
                "confirmed_total": _money_total(confirmations),
            }
        )
    return {
        "requests": requests,
        "confirmed_totals": _money_total(all_confirmations),
        "outstanding_reservations": outstanding_reservations,
        "outstanding_reservation_totals": _money_total(outstanding_reservations),
    }


def _customer_receipts(db, user, project_id, sales_contracts, customer_nodes):
    if not access(db, user, "customer_receipt.read", {"project_id": project_id}).allowed:
        return {"receipts": [], "confirmed_totals": [], "by_stage": [], "unallocated_receipts": []}, True

    visible_contract_ids = {contract.get("id") for contract in sales_contracts if contract.get("id")}
    stage_map = {
        node.get("stage_id"): {
            "contract_id": node.get("contract_id"),
            "contract_number": node.get("contract_number"),
            "stage_id": node.get("stage_id"),
            "stage_name": node.get("name"),
            "stage_condition": node.get("condition"),
            "stage_amount": node.get("amount"),
            "currency": node.get("currency"),
        }
        for node in customer_nodes
        if node.get("stage_id")
    }
    receipts = []
    for receipt in db.scalars(
        select(m.CustomerReceiptConfirmation)
        .where(m.CustomerReceiptConfirmation.project_id == project_id)
        .order_by(m.CustomerReceiptConfirmation.received_date.desc(), m.CustomerReceiptConfirmation.created_at.desc(), m.CustomerReceiptConfirmation.id)
        .limit(100)
    ):
        if receipt.contract_subject_id not in visible_contract_ids:
            continue
        stage = stage_map.get(receipt.stage_id, {})
        receipts.append(
            {
                "id": receipt.id,
                "contract_id": receipt.contract_subject_id,
                "contract_number": stage.get("contract_number"),
                "stage_id": receipt.stage_id,
                "stage_name": stage.get("stage_name"),
                "amount": str(receipt.amount),
                "currency": receipt.currency,
                "received_date": receipt.received_date.isoformat(),
                "reference": receipt.reference,
                "evidence_present": bool(receipt.evidence),
                "confirmed_by": receipt.confirmed_by,
                "source_system": receipt.source_system,
                "source_ref": receipt.source_ref,
            }
        )

    by_stage = []
    for stage_id, stage in stage_map.items():
        stage_receipts = [row for row in receipts if row.get("stage_id") == stage_id]
        if stage_receipts:
            by_stage.append({**stage, "confirmed_totals": _money_total(stage_receipts), "receipt_count": len(stage_receipts)})
    unallocated = [row for row in receipts if not row.get("stage_id")]
    return {
        "receipts": receipts,
        "confirmed_totals": _money_total(receipts),
        "by_stage": by_stage,
        "unallocated_receipts": unallocated,
    }, False


def _corrections(rows):
    result = []
    for row in rows:
        detail = row.get("detail") if isinstance(row.get("detail"), dict) else {}
        result.append(
            {
                "id": row.get("id"),
                "number": row.get("number"),
                "status": row.get("status"),
                "original_payment_id": detail.get("original_payment_id"),
                "reversal_id": detail.get("reversal_id"),
                "reason": detail.get("reason"),
                "reversal_date": detail.get("reversal_date"),
                "reversal_evidence_present": bool(detail.get("reversal_evidence")),
            }
        )
    return result


def _cost_impacts(db, user, project_id, allowed_tools):
    contacts = []
    if "query_contact_cases" in allowed_tools:
        from .contacts import permitted

        for case in db.scalars(select(m.ContactCase).where(m.ContactCase.project_id == project_id).order_by(m.ContactCase.created_at.desc(), m.ContactCase.id).limit(100)):
            if not permitted(db, user, "read", case):
                continue
            tasks = []
            for task in db.scalars(select(m.ContactTask).where(m.ContactTask.case_id == case.id).limit(100)):
                if task.estimated_amount is None and task.actual_amount is None and task.actual_hours is None:
                    continue
                tasks.append(
                    {
                        "id": task.id,
                        "title": task.title,
                        "status": task.status,
                        "affected_type": task.affected_type,
                        "affected_ref": task.affected_ref,
                        "planned_action": task.planned_action,
                        "estimated_amount": str(task.estimated_amount) if task.estimated_amount is not None else None,
                        "currency": task.currency,
                        "actual_hours": str(task.actual_hours) if task.actual_hours is not None else None,
                        "actual_amount": str(task.actual_amount) if task.actual_amount is not None else None,
                        "actual_currency": task.actual_currency,
                        "execution_evidence_present": bool(task.execution_evidence),
                    }
                )
            if tasks:
                contacts.append({"id": case.id, "title": case.title, "status": "CLOSED" if case.closed_at else "OPEN", "tasks": tasks})
    return contacts[:50]


def _closure_finance_items(db, user, project_id):
    if not access(db, user, "project_close.read", {"project_id": project_id}).allowed:
        return []
    keys = {"INVOICE", "CUSTOMER_RECEIPT", "SUPPLIER_SETTLEMENT", "ARCHIVE_FINANCE", "RECEIVABLE_PAYABLE", "CUSTOMER_SETTLEMENT", "SUPPLIER_SETTLEMENT"}
    rows = []
    for case in db.scalars(select(m.ProjectClosureCase).where(m.ProjectClosureCase.project_id == project_id).order_by(m.ProjectClosureCase.created_at.desc(), m.ProjectClosureCase.id).limit(20)):
        for item in db.scalars(select(m.ProjectClosureItem).where(m.ProjectClosureItem.case_id == case.id).limit(100)):
            if item.item_key in keys or any(word in ((item.label or "") + (item.result or "")) for word in ("财务", "回款", "付款", "发票", "结算")):
                rows.append(
                    {
                        "case_id": case.id,
                        "case_mode": case.mode,
                        "case_status": case.status,
                        "item_key": item.item_key,
                        "label": item.label,
                        "status": item.status,
                        "result": item.result,
                        "evidence_present": bool(item.evidence),
                        "source_system": item.source_system,
                        "source_ref": item.source_ref,
                        "source_as_of": item.source_as_of.isoformat() if item.source_as_of else None,
                    }
                )
    return rows


def _analysis(project, profile, starts, sales_contracts, customer_nodes, customer_receipts, outsource_contracts, supplier_payments, corrections, cost_impacts, closure_items):
    open_reservation = bool(supplier_payments["outstanding_reservations"])
    supplier_paid = bool(supplier_payments["confirmed_totals"])
    customer_received = bool(customer_receipts["confirmed_totals"])
    condition_pending = [node for node in customer_nodes if not node.get("condition_confirmed")]
    invoice_done = any(item.get("item_key") == "INVOICE" and item.get("status") == "DONE" for item in closure_items)
    customer_receipt_done = any(item.get("item_key") == "CUSTOMER_RECEIPT" and item.get("status") == "DONE" for item in closure_items)
    supplier_settlement_done = any(item.get("item_key") == "SUPPLIER_SETTLEMENT" and item.get("status") == "DONE" for item in closure_items)
    cost_tasks = [task for case in cost_impacts for task in case.get("tasks") or []]

    warnings = []
    gaps = []
    if not starts:
        gaps.append("未见正式开工通知上下文，无法证明已向财务形成开工交接。")
    if not sales_contracts:
        gaps.append("未见可见销售合同及客户收款节点；不能判断客户应收条件。")
    if customer_nodes and not customer_received:
        warnings.append("当前只见客户合同收款节点或关闭清单，未见客户实际回款确认；不能把节点到期或清单核对当成实际回款。")
    if customer_received and customer_receipt_done:
        warnings.append("客户实际回款确认与关闭清单均存在；仍须按合同节点、发票和财务口径核对，不能用单次回款代表项目已结束。")
    if condition_pending:
        warnings.append("存在付款/收款节点条件未由财务确认，不能作为到期或付款依据。")
    if any(request.get("status") == "EFFECTIVE" for request in supplier_payments["requests"]):
        warnings.append("存在已审批供应商付款申请；审批通过不等于已付款或全部付清，仍须以财务实际付款确认汇总。")
    if open_reservation:
        warnings.append("存在未释放的供应商付款授权占用，项目关闭或结算前需核对。")
    if corrections:
        warnings.append("存在财务冲正/更正记录，汇总必须按有符号实付和冲正依据计算，不能覆盖原付款记录。")
    if cost_tasks:
        warnings.append("存在设变、异常或联络单费用/工时线索；收入、成本、利润需财务适配口径，不能用报价成本或回款金额直接替代。")
    gaps.append("发票、收入确认、含税口径、工时计价、费用分摊和占用资金公式尚未完整接入；当前只做可见事实核对。")

    return {
        "finance_handoff": {
            "effective_start_notices": starts,
            "profile_execution_mode": (profile or {}).get("execution_mode"),
            "settlement_status": (profile or {}).get("settlement_status"),
        },
        "sales_contracts": sales_contracts,
        "customer_receivable_nodes": customer_nodes,
        "customer_receipt_summary": customer_receipts,
        "full_outsource_contracts": outsource_contracts,
        "supplier_payment_summary": supplier_payments,
        "finance_corrections": corrections,
        "cost_and_change_impacts": cost_impacts,
        "closure_finance_items": closure_items,
        "gaps": gaps,
        "warnings": warnings,
        "derived_status": {
            "project_status": project.status,
            "has_effective_start_notice": bool(starts),
            "has_sales_contract_payment_nodes": bool(customer_nodes),
            "has_customer_actual_receipt_ledger": customer_received,
            "has_invoice_or_customer_receipt_closure_evidence": invoice_done or customer_receipt_done,
            "has_supplier_payment_request": bool(supplier_payments["requests"]),
            "has_confirmed_supplier_payment": supplier_paid,
            "has_open_supplier_payment_reservation": open_reservation,
            "has_finance_correction": bool(corrections),
            "has_cost_or_deduction_signal": bool(cost_tasks),
            "has_supplier_settlement_closure_evidence": supplier_settlement_done,
        },
    }


def customer_receipt_schema():
    return CustomerReceiptProposalInput.model_json_schema()


def parse_customer_receipt(arguments):
    try:
        return CustomerReceiptProposalInput.model_validate(arguments or {})
    except ValidationError as error:
        raise DomainError("INVALID_TOOL_INPUT", "客户回款确认参数不完整或不符合要求："+error.errors()[0]["msg"]) from None


def _receipt_payload(data: CustomerReceiptProposalInput):
    return CustomerReceipt(amount=data.amount, currency=data.currency, received_date=data.received_date,
        reference=data.reference, evidence=data.evidence, stage_id=data.stage_id,
        source_ref=data.source_ref, note=data.note)


def preview_customer_receipt(db, user, data: CustomerReceiptProposalInput):
    project = db.get(m.Project, data.project_id)
    if not project:
        raise DomainError("NOT_FOUND", "项目不存在", 404)
    scope = {"project_id": project.id}
    require(db, user, "project.read", scope)
    require(db, user, "sales_contract.read", scope)
    require(db, user, "customer_receipt.confirm", scope)
    if project.row_version != data.project_version:
        raise DomainError("VERSION_CONFLICT", "项目状态已变化，请重新查询后准备", 409)
    contract = db.get(m.BusinessSubject, data.contract_subject_id)
    if not contract or contract.project_id != project.id:
        raise DomainError("NOT_FOUND", "销售合同不存在或不属于该项目", 404)
    payload = _receipt_payload(data)
    detail, stage = validate_customer_receipt(db, contract, payload)
    display = {
        "操作": "登记客户实际回款确认",
        "项目": project.code+" · "+project.name,
        "项目版本": project.row_version,
        "销售合同": detail.contract_number,
        "收款节点": stage.name if stage else "未指定节点",
        "回款金额": str(data.amount)+" "+data.currency,
        "回款日期": data.received_date.isoformat(),
        "银行流水/凭证号": data.reference,
        "来源引用": data.source_ref or "未填写",
        "依据": data.evidence,
        "备注": data.note or "无",
        "说明": "本人确认后仅登记财务已确认的客户实际回款事实；不代表开票、收入确认、项目关闭或 ERP 财务对账完成。",
    }
    return payload, display


def execute_finance_tool(db, user, key, arguments, run=None):
    if key != "prepare_customer_receipt_confirmation":
        raise DomainError("TOOL_UNKNOWN", "工具未实现", 403)
    data = parse_customer_receipt(arguments)
    _, display = preview_customer_receipt(db, user, data)
    proposal = {"kind": "customer_receipt", "action": "confirm_customer_receipt",
        "requires_approval": False, "input": data.model_dump(mode="json"), "display": display,
        "confirmation_policy": proposal_confirmation_policy(run, requires_approval=False)}
    return {"data": [], "source": "agent_proposal", "as_of": now().isoformat(), "proposal": proposal,
        "limitations": ["仅准备客户实际回款登记建议；本人确认后才写入回款确认台账，不执行收款、不开票、不计算收入利润。"]}


def source(db, user, step_id):
    from .tool_gateway import available_tools
    step = db.get(m.Step, step_id)
    run = db.get(m.Run, step.run_id) if step else None
    if not run or run.user_id != user.id:
        raise DomainError("NOT_FOUND", "操作建议不存在或无权访问", 404)
    if run.status not in {"RUNNING", "SUCCEEDED"}:
        raise DomainError("PROPOSAL_STOPPED", "任务已停止，请重新准备操作", 409)
    if run.security_version != user.security_version or run.checkpoint.get("authorization_hash") != fingerprint(db, user):
        raise DomainError("AUTHORIZATION_CHANGED", "授权已变化，请重新准备操作", 403)
    proposal = step.result.get("proposal")
    if step.tool not in available_tools(db, user) or step.tool != "prepare_customer_receipt_confirmation" or not proposal:
        raise DomainError("TOOL_FORBIDDEN", "操作能力不可用", 403)
    return proposal


def validate_intent(db, user, payload):
    proposal = source(db, user, payload["step_id"])
    if content_hash(proposal) != payload["proposal_hash"]:
        raise DomainError("CONFIRMATION_INVALID", "操作建议内容已变化", 409)
    data = parse_customer_receipt(proposal["input"])
    _, display = preview_customer_receipt(db, user, data)
    if content_hash(display) != content_hash(proposal["display"]):
        raise DomainError("VERSION_CONFLICT", "项目、合同或回款资料已变化，请重新准备", 409)
    return proposal, data


def confirm(db, user, payload):
    from .domain_commands import execute_command
    _, data = validate_intent(db, user, payload)
    receipt = _receipt_payload(data)
    result = execute_command(db, user, "customer_receipt.confirm", data.contract_subject_id, receipt.model_dump(mode="json"))
    return {"project_id": data.project_id, "contract_subject_id": data.contract_subject_id,
        "customer_receipt_id": result["customer_receipt_id"], "action": "customer_receipt_confirm", "status": "CONFIRMED"}


def query(db, user, data: ProjectDossierInput, allowed_tools: set[str]):
    project, alternatives, truncated = _resolve(db, user, data)
    limitations = [
        "只读取当前用户可见项目与财务相关业务事实；合同、付款、冲正、关闭清单仍受对应数据权限约束。",
        "合同收款节点、系统提醒、付款申请审批、实际付款确认、客户实际回款、发票和项目关闭是不同事实，不能相互替代。",
        "本工具不创建同义财务台账、不确认回款或付款、不执行银行转账、不计算正式收入成本利润；ERP/财务适配事实缺失时必须明确未知。",
    ]
    if truncated:
        limitations.append("最多检查前500个可见项目，结果可能未覆盖全部可见范围。")
    if project:
        profile = _profile(db, user, project.id)
        skipped = []
        rows = {}
        for kind, label in (
            ("internal_start", "正式开工通知"),
            ("sales_contract", "销售合同/客户收款节点"),
            ("customer_receipt", "客户实际回款确认"),
            ("full_outsource_contract", "整套委外合同/供应商付款节点"),
            ("supplier_payment", "供应商付款申请和实付"),
            ("finance_correction", "财务冲正"),
            ("project_close", "项目关闭财务清单"),
        ):
            if _can_read_kind(db, user, kind, project.id):
                rows[kind] = _subject_rows(db, user, project.id, kind)
            else:
                rows[kind] = []
                skipped.append(label)
        starts = []
        for row in rows["internal_start"]:
            detail = row.get("detail") if isinstance(row.get("detail"), dict) else {}
            if detail.get("decision") == "START" and row.get("status") == "EFFECTIVE":
                starts.append(
                    {
                        "id": row.get("id"),
                        "number": row.get("number"),
                        "effective_date": detail.get("effective_date"),
                        "source_subject_id": detail.get("source_subject_id"),
                        "evidence_present": bool(detail.get("evidence")),
                    }
                )
        sales_contracts, customer_nodes = _contract_context(rows["sales_contract"], "CUSTOMER_RECEIVABLE")
        customer_receipts, receipts_skipped = _customer_receipts(db, user, project.id, sales_contracts, customer_nodes)
        outsource_contracts, _ = _contract_context(rows["full_outsource_contract"], "SUPPLIER_PAYABLE")
        supplier_payments = _supplier_payments(rows["supplier_payment"], sales_contracts, outsource_contracts)
        corrections = _corrections(rows["finance_correction"])
        cost_impacts = _cost_impacts(db, user, project.id, allowed_tools)
        closure_items = _closure_finance_items(db, user, project.id)
        if receipts_skipped:
            skipped.append("客户实际回款确认")
        if skipped:
            limitations.append("未授权或未分配对应财务事实读取范围，未返回：" + "、".join(dict.fromkeys(skipped)))
        if "query_contact_cases" not in allowed_tools:
            limitations.append("未分配工程联络查询工具，未汇总设变费用、扣款或额外工时线索。")
        if "prepare_customer_receipt_confirmation" in allowed_tools:
            limitations.append("可在取得真实销售合同和收款节点后准备客户实际回款确认；该操作仍需本人核对卡片后才写入。")
        return {
            "resolution": "RESOLVED",
            "data": [
                {
                    "project": _project_card(db, user, project, alternatives or ("项目定位",)),
                    "profile": profile,
                    "analysis": _analysis(project, profile, starts, sales_contracts, customer_nodes, customer_receipts, outsource_contracts, supplier_payments, corrections, cost_impacts, closure_items),
                }
            ],
            "source": "agent_db",
            "as_of": now().isoformat(),
            "limitations": limitations,
        }
    if alternatives is None:
        return {"resolution": "NOT_FOUND_OR_FORBIDDEN", "data": [], "source": "agent_db", "as_of": now().isoformat(), "limitations": limitations}
    if alternatives:
        return {
            "resolution": "MULTIPLE_CANDIDATES",
            "data": alternatives,
            "source": "agent_db",
            "as_of": now().isoformat(),
            "limitations": limitations + ["线索命中多个候选项目，请使用项目 ID 或更完整编号后再查询。"],
        }
    return {"resolution": "NOT_FOUND", "data": [], "source": "agent_db", "as_of": now().isoformat(), "limitations": limitations}


router = APIRouter()


@router.get("/api/finance-proposals/{step_id}")
def proposal_status(step_id: str, user=Depends(current_user), db=Depends(get_db)):
    source(db, user, step_id)
    intent = db.scalar(select(m.HumanIntent).where(m.HumanIntent.user_id == user.id,
        m.HumanIntent.action == "finance.execute", m.HumanIntent.resource_id == step_id,
        m.HumanIntent.receipt["status"].as_string() == "CONFIRMED").order_by(m.HumanIntent.created_at.desc()))
    return {"receipt": intent.receipt if intent else None}


@router.post("/api/finance-proposals/{step_id}/intent")
def intent(step_id: str, user=Depends(current_user), db=Depends(get_db)):
    from .business import create_intent
    proposal = source(db, user, step_id)
    payload = {"step_id": step_id, "proposal_hash": content_hash(proposal)}
    result = create_intent(db, user, "finance.execute", step_id, payload)
    result["display"] = proposal["display"]
    result["confirmation_policy"] = proposal.get("confirmation_policy")
    db.commit()
    return result
