"""Project-role handoffs emitted by an effective internal start notice."""
from sqlalchemy import func, select

from domain_packs.mold import models as m
from domain_packs.mold.ports.db import now
from domain_packs.mold.ports.events import record


REQUIRED_HANDOFFS = (
    ("DESIGN_OWNER", "设计负责人", "设计"),
    ("PURCHASE_OWNER", "采购负责人", "采购"),
    ("MANUFACTURING_OWNER", "制造负责人", "生产制造"),
    ("ASSEMBLY_OWNER", "装配负责人", "装配"),
    ("FINANCE_OWNER", "财务负责人", "财务"),
)


def _role_people(db, project_id, role_key):
    rows = db.execute(
        select(m.User, m.ProjectRoleMember)
        .join(m.ProjectRoleMember, m.ProjectRoleMember.user_id == m.User.id)
        .where(
            m.ProjectRoleMember.project_id == project_id,
            m.ProjectRoleMember.role_key == role_key,
            m.User.active.is_(True),
        )
        .order_by(m.User.display_name, m.User.id)
    )
    return [
        {
            "user_id": person.id,
            "name": person.display_name,
            "department": person.department or "未设置部门",
        }
        for person, _ in rows
    ]


def create_for_internal_start(db, user, subject):
    """Freeze all required handoffs without fabricating missing recipients."""
    created = []
    for role_key, role_name, department_label in REQUIRED_HANDOFFS:
        existing = db.scalar(
            select(m.InternalStartDispatch).where(
                m.InternalStartDispatch.start_subject_id == subject.id,
                m.InternalStartDispatch.role_key == role_key,
            )
        )
        if existing:
            created.append(existing)
            continue

        recipients = _role_people(db, subject.project_id, role_key)
        event = None
        if recipients:
            event = record(
                db,
                user,
                "internal_start.department_handoff",
                subject.id,
                {
                    "project_id": subject.project_id,
                    "role_key": role_key,
                    "role_name": role_name,
                    "department": department_label,
                    "recipient_snapshot": recipients,
                },
                [item["user_id"] for item in recipients],
            )
            db.flush()

        row = m.InternalStartDispatch(
            start_subject_id=subject.id,
            project_id=subject.project_id,
            role_key=role_key,
            role_name=role_name,
            department_label=department_label,
            recipient_snapshot=recipients,
            dispatch_status="QUEUED" if event else "UNASSIGNED",
            event_id=event.id if event else None,
            dispatched_by=user.id,
            dispatched_at=now(),
        )
        db.add(row)
        created.append(row)
    db.flush()
    return created


def _delivery_state(db, row):
    if row.dispatch_status == "UNASSIGNED":
        return "UNASSIGNED", 0
    event = db.get(m.Outbox, row.event_id)
    if not event:
        return "EVENT_MISSING", 0
    notification_count = db.scalar(
        select(func.count()).select_from(m.Notification).where(
            m.Notification.event_id == row.event_id
        )
    ) or 0
    processed = db.scalar(
        select(m.Inbox.id).where(m.Inbox.event_id == row.event_id).limit(1)
    )
    recipient_count = len(row.recipient_snapshot or [])
    if not processed:
        return "PENDING_DELIVERY", notification_count
    if notification_count >= recipient_count:
        return "DELIVERED", notification_count
    if notification_count:
        return "PARTIALLY_DELIVERED", notification_count
    return "FILTERED_BY_CURRENT_PERMISSION", 0


def serialize(db, row):
    delivery_state, delivered_count = _delivery_state(db, row)
    return {
        "id": row.id,
        "start_subject_id": row.start_subject_id,
        "project_id": row.project_id,
        "role_key": row.role_key,
        "role_name": row.role_name,
        "department": row.department_label,
        "assignment_source": row.assignment_source,
        "recipients": row.recipient_snapshot or [],
        "recipient_count": len(row.recipient_snapshot or []),
        "dispatch_status": row.dispatch_status,
        "delivery_state": delivery_state,
        "delivered_notification_count": delivered_count,
        "event_id": row.event_id,
        "dispatched_at": row.dispatched_at.isoformat(),
    }


def summary(db, start_subject_id=None):
    if not start_subject_id:
        return {
            "status": "NOT_CREATED",
            "required_count": len(REQUIRED_HANDOFFS),
            "assigned_count": 0,
            "delivered_count": 0,
            "gaps": [],
            "items": [],
        }
    rows = list(
        db.scalars(
            select(m.InternalStartDispatch)
            .where(m.InternalStartDispatch.start_subject_id == start_subject_id)
            .order_by(m.InternalStartDispatch.role_key)
        )
    )
    items = [serialize(db, row) for row in rows]
    missing_roles = [
        {"role_key": key, "role_name": name, "department": department}
        for key, name, department in REQUIRED_HANDOFFS
        if key not in {item["role_key"] for item in items}
    ]
    gaps = [
        {
            "role_key": item["role_key"],
            "role_name": item["role_name"],
            "department": item["department"],
            "reason": "项目角色尚未配置有效人员",
        }
        for item in items
        if item["delivery_state"] == "UNASSIGNED"
    ]
    gaps.extend({**item, "reason": "开工交接记录缺失"} for item in missing_roles)
    delivered_count = sum(
        1 for item in items if item["delivery_state"] == "DELIVERED"
    )
    assigned_count = sum(1 for item in items if item["recipient_count"] > 0)
    if len(items) < len(REQUIRED_HANDOFFS):
        status = "INCOMPLETE"
    elif gaps:
        status = "RECIPIENT_CONFIGURATION_REQUIRED"
    elif delivered_count == len(REQUIRED_HANDOFFS):
        status = "DELIVERED"
    else:
        status = "DELIVERY_PENDING"
    return {
        "status": status,
        "required_count": len(REQUIRED_HANDOFFS),
        "assigned_count": assigned_count,
        "delivered_count": delivered_count,
        "gaps": gaps,
        "items": items,
    }
