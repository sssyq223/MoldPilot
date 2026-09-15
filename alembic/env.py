import os
from dotenv import dotenv_values
from alembic import context
from sqlalchemy import create_engine
from app.models import Base
from app.config import settings

target_metadata = Base.metadata
url = os.environ.get("MOLD_MIGRATION_URL") or dotenv_values('.env').get("MOLD_MIGRATION_URL")
if not url: raise RuntimeError("MOLD_MIGRATION_URL is required; application credentials cannot migrate")

if context.is_offline_mode():
    context.configure(url=url, target_metadata=target_metadata, literal_binds=True)
    with context.begin_transaction(): context.run_migrations()
else:
    engine = create_engine(url)
    with engine.connect() as connection:
        context.configure(connection=connection, target_metadata=target_metadata, compare_type=True)
        with context.begin_transaction(): context.run_migrations()
