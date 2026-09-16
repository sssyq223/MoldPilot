import json

import pytest
from sqlalchemy import create_engine
from sqlalchemy import select
from sqlalchemy.orm import sessionmaker

from app.config import settings
from app.db import Base
from app.models import User
from app.tool_gateway import SKILLS, TOOLS, available_tools, execute, skill_context, tool_schema


@pytest.fixture()
def sqlite_factory():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    factory = sessionmaker(engine, expire_on_commit=False)
    with factory.begin() as db:
        db.add(User(username="admin", display_name="测试管理员", password_hash="x", super_admin=True))
    yield factory
    engine.dispose()


def test_operations_readiness_tool_schema_and_skill_registered(sqlite_factory):
    assert "query_operations_readiness_context" in TOOLS
    assert "operations_readiness_review" in SKILLS
    schema = tool_schema("query_operations_readiness_context")["function"]["parameters"]
    assert "include_runtime_counters" in schema["properties"]
    with sqlite_factory() as db:
        admin = db.scalar(select(User).where(User.username == "admin"))
        assert "query_operations_readiness_context" in available_tools(db, admin)
        assert any(item["key"] == "operations_readiness_review" for item in skill_context(db, admin))


def test_operations_readiness_reports_unconfirmed_fr118_gates_and_redacts_secrets(sqlite_factory, monkeypatch):
    cfg = settings()
    monkeypatch.setattr(cfg, "database_url", "postgresql://agent:secret-db-pass@localhost:5432/agent_test?sslmode=require")
    monkeypatch.setattr(cfg, "redis_url", "redis://:secret-redis-pass@127.0.0.1:6379/0")
    monkeypatch.setattr(cfg, "worker_secret", "secret-worker-value")
    monkeypatch.setattr(cfg, "credential_encryption_key", "secret-credential-value")
    monkeypatch.setattr(cfg, "file_s3_secret_key", "secret-s3-value")
    with sqlite_factory() as db:
        admin = db.scalar(select(User).where(User.username == "admin"))
        result = execute(db, admin, "query_operations_readiness_context", {})
    payload = result["data"][0]
    assert payload["log_retention"]["retention_script"]["exists"] is True
    assert payload["log_retention"]["retention_script"]["default_mode"] == "dry-run"
    assert "--i-understand-this-will-prune-logs" in payload["log_retention"]["retention_script"]["execute_requires"]
    assert payload["backup_restore"]["backup_script"]["client_modes"] == ["native", "docker", "auto"]
    assert payload["backup_restore"]["restore_script"]["client_modes"] == ["native", "docker", "auto"]
    assert payload["backup_restore"]["docker_pg_client"]["image"]
    gates = {item["key"]: item for item in payload["acceptance_gates"]}
    for key in [
        "deployment_topology",
        "user_scale",
        "response_time",
        "availability",
        "backup_frequency",
        "restore_objective",
        "log_retention",
    ]:
        assert gates[key]["confirmed"] is False
    assert payload["database"]["password_present"] is True
    assert payload["redis"]["password_present"] is True
    assert payload["security_runtime"]["worker_secret_configured"] is True
    serialized = json.dumps(result, ensure_ascii=False)
    assert "secret-db-pass" not in serialized
    assert "secret-redis-pass" not in serialized
    assert "secret-worker-value" not in serialized
    assert "secret-credential-value" not in serialized
    assert "secret-s3-value" not in serialized
    assert "未确认前不得承诺性能" in "".join(result["limitations"])


def test_operations_readiness_acceptance_evidence_file_confirms_one_gate(sqlite_factory, monkeypatch, tmp_path):
    evidence = tmp_path / "acceptance-gates.json"
    evidence.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "environment": "local-delivery-test",
                "confirmed_by": "tester",
                "confirmed_at": "2026-09-16T12:00:00+08:00",
                "gates": {
                    "deployment_topology": {
                        "confirmed": True,
                        "evidence_refs": ["test://deployment-topology"],
                        "notes": "synthetic acceptance evidence",
                    },
                    "user_scale": {
                        "confirmed": True,
                        "evidence_refs": [],
                    },
                },
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    cfg = settings()
    monkeypatch.setattr(cfg, "acceptance_evidence_file", str(evidence))
    with sqlite_factory() as db:
        admin = db.scalar(select(User).where(User.username == "admin"))
        result = execute(db, admin, "query_operations_readiness_context", {})
    payload = result["data"][0]
    gates = {item["key"]: item for item in payload["acceptance_gates"]}
    assert payload["acceptance_evidence"]["status"] == "LOADED"
    assert payload["acceptance_evidence"]["valid_gate_keys"] == ["deployment_topology"]
    assert payload["acceptance_evidence"]["invalid_gate_keys"] == ["user_scale"]
    assert gates["deployment_topology"]["confirmed"] is True
    assert gates["deployment_topology"]["confirmation_source"] == "acceptance_evidence_file"
    assert gates["deployment_topology"]["acceptance_record"]["evidence_refs"] == ["test://deployment-topology"]
    assert gates["user_scale"]["confirmed"] is False
