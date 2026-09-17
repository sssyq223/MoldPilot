"""Compatibility facade for the active business pack's ERP adapter."""
from agent_core.domain_pack import component


_adapter = component("erp_adapter")


def __getattr__(name):
    return getattr(_adapter, name)


def __dir__():
    return sorted(set(globals()) | set(dir(_adapter)))
