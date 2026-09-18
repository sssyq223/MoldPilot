from sqlalchemy import create_engine, event
from sqlalchemy.orm import sessionmaker
from .config import settings
from agent_core.model_base import Base, aware, now


def make_engine(url: str):
    if url.startswith("sqlite"):
        raise RuntimeError("SQLite is not allowed for runtime; configure the PostgreSQL database URL.")
    engine = create_engine(url, pool_pre_ping=True)
    if engine.dialect.name == "postgresql":
        @event.listens_for(engine, "connect")
        def configure(connection, _):
            previous = connection.autocommit
            connection.autocommit = True
            with connection.cursor() as cur:
                cur.execute("SET TIME ZONE 'Asia/Shanghai'")
                cur.execute("SET statement_timeout = '10s'")
            connection.autocommit = previous
    return engine


engine = make_engine(settings().database_url)
SessionLocal = sessionmaker(engine, expire_on_commit=False)


def get_db():
    with SessionLocal() as db:
        yield db
