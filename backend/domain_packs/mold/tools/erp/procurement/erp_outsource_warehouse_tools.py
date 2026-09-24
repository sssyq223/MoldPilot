"""Warehouse outsource supply: query pending tasks, then confirm ship / prep."""
from __future__ import annotations

from typing import Any

from pydantic import Field, ValidationError, field_validator

from domain_packs.mold import models as m
from domain_packs.mold.authorization import fingerprint
from domain_packs.mold.erp.procurement.erp_outsource_http import post_erp
from domain_packs.mold.erp.procurement.erp_outsource_scope import require_allow
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
from domain_packs.mold.tools.erp.procurement.outsource_queries import warehouse_todo
from domain_packs.mold.tools.erp.procurement.outsource_queries.buyer_todo import identity_display

TODO_TOOL = "query_erp_outsource_warehouse_tasks"
SHIP_TOOL = "prepare_erp_outsource_warehouse_ship"
QUERY_TOOL_KEYS = (TODO_TOOL,)
PREPARE_TOOL_KEYS = (SHIP_TOOL,)
TOOL_KEYS = QUERY_TOOL_KEYS + PREPARE_TOOL_KEYS

SKILL_SPECS = {
    "outsource_warehouse_ops": {
        "name": "委外仓管供料办理",
        "tools": [TODO_TOOL],
        "optional_tools": [SHIP_TOOL],
        "activation_tools": list(QUERY_TOOL_KEYS),
        "activation_queries": [
            "仓库发料", "原料发货", "备料完成", "待备料", "待发料",
            "发料待办", "备料待办",
        ],
        "auto_activation_queries": [
            "仓库发料", "原料发货", "备料完成", "待备料", "待发料",
            "发料待办", "备料待办",
        ],
        "priority_patterns": ["仓库发料|原料发货|备料完成|待备料|待发料|发料待办|备料待办"],
        "requires_tool_evidence": True,
        "suppress_tool_search_on_auto_activation": True,
        "host_auto_invoke_empty_arguments": True,
    },
}

TOOL_SPECS = {
    TODO_TOOL: {
        "description": "只读查询仓库委外待发料/待备料明细。零件委外确认后加工商还要收货；工序委外确认即备料完成、加工商不用收货。采购直发不在本待办。",
        "permission": "erp_outsource_warehouse.read",
    },
    SHIP_TOOL: {
        "description": "准备确认仓库待发货明细。用订单号，必要时加模具号、批次号定位仍是仓库 pending 的同一工单。热处理工序备料要带零件号和实际重量。禁止使用内部数字 id。本人确认后才写入 ERP。",
        "permission": "erp_outsource_warehouse.execute",
    },
}

TOOL_NAMES = {
    TODO_TOOL: "查询仓库委外待发料",
    SHIP_TOOL: "准备确认仓库发料或备料",
}


class WarehouseTodoInput(StrictModel):
    question: str | None = Field(default=None, max_length=500)
    mold: str | None = Field(default=None, max_length=40)

    @field_validator("question", "mold")
    @classmethod
    def strip_text(cls, value):
        return value.strip() or None if isinstance(value, str) else value


class LineWeightInput(StrictModel):
    part_no: str = Field(min_length=1, max_length=80, description="查询结果中的零件号。")
    actual_weight: float = Field(gt=0, description="热处理备料实际重量(kg)。")

    @field_validator("part_no")
    @classmethod
    def strip_part(cls, value):
        return value.strip()


class WarehouseShipInput(CamelModel):
    order_no: str = identity_order_no(min_length=3, max_length=80, description="查询结果中的订单号。禁止内部数字 id。")
    mold: str | None = identity_mold(default=None, max_length=40)
    batch: str | None = identity_batch(default=None, max_length=40)
    logistics_company: str | None = Field(default=None, max_length=80)
    tracking_no: str | None = Field(default=None, max_length=80)
    remark: str | None = Field(default=None, max_length=400)
    line_weights: list[LineWeightInput] = Field(default_factory=list)

    @field_validator("mold", "logistics_company", "tracking_no", "remark")
    @classmethod
    def strip_text(cls, value):
        return value.strip() or None if isinstance(value, str) else value


INPUT_MODELS = {
    TODO_TOOL: WarehouseTodoInput,
    SHIP_TOOL: WarehouseShipInput,
}

KIND_BY_TOOL = {SHIP_TOOL: "erp_outsource_warehouse_ship"}
ACTION_BY_TOOL = {SHIP_TOOL: "confirm_erp_outsource_warehouse_ship"}


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
        raise DomainError("INVALID_TOOL_INPUT", "仓管办理参数无效：" + error.errors()[0]["msg"]) from None


def _require_read(db, user) -> None:
    require_allow(db, user, "erp_outsource_warehouse.read", "仅委外仓管可查询仓库待发料")


def _require_execute(db, user) -> None:
    require_allow(db, user, "erp_outsource_warehouse.execute", "仅委外仓管可确认发料或备料")


def execute_query(db, user, arguments: dict | None, run=None) -> dict[str, Any]:
    _require_read(db, user)
    data = parse(TODO_TOOL, arguments)
    question = data.question or str(getattr(run, "prompt", "") or "")
    parsed = warehouse_todo.parse_question(" ".join(part for part in (question, data.mold) if part))
    payload = warehouse_todo.run(parsed)
    return {
        "data": payload,
        "source": "management-system ERP 仓库委外待发料只读查询",
        "as_of": now().isoformat(),
        "limitations": ["只读查询仓库待发料/待备料。采购直发由物料供应商办理，不在本待办。"],
    }


def _lookup(data) -> list[dict[str, Any]]:
    items = warehouse_todo.find_pending_by_identity(
        order_no=data.order_no, mold=data.mold, batch=data.batch,
    )
    if not items:
        raise DomainError("NOT_FOUND", "没有找到这张仓库待发料/待备料，请用订单号重新查询", 404)
    for item in items:
        if item.get("status") != "pending" or item.get("sourceType") not in {"material_stock", "semi_finished_stock"}:
            raise DomainError("STATE_BLOCKED", f"{item.get('partNo') or item.get('orderNo')} 不是仓库待发料，不能确认", 409)
    order_ids = {item.get("orderId") for item in items}
    sources = {item.get("sourceType") for item in items}
    if len(order_ids) != 1:
        raise DomainError("STATE_BLOCKED", "同一张发货单只能选择同一个委外工单的明细", 409)
    if len(sources) != 1:
        raise DomainError("STATE_BLOCKED", "同一张发货单只能选择同一个发货来源的明细", 409)
    if any(item.get("needsWeight") for item in items):
        weighed = {str(row.part_no).strip().casefold() for row in data.line_weights}
        missing = [item.get("partNo") for item in items if str(item.get("partNo") or "").strip().casefold() not in weighed]
        if missing:
            raise DomainError("STATE_BLOCKED", "热处理工序委外备料必须填写每个零件的实际重量", 409)
    return items


def preview(key: str, data) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    items = _lookup(data)
    first = items[0]
    operation = first.get("outsourceType") == "operation"
    display = {
        **identity_display(first),
        "委外类型": first.get("outsourceTypeLabel") or first.get("outsourceType"),
        "加工商": first.get("processorName") or "未标注",
        "操作": "备料完成" if operation else "原料发货",
        "明细": "、".join(f"{item.get('partNo') or item.get('taskId')}×{item.get('qty')}" for item in items),
        "说明": first.get("nextHint") or "",
    }
    if data.logistics_company:
        display["物流公司"] = data.logistics_company
    if data.tracking_no:
        display["运单号"] = data.tracking_no
    return items, display


def execute_tool(db, user, key: str, arguments: dict | None, run=None) -> dict[str, Any]:
    if key == TODO_TOOL:
        return execute_query(db, user, arguments, run=run)
    _require_execute(db, user)
    data = parse(key, arguments)
    _, display = preview(key, data)
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
        "limitations": ["仅准备仓库发料/备料；本人确认后才调用 ERP。"],
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
    data = parse(SHIP_TOOL, proposal["input"])
    _, display = preview(SHIP_TOOL, data)
    if content_hash(display) != content_hash(proposal["display"]):
        raise DomainError("VERSION_CONFLICT", "仓库待发料状态已变化，请重新查询后准备", 409)
    return proposal, data


def confirm(db, user, payload):
    _, data = validate_intent(db, user, payload)
    items = warehouse_todo.find_pending_by_identity(
        order_no=data.order_no, mold=data.mold, batch=data.batch,
    )
    task_ids = [int(item["taskId"]) for item in items]
    weights = {str(row.part_no).strip().casefold(): row.actual_weight for row in data.line_weights}
    result = post_erp(db, user, "entrust/material-supply/warehouse-tasks/confirm-shipped", {
        "taskIds": task_ids,
        "logisticsCompany": data.logistics_company,
        "trackingNo": data.tracking_no,
        "remark": data.remark,
        "lineWeights": [
            {"taskId": item["taskId"], "actualWeight": weights[str(item.get("partNo") or "").strip().casefold()]}
            for item in items
            if item.get("needsWeight")
        ],
    }, intent_id=payload.get("_intent_id"), action="warehouse_material_ship",
        native_id="order:" + (data.order_no or items[0].get("orderNo") or ""))
    operation = items and items[0].get("outsourceType") == "operation"
    return {
        "order_no": data.order_no,
        "action": "warehouse_prep" if operation else "warehouse_ship",
        "status": "CONFIRMED",
        "erp": result,
        "nextHint": (
            "工序委外备料已完成，加工商不用收货，可直接成品发货。"
            if operation
            else "原料已发出，等待加工商确认收货。"
        ),
    }
