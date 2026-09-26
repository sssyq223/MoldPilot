import pytest

from domain_packs.mold.erp.project.start_notice_workflow import (
    build_material_snapshot,
    require_project_decision_gate,
)
from domain_packs.mold.ports.errors import DomainError


def test_material_snapshot_contains_only_authoritative_references():
    snapshot = build_material_snapshot({
        "project_id": "p1",
        "project_version": 3,
        "bid_intake_revision_id": "r1",
        "customer_id": "c1",
        "mold_ids": ["m1"],
        "amount": "原文金额不应进入这里",
    })
    assert snapshot == {
        "project_id": "p1",
        "project_version": 3,
        "bid_intake_revision_id": "r1",
        "customer_id": "c1",
        "mold_ids": ["m1"],
    }


def test_project_decision_requires_all_department_acceptances():
    with pytest.raises(DomainError) as error:
        require_project_decision_gate(
            "PROJECT_ACCEPTED",
            [{"department_key": "DESIGN", "status": "ACCEPTED"}],
            required_keys=("DESIGN", "PURCHASE"),
        )
    assert error.value.code == "DEPARTMENT_ACK_REQUIRED"


def test_full_outsource_decision_is_allowed_only_after_all_acceptances():
    assert require_project_decision_gate(
        "FULL_OUTSOURCE_ACCEPTED",
        [
            {"department_key": "DESIGN", "status": "ACCEPTED"},
            {"department_key": "PURCHASE", "status": "ACCEPTED"},
        ],
        required_keys=("DESIGN", "PURCHASE"),
    ) is None
