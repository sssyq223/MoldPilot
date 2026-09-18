"""MoldPilot mold-manufacturing domain pack."""
from functools import lru_cache
from importlib import import_module
import re

PACK_ID = "mold"
DISPLAY_NAME = "Mold manufacturing"

_MODULE_NAME = re.compile(r"^[a-z][a-z0-9_]*$")
_COMPATIBILITY_MODULES = {
    "attachment_models": "erp.change.attachment_models",
    "bpm": "ports.bpm",
    "business": "erp.core.business",
    "contact_lifecycle": "erp.change.contact_lifecycle",
    "contact_models": "erp.change.contact_models",
    "contact_tools": "tools.erp.change.contact_tools",
    "contacts": "erp.change.contacts",
    "domain_extensions": "erp.core.domain_extensions",
    "domain_models": "erp.core.domain_models",
    "domain_schemas": "erp.core.domain_schemas",
    "domains": "erp.core.domains",
    "erp_design_mcp": "tools.erp.design.erp_design_mcp",
    "erp_progress": "erp.design.erp_progress",
    "files": "ports.files",
    "plan_confirmations": "erp.project.plan_confirmations",
    "procurement": "erp.procurement.procurement",
    "project_closure": "erp.project.project_closure",
    "workflow_selection": "erp.core.workflow_selection",
}


@lru_cache
def _categorized_module(name: str):
    """Resolve an explicitly registered compatibility module."""
    if not _MODULE_NAME.fullmatch(name):
        raise AttributeError(name)
    module_name = _COMPATIBILITY_MODULES.get(name)
    if not module_name:
        raise AttributeError(name)
    return import_module("." + module_name, __name__)


def __getattr__(name: str):
    value = _categorized_module(name)
    globals()[name] = value
    return value
