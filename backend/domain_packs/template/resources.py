"""A new pack must explicitly register every approvable resource type."""

APPROVAL_RESOURCE_TYPES = frozenset()


def initiated_approval_ids(db, user_id: str, limit: int = 50) -> list[str]:
    """The empty template has no domain resources owned by a user."""
    return []
