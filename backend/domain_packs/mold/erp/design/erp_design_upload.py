"""Authenticated HTTP bridge for the local ERP design-upload MCP.

The MCP remains a local stdio package.  This router gives MoldPilot's own
web application a narrow, audited way to use it without exposing ERP
credentials to the browser or wiring it into the Agent tool gateway.
"""
from __future__ import annotations

from base64 import b64decode
from binascii import Error as BinasciiError
from datetime import date, datetime, timedelta, timezone
from hashlib import sha256
import json
import secrets
from pathlib import Path
from queue import Empty, Queue
import shutil
import subprocess
import tempfile
from threading import Thread
from typing import Literal
from urllib.parse import quote
from uuid import UUID

from fastapi import APIRouter, Depends, Query, Response
from pydantic import Field, field_validator
from sqlalchemy import select

from domain_packs.mold import files, models as m
from agent_core.errors import DomainError
from agent_core.host_ports import host_ports
from agent_core.schemas import StrictModel
from domain_packs.mold.mcp_runtime import erp_design_upload_runtime
from domain_packs.mold.tools.erp.design import erp_design_mcp


router = APIRouter(prefix="/api/erp-design-uploads", tags=["ERP new-mold design uploads"])

_host = host_ports()
object_storage = _host.object_storage
get_db = _host.get_db
record = _host.record
current_user = _host.current_user
require = _host.require
_RUNTIME_ROOT = erp_design_upload_runtime()
_ENV_FILE = _RUNTIME_ROOT / ".env"
_SERVER_FILE = _RUNTIME_ROOT / "node_modules" / "erp-design-upload-mcp" / "scripts" / "erp-design-upload-mcp.mjs"
_PARSE_ACTION = "erp_design_upload.parsed"
_IMPORT_ACTION = "erp_design_upload.imported"
_APPROVAL_CONFIG_ACTION = "erp_design_upload.approval_configured"
_APPROVAL_TOKEN_TTL = timedelta(minutes=10)
_OWNED_SESSION_ACTIONS = (_PARSE_ACTION, erp_design_mcp._SESSION_ACTION)
_SHEET_TYPES = Literal["steel", "hardware", "stock_prepare"]
_PARSE_SHEET_TYPES = Literal["auto", "steel", "hardware", "stock_prepare"]
_DESIGN_FILE_EXTENSIONS = {".xlsx", ".xls", ".csv"}


class ParseInput(StrictModel):
    file_id: UUID
    sheet_type: _PARSE_SHEET_TYPES = "auto"
    design_order_sub_type: str | None = Field(default=None, max_length=100)


class SessionInput(StrictModel):
    session_id: int = Field(ge=1)


class ApprovalConfigInput(SessionInput):
    sheet_type: _SHEET_TYPES | None = None
    preview_rows: list[dict] | None = Field(default=None, max_length=1000)
    mold_code: str | None = Field(default=None, max_length=120)
    design_order_type: Literal["new_model", "repair_other"] | None = None
    urgency_level: Literal["normal", "important", "urgent"] = "normal"
    expected_date: str | None = Field(default=None, min_length=10, max_length=10, pattern=r"^\d{4}-\d{2}-\d{2}$")
    purchase_reason: str | None = Field(default=None, max_length=200)
    remark: str | None = Field(default=None, max_length=1000)
    allow_duplicate: bool = False


class StatusInput(SessionInput):
    include_result: bool = False


class RowsInput(SessionInput):
    sheet_type: _SHEET_TYPES
    preview_rows: list[dict] = Field(min_length=1, max_length=1000)
    mold_code: str | None = Field(default=None, max_length=120)


class RepriceInput(RowsInput):
    pass


class ImportInput(RowsInput):
    confirm_import: Literal[True]
    approval_token: str = Field(
        min_length=20,
        max_length=200,
        description="由宿主在展示 ERP 设计审批配置后签发的一次性导入凭证。模型不能自行生成。",
    )
    # The ERP order form owns this choice.  Parsing a file only prepares the
    # session; the browser may select 新模 or 改模 before the final import.
    design_order_type: Literal["new_model", "repair_other"] | None = None
    urgency_level: Literal["normal", "important", "urgent"] = "normal"
    expected_date: str = Field(min_length=10, max_length=10, pattern=r"^\d{4}-\d{2}-\d{2}$")
    purchase_reason: str | None = Field(default=None, max_length=200)
    remark: str | None = Field(default=None, max_length=1000)
    allow_duplicate: bool = False

    @field_validator("expected_date")
    @classmethod
    def expected_date_must_be_reasonable(cls, value: str) -> str:
        # Do not let a direct browser call bypass the follow-up validation
        # performed by the agent. A procurement delivery date in the past is
        # almost always an omitted/guessed answer and must be corrected first.
        parsed = date.fromisoformat(value)
        if parsed < date.today():
            raise ValueError("交期不能早于今天，请重新选择合理交期")
        return value


class ImportStatusesInput(StrictModel):
    session_ids: list[int] = Field(min_length=1, max_length=200)


def _mcp_failure(message: str, status: int = 502):
    # The package does not return tokens, but bound any unexpected tool output
    # before it becomes a browser-visible error message.
    message = " ".join(str(message).split())[:1000]
    if "重复上传" in message:
        raise DomainError("ERP_DUPLICATE_CONFIRMATION_REQUIRED", message, 409)
    raise DomainError("ERP_DESIGN_MCP_FAILED", message or "ERP 设计上传服务调用失败", status)


def _read_line(stream, queue: Queue):
    try:
        while True:
            line = stream.readline()
            if not line:
                return
            queue.put(line)
    finally:
        queue.put(None)


def call_mcp(name: str, arguments: dict):
    """Run one stdio MCP request while keeping stdin open for async tool work."""
    if not _ENV_FILE.is_file() or not _SERVER_FILE.is_file():
        _mcp_failure("ERP 设计上传 MCP 尚未安装或本地配置不完整", 503)

    try:
        process = subprocess.Popen(
            ["node", f"--env-file-if-exists={_ENV_FILE}", str(_SERVER_FILE)],
            cwd=_RUNTIME_ROOT,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
            encoding="utf-8",
            errors="replace",
        )
    except OSError:
        _mcp_failure("无法启动 ERP 设计上传 MCP，请检查本机 Node.js 环境", 503)
    if process.stdin is None or process.stdout is None:
        _mcp_failure("无法启动 ERP 设计上传 MCP", 503)

    output: Queue[str | None] = Queue()
    reader = Thread(target=_read_line, args=(process.stdout, output), daemon=True)
    reader.start()
    requests = [
        {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {
            "protocolVersion": "2024-11-05", "capabilities": {}, "clientInfo": {"name": "MoldPilot", "version": "0.1.0"}
        }},
        {"jsonrpc": "2.0", "method": "notifications/initialized", "params": {}},
        {"jsonrpc": "2.0", "id": 2, "method": "tools/call", "params": {"name": name, "arguments": arguments}},
    ]
    try:
        for request in requests:
            process.stdin.write(json.dumps(request, ensure_ascii=False) + "\n")
        process.stdin.flush()

        while True:
            try:
                line = output.get(timeout=910)
            except Empty:
                _mcp_failure("ERP 设计上传处理超时", 504)
            if line is None:
                _mcp_failure("ERP 设计上传 MCP 在返回结果前停止", 502)
            try:
                response = json.loads(line)
            except json.JSONDecodeError:
                continue
            if response.get("id") != 2:
                continue
            if response.get("error"):
                _mcp_failure(response["error"].get("message", "MCP 请求失败"))
            result = response.get("result")
            if not isinstance(result, dict):
                _mcp_failure("ERP 设计上传 MCP 返回结构无效")
            if result.get("isError"):
                content = result.get("content") or []
                text = content[0].get("text", "ERP 设计上传失败") if content and isinstance(content[0], dict) else "ERP 设计上传失败"
                _mcp_failure(text)
            value = result.get("structuredContent")
            if isinstance(value, (dict, list)):
                return value
            content = result.get("content") or []
            text = content[0].get("text") if content and isinstance(content[0], dict) else None
            try:
                return json.loads(text) if isinstance(text, str) else {}
            except json.JSONDecodeError:
                _mcp_failure("ERP 设计上传 MCP 未返回可读取结果")
    finally:
        if process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=3)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=3)


def _owned_session(db, user, session_id: int):
    event = db.scalar(select(m.AuditEvent).where(
        m.AuditEvent.user_id == user.id,
        m.AuditEvent.action.in_(_OWNED_SESSION_ACTIONS),
        m.AuditEvent.resource_id == str(session_id),
    ).order_by(m.AuditEvent.created_at.desc()))
    if not event:
        raise DomainError("ERP_UPLOAD_NOT_FOUND", "上传会话不存在，或不属于当前账号", 404)
    return event


def _temp_design_file(filename: str, data: bytes):
    directory = Path(tempfile.mkdtemp(prefix="moldpilot-erp-design-"))
    path = directory / filename
    path.write_bytes(data)
    return directory, path


def _validation_allows_import(value):
    return isinstance(value, dict) and value.get("canImport") is not False and value.get("valid") is not False and not value.get("errors")


def _import_result(value) -> dict:
    if not isinstance(value, dict):
        return {}
    nested = value.get("data")
    return nested if isinstance(nested, dict) else value


def _with_form_options(value):
    if not isinstance(value, dict):
        return value
    return {**value, **erp_design_mcp.design_upload_form_options()}


def _import_receipt_detail(value, row_count: int) -> dict:
    result = _import_result(value)
    workflow = result.get("workflow") if isinstance(result.get("workflow"), dict) else result
    summary = _approval_process_summary(value)
    return {
        "row_count": row_count,
        "request_no": str(result.get("requestNo") or result.get("request_no") or "").strip(),
        "message": str(result.get("message") or result.get("successMessage") or result.get("success_message") or "").strip()[:500],
        "request_id": str(workflow.get("requestId") or workflow.get("request_id") or result.get("requestId") or result.get("request_id") or "").strip(),
        "process_code": str(workflow.get("processCode") or workflow.get("process_code") or result.get("processCode") or result.get("process_code") or summary.get("processCode") or "").strip(),
        "process_name": str(workflow.get("processName") or workflow.get("process_name") or result.get("processName") or result.get("process_name") or summary.get("processName") or "").strip(),
        "current_node_name": str(workflow.get("currentNodeName") or workflow.get("current_node_name") or workflow.get("currentApprovalNodeName") or workflow.get("current_approval_node_name") or result.get("currentNodeName") or result.get("current_node_name") or result.get("currentApprovalNodeName") or result.get("current_approval_node_name") or summary.get("currentNodeName") or "").strip(),
        "current_approver_names": _string_list(workflow.get("currentApproverNames") or workflow.get("current_approver_names") or result.get("currentApproverNames") or result.get("current_approver_names") or summary.get("currentApproverNames")),
        "approval_steps": _normalize_approval_steps(workflow.get("approvalSteps") or workflow.get("approval_steps") or result.get("approvalSteps") or result.get("approval_steps") or summary.get("approvalSteps")),
        "approval_status": str(workflow.get("approvalStatus") or workflow.get("approval_status") or result.get("approvalStatus") or result.get("approval_status") or "PENDING").strip(),
        "workflow_instance_id": str(workflow.get("workflowInstanceId") or workflow.get("workflow_instance_id") or result.get("workflowInstanceId") or result.get("workflow_instance_id") or "").strip(),
    }


def _enrich_import_from_order_detail(value):
    """Read the ERP order created by import so its workflow id is not guessed."""
    result = _import_result(value)
    request_id = result.get("requestId") or result.get("request_id")
    try:
        request_id = int(request_id)
    except (TypeError, ValueError):
        return value
    try:
        detail = call_mcp("get_erp_design_record", {"resource": "design_order", "id": request_id})
    except DomainError:
        # The import has already committed in ERP. A supplementary read must
        # never turn that successful write into an apparent failed write.
        return value
    detail = _import_result(detail)
    if not isinstance(detail, dict):
        return value
    enriched = dict(result)
    aliases = {
        "requestId": ("requestId", "request_id"),
        "requestNo": ("requestNo", "request_no", "orderNo", "order_no"),
        "currentNodeName": ("currentNodeName", "current_node_name", "currentApprovalNodeName", "current_approval_node_name"),
        "approvalStatus": ("approvalStatus", "approval_status"),
        "workflowInstanceId": ("workflowInstanceId", "workflow_instance_id"),
    }
    for output_key, candidates in aliases.items():
        if enriched.get(output_key) not in (None, ""):
            continue
        candidate = next((detail.get(key) for key in candidates if detail.get(key) not in (None, "")), None)
        if candidate not in (None, ""):
            enriched[output_key] = candidate
    if isinstance(value, dict) and isinstance(value.get("data"), dict):
        return {**value, "data": enriched}
    return enriched


def _approval_hash(value) -> str:
    return sha256(json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str).encode("utf-8")).hexdigest()


def _approval_result(value) -> dict:
    result = _import_result(value)
    return result if isinstance(result, dict) else {}


def _string_list(value) -> list[str]:
    values = value if isinstance(value, list) else ([value] if value not in (None, "") else [])
    result: list[str] = []
    for item in values:
        text = str(item or "").strip()
        if text and text not in result:
            result.append(text)
    return result


def _approval_user_names(value) -> list[str]:
    users = value if isinstance(value, list) else []
    names: list[str] = []
    for user in users:
        if not isinstance(user, dict):
            continue
        name = str(
            user.get("nickName") or user.get("nick_name")
            or user.get("displayName") or user.get("display_name")
            or user.get("userName") or user.get("user_name") or ""
        ).strip()
        if name and name not in names:
            names.append(name)
    return names


def _normalize_approval_steps(value) -> list[dict]:
    steps: list[dict] = []
    for item in value if isinstance(value, list) else []:
        if not isinstance(item, dict):
            continue
        node_name = str(item.get("nodeName") or item.get("node_name") or item.get("name") or "").strip()
        approver_names = _string_list(item.get("approverNames") or item.get("approver_names"))
        if node_name:
            steps.append({"nodeName": node_name, "approverNames": approver_names})
    return steps


def _approval_steps(nodes) -> list[dict]:
    steps: list[dict] = []
    for node in nodes if isinstance(nodes, list) else []:
        if not isinstance(node, dict) or str(node.get("nodeType") or node.get("node_type") or "").lower() != "approval":
            continue
        node_name = str(node.get("nodeName") or node.get("node_name") or node.get("name") or "").strip()
        resolved_users = node.get("resolvedAssigneeUsers") or node.get("resolved_assignee_users") or []
        if node_name:
            steps.append({"nodeName": node_name, "approverNames": _approval_user_names(resolved_users)})
    return steps


def _approval_process_summary(value) -> dict:
    result = _approval_result(value)
    workflow = result.get("workflow") if isinstance(result.get("workflow"), dict) else result
    process = result.get("process") if isinstance(result.get("process"), dict) else {}
    nodes = result.get("nodes") if isinstance(result.get("nodes"), list) else []
    approval_node = next(
        (
            node for node in nodes
            if isinstance(node, dict)
            and str(node.get("nodeType") or node.get("node_type") or "").lower() == "approval"
        ),
        next((node for node in nodes if isinstance(node, dict)), {}),
    )
    approval_steps = _approval_steps(nodes)
    current_approver_names = approval_steps[0]["approverNames"] if approval_steps else []
    return {
        "processCode": str(
            workflow.get("processCode") or workflow.get("process_code")
            or result.get("resolvedProcessCode") or result.get("resolved_process_code")
            or process.get("processCode") or process.get("process_code") or process.get("code") or ""
        ).strip(),
        "processName": str(
            workflow.get("processName") or workflow.get("process_name")
            or result.get("processName") or result.get("process_name")
            or process.get("name") or process.get("processName") or process.get("process_name") or ""
        ).strip(),
        "currentNodeName": str(
            workflow.get("currentNodeName") or workflow.get("current_node_name")
            or result.get("currentNodeName") or result.get("current_node_name")
            or approval_node.get("nodeName") or approval_node.get("node_name") or approval_node.get("name") or ""
        ).strip(),
        "currentApproverNames": current_approver_names,
        "approvalSteps": approval_steps,
    }


def _approval_binding(data: ApprovalConfigInput, design_order_type: str, config: dict) -> dict:
    return {
        "session_id": data.session_id,
        "sheet_type": data.sheet_type,
        "preview_rows": data.preview_rows or [],
        "mold_code": data.mold_code,
        "design_order_type": design_order_type,
        "urgency_level": data.urgency_level,
        "expected_date": data.expected_date,
        "purchase_reason": data.purchase_reason,
        "remark": data.remark,
        "allow_duplicate": data.allow_duplicate,
        "approval_config_hash": _approval_hash(_approval_process_summary(config)),
    }


def _approval_token_detail(db, user, data: ImportInput, design_order_type: str, config: dict):
    binding = _approval_binding(ApprovalConfigInput(
        session_id=data.session_id,
        sheet_type=data.sheet_type,
        preview_rows=data.preview_rows,
        mold_code=data.mold_code,
        design_order_type=design_order_type,
        urgency_level=data.urgency_level,
        expected_date=data.expected_date,
        purchase_reason=data.purchase_reason,
        remark=data.remark,
        allow_duplicate=data.allow_duplicate,
    ), design_order_type, config)
    payload_hash = _approval_hash(binding)
    event = db.scalar(select(m.AuditEvent).where(
        m.AuditEvent.user_id == user.id,
        m.AuditEvent.action == _APPROVAL_CONFIG_ACTION,
        m.AuditEvent.resource_id == str(data.session_id),
    ).order_by(m.AuditEvent.created_at.desc(), m.AuditEvent.id.desc()).with_for_update())
    detail = event.detail if event and isinstance(event.detail, dict) else {}
    expires_at = str(detail.get("expires_at") or "")
    try:
        expired = datetime.fromisoformat(expires_at).astimezone(timezone.utc) <= datetime.now(timezone.utc)
    except (TypeError, ValueError):
        expired = True
    if not event or detail.get("consumed") or expired or detail.get("payload_hash") != payload_hash:
        raise DomainError("ERP_DESIGN_CONFIRMATION_REQUIRED", "请重新读取 ERP 设计审批配置并在确认后导入", 409)
    token_hash = sha256(data.approval_token.encode("utf-8")).hexdigest()
    if not secrets.compare_digest(str(detail.get("token_hash") or ""), token_hash):
        raise DomainError("ERP_DESIGN_CONFIRMATION_INVALID", "ERP 设计导入确认凭证无效，请重新确认", 403)
    return event


def _mark_approval_consumed(event, receipt: dict):
    detail = dict(event.detail) if isinstance(event.detail, dict) else {}
    detail.update({"consumed": True, "consumed_at": datetime.now(timezone.utc).isoformat(), "receipt": receipt})
    event.detail = detail


def _query_import_after_timeout(session_id: int):
    """Reconcile a lost ERP response once; never blindly submit again."""
    try:
        status = call_mcp("get_new_mold_upload_status", {"sessionId": session_id, "includeResult": True})
    except DomainError:
        return None
    result = _import_result(status)
    if any(result.get(key) for key in ("requestId", "request_id", "requestNo", "request_no")):
        return result
    state = str(result.get("status") or result.get("sessionStatus") or result.get("session_status") or "").strip().lower()
    if state in {"imported", "completed_import", "submitted"}:
        raise DomainError(
            "ERP_IMPORT_RECONCILIATION_REQUIRED",
            "ERP 响应超时，上传会话已显示为已导入；已停止自动重试，请先查询 ERP 请购结果",
            504,
            details={"sessionId": session_id, "status": state},
        )
    return None


def _import_once_or_reconcile(session_id: int, importer):
    try:
        return importer()
    except DomainError as exc:
        if exc.status != 504:
            raise
        reconciled = _query_import_after_timeout(session_id)
        if reconciled is not None:
            return reconciled
        raise DomainError(
            "ERP_IMPORT_TIMEOUT_REQUIRES_RECONCILIATION",
            "ERP 导入响应超时，已查询原上传会话但未取得请购回执；为避免重复创建，未自动重试，请查询 ERP 后再继续",
            504,
            details={"sessionId": session_id},
        ) from exc


def _drawing_rows(value) -> list[dict]:
    if not isinstance(value, dict):
        return []
    source = value.get("data") if isinstance(value.get("data"), dict) else value
    rows = source.get("previewRows") if isinstance(source.get("previewRows"), list) else source.get("preview_rows")
    return [row for row in rows if isinstance(row, dict)] if isinstance(rows, list) else []


def _drawing_ids(value) -> set[int]:
    result = set()
    for row in _drawing_rows(value):
        raw_id = row.get("drawing_resource_id") or row.get("drawingResourceId") or row.get("drawing_id") or row.get("drawingId")
        try:
            drawing_id = int(raw_id)
        except (TypeError, ValueError):
            continue
        if drawing_id > 0:
            result.add(drawing_id)
    return result


def _drawing_row(value, drawing_id: int) -> dict | None:
    for row in _drawing_rows(value):
        raw_id = row.get("drawing_resource_id") or row.get("drawingResourceId") or row.get("drawing_id") or row.get("drawingId")
        try:
            if int(raw_id) == drawing_id:
                return row
        except (TypeError, ValueError):
            continue
    return None


@router.post("/parse")
def parse_design(data: ParseInput, user=Depends(current_user), db=Depends(get_db)):
    require(db, user, "design_route.create")
    source = files.uploaded_file(db, user, str(data.file_id))
    if Path(source.filename).suffix.lower() not in _DESIGN_FILE_EXTENSIONS:
        raise DomainError("ERP_DESIGN_FILE_TYPE", "新模设计上传仅支持 XLSX、XLS 或 CSV 文件")
    file_data = object_storage.read(source)
    filename, _ = files.validate_file(source.filename, file_data)
    directory, path = _temp_design_file(filename, file_data)
    try:
        result = erp_design_mcp.call_design_control_mcp("parse_new_mold_design_file_auto", {
            "filePath": str(path), "sheetType": data.sheet_type, "designOrderSubType": data.design_order_sub_type,
        })
    finally:
        shutil.rmtree(directory, ignore_errors=True)
    session_id = result.get("sessionId") if isinstance(result, dict) else None
    if not isinstance(session_id, int) or session_id < 1:
        _mcp_failure("ERP 未返回有效上传会话编号")
    detected_sheet_type = result.get("sheetType") if isinstance(result, dict) else None
    if detected_sheet_type not in {"steel", "hardware", "stock_prepare"}:
        _mcp_failure("ERP 未返回有效的清单类型")
    record(db, user, _PARSE_ACTION, str(session_id), {
        "file_id": str(source.id), "filename": filename, "sheet_type": detected_sheet_type,
    })
    db.commit()
    return _with_form_options(result)


@router.post("/status")
def upload_status(data: StatusInput, user=Depends(current_user), db=Depends(get_db)):
    require(db, user, "design_route.read")
    _owned_session(db, user, data.session_id)
    return _with_form_options(call_mcp("get_new_mold_upload_status", {"sessionId": data.session_id, "includeResult": data.include_result}))


@router.post("/result")
def upload_result(data: SessionInput, user=Depends(current_user), db=Depends(get_db)):
    require(db, user, "design_route.read")
    _owned_session(db, user, data.session_id)
    return _with_form_options(call_mcp("get_new_mold_upload_result", {"sessionId": data.session_id}))


@router.get("/{session_id}/drawings/{drawing_id}/preview")
def drawing_preview(session_id: int, drawing_id: int, user=Depends(current_user), db=Depends(get_db)):
    require(db, user, "design_route.read")
    _owned_session(db, user, session_id)
    status = call_mcp("get_new_mold_upload_status", {"sessionId": session_id, "includeResult": True})
    row = _drawing_row(status, drawing_id)
    if row is None:
        raise DomainError("ERP_DRAWING_NOT_IN_SESSION", "该图纸不属于当前上传会话", 404)
    preview_url = row.get("drawing_preview_url") or row.get("drawingPreviewUrl")
    value = erp_design_mcp.call_design_control_mcp("download_erp_design_file", {
        "artifact": "drawing_preview", "drawingId": drawing_id, "previewUrl": preview_url,
    })
    return _drawing_preview_response(value, f"drawing-{drawing_id}-preview")


@router.get("/standard-hardware/preview")
def standard_hardware_preview(
    relative_path: str = Query(min_length=1, max_length=500),
    user=Depends(current_user), db=Depends(get_db),
):
    require(db, user, "design_route.read")
    # The ERP endpoint owns path validation, file selection and preview generation.
    value = erp_design_mcp.call_design_control_mcp("download_erp_design_file", {
        "artifact": "standard_hardware_preview", "relativePath": relative_path,
    })
    return _drawing_preview_response(value, "standard-hardware-preview")


def _drawing_preview_response(value, fallback_name: str):
    if not isinstance(value, dict) or not isinstance(value.get("base64"), str):
        _mcp_failure("ERP 图纸预览未返回有效文件")
    try:
        content = b64decode(value["base64"], validate=True)
    except (ValueError, BinasciiError):
        _mcp_failure("ERP 图纸预览内容无效")
    if len(content) < 64 or len(content) > 20 * 1024 * 1024:
        _mcp_failure("ERP 图纸预览为空、不完整或超过 20 MB 限制", 413)
    media_type = str(value.get("mediaType") or "application/octet-stream").split(";", 1)[0].strip()
    if media_type.lower() == "application/json":
        _mcp_failure("ERP 返回了错误信息而不是图纸预览")
    filename = Path(str(value.get("fileName") or fallback_name)).name.replace('"', "_")
    return Response(content=content, media_type=media_type, headers={
        "Cache-Control": "private, max-age=300",
        "Content-Disposition": f"inline; filename=\"{fallback_name}\"; filename*=UTF-8''{quote(filename)}",
    })


@router.post("/validate")
def validate_rows(data: RowsInput, user=Depends(current_user), db=Depends(get_db)):
    require(db, user, "design_route.create")
    _owned_session(db, user, data.session_id)
    result = call_mcp("validate_new_mold_design_rows", {
        "sessionId": data.session_id, "sheetType": data.sheet_type, "moldCode": data.mold_code,
        "previewRows": data.preview_rows, "pricingAlreadyEnriched": True,
    })
    record(db, user, "erp_design_upload.validated", str(data.session_id), {"row_count": len(data.preview_rows)})
    db.commit()
    return result


@router.post("/reprice")
def reprice_rows(data: RepriceInput, user=Depends(current_user), db=Depends(get_db)):
    require(db, user, "design_route.create")
    _owned_session(db, user, data.session_id)
    return call_mcp("reprice_new_mold_design_rows", {
        "sheetType": data.sheet_type, "moldCode": data.mold_code, "previewRows": data.preview_rows,
    })


@router.post("/approval-config")
def approval_config(data: ApprovalConfigInput, user=Depends(current_user), db=Depends(get_db)):
    require(db, user, "design_route.read")
    session_event = _owned_session(db, user, data.session_id)
    session_detail = session_event.detail if isinstance(session_event.detail, dict) else {}
    design_order_type = str(
        data.design_order_type or session_detail.get("design_order_type") or "new_model"
    ).strip().lower()
    if design_order_type == "repair_other":
        config = erp_design_mcp.call_design_control_mcp(
            "get_modify_mold_approval_launch_config", {"sessionId": data.session_id}
        )
    else:
        config = call_mcp("get_new_mold_approval_launch_config", {"sessionId": data.session_id})
    binding = _approval_binding(data, design_order_type, config if isinstance(config, dict) else {})
    token = secrets.token_urlsafe(32)
    approval_id = secrets.token_urlsafe(16)
    expires_at = datetime.now(timezone.utc) + _APPROVAL_TOKEN_TTL
    record(db, user, _APPROVAL_CONFIG_ACTION, str(data.session_id), {
        "approval_id": approval_id,
        "token_hash": sha256(token.encode("utf-8")).hexdigest(),
        "payload_hash": _approval_hash(binding),
        # Keep the exact, non-secret business payload server-side. If the page
        # refreshes after showing the approval configuration, the Agent path
        # can restore the user's order type, due date and edited rows without
        # asking a model to reconstruct them or trusting browser-only state.
        "binding": binding,
        "expires_at": expires_at.isoformat(),
        "consumed": False,
        "design_order_type": design_order_type,
        "process": _approval_process_summary(config),
    })
    db.flush()
    summary = _approval_process_summary(config)
    response = {
        **(_with_form_options(config) if isinstance(config, dict) else {"data": config}),
        "approval": {
            "proposalId": approval_id,
            "approvalToken": token,
            "expiresAt": expires_at.isoformat(),
            **summary,
        },
    }
    db.commit()
    return response


@router.post("/import-statuses")
def import_statuses(data: ImportStatusesInput, user=Depends(current_user), db=Depends(get_db)):
    """Restore completed import receipts and refresh their live ERP workflow state."""
    require(db, user, "design_route.read")
    requested = {str(session_id) for session_id in data.session_ids}
    events = db.scalars(select(m.AuditEvent).where(
        m.AuditEvent.user_id == user.id,
        m.AuditEvent.action == _IMPORT_ACTION,
        m.AuditEvent.resource_id.in_(requested),
    ).order_by(m.AuditEvent.created_at.desc())).all()
    receipts = []
    seen = set()
    for event in events:
        if event.resource_id in seen:
            continue
        seen.add(event.resource_id)
        detail = event.detail if isinstance(event.detail, dict) else {}
        receipt = {
            "sessionId": int(event.resource_id),
            "requestNo": str(detail.get("request_no") or ""),
            "message": str(detail.get("message") or ""),
            "requestId": str(detail.get("request_id") or ""),
            "processCode": str(detail.get("process_code") or ""),
            "processName": str(detail.get("process_name") or ""),
            "currentNodeName": str(detail.get("current_node_name") or ""),
            "currentApproverNames": _string_list(detail.get("current_approver_names")),
            "approvalSteps": _normalize_approval_steps(detail.get("approval_steps")),
            "approvalStatus": str(detail.get("approval_status") or "PENDING"),
            "workflowInstanceId": str(detail.get("workflow_instance_id") or ""),
            "importedAt": event.created_at.isoformat(),
        }
        # The audit event is intentionally an immutable import receipt.  Its
        # current node is only the node that existed at import time, so using
        # it as the live card state leaves the UI stuck on 设计主管审批 after
        # the workflow advances.  A supplementary ERP read is best-effort:
        # failure must preserve the successful import receipt.
        request_id = str(receipt.get("requestId") or "").strip()
        try:
            if request_id:
                live = _import_receipt_detail(
                    call_mcp(
                        "get_erp_design_record",
                        {"resource": "design_order", "id": int(request_id)},
                    ),
                    int(detail.get("row_count") or 0),
                )
                for key, live_key in (
                    ("requestNo", "request_no"),
                    ("requestId", "request_id"),
                    ("processCode", "process_code"),
                    ("processName", "process_name"),
                    ("currentNodeName", "current_node_name"),
                    ("currentApproverNames", "current_approver_names"),
                    ("approvalSteps", "approval_steps"),
                    ("approvalStatus", "approval_status"),
                    ("workflowInstanceId", "workflow_instance_id"),
                ):
                    value = live.get(live_key)
                    if value not in (None, "", [], {}):
                        receipt[key] = value
        except Exception:  # ERP status refresh is supplementary to the receipt.
            pass
        receipts.append(receipt)
    return {"receipts": receipts}


@router.post("/import")
def import_design(data: ImportInput, user=Depends(current_user), db=Depends(get_db)):
    require(db, user, "design_route.execute")
    session_event = _owned_session(db, user, data.session_id)
    validation = call_mcp("validate_new_mold_design_rows", {
        "sessionId": data.session_id, "sheetType": data.sheet_type, "moldCode": data.mold_code,
        "previewRows": data.preview_rows, "pricingAlreadyEnriched": True,
    })
    if not _validation_allows_import(validation):
        raise DomainError("ERP_VALIDATION_FAILED", "ERP 校验未通过，不能导入。请处理明细错误后重新校验", 409)
    detail = session_event.detail if isinstance(session_event.detail, dict) else {}
    design_order_type = str(
        data.design_order_type or detail.get("design_order_type") or "new_model"
    ).strip().lower()
    approval_config = (
        erp_design_mcp.call_design_control_mcp("get_modify_mold_approval_launch_config", {"sessionId": data.session_id})
        if design_order_type == "repair_other"
        else call_mcp("get_new_mold_approval_launch_config", {"sessionId": data.session_id})
    )
    approval_event = _approval_token_detail(db, user, data, design_order_type, approval_config if isinstance(approval_config, dict) else {})
    common_arguments = {
        "sessionId": data.session_id, "sheetType": data.sheet_type, "moldCode": data.mold_code,
        "previewRows": data.preview_rows, "urgencyLevel": data.urgency_level, "expectedDate": data.expected_date,
        "purchaseReason": data.purchase_reason, "remark": data.remark, "allowDuplicate": data.allow_duplicate,
    }
    try:
        if design_order_type == "repair_other":
            allowed_reasons = {
                str(item["value"])
                for item in erp_design_mcp.MODIFY_MOLD_PURCHASE_REASON_OPTIONS
            }
            if data.purchase_reason not in allowed_reasons:
                raise DomainError("ERP_MODIFY_PURCHASE_REASON_INVALID", "请从 ERP 提供的修模改模请购原因选项中选择", 422)
            # Reuse the existing ERP control MCP flow; do not downgrade a
            # repair_other session to the new-model import endpoint.
            result = _import_once_or_reconcile(
                data.session_id,
                lambda: erp_design_mcp.call_design_control_mcp("import_modify_mold_design", common_arguments),
            )
        else:
            result = _import_once_or_reconcile(
                data.session_id,
                lambda: call_mcp("import_new_mold_design", common_arguments),
            )
    except DomainError as exc:
        if exc.code in {"ERP_IMPORT_RECONCILIATION_REQUIRED", "ERP_IMPORT_TIMEOUT_REQUIRES_RECONCILIATION"}:
            _mark_approval_consumed(approval_event, {
                "status": "AMBIGUOUS",
                "message": exc.message,
                "session_id": data.session_id,
            })
            db.commit()
        raise
    result = _enrich_import_from_order_detail(result)
    receipt = _import_receipt_detail(result, len(data.preview_rows))
    approval_summary = _approval_process_summary(approval_config)
    receipt["process_code"] = receipt["process_code"] or approval_summary["processCode"]
    receipt["process_name"] = receipt["process_name"] or approval_summary["processName"]
    receipt["current_node_name"] = receipt["current_node_name"] or approval_summary["currentNodeName"]
    receipt["current_approver_names"] = receipt["current_approver_names"] or approval_summary["currentApproverNames"]
    receipt["approval_steps"] = receipt["approval_steps"] or approval_summary["approvalSteps"]
    result_payload = _approval_result(result)
    if not result_payload.get("duplicateUpload") and not result_payload.get("duplicate_upload"):
        _mark_approval_consumed(approval_event, receipt)
    receipt["design_order_type"] = design_order_type
    record(db, user, _IMPORT_ACTION, str(data.session_id), receipt)
    db.commit()
    if isinstance(result, dict):
        enriched = dict(result)
        for key, value in {
            "requestNo": receipt["request_no"],
            "requestId": receipt["request_id"],
            "processCode": receipt["process_code"],
            "processName": receipt["process_name"],
            "currentNodeName": receipt["current_node_name"],
            "currentApproverNames": receipt["current_approver_names"],
            "approvalSteps": receipt["approval_steps"],
            "approvalStatus": receipt["approval_status"],
            "workflowInstanceId": receipt["workflow_instance_id"],
        }.items():
            if value:
                enriched.setdefault(key, value)
        return enriched
    return result
