from collections import defaultdict
from datetime import date
from decimal import Decimal
from typing import Literal

from pydantic import Field, ValidationError
from sqlalchemy import and_, select

from domain_packs.mold import models as m
from domain_packs.mold.authorization import access, fingerprint, predicate, require, select_fields
from domain_packs.mold.ports.db import now
from domain_packs.mold.ports.errors import DomainError
from domain_packs.mold.ports.files import uploaded_file
from domain_packs.mold.ports.schemas import StrictModel
from domain_packs.mold.erp.core.domain_commands import execute_command, validate_command
from domain_packs.mold.tools.erp.project.plan_tools import ProjectPlanContextInput, _strength


ASSEMBLY_TRIAL_PROPOSAL_TOOLS = frozenset(
    {"prepare_assembly_execution", "prepare_trial_result"}
)


class AssemblyExecutionProposalInput(StrictModel):
    project_id: str = Field(min_length=1, max_length=36)
    project_version: int = Field(ge=1)
    assembly_id: str = Field(min_length=1, max_length=36)
    action: Literal["START", "DONE"]
    actual_date: date
    evidence: str = Field(min_length=1, max_length=4000)


class TrialResultProposalInput(StrictModel):
    project_id: str = Field(min_length=1, max_length=36)
    project_version: int = Field(ge=1)
    trial_id: str = Field(min_length=1, max_length=36)
    passed: bool
    actual_date: date
    findings: str = Field(min_length=1, max_length=4000)
    change_id: str | None = Field(default=None, max_length=36)
    file_ids: list[str] = Field(default_factory=list, max_length=20)


def assembly_execution_schema():
    return AssemblyExecutionProposalInput.model_json_schema()


def trial_result_schema():
    return TrialResultProposalInput.model_json_schema()


def _parse_assembly_execution(arguments):
    try:
        data = AssemblyExecutionProposalInput.model_validate(arguments or {})
    except ValidationError as error:
        raise DomainError(
            "INVALID_TOOL_INPUT",
            "装配执行参数不完整或不符合要求：" + error.errors()[0]["msg"],
        ) from None
    if data.actual_date > now().date():
        raise DomainError("DATE_INVALID", "装配实际日期不能在未来")
    return data


def _parse_trial_result(arguments):
    try:
        data = TrialResultProposalInput.model_validate(arguments or {})
    except ValidationError as error:
        raise DomainError(
            "INVALID_TOOL_INPUT",
            "试模结论参数不完整或不符合要求：" + error.errors()[0]["msg"],
        ) from None
    if data.actual_date > now().date():
        raise DomainError("DATE_INVALID", "试模实际日期不能在未来")
    if len(data.file_ids) != len(set(data.file_ids)):
        raise DomainError("FILE_CONTEXT_INVALID", "试模报告附件不能重复", 409)
    if not data.passed and not data.change_id:
        raise DomainError("CHANGE_REQUIRED", "试模未通过时必须关联工程联络单", 409)
    return data


def _project_for_operation(db, user, project_id, project_version, permission):
    project = db.get(m.Project, project_id)
    if not project:
        raise DomainError("NOT_FOUND", "项目不存在", 404)
    require(db, user, "project.read", {"project_id": project.id})
    require(db, user, permission, {"project_id": project.id})
    if project.row_version != project_version:
        raise DomainError("VERSION_CONFLICT", "项目状态已变化，请重新查询后再准备操作", 409)
    if project.status != "ACTIVE":
        raise DomainError("PROJECT_BLOCKED", "项目当前不允许登记装配或试模执行", 409)
    return project


def _operation_files(db, user, file_ids, *, run=None, step_id=None):
    requested = [str(value) for value in (file_ids or [])]
    if len(requested) != len(set(requested)):
        raise DomainError("FILE_CONTEXT_INVALID", "试模报告附件不能重复", 409)
    if not requested:
        return []
    if run is None and step_id:
        step = db.get(m.Step, step_id)
        run = db.get(m.Run, step.run_id) if step else None
    if not run or run.user_id != user.id:
        raise DomainError("FILE_CONTEXT_INVALID", "试模报告附件须绑定当前本人任务", 403)
    blobs = []
    for file_id in requested:
        blob = uploaded_file(db, user, file_id)
        if blob.conversation_id != run.conversation_id:
            raise DomainError("FILE_CONTEXT_INVALID", "只能关联当前会话中的试模报告原件", 403)
        if not db.scalar(
            select(m.RunFile).where(
                m.RunFile.run_id == run.id,
                m.RunFile.file_id == blob.id,
            )
        ):
            raise DomainError("FILE_CONTEXT_INVALID", "试模报告附件尚未绑定当前任务", 403)
        blobs.append(blob)
    return blobs


def _display_files(display, blobs):
    if not blobs:
        return display
    return {
        **display,
        "试模报告原件": [
            {"id": blob.id, "filename": blob.filename, "sha256": blob.sha256}
            for blob in blobs
        ],
        "说明": display["说明"] + " 原件只作为不可变 Agent 附件登记，不替代 ERP 试模报告。",
    }


def preview_assembly_execution(db, user, data: AssemblyExecutionProposalInput):
    project = _project_for_operation(
        db, user, data.project_id, data.project_version, "assembly.execute"
    )
    subject = db.get(m.BusinessSubject, data.assembly_id)
    if (
        not subject
        or subject.project_id != project.id
        or subject.kind != "assembly_issue"
        or subject.status != "EFFECTIVE"
    ):
        raise DomainError("ASSEMBLY_NOT_FOUND", "装配任务不存在、未生效或不属于该项目", 404)
    detail = db.get(m.AssemblyDetail, subject.id)
    if not detail:
        raise DomainError("ASSEMBLY_DETAIL_MISSING", "装配任务明细不存在", 409)
    payload = {
        "action": data.action,
        "actual_date": data.actual_date.isoformat(),
        "evidence": data.evidence,
    }
    validate_command(db, user, "assembly.execute", subject.id, payload)
    if data.action == "START" and detail.execution_status != "NOT_STARTED":
        raise DomainError("ASSEMBLY_STATE", "当前装配状态不能开始", 409)
    if data.action == "DONE" and detail.execution_status != "RUNNING":
        raise DomainError("ASSEMBLY_STATE", "当前装配状态不能登记完工", 409)
    display = {
        "操作": "登记装配执行",
        "项目": f"{project.code} · {project.name}",
        "项目版本": project.row_version,
        "装配任务": subject.number,
        "执行动作": "装配开工" if data.action == "START" else "装配完工",
        "实际日期": data.actual_date.isoformat(),
        "钳工主管": detail.supervisor_id,
        "当前状态": detail.execution_status,
        "依据": data.evidence,
        "说明": "本人确认后仅登记 Agent 装配执行回执；实际装配工单和 ERP 执行仍以原系统为准。",
    }
    return project, subject, display


def preview_trial_result(db, user, data: TrialResultProposalInput, *, run=None, step_id=None):
    project = _project_for_operation(
        db, user, data.project_id, data.project_version, "trial.confirm"
    )
    subject = db.get(m.BusinessSubject, data.trial_id)
    if (
        not subject
        or subject.project_id != project.id
        or subject.kind != "trial_request"
        or subject.status != "EFFECTIVE"
    ):
        raise DomainError("TRIAL_NOT_FOUND", "试模申请不存在、未生效或不属于该项目", 404)
    detail = db.get(m.TrialDetail, subject.id)
    if not detail:
        raise DomainError("TRIAL_DETAIL_MISSING", "试模申请明细不存在", 409)
    payload = {
        "passed": data.passed,
        "actual_date": data.actual_date.isoformat(),
        "findings": data.findings,
        "change_id": data.change_id,
        "file_ids": list(data.file_ids),
        "evidence": data.findings,
    }
    validate_command(db, user, "trial.confirm", subject.id, payload)
    blobs = _operation_files(db, user, data.file_ids, run=run, step_id=step_id)
    if db.scalar(select(m.TrialResult.id).where(m.TrialResult.trial_id == subject.id)):
        raise DomainError("ALREADY_CONFIRMED", "试模结论已经登记；再次试模须重新申请", 409)
    display = {
        "操作": "登记试模结论",
        "项目": f"{project.code} · {project.name}",
        "项目版本": project.row_version,
        "试模申请": subject.number,
        "试模日期": data.actual_date.isoformat(),
        "结论": "通过" if data.passed else "未通过",
        "试模地点": detail.location,
        "整改工程联络": data.change_id or "不适用",
        "发现与结论": data.findings,
        "说明": "本人确认后仅登记 Agent 试模结论；通过不等于出厂放行，未通过必须继续整改复验。",
    }
    return project, subject, _display_files(display, blobs)


def execute_assembly_trial_tool(db, user, key, arguments, run=None):
    from domain_packs.mold.ports.confirmation_policy import proposal_confirmation_policy

    if key == "prepare_assembly_execution":
        data = _parse_assembly_execution(arguments)
        _, _, display = preview_assembly_execution(db, user, data)
        kind = "assembly_execution"
        action = "confirm_assembly_execution"
        limitation = (
            "仅准备装配执行回执登记建议；本人确认后才写入 Agent 事实，"
            "不替代 ERP 装配工单或现场执行。"
        )
    elif key == "prepare_trial_result":
        data = _parse_trial_result(arguments)
        _, _, display = preview_trial_result(db, user, data, run=run)
        kind = "trial_result"
        action = "confirm_trial_result"
        limitation = (
            "仅准备试模结论登记建议；本人确认后才写入 Agent 试模事实，"
            "通过不等于出厂放行，未通过必须继续整改复验。"
        )
    else:
        raise DomainError("TOOL_UNKNOWN", "工具未实现", 403)
    proposal = {
        "kind": kind,
        "action": action,
        "requires_approval": False,
        "input": data.model_dump(mode="json"),
        "display": display,
        "confirmation_policy": proposal_confirmation_policy(run, requires_approval=False),
    }
    return {
        "data": [],
        "source": "agent_proposal",
        "as_of": now().isoformat(),
        "proposal": proposal,
        "limitations": [limitation],
    }


def source(db, user, step_id):
    from domain_packs.mold.tool_gateway import available_tools

    step = db.get(m.Step, step_id)
    run = db.get(m.Run, step.run_id) if step else None
    if not run or run.user_id != user.id:
        raise DomainError("NOT_FOUND", "操作建议不存在或无权访问", 404)
    if run.status not in {"RUNNING", "RUNNING_SCOPED", "SUCCEEDED"}:
        raise DomainError("PROPOSAL_STOPPED", "任务已停止，请重新准备操作", 409)
    if run.security_version != user.security_version or run.checkpoint.get(
        "authorization_hash"
    ) != fingerprint(db, user):
        raise DomainError("AUTHORIZATION_CHANGED", "授权已变化，请重新准备操作", 403)
    proposal = step.result.get("proposal") if isinstance(step.result, dict) else None
    if step.tool not in available_tools(db, user) or step.tool not in ASSEMBLY_TRIAL_PROPOSAL_TOOLS or not proposal:
        raise DomainError("TOOL_FORBIDDEN", "操作能力不可用", 403)
    return proposal


def validate_intent(db, user, payload):
    from domain_packs.mold.ports.bpm import content_hash

    proposal = source(db, user, payload["step_id"])
    if content_hash(proposal) != payload["proposal_hash"]:
        raise DomainError("CONFIRMATION_INVALID", "操作建议内容已变化", 409)
    kind = proposal.get("kind")
    if kind == "assembly_execution":
        data = AssemblyExecutionProposalInput.model_validate(proposal["input"])
        _, _, display = preview_assembly_execution(db, user, data)
    elif kind == "trial_result":
        data = TrialResultProposalInput.model_validate(proposal["input"])
        blobs = _operation_files(db, user, data.file_ids, step_id=payload["step_id"])
        _, _, display = preview_trial_result(db, user, data, step_id=payload["step_id"])
        display = _display_files(display, blobs)
    else:
        raise DomainError("TOOL_FORBIDDEN", "操作建议类型不可用", 403)
    if content_hash(display) != content_hash(proposal["display"]):
        raise DomainError("VERSION_CONFLICT", "项目、任务或权限资料已变化，请重新准备", 409)
    return proposal, data


def confirm(db, user, payload):
    proposal, data = validate_intent(db, user, payload)
    if proposal["kind"] == "assembly_execution":
        receipt = execute_command(
            db,
            user,
            "assembly.execute",
            data.assembly_id,
            {
                "action": data.action,
                "actual_date": data.actual_date.isoformat(),
                "evidence": data.evidence,
            },
        )
        return {
            **receipt,
            "project_id": data.project_id,
            "action": "assembly_execution",
            "status": "CONFIRMED",
        }
    receipt = execute_command(
        db,
        user,
        "trial.confirm",
        data.trial_id,
        {
            "passed": data.passed,
            "actual_date": data.actual_date.isoformat(),
            "findings": data.findings,
            "change_id": data.change_id,
            "file_ids": list(data.file_ids),
            "evidence": data.findings,
        },
    )
    return {
        **receipt,
        "project_id": data.project_id,
        "action": "trial_result",
        "status": "CONFIRMED",
    }


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


def _assembly_readiness(db, user, project_id, design, trials, allowed_tools):
    """Project assembly inputs without creating a second inventory ledger.

    Purchase, receipt and stock values are read from the existing Agent
    records only when the caller has the corresponding permission.  Key-part
    policy and machine availability are deliberately reported as unconfigured
    or unverified until a real source/confirmation exists.
    """
    detail_rows = []
    seen = set()
    for route in design.get("bom_routes") or []:
        material_id = route.get("material_id")
        if not material_id or material_id in seen:
            continue
        seen.add(material_id)
        detail_rows.append(route)

    procurement_visible = (
        "query_procurement_price_context" in allowed_tools
        or "query_purchase_orders" in allowed_tools
        or access(db, user, "purchase.read", {"project_id": project_id}).allowed
    )
    warehouse_visible = (
        "query_delivery_logistics_context" in allowed_tools
        or access(db, user, "warehouse.read", {"project_id": project_id}).allowed
    )

    item_rows = []
    for route in detail_rows[:200]:
        material_id = route.get("material_id")
        required = Decimal(str(route.get("quantity") or "0"))
        shipped = received = accepted = Decimal("0")
        purchase_order_ids = set()
        if procurement_visible:
            order_lines = list(
                db.scalars(
                    select(m.OrderLine)
                    .join(m.PurchaseOrder, m.PurchaseOrder.id == m.OrderLine.order_id)
                    .where(
                        m.PurchaseOrder.project_id == project_id,
                        m.PurchaseOrder.status.in_(("ISSUED", "CLOSED")),
                        m.OrderLine.material_id == material_id,
                    )
                    .limit(200)
                )
            )
            for line in order_lines:
                purchase_order_ids.add(line.order_id)
                shipments = list(
                    db.scalars(
                        select(m.SupplierShipment)
                        .where(m.SupplierShipment.order_line_id == line.id)
                        .limit(200)
                    )
                )
                shipped += sum((Decimal(str(row.quantity)) for row in shipments), Decimal("0"))
                for shipment in shipments:
                    receipts = list(
                        db.scalars(
                            select(m.GoodsReceipt)
                            .where(m.GoodsReceipt.shipment_id == shipment.id)
                            .limit(200)
                        )
                    )
                    received += sum((Decimal(str(row.quantity)) for row in receipts), Decimal("0"))
                    for receipt in receipts:
                        inspection = db.scalar(
                            select(m.ReceiptInspection).where(
                                m.ReceiptInspection.receipt_id == receipt.id
                            )
                        )
                        if inspection:
                            accepted += Decimal(str(inspection.accepted_quantity))

        stock = Decimal("0")
        stock_rows = 0
        if warehouse_visible:
            for balance in db.scalars(
                select(m.StockBalance)
                .where(
                    m.StockBalance.project_id == project_id,
                    m.StockBalance.material_id == material_id,
                )
                .limit(200)
            ):
                if not access(
                    db, user, "warehouse.read", {"warehouse_id": balance.warehouse_id}
                ).allowed:
                    continue
                stock_rows += 1
                stock += Decimal(str(balance.quantity))

        if not procurement_visible and not warehouse_visible:
            quantity_state = "UNAVAILABLE"
        elif warehouse_visible and stock_rows:
            if stock >= required:
                quantity_state = "READY"
            elif stock > 0:
                quantity_state = "PARTIAL"
            else:
                quantity_state = "MISSING"
        elif procurement_visible and accepted >= required and accepted > 0:
            quantity_state = "RECEIVED_UNVERIFIED"
        elif procurement_visible and accepted > 0:
            quantity_state = "PARTIAL_UNVERIFIED"
        else:
            quantity_state = "UNVERIFIED"

        item_rows.append(
            {
                "material_id": material_id,
                "material_code": route.get("material_code"),
                "material_name": route.get("material_name"),
                "route": route.get("route"),
                "required_quantity": str(required),
                "ordered_quantity_visible": procurement_visible,
                "shipped_quantity": str(shipped) if procurement_visible else None,
                "received_quantity": str(received) if procurement_visible else None,
                "accepted_quantity": str(accepted) if procurement_visible else None,
                "stock_quantity": str(stock) if warehouse_visible else None,
                "purchase_order_count": len(purchase_order_ids) if procurement_visible else None,
                "quantity_state": quantity_state,
                "key_part_state": "UNCONFIGURED",
                "key_part_reason": "当前设计物料模型没有经确认的关键件标识，不能按物料名称或类别猜测。",
            }
        )

    quantity_states = [row["quantity_state"] for row in item_rows]
    explicit_shortage = any(state in {"MISSING", "PARTIAL"} for state in quantity_states)
    complete_quantity_read = bool(item_rows) and all(
        state in {"READY", "MISSING", "PARTIAL"} for state in quantity_states
    )
    if not item_rows:
        readiness_status = "NO_EFFECTIVE_BOM"
    elif explicit_shortage and complete_quantity_read:
        readiness_status = "BLOCKED"
    elif all(state == "READY" for state in quantity_states) and warehouse_visible:
        readiness_status = "READY_PENDING_KEY_PART_RULE"
    elif all(state == "UNAVAILABLE" for state in quantity_states):
        readiness_status = "UNAVAILABLE"
    else:
        readiness_status = "UNVERIFIED"

    trial_resources = []
    for trial in trials[:50]:
        planned = trial.get("planned_date")
        location = (trial.get("location") or "").strip()
        responsible = trial.get("responsible_id")
        declaration = "DECLARED" if planned and location and responsible else "INCOMPLETE"
        trial_resources.append(
            {
                "trial_request_id": trial.get("id"),
                "trial_number": trial.get("number"),
                "planned_date": planned,
                "location": location or None,
                "responsible_id": responsible,
                "declaration_state": declaration,
                "availability_state": "UNVERIFIED",
                "availability_reason": "当前模型未连接机台租赁/内部机台占用或资源排程回执。",
            }
        )

    report_evidence = []
    for trial in trials[:50]:
        for result in trial.get("results") or []:
            evidence = (result.get("evidence") or "").strip()
            attachments = result.get("attachments") or []
            report_evidence.append(
                {
                    "trial_request_id": trial.get("id"),
                    "trial_number": trial.get("number"),
                    "trial_result_id": result.get("id"),
                    "passed": result.get("passed"),
                    "actual_date": result.get("actual_date"),
                    "evidence_text": evidence or None,
                    "attachment_state": "VERIFIED" if attachments else "UNAVAILABLE",
                    "attachment_count": len(attachments),
                    "attachments": attachments,
                    "attachment_reason": (
                        "已关联当前试模结论的报告原件。"
                        if attachments
                        else "试模结果当前只保存证据文本，没有与 FileObject 建立正式附件关联。"
                    ),
                    "report_state": (
                        "ATTACHED"
                        if attachments
                        else "TEXT_EVIDENCE_ONLY"
                        if evidence
                        else "MISSING"
                    ),
                }
            )

    return {
        "readiness_status": readiness_status,
        "quantity_scope": {
            "item_count": len(item_rows),
            "quantity_source": (
                "agent_stock_balance"
                if warehouse_visible
                else "agent_receipt_inspection"
                if procurement_visible
                else "NONE"
            ),
            "warehouse_visible": warehouse_visible,
            "procurement_visible": procurement_visible,
            "threshold_configured": False,
            "threshold_reason": "70%至80%仅是需求参考范围，当前没有项目/客户确认的阈值和统计范围。",
        },
        "items": item_rows,
        "trial_resources": trial_resources,
        "trial_report_evidence": report_evidence,
        "key_part_policy": {
            "state": "UNCONFIGURED",
            "reason": "未见经过业务确认的关键件清单或关键件规则。",
        },
        "limitations": [
            "齐套数量只从当前权限可见的 Agent 采购、收货、检验和库存事实汇总，不复制 ERP 台账。",
            "没有项目/客户确认的齐套阈值、关键件规则或机台资源回执时，状态保持 UNVERIFIED/UNCONFIGURED。",
            "试模报告的证据文本不等于附件原件；当前未把试模结果直接绑定为 FileObject。",
        ],
    }


def _assembly_trial_handoffs(
    *,
    has_design_route,
    has_assembly_order,
    has_assembly_done,
    has_trial_request,
    has_trial_result,
    has_trial_passed,
    has_trial_failed,
    has_blocked_plan_dependency,
):
    """Expose the handoff boundaries from design/BOM through trial readiness.

    These are evidence projections only.  They do not create an assembly
    order, mark a task complete, or treat a passed trial as outbound release.
    """
    if not has_design_route:
        design_to_assembly = {
            "state": "BLOCKED",
            "reason": "未见当前生效设计 BOM/路线，不能判断装配输入是否齐套。",
        }
    elif has_assembly_done:
        design_to_assembly = {
            "state": "CONNECTED",
            "reason": "设计 BOM/路线已与装配完工事实形成可核对交接。",
        }
    elif has_assembly_order:
        design_to_assembly = {
            "state": "ACTIVE",
            "reason": "已见生效装配任务，仍需装配完工和齐套回执。",
        }
    else:
        design_to_assembly = {
            "state": "READY",
            "reason": "设计 BOM/路线已具备，等待生效装配任务承接。",
        }

    if has_blocked_plan_dependency or not has_assembly_done:
        assembly_to_trial = {
            "state": "BLOCKED",
            "reason": (
                "装配前置依赖尚未完成，不能进入试模。"
                if has_blocked_plan_dependency
                else "未见装配完工和齐套确认，不能进入试模。"
            ),
        }
    elif has_trial_failed:
        assembly_to_trial = {
            "state": "NEEDS_ATTENTION",
            "reason": "试模已有未通过结果，需整改并重新验证。",
        }
    elif has_trial_passed:
        assembly_to_trial = {
            "state": "CONNECTED",
            "reason": "装配完工已与试模通过结果形成可核对交接。",
        }
    elif has_trial_request:
        assembly_to_trial = {
            "state": "ACTIVE",
            "reason": "已见试模申请，等待试模执行和结论。",
        }
    else:
        assembly_to_trial = {
            "state": "READY",
            "reason": "装配已完成，等待试模申请和资源安排。",
        }

    if not has_trial_result:
        trial_to_delivery = {
            "state": "BLOCKED",
            "reason": "未见试模报告或结论，不能进入出厂放行与交付核对。",
        }
    elif has_trial_failed:
        trial_to_delivery = {
            "state": "NEEDS_ATTENTION",
            "reason": "试模未通过，需完成整改复验后才能交给交付放行。",
        }
    else:
        trial_to_delivery = {
            "state": "READY",
            "reason": "试模已通过，下一步应核对出厂自检/放行和发运事实。",
        }

    return [
        {
            "key": "design_to_assembly",
            "from": "design_bom",
            "to": "assembly",
            **design_to_assembly,
            "next_query_tool": "query_assembly_trial_context",
        },
        {
            "key": "assembly_to_trial",
            "from": "assembly",
            "to": "trial",
            **assembly_to_trial,
            "next_query_tool": "query_assembly_trial_context",
        },
        {
            "key": "trial_to_delivery_release",
            "from": "trial",
            "to": "delivery_release",
            **trial_to_delivery,
            "next_query_tool": "query_delivery_logistics_context",
        },
    ]


def _analysis(project, profile, records, tasks, design, assemblies, trials, contacts, erp_execution_context, assembly_readiness):
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
    active_plan = _active_plan(records)
    plan_detail = active_plan.get("detail") if isinstance(active_plan, dict) else {}
    plan_tasks = (plan_detail.get("tasks") or []) if isinstance(plan_detail, dict) else []
    plan_by_key = {
        str(row.get("key")): row
        for row in plan_tasks
        if isinstance(row, dict) and str(row.get("key") or "").strip()
    }
    dependency_blocked_tasks = []
    for task in tasks:
        missing = [
            str(key)
            for key in (task.get("prerequisites") or [])
            if (plan_by_key.get(str(key)) or {}).get("status") != "DONE"
        ]
        if missing:
            dependency_blocked_tasks.append({
                "id": task.get("id"),
                "key": task.get("key"),
                "name": task.get("name"),
                "waiting_for": missing,
                "task_status": task.get("status"),
            })
    erp_records = (erp_execution_context or {}).get("records") or {}
    erp_orders = erp_records.get("manufacturing_orders") or []
    erp_reports = (erp_records.get("work_reports") or []) + (erp_records.get("work_order_reports") or [])
    erp_quality = erp_records.get("quality_inspections") or []
    relevant_erp_rows = [row for row in erp_orders + erp_reports if any(
        keyword.casefold() in (str(row.get("operation_id") or "") + " " + str(row.get("operation_name") or "") + " " + str(row.get("face_detail") or "")).casefold()
        for keyword in ASSEMBLY_KEYWORDS + TRIAL_KEYWORDS
    )]
    erp_quality_failed = [
        row for row in erp_quality
        if str(row.get("result") or "").lower() in {"unqualified", "partial"}
        or str(row.get("status") or "").lower() in {"partial", "reject_return"}
    ]
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
    if dependency_blocked_tasks:
        warnings.append("装配或试模计划节点存在未完成前置依赖；不能把已创建的装配/试模记录当作前置条件已满足。")
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
    if relevant_erp_rows:
        warnings.append("ERP 原系统存在装配/试模相关工序或报工事实；需按原系统工单和正式试模资料核对，不以 Agent 计划节点替代。")
    if erp_quality_failed:
        warnings.append("ERP 存在装配/试模相关质检不合格或部分合格结果，需关联工程联络、整改责任任务和复验依据。")
    if open_contacts:
        warnings.append("存在未关闭装配/试模/质量工程联络事项，不能认定异常闭环完成。")
    if purchase_routes:
        warnings.append("存在采购路线物料；装配齐套仍需采购到货、检验和发料依据。")
    if internal_or_outsource and not design["design_routes"]:
        warnings.append("存在制造/委外路线线索但缺设计明细，无法核对装配前置。")
    if not relevant_erp_rows:
        gaps.append("未见独立齐套率、关键件齐套口径或 ERP 齐套检查回执；不得仅凭计划节点判断可装配。")
    gaps.append("未见出厂自检合格资料、试模报告附件解析或客户验收依据；通过后仍需正式资料闭环。")
    if assembly_readiness["readiness_status"] == "BLOCKED":
        warnings.append("当前可见库存/检验事实显示至少一项装配物料未达到需求数量；齐套不足时不能认定装配条件满足。")
    elif assembly_readiness["readiness_status"] in {"UNVERIFIED", "UNAVAILABLE"}:
        gaps.append("装配物料齐套数量或权限范围不足，当前不能确认实际齐套率。")
    elif assembly_readiness["readiness_status"] == "READY_PENDING_KEY_PART_RULE":
        warnings.append("当前可见库存数量覆盖设计 BOM，但关键件规则和正式齐套阈值尚未配置，不能直接替代钳工主管确认。")
    if assembly_readiness["key_part_policy"]["state"] != "CONFIGURED":
        gaps.append("未见经确认的关键件清单或规则；总体数量覆盖不能替代关键件条件。")
    if assembly_readiness["trial_resources"] and any(
        row["availability_state"] != "VERIFIED"
        for row in assembly_readiness["trial_resources"]
    ):
        gaps.append("试模计划已登记但机台租赁/内部资源可用性没有正式回执。")
    if assembly_readiness["trial_report_evidence"] and any(
        row["attachment_state"] != "VERIFIED"
        for row in assembly_readiness["trial_report_evidence"]
    ):
        gaps.append("试模结果只有文本证据或缺少报告附件关联，不能视为报告原件已归档。")
    assembly_trial_handoffs = _assembly_trial_handoffs(
        has_design_route=bool(design["design_routes"]),
        has_assembly_order=bool(effective_assemblies),
        has_assembly_done=bool(done_assemblies),
        has_trial_request=bool(effective_trials),
        has_trial_result=bool(trial_results),
        has_trial_passed=bool(passed_trials),
        has_trial_failed=bool(failed_trials),
        has_blocked_plan_dependency=bool(dependency_blocked_tasks),
    )

    return {
        "active_plan": _active_plan(records),
        "assembly_trial_plan_tasks": tasks[:50],
        "dependency_blocked_tasks": dependency_blocked_tasks[:50],
        "assembly_tasks": assembly_tasks[:20],
        "trial_tasks": trial_tasks[:20],
        "design_bom_routes": design["bom_routes"][:100],
        "assembly_orders": assemblies,
        "trial_requests": trials,
        "assembly_or_trial_contacts": contacts,
        "assembly_readiness": assembly_readiness,
        "assembly_trial_handoffs": assembly_trial_handoffs,
        "erp_manufacturing_execution": erp_execution_context,
        "erp_quality_inspections": erp_quality,
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
            "has_blocked_plan_dependency": bool(dependency_blocked_tasks),
            "has_open_assembly_or_trial_issue": bool(open_contacts),
            "has_erp_assembly_or_trial_fact": bool(relevant_erp_rows),
            "has_erp_work_report": bool(erp_reports),
            "has_erp_quality_inspection": bool(erp_quality),
            "has_erp_quality_failure": bool(erp_quality_failed),
            "assembly_trial_handoff_states": {
                row["key"]: row["state"] for row in assembly_trial_handoffs
            },
            "assembly_readiness_status": assembly_readiness["readiness_status"],
            "has_key_part_policy": assembly_readiness["key_part_policy"]["state"] == "CONFIGURED",
            "has_verified_trial_resource": any(
                row["availability_state"] == "VERIFIED"
                for row in assembly_readiness["trial_resources"]
            ),
            "has_trial_report_attachment": any(
                row["attachment_state"] == "VERIFIED"
                for row in assembly_readiness["trial_report_evidence"]
            ),
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
        assembly_readiness = _assembly_readiness(
            db, user, project.id, design, trials, allowed_tools
        )
        from domain_packs.mold.erp.design.erp_progress import query_project_manufacturing_execution
        erp_execution_context = query_project_manufacturing_execution(db, user, project)
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
        analysis = _analysis(
            project,
            profile,
            records,
            tasks,
            design,
            assemblies,
            trials,
            contacts,
            erp_execution_context,
            assembly_readiness,
        )
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
