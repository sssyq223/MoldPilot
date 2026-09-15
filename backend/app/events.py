from .models import AuditEvent, Outbox


def record(db, user, action, resource_id, detail=None, recipients=None):
    db.add(AuditEvent(user_id=user.id if user else None, action=action, resource_id=resource_id, detail=detail or {}))
    db.add(Outbox(kind=action, resource_id=resource_id, payload={"recipients": recipients or [], "action": action}))
