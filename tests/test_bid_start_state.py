import pytest

from domain_packs.mold.erp.commercial.bid_start_workflow import (
    require_intake_confirmed,
    summarize_department_gate,
)
from domain_packs.mold.ports.errors import DomainError


def test_start_notice_requires_confirmed_intake_match_and_revision():
    with pytest.raises(DomainError) as error:
        require_intake_confirmed({"status": "PENDING_MATCH"})
    assert error.value.code == "BID_MATCH_REQUIRED"

    with pytest.raises(DomainError) as error:
        require_intake_confirmed({"status": "INTAKE_CONFIRMED", "project_id": "p1"})
    assert error.value.code == "BID_INTAKE_REVISION_REQUIRED"

    assert require_intake_confirmed({
        "status": "INTAKE_CONFIRMED",
        "project_id": "p1",
        "bid_intake_revision_id": "r1",
    }) is None


def test_department_gate_keeps_independent_returned_and_pending_departments():
    result = summarize_department_gate([
        {"department_key": "DESIGN", "status": "ACCEPTED"},
        {"department_key": "PURCHASE", "status": "RETURNED"},
        {"department_key": "MANUFACTURING", "status": "PENDING"},
    ], required_keys=("DESIGN", "PURCHASE", "MANUFACTURING"))
    assert result == {
        "ready": False,
        "accepted": ["DESIGN"],
        "pending": ["MANUFACTURING"],
        "returned": ["PURCHASE"],
    }
