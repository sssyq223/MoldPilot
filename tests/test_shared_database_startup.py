import os
from pathlib import Path
import subprocess
import sys
from uuid import uuid4

import pytest
from sqlalchemy import Column, Integer, MetaData, String, Table, create_engine, text
from sqlalchemy.engine import make_url

from agent_core import migration_runtime
from agent_core.schema_verification import verify_schema
from pg_db import _test_database_url


ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def shared_probe():
    raw_url = _test_database_url()
    if not raw_url:
        pytest.skip("Isolated PostgreSQL test URL is required")
    base = create_engine(raw_url)
    if base.url.database != "moldpilot_test" or base.url.host not in {"127.0.0.1", "localhost", "postgres"}:
        raise RuntimeError("Refusing shared database tests outside isolated moldpilot_test")
    schema = f"shared_startup_test_{uuid4().hex[:12]}"
    with base.begin() as connection:
        connection.execute(text(f'CREATE SCHEMA "{schema}"'))
    probe_url = make_url(raw_url).update_query_dict(
        {"options": f"-csearch_path={schema}"}
    ).render_as_string(hide_password=False)
    probe = create_engine(probe_url)
    try:
        yield probe, probe_url
    finally:
        probe.dispose()
        with base.begin() as connection:
            connection.execute(text(f'DROP SCHEMA "{schema}" CASCADE'))
        base.dispose()


def _run(probe_url, command, **overrides):
    return subprocess.run(
        [sys.executable, "scripts/migrate.py", command],
        cwd=ROOT,
        env={
            **os.environ,
            "AGENT_BUSINESS_PACK": "mold",
            "AGENT_DATABASE_URL": probe_url,
            "AGENT_MIGRATION_URL": probe_url,
            "AGENT_STARTUP_MIGRATIONS": "upgrade",
            **overrides,
        },
        capture_output=True, text=True, timeout=300, check=False,
    )


@pytest.mark.integration
def test_shared_startup_preserves_unknown_revision_and_rows(shared_probe):
    engine, url = shared_probe
    upgraded = _run(url, "upgrade")
    assert upgraded.returncode == 0, upgraded.stdout + upgraded.stderr
    with engine.begin() as connection:
        connection.execute(text("UPDATE alembic_mold_version SET version_num='mb0d0e000013'"))
        connection.execute(text("CREATE TABLE document_intake_file (id varchar(36) PRIMARY KEY)"))
        connection.execute(text("INSERT INTO document_intake_file VALUES ('preserved')"))
        connection.execute(text("""
            ALTER TABLE contract_attachment
            ADD COLUMN intake_file_id varchar(36) UNIQUE REFERENCES document_intake_file(id),
            ADD COLUMN role varchar(30)
        """))
        connection.execute(text("ALTER TABLE payment_stage ADD COLUMN sequence integer NOT NULL DEFAULT 1"))

    verified = _run(
        url, "startup", AGENT_STARTUP_MIGRATIONS="verify",
        # Verification must connect to the application's URL, not the migration
        # owner's URL (which may belong to a different server or account).
        AGENT_MIGRATION_URL="postgresql+psycopg://unused@127.0.0.1:1/unused",
    )
    assert verified.returncode == 0, verified.stdout + verified.stderr
    assert "mb0d0e000013" in verified.stdout
    assert "read-only; no migrations applied" in verified.stdout
    with engine.connect() as connection:
        assert connection.scalar(text("SELECT version_num FROM alembic_mold_version")) == "mb0d0e000013"
        assert connection.scalar(text("SELECT id FROM document_intake_file")) == "preserved"

    # Even with verify configured, explicit migration commands remain strict.
    strict = _run(url, "upgrade", AGENT_STARTUP_MIGRATIONS="verify")
    assert strict.returncode != 0
    assert "Can't locate revision" in strict.stderr
    with engine.begin() as connection:
        connection.execute(text("ALTER TABLE contract_attachment DROP COLUMN title"))
    rejected = _run(url, "startup", AGENT_STARTUP_MIGRATIONS="verify")
    assert rejected.returncode != 0
    assert "contract_attachment.title" in rejected.stderr
    with engine.connect() as connection:
        assert connection.scalar(text("SELECT version_num FROM alembic_mold_version")) == "mb0d0e000013"


@pytest.mark.integration
@pytest.mark.parametrize(("change", "problem"), [
    ("DROP TABLE example", "missing table example"),
    ("ALTER TABLE example DROP COLUMN label", "example.label"),
    ("ALTER TABLE example ALTER COLUMN label TYPE varchar(20)", "modify_type"),
    ("ALTER TABLE example ALTER COLUMN label SET NOT NULL", "modify_nullable"),
    ("ALTER TABLE example ADD COLUMN required text NOT NULL", "unmapped required column"),
    ("ALTER TABLE example DROP CONSTRAINT example_pkey", "primary key differs"),
    ("ALTER TABLE example ADD UNIQUE(label)", "remove_constraint"),
    ("ALTER TABLE example ADD COLUMN other_id integer REFERENCES example(id)", None),
    ("ALTER TABLE example ADD COLUMN other_id integer DEFAULT 1 REFERENCES example(id)", "remove_fk"),
    ("ALTER TABLE example ADD COLUMN external_id integer UNIQUE", None),
    ("ALTER TABLE example ADD COLUMN external_id integer UNIQUE NULLS NOT DISTINCT", "remove_constraint"),
    ("ALTER TABLE example ADD COLUMN sequence integer NOT NULL DEFAULT 1", None),
])
def test_shared_structure_verification_blocks_incompatible_changes(shared_probe, change, problem):
    engine, _ = shared_probe
    metadata = MetaData()
    Table("example", metadata,
          Column("id", Integer, primary_key=True), Column("label", String(80)))
    with engine.begin() as connection:
        metadata.create_all(connection)
        connection.execute(text(change))
    with engine.connect() as connection:
        with connection.begin():
            connection.exec_driver_sql("SET TRANSACTION READ ONLY")
            if problem:
                with pytest.raises(RuntimeError, match=problem):
                    verify_schema(connection, metadata)
            else:
                verify_schema(connection, metadata)


def test_startup_never_falls_back_from_upgrade_to_verification(monkeypatch):
    def fail_upgrade():
        raise RuntimeError("unknown migration")

    def unexpected_verify(url):
        pytest.fail("Upgrade failures must not silently select verify mode")

    monkeypatch.setattr(migration_runtime, "upgrade_all", fail_upgrade)
    monkeypatch.setattr(migration_runtime, "verify_runtime_database", unexpected_verify)
    with pytest.raises(RuntimeError, match="unknown migration"):
        migration_runtime.startup_database("upgrade", "unused")
    with pytest.raises(RuntimeError, match="must be upgrade or verify"):
        migration_runtime.startup_database("skip", "unused")
