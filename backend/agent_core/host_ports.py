"""Validated loading of the generic host services available to a domain pack.

Business packs depend on this contract instead of importing arbitrary host
implementation modules.  The concrete adapter is selected independently from
the active business pack so another host can embed the same Agent Core.
"""
from dataclasses import dataclass
from functools import lru_cache
from importlib import import_module
from types import ModuleType
from typing import Any, Callable
import os
import re


_MODULE_NAME = re.compile(r"^[a-zA-Z_][a-zA-Z0-9_.]*$")


@dataclass(frozen=True)
class HostPorts:
    models: ModuleType
    object_storage: ModuleType
    access: Callable[..., Any]
    grants_for: Callable[..., Any]
    fingerprint: Callable[..., str]
    predicate: Callable[..., Any]
    require: Callable[..., Any]
    select_fields: Callable[..., dict]
    content_hash: Callable[[Any], str]
    proposal_confirmation_policy: Callable[..., dict]
    settings: Callable[[], Any]
    model_settings: Callable[[], Any]
    get_db: Callable[..., Any]
    now: Callable[[], Any]
    record: Callable[..., Any]
    current_user: Callable[..., Any]
    conversation_files: Callable[..., Any]
    uploaded_file: Callable[..., Any]
    file_metadata: Callable[..., dict]


@lru_cache
def host_ports() -> HostPorts:
    module_name = os.environ.get("AGENT_HOST_PORTS_MODULE", "app.host_ports").strip()
    if not _MODULE_NAME.fullmatch(module_name):
        raise RuntimeError("AGENT_HOST_PORTS_MODULE must be an importable module name")
    adapter = import_module(module_name)
    ports = getattr(adapter, "PORTS", None)
    if not isinstance(ports, HostPorts):
        raise RuntimeError(f"{module_name} must expose PORTS as HostPorts")
    return ports


def reset_host_ports_cache() -> None:
    """Test helper for hosts that replace their port adapter before startup."""
    host_ports.cache_clear()
