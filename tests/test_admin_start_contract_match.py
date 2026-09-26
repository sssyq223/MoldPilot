from bid_start_db import bid_context, bid_db  # noqa: F401
from domain_packs.mold import models as m
from domain_packs.mold.erp.commercial import contract_intake
from domain_packs.mold.erp.commercial.admin_start_workflow import confirm_admin_start_notice
from domain_packs.mold.erp.commercial.contract_match_workflow import propose_contract_matches
from domain_packs.mold.ports.events import record
from domain_packs.mold.skills.erp.commercial.bid_to_start_notice.orchestrator import trigger_bid_to_start_notice


def test_contract_ocr_event_proposes_candidate_without_formal_binding(bid_context, monkeypatch):
    c = bid_context
    draft = trigger_bid_to_start_notice(c.db, c.owner, c.event.id)
    confirm_admin_start_notice(
        c.db, c.owner, draft.id, expected_row_version=1,
        decision="INTERNAL_ACCEPTED", reason="超级管理员确认内部承接",
        material_snapshot={
            "project_id": c.project.id,
            "project_version": c.project.row_version,
            "internal_mold_ids": [],
            "effective_date": "2026-09-23",
        }, operation_id="admin-confirm-1",
    )
    group = m.ContractIntakeGroup(
        intake_id=c.intake.id, group_key="sales-contract-1",
        status="AWAITING_FIELD_CONFIRMATION", row_version=1,
    )
    c.db.add(group)
    c.db.flush()
    monkeypatch.setattr(contract_intake, "project_candidates", lambda db, user, group_id: {
        "resolution": "RESOLVED",
        "projects": [{
            "id": c.project.id, "row_version": c.project.row_version,
            "score": 4, "matched_by": ["project_number"],
        }],
    })
    record(c.db, c.owner, "contract.ocr.ready", group.id, {
        "contract_intake_group_id": group.id,
        "contract_group_version": group.row_version,
    })
    c.db.flush()
    event = c.db.scalar(c.db.query(m.AuditEvent).filter_by(
        action="contract.ocr.ready", resource_id=group.id,
    ).statement)
    result = propose_contract_matches(c.db, c.owner, event.id)

    assert result["candidate_ids"]
    candidate = c.db.get(m.StartContractMatchCandidate, result["candidate_ids"][0])
    assert candidate.status == "PROPOSED"
    assert c.db.query(m.PostStartBinding).count() == 0
