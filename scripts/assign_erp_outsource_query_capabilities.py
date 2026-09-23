"""Assign outsource Skills/Tools by role: buyer, supervisor, processor."""
from __future__ import annotations

import os
import sys
from pathlib import Path

from dotenv import dotenv_values
from sqlalchemy import func, select
from sqlalchemy.orm import sessionmaker

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))

from app.db import make_engine
from app.models import Capability, User
from domain_packs.mold.tools.erp.procurement.erp_outsource_approval_tools import (
    SKILL_SPECS as APPROVAL_SKILL_SPECS,
    TOOL_SPECS as APPROVAL_TOOL_SPECS,
)
from domain_packs.mold.tools.erp.procurement.erp_outsource_buyer_tools import (
    SKILL_SPECS as BUYER_SKILL_SPECS,
    TOOL_SPECS as BUYER_TOOL_SPECS,
)
from domain_packs.mold.tools.erp.procurement.erp_outsource_processor_fulfillment_tools import (
    SKILL_SPECS as PROCESSOR_FULFILLMENT_SKILL_SPECS,
    TOOL_SPECS as PROCESSOR_FULFILLMENT_TOOL_SPECS,
)
from domain_packs.mold.tools.erp.procurement.erp_outsource_processor_ship_tools import (
    SKILL_SPECS as PROCESSOR_SHIP_SKILL_SPECS,
    TOOL_SPECS as PROCESSOR_SHIP_TOOL_SPECS,
)
from domain_packs.mold.tools.erp.procurement.erp_outsource_processor_tools import (
    SKILL_SPECS as PROCESSOR_SKILL_SPECS,
    TOOL_SPECS as PROCESSOR_TOOL_SPECS,
)
from domain_packs.mold.tools.erp.procurement.erp_outsource_quality_tools import (
    SKILL_SPECS as QUALITY_SKILL_SPECS,
    TOOL_SPECS as QUALITY_TOOL_SPECS,
)
from domain_packs.mold.tools.erp.procurement.erp_outsource_warehouse_inbound_tools import (
    SKILL_SPECS as WAREHOUSE_INBOUND_SKILL_SPECS,
    TOOL_SPECS as WAREHOUSE_INBOUND_TOOL_SPECS,
)
from domain_packs.mold.tools.erp.procurement.erp_outsource_warehouse_tools import (
    SKILL_SPECS as WAREHOUSE_SKILL_SPECS,
    TOOL_SPECS as WAREHOUSE_TOOL_SPECS,
)
from domain_packs.mold.tools.erp.procurement.erp_outsource_query_tools import (
    BUYER_TOOL_KEYS,
    PROCESSOR_TOOL_KEYS,
    RETIRED_TOOLS,
    SKILL_SPECS as QUERY_SKILL_SPECS,
)

RETIRED_SKILLS = (
    "buyer_todo_station_query",
    "unoutsourced_part_query",
    "outsource_fulfillment_gap",
    "outsource_timeline_query",
    "outsource_quote_compare",
)

ROLE_CAPS = {
    "xuguili": {
        "skills": ("outsource_followup_query", "outsource_buyer_ops"),
        "tools": (*BUYER_TOOL_KEYS, *BUYER_TOOL_SPECS),
    },
    "wangqun": {
        "skills": ("outsource_followup_query", "outsource_approval_ops"),
        "tools": (*BUYER_TOOL_KEYS, *APPROVAL_TOOL_SPECS),
    },
    "lihui": {
        "skills": ("outsource_followup_query", "outsource_approval_ops"),
        "tools": (*BUYER_TOOL_KEYS, *APPROVAL_TOOL_SPECS),
    },
    "SUP000001": {
        "skills": (
            "outsource_processor_query", "outsource_processor_ops",
            "outsource_processor_fulfillment", "outsource_processor_product_ship",
        ),
        "tools": (
            *PROCESSOR_TOOL_KEYS, *PROCESSOR_TOOL_SPECS,
            *PROCESSOR_FULFILLMENT_TOOL_SPECS, *PROCESSOR_SHIP_TOOL_SPECS,
        ),
    },
    "xuehaifeng": {
        "skills": ("outsource_warehouse_ops", "outsource_warehouse_inbound"),
        "tools": (*WAREHOUSE_TOOL_SPECS, *WAREHOUSE_INBOUND_TOOL_SPECS),
    },
    "zhaodianye": {
        "skills": ("outsource_quality_ops",),
        "tools": tuple(QUALITY_TOOL_SPECS),
    },
}

ALL_SKILLS = (
    set(QUERY_SKILL_SPECS) | set(BUYER_SKILL_SPECS) | set(APPROVAL_SKILL_SPECS)
    | set(PROCESSOR_SKILL_SPECS) | set(PROCESSOR_FULFILLMENT_SKILL_SPECS)
    | set(PROCESSOR_SHIP_SKILL_SPECS)
    | set(WAREHOUSE_SKILL_SPECS) | set(WAREHOUSE_INBOUND_SKILL_SPECS)
    | set(QUALITY_SKILL_SPECS) | set(RETIRED_SKILLS)
)
ALL_TOOLS = (
    set(BUYER_TOOL_KEYS) | set(PROCESSOR_TOOL_KEYS) | set(BUYER_TOOL_SPECS)
    | set(APPROVAL_TOOL_SPECS) | set(PROCESSOR_TOOL_SPECS)
    | set(PROCESSOR_FULFILLMENT_TOOL_SPECS) | set(PROCESSOR_SHIP_TOOL_SPECS)
    | set(WAREHOUSE_TOOL_SPECS) | set(WAREHOUSE_INBOUND_TOOL_SPECS)
    | set(QUALITY_TOOL_SPECS)
    | set(RETIRED_TOOLS)
)


def _database_url() -> str:
    if os.environ.get("AGENT_DATABASE_URL"):
        return os.environ["AGENT_DATABASE_URL"]
    values = dotenv_values(Path(__file__).resolve().parents[1] / ".env")
    return values.get("AGENT_DATABASE_URL") or values.get("MOLD_DATABASE_URL") or ""


def _set_capability(db, user: User, kind: str, key: str, enabled: bool) -> bool:
    row = db.scalar(
        select(Capability).where(
            Capability.user_id == user.id,
            Capability.kind == kind,
            Capability.key == key,
        )
    )
    if row is None:
        if not enabled:
            return False
        db.add(Capability(user_id=user.id, kind=kind, key=key, enabled=True))
        return True
    if row.enabled != enabled:
        row.enabled = enabled
        return True
    return False


def main() -> None:
    url = _database_url()
    if not url:
        raise SystemExit("Database URL is required.")
    engine = make_engine(url)
    Session = sessionmaker(bind=engine, autoflush=False, autocommit=False, future=True)
    with Session.begin() as db:
        for username, caps in ROLE_CAPS.items():
            user = db.scalar(select(User).where(func.lower(User.username) == username.lower()))
            if user is None:
                print(f"skip missing user: {username}")
                continue
            want_skills = set(caps["skills"])
            want_tools = set(caps["tools"])
            changed = False
            for key in ALL_TOOLS:
                changed = _set_capability(db, user, "TOOL", key, key in want_tools) or changed
            for key in ALL_SKILLS:
                changed = _set_capability(db, user, "SKILL", key, key in want_skills) or changed
            if changed:
                user.security_version += 1
            print(f"{username}: capabilities={'updated' if changed else 'already assigned'}")


if __name__ == "__main__":
    main()
