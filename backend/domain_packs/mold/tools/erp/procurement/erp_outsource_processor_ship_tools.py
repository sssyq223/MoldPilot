"""Processor product shipment: query shippable qty/target, then confirm ship."""
from __future__ import annotations

import re
from typing import Any

from pydantic import Field, ValidationError, field_validator

from domain_packs.mold import models as m
from domain_packs.mold.authorization import fingerprint
from domain_packs.mold.erp.procurement.erp_outsource_http import dispatch_erp_batch, post_erp
from domain_packs.mold.erp.procurement.erp_outsource_scope import require_allow, supplier_codes_for
from domain_packs.mold.ports.bpm import content_hash
from domain_packs.mold.ports.confirmation_policy import proposal_confirmation_policy
from domain_packs.mold.ports.db import now
from domain_packs.mold.ports.errors import DomainError
from domain_packs.mold.ports.schemas import StrictModel
from domain_packs.mold.tools.erp.procurement.outsource_queries import buyer_todo, processor_fulfillment
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
            "发货把", "发货吧", "成品发货把", "成品发货吧", "确认发货", "办发货",
        ],
        "priority_patterns": ["成品发货吧|成品发货把|成品发货|发成品|发半成品|回厂发货|待发货|成品发货待办|发货把|发货吧|确认发货|办发货"],
        "requires_tool_evidence": True,
        "activation_route": "authorized",
        "suppress_tool_search_on_auto_activation": False,
        "host_auto_invoke_empty_arguments": True,
    },
}

TOOL_SPECS = {
    TODO_TOOL: {
        "description": "只读查询本加工商可成品发货行：已收原料、剩余可发、第一道/末道 T/F、入库目标（成品库或半成品库）。部分收料只能发对应部分。",
        "permission": "erp_outsource_processor.read",
    },
    SHIP_TOOL: {
        "description": "准备成品发货。用户说 NO.N / 发货吧 / 全部发货或订单号时立刻锁定。NO. 是可成品发货表第一列。未指定行时按剩余可发数量发。禁止使用内部数字 id。本人确认后才写入 ERP。",
        "permission": "erp_outsource_processor.execute",
    },
}

TOOL_NAMES = {
    TODO_TOOL: "查询加工商成品发货待办",
    SHIP_TOOL: "准备成品发货",
}


PRODUCT_SHIP_SPEECH = (
    "确认成品发货", "办成品发货", "发成品", "发半成品", "回厂发货",
    "成品发货把", "成品发货吧", "发货把", "发货吧", "确认发货", "办发货", "把货发了",
)
_SHIP_IMPERATIVE = re.compile(r"(成品)?发货[把吧]")
_SHIP_NOW = frozenset({"成品发货", "发货", "发一下货"})
_SHIP_NEXT_HINT = "成品已发出。下一步仓库按入库目标（成品库/半成品库）做回厂入库确认。"
_QR_KEY_MARKERS = ("qr", "barcode", "二维码", "条码")
_QR_EXACT_KEYS = {"qrcode", "qrurl", "qrimage", "qrpayload", "qrtoken", "barcode"}
_QR_TALK = re.compile(
    r"[，,、;；]?\s*(?:二维码(?:已|已经)?(?:生成|创建)?|QR\s*codes?(?:\s+generated)?)",
    re.IGNORECASE,
)
_SHIPMENT_NO_KEYS = {"shipmentno", "shipmentnumber", "shipno"}


def _unique_order_nos(text: str) -> list[str]:
    found: list[str] = []
    for match in buyer_todo.ORDER_NO.finditer(text or ""):
        order_no = buyer_todo.normalize_order_no(match.group(1))
        if order_no and order_no not in found:
            found.append(order_no)
    return found


def _folded_key(key: str) -> str:
    return re.sub(r"[^a-z]", "", str(key or "").lower())


def strip_qr_talk(text: str) -> str:
    cleaned = _QR_TALK.sub("", text or "")
    cleaned = re.sub(r"[，,]{2,}", "，", cleaned)
    cleaned = re.sub(r"\s{2,}", " ", cleaned)
    return cleaned.strip("，,、;； ").strip()


def _is_qr_key(key: str) -> bool:
    folded = _folded_key(key)
    if folded in _QR_EXACT_KEYS:
        return True
    if any(marker in str(key) for marker in ("二维码", "条码")):
        return True
    return any(marker in folded for marker in ("qr", "barcode"))


def clip_ship_erp(payload: Any) -> Any:
    """Drop QR/barcode fields so follow-up speech cannot mention 二维码."""
    if isinstance(payload, dict):
        cleaned: dict[str, Any] = {}
        for key, value in payload.items():
            if _is_qr_key(key):
                continue
            if isinstance(value, str):
                value = strip_qr_talk(value)
                if not value:
                    continue
            cleaned[key] = clip_ship_erp(value)
        return cleaned
    if isinstance(payload, list):
        return [clip_ship_erp(item) for item in payload]
    return payload


def _shipment_no_from_erp(payload: Any) -> str:
    if isinstance(payload, dict):
        for key, value in payload.items():
            if _folded_key(key) in _SHIPMENT_NO_KEYS and value not in (None, ""):
                return str(value).strip()
        for value in payload.values():
            found = _shipment_no_from_erp(value)
            if found:
                return found
    elif isinstance(payload, list):
        for item in payload:
            found = _shipment_no_from_erp(item)
            if found:
                return found
    return ""


def _latest_order_no(text: str) -> str | None:
    found = _unique_order_nos(text)
    return found[-1] if found else None


SHIP_ALL_SPEECH = ("发货吧", "发货把", "全部发货", "都发了", "全都发", "这几个都发", "把货发了", "成品发货吧", "成品发货把")


def spoken_product_ship_arguments(prompt: str, context_text: str = "") -> dict[str, Any] | None:
    """Parse 确认成品发货 / NO.n成品发货 into prepare arguments.

    「有没有/有几个/查询」是查询，不准备确认卡。
    界面按钮或口令「成品发货」是办理：按当前可发表出确认卡，不要让模型口头再确认。
    对话里有多张 EO- 时不要抓最后一张旧单；「发货吧」按当前可发表全部发。
    NO. 是可成品发货表的行号，不是待办表。
    """
    text = prompt or ""
    if any(token in text for token in ("有几个", "有哪些", "有没有", "不要发", "不发货", "查询", "查一下", "看看")):
        return None
    if any(token in text for token in ("收货", "收料", "来料", "原料发货", "备料", "发料")):
        return None
    compact = re.sub(r"[。.!！？\s]+$", "", text.strip())
    row_no = buyer_todo.spoken_board_row_number(text)
    named = any(token in text for token in PRODUCT_SHIP_SPEECH) or bool(_SHIP_IMPERATIVE.search(text))
    if compact in _SHIP_NOW:
        named = True
    context_orders = _unique_order_nos(context_text)
    row_ship = bool(row_no) and "成品发货" in text
    if not named and not row_ship:
        return None
    arguments: dict[str, Any] = {}
    if row_no:
        arguments["board_row"] = row_no
    order_in_prompt = _latest_order_no(text)
    if order_in_prompt:
        arguments["order_no"] = order_in_prompt
    elif compact in _SHIP_NOW:
        arguments["ship_all"] = True
    elif not row_no and len(context_orders) == 1:
        arguments["order_no"] = context_orders[0]
    elif not row_no and (any(token in compact for token in SHIP_ALL_SPEECH) or compact in {"确认成品发货", "办成品发货", "确认发货", "办发货"}):
        arguments["ship_all"] = True
    parsed = buyer_todo.parse_question(text)
    if parsed.get("mold_batch"):
        arguments["batch"] = parsed["mold_batch"]
    if parsed.get("mold_family"):
        arguments["mold"] = parsed["mold_family"]
    elif arguments.get("order_no") or arguments.get("board_row"):
        context_parsed = buyer_todo.parse_question(context_text or "")
        if context_parsed.get("mold_batch"):
            arguments["batch"] = context_parsed["mold_batch"]
        if context_parsed.get("mold_family"):
            arguments["mold"] = context_parsed["mold_family"]
    return arguments


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
    order_no: str | None = Field(default=None, max_length=80, description="查询结果中的订单号，例如 EO-260923-0KKR。禁止使用内部数字 id。只剩一张可发货订单时可以不填。")
    board_row: int | None = Field(default=None, ge=1, description="可成品发货表第一列 NO.。说第N行或 NO.N 时填这个，不要用待办表行号。")
    ship_all: bool = Field(default=False, description="发货吧 / 全部发货时为 true，锁定当前可成品发货列表。")
    order_nos: list[str] | None = Field(default=None, description="全部发货锁定后的订单号列表。")
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


def _product_model_context(payload: dict[str, Any]) -> dict[str, Any]:
    items = []
    for item in payload.get("items") or []:
        if not isinstance(item, dict):
            continue
        items.append({
            "station": item.get("station") or "待成品发货",
            "stationLabel": item.get("stationLabel") or "待成品发货",
            "orderNo": item.get("orderNo") or "",
            "moldNo": item.get("moldNo") or "",
            "moldFamily": item.get("moldFamily") or "",
            "moldBatch": item.get("moldBatch") or "",
            "outsourceTypeLabel": item.get("outsourceTypeLabel") or "",
            "partDetails": item.get("partDetails") or "",
            "inboundTargets": item.get("inboundTargets") or [],
            "lineCount": item.get("lineCount") or 0,
            "remainQty": item.get("remainQty") or 0,
        })
    return {
        "summary": payload.get("summary") or "",
        "item_count": len(items),
        "items": items,
    }


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
        "model_context": _product_model_context(payload),
        "model_context_complete": True,
        "source": "management-system ERP 加工商成品发货只读查询",
        "as_of": now().isoformat(),
        "limitations": [
            "只读。可发数量 = min(订单剩余, 已收原料−已发)。入库目标按 ERP processor-inbound-v1：零件/模具及明确末道进成品库，非末道或末道缺失进半成品库。",
            "拒单转下一家后，只有新加工商接单并完成供料后才能发货。",
        ],
    }


def _resolved_lines(data, item: dict[str, Any]) -> list[dict[str, Any]]:
    remain_parts = [
        part for part in item.get("parts") or []
        if int(part.get("remainQty") or 0) > 0
    ]
    remain = {
        int(part.get("orderPartId") or 0): part
        for part in remain_parts
        if int(part.get("orderPartId") or 0) > 0
    }
    if data.lines:
        chosen = []
        for line in data.lines:
            part_id = int(line.order_part_id)
            if part_id not in remain:
                # 模型常把可发表 NO.1 当成 order_part_id；NO. 不是 ERP 零件行 id。
                if 1 <= part_id <= len(remain_parts):
                    part_id = int(remain_parts[part_id - 1].get("orderPartId") or 0)
                elif len(remain_parts) == 1:
                    part_id = int(remain_parts[0].get("orderPartId") or 0)
                else:
                    raise DomainError("INVALID_TOOL_INPUT", "请说零件号或按可发数量全发，不要使用表格行号", 400)
            chosen.append({"order_part_id": part_id, "qty": line.qty})
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
        label = (part or {}).get("partNo") or line["order_part_id"]
        if allowed <= 0:
            raise DomainError("STATE_BLOCKED", f"零件 {label} 当前不可发", 409)
        if line["qty"] > allowed:
            received = int((part or {}).get("receivedQty") or 0)
            raise DomainError(
                "STATE_BLOCKED",
                f"零件 {label} 只能按已收原料发成品（已收 {received}，可发 {allowed}）",
                409,
            )
    return chosen


def _shippable(tokens: list[str] | None) -> list[dict[str, Any]]:
    return processor_fulfillment.query_product_items(
        processor_fulfillment.parse_question(""),
        processor_tokens=tokens,
    )


def _filter_shippable(items: list[dict[str, Any]], data) -> list[dict[str, Any]]:
    order_nos = {
        buyer_todo.normalize_order_no(item)
        for item in (getattr(data, "order_nos", None) or [])
        if item
    }
    wanted_order = buyer_todo.normalize_order_no(getattr(data, "order_no", None))
    mold = str(getattr(data, "mold", None) or "")
    batch = str(getattr(data, "batch", None) or "")
    hits = []
    for item in items:
        if order_nos and buyer_todo.normalize_order_no(item.get("orderNo")) not in order_nos:
            continue
        if wanted_order or mold or batch:
            if not buyer_todo.item_matches_identity(
                item, order_no=wanted_order if not order_nos else "", mold=mold, batch=batch
            ):
                continue
        hits.append(item)
    return hits


def _resolve_ship_items(data, tokens: list[str] | None) -> list[tuple[dict[str, Any], list[dict[str, Any]]]]:
    items = _shippable(tokens)
    if getattr(data, "board_row", None):
        index = int(data.board_row) - 1
        if index < 0 or index >= len(items):
            raise DomainError("NOT_FOUND", f"可成品发货没有第 {data.board_row} 行", 404)
        chosen = [items[index]]
    elif getattr(data, "ship_all", False) or getattr(data, "order_nos", None):
        chosen = _filter_shippable(items, data)
        if not chosen:
            raise DomainError("NOT_FOUND", "没有找到可成品发货的工单，请重新查询可成品发货", 404)
    elif getattr(data, "order_no", None) or getattr(data, "mold", None) or getattr(data, "batch", None):
        item = processor_fulfillment.find_product_order_by_identity(
            order_no=getattr(data, "order_no", None),
            mold=getattr(data, "mold", None),
            batch=getattr(data, "batch", None),
        )
        if not item:
            raise DomainError("NOT_FOUND", "没有找到可成品发货的工单，请用订单号、模具号和批次号重新查询", 404)
        chosen = [item]
    else:
        if not items:
            raise DomainError("NOT_FOUND", "当前没有可成品发货的订单", 404)
        if len(items) > 1:
            raise DomainError("AMBIGUOUS", "有多张可成品发货，请说 NO.几 或订单号", 409)
        chosen = [items[0]]
    resolved = []
    for item in chosen:
        _guard(item, tokens)
        _assert_erp_ship_stage(item)
        resolved.append((item, _resolved_lines(data, item)))
    return resolved


def _assert_erp_ship_stage(item: dict[str, Any]) -> None:
    stage = str(item.get("stage") or "").casefold()
    if not stage or stage in processor_fulfillment.ERP_PRODUCT_SHIP_STAGES:
        return
    raise DomainError(
        "STATE_BLOCKED",
        processor_fulfillment.product_ship_blocked_message(item.get("orderNo")),
        409,
    )


def _lookup(data, tokens: list[str] | None) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    resolved = _resolve_ship_items(data, tokens)
    return resolved[0]


def _ship_detail_lines(item: dict[str, Any], lines: list[dict[str, Any]]) -> list[str]:
    parts = {int(part.get("orderPartId") or 0): part for part in item.get("parts") or []}
    details = []
    for line in lines:
        part = parts.get(line["order_part_id"]) or {}
        details.append(
            f"{part.get('partNo') or line['order_part_id']}×{line['qty']}"
            f"（订单{part.get('orderQty')}/已收{part.get('receivedQty')}/已发{part.get('shippedQty')}/可发{part.get('remainQty')}，"
            f"首道{part.get('isFirstOperationLabel')}/末道{part.get('isEndOperationLabel')}→{part.get('inboundTargetLabel')}）"
        )
    return details


def preview(db, user, key: str, data) -> tuple[dict[str, Any], dict[str, Any]]:
    resolved = _resolve_ship_items(data, _tokens(db, user))
    item, lines = resolved[0]
    display = {
        **identity_display(item),
        "委外类型": item.get("outsourceTypeLabel") or item.get("outsourceType"),
        "操作": "成品发货" if len(resolved) == 1 else f"成品发货（{len(resolved)}张）",
        "入库目标": "、".join(item.get("inboundTargets") or []),
        "明细": "；".join(_ship_detail_lines(item, lines)),
        "说明": "本人确认后写入 ERP。下一步仓库按同一入库目标做回厂入库确认。",
    }
    if len(resolved) > 1:
        display["订单"] = "、".join(str(row.get("orderNo") or "") for row, _ in resolved if row.get("orderNo"))
        display["明细"] = "；".join(
            f"{row.get('orderNo')} {_ship_detail_lines(row, row_lines)[0]}" if _ship_detail_lines(row, row_lines) else str(row.get("orderNo") or "")
            for row, row_lines in resolved
        )
    if data.logistics_company:
        display["物流公司"] = data.logistics_company
    if data.tracking_no:
        display["运单号"] = data.tracking_no
    item = dict(item)
    item["shipLines"] = lines
    item["shipBundle"] = [
        {"orderNo": row.get("orderNo"), "orderId": row.get("orderId"), "lines": row_lines}
        for row, row_lines in resolved
    ]
    return item, display


def execute_tool(db, user, key: str, arguments: dict | None, run=None) -> dict[str, Any]:
    if key == TODO_TOOL:
        return execute_query(db, user, arguments, run=run)
    _require_execute(db, user)
    data = parse(key, arguments)
    item, display = preview(db, user, key, data)
    dumped = data.model_dump(mode="json")
    bundle = item.get("shipBundle") or []
    if len(bundle) > 1:
        dumped["ship_all"] = True
        dumped["order_nos"] = [row.get("orderNo") for row in bundle if row.get("orderNo")]
        dumped["order_no"] = None
        dumped["mold"] = None
        dumped["batch"] = None
        dumped["lines"] = []
    else:
        dumped["lines"] = [
            {"order_part_id": int(line["order_part_id"]), "qty": int(line["qty"])}
            for line in item.get("shipLines") or []
        ]
    return {
        "data": [],
        "source": "agent_proposal",
        "as_of": now().isoformat(),
        "proposal": {
            "kind": KIND_BY_TOOL[key],
            "action": ACTION_BY_TOOL[key],
            "requires_approval": False,
            "input": dumped,
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
    _, display = preview(db, user, SHIP_TOOL, data)
    if content_hash(display) != content_hash(proposal["display"]):
        raise DomainError("VERSION_CONFLICT", "可发数量或入库目标已变化，请重新查询后准备", 409)
    return proposal, data


def confirm(db, user, payload):
    _, data = validate_intent(db, user, payload)
    resolved = _resolve_ship_items(data, _tokens(db, user))
    jobs = []
    for item, lines in resolved:
        order_id = item.get("orderId")
        if not order_id:
            raise DomainError("STATE_BLOCKED", "这张待办还没有委外订单，不能发货", 409)
        jobs.append({
            "native_id": f"order:{order_id}",
            "path": "entrust/fulfillment/product-shipment",
            "body": {
                "order_id": order_id,
                "lines": lines,
                "logistics_company": data.logistics_company,
                "tracking_no": data.tracking_no,
                "remark": data.remark,
            },
        })
    item, _ = resolved[0]
    if len(jobs) == 1:
        result = post_erp(
            db, user, jobs[0]["path"], jobs[0]["body"],
            intent_id=payload.get("_intent_id"), action="processor_product_ship",
            native_id=jobs[0]["native_id"],
        )
    else:
        result = dispatch_erp_batch(
            db, user,
            intent_id=payload.get("_intent_id") or "",
            action="processor_product_ship",
            jobs=jobs,
        )
    shipment_no = _shipment_no_from_erp(result)
    identity = {
        "order_no": item.get("orderNo") or data.order_no,
        "mold": item.get("moldFamily") or item.get("moldNo") or data.mold,
        "batch": item.get("moldBatch") or item.get("moldNo") or data.batch,
    }
    targets = item.get("inboundTargets") or []
    receipt = {
        **identity,
        "action": "processor_product_ship",
        "status": "CONFIRMED",
        "erp": clip_ship_erp(result),
        "inboundTargets": targets,
        "nextHint": _SHIP_NEXT_HINT,
        "model_context": {
            "action": "processor_product_ship",
            "status": "CONFIRMED",
            "orderNo": identity["order_no"],
            "mold": identity["mold"],
            "batch": identity["batch"],
            "inboundTargets": targets,
            "nextHint": _SHIP_NEXT_HINT,
        },
    }
    if shipment_no:
        receipt["shipment_no"] = shipment_no
        receipt["model_context"]["shipmentNo"] = shipment_no
    if len(resolved) > 1:
        order_nos = [row.get("orderNo") for row, _ in resolved if row.get("orderNo")]
        receipt["order_nos"] = order_nos
        receipt["model_context"]["orderNos"] = order_nos
    return receipt
