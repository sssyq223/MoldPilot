"""Validate or restore a MoldPilot PostgreSQL logical backup safely.

The default mode is a dry-run. Real restore requires both `--execute` and
`--i-understand-this-will-change-target-db`, and the target database defaults to
`MOLD_RESTORE_DATABASE_URL`, not the primary application database.
"""
from __future__ import annotations

import argparse
import os
from pathlib import Path
import shutil
import subprocess
from urllib.parse import unquote, urlsplit

from dotenv import dotenv_values


def _configured(value: str | None) -> bool:
    return bool((value or "").strip())


def _parse_url(value: str, expected_db: str | None, allow_primary_target: bool) -> dict:
    parsed = urlsplit(value)
    if parsed.scheme.startswith("sqlite"):
        raise SystemExit("Refusing SQLite: MoldPilot restore rehearsals must target PostgreSQL.")
    if not parsed.scheme.startswith("postgresql"):
        raise SystemExit(f"Unsupported database scheme: {parsed.scheme}")
    database = parsed.path.lstrip("/")
    if expected_db and database != expected_db:
        raise SystemExit(f"Unexpected restore database {database!r}; expected {expected_db!r}.")
    if database == "moldpilot" and not allow_primary_target:
        raise SystemExit("Refusing to restore into primary database 'moldpilot'. Use an isolated restore database.")
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


def _run(command: list[str], password: str, execute: bool) -> int:
    env = os.environ.copy()
    if password:
        env["PGPASSWORD"] = password
    if not execute:
        return 0
    return subprocess.run(command, env=env, check=False).returncode


def _common_postgres_tool_paths(name: str) -> list[Path]:
    executable = name if name.endswith(".exe") else f"{name}.exe"
    roots = [Path("C:/Program Files/PostgreSQL"), Path("C:/Program Files (x86)/PostgreSQL"), Path("D:/PostgreSQL")]
    paths: list[Path] = []
    for root in roots:
        try:
            paths.extend(sorted(root.glob(f"*/bin/{executable}"), reverse=True))
        except OSError:
            continue
    return paths


def _find_tool(name: str, explicit_path: str = "") -> tuple[str, str | None]:
    if explicit_path.strip():
        path = Path(explicit_path.strip())
        return (str(path), "explicit") if path.exists() else ("", None)
    path = shutil.which(name)
    if path:
        return path, "PATH"
    for candidate in _common_postgres_tool_paths(name):
        if candidate.exists():
            return str(candidate), "common_install_dir"
    return "", None


def main() -> int:
    parser = argparse.ArgumentParser(description="Validate or restore a MoldPilot PostgreSQL pg_dump backup.")
    parser.add_argument("--env-file", default=".env", help="Path to local dotenv file. Defaults to .env.")
    parser.add_argument("--url-key", default="MOLD_RESTORE_DATABASE_URL", help="Dotenv key containing isolated restore PostgreSQL DSN.")
    parser.add_argument("--expected-db", default="moldpilot_restore", help="Expected restore database. Defaults to moldpilot_restore.")
    parser.add_argument("--backup", default="", help="Path to a pg_dump custom-format backup file.")
    parser.add_argument("--pg-restore", default="", help="Optional explicit pg_restore executable path.")
    parser.add_argument("--execute", action="store_true", help="Actually run pg_restore. Omit for dry-run.")
    parser.add_argument("--clean", action="store_true", help="Pass --clean --if-exists to pg_restore during execution.")
    parser.add_argument("--allow-primary-target", action="store_true", help="Allow restoring into database named moldpilot. Not recommended.")
    parser.add_argument("--i-understand-this-will-change-target-db", action="store_true", help="Required with --execute.")
    args = parser.parse_args()

    config = dotenv_values(args.env_file)
    url = config.get(args.url_key)
    if not _configured(url):
        print(f"{args.url_key}_configured=False")
        print("restore_ready=False")
        print("hint=Set MOLD_RESTORE_DATABASE_URL to an isolated PostgreSQL restore database.")
        return 0 if not args.execute else 2

    target = _parse_url(str(url), args.expected_db or None, args.allow_primary_target)
    pg_restore, pg_restore_source = _find_tool("pg_restore", args.pg_restore or str(config.get("MOLD_PG_RESTORE_PATH") or ""))
    if not pg_restore:
        print("pg_restore_available=False")
        print(f"database={target['database']}")
        print(f"host={target['host']}")
        print(f"port={target['port']}")
        print("restore_ready=False")
        print("hint=Install PostgreSQL client tools or pass --pg-restore with an explicit executable path.")
        return 0 if not args.execute else 2

    backup_path = Path(args.backup) if args.backup else None
    backup_exists = bool(backup_path and backup_path.exists() and backup_path.is_file())
    print("pg_restore_available=True")
    print(f"pg_restore_source={pg_restore_source}")
    print(f"database={target['database']}")
    print(f"host={target['host']}")
    print(f"port={target['port']}")
    print(f"backup_specified={bool(backup_path)}")
    print(f"backup_exists={backup_exists}")

    if backup_path and backup_exists:
        list_command = [str(pg_restore), "--list", str(backup_path)]
        list_result = subprocess.run(list_command, capture_output=True, text=True, check=False)
        print(f"backup_list_ok={list_result.returncode == 0}")
        if list_result.returncode != 0:
            return list_result.returncode

    if not args.execute:
        print("dry_run=True")
        print(f"restore_ready={backup_exists}")
        return 0
    if not args.i_understand_this_will_change_target_db:
        raise SystemExit("Execution requires --i-understand-this-will-change-target-db.")
    if not backup_path or not backup_exists:
        raise SystemExit("Execution requires an existing --backup file.")

    command = [
        str(pg_restore),
        "--no-owner",
        "--no-acl",
        "--host",
        target["host"],
        "--port",
        target["port"],
        "--username",
        target["username"],
        "--dbname",
        target["database"],
    ]
    if args.clean:
        command.extend(["--clean", "--if-exists"])
    command.append(str(backup_path))
    code = _run(command, target["password"], execute=True)
    if code == 0:
        print("restore_completed=True")
    return code


if __name__ == "__main__":
    raise SystemExit(main())
