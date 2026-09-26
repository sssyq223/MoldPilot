from bid_start_db import bid_context, bid_db  # noqa: F401
from domain_packs.mold.erp.commercial.admin_start_schemas import material_values
from domain_packs.mold.erp.commercial.admin_start_workflow import update_admin_start_notice_draft
from domain_packs.mold.skills.erp.commercial.bid_to_start_notice.orchestrator import trigger_bid_to_start_notice


def test_manual_internal_mold_number_is_a_reference_not_a_project_mold_id():
    values = material_values({'internal_mold_numbers': ['M260063-P3']})
    assert values == {'internal_mold_numbers': ['M260063-P3']}


def test_update_accepts_manual_internal_mold_number_not_yet_in_project_master(bid_context):
    c = bid_context
    draft = trigger_bid_to_start_notice(c.db, c.owner, c.event.id)
    result = update_admin_start_notice_draft(
        c.db, c.owner, draft.id, expected_row_version=1,
        material_snapshot={
            'project_id': c.project.id,
            'project_version': c.project.row_version,
            'internal_mold_numbers': ['M260063-P3'],
            'effective_date': '2026-10-01',
        }, reason='人工填写内部模具编号，待合同和核算清单后续匹配',
        operation_id='manual-mold-reference-1',
    )
    assert result['row_version'] == 2
    assert draft.material_snapshot['internal_mold_numbers'] == ['M260063-P3']
