import pytest

from domain_packs.mold.erp.commercial.bid_start_workflow import (
    validate_intake_revision_confirmation,
)
from domain_packs.mold.ports.errors import DomainError


def test_intake_confirmation_requires_matched_row_and_matching_revision_project():
    with pytest.raises(DomainError) as error:
        validate_intake_revision_confirmation(
            {"status": "PENDING_MATCH", "row_version": 1, "project_id": "p1"},
            {"id": "r1", "case_project_id": "p1"},
            expected_row_version=1,
            revision_id="r1",
        )
    assert error.value.code == "BID_MATCH_REQUIRED"

    with pytest.raises(DomainError) as error:
        validate_intake_revision_confirmation(
            {"status": "MATCHED", "row_version": 2, "project_id": "p1"},
            {"id": "r1", "case_project_id": "p2"},
            expected_row_version=2,
            revision_id="r1",
        )
    assert error.value.code == "BID_REVISION_PROJECT_CONFLICT"


def test_intake_confirmation_rejects_stale_or_wrong_revision():
    with pytest.raises(DomainError) as error:
        validate_intake_revision_confirmation(
            {"status": "MATCHED", "row_version": 1, "project_id": "p1"},
            {"id": "r2", "case_project_id": "p1"},
            expected_row_version=1,
            revision_id="r1",
        )
    assert error.value.code == "BID_REVISION_NOT_FOUND"

    with pytest.raises(DomainError) as error:
        validate_intake_revision_confirmation(
            {"status": "MATCHED", "row_version": 2, "project_id": "p1"},
            {"id": "r1", "case_project_id": "p1"},
            expected_row_version=1,
            revision_id="r1",
        )
    assert error.value.code == "STALE_VERSION"
