"""Registered read-only ERP outsource follow-up tools.

Two tools, matching the follow-up skill: the ERP 委外待办 board, and one
ticket's progress/timeline.
"""
from __future__ import annotations

import re
from typing import Any, Literal

from pydantic import Field, ValidationError, model_validator

from domain_packs.mold.erp.procurement.erp_outsource_scope import (
    require_allow,
    supplier_codes_for,
)
from domain_packs.mold.ports.db import now
from domain_packs.mold.ports.errors import DomainError
from domain_packs.mold.ports.schemas import StrictModel
from domain_packs.mold.tools.erp.procurement.outsource_queries import buyer_todo, timeline

BOARD_TOOL = "query_erp_outsource_followup_board"
PROGRESS_TOOL = "query_erp_outsource_order_progress"
PROCESSOR_BOARD_TOOL = "query_erp_outsource_processor_board"
PROCESSOR_PROGRESS_TOOL = "query_erp_outsource_processor_progress"
BUYER_TOOL_KEYS = (BOARD_TOOL, PROGRESS_TOOL)
PROCESSOR_TOOL_KEYS = (PROCESSOR_BOARD_TOOL, PROCESSOR_PROGRESS_TOOL)
TOOL_KEYS = BUYER_TOOL_KEYS + PROCESSOR_TOOL_KEYS
RETIRED_TOOLS = (
    "query_buyer_todo",
    "query_unoutsourced_parts",
    "query_quote_compare",
    "query_outsource_timeline",
    "query_fulfillment_gap",
)

SKILL_SPECS = {
    "outsource_followup_query": {
        "name": "委外跟单进度查询",
        "tools": [BOARD_TOOL],
        "optional_tools": [PROGRESS_TOOL],
        "activation_tools": list(BUYER_TOOL_KEYS),
        "activation_queries": [
            "委外跟单", "零件委外", "工序委外", "委外待办", "委外项目", "委外订单", "委外单子",
            "待采购填报价", "待填价", "待发询价", "待报价", "待下单",
            "待接单", "全部拒单", "委外时间线", "委外到哪一步", "有没有委外", "有委外",
            "几个委外", "有几个", "多少委外",
        ],
        "auto_activation_queries": [
            "委外跟单", "零件委外", "工序委外", "委外待办", "委外项目", "委外订单",
            "待采购填报价", "待填价", "待发询价", "待报价", "待下单",
            "待接单", "全部拒单", "委外时间线", "委外到哪一步", "所有委外", "全部委外",
            "委外单子", "有没有委外", "有委外", "几个委外", "有几个", "多少委外",
        ],
        "priority_patterns": [
            "零件委外|工序委外|委外跟单|委外待办|委外订单|委外项目|委外单子|待填价|委外时间线|有几个委外|几个委外",
        ],
        "requires_tool_evidence": True,
        "suppress_tool_search_on_auto_activation": True,
        "host_auto_invoke_empty_arguments": True,
    },
    "outsource_processor_query": {
        "name": "委外加工商待办查询",
        "tools": [PROCESSOR_BOARD_TOOL],
        "optional_tools": [PROCESSOR_PROGRESS_TOOL],
        "activation_tools": list(PROCESSOR_TOOL_KEYS),
        "activation_queries": [
            "我的委外", "待报价", "待接单", "加工商待办", "有几个", "有没有", "待办",
        ],
        "auto_activation_queries": [
            "我的委外", "待报价", "待接单", "加工商待办", "有几个", "有没有", "待办",
        ],
        "priority_patterns": ["我的委外|加工商待办|待报价|待接单|有几个|有没有"],
        "requires_tool_evidence": True,
        "suppress_tool_search_on_auto_activation": True,
        "host_auto_invoke_empty_arguments": True,
    },
}

TOOL_SPECS = {
    BOARD_TOOL: {
        "description": "只读读取 ERP 委外待办看板：进度、委外类型、模具号、零件明细和价格。有没有委外、几个订单、全部待办都直接查责任域看板；有模具号则只过滤该模具。不要先追问模具号。采购员/主管使用。",
        "permission": "erp_outsource_buyer.read",
    },
    PROGRESS_TOOL: {
        "description": "只读查看指定模具号下一张委外待办的进度时间线与零件/价格。必须传入模具号或批次号。采购员/主管使用。",
        "permission": "erp_outsource_buyer.read",
    },
    PROCESSOR_BOARD_TOOL: {
        "description": "只读读取本加工商可见的委外待办：待报价、待接单等。不含其他供应商订单，不含我方内部价。",
        "permission": "erp_outsource_processor.read",
    },
    PROCESSOR_PROGRESS_TOOL: {
        "description": "只读查看本加工商名下某一模具的委外进度。必须传入模具号。",
        "permission": "erp_outsource_processor.read",
    },
}

TOOL_NAMES = {
    BOARD_TOOL: "查询 ERP 委外待办",
    PROGRESS_TOOL: "查询委外单进度",
    PROCESSOR_BOARD_TOOL: "查询加工商委外待办",
    PROCESSOR_PROGRESS_TOOL: "查询加工商委外进度",
}

TODO_TABS = Literal[
    "buyer_quote", "inquiry_send", "supplier_quote", "place_order",
    "order_approval", "accept", "exhausted",
]


class FollowupBoardInput(StrictModel):
    question: str | None = Field(default=None, max_length=500, description="用户原话。")
    todo_tab: TODO_TABS | None = Field(default=None, description="ERP 待办分站。")
    outsource_kind: Literal["part", "operation", "mold", "all"] | None = None
    mold: str | None = Field(default=None, max_length=40, description="模具号，例如 M260063。看全部待办时可空。")
    batch: str | None = Field(default=None, max_length=40, description="模具批次，例如 M260063-P4。")

    @model_validator(mode="after")
    def strip_values(self):
        for name in ("question", "mold", "batch"):
            value = getattr(self, name)
            if isinstance(value, str):
                setattr(self, name, value.strip() or None)
        return self


class FollowupProgressInput(StrictModel):
    question: str | None = Field(default=None, max_length=500, description="用户原话。")
    mold: str | None = Field(default=None, max_length=40, description="模具号，必填，例如 M260063。")
    batch: str | None = Field(default=None, max_length=40)

    @model_validator(mode="after")
    def strip_values(self):
        for name in ("question", "mold", "batch"):
            value = getattr(self, name)
            if isinstance(value, str):
                setattr(self, name, value.strip() or None)
        return self


INPUT_MODELS = {
    BOARD_TOOL: FollowupBoardInput,
    PROGRESS_TOOL: FollowupProgressInput,
    PROCESSOR_BOARD_TOOL: FollowupBoardInput,
    PROCESSOR_PROGRESS_TOOL: FollowupProgressInput,
}

MOLD_FAMILY_CODE = re.compile(r"(?i)^M\d{5,}$")
MOLD_BATCH_CODE = re.compile(r"(?i)^M\d{5,}-P\d+$")


def _asks_full_board(text: str) -> bool:
    source = text or ""
    if any(word in source for word in (
        "所有委外", "全部委外", "所有待办", "全部待办",
        "有没有委外", "有委外", "几个委外", "多少委外", "委外单子",
        "委外订单吗", "委外项目吗",
    )):
        return True
    return ("所有" in source or "全部" in source) and any(
        word in source for word in ("委外项目", "委外订单", "委外待办", "待办事项")
    )


def _has_mold_scope(parsed: dict[str, str]) -> bool:
    return bool(str(parsed.get("mold_family") or "").strip() or str(parsed.get("mold_batch") or "").strip())


def _need_mold_code() -> dict[str, Any]:
    return {
        "status": "NEED_MOLD_CODE",
        "summary": "请先提供模具号（例如 M260063），以便查看这一票委外进度。",
        "moldFamily": "",
        "moldBatch": "",
    }


def _scope(data: Any, parsed: dict[str, str], *, spoken: str = "") -> dict[str, str]:
    result = dict(parsed)
    spoken_text = spoken or getattr(data, "question", None) or ""
    if parsed.get("station"):
        result["station"] = parsed["station"]
    elif getattr(data, "todo_tab", None) and not spoken_text.strip():
        result["station"] = data.todo_tab
    if parsed.get("outsource_type"):
        result["outsource_type"] = parsed["outsource_type"]
    elif getattr(data, "outsource_kind", None) in {"part", "operation", "mold"}:
        result["outsource_type"] = data.outsource_kind
    batch = str(getattr(data, "batch", None) or "").strip().upper()
    mold = str(getattr(data, "mold", None) or "").strip().upper()
    if MOLD_BATCH_CODE.match(batch) or MOLD_BATCH_CODE.match(mold):
        result["mold_batch"] = batch or mold
        result["mold_family"] = ""
    elif MOLD_FAMILY_CODE.match(mold) or MOLD_FAMILY_CODE.match(batch):
        result["mold_family"] = mold or batch
        result["mold_batch"] = ""
    return result


def _compact_board_items(items: list[Any]) -> list[dict[str, Any]]:
    compact: list[dict[str, Any]] = []
    for item in items[:30]:
        if not isinstance(item, dict):
            continue
        details = str(item.get("partDetails") or "")
        if len(details) > 80:
            details = details[:80] + "…"
        compact.append({
            "orderNo": item.get("orderNo") or "",
            "mold": item.get("moldFamily") or item.get("moldNo") or "",
            "batch": item.get("moldBatch") or item.get("moldNo") or "",
            "station": item.get("stationLabel") or item.get("station"),
            "kind": item.get("outsourceTypeLabel") or item.get("outsourceType"),
            "parts": details,
            "ourQuote": item.get("ourQuoteAmount"),
            "deal": item.get("finalDealAmount"),
            "pending": item.get("pendingQuoteSuppliers") or "",
        })
    return compact


def _model_context(payload: dict[str, Any]) -> dict[str, Any]:
    if payload.get("status") == "NEED_MOLD_CODE":
        return {"status": "NEED_MOLD_CODE", "summary": payload.get("summary") or ""}
    if payload.get("steps") is not None:
        return {
            "summary": payload.get("summary") or "",
            "currentStep": payload.get("currentStep"),
            "steps": payload.get("steps") or [],
        }
    items = [item for item in (payload.get("items") or []) if isinstance(item, dict)]
    return {
        "summary": payload.get("summary") or "",
        "counts": payload.get("counts") or {},
        "typeCounts": payload.get("typeCounts") or {},
        "item_count": len(items),
        "truncated": bool(payload.get("truncated")),
        "items": _compact_board_items(items),
    }


def tool_schema(key: str) -> dict[str, Any]:
    model = INPUT_MODELS[key]
    return {
        "type": "function",
        "function": {
            "name": key,
            "description": TOOL_SPECS[key]["description"],
            "parameters": model.model_json_schema(),
        },
    }


def execute_tool(db, user, key: str, arguments: dict | None, run=None) -> dict[str, Any]:
    processor = key in PROCESSOR_TOOL_KEYS
    if processor:
        require_allow(db, user, "erp_outsource_processor.read", "仅委外加工商可查询本供应商待办")
    else:
        # Follow-up board is read-only. ERP purchase_buyer_scope is enforced on
        # prepare/execute writes, not on “有没有委外单子” listing.
        require_allow(db, user, "erp_outsource_buyer.read", "仅委外采购员或采购主管可查询跟单看板")
    model = INPUT_MODELS[key]
    try:
        data = model.model_validate(arguments or {})
    except ValidationError as error:
        raise DomainError("INVALID_TOOL_INPUT", "委外查询参数无效：" + error.errors()[0]["msg"]) from None
    question = data.question or str(getattr(run, "prompt", "") or "")
    board = key in {BOARD_TOOL, PROCESSOR_BOARD_TOOL}
    parser = buyer_todo.parse_question if board else timeline.parse_question
    scoped = _scope(data, parser(question), spoken=question)
    tokens = supplier_codes_for(db, user) if processor else None
    if processor and tokens == []:
        payload = {
            "status": "NO_SUPPLIER_SCOPE",
            "summary": "当前账号未绑定加工商范围，看不到委外待办。",
            "items": [],
        }
    elif board:
        payload = buyer_todo.run(scoped, processor_tokens=tokens)
    elif not _has_mold_scope(scoped):
        payload = _need_mold_code()
    elif processor:
        payload = buyer_todo.run(scoped, processor_tokens=tokens)
    else:
        payload = timeline.run(scoped)
    return {
        "data": payload,
        "model_context": _model_context(payload),
        "source": "management-system ERP 委外待办只读查询",
        "as_of": now().isoformat(),
        "role_lens": "processor" if processor else "buyer",
        "limitations": [
            "只读查询 ERP 委外待办事实，不改数据。",
            "加工商视角不含其他供应商订单和我方内部价。" if processor
            else "有没有委外、几个订单默认查责任域待办看板；单票进度才需要模具号。",
        ],
    }
