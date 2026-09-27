from sqlalchemy import select, func

from app.models import ContactRecord, ContactTask, User
from domain_packs.mold.erp.change import contacts as domain
from conftest import sign_in
from test_bpm_assignments import group
from test_contacts import create, grant

from test_engineering_contact_task_proposals import complete_payload


def unit(department_id, assignee_id, **overrides):
    return {
        "department_id": department_id,
        "assignee_id": assignee_id,
        "completion_date": "2026-09-29",
        "work_content": "完成图纸核对",
        "hours": "4.00",
        "amount": "0.00",
        "currency": "CNY",
        "remark": "保留复核记录",
        **overrides,
    }


def test_batch_service_creates_all_units_and_snapshot(client, data):
    ids, factory = data
    sign_in(client)
    first = group(client, [ids["admin"]], kind="DEPARTMENT", name="设计部", heads=[ids["admin"]])
    second = group(client, [ids["buyer"]], kind="DEPARTMENT", name="品质部", heads=[ids["buyer"]])
    grant(factory, ids, ids["admin"], ["read", "respond"])
    grant(factory, ids, ids["buyer"], ["read", "respond"])
    case, _ = create(client, ids)
    payload = complete_payload(form={
        "responsible_department_id": first["id"],
        "related_units": [unit(first["id"], ids["admin"]), unit(second["id"], ids["buyer"], work_content="完成品质复核")],
    })
    data_input = domain.FormTaskBatchInput(
        request_key=__import__("uuid").uuid4(),
        case_id=case["id"],
        revision=case["revision"],
        form=payload["form"],
    )

    with factory.begin() as db:
        user = db.get(User, ids["admin"])
        row = db.get(domain.m.ContactCase, case["id"])
        groups = [domain.department(db, unit["department_id"]) for unit in payload["form"]["related_units"]]
        people = [db.get(User, unit["assignee_id"]) for unit in payload["form"]["related_units"]]
        assert [domain.assignee_eligible(db, person, group_row, row) for person, group_row in zip(people, groups)] == [True, True]
        result = domain.add_form_tasks(case["id"], data_input, user, db)

    assert len(result["tasks"]) == 2
    assert {row["department_id"] for row in result["tasks"]} == {first["id"], second["id"]}
    assert {row["status"] for row in result["tasks"]} == {"ASSIGNED"}
    assert result["engineering_contact_form"]["form_version"] == domain.FORM_SNAPSHOT_VERSION
    assert result["engineering_contact_form"]["change_categories"] == ["DESIGN_ISSUE", "PROCESS_IMPROVEMENT"]
    assert len(result["engineering_contact_form"]["related_units"]) == 2


def test_history_batch_service_does_not_create_tasks(client, data):
    ids, factory = data
    sign_in(client)
    department = group(client, [ids["admin"]], kind="DEPARTMENT", name="历史设计部", heads=[ids["admin"]])
    case, _ = create(client, ids, mode="HISTORY")
    payload = complete_payload(form={
        "responsible_department_id": department["id"],
        "related_units": [unit(department["id"], ids["admin"])],
    })
    data_input = domain.FormTaskBatchInput(
        request_key=__import__("uuid").uuid4(),
        case_id=case["id"],
        revision=case["revision"],
        form=payload["form"],
    )

    with factory.begin() as db:
        user = db.get(User, ids["admin"])
        try:
            domain.add_form_tasks(case["id"], data_input, user, db)
        except domain.DomainError as error:
            assert error.code == "HISTORY_NO_DISPATCH"
        else:
            raise AssertionError("历史补录不应创建线上事项")

    with factory() as db:
        assert db.scalar(select(func.count()).select_from(ContactTask).where(ContactTask.case_id == case["id"])) == 0


def test_ineligible_assignee_rolls_back_all_units(client, data):
    ids, factory = data
    sign_in(client)
    valid_department = group(client, [ids["admin"]], kind="DEPARTMENT", name="有效设计部", heads=[ids["admin"]])
    invalid_department = group(client, [], kind="DEPARTMENT", name="无权限品质部")
    grant(factory, ids, ids["admin"], ["read", "respond"])
    case, _ = create(client, ids)
    payload = complete_payload(form={
        "responsible_department_id": valid_department["id"],
        "related_units": [
            unit(valid_department["id"], ids["admin"]),
            unit(invalid_department["id"], ids["admin"], work_content="不应创建"),
        ],
    })
    data_input = domain.FormTaskBatchInput(
        request_key=__import__("uuid").uuid4(),
        case_id=case["id"],
        revision=case["revision"],
        form=payload["form"],
    )

    with factory.begin() as db:
        user = db.get(User, ids["admin"])
        try:
            domain.add_form_tasks(case["id"], data_input, user, db)
        except domain.DomainError as error:
            assert error.code == "ASSIGNEE_UNAVAILABLE"
        else:
            raise AssertionError("无权限处理人不应通过批量创建")

    with factory() as db:
        assert db.scalar(select(func.count()).select_from(ContactTask).where(ContactTask.case_id == case["id"])) == 0
        assert db.scalar(select(func.count()).select_from(ContactRecord).where(ContactRecord.case_id == case["id"])) == 0
