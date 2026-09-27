from datetime import date, timedelta
from decimal import Decimal
from uuid import uuid4

import pytest
from pydantic import ValidationError

from domain_packs.mold.erp.change.contacts import FormTaskBatchInput


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
