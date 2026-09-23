"""Quality claim and qualified submit after warehouse inbound."""
from __future__ import annotations

from typing import Any

from pydantic import Field, ValidationError, field_validator

from domain_packs.mold import models as m
from domain_packs.mold.authorization import fingerprint
from domain_packs.mold.erp.procurement.erp_outsource_http import put_erp
from domain_packs.mold.erp.procurement.erp_outsource_scope import require_allow
from domain_packs.mold.ports.bpm import content_hash
from domain_packs.mold.ports.confirmation_policy import proposal_confirmation_policy
from domain_packs.mold.ports.db import now
from domain_packs.mold.ports.errors import DomainError
from domain_packs.mold.ports.schemas import StrictModel
from domain_packs.mold.tools.erp.procurement.outsource_queries import quality_todo

TODO_TOOL = "query_erp_outsource_quality_tasks"
CLAIM_TOOL = "prepare_erp_outsource_quality_claim"
PASS_TOOL = "prepare_erp_outsource_quality_pass"
QUERY_TOOL_KEYS = (TODO_TOOL,)
PREPARE_TOOL_KEYS = (CLAIM_TOOL, PASS_TOOL)
TOOL_KEYS = QUERY_TOOL_KEYS + PREPARE_TOOL_KEYS

SKILL_SPECS = {
    "outsource_quality_ops": {
        "name": "委外质检领取与合格",
        "tools": [TODO_TOOL],
        "optional_tools": list(PREPARE_TOOL_KEYS),
        "activation_tools": list(QUERY_TOOL_KEYS),
        "activation_queries": [
            "领取质检", "质检任务", "检验合格", "质检合格", "质检待办", "有几个", "有没有",
        ],
        "auto_activation_queries": [
            "领取质检", "质检任务", "检验合格", "质检合格", "质检待办", "有几个", "有没有",
        ],
        "priority_patterns": ["领取质检|质检任务|检验合格|质检合格|质检待办|有几个|有没有"],
        "requires_tool_evidence": True,
        "suppress_tool_search_on_auto_activation": True,
    },
}

TOOL_SPECS = {
    TODO_TOOL: {
        "description": "只读查询委外回厂入库后的质检待办：待领取、质检中。不含厂内工序质检。",
        "permission": "erp_outsource_quality.read",
    },
    CLAIM_TOOL: {
        "description": "准备领取质检任务。必须已锁定 taskId，且任务仍是 pending。本人确认后才写入 ERP。",
        "permission": "erp_outsource_quality.execute",
    },
    PASS_TOOL: {
        "description": "准备提交全检合格。必须已锁定 taskId。ERP 支持从待领取或质检中提交；待领取时会同时记录领取人。本人确认后才写入 ERP。",
        "permission": "erp_outsource_quality.execute",
    },
}

TOOL_NAMES = {
    TODO_TOOL: "查询委外质检待办",
    CLAIM_TOOL: "准备领取质检任务",
    PASS_TOOL: "准备提交质检合格",
}


class QualityTodoInput(StrictModel):
    question: str | None = Field(default=None, max_length=500)
    mold: str | None = Field(default=None, max_length=40)

    @field_validator("question", "mold")
    @classmethod
    def strip_text(cls, value):
        return value.strip() or None if isinstance(value, str) else value


class QualityTaskInput(StrictModel):
    task_id: int = Field(ge=1, description="查询结果中的 taskId。")
    mold: str | None = Field(default=None, max_length=40)
    remark: str | None = Field(default=None, max_length=400)

    @field_validator("mold", "remark")
    @classmethod
    def strip_text(cls, value):
        return value.strip() or None if isinstance(value, str) else value


INPUT_MODELS = {
    TODO_TOOL: QualityTodoInput,
    CLAIM_TOOL: QualityTaskInput,
    PASS_TOOL: QualityTaskInput,
}
KIND_BY_TOOL = {
    CLAIM_TOOL: "erp_outsource_quality_claim",
    PASS_TOOL: "erp_outsource_quality_pass",
}
ACTION_BY_TOOL = {
    CLAIM_TOOL: "confirm_erp_outsource_quality_claim",
    PASS_TOOL: "confirm_erp_outsource_quality_pass",
}


def tool_schema(key: str) -> dict[str, Any]:
    return {
        "type": "function",
        "function": {
            "name": key,
            "description": TOOL_SPECS[key]["description"],
            "parameters": INPUT_MODELS[key].model_json_schema(),
        },
    }


def parse(key: str, arguments: dict | None):
    try:
        return INPUT_MODELS[key].model_validate(arguments or {})
    except ValidationError as error:
        raise DomainError("INVALID_TOOL_INPUT", "质检办理参数无效：" + error.errors()[0]["msg"]) from None


def _require_read(db, user) -> None:
    require_allow(db, user, "erp_outsource_quality.read", "仅委外质检可查询质检待办")


def _require_execute(db, user) -> None:
    require_allow(db, user, "erp_outsource_quality.execute", "仅委外质检可领取或提交质检")


def execute_query(db, user, arguments: dict | None, run=None) -> dict[str, Any]:
    _require_read(db, user)
    data = parse(TODO_TOOL, arguments)
    question = data.question or str(getattr(run, "prompt", "") or "")
    parsed = quality_todo.parse_question(" ".join(part for part in (question, data.mold) if part))
    return {
        "data": quality_todo.run(parsed),
        "source": "management-system ERP 委外质检只读查询",
        "as_of": now().isoformat(),
        "limitations": ["只读。仅委外回厂入库质检。不合格/退货本期不办。"],
    }


def _require_task(task_id: int) -> dict[str, Any]:
    item = quality_todo.find_task(task_id)
    if not item:
        raise DomainError("NOT_FOUND", "没有找到待领取或质检中的委外质检任务", 404)
    return item


def preview(db, user, key: str, data) -> tuple[dict[str, Any], dict[str, Any]]:
    item = _require_task(data.task_id)
    status = item.get("status")
    if key == CLAIM_TOOL and status != "pending":
        if status == "inspecting":
            raise DomainError("STATE_BLOCKED", "该任务已被领取，请办提交合格", 409)
        raise DomainError("STATE_BLOCKED", "当前状态不能领取", 409)
    if key == PASS_TOOL and status not in {"pending", "inspecting"}:
        raise DomainError("STATE_BLOCKED", "当前状态不能提交合格", 409)
    details = "、".join(
        f"{line.get('partNo') or line.get('inboundDetailId')}×{line.get('inboundQty')}"
        for line in item.get("details") or []
    )
    display = {
        "质检单": item.get("inspectionNo") or item.get("taskId"),
        "入库单": item.get("inboundNo") or item.get("inboundId") or "未标注",
        "工单": item.get("orderNo") or item.get("orderId") or "未标注",
        "加工商": item.get("partnerName") or "未标注",
        "仓库": item.get("warehouse") or item.get("inboundTargetLabel") or "未标注",
        "操作": "领取质检" if key == CLAIM_TOOL else "提交全检合格",
        "明细": details or "无明细",
        "说明": (
            "本人确认后写入 ERP。领取后由同一人提交合格。"
            if key == CLAIM_TOOL
            else "本人确认后按全检合格写入 ERP；若尚未领取，ERP 会同时记录当前人为领取人。"
        ),
    }
    return item, display


def execute_tool(db, user, key: str, arguments: dict | None, run=None) -> dict[str, Any]:
    if key == TODO_TOOL:
        return execute_query(db, user, arguments, run=run)
    _require_execute(db, user)
    data = parse(key, arguments)
    _, display = preview(db, user, key, data)
    return {
        "data": [],
        "source": "agent_proposal",
        "as_of": now().isoformat(),
        "proposal": {
            "kind": KIND_BY_TOOL[key],
            "action": ACTION_BY_TOOL[key],
            "requires_approval": False,
            "input": data.model_dump(mode="json"),
            "display": display,
            "confirmation_policy": proposal_confirmation_policy(run, requires_approval=False),
        },
        "limitations": ["仅准备质检办理；本人确认后才调用 ERP。不合格本期不办。"],
    }


def source(db, user, step_id):
    from domain_packs.mold.tool_gateway import available_tools

    step = db.get(m.Step, step_id)
    run = db.get(m.Run, step.run_id) if step else None
    if not run or run.user_id != user.id:
        raise DomainError("NOT_FOUND", "操作建议不存在或无权访问", 404)
    if run.status not in {"RUNNING", "RUNNING_SCOPED", "SUCCEEDED"}:
        raise DomainError("PROPOSAL_STOPPED", "任务已停止，请重新准备操作", 409)
    if run.security_version != user.security_version or run.checkpoint.get("authorization_hash") != fingerprint(db, user):
        raise DomainError("AUTHORIZATION_CHANGED", "授权已变化，请重新准备操作", 403)
    proposal = step.result.get("proposal")
    if step.tool not in available_tools(db, user) or step.tool not in PREPARE_TOOL_KEYS or not proposal:
        raise DomainError("TOOL_FORBIDDEN", "操作能力不可用", 403)
    return proposal


def validate_intent(db, user, payload):
    _require_execute(db, user)
    proposal = source(db, user, payload["step_id"])
    if content_hash(proposal) != payload["proposal_hash"]:
        raise DomainError("CONFIRMATION_INVALID", "操作建议内容已变化", 409)
    key = next((name for name, kind in KIND_BY_TOOL.items() if kind == proposal.get("kind")), None)
    if key is None:
        raise DomainError("CONFIRMATION_INVALID", "操作建议类型无效", 409)
    data = parse(key, proposal["input"])
    _, display = preview(db, user, key, data)
    if content_hash(display) != content_hash(proposal["display"]):
        raise DomainError("VERSION_CONFLICT", "质检任务状态已变化，请重新查询后准备", 409)
    return proposal, data, key


def confirm(db, user, payload):
    _, data, key = validate_intent(db, user, payload)
    if key == CLAIM_TOOL:
        result = put_erp(
            db, user, f"quality/inspection/{data.task_id}/claim", {},
            intent_id=payload.get("_intent_id"), action="quality_claim", native_id=f"quality-task:{data.task_id}",
        )
        return {
            "task_id": data.task_id,
            "action": "quality_claim",
            "status": "CONFIRMED",
            "erp": result,
            "nextHint": "已领取。下一步由同一质检员提交合格。",
        }
    result = put_erp(db, user, f"quality/inspection/{data.task_id}/submit", {
        "inspectionType": "full",
        "result": "qualified",
        "handlingAction": "inbound",
        "remark": data.remark,
        "details": [],
    }, intent_id=payload.get("_intent_id"), action="quality_pass", native_id=f"quality-task:{data.task_id}")
    return {
        "task_id": data.task_id,
        "action": "quality_pass",
        "status": "CONFIRMED",
        "erp": result,
        "nextHint": "质检合格已提交。合格数量进入对应成品库或半成品库存。",
    }
