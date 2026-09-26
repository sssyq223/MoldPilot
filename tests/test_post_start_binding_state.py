import pytest

from domain_packs.mold.erp.commercial.post_start_binding import (
    build_binding_snapshot,
    require_binding_decision,
)
from domain_packs.mold.ports.errors import DomainError


def test_binding_requires_project_accepted_decision():
    with pytest.raises(DomainError) as error:
        require_binding_decision({"decision": "RETURNED"}, notice_version=2, target_version=1)
    assert error.value.code == "START_DECISION_REQUIRED"

    assert require_binding_decision(
        {"decision": "PROJECT_ACCEPTED", "start_notice_version": 2},
        notice_version=2,
        target_version=1,
    ) is None


def test_binding_snapshot_contains_versions_and_mold_rows_only():
    result = build_binding_snapshot(
        decision_id="d1",
        start_notice_id="s1",
        start_notice_version=2,
        target_type="SALES_CONTRACT",
        target_id="c1",
        target_version=4,
        mold_rows=[{"mold_id": "m1", "line_no": 1, "raw_text": "不要保存原文"}],
    )
    assert result == {
        "decision_id": "d1",
        "start_notice_id": "s1",
        "start_notice_version": 2,
        "target_type": "SALES_CONTRACT",
        "target_id": "c1",
        "target_version": 4,
        "mold_rows": [{"mold_id": "m1", "line_no": 1}],
    }
