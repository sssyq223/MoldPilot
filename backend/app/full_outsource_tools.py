from collections import Counter, defaultdict
from decimal import Decimal

from sqlalchemy import select

from . import models as m
from .authorization import access, predicate, select_fields
from .db import now
from .errors import DomainError
from .plan_tools import ProjectPlanContextInput, _strength


OUTSOURCE_KEYWORDS = ("委外", "供应商", "外协", "外包", "outsource", "supplier")
ISSUE_KEYWORDS = ("质量", "延期", "整改", "复验", "扣款", "索赔", "验收", "交付", "合同", "结算")


def _project_card(db, user, project, matched_by=()):
    fields = access(db, user, "project.read", {"project_id": project.id}).fields
    card = select_fields(
        {"id": project.id, "code": project.code, "name": project.name, "status": project.status, "row_version": project.row_version},
        fields,
    )
    card["matched_by"] = sorted(set(matched_by))
    return card


def _visible_projects(db, user):
    rows = list(
        db.scalars(
            select(m.Project)
            .where(predicate(db, user, "project.read", {"project_id": m.Project.id}))
            .order_by(m.Project.code)
            .limit(501)
        )
    )
    return rows[:500], len(rows) > 500


def _can_read_kind(kind, allowed_tools):
    context_tools = {
        "quote_acceptance": {"query_quote_evaluation_context", "query_quote_acceptance_context", "query_full_outsource_context"},
        "full_outsource_contract": {"query_contract_context", "query_full_outsource_context"},
        "project_plan": {"query_project_plan_context", "query_full_outsource_context"},
        "plan_change": {"query_project_plan_context", "query_full_outsource_context"},
        "engineering_change": {"query_engineering_change", "query_full_outsource_context"},
        "supplier_payment": {"query_supplier_payment", "query_full_outsource_context"},
        "project_close": {"query_project_closure_context", "query_full_outsource_context"},
    }
    return "query_" + kind in allowed_tools or bool(context_tools.get(kind, set()) & allowed_tools)


def _subject_rows(db, user, project_id, kind, allowed_tools, limit=50):
    if not _can_read_kind(kind, allowed_tools):
        return []
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


def _resolve(db, user, data: ProjectPlanContextInput, allowed_tools: set[str]):
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
    if by_id and _can_read_kind("full_outsource_contract", allowed_tools):
        for subject in db.scalars(
            select(m.BusinessSubject)
            .where(m.BusinessSubject.project_id.in_(list(by_id)), m.BusinessSubject.kind == "full_outsource_contract")
            .limit(501)
        ):
            try:
                row = _subject_rows(db, user, subject.project_id, "full_outsource_contract", allowed_tools, limit=100)
            except DomainError:
                row = []
            for record in row:
                if record.get("id") != subject.id:
                    continue
                detail = record.get("detail") if isinstance(record.get("detail"), dict) else {}
                add(subject.project_id, record.get("number"), "整套委外业务单号")
                add(subject.project_id, detail.get("contract_number"), "整套委外合同号")
    if ("query_purchase_orders" in allowed_tools or "query_orders" in allowed_tools) and by_id:
        for order in db.scalars(select(m.PurchaseOrder).where(m.PurchaseOrder.project_id.in_(list(by_id))).limit(501)):
            add(order.project_id, order.number, "采购/委外订单号")
    if "query_contact_cases" in allowed_tools and by_id:
        for case in db.scalars(select(m.ContactCase).where(m.ContactCase.project_id.in_(list(by_id))).limit(501)):
            add(case.project_id, case.title, "工程联络标题")
            add(case.project_id, case.customer_ref, "客户引用")
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
    fields = access(db, user, "project.read", {"project_id": project_id}).fields
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


def _latest_outsource_acceptance(rows):
    effective = [row for row in rows if row.get("status") == "EFFECTIVE"]
    for row in effective:
        detail = row.get("detail") if isinstance(row.get("detail"), dict) else {}
        if detail.get("decision") == "ACCEPT" and detail.get("execution_mode") == "FULL_OUTSOURCE":
            return {
                "id": row.get("id"),
                "number": row.get("number"),
                "effective_date": detail.get("effective_date"),
                "amount": detail.get("amount"),
                "currency": detail.get("currency"),
                "evidence": detail.get("evidence"),
            }
    return None


def _contract_summary(rows):
    summaries = []
    for row in rows:
        detail = row.get("detail") if isinstance(row.get("detail"), dict) else {}
        summaries.append(
            {
                "id": row.get("id"),
                "number": row.get("number"),
                "status": row.get("status"),
                "contract_number": detail.get("contract_number"),
                "supplier_id": detail.get("supplier_id"),
                "amount": detail.get("amount"),
                "currency": detail.get("currency"),
                "expected_date": detail.get("expected_date"),
                "stage_count": len(detail.get("stages") or []),
                "signed_evidence_visible": bool(detail.get("contract_number") and row.get("status") == "EFFECTIVE"),
            }
        )
    return summaries


def _payment_summary(rows):
    result = []
    paid_total = Counter()
    requested_total = Counter()
    for row in rows:
        detail = row.get("detail") if isinstance(row.get("detail"), dict) else {}
        currency = detail.get("currency")
        if currency and detail.get("amount"):
            requested_total[currency] += Decimal(str(detail.get("amount")))
        payments = detail.get("payments") or []
        for payment in payments:
            payment_currency = payment.get("currency") or currency
            if payment_currency and payment.get("amount"):
                paid_total[payment_currency] += Decimal(str(payment.get("amount")))
        result.append(
            {
                "id": row.get("id"),
                "number": row.get("number"),
                "status": row.get("status"),
                "stage_id": detail.get("stage_id"),
                "amount": detail.get("amount"),
                "currency": currency,
                "reservation": detail.get("reservation"),
                "payment_count": len(payments),
            }
        )
    return {
        "requests": result,
        "totals": [
            {"currency": currency, "requested_amount": str(requested_total[currency]), "paid_amount": str(paid_total[currency])}
            for currency in sorted(set(requested_total) | set(paid_total))
        ],
    }


def _contract_signing_records(db, user, contract_rows, allowed_tools):
    if not contract_rows or not _can_read_kind("full_outsource_contract", allowed_tools):
        return []
    contract_ids = [row.get("id") for row in contract_rows if row.get("id")]
    if not contract_ids:
        return []
    allowed_contracts = {
        row.get("id")
        for row in contract_rows
        if access(db, user, "full_outsource_contract.read", {"project_id": row.get("project_id"), "category": row.get("category")}).allowed
    }
    if not allowed_contracts:
        return []
    rows = []
    q = (
        select(m.ContractSigningRecord)
        .where(m.ContractSigningRecord.contract_subject_id.in_(list(allowed_contracts)))
        .order_by(m.ContractSigningRecord.created_at.desc(), m.ContractSigningRecord.id)
        .limit(100)
    )
    for record in db.scalars(q):
        rows.append(
            {
                "id": record.id,
                "contract_subject_id": record.contract_subject_id,
                "template_name": record.template_name,
                "signing_method": record.signing_method,
                "status": record.status,
                "signed_date": record.signed_date.isoformat() if record.signed_date else None,
                "signed_file_id": record.signed_file_id,
                "signed_file_title": record.signed_file_title,
                "supplier_signer": record.supplier_signer,
                "buyer_reviewer_id": record.buyer_reviewer_id,
                "approved_by": record.approved_by,
                "evidence": record.evidence,
                "source_system": record.source_system,
                "source_ref": record.source_ref,
                "recorded_by": record.recorded_by,
            }
        )
    return rows


def _plan_tasks(rows):
    tasks = []
    active = next((row for row in rows if row.get("status") == "EFFECTIVE"), None)
    for row in rows:
        detail = row.get("detail") if isinstance(row.get("detail"), dict) else {}
        for task in detail.get("tasks") or []:
            text = " ".join(str(task.get(key) or "") for key in ("key", "name"))
            if any(keyword in text for keyword in OUTSOURCE_KEYWORDS + ISSUE_KEYWORDS):
                tasks.append(
                    {
                        "plan_id": row.get("id"),
                        "plan_number": row.get("number"),
                        "key": task.get("key"),
                        "name": task.get("name"),
                        "status": task.get("status"),
                        "planned_start": task.get("planned_start"),
                        "planned_end": task.get("planned_end"),
                        "actual_start": task.get("actual_start"),
                        "actual_end": task.get("actual_end"),
                        "prerequisites": task.get("prerequisites") or [],
                    }
                )
    return active, tasks[:50]


def _order_tracking(db, user, project_id, allowed_tools):
    if "query_purchase_orders" not in allowed_tools and "query_orders" not in allowed_tools:
        return {"orders": [], "totals": {}, "lines": []}
    from .delivery_logistics_tools import _augment_order_receipts, _order_headers, _order_rows, _shipment_tracking

    orders = _order_rows(db, user, project_id, allowed_tools)
    _augment_order_receipts(db, user, orders)
    tracking = _shipment_tracking(orders)
    return {"orders": _order_headers(orders), **tracking}


def _supplier_progress_reports(db, user, project_id, allowed_tools):
    if not _can_read_kind("full_outsource_contract", allowed_tools):
        return []
    if not access(db, user, "full_outsource_contract.read", {"project_id": project_id, "category": "outsource"}).allowed:
        return []
    rows = []
    q = (
        select(m.SupplierProgressReport, m.Supplier)
        .join(m.Supplier, m.SupplierProgressReport.supplier_id == m.Supplier.id)
        .where(m.SupplierProgressReport.project_id == project_id)
        .order_by(m.SupplierProgressReport.report_date.desc(), m.SupplierProgressReport.created_at.desc(), m.SupplierProgressReport.id)
        .limit(100)
    )
    today = now().date()
    for report, supplier in db.execute(q):
        overdue_followup = report.status in {"AT_RISK", "BLOCKED", "REWORK"} and report.next_due_date is not None and report.next_due_date < today
        rows.append(
            {
                "id": report.id,
                "supplier_id": supplier.id,
                "supplier_name": supplier.name,
                "contract_subject_id": report.contract_subject_id,
                "plan_task_id": report.plan_task_id,
                "stage_key": report.stage_key,
                "stage_name": report.stage_name,
                "report_date": report.report_date.isoformat(),
                "status": report.status,
                "progress_percent": report.progress_percent,
                "next_due_date": report.next_due_date.isoformat() if report.next_due_date else None,
                "overdue_followup": overdue_followup,
                "issue_summary": report.issue_summary,
                "evidence": report.evidence,
                "source_system": report.source_system,
                "source_ref": report.source_ref,
                "reported_by": report.reported_by,
                "followed_by": report.followed_by,
            }
        )
    return rows


def _material_handoffs(db, user, project_id, allowed_tools):
    if not _can_read_kind("full_outsource_contract", allowed_tools):
        return []
    if not access(db, user, "full_outsource_contract.read", {"project_id": project_id, "category": "outsource"}).allowed:
        return []
    rows = []
    q = (
        select(m.SupplierMaterialHandoff, m.Supplier)
        .join(m.Supplier, m.SupplierMaterialHandoff.supplier_id == m.Supplier.id)
        .where(m.SupplierMaterialHandoff.project_id == project_id)
        .order_by(m.SupplierMaterialHandoff.provided_date.desc(), m.SupplierMaterialHandoff.created_at.desc(), m.SupplierMaterialHandoff.id)
        .limit(100)
    )
    for handoff, supplier in db.execute(q):
        rows.append(
            {
                "id": handoff.id,
                "supplier_id": supplier.id,
                "supplier_name": supplier.name,
                "contract_subject_id": handoff.contract_subject_id,
                "file_id": handoff.file_id,
                "document_title": handoff.document_title,
                "document_type": handoff.document_type,
                "approval_status": handoff.approval_status,
                "provided_date": handoff.provided_date.isoformat(),
                "provided_to": handoff.provided_to,
                "handoff_channel": handoff.handoff_channel,
                "evidence": handoff.evidence,
                "source_system": handoff.source_system,
                "source_ref": handoff.source_ref,
                "provided_by": handoff.provided_by,
                "verified_by": handoff.verified_by,
            }
        )
    return rows


def _deduction_settlements(db, user, project_id, allowed_tools):
    if not _can_read_kind("full_outsource_contract", allowed_tools):
        return []
    if not access(db, user, "full_outsource_contract.read", {"project_id": project_id, "category": "outsource"}).allowed:
        return []
    rows = []
    q = (
        select(m.SupplierDeductionSettlement, m.Supplier)
        .join(m.Supplier, m.SupplierDeductionSettlement.supplier_id == m.Supplier.id)
        .where(m.SupplierDeductionSettlement.project_id == project_id)
        .order_by(m.SupplierDeductionSettlement.created_at.desc(), m.SupplierDeductionSettlement.id)
        .limit(100)
    )
    for settlement, supplier in db.execute(q):
        rows.append(
            {
                "id": settlement.id,
                "supplier_id": supplier.id,
                "supplier_name": supplier.name,
                "contract_subject_id": settlement.contract_subject_id,
                "contact_case_id": settlement.contact_case_id,
                "contact_task_id": settlement.contact_task_id,
                "reason": settlement.reason,
                "responsibility": settlement.responsibility,
                "deduction_amount": str(settlement.deduction_amount),
                "currency": settlement.currency,
                "status": settlement.status,
                "settlement_reference": settlement.settlement_reference,
                "responsibility_evidence": settlement.responsibility_evidence,
                "settlement_evidence": settlement.settlement_evidence,
                "confirmed_by": settlement.confirmed_by,
                "settled_by": settlement.settled_by,
                "settled_at": settlement.settled_at.isoformat() if settlement.settled_at else None,
                "source_system": settlement.source_system,
                "source_ref": settlement.source_ref,
            }
        )
    return rows


def _engineering_changes(rows):
    result = []
    for row in rows:
        detail = row.get("detail") if isinstance(row.get("detail"), dict) else {}
        text = " ".join(str(detail.get(key) or "") for key in ("problem", "solution"))
        impacts = detail.get("impacts") or []
        if any(keyword in text for keyword in OUTSOURCE_KEYWORDS + ISSUE_KEYWORDS) or any(
            impact.get("action") in {"REWORK", "PAUSE", "CANCEL"} for impact in impacts
        ):
            result.append(
                {
                    "id": row.get("id"),
                    "number": row.get("number"),
                    "status": row.get("status"),
                    "problem": detail.get("problem"),
                    "solution": detail.get("solution"),
                    "customer_due_affected": detail.get("customer_due_affected"),
                    "customer_evidence_present": bool(detail.get("customer_evidence")),
                    "impact_count": len(impacts),
                    "unimplemented_impacts": [
                        {
                            "task_id": impact.get("task_id"),
                            "action": impact.get("action"),
                            "implemented": bool(impact.get("implemented_by") and impact.get("implementation_evidence")),
                            "rechecked": impact.get("recheck_passed"),
                        }
                        for impact in impacts
                        if not impact.get("implemented_by") or impact.get("recheck_passed") is not True
                    ],
                }
            )
    return result[:50]


def _contact_issues(db, user, project_id, allowed_tools):
    if "query_contact_cases" not in allowed_tools:
        return []
    from .contacts import permitted

    issues = []
    q = (
        select(m.ContactCase)
        .where(m.ContactCase.project_id == project_id, predicate(db, user, "contact.read", {"project_id": m.ContactCase.project_id, "category": m.ContactCase.category}))
        .order_by(m.ContactCase.created_at.desc(), m.ContactCase.id)
        .limit(100)
    )
    for case in db.scalars(q):
        if not permitted(db, user, "read", case):
            continue
        tasks = []
        for task in db.scalars(select(m.ContactTask).where(m.ContactTask.case_id == case.id, m.ContactTask.status != "CANCELLED").limit(100)):
            task_text = " ".join(str(getattr(task, key) or "") for key in ("title", "affected_type", "affected_ref", "impact_description", "planned_action"))
            if any(keyword in task_text for keyword in OUTSOURCE_KEYWORDS + ISSUE_KEYWORDS):
                tasks.append(
                    {
                        "id": task.id,
                        "title": task.title,
                        "status": task.status,
                        "affected_type": task.affected_type,
                        "affected_ref": task.affected_ref,
                        "planned_action": task.planned_action,
                        "delivery_impact_days": task.delivery_impact_days,
                        "estimated_amount": str(task.estimated_amount) if task.estimated_amount is not None else None,
                        "currency": task.currency,
                        "actual_completed_at": task.actual_completed_at.isoformat() if task.actual_completed_at else None,
                        "actual_amount": str(task.actual_amount) if task.actual_amount is not None else None,
                        "actual_currency": task.actual_currency,
                        "execution_evidence_present": bool(task.execution_evidence),
                    }
                )
        case_text = " ".join(str(getattr(case, key) or "") for key in ("title", "problem_source", "current_stage", "change_type"))
        if tasks or any(keyword in case_text for keyword in OUTSOURCE_KEYWORDS + ISSUE_KEYWORDS) or case.problem_source == "OUTSOURCE_DEFECT":
            issues.append(
                {
                    "id": case.id,
                    "title": case.title,
                    "collaboration_status": "CLOSED" if case.closed_at else "HISTORY_RECORD" if case.mode == "HISTORY" else "OPEN",
                    "problem_source": case.problem_source,
                    "current_stage": case.current_stage,
                    "change_type": case.change_type,
                    "urgency": case.urgency,
                    "tasks": tasks[:30],
                }
            )
    return issues[:30]


def _closure_items(db, user, project_id, allowed_tools):
    if "query_project_closure_context" not in allowed_tools and "query_full_outsource_context" not in allowed_tools:
        return []
    if not access(db, user, "project_close.read", {"project_id": project_id}).allowed:
        return []
    rows = []
    for case in db.scalars(
        select(m.ProjectClosureCase)
        .where(m.ProjectClosureCase.project_id == project_id)
        .order_by(m.ProjectClosureCase.created_at.desc(), m.ProjectClosureCase.id)
        .limit(20)
    ):
        for item in db.scalars(select(m.ProjectClosureItem).where(m.ProjectClosureItem.case_id == case.id).limit(100)):
            text = (item.item_key or "") + (item.label or "") + (item.result or "")
            if any(keyword in text for keyword in OUTSOURCE_KEYWORDS + ISSUE_KEYWORDS + ("回款", "付款", "关闭")):
                rows.append(
                    {
                        "case_id": case.id,
                        "mode": case.mode,
                        "case_status": case.status,
                        "item_key": item.item_key,
                        "label": item.label,
                        "status": item.status,
                        "result": item.result,
                        "evidence_present": bool(item.evidence),
                        "source_system": item.source_system,
                    }
                )
    return rows[:100]


def _analysis(project, profile, quote_acceptance, contracts, signing_records, active_plan, plan_tasks, supplier_progress_reports, material_handoffs, deduction_settlements, order_tracking, engineering_changes, contacts, payments, closure_items):
    contract_effective = [row for row in contracts if row.get("status") == "EFFECTIVE"]
    signed_contracts = [row for row in signing_records if row.get("status") == "SIGNED"]
    non_signed_contracts = [row for row in signing_records if row.get("status") != "SIGNED"]
    totals = order_tracking["totals"]
    open_contacts = [row for row in contacts if row.get("collaboration_status") != "CLOSED"]
    open_change_impacts = [impact for row in engineering_changes for impact in row.get("unimplemented_impacts") or []]
    risky_reports = [row for row in supplier_progress_reports if row.get("status") in {"AT_RISK", "BLOCKED", "REWORK"}]
    overdue_reports = [row for row in supplier_progress_reports if row.get("overdue_followup")]
    approved_handoffs = [row for row in material_handoffs if row.get("approval_status") == "APPROVED"]
    draft_or_revoked_handoffs = [row for row in material_handoffs if row.get("approval_status") != "APPROVED"]
    confirmed_deductions = [row for row in deduction_settlements if row.get("responsibility") != "UNKNOWN" and row.get("status") in {"RESPONSIBILITY_CONFIRMED", "SETTLED"}]
    settled_deductions = [row for row in deduction_settlements if row.get("status") == "SETTLED"]
    pending_deductions = [row for row in deduction_settlements if row.get("status") == "PROPOSED" or row.get("responsibility") == "UNKNOWN"]
    deduction_tasks = [
        task
        for issue in contacts
        for task in issue.get("tasks") or []
        if task.get("estimated_amount") or task.get("actual_amount") or any(keyword in (task.get("title") or task.get("impact_description") or "") for keyword in ("扣款", "索赔"))
    ]
    acceptance_done = [item for item in closure_items if "ACCEPTANCE" in item.get("item_key", "") and item.get("status") == "DONE"]

    gaps = []
    warnings = []
    mode = (profile or {}).get("execution_mode")
    if mode != "FULL_OUTSOURCE" and not quote_acceptance:
        gaps.append("未见项目档案或有效承接记录明确当前为整套委外。")
    if mode == "FULL_OUTSOURCE" and not quote_acceptance:
        warnings.append("项目档案为整套委外，但未见有效承接/后续审批中明确整套委外路径的证据。")
    if quote_acceptance and mode and mode != "FULL_OUTSOURCE":
        warnings.append("有效承接为整套委外，但项目档案加工方式不是整套委外，需核对是否已有后续变更依据。")
    if not contract_effective:
        gaps.append("未见已生效整套委外合同；不能把合同草稿或报价委外金额当成合同已签署。")
    if contract_effective and not signed_contracts:
        gaps.append("未见整套委外合同的人工签署文件或签署依据；不能把模板草稿、合同号或审批上下文等同于已签署合同。")
    if non_signed_contracts:
        warnings.append("存在非已签署状态的合同签署记录，不能作为正式合同签署依据。")
    if contract_effective and not approved_handoffs:
        gaps.append("未见按合同或业务需要向供应商提供获准客户资料/设计资料的交接依据。")
    if draft_or_revoked_handoffs:
        warnings.append("存在草稿或已撤回的供应商资料交接记录，不能作为正式获准交接依据。")
    if not active_plan:
        warnings.append("当前可见范围未见有效项目计划，无法核对供应商节点上报与项目同步节奏。")
    if not plan_tasks:
        gaps.append("未见供应商设计、采购、生产、质检、装配、试模、验收或交付等委外协同计划节点。")
    if not supplier_progress_reports:
        gaps.append("未见结构化供应商节点上报/导入记录；无法核对供应商设计、采购、生产、质检、装配、试模、验收等阶段的最近进度与证据。")
    if risky_reports:
        warnings.append("存在供应商节点风险、阻塞或返工上报，需采购跟进并同步项目。")
    if overdue_reports:
        warnings.append("存在供应商风险/阻塞节点已超过下次跟进日期，需更新整改或复验进度。")
    if not totals.get("supplier_shipments") and not totals.get("goods_receipts"):
        gaps.append("未见供应商发货、仓库收货或交付节点执行事实；不能据此认定委外交付完成。")
    if totals.get("supplier_shipments") and not totals.get("goods_receipts"):
        gaps.append("已有供应商发货记录，但未见我方收货/签收依据。")
    if totals.get("rejected_receipt_lines"):
        warnings.append("存在收货检验不合格数量，需核对整改、退换货、复验和扣款责任依据。")
    if open_contacts:
        warnings.append("存在未关闭委外质量、延期、验收或扣款相关工程联络事项，不能认定异常已闭环。")
    if open_change_impacts:
        warnings.append("存在设变或整改影响项未见执行与复验全部完成，不能把方案批准等同于整改完成。")
    if deduction_tasks and not contract_effective:
        warnings.append("存在扣款/费用影响线索，但未见已生效委外合同，不能确认责任与结算依据。")
    if deduction_tasks and not confirmed_deductions:
        warnings.append("存在扣款/费用影响线索，但未见责任已确认的供应商扣款结算依据；不能仅凭延期或质量问题自动认定供应商扣款。")
    if confirmed_deductions and not settled_deductions:
        warnings.append("存在责任已确认的供应商扣款，但未见已结算记录；需同步供应商结算或财务依据。")
    if pending_deductions:
        warnings.append("存在待确认责任或拟议状态的供应商扣款记录，不能作为正式结算结果。")
    if not acceptance_done:
        gaps.append("未见委外项目客户验收完成、回款或关闭清单中的正式依据。")
    gaps.append("当前未接入供应商门户、供应商在线签署、供应商节点填报频率和证据模板；只能读取已授权本地/ERP适配事实。")

    return {
        "latest_full_outsource_acceptance": quote_acceptance,
        "contract_signing_records": signing_records,
        "active_plan": (
            {"id": active_plan.get("id"), "number": active_plan.get("number"), "status": active_plan.get("status"), "created_at": active_plan.get("created_at")}
            if active_plan
            else None
        ),
        "outsource_plan_tasks": plan_tasks,
        "supplier_progress_reports": supplier_progress_reports,
        "supplier_material_handoffs": material_handoffs,
        "supplier_deduction_settlements": deduction_settlements,
        "supplier_execution_tracking": order_tracking,
        "engineering_changes": engineering_changes,
        "outsource_quality_delay_contacts": contacts,
        "supplier_payment_summary": payments,
        "closure_and_settlement_items": closure_items,
        "gaps": gaps,
        "warnings": warnings,
        "derived_status": {
            "project_status": project.status,
            "profile_execution_mode": mode,
            "has_full_outsource_mode": mode == "FULL_OUTSOURCE" or bool(quote_acceptance),
            "has_effective_full_outsource_contract": bool(contract_effective),
            "has_signed_full_outsource_contract_file": bool(signed_contracts),
            "has_unsigned_contract_signing_record": bool(non_signed_contracts),
            "has_outsource_plan_node": bool(plan_tasks),
            "has_supplier_progress_report": bool(supplier_progress_reports),
            "has_supplier_progress_risk": bool(risky_reports),
            "has_overdue_supplier_progress_followup": bool(overdue_reports),
            "has_approved_supplier_material_handoff": bool(approved_handoffs),
            "has_draft_or_revoked_supplier_material_handoff": bool(draft_or_revoked_handoffs),
            "has_confirmed_supplier_deduction": bool(confirmed_deductions),
            "has_settled_supplier_deduction": bool(settled_deductions),
            "has_pending_supplier_deduction": bool(pending_deductions),
            "has_supplier_shipment_or_receipt": bool(totals.get("supplier_shipments") or totals.get("goods_receipts")),
            "has_rejected_receipt": bool(totals.get("rejected_receipt_lines")),
            "has_open_outsource_issue": bool(open_contacts or open_change_impacts),
            "has_deduction_or_cost_impact_signal": bool(deduction_tasks),
            "has_supplier_payment_request": bool(payments["requests"]),
            "has_customer_acceptance_or_close_evidence": bool(acceptance_done),
        },
    }


def query(db, user, data: ProjectPlanContextInput, allowed_tools: set[str]):
    project, alternatives, truncated = _resolve(db, user, data, allowed_tools)
    limitations = [
        "只读取当前用户可见项目；承接、合同、计划、订单、工程联络、付款和结项材料分别受对应工具与权限约束。",
        "本工具只核对整套委外协同上下文，不创建供应商门户、不生成或签署合同、不下达委外、不登记扣款、不确认付款或客户验收。",
        "报价委外金额、整套委外合同、供应商节点上报、我方收货、客户验收、扣款和结算是不同事实，不能相互替代。",
    ]
    if truncated:
        limitations.append("最多检查前500个可见项目，结果可能未覆盖全部可见范围。")
    if project:
        profile = _profile(db, user, project.id)
        quote_rows = _subject_rows(db, user, project.id, "quote_acceptance", allowed_tools)
        contract_rows = _subject_rows(db, user, project.id, "full_outsource_contract", allowed_tools)
        signing_records = _contract_signing_records(db, user, contract_rows, allowed_tools)
        plan_rows = _subject_rows(db, user, project.id, "project_plan", allowed_tools) + _subject_rows(db, user, project.id, "plan_change", allowed_tools)
        active_plan, plan_tasks = _plan_tasks(plan_rows)
        supplier_progress_reports = _supplier_progress_reports(db, user, project.id, allowed_tools)
        material_handoffs = _material_handoffs(db, user, project.id, allowed_tools)
        deduction_settlements = _deduction_settlements(db, user, project.id, allowed_tools)
        engineering_changes = _engineering_changes(_subject_rows(db, user, project.id, "engineering_change", allowed_tools))
        contacts = _contact_issues(db, user, project.id, allowed_tools)
        payments = _payment_summary(_subject_rows(db, user, project.id, "supplier_payment", allowed_tools))
        closure_items = _closure_items(db, user, project.id, allowed_tools)
        order_tracking = _order_tracking(db, user, project.id, allowed_tools)
        skipped = []
        for kind, label in (
            ("quote_acceptance", "报价承接/加工方式"),
            ("full_outsource_contract", "整套委外合同"),
            ("project_plan", "项目计划/委外节点"),
            ("engineering_change", "设变/整改业务记录"),
            ("supplier_payment", "供应商付款/结算"),
        ):
            if not _can_read_kind(kind, allowed_tools):
                skipped.append(label)
        if "query_purchase_orders" not in allowed_tools and "query_orders" not in allowed_tools:
            skipped.append("正式订单/供应商发货/收货")
        if "query_contact_cases" not in allowed_tools:
            skipped.append("工程联络质量延期扣款事项")
        if "query_project_closure_context" not in allowed_tools:
            skipped.append("项目关闭/客户验收清单")
        if skipped:
            limitations.append("未分配对应查询工具或权限，未返回：" + "、".join(skipped))
        return {
            "resolution": "RESOLVED",
            "data": [
                {
                    "project": _project_card(db, user, project, alternatives or ("项目定位",)),
                    "profile": profile,
                    "full_outsource_contracts": _contract_summary(contract_rows),
                    "analysis": _analysis(
                        project,
                        profile,
                        _latest_outsource_acceptance(quote_rows),
                        contract_rows,
                        signing_records,
                        active_plan,
                        plan_tasks,
                        supplier_progress_reports,
                        material_handoffs,
                        deduction_settlements,
                        order_tracking,
                        engineering_changes,
                        contacts,
                        payments,
                        closure_items,
                    ),
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
