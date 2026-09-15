from functools import lru_cache
from typing import Literal
from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="MOLD_", env_file=".env", extra="ignore")
    database_url: str = "postgresql+psycopg://agent_app@127.0.0.1:55432/agent_db"
    redis_url: str = "redis://127.0.0.1:56379/0"
    origin: str = "http://127.0.0.1:5173"
    cookie_secure: bool = True
    environment: str = "development"
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
