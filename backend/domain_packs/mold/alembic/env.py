from dotenv import dotenv_values
from alembic import context
from sqlalchemy import create_engine
from app.models import Base
from agent_core.domain_pack import migration_contract
from agent_core.migration_runtime import resolve_migration_url

if migration_contract().ALEMBIC_CONFIG != "backend/domain_packs/mold/alembic.ini":
    raise RuntimeError(
        "This pack does not use the legacy MoldPilot migration chain; run scripts/migrate.py"
    )

target_metadata = Base.metadata
environment = dotenv_values('.env')
url = resolve_migration_url(environment)
version_table = context.config.attributes.get("version_table", "alembic_version")

if context.is_offline_mode():
    context.configure(
        url=url, target_metadata=target_metadata, literal_binds=True,
        version_table=version_table,
    )
    with context.begin_transaction(): context.run_migrations()
else:
    engine = create_engine(url)
    with engine.connect() as connection:
        context.configure(
            connection=connection, target_metadata=target_metadata,
            compare_type=True, version_table=version_table,
        )
        with context.begin_transaction(): context.run_migrations()
