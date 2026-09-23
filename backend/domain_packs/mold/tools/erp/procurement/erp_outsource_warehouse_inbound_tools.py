"""Warehouse product arrival and inbound after processor shipment."""
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
from domain_packs.mold.tools.erp.procurement.outsource_queries import warehouse_inbound

TODO_TOOL = "query_erp_outsource_warehouse_inbound"
ARRIVAL_TOOL = "prepare_erp_outsource_warehouse_arrival"
INBOUND_TOOL = "prepare_erp_outsource_warehouse_inbound"
QUERY_TOOL_KEYS = (TODO_TOOL,)
PREPARE_TOOL_KEYS = (ARRIVAL_TOOL, INBOUND_TOOL)
TOOL_KEYS = QUERY_TOOL_KEYS + PREPARE_TOOL_KEYS

SKILL_SPECS = {
    "outsource_warehouse_inbound": {
        "name": "委外仓管回厂收货入库",
        "tools": [TODO_TOOL],
        "optional_tools": list(PREPARE_TOOL_KEYS),
        "activation_tools": list(QUERY_TOOL_KEYS),
        "activation_queries": [
            "仓库收货", "确认收货", "到货确认", "回厂入库", "成品入库", "半成品入库",
            "收货待办", "入库待办", "回厂待办",
        ],
        "auto_activation_queries": [
            "仓库收货", "确认收货", "到货确认", "回厂入库", "成品入库",
            "收货待办", "入库待办", "回厂待办",
        ],
        "priority_patterns": ["仓库收货|确认收货|到货确认|回厂入库|成品入库|收货待办|入库待办|回厂待办"],
        "requires_tool_evidence": True,
        "suppress_tool_search_on_auto_activation": True,
    },
}

TOOL_SPECS = {
    TODO_TOOL: {
        "description": "只读查询加工商成品发货后的仓库待办：待到货确认、待入库。入库目标按业务规则：零件/模具及工序末道 T 进成品库，非末道工序进半成品库。",
        "permission": "erp_outsource_warehouse.read",
    },
    ARRIVAL_TOOL: {
        "description": "准备仓库到货确认（确认收货）。必须已锁定 shipmentId。未指定行则按待到货数量全确认。本人确认后才写入 ERP。",
        "permission": "erp_outsource_warehouse.execute",
    },
    INBOUND_TOOL: {
        "description": "准备仓储入库确认。必须已锁定 shipmentId。数量不能超过待入库。本人确认后才写入 ERP。",
        "permission": "erp_outsource_warehouse.execute",
    },
}

TOOL_NAMES = {
    TODO_TOOL: "查询仓库回厂收货入库待办",
    ARRIVAL_TOOL: "准备仓库到货确认",
    INBOUND_TOOL: "准备仓储入库确认",
}


class InboundTodoInput(StrictModel):
    question: str | None = Field(default=None, max_length=500)
    mold: str | None = Field(default=None, max_length=40)

    @field_validator("question", "mold")
    @classmethod
    def strip_text(cls, value):
        return value.strip() or None if isinstance(value, str) else value


class ArrivalLineInput(StrictModel):
    shipment_line_id: int = Field(ge=1)
    qty: int = Field(ge=1)


class InboundLineInput(StrictModel):
    shipment_line_id: int = Field(ge=1)
    qty: int = Field(ge=1)


class WarehouseArrivalInput(StrictModel):
    shipment_id: int = Field(ge=1, description="查询结果中的 shipmentId。")
    mold: str | None = Field(default=None, max_length=40)
    lines: list[ArrivalLineInput] = Field(default_factory=list, description="空则按各行待到货数量全部确认。")
    remark: str | None = Field(default=None, max_length=400)

    @field_validator("mold", "remark")
    @classmethod
    def strip_text(cls, value):
        return value.strip() or None if isinstance(value, str) else value


class WarehouseInboundInput(StrictModel):
    shipment_id: int = Field(ge=1, description="查询结果中的 shipmentId。")
    mold: str | None = Field(default=None, max_length=40)
    lines: list[InboundLineInput] = Field(default_factory=list, description="空则按各行待入库数量全部入库。")
    remark: str | None = Field(default=None, max_length=400)

    @field_validator("mold", "remark")
    @classmethod
    def strip_text(cls, value):
        return value.strip() or None if isinstance(value, str) else value


INPUT_MODELS = {
    TODO_TOOL: InboundTodoInput,
    ARRIVAL_TOOL: WarehouseArrivalInput,
    INBOUND_TOOL: WarehouseInboundInput,
}
KIND_BY_TOOL = {
    ARRIVAL_TOOL: "erp_outsource_warehouse_arrival",
    INBOUND_TOOL: "erp_outsource_warehouse_inbound",
}
ACTION_BY_TOOL = {
    ARRIVAL_TOOL: "confirm_erp_outsource_warehouse_arrival",
    INBOUND_TOOL: "confirm_erp_outsource_warehouse_inbound",
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
        raise DomainError("INVALID_TOOL_INPUT", "仓库回厂办理参数无效：" + error.errors()[0]["msg"]) from None


def _require_read(db, user) -> None:
    require_allow(db, user, "erp_outsource_warehouse.read", "仅委外仓管可查询回厂收货入库待办")


def _require_execute(db, user) -> None:
    require_allow(db, user, "erp_outsource_warehouse.execute", "仅委外仓管可确认回厂收货或入库")


def execute_query(db, user, arguments: dict | None, run=None) -> dict[str, Any]:
    _require_read(db, user)
    data = parse(TODO_TOOL, arguments)
    question = data.question or str(getattr(run, "prompt", "") or "")
    parsed = warehouse_inbound.parse_question(" ".join(part for part in (question, data.mold) if part))
    return {
        "data": warehouse_inbound.run(parsed),
        "source": "management-system ERP 仓库回厂收货入库只读查询",
        "as_of": now().isoformat(),
        "limitations": [
            "只读。到货确认与仓储入库是两步。拒收/破损本期不办。",
            "入库目标按 ERP processor-inbound-v1：零件/模具及明确末道进成品库；非末道或末道缺失进半成品库并提示。缺目标禁止提交入库。",
        ],
    }


def _require_shipment(shipment_id: int) -> dict[str, Any]:
    item = warehouse_inbound.find_shipment(shipment_id)
    if not item:
        raise DomainError("NOT_FOUND", "没有找到待收货或待入库的成品发货单", 404)
    return item


def _resolved_lines(data, item: dict[str, Any], *, field: str) -> list[dict[str, Any]]:
    remain = {
        int(line.get("lineId") or 0): line
        for line in item.get("lines") or []
        if int(line.get(field) or 0) > 0
    }
    if data.lines:
        chosen = [{"shipment_line_id": line.shipment_line_id, "qty": line.qty} for line in data.lines]
    else:
        chosen = [
            {"shipment_line_id": line_id, "qty": int(line.get(field) or 0)}
            for line_id, line in remain.items()
        ]
    if not chosen:
        raise DomainError("STATE_BLOCKED", "没有可确认数量，请重新查询", 409)
    for line in chosen:
        part = remain.get(line["shipment_line_id"])
        allowed = int((part or {}).get(field) or 0)
        if allowed <= 0:
            raise DomainError("STATE_BLOCKED", f"发货明细 {line['shipment_line_id']} 当前不可办", 409)
        if line["qty"] > allowed:
            raise DomainError(
                "STATE_BLOCKED",
                f"发货明细 {line['shipment_line_id']} 不能超过待办数量 {allowed}",
                409,
            )
    return chosen


def preview(db, user, key: str, data) -> tuple[dict[str, Any], dict[str, Any]]:
    item = _require_shipment(data.shipment_id)
    field = "pendingArrivalQty" if key == ARRIVAL_TOOL else "pendingInboundQty"
    if key == ARRIVAL_TOOL and int(item.get("pendingArrivalQty") or 0) <= 0:
        raise DomainError("STATE_BLOCKED", "该发货单已无待到货数量，若还需入库请办仓储入库", 409)
    if key == INBOUND_TOOL and int(item.get("pendingInboundQty") or 0) <= 0:
        raise DomainError("STATE_BLOCKED", "该发货单已无待入库数量", 409)
    lines = _resolved_lines(data, item, field=field)
    parts = {int(line.get("lineId") or 0): line for line in item.get("lines") or []}
    if key == INBOUND_TOOL:
        missing = [
            line["shipment_line_id"]
            for line in lines
            if not (parts.get(line["shipment_line_id"]) or {}).get("inboundTarget")
        ]
        if missing:
            raise DomainError(
                "STATE_BLOCKED",
                "ERP 未返回入库目标，不能提交入库。请重新查询发货明细后再办理",
                409,
            )
    details = []
    warnings = []
    for line in lines:
        part = parts.get(line["shipment_line_id"]) or {}
        warning = part.get("inboundTargetWarning")
        details.append(
            f"{part.get('partNo') or line['shipment_line_id']}×{line['qty']}"
            f"（末道{part.get('isEndOperationLabel')}→{part.get('inboundTargetLabel') or '未返回'}）"
        )
        if warning:
            warnings.append(str(warning))
    targets = [
        (parts.get(line["shipment_line_id"]) or {}).get("inboundTargetLabel")
        for line in lines
        if (parts.get(line["shipment_line_id"]) or {}).get("inboundTarget")
    ]
    display = {
        "模具号": item.get("moldNo") or "未标注",
        "委外类型": item.get("outsourceTypeLabel") or item.get("outsourceType"),
        "工单": item.get("orderNo") or item.get("orderId"),
        "发货单": item.get("shipmentNo") or item.get("shipmentId"),
        "操作": "仓库收货" if key == ARRIVAL_TOOL else "仓储入库",
        "入库目标": "、".join(dict.fromkeys(target for target in targets if target)) or "未返回",
        "规则版本": item.get("inboundRuleVersion") or warehouse_inbound.PROCESSOR_INBOUND_RULE_VERSION,
        "明细": "；".join(details),
        "说明": (
            "本人确认后写入 ERP 到货确认。下一步再办仓储入库。"
            if key == ARRIVAL_TOOL
            else "确认卡展示 ERP processor-inbound-v1 行级库别，不以整单默认成品库。本人确认后写入 ERP 入库。"
        ),
    }
    if warnings:
        display["数据缺失"] = "；".join(dict.fromkeys(warnings))
    item = dict(item)
    item["confirmLines"] = lines
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
        "limitations": ["仅准备仓库回厂办理；本人确认后才调用 ERP。"],
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
        raise DomainError("CONFIRMATION_INVALID", "操作建议类型无效", 409)
    data = parse(key, proposal["input"])
    _, display = preview(db, user, key, data)
    if content_hash(display) != content_hash(proposal["display"]):
        raise DomainError("VERSION_CONFLICT", "到货或入库数量已变化，请重新查询后准备", 409)
    return proposal, data, key


def confirm(db, user, payload):
    _, data, key = validate_intent(db, user, payload)
    item = _require_shipment(data.shipment_id)
    field = "pendingArrivalQty" if key == ARRIVAL_TOOL else "pendingInboundQty"
    lines = _resolved_lines(data, item, field=field)
    if key == ARRIVAL_TOOL:
        result = post_erp(db, user, f"entrust/arrival-confirm/{data.shipment_id}/confirm", {
            "remark": data.remark,
            "confirmDetails": [
                {
                    "shipmentLineId": line["shipment_line_id"],
                    "confirmedArrivalQty": line["qty"],
                    "missingQty": 0,
                    "damagedQty": 0,
                }
                for line in lines
            ],
        }, intent_id=payload.get("_intent_id"), action="warehouse_arrival",
            native_id=f"product-shipment:{data.shipment_id}")
        return {
            "shipment_id": data.shipment_id,
            "action": "warehouse_arrival",
            "status": "CONFIRMED",
            "erp": result,
            "nextHint": "到货已确认。下一步办理仓储入库，再交给质检领取。",
        }
    result = post_erp(db, user, f"entrust/arrival-confirm/{data.shipment_id}/confirm-inbound", {
        "remark": data.remark,
        "inboundDetails": [
            {"shipmentLineId": line["shipment_line_id"], "inboundQty": line["qty"]}
            for line in lines
        ],
    }, intent_id=payload.get("_intent_id"), action="warehouse_inbound",
        native_id=f"product-shipment:{data.shipment_id}")
    erp_lines = warehouse_inbound.erp_inbound_confirm_lines(result)
    targets = [line["inboundTargetLabel"] for line in erp_lines if line.get("inboundTarget")]
    warnings = [line["inboundTargetWarning"] for line in erp_lines if line.get("inboundTargetWarning")]
    hint = "已入库。下一步质检领取任务并提交合格。"
    if not erp_lines or not targets:
        hint = "已提交入库。ERP 回执未给出行级库别，不能按成品库理解，请重新查询入库结果。"
    elif warnings:
        hint = f"{hint} {'；'.join(dict.fromkeys(str(item) for item in warnings))}"
    return {
        "shipment_id": data.shipment_id,
        "action": "warehouse_inbound",
        "status": "CONFIRMED",
        "erp": result,
        "inboundLines": erp_lines,
        "inboundTargets": list(dict.fromkeys(targets)),
        "inboundRuleVersion": next(
            (line.get("inboundRuleVersion") for line in erp_lines if line.get("inboundRuleVersion")),
            warehouse_inbound.PROCESSOR_INBOUND_RULE_VERSION,
        ),
        "nextHint": hint,
    }
