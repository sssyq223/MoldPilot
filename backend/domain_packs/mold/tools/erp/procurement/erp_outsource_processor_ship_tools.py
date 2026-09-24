"""Processor product shipment: query shippable qty/target, then confirm ship."""
from __future__ import annotations

from typing import Any

from pydantic import Field, ValidationError, field_validator

from domain_packs.mold import models as m
from domain_packs.mold.authorization import fingerprint
from domain_packs.mold.erp.procurement.erp_outsource_http import post_erp
from domain_packs.mold.erp.procurement.erp_outsource_scope import require_allow, supplier_codes_for
from domain_packs.mold.ports.bpm import content_hash
from domain_packs.mold.ports.confirmation_policy import proposal_confirmation_policy
from domain_packs.mold.ports.db import now
from domain_packs.mold.ports.errors import DomainError
from domain_packs.mold.ports.schemas import StrictModel
from domain_packs.mold.tools.erp.procurement.outsource_queries import processor_fulfillment
from domain_packs.mold.tools.erp.procurement.outsource_queries.buyer_todo import identity_display

TODO_TOOL = "query_erp_outsource_processor_product_ship"
SHIP_TOOL = "prepare_erp_outsource_processor_product_ship"
QUERY_TOOL_KEYS = (TODO_TOOL,)
PREPARE_TOOL_KEYS = (SHIP_TOOL,)
TOOL_KEYS = QUERY_TOOL_KEYS + PREPARE_TOOL_KEYS

SKILL_SPECS = {
    "outsource_processor_product_ship": {
        "name": "委外加工商成品发货",
        "tools": [TODO_TOOL],
        "optional_tools": [SHIP_TOOL],
        "activation_tools": list(QUERY_TOOL_KEYS),
        "activation_queries": [
            "成品发货", "发成品", "发半成品", "回厂发货",
            "待发货", "有没有发货", "成品发货待办",
        ],
        "auto_activation_queries": [
            "成品发货", "发成品", "发半成品", "回厂发货",
            "待发货", "有没有发货", "成品发货待办",
        ],
        "priority_patterns": ["成品发货|发成品|发半成品|回厂发货|待发货|成品发货待办"],
        "requires_tool_evidence": True,
        "suppress_tool_search_on_auto_activation": True,
        "host_auto_invoke_empty_arguments": True,
    },
}

TOOL_SPECS = {
    TODO_TOOL: {
        "description": "只读查询本加工商可成品发货行：已收原料、剩余可发、第一道/末道 T/F、入库目标（成品库或半成品库）。部分收料只能发对应部分。",
        "permission": "erp_outsource_processor.read",
    },
    SHIP_TOOL: {
        "description": "准备成品发货。用查询结果中的订单号，必要时加模具号、批次号。未指定行时按剩余可发数量发。禁止使用内部数字 id。本人确认后才写入 ERP。",
        "permission": "erp_outsource_processor.execute",
    },
}

TOOL_NAMES = {
    TODO_TOOL: "查询加工商成品发货待办",
    SHIP_TOOL: "准备成品发货",
}


class ProductShipTodoInput(StrictModel):
    question: str | None = Field(default=None, max_length=500)
    mold: str | None = Field(default=None, max_length=40)

    @field_validator("question", "mold")
    @classmethod
    def strip_text(cls, value):
        return value.strip() or None if isinstance(value, str) else value


class ProductShipLineInput(StrictModel):
    order_part_id: int = Field(ge=1)
    qty: int = Field(ge=1)


class ProcessorProductShipInput(StrictModel):
    order_no: str = Field(min_length=3, max_length=80, description="查询结果中的订单号，例如 EO-260923-0KKR。禁止使用内部数字 id。")
    mold: str | None = Field(default=None, max_length=40, description="模具号，例如 M260063。")
    batch: str | None = Field(default=None, max_length=40, description="批次号，例如 M260063-P1。同一订单有多批次时必填。")
    lines: list[ProductShipLineInput] = Field(default_factory=list, description="空则按各零件剩余可发数量全部发出。")
    logistics_company: str | None = Field(default=None, max_length=80)
    tracking_no: str | None = Field(default=None, max_length=80)
    remark: str | None = Field(default=None, max_length=400)

    @field_validator("order_no", "mold", "batch", "logistics_company", "tracking_no", "remark")
    @classmethod
    def strip_text(cls, value):
        return value.strip() or None if isinstance(value, str) else value


INPUT_MODELS = {
    TODO_TOOL: ProductShipTodoInput,
    SHIP_TOOL: ProcessorProductShipInput,
}
KIND_BY_TOOL = {SHIP_TOOL: "erp_outsource_processor_product_ship"}
ACTION_BY_TOOL = {SHIP_TOOL: "confirm_erp_outsource_processor_product_ship"}


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
        raise DomainError("INVALID_TOOL_INPUT", "成品发货参数无效：" + error.errors()[0]["msg"]) from None


def _require_read(db, user) -> None:
    require_allow(db, user, "erp_outsource_processor.read", "仅委外加工商可查询成品发货待办")


def _require_execute(db, user) -> None:
    require_allow(db, user, "erp_outsource_processor.execute", "仅委外加工商可办理成品发货")


def _tokens(db, user) -> list[str] | None:
    tokens = supplier_codes_for(db, user)
    if tokens == []:
        raise DomainError("FORBIDDEN", "当前账号未绑定加工商范围，不能办理", 403)
    return tokens


def _guard(item: dict[str, Any], tokens: list[str] | None) -> None:
    if tokens is None:
        return
    values = {
        str(item.get("supplierId") or "").strip().casefold(),
        str(item.get("supplierCode") or "").strip().casefold(),
    }
    allowed = {str(token).strip().casefold() for token in tokens if str(token).strip()}
    if not (values - {""}) & allowed:
        raise DomainError("FORBIDDEN", "这张工单不属于本加工商", 403)


def execute_query(db, user, arguments: dict | None, run=None) -> dict[str, Any]:
    _require_read(db, user)
    data = parse(TODO_TOOL, arguments)
    question = data.question or str(getattr(run, "prompt", "") or "")
    parsed = processor_fulfillment.parse_question(" ".join(part for part in (question, data.mold) if part))
    tokens = supplier_codes_for(db, user)
    if tokens == []:
        payload = {"status": "NO_SUPPLIER_SCOPE", "summary": "当前账号未绑定加工商范围，看不到成品发货待办。", "items": []}
    else:
        payload = processor_fulfillment.run_product(parsed, processor_tokens=tokens)
    return {
        "data": payload,
        "source": "management-system ERP 加工商成品发货只读查询",
        "as_of": now().isoformat(),
        "limitations": [
            "只读。可发数量 = min(订单剩余, 已收原料−已发)。入库目标按 ERP processor-inbound-v1：零件/模具及明确末道进成品库，非末道或末道缺失进半成品库。",
            "拒单转下一家后，只有新加工商接单并完成供料后才能发货。",
        ],
    }


def _resolved_lines(data, item: dict[str, Any]) -> list[dict[str, Any]]:
    remain = {
        int(part.get("orderPartId") or 0): part
        for part in item.get("parts") or []
        if int(part.get("remainQty") or 0) > 0
    }
    if data.lines:
        chosen = [{"order_part_id": line.order_part_id, "qty": line.qty} for line in data.lines]
    else:
        chosen = [
            {"order_part_id": part_id, "qty": int(part.get("remainQty") or 0)}
            for part_id, part in remain.items()
        ]
    if not chosen:
        raise DomainError("STATE_BLOCKED", "没有可发数量。零件委外需先确认已收到的那部分原料。", 409)
    for line in chosen:
        part = remain.get(line["order_part_id"])
        allowed = int((part or {}).get("remainQty") or 0)
        if allowed <= 0:
            raise DomainError("STATE_BLOCKED", f"零件行 {line['order_part_id']} 当前不可发", 409)
        if line["qty"] > allowed:
            received = int((part or {}).get("receivedQty") or 0)
            raise DomainError(
                "STATE_BLOCKED",
                f"零件行 {line['order_part_id']} 只能按已收原料发成品（已收 {received}，可发 {allowed}）",
                409,
            )
    return chosen


def _lookup(data, tokens: list[str] | None) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    item = processor_fulfillment.find_product_order_by_identity(
        order_no=getattr(data, "order_no", None),
        mold=getattr(data, "mold", None),
        batch=getattr(data, "batch", None),
    )
    if not item:
        raise DomainError("NOT_FOUND", "没有找到可成品发货的工单，请用订单号、模具号和批次号重新查询", 404)
    _guard(item, tokens)
    return item, _resolved_lines(data, item)


def preview(db, user, key: str, data) -> tuple[dict[str, Any], dict[str, Any]]:
    item, lines = _lookup(data, _tokens(db, user))
    parts = {int(part.get("orderPartId") or 0): part for part in item.get("parts") or []}
    details = []
    for line in lines:
        part = parts.get(line["order_part_id"]) or {}
        details.append(
            f"{part.get('partNo') or line['order_part_id']}×{line['qty']}"
            f"（订单{part.get('orderQty')}/已收{part.get('receivedQty')}/已发{part.get('shippedQty')}/可发{part.get('remainQty')}，"
            f"首道{part.get('isFirstOperationLabel')}/末道{part.get('isEndOperationLabel')}→{part.get('inboundTargetLabel')}）"
        )
    display = {
        **identity_display(item),
        "委外类型": item.get("outsourceTypeLabel") or item.get("outsourceType"),
        "操作": "成品发货",
        "入库目标": "、".join(item.get("inboundTargets") or []),
        "明细": "；".join(details),
        "说明": "本人确认后写入 ERP。下一步仓库按同一入库目标做回厂入库确认（下期）。",
    }
    if data.logistics_company:
        display["物流公司"] = data.logistics_company
    if data.tracking_no:
        display["运单号"] = data.tracking_no
    item = dict(item)
    item["shipLines"] = lines
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
        "limitations": ["仅准备成品发货；数量受已收原料约束。本人确认后才调用 ERP。"],
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
    _, display = preview(db, user, SHIP_TOOL, data)
    if content_hash(display) != content_hash(proposal["display"]):
        raise DomainError("VERSION_CONFLICT", "可发数量或入库目标已变化，请重新查询后准备", 409)
    return proposal, data


def confirm(db, user, payload):
    _, data = validate_intent(db, user, payload)
    item, lines = _lookup(data, _tokens(db, user))
    order_id = item.get("orderId")
    if not order_id:
        raise DomainError("STATE_BLOCKED", "这张待办还没有委外订单，不能发货", 409)
    result = post_erp(db, user, "entrust/fulfillment/product-shipment", {
        "order_id": order_id,
        "lines": lines,
        "logistics_company": data.logistics_company,
        "tracking_no": data.tracking_no,
        "remark": data.remark,
    }, intent_id=payload.get("_intent_id"), action="processor_product_ship", native_id=f"order:{order_id}")
    return {
        "order_no": item.get("orderNo") or data.order_no,
        "mold": item.get("moldFamily") or item.get("moldNo") or data.mold,
        "batch": item.get("moldBatch") or item.get("moldNo") or data.batch,
        "action": "processor_product_ship",
        "status": "CONFIRMED",
        "erp": result,
        "inboundTargets": item.get("inboundTargets") or [],
        "nextHint": "成品已发出。下一步仓库按入库目标（成品库/半成品库）做回厂入库确认。",
    }
