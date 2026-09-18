from collections import defaultdict

from sqlalchemy import and_, select

from domain_packs.mold import models as m
from domain_packs.mold.authorization import access, predicate, select_fields
from domain_packs.mold.ports.db import now
from domain_packs.mold.ports.errors import DomainError
from domain_packs.mold.tools.erp.project.plan_tools import ProjectPlanContextInput, _strength


ASSEMBLY_KEYWORDS = ("装配", "组立", "钳工", "assembly", "fit")
TRIAL_KEYWORDS = ("试模", "调试", "t0", "t1", "trial", "debug")
ISSUE_SOURCES = {"ASSEMBLY_ISSUE", "TRIAL_ISSUE", "QUALITY_ISSUE"}


def _project_card(db, user, project, matched_by=()):
    fields = access(db, user, "project.read", {"project_id": project.id}).fields
    card = select_fields(
        {"id": project.id, "code": project.code, "name": project.name, "status": project.status, "row_version": project.row_version},
        fields,
    )
    card["matched_by"] = sorted(set(matched_by))
    return card


def _visible_projects(db, user):
    gate = and_(predicate(db, user, "project.read", {"project_id": m.Project.id}), predicate(db, user, "assembly_issue.read", {"project_id": m.Project.id}))
    rows = list(db.scalars(select(m.Project).where(gate).order_by(m.Project.code).limit(501)))
    return rows[:500], len(rows) > 500


def _visible_subjects(db, user, project_ids, kind, allowed_tools):
    context_tools = {
        "project_plan": {"query_project_plan_context", "query_assembly_trial_context"},
        "plan_change": {"query_project_plan_context", "query_assembly_trial_context"},
        "design_route": {"query_design_route_context", "query_design_route"},
        "assembly_issue": {"query_assembly_trial_context", "query_assembly_issue"},
        "trial_request": {"query_assembly_trial_context", "query_trial_request"},
        "engineering_change": {"query_engineering_change"},
    }
    direct_tool = "query_" + kind
    if direct_tool not in allowed_tools and not (context_tools.get(kind, set()) & allowed_tools):
        return []
    from domain_packs.mold.erp.core.domains import data as subject_data

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
        for kind, label in (
            ("assembly_issue", "装配任务"),
            ("trial_request", "试模申请"),
            ("project_plan", "项目计划"),
            ("plan_change", "计划变更"),
            ("design_route", "设计BOM"),
        ):
            for subject in _visible_subjects(db, user, list(by_id), kind, allowed_tools):
                add(subject.get("project_id"), subject.get("id"), label + "ID")
                add(subject.get("project_id"), subject.get("number"), label + "单号")
                detail = subject.get("detail") if isinstance(subject.get("detail"), dict) else {}
                for task in detail.get("tasks") or []:
                    add(subject.get("project_id"), task.get("key"), label + "任务标识")
                    add(subject.get("project_id"), task.get("name"), label + "任务名称")
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


def _task_rows(plan):
    detail = plan.get("detail") if isinstance(plan.get("detail"), dict) else {}
    result = []
    for task in detail.get("tasks") or []:
        text = (str(task.get("key") or "") + " " + str(task.get("name") or "")).casefold()
        assembly_like = any(keyword.casefold() in text for keyword in ASSEMBLY_KEYWORDS)
        trial_like = any(keyword.casefold() in text for keyword in TRIAL_KEYWORDS)
        if not (assembly_like or trial_like):
            continue
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
                "assembly_like": assembly_like,
                "trial_like": trial_like,
            }
        )
    return result


def _design_context(db, user, project_id, allowed_tools):
    designs = _visible_subjects(db, user, [project_id], "design_route", allowed_tools)[:20]
    rows = []
    material_ids = {
        item.get("material_id")
        for design in designs
        for item in ((design.get("detail") or {}).get("items") or [])
        if isinstance(design.get("detail"), dict) and item.get("material_id")
    }
    materials = {row.id: row for row in db.scalars(select(m.Material).where(m.Material.id.in_(material_ids)).limit(501))} if material_ids else {}
    for design in designs:
        detail = design.get("detail") if isinstance(design.get("detail"), dict) else {}
        for item in detail.get("items") or []:
            material = materials.get(item.get("material_id"))
            rows.append(
                {
                    "design_number": design.get("number"),
                    "drawing_revision": detail.get("drawing_revision"),
                    "material_id": item.get("material_id"),
                    "material_code": material.code if material else None,
                    "material_name": material.name if material else None,
                    "route": item.get("route"),
                    "task_id": item.get("task_id"),
                    "quantity": item.get("quantity"),
                }
            )
    return {"design_routes": designs, "bom_routes": rows[:100]}


def _assembly_rows(db, user, project_id, allowed_tools):
    rows = _visible_subjects(db, user, [project_id], "assembly_issue", allowed_tools)[:50]
    return [
        {
            "id": row.get("id"),
            "number": row.get("number"),
            "status": row.get("status"),
            "revision": row.get("revision"),
            "created_at": row.get("created_at"),
            "design_id": (row.get("detail") or {}).get("design_id"),
            "supervisor_id": (row.get("detail") or {}).get("supervisor_id"),
            "prerequisites_evidence": (row.get("detail") or {}).get("prerequisites_evidence"),
            "planned_date": (row.get("detail") or {}).get("planned_date"),
            "execution_status": (row.get("detail") or {}).get("execution_status"),
            "execution": (row.get("detail") or {}).get("execution") or [],
        }
        for row in rows
    ]


def _trial_rows(db, user, project_id, allowed_tools):
    rows = _visible_subjects(db, user, [project_id], "trial_request", allowed_tools)[:50]
    return [
        {
            "id": row.get("id"),
            "number": row.get("number"),
            "status": row.get("status"),
            "revision": row.get("revision"),
            "created_at": row.get("created_at"),
            "assembly_id": (row.get("detail") or {}).get("assembly_id"),
            "planned_date": (row.get("detail") or {}).get("planned_date"),
            "location": (row.get("detail") or {}).get("location"),
            "acceptance_criteria": (row.get("detail") or {}).get("acceptance_criteria"),
            "responsible_id": (row.get("detail") or {}).get("responsible_id"),
            "results": (row.get("detail") or {}).get("results") or [],
        }
        for row in rows
    ]


def _contact_issues(db, user, project_id, allowed_tools):
    if "query_contact_cases" not in allowed_tools:
        return []
    from domain_packs.mold.erp.change.contacts import permitted

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
            task_like = task.affected_type in {"ASSEMBLY", "TRIAL", "PLAN_NODE", "QUALITY_REPORT"} or any(
                token in (str(task.title) + str(task.affected_ref) + str(task.impact_description or "")) for token in ("装配", "试模", "组立", "调试")
            )
            if task_like:
                tasks.append(
                    {
                        "id": task.id,
                        "title": task.title,
                        "status": task.status,
                        "affected_type": task.affected_type,
                        "affected_ref": task.affected_ref,
                        "planned_action": task.planned_action,
                        "actual_completed_at": task.actual_completed_at.isoformat() if task.actual_completed_at else None,
                        "delivery_impact_days": task.delivery_impact_days,
                    }
                )
        case_like = case.problem_source in ISSUE_SOURCES or any(token in (str(case.current_stage) + str(case.title)) for token in ("装配", "试模", "组立", "调试"))
        if case_like or tasks:
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


def _analysis(project, profile, records, tasks, design, assemblies, trials, contacts):
    assembly_tasks = [task for task in tasks if task["assembly_like"]]
    trial_tasks = [task for task in tasks if task["trial_like"]]
    effective_assemblies = [row for row in assemblies if row.get("status") == "EFFECTIVE"]
    done_assemblies = [row for row in effective_assemblies if row.get("execution_status") == "DONE" or any(e.get("action") == "DONE" for e in row.get("execution", []))]
    running_assemblies = [row for row in effective_assemblies if row.get("execution_status") == "RUNNING" or any(e.get("action") == "START" for e in row.get("execution", []))]
    effective_trials = [row for row in trials if row.get("status") == "EFFECTIVE"]
    trial_results = [result for trial in effective_trials for result in trial.get("results", [])]
    failed_trials = [result for result in trial_results if result.get("passed") is False]
    passed_trials = [result for result in trial_results if result.get("passed") is True]
    open_contacts = [case for case in contacts if case.get("collaboration_status") != "CLOSED"]
    internal_or_outsource = [row for row in design["bom_routes"] if row.get("route") in {"INTERNAL", "OUTSOURCE"}]
    purchase_routes = [row for row in design["bom_routes"] if row.get("route") == "PURCHASE"]
    warnings = []
    gaps = []

    if not _active_plan(records):
        warnings.append("当前可见范围未见有效项目计划，无法核对装配/试模节点与前置依赖。")
    if not assembly_tasks:
        gaps.append("未见明确装配计划节点或钳工装配任务节点。")
    if not trial_tasks:
        gaps.append("未见明确试模/调试计划节点。")
    if not design["design_routes"]:
        warnings.append("当前可见范围未见设计BOM与路线，不能判断装配前零件、加工路线或试模料是否齐套。")
    if not effective_assemblies:
        gaps.append("未见生效装配工单/装配任务下发记录。")
    if effective_assemblies and not done_assemblies:
        gaps.append("已有装配任务，但未见装配完工确认。")
    if not effective_trials:
        gaps.append("未见生效试模申请或试模资源安排记录。")
    if effective_trials and not trial_results:
        gaps.append("已有试模申请，但未见试模执行报告或结论。")
    if failed_trials:
        warnings.append("存在试模未通过结果，需关联工程联络单、整改责任任务和重新验证依据。")
    if passed_trials:
        warnings.append("试模通过只代表当前可见试模结论，不等于客户验收、出厂放行或项目关闭。")
    if open_contacts:
        warnings.append("存在未关闭装配/试模/质量工程联络事项，不能认定异常闭环完成。")
    if purchase_routes:
        warnings.append("存在采购路线物料；装配齐套仍需采购到货、检验和发料依据。")
    if internal_or_outsource and not design["design_routes"]:
        warnings.append("存在制造/委外路线线索但缺设计明细，无法核对装配前置。")
    gaps.append("未见独立齐套率、关键件齐套口径或 ERP 齐套检查回执；不得仅凭计划节点判断可装配。")
    gaps.append("未见出厂自检合格资料、试模报告附件解析或客户验收依据；通过后仍需正式资料闭环。")

    return {
        "active_plan": _active_plan(records),
        "assembly_trial_plan_tasks": tasks[:50],
        "assembly_tasks": assembly_tasks[:20],
        "trial_tasks": trial_tasks[:20],
        "design_bom_routes": design["bom_routes"][:100],
        "assembly_orders": assemblies,
        "trial_requests": trials,
        "assembly_or_trial_contacts": contacts,
        "gaps": gaps,
        "warnings": warnings,
        "derived_status": {
            "project_status": project.status,
            "execution_mode": (profile or {}).get("execution_mode"),
            "has_effective_plan": bool(_active_plan(records)),
            "has_assembly_plan_node": bool(assembly_tasks),
            "has_trial_plan_node": bool(trial_tasks),
            "has_design_route": bool(design["design_routes"]),
            "has_assembly_order": bool(effective_assemblies),
            "has_assembly_started": bool(running_assemblies or done_assemblies),
            "has_assembly_done": bool(done_assemblies),
            "has_trial_request": bool(effective_trials),
            "has_trial_result": bool(trial_results),
            "has_trial_passed": bool(passed_trials),
            "has_trial_failed": bool(failed_trials),
            "has_open_assembly_or_trial_issue": bool(open_contacts),
        },
    }


def query(db, user, data: ProjectPlanContextInput, allowed_tools: set[str]):
    project, alternatives, truncated = _resolve(db, user, data, allowed_tools)
    limitations = [
        "只读取当前用户具备项目读取和装配读取权限的项目。",
        "本工具只核对装配、试模、前置、资源和异常上下文，不下达装配工单、不登记试模、不修改 ERP 装配/试模执行数据。",
        "实际装配开完工、试模开始/完成等执行事实应复用 ERP 或已登记正式业务回执；本工具只展示 Agent 当前可见事实和缺口。",
    ]
    if truncated:
        limitations.append("最多检查前500个可见项目，结果可能未覆盖全部可见范围。")
    if project:
        records = _plan_records(db, user, project.id, allowed_tools)
        active = _active_plan(records)
        tasks = _task_rows(active) if active else []
        design = _design_context(db, user, project.id, allowed_tools)
        assemblies = _assembly_rows(db, user, project.id, allowed_tools)
        trials = _trial_rows(db, user, project.id, allowed_tools)
        contacts = _contact_issues(db, user, project.id, allowed_tools)
        profile = _profile(db, user, project.id)
        skipped = []
        if not records["project_plan"] and not records["plan_change"]:
            skipped.append("项目计划/计划变更")
        if not design["design_routes"]:
            skipped.append("设计BOM与路线")
        if not trials:
            skipped.append("试模申请与试模结果")
        if "query_contact_cases" not in allowed_tools:
            skipped.append("工程联络异常与整改")
        if skipped:
            limitations.append("当前授权或数据不足，未返回：" + "、".join(skipped))
        analysis = _analysis(project, profile, records, tasks, design, assemblies, trials, contacts)
        return {
            "resolution": "RESOLVED",
            "data": [
                {
                    "project": _project_card(db, user, project, alternatives or ("项目定位",)),
                    "profile": profile,
                    "project_plans": records["project_plan"],
                    "plan_changes": records["plan_change"],
                    "design_routes": design["design_routes"],
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
