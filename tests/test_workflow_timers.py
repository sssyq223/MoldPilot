from datetime import timedelta

import pytest
from sqlalchemy import func, select

from app import bpm
from app.db import now
from app.errors import DomainError
from app.models import (
    ApprovalAction,
    ApprovalInstance,
    ApprovalSeat,
    AuditEvent,
    Outbox,
    WorkflowTimer,
    WorkflowEscalationTask,
)
from agent_core.workflow_timers import tick_due_timers
from conftest import draft, sign_in, submit
from test_bpm_assignments import change, group, node, publish
from test_core import confirm_decision


def test_sla_contract_is_restricted_and_never_configures_auto_approval():
    valid = {
        "business_type": "purchase_request",
        "nodes": [{
            "key": "review_1", "name": "时限审批", "users": ["reviewer"],
            "mode": "ALL", "reject_rules": [],
            "sla": {"due_hours": 24, "remind_before_hours": 2},
        }],
    }
    bpm.validate(valid)
    for sla in (
        {"due_hours": 0, "remind_before_hours": 0},
        {"due_hours": 24, "remind_before_hours": 24},
        {"due_hours": 24},
        {"due_hours": 1.5, "remind_before_hours": 0},
        {"due_hours": 24, "remind_before_hours": 1, "decision": "APPROVE"},
        {"due_hours": 24, "remind_before_hours": 1, "cc_user_ids": []},
        {"due_hours": 24, "remind_before_hours": 1, "escalation_user_ids": ["same", "same"]},
    ):
        invalid = {**valid, "nodes": [{**valid["nodes"][0], "sla": sla}]}
        with pytest.raises(DomainError) as error:
            bpm.validate(invalid)
        assert error.value.code == "INVALID_WORKFLOW"


def _sla_workflow(client, ids, *, nodes=1, sla_extra=None):
    approval_group = group(client, [ids["admin"]], name="定时审批组")
    definitions = []
    for index in range(nodes):
        item = node(approval_group["id"], key=f"review_{index + 1}")
        item["name"] = f"限时审批 {index + 1}"
        item["sla"] = {"due_hours": 2, "remind_before_hours": 1}
        item["sla"].update(sla_extra or {})
        definitions.append(item)
    definition_id, published = publish(client, definitions)
    assert published.status_code == 200, published.text
    return definition_id, approval_group


def test_durable_reminder_and_due_timer_do_not_approve(client, data):
    ids, factory = data
    sign_in(client)
    definition_id, _ = _sla_workflow(client, ids)
    sign_in(client, "test_buyer")
    instance_id = submit(client, {**ids, "definition": definition_id}, draft(client, ids))

    with factory() as db:
        timers = list(db.scalars(select(WorkflowTimer).where(
            WorkflowTimer.instance_id == instance_id,
        ).order_by(WorkflowTimer.due_at)))
        assert [timer.timer_key for timer in timers] == ["REMINDER", "DUE"]
        assert all(timer.status == "SCHEDULED" for timer in timers)
        instance = db.get(ApprovalInstance, instance_id)
        assert instance.assignment_snapshots["0"]["deadline"]["status"] == "WAITING"

    with factory.begin() as db:
        reminder = db.scalar(select(WorkflowTimer).where(
            WorkflowTimer.instance_id == instance_id,
            WorkflowTimer.timer_key == "REMINDER",
        ).with_for_update())
        reminder.due_at = now() - timedelta(seconds=1)
    assert tick_due_timers(factory) == ["FIRED"]
    assert tick_due_timers(factory) == []

    with factory() as db:
        assert db.scalar(select(func.count()).select_from(ApprovalAction)) == 0
        instance = db.get(ApprovalInstance, instance_id)
        assert instance.status == "RUNNING" and instance.incident is None
        assert db.scalar(select(func.count()).select_from(AuditEvent).where(
            AuditEvent.resource_id == instance_id,
            AuditEvent.action == "approval.reminder",
        )) == 1

    with factory.begin() as db:
        due = db.scalar(select(WorkflowTimer).where(
            WorkflowTimer.instance_id == instance_id,
            WorkflowTimer.timer_key == "DUE",
        ).with_for_update())
        due.due_at = now() - timedelta(seconds=1)
    assert tick_due_timers(factory) == ["FIRED"]

    sign_in(client)
    detail = client.get(f"/api/approvals/{instance_id}").json()
    assert detail["status"] == "RUNNING"
    assert detail["allowed_actions"] == ["APPROVE", "REJECT", "RETURN"]
    assert detail["deadline"]["status"] == "OVERDUE"
    assert detail["deadline"]["overdue_at"]
    incidents = client.get("/api/workflow-incidents").json()
    assert incidents[0]["id"] == instance_id
    assert incidents[0]["overdue"] is True
    assert incidents[0]["retryable"] is False
    assert db_event_count(factory, instance_id, "approval.overdue") == 1


def db_event_count(factory, resource_id, action):
    with factory() as db:
        return db.scalar(select(func.count()).select_from(AuditEvent).where(
            AuditEvent.resource_id == resource_id,
            AuditEvent.action == action,
        ))


def test_stage_completion_cancels_old_timers_and_schedules_next_stage(client, data):
    ids, factory = data
    sign_in(client)
    definition_id, _ = _sla_workflow(client, ids, nodes=2)
    sign_in(client, "test_buyer")
    instance_id = submit(client, {**ids, "definition": definition_id}, draft(client, ids))
    with factory.begin() as db:
        due = db.scalar(select(WorkflowTimer).where(
            WorkflowTimer.instance_id == instance_id,
            WorkflowTimer.stage_index == 0,
            WorkflowTimer.timer_key == "DUE",
        ).with_for_update())
        due.due_at = now() - timedelta(seconds=1)
    assert tick_due_timers(factory) == ["FIRED"]
    sign_in(client)
    _, response = confirm_decision(client, instance_id)
    assert response.status_code == 200, response.text
    with factory() as db:
        first = list(db.scalars(select(WorkflowTimer).where(
            WorkflowTimer.instance_id == instance_id,
            WorkflowTimer.stage_index == 0,
        )))
        second = list(db.scalars(select(WorkflowTimer).where(
            WorkflowTimer.instance_id == instance_id,
            WorkflowTimer.stage_index == 1,
        )))
        assert {timer.status for timer in first} == {"CANCELLED", "FIRED"}
        assert len(second) == 2 and {timer.status for timer in second} == {"SCHEDULED"}
    assert all(row["id"] != instance_id for row in client.get("/api/workflow-incidents").json())


def test_overdue_cc_and_escalation_create_follow_up_without_approval(client, data):
    ids, factory = data
    sign_in(client)
    definition_id, _ = _sla_workflow(client, ids, sla_extra={
        "cc_user_ids": [ids["reviewer"]],
        "escalation_user_ids": [ids["reviewer"]],
    })
    sign_in(client, "test_buyer")
    instance_id = submit(client, {**ids, "definition": definition_id}, draft(client, ids))
    with factory.begin() as db:
        due = db.scalar(select(WorkflowTimer).where(
            WorkflowTimer.instance_id == instance_id,
            WorkflowTimer.timer_key == "DUE",
        ).with_for_update())
        due.due_at = now() - timedelta(seconds=1)
    assert tick_due_timers(factory) == ["FIRED"]
    assert tick_due_timers(factory) == []

    with factory() as db:
        tasks = list(db.scalars(select(WorkflowEscalationTask).where(
            WorkflowEscalationTask.instance_id == instance_id,
        )))
        assert len(tasks) == 1
        assert tasks[0].user_id == ids["reviewer"] and tasks[0].status == "OPEN"
        assert db.scalar(select(func.count()).select_from(ApprovalAction)) == 0
        assert not list(db.scalars(select(ApprovalSeat).where(
            ApprovalSeat.instance_id == instance_id,
            ApprovalSeat.user_id == ids["reviewer"],
        )))
        event = db.scalar(select(AuditEvent).where(
            AuditEvent.resource_id == instance_id,
            AuditEvent.action == "approval.overdue",
        ))
        assert event.detail["cc_user_ids"] == [ids["reviewer"]]
        assert event.detail["escalation_user_ids"] == [ids["reviewer"]]
        outbox = db.scalar(select(Outbox).where(
            Outbox.resource_id == instance_id,
            Outbox.kind == "approval.overdue",
        ))
        assert ids["reviewer"] in outbox.payload["recipients"]

    sign_in(client)
    incident = next(row for row in client.get("/api/workflow-incidents").json()
                    if row["id"] == instance_id)
    assert incident["escalations"][0]["status"] == "OPEN"
    assert incident["escalations"][0]["user"]["id"] == ids["reviewer"]
    _, response = confirm_decision(client, instance_id)
    assert response.status_code == 200, response.text
    with factory() as db:
        task = db.scalar(select(WorkflowEscalationTask).where(
            WorkflowEscalationTask.instance_id == instance_id,
        ))
        assert task.status == "CLOSED"
        assert task.close_reason == "STAGE_COMPLETED" and task.closed_at


def test_failed_timer_requires_explicit_versioned_retry(client, data):
    ids, factory = data
    sign_in(client)
    definition_id, _ = _sla_workflow(client, ids)
    sign_in(client, "test_buyer")
    instance_id = submit(client, {**ids, "definition": definition_id}, draft(client, ids))
    with factory.begin() as db:
        instance = db.get(ApprovalInstance, instance_id)
        timer = db.scalar(select(WorkflowTimer).where(
            WorkflowTimer.instance_id == instance_id,
            WorkflowTimer.timer_key == "DUE",
        ).with_for_update())
        timer.status = "FAILED"
        timer.attempts = 10
        timer.last_error = "SyntheticTimerError"
        timer.due_at = now() - timedelta(seconds=1)
        instance.incident = "TIMER_FAILED"
        instance.version += 1
        failed_version = instance.version

    sign_in(client)
    incident = next(row for row in client.get("/api/workflow-incidents").json()
                    if row["id"] == instance_id)
    assert incident["retryable"] is True
    assert incident["failed_timers"] == [{
        "id": incident["failed_timers"][0]["id"],
        "timer_key": "DUE",
        "attempts": 10,
        "last_error": "SyntheticTimerError",
        "due_at": incident["failed_timers"][0]["due_at"],
    }]
    retry = client.post(f"/api/workflow-incidents/{instance_id}/retry", json={
        "expected_version": failed_version,
        "reason": "已修复外部通知适配器并人工复核",
    })
    assert retry.status_code == 200, retry.text
    assert retry.json()["requeued_timers"] == 1
    stale = client.post(f"/api/workflow-incidents/{instance_id}/retry", json={
        "expected_version": failed_version,
        "reason": "重复旧请求",
    })
    assert stale.status_code == 409
    with factory() as db:
        timer = db.scalar(select(WorkflowTimer).where(
            WorkflowTimer.instance_id == instance_id,
            WorkflowTimer.timer_key == "DUE",
        ))
        assert timer.status == "SCHEDULED"
        assert timer.attempts == 0 and timer.last_error is None
        audit = db.scalar(select(AuditEvent).where(
            AuditEvent.resource_id == instance_id,
            AuditEvent.action == "approval.timer.retried",
        ))
        assert audit.detail["failures"][0]["error_type"] == "SyntheticTimerError"
        assert audit.detail["reason"] == "已修复外部通知适配器并人工复核"
    assert tick_due_timers(factory) == ["FIRED"]
    with factory() as db:
        instance = db.get(ApprovalInstance, instance_id)
        assert instance.incident is None
        assert db.scalar(select(func.count()).select_from(ApprovalAction)) == 0


def test_assignment_incident_center_recovers_after_operator_fix(client, data):
    ids, factory = data
    sign_in(client)
    definition_id, approval_group = _sla_workflow(client, ids)
    disabled = change(client, approval_group, [], active=False)
    assert disabled.status_code == 200, disabled.text
    sign_in(client, "test_buyer")
    instance_id = submit(client, {**ids, "definition": definition_id}, draft(client, ids))

    sign_in(client)
    incidents = client.get("/api/workflow-incidents")
    assert incidents.status_code == 200, incidents.text
    item = next(row for row in incidents.json() if row["id"] == instance_id)
    assert item["incident"] == "ASSIGNMENT_BLOCKED"
    assert item["retryable"] is True
    failed_retry = client.post(f"/api/workflow-incidents/{instance_id}/retry", json={
        "expected_version": item["version"],
        "reason": "尚未恢复审批组，用于验证失败后仍保持阻塞",
    })
    assert failed_retry.status_code == 200, failed_retry.text
    assert failed_retry.json()["incident"] == "ASSIGNMENT_BLOCKED"

    current_group = disabled.json()
    enabled = change(client, current_group, [ids["admin"]], active=True)
    assert enabled.status_code == 200, enabled.text
    refreshed = next(row for row in client.get("/api/workflow-incidents").json()
                     if row["id"] == instance_id)
    recovered = client.post(f"/api/workflow-incidents/{instance_id}/retry", json={
        "expected_version": refreshed["version"],
        "reason": "审批组已恢复，重新解析当前节点候选人员",
    })
    assert recovered.status_code == 200, recovered.text
    assert recovered.json()["incident"] is None
    with factory() as db:
        instance = db.get(ApprovalInstance, instance_id)
        seats = list(db.scalars(select(ApprovalSeat).where(
            ApprovalSeat.instance_id == instance_id,
        )))
        timers = list(db.scalars(select(WorkflowTimer).where(
            WorkflowTimer.instance_id == instance_id,
        )))
        assert instance.incident is None
        assert len(seats) == 1 and seats[0].user_id == ids["admin"]
        assert len(timers) == 2  # recovery reuses the original durable deadline
        assert db.scalar(select(func.count()).select_from(AuditEvent).where(
            AuditEvent.resource_id == instance_id,
            AuditEvent.action == "approval.incident.retried",
        )) == 2


def test_incident_center_requires_workflow_permission(client, data):
    sign_in(client, "test_buyer")
    assert client.get("/api/workflow-incidents").status_code == 403
