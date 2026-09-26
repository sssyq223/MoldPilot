import pytest

from domain_packs.mold.erp.project.start_notice_workflow import (
    validate_department_ack,
)
from domain_packs.mold.ports.errors import DomainError


def test_department_ack_requires_current_version_and_evidence():
    with pytest.raises(DomainError) as error:
        validate_department_ack(
            {"status": "PENDING", "row_version": 1},
            expected_row_version=2,
            status="ACCEPTED",
            evidence="已核对",
        )
    assert error.value.code == "STALE_VERSION"

    with pytest.raises(DomainError) as error:
        validate_department_ack(
            {"status": "PENDING", "row_version": 1},
            expected_row_version=1,
            status="RETURNED",
            evidence="",
        )
    assert error.value.code == "DEPARTMENT_EVIDENCE_REQUIRED"


def test_department_ack_rejects_unknown_status():
    with pytest.raises(DomainError) as error:
        validate_department_ack(
            {"status": "PENDING", "row_version": 1},
            expected_row_version=1,
            status="APPROVED",
            evidence="依据",
        )
    assert error.value.code == "DEPARTMENT_STATUS_INVALID"
