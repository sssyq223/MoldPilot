"""Inspect or apply MoldPilot PostgreSQL log retention rules.

The default mode is dry-run. Destructive pruning requires both --execute and an
explicit acknowledgement flag. SQLite is refused because delivery validation is
PostgreSQL/Navicat based.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime, timedelta
from decimal import Decimal
from pathlib import Path
from urllib.parse import urlsplit
from zoneinfo import ZoneInfo

from dotenv import dotenv_values
from sqlalchemy import create_engine, text

SHANGHAI = ZoneInfo("Asia/Shanghai")


def now() -> datetime:
    return datetime.now(SHANGHAI)


def _env(env_file: str, key: str, default: str = "") -> str:
    values = dotenv_values(env_file)
    legacy = "MOLD_" + key.removeprefix("AGENT_") if key.startswith("AGENT_") else ""
    return os.environ.get(key) or values.get(key) or os.environ.get(legacy) or values.get(legacy) or default


def _days(env_file: str, key: str) -> int:
    value = _env(env_file, key, "0").strip()
    try:
        return int(value)
    except ValueError as exc:
        raise SystemExit(f"{key} must be an integer number of days") from exc


def _require_postgresql(url: str) -> None:
    parsed = urlsplit(url)
    if not url:
        raise SystemExit("AGENT_DATABASE_URL is required")
    if parsed.scheme.startswith("sqlite"):
        raise SystemExit("Refusing SQLite: log retention must run against PostgreSQL.")
    if not parsed.scheme.startswith("postgresql"):
        raise SystemExit(f"Unsupported database scheme: {parsed.scheme}")


def _json_default(value):
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, Decimal):
        return str(value)
    return str(value)


def _archive_rows(archive_dir: Path, category: str, rows: list[dict], metadata: dict) -> str | None:
    if not rows:
        return None
    archive_dir.mkdir(parents=True, exist_ok=True)
    path = archive_dir / f"{category}_{now().strftime('%Y%m%d_%H%M%S')}.jsonl"
    with path.open("w", encoding="utf-8") as handle:
        handle.write(json.dumps({"type": "metadata", **metadata}, ensure_ascii=False, default=_json_default) + "\n")
        for row in rows:
            handle.write(json.dumps({"type": "row", **row}, ensure_ascii=False, default=_json_default) + "\n")
    return str(path)


def _ids(rows: list[dict]) -> list[str]:
    return [str(row["id"]) for row in rows]


def _load_rows(connection, sql: str, params: dict) -> list[dict]:
    return [dict(row) for row in connection.execute(text(sql), params).mappings().all()]


def _audit_retention(connection, *, days: int, limit: int, archive_dir: Path, execute: bool) -> dict:
    if days <= 0:
        return {"configured": False, "action": "skipped", "reason": "AGENT_AUDIT_LOG_RETENTION_DAYS is not configured"}
    cutoff = now() - timedelta(days=days)
    total = connection.execute(text("SELECT count(*) FROM audit_event WHERE created_at < :cutoff"), {"cutoff": cutoff}).scalar_one()
    rows = _load_rows(
        connection,
        """
        SELECT id, created_at, user_id, action, resource_id, detail
        FROM audit_event
        WHERE created_at < :cutoff
        ORDER BY created_at, id
        LIMIT :limit
        """,
        {"cutoff": cutoff, "limit": limit},
    )
    archive_path = _archive_rows(archive_dir, "audit_event", rows, {"category": "audit_log", "cutoff": cutoff, "retention_days": days}) if execute else None
    deleted = 0
    if execute and rows:
        deleted = connection.execute(text("DELETE FROM audit_event WHERE id = ANY(:ids)"), {"ids": _ids(rows)}).rowcount or 0
    return {
        "configured": True,
        "retention_days": days,
        "cutoff": cutoff.isoformat(),
        "eligible_count": total,
        "batch_count": len(rows),
        "archived_to": archive_path,
        "deleted_count": deleted,
    }


def _model_retention(connection, *, days: int, limit: int, archive_dir: Path, execute: bool) -> dict:
    if days <= 0:
        return {"configured": False, "action": "skipped", "reason": "AGENT_MODEL_LOG_RETENTION_DAYS is not configured"}
    cutoff = now() - timedelta(days=days)
    step_total = connection.execute(
        text(
            """
            SELECT count(*)
            FROM ai_step step
            JOIN ai_run run ON run.id = step.run_id
            WHERE run.created_at < :cutoff
              AND NOT (step.result::jsonb ? 'retention_redacted')
            """
        ),
        {"cutoff": cutoff},
    ).scalar_one()
    step_rows = _load_rows(
        connection,
        """
        SELECT step.id, step.created_at, step.run_id, step.sequence, step.tool, step.request_hash, step.result
        FROM ai_step step
        JOIN ai_run run ON run.id = step.run_id
        WHERE run.created_at < :cutoff
          AND NOT (step.result::jsonb ? 'retention_redacted')
        ORDER BY run.created_at, step.sequence, step.id
        LIMIT :limit
        """,
        {"cutoff": cutoff, "limit": limit},
    )
    run_total = connection.execute(
        text(
            """
            SELECT count(*)
            FROM ai_run
            WHERE created_at < :cutoff
              AND checkpoint::jsonb <> '{}'::jsonb
              AND NOT (checkpoint::jsonb ? 'retention_redacted')
            """
        ),
        {"cutoff": cutoff},
    ).scalar_one()
    run_rows = _load_rows(
        connection,
        """
        SELECT id, created_at, conversation_id, user_id, status, checkpoint
        FROM ai_run
        WHERE created_at < :cutoff
          AND checkpoint::jsonb <> '{}'::jsonb
          AND NOT (checkpoint::jsonb ? 'retention_redacted')
        ORDER BY created_at, id
        LIMIT :limit
        """,
        {"cutoff": cutoff, "limit": limit},
    )
    archived_steps = _archive_rows(archive_dir, "ai_step_result", step_rows, {"category": "model_log_step_result", "cutoff": cutoff, "retention_days": days}) if execute else None
    archived_runs = _archive_rows(archive_dir, "ai_run_checkpoint", run_rows, {"category": "model_log_run_checkpoint", "cutoff": cutoff, "retention_days": days}) if execute else None
    redacted_steps = 0
    redacted_runs = 0
    applied_at = now().isoformat()
    if execute and step_rows:
        redacted_steps = connection.execute(
            text(
                """
                UPDATE ai_step
                SET result = jsonb_build_object(
                    'retention_redacted', true,
                    'retention_applied_at', :applied_at,
                    'tool', tool,
                    'request_hash', request_hash
                )
                WHERE id = ANY(:ids)
                """
            ),
            {"ids": _ids(step_rows), "applied_at": applied_at},
        ).rowcount or 0
    if execute and run_rows:
        redacted_runs = connection.execute(
            text(
                """
                UPDATE ai_run
                SET checkpoint = jsonb_build_object(
                    'retention_redacted', true,
                    'retention_applied_at', :applied_at,
                    'previous_checkpoint_archived', true
                )
                WHERE id = ANY(:ids)
                """
            ),
            {"ids": _ids(run_rows), "applied_at": applied_at},
        ).rowcount or 0
    return {
        "configured": True,
        "retention_days": days,
        "cutoff": cutoff.isoformat(),
        "eligible_step_result_count": step_total,
        "eligible_checkpoint_count": run_total,
        "batch_step_count": len(step_rows),
        "batch_checkpoint_count": len(run_rows),
        "step_archive": archived_steps,
        "checkpoint_archive": archived_runs,
        "redacted_step_count": redacted_steps,
        "redacted_checkpoint_count": redacted_runs,
        "note": "模型日志保留只归档并脱敏工具结果与运行 checkpoint；不删除会话、用户 prompt 或最终业务摘要。",
    }


def _access_retention(connection, *, days: int, execute: bool) -> dict:
    if days <= 0:
        return {"configured": False, "action": "skipped", "reason": "AGENT_ACCESS_LOG_RETENTION_DAYS is not configured"}
    cutoff = now() - timedelta(days=days)
    expired_total = connection.execute(text("SELECT count(*) FROM login_session WHERE expires_at < :now"), {"now": now()}).scalar_one()
    old_total = connection.execute(text("SELECT count(*) FROM login_session WHERE created_at < :cutoff"), {"cutoff": cutoff}).scalar_one()
    deleted = 0
    if execute:
        deleted = connection.execute(
            text("DELETE FROM login_session WHERE expires_at < :now OR created_at < :cutoff"),
            {"now": now(), "cutoff": cutoff},
        ).rowcount or 0
    return {
        "configured": True,
        "retention_days": days,
        "cutoff": cutoff.isoformat(),
        "expired_session_count": expired_total,
        "old_session_count": old_total,
        "deleted_session_count": deleted,
        "note": "本地访问日志目前只包含登录会话清理；Web/反向代理访问日志需在部署层按同一保留天数配置。",
    }


def build_report(env_file: str, archive_dir: Path, limit: int, execute: bool, acknowledge: bool) -> dict:
    if execute and not acknowledge:
        raise SystemExit("Refusing to prune logs without --i-understand-this-will-prune-logs")
    url = _env(env_file, "AGENT_DATABASE_URL")
    _require_postgresql(url)
    engine = create_engine(url, pool_pre_ping=True)
    cfg = {
        "audit": _days(env_file, "AGENT_AUDIT_LOG_RETENTION_DAYS"),
        "application": _days(env_file, "AGENT_APP_LOG_RETENTION_DAYS"),
        "access": _days(env_file, "AGENT_ACCESS_LOG_RETENTION_DAYS"),
        "model": _days(env_file, "AGENT_MODEL_LOG_RETENTION_DAYS"),
    }
    with engine.begin() as connection:
        database = connection.execute(text("SELECT current_database()")).scalar_one()
        if database != "moldpilot":
            raise SystemExit(f"Refusing database {database!r}; expected 'moldpilot'.")
        report = {
            "mode": "execute" if execute else "dry-run",
            "database": database,
            "as_of": now().isoformat(),
            "retention_days": cfg,
            "archive_dir": str(archive_dir),
            "batch_limit": limit,
            "audit_log": _audit_retention(connection, days=cfg["audit"], limit=limit, archive_dir=archive_dir, execute=execute),
            "access_log": _access_retention(connection, days=cfg["access"], execute=execute),
            "model_log": _model_retention(connection, days=cfg["model"], limit=limit, archive_dir=archive_dir, execute=execute),
            "application_log": {
                "configured": cfg["application"] > 0,
                "retention_days": cfg["application"] if cfg["application"] > 0 else None,
                "note": "应用日志文件由部署层采集/轮转；本脚本不扫描或删除未登记目录，避免误删本机文件。",
            },
        }
    engine.dispose()
    return report


def main() -> int:
    parser = argparse.ArgumentParser(description="Dry-run or apply MoldPilot PostgreSQL log retention rules.")
    parser.add_argument("--env-file", default=".env")
    parser.add_argument("--archive-dir", type=Path, default=Path(".local/log-archives"))
    parser.add_argument("--limit", type=int, default=5000)
    parser.add_argument("--execute", action="store_true", help="Apply pruning/redaction. Default is dry-run only.")
    parser.add_argument("--i-understand-this-will-prune-logs", action="store_true")
    args = parser.parse_args()
    if args.limit <= 0:
        raise SystemExit("--limit must be positive")
    report = build_report(args.env_file, args.archive_dir, args.limit, args.execute, args.i_understand_this_will_prune_logs)
    print(json.dumps(report, ensure_ascii=False, indent=2, default=_json_default))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
