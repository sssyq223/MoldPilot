"""Run the Alembic repository selected by AGENT_BUSINESS_PACK."""
from __future__ import annotations

import argparse
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))

from alembic import command

from agent_core.migration_runtime import alembic_config


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("upgrade", "downgrade", "current", "heads", "check"))
    parser.add_argument("revision", nargs="?", default="head")
    args = parser.parse_args()
    config = alembic_config()
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


if __name__ == "__main__":
    raise SystemExit(main())
