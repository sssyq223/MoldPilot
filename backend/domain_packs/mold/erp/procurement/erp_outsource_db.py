"""Read-only connection to the ERP outsource database.

SQL is compiled into registered query tools; the Agent cannot invent statements.
"""
from __future__ import annotations

import os
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator

import psycopg
from psycopg.rows import dict_row

from domain_packs.mold.config import settings
from domain_packs.mold.ports.errors import DomainError

DEFAULT_ENV_CANDIDATES = (
    Path(r"d:\ERP\management-system\ruoyi-fastapi-backend\.env.dev"),
    Path(r"d:\ERP\management-system\ruoyi-fastapi-backend\.env.local"),
)


def _load_env_file(path: Path) -> dict[str, str]:
    if not path.exists():
        return {}
    values: dict[str, str] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        text = line.strip()
        if not text or text.startswith("#") or "=" not in text:
            continue
        key, raw = text.split("=", 1)
        raw = raw.strip()
        if len(raw) >= 2 and raw[0] == raw[-1] and raw[0] in {"'", '"'}:
            raw = raw[1:-1]
        values[key.strip()] = raw
    return values


def _merged_env() -> dict[str, str]:
    merged: dict[str, str] = {}
    config = settings()
    configured = Path(getattr(config, "erp_env_file", "") or "")
    paths = [configured] if configured else list(DEFAULT_ENV_CANDIDATES)
    for path in paths:
        if path:
            merged.update(_load_env_file(path))
    return merged


def connection_kwargs() -> dict[str, Any]:
    config = settings()
    file_env = _merged_env()
    host = (
        getattr(config, "erp_db_host", "")
        or os.environ.get("ERP_DB_HOST")
        or os.environ.get("DB_HOST")
        or file_env.get("DB_HOST")
        or file_env.get("ERP_DB_HOST")
        or ""
    )
    port = (
        str(getattr(config, "erp_db_port", "") or "")
        or os.environ.get("ERP_DB_PORT")
        or os.environ.get("DB_PORT")
        or file_env.get("DB_PORT")
        or file_env.get("ERP_DB_PORT")
        or "5432"
    )
    user = (
        getattr(config, "erp_db_username", "")
        or os.environ.get("ERP_DB_USERNAME")
        or os.environ.get("DB_USERNAME")
        or file_env.get("DB_USERNAME")
        or file_env.get("ERP_DB_USERNAME")
        or ""
    )
    password = (
        getattr(config, "erp_db_password", "")
        or os.environ.get("ERP_DB_PASSWORD")
        or os.environ.get("DB_PASSWORD")
        or file_env.get("DB_PASSWORD")
        or file_env.get("ERP_DB_PASSWORD")
        or ""
    )
    database = (
        getattr(config, "erp_db_database", "")
        or os.environ.get("ERP_DB_DATABASE")
        or os.environ.get("DB_DATABASE")
        or file_env.get("DB_DATABASE")
        or file_env.get("ERP_DB_DATABASE")
        or ""
    )
    if not all((host, user, password, database)):
        raise DomainError(
            "ERP_NOT_CONFIGURED",
            "尚未配置 ERP 委外只读库。请设置 MOLD_ERP_DB_* 或 ERP_DB_*，或指定 MOLD_ERP_ENV_FILE。",
            503,
        )
    return {
        "host": host,
        "port": int(port),
        "user": user,
        "password": password,
        "dbname": database,
        "connect_timeout": 8,
    }


@contextmanager
def connect() -> Iterator[psycopg.Cursor]:
    try:
        with psycopg.connect(**connection_kwargs(), row_factory=dict_row) as conn:
            with conn.cursor() as cursor:
                yield cursor
    except DomainError:
        raise
    except psycopg.Error as error:
        raise DomainError("ERP_OUTCOME_UNKNOWN", "ERP 委外只读查询失败，请核对 ERP 库连接", 502) from error


def fetch_all(sql: str, params: dict[str, Any] | None = None) -> list[dict[str, Any]]:
    with connect() as cursor:
        cursor.execute(sql, params or {})
        return [dict(row) for row in cursor.fetchall()]


def fetch_one(sql: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
    rows = fetch_all(sql, params)
    return rows[0] if rows else {}
