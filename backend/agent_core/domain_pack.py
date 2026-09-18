"""Load one explicitly configured business pack behind a small stable interface."""
from functools import lru_cache
from importlib import import_module
import os
import re


_PACK_NAME = re.compile(r"^[a-z][a-z0-9_]*$")


def validate_public_metadata(metadata: object, pack_name: str) -> dict:
    """Validate the small, serializable contract exposed to the generic host UI."""
    if not isinstance(metadata, dict) or metadata.get("id") != pack_name:
        raise RuntimeError("Domain-pack manifest PUBLIC_METADATA.id must match the active pack")
    if not isinstance(metadata.get("product_name"), str) or not metadata["product_name"].strip():
        raise RuntimeError("Domain-pack manifest requires a product_name")

    workspace_tabs = metadata.get("workspace_tabs", [])
    if not isinstance(workspace_tabs, list) or not all(
        isinstance(tab, dict) and isinstance(tab.get("key"), str) and tab["key"].strip()
        for tab in workspace_tabs
    ):
        raise RuntimeError("Domain-pack workspace_tabs must contain objects with string keys")
    workspace_targets = {tab["key"] for tab in workspace_tabs}

    presentation = metadata.get("proposal_presentation", {})
    if not isinstance(presentation, dict):
        raise RuntimeError("Domain-pack proposal_presentation must be an object")
    for key in ("action_prefixes", "action_suffixes"):
        values = presentation.get(key, [])
        if not isinstance(values, list) or not all(isinstance(value, str) for value in values):
            raise RuntimeError(f"Domain-pack proposal_presentation.{key} must be a string list")
    value_names = presentation.get("value_names", {})
    if not isinstance(value_names, dict) or not all(
        isinstance(key, str) and isinstance(value, str)
        for key, value in value_names.items()
    ):
        raise RuntimeError("Domain-pack proposal_presentation.value_names must map strings to strings")
    detail_links = presentation.get("detail_links", {})
    if not isinstance(detail_links, dict):
        raise RuntimeError("Domain-pack proposal_presentation.detail_links must be an object")
    for kind, link in detail_links.items():
        if not isinstance(kind, str) or not isinstance(link, dict) or any(
            not isinstance(link.get(field), str) or not link[field].strip()
            for field in ("target", "receipt_field", "label")
        ):
            raise RuntimeError(
                "Each proposal detail link requires string target, receipt_field and label"
            )
        if link["target"] not in workspace_targets:
            raise RuntimeError("Proposal detail link target must name an installed workspace tab")
    return metadata


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


@lru_cache
def manifest():
    """Return the selected pack's validated host-integration contract."""
    value = component("manifest")
    validate_public_metadata(getattr(value, "PUBLIC_METADATA", None), active_pack_name())
    if not callable(getattr(value, "install", None)):
        raise RuntimeError("Domain-pack manifest requires install(app, domain_router)")
    if not callable(getattr(value, "conversation_title", None)):
        raise RuntimeError("Domain-pack manifest requires conversation_title(prompt)")
    return value


@lru_cache
def authorization_contract():
    """Return validated permissions and scope dimensions for the active pack."""
    value = component("authorization")
    permissions = getattr(value, "PERMISSIONS", None)
    dimensions = getattr(value, "DIMENSIONS", None)
    if not isinstance(permissions, dict) or not all(
        isinstance(name, str)
        and name.strip()
        and isinstance(fields, (list, tuple))
        and all(isinstance(field, str) and field for field in fields)
        for name, fields in permissions.items()
    ):
        raise RuntimeError("Domain-pack authorization.PERMISSIONS must map names to field lists")
    if not isinstance(dimensions, (set, frozenset)) or not all(
        isinstance(dimension, str) and dimension for dimension in dimensions
    ):
        raise RuntimeError("Domain-pack authorization.DIMENSIONS must be a string set")
    return value


@lru_cache
def resource_contract():
    """Return validated resource types accepted by generic persistence records."""
    value = component("resources")
    approval_types = getattr(value, "APPROVAL_RESOURCE_TYPES", None)
    if not isinstance(approval_types, (set, frozenset)) or not all(
        isinstance(resource_type, str) and resource_type for resource_type in approval_types
    ):
        raise RuntimeError(
            "Domain-pack resources.APPROVAL_RESOURCE_TYPES must be a string set"
        )
    if not callable(getattr(value, "initiated_approval_ids", None)):
        raise RuntimeError(
            "Domain-pack resources must provide initiated_approval_ids(db, user_id, limit)"
        )
    return value


@lru_cache
def migration_contract():
    """Return the ordered Core + domain migration contract for the active pack."""
    value = component("migrations")
    stages = getattr(value, "STAGES", None)
    if not isinstance(stages, tuple) or not stages:
        raise RuntimeError("Domain-pack migrations.STAGES must be a non-empty tuple")
    names = set()
    configs = set()
    tables = set()
    for stage in stages:
        if not isinstance(stage, dict) or set(stage) != {"name", "config", "version_table"}:
            raise RuntimeError("Each migration stage requires name, config and version_table")
        name, config_file, version_table = (
            stage["name"], stage["config"], stage["version_table"]
        )
        if not isinstance(name, str) or not _PACK_NAME.fullmatch(name) or name in names:
            raise RuntimeError("Migration stage names must be unique lowercase identifiers")
        if (not isinstance(config_file, str) or not config_file.endswith(".ini")
                or config_file in configs):
            raise RuntimeError("Migration stage configs must name unique ini files")
        if (not isinstance(version_table, str) or not _PACK_NAME.fullmatch(version_table)
                or version_table in tables):
            raise RuntimeError("Migration version tables must be unique lowercase identifiers")
        names.add(name)
        configs.add(config_file)
        tables.add(version_table)
    legacy = getattr(value, "LEGACY", None)
    if legacy is not None and (
        not isinstance(legacy, dict)
        or set(legacy) != {"config", "version_table"}
        or not isinstance(legacy["config"], str)
        or not legacy["config"].endswith(".ini")
        or not isinstance(legacy["version_table"], str)
        or not _PACK_NAME.fullmatch(legacy["version_table"])
        or legacy["config"] in configs
        or legacy["version_table"] in tables
    ):
        raise RuntimeError(
            "Domain-pack migrations.LEGACY must name a distinct config and version table"
        )
    return value


def reset_domain_pack_cache():
    """Test helper for applications that switch pack configuration before startup."""
    manifest.cache_clear()
    authorization_contract.cache_clear()
    resource_contract.cache_clear()
    migration_contract.cache_clear()
    component.cache_clear()
