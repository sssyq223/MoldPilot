"""Outsource order approval: query pending tasks, then prepare pass/reject."""
from __future__ import annotations

import re
from typing import Any, Literal

from pydantic import Field, ValidationError, field_validator, model_validator

from domain_packs.mold import models as m
from domain_packs.mold.authorization import fingerprint
from domain_packs.mold.erp.procurement.erp_outsource_http import post_erp
from domain_packs.mold.erp.procurement.erp_outsource_scope import (
    approval_node_tokens,
    require_allow,
)
from domain_packs.mold.ports.bpm import content_hash
from domain_packs.mold.ports.confirmation_policy import proposal_confirmation_policy
from domain_packs.mold.ports.db import now
from domain_packs.mold.ports.errors import DomainError
from domain_packs.mold.ports.schemas import StrictModel
from domain_packs.mold.tools.erp.procurement.outsource_identity import (
    CamelModel,
    identity_batch,
    identity_mold,
    identity_order_no,
)
from domain_packs.mold.tools.erp.procurement.outsource_queries import approval_todo, buyer_todo
from domain_packs.mold.tools.erp.procurement.outsource_queries.buyer_todo import identity_display

TODO_TOOL = "query_erp_outsource_approval_todos"
PASS_TOOL = "prepare_erp_outsource_approval_pass"
REJECT_TOOL = "prepare_erp_outsource_approval_reject"
QUERY_TOOL_KEYS = (TODO_TOOL,)
PREPARE_TOOL_KEYS = (PASS_TOOL, REJECT_TOOL)
TOOL_KEYS = QUERY_TOOL_KEYS + PREPARE_TOOL_KEYS

SKILL_SPECS = {
    "outsource_approval_ops": {
        "name": "委外下单审批",
        "tools": [TODO_TOOL],
        "optional_tools": [PASS_TOOL, REJECT_TOOL],
        "activation_tools": list(QUERY_TOOL_KEYS),
        "activation_queries": [
            "下单审批", "委外审批", "审批中", "待我审批", "通过这单", "驳回这单",
            "通过", "驳回", "审批通过", "审批驳回",
            "采购主管审批", "总经理审批", "审批待办", "查看待办", "查待办",
        ],
        "auto_activation_queries": [
            "下单审批", "委外审批", "待我审批", "采购主管审批", "总经理审批",
            "审批待办", "现在有审批吗", "查看待办", "查待办",
            "通过这单", "驳回这单", "审批通过", "审批驳回",
        ],
        "priority_patterns": [
            "下单审批|委外审批|待我审批|采购主管审批|总经理审批|审批待办|现在有审批|通过这单|驳回这单|查看待办",
        ],
        "requires_tool_evidence": True,
        "activation_route": "authorized",
        "suppress_tool_search_on_auto_activation": False,
        "host_auto_invoke_empty_arguments": True,
    },
}

TOOL_SPECS = {
    TODO_TOOL: {
        "description": "只读查询 ERP 委外下单审批待办。采购主管只看主管节点，总经理只看总经理节点。有模具号则过滤该模具。",
        "permission": "erp_outsource_approval.read",
    },
    PASS_TOOL: {
        "description": "准备通过一张委外下单审批。用查询结果中的表格 NO. 或订单号定位，必要时加模具号、批次号。本节点只有一条待办时可省略。当前节点必须仍是本角色待办。禁止使用内部数字 id。本人确认后才调用 ERP。",
        "permission": "erp_outsource_approval.approve",
    },
    REJECT_TOOL: {
        "description": "准备驳回一张委外下单审批。用表格 NO. 或订单号定位，必要时加模具号、批次号，并写明驳回原因。本节点只有一条待办时可省略订单号。禁止使用内部数字 id。本人确认后才调用 ERP。",
        "permission": "erp_outsource_approval.approve",
    },
}

TOOL_NAMES = {
    TODO_TOOL: "查询委外下单审批待办",
    PASS_TOOL: "准备通过委外下单审批",
    REJECT_TOOL: "准备驳回委外下单审批",
}


class ApprovalTodoInput(StrictModel):
    question: str | None = Field(default=None, max_length=500)
    mold: str | None = Field(default=None, max_length=40)


class ApprovalPassInput(CamelModel):
    order_no: str | None = identity_order_no(default=None, max_length=80, description="查询结果中的订单号，例如 EO-260922-2RHX。表格 NO. 或本节点只有一条待办时可省略。禁止使用内部数字 id。")
    mold: str | None = identity_mold(default=None, max_length=40, description="模具号，例如 M260063。")
    batch: str | None = identity_batch(default=None, max_length=80, description="批次号，例如 M260063-P1。同一订单有多批次时必填。")
    board_row: int | None = Field(default=None, ge=1, description="审批待办第一列 NO。说第N行或 NO.N 时填这个。")
    comment: str = Field(default="同意", max_length=400)

    @field_validator("order_no", "mold", "batch")
    @classmethod
    def strip_identity(cls, value):
        return value.strip() or None if isinstance(value, str) else value


class ApprovalRejectInput(CamelModel):
    order_no: str | None = identity_order_no(default=None, max_length=80, description="查询结果中的订单号，例如 EO-260922-2RHX。表格 NO. 或本节点只有一条待办时可省略。禁止使用内部数字 id。")
    mold: str | None = identity_mold(default=None, max_length=40, description="模具号，例如 M260063。")
    batch: str | None = identity_batch(default=None, max_length=80, description="批次号，例如 M260063-P1。同一订单有多批次时必填。")
    board_row: int | None = Field(default=None, ge=1, description="审批待办第一列 NO。说第N行或 NO.N 时填这个。")
    comment: str = Field(default="驳回", max_length=400, description="驳回原因。")

    @field_validator("order_no", "mold", "batch", "comment")
    @classmethod
    def strip_identity(cls, value):
        return value.strip() or None if isinstance(value, str) else value

    @model_validator(mode="after")
    def require_comment(self):
        if not self.comment:
            raise ValueError("请写明驳回原因")
        return self


INPUT_MODELS = {
    TODO_TOOL: ApprovalTodoInput,
    PASS_TOOL: ApprovalPassInput,
    REJECT_TOOL: ApprovalRejectInput,
}

KIND_BY_TOOL = {
    PASS_TOOL: "erp_outsource_approval_pass",
    REJECT_TOOL: "erp_outsource_approval_reject",
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
        raise DomainError("INVALID_TOOL_INPUT", "委外审批参数无效：" + error.errors()[0]["msg"]) from None


def _require_read(db, user) -> None:
    require_allow(db, user, "erp_outsource_approval.read", "仅委外审批人可查询下单审批待办")


def _require_approve(db, user) -> None:
    require_allow(db, user, "erp_outsource_approval.approve", "仅委外审批人可通过或驳回下单审批")


def _task_allowed(item: dict[str, Any], tokens: list[str] | None) -> bool:
    if tokens is None:
        return True
    if not tokens:
        return False
    name = str(item.get("nodeName") or "")
    return any(token in name for token in tokens)


LOOK_UP = ("有几个", "有哪些", "有没有", "查询", "查看待办", "查待办", "看看待办")
PASS_SPEECH = (
    "通过这单", "通过这张", "审批通过", "批过", "同意这单",
    "过了这单", "这单过了", "这单先过了",
)
REJECT_SPEECH = ("驳回这单", "驳回这张", "审批驳回", "不同意这单")
REJECT_COMMENT = re.compile(r"(?:驳回(?:这单|这张)?|原因)[：:，,\s]*(.+)$")


def is_spoken_approval_reject(text: str) -> bool:
    prompt = text or ""
    if any(token in prompt for token in ("不要驳回", "不驳回")):
        return False
    if any(token in prompt for token in REJECT_SPEECH):
        return True
    return "驳回" in prompt and not any(token in prompt for token in LOOK_UP)


def is_spoken_approval_pass(text: str) -> bool:
    prompt = text or ""
    if is_spoken_approval_reject(prompt):
        return False
    if any(token in prompt for token in ("不要通过", "不通过", "未通过")):
        return False
    if any(token in prompt for token in PASS_SPEECH):
        return True
    return "通过" in prompt


def _spoken_reject_comment(text: str) -> str:
    match = REJECT_COMMENT.search(text or "")
    comment = (match.group(1) if match else "").strip()
    if comment and comment not in {"这单", "这张", "吧", "了", "了吧"}:
        return comment[:400]
    return "驳回"


def spoken_approval_arguments(prompt: str, context_text: str = "") -> dict[str, Any] | None:
    text = prompt or ""
    if not (is_spoken_approval_pass(text) or is_spoken_approval_reject(text)):
        return None
    source = f"{text}\n{context_text or ''}"
    arguments: dict[str, Any] = {
        "comment": _spoken_reject_comment(text) if is_spoken_approval_reject(text) else "同意",
    }
    row_no = buyer_todo.spoken_board_row_number(text)
    if row_no:
        arguments["board_row"] = row_no
    order = buyer_todo.ORDER_NO.search(source)
    if order:
        arguments["order_no"] = order.group(1).upper()
    parsed = approval_todo.parse_question(source)
    if parsed.get("mold_batch"):
        arguments["batch"] = parsed["mold_batch"]
        arguments["mold"] = parsed["mold_batch"].split("-P", 1)[0]
    elif parsed.get("mold_family"):
        arguments["mold"] = parsed["mold_family"]
    return arguments


def spoken_approval_invoke(
    prompt: str,
    context_text: str = "",
    active_tool_names=None,
) -> tuple[str, dict[str, Any]] | None:
    arguments = spoken_approval_arguments(prompt, context_text)
    if arguments is None:
        return None
    names = set(active_tool_names or ())
    tool = REJECT_TOOL if is_spoken_approval_reject(prompt or "") else PASS_TOOL
    if tool not in names:
        return None
    return tool, arguments


def spoken_approval_from_board(prompt: str, items, active_tool_names):
    invoked = spoken_approval_invoke(prompt, "", active_tool_names)
    if invoked is None:
        return None
    tool, arguments = invoked
    visible = [item for item in (items or []) if isinstance(item, dict)]
    row = arguments.get("board_row")
    if row and 1 <= int(row) <= len(visible):
        item = visible[int(row) - 1]
    elif arguments.get("order_no"):
        return tool, arguments
    elif len(visible) == 1:
        item = visible[0]
    else:
        return tool, arguments
    if item.get("orderNo"):
        arguments["order_no"] = item["orderNo"]
    if item.get("moldFamily") or item.get("mold"):
        arguments.setdefault("mold", item.get("moldFamily") or item.get("mold"))
    if item.get("moldBatch") or item.get("batch"):
        arguments.setdefault("batch", item.get("moldBatch") or item.get("batch"))
    return tool, arguments


def _lookup(db, user, data) -> dict[str, Any]:
    tokens = approval_node_tokens(db, user)
    row = getattr(data, "board_row", None)
    if row:
        item = approval_todo.item_from_visible_row(int(row), node_tokens=tokens)
        if not item:
            raise DomainError("NOT_FOUND", "没有找到该行仍待审的委外下单审批，请先查看待办后再用表格 NO. 指定", 404)
    elif getattr(data, "order_no", None) or getattr(data, "mold", None) or getattr(data, "batch", None):
        item = approval_todo.find_task_by_identity(
            order_no=getattr(data, "order_no", None),
            mold=getattr(data, "mold", None),
            batch=getattr(data, "batch", None),
            node_tokens=tokens,
        )
        if not item:
            raise DomainError("NOT_FOUND", "没有找到仍待审的委外下单审批，请用订单号、模具号和批次号重新查询", 404)
    else:
        items = approval_todo.query_items(node_tokens=tokens)
        if len(items) > 1:
            raise DomainError("AMBIGUOUS", "本节点有多张审批待办，请说表格 NO. 或订单号", 409)
        if not items:
            raise DomainError("NOT_FOUND", "没有找到仍待审的委外下单审批，请先查看待办", 404)
        item = items[0]
    if not _task_allowed(item, tokens):
        raise DomainError("FORBIDDEN", "当前节点不是本角色可批的委外下单审批", 403)
    return item


def preview(db, user, key: str, data) -> tuple[dict[str, Any], dict[str, Any]]:
    item = _lookup(db, user, data)
    action = "通过" if key == PASS_TOOL else "驳回"
    display = {
        "操作": f"{action}委外下单审批",
        "当前节点": item.get("nodeName") or "",
        **identity_display(item),
        "加工商": item.get("supplierName") or "",
        "金额": item.get("amount"),
        "审批意见": data.comment,
        "说明": "本人确认后调用 ERP 工作流。主管通过后还要总经理批；总经理通过后加工商才能接单。驳回后采购员重新定标。",
    }
    return item, display


def execute_tool(db, user, key: str, arguments: dict | None, run=None) -> dict[str, Any]:
    data = parse(key, arguments)
    if key == TODO_TOOL:
        _require_read(db, user)
        question = data.question or str(getattr(run, "prompt", "") or "")
        if data.mold and data.mold not in question:
            question = f"{data.mold} {question}".strip()
        payload = approval_todo.run(
            node_tokens=approval_node_tokens(db, user),
            question=question,
        )
        items = [item for item in (payload.get("items") or []) if isinstance(item, dict)]
        return {
            "data": payload,
            "model_context": {
                "summary": payload.get("summary") or "",
                "nodeLens": payload.get("nodeLens") or [],
                "item_count": len(items),
                "items": [
                    {
                        "station": "审批中",
                        "stationLabel": "审批中",
                        "orderNo": item.get("orderNo") or "",
                        "mold": item.get("moldFamily") or item.get("moldNo") or "",
                        "batch": item.get("moldBatch") or item.get("moldNo") or "",
                        "node": item.get("nodeName") or "",
                        "assignee": item.get("assigneeName") or "",
                    }
                    for item in items[:30]
                ],
            },
            "source": "management-system ERP 委外下单审批只读查询",
            "as_of": now().isoformat(),
            "limitations": ["只读查询 ERP 工作流待办，不改数据。采购主管与总经理看到的是各自节点。"],
        }
    _require_approve(db, user)
    _, display = preview(db, user, key, data)
    proposal = {
        "kind": KIND_BY_TOOL[key],
        "action": "approve" if key == PASS_TOOL else "reject",
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
        "limitations": [
            "仅准备审批建议；本人确认后才调用 ERP。",
        ],
    }


def source(db, user, step_id):
    from domain_packs.mold.tool_gateway import available_tools

    step = db.get(m.Step, step_id)
    run = db.get(m.Run, step.run_id) if step else None
    if not run or run.user_id != user.id:
        raise DomainError("NOT_FOUND", "操作建议不存在或无权访问", 404)
    if run.status not in {"RUNNING", "RUNNING_SCOPED", "SUCCEEDED", "FAILED"}:
        raise DomainError("PROPOSAL_STOPPED", "任务已停止，请重新准备操作", 409)
    if run.security_version != user.security_version or run.checkpoint.get("authorization_hash") != fingerprint(db, user):
        raise DomainError("AUTHORIZATION_CHANGED", "授权已变化，请重新准备操作", 403)
    proposal = step.result.get("proposal")
    if step.tool not in available_tools(db, user) or step.tool not in PREPARE_TOOL_KEYS or not proposal:
        raise DomainError("TOOL_FORBIDDEN", "操作能力不可用", 403)
    return proposal


def validate_intent(db, user, payload):
    _require_approve(db, user)
    proposal = source(db, user, payload["step_id"])
    if content_hash(proposal) != payload["proposal_hash"]:
        raise DomainError("CONFIRMATION_INVALID", "操作建议内容已变化", 409)
    key = next((name for name, kind in KIND_BY_TOOL.items() if kind == proposal.get("kind")), None)
    if key is None:
        raise DomainError("TOOL_FORBIDDEN", "操作建议类型不可用", 403)
    data = parse(key, proposal["input"])
    _, display = preview(db, user, key, data)
    if content_hash(display) != content_hash(proposal["display"]):
        raise DomainError("VERSION_CONFLICT", "审批任务状态已变化，请重新查询后准备", 409)
    return proposal, key, data


def confirm(db, user, payload):
    _, key, data = validate_intent(db, user, payload)
    item = _lookup(db, user, data)
    task_id = item.get("taskId")
    if not task_id:
        raise DomainError("STATE_BLOCKED", "这张审批待办缺少任务编号，不能提交", 409)
    action: Literal["approve", "reject"] = "approve" if key == PASS_TOOL else "reject"
    path = f"workflow/tasks/{task_id}/{'approve' if action == 'approve' else 'reject'}"
    result = post_erp(db, user, path, {
        "taskId": task_id,
        "action": action,
        "comment": data.comment,
    }, intent_id=payload.get("_intent_id"), action=f"approval_{action}", native_id=f"task:{task_id}")
    return {
        "order_no": item.get("orderNo") or data.order_no,
        "mold": item.get("moldFamily") or item.get("moldNo") or data.mold,
        "batch": item.get("moldBatch") or item.get("moldNo") or data.batch,
        "action": action,
        "status": "CONFIRMED",
        "erp": result,
    }
