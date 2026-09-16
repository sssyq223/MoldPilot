import os

import pytest
from alembic import command
from alembic.config import Config
from dotenv import dotenv_values
from sqlalchemy import text
from sqlalchemy.engine import make_url
from sqlalchemy.orm import sessionmaker

from app.db import make_engine
from app.models import Base


_MIGRATED_URLS: set[str] = set()


def _test_database_url() -> str | None:
    values = dotenv_values(".env")
    explicit = os.environ.get("MOLD_TEST_DATABASE_URL") or values.get("MOLD_TEST_DATABASE_URL")
    if explicit:
        candidate = explicit
    else:
        main = os.environ.get("MOLD_DATABASE_URL") or values.get("MOLD_DATABASE_URL")
        if not main:
            return None
        url = make_url(main)
        if url.drivername.startswith("sqlite"):
            return None
        candidate = url.set(database="moldpilot_test").render_as_string(hide_password=False)
    url = make_url(candidate)
    if url.drivername.startswith("sqlite"):
        raise RuntimeError("SQLite is not allowed for MoldPilot tests; use PostgreSQL moldpilot_test.")
    if not url.drivername.startswith("postgresql"):
        raise RuntimeError("MoldPilot tests require a PostgreSQL URL.")
    return candidate


def _ensure_safe_test_database(engine) -> None:
    if engine.url.database != "moldpilot_test" or engine.url.host not in {"127.0.0.1", "localhost", "postgres"}:
        raise RuntimeError("Refusing to initialize a database outside the isolated moldpilot_test target")


def _migrate(url: str) -> None:
    if url in _MIGRATED_URLS:
        return
    previous = os.environ.get("MOLD_MIGRATION_URL")
    os.environ["MOLD_MIGRATION_URL"] = url
    try:
        command.upgrade(Config("alembic.ini"), "head")
    finally:
        if previous is None:
            os.environ.pop("MOLD_MIGRATION_URL", None)
        else:
            os.environ["MOLD_MIGRATION_URL"] = previous
    _MIGRATED_URLS.add(url)


def _truncate(engine) -> None:
    tables = ",".join('"' + table.name + '"' for table in Base.metadata.sorted_tables)
    with engine.begin() as conn:
        conn.execute(text("SET LOCAL statement_timeout = '60s'"))
        conn.execute(text("TRUNCATE TABLE " + tables + " CASCADE"))


def factory():
    url = _test_database_url()
    if not url:
        pytest.skip("Isolated PostgreSQL test URL is required")
    engine = make_engine(url)
    _ensure_safe_test_database(engine)
    _migrate(url)
    _truncate(engine)
    return engine, sessionmaker(engine, expire_on_commit=False)


def database():
    engine, session_factory = factory()
    db = session_factory()
    db.info["moldpilot_test_engine"] = engine
    return db
