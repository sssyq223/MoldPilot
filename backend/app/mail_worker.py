"""Standalone mailbox worker for the MoldPilot domain pack."""

from __future__ import annotations

import argparse
import logging
import os
import time
import uuid
from collections import defaultdict
from pathlib import Path

from sqlalchemy import select

from domain_packs.mold import models as m
from domain_packs.mold.mail.ledger import SqlAlchemyMailLedger
from domain_packs.mold.mail.monitor import MailMonitor, MailMonitorConfig, MailRouteConfig
from domain_packs.mold.mail.rules import (
    DEFAULT_MAIL_ROUTE_MATCHERS,
    DEFAULT_MAIL_ROUTE_PLAN,
)

from .db import SessionLocal

log = logging.getLogger("moldpilot.mail_worker")
_MAIL_ENV_LOADED = False
SENT_FOLDER_ALIASES = {"SENT", "&XfJT0ZAB-"}


def load_mail_env_file() -> None:
    """Load the untracked project-root mail secret file once.

    Explicit process environment variables win over the local file.  Values
    are never logged or returned; this only bridges deployment configuration
    to the existing ``env://`` secret references.
    """
    global _MAIL_ENV_LOADED
    if _MAIL_ENV_LOADED:
        return
    _MAIL_ENV_LOADED = True
    path = Path(__file__).resolve().parents[2] / ".env.mail.local"
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except (FileNotFoundError, OSError, UnicodeError):
        return
    for raw in lines:
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip()
        if not key or not key.replace("_", "").isalnum():
            continue
        if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
            value = value[1:-1]
        os.environ.setdefault(key, value)


def resolve_secret(secret_ref: str) -> str:
    """Resolve only explicit env references; deployments can inject a vault adapter."""
    load_mail_env_file()
    if secret_ref.startswith("env://"):
        return os.environ.get(secret_ref.removeprefix("env://"), "")
    return ""


def enabled_accounts(db, resolver=resolve_secret):
    for row in db.scalars(select(m.MailMonitorAccount).where(m.MailMonitorAccount.enabled.is_(True))):
        password = resolver(row.secret_ref)
        if not password:
            row.status = "CONFIG_ERROR"
            row.last_error = "secret_ref 未解析到密码；未建立 IMAP 连接"
            continue
        routes = list(db.scalars(select(m.MailMonitorRoute).where(
            m.MailMonitorRoute.account_id == row.id,
            m.MailMonitorRoute.enabled.is_(True),
        ).order_by(m.MailMonitorRoute.folder, m.MailMonitorRoute.priority, m.MailMonitorRoute.name)))
        grouped: dict[str, list[m.MailMonitorRoute]] = defaultdict(list)
        for route in routes:
            grouped[route.folder].append(route)
        if not grouped:
            defaults = [item for item in DEFAULT_MAIL_ROUTE_PLAN if item[0] == row.username.lower()]
            if defaults:
                for username, folder, category in defaults:
                    grouped[folder].append((username, folder, category))
            else:
                grouped[row.folder].append(None)
        for folder, folder_routes in grouped.items():
            route_configs = tuple(
                MailRouteConfig(
                    route_id=route.id if hasattr(route, "id") else "",
                    name=route.name if hasattr(route, "name") else f"default-{folder.lower()}-{category.lower()}",
                    folder=route.folder if hasattr(route, "folder") else folder,
                    direction=route.direction if hasattr(route, "direction") else ("SENT" if folder.upper() in SENT_FOLDER_ALIASES else "INBOX"),
                    category=route.category if hasattr(route, "category") else category,
                    priority=route.priority if hasattr(route, "priority") else 100,
                    matcher=(dict(route.matcher or {}) if hasattr(route, "matcher") else
                             dict(DEFAULT_MAIL_ROUTE_MATCHERS.get((row.username.lower(), folder, category), {}))),
                    allowed_senders=tuple((route.matcher or {}).get("allowed_senders") or ()) if hasattr(route, "matcher") else (),
                    notify_inbox=bool(route.notify_inbox) if hasattr(route, "notify_inbox") else folder.upper() == "INBOX",
                    archive=bool(route.archive) if hasattr(route, "archive") else True,
                )
                for route in folder_routes
                if route is not None
                for category in ([route.category] if hasattr(route, "category") else [route[2]])
            )
            route_senders = tuple(
                sender
                for item in route_configs
                for sender in item.allowed_senders
            )
            yield MailMonitorConfig(
                account_id=row.id,
                account_name=row.name,
                host=row.host,
                port=row.port,
                username=row.username,
                password=password,
                folder=folder,
                transport=row.transport,
                allowed_senders=tuple(dict.fromkeys(tuple(row.allowed_senders or ()) + route_senders)),
                keywords={key: tuple(value) for key, value in (row.keywords or {}).items()},
                routes=route_configs,
            )


def run_once(*, resolver=resolve_secret, archive_root=None, client_factory=None, owner=None):
    owner = owner or f"mail-worker-{uuid.uuid4()}"
    with SessionLocal.begin() as db:
        ledger = SqlAlchemyMailLedger(db)
        configs = list(enabled_accounts(db, resolver))
        results = {}
        for config in configs:
            account = db.get(m.MailMonitorAccount, config.account_id)
            if not ledger.try_acquire_lease(config.account_id, owner, folder=config.folder):
                continue
            try:
                result = MailMonitor(
                    config, ledger, client_factory=client_factory, archive_root=archive_root
                ).poll_once()
                account.status = "HEALTHY"
                account.last_error = ""
                key = config.account_id if config.folder == "INBOX" else f"{config.account_id}:{config.folder}"
                results[key] = result
            except Exception as exc:
                account.status = "ERROR"
                account.last_error = str(exc)[:1000]
                log.warning("mail account poll failed: %s", type(exc).__name__)
            finally:
                ledger.release_lease(config.account_id, owner, config.folder)
        return results


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--interval", type=int, default=30, help="poll interval in seconds")
    args = parser.parse_args()
    owner = f"mail-worker-{uuid.uuid4()}"
    log.info("mail worker started: %s", owner)
    while True:
        started = time.monotonic()
        try:
            run_once()
        except Exception as exc:
            log.warning("mail worker cycle failed: %s", type(exc).__name__)
        time.sleep(max(1, args.interval - int(time.monotonic() - started)))


if __name__ == "__main__":
    main()
