import os
from pathlib import Path
import subprocess
import sys
from uuid import uuid4

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url

from pg_db import _test_database_url


PROJECT_ROOT = Path(__file__).resolve().parents[1]


def _isolated_schema():
    raw_url = _test_database_url()
    if not raw_url:
        pytest.skip("Isolated PostgreSQL test URL is required")
    base = create_engine(raw_url)
    if base.url.database != "moldpilot_test" or base.url.host not in {
        "127.0.0.1", "localhost", "postgres",
    }:
        raise RuntimeError("Refusing migration probe outside isolated moldpilot_test")
    schema = f"mold_split_test_{uuid4().hex[:12]}"
    with base.begin() as connection:
        connection.execute(text(f'CREATE SCHEMA "{schema}"'))
    probe_url = make_url(raw_url).update_query_dict(
        {"options": f"-csearch_path={schema}"}
    ).render_as_string(hide_password=False)
    return base, schema, probe_url


def _environment(probe_url: str):
    return {
        **os.environ,
        "AGENT_BUSINESS_PACK": "mold",
        "AGENT_MIGRATION_URL": probe_url,
        "PYTHONPATH": str(PROJECT_ROOT / "backend"),
    }


def _run(probe_url: str, *arguments: str):
    completed = subprocess.run(
        [sys.executable, *arguments],
        cwd=PROJECT_ROOT,
        env=_environment(probe_url),
        capture_output=True,
        text=True,
        timeout=90,
        check=False,
    )
    assert completed.returncode == 0, completed.stdout + completed.stderr
    return completed.stdout + completed.stderr


@pytest.mark.integration
def test_mold_pack_installs_core_and_domain_repositories_into_empty_postgres():
    base, schema, probe_url = _isolated_schema()
    try:
        _run(probe_url, "scripts/migrate.py", "upgrade", "head")
        current = _run(probe_url, "scripts/migrate.py", "current")
        assert "a10c0e000008 (head)" in current
        assert "mb0d0e000011 (head)" in current
        assert "No new upgrade operations detected" in _run(
            probe_url, "scripts/migrate.py", "check"
        )

        probe = create_engine(probe_url)
        with probe.connect() as connection:
            tables = set(connection.execute(text(
                "SELECT tablename FROM pg_tables WHERE schemaname=:schema"
            ), {"schema": schema}).scalars())
            assert connection.scalar(text(
                "SELECT version_num FROM alembic_core_version"
            )) == "a10c0e000008"
            assert connection.scalar(text(
                "SELECT version_num FROM alembic_mold_version"
                    )) == "mb0d0e000011"
            revision_columns = set(connection.execute(text("""
                SELECT column_name FROM information_schema.columns
                WHERE table_schema=:schema AND table_name='bid_intake_revision'
            """), {"schema": schema}).scalars())
            link_columns = set(connection.execute(text("""
                SELECT column_name FROM information_schema.columns
                WHERE table_schema=:schema AND table_name='bid_intake_lifecycle_link'
            """), {"schema": schema}).scalars())
            assert {"customer_process_confirmed", "customer_process_confirmation_evidence"} <= revision_columns
            assert "source_revision_id" in link_columns
        assert {"app_user", "approval_instance", "project", "purchase_request",
                "contact_case", "contract_attachment", "contract_settlement_allocation",
                "supplier_shipment", "quote_inbound_record", "quotation_detail",
                "quotation_source_link", "quotation_feedback", "bid_intake_case",
                "bid_intake_revision", "bid_intake_attachment", "bid_intake_lifecycle_link",
                "internal_start_dispatch", "internal_start_snapshot",
                "contract_receipt_evidence"} <= tables
        with probe.connect() as connection:
            immutable = set(connection.execute(text("""
                SELECT event_object_table FROM information_schema.triggers
                WHERE trigger_name LIKE '%_immutable' AND event_object_schema=:schema
            """), {"schema": schema}).scalars())
        assert {"file_object", "agent_run_file", "contact_attachment", "contract_attachment",
                "contract_settlement_allocation", "quote_inbound_record", "quotation_detail",
                "quotation_source_link", "quotation_feedback", "bid_intake_case",
                "bid_intake_revision", "bid_intake_attachment", "bid_intake_lifecycle_link",
                "internal_start_dispatch", "internal_start_snapshot",
                "contract_receipt_evidence"} <= immutable
        assert "alembic_version" not in tables

        _run(probe_url, "scripts/migrate.py", "downgrade", "base")
        with probe.connect() as connection:
            remaining = set(connection.execute(text(
                "SELECT tablename FROM pg_tables WHERE schemaname=:schema"
            ), {"schema": schema}).scalars())
        assert remaining <= {"alembic_core_version", "alembic_mold_version"}
        probe.dispose()
    finally:
        with base.begin() as connection:
            connection.execute(text(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE'))
        base.dispose()


@pytest.mark.integration
def test_existing_legacy_mold_schema_is_adopted_without_rewriting_business_rows():
    base, schema, probe_url = _isolated_schema()
    try:
        _run(
            probe_url,
            "-m", "alembic", "-c", "backend/domain_packs/mold/alembic.ini",
            "upgrade", "head",
        )
        probe = create_engine(probe_url)
        with probe.begin() as connection:
            connection.execute(text("""
                INSERT INTO app_user (
                    username, display_name, department, password_hash,
                    super_admin, active, security_version, id, created_at
                ) VALUES (
                    'migration-sentinel', '迁移哨兵', '验证部门', 'not-a-login-secret',
                    FALSE, TRUE, 1, '00000000-0000-0000-0000-000000000001', now()
                )
            """))
            connection.execute(text("""
                INSERT INTO project (code, name, status, row_version, id, created_at)
                VALUES (
                    'SPLIT-MIGRATION-SENTINEL', '迁移保留项目', 'ACTIVE', 7,
                    '00000000-0000-0000-0000-000000000002', now()
                )
            """))

        _run(probe_url, "scripts/migrate.py", "upgrade", "head")
        assert "No new upgrade operations detected" in _run(
            probe_url, "scripts/migrate.py", "check"
        )
        with probe.connect() as connection:
            assert connection.scalar(text(
                "SELECT version_num FROM alembic_version"
            )) == "f60e6c8a3d48"
            assert connection.scalar(text(
                "SELECT version_num FROM alembic_core_version"
            )) == "a10c0e000008"
            assert connection.scalar(text(
                "SELECT version_num FROM alembic_mold_version"
                    )) == "mb0d0e000011"
            assert connection.scalar(text(
                "SELECT display_name FROM app_user WHERE username='migration-sentinel'"
            )) == "迁移哨兵"
            assert connection.execute(text("""
                SELECT name, status, row_version FROM project
                WHERE code='SPLIT-MIGRATION-SENTINEL'
            """)).one() == ("迁移保留项目", "ACTIVE", 7)
        probe.dispose()
    finally:
        with base.begin() as connection:
            connection.execute(text(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE'))
        base.dispose()
