"""Compatibility facade; new runtime code imports :mod:`agent_core.domain_pack`."""
from agent_core.domain_pack import active_pack_name, component, manifest, reset_domain_pack_cache

__all__ = ["active_pack_name", "component", "manifest", "reset_domain_pack_cache"]
