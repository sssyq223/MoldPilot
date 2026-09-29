"""Warehouse outsource supply: query pending tasks, then confirm ship / prep."""
from __future__ import annotations

from typing import Any

from pydantic import ConfigDict, Field, ValidationError, field_validator

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
    identity_part,
)
from domain_packs.mold.tools.erp.procurement.outsource_queries import buyer_todo, warehouse_inbound, warehouse_todo
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
            "仓库发料", "原料发货", "备料完成", "待备料", "待发料", "待发货", "委外待发货",
            "发料待办", "备料待办", "确认备料", "确认发料", "办发料", "办备料",
            "待办任务", "有没有待办", "查看待办",
        ],
        "auto_activation_queries": [
            "仓库发料", "原料发货", "备料完成", "待备料", "待发料", "待发货", "委外待发货",
            "发料待办", "备料待办", "确认备料", "确认发料", "办发料", "办备料",
            "待办任务", "有没有待办", "查看待办",
        ],
        "priority_patterns": [
            "仓库发料|原料发货|备料完成|待备料|待发料|待发货|委外待发货|发料待办|备料待办|待办任务|有没有待办|查看待办|(?<!收货|入库|回厂)待办"
        ],
        "requires_tool_evidence": True,
        "activation_route": "authorized",
        "suppress_tool_search_on_auto_activation": False,
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

SHIP_SPEECH = ("确认备料", "确认发料", "确认原料发货", "办发料", "办备料")
SUPPLY_ONLY_PHRASES = (
    "待发料", "待备料", "发料待办", "备料待办", "待发货", "委外待发货",
    "原料发货", "仓库发料", "确认发料", "确认备料", "办发料", "办备料",
    "备料完成",
)


def include_inbound_on_warehouse_todo(question: str) -> bool:
    """Generic 查询待办 covers inbound; 待发料/待备料 stays supply-only."""
    text = question or ""
    return not any(phrase in text for phrase in SUPPLY_ONLY_PHRASES)


def spoken_ship_arguments(prompt: str, context_text: str = "") -> dict[str, Any] | None:
    """Parse warehouse ship/prep speech into prepare_erp_outsource_warehouse_ship arguments."""
    text = prompt or ""
    if any(token in text for token in ("有几个", "有哪些", "有没有", "到哪一步", "不要发", "不要备")):
        return None
    if not any(token in text for token in SHIP_SPEECH):
        return None
    identity_source = f"{text}\n{context_text or ''}"
    order = buyer_todo.ORDER_NO.search(identity_source)
    if not order:
        return None
    arguments: dict[str, Any] = {"order_no": order.group(1).upper()}
    parsed = buyer_todo.parse_question(identity_source)
    if parsed.get("mold_family"):
        arguments["mold"] = parsed["mold_family"]
    if parsed.get("mold_batch"):
        arguments["batch"] = parsed["mold_batch"]
        arguments.setdefault("mold", parsed["mold_batch"].split("-P", 1)[0])
    return arguments


class WarehouseTodoInput(CamelModel):
    model_config = ConfigDict(extra="ignore", populate_by_name=True)
    question: str | None = Field(default=None, max_length=500)
    mold: str | None = identity_mold(default=None, max_length=40)
    order_no: str | None = identity_order_no(default=None, max_length=80)
    batch: str | None = identity_batch(default=None, max_length=40)
    part: str | None = identity_part(default=None, max_length=80)

    @field_validator("question", "mold", "order_no", "batch")
    @classmethod
    def strip_text(cls, value):
        return value.strip() or None if isinstance(value, str) else value

    @field_validator("part", mode="before")
    @classmethod
    def strip_part(cls, value):
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
    parsed = warehouse_todo.parse_question(
        " ".join(part for part in (question, data.mold, data.order_no, data.batch, data.part) if part)
    )
    if data.batch and not parsed.get("mold_batch"):
        parsed["mold_batch"] = data.batch
        parsed["mold_family"] = ""
    elif data.mold and not parsed.get("mold_family") and not parsed.get("mold_batch"):
        parsed["mold_family"] = data.mold
    items = warehouse_todo.query_items(
        mold_family=parsed.get("mold_family") or "",
        mold_batch=parsed.get("mold_batch") or "",
    )
    if data.order_no or data.part:
        items = [
            item for item in items
            if buyer_todo.item_matches_identity(
                item,
                order_no=data.order_no or "",
                mold=data.mold or parsed.get("mold_family") or "",
                batch=data.batch or parsed.get("mold_batch") or "",
            )
        ]
        if data.part:
            token = str(data.part).strip().casefold()
            items = [item for item in items if token in str(item.get("partNo") or "").strip().casefold()]
    payload = warehouse_todo.present(
        items,
        mold_family=parsed.get("mold_family") or "",
        mold_batch=parsed.get("mold_batch") or "",
    )
    inbound_items = []
    if include_inbound_on_warehouse_todo(question):
        inbound_items = _query_inbound_for_todo(question, data, parsed)
        payload["inboundItems"] = inbound_items
        payload["inboundCount"] = len(inbound_items)
        inbound_payload = warehouse_inbound.present(
            {
                "mold_family": parsed.get("mold_family") or "",
                "mold_batch": parsed.get("mold_batch") or "",
            },
            inbound_items,
        )
        if inbound_items and payload.get("items"):
            payload["summary"] = f"{payload.get('summary') or ''}\n{inbound_payload.get('summary') or ''}".strip()
        elif inbound_items:
            payload["items"] = inbound_items
            payload["summary"] = (
                f"{inbound_payload.get('summary') or ''}\n"
                "待发料/待备料是 0 单，不代表没有仓库待办。回厂收货入库也是仓库待办。"
            ).strip()
        else:
            payload["summary"] = (
                "仓库待发料/待备料是 0 单，回厂收货入库也是 0 条。采购直发不在发料待办。"
            )
        limitations = [
            "只读。泛化仓库待办同时含待发料/待备料和加工商成品发货后的回厂收货入库。",
            "采购直发由物料供应商办理，不在发料待办。",
        ]
        source = "management-system ERP 仓库委外待办只读查询"
    else:
        limitations = ["只读查询仓库待发料/待备料。采购直发由物料供应商办理，不在本待办。"]
        source = "management-system ERP 仓库委外待发料只读查询"
    return {
        "data": payload,
        "model_context": _model_context(payload),
        "source": source,
        "as_of": now().isoformat(),
        "limitations": limitations,
    }


def _query_inbound_for_todo(question: str, data, parsed: dict[str, str]) -> list[dict[str, Any]]:
    inbound_parsed = warehouse_inbound.parse_question(
        " ".join(part for part in (question, data.mold, data.order_no, data.batch) if part)
    )
    inbound_parsed["mold_family"] = inbound_parsed.get("mold_family") or parsed.get("mold_family") or ""
    inbound_parsed["mold_batch"] = inbound_parsed.get("mold_batch") or parsed.get("mold_batch") or ""
    inbound_parsed["tab"] = ""
    items = warehouse_inbound.query_items(inbound_parsed)
    if data.order_no:
        token = str(data.order_no).strip().casefold()
        items = [item for item in items if token in str(item.get("orderNo") or "").strip().casefold()]
    return items


def _inbound_part_details(item: dict[str, Any]) -> str:
    labels = []
    for line in item.get("lines") or []:
        if not isinstance(line, dict):
            continue
        head = " ".join(part for part in (line.get("partNo"), line.get("partName")) if part) or "零件"
        qty = line.get("pendingInboundQty") or line.get("pendingArrivalQty") or line.get("qty")
        labels.append(f"{head}×{qty}" if qty not in (None, "") else head)
    return "；".join(labels)


def _model_context(payload: dict[str, Any]) -> dict[str, Any]:
    orders = [item for item in (payload.get("orders") or []) if isinstance(item, dict)]
    inbound_items = [item for item in (payload.get("inboundItems") or []) if isinstance(item, dict)]
    line_count = int(payload.get("lineCount") or 0)
    visible = [
        {
            "station": "待发料" if item.get("action") == "ship" else "待备料",
            "action": item.get("actionLabel"),
            "outsourceType": item.get("outsourceTypeLabel"),
            "orderNo": item.get("orderNo"),
            "mold": item.get("moldBatch") or item.get("moldNo"),
            "lineCount": item.get("lineCount"),
            "qty": item.get("qty"),
            "partDetails": item.get("partDetails"),
            "processor": item.get("processorName"),
        }
        for item in orders[:8]
    ]
    visible.extend(
        {
            "station": item.get("stationLabel") or item.get("station") or item.get("actionLabel"),
            "action": item.get("actionLabel"),
            "outsourceType": item.get("outsourceTypeLabel"),
            "orderNo": item.get("orderNo"),
            "mold": item.get("moldNo"),
            "shipmentNo": item.get("shipmentNo"),
            "pendingArrivalQty": item.get("pendingArrivalQty"),
            "pendingInboundQty": item.get("pendingInboundQty"),
            "partDetails": _inbound_part_details(item),
            "processor": item.get("supplierName"),
        }
        for item in inbound_items[:8]
    )
    inbound_count = int(payload.get("inboundCount") or len(inbound_items))
    if orders and inbound_items:
        summary = (
            f"仓库待办：待发料/待备料 {len(orders)} 单、{line_count} 个零件明细；"
            f"回厂收货入库 {inbound_count} 条。两类都要报，不要只说发料。"
            "不要改口索要项目号或合同号。"
        )
    elif orders:
        summary = (
            f"仓库委外待发货共 {len(orders)} 单、{line_count} 个零件明细。"
            "按订单说明办理、订单号和零件明细。不要把零件行数说成待办单数，"
            "也不要说未查询到或改口索要项目号、合同号。"
        )
        if "inboundItems" in payload:
            summary += f"回厂收货入库是 {inbound_count} 条。"
    elif inbound_items:
        summary = (
            f"仓库待发料和待备料是 0 单，但回厂收货入库待办有 {inbound_count} 条。"
            "这是仓库待办，不要说没有待办或改口索要项目号、合同号。"
        )
    else:
        summary = "仓库待发料和待备料是 0 单。采购直发不在本待办。不要改口索要项目号或合同号。"
        if "inboundItems" in payload:
            summary = (
                "仓库待发料/待备料是 0 单，回厂收货入库也是 0 条。"
                "采购直发不在发料待办。不要改口索要项目号或合同号。"
            )
    return {
        "summary": summary,
        "item_count": len(orders) + inbound_count,
        "line_count": line_count,
        "inbound_count": inbound_count,
        "items": visible,
    }


def _lookup(data) -> list[dict[str, Any]]:
    items = warehouse_todo.find_pending_by_identity(
        order_no=data.order_no, mold=data.mold, batch=data.batch,
    )
    if not items:
        counts = warehouse_todo.supply_status_counts(getattr(data, "order_no", "") or "")
        if counts.get("shipped") and not counts.get("pending"):
            raise DomainError(
                "STATE_BLOCKED",
                f"{data.order_no} 的原料已经发出，正在等加工商确认收货，不用再发一次。",
                409,
            )
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
    if run.status not in {"RUNNING", "RUNNING_SCOPED", "SUCCEEDED", "FAILED"}:
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
