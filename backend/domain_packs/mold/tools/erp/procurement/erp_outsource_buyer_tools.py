"""Buyer-side outsource prepare tools: quote, send inquiry, deal price, reselect."""
from __future__ import annotations

from typing import Any

from pydantic import AliasChoices, ConfigDict, Field, ValidationError, field_validator, model_validator

from domain_packs.mold import models as m
from domain_packs.mold.authorization import fingerprint
from domain_packs.mold.erp.procurement.erp_outsource_http import post_erp
from domain_packs.mold.erp.procurement.erp_outsource_scope import (
    require_allow,
    require_outsource_buyer_scope,
)
from domain_packs.mold.ports.bpm import content_hash
from domain_packs.mold.ports.confirmation_policy import proposal_confirmation_policy
from domain_packs.mold.ports.db import now
from domain_packs.mold.ports.errors import DomainError
from domain_packs.mold.tools.erp.procurement.outsource_identity import (
    CamelModel,
    identity_batch,
    identity_mold,
    identity_order_no,
    identity_part,
)
from domain_packs.mold.tools.erp.procurement.outsource_queries import buyer_todo

QUOTE_TOOL = "prepare_erp_outsource_buyer_quote"
SEND_TOOL = "prepare_erp_outsource_inquiry_send"
DEAL_TOOL = "prepare_erp_outsource_final_deal"
RESELECT_TOOL = "prepare_erp_outsource_reselect"
TOOL_KEYS = (QUOTE_TOOL, SEND_TOOL, DEAL_TOOL, RESELECT_TOOL)

STATION_BY_TOOL = {
    QUOTE_TOOL: "buyer_quote",
    SEND_TOOL: "inquiry_send",
    DEAL_TOOL: "place_order",
    RESELECT_TOOL: "exhausted",
}

SKILL_SPECS = {
    "outsource_buyer_ops": {
        "name": "委外采购办理",
        "tools": ["query_erp_outsource_followup_board"],
        "optional_tools": list(TOOL_KEYS),
        "activation_tools": ["query_erp_outsource_followup_board"],
        "activation_queries": [
            "填我方报价", "填价格", "帮我填报价", "帮我填价格",
            "发询价", "选加工商",
            "填成交价", "成交价", "重选加工商", "拒单重选",
        ],
        "auto_activation_queries": [
            "填我方报价", "填价格", "帮我填报价", "帮我填价格",
            "发询价", "填成交价", "成交价", "重选加工商",
        ],
        "priority_patterns": ["填我方报价|填价格|帮我填报价|帮我填价格|发询价|填成交价|成交价|重选加工商|拒单重选"],
        "activation_route": "authorized",
        "suppress_tool_search_on_auto_activation": False,
    },
}

TOOL_SPECS = {
    QUOTE_TOOL: {
        "description": "准备填写零件/模具委外的我方报价（订单总价/总价格）与直接接单上限（上限区间）。必须调用本工具，禁止编造 prepare_project_quote 或其他函数名。参数 our_quote_amount 填总价，auto_accept_max_amount 填上限。用订单号，或模具号+批次号+零件号定位。同一批次常有多张询价，用户点名零件时必须带 part。当前分站必须是待采购填报价。禁止使用内部数字 id。本人确认后才写入 ERP。",
        "permission": "erp_outsource_buyer.execute",
    },
    SEND_TOOL: {
        "description": "准备向选定加工商发出询价。用订单号或模具号+批次号定位，加工商用编码或名称。当前分站必须是待发询价。禁止使用内部数字 id。本人确认后才写入 ERP。",
        "permission": "erp_outsource_buyer.execute",
    },
    DEAL_TOOL: {
        "description": "准备填写超区间成交价并提交定标审批。用订单号或模具号+批次号定位，并提供要定标的报价单。当前分站必须是待下单。禁止使用内部工单数字 id。本人确认后才写入 ERP。",
        "permission": "erp_outsource_buyer.execute",
    },
    RESELECT_TOOL: {
        "description": "准备拒单后重选加工商的确认卡。用订单号或模具号+批次号定位。ERP 暂无独立重选接口，确认后只说明下一步应发询价，不直接改 ERP。",
        "permission": "erp_outsource_buyer.execute",
    },
}

TOOL_NAMES = {
    QUOTE_TOOL: "准备填写委外我方报价",
    SEND_TOOL: "准备发出委外询价",
    DEAL_TOOL: "准备填写委外成交价",
    RESELECT_TOOL: "准备重选委外加工商",
}


class _BuyerIdentity(CamelModel):
    order_no: str | None = identity_order_no(default=None, max_length=80, description="查询结果中的订单号。尚未下单时可省略。禁止内部数字 id。")
    mold: str | None = identity_mold(default=None, max_length=40, description="模具号，例如 M260063。")
    batch: str | None = identity_batch(default=None, max_length=80, description="批次号，例如 M260063-P1。尚未下单时必填。")
    part: str | None = identity_part(default=None, max_length=200, description="零件号，例如 PH-01。同一批次有多张询价时必填。")

    @field_validator("order_no", "mold", "batch")
    @classmethod
    def strip_identity(cls, value):
        return value.strip() or None if isinstance(value, str) else value

    @field_validator("part", mode="before")
    @classmethod
    def normalize_part(cls, value):
        token = buyer_todo.normalize_part_token(value)
        return token or None

    @model_validator(mode="after")
    def require_identity(self):
        if not self.order_no and not self.mold and not self.batch and not self.part:
            raise ValueError("请用订单号、模具号、批次号或零件号定位，不要使用内部数字编号")
        return self


class BuyerQuoteInput(_BuyerIdentity):
    model_config = ConfigDict(extra="ignore", populate_by_name=True)
    our_quote_amount: float = Field(
        gt=0,
        validation_alias=AliasChoices(
            "our_quote_amount", "ourQuoteAmount", "our_quote", "ourQuote",
            "total_price", "totalPrice",
        ),
        description="我方报价（订单总额，用户说的总价格）。",
    )
    auto_accept_max_amount: float = Field(
        gt=0,
        validation_alias=AliasChoices(
            "auto_accept_max_amount", "autoAcceptMaxAmount",
            "upper_limit", "upperLimit", "max_amount", "maxAmount",
        ),
        description="直接接单上限（用户说的上限或上限区间）。",
    )


class InquirySendInput(_BuyerIdentity):
    suppliers: list[str] = Field(min_length=1, description="查询结果中的加工商编码或名称。禁止使用内部数字 id。")

    @field_validator("suppliers")
    @classmethod
    def strip_suppliers(cls, value):
        names = [str(item).strip() for item in value if str(item).strip()]
        if not names:
            raise ValueError("请提供加工商编码或名称")
        return names


class FinalDealInput(_BuyerIdentity):
    quotation_id: int = Field(ge=1, description="要定标的报价单 ID。")
    final_deal_amount: float = Field(gt=0)


class ReselectInput(_BuyerIdentity):
    note: str = Field(min_length=1, max_length=400, description="重选说明，例如要换哪家。")

    @field_validator("note")
    @classmethod
    def strip_note(cls, value):
        return value.strip()


INPUT_MODELS = {
    QUOTE_TOOL: BuyerQuoteInput,
    SEND_TOOL: InquirySendInput,
    DEAL_TOOL: FinalDealInput,
    RESELECT_TOOL: ReselectInput,
}

KIND_BY_TOOL = {
    QUOTE_TOOL: "erp_outsource_buyer_quote",
    SEND_TOOL: "erp_outsource_inquiry_send",
    DEAL_TOOL: "erp_outsource_final_deal",
    RESELECT_TOOL: "erp_outsource_reselect",
}

ACTION_BY_TOOL = {
    QUOTE_TOOL: "confirm_erp_outsource_buyer_quote",
    SEND_TOOL: "confirm_erp_outsource_inquiry_send",
    DEAL_TOOL: "confirm_erp_outsource_final_deal",
    RESELECT_TOOL: "confirm_erp_outsource_reselect",
}

LIMIT_BY_TOOL = {
    QUOTE_TOOL: "仅准备填写我方报价与接单上限；本人确认后才调用 ERP，确认前不改数据。",
    SEND_TOOL: "仅准备发出询价；本人确认后才调用 ERP。工序委外不走询价。",
    DEAL_TOOL: "仅准备超区间成交价并提交定标审批；本人确认后才调用 ERP。",
    RESELECT_TOOL: "ERP 暂无独立重选接口。确认后只提示下一步用发询价办理，不直接改 ERP。",
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
        raise DomainError("INVALID_TOOL_INPUT", "委外办理参数无效：" + error.errors()[0]["msg"]) from None


def _require_buyer(db, user) -> None:
    require_allow(db, user, "erp_outsource_buyer.execute", "仅委外采购员可办理填价、发询价、成交价或重选")
    require_outsource_buyer_scope(db, user)


def _lookup(data, expected_station: str) -> dict[str, Any]:
    item = buyer_todo.find_item_by_identity(
        order_no=getattr(data, "order_no", None),
        mold=getattr(data, "mold", None),
        batch=getattr(data, "batch", None),
        part=getattr(data, "part", None),
        require_inquiry=True,
    )
    if not item:
        raise DomainError("NOT_FOUND", "没有找到这张仍停在采购待办的委外询价单，请重新查询", 404)
    if item.get("outsourceType") == "operation" and expected_station in {"buyer_quote", "inquiry_send", "place_order"}:
        raise DomainError("STATE_BLOCKED", "工序委外不走填价、发询价或成交价", 409)
    if item.get("station") != expected_station:
        raise DomainError(
            "STATE_BLOCKED",
            f"当前是{item.get('stationLabel') or item.get('station')}，不能做这一步",
            409,
        )
    return item


def _supplier_ids(item: dict[str, Any], suppliers: list[str]) -> list[int]:
    return buyer_todo.resolve_supplier_ids(item, suppliers)


def _card(item: dict[str, Any], extra: dict[str, Any]) -> dict[str, Any]:
    display = buyer_todo.identity_display(item)
    display.update({
        "委外类型": item.get("outsourceTypeLabel") or item.get("outsourceType") or "委外",
        "当前分站": item.get("stationLabel") or item.get("station"),
        "零件": item.get("partDetails") or "未返回零件明细",
    })
    display.update(extra)
    return display


def preview(key: str, data) -> tuple[dict[str, Any], dict[str, Any]]:
    item = _lookup(data, STATION_BY_TOOL[key])
    if key == QUOTE_TOOL:
        extra = {
            "操作": "填写我方报价与直接接单上限",
            "我方报价": data.our_quote_amount,
            "直接接单上限": data.auto_accept_max_amount,
            "说明": "本人确认后写入 ERP。报价合计不超过上限时加工商报价可免审定标。",
        }
    elif key == SEND_TOOL:
        _supplier_ids(item, data.suppliers)
        extra = {
            "操作": "向选定加工商发出询价",
            "加工商": "、".join(data.suppliers),
            "说明": "本人确认后调用 ERP 发询价。",
        }
    elif key == DEAL_TOOL:
        extra = {
            "操作": "填写成交价并提交定标审批",
            "报价单": data.quotation_id,
            "成交价": data.final_deal_amount,
            "说明": "本人确认后写入 ERP 并提交下单审批，审批通过前加工商不能接单。",
        }
    else:
        extra = {
            "操作": "拒单后重选加工商",
            "说明": data.note,
            "注意事项": "ERP 没有独立重选接口。确认后请用发询价把新加工商发出，或到 ERP 待办重选。本次确认不改 ERP。",
        }
    return item, _card(item, extra)


def execute_tool(db, user, key: str, arguments: dict | None, run=None) -> dict[str, Any]:
    _require_buyer(db, user)
    data = parse(key, arguments)
    _, display = preview(key, data)
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
    if run.status not in {"RUNNING", "RUNNING_SCOPED", "SUCCEEDED", "FAILED"}:
        raise DomainError("PROPOSAL_STOPPED", "任务已停止，请重新准备操作", 409)
    if run.security_version != user.security_version or run.checkpoint.get("authorization_hash") != fingerprint(db, user):
        raise DomainError("AUTHORIZATION_CHANGED", "授权已变化，请重新准备操作", 403)
    proposal = step.result.get("proposal")
    if step.tool not in available_tools(db, user) or step.tool not in TOOL_SPECS or not proposal:
        raise DomainError("TOOL_FORBIDDEN", "操作能力不可用", 403)
    return proposal


def validate_intent(db, user, payload):
    _require_buyer(db, user)
    proposal = source(db, user, payload["step_id"])
    if content_hash(proposal) != payload["proposal_hash"]:
        raise DomainError("CONFIRMATION_INVALID", "操作建议内容已变化", 409)
    key = next((name for name, kind in KIND_BY_TOOL.items() if kind == proposal.get("kind")), None)
    if key is None:
        raise DomainError("TOOL_FORBIDDEN", "操作建议类型不可用", 403)
    data = parse(key, proposal["input"])
    _, display = preview(key, data)
    if content_hash(display) != content_hash(proposal["display"]):
        raise DomainError("VERSION_CONFLICT", "委外待办状态或金额已变化，请重新查询后准备", 409)
    return proposal, key, data


def confirm(db, user, payload):
    _proposal, key, data = validate_intent(db, user, payload)
    item, _ = preview(key, data)
    inquiry_id = item.get("inquiryId")
    identity = {
        "order_no": item.get("orderNo") or data.order_no,
        "mold": item.get("moldFamily") or item.get("moldNo") or data.mold,
        "batch": item.get("moldBatch") or item.get("moldNo") or data.batch,
    }
    if key == RESELECT_TOOL:
        return {
            **identity,
            "action": "reselect",
            "status": "PREPARE_ONLY",
            "message": "ERP 暂无独立重选接口。请用发询价把新加工商发出，或在 ERP 待办中重选。",
        }
    if not inquiry_id:
        raise DomainError("STATE_BLOCKED", "这张待办还没有询价单，不能写入 ERP", 409)
    if key == QUOTE_TOOL:
        result = post_erp(db, user, f"entrust/inquiry/{inquiry_id}/buyer-quote", {
            "ourQuoteAmount": data.our_quote_amount,
            "autoAcceptMaxAmount": data.auto_accept_max_amount,
        }, intent_id=payload.get("_intent_id"), action="buyer_quote", native_id=f"inquiry:{inquiry_id}")
        action = "buyer_quote"
    elif key == SEND_TOOL:
        result = post_erp(db, user, f"entrust/inquiry/{inquiry_id}/send", {
            "supplierIds": _supplier_ids(item, data.suppliers),
        }, intent_id=payload.get("_intent_id"), action="inquiry_send", native_id=f"inquiry:{inquiry_id}")
        action = "inquiry_send"
    else:
        result = post_erp(db, user, f"entrust/inquiry/{inquiry_id}/final-deal-price", {
            "quotationId": data.quotation_id,
            "finalDealAmount": data.final_deal_amount,
        }, intent_id=payload.get("_intent_id"), action="final_deal", native_id=f"inquiry:{inquiry_id}")
        action = "final_deal"
    return {
        **identity,
        "action": action,
        "status": "CONFIRMED",
        "erp": result,
    }
