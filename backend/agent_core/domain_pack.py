"""Load one explicitly configured business pack behind a small stable interface."""
from functools import lru_cache
from importlib import import_module
import os
import re


_PACK_NAME = re.compile(r"^[a-z][a-z0-9_]*$")


def active_pack_name() -> str:
    configured = os.environ.get("AGENT_BUSINESS_PACK")
    if configured is None:
        configured = import_module("domain_packs.active").PACK_NAME
    name = configured.strip().lower()
    if not _PACK_NAME.fullmatch(name):
        raise RuntimeError("AGENT_BUSINESS_PACK must be a simple installed pack name")
    return name


@lru_cache
def component(name: str):
    """Import a component only from the configured domain-pack namespace."""
    if not _PACK_NAME.fullmatch(name):
        raise RuntimeError("Invalid domain-pack component name")
    return import_module(f"domain_packs.{active_pack_name()}.{name}")


def reset_domain_pack_cache():
    """Test helper for applications that switch pack configuration before startup."""
    component.cache_clear()
