"""HTTP lifecycle for generic, versioned workflow work calendars."""
from fastapi import APIRouter, Depends, Query
from sqlalchemy import func, select, text

from agent_core.hashing import content_hash
from agent_core.workflow_calendars import validate_calendar

from . import authorization as auth, models as m, schemas as s
from .db import get_db, now
from .errors import DomainError
from .events import record
from .security import current_user


router = APIRouter(prefix="/api/workflow-calendars")


def calendar_data(item):
    editable = {
        "name": item.name,
        "timezone": item.timezone,
        "config": item.config,
    }
    return {
        "id": item.id,
        "calendar_key": item.calendar_key,
        "version": item.version,
        "name": item.name,
        "status": item.status,
        "timezone": item.timezone,
        "config": item.config,
        "package_hash": item.package_hash,
        "edit_hash": content_hash(editable),
        "created_at": item.created_at,
        "published_at": item.published_at,
    }


def require_workflow_calendars(db, config):
    ids = {
        node.get("sla", {}).get("calendar_id")
        for node in config.get("nodes", [])
        if node.get("sla", {}).get("calendar_id")
    }
    if not ids:
        return
    rows = {
        row.id: row for row in db.scalars(
            select(m.WorkflowCalendar).where(m.WorkflowCalendar.id.in_(ids))
        )
    }
    missing = [calendar_id for calendar_id in ids if calendar_id not in rows]
    unavailable = [calendar_id for calendar_id in ids if calendar_id in rows and rows[calendar_id].status != "PUBLISHED"]
    if missing:
        raise DomainError("CALENDAR_NOT_FOUND", "流程引用的工作日历不存在", 409)
    if unavailable:
        raise DomainError("CALENDAR_NOT_PUBLISHED", "流程只能引用已发布的工作日历版本", 409)


@router.get("")
def calendars(
    offset: int = Query(0, ge=0),
    limit: int = Query(100, ge=1, le=100),
    user=Depends(current_user),
    db=Depends(get_db),
):
    can_design = auth.access(db, user, "workflow.design", {}).allowed
    query = select(m.WorkflowCalendar).order_by(
        m.WorkflowCalendar.calendar_key, m.WorkflowCalendar.version.desc()
    )
    if not can_design:
        query = query.where(m.WorkflowCalendar.status == "PUBLISHED")
    return [calendar_data(row) for row in db.scalars(query.offset(offset).limit(limit))]


@router.post("")
def create_calendar(data: s.WorkflowCalendarInput, user=Depends(current_user), db=Depends(get_db)):
    auth.require(db, user, "workflow.design")
    normalized = validate_calendar(data.timezone, data.config)
    db.execute(text("SELECT pg_advisory_xact_lock(hashtextextended(:key, 0))"), {
        "key": "workflow-calendar:" + data.calendar_key,
    })
    version = (db.scalar(select(func.max(m.WorkflowCalendar.version)).where(
        m.WorkflowCalendar.calendar_key == data.calendar_key
    )) or 0) + 1
    item = m.WorkflowCalendar(
        calendar_key=data.calendar_key,
        version=version,
        name=data.name,
        timezone=data.timezone,
        config=normalized,
        created_by=user.id,
    )
    db.add(item)
    db.flush()
    record(db, user, "workflow.calendar.draft.created", item.id, {"version": version})
    db.commit()
    return calendar_data(item)


@router.put("/{calendar_id}")
def edit_calendar(calendar_id: str, data: s.WorkflowCalendarEditInput, user=Depends(current_user), db=Depends(get_db)):
    auth.require(db, user, "workflow.design")
    item = db.scalar(select(m.WorkflowCalendar).where(
        m.WorkflowCalendar.id == calendar_id
    ).with_for_update())
    if not item:
        raise DomainError("NOT_FOUND", "工作日历不存在", 404)
    if item.status != "DRAFT":
        raise DomainError("PUBLISHED_IMMUTABLE", "已发布日历不能覆盖，请另存为新版本", 409)
    if data.expected_hash != calendar_data(item)["edit_hash"]:
        raise DomainError("VERSION_CONFLICT", "工作日历草稿已变化，请重新打开", 409)
    item.name = data.name
    item.timezone = data.timezone
    item.config = validate_calendar(data.timezone, data.config)
    record(db, user, "workflow.calendar.draft.updated", item.id)
    db.commit()
    return calendar_data(item)


@router.post("/{calendar_id}/publish")
def publish_calendar(calendar_id: str, user=Depends(current_user), db=Depends(get_db)):
    auth.require(db, user, "workflow.publish")
    item = db.scalar(select(m.WorkflowCalendar).where(
        m.WorkflowCalendar.id == calendar_id
    ).with_for_update())
    if not item:
        raise DomainError("NOT_FOUND", "工作日历不存在", 404)
    if item.status == "PUBLISHED":
        return calendar_data(item)
    normalized = validate_calendar(item.timezone, item.config)
    item.config = normalized
    item.package_hash = content_hash({
        "calendar_key": item.calendar_key,
        "version": item.version,
        "timezone": item.timezone,
        "config": normalized,
    })
    item.status = "PUBLISHED"
    item.published_by = user.id
    item.published_at = now()
    record(db, user, "workflow.calendar.published", item.id, {"hash": item.package_hash})
    db.commit()
    return calendar_data(item)
