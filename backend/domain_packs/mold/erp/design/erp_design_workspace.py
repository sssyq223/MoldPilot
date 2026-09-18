"""Authenticated visual workspace for ERP design management.

The D-drive ERP remains the system of record.  This module exposes the
already-registered ERP design MCP capabilities to MoldPilot's native tables;
it does not create a second set of design business rules or local replicas.
"""
from __future__ import annotations

from base64 import b64decode
from binascii import Error as BinasciiError
from decimal import Decimal
from pathlib import Path
import shutil
import tempfile
from typing import Literal
from urllib.parse import quote
from uuid import UUID

from fastapi import APIRouter, Depends, Query, Response
from pydantic import Field, model_validator

from agent_core.errors import DomainError
from agent_core.host_ports import host_ports
from agent_core.schemas import StrictModel
from domain_packs.mold import files
from domain_packs.mold.tools.erp.design import erp_design_mcp


router = APIRouter(prefix="/api/erp-design-workspace", tags=["ERP design workspace"])

_host = host_ports()
current_user = _host.current_user
get_db = _host.get_db
object_storage = _host.object_storage
record = _host.record
require = _host.require
now = _host.now


class QueryInput(StrictModel):
    query: dict = Field(default_factory=dict, max_length=30)


class DrawingCompareInput(StrictModel):
    from_version_id: int = Field(ge=1)
    to_version_id: int = Field(ge=1)


class OrderActionInput(StrictModel):
    operation: Literal["delete", "approve", "resubmit"]
    approval_version: str | None = Field(default=None, min_length=1, max_length=200)
    confirm: Literal[True]


class DensityInput(StrictModel):
    operation: Literal["create", "update", "delete"]
    id: int | None = Field(default=None, ge=1)
    material_mark: str | None = Field(default=None, min_length=1, max_length=120)
    density: Decimal | None = Field(default=None, gt=0, le=100)
    confirm: Literal[True]

    @model_validator(mode="after")
    def valid_operation(self):
        if self.operation in {"update", "delete"} and self.id is None:
            raise ValueError("修改或删除时必须提供密度记录 ID")
        if self.operation in {"create", "update"} and (self.material_mark is None or self.density is None):
            raise ValueError("新增或修改时必须填写材质和密度")
        return self


class StandardHardwareRenameInput(StrictModel):
    relative_path: str = Field(min_length=1, max_length=500)
    new_file_name: str = Field(min_length=1, max_length=240, pattern=r"^[^/\\:\x00-\x1f]+$")
    confirm: Literal[True]


class StandardHardwareDeleteInput(StrictModel):
    relative_path: str = Field(min_length=1, max_length=500)
    confirm: Literal[True]


class StandardHardwareUploadInput(StrictModel):
    file_ids: list[UUID] = Field(min_length=2, max_length=2)
    folder_name: str = Field(min_length=1, max_length=120, pattern=r"^[^/\\:\x00-\x1f]+$")
    confirm: Literal[True]

    @model_validator(mode="after")
    def unique_files(self):
        if len(set(self.file_ids)) != len(self.file_ids):
            raise ValueError("标准件上传文件不能重复")
        return self


def _result(value):
    return {
        "data": value,
        "source": "management-system ERP via erp-design-upload MCP",
        "as_of": now().isoformat(),
        "limitations": ["ERP 是唯一业务数据源；本页面不保存设计订单、图纸版本或基础资料副本。"],
    }


def _download(arguments: dict, *, inline: bool) -> Response:
    value = erp_design_mcp.call_design_control_mcp("download_erp_design_file", arguments)
    if not isinstance(value, dict) or not isinstance(value.get("base64"), str):
        raise DomainError("ERP_DESIGN_FILE_INVALID", "ERP 未返回有效文件", 502)
    try:
        content = b64decode(value["base64"], validate=True)
    except (ValueError, BinasciiError):
        raise DomainError("ERP_DESIGN_FILE_INVALID", "ERP 返回的文件内容无效", 502) from None
    if not content or len(content) > 100 * 1024 * 1024:
        raise DomainError("ERP_DESIGN_FILE_INVALID", "ERP 文件为空或超过 100 MB 限制", 413)
    filename = Path(str(value.get("fileName") or "erp-design-file")).name.replace('"', "_")
    media_type = str(value.get("mediaType") or "application/octet-stream").split(";", 1)[0].strip()
    return Response(content=content, media_type=media_type, headers={
        "Cache-Control": "private, no-store",
        "X-Content-Type-Options": "nosniff",
        "Content-Disposition": ("inline" if inline else "attachment") + "; filename*=UTF-8''" + quote(filename, safe=""),
    })


@router.post("/orders/query")
def query_orders(data: QueryInput, user=Depends(current_user), db=Depends(get_db)):
    require(db, user, "design_route.read")
    return _result(erp_design_mcp.call_mcp("query_erp_design_orders", {"query": data.query}))


@router.get("/orders/{request_id}")
def order_detail(request_id: int, user=Depends(current_user), db=Depends(get_db)):
    require(db, user, "design_route.read")
    return _result(erp_design_mcp.call_mcp("get_erp_design_record", {"resource": "design_order", "id": request_id}))


@router.post("/orders/{request_id}/action")
def order_action(request_id: int, data: OrderActionInput, user=Depends(current_user), db=Depends(get_db)):
    require(db, user, "design_route.execute")
    return erp_design_mcp.execute_tool(db, user, "erp_design_manage_order", {
        "operation": data.operation,
        "request_id": request_id,
        "approval_version": data.approval_version,
        "confirm": data.confirm,
    })


@router.post("/drawing-versions/query")
def query_drawing_versions(data: QueryInput, user=Depends(current_user), db=Depends(get_db)):
    require(db, user, "design_route.read")
    return _result(erp_design_mcp.call_mcp("query_erp_drawing_versions", {"query": data.query}))


@router.get("/drawing-versions/{drawing_id}")
def drawing_detail(drawing_id: int, user=Depends(current_user), db=Depends(get_db)):
    require(db, user, "design_route.read")
    return _result(erp_design_mcp.call_mcp("get_erp_design_record", {"resource": "drawing_version", "id": drawing_id}))


@router.post("/drawing-versions/compare")
def compare_drawing_versions(data: DrawingCompareInput, user=Depends(current_user), db=Depends(get_db)):
    require(db, user, "design_route.read")
    return _result(erp_design_mcp.call_mcp("compare_erp_drawing_versions", {
        "fromVersionId": data.from_version_id,
        "toVersionId": data.to_version_id,
    }))


@router.get("/drawing-versions/{drawing_id}/preview")
def preview_drawing(drawing_id: int, user=Depends(current_user), db=Depends(get_db)):
    require(db, user, "design_route.read")
    return _download({"artifact": "drawing_preview", "drawingId": drawing_id}, inline=True)


@router.get("/drawing-versions/{drawing_id}/download")
def download_drawing(drawing_id: int, user=Depends(current_user), db=Depends(get_db)):
    require(db, user, "design_route.read")
    return _download({"artifact": "drawing_download", "drawingId": drawing_id}, inline=False)


@router.post("/densities/query")
def query_densities(data: QueryInput, user=Depends(current_user), db=Depends(get_db)):
    require(db, user, "design_route.read")
    return _result(erp_design_mcp.call_mcp("query_erp_design_densities", {"query": data.query}))


@router.post("/densities/manage")
def manage_density(data: DensityInput, user=Depends(current_user), db=Depends(get_db)):
    require(db, user, "design_route.execute")
    return erp_design_mcp.execute_tool(db, user, "erp_design_manage_density", {
        "operation": data.operation,
        "id": data.id,
        "material_mark": data.material_mark,
        "density": data.density,
        "confirm": data.confirm,
    })


@router.post("/standard-hardware/query")
def query_standard_hardware(data: QueryInput, user=Depends(current_user), db=Depends(get_db)):
    require(db, user, "design_route.read")
    return _result(erp_design_mcp.call_mcp("query_erp_standard_hardware_drawings", {"query": data.query}))


@router.post("/standard-hardware/upload")
def upload_standard_hardware(data: StandardHardwareUploadInput, user=Depends(current_user), db=Depends(get_db)):
    require(db, user, "design_route.execute")
    directory = Path(tempfile.mkdtemp(prefix="moldpilot-standard-hardware-"))
    paths: list[Path] = []
    names: set[str] = set()
    try:
        for file_id in data.file_ids:
            source = files.uploaded_file(db, user, str(file_id))
            content = object_storage.read(source)
            filename, _ = files.validate_file(source.filename, content)
            folded = filename.casefold()
            if folded in names:
                raise DomainError("ERP_STANDARD_HARDWARE_DUPLICATE", "同一批次不能包含重名标准件文件", 409)
            names.add(folded)
            path = directory / filename
            path.write_bytes(content)
            paths.append(path)
        extensions = {path.suffix.lower() for path in paths}
        stems = {path.stem.casefold() for path in paths}
        if extensions != {".prt", ".dwg"} or len(stems) != 1:
            raise DomainError("ERP_STANDARD_HARDWARE_PACKAGE", "标准件文件夹必须包含同名的 PRT、DWG 各一个", 400)
        stem = next(iter(stems))
        if not stem.startswith("r-bz-") or stem != data.folder_name.casefold():
            raise DomainError("ERP_STANDARD_HARDWARE_PACKAGE", "标准件文件夹和文件必须同名，并以 R-BZ- 开头", 400)
        value = erp_design_mcp.call_design_control_mcp("upload_standard_hardware_drawings", {
            "filePaths": [str(path) for path in paths],
            "folderName": data.folder_name,
        })
    finally:
        shutil.rmtree(directory, ignore_errors=True)
    record(db, user, "erp_design_workspace.standard_hardware_uploaded", data.folder_name, {
        "file_ids": [str(file_id) for file_id in data.file_ids], "file_count": len(data.file_ids),
    })
    db.commit()
    return _result(value)


@router.post("/standard-hardware/rename")
def rename_standard_hardware(data: StandardHardwareRenameInput, user=Depends(current_user), db=Depends(get_db)):
    require(db, user, "design_route.execute")
    return erp_design_mcp.execute_tool(db, user, "erp_design_rename_standard_hardware", {
        "relative_path": data.relative_path,
        "new_file_name": data.new_file_name,
        "confirm": data.confirm,
    })


@router.post("/standard-hardware/delete")
def delete_standard_hardware(data: StandardHardwareDeleteInput, user=Depends(current_user), db=Depends(get_db)):
    require(db, user, "design_route.execute")
    return erp_design_mcp.execute_tool(db, user, "erp_design_delete_standard_hardware", {
        "relative_path": data.relative_path,
        "confirm": data.confirm,
    })


@router.get("/standard-hardware/preview")
def preview_standard_hardware(relative_path: str = Query(min_length=1, max_length=500), user=Depends(current_user), db=Depends(get_db)):
    require(db, user, "design_route.read")
    return _download({"artifact": "standard_hardware_preview", "relativePath": relative_path}, inline=True)


@router.get("/standard-hardware/download")
def download_standard_hardware(relative_path: str = Query(min_length=1, max_length=500), user=Depends(current_user), db=Depends(get_db)):
    require(db, user, "design_route.read")
    return _download({"artifact": "standard_hardware_folder", "relativePath": relative_path}, inline=False)
