import json

from app import config
from conftest import sign_in


def _legacy_config():
    return {
        "llm_enabled": True,
        "llm_provider": "company",
        "llm_base_url": "https://model.example/v1",
        "llm_api_key": "top-secret",
        "llm_model": "New-27B",
        "llm_max_turns": 12,
        "llm_max_output_tokens": 4096,
        "llm_context_window": 131072,
        "llm_connect_timeout": 10,
        "llm_read_timeout": 90,
    }


def _profile_input(name="Local 8B", model="qwen3:8b"):
    return {
        "name": name,
        "enabled": True,
        "provider": "ollama",
        "company": {},
        "ollama": {"base_url": "http://127.0.0.1:11434", "model": model},
        "max_output_tokens": 2048,
        "context_window": 40960,
        "max_turns": 10,
        "connect_timeout": 10,
        "read_timeout": 60,
    }


def test_legacy_runtime_and_environment_are_exposed_as_switchable_profiles(tmp_path, monkeypatch):
    path = tmp_path / "model-config.json"
    path.write_text(json.dumps(_legacy_config()), encoding="utf-8")
    monkeypatch.setattr(config, "_model_config_path", lambda: path)

    result = config.public_model_config()

    assert result["active_profile_id"] == "runtime"
    assert result["model"] == "New-27B"
    assert result["company"]["api_key_configured"] is True
    assert "top-secret" not in json.dumps(result)
    assert {item["model"] for item in result["profiles"]} >= {
        "New-27B", config.settings().active_model,
    }


def test_profile_create_activate_update_and_delete_preserve_independent_secrets(tmp_path, monkeypatch):
    path = tmp_path / "model-config.json"
    path.write_text(json.dumps(_legacy_config()), encoding="utf-8")
    monkeypatch.setattr(config, "_model_config_path", lambda: path)

    created = config.create_model_profile(_profile_input())
    local_id = created["active_profile_id"]
    assert created["model"] == "qwen3:8b"
    assert len(created["profiles"]) >= 2

    activated = config.activate_model_profile("runtime")
    assert activated["model"] == "New-27B"
    assert config.model_settings().llm_api_key == "top-secret"

    update = _profile_input(name="Local renamed")
    updated = config.update_model_profile(local_id, update)
    local = next(item for item in updated["profiles"] if item["id"] == local_id)
    assert local["name"] == "Local renamed"

    deleted = config.delete_model_profile(local_id)
    assert all(item["id"] != local_id for item in deleted["profiles"])


def test_updating_company_profile_without_new_key_keeps_saved_key(tmp_path, monkeypatch):
    path = tmp_path / "model-config.json"
    path.write_text(json.dumps(_legacy_config()), encoding="utf-8")
    monkeypatch.setattr(config, "_model_config_path", lambda: path)
    payload = {
        "name": "Renamed 27B",
        "enabled": True,
        "provider": "company",
        "company": {"base_url": "https://model.example/v1", "model": "New-27B",
                    "trusted_http_origin": "", "proxy_url": "", "api_key": "",
                    "clear_api_key": False},
        "ollama": {"base_url": "http://127.0.0.1:11434", "model": ""},
        "max_output_tokens": 4096,
        "context_window": 131072,
        "max_turns": 12,
        "connect_timeout": 10,
        "read_timeout": 90,
    }

    config.update_model_profile("runtime", payload)

    assert config.model_settings().llm_api_key == "top-secret"
    stored = json.loads(path.read_text(encoding="utf-8"))
    active = next(item for item in stored["profiles"] if item["id"] == "runtime")
    assert active["llm_api_key"] == "top-secret"


def test_admin_can_create_and_one_click_activate_profiles(client, tmp_path, monkeypatch):
    path = tmp_path / "model-config.json"
    path.write_text(json.dumps(_legacy_config()), encoding="utf-8")
    monkeypatch.setattr(config, "_model_config_path", lambda: path)
    sign_in(client)

    initial = client.get("/api/model-config")
    assert initial.status_code == 200
    assert len(initial.json()["profiles"]) >= 2

    created = client.post("/api/model-profiles", json=_profile_input())
    assert created.status_code == 200, created.text
    local_id = created.json()["active_profile_id"]
    assert created.json()["model"] == "qwen3:8b"

    activated = client.post("/api/model-profiles/runtime/activate")
    assert activated.status_code == 200, activated.text
    assert activated.json()["active_profile_id"] == "runtime"
    assert activated.json()["model"] == "New-27B"

    removed = client.delete(f"/api/model-profiles/{local_id}")
    assert removed.status_code == 200, removed.text
    assert all(item["id"] != local_id for item in removed.json()["profiles"])
