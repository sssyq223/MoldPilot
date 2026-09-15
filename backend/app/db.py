from datetime import datetime
from zoneinfo import ZoneInfo
from sqlalchemy import create_engine, event
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker
from .config import settings

SHANGHAI = ZoneInfo("Asia/Shanghai")


def now():
    return datetime.now(SHANGHAI)


def aware(value):
    """SQLite smoke fixtures return naive datetimes; production PostgreSQL does not."""
    return value.replace(tzinfo=SHANGHAI) if value.tzinfo is None else value


class Base(DeclarativeBase):
    pass


def make_engine(url: str):
    engine = create_engine(url, pool_pre_ping=True,
                           connect_args={'check_same_thread':False} if url.startswith('sqlite') else {})
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
