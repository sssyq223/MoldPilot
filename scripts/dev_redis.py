"""Manage the local MoldPilot Redis development dependency.

Default command is read-only status. Mutating commands require `--execute` so a
readiness check cannot accidentally start containers or create Redis streams.
"""
from __future__ import annotations

import argparse
from pathlib import Path
import socket
import subprocess
import sys
from urllib.parse import urlsplit

from dotenv import dotenv_values
from redis import Redis
from redis.exceptions import ResponseError


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from app.message_worker import GROUP, STREAM

COMPOSE_FILE = ROOT / "docker-compose.redis.yml"
DEFAULT_REDIS_HOME = Path(r"D:\Redis")


def _redis_url(env_file: str) -> str:
    values = dotenv_values(env_file)
    return values.get("AGENT_REDIS_URL") or values.get("MOLD_REDIS_URL") or "redis://127.0.0.1:6379/0"


def _redis_home(env_file: str) -> Path:
    values = dotenv_values(env_file)
    configured = values.get("AGENT_REDIS_HOME") or values.get("MOLD_REDIS_HOME")
    return Path(configured) if configured else DEFAULT_REDIS_HOME


def _endpoint(url: str) -> tuple[str, int]:
    parsed = urlsplit(url)
    return parsed.hostname or "127.0.0.1", parsed.port or 6379


def _command(command: list[str], timeout: float = 5.0) -> dict:
    try:
        completed = subprocess.run(command, capture_output=True, text=True, timeout=timeout, check=False)
        output = (completed.stdout or completed.stderr or "").strip().splitlines()
        return {"ok": completed.returncode == 0, "returncode": completed.returncode, "summary": output[0][:180] if output else ""}
    except Exception as error:  # pragma: no cover - host dependent
        return {"ok": False, "error_type": type(error).__name__}


def _port_open(host: str, port: int) -> bool:
    try:
        with socket.create_connection((host, port), timeout=1):
            return True
    except OSError:
        return False


def _redis_probe(url: str) -> dict:
    try:
        client = Redis.from_url(url, decode_responses=True, socket_connect_timeout=1, socket_timeout=1, protocol=2)
        try:
            client.ping()
            try:
                groups = [item.get("name") for item in client.xinfo_groups(STREAM) if item.get("name")]
                stream_exists = True
            except ResponseError:
                groups = []
                stream_exists = False
            return {"reachable": True, "stream_exists": stream_exists, "group_ready": GROUP in groups, "groups": groups}
        finally:
            client.close()
    except Exception as error:
        return {"reachable": False, "error_type": type(error).__name__}


def status(args) -> int:
    url = _redis_url(args.env_file)
    host, port = _endpoint(url)
    redis_home = _redis_home(args.env_file)
    redis_server = redis_home / "redis-server.exe"
    redis_conf = redis_home / "redis.windows-service.conf"
    print(f"redis_url_configured={bool(url)}")
    print(f"host={host}")
    print(f"port={port}")
    print(f"native_redis_home={redis_home}")
    print(f"native_redis_server_exists={redis_server.exists()}")
    print(f"native_redis_config_exists={redis_conf.exists()}")
    print(f"compose_file_exists={COMPOSE_FILE.exists()}")
    print(f"port_open={_port_open(host, port)}")
    if args.backend in {"docker", "auto"}:
        docker_version = _command(["docker", "--version"])
        docker_info = _command(["docker", "info", "--format", "{{.ServerVersion}}"])
        print(f"docker_cli_ok={docker_version['ok']}")
        print(f"docker_daemon_ok={docker_info['ok']}")
        if not docker_info["ok"] and docker_info.get("summary"):
            print(f"docker_daemon_summary={docker_info['summary']}")
    probe = _redis_probe(url)
    print(f"redis_reachable={probe.get('reachable')}")
    if probe.get("error_type"):
        print(f"redis_error_type={probe['error_type']}")
    print(f"stream_exists={probe.get('stream_exists')}")
    print(f"group_ready={probe.get('group_ready')}")
    return 0


def _start_native(args) -> int:
    url = _redis_url(args.env_file)
    host, port = _endpoint(url)
    redis_home = _redis_home(args.env_file)
    redis_server = redis_home / "redis-server.exe"
    redis_conf = redis_home / "redis.windows-service.conf"
    command = [str(redis_server), str(redis_conf)] if redis_conf.exists() else [str(redis_server)]
    if not args.execute:
        print("dry_run=True")
        print("backend=native")
        print(f"command={' '.join(command)}")
        print("hint=Pass --execute to start the native Redis process.")
        return 0
    if _port_open(host, port):
        print("started=False")
        print("already_running=True")
        return 0
    if not redis_server.exists():
        print("started=False")
        print(f"missing_redis_server={redis_server}")
        print("hint=Set AGENT_REDIS_HOME to the folder containing redis-server.exe, or use --backend docker.")
        return 2
    creationflags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    subprocess.Popen(command, cwd=str(redis_home), stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, creationflags=creationflags)
    print("started=True")
    print("backend=native")
    return 0


def _start_docker(args) -> int:
    if not args.execute:
        print("dry_run=True")
        print("backend=docker")
        print(f"command=docker compose -f {COMPOSE_FILE} up -d redis")
        print("hint=Pass --execute to start the optional Redis container.")
        return 0
    command = ["docker", "compose", "-f", str(COMPOSE_FILE), "up", "-d", "redis"]
    result = _command(command, timeout=120)
    print(f"started={result['ok']}")
    print("backend=docker")
    if result.get("summary"):
        print(f"summary={result['summary']}")
    return 0 if result["ok"] else result.get("returncode", 1)


def start(args) -> int:
    if args.backend == "docker":
        return _start_docker(args)
    return _start_native(args)


def init_stream(args) -> int:
    url = _redis_url(args.env_file)
    if not args.execute:
        print("dry_run=True")
        print(f"stream={STREAM}")
        print(f"group={GROUP}")
        print("hint=Pass --execute to create the stream/group in local Redis.")
        return 0
    try:
        client = Redis.from_url(url, decode_responses=True, socket_connect_timeout=2, socket_timeout=3, protocol=2)
        try:
            try:
                client.xgroup_create(STREAM, GROUP, id="0", mkstream=True)
                print("stream_group_created=True")
            except ResponseError as error:
                if "BUSYGROUP" not in str(error):
                    raise
                print("stream_group_created=False")
                print("stream_group_already_exists=True")
        finally:
            client.close()
    except Exception as error:
        print("stream_group_created=False")
        print(f"redis_error_type={type(error).__name__}")
        print("hint=Start native Redis first with: scripts/dev_redis.py start --execute")
        return 2
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--env-file", default=".env")
    parser.add_argument("--backend", choices=["native", "docker", "auto"], default="native", help="Redis runtime to inspect/start. Native uses AGENT_REDIS_HOME, default D:\\Redis.")
    sub = parser.add_subparsers(dest="command")
    sub.add_parser("status")
    start_parser = sub.add_parser("start")
    start_parser.add_argument("--execute", action="store_true")
    init_parser = sub.add_parser("init-stream")
    init_parser.add_argument("--execute", action="store_true")
    args = parser.parse_args()
    if args.command == "start":
        return start(args)
    if args.command == "init-stream":
        return init_stream(args)
    return status(args)


if __name__ == "__main__":
    raise SystemExit(main())
