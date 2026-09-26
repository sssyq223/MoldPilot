"""Authenticated HTTP bridge for the local ERP design-upload MCP.

The MCP remains a local stdio package.  This router gives MoldPilot's own
web application a narrow, audited way to use it without exposing ERP
credentials to the browser or wiring it into the Agent tool gateway.
"""
from __future__ import annotations

from base64 import b64decode
from binascii import Error as BinasciiError
from datetime import date
import json
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
_OWNED_SESSION_ACTIONS = (_PARSE_ACTION, erp_design_mcp._SESSION_ACTION)
_SHEET_TYPES = Literal["steel", "hardware"]
_PARSE_SHEET_TYPES = Literal["auto", "steel", "hardware"]
_DESIGN_FILE_EXTENSIONS = {".xlsx", ".xls", ".csv"}


class ParseInput(StrictModel):
    file_id: UUID
    sheet_type: _PARSE_SHEET_TYPES = "auto"
    design_order_sub_type: str | None = Field(default=None, max_length=100)


class SessionInput(StrictModel):
    session_id: int = Field(ge=1)


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
    return {
        "row_count": row_count,
        "request_no": str(result.get("requestNo") or result.get("request_no") or "").strip(),
        "message": str(result.get("message") or result.get("successMessage") or result.get("success_message") or "").strip()[:500],
    }


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
    if detected_sheet_type not in {"steel", "hardware"}:
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
def approval_config(data: SessionInput, user=Depends(current_user), db=Depends(get_db)):
    require(db, user, "design_route.read")
    _owned_session(db, user, data.session_id)
    return call_mcp("get_new_mold_approval_launch_config", {"sessionId": data.session_id})


@router.post("/import-statuses")
def import_statuses(data: ImportStatusesInput, user=Depends(current_user), db=Depends(get_db)):
    """Restore completed import receipts for order actions shown in a conversation."""
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
        receipts.append({
            "sessionId": int(event.resource_id),
            "requestNo": str(detail.get("request_no") or ""),
            "message": str(detail.get("message") or ""),
            "importedAt": event.created_at.isoformat(),
        })
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
    common_arguments = {
        "sessionId": data.session_id, "sheetType": data.sheet_type, "moldCode": data.mold_code,
        "previewRows": data.preview_rows, "urgencyLevel": data.urgency_level, "expectedDate": data.expected_date,
        "purchaseReason": data.purchase_reason, "remark": data.remark, "allowDuplicate": data.allow_duplicate,
    }
    if design_order_type == "repair_other":
        allowed_reasons = {
            str(item["value"])
            for item in erp_design_mcp.MODIFY_MOLD_PURCHASE_REASON_OPTIONS
        }
        if data.purchase_reason not in allowed_reasons:
            raise DomainError("ERP_MODIFY_PURCHASE_REASON_INVALID", "请从 ERP 提供的修模改模请购原因选项中选择", 422)
        # Reuse the existing ERP control MCP flow; do not downgrade a
        # repair_other session to the new-model import endpoint.
        result = erp_design_mcp.call_design_control_mcp("import_modify_mold_design", common_arguments)
    else:
        result = call_mcp("import_new_mold_design", common_arguments)
    receipt = _import_receipt_detail(result, len(data.preview_rows))
    receipt["design_order_type"] = design_order_type
    record(db, user, _IMPORT_ACTION, str(data.session_id), receipt)
    db.commit()
    return result
