"""Confirmation cards for ERP design write actions.

ERP design controls are exposed as tools so the Harness can discover them, but
they must not execute on the first model call.  The first call persists a
proposal card; the trusted human confirmation endpoint calls ``confirm`` and
only then invokes the existing ERP adapter.
"""
from __future__ import annotations

from typing import Any

from pydantic import ValidationError

from domain_packs.mold import models as m
from domain_packs.mold.authorization import fingerprint
from domain_packs.mold.ports.bpm import content_hash
from domain_packs.mold.ports.confirmation_policy import proposal_confirmation_policy
from domain_packs.mold.ports.db import now
from domain_packs.mold.ports.errors import DomainError
from domain_packs.mold.tools.erp.design import erp_design_mcp


# Keep this derived from the authoritative catalogue so a newly registered
# design write tool cannot silently bypass the confirmation boundary.
DESIGN_WRITE_TOOLS = frozenset(
    key for key, spec in erp_design_mcp.TOOL_SPECS.items()
    if spec.get("write") is True
)


def _run_for_step(db, user, step_id):
    step = db.get(m.Step, step_id)
    run = db.get(m.Run, step.run_id) if step else None
    if not step or not run or run.user_id != user.id:
        raise DomainError("NOT_FOUND", "操作建议不存在或无权访问", 404)
    if run.security_version != user.security_version or run.checkpoint.get("authorization_hash") != fingerprint(db, user):
        raise DomainError("AUTHORIZATION_CHANGED", "授权已变化，请重新准备操作", 403)
    if run.status not in {"RUNNING", "RUNNING_SCOPED", "SUCCEEDED"}:
        raise DomainError("PROPOSAL_STOPPED", "任务已停止，请重新准备操作", 409)
    return step, run


def _display(key: str, arguments: dict[str, Any]) -> dict[str, Any]:
    title = erp_design_mcp.TOOL_NAMES.get(key, key)
    return {
        "操作": title,
        "工具": key,
        "参数": arguments,
        "说明": "本人批准后才执行该 ERP 设计正式动作；本次准备阶段尚未调用 ERP 控制接口。",
    }


def _normalized_arguments(key: str, arguments: dict[str, Any]) -> dict[str, Any]:
    """Prepare adapter arguments without treating the card as approval itself.

    ERP design input models historically require ``confirm=True`` as a safety
    field.  The confirmation card is the host-level approval in the new flow,
    so a model request that has all business fields but omits that transport
    flag should still be able to create a card.  The flag is added only to the
    persisted, post-confirmation payload; a false value remains invalid.
    """
    normalized = dict(arguments)
    model = erp_design_mcp._INPUTS[key]
    if "confirm" in getattr(model, "model_fields", {}) and "confirm" not in normalized:
        normalized["confirm"] = True
    return normalized


def execute_tool(db, user, key: str, arguments: dict, run=None):
    """Create a persisted proposal instead of executing an ERP write."""
    if key not in DESIGN_WRITE_TOOLS:
        raise DomainError("TOOL_UNKNOWN", "ERP 设计正式动作未登记", 403)
    if not isinstance(arguments, dict):
        raise DomainError("INVALID_TOOL_INPUT", "ERP 设计正式动作参数必须是对象")
    # Validate the input shape before showing a confirmation card.  This keeps
    # malformed requests in the normal tool-repair path and prevents a user
    # from approving an action that could not execute after confirmation.
    normalized = _normalized_arguments(key, arguments)
    try:
        erp_design_mcp._INPUTS[key].model_validate(normalized)
    except ValidationError as error:
        raise DomainError(
            "INVALID_TOOL_INPUT",
            "ERP 设计正式动作参数无效：" + error.errors()[0]["msg"],
        ) from None
    proposal = {
        "kind": "erp_design_action",
        "action": "design_erp.execute",
        "tool": key,
        "requires_approval": True,
        "input": normalized,
        "display": _display(key, arguments),
        "confirmation_policy": proposal_confirmation_policy(run, requires_approval=True),
    }
    return {
        "data": [],
        "source": "agent_proposal",
        "as_of": now().isoformat(),
        "proposal": proposal,
        "limitations": [
            "本轮只生成确认卡，未执行 ERP 设计正式动作。",
            "批准后由确认接口重新校验权限和参数，再调用已登记的 ERP 设计控制工具。",
        ],
    }


def source(db, user, step_id: str):
    step, _run = _run_for_step(db, user, step_id)
    proposal = step.result.get("proposal") if isinstance(step.result, dict) else None
    if step.tool not in DESIGN_WRITE_TOOLS or not isinstance(proposal, dict):
        raise DomainError("TOOL_FORBIDDEN", "该设计正式动作没有可用的确认卡", 403)
    if proposal.get("tool") != step.tool or proposal.get("action") != "design_erp.execute":
        raise DomainError("CONFIRMATION_INVALID", "确认卡与设计动作不一致", 409)
    if step.tool not in erp_design_mcp.TOOL_SPECS:
        raise DomainError("TOOL_FORBIDDEN", "设计正式动作已从能力目录移除", 403)
    return proposal


def validate_intent(db, user, payload):
    proposal = source(db, user, payload["step_id"])
    if content_hash(proposal) != payload.get("proposal_hash"):
        raise DomainError("CONFIRMATION_INVALID", "确认卡内容已变化，请重新准备操作", 409)
    tool = proposal.get("tool")
    if tool not in erp_design_mcp.TOOL_SPECS or tool not in erp_design_mcp._INPUTS:
        raise DomainError("TOOL_FORBIDDEN", "设计正式动作当前不可用", 403)
    if tool not in _available_tools(db, user):
        raise DomainError("TOOL_FORBIDDEN", "当前用户没有该设计正式动作权限", 403)
    try:
        erp_design_mcp._INPUTS[tool].model_validate(proposal.get("input") or {})
    except ValidationError as error:
        raise DomainError(
            "INVALID_TOOL_INPUT",
            "ERP 设计正式动作参数无效：" + error.errors()[0]["msg"],
        ) from None
    return proposal


def _available_tools(db, user):
    from domain_packs.mold.tool_gateway import available_tools
    return set(available_tools(db, user))


def confirm(db, user, payload):
    """Execute the already-approved ERP design action once."""
    proposal = validate_intent(db, user, payload)
    step, run = _run_for_step(db, user, payload["step_id"])
    # Bypass tool_gateway.execute intentionally: that function wraps the first
    # call into another proposal.  This is the trusted confirmation boundary.
    result = erp_design_mcp.execute_tool(
        db, user, proposal["tool"], proposal.get("input") or {}, run=run
    )
    return {
        "action": proposal["action"],
        "tool": proposal["tool"],
        "status": "EXECUTED",
        "step_id": step.id,
        "result": result,
    }
