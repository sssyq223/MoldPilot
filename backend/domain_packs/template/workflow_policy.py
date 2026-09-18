"""Minimal workflow policy proving that the host does not require mold semantics."""

CATALOG = frozenset()
WORKFLOW_TYPES = frozenset()


def validate_applicability(config):
    applicability = config.get("applicability", {})
    if not isinstance(applicability, dict):
        raise ValueError("applicability must be an object")


def validate_rule(condition):
    if not isinstance(condition, dict):
        raise ValueError("condition must be an object")


def evaluate(condition, values):
    return False


def available_workflows(db, user, resource_type, resource_id):
    return []
