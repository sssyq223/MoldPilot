"""Generic audit/outbox recording through the active host model contract."""
from agent_core.host_ports import host_ports


def record(db, user, action, resource_id, detail=None, recipients=None):
    models = host_ports().models
    db.add(models.AuditEvent(
        user_id=user.id if user else None,
        action=action,
        resource_id=resource_id,
        detail=detail or {},
    ))
    db.add(models.Outbox(
        kind=action,
        resource_id=resource_id,
        payload={"recipients": recipients or [], "action": action},
    ))
