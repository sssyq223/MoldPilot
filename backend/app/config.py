from functools import lru_cache
import hashlib
import json
import os
from pathlib import Path
import socket
from types import SimpleNamespace
from typing import Literal
from urllib.parse import urlsplit
from uuid import uuid4
from pydantic import AliasChoices, Field, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


def _compatible(name: str, default, **constraints):
    """Prefer neutral AGENT_* variables while accepting legacy MOLD_* installs."""
    upper = name.upper()
    return Field(
        default=default,
        validation_alias=AliasChoices(f"AGENT_{upper}", f"MOLD_{upper}"),
        **constraints,
    )


def _default_worker_scope() -> str:
    """Return a stable, installation-local queue scope.

    The hostname separates machines connected to one database.  The code root
    digest also separates two checkouts (and therefore potentially two runtime
    versions) on the same machine.  Deployments can override this with
    AGENT_WORKER_SCOPE when API and worker processes use different paths.
    """
    code_root = str(Path(__file__).resolve().parents[2]).casefold()
    root_digest = hashlib.sha256(code_root.encode("utf-8")).hexdigest()[:12]
    return f"{socket.gethostname()}:{root_digest}"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="AGENT_", env_file=".env", extra="ignore", populate_by_name=True
    )
    database_url: str = _compatible("database_url", "postgresql+psycopg://postgres@127.0.0.1:5432/agent_workbench")
    redis_url: str = _compatible("redis_url", "redis://127.0.0.1:6379/0")
    redis_home: str = _compatible("redis_home", "D:\\Redis")
    restore_database_url: str = _compatible("restore_database_url", "")
    pg_dump_path: str = _compatible("pg_dump_path", "")
    pg_restore_path: str = _compatible("pg_restore_path", "")
    pg_client_image: str = _compatible("pg_client_image", "postgres:18-alpine")
    acceptance_evidence_file: str = _compatible("acceptance_evidence_file", ".local/acceptance-gates.json")
    origin: str = _compatible("origin", "http://127.0.0.1:5173")
    cookie_secure: bool = _compatible("cookie_secure", True)
    environment: str = _compatible("environment", "development")
    audit_log_retention_days: int = _compatible("audit_log_retention_days", 0, ge=0, le=3650)
    app_log_retention_days: int = _compatible("app_log_retention_days", 0, ge=0, le=3650)
    access_log_retention_days: int = _compatible("access_log_retention_days", 0, ge=0, le=3650)
    model_log_retention_days: int = _compatible("model_log_retention_days", 0, ge=0, le=3650)
    llm_base_url: str = _compatible("llm_base_url", "")
    # Explicit HTTP origin for a trusted private-IP model service; empty requires HTTPS.
    llm_trusted_http_origin: str = _compatible("llm_trusted_http_origin", "")
    llm_provider: Literal['company','ollama'] = _compatible("llm_provider", "company")
    ollama_base_url: str = _compatible("ollama_base_url", "http://127.0.0.1:11434")
    ollama_model: str = _compatible("ollama_model", "deepseek-r1:7b")
    llm_api_key: str = _compatible("llm_api_key", "")
    llm_proxy_url: str | None = _compatible("llm_proxy_url", None)
    llm_tls_max_version: Literal["auto", "1.2"] = _compatible("llm_tls_max_version", "auto")
    llm_tls_key_exchange: Literal["auto", "x25519"] = _compatible("llm_tls_key_exchange", "auto")
    llm_connect_timeout: float = _compatible("llm_connect_timeout", 20, gt=0, le=20)
    llm_read_timeout: float = _compatible("llm_read_timeout", 60, gt=0, le=75)
    llm_model: str = _compatible("llm_model", "")
    llm_max_turns: int = _compatible("llm_max_turns", 12)
    llm_max_output_tokens: int = _compatible("llm_max_output_tokens", 2048)
    llm_context_window: int = _compatible("llm_context_window", 8192)
    llm_enabled: bool = _compatible("llm_enabled", False)
    ocr_service_url: str = _compatible("ocr_service_url", "http://127.0.0.1:18081")
    ocr_service_token: str = _compatible("ocr_service_token", "")
    ocr_service_connect_timeout: float = _compatible("ocr_service_connect_timeout", 5, gt=0, le=30)
    ocr_service_read_timeout: float = _compatible("ocr_service_read_timeout", 120, gt=0, le=180)
    ocr_render_dpi: int = _compatible("ocr_render_dpi", 300, ge=72, le=600)
    ocr_min_text_chars: int = _compatible("ocr_min_text_chars", 40, ge=0, le=10000)
    ocr_image_coverage_threshold: float = _compatible(
        "ocr_image_coverage_threshold", 0.25, ge=0, le=1
    )
    ocr_max_pages: int = _compatible("ocr_max_pages", 200, ge=1, le=500)
    ocr_max_attempts: int = _compatible("ocr_max_attempts", 5, ge=1, le=10)
    document_model_profile_id: str = _compatible("document_model_profile_id", "")
    document_model_reasoning_effort: Literal['','low','high','max'] = _compatible("document_model_reasoning_effort", "")
    document_model_total_timeout: float = _compatible("document_model_total_timeout", 240, gt=0, le=600)
    document_model_base_url: str = _compatible("document_model_base_url", "")
    document_model_api_key: str = _compatible("document_model_api_key", "")
    document_model: str = _compatible("document_model", "Qwen3-30B-A3B-Instruct")
    document_model_trusted_http_origin: str = _compatible("document_model_trusted_http_origin", "")
    document_model_connect_timeout: float = _compatible(
        "document_model_connect_timeout", 10, gt=0, le=30
    )
    document_model_read_timeout: float = _compatible(
        "document_model_read_timeout", 75, gt=0, le=180
    )
    worker_secret: str = _compatible("worker_secret", "")
    worker_scope: str = _compatible("worker_scope", _default_worker_scope(), min_length=1, max_length=160)
    api_base_url: str = _compatible("api_base_url", "http://127.0.0.1:8000")
    file_backend: Literal['local','s3'] = _compatible("file_backend", "local")
    file_local_root: str = _compatible("file_local_root", ".local/files")
    file_max_bytes: int = _compatible("file_max_bytes", 20*1024*1024, ge=1024, le=50*1024*1024)
    file_daily_bytes: int = _compatible("file_daily_bytes", 200*1024*1024, ge=1024)
    file_s3_endpoint: str = _compatible("file_s3_endpoint", "")
    file_s3_bucket: str = _compatible("file_s3_bucket", "")
    file_s3_region: str = _compatible("file_s3_region", "us-east-1")
    file_s3_access_key: str = _compatible("file_s3_access_key", "")
    file_s3_secret_key: str = _compatible("file_s3_secret_key", "")

    @model_validator(mode="after")
    def validate_file_storage(self):
        """Fail fast when file storage is unsafe or incomplete for the environment."""
        environment = self.environment.strip().lower()
        development_like = environment in {"development", "dev", "test", "testing"}

        if not development_like and self.file_backend != "s3":
            raise ValueError(
                "生产、预发布等非开发环境必须使用 AGENT_FILE_BACKEND=s3；"
                "本地文件存储仅允许 development/test"
            )

        if self.file_backend == "s3":
            missing = [
                name for name, value in {
                    "AGENT_FILE_S3_ENDPOINT": self.file_s3_endpoint,
                    "AGENT_FILE_S3_BUCKET": self.file_s3_bucket,
                    "AGENT_FILE_S3_ACCESS_KEY": self.file_s3_access_key,
                    "AGENT_FILE_S3_SECRET_KEY": self.file_s3_secret_key,
                }.items() if not str(value or "").strip()
            ]
            if missing:
                raise ValueError("S3 文件存储配置不完整，缺少：" + ", ".join(missing))
            endpoint = urlsplit(self.file_s3_endpoint.strip())
            if endpoint.scheme not in {"http", "https"} or not endpoint.hostname:
                raise ValueError("AGENT_FILE_S3_ENDPOINT 必须是带主机名的 http(s) 地址")
            if endpoint.username or endpoint.password:
                raise ValueError("AGENT_FILE_S3_ENDPOINT 不应在 URL 中携带账号或密码")
            if not development_like and endpoint.scheme != "https":
                raise ValueError("生产、预发布等非开发环境的 S3 存储必须使用 HTTPS")

        return self

    @property
    def active_model(self):
        return self.ollama_model if self.llm_provider=='ollama' else self.llm_model


@lru_cache
def settings() -> Settings:
    return Settings()


def _model_config_path() -> Path:
    return Path(os.environ.get("AGENT_MODEL_CONFIG_FILE") or os.environ.get(
        "MOLD_MODEL_CONFIG_FILE", ".local/model-config.json"
    ))


def _runtime_model_config() -> dict:
    path = _model_config_path()
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except FileNotFoundError:
        return {}
    except (OSError, json.JSONDecodeError):
        return {}


_PROFILE_RUNTIME_KEYS = {
    "llm_enabled", "llm_provider", "llm_base_url", "llm_trusted_http_origin",
    "llm_api_key", "llm_proxy_url", "llm_tls_max_version", "llm_tls_key_exchange",
    "llm_connect_timeout", "llm_read_timeout", "llm_model", "ollama_base_url",
    "ollama_model", "llm_max_turns", "llm_max_output_tokens", "llm_context_window",
}


def _profile_name(data: dict, fallback: str = "模型配置") -> str:
    provider = data.get("llm_provider")
    model = data.get("ollama_model") if provider == "ollama" else data.get("llm_model")
    return str(model or fallback).strip() or fallback


def _legacy_profile(data: dict, profile_id: str, *, fallback_name: str) -> dict:
    profile = {key: data[key] for key in _PROFILE_RUNTIME_KEYS if key in data}
    profile["id"] = profile_id
    profile["name"] = str(data.get("name") or _profile_name(profile, fallback_name)).strip()
    return profile


def _environment_profile() -> dict:
    base = settings()
    return {
        "id": "environment",
        "name": _profile_name({"llm_provider": base.llm_provider, "llm_model": base.llm_model,
                               "ollama_model": base.ollama_model}, "环境变量配置"),
        "llm_enabled": base.llm_enabled,
        "llm_provider": base.llm_provider,
        "llm_base_url": base.llm_base_url,
        "llm_trusted_http_origin": base.llm_trusted_http_origin,
        "llm_api_key": base.llm_api_key,
        "llm_proxy_url": base.llm_proxy_url,
        "llm_tls_max_version": base.llm_tls_max_version,
        "llm_tls_key_exchange": base.llm_tls_key_exchange,
        "llm_connect_timeout": base.llm_connect_timeout,
        "llm_read_timeout": base.llm_read_timeout,
        "llm_model": base.llm_model,
        "ollama_base_url": base.ollama_base_url,
        "ollama_model": base.ollama_model,
        "llm_max_turns": base.llm_max_turns,
        "llm_max_output_tokens": base.llm_max_output_tokens,
        "llm_context_window": base.llm_context_window,
    }


def _profile_document() -> dict:
    """Read v2 profiles or present the legacy file and .env as migratable profiles."""
    raw = _runtime_model_config()
    if raw.get('version') == 3:
        from .model_catalog import flatten_catalog
        return flatten_catalog(raw)
    profiles = raw.get("profiles")
    if isinstance(profiles, list) and profiles:
        valid = [dict(item) for item in profiles
                 if isinstance(item, dict) and item.get("id") and item.get("name")]
        if valid:
            active_id = str(raw.get("active_profile_id") or valid[0]["id"])
            if not any(str(item["id"]) == active_id for item in valid):
                active_id = str(valid[0]["id"])
            return {"version": 2, "active_profile_id": active_id, "profiles": valid}

    candidates = []
    if any(key in raw for key in _PROFILE_RUNTIME_KEYS):
        candidates.append(_legacy_profile(raw, "runtime", fallback_name="当前模型"))
    env_profile = _environment_profile()
    env_identity = (env_profile.get("llm_provider"), env_profile.get("llm_base_url"),
                    env_profile.get("llm_model"), env_profile.get("ollama_base_url"),
                    env_profile.get("ollama_model"))
    current_identities = {
        (item.get("llm_provider"), item.get("llm_base_url"), item.get("llm_model"),
         item.get("ollama_base_url"), item.get("ollama_model")) for item in candidates
    }
    if (env_profile.get("llm_model") or env_profile.get("ollama_model")) and env_identity not in current_identities:
        candidates.append(env_profile)
    if not candidates:
        candidates.append(env_profile)
    return {"version": 2, "active_profile_id": str(candidates[0]["id"]), "profiles": candidates}


def _write_profile_document(document: dict) -> None:
    from .model_catalog import config_lock
    with config_lock():
        if _runtime_model_config().get('version') == 3:
            raise ValueError('MODEL_CONFIG_V3_REQUIRED')
        path = _model_config_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(path.suffix + ".tmp")
        tmp.write_text(json.dumps(document, ensure_ascii=False, indent=2), encoding="utf-8")
        tmp.replace(path)


def _active_profile(document: dict | None = None) -> dict:
    document = document or _profile_document()
    active_id = str(document.get("active_profile_id") or "")
    return next((item for item in document["profiles"] if str(item["id"]) == active_id),
                document["profiles"][0])


def _settings_values(data: dict) -> dict:
    base = settings()
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
        "llm_reasoning_policy": str(data.get('llm_reasoning_policy') or 'default'),
        "llm_reasoning_effort": str(data.get('llm_reasoning_effort') or ''),
        "llm_max_turns": int(data.get("llm_max_turns", base.llm_max_turns)),
        "llm_max_output_tokens": int(data.get("llm_max_output_tokens", base.llm_max_output_tokens)),
        "llm_context_window": int(data.get("llm_context_window", base.llm_context_window)),
        "worker_secret": base.worker_secret,
        "api_base_url": base.api_base_url,
    }
    values["active_model"] = values["ollama_model"] if values["llm_provider"] == "ollama" else values["llm_model"]
    return values


def document_model_settings():
    """固定引用文档专用 profile，不随聊天窗口切换模型，也不复制 API Key。"""
    base = settings()
    values = base.model_dump()
    if base.document_model_profile_id:
        profile = next((p for p in _profile_document()['profiles']
                        if str(p['id']) == base.document_model_profile_id), None)
        if profile is None:
            raise ValueError('Document model profile not found')
        runtime = _settings_values(profile)
        if runtime['llm_provider'] != 'company' or not runtime['llm_enabled']:
            raise ValueError('Document model requires an enabled OpenAI-compatible profile')
        for target, source in {
            'document_model_base_url':'llm_base_url', 'document_model_api_key':'llm_api_key',
            'document_model':'llm_model', 'document_model_trusted_http_origin':'llm_trusted_http_origin',
            'document_model_proxy_url':'llm_proxy_url', 'document_model_tls_max_version':'llm_tls_max_version',
            'document_model_tls_key_exchange':'llm_tls_key_exchange',
        }.items():
            values[target] = runtime[source]
    return SimpleNamespace(**values)


def model_settings():
    """Return model settings with local UI overrides applied.

    The base Settings object remains environment-driven. The small runtime JSON file
    lets the desktop UI update model routing without mutating .env or restarting the
    API process. Secrets are never included in public_model_config().
    """
    values = _settings_values(_active_profile())
    values["config_version"] = json.dumps({k: v for k, v in values.items() if k not in {"worker_secret", "api_base_url"}}, sort_keys=True, ensure_ascii=False)
    return SimpleNamespace(**values)


def _public_profile(profile: dict) -> dict:
    config = SimpleNamespace(**_settings_values(profile))
    return {
        "id": str(profile.get("id") or ""),
        "name": str(profile.get("name") or _profile_name(profile)),
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


def public_model_config() -> dict:
    document = _profile_document()
    active = _active_profile(document)
    result = _public_profile(active)
    result["active_profile_id"] = str(active["id"])
    result["profiles"] = [_public_profile(profile) for profile in document["profiles"]]
    return result


def _merge_profile(current: dict, data: dict, *, profile_id: str, name: str | None = None) -> dict:
    provider = data.get("provider", current.get("llm_provider", settings().llm_provider))
    if provider not in {"company", "ollama"}:
        raise ValueError("Unsupported model provider")
    merged = {
        **{key: value for key, value in current.items() if key in _PROFILE_RUNTIME_KEYS},
        "id": profile_id,
        "name": str(name if name is not None else current.get("name") or _profile_name(current)).strip(),
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
    if not merged["name"]:
        raise ValueError("Profile name is required")
    return merged


def save_model_config(data: dict) -> dict:
    document = _profile_document()
    active = _active_profile(document)
    replacement = _merge_profile(active, data, profile_id=str(active["id"]))
    document["profiles"] = [replacement if str(item["id"]) == str(active["id"]) else item
                            for item in document["profiles"]]
    _write_profile_document(document)
    return public_model_config()


def create_model_profile(data: dict) -> dict:
    document = _profile_document()
    profile_id = str(uuid4())
    profile = _merge_profile({}, data, profile_id=profile_id, name=data.get("name"))
    document["profiles"].append(profile)
    document["active_profile_id"] = profile_id
    _write_profile_document(document)
    return public_model_config()


def update_model_profile(profile_id: str, data: dict) -> dict:
    document = _profile_document()
    current = next((item for item in document["profiles"] if str(item["id"]) == profile_id), None)
    if current is None:
        raise KeyError(profile_id)
    replacement = _merge_profile(current, data, profile_id=profile_id, name=data.get("name"))
    document["profiles"] = [replacement if str(item["id"]) == profile_id else item
                            for item in document["profiles"]]
    _write_profile_document(document)
    return public_model_config()


def activate_model_profile(profile_id: str) -> dict:
    document = _profile_document()
    if not any(str(item["id"]) == profile_id for item in document["profiles"]):
        raise KeyError(profile_id)
    document["active_profile_id"] = profile_id
    _write_profile_document(document)
    return public_model_config()


def delete_model_profile(profile_id: str) -> dict:
    document = _profile_document()
    if len(document["profiles"]) <= 1:
        raise ValueError("At least one model profile must remain")
    remaining = [item for item in document["profiles"] if str(item["id"]) != profile_id]
    if len(remaining) == len(document["profiles"]):
        raise KeyError(profile_id)
    document["profiles"] = remaining
    if str(document["active_profile_id"]) == profile_id:
        document["active_profile_id"] = str(remaining[0]["id"])
    _write_profile_document(document)
    return public_model_config()
