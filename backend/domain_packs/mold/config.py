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
    credential_encryption_key: str = ""
    erp_design_mcp_root: str = ""
    erp_design_mcp_package: str = ""


@lru_cache
def settings() -> MoldSettings:
    return MoldSettings()
