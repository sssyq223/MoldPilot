import pytest

from agent_core.errors import DomainError
from domain_packs.mold.tools.local import model_configuration_tools


class Admin:
    super_admin = True
    id = "admin-1"
    security_version = 1


class User:
    super_admin = False
    id = "user-1"
    security_version = 1


def test_model_catalog_query_is_public_and_never_contains_api_key(monkeypatch):
    monkeypatch.setattr(model_configuration_tools.catalog, "public_catalog", lambda: {
        "version": 3,
        "revision": "a" * 64,
        "providers": [{"id": "p1", "api_key_configured": True}],
        "models": [],
    })
    result = model_configuration_tools.execute_tool(None, Admin(), "query_model_configuration", {})
    assert result["source"] == "agent_model_catalog"
    assert "'api_key':" not in str(result)


def test_model_provider_directory_query_uses_saved_revision_without_writing(monkeypatch):
    monkeypatch.setattr(model_configuration_tools.model_discovery, "discover_models", lambda data: {
        "models": [{"model": "qwen3", "capability_status": "unverified"}],
        "count": 1,
        "complete": True,
    })
    result = model_configuration_tools.execute_tool(None, Admin(), "query_model_provider_directory", {
        "provider_id": "provider-1",
        "revision": "a" * 64,
    })
    assert result["source"] == "agent_model_provider"
    assert result["data"][0]["models"][0]["capability_status"] == "unverified"


def test_model_provider_prepare_is_proposal_and_does_not_accept_raw_key():
    with pytest.raises(DomainError):
        model_configuration_tools.execute_tool(None, Admin(), "prepare_model_provider_save", {
            "revision": "a" * 64,
            "name": "白山",
            "protocol": "company",
            "base_url": "https://example.test/v1",
            "api_key": "secret-value",
        })


def test_model_configuration_is_admin_only():
    with pytest.raises(DomainError):
        model_configuration_tools.execute_tool(None, User(), "query_model_configuration", {})
