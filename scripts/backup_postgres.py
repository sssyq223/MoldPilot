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


def _config_value(config: dict, key: str, default: str = "") -> str:
    legacy = "MOLD_" + key.removeprefix("AGENT_") if key.startswith("AGENT_") else ""
    return os.environ.get(key) or str(config.get(key) or os.environ.get(legacy) or config.get(legacy) or default)


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


def _docker_probe() -> dict:
    docker = shutil.which("docker")
    if not docker:
        return {"available": False, "cli": False, "daemon": False}
    result = subprocess.run([docker, "info", "--format", "{{.ServerVersion}}"], capture_output=True, text=True, check=False, timeout=5)
    stderr = (result.stderr or "").lower()
    stdout = (result.stdout or "").strip()
    combined = f"{stdout}\n{stderr}".lower()
    ok = result.returncode == 0 and "error during connect" not in combined and "internal server error" not in combined and bool(stdout)
    return {
        "available": ok,
        "cli": True,
        "daemon": ok,
        "server_version": stdout if ok else "",
    }


def _container_host(host: str) -> str:
    return "host.docker.internal" if host in {"127.0.0.1", "localhost"} else host


def _native_command(pg_dump: str, database: dict, output_file: Path) -> list[str]:
    return [
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


def _docker_command(image: str, database: dict, output_dir: Path, output_file: Path) -> list[str]:
    docker = shutil.which("docker") or "docker"
    return [
        docker,
        "run",
        "--rm",
        "-e",
        "PGPASSWORD",
        "-v",
        f"{output_dir.resolve()}:/backup",
        image,
        "pg_dump",
        "--format=custom",
        "--no-owner",
        "--no-acl",
        "--file",
        f"/backup/{output_file.name}",
        "--host",
        _container_host(database["host"]),
        "--port",
        database["port"],
        "--username",
        database["username"],
        database["database"],
    ]


def main() -> int:
    parser = argparse.ArgumentParser(description="Create a MoldPilot PostgreSQL pg_dump backup.")
    parser.add_argument("--env-file", default=".env", help="Path to local dotenv file. Defaults to .env.")
    parser.add_argument("--url-key", default="AGENT_DATABASE_URL", help="Dotenv key containing PostgreSQL DSN.")
    parser.add_argument("--expected-db", default="moldpilot", help="Expected database name. Defaults to moldpilot.")
    parser.add_argument("--output-dir", default=".local/backups", help="Backup directory. Defaults to .local/backups.")
    parser.add_argument("--pg-dump", default="", help="Optional explicit pg_dump executable path.")
    parser.add_argument("--client-mode", choices=["auto", "native", "docker"], default="auto", help="PostgreSQL client mode. Defaults to auto.")
    parser.add_argument("--docker-image", default="", help="Docker image containing pg_dump. Defaults to AGENT_PG_CLIENT_IMAGE or postgres:18-alpine.")
    parser.add_argument("--dry-run", action="store_true", help="Validate configuration and print the backup target only.")
    args = parser.parse_args()

    config = dotenv_values(args.env_file)
    url = _config_value(config, args.url_key)
    if not _configured(url):
        raise SystemExit(f"{args.url_key} is missing in {args.env_file}")
    database = _parse_url(str(url), args.expected_db)

    pg_dump, pg_dump_source = _find_tool("pg_dump", args.pg_dump or _config_value(config, "AGENT_PG_DUMP_PATH"))
    docker = _docker_probe()
    image = args.docker_image or _config_value(config, "AGENT_PG_CLIENT_IMAGE", "postgres:18-alpine")
    client_mode = "native" if pg_dump and args.client_mode in {"auto", "native"} else ""
    if not client_mode and args.client_mode in {"auto", "docker"} and docker["available"]:
        client_mode = "docker"
    if not client_mode:
        print("pg_dump_available=False")
        print(f"docker_client_available={docker['available']}")
        print(f"database={database['database']}")
        print(f"host={database['host']}")
        print(f"port={database['port']}")
        print("backup_ready=False")
        print("hint=Install PostgreSQL client tools, pass --pg-dump, or start Docker and use --client-mode docker.")
        return 0 if args.dry_run else 2

    output_dir = Path(args.output_dir)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    output_file = output_dir / f"{_safe_name(database['database'])}_{timestamp}.dump"
    command = _native_command(pg_dump, database, output_file) if client_mode == "native" else _docker_command(image, database, output_dir, output_file)

    print(f"pg_dump_available={bool(pg_dump)}")
    print(f"pg_dump_source={pg_dump_source or ''}")
    print(f"docker_client_available={docker['available']}")
    print(f"client_mode={client_mode}")
    if client_mode == "docker":
        print(f"docker_image={image}")
        print(f"container_host={_container_host(database['host'])}")
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

