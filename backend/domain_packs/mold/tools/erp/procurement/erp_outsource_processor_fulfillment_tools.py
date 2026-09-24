"""Processor fulfillment: confirm material receipt after warehouse ship."""
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

TODO_TOOL = "query_erp_outsource_processor_fulfillment"
RECEIPT_TOOL = "prepare_erp_outsource_processor_receipt"
QUERY_TOOL_KEYS = (TODO_TOOL,)
PREPARE_TOOL_KEYS = (RECEIPT_TOOL,)
TOOL_KEYS = QUERY_TOOL_KEYS + PREPARE_TOOL_KEYS

SKILL_SPECS = {
    "outsource_processor_fulfillment": {
        "name": "委外加工商履约",
        "tools": [TODO_TOOL],
        "optional_tools": [RECEIPT_TOOL],
        "activation_tools": list(QUERY_TOOL_KEYS),
        "activation_queries": [
            "确认收料", "确认来料", "原料收货", "收料待办", "待收料", "履约待办",
        ],
        "auto_activation_queries": [
            "确认收料", "确认来料", "原料收货", "收料待办", "待收料", "履约待办",
        ],
        "priority_patterns": ["确认收料|确认来料|原料收货|收料待办|待收料|履约待办"],
        "requires_tool_evidence": True,
        "activation_route": "authorized",
        "suppress_tool_search_on_auto_activation": False,
        "host_auto_invoke_empty_arguments": True,
    },
}

TOOL_SPECS = {
    TODO_TOOL: {
        "description": "只读查询本加工商收料待办：零件委外待确认收料，以及仍在等仓库发料/备料的工单。成品发货用 outsource_processor_product_ship。工序委外没有收料待办。",
        "permission": "erp_outsource_processor.read",
    },
    RECEIPT_TOOL: {
        "description": "准备确认零件/模具委外原料收货。必须已锁定 shipmentId。工序委外不用收货。本人确认后才写入 ERP。",
        "permission": "erp_outsource_processor.execute",
    },
}

TOOL_NAMES = {
    TODO_TOOL: "查询加工商履约待办",
    RECEIPT_TOOL: "准备确认原料收货",
}


class FulfillmentTodoInput(StrictModel):
    question: str | None = Field(default=None, max_length=500)
    mold: str | None = Field(default=None, max_length=40)

    @field_validator("question", "mold")
    @classmethod
    def strip_text(cls, value):
        return value.strip() or None if isinstance(value, str) else value


class ProcessorReceiptInput(StrictModel):
    shipment_id: int = Field(ge=1, description="查询结果中的 shipmentId。")
    mold: str | None = Field(default=None, max_length=40)
    line_ids: list[int] = Field(default_factory=list, description="待确认明细行，空则确认全部待收货行。")

    @field_validator("mold")
    @classmethod
    def strip_mold(cls, value):
        return value.strip() or None if isinstance(value, str) else value


INPUT_MODELS = {
    TODO_TOOL: FulfillmentTodoInput,
    RECEIPT_TOOL: ProcessorReceiptInput,
}

KIND_BY_TOOL = {
    RECEIPT_TOOL: "erp_outsource_processor_receipt",
}
ACTION_BY_TOOL = {
    RECEIPT_TOOL: "confirm_erp_outsource_processor_receipt",
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
        raise DomainError("INVALID_TOOL_INPUT", "加工商履约参数无效：" + error.errors()[0]["msg"]) from None


def _require_read(db, user) -> None:
    require_allow(db, user, "erp_outsource_processor.read", "仅委外加工商可查询本供应商履约待办")


def _require_execute(db, user) -> None:
    require_allow(db, user, "erp_outsource_processor.execute", "仅委外加工商可确认收料或成品发货")


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
        payload = {"status": "NO_SUPPLIER_SCOPE", "summary": "当前账号未绑定加工商范围，看不到履约待办。", "items": []}
    else:
        payload = processor_fulfillment.run(parsed, processor_tokens=tokens)
    return {
        "data": payload,
        "source": "management-system ERP 加工商履约只读查询",
        "as_of": now().isoformat(),
        "limitations": ["只读。工序委外没有收料待办；零件委外先收料再成品发货。"],
    }


def _lookup_receipt(data, tokens: list[str] | None) -> dict[str, Any]:
    item = processor_fulfillment.find_shipment(data.shipment_id)
    if not item:
        raise DomainError("NOT_FOUND", "没有找到仍待确认收货的原料发货单，请重新查询", 404)
    _guard(item, tokens)
    if item.get("outsourceType") == "operation":
        raise DomainError("STATE_BLOCKED", "工序委外备料完成后自动收货，不用确认来料，请办成品发货", 409)
    pending = [int(line.get("lineId") or 0) for line in item.get("pendingLines") or [] if line.get("lineId")]
    if not pending:
        raise DomainError("STATE_BLOCKED", "该发货单已全部确认收货", 409)
    chosen = [int(value) for value in (data.line_ids or pending)]
    if any(line_id not in pending for line_id in chosen):
        raise DomainError("STATE_BLOCKED", "所选明细已不是待收货状态，请重新查询", 409)
    item["confirmLineIds"] = chosen
    return item


def preview(db, user, key: str, data) -> tuple[dict[str, Any], dict[str, Any]]:
    item = _lookup_receipt(data, _tokens(db, user))
    display = {
        **identity_display(item),
        "委外类型": item.get("outsourceTypeLabel") or item.get("outsourceType"),
        "发货单": item.get("shipmentNo") or item.get("shipmentId"),
        "操作": "确认原料收货",
        "明细行": "、".join(str(value) for value in item.get("confirmLineIds") or []),
        "说明": "本人确认后写入 ERP。确认后用成品发货 Skill 按已收数量发回厂。",
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
        "limitations": ["仅准备原料收货；本人确认后才调用 ERP。成品发货用 outsource_processor_product_ship。"],
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
        raise DomainError("TOOL_FORBIDDEN", "操作建议类型不可用", 403)
    data = parse(key, proposal["input"])
    _, display = preview(db, user, key, data)
    if content_hash(display) != content_hash(proposal["display"]):
        raise DomainError("VERSION_CONFLICT", "履约待办状态已变化，请重新查询后准备", 409)
    return proposal, key, data


def confirm(db, user, payload):
    _, key, data = validate_intent(db, user, payload)
    item, _ = preview(db, user, key, data)
    result = post_erp(
        db,
        user,
        f"entrust/fulfillment/material-shipment/{data.shipment_id}/confirm-receipt",
        {"confirmedLineIds": item.get("confirmLineIds") or data.line_ids},
        intent_id=payload.get("_intent_id"),
        action="processor_receipt",
        native_id=f"material-shipment:{data.shipment_id}",
    )
    return {
        "shipment_id": data.shipment_id,
        "action": "processor_receipt",
        "status": "CONFIRMED",
        "erp": result,
        "nextHint": "原料已确认。请用成品发货 Skill 按已收数量发回厂。",
    }
