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
        "pause_resume": {"query_project_control_context", "query_change_intake_context"},
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


def _iso(value):
    return value.isoformat() if value else None


def _pause_context(db, user, project_id):
    if not access(db, user, "pause_resume.read", {"project_id": project_id}).allowed:
        return None, True

    active_pause = db.scalar(
        select(m.PauseRecord)
        .where(m.PauseRecord.project_id == project_id, m.PauseRecord.end_date.is_(None))
        .order_by(m.PauseRecord.start_date.desc(), m.PauseRecord.id)
    )
    pause_detail = db.get(m.ProjectPauseDetail, active_pause.subject_id) if active_pause else None

    pending = []
    for subject in db.scalars(
        select(m.BusinessSubject)
        .where(m.BusinessSubject.project_id == project_id, m.BusinessSubject.kind == "pause_resume", m.BusinessSubject.status.in_(["DRAFT", "SUBMITTED"]))
        .order_by(m.BusinessSubject.created_at.desc(), m.BusinessSubject.id)
        .limit(20)
    ):
        detail = db.get(m.ProjectPauseDetail, subject.id)
        pending.append(
            {
                "id": subject.id,
                "number": subject.number,
                "status": subject.status,
                "decision": detail.decision if detail else None,
                "effective_date": _iso(detail.effective_date) if detail else None,
                "expected_resume_date": _iso(detail.expected_resume_date) if detail else None,
                "source_pause_subject_id": detail.source_pause_subject_id if detail else None,
                "reason_present": bool(detail and detail.reason),
                "evidence_present": bool(detail and detail.evidence),
            }
        )

    history = []
    for record in db.scalars(
        select(m.PauseRecord)
        .where(m.PauseRecord.project_id == project_id)
        .order_by(m.PauseRecord.start_date.desc(), m.PauseRecord.id)
        .limit(20)
    ):
        pause = db.get(m.ProjectPauseDetail, record.subject_id)
        resume = db.get(m.ProjectPauseDetail, record.resume_subject_id) if record.resume_subject_id else None
        shifts = [
            {
                "task_id": shift.task_id,
                "previous_start": _iso(shift.previous_start),
                "previous_end": _iso(shift.previous_end),
                "shifted_start": _iso(shift.shifted_start),
                "shifted_end": _iso(shift.shifted_end),
                "shifted_days": shift.shifted_days,
                "task_status": shift.task_status,
            }
            for shift in db.scalars(select(m.PauseTaskShift).where(m.PauseTaskShift.pause_id == record.id).order_by(m.PauseTaskShift.id).limit(50))
        ]
        history.append(
            {
                "pause_subject_id": record.subject_id,
                "resume_subject_id": record.resume_subject_id,
                "start_date": _iso(record.start_date),
                "end_date": _iso(record.end_date),
                "expected_resume_date": _iso(pause.expected_resume_date) if pause else None,
                "pause_reason_present": bool(pause and pause.reason),
                "pause_evidence_present": bool(pause and pause.evidence),
                "resume_reason_present": bool(resume and resume.reason),
                "resume_evidence_present": bool(resume and resume.evidence),
                "shifted_days": record.shifted_days,
                "shift_applied": record.shift_applied,
                "customer_due_date_snapshot": _iso(record.customer_due_date_snapshot),
                "task_shift_count": len(shifts),
                "task_shifts": shifts,
            }
        )

    return (
        {
            "active_pause": (
                {
                    "pause_subject_id": active_pause.subject_id,
                    "start_date": _iso(active_pause.start_date),
                    "expected_resume_date": _iso(pause_detail.expected_resume_date) if pause_detail else None,
                    "reason_present": bool(pause_detail and pause_detail.reason),
                    "evidence_present": bool(pause_detail and pause_detail.evidence),
                }
                if active_pause
                else None
            ),
            "pending_pause_requests": pending,
            "pause_history": history,
            "allowed_during_pause": ["资料补录", "沟通记录", "合同与结算核对", "工程联络与恢复申请"],
            "blocked_during_pause": ["普通下单", "报工", "发料", "计划执行"],
            "derived_status": {
                "has_active_pause": bool(active_pause),
                "has_pending_pause_request": bool(pending),
                "has_resume_shift_evidence": any(row["shift_applied"] and row["task_shift_count"] for row in history),
                "customer_due_date_is_independent": True,
            },
        },
        False,
    )


def _plan_task_matches(contact_task, plan_tasks):
    ref = str(contact_task.get("affected_ref") or "").strip().casefold()
    if not ref:
        return []
    matches = []
    for task in sorted(plan_tasks, key=lambda row: (0 if row.get("plan_status") == "EFFECTIVE" else 1, str(row.get("plan_number") or ""), str(row.get("key") or ""))):
        values = {str(task.get("task_id") or "").casefold(), str(task.get("key") or "").casefold(), str(task.get("name") or "").casefold()}
        if ref in values:
            matches.append(task)
    return [
        {
            "plan_id": task.get("plan_id"),
            "plan_number": task.get("plan_number"),
            "plan_status": task.get("plan_status"),
            "task_id": task.get("task_id"),
            "key": task.get("key"),
            "name": task.get("name"),
            "status": task.get("status"),
            "planned_start": task.get("planned_start"),
            "planned_end": task.get("planned_end"),
        }
        for task in matches[:10]
    ]


def _plan_change_prepare_seed(project, case, contact_task, matched_tasks, evidence_gaps, recommended_tools):
    plan_ids = {task.get("plan_id") for task in matched_tasks if task.get("plan_id")}
    effective_matches = [task for task in matched_tasks if task.get("plan_status") == "EFFECTIVE"]
    primary_matches = effective_matches or matched_tasks
    previous_id = primary_matches[0].get("plan_id") if len(plan_ids) == 1 and primary_matches else None
    previous_number = primary_matches[0].get("plan_number") if previous_id else None
    seed_status = "READY_TO_QUERY_PLAN_CONTEXT" if previous_id and not evidence_gaps and "query_project_plan_context" in recommended_tools else "NEEDS_CONTEXT"
    return {
        "status": seed_status,
        "project_id": project.id,
        "project_version": project.row_version,
        "previous_id": previous_id,
        "previous_plan_number": previous_number,
        "source_contact_case_id": case.get("id"),
        "source_contact_task_id": contact_task.get("id"),
        "candidate_task_keys": [task.get("key") for task in primary_matches if task.get("key")],
        "change_intent": [
            {
                "task_key": task.get("key"),
                "task_name": task.get("name"),
                "planned_action": contact_task.get("planned_action"),
                "delivery_impact_days": contact_task.get("delivery_impact_days"),
                "impact_description": contact_task.get("impact_description"),
            }
            for task in primary_matches
        ],
        "reason_basis": "工程联络单《{case_title}》事项《{task_title}》：{impact}".format(
            case_title=case.get("title") or "",
            task_title=contact_task.get("title") or "",
            impact=contact_task.get("impact_description") or "",
        ),
        "required_before_prepare": [
            "必须先调用 query_project_plan_context，以当前有效计划 previous_id、project_version、完整任务清单和 workflow_options 为准。",
            "prepare_project_plan_change 的 tasks 必须提交变更后的完整任务列表；未受影响节点保持原值。",
            "delivery_impact_days 只是项目负责人评估依据，不能自动等量顺延全部节点或修改客户承诺交期。",
        ],
    }


def _plan_adjustment_candidates(project, contacts, plan_tasks, resolutions, allowed_tools, project_control=None):
    effective_resolution_cases = {row.get("case_id") for row in resolutions if row.get("status") == "EFFECTIVE"}
    recommended_tools = []
    project_control_status = (project_control or {}).get("derived_status") or {}
    if (project_control_status.get("has_active_pause") or project_control_status.get("has_pending_pause_request")) and "query_project_control_context" in allowed_tools:
        recommended_tools.append("query_project_control_context")
    if "query_project_plan_context" in allowed_tools:
        recommended_tools.append("query_project_plan_context")
        if "prepare_project_plan_change" in allowed_tools:
            recommended_tools.append("prepare_project_plan_change")
    result = []
    for case in contacts:
        for task in case.get("tasks") or []:
            if task.get("status") == "CANCELLED":
                continue
            relevant = (
                task.get("affected_type") in {"PLAN_NODE", "WIP_TASK"}
                or int(task.get("delivery_impact_days") or 0) > 0
                or task.get("planned_action") in {"PAUSE", "CANCEL", "REWORK", "REISSUE"}
            )
            if not relevant:
                continue
            matched_tasks = _plan_task_matches(task, plan_tasks)
            evidence_gaps = []
            if case.get("id") not in effective_resolution_cases:
                evidence_gaps.append("尚无已审批生效的联络单处理方案，不能直接作为计划变更依据。")
            if not matched_tasks:
                evidence_gaps.append("联络事项 affected_ref 未精确匹配当前可见计划任务 ID、标识或名称。")
            if task.get("status") not in {"RESPONDED", "VERIFIED"}:
                evidence_gaps.append("责任部门尚未提交处理反馈或执行依据。")
            if int(task.get("delivery_impact_days") or 0) == 0 and task.get("planned_action") == "CONTINUE":
                evidence_gaps.append("未登记交期影响天数且计划动作为继续，需项目负责人确认是否需要计划变更。")
            if project_control_status.get("has_active_pause"):
                evidence_gaps.append("项目当前处于暂停状态；普通计划执行、下单、报工和发料受限，计划变更前需先核对暂停/恢复上下文。")
            if project_control_status.get("has_pending_pause_request"):
                evidence_gaps.append("存在待审批暂停/恢复申请；计划调整前需确认暂停区间和冻结任务范围是否已变化。")
            result.append(
                {
                    "case_id": case.get("id"),
                    "case_title": case.get("title"),
                    "case_revision": case.get("revision"),
                    "contact_task_id": task.get("id"),
                    "contact_task_title": task.get("title"),
                    "contact_task_status": task.get("status"),
                    "affected_type": task.get("affected_type"),
                    "affected_ref": task.get("affected_ref"),
                    "planned_action": task.get("planned_action"),
                    "delivery_impact_days": task.get("delivery_impact_days"),
                    "impact_description": task.get("impact_description"),
                    "matched_plan_tasks": matched_tasks,
                    "candidate_status": "READY_FOR_PLAN_CHANGE_PREPARE" if matched_tasks and not evidence_gaps else "NEEDS_CONTEXT",
                    "evidence_gaps": evidence_gaps,
                    "recommended_next_tools": recommended_tools,
                    "plan_change_prepare_seed": _plan_change_prepare_seed(project, case, task, matched_tasks, evidence_gaps, recommended_tools),
                    "guardrail": "只提示项目负责人核对并准备计划变更；不会自动改计划、自动顺延任务或跳过 BPM 审批。",
                }
            )
    return result[:50]


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


def _analysis(project, profile, molds, quote_acceptance, starts, sales_contracts, outsource_contracts, plan_tasks, engineering_changes, contacts, resolutions, project_control, allowed_tools):
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
    project_control_status = (project_control or {}).get("derived_status") or {}
    plan_adjustment_candidates = _plan_adjustment_candidates(project, contacts, plan_tasks, resolutions, allowed_tools, project_control)

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
    if project_control_status.get("has_active_pause"):
        warnings.append("项目当前处于暂停状态；工程联络和恢复申请可继续办理，但普通计划执行、下单、报工和发料受暂停门禁限制。")
    if project_control_status.get("has_pending_pause_request"):
        warnings.append("存在待审批暂停/恢复申请；设变计划调整、执行安排和交期判断需先核对冻结任务范围。")
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
        "project_control": project_control,
        "plan_adjustment_candidates": plan_adjustment_candidates,
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
            "has_plan_adjustment_candidate": bool(plan_adjustment_candidates),
            "plan_adjustment_candidate_count": len(plan_adjustment_candidates),
            "has_active_project_pause": bool(project_control_status.get("has_active_pause")),
            "has_pending_pause_request": bool(project_control_status.get("has_pending_pause_request")),
            "has_resume_shift_evidence": bool(project_control_status.get("has_resume_shift_evidence")),
            "customer_due_date_is_independent": bool(project_control_status.get("customer_due_date_is_independent")),
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
        project_control, pause_skipped = _pause_context(db, user, project.id)
        contact_total = db.scalar(select(func.count()).select_from(m.ContactCase).where(m.ContactCase.project_id == project.id)) or 0
        skipped = []
        for kind, label in (
            ("quote_acceptance", "报价/承接记录"),
            ("internal_start", "正式开工通知"),
            ("sales_contract", "销售合同"),
            ("full_outsource_contract", "委外合同"),
            ("project_plan", "项目计划/受影响任务"),
            ("pause_resume", "项目暂停/恢复资料"),
            ("engineering_change", "工程变更审批记录"),
            ("contact_resolution", "联络单处理方案审批"),
        ):
            if not _can_read_kind(kind, allowed_tools):
                skipped.append(label)
        if pause_skipped:
            skipped.append("项目暂停/恢复资料")
        if ("query_contact_cases" not in allowed_tools and "query_change_intake_context" not in allowed_tools) or (contact_total and not contacts):
            skipped.append("工程联络协作事项")
        if skipped:
            limitations.append("未分配对应查询工具或权限，未返回：" + "、".join(dict.fromkeys(skipped)))
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
                        project_control,
                        allowed_tools,
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
