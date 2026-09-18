"""Run ordered Core and business-pack Alembic repositories."""
from __future__ import annotations

import argparse
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))

from alembic import command

from agent_core.migration_runtime import (
    alembic_config,
    alembic_configs,
    check_all,
    downgrade_all,
    upgrade_all,
)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("upgrade", "downgrade", "current", "heads", "check"))
    parser.add_argument("revision", nargs="?", default="head")
    parser.add_argument("--stage", help="Run one named stage instead of the complete ordered chain")
    args = parser.parse_args()
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
