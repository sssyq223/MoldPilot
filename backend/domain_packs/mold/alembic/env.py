from dotenv import dotenv_values
from alembic import context
from sqlalchemy import create_engine
from agent_core.model_base import Base
from domain_packs.mold import models as _mold_models  # noqa: F401
from agent_core.domain_pack import migration_contract
from agent_core.migration_runtime import resolve_migration_url

legacy = getattr(migration_contract(), "LEGACY", {})
if legacy.get("config") != "backend/domain_packs/mold/alembic.ini":
    raise RuntimeError(
        "This compatibility repository is available only to the mold legacy adopter"
    )

target_metadata = Base.metadata
legacy_tables = set(getattr(migration_contract(), "LEGACY_TABLES", ()))
legacy_model_exclusions = set(getattr(migration_contract(), "LEGACY_MODEL_EXCLUSIONS", ()))
if not legacy_tables:
    raise RuntimeError("The mold legacy repository requires a frozen table ownership snapshot")
environment = dotenv_values('.env')
url = resolve_migration_url(environment)
version_table = context.config.attributes.get("version_table", "alembic_version")


def include_object(obj, name, type_, reflected, compare_to):
    if type_ == "table":
        return name in legacy_tables
    table = getattr(obj, "table", None)
    table_name = table.name if table is not None else None
    if not reflected and (type_, table_name, name) in legacy_model_exclusions:
        return False
    return table is None or table.name in legacy_tables

if context.is_offline_mode():
    context.configure(
        url=url, target_metadata=target_metadata, literal_binds=True,
        version_table=version_table, include_object=include_object,
    )
    with context.begin_transaction(): context.run_migrations()
else:
    engine = create_engine(url)
    with engine.connect() as connection:
        context.configure(
            connection=connection, target_metadata=target_metadata,
            compare_type=True, version_table=version_table,
            include_object=include_object,
        )
        with context.begin_transaction(): context.run_migrations()
