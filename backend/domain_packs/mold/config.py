"""Configuration owned exclusively by the Mold ERP business pack."""
from functools import lru_cache

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class MoldSettings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="MOLD_", env_file=".env", extra="ignore", populate_by_name=True
    )
    logistics_quote_max_valid_days: int = Field(default=183, ge=1, le=3660)
    erp_base_url: str = ""
    erp_allow_insecure_local: bool = False
    # The ERP is a separate application/database.  When enabled, write-side
    # design actions run the ERP's local service worker against PostgreSQL and
    # do not forward MoldPilot browser tokens to ERP HTTP endpoints.
    erp_direct_enabled: bool = True
    erp_database_url: str = ""
    erp_project_root: str = ""
    erp_python: str = ""
    erp_actor_username: str = "admin"
    erp_direct_timeout_seconds: int = Field(default=300, ge=30, le=1800)
    credential_encryption_key: str = ""
    erp_design_mcp_root: str = ""
    erp_design_mcp_package: str = ""


@lru_cache
def settings() -> MoldSettings:
    return MoldSettings()
