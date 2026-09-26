import pytest

from bid_start_db import bid_context, bid_db  # noqa: F401
from domain_packs.mold.erp.commercial.admin_start_workflow import (
    confirm_admin_start_notice,
    update_admin_start_notice_draft,
)
from domain_packs.mold.skills.erp.commercial.bid_to_start_notice.orchestrator import trigger_bid_to_start_notice


def test_acceptance_does_not_require_project_before_project_stage(bid_context):
    c = bid_context
    draft = trigger_bid_to_start_notice(c.db, c.owner, c.event.id)

    result = confirm_admin_start_notice(
        c.db, c.owner, draft.id, expected_row_version=1,
        decision='INTERNAL_ACCEPTED', reason='先确认承接，项目资料后补',
        material_snapshot={}, operation_id='accept-before-project',
    )

    assert result['status'] == 'ADMIN_CONFIRMED'
    assert draft.project_id is None
    assert draft.decision == 'INTERNAL_ACCEPTED'


def test_project_material_can_be_added_after_acceptance(bid_context):
    c = bid_context
    draft = trigger_bid_to_start_notice(c.db, c.owner, c.event.id)
    confirm_admin_start_notice(
        c.db, c.owner, draft.id, expected_row_version=1,
        decision='INTERNAL_ACCEPTED', reason='先确认承接，项目资料后补',
        material_snapshot={}, operation_id='accept-before-project',
    )

    result = update_admin_start_notice_draft(
        c.db, c.owner, draft.id, expected_row_version=2,
        material_snapshot={
            'project_id': c.project.id,
            'project_version': c.project.row_version,
            'internal_mold_numbers': ['M260063-P3'],
            'effective_date': '2026-10-01',
        }, reason='承接后选择项目并补充内部模具编号',
        operation_id='select-project-after-acceptance',
    )

    assert result['status'] == 'READY_FOR_CONTRACT_MATCH'
    assert draft.project_id == c.project.id
    assert draft.material_snapshot['internal_mold_numbers'] == ['M260063-P3']
