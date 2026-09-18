"""Authorization vocabulary owned by the mold ERP business pack."""
from domain_packs.mold.erp.core.domain_schemas import PERMISSIONS as DOMAIN_PERMISSIONS
from agent_core.host_ports import host_ports


PERMISSIONS = {
    "project.read": ["id", "code", "name", "status"],
    "project.dossier.read": ["*"],
    "purchase.read": [
        "id", "number", "project_id", "material_id", "material_name",
        "category", "quantity", "unit", "due_date", "remark", "status",
        "created_at", "revision", "created_by",
    ],
    "purchase.create": ["project_id", "material_id", "quantity", "due_date", "remark"],
    "purchase.submit": ["*"],
    "purchase.approve": ["*"],
    **DOMAIN_PERMISSIONS,
    **{
        f"contact.{action}": ["*"]
        for action in (
            "read", "create", "coordinate", "assign", "respond", "record",
            "attach", "plan", "review", "close", "set_reviewer", "cancel_task",
        )
    },
}

DIMENSIONS = frozenset({"project_id", "category", "warehouse_id"})


def access(*args, **kwargs):
    return host_ports().access(*args, **kwargs)


def fingerprint(*args, **kwargs):
    return host_ports().fingerprint(*args, **kwargs)


def grants_for(*args, **kwargs):
    return host_ports().grants_for(*args, **kwargs)


def predicate(*args, **kwargs):
    return host_ports().predicate(*args, **kwargs)


def require(*args, **kwargs):
    return host_ports().require(*args, **kwargs)


def select_fields(*args, **kwargs):
    return host_ports().select_fields(*args, **kwargs)
