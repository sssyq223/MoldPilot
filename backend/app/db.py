from datetime import datetime
from zoneinfo import ZoneInfo
from sqlalchemy import create_engine, event
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker
from .config import settings

SHANGHAI = ZoneInfo("Asia/Shanghai")


def now():
    return datetime.now(SHANGHAI)


def aware(value):
    """Normalize datetimes returned by legacy synthetic tests."""
    return value.replace(tzinfo=SHANGHAI) if value.tzinfo is None else value


class Base(DeclarativeBase):
    pass


def make_engine(url: str):
    if url.startswith("sqlite"):
        raise RuntimeError("SQLite is not allowed for MoldPilot runtime; configure MOLD_DATABASE_URL for PostgreSQL.")
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
