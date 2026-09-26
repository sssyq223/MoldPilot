import pytest

from domain_packs.mold.erp.project.start_notice_workflow import (
    legacy_internal_start_reference,
    require_start_prerequisites,
    validate_start_notice_creation,
)
from domain_packs.mold.ports.errors import DomainError


def test_start_notice_creation_requires_intake_confirmed_and_current_project():
    with pytest.raises(DomainError) as error:
        validate_start_notice_creation(
            {"status": "MATCHED", "project_id": "p1", "project_version": 2,
             "bid_intake_revision_id": "r1"},
            project_version=2,
        )
    assert error.value.code == "BID_INTAKE_CONFIRMATION_REQUIRED"

    with pytest.raises(DomainError) as error:
        validate_start_notice_creation(
            {"status": "INTAKE_CONFIRMED", "project_id": "p1", "project_version": 2,
             "bid_intake_revision_id": "r1"},
            project_version=3,
        )
    assert error.value.code == "VERSION_CONFLICT"

    assert validate_start_notice_creation(
        {"status": "INTAKE_CONFIRMED", "project_id": "p1", "project_version": 2,
         "bid_intake_revision_id": "r1"},
        project_version=2,
    ) is None


def test_legacy_internal_start_reference_is_read_only_compatibility_data():
    assert legacy_internal_start_reference(None) is None
    assert legacy_internal_start_reference(
        type("Subject", (), {"id": "legacy-1", "status": "EFFECTIVE", "revision": 2})()
    ) == {
        "subject_id": "legacy-1", "status": "EFFECTIVE", "revision": 2,
        "compatibility_only": True,
    }


def test_start_notice_requires_customer_conditions_and_quote_acceptance():
    with pytest.raises(DomainError) as error:
        require_start_prerequisites({"complete": False, "blockers": ["客户交期缺失"]}, False)
    assert error.value.code == "START_CONDITIONS_MISSING"

    with pytest.raises(DomainError) as error:
        require_start_prerequisites({"complete": True, "blockers": []}, False)
    assert error.value.code == "QUOTE_ACCEPTANCE_REQUIRED"

    assert require_start_prerequisites({"complete": True, "blockers": []}, True) is None
