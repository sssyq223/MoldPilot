from __future__ import annotations

import json
from pathlib import Path
import shutil
import subprocess
import sys
from urllib.parse import urlsplit

from pydantic import Field
from sqlalchemy import func, select, text

from .config import model_settings, settings
from .db import now
from .models import AuditEvent, Outbox, Run, Step
from .schemas import StrictModel


REPO_ROOT = Path(__file__).resolve().parents[2]


class OperationsReadinessInput(StrictModel):
    include_runtime_counters: bool = Field(
        default=True,
        description="是否返回只读运行计数。不会读取业务明细或密钥。",
    )


def _configured(value: str | None) -> bool:
    return bool((value or "").strip())


def _safe_url(value: str | None) -> dict:
    if not _configured(value):
        return {"configured": False}
    try:
        parsed = urlsplit(value or "")
    except ValueError:
        return {"configured": True, "parseable": False}
    try:
        port = parsed.port
        parseable = True
    except ValueError:
        port = None
        parseable = False
    return {
        "configured": True,
        "parseable": parseable,
        "scheme": parsed.scheme or None,
        "host": parsed.hostname or None,
        "port": port,
        "database_name": parsed.path.lstrip("/") or None,
        "path_present": bool(parsed.path and parsed.path != "/"),
        "username_present": bool(parsed.username),
        "password_present": bool(parsed.password),
        "query_present": bool(parsed.query),
    }


def _database_baseline(value: str | None, expected_database: str = "moldpilot") -> dict:
    if not _configured(value):
        return {
            "expected_engine": "postgresql",
            "expected_database": expected_database,
            "status": "NOT_CONFIGURED",
            "delivery_ready": False,
        }
    try:
        parsed = urlsplit(value or "")
        _ = parsed.port
    except ValueError:
        return {
            "expected_engine": "postgresql",
            "expected_database": expected_database,
            "status": "INVALID_DATABASE_URL",
            "delivery_ready": False,
        }
    scheme = parsed.scheme or ""
    database_name = parsed.path.lstrip("/") or None
    is_postgresql = scheme.startswith("postgresql")
    is_sqlite = scheme.startswith("sqlite")
    if is_sqlite:
        status = "SQLITE_NOT_ALLOWED_FOR_DELIVERY"
    elif not is_postgresql:
        status = "NON_POSTGRESQL_DATABASE"
    elif database_name != expected_database:
        status = "POSTGRESQL_WRONG_DATABASE"
    else:
        status = "POSTGRESQL_MOLDPILOT_READY_CONFIG"
    return {
        "expected_engine": "postgresql",
        "expected_database": expected_database,
        "configured_engine": scheme or None,
        "configured_database": database_name,
        "is_postgresql": is_postgresql,
        "is_sqlite": is_sqlite,
        "matches_expected_database": database_name == expected_database,
        "status": status,
        "delivery_ready": status == "POSTGRESQL_MOLDPILOT_READY_CONFIG",
        "navicat_note": "Navicat 连接应指向同一个 PostgreSQL 实例和 moldpilot 数据库；本结果不会返回密码。",
    }


def _path_summary(value: str | None) -> dict:
    if not _configured(value):
        return {"configured": False}
    path = Path(value or "")
    return {
        "configured": True,
        "is_absolute": path.is_absolute(),
        "name": path.name,
        "exists": path.exists(),
    }


def _command_probe(command: list[str], timeout: float = 2.0) -> dict:
    executable = shutil.which(command[0])
    result = {
        "name": command[0],
        "available": bool(executable),
        "path_present": bool(executable),
        "probe": command[1:],
    }
    if not executable:
        return result
    try:
        completed = subprocess.run(
            [executable, *command[1:]],
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
    except Exception as error:  # pragma: no cover - depends on host tools
        result.update({"probe_ok": False, "error_type": type(error).__name__})
        return result
    stderr = (completed.stderr or "").strip()
    stdout = (completed.stdout or "").strip()
    combined = f"{stdout}\n{stderr}".lower()
    docker_probe = command[0] == "docker"
    docker_connect_error = docker_probe and (
        "error during connect" in combined
        or "internal server error" in combined
        or "servererrors" in combined
    )
    docker_info_empty = docker_probe and len(command) > 1 and command[1] == "info" and not stdout.strip()
    output = (stdout or stderr or "").splitlines()
    result.update(
        {
            "probe_ok": completed.returncode == 0 and not docker_connect_error and not docker_info_empty,
            "returncode": completed.returncode,
            "summary": output[0][:160] if output else "",
        }
    )
    return result


def _deployment_runtime_status() -> dict:
    docker_cli = _command_probe(["docker", "--version"])
    docker_daemon = _command_probe(["docker", "info", "--format", "{{.ServerVersion}}"], timeout=3.0)
    docker_compose = _command_probe(["docker", "compose", "version"], timeout=3.0)
    node = _command_probe(["node", "--version"])
    npm = _command_probe(["npm", "--version"])
    web_root = REPO_ROOT / "web"
    frontend_build = {
        "package_json_exists": (web_root / "package.json").exists(),
        "node_modules_exists": (web_root / "node_modules").exists(),
        "dist_index_exists": (web_root / "dist" / "index.html").exists(),
    }
    backend_entrypoints = {
        "api_module_exists": (REPO_ROOT / "backend" / "app" / "api.py").exists(),
        "agent_worker_module_exists": (REPO_ROOT / "backend" / "app" / "agent_worker.py").exists(),
        "message_worker_module_exists": (REPO_ROOT / "backend" / "app" / "message_worker.py").exists(),
    }
    docker_ready = bool(docker_cli.get("probe_ok") and docker_daemon.get("probe_ok") and docker_compose.get("probe_ok"))
    node_ready = bool(node.get("probe_ok") and npm.get("probe_ok"))
    frontend_ready = bool(frontend_build["package_json_exists"] and frontend_build["dist_index_exists"])
    backend_ready = all(backend_entrypoints.values())
    return {
        "python": {
            "version": sys.version.split()[0],
            "executable_present": bool(sys.executable),
        },
        "node": node,
        "npm": npm,
        "docker_cli": docker_cli,
        "docker_daemon": docker_daemon,
        "docker_compose": docker_compose,
        "frontend_build": frontend_build,
        "backend_entrypoints": backend_entrypoints,
        "docker_ready": docker_ready,
        "node_ready": node_ready,
        "frontend_build_ready": frontend_ready,
        "backend_entrypoints_ready": backend_ready,
        "status": "DEPLOYMENT_RUNTIME_READY" if docker_ready and node_ready and frontend_ready and backend_ready else "DEPLOYMENT_RUNTIME_INCOMPLETE",
        "note": "只读探测本机部署前提；不启动 Docker、不构建前端、不启动 API 或 Worker。",
    }


def _db_status(db) -> dict:
    status: dict = {}
    try:
        bind = db.get_bind()
        status["dialect"] = bind.dialect.name if bind is not None else None
    except Exception:  # pragma: no cover - defensive status path
        status["dialect"] = None
    try:
        db.execute(text("SELECT 1"))
        status["reachable"] = True
    except Exception as error:  # pragma: no cover - defensive status path
        status["reachable"] = False
        status["error_type"] = type(error).__name__
        return status
    if status.get("dialect") == "postgresql":
        try:
            current_database = db.scalar(text("SELECT current_database()"))
            status["current_database"] = current_database
            status["matches_expected_database"] = current_database == "moldpilot"
        except Exception as error:  # pragma: no cover - defensive status path
            status["current_database_error_type"] = type(error).__name__
    return status


def _migration_repository_status() -> dict:
    try:
        from alembic.script import ScriptDirectory
        from agent_core.migration_runtime import alembic_config, version_table

        config = alembic_config(REPO_ROOT)
        script = ScriptDirectory.from_config(config)
        heads = sorted(script.get_heads())
        return {
            "available": True,
            "script_location": config.get_main_option("script_location"),
            "version_table": version_table(),
            "heads": heads,
            "head_count": len(heads),
        }
    except Exception as error:  # pragma: no cover - defensive status path
        return {
            "available": False,
            "error_type": type(error).__name__,
        }


def _migration_status(db) -> dict:
    repository = _migration_repository_status()
    status: dict = {"repository": repository}
    try:
        version_table = repository.get("version_table", "alembic_version")
        if not version_table.replace("_", "").isalnum():
            raise RuntimeError("Invalid migration version table")
        versions = sorted(str(row[0]) for row in db.execute(
            text(f'SELECT version_num FROM "{version_table}"')
        ).all())
    except Exception as error:  # pragma: no cover - defensive status path
        status.update(
            {
                "database_versions_available": False,
                "error_type": type(error).__name__,
                "matches_repository_heads": False,
            }
        )
        return status
    heads = repository.get("heads") if repository.get("available") else []
    status.update(
        {
            "database_versions_available": True,
            "database_versions": versions,
            "database_version_count": len(versions),
            "matches_repository_heads": bool(heads) and set(versions) == set(heads),
            "status": "MIGRATIONS_MATCH_REPOSITORY_HEADS" if bool(heads) and set(versions) == set(heads) else "MIGRATIONS_OUT_OF_SYNC",
            "note": f"只读比较数据库 {version_table} 与活动业务包的 Alembic head；不会执行迁移。",
        }
    )
    return status


def _common_postgres_tool_paths(name: str) -> list[Path]:
    executable = name if name.endswith(".exe") else f"{name}.exe"
    roots = [
        Path("C:/Program Files/PostgreSQL"),
        Path("C:/Program Files (x86)/PostgreSQL"),
        Path("D:/PostgreSQL"),
    ]
    paths: list[Path] = []
    for root in roots:
        try:
            paths.extend(sorted(root.glob(f"*/bin/{executable}"), reverse=True))
        except OSError:
            continue
    return paths


def _tool_path(name: str, explicit_path: str = "") -> dict:
    source = "PATH"
    path = explicit_path.strip() if explicit_path else ""
    if path:
        source = "explicit"
        found = Path(path).exists()
    else:
        path = shutil.which(name) or ""
        found = bool(path)
        if not found:
            for candidate in _common_postgres_tool_paths(name):
                if candidate.exists():
                    path = str(candidate)
                    found = True
                    source = "common_install_dir"
                    break
    return {
        "name": name,
        "available": bool(found),
        "path_present": bool(path),
        "source": source if path else None,
    }


def _backup_restore_status() -> dict:
    backup_script = REPO_ROOT / "scripts" / "backup_postgres.py"
    restore_script = REPO_ROOT / "scripts" / "restore_postgres.py"
    cfg = settings()
    pg_dump = _tool_path("pg_dump", cfg.pg_dump_path)
    pg_restore = _tool_path("pg_restore", cfg.pg_restore_path)
    docker_cli = _command_probe(["docker", "--version"])
    docker_daemon = _command_probe(["docker", "info", "--format", "{{.ServerVersion}}"], timeout=3.0)
    docker_image = (cfg.pg_client_image or "postgres:18-alpine").strip()
    docker_image_present = _command_probe(["docker", "image", "inspect", docker_image], timeout=3.0) if docker_daemon.get("probe_ok") else {"probe_ok": False}
    docker_client = {
        "image": docker_image,
        "cli_available": bool(docker_cli.get("probe_ok")),
        "daemon_available": bool(docker_daemon.get("probe_ok")),
        "image_present": bool(docker_image_present.get("probe_ok")),
        "can_use_as_pg_client": bool(docker_cli.get("probe_ok") and docker_daemon.get("probe_ok")),
        "note": "Docker client 模式会使用临时 postgres 镜像运行 pg_dump/pg_restore；本机 127.0.0.1 会映射为 host.docker.internal。",
    }
    restore_target = _safe_url(cfg.restore_database_url)
    native_can_backup = backup_script.exists() and pg_dump["available"]
    native_can_restore = restore_script.exists() and pg_restore["available"] and restore_target.get("configured") is True
    docker_can_backup = backup_script.exists() and docker_client["can_use_as_pg_client"]
    docker_can_restore = restore_script.exists() and docker_client["can_use_as_pg_client"] and restore_target.get("configured") is True
    can_backup = native_can_backup or docker_can_backup
    can_restore_rehearse = native_can_restore or docker_can_restore
    return {
        "backup_script": {
            "path": "scripts/backup_postgres.py",
            "exists": backup_script.exists(),
            "mode": "logical_pg_dump_custom_format",
            "default_output_dir": ".local/backups",
            "client_modes": ["native", "docker", "auto"],
        },
        "restore_script": {
            "path": "scripts/restore_postgres.py",
            "exists": restore_script.exists(),
            "mode": "pg_restore_custom_format_to_isolated_database",
            "target": restore_target,
            "client_modes": ["native", "docker", "auto"],
        },
        "pg_dump": pg_dump,
        "pg_restore": pg_restore,
        "docker_pg_client": docker_client,
        "native_can_run_local_backup": native_can_backup,
        "native_can_rehearse_restore": native_can_restore,
        "docker_can_run_local_backup": docker_can_backup,
        "docker_can_rehearse_restore": docker_can_restore,
        "can_run_local_backup": can_backup,
        "can_rehearse_restore": can_restore_rehearse,
        "status": "BACKUP_TOOLING_READY" if can_backup and can_restore_rehearse else "BACKUP_TOOLING_INCOMPLETE",
        "note": "备份/恢复脚本只读取本机 .env，拒绝 SQLite；可使用本机 PostgreSQL 客户端或 Docker 临时 postgres 客户端；恢复默认要求隔离库 MOLD_RESTORE_DATABASE_URL，正式 RTO/RPO 仍需隔离恢复演练证明。",
    }


def _retention_item(days: int) -> dict:
    return {
        "configured": days > 0,
        "days": days if days > 0 else None,
    }


def _log_retention_status(db, cfg) -> dict:
    retention_script = REPO_ROOT / "scripts" / "log_retention.py"
    audit_total = db.scalar(select(func.count()).select_from(AuditEvent)) or 0
    oldest = db.scalar(select(func.min(AuditEvent.created_at)))
    newest = db.scalar(select(func.max(AuditEvent.created_at)))
    policy = {
        "audit_log": _retention_item(cfg.audit_log_retention_days),
        "application_log": _retention_item(cfg.app_log_retention_days),
        "access_log": _retention_item(cfg.access_log_retention_days),
        "model_log": _retention_item(cfg.model_log_retention_days),
    }
    configured_keys = [key for key, value in policy.items() if value["configured"]]
    missing_keys = [key for key, value in policy.items() if not value["configured"]]
    return {
        "policy": policy,
        "configured_keys": configured_keys,
        "missing_keys": missing_keys,
        "policy_fully_configured": not missing_keys,
        "audit_event_table": {
            "row_count": audit_total,
            "oldest_created_at": oldest.isoformat() if oldest else None,
            "newest_created_at": newest.isoformat() if newest else None,
        },
        "retention_script": {
            "path": "scripts/log_retention.py",
            "exists": retention_script.exists(),
            "default_mode": "dry-run",
            "execute_requires": ["--execute", "--i-understand-this-will-prune-logs"],
            "postgresql_only": True,
            "archive_dir": ".local/log-archives",
            "handles": ["audit_event archive/delete", "login_session prune", "ai_step result redaction", "ai_run checkpoint redaction"],
        },
        "status": "LOG_RETENTION_POLICY_CONFIGURED" if not missing_keys else "LOG_RETENTION_POLICY_INCOMPLETE",
        "note": "这里只核对保留期限配置、审计表事实和本地保留脚本存在性；正式验收仍需确认部署层应用/访问日志采集位置、脱敏、归档、检索和删除策略。",
    }


def _redis_status(value: str | None) -> dict:
    if not _configured(value):
        return {
            "status": "NOT_CONFIGURED",
            "probe_performed": False,
            "note": "未配置 Redis URL；消息流和缓存不能作为运行交付就绪项。",
        }
    try:
        from redis import Redis
        from redis.exceptions import ResponseError

        from .message_worker import GROUP, STREAM

        client = Redis.from_url(value, decode_responses=True, socket_connect_timeout=1, socket_timeout=1, protocol=2)
        try:
            client.ping()
            server_info = client.info("server")
            stream_exists = True
            stream_length = None
            groups: list[str] = []
            try:
                stream_info = client.xinfo_stream(STREAM)
                stream_length = stream_info.get("length")
                groups = [item.get("name") for item in client.xinfo_groups(STREAM) if item.get("name")]
            except ResponseError:
                stream_exists = False
            stream_group_ready = GROUP in groups
            if stream_group_ready:
                status = "REDIS_REACHABLE_STREAM_GROUP_READY"
            elif stream_exists:
                status = "REDIS_REACHABLE_STREAM_EXISTS_GROUP_MISSING"
            else:
                status = "REDIS_REACHABLE_STREAM_NOT_INITIALIZED"
            return {
                "status": status,
                "probe_performed": True,
                "reachable": True,
                "server_version": server_info.get("redis_version"),
                "mode": server_info.get("redis_mode"),
                "stream": {
                    "key": STREAM,
                    "exists": stream_exists,
                    "length": stream_length,
                    "expected_group": GROUP,
                    "group_ready": stream_group_ready,
                    "groups": groups,
                },
                "note": "只读执行 PING/INFO/XINFO；不会创建 stream/group，不会发布或消费消息。",
            }
        finally:
            client.close()
    except Exception as error:  # pragma: no cover - depends on local Redis availability
        return {
            "status": "REDIS_UNREACHABLE",
            "probe_performed": True,
            "reachable": False,
            "error_type": type(error).__name__,
            "note": "Redis 连接不可达或探测失败；返回错误类型，不包含 Redis URL、密码或服务端响应正文。",
        }


def _counts(db, include: bool) -> dict:
    if not include:
        return {"included": False}
    run_statuses = dict(db.execute(select(Run.status, func.count()).group_by(Run.status)).all())
    outbox_total = db.scalar(select(func.count()).select_from(Outbox)) or 0
    outbox_pending = db.scalar(select(func.count()).select_from(Outbox).where(Outbox.published_at.is_(None), Outbox.dead_at.is_(None))) or 0
    outbox_dead = db.scalar(select(func.count()).select_from(Outbox).where(Outbox.dead_at.is_not(None))) or 0
    return {
        "included": True,
        "ai_runs_by_status": run_statuses,
        "tool_steps_total": db.scalar(select(func.count()).select_from(Step)) or 0,
        "audit_events_total": db.scalar(select(func.count()).select_from(AuditEvent)) or 0,
        "outbox": {
            "total": outbox_total,
            "pending_or_retrying": outbox_pending,
            "dead_lettered": outbox_dead,
        },
    }


ACCEPTANCE_GATE_KEYS = [
    "deployment_topology",
    "user_scale",
    "response_time",
    "availability",
    "backup_frequency",
    "restore_objective",
    "log_retention",
    "production_storage",
    "model_operations",
]


def _acceptance_evidence_status(cfg) -> dict:
    path = Path(cfg.acceptance_evidence_file)
    if not path.is_absolute():
        path = REPO_ROOT / path
    status = {
        "path": str(path.relative_to(REPO_ROOT)) if path.is_relative_to(REPO_ROOT) else str(path),
        "exists": path.exists(),
        "schema_version": None,
        "environment": None,
        "valid_gate_keys": [],
        "invalid_gate_keys": [],
        "status": "NOT_CONFIGURED",
        "note": "正式验收确认文件不提交到 Git；需由实施/业务负责人填写 confirmed_by、confirmed_at 和 evidence_refs。",
    }
    if not path.exists():
        return status
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        return {**status, "status": "INVALID_JSON", "error_type": type(error).__name__}
    if not isinstance(data, dict):
        return {**status, "status": "INVALID_SHAPE"}
    gates = data.get("gates")
    if not isinstance(gates, dict):
        return {**status, "status": "INVALID_SHAPE", "schema_version": data.get("schema_version"), "environment": data.get("environment")}
    root_confirmed_by = str(data.get("confirmed_by") or "").strip()
    root_confirmed_at = str(data.get("confirmed_at") or "").strip()
    valid: dict[str, dict] = {}
    invalid: list[str] = []
    for key in ACCEPTANCE_GATE_KEYS:
        item = gates.get(key)
        if not isinstance(item, dict):
            continue
        confirmed_by = str(item.get("confirmed_by") or root_confirmed_by).strip()
        confirmed_at = str(item.get("confirmed_at") or root_confirmed_at).strip()
        evidence_refs = item.get("evidence_refs")
        if item.get("confirmed") is True and confirmed_by and confirmed_at and isinstance(evidence_refs, list) and bool(evidence_refs):
            valid[key] = {
                "confirmed_by": confirmed_by,
                "confirmed_at": confirmed_at,
                "evidence_refs": [str(value) for value in evidence_refs],
                "notes": str(item.get("notes") or ""),
            }
        elif item.get("confirmed"):
            invalid.append(key)
    return {
        **status,
        "schema_version": data.get("schema_version"),
        "environment": data.get("environment"),
        "valid_gate_keys": sorted(valid),
        "invalid_gate_keys": sorted(invalid),
        "status": "LOADED",
        "records": valid,
    }


def _apply_acceptance_evidence(gates: list[dict], evidence_status: dict) -> list[dict]:
    records = evidence_status.get("records") if isinstance(evidence_status.get("records"), dict) else {}
    result = []
    for gate in gates:
        record = records.get(gate["key"])
        if record:
            gate = {
                **gate,
                "confirmed": True,
                "confirmation_source": "acceptance_evidence_file",
                "acceptance_record": record,
            }
        else:
            gate = {
                **gate,
                "confirmation_source": "runtime" if gate.get("confirmed") else "missing_acceptance_evidence",
            }
        result.append(gate)
    return result


def _gates(cfg, model_cfg, backup_restore: dict, log_retention: dict, redis_status: dict, deployment_runtime: dict, acceptance_evidence: dict) -> list[dict]:
    s3_ready = cfg.file_backend == "s3" and _configured(cfg.file_s3_bucket) and _configured(cfg.file_s3_endpoint)
    backup_tooling_ready = backup_restore.get("status") == "BACKUP_TOOLING_READY"
    log_policy_ready = log_retention.get("policy_fully_configured") is True
    redis_ready = redis_status.get("status") == "REDIS_REACHABLE_STREAM_GROUP_READY"
    deployment_ready = deployment_runtime.get("status") == "DEPLOYMENT_RUNTIME_READY"
    gates = [
        {
            "key": "deployment_topology",
            "name": "部署方式与隔离边界",
            "confirmed": False,
            "current_evidence": "Docker CLI/daemon/compose、Node/npm、前端构建产物和后端入口文件均可探测；正式生产拓扑验收仍未登记。" if deployment_ready else "已能读取当前运行配置；Docker、Node/npm、前端构建产物或后端入口文件仍有未满足项，尚未登记正式 Docker/生产拓扑验收结果。",
            "required_test": "实施方案中确认 API、前端、数据库、Redis、对象存储、Worker 的部署边界与回滚方式，并在目标环境演练。",
        },
        {
            "key": "user_scale",
            "name": "用户规模",
            "confirmed": False,
            "current_evidence": "当前系统没有登记正式并发用户数或账号规模验收基线。",
            "required_test": "登记目标用户数、并发峰值、部门范围和权限矩阵样本，并纳入压测。",
        },
        {
            "key": "response_time",
            "name": "响应时间",
            "confirmed": False,
            "current_evidence": "模型连接超时和读取超时已配置；业务接口响应时间目标尚未验收。",
            "required_test": "分别测试只读工具、人工确认、附件上传、模型工具循环、前端首屏和错误恢复响应时间。",
        },
        {
            "key": "availability",
            "name": "可用性",
            "confirmed": False,
            "current_evidence": "数据库健康检查可读；Redis 消息 stream 和消费组已可读；正式可用性目标、监控和告警未验收。" if redis_ready else "数据库健康检查可读；Redis 消息 stream 或消费组尚未确认可用；正式可用性目标、监控和告警未验收。",
            "required_test": "确认可用性口径、健康检查、进程守护、告警、故障切换和人工降级预案。",
        },
        {
            "key": "backup_frequency",
            "name": "备份频率",
            "confirmed": False,
            "current_evidence": "已提供 PostgreSQL 逻辑备份脚本并检测到 pg_dump/pg_restore。" if backup_tooling_ready else "已提供 PostgreSQL 逻辑备份脚本；当前运行环境尚未同时检测到 pg_dump 和 pg_restore，且未登记正式备份策略。",
            "required_test": "确认全量/增量备份频率、保留周期、备份加密、备份介质和责任人。",
        },
        {
            "key": "restore_objective",
            "name": "恢复目标",
            "confirmed": False,
            "current_evidence": "已有备份/恢复客户端工具可用于演练，但未登记 RTO/RPO 或恢复演练通过证据。" if backup_tooling_ready else "未满足本机备份/恢复客户端工具基线，也未登记 RTO/RPO 或恢复演练通过证据。",
            "required_test": "在隔离环境恢复数据库、附件对象、模型配置和迁移版本，记录 RTO/RPO。",
        },
        {
            "key": "log_retention",
            "name": "日志保留期限",
            "confirmed": False,
            "current_evidence": "审计日志、应用日志、访问日志和模型调用日志保留天数均已配置；正式采集、脱敏、归档、检索和删除策略仍待验收。" if log_policy_ready else "系统有审计事件表；审计日志、应用日志、访问日志或模型调用日志保留期限尚未全部配置。",
            "required_test": "按合规要求确认日志种类、脱敏规则、保留期限、检索方式和删除策略。",
        },
        {
            "key": "production_storage",
            "name": "生产附件存储",
            "confirmed": bool(s3_ready and cfg.environment.lower() in {"production", "prod"}),
            "current_evidence": "当前文件后端为 S3 且必要桶/端点已配置。" if s3_ready else "当前未确认可用于生产的私有 S3 兼容对象存储。",
            "required_test": "验证版本保留、权限隔离、原件哈希校验、下载撤权、备份恢复和防病毒/OCR 边界。",
        },
        {
            "key": "model_operations",
            "name": "模型运行边界",
            "confirmed": bool(model_cfg.llm_enabled and model_cfg.active_model and model_cfg.llm_context_window > 0),
            "current_evidence": f"模型开关={'已启用' if model_cfg.llm_enabled else '未启用'}，上下文窗口={model_cfg.llm_context_window}。",
            "required_test": "验证模型供应商、模型名、上下文窗口、超时、工具循环、强制压缩和失败回执。",
        },
    ]
    return _apply_acceptance_evidence(gates, acceptance_evidence)


def _readiness_summary(
    cfg,
    model_cfg,
    database_baseline: dict,
    database_health: dict,
    migration_status: dict,
    redis_status: dict,
    deployment_runtime: dict,
    backup_restore: dict,
    log_retention: dict,
    gates: list[dict],
) -> dict:
    machine_blockers: list[dict] = []
    ready_items: list[str] = []

    def block(key: str, title: str, status: str | None, next_action: str) -> None:
        machine_blockers.append({"key": key, "title": title, "status": status, "next_action": next_action})

    if database_baseline.get("delivery_ready") and database_health.get("reachable"):
        ready_items.append("postgres_moldpilot_database")
    else:
        block("database", "PostgreSQL/moldpilot 数据库基线", database_baseline.get("status"), "检查 MOLD_DATABASE_URL，必须指向 PostgreSQL 的 moldpilot 库，并确认数据库可读。")

    if migration_status.get("matches_repository_heads"):
        ready_items.append("alembic_migration_head")
    else:
        block("migrations", "数据库迁移版本", migration_status.get("status"), "用迁移账号核对或执行 alembic upgrade head，确认 alembic_version 等于仓库 head。")

    if redis_status.get("status") == "REDIS_REACHABLE_STREAM_GROUP_READY":
        ready_items.append("redis_stream_group")
    else:
        block("redis", "Redis 消息 stream/消费组", redis_status.get("status"), "启动或修复 Redis，运行消息 Worker 初始化 stream/group，并复核重试、死信和去重。")

    if deployment_runtime.get("status") == "DEPLOYMENT_RUNTIME_READY":
        ready_items.append("local_deployment_runtime_prerequisites")
    else:
        block("deployment_runtime", "本机部署运行前提", deployment_runtime.get("status"), "核对 Docker daemon/compose、Node/npm、前端构建产物和后端 API/Worker 入口。")

    if backup_restore.get("status") == "BACKUP_TOOLING_READY":
        ready_items.append("postgres_backup_restore_tooling")
    else:
        block("backup_restore", "PostgreSQL 备份/恢复工具链", backup_restore.get("status"), "安装 PostgreSQL 客户端或指定 pg_dump/pg_restore，配置 MOLD_RESTORE_DATABASE_URL，执行备份并在隔离库恢复演练。")

    if log_retention.get("policy_fully_configured"):
        ready_items.append("log_retention_policy")
    else:
        block("log_retention", "日志保留期限", log_retention.get("status"), "配置审计、应用、访问、模型调用日志保留天数，并确认脱敏、归档、检索和删除策略。")

    if cfg.environment.lower() in {"production", "prod"} and cfg.file_backend != "s3":
        block("production_storage", "生产附件存储", cfg.file_backend, "生产环境必须配置私有 S3 兼容对象存储，并完成版本、权限、备份恢复和撤权验证。")
    elif cfg.file_backend == "s3":
        ready_items.append("s3_file_storage_configured")

    if not model_cfg.llm_enabled or not model_cfg.active_model:
        block("model_runtime", "模型运行配置", "MODEL_NOT_READY", "启用模型配置并确认供应商、模型名、上下文窗口、超时、工具循环和失败回执。")
    else:
        ready_items.append("model_runtime_configured")

    acceptance_gaps = [
        {"key": item["key"], "name": item["name"], "required_test": item["required_test"]}
        for item in gates
        if not item["confirmed"]
    ]
    return {
        "overall_status": "BLOCKED" if machine_blockers or acceptance_gaps else "READY_FOR_DELIVERY",
        "machine_status": "BLOCKED" if machine_blockers else "MACHINE_PREREQUISITES_READY",
        "acceptance_status": "NOT_VERIFIED" if acceptance_gaps else "VERIFIED",
        "machine_blocker_count": len(machine_blockers),
        "acceptance_gap_count": len(acceptance_gaps),
        "ready_items": ready_items,
        "machine_blockers": machine_blockers,
        "acceptance_gaps": acceptance_gaps,
        "note": "该汇总由只读运行事实和验收门槛生成；不能替代正式生产验收、压测、恢复演练或业务验收。",
    }


def query(db, _user, data: OperationsReadinessInput) -> dict:
    cfg = settings()
    model_cfg = model_settings()
    deployment_runtime = _deployment_runtime_status()
    backup_restore = _backup_restore_status()
    log_retention = _log_retention_status(db, cfg)
    redis_status = _redis_status(cfg.redis_url)
    acceptance_evidence = _acceptance_evidence_status(cfg)
    gates = _gates(cfg, model_cfg, backup_restore, log_retention, redis_status, deployment_runtime, acceptance_evidence)
    database_baseline = _database_baseline(cfg.database_url)
    database_health = _db_status(db)
    migration_status = _migration_status(db)
    readiness_summary = _readiness_summary(
        cfg,
        model_cfg,
        database_baseline,
        database_health,
        migration_status,
        redis_status,
        deployment_runtime,
        backup_restore,
        log_retention,
        gates,
    )
    warnings = [
        "FR-118 要求在实施方案中确认部署、用户规模、响应时间、可用性、备份频率、恢复目标和日志保留期限；未确认前不得承诺性能、可用性或准确率。",
        "本工具只读核对当前运行事实和验收缺口；不执行部署、备份、恢复、压测或清理。",
        "返回结果只包含脱敏后的配置形态和布尔状态，不返回数据库密码、API Key、S3 密钥或 Worker 密钥。",
    ]
    if not database_baseline["delivery_ready"]:
        warnings.append("当前数据库配置没有满足 PostgreSQL/moldpilot/Navicat 交付基线；不要再用 SQLite 结果作为交付依据。")
    if database_health.get("dialect") == "postgresql" and database_health.get("current_database") != "moldpilot":
        warnings.append("当前实际 PostgreSQL 会话没有连到 moldpilot 数据库，请检查 .env 与 Navicat 连接库名是否一致。")
    if not migration_status.get("matches_repository_heads"):
        warnings.append("当前数据库迁移版本没有确认等于仓库 Alembic head；请先用迁移账号核对或执行 alembic upgrade head 后再验收。")
    if backup_restore.get("status") != "BACKUP_TOOLING_READY":
        warnings.append("当前备份/恢复工具链未完整就绪；请安装 PostgreSQL 客户端工具或提供 pg_dump/pg_restore 路径后再做备份恢复演练。")
    if not log_retention.get("policy_fully_configured"):
        warnings.append("当前日志保留期限尚未全部配置；请明确审计、应用访问和模型调用日志的保留天数、脱敏、归档和删除策略。")
    if redis_status.get("status") != "REDIS_REACHABLE_STREAM_GROUP_READY":
        warnings.append("当前 Redis 消息链路未达到运行就绪：需要 Redis 可达、业务事件 stream 存在且通知消费组已初始化。")
    if deployment_runtime.get("status") != "DEPLOYMENT_RUNTIME_READY":
        warnings.append("当前部署运行前提未完整满足；请核对 Docker daemon/compose、Node/npm、前端构建产物和后端 Worker/API 入口后再做生产拓扑验收。")
    if cfg.environment.lower() in {"production", "prod"} and cfg.file_backend == "local":
        warnings.append("当前声明为生产环境但附件后端仍是 local，需改为私有对象存储并完成恢复演练后才能作为生产交付。")
    return {
        "data": [
            {
                "environment": cfg.environment,
                "api_origin": cfg.origin,
                "cookie_secure": cfg.cookie_secure,
                "database": {
                    **_safe_url(cfg.database_url),
                    "baseline": database_baseline,
                    "health": database_health,
                    "migrations": migration_status,
                },
                "redis": {
                    **_safe_url(cfg.redis_url),
                    **redis_status,
                },
                "deployment_runtime": deployment_runtime,
                "file_storage": {
                    "backend": cfg.file_backend,
                    "local_root": _path_summary(cfg.file_local_root) if cfg.file_backend == "local" else None,
                    "s3": {
                        "endpoint": _safe_url(cfg.file_s3_endpoint),
                        "bucket_configured": _configured(cfg.file_s3_bucket),
                        "region": cfg.file_s3_region,
                        "access_key_configured": _configured(cfg.file_s3_access_key),
                        "secret_key_configured": _configured(cfg.file_s3_secret_key),
                    } if cfg.file_backend == "s3" else None,
                    "max_bytes": cfg.file_max_bytes,
                    "daily_bytes": cfg.file_daily_bytes,
                },
                "backup_restore": backup_restore,
                "log_retention": log_retention,
                "acceptance_evidence": acceptance_evidence,
                "security_runtime": {
                    "worker_secret_configured": _configured(cfg.worker_secret),
                    "credential_encryption_key_configured": _configured(cfg.credential_encryption_key),
                    "erp_base_url_configured": _configured(cfg.erp_base_url),
                },
                "model_runtime": {
                    "enabled": model_cfg.llm_enabled,
                    "provider": model_cfg.llm_provider,
                    "model": model_cfg.active_model,
                    "context_window": model_cfg.llm_context_window,
                    "max_turns": model_cfg.llm_max_turns,
                    "max_output_tokens": model_cfg.llm_max_output_tokens,
                    "connect_timeout": model_cfg.llm_connect_timeout,
                    "read_timeout": model_cfg.llm_read_timeout,
                },
                "runtime_counters": _counts(db, data.include_runtime_counters),
                "readiness_summary": readiness_summary,
                "acceptance_gates": gates,
                "confirmed_gate_count": sum(1 for item in gates if item["confirmed"]),
                "required_gate_count": len(gates),
            }
        ],
        "source": "agent_runtime",
        "as_of": now().isoformat(),
        "limitations": warnings,
    }

