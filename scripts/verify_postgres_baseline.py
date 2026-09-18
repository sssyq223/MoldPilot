"""Verify the MoldPilot PostgreSQL baseline without exposing secrets.

This script is intentionally PostgreSQL-only. It reads `.env`, executes the
same SQL used for Navicat verification, and fails if the configured database is
SQLite or not the expected local development database.
"""
from __future__ import annotations

import argparse
import os
from pathlib import Path
from urllib.parse import urlsplit

from alembic.config import Config
from alembic.script import ScriptDirectory
from dotenv import dotenv_values
from sqlalchemy import create_engine, text


def _statements(path: Path) -> list[str]:
    lines = [line for line in path.read_text(encoding="utf-8").splitlines() if not line.strip().startswith("--")]
    return [part.strip() for part in "\n".join(lines).split(";") if part.strip()]


def main() -> int:
    parser = argparse.ArgumentParser(description="Verify MoldPilot PostgreSQL/Navicat baseline.")
    parser.add_argument("--env-file", default=".env", help="Path to local dotenv file. Defaults to .env.")
    parser.add_argument("--url-key", default="AGENT_DATABASE_URL", help="Dotenv key containing PostgreSQL DSN.")
    parser.add_argument("--expected-db", default="moldpilot", help="Expected database name. Defaults to moldpilot.")
    parser.add_argument("--sql", default="database/verify_moldpilot_navicat.sql", help="Verification SQL file.")
    parser.add_argument("--alembic-ini", default="alembic.ini", help="Alembic config used to resolve repository heads.")
    args = parser.parse_args()

    config = dotenv_values(args.env_file)
    url = os.environ.get(args.url_key) or config.get(args.url_key)
    if not url and args.url_key == "AGENT_DATABASE_URL":
        url = os.environ.get("MOLD_DATABASE_URL") or config.get("MOLD_DATABASE_URL")
    if not url:
        raise SystemExit(f"{args.url_key} is missing in {args.env_file}")

    parsed = urlsplit(url)
    if "sqlite" in parsed.scheme:
        raise SystemExit("Refusing SQLite: MoldPilot development baseline must use PostgreSQL.")
    if not parsed.scheme.startswith("postgresql"):
        raise SystemExit(f"Unsupported database scheme: {parsed.scheme}")

    configured_db = parsed.path.lstrip("/")
    if configured_db != args.expected_db:
        raise SystemExit(f"Unexpected database {configured_db!r}; expected {args.expected_db!r}.")

    engine = create_engine(url)
    statements = _statements(Path(args.sql))
    if len(statements) < 4:
        raise SystemExit("Verification SQL is incomplete.")

    alembic_config = Config(args.alembic_ini)
    heads = sorted(ScriptDirectory.from_config(alembic_config).get_heads())
    if not heads:
        raise SystemExit("No Alembic repository heads found.")

    with engine.connect() as connection:
        database_row = connection.execute(text(statements[0])).one()
        admin_rows = connection.execute(text(statements[1])).mappings().all()
        count_rows = connection.execute(text(statements[2])).mappings().all()
        migration_rows = connection.execute(text(statements[3])).mappings().all()

    actual_db = database_row[0]
    if actual_db != args.expected_db:
        raise SystemExit(f"Connected to {actual_db!r}; expected {args.expected_db!r}.")
    if len(admin_rows) != 1:
        raise SystemExit(f"Expected exactly one admin row, found {len(admin_rows)}.")
    admin = admin_rows[0]
    if not admin["super_admin"] or not admin["active"]:
        raise SystemExit("Admin account exists but is not an active super administrator.")
    versions = sorted(str(row["alembic_version"]) for row in migration_rows)
    if set(versions) != set(heads):
        raise SystemExit(f"Database migrations out of sync: database={versions!r}, repository_heads={heads!r}.")

    print(f"database={actual_db}")
    print(f"host={parsed.hostname}")
    print(f"port={parsed.port}")
    print(f"admin={admin['username']} active={admin['active']} super_admin={admin['super_admin']}")
    for row in count_rows:
        print(f"{row['table_name']}={row['row_count']}")
    print(f"alembic_versions={','.join(versions)}")
    print(f"alembic_heads={','.join(heads)}")
    print("migration_baseline=OK")
    print("postgres_baseline=OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
