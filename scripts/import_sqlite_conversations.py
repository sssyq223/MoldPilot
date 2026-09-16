"""Import legacy SQLite conversation history into the PostgreSQL agent DB.

This is intentionally narrow: it migrates the workbench conversation tables
only (ai_conversation, ai_run, ai_step) and remaps the source owner to one
explicit PostgreSQL user. Business tables remain owned by migrations/imports
for their own domains.
"""

from __future__ import annotations

import argparse
import json
import sqlite3
from datetime import datetime
from pathlib import Path
from typing import Any

from sqlalchemy import select

from app import models as m
from app.db import SessionLocal


REQUIRED_TABLES = ("ai_conversation", "ai_run", "ai_step")


def _parse_json(value: Any) -> Any:
    if value is None or isinstance(value, (dict, list)):
        return _strip_nul(value)
    if isinstance(value, str) and value.strip():
        return _strip_nul(json.loads(value))
    return {}


def _strip_nul(value: Any) -> Any:
    if isinstance(value, str):
        return value.replace("\x00", "")
    if isinstance(value, list):
        return [_strip_nul(item) for item in value]
    if isinstance(value, dict):
        return {str(_strip_nul(key)): _strip_nul(item) for key, item in value.items()}
    return value


def _parse_datetime(value: Any) -> datetime | None:
    if value is None or isinstance(value, datetime):
        return value
    text = str(value).strip()
    if not text:
        return None
    return datetime.fromisoformat(text.replace("Z", "+00:00"))


def _bool(value: Any) -> bool:
    return bool(int(value)) if isinstance(value, int) else bool(value)


def _rows(connection: sqlite3.Connection, table: str) -> list[dict[str, Any]]:
    connection.row_factory = sqlite3.Row
    return [dict(row) for row in connection.execute(f"select * from {table}").fetchall()]


def _validate_source(connection: sqlite3.Connection) -> None:
    tables = {
        row[0]
        for row in connection.execute("select name from sqlite_master where type='table'")
    }
    missing = set(REQUIRED_TABLES) - tables
    if missing:
        raise SystemExit("SQLite 源库缺少对话表：" + "、".join(sorted(missing)))


def import_conversations(
    source: str | Path,
    db,
    target_user: m.User,
    *,
    execute: bool = False,
    preserve_authorization_hash: bool = False,
) -> dict[str, int | str | bool]:
    source_path = Path(source)
    if not source_path.exists():
        raise SystemExit(f"SQLite 源库不存在：{source_path}")

    with sqlite3.connect(source_path) as sqlite:
        _validate_source(sqlite)
        conversations = _rows(sqlite, "ai_conversation")
        runs = _rows(sqlite, "ai_run")
        steps = _rows(sqlite, "ai_step")

    imported = {"conversations": 0, "runs": 0, "steps": 0}
    skipped = {"conversations": 0, "runs": 0, "steps": 0}

    conversation_ids = {row["id"] for row in conversations}
    run_ids = {row["id"] for row in runs}

    if execute:
        for row in sorted(conversations, key=lambda item: str(item.get("created_at") or "")):
            if db.get(m.Conversation, row["id"]):
                skipped["conversations"] += 1
                continue
            db.add(
                m.Conversation(
                    id=row["id"],
                    user_id=target_user.id,
                    title=_strip_nul(row["title"]),
                    pinned=_bool(row.get("pinned", False)),
                    archived=_bool(row.get("archived", False)),
                    created_at=_parse_datetime(row.get("created_at")) or datetime.now(),
                )
            )
            imported["conversations"] += 1

        db.flush()
        for row in sorted(runs, key=lambda item: str(item.get("created_at") or "")):
            if row["conversation_id"] not in conversation_ids:
                skipped["runs"] += 1
                continue
            if db.get(m.Run, row["id"]):
                skipped["runs"] += 1
                continue
            checkpoint = _parse_json(row.get("checkpoint")) or {}
            if not preserve_authorization_hash:
                checkpoint.pop("authorization_hash", None)
            checkpoint["imported_from_sqlite"] = str(source_path)
            db.add(
                m.Run(
                    id=row["id"],
                    conversation_id=row["conversation_id"],
                    user_id=target_user.id,
                    security_version=target_user.security_version,
                    prompt=_strip_nul(row["prompt"]),
                    status=row["status"],
                    checkpoint=checkpoint,
                    result=_parse_json(row.get("result")),
                    lease_epoch=row.get("lease_epoch") or 0,
                    lease_until=_parse_datetime(row.get("lease_until")),
                    created_at=_parse_datetime(row.get("created_at")) or datetime.now(),
                )
            )
            imported["runs"] += 1

        db.flush()
        for row in sorted(steps, key=lambda item: (str(item.get("run_id") or ""), int(item.get("sequence") or 0))):
            if row["run_id"] not in run_ids:
                skipped["steps"] += 1
                continue
            if db.get(m.Step, row["id"]):
                skipped["steps"] += 1
                continue
            db.add(
                m.Step(
                    id=row["id"],
                    run_id=row["run_id"],
                    sequence=row["sequence"],
                    tool=row["tool"],
                    request_hash=row["request_hash"],
                    result=_parse_json(row.get("result")) or {},
                    created_at=_parse_datetime(row.get("created_at")) or datetime.now(),
                )
            )
            imported["steps"] += 1
        db.commit()
    else:
        imported = {"conversations": len(conversations), "runs": len(runs), "steps": len(steps)}

    return {
        "source": str(source_path),
        "target_username": target_user.username,
        "execute": execute,
        "imported_conversations": imported["conversations"],
        "imported_runs": imported["runs"],
        "imported_steps": imported["steps"],
        "skipped_conversations": skipped["conversations"],
        "skipped_runs": skipped["runs"],
        "skipped_steps": skipped["steps"],
        "authorization_hash_preserved": preserve_authorization_hash,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Import legacy SQLite AI conversations into PostgreSQL.")
    parser.add_argument("source", help="SQLite database path containing ai_conversation/ai_run/ai_step.")
    parser.add_argument("--target-username", default="admin", help="PostgreSQL user that should own imported history.")
    parser.add_argument("--execute", action="store_true", help="Actually insert rows. Omit for dry-run.")
    parser.add_argument("--preserve-authorization-hash", action="store_true", help="Keep old checkpoint authorization hashes.")
    args = parser.parse_args()

    with SessionLocal() as db:
        target_user = db.scalar(select(m.User).where(m.User.username == args.target_username))
        if not target_user:
            raise SystemExit(f"目标用户不存在：{args.target_username}")
        summary = import_conversations(
            args.source,
            db,
            target_user,
            execute=args.execute,
            preserve_authorization_hash=args.preserve_authorization_hash,
        )
    for key, value in summary.items():
        print(f"{key}={value}")


if __name__ == "__main__":
    main()
