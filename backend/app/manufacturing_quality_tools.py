from collections import defaultdict
from sqlalchemy import select, and_

from . import models as m
from .authorization import access, predicate, select_fields
from .db import now
from .errors import DomainError
from .plan_tools import ProjectPlanContextInput, _strength


PROCESS_KEYWORDS = ("加工", "工序", "生产", "CNC", "EDM", "线切割", "铣", "车", "磨", "machin", "manufactur", "process")


def _project_card(db, user, project, matched_by=()):
    fields = access(db, user, "project.read", {"project_id": project.id}).fields
    card = select_fields(
        {"id": project.id, "code": project.code, "name": project.name, "status": project.status, "row_version": project.row_version},
        fields,
    )
    card["matched_by"] = sorted(set(matched_by))
    return card


def _visible_projects(db, user):
    gate = and_(predicate(db, user, "project.read", {"project_id": m.Project.id}), predicate(db, user, "project_plan.read", {"project_id": m.Project.id}))
    rows = list(db.scalars(select(m.Project).where(gate).order_by(m.Project.code).limit(501)))
    return rows[:500], len(rows) > 500


def _visible_subjects(db, user, project_ids, kind, allowed_tools):
    context_tools = {
        "project_plan": {"query_project_plan_context", "query_manufacturing_quality_context"},
        "plan_change": {"query_project_plan_context", "query_manufacturing_quality_context"},
        "design_route": {"query_design_route_context"},
        "assembly_issue": {"query_assembly_issue"},
        "trial_request": {"query_trial_request"},
        "engineering_change": {"query_engineering_change"},
    }
    direct_tool = "query_" + kind
    if direct_tool not in allowed_tools and not (context_tools.get(kind, set()) & allowed_tools):
        return []
    from .domains import data as subject_data

    result = []
    for subject in db.scalars(
        select(m.BusinessSubject)
        .where(m.BusinessSubject.project_id.in_(project_ids), m.BusinessSubject.kind == kind)
        .order_by(m.BusinessSubject.created_at.desc(), m.BusinessSubject.id)
        .limit(501)
    ):
        try:
            result.append(subject_data(db, user, subject))
        except DomainError:
            continue
    return result[:500]


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
        for kind, label in (("project_plan", "项目计划"), ("plan_change", "计划变更")):
            for subject in _visible_subjects(db, user, list(by_id), kind, allowed_tools):
                add(subject.get("project_id"), subject.get("number"), label + "单号")
                detail = subject.get("detail") if isinstance(subject.get("detail"), dict) else {}
                for task in detail.get("tasks") or []:
                    add(subject.get("project_id"), task.get("key"), label + "任务标识")
                    add(subject.get("project_id"), task.get("name"), label + "任务名称")
        if "query_design_route_context" in allowed_tools:
            for subject in _visible_subjects(db, user, list(by_id), "design_route", allowed_tools):
                add(subject.get("project_id"), subject.get("number"), "设计单号")
                detail = subject.get("detail") if isinstance(subject.get("detail"), dict) else {}
                for item in detail.get("items") or []:
                    add(subject.get("project_id"), item.get("route"), "加工路线")
                    if item.get("material_id"):
                        material = db.get(m.Material, item["material_id"])
                        if material:
                            add(subject.get("project_id"), material.code, "物料编号")
                            add(subject.get("project_id"), material.name, "物料名称")
        if "query_contact_cases" in allowed_tools:
            for case in db.scalars(select(m.ContactCase).where(m.ContactCase.project_id.in_(list(by_id))).limit(501)):
                add(case.project_id, case.title, "工程联络标题")
                add(case.project_id, case.mold_number, "工程联络模具号")
                add(case.project_id, case.product_ref, "工程联络产品")
    if not scores:
        return None, [], truncated
    best = max(scores.values())
    ids = [pid for pid, score in scores.items() if score == best]
    if len(ids) != 1:
        return None, [_project_card(db, user, by_id[pid], reasons[pid]) for pid in ids[:20]], truncated
    return by_id[ids[0]], reasons[ids[0]], truncated


def _profile(db, user, project_id):
    profile = db.get(m.ProjectProfile, project_id)
    if not profile:
        return None
    fields = access(db, user, "project.read", {"project_id": project_id}).fields
    return select_fields(
        {
            "execution_mode": profile.execution_mode,
            "customer_due_date": profile.customer_due_date.isoformat() if profile.customer_due_date else None,
            "settlement_status": profile.settlement_status,
        },
        fields | {"execution_mode", "customer_due_date", "settlement_status"},
    )


def _plan_records(db, user, project_id, allowed_tools):
    return {
        "project_plan": _visible_subjects(db, user, [project_id], "project_plan", allowed_tools)[:20],
        "plan_change": _visible_subjects(db, user, [project_id], "plan_change", allowed_tools)[:20],
    }


def _active_plan(records):
    plans = records["project_plan"] + records["plan_change"]
    effective = [row for row in plans if row.get("status") == "EFFECTIVE"]
    return max(effective, key=lambda row: row.get("created_at", "")) if effective else None


def _task_summary(plan):
    detail = plan.get("detail") if isinstance(plan.get("detail"), dict) else {}
    result = []
    for task in detail.get("tasks") or []:
        text = (str(task.get("key") or "") + " " + str(task.get("name") or "")).casefold()
        process_like = any(keyword.casefold() in text for keyword in PROCESS_KEYWORDS)
        result.append(
            {
                "id": task.get("id"),
                "key": task.get("key"),
                "name": task.get("name"),
                "status": task.get("status"),
                "planned_start": task.get("planned_start"),
                "planned_end": task.get("planned_end"),
                "actual_start": task.get("actual_start"),
                "actual_end": task.get("actual_end"),
                "prerequisites": task.get("prerequisites") or [],
                "process_like": process_like,
                "has_start_report": bool(task.get("actual_start")),
                "has_finish_report": bool(task.get("actual_end") and task.get("status") == "DONE"),
            }
        )
    return result


def _materials(db, designs):
    ids = {
        item.get("material_id")
        for design in designs
        for item in (design.get("detail") or {}).get("items", [])
        if isinstance(design.get("detail"), dict) and item.get("material_id")
    }
    materials = {row.id: row for row in db.scalars(select(m.Material).where(m.Material.id.in_(ids)).limit(501))} if ids else {}
    rows = []
    for design in designs:
        detail = design.get("detail") if isinstance(design.get("detail"), dict) else {}
        for item in detail.get("items") or []:
            material = materials.get(item.get("material_id"))
            rows.append(
                {
                    "design_id": design.get("id"),
                    "design_number": design.get("number"),
                    "drawing_revision": detail.get("drawing_revision"),
                    "material_id": item.get("material_id"),
                    "material_code": material.code if material else None,
                    "material_name": material.name if material else None,
                    "quantity": item.get("quantity"),
                    "route": item.get("route"),
                    "task_id": item.get("task_id"),
                }
            )
    return rows


def _assembly_trials(db, user, project_id, allowed_tools):
    assemblies = _visible_subjects(db, user, [project_id], "assembly_issue", allowed_tools)[:20]
    trials = _visible_subjects(db, user, [project_id], "trial_request", allowed_tools)[:20]
    return {
        "assembly_issues": [
            {
                "id": row.get("id"),
                "number": row.get("number"),
                "status": row.get("status"),
                "execution_status": (row.get("detail") or {}).get("execution_status"),
                "planned_date": (row.get("detail") or {}).get("planned_date"),
                "execution": (row.get("detail") or {}).get("execution") or [],
            }
            for row in assemblies
        ],
        "trial_requests": [
            {
                "id": row.get("id"),
                "number": row.get("number"),
                "status": row.get("status"),
                "planned_date": (row.get("detail") or {}).get("planned_date"),
                "location": (row.get("detail") or {}).get("location"),
                "results": (row.get("detail") or {}).get("results") or [],
            }
            for row in trials
        ],
    }


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
        for task in db.scalars(select(m.ContactTask).where(m.ContactTask.case_id == case.id, m.ContactTask.status != "CANCELLED").limit(50)):
            tasks.append(
                {
                    "id": task.id,
                    "title": task.title,
                    "status": task.status,
                    "affected_type": task.affected_type,
                    "affected_ref": task.affected_ref,
                    "planned_action": task.planned_action,
                    "actual_completed_at": task.actual_completed_at.isoformat() if task.actual_completed_at else None,
                    "actual_hours": str(task.actual_hours) if task.actual_hours is not None else None,
                    "actual_amount": str(task.actual_amount) if task.actual_amount is not None else None,
                }
            )
        if tasks or case.problem_source:
            issues.append(
                {
                    "id": case.id,
                    "title": case.title,
                    "collaboration_status": "CLOSED" if case.closed_at else "HISTORY_RECORD" if case.mode == "HISTORY" else "OPEN",
                    "problem_source": case.problem_source,
                    "current_stage": case.current_stage,
                    "change_type": case.change_type,
                    "urgency": case.urgency,
                    "tasks": tasks[:20],
                }
            )
    return issues[:20]


def _analysis(project, profile, records, task_rows, design_materials, assembly_trial, contacts, designs):
    process_tasks = [task for task in task_rows if task["process_like"]]
    started = [task for task in process_tasks if task["has_start_report"]]
    done = [task for task in process_tasks if task["has_finish_report"]]
    internal_items = [row for row in design_materials if row.get("route") == "INTERNAL"]
    unlinked_internal = [row for row in internal_items if not row.get("task_id")]
    purchase_or_outsource = [row for row in design_materials if row.get("route") in {"PURCHASE", "OUTSOURCE"}]
    open_contacts = [case for case in contacts if case.get("collaboration_status") != "CLOSED"]
    trial_failures = [
        result
        for trial in assembly_trial["trial_requests"]
        for result in trial.get("results", [])
        if result.get("passed") is False
    ]
    warnings = []
    gaps = []
    if not records.get("project_plan") and not records.get("plan_change"):
        warnings.append("当前可见范围未见项目计划，不能判断工序任务安排。")
    if not process_tasks:
        gaps.append("未见明确的加工/工序/生产类计划任务。")
    if process_tasks and not started:
        gaps.append("未见加工类任务实际开工或现场报工日期。")
    if started and not done:
        warnings.append("已有加工类任务开工记录，但未见全部完工记录。")
    gaps.append("未见结构化工时、设备、人员班组或现场异常报工明细；当前只能读取计划任务实际开始/完成日期。")
    gaps.append("未见独立工序检测报告、合格验收资料或质检结论；不能把计划任务完成直接等同于检验合格。")
    if unlinked_internal:
        warnings.append("存在内部加工路线物料未关联计划任务，不能判断该工序已排入计划。")
    if purchase_or_outsource:
        warnings.append("存在采购或委外路线物料；内部制造结论须区分局部委外交接、收货和检验依据。")
    if open_contacts:
        warnings.append("存在未关闭工程联络或整改事项，不能仅凭方案或任务进度认定问题关闭。")
    if trial_failures:
        warnings.append("存在试模未通过结果，需关联工程联络/整改和复验依据。")
    return {
        "active_plan": _active_plan(records),
        "process_tasks": process_tasks[:50],
        "started_process_tasks": started[:20],
        "done_process_tasks": done[:20],
        "design_internal_route_items": internal_items[:50],
        "purchase_or_outsource_route_items": purchase_or_outsource[:50],
        "assembly": assembly_trial["assembly_issues"],
        "trial": assembly_trial["trial_requests"],
        "quality_or_rework_contacts": contacts,
        "gaps": gaps,
        "warnings": warnings,
        "derived_status": {
            "project_status": project.status,
            "execution_mode": (profile or {}).get("execution_mode"),
            "has_effective_plan": bool(_active_plan(records)),
            "has_process_task": bool(process_tasks),
            "has_start_report": bool(started),
            "has_finish_report": bool(done),
            "has_independent_quality_report": False,
            "has_open_quality_or_rework_contact": bool(open_contacts),
            "has_internal_route_without_task": bool(unlinked_internal),
            "has_purchase_or_outsource_route": bool(purchase_or_outsource),
        },
    }


def query(db, user, data: ProjectPlanContextInput, allowed_tools: set[str]):
    project, alternatives, truncated = _resolve(db, user, data, allowed_tools)
    limitations = [
        "只读取当前用户可见且具备项目计划读取权限的项目。",
        "本工具只核对制造工序、报工、质检和整改上下文，不创建工单、不登记报工、不确认检验、不修改 ERP 制造数据。",
        "计划任务实际日期只能作为当前 Agent 可见的执行事实，不能替代完整工时、设备、人员、检测报告、仓库收货或质量验收记录。",
    ]
    if truncated:
        limitations.append("最多检查前500个可见项目，结果可能未覆盖全部可见范围。")
    if project:
        records = _plan_records(db, user, project.id, allowed_tools)
        active = _active_plan(records)
        task_rows = _task_summary(active) if active else []
        designs = _visible_subjects(db, user, [project.id], "design_route", allowed_tools)[:20]
        design_materials = _materials(db, designs)
        assembly_trial = _assembly_trials(db, user, project.id, allowed_tools)
        contacts = _contact_issues(db, user, project.id, allowed_tools)
        profile = _profile(db, user, project.id)
        skipped = []
        if "query_design_route_context" not in allowed_tools and "query_design_route" not in allowed_tools:
            skipped.append("设计BOM与内部/采购/委外路线")
        if "query_contact_cases" not in allowed_tools:
            skipped.append("工程联络异常与整改")
        if "query_assembly_issue" not in allowed_tools:
            skipped.append("装配任务")
        if "query_trial_request" not in allowed_tools:
            skipped.append("试模记录")
        if skipped:
            limitations.append("未分配对应查询工具，未返回：" + "、".join(skipped))
        analysis = _analysis(project, profile, records, task_rows, design_materials, assembly_trial, contacts, designs)
        return {
            "resolution": "RESOLVED",
            "data": [
                {
                    "project": _project_card(db, user, project, alternatives or ("项目定位",)),
                    "profile": profile,
                    "project_plans": records["project_plan"],
                    "plan_changes": records["plan_change"],
                    "design_routes": designs,
                    "analysis": analysis,
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
