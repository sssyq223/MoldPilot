"""Configuration owned exclusively by the Mold ERP business pack."""
from functools import lru_cache
from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

# Always the checkout .env, not whatever the process current directory happens to be.
REPO_ENV_FILE = Path(__file__).resolve().parents[3] / ".env"


class MoldSettings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="MOLD_",
        env_file=str(REPO_ENV_FILE),
        env_file_encoding="utf-8",
        extra="ignore",
        populate_by_name=True,
        env_ignore_empty=True,
    )
    logistics_quote_max_valid_days: int = Field(default=183, ge=1, le=3660)
    erp_base_url: str = ""
    erp_allow_insecure_local: bool = False
    credential_encryption_key: str = ""
    erp_design_mcp_root: str = ""
    erp_design_mcp_package: str = ""
    erp_env_file: str = ""
    erp_db_host: str = ""
    erp_db_port: int = 5432
    erp_db_username: str = ""
    erp_db_password: str = ""
    erp_db_database: str = ""


@lru_cache
def settings() -> MoldSettings:
    return MoldSettings()
