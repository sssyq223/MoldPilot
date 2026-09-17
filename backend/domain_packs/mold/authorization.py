"""Authorization vocabulary owned by the mold ERP business pack."""
from app.domain_schemas import PERMISSIONS as DOMAIN_PERMISSIONS


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
