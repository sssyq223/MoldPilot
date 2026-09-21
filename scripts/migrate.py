"""Run ordered Core and business-pack Alembic repositories."""
from __future__ import annotations

import argparse
import os
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))

from alembic import command
from dotenv import dotenv_values

from agent_core.migration_runtime import (
    alembic_config,
    alembic_configs,
    check_all,
    downgrade_all,
    startup_database,
    upgrade_all,
    verify_runtime_database,
)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "command", choices=("upgrade", "downgrade", "current", "heads", "check", "startup", "verify")
    )
    parser.add_argument("revision", nargs="?", default="head")
    parser.add_argument("--stage", help="Run one named stage instead of the complete ordered chain")
    args = parser.parse_args()
    if args.command in {"startup", "verify"}:
        if args.stage or args.revision != "head":
            parser.error("startup/verify checks the complete runtime; do not specify a stage or revision")
        from app.config import settings

        if args.command == "verify":
            verify_runtime_database(settings().database_url)
        else:
            environment = dotenv_values(".env")
            mode = os.environ.get(
                "AGENT_STARTUP_MIGRATIONS", environment.get("AGENT_STARTUP_MIGRATIONS", "upgrade")
            )
            startup_database(mode, settings().database_url)
        return 0
    if args.stage:
        config = alembic_config(stage=args.stage)
        if args.command == "upgrade":
            command.upgrade(config, args.revision)
        elif args.command == "downgrade":
            command.downgrade(config, args.revision)
        elif args.command == "current":
            command.current(config)
        elif args.command == "heads":
            command.heads(config)
        else:
            command.check(config)
        return 0

    if args.command == "upgrade":
        if args.revision != "head":
            parser.error("multi-stage upgrade accepts only head; use --stage for a revision")
        upgrade_all(args.revision)
    elif args.command == "downgrade":
        if args.revision == "head":
            args.revision = "base"
        if args.revision != "base":
            parser.error("multi-stage downgrade accepts only base; use --stage for a revision")
        downgrade_all(args.revision)
    elif args.command in {"current", "heads"}:
        for stage, config in alembic_configs():
            print(f"[{stage.name}] {stage.version_table}")
            getattr(command, args.command)(config)
    else:
        check_all()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
