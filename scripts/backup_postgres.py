"""Create a local MoldPilot PostgreSQL logical backup without exposing secrets.

The script is intentionally PostgreSQL-only and refuses SQLite. It reads the
local `.env` by default, writes into `.local/backups` by default, and passes the
database password to `pg_dump` through the process environment instead of the
command line.
"""
from __future__ import annotations

import argparse
from datetime import datetime
import os
from pathlib import Path
import shutil
import subprocess
from urllib.parse import unquote, urlsplit

from dotenv import dotenv_values


def _configured(value: str | None) -> bool:
    return bool((value or "").strip())


def _parse_url(value: str, expected_db: str) -> dict:
    parsed = urlsplit(value)
    if parsed.scheme.startswith("sqlite"):
        raise SystemExit("Refusing SQLite: MoldPilot backups must target PostgreSQL.")
    if not parsed.scheme.startswith("postgresql"):
        raise SystemExit(f"Unsupported database scheme: {parsed.scheme}")
    database = parsed.path.lstrip("/")
    if database != expected_db:
        raise SystemExit(f"Unexpected database {database!r}; expected {expected_db!r}.")
    try:
        port = parsed.port
    except ValueError as error:
        raise SystemExit(f"Invalid database port: {error}") from error
    return {
        "host": parsed.hostname or "127.0.0.1",
        "port": str(port or 5432),
        "username": unquote(parsed.username or ""),
        "password": unquote(parsed.password or ""),
        "database": database,
    }


def _safe_name(value: str) -> str:
    return "".join(ch if ch.isalnum() or ch in {"-", "_"} else "_" for ch in value)


def main() -> int:
    parser = argparse.ArgumentParser(description="Create a MoldPilot PostgreSQL pg_dump backup.")
    parser.add_argument("--env-file", default=".env", help="Path to local dotenv file. Defaults to .env.")
    parser.add_argument("--url-key", default="MOLD_DATABASE_URL", help="Dotenv key containing PostgreSQL DSN.")
    parser.add_argument("--expected-db", default="moldpilot", help="Expected database name. Defaults to moldpilot.")
    parser.add_argument("--output-dir", default=".local/backups", help="Backup directory. Defaults to .local/backups.")
    parser.add_argument("--pg-dump", default="", help="Optional explicit pg_dump executable path.")
    parser.add_argument("--dry-run", action="store_true", help="Validate configuration and print the backup target only.")
    args = parser.parse_args()

    config = dotenv_values(args.env_file)
    url = config.get(args.url_key)
    if not _configured(url):
        raise SystemExit(f"{args.url_key} is missing in {args.env_file}")
    database = _parse_url(str(url), args.expected_db)

    pg_dump = args.pg_dump or shutil.which("pg_dump")
    if not pg_dump:
        print("pg_dump_available=False")
        print(f"database={database['database']}")
        print(f"host={database['host']}")
        print(f"port={database['port']}")
        print("backup_ready=False")
        print("hint=Install PostgreSQL client tools or pass --pg-dump with an explicit executable path.")
        return 0 if args.dry_run else 2

    output_dir = Path(args.output_dir)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    output_file = output_dir / f"{_safe_name(database['database'])}_{timestamp}.dump"
    command = [
        str(pg_dump),
        "--format=custom",
        "--no-owner",
        "--no-acl",
        "--file",
        str(output_file),
        "--host",
        database["host"],
        "--port",
        database["port"],
        "--username",
        database["username"],
        database["database"],
    ]

    print("pg_dump_available=True")
    print(f"database={database['database']}")
    print(f"host={database['host']}")
    print(f"port={database['port']}")
    print(f"output={output_file}")
    if args.dry_run:
        print("dry_run=True")
        print("backup_ready=True")
        return 0

    output_dir.mkdir(parents=True, exist_ok=True)
    env = os.environ.copy()
    if database["password"]:
        env["PGPASSWORD"] = database["password"]
    completed = subprocess.run(command, env=env, check=False)
    if completed.returncode != 0:
        raise SystemExit(completed.returncode)
    print("backup_created=True")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
