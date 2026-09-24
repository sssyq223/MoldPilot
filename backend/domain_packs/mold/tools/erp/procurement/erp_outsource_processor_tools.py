"""Processor-side outsource prepare tools: quote, accept, reject."""
from __future__ import annotations

from typing import Any

from pydantic import Field, ValidationError, field_validator

from domain_packs.mold import models as m
from domain_packs.mold.authorization import fingerprint
from domain_packs.mold.erp.procurement.erp_outsource_http import post_erp
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
from domain_packs.mold.tools.erp.procurement.outsource_queries import buyer_todo

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
            "我要接单", "确认接单",
            "拒绝接单", "我要拒单",
        ],
        "auto_activation_queries": [
            "提交报价", "我要报价", "加工商报价",
            "我要接单", "确认接单",
            "拒绝接单", "我要拒单",
        ],
        "priority_patterns": ["加工商报价|提交报价|我要报价|确认接单|我要接单|拒绝接单|我要拒单"],
        "activation_route": "authorized",
        "suppress_tool_search_on_auto_activation": False,
    },
}

TOOL_SPECS = {
    QUOTE_TOOL: {
        "description": "准备提交本加工商报价。必须已用查询锁定 invitationId，且当前分站是待报价。工序委外不走报价。本人确认后才写入 ERP。",
        "permission": "erp_outsource_processor.execute",
    },
    ACCEPT_TOOL: {
        "description": "准备确认接单。用查询结果中的订单号，必要时加模具号、批次号定位。当前分站必须是待接单。禁止使用内部数字 id。本人确认后才写入 ERP。",
        "permission": "erp_outsource_processor.execute",
    },
    REJECT_TOOL: {
        "description": "准备拒绝接单。用查询结果中的订单号，必要时加模具号、批次号定位，并提供 ERP 拒单原因编码。工序委外拒单后 ERP 自动转下一家。禁止使用内部数字 id。本人确认后才写入 ERP。",
        "permission": "erp_outsource_processor.execute",
    },
}

TOOL_NAMES = {
    QUOTE_TOOL: "准备提交加工商报价",
    ACCEPT_TOOL: "准备确认接单",
    REJECT_TOOL: "准备拒绝接单",
}


class ProcessorQuoteInput(StrictModel):
    invitation_id: int = Field(ge=1, description="查询结果中本加工商的 invitationId。")
    mold: str | None = Field(default=None, max_length=40)
    unit_price: float = Field(gt=0, description="本加工商报价（订单总额）。")
    delivery_date: str = Field(pattern=r"^\d{4}-\d{2}-\d{2}$", description="承诺交付日期 YYYY-MM-DD。")
    tax_included: bool = Field(description="报价是否含税。")
    note: str | None = Field(default=None, max_length=400)
    lead_time_days: int | None = Field(default=None, ge=1, le=365)

    @field_validator("mold", "note")
    @classmethod
    def strip_text(cls, value):
        return value.strip() or None if isinstance(value, str) else value


class ProcessorAcceptInput(CamelModel):
    order_no: str = identity_order_no(min_length=3, max_length=80, description="查询结果中的订单号，例如 EO-260923-0KKR。禁止使用内部数字 id。")
    mold: str | None = identity_mold(default=None, max_length=40, description="模具号，例如 M260063。")
    batch: str | None = identity_batch(default=None, max_length=40, description="批次号，例如 M260063-P1。同一订单有多批次时必填。")

    @field_validator("order_no", "mold", "batch")
    @classmethod
    def strip_identity(cls, value):
        return value.strip() or None if isinstance(value, str) else value


class ProcessorRejectInput(CamelModel):
    order_no: str = identity_order_no(min_length=3, max_length=80, description="查询结果中的订单号，例如 EO-260923-0KKR。禁止使用内部数字 id。")
    mold: str | None = identity_mold(default=None, max_length=40, description="模具号，例如 M260063。")
    batch: str | None = identity_batch(default=None, max_length=40, description="批次号，例如 M260063-P1。同一订单有多批次时必填。")
    reason_code: str = Field(min_length=1, max_length=80, description="ERP 拒单原因编码。")

    @field_validator("order_no", "mold", "batch")
    @classmethod
    def strip_identity(cls, value):
        return value.strip() or None if isinstance(value, str) else value

    @field_validator("reason_code")
    @classmethod
    def strip_reason(cls, value):
        return value.strip()


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
    QUOTE_TOOL: "仅准备本加工商报价；本人确认后才调用 ERP。提交后必须重新查询，再决定提醒接单还是等待采购/审批。",
    ACCEPT_TOOL: "仅准备确认接单；本人确认后才调用 ERP。",
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
        raise DomainError("INVALID_TOOL_INPUT", "加工商办理参数无效：" + error.errors()[0]["msg"]) from None


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


def _lookup_order(data, tokens: list[str] | None) -> dict[str, Any]:
    item = buyer_todo.find_item_by_identity(
        order_no=getattr(data, "order_no", None),
        mold=getattr(data, "mold", None),
        batch=getattr(data, "batch", None),
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
        item, invitation = _lookup_quote(data, tokens)
        extra = {
            "操作": "提交本加工商报价",
            "报价金额": data.unit_price,
            "承诺交期": data.delivery_date,
            "是否含税": "含税" if data.tax_included else "不含税",
            "说明": "本人确认后写入 ERP。区间内会免审待接单；超区间则等采购填成交价、主管和总经理审批。",
        }
        if invitation.get("supplierName"):
            extra["加工商"] = invitation["supplierName"]
        return item, _card(item, extra)
    item = _lookup_order(data, tokens)
    if key == ACCEPT_TOOL:
        extra = {
            "操作": "确认接单",
            "说明": "本人确认后调用 ERP 接单。",
        }
    else:
        extra = {
            "操作": "拒绝接单",
            "拒单原因编码": data.reason_code,
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
    _, display = preview(db, user, key, data)
    if content_hash(display) != content_hash(proposal["display"]):
        raise DomainError("VERSION_CONFLICT", "委外待办状态已变化，请重新查询后准备", 409)
    return proposal, key, data


def _quote_hint() -> str:
    return "请立即重新查询本加工商待办。若出现待接单，提醒接单；若变为待下单或审批中，说明报价超区间，等采购填成交价、主管和总经理审批。"


def _reject_hint(item: dict[str, Any]) -> str:
    if item.get("outsourceType") == "operation":
        return "工序委外拒单后 ERP 会自动把同一张单转给下一家。本加工商不要再操作；下一家用同一套接单/拒单办理。"
    return "零件/模具委外拒单后由采购员重选加工商再发询价。"


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
    item, _ = preview(db, user, key, data)
    order_id = item.get("orderId")
    if not order_id:
        raise DomainError("STATE_BLOCKED", "这张待办还没有委外订单，不能接单或拒单", 409)
    identity = {
        "order_no": item.get("orderNo") or data.order_no,
        "mold": item.get("moldFamily") or item.get("moldNo") or data.mold,
        "batch": item.get("moldBatch") or item.get("moldNo") or data.batch,
    }
    if key == ACCEPT_TOOL:
        result = post_erp(
            db, user, f"entrust/inquiry/order/{order_id}/accept",
            intent_id=payload.get("_intent_id"), action="processor_accept",
            native_id=f"order:{order_id}",
        )
        return {
            **identity,
            "action": "processor_accept",
            "status": "CONFIRMED",
            "erp": result,
        }
    result = post_erp(
        db,
        user,
        f"entrust/inquiry/order/{order_id}/reject",
        params={"reasonCode": data.reason_code},
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
