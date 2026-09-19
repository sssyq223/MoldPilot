from pydantic import Field, ValidationError, model_validator

from domain_packs.mold.ports.db import now
from domain_packs.mold.ports.errors import DomainError
from domain_packs.mold.ports.schemas import StrictModel
from domain_packs.mold.tools.erp.project.kickoff_lifecycle_tools import (
    _first_row,
    _project_card,
    _resolve,
)


class ProjectLifecycleContextInput(StrictModel):
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
            note = f"并行事项“{item.get('name') or item.get('key')}”尚未完成。"
            if note not in blockers:
                blockers.append(note)

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
        "blockers": blockers,
        "access_gaps": list(lifecycle.get("access_gaps") or []),
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
    if project.status == "ACTIVE" or lifecycles["kickoff"].get("phase") == "EXECUTION":
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
    return {
        "resolution": "RESOLVED",
        "data": [{
            "project": _project_card(db, user, project, alternatives or ("项目定位",)),
            "analysis": {"project_lifecycle": overview},
        }],
        "source": "agent_db",
        "as_of": now().isoformat(),
        "limitations": limitations,
    }
