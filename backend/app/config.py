from functools import lru_cache
import json
import os
from pathlib import Path
from types import SimpleNamespace
from typing import Literal
from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="MOLD_", env_file=".env", extra="ignore")
    database_url: str = "postgresql+psycopg://agent_app@127.0.0.1:55432/agent_db"
    redis_url: str = "redis://127.0.0.1:56379/0"
    restore_database_url: str = ""
    pg_dump_path: str = ""
    pg_restore_path: str = ""
    origin: str = "http://127.0.0.1:5173"
    cookie_secure: bool = True
    environment: str = "development"
    audit_log_retention_days: int = Field(default=0, ge=0, le=3650)
    app_log_retention_days: int = Field(default=0, ge=0, le=3650)
    access_log_retention_days: int = Field(default=0, ge=0, le=3650)
    model_log_retention_days: int = Field(default=0, ge=0, le=3650)
    llm_base_url: str = ""
    # Explicit HTTP origin for a trusted private-IP model service; empty requires HTTPS.
    llm_trusted_http_origin: str = ""
    llm_provider: Literal['company','ollama'] = 'company'
    ollama_base_url: str = 'http://127.0.0.1:11434'
    ollama_model: str = 'deepseek-r1:7b'
    llm_api_key: str = ""
    llm_proxy_url: str | None = None
    llm_tls_max_version: Literal["auto", "1.2"] = "auto"
    llm_tls_key_exchange: Literal["auto", "x25519"] = "auto"
    llm_connect_timeout: float = Field(default=10, gt=0, le=20)
    llm_read_timeout: float = Field(default=60, gt=0, le=75)
    llm_model: str = ""
    llm_max_turns: int = 12
    llm_max_output_tokens: int = 2048
    llm_context_window: int = 8192
    llm_enabled: bool = False
    worker_secret: str = ""
    api_base_url: str = "http://127.0.0.1:8000"
    erp_base_url: str = ""
    erp_allow_insecure_local: bool = False
    credential_encryption_key: str = ""
    file_backend: Literal['local','s3'] = 'local'
    file_local_root: str = '.local/files'
    file_max_bytes: int = Field(default=20*1024*1024,ge=1024,le=50*1024*1024)
    file_daily_bytes: int = Field(default=200*1024*1024,ge=1024)
    file_s3_endpoint: str = ''
    file_s3_bucket: str = ''
    file_s3_region: str = 'us-east-1'
    file_s3_access_key: str = ''
    file_s3_secret_key: str = ''

    @property
    def active_model(self):
        return self.ollama_model if self.llm_provider=='ollama' else self.llm_model


@lru_cache
def settings() -> Settings:
    return Settings()


def _model_config_path() -> Path:
    return Path(os.environ.get("MOLD_MODEL_CONFIG_FILE", ".local/model-config.json"))


def _runtime_model_config() -> dict:
    path = _model_config_path()
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except FileNotFoundError:
        return {}
    except (OSError, json.JSONDecodeError):
        return {}


def model_settings():
    """Return model settings with local UI overrides applied.

    The base Settings object remains environment-driven. The small runtime JSON file
    lets the desktop UI update model routing without mutating .env or restarting the
    API process. Secrets are never included in public_model_config().
    """
    base = settings()
    data = _runtime_model_config()
    provider = data.get("llm_provider", base.llm_provider)
    values = {
        "llm_enabled": bool(data.get("llm_enabled", base.llm_enabled)),
        "llm_provider": provider if provider in {"company", "ollama"} else base.llm_provider,
        "llm_base_url": str(data.get("llm_base_url", base.llm_base_url) or ""),
        "llm_trusted_http_origin": str(data.get("llm_trusted_http_origin", base.llm_trusted_http_origin) or ""),
        "llm_api_key": str(data.get("llm_api_key", base.llm_api_key) or ""),
        "llm_proxy_url": data.get("llm_proxy_url", base.llm_proxy_url) or None,
        "llm_tls_max_version": data.get("llm_tls_max_version", base.llm_tls_max_version),
        "llm_tls_key_exchange": data.get("llm_tls_key_exchange", base.llm_tls_key_exchange),
        "llm_connect_timeout": float(data.get("llm_connect_timeout", base.llm_connect_timeout)),
        "llm_read_timeout": float(data.get("llm_read_timeout", base.llm_read_timeout)),
        "llm_model": str(data.get("llm_model", base.llm_model) or ""),
        "ollama_base_url": str(data.get("ollama_base_url", base.ollama_base_url) or ""),
        "ollama_model": str(data.get("ollama_model", base.ollama_model) or ""),
        "llm_max_turns": int(data.get("llm_max_turns", base.llm_max_turns)),
        "llm_max_output_tokens": int(data.get("llm_max_output_tokens", base.llm_max_output_tokens)),
        "llm_context_window": int(data.get("llm_context_window", base.llm_context_window)),
        "worker_secret": base.worker_secret,
        "api_base_url": base.api_base_url,
    }
    values["active_model"] = values["ollama_model"] if values["llm_provider"] == "ollama" else values["llm_model"]
    values["config_version"] = json.dumps({k: v for k, v in values.items() if k not in {"worker_secret", "api_base_url"}}, sort_keys=True, ensure_ascii=False)
    return SimpleNamespace(**values)


def public_model_config() -> dict:
    config = model_settings()
    return {
        "enabled": config.llm_enabled,
        "provider": config.llm_provider,
        "model": config.active_model,
        "company": {
            "base_url": config.llm_base_url,
            "model": config.llm_model,
            "trusted_http_origin": config.llm_trusted_http_origin,
            "api_key_configured": bool(config.llm_api_key),
            "proxy_url": config.llm_proxy_url or "",
        },
        "ollama": {
            "base_url": config.ollama_base_url,
            "model": config.ollama_model,
        },
        "max_output_tokens": config.llm_max_output_tokens,
        "context_window": config.llm_context_window,
        "max_turns": config.llm_max_turns,
        "connect_timeout": config.llm_connect_timeout,
        "read_timeout": config.llm_read_timeout,
    }


def save_model_config(data: dict) -> dict:
    current = _runtime_model_config()
    provider = data.get("provider", current.get("llm_provider", settings().llm_provider))
    if provider not in {"company", "ollama"}:
        raise ValueError("Unsupported model provider")
    merged = {
        **current,
        "llm_enabled": bool(data.get("enabled", True)),
        "llm_provider": provider,
        "llm_max_output_tokens": int(data.get("max_output_tokens", current.get("llm_max_output_tokens", settings().llm_max_output_tokens))),
        "llm_context_window": int(data.get("context_window", current.get("llm_context_window", settings().llm_context_window))),
        "llm_max_turns": int(data.get("max_turns", current.get("llm_max_turns", settings().llm_max_turns))),
        "llm_connect_timeout": float(data.get("connect_timeout", current.get("llm_connect_timeout", settings().llm_connect_timeout))),
        "llm_read_timeout": float(data.get("read_timeout", current.get("llm_read_timeout", settings().llm_read_timeout))),
    }
    company = data.get("company") or {}
    ollama = data.get("ollama") or {}
    if "base_url" in company: merged["llm_base_url"] = str(company.get("base_url") or "").strip()
    if "model" in company: merged["llm_model"] = str(company.get("model") or "").strip()
    if "trusted_http_origin" in company: merged["llm_trusted_http_origin"] = str(company.get("trusted_http_origin") or "").strip()
    if "proxy_url" in company: merged["llm_proxy_url"] = str(company.get("proxy_url") or "").strip()
    if company.get("clear_api_key"):
        merged["llm_api_key"] = ""
    elif company.get("api_key"):
        merged["llm_api_key"] = str(company["api_key"])
    if "base_url" in ollama: merged["ollama_base_url"] = str(ollama.get("base_url") or "").strip()
    if "model" in ollama: merged["ollama_model"] = str(ollama.get("model") or "").strip()
    if provider == "company" and merged.get("llm_enabled") and (not merged.get("llm_base_url") or not merged.get("llm_model")):
        raise ValueError("Company model requires base_url and model")
    if provider == "ollama" and merged.get("llm_enabled") and (not merged.get("ollama_base_url") or not merged.get("ollama_model")):
        raise ValueError("Ollama model requires base_url and model")
    path = _model_config_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(merged, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(path)
    return public_model_config()
