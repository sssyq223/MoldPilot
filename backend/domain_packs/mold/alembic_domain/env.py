from alembic import context
from dotenv import dotenv_values
from sqlalchemy import create_engine

from agent_core.model_base import Base
from agent_core.domain_pack import migration_contract
from agent_core.migration_runtime import resolve_migration_url
from domain_packs.mold import models as mold_models


config_file = "backend/domain_packs/mold/alembic-domain.ini"
if config_file not in {stage["config"] for stage in migration_contract().STAGES}:
    raise RuntimeError("The active pack does not install the mold domain migration stage")

target_metadata = Base.metadata
domain_tables = {
    getattr(mold_models, name).__table__.name
    for name in mold_models.EXPORTED_MODELS
}
# An intermediate migration persisted ERP design workspace provenance directly
# in MoldPilot.  Those fields are no longer mapped after ERP access returned to
# the tool boundary, but deployed databases retain them so recovery never
# destroys historical data.  Alembic must not treat the preserved columns as
# current-model drift.
retired_columns = {
    "design_detail": {
        "source_as_of",
        "source_resource_id",
        "source_resource_type",
        "source_resource_version",
        "source_snapshot",
        "source_snapshot_hash",
        "source_summary",
        "source_system",
    },
}
retired_indexes = {"ix_design_detail_source_resource"}
environment = dotenv_values(".env")
url = resolve_migration_url(environment)
version_table = context.config.attributes.get("version_table", "alembic_mold_version")


def include_object(obj, name, type_, reflected, compare_to):
    if type_ == "table":
        return name in domain_tables
    table = getattr(obj, "table", None)
    if (reflected and compare_to is None and type_ == "column" and table is not None
            and name in retired_columns.get(table.name, set())):
        return False
    if reflected and compare_to is None and type_ == "index" and name in retired_indexes:
        return False
    return table is None or table.name in domain_tables


if context.is_offline_mode():
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        version_table=version_table,
        include_object=include_object,
    )
    with context.begin_transaction():
        context.run_migrations()
else:
    engine = create_engine(url)
    with engine.connect() as connection:
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
            compare_type=True,
            version_table=version_table,
            include_object=include_object,
        )
        with context.begin_transaction():
            context.run_migrations()
