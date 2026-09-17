"""Authenticated HTTP bridge for the local ERP design-upload MCP.

The MCP remains a local stdio package.  This router gives MoldPilot's own
web application a narrow, audited way to use it without exposing ERP
credentials to the browser or wiring it into the Agent tool gateway.
"""
from __future__ import annotations

import json
from pathlib import Path
from queue import Empty, Queue
import shutil
import subprocess
import tempfile
from threading import Thread
from typing import Literal
from uuid import UUID

from fastapi import APIRouter, Depends
from pydantic import Field
from sqlalchemy import select

from app import authorization as auth, files, models as m, object_storage
from app.db import get_db
from app.errors import DomainError
from app.events import record
from app.schemas import StrictModel
from app.security import current_user


router = APIRouter(prefix="/api/erp-design-uploads", tags=["ERP new-mold design uploads"])

_PROJECT_ROOT = Path(__file__).resolve().parents[3]
_RUNTIME_ROOT = _PROJECT_ROOT / "mcp" / "erp-design-upload"
_ENV_FILE = _RUNTIME_ROOT / ".env"
_SERVER_FILE = _RUNTIME_ROOT / "node_modules" / "erp-design-upload-mcp" / "scripts" / "erp-design-upload-mcp.mjs"
_PARSE_ACTION = "erp_design_upload.parsed"
_SHEET_TYPES = Literal["steel", "hardware"]


class ParseInput(StrictModel):
    file_id: UUID
    sheet_type: _SHEET_TYPES
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
    urgency_level: Literal["normal", "important", "urgent"] = "normal"
    expected_date: str = Field(min_length=10, max_length=10, pattern=r"^\d{4}-\d{2}-\d{2}$")
    purchase_reason: str | None = Field(default=None, max_length=200)
    remark: str | None = Field(default=None, max_length=1000)
    allow_duplicate: bool = False


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
        m.AuditEvent.action == _PARSE_ACTION,
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


@router.post("/parse")
def parse_design(data: ParseInput, user=Depends(current_user), db=Depends(get_db)):
    auth.require(db, user, "design_route.create")
    source = files.uploaded_file(db, user, str(data.file_id))
    if Path(source.filename).suffix.lower() != ".xlsx":
        raise DomainError("ERP_DESIGN_FILE_TYPE", "新模设计上传仅支持 XLSX 文件")
    file_data = object_storage.read(source)
    filename, _ = files.validate_file(source.filename, file_data)
    directory, path = _temp_design_file(filename, file_data)
    try:
        result = call_mcp("parse_new_mold_design_file", {
            "filePath": str(path), "sheetType": data.sheet_type, "designOrderSubType": data.design_order_sub_type,
        })
    finally:
        shutil.rmtree(directory, ignore_errors=True)
    session_id = result.get("sessionId") if isinstance(result, dict) else None
    if not isinstance(session_id, int) or session_id < 1:
        _mcp_failure("ERP 未返回有效上传会话编号")
    record(db, user, _PARSE_ACTION, str(session_id), {
        "file_id": str(source.id), "filename": filename, "sheet_type": data.sheet_type,
    })
    db.commit()
    return result


@router.post("/status")
def upload_status(data: StatusInput, user=Depends(current_user), db=Depends(get_db)):
    auth.require(db, user, "design_route.read")
    _owned_session(db, user, data.session_id)
    return call_mcp("get_new_mold_upload_status", {"sessionId": data.session_id, "includeResult": data.include_result})


@router.post("/result")
def upload_result(data: SessionInput, user=Depends(current_user), db=Depends(get_db)):
    auth.require(db, user, "design_route.read")
    _owned_session(db, user, data.session_id)
    return call_mcp("get_new_mold_upload_result", {"sessionId": data.session_id})


@router.post("/validate")
def validate_rows(data: RowsInput, user=Depends(current_user), db=Depends(get_db)):
    auth.require(db, user, "design_route.create")
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
    auth.require(db, user, "design_route.create")
    _owned_session(db, user, data.session_id)
    return call_mcp("reprice_new_mold_design_rows", {
        "sheetType": data.sheet_type, "moldCode": data.mold_code, "previewRows": data.preview_rows,
    })


@router.post("/approval-config")
def approval_config(data: SessionInput, user=Depends(current_user), db=Depends(get_db)):
    auth.require(db, user, "design_route.read")
    _owned_session(db, user, data.session_id)
    return call_mcp("get_new_mold_approval_launch_config", {"sessionId": data.session_id})


@router.post("/import")
def import_design(data: ImportInput, user=Depends(current_user), db=Depends(get_db)):
    auth.require(db, user, "design_route.execute")
    _owned_session(db, user, data.session_id)
    validation = call_mcp("validate_new_mold_design_rows", {
        "sessionId": data.session_id, "sheetType": data.sheet_type, "moldCode": data.mold_code,
        "previewRows": data.preview_rows, "pricingAlreadyEnriched": True,
    })
    if not _validation_allows_import(validation):
        raise DomainError("ERP_VALIDATION_FAILED", "ERP 校验未通过，不能导入。请处理明细错误后重新校验", 409)
    result = call_mcp("import_new_mold_design", {
        "sessionId": data.session_id, "sheetType": data.sheet_type, "moldCode": data.mold_code,
        "previewRows": data.preview_rows, "urgencyLevel": data.urgency_level, "expectedDate": data.expected_date,
        "purchaseReason": data.purchase_reason, "remark": data.remark, "allowDuplicate": data.allow_duplicate,
    })
    record(db, user, "erp_design_upload.imported", str(data.session_id), {"row_count": len(data.preview_rows)})
    db.commit()
    return result
