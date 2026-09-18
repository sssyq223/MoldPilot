"""PostgreSQL-backed approval timers and restart-safe wake processing."""
from __future__ import annotations

from datetime import timedelta
import uuid

from sqlalchemy import func, or_, select, update

from agent_core.host_ports import host_ports
from agent_core.workflow_calendars import add_working_hours


ACTIVE_STATUS = "SCHEDULED"
TERMINAL_STATUSES = {"FIRED", "CANCELLED", "STALE", "FAILED"}


def _models():
    return host_ports().models


def _now():
    return host_ports().now()


def schedule_stage_timers(db, instance, node, *, started_at=None):
    """Create one durable schedule per stage entry and return UI metadata."""
    sla = node.get("sla")
    if not sla:
        return None
    models = _models()
    calendar = None
    if sla.get("calendar_id"):
        calendar = db.get(models.WorkflowCalendar, sla["calendar_id"])
        if not calendar or calendar.status != "PUBLISHED":
            raise ValueError("published workflow calendar is unavailable")
    existing = db.scalar(
        select(models.WorkflowTimer)
        .where(
            models.WorkflowTimer.instance_id == instance.id,
            models.WorkflowTimer.stage_index == instance.stage_index,
            models.WorkflowTimer.node_key == node["key"],
            models.WorkflowTimer.timer_key == "DUE",
            models.WorkflowTimer.status.in_(("SCHEDULED", "FIRED")),
        )
        .order_by(models.WorkflowTimer.schedule_version.desc())
        .limit(1)
    )
    if existing:
        reminder = db.scalar(
            select(models.WorkflowTimer).where(
                models.WorkflowTimer.instance_id == instance.id,
                models.WorkflowTimer.stage_index == instance.stage_index,
                models.WorkflowTimer.timer_key == "REMINDER",
                models.WorkflowTimer.schedule_version == existing.schedule_version,
            )
        )
        return _deadline_metadata(existing, reminder, calendar)

    schedule_version = (
        db.scalar(
            select(func.max(models.WorkflowTimer.schedule_version)).where(
                models.WorkflowTimer.instance_id == instance.id,
                models.WorkflowTimer.stage_index == instance.stage_index,
            )
        )
        or 0
    ) + 1
    origin = started_at or _now()
    due_at = (
        add_working_hours(origin, sla["due_hours"], calendar.timezone, calendar.config)
        if calendar else origin + timedelta(hours=sla["due_hours"])
    )
    due = models.WorkflowTimer(
        instance_id=instance.id,
        stage_index=instance.stage_index,
        node_key=node["key"],
        timer_key="DUE",
        due_at=due_at,
        schedule_version=schedule_version,
    )
    db.add(due)
    reminder = None
    if sla["remind_before_hours"]:
        reminder = models.WorkflowTimer(
            instance_id=instance.id,
            stage_index=instance.stage_index,
            node_key=node["key"],
            timer_key="REMINDER",
            due_at=(
                add_working_hours(
                    origin,
                    sla["due_hours"] - sla["remind_before_hours"],
                    calendar.timezone,
                    calendar.config,
                )
                if calendar else due_at - timedelta(hours=sla["remind_before_hours"])
            ),
            schedule_version=schedule_version,
        )
        db.add(reminder)
    db.flush()
    return _deadline_metadata(due, reminder, calendar)


def _deadline_metadata(due, reminder=None, calendar=None):
    return {
        "due_at": due.due_at.isoformat(),
        "schedule_version": due.schedule_version,
        "status": "OVERDUE" if due.status == "FIRED" else "WAITING",
        "reminder_at": reminder.due_at.isoformat() if reminder else None,
        "calendar": ({
            "id": calendar.id,
            "key": calendar.calendar_key,
            "version": calendar.version,
            "name": calendar.name,
            "timezone": calendar.timezone,
            "package_hash": calendar.package_hash,
        } if calendar else None),
    }


def cancel_stage_timers(db, instance_id, stage_index):
    models = _models()
    return db.execute(
        update(models.WorkflowTimer)
        .where(
            models.WorkflowTimer.instance_id == instance_id,
            models.WorkflowTimer.stage_index == stage_index,
            models.WorkflowTimer.status == ACTIVE_STATUS,
        )
        .values(status="CANCELLED", lease_id=None, lease_until=None)
    ).rowcount


def cancel_instance_timers(db, instance_id):
    models = _models()
    return db.execute(
        update(models.WorkflowTimer)
        .where(
            models.WorkflowTimer.instance_id == instance_id,
            models.WorkflowTimer.status == ACTIVE_STATUS,
        )
        .values(status="CANCELLED", lease_id=None, lease_until=None)
    ).rowcount


def claim_due_timer(factory, *, lease_seconds=30):
    """Lease one due row.  SKIP LOCKED permits multiple scheduler workers."""
    models = _models()
    current = _now()
    with factory.begin() as db:
        timer = db.scalar(
            select(models.WorkflowTimer)
            .where(
                models.WorkflowTimer.status == ACTIVE_STATUS,
                models.WorkflowTimer.due_at <= current,
                or_(
                    models.WorkflowTimer.lease_until.is_(None),
                    models.WorkflowTimer.lease_until <= current,
                ),
            )
            .order_by(models.WorkflowTimer.due_at, models.WorkflowTimer.id)
            .with_for_update(skip_locked=True)
            .limit(1)
        )
        if not timer:
            return None
        lease_id = str(uuid.uuid4())
        timer.lease_id = lease_id
        timer.lease_until = current + timedelta(seconds=lease_seconds)
        return timer.id, lease_id


def _timer_recipients(db, instance):
    models = _models()
    recipients = {
        row
        for row in db.scalars(
            select(models.ApprovalSeat.user_id).where(
                models.ApprovalSeat.instance_id == instance.id,
                models.ApprovalSeat.stage_index == instance.stage_index,
                models.ApprovalSeat.status.in_(
                    ("PENDING", "WAITING_COUNTERSIGN", "WAITING_PREDECESSOR")
                ),
            )
        )
    }
    recipients.update(
        db.scalars(
            select(models.ApprovalCandidate.user_id).where(
                models.ApprovalCandidate.instance_id == instance.id,
                models.ApprovalCandidate.stage_index == instance.stage_index,
                models.ApprovalCandidate.status == "AVAILABLE",
            )
        )
    )
    submitter = (instance.snapshot or {}).get("submitter", {}).get("id")
    if submitter:
        recipients.add(submitter)
    return sorted(recipients)


def process_claimed_timer(factory, timer_id, lease_id):
    """Fire a claimed timer exactly once or retire it when its stage is stale."""
    models = _models()
    try:
        with factory.begin() as db:
            timer = db.scalar(
                select(models.WorkflowTimer)
                .where(
                    models.WorkflowTimer.id == timer_id,
                    models.WorkflowTimer.lease_id == lease_id,
                    models.WorkflowTimer.status == ACTIVE_STATUS,
                )
                .with_for_update()
            )
            if not timer:
                return "LEASE_LOST"
            instance = db.scalar(
                select(models.ApprovalInstance)
                .where(models.ApprovalInstance.id == timer.instance_id)
                .with_for_update()
            )
            if (
                not instance
                or instance.status != "RUNNING"
                or instance.stage_index != timer.stage_index
            ):
                timer.status = "STALE"
                timer.lease_id = None
                timer.lease_until = None
                return "STALE"

            current = _now()
            timer.status = "FIRED"
            timer.fired_at = current
            timer.lease_id = None
            timer.lease_until = None
            timer.last_error = None
            snapshot_key = str(timer.stage_index)
            stage_snapshot = dict((instance.assignment_snapshots or {}).get(snapshot_key, {}))
            deadline = dict(stage_snapshot.get("deadline", {}))
            if timer.timer_key == "DUE":
                deadline.update({"status": "OVERDUE", "overdue_at": current.isoformat()})
                stage_snapshot["deadline"] = deadline
                instance.assignment_snapshots = {
                    **(instance.assignment_snapshots or {}),
                    snapshot_key: stage_snapshot,
                }
            action = "approval.overdue" if timer.timer_key == "DUE" else "approval.reminder"
            detail = {
                "stage_index": timer.stage_index,
                "node_key": timer.node_key,
                "timer_key": timer.timer_key,
                "due_at": timer.due_at.isoformat(),
                "schedule_version": timer.schedule_version,
            }
            host_ports().record(
                db, None, action, instance.id, detail, _timer_recipients(db, instance)
            )
            return "FIRED"
    except Exception as error:
        _record_failure(factory, timer_id, lease_id, error)
        return "RETRY"


def _record_failure(factory, timer_id, lease_id, error):
    models = _models()
    with factory.begin() as db:
        timer = db.scalar(
            select(models.WorkflowTimer)
            .where(
                models.WorkflowTimer.id == timer_id,
                models.WorkflowTimer.lease_id == lease_id,
                models.WorkflowTimer.status == ACTIVE_STATUS,
            )
            .with_for_update()
        )
        if not timer:
            return
        timer.attempts += 1
        timer.last_error = type(error).__name__[:80]
        timer.lease_id = None
        if timer.attempts < 10:
            timer.lease_until = _now() + timedelta(seconds=min(300, 2 ** timer.attempts))
            return
        timer.status = "FAILED"
        timer.lease_until = None
        instance = db.scalar(
            select(models.ApprovalInstance)
            .where(models.ApprovalInstance.id == timer.instance_id)
            .with_for_update()
        )
        if instance and instance.status == "RUNNING" and instance.stage_index == timer.stage_index:
            instance.incident = "TIMER_FAILED"
            instance.version += 1
            host_ports().record(
                db,
                None,
                "approval.timer.failed",
                instance.id,
                {
                    "stage_index": timer.stage_index,
                    "node_key": timer.node_key,
                    "timer_key": timer.timer_key,
                    "error_type": timer.last_error,
                },
                _timer_recipients(db, instance),
            )


def tick_due_timers(factory, *, limit=25):
    processed = []
    for _ in range(limit):
        work = claim_due_timer(factory)
        if not work:
            break
        processed.append(process_claimed_timer(factory, *work))
    return processed
