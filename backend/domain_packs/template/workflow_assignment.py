"""Neutral assignment extension for a pack without business-scoped roles."""
from agent_core.errors import DomainError


def catalog(db, user):
    return {
        "kind": "", "label": "", "scope_label": "", "context_key": "",
        "roles": [], "scopes": [], "capabilities": [],
        "responsibility_dimensions": [],
    }


def validate_role_keys(role_keys):
    if role_keys:
        raise DomainError("INVALID_WORKFLOW", "当前业务包没有发布领域角色")


def resolve(db, role_keys, context):
    validate_role_keys(role_keys)
    return [], []


def publish_candidates(db, role_keys):
    validate_role_keys(role_keys)
    return [], []


def binding_data(db, scope_id):
    raise DomainError("NOT_FOUND", "当前业务包没有领域角色配置", 404)


def save_bindings(db, user, scope_id, entries, expected_version, reason):
    raise DomainError("NOT_FOUND", "当前业务包没有领域角色配置", 404)
