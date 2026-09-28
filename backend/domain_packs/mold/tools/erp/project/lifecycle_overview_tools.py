from domain_packs.mold.erp.core.project_locator import ProjectId
from pydantic import Field, ValidationError, model_validator

from domain_packs.mold.ports.db import now
from domain_packs.mold.ports.errors import DomainError
from domain_packs.mold.ports.schemas import StrictModel
from domain_packs.mold.tools.erp.project.kickoff_lifecycle_tools import (
    KICKOFF_PROPOSAL_TOOLS,
    _first_row,
    _project_card,
    _resolve,
)
from domain_packs.mold.tools.erp.project.completion_lifecycle_tools import COMPLETION_PROPOSAL_TOOLS
from domain_packs.mold.tools.erp.project.execution_lifecycle_tools import EXECUTION_PROPOSAL_TOOLS
from domain_packs.mold.tools.erp.project.project_control_tools import PROJECT_CONTROL_PROPOSAL_TOOLS


_BASELINE_MILESTONE_LABELS = {
    "design": "设计工艺分析/结构设计及出图",
    "purchase": "原材料/五金/委外采购",
    "machining": "工序加工",
    "assembly": "装配",
    "trial": "试模/调试",
    "delivery": "最终交付/出库/验收",
}


class ProjectLifecycleContextInput(StrictModel):
    project_id: ProjectId | None = Field(default=None)
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
        return ProjectLifecycleContextInput.model_validate(arguments or {})
    except ValidationError as error:
        raise DomainError(
            "INVALID_TOOL_INPUT",
            "项目全生命周期参数无效：" + error.errors()[0]["msg"],
        ) from None


SEGMENTS = (
    ("kickoff", "启动与基线", "query_project_kickoff_context", "kickoff_lifecycle"),
    ("execution", "项目执行", "query_project_execution_context", "execution_lifecycle"),
    ("completion", "交付结算与关闭", "query_project_completion_context", "completion_lifecycle"),
)

KICKOFF_FOCUS = {
    "REJECTED": ("acceptance", "承接确认", "REJECTED"),
    "QUOTATION": ("quotation", "客户报价", None),
    "ACCEPTANCE": ("acceptance", "承接确认", None),
    "START_PREPARATION": ("internal_start", "正式开工", None),
    "PLAN_APPROVAL": ("project_plan", "项目基线计划", None),
    "EXECUTION": ("completed", "启动与基线", "COMPLETED"),
}


def _lifecycle(row, key):
    analysis = row.get("analysis") if isinstance(row, dict) else None
    value = analysis.get(key) if isinstance(analysis, dict) else None
    return value if isinstance(value, dict) else {}


def _focus_stage(lifecycle, segment_key):
    stages = lifecycle.get("stages") or []
    if segment_key != "kickoff":
        focus = lifecycle.get("current_focus")
        if isinstance(focus, dict):
            return focus
        return {"key": "visibility", "name": "链路可见性", "state": "UNAVAILABLE"}

    focus_key, focus_name, forced_state = KICKOFF_FOCUS.get(
        lifecycle.get("phase"),
        ("visibility", "启动链路可见性", "UNAVAILABLE"),
    )
    stage = next((item for item in stages if item.get("key") == focus_key), None)
    state = forced_state or (stage or {}).get("state") or "UNAVAILABLE"
    return {"key": focus_key, "name": focus_name, "state": state}


def _segment_summary(segment_key, name, query_tool, lifecycle):
    stages = [item for item in lifecycle.get("stages") or [] if isinstance(item, dict)]
    focus = _focus_stage(lifecycle, segment_key)
    states = [item.get("state") for item in stages]
    if stages and all(state == "UNAVAILABLE" for state in states):
        state = "UNAVAILABLE"
    elif "DATA_CONFLICT" in states:
        state = "DATA_CONFLICT"
    elif focus.get("key") == "completed":
        state = "COMPLETED"
    else:
        state = focus.get("state") or "UNAVAILABLE"

    focus_stage = next((item for item in stages if item.get("key") == focus.get("key")), None)
    blockers = list((focus_stage or {}).get("blockers") or [])
    baseline_stage = next((item for item in stages if item.get("key") == "baseline_plan"), None)
    baseline_facts = (baseline_stage or {}).get("facts") or {}
    required_milestones = list(baseline_facts.get("required_milestones") or [])
    missing_milestones = list(baseline_facts.get("missing_milestones") or [])
    baseline_coverage = {
        "baseline_exists": bool(baseline_facts.get("active_plan")),
        "required_for_baseline": [
            {"key": key, "label": _BASELINE_MILESTONE_LABELS.get(key, key)}
            for key in required_milestones
        ],
        "missing_from_active_plan": [
            {"key": key, "label": _BASELINE_MILESTONE_LABELS.get(key, key)}
            for key in missing_milestones
        ],
        "note": (
            "没有生效基线计划时，required_for_baseline 是提交前必须补齐的六类大节点；"
            "不要把它改写成已存在计划的缺失节点。"
        ),
    }
    parallel_follow_ups = []
    for item in stages:
        if item is focus_stage or item.get("state") not in {"DATA_CONFLICT", "NEEDS_ATTENTION"}:
            continue
        for blocker in item.get("blockers") or []:
            if blocker not in blockers:
                blockers.append(blocker)
    if segment_key == "kickoff":
        for item in stages:
            if not item.get("parallel") or item.get("state") in {"COMPLETED", "UNAVAILABLE"}:
                continue
            reasons = item.get("follow_ups") or [
                f"并行事项“{item.get('name') or item.get('key')}”尚未完成。"
            ]
            parallel_follow_ups.extend({
                "stage": item.get("key"),
                "name": item.get("name") or item.get("key"),
                "reason": reason,
            } for reason in reasons if isinstance(reason, str) and reason.strip())

    completed_states = {"COMPLETED"}
    if segment_key == "kickoff":
        completed_states.add("ACTIVE")
    return {
        "key": segment_key,
        "name": name,
        "state": state,
        "phase": lifecycle.get("phase"),
        "focus": focus,
        "query_tool": query_tool,
        "progress": {
            "stage_count": len(stages),
            "completed_count": sum(item.get("state") in completed_states for item in stages),
            "not_applicable_count": sum(item.get("state") == "NOT_APPLICABLE" for item in stages),
            "unavailable_count": sum(item.get("state") == "UNAVAILABLE" for item in stages),
        },
        "stage_statuses": [
            {
                "key": item.get("key"),
                "name": item.get("name"),
                "state": item.get("state"),
            }
            for item in stages
            if item.get("key") and item.get("name")
        ],
        "blockers": blockers,
        "baseline_coverage": baseline_coverage,
        "access_gaps": list(lifecycle.get("access_gaps") or []),
        "parallel_follow_ups": parallel_follow_ups,
    }


def _completion_started(project, lifecycle):
    if project.status in {"TERMINATED", "CLOSED"}:
        return True
    stages = {item.get("key"): item for item in lifecycle.get("stages") or [] if isinstance(item, dict)}
    final_facts = (stages.get("final_close") or {}).get("facts") or {}
    if final_facts.get("closure_case_status"):
        return True
    for key in ("delivery_acceptance", "customer_finance", "supplier_settlement"):
        if (stages.get(key) or {}).get("state") in {"ACTIVE", "COMPLETED", "NEEDS_ATTENTION"}:
            return True
    return False


def _current_segment(project, summaries, lifecycles):
    by_key = {item["key"]: item for item in summaries}
    if project.status == "PAUSED":
        return {
            "key": "project_control",
            "name": "项目暂停与恢复",
            "state": "PAUSED",
            "phase": "PAUSED",
            "focus": {"key": "pause_resume", "name": "暂停期间限制与恢复条件", "state": "PAUSED"},
            "query_tool": "query_project_control_context",
        }
    if _completion_started(project, lifecycles["completion"]):
        return by_key["completion"]
    # ACTIVE is only a coarse project projection.  Keep the user in the
    # kickoff segment until the kickoff coordinator has evidence that the
    # formal-start and baseline-plan handoff is complete; otherwise the
    # execution segment hides the actual plan handoff gap.
    kickoff_phase = lifecycles["kickoff"].get("phase")
    if kickoff_phase == "EXECUTION":
        return by_key["execution"]
    return by_key["kickoff"]


def _consistency_warnings(project, summaries, lifecycles):
    warnings = []
    kickoff = lifecycles["kickoff"]
    completion = lifecycles["completion"]
    by_key = {item["key"]: item for item in summaries}
    if project.status in {"ACTIVE", "PAUSED"} and kickoff.get("phase") != "EXECUTION":
        warnings.append("项目状态已进入执行或暂停，但启动链路尚未证明承接、正式开工和基线计划全部完成；须核对缺失或冲突依据。")
    if project.status == "DRAFT" and by_key["execution"]["state"] in {"ACTIVE", "COMPLETED", "NEEDS_ATTENTION"}:
        warnings.append("项目仍为草稿，但执行链路已出现进行中或完成事实；不得据此倒推正式开工已生效。")
    if project.status == "CLOSED" and by_key["completion"]["state"] != "COMPLETED":
        warnings.append("项目状态为已关闭，但当前可见收尾证据未证明全部阶段完成；须核对关闭应用回执与权限范围。")
    if project.status == "TERMINATED" and completion.get("closure_mode") != "TERMINATION":
        warnings.append("项目状态为已终止，但当前可见收尾资料未形成终止结算分支；须核对终止清单。")
    return warnings


def _recommendation(current, allowed_tools):
    tool = current.get("query_tool")
    if not tool or tool not in allowed_tools:
        return []
    return [{
        "kind": "PRIMARY",
        "segment": current.get("key"),
        "tool": tool,
        "reason": "先展开当前生命周期分段的真实阶段事实与阻塞项，再进入对应专用业务能力。",
        "requires_user_confirmation": False,
    }]


def _business_chain(project, summaries, lifecycles, allowed_tools):
    """Expose cross-coordinator handoffs without inventing a business state.

    The lifecycle coordinators deliberately keep their own domain rules.  This
    projection only answers whether the next coordinator can be entered from
    the facts already returned by those coordinators.  It never writes a
    subject, advances a project, or treats an unavailable read as a failure.
    """
    by_key = {item["key"]: item for item in summaries}
    kickoff = lifecycles["kickoff"]
    execution = lifecycles["execution"]
    completion = lifecycles["completion"]

    kickoff_state = by_key["kickoff"]["state"]
    if kickoff_state == "UNAVAILABLE":
        kickoff_to_execution = "UNAVAILABLE"
        kickoff_reason = "启动分段未分配或无权读取，不能判断是否已交接到执行。"
    elif kickoff_state == "COMPLETED":
        kickoff_to_execution = "CONNECTED"
        kickoff_reason = "启动分段已完成，执行分段可以继续核对。"
    else:
        kickoff_to_execution = "WAITING"
        kickoff_reason = "承接、正式开工或基线计划尚未形成完成证据。"

    execution_state = by_key["execution"]["state"]
    completion_started = _completion_started(project, completion)
    if execution_state == "UNAVAILABLE":
        execution_to_completion = "UNAVAILABLE"
        execution_reason = "执行分段未分配或无权读取，不能判断交付与结算是否已接入。"
    elif completion_started:
        execution_to_completion = "CONNECTED"
        execution_reason = "已见交付、验收、财务、供应商结算或关闭分支事实，收尾分段已接入。"
    elif execution_state == "COMPLETED":
        execution_to_completion = "READY"
        execution_reason = "执行分段已完成，尚未见收尾事实；可进入交付与结算核对。"
    else:
        execution_to_completion = "WAITING"
        execution_reason = "执行分段尚未完成，不能把局部制造或采购事实当作交付结算完成。"

    completion_state = by_key["completion"]["state"]
    final_stage = next(
        (stage for stage in completion.get("stages") or [] if stage.get("key") == "final_close"),
        None,
    )
    if completion_state == "UNAVAILABLE":
        completion_to_close = "UNAVAILABLE"
        completion_reason = "收尾分段未分配或无权读取，不能判断最终关闭条件。"
    elif final_stage and final_stage.get("state") == "COMPLETED":
        completion_to_close = "CONNECTED"
        completion_reason = "收尾分段已提供最终关闭完成证据。"
    else:
        completion_to_close = "WAITING"
        completion_reason = "交付、客户结算、供应商结算、异常和归档仍需分别核对，不能由单项完成代替关闭。"

    def handoff(key, source, target, state, reason, tool):
        return {
            "key": key,
            "from": source,
            "to": target,
            "state": state,
            "reason": reason,
            "next_query_tool": tool if tool in allowed_tools else None,
            "query_available": tool in allowed_tools,
        }

    return [
        handoff(
            "kickoff_to_execution",
            "kickoff",
            "execution",
            kickoff_to_execution,
            kickoff_reason,
            "query_project_execution_context",
        ),
        handoff(
            "execution_to_completion",
            "execution",
            "completion",
            execution_to_completion,
            execution_reason,
            "query_project_completion_context",
        ),
        handoff(
            "completion_to_close",
            "completion",
            "closed",
            completion_to_close,
            completion_reason,
            "query_project_closure_context",
        ),
    ]


def _model_context(project_card, overview):
    return {
        "project": project_card,
        "project_lifecycle": {
            "kind": overview.get("kind"),
            "project_status": overview.get("project_status"),
            "current_segment": overview.get("current_segment"),
            "segments": overview.get("segments") or [],
            "recommended_next_steps": overview.get("recommended_next_steps") or [],
            "business_chain": overview.get("business_chain") or [],
            "consistency_warnings": overview.get("consistency_warnings") or [],
            "access_gaps": overview.get("access_gaps") or [],
        },
    }


def _boundary_write_tools(current):
    if current.get("key") == "kickoff":
        return KICKOFF_PROPOSAL_TOOLS
    if current.get("key") == "execution":
        return EXECUTION_PROPOSAL_TOOLS
    if current.get("key") == "completion":
        return COMPLETION_PROPOSAL_TOOLS
    if current.get("key") == "project_control":
        return PROJECT_CONTROL_PROPOSAL_TOOLS
    return frozenset()


def query(db, user, data: ProjectLifecycleContextInput, allowed_tools: set[str]):
    project, alternatives, truncated = _resolve(db, user, data)
    limitations = [
        "只读取当前用户可见项目，并按启动、执行、收尾三个既有协调器的权限边界生成摘要。",
        "本工具不复制阶段业务逻辑，不承接、不登记合同、不下达开工或计划、不执行 ERP 动作、不关闭项目。",
        "总览只决定应展开的生命周期分段；正式结论仍以分段协调器及专用工具返回的来源、版本和回执为准。",
    ]
    if truncated:
        limitations.append("最多检查前500个可见项目，结果可能未覆盖全部可见范围。")
    if project is None:
        if alternatives is None:
            resolution, rows = "NOT_FOUND_OR_FORBIDDEN", []
        elif alternatives:
            resolution, rows = "MULTIPLE_CANDIDATES", alternatives
            limitations.append("线索命中多个候选项目，请使用项目 ID 或更完整编号后再查询。")
        else:
            resolution, rows = "NOT_FOUND", []
        return {
            "resolution": resolution,
            "data": rows,
            "source": "agent_db",
            "as_of": now().isoformat(),
            "limitations": limitations,
        }

    project_id = project.id
    from domain_packs.mold.tools.erp.project.kickoff_lifecycle_tools import (
        ProjectKickoffContextInput,
        query as kickoff_query,
    )
    from domain_packs.mold.tools.erp.project.execution_lifecycle_tools import (
        ProjectExecutionContextInput,
        query as execution_query,
    )
    from domain_packs.mold.tools.erp.project.completion_lifecycle_tools import (
        ProjectCompletionContextInput,
        query as completion_query,
    )

    rows = {
        "kickoff": _first_row(kickoff_query(
            db, user, ProjectKickoffContextInput(project_id=project_id), allowed_tools
        )),
        "execution": _first_row(execution_query(
            db, user, ProjectExecutionContextInput(project_id=project_id), allowed_tools
        )),
        "completion": _first_row(completion_query(
            db, user, ProjectCompletionContextInput(project_id=project_id), allowed_tools
        )),
    }
    lifecycles = {
        segment_key: _lifecycle(rows[segment_key], analysis_key)
        for segment_key, _, _, analysis_key in SEGMENTS
    }
    summaries = [
        _segment_summary(segment_key, name, query_tool, lifecycles[segment_key])
        for segment_key, name, query_tool, _ in SEGMENTS
    ]
    current = _current_segment(project, summaries, lifecycles)
    business_chain = _business_chain(project, summaries, lifecycles, allowed_tools)
    access_gaps = [
        {"segment": item["key"], "items": item["access_gaps"]}
        for item in summaries if item["access_gaps"]
    ]
    overview = {
        "kind": "project_lifecycle_overview_v1",
        "project_status": project.status,
        "current_segment": current,
        "segments": summaries,
        "recommended_next_steps": _recommendation(current, allowed_tools),
        "business_chain": business_chain,
        "consistency_warnings": _consistency_warnings(project, summaries, lifecycles),
        "access_gaps": access_gaps,
        "guardrails": [
            "总览、启动、执行、收尾和阶段专用能力逐层展开，避免一次向模型暴露全部业务工具。",
            "后续阶段事实不能覆盖前序缺口；项目状态与分段证据不一致时显示资料矛盾，不做自然语言兜底。",
            "UNAVAILABLE 表示该阶段能力未分配或无权读取，不等同于未发生；NOT_APPLICABLE 必须由分段协调器的明确业务依据产生。",
            "所有协调器只读；prepare_* 仍须本人确认，审批生效和领域应用回执才代表业务事实改变。",
        ],
    }
    if access_gaps:
        limitations.append("部分分段存在未分配能力，已在 access_gaps 中逐段列出，未据此推断隐藏事实。")
    project_card = _project_card(db, user, project, alternatives or ("项目定位",))
    recommendations = overview["recommended_next_steps"]
    scope_boundary = {
        "complete": True,
        "scope_key": "project_lifecycle",
        "write_tools": sorted(
            tool for tool in _boundary_write_tools(current) if tool in allowed_tools
        ),
    }
    continuation_read_tools = sorted({
        recommendation["tool"]
        for recommendation in recommendations
        if recommendation.get("tool") in allowed_tools
        and not recommendation.get("tool", "").startswith("prepare_")
    })
    if continuation_read_tools:
        scope_boundary["read_tools"] = continuation_read_tools
    return {
        "resolution": "RESOLVED",
        "data": [{
            "project": project_card,
            "analysis": {"project_lifecycle": overview},
        }],
        "model_context": _model_context(project_card, overview),
        "scope_boundary": scope_boundary,
        "source": "agent_db",
        "as_of": now().isoformat(),
        "limitations": limitations,
    }
