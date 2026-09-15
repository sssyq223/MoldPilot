from collections import Counter, defaultdict

from sqlalchemy import func, select

from . import models as m
from .authorization import access, predicate, select_fields
from .db import now
from .errors import DomainError
from .plan_tools import ProjectPlanContextInput, _strength


CHANGE_KEYWORDS = ("设变", "变更", "改模", "修模", "改版", "change", "revision")
CUSTOMER_KEYWORDS = ("客户", "customer", "邮件", "确认", "合同", "报价", "订单")
OUTSOURCE_KEYWORDS = ("委外", "外协", "供应商", "采购", "outsource", "supplier")
INTERNAL_KEYWORDS = ("内部", "设计", "加工", "制造", "装配", "试模", "质检", "返工")


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
        "quote_acceptance": {"query_quote_acceptance_context", "query_quote_evaluation_context", "query_change_intake_context"},
        "sales_contract": {"query_contract_context", "query_change_intake_context"},
        "full_outsource_contract": {"query_contract_context", "query_full_outsource_context", "query_change_intake_context"},
        "internal_start": {"query_internal_start_readiness", "query_change_intake_context"},
        "project_plan": {"query_project_plan_context", "query_change_intake_context"},
        "plan_change": {"query_project_plan_context", "query_change_intake_context"},
        "engineering_change": {"query_engineering_change", "query_change_intake_context"},
        "contact_resolution": {"query_contact_resolution", "query_change_intake_context"},
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
    if by_id:
        for link, mold in db.execute(select(m.ProjectMold, m.Mold).join(m.Mold, m.Mold.id == m.ProjectMold.mold_id).where(m.ProjectMold.project_id.in_(list(by_id))).limit(501)):
            add(link.project_id, mold.internal_number, "内部模具号")
            add(link.project_id, mold.name, "模具名称")
        for case in db.scalars(select(m.ContactCase).where(m.ContactCase.project_id.in_(list(by_id))).limit(501)):
            add(case.project_id, case.title, "工程联络标题")
            add(case.project_id, case.customer_ref, "客户引用")
            add(case.project_id, case.mold_number, "联络模具号")
            add(case.project_id, case.product_ref, "产品/料品号")
        for kind, label in (("engineering_change", "工程变更单"), ("sales_contract", "销售合同"), ("full_outsource_contract", "整套委外合同")):
            if not _can_read_kind(kind, allowed_tools):
                continue
            for subject in db.scalars(select(m.BusinessSubject).where(m.BusinessSubject.project_id.in_(list(by_id)), m.BusinessSubject.kind == kind).limit(501)):
                add(subject.project_id, subject.number, label)
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
    customer = db.get(m.Customer, profile.customer_id) if profile.customer_id else None
    fields = access(db, user, "project.read", {"project_id": project_id}).fields
    return select_fields(
        {
            "customer_id": profile.customer_id,
            "customer_name": customer.name if customer else None,
            "owner_user_id": profile.owner_user_id,
            "execution_mode": profile.execution_mode,
            "customer_due_date": profile.customer_due_date.isoformat() if profile.customer_due_date else None,
            "settlement_status": profile.settlement_status,
        },
        fields | {"customer_id", "customer_name", "owner_user_id", "execution_mode", "customer_due_date", "settlement_status"},
    )


def _molds(db, user, project_id):
    fields = access(db, user, "project.read", {"project_id": project_id}).fields
    rows = []
    for link, mold in db.execute(
        select(m.ProjectMold, m.Mold)
        .join(m.Mold, m.Mold.id == m.ProjectMold.mold_id)
        .where(m.ProjectMold.project_id == project_id)
        .order_by(m.Mold.internal_number)
        .limit(100)
    ):
        rows.append(
            select_fields(
                {"id": mold.id, "internal_number": mold.internal_number, "name": mold.name, "status": mold.status, "project_mold_id": link.id},
                fields | {"id", "internal_number", "name", "status", "project_mold_id"},
            )
        )
    return rows


def _decision_rows(rows, decision=None, mode=None):
    result = []
    for row in rows:
        detail = row.get("detail") if isinstance(row.get("detail"), dict) else {}
        if decision and detail.get("decision") != decision:
            continue
        if mode and detail.get("execution_mode") != mode:
            continue
        result.append(
            {
                "id": row.get("id"),
                "number": row.get("number"),
                "status": row.get("status"),
                "decision": detail.get("decision"),
                "execution_mode": detail.get("execution_mode"),
                "effective_date": detail.get("effective_date"),
                "amount": detail.get("amount"),
                "currency": detail.get("currency"),
                "evidence_present": bool(detail.get("evidence")),
                "source_subject_id": detail.get("source_subject_id"),
            }
        )
    return result


def _contracts(rows):
    result = []
    for row in rows:
        detail = row.get("detail") if isinstance(row.get("detail"), dict) else {}
        result.append(
            {
                "id": row.get("id"),
                "number": row.get("number"),
                "status": row.get("status"),
                "contract_number": detail.get("contract_number"),
                "amount": detail.get("amount"),
                "currency": detail.get("currency"),
                "expected_date": detail.get("expected_date"),
                "replaces_id": detail.get("replaces_id"),
                "stage_count": len(detail.get("stages") or []),
            }
        )
    return result


def _plan_tasks(rows):
    result = []
    for row in rows:
        detail = row.get("detail") if isinstance(row.get("detail"), dict) else {}
        for task in detail.get("tasks") or []:
            result.append(
                {
                    "plan_id": row.get("id"),
                    "plan_number": row.get("number"),
                    "plan_status": row.get("status"),
                    "task_id": task.get("id"),
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
    return result[:100]


def _engineering_changes(rows, plan_task_names):
    result = []
    for row in rows:
        detail = row.get("detail") if isinstance(row.get("detail"), dict) else {}
        impacts = []
        for impact in detail.get("impacts") or []:
            impacts.append(
                {
                    "id": impact.get("id"),
                    "task_id": impact.get("task_id"),
                    "task_name": plan_task_names.get(impact.get("task_id")),
                    "action": impact.get("action"),
                    "implemented": bool(impact.get("implemented_by") and impact.get("implementation_evidence")),
                    "implementation_evidence_present": bool(impact.get("implementation_evidence")),
                    "recheck_passed": impact.get("recheck_passed"),
                    "rechecked": impact.get("recheck_passed") is not None,
                }
            )
        text = " ".join(str(detail.get(key) or "") for key in ("problem", "solution", "customer_evidence"))
        result.append(
            {
                "id": row.get("id"),
                "number": row.get("number"),
                "status": row.get("status"),
                "category": row.get("category"),
                "problem": detail.get("problem"),
                "solution": detail.get("solution"),
                "customer_due_affected": detail.get("customer_due_affected"),
                "customer_evidence_present": bool(detail.get("customer_evidence")),
                "source_classification": _classify_change(row.get("category"), text),
                "impact_count": len(impacts),
                "impacts": impacts,
            }
        )
    return result


def _contact_cases(db, user, project_id, allowed_tools):
    if "query_contact_cases" not in allowed_tools and "query_change_intake_context" not in allowed_tools:
        return []
    from .contacts import permitted

    rows = []
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
        for task in db.scalars(select(m.ContactTask).where(m.ContactTask.case_id == case.id).order_by(m.ContactTask.created_at.desc(), m.ContactTask.id).limit(100)):
            tasks.append(
                {
                    "id": task.id,
                    "title": task.title,
                    "status": task.status,
                    "affected_type": task.affected_type,
                    "affected_ref": task.affected_ref,
                    "impact_description": task.impact_description,
                    "planned_action": task.planned_action,
                    "delivery_impact_days": task.delivery_impact_days,
                    "estimated_amount": str(task.estimated_amount) if task.estimated_amount is not None else None,
                    "currency": task.currency,
                    "actual_completed_at": task.actual_completed_at.isoformat() if task.actual_completed_at else None,
                    "actual_hours": str(task.actual_hours) if task.actual_hours is not None else None,
                    "actual_amount": str(task.actual_amount) if task.actual_amount is not None else None,
                    "actual_currency": task.actual_currency,
                    "execution_evidence_present": bool(task.execution_evidence),
                    "verified_plan_id": task.verified_plan_id,
                    "source_system": task.source_system,
                    "source_ref": task.source_ref,
                }
            )
        rows.append(
            {
                "id": case.id,
                "title": case.title,
                "mode": case.mode,
                "collaboration_status": "CLOSED" if case.closed_at else "HISTORY_RECORD" if case.mode == "HISTORY" else "OPEN",
                "customer_ref": case.customer_ref,
                "customer_name": case.customer_name,
                "mold_number": case.mold_number,
                "product_ref": case.product_ref,
                "application_date": case.application_date.isoformat() if case.application_date else None,
                "problem_source": case.problem_source,
                "current_stage": case.current_stage,
                "change_type": case.change_type,
                "urgency": case.urgency,
                "revision": case.revision,
                "tasks": tasks,
            }
        )
    return rows


def _resolutions(rows):
    result = []
    for row in rows:
        detail = row.get("detail") if isinstance(row.get("detail"), dict) else {}
        snapshot = detail.get("material_snapshot") if isinstance(detail.get("material_snapshot"), dict) else {}
        result.append(
            {
                "id": row.get("id"),
                "number": row.get("number"),
                "status": row.get("status"),
                "case_id": detail.get("case_id"),
                "case_revision": detail.get("case_revision"),
                "solution": detail.get("solution"),
                "customer_due_affected": detail.get("customer_due_affected"),
                "customer_evidence_present": bool(detail.get("customer_evidence")),
                "snapshot_task_count": len(snapshot.get("tasks") or []),
                "snapshot_attachment_count": len(snapshot.get("attachments") or []),
            }
        )
    return result


def _classify_change(category, text):
    value = ((category or "") + " " + (text or "")).casefold()
    customer = any(keyword.casefold() in value for keyword in CUSTOMER_KEYWORDS) or category == "customer_change"
    outsource = any(keyword.casefold() in value for keyword in OUTSOURCE_KEYWORDS) or category == "outsource"
    internal = any(keyword.casefold() in value for keyword in INTERNAL_KEYWORDS)
    if customer and outsource:
        return "CUSTOMER_CHANGE_OUTSOURCE_EXECUTION"
    if customer:
        return "CUSTOMER_CHANGE_INTERNAL_OR_UNSPECIFIED"
    if outsource:
        return "OUTSOURCE_CHANGE"
    if internal:
        return "INTERNAL_REWORK_OR_MOLD_CHANGE"
    return "UNCLASSIFIED"


def _analysis(project, profile, molds, quote_acceptance, starts, sales_contracts, outsource_contracts, plan_tasks, engineering_changes, contacts, resolutions):
    contact_tasks = [task for case in contacts for task in case.get("tasks") or [] if task.get("status") != "CANCELLED"]
    contact_problem_sources = Counter(case.get("problem_source") or "UNKNOWN" for case in contacts)
    affected_types = Counter(task.get("affected_type") for task in contact_tasks)
    planned_actions = Counter(task.get("planned_action") for task in contact_tasks)
    amount_tasks = [task for task in contact_tasks if task.get("estimated_amount") or task.get("actual_amount")]
    incomplete_contact_tasks = [
        task
        for task in contact_tasks
        if task.get("status") not in {"VERIFIED", "CANCELLED"} or (task.get("actual_completed_at") and not task.get("execution_evidence_present"))
    ]
    effective_changes = [row for row in engineering_changes if row.get("status") == "EFFECTIVE"]
    open_change_impacts = [
        impact
        for change in engineering_changes
        for impact in change.get("impacts") or []
        if change.get("status") != "CLOSED" and (not impact.get("implemented") or impact.get("recheck_passed") is not True)
    ]
    effective_contracts = [row for row in sales_contracts + outsource_contracts if row.get("status") == "EFFECTIVE"]
    effective_starts = [row for row in starts if row.get("status") == "EFFECTIVE"]
    external_or_customer_cases = [case for case in contacts if case.get("problem_source") == "CUSTOMER_CHANGE" or case.get("customer_ref")]
    outsource_cases = [case for case in contacts if case.get("category") == "outsource" or case.get("problem_source") == "OUTSOURCE_DEFECT"]

    gaps = []
    warnings = []
    if not contacts and not engineering_changes:
        gaps.append("未见工程联络单或工程变更业务记录，不能仅凭合同、报价或口头描述认定已有设变记录。")
    if external_or_customer_cases and not any(case.get("customer_ref") for case in external_or_customer_cases) and not any(change.get("customer_evidence_present") for change in engineering_changes):
        gaps.append("存在客户设变线索但未见客户书面确认依据；口头沟通需补书面记录或可核对聊天/邮件证据。")
    if not molds:
        gaps.append("未见项目关联内部模具档案；已有模具再次设变不能重复建模具，需先定位原项目/原模具。")
    if external_or_customer_cases and not effective_starts:
        warnings.append("存在客户设变线索但未见已生效开工通知；无合同或免费小设变也不能跳过开工条件。")
    if amount_tasks and not effective_contracts:
        warnings.append("存在费用或扣款影响线索，但未见已生效合同；收费/免费、是否补合同和设变记录必须分开核对。")
    if open_change_impacts or incomplete_contact_tasks:
        warnings.append("存在未完成执行、复验或关闭的设变/联络事项，不能把方案审批或工程联络单获批等同于整改完成。")
    if outsource_cases and not outsource_contracts:
        warnings.append("存在委外设变或外协不良线索，但未见委外合同上下文；委外金额需由采购与供应商确认并保留依据。")
    if not plan_tasks:
        gaps.append("未见有效计划任务上下文，无法判断当前环节可变更性、继续/暂停/取消/返工/重新下达的边界。")
    gaps.append("当前工具只读核对设变承接上下文；不自动读取客户平台、不替代 ERP 设计/制造/采购/物流/财务执行。")

    return {
        "change_classification_summary": dict(contact_problem_sources),
        "affected_object_summary": dict(affected_types),
        "planned_action_summary": dict(planned_actions),
        "known_molds": molds,
        "latest_quote_acceptance": quote_acceptance[0] if quote_acceptance else None,
        "effective_start_notices": effective_starts,
        "sales_contracts": sales_contracts,
        "full_outsource_contracts": outsource_contracts,
        "plan_tasks": plan_tasks,
        "engineering_changes": engineering_changes,
        "engineering_contact_cases": contacts,
        "contact_resolutions": resolutions,
        "gaps": gaps,
        "warnings": warnings,
        "derived_status": {
            "project_status": project.status,
            "profile_execution_mode": (profile or {}).get("execution_mode"),
            "has_change_record": bool(contacts or engineering_changes),
            "has_customer_change_signal": bool(external_or_customer_cases) or any(change.get("customer_evidence_present") for change in engineering_changes),
            "has_customer_written_evidence": any(case.get("customer_ref") for case in external_or_customer_cases) or any(change.get("customer_evidence_present") for change in engineering_changes),
            "has_internal_mold_identity": bool(molds),
            "has_effective_start_notice": bool(effective_starts),
            "has_effective_contract": bool(effective_contracts),
            "has_charge_or_cost_impact": bool(amount_tasks),
            "has_outsource_change_signal": bool(outsource_cases) or any(change.get("source_classification") in {"OUTSOURCE_CHANGE", "CUSTOMER_CHANGE_OUTSOURCE_EXECUTION"} for change in engineering_changes),
            "has_plan_impact_context": bool(plan_tasks),
            "has_approved_solution": bool([row for row in resolutions if row.get("status") == "EFFECTIVE"]),
            "has_open_execution_or_recheck_items": bool(open_change_impacts or incomplete_contact_tasks),
            "open_impact_count": len(open_change_impacts) + len(incomplete_contact_tasks),
        },
    }


def query(db, user, data: ProjectPlanContextInput, allowed_tools: set[str]):
    project, alternatives, truncated = _resolve(db, user, data, allowed_tools)
    limitations = [
        "只读取当前用户可见项目；工程联络、工程变更、合同、开工、计划和处理方案分别受对应权限约束。",
        "设变记录、客户确认、报价/收费、销售或委外合同、开工通知、计划影响、执行反馈、复验关闭是不同事实，不能相互替代。",
        "本工具只做设变承接和异常闭环上下文核对，不创建联络单、不审批方案、不更新正式图纸/BOM/任务/合同/财务或 ERP 执行记录。",
    ]
    if truncated:
        limitations.append("最多检查前500个可见项目，结果可能未覆盖全部可见范围。")
    if project:
        profile = _profile(db, user, project.id)
        molds = _molds(db, user, project.id)
        quote_acceptance = _decision_rows(_subject_rows(db, user, project.id, "quote_acceptance", allowed_tools), "ACCEPT")
        starts = _decision_rows(_subject_rows(db, user, project.id, "internal_start", allowed_tools), "START")
        sales_contracts = _contracts(_subject_rows(db, user, project.id, "sales_contract", allowed_tools))
        outsource_contracts = _contracts(_subject_rows(db, user, project.id, "full_outsource_contract", allowed_tools))
        plan_tasks = _plan_tasks(_subject_rows(db, user, project.id, "project_plan", allowed_tools) + _subject_rows(db, user, project.id, "plan_change", allowed_tools))
        task_names = {task["task_id"]: task["name"] for task in plan_tasks}
        engineering_changes = _engineering_changes(_subject_rows(db, user, project.id, "engineering_change", allowed_tools), task_names)
        contacts = _contact_cases(db, user, project.id, allowed_tools)
        resolutions = _resolutions(_subject_rows(db, user, project.id, "contact_resolution", allowed_tools))
        contact_total = db.scalar(select(func.count()).select_from(m.ContactCase).where(m.ContactCase.project_id == project.id)) or 0
        skipped = []
        for kind, label in (
            ("quote_acceptance", "报价/承接记录"),
            ("internal_start", "正式开工通知"),
            ("sales_contract", "销售合同"),
            ("full_outsource_contract", "委外合同"),
            ("project_plan", "项目计划/受影响任务"),
            ("engineering_change", "工程变更审批记录"),
            ("contact_resolution", "联络单处理方案审批"),
        ):
            if not _can_read_kind(kind, allowed_tools):
                skipped.append(label)
        if ("query_contact_cases" not in allowed_tools and "query_change_intake_context" not in allowed_tools) or (contact_total and not contacts):
            skipped.append("工程联络协作事项")
        if skipped:
            limitations.append("未分配对应查询工具或权限，未返回：" + "、".join(skipped))
        return {
            "resolution": "RESOLVED",
            "data": [
                {
                    "project": _project_card(db, user, project, alternatives or ("项目定位",)),
                    "profile": profile,
                    "analysis": _analysis(
                        project,
                        profile,
                        molds,
                        quote_acceptance,
                        starts,
                        sales_contracts,
                        outsource_contracts,
                        plan_tasks,
                        engineering_changes,
                        contacts,
                        resolutions,
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
