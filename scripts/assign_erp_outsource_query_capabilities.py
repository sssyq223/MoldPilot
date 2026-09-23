"""Assign registered outsource query Tools/Skills to buyer and supervisor accounts."""
from __future__ import annotations

import os
import sys
from pathlib import Path

from dotenv import dotenv_values
from sqlalchemy import select
from sqlalchemy.orm import sessionmaker

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))

from app.db import make_engine
from app.models import Capability, User
from domain_packs.mold.tools.erp.procurement.erp_outsource_query_tools import (
    RETIRED_TOOLS,
    SKILL_SPECS,
    TOOL_SPECS,
)

TARGETS = ("xuguili", "wangqun")
RETIRED_SKILLS = (
    "buyer_todo_station_query",
    "unoutsourced_part_query",
    "outsource_fulfillment_gap",
    "outsource_timeline_query",
    "outsource_quote_compare",
)


def _database_url() -> str:
    if os.environ.get("AGENT_DATABASE_URL"):
        return os.environ["AGENT_DATABASE_URL"]
    values = dotenv_values(Path(__file__).resolve().parents[1] / ".env")
    return values.get("AGENT_DATABASE_URL") or values.get("MOLD_DATABASE_URL") or ""


def _ensure_capability(db, user: User, kind: str, key: str) -> bool:
    row = db.scalar(
        select(Capability).where(
            Capability.user_id == user.id,
            Capability.kind == kind,
            Capability.key == key,
        )
    )
    if row is None:
        db.add(Capability(user_id=user.id, kind=kind, key=key, enabled=True))
        return True
    if not row.enabled:
        row.enabled = True
        return True
    return False


def main() -> None:
    url = _database_url()
    if not url:
        raise SystemExit("Database URL is required.")
    engine = make_engine(url)
    Session = sessionmaker(bind=engine, autoflush=False, autocommit=False, future=True)
    with Session.begin() as db:
        for username in TARGETS:
            user = db.scalar(select(User).where(User.username == username))
            if user is None:
                print(f"skip missing user: {username}")
                continue
            changed = False
            for key in TOOL_SPECS:
                changed = _ensure_capability(db, user, "TOOL", key) or changed
            for key in SKILL_SPECS:
                changed = _ensure_capability(db, user, "SKILL", key) or changed
            for key in (*RETIRED_SKILLS, *RETIRED_TOOLS):
                kind = "TOOL" if key in RETIRED_TOOLS else "SKILL"
                row = db.scalar(
                    select(Capability).where(
                        Capability.user_id == user.id,
                        Capability.kind == kind,
                        Capability.key == key,
                    )
                )
                if row is not None and row.enabled:
                    row.enabled = False
                    changed = True
            if changed:
                user.security_version += 1
            print(f"{username}: capabilities={'updated' if changed else 'already assigned'}")


if __name__ == "__main__":
    main()
