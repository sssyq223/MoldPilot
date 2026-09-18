"""Run ordered Core and replaceable domain-pack Alembic repositories."""
from __future__ import annotations

from dataclasses import dataclass
import os
from pathlib import Path
from typing import Mapping

from alembic import command
from alembic.config import Config
from dotenv import dotenv_values
from sqlalchemy import create_engine, inspect

from .domain_pack import active_pack_name, migration_contract


REPO_ROOT = Path(__file__).resolve().parents[2]


@dataclass(frozen=True)
class MigrationStage:
    name: str
    config_file: str
    version_table: str


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


def migration_stages() -> tuple[MigrationStage, ...]:
    contract = migration_contract()
    return tuple(MigrationStage(
        name=stage["name"],
        config_file=stage["config"],
        version_table=stage["version_table"],
    ) for stage in contract.STAGES)


def _config(config_file: str, version_table: str, root: Path) -> Config:
    path = (root / config_file).resolve()
    if (path != root and root not in path.parents) or not path.is_file():
        raise RuntimeError("Active domain pack selected an unavailable Alembic config")
    config = Config(str(path))
    script_location = (root / config.get_main_option("script_location")).resolve()
    if ((script_location != root and root not in script_location.parents)
            or not script_location.is_dir()):
        raise RuntimeError("Active domain pack selected an unavailable migration repository")
    config.set_main_option("script_location", str(script_location))
    config.attributes["active_pack"] = active_pack_name()
    config.attributes["version_table"] = version_table
    config.attributes["config_file"] = config_file
    return config


def alembic_configs(repo_root: Path | None = None) -> tuple[tuple[MigrationStage, Config], ...]:
    root = (repo_root or REPO_ROOT).resolve()
    return tuple(
        (stage, _config(stage.config_file, stage.version_table, root))
        for stage in migration_stages()
    )


def alembic_config(repo_root: Path | None = None, stage: str | None = None) -> Config:
    """Return one named stage; single-stage packs may omit ``stage``."""
    configs = alembic_configs(repo_root)
    if stage is None:
        if len(configs) != 1:
            raise RuntimeError("Active business pack has multiple migration stages; name one")
        return configs[0][1]
    for descriptor, config in configs:
        if descriptor.name == stage:
            return config
    raise RuntimeError(f"Unknown migration stage: {stage}")


def legacy_alembic_config(repo_root: Path | None = None) -> Config | None:
    contract = migration_contract()
    legacy = getattr(contract, "LEGACY", None)
    if not legacy:
        return None
    root = (repo_root or REPO_ROOT).resolve()
    return _config(legacy["config"], legacy["version_table"], root)


def version_tables() -> tuple[str, ...]:
    return tuple(stage.version_table for stage in migration_stages())


def _installed_version_tables() -> set[str]:
    url = resolve_migration_url(dotenv_values(REPO_ROOT / ".env"))
    engine = create_engine(url)
    try:
        names = set(inspect(engine).get_table_names())
    finally:
        engine.dispose()
    return names


def _adopt_legacy_database(repo_root: Path | None = None) -> bool:
    """Upgrade and verify a legacy mold schema, then attach split version heads.

    No business row is copied or rewritten. Stamping Core is allowed only
    after the legacy repository reports zero model drift; the domain baseline
    independently verifies that every frozen domain table already exists.
    """
    legacy = legacy_alembic_config(repo_root)
    if legacy is None:
        return False
    installed = _installed_version_tables()
    legacy_table = legacy.attributes["version_table"]
    if legacy_table not in installed:
        return False
    configs = alembic_configs(repo_root)
    if all(stage.version_table in installed for stage, _ in configs):
        return False

    command.upgrade(legacy, "head")
    command.check(legacy)
    installed = _installed_version_tables()
    for stage, config in configs:
        if stage.version_table in installed:
            continue
        if stage.name == "core":
            command.stamp(config, "head")
        else:
            command.upgrade(config, "head")
        installed = _installed_version_tables()
    return True


def upgrade_all(revision: str = "head", repo_root: Path | None = None) -> None:
    if revision == "head":
        _adopt_legacy_database(repo_root)
    for _, config in alembic_configs(repo_root):
        command.upgrade(config, revision)


def downgrade_all(revision: str = "base", repo_root: Path | None = None) -> None:
    for _, config in reversed(alembic_configs(repo_root)):
        command.downgrade(config, revision)


def check_all(repo_root: Path | None = None) -> None:
    for _, config in alembic_configs(repo_root):
        command.check(config)
