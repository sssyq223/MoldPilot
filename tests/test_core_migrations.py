import os
from pathlib import Path
import subprocess
import sys
from uuid import uuid4

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url

from pg_db import _test_database_url


@pytest.mark.integration
def test_template_pack_migrates_real_postgres_without_mold_tables():
    raw_url = _test_database_url()
    if not raw_url:
        pytest.skip("Isolated PostgreSQL test URL is required")
    base = create_engine(raw_url)
    if base.url.database != "moldpilot_test" or base.url.host not in {
        "127.0.0.1", "localhost", "postgres",
    }:
        raise RuntimeError("Refusing migration probe outside isolated moldpilot_test")

    schema = f"agent_core_test_{uuid4().hex[:12]}"
    with base.begin() as connection:
        connection.execute(text(f'CREATE SCHEMA "{schema}"'))
    probe_url = make_url(raw_url).update_query_dict(
        {"options": f"-csearch_path={schema}"}
    ).render_as_string(hide_password=False)
    environment = {
        **os.environ,
        "AGENT_BUSINESS_PACK": "template",
        "AGENT_MIGRATION_URL": probe_url,
        "PYTHONPATH": str((Path(__file__).resolve().parents[1] / "backend")),
    }

    def migrate(*arguments):
        completed = subprocess.run(
            [sys.executable, "scripts/migrate.py", *arguments],
            cwd=Path(__file__).resolve().parents[1],
            env=environment,
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
        )
        assert completed.returncode == 0, completed.stdout + completed.stderr
        return completed.stdout + completed.stderr

    try:
        migrate("upgrade", "head")
        assert "a10c0e000003 (head)" in migrate("current")
        assert "No new upgrade operations detected" in migrate("check")

        probe = create_engine(probe_url)
        with probe.connect() as connection:
            tables = set(connection.execute(text(
                "SELECT tablename FROM pg_tables WHERE schemaname=:schema"
            ), {"schema": schema}).scalars())
            assert connection.scalar(text(
                "SELECT version_num FROM alembic_core_version"
            )) == "a10c0e000003"
            seat_columns = set(connection.execute(text(
                "SELECT column_name FROM information_schema.columns "
                "WHERE table_schema=:schema AND table_name='approval_seat'"
            ), {"schema": schema}).scalars())
            assert {"parent_seat_id", "countersign_timing", "countersign_initiated_by",
                    "countersign_reason", "countersign_sequence"} <= seat_columns
            proxy_columns = set(connection.execute(text(
                "SELECT column_name FROM information_schema.columns "
                "WHERE table_schema=:schema AND table_name='approval_proxy_delegation'"
            ), {"schema": schema}).scalars())
            assert {"principal_user_id", "proxy_user_id", "process_key", "node_key",
                    "allowed_decisions", "valid_from", "valid_to"} <= proxy_columns
        assert "alembic_core_version" in tables
        assert not ({
            "project", "purchase_request", "business_subject", "contact_case",
            "supplier", "warehouse", "logistics_route",
        } & tables)

        migrate("downgrade", "base")
        with probe.connect() as connection:
            remaining = set(connection.execute(text(
                "SELECT tablename FROM pg_tables WHERE schemaname=:schema"
            ), {"schema": schema}).scalars())
        assert remaining <= {"alembic_core_version"}
        probe.dispose()
    finally:
        with base.begin() as connection:
            connection.execute(text(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE'))
        base.dispose()
