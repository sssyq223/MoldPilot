from datetime import datetime
from zoneinfo import ZoneInfo

import pytest
from sqlalchemy import select

from app import bpm
from app.models import ApprovalInstance, WorkflowCalendar, WorkflowTimer
from app.errors import DomainError
from agent_core.workflow_calendars import add_working_hours, validate_calendar
from conftest import draft, sign_in, submit
from test_bpm_assignments import group, node, publish


SHANGHAI = ZoneInfo("Asia/Shanghai")


def calendar_config(**changes):
    value = {
        "working_weekdays": [1, 2, 3, 4, 5],
        "daily_intervals": [{"start": "09:00", "end": "12:00"}, {"start": "13:00", "end": "18:00"}],
        "holiday_dates": ["2026-09-21"],
        "extra_work_dates": ["2026-09-19"],
    }
    value.update(changes)
    return value


def create_calendar(client, *, key="factory_calendar", config=None):
    response = client.post("/api/workflow-calendars", json={
        "calendar_key": key,
        "name": "工厂工作日历",
        "timezone": "Asia/Shanghai",
        "config": config or calendar_config(),
    })
    assert response.status_code == 200, response.text
    return response.json()


def test_calendar_contract_and_working_hour_math():
    config = validate_calendar("Asia/Shanghai", calendar_config())
    friday = datetime(2026, 9, 18, 17, 0, tzinfo=SHANGHAI)
    assert add_working_hours(friday, 1, "Asia/Shanghai", config) == datetime(2026, 9, 18, 18, 0, tzinfo=SHANGHAI)
    assert add_working_hours(friday, 2, "Asia/Shanghai", config) == datetime(2026, 9, 19, 10, 0, tzinfo=SHANGHAI)
    assert add_working_hours(friday, 10, "Asia/Shanghai", config) == datetime(2026, 9, 22, 10, 0, tzinfo=SHANGHAI)

    invalid_values = (
        calendar_config(working_weekdays=[]),
        calendar_config(daily_intervals=[{"start": "12:00", "end": "09:00"}]),
        calendar_config(daily_intervals=[{"start": "09:00", "end": "12:00"}, {"start": "11:00", "end": "13:00"}]),
        calendar_config(holiday_dates=["2026-09-19"]),
    )
    for invalid in invalid_values:
        with pytest.raises(DomainError) as error:
            validate_calendar("Asia/Shanghai", invalid)
        assert error.value.code == "INVALID_CALENDAR"


def test_calendar_versions_are_immutable_and_only_published_versions_bind(client, data):
    sign_in(client)
    draft_calendar = create_calendar(client)
    approval_group = group(client, [data[0]["admin"]], name="日历审批组")
    workflow_node = node(approval_group["id"])
    workflow_node["sla"] = {
        "due_hours": 2,
        "remind_before_hours": 1,
        "calendar_id": draft_calendar["id"],
    }
    rejected = client.post("/api/workflows", json={
        "process_key": "calendar_guard",
        "name": "未发布日历拦截",
        "config": {"business_type": "generic", "nodes": [workflow_node]},
    })
    assert rejected.status_code == 409
    assert rejected.json()["error"]["code"] == "CALENDAR_NOT_PUBLISHED"

    published = client.post(f"/api/workflow-calendars/{draft_calendar['id']}/publish")
    assert published.status_code == 200, published.text
    published_data = published.json()
    assert published_data["status"] == "PUBLISHED"
    assert len(published_data["package_hash"]) == 64
    immutable = client.put(f"/api/workflow-calendars/{draft_calendar['id']}", json={
        "name": "不能覆盖",
        "timezone": "Asia/Shanghai",
        "config": calendar_config(),
        "expected_hash": draft_calendar["edit_hash"],
    })
    assert immutable.status_code == 409
    assert immutable.json()["error"]["code"] == "PUBLISHED_IMMUTABLE"

    second = create_calendar(client, config=calendar_config(extra_work_dates=[]))
    assert second["version"] == 2 and second["status"] == "DRAFT"
    rows = client.get("/api/workflow-calendars").json()
    assert [(row["version"], row["status"]) for row in rows] == [(2, "DRAFT"), (1, "PUBLISHED")]


def test_stage_deadline_freezes_published_calendar_version(client, data, monkeypatch):
    ids, factory = data
    sign_in(client)
    calendar = create_calendar(client)
    calendar = client.post(f"/api/workflow-calendars/{calendar['id']}/publish").json()
    approval_group = group(client, [ids["admin"]], name="冻结日历审批组")
    workflow_node = node(approval_group["id"])
    workflow_node["sla"] = {
        "due_hours": 2,
        "remind_before_hours": 1,
        "calendar_id": calendar["id"],
    }
    definition_id, response = publish(client, [workflow_node])
    assert response.status_code == 200, response.text
    fixed = datetime(2026, 9, 18, 17, 0, tzinfo=SHANGHAI)
    monkeypatch.setattr("agent_core.workflow_timers._now", lambda: fixed)

    sign_in(client, "test_buyer")
    instance_id = submit(client, {**ids, "definition": definition_id}, draft(client, ids))
    with factory() as db:
        instance = db.get(ApprovalInstance, instance_id)
        deadline = instance.assignment_snapshots["0"]["deadline"]
        timers = list(db.scalars(select(WorkflowTimer).where(
            WorkflowTimer.instance_id == instance_id
        ).order_by(WorkflowTimer.due_at)))
        assert deadline["calendar"] == {
            "id": calendar["id"],
            "key": "factory_calendar",
            "version": 1,
            "name": "工厂工作日历",
            "timezone": "Asia/Shanghai",
            "package_hash": calendar["package_hash"],
        }
        assert [timer.due_at.astimezone(SHANGHAI) for timer in timers] == [
            datetime(2026, 9, 18, 18, 0, tzinfo=SHANGHAI),
            datetime(2026, 9, 19, 10, 0, tzinfo=SHANGHAI),
        ]

    sign_in(client)
    second = create_calendar(client, config=calendar_config(extra_work_dates=[]))
    second = client.post(f"/api/workflow-calendars/{second['id']}/publish").json()
    assert second["version"] == 2 and second["package_hash"] != calendar["package_hash"]
    with factory() as db:
        instance = db.get(ApprovalInstance, instance_id)
        assert instance.assignment_snapshots["0"]["deadline"]["calendar"]["package_hash"] == calendar["package_hash"]
        assert db.get(WorkflowCalendar, calendar["id"]).status == "PUBLISHED"


def test_sla_accepts_optional_calendar_reference():
    config = {
        "business_type": "purchase_request",
        "nodes": [{
            "key": "review_1", "name": "日历审批", "users": ["reviewer"],
            "mode": "ALL", "reject_rules": [],
            "sla": {
                "due_hours": 24,
                "remind_before_hours": 2,
                "calendar_id": "11111111-1111-1111-1111-111111111111",
            },
        }],
    }
    bpm.validate(config)
