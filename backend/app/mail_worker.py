"""Standalone mailbox worker for the MoldPilot domain pack.

It is deliberately separate from the agent/message worker: IMAP polling has a
different lease, retry and size budget, and never runs in an API request.
"""

from __future__ import annotations

import argparse
import logging
import os
import time
import uuid

from sqlalchemy import select

from domain_packs.mold import models as m
from domain_packs.mold.mail.ledger import SqlAlchemyMailLedger
from domain_packs.mold.mail.monitor import MailMonitor, MailMonitorConfig

from .db import SessionLocal, now

log = logging.getLogger("moldpilot.mail_worker")


def resolve_secret(secret_ref: str) -> str:
    """Resolve only explicit env references; deployments can inject a vault adapter."""
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
        yield MailMonitorConfig(
            account_id=row.id, account_name=row.name, host=row.host, port=row.port,
            username=row.username, password=password, folder=row.folder, transport=row.transport,
            allowed_senders=tuple(row.allowed_senders or ()),
            keywords={key: tuple(value) for key, value in (row.keywords or {}).items()},
        )


def run_once(*, resolver=resolve_secret, archive_root=None, client_factory=None, owner=None):
    owner = owner or f"mail-worker-{uuid.uuid4()}"
    with SessionLocal.begin() as db:
        ledger = SqlAlchemyMailLedger(db)
        configs = list(enabled_accounts(db, resolver))
        results = {}
        for config in configs:
            account = db.get(m.MailMonitorAccount, config.account_id)
            if not ledger.try_acquire_lease(config.account_id, owner):
                continue
            try:
                result = MailMonitor(config, ledger, client_factory=client_factory, archive_root=archive_root).poll_once()
                account.status = "HEALTHY"
                account.last_error = ""
                results[config.account_id] = result
            except Exception as exc:
                account.status = "ERROR"
                account.last_error = str(exc)[:1000]
                log.warning("mail account poll failed: %s", type(exc).__name__)
            finally:
                ledger.release_lease(config.account_id, owner)
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
