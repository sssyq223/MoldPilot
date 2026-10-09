"""Confirmation cards for ERP design write actions.

ERP design controls are exposed as tools so the Harness can discover them, but
they must not execute on the first model call.  The first call persists a
proposal card; the trusted human confirmation endpoint calls ``confirm`` and
only then invokes the existing ERP adapter.
"""
from __future__ import annotations

from typing import Any

from pydantic import ValidationError
from sqlalchemy import select

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

DESIGN_IMPORT_TOOLS = frozenset({
    "erp_design_import_new_mold",
    "erp_design_import_modify_mold",
})

_BROWSER_APPROVAL_CONFIG_ACTION = "erp_design_upload.approval_configured"


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


def _display(key: str, arguments: dict[str, Any], preflight: dict[str, Any] | None = None,
             file_display: dict[str, Any] | None = None) -> dict[str, Any]:
    title = erp_design_mcp.TOOL_NAMES.get(key, key)
    if key == "erp_design_upload_mold_repair_drawing":
        file_display = file_display or {}
        filename = str(file_display.get("filename") or "本次会话中的 DXF 图纸")
        mold_code = str(file_display.get("mold_code") or "待从文件名核对")
        return {
            "操作": "上传修改后的修模/改模图纸",
            "图纸文件": filename,
            "模号": mold_code,
            "文件格式": "DXF（ERP 支持格式）",
            "视觉识别": "执行" if not arguments.get("skip_vision", False) else "跳过（仅在明确要求时）",
            "执行后流程": "调用 ERP 原有上传接口，进行图纸比对、登记修改图纸异常，并进入设计主管审批。",
            "说明": "点击“确认执行”后才会调用 ERP；当前只是准备确认，尚未上传。",
        }
    is_import = key in DESIGN_IMPORT_TOOLS
    displayed_arguments = arguments
    if is_import:
        displayed_arguments = {
            "sessionId": arguments.get("session_id"),
            "moldCode": arguments.get("mold_code"),
            "sheetType": arguments.get("sheet_type"),
            "rowCount": len(arguments.get("preview_rows") or []),
            "expectedDate": arguments.get("expected_date"),
            "purchaseReason": arguments.get("purchase_reason"),
            "urgencyLevel": arguments.get("urgency_level"),
            "allowDuplicate": arguments.get("allow_duplicate", False),
            "designApproval": (preflight or {}).get("designApproval"),
        }
    return {
        "操作": title,
        "参数": displayed_arguments,
        "说明": (
            "本人批准后才调用 ERP 已封装的设计部门审批导入流程；本次准备阶段尚未调用 ERP 控制接口。"
            if is_import else
            "本人批准后才执行该 ERP 设计正式动作；本次准备阶段尚未调用 ERP 控制接口。"
        ),
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


def _latest_browser_import_binding(db, user, session_id: int) -> dict[str, Any]:
    """Recover the exact form payload previously reviewed in the browser."""
    if db is None:
        return {}
    event = db.scalar(select(m.AuditEvent).where(
        m.AuditEvent.user_id == user.id,
        m.AuditEvent.action == _BROWSER_APPROVAL_CONFIG_ACTION,
        m.AuditEvent.resource_id == str(session_id),
    ).order_by(m.AuditEvent.created_at.desc(), m.AuditEvent.id.desc()))
    detail = event.detail if event and isinstance(event.detail, dict) else {}
    binding = detail.get("binding")
    if not isinstance(binding, dict):
        return {}
    try:
        if int(binding.get("session_id")) != session_id:
            return {}
    except (TypeError, ValueError):
        return {}
    return dict(binding)


def _bind_import_arguments(db, user, key: str, arguments: dict[str, Any], run):
    """Bind an import proposal to the latest owned ERP upload snapshot."""
    session_id = erp_design_mcp._latest_conversation_upload_session(db, user, run)
    erp_design_mcp._owned_session(db, user, session_id)
    value = erp_design_mcp.call_mcp(
        "get_new_mold_upload_result", {"sessionId": session_id}
    )
    if not isinstance(value, dict):
        raise DomainError("ERP_DESIGN_MCP_FAILED", "ERP 上传会话未返回有效结果", 502)
    source = value.get("data") if isinstance(value.get("data"), dict) else value
    receipt = erp_design_mcp._conversation_upload_receipt(db, user, run, session_id) or {}
    browser_binding = _latest_browser_import_binding(db, user, session_id)
    if erp_design_mcp._drawing_status_is_processing(source):
        raise DomainError(
            "ERP_DRAWING_PROCESSING",
            "ERP 图纸匹配与归档仍在处理，完成后才能准备导入确认卡",
            409,
        )

    def field(*names):
        return (
            erp_design_mcp._first(browser_binding, *names)
            or erp_design_mcp._first(source, *names)
            or erp_design_mcp._first(receipt, *names)
        )

    order_type = str(field("designOrderType", "design_order_type") or "new_model").strip().lower()
    expected_tool = (
        "erp_design_import_modify_mold"
        if order_type == "repair_other"
        else "erp_design_import_new_mold"
    )
    if key != expected_tool:
        raise DomainError(
            "ERP_DESIGN_ORDER_TYPE_MISMATCH",
            f"ERP 当前表单类型为 {order_type}，应使用 {expected_tool}",
            409,
        )
    rows = browser_binding.get("preview_rows")
    if not isinstance(rows, list) or not rows:
        rows = erp_design_mcp._drawing_rows(source)
    if not rows:
        raise DomainError("ERP_VALIDATION_FAILED", "ERP 上传结果没有可导入明细", 409)
    normalized = {
        "session_id": session_id,
        "sheet_type": str(field("sheetType", "sheet_type") or "steel"),
        "preview_rows": rows,
        "mold_code": str(field("moldCode", "mold_code") or ""),
        "confirm_import": True,
        "urgency_level": str(field("urgencyLevel", "urgency_level") or "normal"),
        "expected_date": str(field("expectedDate", "expected_date") or ""),
        "purchase_reason": field("purchaseReason", "purchase_reason"),
        "remark": field("remark"),
        "allow_duplicate": bool(
            arguments.get("allow_duplicate", False)
            or browser_binding.get("allow_duplicate", False)
        ),
    }
    # Validate the exact bound payload before any confirmation card is shown.
    try:
        erp_design_mcp._INPUTS[key].model_validate(normalized)
    except ValidationError as error:
        raise DomainError(
            "INVALID_TOOL_INPUT",
            "ERP 设计导入必填信息不完整：" + error.errors()[0]["msg"],
            409,
        ) from None

    validation = erp_design_mcp.call_mcp("validate_new_mold_design_rows", {
        "sessionId": session_id,
        "sheetType": normalized["sheet_type"],
        "moldCode": normalized["mold_code"],
        "previewRows": rows,
        "pricingAlreadyEnriched": True,
    })
    if isinstance(validation, dict) and (
        validation.get("canImport") is False
        or validation.get("valid") is False
        or validation.get("errors")
    ):
        raise DomainError(
            "ERP_VALIDATION_FAILED",
            "ERP 校验未通过，不能生成导入确认卡",
            409,
        )
    if key == "erp_design_import_modify_mold":
        approval = erp_design_mcp.call_design_control_mcp(
            "get_modify_mold_approval_launch_config", {"sessionId": session_id}
        )
    else:
        approval = erp_design_mcp.call_mcp(
            "get_new_mold_approval_launch_config", {"sessionId": session_id}
        )
    approval = erp_design_mcp._with_design_approval_receipt(approval)
    preflight = {
        "sessionId": session_id,
        "validation": validation,
        "designApproval": (
            approval.get("designApproval") if isinstance(approval, dict) else None
        ),
    }
    return normalized, preflight


def execute_tool(db, user, key: str, arguments: dict, run=None):
    """Create a persisted proposal instead of executing an ERP write."""
    if key not in DESIGN_WRITE_TOOLS:
        raise DomainError("TOOL_UNKNOWN", "ERP 设计正式动作未登记", 403)
    if not isinstance(arguments, dict):
        raise DomainError("INVALID_TOOL_INPUT", "ERP 设计正式动作参数必须是对象")
    # Validate the input shape before showing a confirmation card.  This keeps
    # malformed requests in the normal tool-repair path and prevents a user
    # from approving an action that could not execute after confirmation.
    preflight = None
    if key in DESIGN_IMPORT_TOOLS:
        normalized, preflight = _bind_import_arguments(db, user, key, arguments, run)
    else:
        normalized = _normalized_arguments(key, arguments)
    try:
        erp_design_mcp._INPUTS[key].model_validate(normalized)
    except ValidationError as error:
        raise DomainError(
            "INVALID_TOOL_INPUT",
            "ERP 设计正式动作参数无效：" + error.errors()[0]["msg"],
        ) from None
    file_display = None
    if key == "erp_design_upload_mold_repair_drawing" and run:
        file_id = str(normalized.get("file_id") or "")
        blob = db.get(m.FileObject, file_id) if file_id else None
        if (blob and blob.owner_id == user.id
                and blob.conversation_id == run.conversation_id):
            filename = str(blob.filename or "")
            file_display = {"filename": filename}
            import re
            match = re.match(r"^(M\d{6}-P\d+)", filename, re.IGNORECASE)
            if match:
                file_display["mold_code"] = match.group(1).upper()
    confirmation_policy = proposal_confirmation_policy(run, requires_approval=True)
    if key == "erp_design_upload_mold_repair_drawing":
        confirmation_policy = {
            **confirmation_policy,
            "title": "确认后上传并进入设计主管审批",
            "description": "确认后才会调用 ERP 原有上传接口；上传成功后由 ERP 进行图纸比对、登记异常并流转设计主管审批。本人确认不会直接通过审批。",
        }
    elif key.startswith("erp_design_"):
        confirmation_policy = {
            **confirmation_policy,
            "title": "本人确认后办理 ERP 设计操作",
            "description": "确认后才会调用 ERP 原有接口；如该操作需要审批，将按 ERP 既有审批流程流转，不会因本人确认直接通过审批。",
        }
    proposal = {
        "kind": "erp_design_action",
        "action": "design_erp.execute",
        "tool": key,
        "requires_approval": True,
        "input": normalized,
        "display": _display(key, normalized, preflight, file_display),
        **({"preflight": preflight} if preflight else {}),
        "confirmation_policy": confirmation_policy,
    }
    return {
        "data": [],
        "source": "agent_proposal",
        "as_of": now().isoformat(),
        "proposal": proposal,
        "limitations": [
            "本轮只生成确认卡，未执行 ERP 设计正式动作。",
            "批准后由确认接口重新校验权限和参数，再调用 ERP 已封装的设计部门审批流程；ERP 回执不等于审批通过。",
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
    return set(available_tools(db, user, include_writes=True))


def confirm(db, user, payload):
    """Execute the already-approved ERP design action once."""
    proposal = validate_intent(db, user, payload)
    step, run = _run_for_step(db, user, payload["step_id"])
    # Bypass tool_gateway.execute intentionally: that function wraps the first
    # call into another proposal.  This is the trusted confirmation boundary.
    result = erp_design_mcp.execute_tool(
        db, user, proposal["tool"], proposal.get("input") or {}, run=run,
        trusted_confirmation=True,
    )
    return {
        "action": proposal["action"],
        "tool": proposal["tool"],
        "status": "EXECUTED",
        "step_id": step.id,
        "result": result,
    }
