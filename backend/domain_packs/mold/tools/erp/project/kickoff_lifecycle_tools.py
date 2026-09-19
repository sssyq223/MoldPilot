from collections import defaultdict

from pydantic import Field, ValidationError, model_validator
from sqlalchemy import select

from domain_packs.mold import models as m
from domain_packs.mold.authorization import access, predicate, select_fields
from domain_packs.mold.ports.db import now
from domain_packs.mold.ports.errors import DomainError
from domain_packs.mold.ports.schemas import StrictModel


class ProjectKickoffContextInput(StrictModel):
    project_id: str | None = Field(default=None, min_length=1, max_length=36)
    identifier: str | None = Field(
        default=None,
        min_length=1,
        max_length=200,
        description="项目编号、项目名称或当前权限内的项目线索。",
    )

    @model_validator(mode="after")
    def one_locator(self):
        if bool(self.project_id) == bool(self.identifier):
            raise ValueError("project_id 和 identifier 须且只能填写一项")
        if self.identifier:
            self.identifier = self.identifier.strip()
            if not self.identifier:
                raise ValueError("项目线索不能为空")
        return self


def parse(arguments):
    try:
        return ProjectKickoffContextInput.model_validate(arguments or {})
    except ValidationError as error:
        raise DomainError(
            "INVALID_TOOL_INPUT",
            "项目启动链路参数无效：" + error.errors()[0]["msg"],
        ) from None


def _strength(value, needle):
    if value is None:
        return 0
    value = str(value).casefold()
    needle = str(needle).casefold()
    return 100 if value == needle else 50 if needle in value else 0


def _project_card(db, user, project, matched_by=()):
    fields = access(db, user, "project.read", {"project_id": project.id}).fields
    card = select_fields(
        {
            "id": project.id,
            "code": project.code,
            "name": project.name,
            "status": project.status,
            "row_version": project.row_version,
        },
        fields,
    )
    card["matched_by"] = sorted(set(matched_by))
    return card


def _resolve(db, user, data):
    gate = predicate(db, user, "project.read", {"project_id": m.Project.id})
    rows = list(db.scalars(select(m.Project).where(gate).order_by(m.Project.code).limit(501)))
    visible = rows[:500]
    truncated = len(rows) > 500
    by_id = {project.id: project for project in visible}
    if data.project_id:
        project = by_id.get(data.project_id)
        return project, ([] if project else None), truncated

    scores = defaultdict(int)
    reasons = defaultdict(list)
    for project in visible:
        for value, label in (
            (project.id, "项目ID"),
            (project.code, "项目编号"),
            (project.name, "项目名称"),
        ):
            score = _strength(value, data.identifier)
            if score:
                scores[project.id] = max(scores[project.id], score)
                reasons[project.id].append(label)
    if not scores:
        return None, [], truncated
    best = max(scores.values())
    ids = [project_id for project_id, score in scores.items() if score == best]
    if len(ids) != 1:
        return (
            None,
            [_project_card(db, user, by_id[project_id], reasons[project_id]) for project_id in ids[:20]],
            truncated,
        )
    return by_id[ids[0]], reasons[ids[0]], truncated


def _first_row(result):
    rows = result.get("data") if isinstance(result, dict) else None
    return rows[0] if isinstance(rows, list) and rows and isinstance(rows[0], dict) else None


def _subject_fact(row):
    if not isinstance(row, dict):
        return None
    detail = row.get("detail") if isinstance(row.get("detail"), dict) else {}
    return {
        "id": row.get("id"),
        "number": row.get("number"),
        "status": row.get("status"),
        "revision": row.get("revision"),
        "decision": detail.get("decision"),
        "contract_number": detail.get("contract_number"),
    }


def _unavailable_stage(key, name, query_tool):
    return {
        "key": key,
        "name": name,
        "state": "UNAVAILABLE",
        "query_tool": query_tool,
        "action_tool": None,
        "facts": {},
        "blockers": ["当前会话未分配本阶段查询能力，未读取也未推断该阶段业务事实。"],
    }


def _acceptance_stage(row, allowed_tools):
    if row is None:
        return _unavailable_stage("acceptance", "承接确认", "query_quote_acceptance_context")
    acceptance = row.get("latest_acceptance")
    rejection = row.get("latest_rejection")
    pending = row.get("open_quote_decisions") or []
    workflows = row.get("workflow_options") or []
    blockers = []
    if acceptance and rejection:
        state = "DATA_CONFLICT"
        blockers.append("同时存在有效承接与有效拒单记录，需要人工核对有效性。")
    elif rejection:
        state = "REJECTED"
        blockers.append("项目已有有效拒单决定，不能继续普通开工链路。")
    elif acceptance:
        state = "COMPLETED"
    elif pending:
        state = "WAITING_APPROVAL"
    elif workflows and "prepare_quote_acceptance_decision" in allowed_tools:
        state = "READY"
    else:
        state = "NOT_STARTED"
        blockers.append("尚无有效承接/拒单决定或可用审批流程。")
    return {
        "key": "acceptance",
        "name": "承接确认",
        "state": state,
        "query_tool": "query_quote_acceptance_context",
        "action_tool": "prepare_quote_acceptance_decision" if state == "READY" else None,
        "facts": {
            "latest_acceptance": _subject_fact(acceptance),
            "latest_rejection": _subject_fact(rejection),
            "pending_count": len(pending),
            "workflow_count": len(workflows),
        },
        "blockers": blockers,
    }


def _contract_stage(row, allowed_tools):
    if row is None:
        return _unavailable_stage("contract", "销售合同", "query_contract_context")
    contracts = row.get("sales_contracts") or []
    effective = [item for item in contracts if item.get("status") == "EFFECTIVE"]
    pending = [item for item in contracts if item.get("status") in {
        "DRAFT", "SUBMITTED", "RETURNED", "APPLY_BLOCKED"
    }]
    workflows = (row.get("workflow_options") or {}).get("sales_contract") or []
    if effective:
        state = "COMPLETED"
    elif pending:
        state = "WAITING_APPROVAL"
    elif workflows and "prepare_contract_record" in allowed_tools:
        state = "READY"
    else:
        state = "NOT_STARTED"
    blockers = []
    if state == "NOT_STARTED":
        blockers.append("尚无有效销售合同或可用合同登记审批流程。")
    return {
        "key": "contract",
        "name": "销售合同",
        "state": state,
        "query_tool": "query_contract_context",
        "action_tool": "prepare_contract_record" if state == "READY" else None,
        "parallel": True,
        "facts": {
            "effective_contract": _subject_fact(effective[0]) if effective else None,
            "pending_count": len(pending),
            "history_count": len(contracts),
            "late_expected_count": len(row.get("late_expected_contracts") or []),
            "workflow_count": len(workflows),
        },
        "blockers": blockers,
    }


def _start_stage(row, allowed_tools, acceptance_state):
    if row is None:
        return _unavailable_stage("internal_start", "正式开工", "query_internal_start_readiness")
    readiness = row.get("readiness") or {}
    latest = row.get("latest_internal_start")
    pending = row.get("open_start_requests") or []
    workflows = row.get("workflow_options") or []
    blockers = list(readiness.get("known_blockers") or [])
    if readiness.get("has_effective_internal_start") or latest:
        state = "COMPLETED"
    elif pending:
        state = "WAITING_APPROVAL"
    elif readiness.get("can_prepare_start_from_known_facts") and workflows and "prepare_internal_start" in allowed_tools:
        state = "READY"
    elif acceptance_state in {"REJECTED", "DATA_CONFLICT"} or not readiness.get("has_effective_acceptance"):
        state = "BLOCKED"
        if not blockers:
            blockers.append("尚无有效承接决定。")
    else:
        state = "NOT_STARTED"
    return {
        "key": "internal_start",
        "name": "正式开工",
        "state": state,
        "query_tool": "query_internal_start_readiness",
        "action_tool": "prepare_internal_start" if state == "READY" else None,
        "facts": {
            "latest_internal_start": _subject_fact(latest),
            "pending_count": len(pending),
            "workflow_count": len(workflows),
            "project_status": readiness.get("project_status"),
            "can_prepare": bool(readiness.get("can_prepare_start_from_known_facts")),
        },
        "blockers": blockers,
    }


def _plan_stage(row, allowed_tools, start_state, project_status):
    if row is None:
        return _unavailable_stage("project_plan", "项目基线计划", "query_project_plan_context")
    analysis = row.get("analysis") or {}
    derived = analysis.get("derived_status") or {}
    active = analysis.get("active_plan")
    pending = [item for item in (row.get("project_plans") or []) if item.get("status") in {
        "DRAFT", "SUBMITTED", "RETURNED", "APPLY_BLOCKED"
    }]
    workflows = row.get("baseline_workflow_options") or []
    blockers = []
    if derived.get("has_effective_plan") or active:
        state = "ACTIVE"
    elif pending:
        state = "WAITING_APPROVAL"
    elif project_status == "ACTIVE" and workflows and "prepare_project_plan_baseline" in allowed_tools:
        state = "READY"
    elif start_state != "COMPLETED" or project_status != "ACTIVE":
        state = "BLOCKED"
        blockers.append("项目尚未正式开工，不能提交基线计划审批。")
    else:
        state = "NOT_STARTED"
        blockers.append("尚无有效基线计划或可用计划审批流程。")
    return {
        "key": "project_plan",
        "name": "项目基线计划",
        "state": state,
        "query_tool": "query_project_plan_context",
        "action_tool": "prepare_project_plan_baseline" if state == "READY" else None,
        "facts": {
            "active_plan": _subject_fact(active),
            "pending_count": len(pending),
            "workflow_count": len(workflows),
            "task_count": len(analysis.get("tasks") or []),
            "missing_milestones": (analysis.get("milestone_coverage") or {}).get("missing") or [],
        },
        "blockers": blockers,
    }


def _recommendations(stages, allowed_tools):
    by_key = {stage["key"]: stage for stage in stages}
    result = []
    acceptance = by_key["acceptance"]
    contract = by_key["contract"]
    start = by_key["internal_start"]
    plan = by_key["project_plan"]

    if acceptance["state"] not in {"COMPLETED", "REJECTED", "DATA_CONFLICT"}:
        tool = acceptance.get("action_tool") or (
            "query_quote_acceptance_context" if "query_quote_acceptance_context" in allowed_tools else None
        )
        if tool:
            result.append({
                "kind": "PRIMARY",
                "stage": "acceptance",
                "tool": tool,
                "reason": "先完成或核对承接决定；承接未生效前不能正式开工。",
                "requires_user_confirmation": tool.startswith("prepare_"),
            })
    elif acceptance["state"] == "COMPLETED" and start["state"] != "COMPLETED":
        tool = start.get("action_tool") or (
            "query_internal_start_readiness" if "query_internal_start_readiness" in allowed_tools else None
        )
        if tool:
            result.append({
                "kind": "PRIMARY",
                "stage": "internal_start",
                "tool": tool,
                "reason": "承接已生效，下一主线是核对并正式下达内部开工。",
                "requires_user_confirmation": tool.startswith("prepare_"),
            })
    elif start["state"] == "COMPLETED" and plan["state"] != "ACTIVE":
        tool = plan.get("action_tool") or (
            "query_project_plan_context" if "query_project_plan_context" in allowed_tools else None
        )
        if tool:
            result.append({
                "kind": "PRIMARY",
                "stage": "project_plan",
                "tool": tool,
                "reason": "正式开工已生效，下一主线是建立并审批项目基线计划。",
                "requires_user_confirmation": tool.startswith("prepare_"),
            })

    if contract["state"] not in {"COMPLETED", "UNAVAILABLE"}:
        tool = contract.get("action_tool") or (
            "query_contract_context" if "query_contract_context" in allowed_tools else None
        )
        if tool:
            result.append({
                "kind": "PARALLEL",
                "stage": "contract",
                "tool": tool,
                "reason": "合同可以并行补齐；合同晚到不自动阻塞已满足条件的开工，但须保留核对事项。",
                "requires_user_confirmation": tool.startswith("prepare_"),
            })
    return result


def query(db, user, data: ProjectKickoffContextInput, allowed_tools: set[str]):
    project, alternatives, truncated = _resolve(db, user, data)
    limitations = [
        "只读取当前用户具备项目读取权限的项目；每个业务阶段还必须同时具备对应查询工具与业务权限。",
        "本工具只生成项目启动链路投影和下一步建议，不承接、不登记合同、不正式开工、不创建计划。",
        "合同晚到不自动阻塞具备独立依据的正式开工；承接、合同、开工和计划仍是四类独立业务事实。",
    ]
    if truncated:
        limitations.append("最多检查前500个可见项目，结果可能未覆盖全部可见范围。")
    if project is None:
        if alternatives is None:
            resolution = "NOT_FOUND_OR_FORBIDDEN"
            data_rows = []
        elif alternatives:
            resolution = "MULTIPLE_CANDIDATES"
            data_rows = alternatives
            limitations.append("线索命中多个候选项目，请使用项目 ID 或更完整编号后再查询。")
        else:
            resolution = "NOT_FOUND"
            data_rows = []
        return {
            "resolution": resolution,
            "data": data_rows,
            "source": "agent_db",
            "as_of": now().isoformat(),
            "limitations": limitations,
        }

    project_id = project.id
    contexts = {}
    access_gaps = []

    if "query_quote_acceptance_context" in allowed_tools:
        from domain_packs.mold.tools.erp.commercial.quote_tools import QuoteContextInput, query as quote_query

        contexts["acceptance"] = _first_row(
            quote_query(db, user, QuoteContextInput(project_id=project_id), allowed_tools)
        )
    else:
        access_gaps.append("承接确认")

    if "query_contract_context" in allowed_tools:
        from domain_packs.mold.tools.erp.commercial.contract_tools import ContractContextInput, query as contract_query

        contexts["contract"] = _first_row(
            contract_query(db, user, ContractContextInput(project_id=project_id), allowed_tools)
        )
    else:
        access_gaps.append("销售合同")

    if "query_internal_start_readiness" in allowed_tools:
        from domain_packs.mold.tools.erp.project.start_tools import StartReadinessInput, query as start_query

        contexts["internal_start"] = _first_row(
            start_query(db, user, StartReadinessInput(project_id=project_id), allowed_tools)
        )
    else:
        access_gaps.append("正式开工")

    if "query_project_plan_context" in allowed_tools:
        from domain_packs.mold.erp.core.contracts import ProjectPlanContextInput
        from domain_packs.mold.tools.erp.project.plan_tools import query as plan_query

        contexts["project_plan"] = _first_row(
            plan_query(db, user, ProjectPlanContextInput(project_id=project_id), allowed_tools)
        )
    else:
        access_gaps.append("项目计划")

    acceptance = _acceptance_stage(contexts.get("acceptance"), allowed_tools)
    contract = _contract_stage(contexts.get("contract"), allowed_tools)
    start = _start_stage(contexts.get("internal_start"), allowed_tools, acceptance["state"])
    plan = _plan_stage(
        contexts.get("project_plan"),
        allowed_tools,
        start["state"],
        project.status,
    )
    stages = [acceptance, contract, start, plan]
    if acceptance["state"] == "REJECTED":
        phase = "REJECTED"
    elif acceptance["state"] != "COMPLETED":
        phase = "ACCEPTANCE"
    elif start["state"] != "COMPLETED":
        phase = "START_PREPARATION"
    elif plan["state"] != "ACTIVE":
        phase = "PLAN_APPROVAL"
    else:
        phase = "EXECUTION"

    lifecycle = {
        "kind": "project_kickoff_lifecycle_v1",
        "phase": phase,
        "stages": stages,
        "recommended_next_steps": _recommendations(stages, allowed_tools),
        "access_gaps": access_gaps,
        "guardrails": [
            "阶段查询缺失时显示 UNAVAILABLE，不根据其他阶段或历史对话猜测状态。",
            "prepare_* 只生成待确认建议；本人确认后才提交各自 Agent BPM。",
            "合同材料、承接决定、正式开工和项目计划分别保留版本与审批，不合并为一个状态字段。",
        ],
    }
    if access_gaps:
        limitations.append("未读取以下未分配阶段能力：" + "、".join(access_gaps) + "。")
    return {
        "resolution": "RESOLVED",
        "data": [{
            "project": _project_card(db, user, project, alternatives or ("项目定位",)),
            "analysis": {"kickoff_lifecycle": lifecycle},
        }],
        "source": "agent_db",
        "as_of": now().isoformat(),
        "limitations": limitations,
    }
