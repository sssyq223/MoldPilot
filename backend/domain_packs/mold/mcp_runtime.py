"""Locations of MCP runtimes owned by the Mold business pack."""
from pathlib import Path

from domain_packs.mold.config import settings


_PACK_ROOT = Path(__file__).resolve().parent


def erp_design_upload_runtime() -> Path:
    configured = settings().erp_design_mcp_root.strip()
    return Path(configured).expanduser().resolve() if configured else (
        _PACK_ROOT / "mcp" / "erp-design-upload"
    )
