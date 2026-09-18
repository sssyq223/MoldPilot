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
environment = dotenv_values(".env")
url = resolve_migration_url(environment)
version_table = context.config.attributes.get("version_table", "alembic_mold_version")


def include_object(obj, name, type_, reflected, compare_to):
    if type_ == "table":
        return name in domain_tables
    table = getattr(obj, "table", None)
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
