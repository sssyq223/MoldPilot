"""Resolve Alembic configuration without embedding an industry pack in tools."""
import os
from pathlib import Path
from typing import Mapping

from alembic.config import Config

from .domain_pack import active_pack_name, migration_contract


REPO_ROOT = Path(__file__).resolve().parents[2]


def resolve_migration_url(dotenv: Mapping[str, str | None]) -> str:
    """Resolve a URL while keeping process-level test overrides authoritative."""
    url = (
        os.environ.get("AGENT_MIGRATION_URL")
        or os.environ.get("MOLD_MIGRATION_URL")
        or dotenv.get("AGENT_MIGRATION_URL")
        or dotenv.get("MOLD_MIGRATION_URL")
    )
    if not url:
        raise RuntimeError(
            "AGENT_MIGRATION_URL is required; MOLD_MIGRATION_URL remains a legacy alias"
        )
    return url


def alembic_config(repo_root: Path | None = None) -> Config:
    root = (repo_root or REPO_ROOT).resolve()
    contract = migration_contract()
    path = (root / contract.ALEMBIC_CONFIG).resolve()
    if (path != root and root not in path.parents) or not path.is_file():
        raise RuntimeError("Active domain pack selected an unavailable Alembic config")
    config = Config(str(path))
    script_location = (root / config.get_main_option("script_location")).resolve()
    if (script_location != root and root not in script_location.parents) or not script_location.is_dir():
        raise RuntimeError("Active domain pack selected an unavailable migration repository")
    config.set_main_option("script_location", str(script_location))
    config.attributes["active_pack"] = active_pack_name()
    config.attributes["version_table"] = contract.VERSION_TABLE
    return config


def version_table() -> str:
    return migration_contract().VERSION_TABLE
