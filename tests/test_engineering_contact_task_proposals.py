from datetime import date, timedelta
from decimal import Decimal
from uuid import uuid4

import pytest
from pydantic import ValidationError
from sqlalchemy import func, select

from app.models import ContactTask
from domain_packs.mold import proposal_handlers, tool_gateway
from domain_packs.mold.erp.change.contacts import FormTaskBatchInput
from conftest import sign_in
from test_agent_api import start, worker_headers
from test_bpm_assignments import group
from test_contact_proposals import confirm, propose
from test_contacts import create, grant


def complete_payload(**overrides):
    form = {
        "customer_ref": "CUSTOMER-001",
        "customer_name": "合成客户",
        "product_name": "冰箱门胆",
        "mold_number": "MOLD-001",
        "product_ref": "PART-001",
        "responsible_department_id": "dept-001",
        "application_date": date.today(),
        "completion_date": date.today() + timedelta(days=5),
        "completion_type": "URGENT",
        "change_categories": ["DESIGN_ISSUE", "PROCESS_IMPROVEMENT"],
        "change_description": "核对工程变更内容",
        "countermeasure": "完成图纸、加工和质量复核",
        "related_units": [
            {
                "department_id": "dept-001",
                "assignee_id": "user-001",
                "completion_date": date.today() + timedelta(days=2),
                "work_content": "完成图纸核对",
                "hours": Decimal("4.00"),
                "amount": Decimal("0.00"),
                "currency": "CNY",
                "remark": "保留复核记录",
            }
        ],
        "pricing_note": "内部工艺评估",
        "total_amount": Decimal("0.00"),
        "currency": "CNY",
    }
    form.update(overrides.pop("form", {}))
    return {"case_id": "case-001", "revision": 1, "form": form, **overrides}


def test_initial_automatic_proposal_allows_empty_form():
    proposal = FormTaskBatchInput(case_id="case-001", revision=1, request_key=uuid4())

    assert proposal.form is None


def test_form_requires_exact_change_categories_and_one_related_unit():
    proposal = FormTaskBatchInput(
        request_key=uuid4(),
        **complete_payload(),
    )

    assert proposal.form.change_categories == ["DESIGN_ISSUE", "PROCESS_IMPROVEMENT"]
    assert len(proposal.form.related_units) == 1
    assert proposal.form.completion_type == "URGENT"


def test_form_rejects_future_application_date():
    payload = complete_payload(form={"application_date": date.today() + timedelta(days=1)})

    with pytest.raises(ValidationError):
        FormTaskBatchInput(request_key=uuid4(), **payload)


def test_form_rejects_empty_categories_and_units():
    with pytest.raises(ValidationError):
        FormTaskBatchInput(
            request_key=uuid4(),
            **complete_payload(form={"change_categories": [], "related_units": []}),
        )


def test_form_rejects_amount_without_currency():
    with pytest.raises(ValidationError):
        FormTaskBatchInput(
            request_key=uuid4(),
            **complete_payload(form={"currency": None}),
        )


def test_form_task_tool_is_registered_with_contact_proposal_handler():
    assert "prepare_contact_form_tasks" in tool_gateway.TOOLS
    handler = proposal_handlers.handler_for_tool("prepare_contact_form_tasks")
    assert handler is not None
    assert handler.action == "contact.execute"
    assert "prepare_contact_form_tasks" in handler.tools


def test_prepare_form_tasks_returns_inert_empty_form_proposal(client, data, monkeypatch):
    ids, factory = data
    sign_in(client)
    case, _ = create(client, ids)
    _, context = start(client, monkeypatch, "admin")

    result = propose(client, context, "form_tasks", {
        "case_id": case["id"],
        "revision": case["revision"],
        "form": None,
    })

    assert result["proposal"]["action"] == "form_tasks"
    assert result["proposal"]["input"]["form"] is None
    assert result["proposal"]["confirmation_policy"]["requires_human_confirmation"] is True
    with factory() as db:
        assert db.scalar(select(func.count()).select_from(ContactTask)) == 0


def test_form_options_return_authorized_department_people(client, data, monkeypatch):
    ids, factory = data
    sign_in(client)
    department = group(client, [ids["admin"]], kind="DEPARTMENT", name="表单设计部", heads=[ids["admin"]])
    grant(factory, ids, ids["admin"], ["read", "respond"])
    case, _ = create(client, ids)
    _, context = start(client, monkeypatch, "admin")
    result = propose(client, context, "form_tasks", {
        "case_id": case["id"],
        "revision": case["revision"],
        "form": None,
    })

    response = client.get(f"/api/contact-proposals/{result['evidence_id']}/form-options")

    assert response.status_code == 200, response.text
    payload = response.json()
    selected = next(row for row in payload["departments"] if row["id"] == department["id"])
    assert selected["people"] == [{"id": ids["admin"], "name": "测试管理员"}]
    assert payload["completion_types"] == [
        {"value": "NORMAL", "label": "一般"},
        {"value": "URGENT", "label": "急件"},
        {"value": "CRITICAL", "label": "特急件"},
    ]
    assert {row["value"] for row in payload["change_categories"]} == {
        "CUSTOMER_CHANGE", "DESIGN_ISSUE", "ASSEMBLY_ISSUE", "MACHINING_ISSUE",
        "OUTSOURCE_DEFECT", "COST_REDUCTION", "PROCESS_IMPROVEMENT", "OTHER",
    }


def test_form_task_preview_requires_online_case_and_creator(client, data, monkeypatch):
    ids, factory = data
    sign_in(client)
    history_case, _ = create(client, ids, mode="HISTORY")
    _, context = start(client, monkeypatch, "admin")

    response = client.post(
        f"/internal/runs/{context['id']}/tools",
        headers=worker_headers(),
        json={
            "epoch": context["epoch"],
            "sequence": 0,
            "key": "prepare_contact_form_tasks",
            "arguments": {"case_id": history_case["id"], "revision": history_case["revision"], "form": None},
        },
    )

    assert response.status_code in {400, 409}
    with factory() as db:
        assert db.scalar(select(func.count()).select_from(ContactTask).where(ContactTask.case_id == history_case["id"])) == 0


def test_form_intent_rejects_case_revision_override(client, data, monkeypatch):
    ids, factory = data
    sign_in(client)
    department = group(client, [ids["admin"]], kind="DEPARTMENT", name="版本设计部", heads=[ids["admin"]])
    grant(factory, ids, ids["admin"], ["read", "respond"])
    case, _ = create(client, ids)
    _, context = start(client, monkeypatch, "admin")
    initial = propose(client, context, "form_tasks", {
        "case_id": case["id"],
        "revision": case["revision"],
        "form": None,
    })
    form = complete_payload(form={
        "responsible_department_id": department["id"],
        "related_units": [{
            **complete_payload()["form"]["related_units"][0],
            "department_id": department["id"],
            "assignee_id": ids["admin"],
        }],
    })["form"]
    form = FormTaskBatchInput(request_key=uuid4(), case_id=case["id"], revision=case["revision"], form=form).form.model_dump(mode="json")

    response = client.post(
        f"/api/contact-proposals/{initial['evidence_id']}/form-intent",
        json={"input": {"case_id": case["id"], "revision": case["revision"] + 1, "form": form}},
    )

    assert response.status_code == 409
    with factory() as db:
        assert db.scalar(select(func.count()).select_from(ContactTask).where(ContactTask.case_id == case["id"])) == 0


def test_revised_form_proposal_confirms_only_after_human_intent(client, data, monkeypatch):
    ids, factory = data
    sign_in(client)
    department = group(client, [ids["admin"]], kind="DEPARTMENT", name="修订设计部", heads=[ids["admin"]])
    grant(factory, ids, ids["admin"], ["read", "respond"])
    case, _ = create(client, ids)
    _, context = start(client, monkeypatch, "admin")
    initial = propose(client, context, "form_tasks", {
        "case_id": case["id"],
        "revision": case["revision"],
        "form": None,
    })
    form = complete_payload(form={
        "responsible_department_id": department["id"],
        "related_units": [{
            **complete_payload()["form"]["related_units"][0],
            "department_id": department["id"],
            "assignee_id": ids["admin"],
        }],
    })["form"]
    form = FormTaskBatchInput(request_key=uuid4(), case_id=case["id"], revision=case["revision"], form=form).form.model_dump(mode="json")

    response = client.post(
        f"/api/contact-proposals/{initial['evidence_id']}/form-intent",
        json={"input": {"case_id": case["id"], "revision": case["revision"], "form": form}},
    )

    assert response.status_code == 200, response.text
    intent = response.json()
    assert "challenge" in intent
    with factory() as db:
        assert db.scalar(select(func.count()).select_from(ContactTask).where(ContactTask.case_id == case["id"])) == 0

    receipt = confirm(client, intent)
    assert receipt.status_code == 200, receipt.text
    with factory() as db:
        assert db.scalar(select(func.count()).select_from(ContactTask).where(ContactTask.case_id == case["id"])) == 1
