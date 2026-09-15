from __future__ import annotations

from pathlib import Path
from urllib.parse import urlsplit

from pydantic import Field
from sqlalchemy import func, select, text

from .config import model_settings, settings
from .db import now
from .models import AuditEvent, Outbox, Run, Step
from .schemas import StrictModel


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
        "path_present": bool(parsed.path and parsed.path != "/"),
        "username_present": bool(parsed.username),
        "password_present": bool(parsed.password),
        "query_present": bool(parsed.query),
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


def _db_status(db) -> dict:
    try:
        db.execute(text("SELECT 1"))
        return {"reachable": True}
    except Exception as error:  # pragma: no cover - defensive status path
        return {"reachable": False, "error_type": type(error).__name__}


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


def _gates(cfg, model_cfg) -> list[dict]:
    s3_ready = cfg.file_backend == "s3" and _configured(cfg.file_s3_bucket) and _configured(cfg.file_s3_endpoint)
    return [
        {
            "key": "deployment_topology",
            "name": "部署方式与隔离边界",
            "confirmed": False,
            "current_evidence": "已能读取当前运行配置，但尚未登记正式 Docker/生产拓扑验收结果。",
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
            "current_evidence": "数据库健康检查可读；正式可用性目标、监控和告警未验收。",
            "required_test": "确认可用性口径、健康检查、进程守护、告警、故障切换和人工降级预案。",
        },
        {
            "key": "backup_frequency",
            "name": "备份频率",
            "confirmed": False,
            "current_evidence": "未登记数据库、对象存储和模型配置的正式备份策略。",
            "required_test": "确认全量/增量备份频率、保留周期、备份加密、备份介质和责任人。",
        },
        {
            "key": "restore_objective",
            "name": "恢复目标",
            "confirmed": False,
            "current_evidence": "未登记 RTO/RPO 或恢复演练通过证据。",
            "required_test": "在隔离环境恢复数据库、附件对象、模型配置和迁移版本，记录 RTO/RPO。",
        },
        {
            "key": "log_retention",
            "name": "日志保留期限",
            "confirmed": False,
            "current_evidence": "系统有审计事件表；正式应用日志、访问日志、模型调用日志和审计日志保留期限尚未确认。",
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


def query(db, _user, data: OperationsReadinessInput) -> dict:
    cfg = settings()
    model_cfg = model_settings()
    gates = _gates(cfg, model_cfg)
    warnings = [
        "FR-118 要求在实施方案中确认部署、用户规模、响应时间、可用性、备份频率、恢复目标和日志保留期限；未确认前不得承诺性能、可用性或准确率。",
        "本工具只读核对当前运行事实和验收缺口；不执行部署、备份、恢复、压测或清理。",
        "返回结果只包含脱敏后的配置形态和布尔状态，不返回数据库密码、API Key、S3 密钥或 Worker 密钥。",
    ]
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
                    "health": _db_status(db),
                },
                "redis": {
                    **_safe_url(cfg.redis_url),
                    "status": "CONFIGURED_NOT_PROBED" if _configured(cfg.redis_url) else "NOT_CONFIGURED",
                    "note": "避免在只读工具中制造外部副作用；正式验收需单独验证 Redis 连接、重试和死信。",
                },
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
                "acceptance_gates": gates,
                "confirmed_gate_count": sum(1 for item in gates if item["confirmed"]),
                "required_gate_count": len(gates),
            }
        ],
        "source": "agent_runtime",
        "as_of": now().isoformat(),
        "limitations": warnings,
    }
