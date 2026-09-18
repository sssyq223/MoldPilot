"""Approval-resource ownership supplied by the mold business pack."""
from sqlalchemy import and_, or_, select

from agent_core.host_ports import host_ports

APPROVAL_RESOURCE_TYPES = frozenset({"purchase_request", "business_subject"})


def initiated_approval_ids(db, user_id: str, limit: int = 50) -> list[str]:
    """Return newest approval instances whose business resource belongs to a user.

    Resource ownership is domain policy.  The generic host deliberately does
    not know which mold records have creators or how those records map to an
    approval resource type.
    """
    models = host_ports().models
    purchase_ids = select(models.PurchaseRequest.id).where(
        models.PurchaseRequest.created_by == user_id
    )
    subject_ids = select(models.BusinessSubject.id).where(
        models.BusinessSubject.created_by == user_id
    )
    return list(db.scalars(
        select(models.ApprovalInstance.id).where(or_(
            and_(
                models.ApprovalInstance.resource_type == "purchase_request",
                models.ApprovalInstance.resource_id.in_(purchase_ids),
            ),
            and_(
                models.ApprovalInstance.resource_type == "business_subject",
                models.ApprovalInstance.resource_id.in_(subject_ids),
            ),
        )).order_by(models.ApprovalInstance.created_at.desc()).limit(limit)
    ))
