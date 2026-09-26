import pytest

from domain_packs.mold.erp.commercial.bid_start_workflow import (
    validate_project_match,
)
from domain_packs.mold.ports.errors import DomainError


def test_project_match_requires_pending_row_and_current_version():
    match = {"status": "MATCHED", "row_version": 1}
    with pytest.raises(DomainError) as error:
        validate_project_match(match, expected_row_version=1, project_id="p1", project_version=2)
    assert error.value.code == "BID_MATCH_ALREADY_RESOLVED"

    match = {"status": "PENDING_MATCH", "row_version": 2}
    with pytest.raises(DomainError) as error:
        validate_project_match(match, expected_row_version=1, project_id="p1", project_version=2)
    assert error.value.code == "STALE_VERSION"


def test_project_match_rejects_changing_an_existing_project():
    with pytest.raises(DomainError) as error:
        validate_project_match(
            {"status": "PENDING_MATCH", "row_version": 1, "project_id": "p-old"},
            expected_row_version=1,
            project_id="p-new",
            project_version=2,
        )
    assert error.value.code == "PROJECT_MATCH_CONFLICT"

    assert validate_project_match(
        {"status": "PENDING_MATCH", "row_version": 1},
        expected_row_version=1,
        project_id="p1",
        project_version=2,
    ) is None
