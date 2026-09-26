import pytest

from domain_packs.mold.erp.commercial.admin_start_workflow import (
    ADMIN_DECISIONS,
    confirm_admin_start_notice,
    validate_admin_start_decision,
)
from domain_packs.mold.ports.errors import DomainError


def test_admin_decision_requires_super_admin_and_explicit_reason():
    with pytest.raises(DomainError) as error:
        validate_admin_start_decision(
            type("Draft", (), {"status": "ADMIN_PENDING_INPUT", "row_version": 1})(),
            type("User", (), {"super_admin": False})(),
            expected_row_version=1,
            decision="INTERNAL_ACCEPTED",
            reason="确认",
            material_snapshot={},
        )
    assert error.value.code == "SUPER_ADMIN_REQUIRED"

    with pytest.raises(DomainError) as error:
        validate_admin_start_decision(
            type("Draft", (), {"status": "ADMIN_PENDING_INPUT", "row_version": 1})(),
            type("User", (), {"super_admin": True})(),
            expected_row_version=1,
            decision="INTERNAL_ACCEPTED",
            reason="",
            material_snapshot={"project_id": "p1"},
        )
    assert error.value.code == "ADMIN_DECISION_REASON_REQUIRED"


def test_admin_decision_only_accepts_internal_outsource_or_rejected():
    assert ADMIN_DECISIONS == {"INTERNAL_ACCEPTED", "FULL_OUTSOURCE_ACCEPTED", "REJECTED"}
    with pytest.raises(DomainError) as error:
        validate_admin_start_decision(
            type("Draft", (), {"status": "ADMIN_PENDING_INPUT", "row_version": 1})(),
            type("User", (), {"super_admin": True})(),
            expected_row_version=1,
            decision="PROJECT_ACCEPTED",
            reason="不应使用旧决定",
            material_snapshot={"project_id": "p1"},
        )
    assert error.value.code == "ADMIN_DECISION_INVALID"


def test_admin_decision_does_not_require_project_material_fields():
    validate_admin_start_decision(
        type("Draft", (), {"status": "ADMIN_PENDING_INPUT", "row_version": 1})(),
        type("User", (), {"super_admin": True})(),
        expected_row_version=1,
        decision="INTERNAL_ACCEPTED",
        reason="确认承接",
        material_snapshot={},
    )
