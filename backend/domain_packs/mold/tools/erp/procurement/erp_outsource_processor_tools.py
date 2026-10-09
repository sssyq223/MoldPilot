"""Processor-side outsource prepare tools: quote, accept, reject."""
from __future__ import annotations

import re
from typing import Any

from pydantic import Field, ValidationError, field_validator, model_validator
from sqlalchemy import select

from domain_packs.mold import models as m
from domain_packs.mold.authorization import fingerprint
from domain_packs.mold.erp.procurement.erp_outsource_http import dispatch_erp_batch, post_erp
from domain_packs.mold.erp.procurement.erp_outsource_scope import require_allow, supplier_codes_for
from domain_packs.mold.ports.bpm import content_hash
from domain_packs.mold.ports.confirmation_policy import (
    proposal_confirmation_policy,
    proposal_run_is_open,
)
from domain_packs.mold.ports.db import now
from domain_packs.mold.ports.errors import DomainError
from domain_packs.mold.ports.schemas import StrictModel
from domain_packs.mold.tools.erp.procurement.outsource_identity import (
    CamelModel,
    identity_batch,
    identity_mold,
    identity_order_no,
)
from domain_packs.mold.tools.erp.procurement.outsource_queries import buyer_todo, processor_reject_reason

QUOTE_TOOL = "prepare_erp_outsource_processor_quote"
ACCEPT_TOOL = "prepare_erp_outsource_processor_accept"
REJECT_TOOL = "prepare_erp_outsource_processor_reject"
TOOL_KEYS = (QUOTE_TOOL, ACCEPT_TOOL, REJECT_TOOL)
QUOTEABLE = {"sent", "draft", "draft_quoted"}

SKILL_SPECS = {
    "outsource_processor_ops": {
        "name": "委外加工商办理",
        "tools": ["query_erp_outsource_processor_board"],
        "optional_tools": list(TOOL_KEYS),
        "activation_tools": ["query_erp_outsource_processor_board"],
        "activation_queries": [
            "提交报价", "我要报价", "加工商报价",
            "我要接单", "确认接单", "我接了", "这单我接了",
            "拒绝接单", "我要拒单", "拒单",
        ],
        "auto_activation_queries": [
            "提交报价", "我要报价", "加工商报价",
            "我要接单", "确认接单", "我接了", "这单我接了", "全部接单", "NO.1接单",
            "拒绝接单", "我要拒单",
        ],
        "priority_patterns": ["加工商报价|提交报价|我要报价|报价\\s*\\d+|确认接单|我要接单|我接了|全部接单|NO\\.?\\s*\\d+\\s*接单|拒绝接单|我要拒单"],
        "activation_route": "authorized",
        "suppress_tool_search_on_auto_activation": False,
    },
}

TOOL_SPECS = {
    QUOTE_TOOL: {
        "description": "准备提交本加工商报价确认卡。用户说 NO.几、第N行、订单号或模具/批次号并给出金额时立刻锁定待报价行，不要要单价数量，不要口头请用户确认。交期未说时用询价单交期；含税未说按含税，都写在确认卡上。工序委外不走报价。本人确认后才写入 ERP。",
        "permission": "erp_outsource_processor.execute",
    },
    ACCEPT_TOOL: {
        "description": "准备确认接单。用户说 NO.N / 第N行接单、全部接单或订单号时立刻锁定，不要再口头请用户确认。NO. 是我的委外待办第一列。当前分站必须是待接单。禁止使用内部数字 id。本人确认后才写入 ERP。",
        "permission": "erp_outsource_processor.execute",
    },
    REJECT_TOOL: {
        "description": "准备拒绝接单。用查询结果中的订单号，必要时加模具号、批次号定位。拒单原因用 ERP 当前启用的中文名称或编码；没说原因时用第一条启用原因。禁止使用已停用原因或内部数字 id。本人确认后才写入 ERP。",
        "permission": "erp_outsource_processor.execute",
    },
}

TOOL_NAMES = {
    QUOTE_TOOL: "准备提交加工商报价",
    ACCEPT_TOOL: "准备确认接单",
    REJECT_TOOL: "准备拒绝接单",
}

ACCEPT_SPEECH = ("我接了", "这单我接了", "确认接单", "我要接单", "帮我接单", "接这单")
ACCEPT_ALL_SPEECH = ("全部接单", "全都接", "都接了", "全接单", "这几个都接", "全部都接", "都接单")
ACCEPT_LOOK_UP = ("有几个", "有哪些", "有没有", "到哪一步", "不要接", "不接单")
ACCEPT_WRITE = re.compile(
    r"(?:全部接单|全都接|都接了|全接单|这几个都接|全部都接|都接单|"
    r"我接了|这单我接了|确认接单|我要接单|帮我接单|接这单|"
    r"(?:NO[.\u3002\uff0e]?\s*\d+|第\s*(?:\d+|[一二三四五六七八九十])\s*行)\s*接单|"
    r"(?<![待不])接单)",
    re.IGNORECASE,
)
REJECT_SPEECH = ("拒绝接单", "我要拒单", "拒这单", "这单我拒了", "拒单吧", "拒单")
REJECT_LOOK_UP = ("有几个", "有哪些", "有没有", "不要拒", "不拒单", "全部拒单")
REJECT_REASON = re.compile(r"(?:因为|原因|由于)[:：,\s]*(.+)$")
CONFIRM_ONLY = re.compile(r"^(?:确认|是的|好的|可以|同意|对|嗯|行|好)(?:吧|了)?$")
QUOTE_FOLLOWUP = ("提交报价", "办理报价", "确认办理", "确认报价", "办理提交", "填报价")
QUOTE_UTTERANCE = re.compile(
    r"(?:NO\.?\s*\d+|第\s*(?:\d+|[一二三四五六七八九十])\s*行|"
    r"EO[\-\u2010-\u2015\u2212\uff0d]\d{6}[\-\u2010-\u2015\u2212\uff0d][A-Z0-9]+|"
    r"M\d{5,}(?:-P\d+)?)"
    r"[^\n]{0,80}",
    re.IGNORECASE,
)
SPOKEN_DATE = re.compile(r"(20\d{2}-\d{2}-\d{2})")
# 上限 / 我方报价属于采购员填价。加工商说「NO.1填报价 3000」仍是本角色报价。
PROCESSOR_QUOTE_BLOCK = (
    "上限", "我方报价", "发询价", "发送询价",
    "有几个", "有哪些", "有没有", "不要报价", "不报价",
)
PRICE_ALIASES = ("unitPrice", "quote_amount", "quoteAmount", "amount", "price", "报价金额")


def _plain_int(value: Any) -> int | None:
    if value is None or value is False or value == "":
        return None
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, float) and value.is_integer():
        return int(value)
    text = str(value).strip().replace(",", "")
    if re.fullmatch(r"\d+(?:\.0+)?", text):
        return int(float(text))
    return None


def _board_row_value(value: Any) -> int | None:
    number = _plain_int(value)
    if number is not None:
        return number
    if value is None or value == "":
        return None
    return buyer_todo.spoken_board_row_number(str(value))


def _looks_like_row_label(value: Any) -> bool:
    return buyer_todo.spoken_board_row_number(str(value or "")) is not None and not str(value).strip().isdigit()


def is_spoken_processor_quote(text: str) -> bool:
    """Processor quote locked to NO. / 订单号 / 模具批次 + 金额."""
    if any(token in text for token in PROCESSOR_QUOTE_BLOCK):
        return False
    if "报价" not in text or "询价" in text:
        return False
    return buyer_todo.QUOTE_AMOUNT.search(text) is not None


def _is_quote_followup(text: str) -> bool:
    stripped = (text or "").strip()
    if CONFIRM_ONLY.match(stripped):
        return True
    if any(token in stripped for token in ("不要", "不报价", "有几个", "有哪些", "有没有")):
        return False
    return any(token in stripped for token in QUOTE_FOLLOWUP)


def spoken_quote_arguments(prompt: str, context_text: str = "") -> dict[str, Any] | None:
    """Parse NO.n / 第N行报价 into prepare_erp_outsource_processor_quote arguments.

    Row number is the processor board NO., not the buyer follow-up board.
    A bare 确认 reuses the previous quote sentence, not a date the model invented.
    """
    text = (prompt or "").strip()
    source = text
    extra = text
    if not is_spoken_processor_quote(text):
        if not _is_quote_followup(text):
            return None
        found = None
        for match in QUOTE_UTTERANCE.finditer(context_text or ""):
            if is_spoken_processor_quote(match.group(0)):
                found = match.group(0)
        if not found:
            return None
        source = found
    amount = buyer_todo.QUOTE_AMOUNT.search(source)
    row_no = buyer_todo.spoken_board_row_number(source)
    order = buyer_todo.ORDER_NO.search(source)
    parsed = buyer_todo.parse_question(source)
    if amount is None:
        return None
    arguments: dict[str, Any] = {
        "unit_price": float(amount.group(1)),
        "tax_included": "不含税" not in source and "不含税" not in extra,
    }
    if row_no:
        arguments["board_row"] = row_no
    if order:
        arguments["order_no"] = buyer_todo.normalize_order_no(order.group(1))
    if parsed.get("mold_batch"):
        arguments["batch"] = parsed["mold_batch"]
        arguments.setdefault("mold", parsed["mold_batch"].split("-P", 1)[0])
    elif parsed.get("mold_family"):
        arguments["mold"] = parsed["mold_family"]
    spoken_date = SPOKEN_DATE.search(source)
    if spoken_date is None and extra != source:
        spoken_date = SPOKEN_DATE.search(extra)
    if spoken_date:
        arguments["delivery_date"] = spoken_date.group(1)
    return arguments


def _has_quote_target(arguments: dict[str, Any] | None) -> bool:
    return bool(
        arguments
        and (
            arguments.get("board_row")
            or arguments.get("order_no")
            or arguments.get("batch")
            or arguments.get("mold")
        )
    )


def _quote_identity_from_item(item: dict[str, Any]) -> dict[str, Any]:
    arguments: dict[str, Any] = {}
    order = item.get("orderNo") or item.get("rejectedOrderNo") or item.get("order_no")
    if order:
        arguments["order_no"] = buyer_todo.normalize_order_no(order)
    batch = item.get("moldBatch") or item.get("batch") or ""
    if batch:
        arguments["batch"] = batch
        arguments.setdefault("mold", str(batch).split("-P", 1)[0])
    else:
        mold = item.get("moldFamily") or item.get("moldNo") or item.get("mold")
        if mold:
            arguments["mold"] = mold
    return arguments


def spoken_quote_from_board(prompt: str, items) -> dict[str, Any] | None:
    """Lock 报价+金额 to the only 待报价 row when speech omitted NO./订单号."""
    locked = spoken_quote_arguments(prompt)
    if locked and _has_quote_target(locked):
        return locked
    if any(token in (prompt or "") for token in PROCESSOR_QUOTE_BLOCK):
        return None
    if "报价" not in (prompt or "") or "询价" in (prompt or ""):
        return None
    amount = buyer_todo.QUOTE_AMOUNT.search(prompt or "")
    if amount is None and not locked:
        return None
    quote_rows = [
        item for item in (items or [])
        if isinstance(item, dict)
        and str(item.get("station") or item.get("stationLabel") or "") in {"supplier_quote", "待报价"}
    ]
    if len(quote_rows) != 1:
        return locked
    item = quote_rows[0]
    arguments: dict[str, Any] = dict(locked) if locked else {
        "unit_price": float(amount.group(1)),
        "tax_included": "不含税" not in (prompt or ""),
    }
    arguments.update(_quote_identity_from_item(item))
    if not _has_quote_target(arguments):
        arguments["board_row"] = 1
    return arguments


def is_spoken_processor_accept(text: str) -> bool:
    prompt = text or ""
    if any(token in prompt for token in ACCEPT_LOOK_UP):
        return False
    if any(token in prompt for token in ("拒", "不接")):
        return False
    return bool(ACCEPT_WRITE.search(prompt))


def spoken_accept_arguments(prompt: str, context_text: str = "") -> dict[str, Any] | None:
    """Parse accept speech into prepare_erp_outsource_processor_accept arguments."""
    text = prompt or ""
    if not is_spoken_processor_accept(text):
        return None
    identity_source = f"{text}\n{context_text or ''}"
    arguments: dict[str, Any] = {}
    parsed = buyer_todo.parse_question(identity_source)
    if parsed.get("mold_family"):
        arguments["mold"] = parsed["mold_family"]
    if parsed.get("mold_batch"):
        arguments["batch"] = parsed["mold_batch"]
        arguments.setdefault("mold", parsed["mold_batch"].split("-P", 1)[0])
    if any(token in text for token in ACCEPT_ALL_SPEECH) or re.search(r"这[两三四五六七八九十\d]+[张单]都接", text):
        arguments["accept_all"] = True
        return arguments
    row_no = buyer_todo.spoken_board_row_number(text)
    if row_no:
        arguments["board_row"] = row_no
        return arguments
    order = buyer_todo.ORDER_NO.search(identity_source)
    if order:
        arguments["order_no"] = buyer_todo.normalize_order_no(order.group(1))
        return arguments
    if arguments.get("batch") or arguments.get("mold"):
        arguments["accept_all"] = True
        return arguments
    return None


def is_spoken_processor_reject(text: str) -> bool:
    prompt = text or ""
    if any(token in prompt for token in REJECT_LOOK_UP):
        return False
    return any(token in prompt for token in REJECT_SPEECH)


def spoken_reject_arguments(prompt: str, context_text: str = "") -> dict[str, Any] | None:
    text = prompt or ""
    if not is_spoken_processor_reject(text):
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
    reason = REJECT_REASON.search(text)
    if reason:
        arguments["reason_code"] = reason.group(1).strip()
    elif "报价过高" in text:
        arguments["reason_code"] = "报价过高"
    elif "产能" in text:
        arguments["reason_code"] = "产能"
    return arguments


class ProcessorQuoteInput(StrictModel):
    invitation_id: int | None = Field(default=None, ge=1, description="查询结果中本加工商的 invitationId。说了表格 NO. 或订单号时可以不填。")
    board_row: int | None = Field(default=None, ge=1, description="我的委外待办第一列 NO.。加工商说第N行或 NO.N 时填这个，不要用采购员完整表的行号。")
    order_no: str | None = Field(default=None, max_length=80, description="查询结果或拒单后重发询价仍沿用的订单号。说 EO- 加金额时填这个。")
    mold: str | None = Field(default=None, max_length=40, description="模具号，例如 M260063。待报价尚未下单时用模具号或批次号锁定。")
    batch: str | None = Field(default=None, max_length=40, description="批次号，例如 M260063-P4。")
    unit_price: float = Field(gt=0, description="本加工商报价（订单总额）。")
    delivery_date: str | None = Field(default=None, pattern=r"^\d{4}-\d{2}-\d{2}$", description="承诺交付日期 YYYY-MM-DD。没说时用询价单交期。")
    tax_included: bool = Field(default=True, description="报价是否含税。用户没说时按含税，并写在确认卡上。")
    note: str | None = Field(default=None, max_length=400)
    lead_time_days: int | None = Field(default=None, ge=1, le=365)

    @model_validator(mode="before")
    @classmethod
    def coerce_row_and_price(cls, value):
        """Accept NO.1 / 第1行 and empty invitation ids the model often sends."""
        if not isinstance(value, dict):
            return value
        data = dict(value)
        for alias in PRICE_ALIASES:
            if data.get("unit_price") in (None, "") and data.get(alias) not in (None, ""):
                data["unit_price"] = data[alias]
            data.pop(alias, None)
        row = _board_row_value(data.get("board_row"))
        invite_raw = data.get("invitation_id")
        if row is None and _looks_like_row_label(invite_raw):
            row = _board_row_value(invite_raw)
            invite_raw = None
        data["board_row"] = row
        data["invitation_id"] = None if invite_raw in (None, "") else _plain_int(invite_raw)
        order_raw = data.get("order_no") or data.get("orderNo")
        data["order_no"] = str(order_raw).strip().upper() or None if order_raw not in (None, "") else None
        data.pop("orderNo", None)
        if isinstance(data.get("tax_included"), str):
            tax = data["tax_included"].strip()
            if tax in {"含税", "是", "true", "True", "1"}:
                data["tax_included"] = True
            elif tax in {"不含税", "否", "false", "False", "0"}:
                data["tax_included"] = False
        return data

    @field_validator("mold", "batch", "note")
    @classmethod
    def strip_text(cls, value):
        return value.strip() or None if isinstance(value, str) else value

    @field_validator("order_no")
    @classmethod
    def strip_order(cls, value):
        return value.strip().upper() or None if isinstance(value, str) else value

    @field_validator("delivery_date", mode="before")
    @classmethod
    def empty_date(cls, value):
        if isinstance(value, str) and not value.strip():
            return None
        return value

    @model_validator(mode="after")
    def need_target(self):
        return self


class ProcessorAcceptInput(CamelModel):
    order_no: str | None = identity_order_no(default=None, max_length=80, description="查询结果中的订单号，例如 EO-260923-0KKR。说了表格 NO. 或全部接单时可省略。禁止使用内部数字 id。")
    board_row: int | None = Field(default=None, ge=1, description="我的委外待办第一列 NO.。说第N行或 NO.N 接单时填这个。")
    accept_all: bool = Field(default=False, description="全部接单时为 true，锁定当前待接单列表。")
    order_nos: list[str] | None = Field(default=None, description="全部接单锁定后的订单号列表，确认时按这个清单写入。")
    mold: str | None = identity_mold(default=None, max_length=40, description="模具号，例如 M260063。")
    batch: str | None = identity_batch(default=None, max_length=40, description="批次号，例如 M260063-P1。同一订单有多批次时必填。")

    @field_validator("order_no", "mold", "batch")
    @classmethod
    def strip_identity(cls, value):
        return value.strip() or None if isinstance(value, str) else value

    @model_validator(mode="before")
    @classmethod
    def coerce_row(cls, value):
        if not isinstance(value, dict):
            return value
        data = dict(value)
        row = _board_row_value(data.get("board_row") or data.get("boardRow"))
        if row:
            data["board_row"] = row
        return data

    @model_validator(mode="after")
    def need_target(self):
        if self.accept_all or self.board_row or self.order_no or self.order_nos:
            return self
        raise ValueError("请用表格 NO.、订单号，或说全部接单")


class ProcessorRejectInput(CamelModel):
    order_no: str = identity_order_no(min_length=3, max_length=80, description="查询结果中的订单号，例如 EO-260923-0KKR。禁止使用内部数字 id。")
    mold: str | None = identity_mold(default=None, max_length=40, description="模具号，例如 M260063。")
    batch: str | None = identity_batch(default=None, max_length=40, description="批次号，例如 M260063-P1。同一订单有多批次时必填。")
    reason_code: str | None = Field(default=None, max_length=80, description="ERP 当前启用的拒单原因编码或中文名称。没说时用第一条启用原因。")

    @field_validator("order_no", "mold", "batch")
    @classmethod
    def strip_identity(cls, value):
        return value.strip() or None if isinstance(value, str) else value

    @field_validator("reason_code")
    @classmethod
    def strip_reason(cls, value):
        return value.strip() or None if isinstance(value, str) else value


INPUT_MODELS = {
    QUOTE_TOOL: ProcessorQuoteInput,
    ACCEPT_TOOL: ProcessorAcceptInput,
    REJECT_TOOL: ProcessorRejectInput,
}

KIND_BY_TOOL = {
    QUOTE_TOOL: "erp_outsource_processor_quote",
    ACCEPT_TOOL: "erp_outsource_processor_accept",
    REJECT_TOOL: "erp_outsource_processor_reject",
}

ACTION_BY_TOOL = {
    QUOTE_TOOL: "confirm_erp_outsource_processor_quote",
    ACCEPT_TOOL: "confirm_erp_outsource_processor_accept",
    REJECT_TOOL: "confirm_erp_outsource_processor_reject",
}

LIMIT_BY_TOOL = {
    QUOTE_TOOL: "仅准备本加工商报价；本人确认后才调用 ERP。提交后重新查询。对加工商只说：待接单可以接单，否则等待采购处理。不要提区间、成交价、主管或总经理。",
    ACCEPT_TOOL: "仅准备确认接单；本人确认后才调用 ERP。接单成功后只说接单结果和下一步，不要引用历史拒单原因或备注。",
    REJECT_TOOL: "仅准备拒绝接单；本人确认后才调用 ERP。工序委外拒单后由 ERP 自动转下一家，不要自己选下一家。",
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
        detail = error.errors()[0]
        loc = ".".join(str(part) for part in detail.get("loc") or [])
        message = detail.get("msg") or "参数无效"
        if loc:
            message = f"{loc}：{message}"
        raise DomainError("INVALID_TOOL_INPUT", "加工商办理参数无效：" + message) from None


def _require_processor(db, user) -> None:
    require_allow(db, user, "erp_outsource_processor.execute", "仅委外加工商可办理报价、接单或拒单")


def _tokens(db, user) -> list[str] | None:
    tokens = supplier_codes_for(db, user)
    if tokens == []:
        raise DomainError("FORBIDDEN", "当前账号未绑定加工商范围，不能办理", 403)
    return tokens


def _guard_scope(item: dict[str, Any], invitation: dict[str, Any] | None, tokens: list[str] | None) -> None:
    if tokens is None:
        return
    if invitation is not None:
        if not buyer_todo.invitation_matches_processor(invitation, tokens):
            raise DomainError("FORBIDDEN", "这张询价邀请不属于本加工商", 403)
        return
    if not buyer_todo.item_mentions_processor(item, tokens):
        raise DomainError("FORBIDDEN", "这张工单不属于本加工商", 403)


def _item_from_processor_board(tokens: list[str] | None, row_no: int) -> dict[str, Any]:
    """NO. on 我的委外待办, already limited to this processor."""
    payload = buyer_todo.run(
        {
            "station": "",
            "mold_family": "",
            "mold_batch": "",
            "project_no": "",
            "outsource_type": "",
        },
        processor_tokens=tokens,
    )
    items = payload.get("items") or []
    if row_no < 1 or row_no > len(items):
        raise DomainError(
            "NOT_FOUND",
            f"我的委外待办没有 NO.{row_no}。请按当前表格第一列的 NO. 再说一次。",
            404,
        )
    return items[row_no - 1]


def _quoteable_invitation(item: dict[str, Any], tokens: list[str] | None) -> dict[str, Any] | None:
    for invitation in item.get("invitations") or []:
        if not isinstance(invitation, dict):
            continue
        if tokens is not None and not buyer_todo.invitation_matches_processor(invitation, tokens):
            continue
        if str(invitation.get("status") or "") in QUOTEABLE:
            return invitation
    return None


def _unique_processor_quote_item(tokens: list[str] | None) -> dict[str, Any] | None:
    payload = buyer_todo.run(
        {
            "station": "supplier_quote",
            "mold_family": "",
            "mold_batch": "",
            "project_no": "",
            "outsource_type": "",
        },
        processor_tokens=tokens,
    )
    rows = [
        item for item in payload.get("items") or []
        if isinstance(item, dict)
        and str(item.get("station") or item.get("stationLabel") or "") in {"supplier_quote", "待报价"}
    ]
    return rows[0] if len(rows) == 1 else None


def _item_from_processor_order(
    tokens: list[str] | None,
    order_no: str,
    mold: str | None = None,
    batch: str | None = None,
) -> dict[str, Any]:
    item = buyer_todo.find_item_by_identity(
        order_no=order_no or None,
        mold=mold,
        batch=batch,
        station="supplier_quote",
    )
    if not item:
        item = buyer_todo.find_item_by_identity(order_no=order_no or None, mold=mold, batch=batch)
    if not item:
        raise DomainError("NOT_FOUND", "没有找到这张仍待报价的询价，请用表格 NO.、订单号或批次号重新查询", 404)
    _guard_scope(item, None, tokens)
    return item


def _resolve_processor_quote(data, tokens: list[str] | None):
    """Turn 我的委外待办 NO. / 订单号 into invitationId, and fill a missing due date from the inquiry."""
    updates: dict[str, Any] = {}
    item = None
    if not data.invitation_id:
        if data.board_row:
            item = _item_from_processor_board(tokens, int(data.board_row))
            label = f"NO.{int(data.board_row)}"
        elif data.order_no or data.mold or getattr(data, "batch", None):
            item = _item_from_processor_order(
                tokens,
                str(data.order_no or ""),
                data.mold,
                getattr(data, "batch", None),
            )
            label = str(data.order_no or data.batch or data.mold or "该行")
        else:
            item = _unique_processor_quote_item(tokens)
            if not item:
                raise DomainError(
                    "INVALID_TOOL_INPUT",
                    "请用表格 NO.、订单号或批次号指定要报的那一行，例如 NO.1报价66666 或 M260063-P4报价66666。",
                )
            label = "当前唯一待报价"
        if item.get("outsourceType") == "operation":
            raise DomainError("STATE_BLOCKED", f"{label} 是工序委外，不走报价，只办接单或拒单", 409)
        if item.get("station") != "supplier_quote":
            raise DomainError(
                "STATE_BLOCKED",
                f"{label} 当前是{item.get('stationLabel') or item.get('station')}，不能报价",
                409,
            )
        invitation = _quoteable_invitation(item, tokens)
        if not invitation or not invitation.get("invitationId"):
            raise DomainError("STATE_BLOCKED", f"{label} 已报过价或没有待报价邀请，不能再报", 409)
        updates["invitation_id"] = int(invitation["invitationId"])
    if not data.delivery_date:
        if item is None and data.invitation_id:
            item, _invitation = buyer_todo.find_invitation(data.invitation_id, mold=data.mold)
        due = buyer_todo.iso_date((item or {}).get("deliveryDate"))
        if not due:
            raise DomainError(
                "INVALID_TOOL_INPUT",
                "这张询价单没有交期。请在报价时写上承诺交期，例如 NO.1报价3000，交期2026-10-15。",
            )
        updates["delivery_date"] = due
    if updates:
        data = data.model_copy(update=updates)
    return data


def _lookup_quote(data, tokens: list[str] | None) -> tuple[dict[str, Any], dict[str, Any]]:
    item, invitation = buyer_todo.find_invitation(data.invitation_id, mold=getattr(data, "mold", None))
    if not item or not invitation:
        raise DomainError("NOT_FOUND", "没有找到这张仍待报价的询价邀请，请重新查询", 404)
    _guard_scope(item, invitation, tokens)
    if item.get("outsourceType") == "operation":
        raise DomainError("STATE_BLOCKED", "工序委外不走报价，只办接单或拒单", 409)
    if item.get("station") != "supplier_quote":
        raise DomainError(
            "STATE_BLOCKED",
            f"当前是{item.get('stationLabel') or item.get('station')}，不能报价",
            409,
        )
    if str(invitation.get("status") or "") not in QUOTEABLE:
        raise DomainError("STATE_BLOCKED", "本加工商已报过价或邀请已关闭，不能再报", 409)
    return item, invitation


POST_ACCEPT_STAGES = frozenset({
    "accepted", "material_receiving", "producing", "shipping", "delivered",
})


def _pending_accept_items(
    tokens: list[str] | None,
    *,
    mold: str | None = None,
    batch: str | None = None,
) -> list[dict[str, Any]]:
    payload = buyer_todo.run(
        {
            "station": "accept",
            "mold_family": "" if batch else str(mold or ""),
            "mold_batch": str(batch or ""),
            "project_no": "",
            "outsource_type": "",
        },
        processor_tokens=tokens,
    )
    return [
        item for item in payload.get("items") or []
        if isinstance(item, dict) and item.get("station") == "accept"
    ]


def _lookup_order(data, tokens: list[str] | None) -> dict[str, Any]:
    if getattr(data, "board_row", None):
        item = _item_from_processor_board(tokens, int(data.board_row))
        _guard_scope(item, None, tokens)
        if item.get("station") != "accept":
            raise DomainError(
                "STATE_BLOCKED",
                f"NO.{int(data.board_row)} 当前是{item.get('stationLabel') or item.get('station')}，不能接单",
                409,
            )
        return item
    item = buyer_todo.find_item_by_identity(
        order_no=getattr(data, "order_no", None),
        mold=getattr(data, "mold", None),
        batch=getattr(data, "batch", None),
        station="accept",
    )
    if not item:
        raise DomainError("NOT_FOUND", "没有找到这张仍待接单的委外工单，请用订单号、模具号和批次号重新查询", 404)
    _guard_scope(item, None, tokens)
    if item.get("station") != "accept":
        raise DomainError(
            "STATE_BLOCKED",
            f"当前是{item.get('stationLabel') or item.get('station')}，不能接单或拒单",
            409,
        )
    return item


def _resolve_accept_items(data, tokens: list[str] | None) -> list[dict[str, Any]]:
    order_nos = [buyer_todo.normalize_order_no(item) for item in (getattr(data, "order_nos", None) or []) if item]
    if getattr(data, "accept_all", False) or (len(order_nos) > 1 and not getattr(data, "board_row", None)):
        items = []
        for order_no in order_nos:
            item = buyer_todo.find_item_by_identity(
                order_no=order_no,
                mold=getattr(data, "mold", None),
                batch=getattr(data, "batch", None),
                station="accept",
            )
            if item and item.get("station") == "accept":
                _guard_scope(item, None, tokens)
                items.append(item)
        if items:
            return items
        items = _pending_accept_items(
            tokens,
            mold=getattr(data, "mold", None),
            batch=getattr(data, "batch", None),
        )
        if not items:
            raise DomainError("NOT_FOUND", "没有仍待接单的委外工单，请重新查询待办", 404)
        return items
    return [_lookup_order(data, tokens)]


def _accepted_order(data, tokens: list[str] | None) -> dict[str, Any] | None:
    """Return the EO if ERP already left 待接单 (timeout after a successful accept)."""
    item = buyer_todo.find_awarded_order(
        order_no=getattr(data, "order_no", None),
        mold=getattr(data, "mold", None),
        batch=getattr(data, "batch", None),
    )
    if not item:
        return None
    _guard_scope(item, None, tokens)
    stage = str(item.get("awardedStage") or "").casefold()
    if stage not in POST_ACCEPT_STAGES:
        return None
    return item


def _mark_accept_observed(db, order_id: int, response: dict[str, Any]) -> None:
    if db is None or not order_id:
        return
    operations = list(db.scalars(
        select(m.ERPOperation).where(
            m.ERPOperation.action == "processor_accept",
            m.ERPOperation.native_id == f"order:{order_id}",
            m.ERPOperation.state.in_(("UNKNOWN", "DISPATCHING")),
        )
    ))
    if not operations:
        return
    for operation in operations:
        operation.state = "OBSERVED_APPLIED"
        operation.response = response
        operation.error_code = None
    db.commit()


def _accept_receipt(item: dict[str, Any], data, erp: dict[str, Any], *, items: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    rows = items or [item]
    identity = {
        "order_no": item.get("orderNo") or getattr(data, "order_no", None),
        "mold": item.get("moldFamily") or item.get("moldNo") or getattr(data, "mold", None),
        "batch": item.get("moldBatch") or item.get("moldNo") or getattr(data, "batch", None),
    }
    hint = _accept_hint(item)
    receipt = {
        **identity,
        "action": "processor_accept",
        "status": "CONFIRMED",
        "erp": clip_accept_erp(erp),
        "nextHint": hint,
        "model_context": {
            "action": "processor_accept",
            "status": "CONFIRMED",
            "orderNo": identity["order_no"],
            "mold": identity["mold"],
            "batch": identity["batch"],
            "nextHint": hint,
        },
    }
    if len(rows) > 1:
        order_nos = [str(row.get("orderNo") or "") for row in rows if row.get("orderNo")]
        receipt["order_nos"] = order_nos
        receipt["model_context"]["orderNos"] = order_nos
    return receipt


def _confirm_accept_items(db, user, data, items: list[dict[str, Any]], payload: dict[str, Any]) -> dict[str, Any]:
    jobs = []
    missing = []
    for item in items:
        order_id = item.get("orderId")
        if not order_id:
            missing.append(item.get("orderNo") or "未编号")
            continue
        jobs.append({
            "native_id": f"order:{order_id}",
            "path": f"entrust/inquiry/order/{order_id}/accept",
        })
    if missing:
        raise DomainError("STATE_BLOCKED", f"这些待办还没有委外订单，不能接单：{'、'.join(str(item) for item in missing)}", 409)
    if len(jobs) == 1:
        result = post_erp(
            db, user, jobs[0]["path"],
            intent_id=payload.get("_intent_id"), action="processor_accept",
            native_id=jobs[0]["native_id"],
        )
        return _accept_receipt(items[0], data, result, items=items)
    result = dispatch_erp_batch(
        db, user,
        intent_id=payload.get("_intent_id") or "",
        action="processor_accept",
        jobs=jobs,
    )
    return _accept_receipt(items[0], data, result, items=items)


def _card(item: dict[str, Any], extra: dict[str, Any]) -> dict[str, Any]:
    display = buyer_todo.identity_display(item)
    display.update({
        "委外类型": item.get("outsourceTypeLabel") or item.get("outsourceType") or "委外",
        "当前分站": item.get("stationLabel") or item.get("station"),
        "零件": item.get("partDetails") or "未返回零件明细",
    })
    display.update(extra)
    return display


def preview(db, user, key: str, data) -> tuple[dict[str, Any], dict[str, Any]]:
    tokens = _tokens(db, user)
    if key == QUOTE_TOOL:
        data = _resolve_processor_quote(data, tokens)
        item, invitation = _lookup_quote(data, tokens)
        extra = {
            "操作": "提交本加工商报价",
            "报价金额": data.unit_price,
            "承诺交期": data.delivery_date,
            "是否含税": "含税" if data.tax_included else "不含税",
            "说明": "本人确认后写入 ERP。提交后再查待办：变成待接单就可以接单；还不是待接单就等待采购处理。",
        }
        if item.get("ourQuoteAmount") is not None:
            extra["采购报价"] = item["ourQuoteAmount"]
        if invitation.get("supplierName"):
            extra["加工商"] = invitation["supplierName"]
        return item, _card(item, extra)
    items = _resolve_accept_items(data, tokens) if key == ACCEPT_TOOL else [_lookup_order(data, tokens)]
    item = items[0]
    if key == ACCEPT_TOOL:
        extra = {
            "操作": "确认接单" if len(items) == 1 else f"确认接单（{len(items)}张）",
            "说明": "本人确认后调用 ERP 接单。工序委外下一步等仓管备料，不要确认原料收货。",
        }
        if len(items) > 1:
            extra["订单"] = "、".join(str(row.get("orderNo") or "") for row in items if row.get("orderNo"))
    else:
        reason = processor_reject_reason.resolve(data.reason_code, db, user)
        extra = {
            "操作": "拒绝接单",
            "拒单原因": reason["reason_label"],
            "说明": (
                "本人确认后调用 ERP 拒单。工序委外会自动转下一家；零件/模具委外由采购员重选加工商。"
                if item.get("outsourceType") == "operation"
                else "本人确认后调用 ERP 拒单。采购员随后重选加工商再发询价。"
            ),
        }
    return item, _card(item, extra)


def execute_tool(db, user, key: str, arguments: dict | None, run=None) -> dict[str, Any]:
    _require_processor(db, user)
    data = parse(key, arguments)
    if key == QUOTE_TOOL:
        data = _resolve_processor_quote(data, _tokens(db, user))
    if key == REJECT_TOOL:
        reason = processor_reject_reason.resolve(data.reason_code, db, user)
        data = data.model_copy(update={"reason_code": reason["reason_code"]})
    if key == ACCEPT_TOOL:
        items = _resolve_accept_items(data, _tokens(db, user))
        locked = {
            "order_no": items[0].get("orderNo") or data.order_no,
            "mold": items[0].get("moldFamily") or items[0].get("moldNo") or data.mold,
            "batch": items[0].get("moldBatch") or data.batch,
        }
        if len(items) > 1 or data.accept_all:
            locked["accept_all"] = True
            locked["order_nos"] = [row.get("orderNo") for row in items if row.get("orderNo")]
        data = data.model_copy(update=locked)
    _, display = preview(db, user, key, data)
    proposal = {
        "kind": KIND_BY_TOOL[key],
        "action": ACTION_BY_TOOL[key],
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
        "limitations": [LIMIT_BY_TOOL[key]],
    }


def source(db, user, step_id):
    from domain_packs.mold.tool_gateway import available_tools

    step = db.get(m.Step, step_id)
    run = db.get(m.Run, step.run_id) if step else None
    if not run or run.user_id != user.id:
        raise DomainError("NOT_FOUND", "操作建议不存在或无权访问", 404)
    if not proposal_run_is_open(run):
        raise DomainError("PROPOSAL_STOPPED", "任务已停止，请重新准备操作", 409)
    if run.security_version != user.security_version or run.checkpoint.get("authorization_hash") != fingerprint(db, user):
        raise DomainError("AUTHORIZATION_CHANGED", "授权已变化，请重新准备操作", 403)
    proposal = step.result.get("proposal")
    if step.tool not in available_tools(db, user) or step.tool not in TOOL_SPECS or not proposal:
        raise DomainError("TOOL_FORBIDDEN", "操作能力不可用", 403)
    return proposal


def validate_intent(db, user, payload):
    _require_processor(db, user)
    proposal = source(db, user, payload["step_id"])
    if content_hash(proposal) != payload["proposal_hash"]:
        raise DomainError("CONFIRMATION_INVALID", "操作建议内容已变化", 409)
    key = next((name for name, kind in KIND_BY_TOOL.items() if kind == proposal.get("kind")), None)
    if key is None:
        raise DomainError("TOOL_FORBIDDEN", "操作建议类型不可用", 403)
    data = parse(key, proposal["input"])
    try:
        _, display = preview(db, user, key, data)
    except DomainError as error:
        if key == ACCEPT_TOOL and error.code in {"NOT_FOUND", "STATE_BLOCKED"}:
            if _accepted_order(data, _tokens(db, user)):
                return proposal, key, data
        raise
    if content_hash(display) != content_hash(proposal["display"]):
        raise DomainError("VERSION_CONFLICT", "委外待办状态已变化，请重新查询后准备", 409)
    return proposal, key, data


def _quote_hint() -> str:
    return "报价已提交。请重新查询待办。变成待接单就可以接单或拒单。如果还不是待接单，请等待采购处理，不要自己接单。"


def _accept_hint(item: dict[str, Any] | None = None) -> str:
    if item and item.get("outsourceType") == "operation":
        return (
            "接单已写入 ERP。工序委外下一步等仓管备料完成，本加工商不要确认原料收货，也不要说待收料。"
            "请重新查询待办，只说还剩的待接单。不要引用这张单历史上的拒单原因或备注。"
        )
    return (
        "接单已写入 ERP。下一步等仓库原料发货后，本加工商再确认收货。"
        "请重新查询待办，只按查到的分站说话。不要引用这张单历史上的拒单原因或备注。"
    )


def _reject_hint(item: dict[str, Any]) -> str:
    if item.get("outsourceType") == "operation":
        return "工序委外拒单后 ERP 会自动把同一张单转给下一家。本加工商不要再操作；下一家用同一套接单/拒单办理。"
    return "零件/模具委外拒单后由采购员重选加工商再发询价。"


REJECT_ERP_KEY_MARKERS = ("reject", "declin")
REJECT_ERP_EXACT_KEYS = {"reasoncode", "reasonlabel", "reasonname"}
REJECT_REMARK_MARKERS = ("无法接单", "拒单", "产能不足")


def _folded_key(key: str) -> str:
    return re.sub(r"[^a-z]", "", str(key or "").lower())


def _looks_like_reject_text(value: Any) -> bool:
    text = str(value or "")
    return any(marker in text for marker in REJECT_REMARK_MARKERS)


def clip_accept_erp(payload: Any) -> Any:
    """Drop leftover reject-reason fields from an accept receipt.

    ERP often keeps the previous 拒单原因 on the same EO- number after
    the buyer re-awards. That text must not appear on a successful 接单.
    """
    if isinstance(payload, dict):
        cleaned: dict[str, Any] = {}
        for key, value in payload.items():
            folded = _folded_key(key)
            if any(marker in folded for marker in REJECT_ERP_KEY_MARKERS) or folded in REJECT_ERP_EXACT_KEYS:
                continue
            if folded in {"remark", "note", "comment", "memo", "message"} and _looks_like_reject_text(value):
                continue
            cleaned[key] = clip_accept_erp(value)
        return cleaned
    if isinstance(payload, list):
        return [clip_accept_erp(item) for item in payload]
    return payload


def confirm(db, user, payload):
    proposal, key, data = validate_intent(db, user, payload)
    if key == QUOTE_TOOL:
        body: dict[str, Any] = {
            "unit_price": data.unit_price,
            "delivery_date": data.delivery_date,
            "tax_included": data.tax_included,
        }
        if data.note:
            body["note"] = data.note
        if data.lead_time_days:
            body["lead_time_days"] = data.lead_time_days
        result = post_erp(
            db, user, f"entrust/inquiry/invitation/{data.invitation_id}/quote", body,
            intent_id=payload.get("_intent_id"), action="processor_quote",
            native_id=f"invitation:{data.invitation_id}",
        )
        return {
            "invitation_id": data.invitation_id,
            "action": "processor_quote",
            "status": "CONFIRMED",
            "erp": result,
            "nextHint": _quote_hint(),
        }
    tokens = _tokens(db, user)
    if key == ACCEPT_TOOL:
        try:
            items = _resolve_accept_items(data, tokens)
        except DomainError as error:
            if error.code in {"NOT_FOUND", "STATE_BLOCKED"}:
                item = _accepted_order(data, tokens)
                if item:
                    order_id = item.get("orderId")
                    observed = {
                        "msg": "已确认接单",
                        "code": 200,
                        "observed": True,
                        "data": {
                            "id": order_id,
                            "order_no": item.get("orderNo"),
                            "stage": item.get("awardedStage"),
                            "status": item.get("awardedStatus") or "open",
                        },
                    }
                    _mark_accept_observed(db, order_id, observed)
                    return _accept_receipt(item, data, observed)
            raise
        return _confirm_accept_items(db, user, data, items, payload)
    try:
        item, _ = preview(db, user, key, data)
    except DomainError:
        raise
    order_id = item.get("orderId")
    if not order_id:
        raise DomainError("STATE_BLOCKED", "这张待办还没有委外订单，不能接单或拒单", 409)
    identity = {
        "order_no": item.get("orderNo") or data.order_no,
        "mold": item.get("moldFamily") or item.get("moldNo") or data.mold,
        "batch": item.get("moldBatch") or item.get("moldNo") or data.batch,
    }
    reason = processor_reject_reason.resolve(data.reason_code, db, user)
    result = post_erp(
        db,
        user,
        f"entrust/inquiry/order/{order_id}/reject",
        {"reasonCode": reason["reason_code"]},
        params={"reasonCode": reason["reason_code"]},
        intent_id=payload.get("_intent_id"),
        action="processor_reject",
        native_id=f"order:{order_id}",
    )
    return {
        **identity,
        "action": "processor_reject",
        "status": "CONFIRMED",
        "erp": result,
        "nextHint": _reject_hint(item),
    }
