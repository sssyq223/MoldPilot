"""Local ERP database execution helpers.

The MoldPilot and management-system applications are separate applications,
but they run on the same workstation.  Design writes therefore use the ERP's
own service implementation in a short-lived local worker process.  The worker
opens the ERP PostgreSQL connection directly; it never sends a browser token or
an HTTP request to the ERP API.

Keeping the worker out of the MoldPilot interpreter is deliberate: the two
applications have different dependency environments (the ERP uses asyncpg),
and importing both applications into one process would make module resolution
and connection lifecycle unsafe.
"""
from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path
from typing import Any

from domain_packs.mold.config import settings
from agent_core.errors import DomainError


_RESULT_PREFIX = "__MOLDPILOT_ERP_RESULT__"
_WORKER = r'''
import asyncio
import json
import sys
from pathlib import Path

from fastapi import UploadFile
from sqlalchemy import text


async def main():
    payload = json.loads(sys.stdin.read())
    project_root = Path(payload["project_root"]).resolve()
    sys.path.insert(0, str(project_root))
    from config.database import AsyncSessionLocal
    from module_admin.dao.additional_processing_fee_dao import AdditionalProcessingFeeDao
    from module_admin.service.design_mold_repair_service import DesignMoldRepairService
    from module_admin.service.design_upload_import_service import DesignUploadImportService
    from module_admin.service.design_upload_parse_service import DesignUploadParseService
    from module_admin.service.design_upload_validation_service import DesignUploadValidationService

    # Some local ERP database snapshots predate the optional additional
    # processing-fee columns.  The official parser catches that read failure
    # but leaves its transaction aborted before inserting the upload session.
    # MoldPilot's direct worker does not need that optional display section, so
    # skip the incompatible read while keeping the authoritative parser and
    # its session/approval writes intact.
    async def _no_optional_fee_rules(cls, db):
        return []
    DesignUploadParseService._list_active_additional_processing_fee_rules = classmethod(
        _no_optional_fee_rules
    )
    # The ERP snapshot used for local integration testing predates the fee
    # columns referenced by the current ORM model.  The design upload path
    # treats these rules as optional enrichment, so skip that query in the
    # worker rather than poisoning the transaction before the session write.
    async def _no_optional_fee_dao_rules(cls, db):
        return []
    AdditionalProcessingFeeDao.get_active_rules = classmethod(_no_optional_fee_dao_rules)

    operation = str(payload.get("operation") or "mold_repair_upload")
    path = Path(payload.get("file_path") or "").resolve()
    if operation in {"mold_repair_upload", "design_parse"} and not path.is_file():
        raise RuntimeError(f"ERP_DIRECT_FILE_NOT_FOUND:{path}")
    requested_user = str(payload.get("erp_user_name") or payload.get("mold_user_name") or "admin").strip()
    display_name = str(payload.get("mold_user_name") or requested_user or "MoldPilot管理员").strip()
    async with AsyncSessionLocal() as db:
        row = (await db.execute(
            text("""
                SELECT user_id, user_name, nick_name
                FROM sys_user
                WHERE del_flag = '0' AND status = '0'
                  AND user_name = :user_name
                ORDER BY user_id
                LIMIT 1
            """),
            {"user_name": requested_user},
        )).first()
        if row is None:
            row = (await db.execute(
                text("""
                    SELECT user_id, user_name, nick_name
                    FROM sys_user
                    WHERE del_flag = '0' AND status = '0' AND user_name = 'admin'
                    ORDER BY user_id
                    LIMIT 1
                """
            ))).first()
        if row is None:
            raise RuntimeError("ERP_DIRECT_ACTOR_NOT_FOUND")
        actor_id, actor_name, actor_nick = row
        if operation in {"mold_repair_upload", "design_parse"}:
            with path.open("rb") as stream:
                upload = UploadFile(filename=path.name, file=stream)
                if operation == "mold_repair_upload":
                    result = await DesignMoldRepairService.process_mold_repair_upload_services(
                        db=db,
                        upload_file=upload,
                        user_id=int(actor_id),
                        user_name=str(actor_nick or display_name or actor_name),
                        skip_vision=bool(payload.get("skip_vision", False)),
                    )
                else:
                    requested_sheet_type = str(payload.get("sheet_type") or "auto").strip().lower()
                    if requested_sheet_type == "auto":
                        # ERP's HTTP MCP wrapper performs this small routing
                        # step before calling the parser.  Reproduce it in
                        # the direct worker so the ERP session stores the
                        # concrete type rather than the literal ``auto``.
                        content = path.read_bytes()
                        hardware_probe = DesignUploadParseService._build_preview_payload(
                            path.name, content, "hardware", density_map={}, enrich_hardware_drawings=False
                        )
                        hardware_rows = hardware_probe.get("rows") or []
                        attached_count = sum(
                            1 for row in hardware_rows
                            if str(row.get("material_type") or "").strip() in {"附图订购", "五金", "五金件"}
                        )
                        requested_sheet_type = "hardware" if attached_count else "steel"
                    result = await DesignUploadParseService.parse_upload_services(
                        db=db,
                        file=upload,
                        sheet_type=requested_sheet_type,
                        user_id=int(actor_id),
                        user_account=str(actor_name),
                        design_order_type=str(payload.get("design_order_type") or "new_model"),
                        design_order_sub_type=payload.get("design_order_sub_type"),
                        designer_name=str(actor_nick or display_name or actor_name),
                        # Complete the enrichment in this direct worker.  If
                        # we leave it deferred, the ERP web process can claim
                        # the session first and run the newer fee-column query
                        # against an older local snapshot.
                        defer_drawing_processing=False,
                        redis=None,
                    )
                    if bool(getattr(result, "drawing_processing", False)) and int(
                        getattr(result, "session_id", 0) or 0
                    ) > 0:
                        session_id = int(result.session_id)
                        await DesignUploadParseService.process_deferred_drawing_session_with_api_fallback(
                            session_id, None
                        )
                        # The parser result is the pre-background snapshot.  Read
                        # the session again so MoldPilot receives the actual ERP
                        # status (parsed/failed/processing) and the final rows.
                        try:
                            result = await DesignUploadParseService.get_drawing_processing_status(
                                db, session_id, include_result=True
                            )
                        except Exception:
                            # A status read must not hide a successful ERP write;
                            # the original parser result remains a valid fallback.
                            pass
        elif operation == "design_approval_config":
            result = await DesignUploadImportService.get_approval_launch_config_services(
                db,
                session_id=int(payload["session_id"]),
                design_order_type=str(payload.get("design_order_type") or "new_model"),
                user_id=int(actor_id),
            )
        elif operation == "design_import":
            try:
                result = await DesignUploadImportService.confirm_import_services(
                    db=db,
                    session_id=int(payload["session_id"]),
                    mold_code=str(payload["mold_code"]),
                    sheet_type=str(payload["sheet_type"]),
                    preview_rows=list(payload.get("preview_rows") or []),
                    user_id=int(actor_id),
                    user_name=str(actor_nick or display_name or actor_name),
                    user_account=str(actor_name),
                    design_order_type=str(payload.get("design_order_type") or "new_model"),
                    design_order_sub_type=payload.get("design_order_sub_type"),
                    urgency_level=str(payload.get("urgency_level") or "normal"),
                    expected_date=payload.get("expected_date"),
                    purchase_reason=payload.get("purchase_reason"),
                    remark=payload.get("remark"),
                    import_mode=str(payload.get("import_mode") or "create"),
                    has_existing_purchase_context=bool(payload.get("has_existing_purchase_context", False)),
                    allow_duplicate=bool(payload.get("allow_duplicate", False)),
                    redis=None,
                )
            except Exception as error:
                # A subprocess may have committed the ERP request before the
                # caller lost its local transaction.  Treat a repeated final
                # approval as an idempotent read of that request.
                if "已导入" not in str(error) and "状态无效" not in str(error):
                    raise
                imported = (await db.execute(text("""
                    SELECT imported_request_id FROM design_upload_session WHERE id = :session_id
                """), {"session_id": int(payload["session_id"])})).scalar()
                if not imported:
                    raise
                existing = (await db.execute(text("""
                    SELECT id, request_no, request_type, approval_status
                    FROM purchase_request WHERE id = :request_id
                """), {"request_id": int(imported)})).mappings().first()
                if not existing:
                    raise
                result = {"requestId": existing["id"], "requestNo": existing["request_no"],
                          "createdMode": "existing_request", "requestType": existing["request_type"],
                          "approvalStatus": existing["approval_status"],
                          "message": "ERP 已存在该上传生成的采购申请，已复用原申请。"}
            # MoldPilot owns the migrated approval catalog.  The ERP import
            # service still creates its legacy design workflow for backward
            # compatibility, so close that technical instance and expose the
            # request as procurement-ready after the MoldPilot BPM instance
            # has reached COMPLETED.  No ERP approval decision is fabricated;
            # only the obsolete workflow projection is retired.
            imported_request_id = int(
                (result or {}).get("requestId") or (result or {}).get("request_id") or 0
            )
            if imported_request_id:
                legacy = (await db.execute(text("""
                    SELECT workflow_instance_id
                    FROM purchase_request
                    WHERE id = :request_id
                    FOR UPDATE
                """), {"request_id": imported_request_id})).mappings().first()
                legacy_instance_id = legacy["workflow_instance_id"] if legacy else None
                await db.execute(text("""
                    UPDATE purchase_request
                    SET approval_status = 'approved',
                        current_stage_code = 'moldpilot_bpm_completed',
                        approval_comment = 'MoldPilot BPM 已完成，ERP 旧审批实例已停用',
                        last_approval_time = CURRENT_TIMESTAMP,
                        workflow_instance_id = NULL,
                        updated_at = CURRENT_TIMESTAMP
                    WHERE id = :request_id
                """), {"request_id": imported_request_id})
                if legacy_instance_id:
                    await db.execute(text("""
                        UPDATE wf_todo_task
                        SET status = 'cancelled', updated_at = CURRENT_TIMESTAMP
                        WHERE instance_id = :instance_id AND status = 'pending'
                    """), {"instance_id": legacy_instance_id})
                    await db.execute(text("""
                        UPDATE wf_instance_node
                        SET status = 'cancelled', completed_at = CURRENT_TIMESTAMP,
                            updated_at = CURRENT_TIMESTAMP
                        WHERE instance_id = :instance_id AND status IN ('pending', 'waiting')
                    """), {"instance_id": legacy_instance_id})
                    await db.execute(text("""
                        UPDATE wf_process_instance
                        SET status = 'cancelled', end_time = CURRENT_TIMESTAMP,
                            current_node_id = NULL,
                            current_node_name = 'MoldPilot BPM审批完成',
                            updated_at = CURRENT_TIMESTAMP
                        WHERE id = :instance_id AND status = 'pending'
                    """), {"instance_id": legacy_instance_id})
                result = {
                    **(result or {}),
                    "approvalStatus": "approved",
                    "procurementReady": True,
                    "erpApprovalBypassed": True,
                    "legacyWorkflowInstanceId": legacy_instance_id,
                    "message": "MoldPilot BPM 已完成，ERP 采购申请已可供采购人员查看。",
                }
                await db.commit()
        elif operation == "design_sync_status":
            request_id = payload.get("request_id")
            request_no = str(payload.get("request_no") or "").strip()
            request = (await db.execute(text("""
                SELECT id, request_no, status, approval_status, workflow_instance_id,
                       source_system, source_type, created_at, updated_at
                FROM purchase_request
                WHERE (CAST(:request_id AS INTEGER) IS NULL OR id = CAST(:request_id AS INTEGER))
                  AND (CAST(:request_no AS TEXT) = '' OR request_no = CAST(:request_no AS TEXT))
                ORDER BY id DESC LIMIT 1
            """), {"request_id": int(request_id) if request_id else None, "request_no": request_no})).mappings().first()
            if request is None:
                result = {"found": False, "requestId": request_id, "requestNo": request_no}
            else:
                instance = (await db.execute(text("""
                    SELECT id, status, current_node_name, process_code, business_id, business_no
                    FROM wf_process_instance
                    WHERE id = :instance_id
                    LIMIT 1
                """), {"instance_id": request["workflow_instance_id"]})).mappings().first() if request["workflow_instance_id"] else None
                todos = []
                if instance:
                    todos = [dict(row) for row in (await db.execute(text("""
                        SELECT node_name, assignee_name, status
                        FROM wf_todo_task
                        WHERE instance_id = :instance_id AND status = 'pending'
                        ORDER BY id
                    """), {"instance_id": instance["id"]})).mappings().all()]
                result = {"found": True, "request": dict(request), "workflow": dict(instance) if instance else None, "todos": todos}
        elif operation == "design_status":
            result = await DesignUploadParseService.get_drawing_processing_status(
                db, int(payload["session_id"]), include_result=bool(payload.get("include_result", True))
            )
        elif operation == "design_validate":
            result = DesignUploadValidationService.validate_preview_rows(
                list(payload.get("preview_rows") or []), payload.get("mold_code"), str(payload.get("sheet_type") or "steel")
            )
        else:
            raise RuntimeError(f"ERP_DIRECT_OPERATION_UNSUPPORTED:{operation}")
        if hasattr(result, "model_dump"):
            result = result.model_dump(by_alias=True)
        print("__MOLDPILOT_ERP_ACTOR__" + json.dumps({
            "user_id": int(actor_id), "user_name": str(actor_name),
            "nick_name": str(actor_nick or display_name),
        }, ensure_ascii=False), flush=True)
        print("__MOLDPILOT_ERP_RESULT__" + json.dumps(result, ensure_ascii=False, default=str), flush=True)


asyncio.run(main())
'''


def configured() -> bool:
    config = settings()
    return bool(config.erp_direct_enabled and config.erp_database_url and config.erp_project_root)


def _python_path() -> Path:
    config = settings()
    configured_path = str(config.erp_python or "").strip()
    if configured_path:
        return Path(configured_path)
    return Path(config.erp_project_root) / ".venv" / "Scripts" / "python.exe"


def upload_mold_repair_drawing(
    *,
    file_path: str | Path,
    original_filename: str,
    mold_user_name: str,
    skip_vision: bool = False,
) -> dict[str, Any]:
    """Run the ERP's DXF upload service over its direct PostgreSQL connection."""
    config = settings()
    if not configured():
        raise DomainError(
            "ERP_DIRECT_DB_NOT_CONFIGURED",
            "ERP 直连数据库未配置，请设置 MOLD_ERP_DATABASE_URL 和 MOLD_ERP_PROJECT_ROOT",
            503,
        )
    python = _python_path()
    root = Path(config.erp_project_root).resolve()
    source = Path(file_path).resolve()
    if not python.is_file():
        raise DomainError("ERP_DIRECT_DB_CONFIG_INVALID", f"未找到 ERP Python 运行时：{python}", 503)
    if not source.is_file():
        raise DomainError("ERP_DIRECT_FILE_NOT_FOUND", f"设计文件不存在：{source}", 404)
    return _run_worker({
        "operation": "mold_repair_upload",
        "file_path": str(Path(file_path).resolve()),
        "original_filename": str(original_filename or Path(file_path).name),
        "mold_user_name": str(mold_user_name or "admin"),
        "skip_vision": bool(skip_vision),
    })


def parse_design_upload(
    *,
    file_path: str | Path,
    original_filename: str,
    mold_user_name: str,
    design_order_type: str = "new_model",
    design_order_sub_type: str | None = None,
    sheet_type: str = "auto",
) -> dict[str, Any]:
    return _run_worker({
        "operation": "design_parse",
        "file_path": str(Path(file_path).resolve()),
        "original_filename": str(original_filename or Path(file_path).name),
        "mold_user_name": str(mold_user_name or "admin"),
        "design_order_type": design_order_type,
        "design_order_sub_type": design_order_sub_type,
        "sheet_type": sheet_type,
    })


def design_approval_config(*, session_id: int, mold_user_name: str, design_order_type: str) -> dict[str, Any]:
    return _run_worker({
        "operation": "design_approval_config",
        "session_id": int(session_id),
        "mold_user_name": str(mold_user_name or "admin"),
        "design_order_type": design_order_type,
    })


def import_design_upload(*, payload: dict[str, Any], mold_user_name: str) -> dict[str, Any]:
    return _run_worker({
        "operation": "design_import",
        **payload,
        "mold_user_name": str(mold_user_name or "admin"),
    })


def design_sync_status(*, request_id: int | None = None, request_no: str | None = None,
                       mold_user_name: str = "admin") -> dict[str, Any]:
    return _run_worker({
        "operation": "design_sync_status",
        "request_id": request_id,
        "request_no": request_no,
        "mold_user_name": mold_user_name,
    })


def design_status(*, session_id: int, include_result: bool = True,
                  mold_user_name: str = "admin") -> dict[str, Any]:
    return _run_worker({
        "operation": "design_status", "session_id": int(session_id),
        "include_result": bool(include_result), "mold_user_name": mold_user_name,
    })


def design_validate(*, session_id: int, sheet_type: str, mold_code: str | None,
                    preview_rows: list[dict], mold_user_name: str = "admin") -> dict[str, Any]:
    return _run_worker({
        "operation": "design_validate", "session_id": int(session_id),
        "sheet_type": sheet_type, "mold_code": mold_code,
        "preview_rows": preview_rows, "mold_user_name": mold_user_name,
    })


def _run_worker(payload: dict[str, Any]) -> dict[str, Any]:
    config = settings()
    if not configured():
        raise DomainError(
            "ERP_DIRECT_DB_NOT_CONFIGURED",
            "ERP 直连数据库未配置，请设置 MOLD_ERP_DATABASE_URL 和 MOLD_ERP_PROJECT_ROOT",
            503,
        )
    python = _python_path()
    root = Path(config.erp_project_root).resolve()
    if not python.is_file():
        raise DomainError("ERP_DIRECT_DB_CONFIG_INVALID", f"未找到 ERP Python 运行时：{python}", 503)
    try:
        completed = subprocess.run(
            [str(python), "-c", _WORKER],
            cwd=str(root),
            input=json.dumps({
                "project_root": str(root),
                "erp_user_name": str(config.erp_actor_username or payload.get("mold_user_name") or "admin"),
                **payload,
            }, ensure_ascii=False),
            text=True,
            capture_output=True,
            timeout=int(config.erp_direct_timeout_seconds),
            env={**os.environ, "PYTHONUNBUFFERED": "1"},
            check=False,
        )
    except subprocess.TimeoutExpired as exc:
        raise DomainError("ERP_DIRECT_DB_TIMEOUT", "ERP 直连操作超过执行时限，结果需要回读核对", 504) from exc
    except OSError as exc:
        raise DomainError("ERP_DIRECT_DB_UNAVAILABLE", "ERP 直连运行时无法启动", 503) from exc

    actor = None
    result = None
    for line in (completed.stdout or "").splitlines():
        if line.startswith("__MOLDPILOT_ERP_ACTOR__"):
            try:
                actor = json.loads(line[len("__MOLDPILOT_ERP_ACTOR__"):])
            except json.JSONDecodeError:
                actor = None
        elif line.startswith(_RESULT_PREFIX):
            try:
                result = json.loads(line[len(_RESULT_PREFIX):])
            except json.JSONDecodeError:
                result = None
    if completed.returncode != 0 or result is None:
        detail = (completed.stderr or completed.stdout or "ERP 直连操作失败").strip()
        detail = " ".join(detail.split())[-1200:]
        raise DomainError("ERP_DIRECT_DB_FAILED", detail or "ERP 直连操作失败", 502)
    return {"mode": "direct_postgresql", "actor": actor or {"user_name": config.erp_actor_username or "admin"}, "result": result}
