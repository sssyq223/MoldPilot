"""Department confirmations for effective project plan changes."""
from sqlalchemy import select
from . import models as m
from .authorization import require
from .db import now
from .errors import DomainError
from .events import record


def _department_head_ids(db, department):
    group = db.scalar(select(m.AssignmentGroup).where(
        m.AssignmentGroup.kind == 'DEPARTMENT',
        m.AssignmentGroup.name == department,
        m.AssignmentGroup.active.is_(True),
    ))
    if not group:
        return []
    return list(db.scalars(select(m.User.id).join(m.AssignmentMember, m.AssignmentMember.user_id == m.User.id).where(
        m.AssignmentMember.group_id == group.id,
        m.AssignmentMember.is_head.is_(True),
        m.User.active.is_(True),
    ).order_by(m.User.display_name, m.User.id)))


def _fallback_user_ids(row):
    return [user.get('user_id') for user in row.get('users', []) if user.get('user_id')]


def serialize(db, row):
    subject = db.get(m.BusinessSubject, row.plan_change_id)
    confirmer = db.get(m.User, row.confirmed_by) if row.confirmed_by else None
    assigned = [db.get(m.User, user_id) for user_id in row.assigned_user_ids or []]
    return {
        'id': row.id,
        'plan_change_id': row.plan_change_id,
        'plan_change_number': subject.number if subject else None,
        'project_id': row.project_id,
        'department': row.department,
        'assigned_people': [
            {'id': user.id, 'name': user.display_name, 'department': user.department}
            for user in assigned if user and user.active
        ],
        'task_keys': row.task_keys or [],
        'change_types': row.change_types or [],
        'status': row.status,
        'version': row.version,
        'confirmed_by': {'id': confirmer.id, 'name': confirmer.display_name} if confirmer else None,
        'confirmed_at': row.confirmed_at.isoformat() if row.confirmed_at else None,
        'note': row.note,
        'created_at': row.created_at.isoformat(),
    }


def create_for_plan_change(db, user, subject, impact_detail):
    """Create one pending confirmation per affected department.

    Confirmation assignees prefer the department heads configured in the Agent
    organization directory. If no matching department group exists yet, the
    changed task owners remain the fallback assignees so the impact does not
    disappear silently.
    """
    created = []
    for item in impact_detail.get('affected_departments', []):
        department = item.get('department') or '未设置部门'
        existing = db.scalar(select(m.PlanDepartmentConfirmation).where(
            m.PlanDepartmentConfirmation.plan_change_id == subject.id,
            m.PlanDepartmentConfirmation.department == department,
        ).with_for_update())
        if existing:
            created.append(existing)
            continue
        assigned = _department_head_ids(db, department) or _fallback_user_ids(item)
        row = m.PlanDepartmentConfirmation(
            plan_change_id=subject.id,
            project_id=subject.project_id,
            department=department,
            assigned_user_ids=sorted(set(assigned)),
            task_keys=item.get('task_keys', []),
            change_types=item.get('change_types', []),
        )
        db.add(row)
        db.flush()
        if row.assigned_user_ids:
            record(db, user, 'plan.department_confirmation.pending', subject.id, {
                'confirmation_id': row.id,
                'department': row.department,
                'task_keys': row.task_keys,
                'change_types': row.change_types,
                'assigned_user_ids': row.assigned_user_ids,
            }, row.assigned_user_ids)
        created.append(row)
    return created


def _is_department_head(db, user, department):
    group = db.scalar(select(m.AssignmentGroup).where(
        m.AssignmentGroup.kind == 'DEPARTMENT',
        m.AssignmentGroup.name == department,
        m.AssignmentGroup.active.is_(True),
    ))
    return bool(group and db.get(m.AssignmentMember, (group.id, user.id)) and db.get(m.AssignmentMember, (group.id, user.id)).is_head)


def require_confirmable(db, user, confirmation_id, expected_version):
    row = db.scalar(select(m.PlanDepartmentConfirmation).where(
        m.PlanDepartmentConfirmation.id == confirmation_id).with_for_update())
    if not row:
        raise DomainError('NOT_FOUND', '部门确认项不存在', 404)
    subject = db.get(m.BusinessSubject, row.plan_change_id)
    if not subject or subject.kind != 'plan_change':
        raise DomainError('NOT_FOUND', '计划变更不存在', 404)
    require(db, user, 'plan_change.read', {'project_id': row.project_id})
    require(db, user, 'plan_change.execute', {'project_id': row.project_id})
    if row.version != expected_version:
        raise DomainError('VERSION_CONFLICT', '部门确认项已变化，请重新查询后再确认', 409)
    if row.status != 'PENDING':
        raise DomainError('INVALID_STATE', '部门确认项已完成，不能重复确认', 409)
    if not (user.super_admin or user.id in set(row.assigned_user_ids or []) or _is_department_head(db, user, row.department)):
        raise DomainError('FORBIDDEN', '只有该部门负责人或指定确认人可确认计划影响', 403)
    return subject, row


def confirm(db, user, confirmation_id, expected_version, note):
    subject, row = require_confirmable(db, user, confirmation_id, expected_version)
    row.status = 'CONFIRMED'
    row.version += 1
    row.confirmed_by = user.id
    row.confirmed_at = now()
    row.note = note
    record(db, user, 'plan.department_confirmation.confirmed', subject.id, {
        'confirmation_id': row.id,
        'department': row.department,
        'task_keys': row.task_keys,
        'change_types': row.change_types,
        'note': note,
    }, [subject.created_by])
    return serialize(db, row)


def visible_for_project(db, user, project_id):
    require(db, user, 'plan_change.read', {'project_id': project_id})
    rows = db.scalars(select(m.PlanDepartmentConfirmation).where(
        m.PlanDepartmentConfirmation.project_id == project_id).order_by(
            m.PlanDepartmentConfirmation.created_at.desc(),
            m.PlanDepartmentConfirmation.department,
        ).limit(100))
    return [serialize(db, row) for row in rows]
