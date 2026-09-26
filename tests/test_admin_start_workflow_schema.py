import pytest

from domain_packs.mold import models as m


def test_admin_start_models_are_registered_with_revision_and_candidate_tables():
    expected = {
        "admin_start_notice_draft",
        "admin_start_notice_revision",
        "start_contract_match_candidate",
        "admin_start_department_ack",
    }
    actual = {table.name for table in m.Base.metadata.sorted_tables}
    assert expected <= actual


def test_admin_start_draft_has_safe_states_and_source_event_idempotency():
    table = m.AdminStartNoticeDraft.__table__
    assert {
        "source_event_id", "source_file_id", "inbound_record_id",
        "status", "decision", "material_snapshot", "row_version",
    } <= set(table.c.keys())
    checks = {constraint.name for constraint in table.constraints if constraint.name}
    assert "admin_start_notice_draft_status" in checks
    assert "admin_start_notice_decision" in checks
    assert any(
        constraint.name == "admin_start_notice_source_event"
        for constraint in table.constraints
    )


def test_admin_start_revision_and_candidate_are_append_only_keys():
    revision_constraints = {
        constraint.name
        for constraint in m.AdminStartNoticeRevision.__table__.constraints
        if constraint.name
    }
    candidate_constraints = {
        constraint.name
        for constraint in m.StartContractMatchCandidate.__table__.constraints
        if constraint.name
    }
    assert "admin_start_notice_revision_unique" in revision_constraints
    assert "start_contract_match_candidate_unique" in candidate_constraints


def test_admin_start_department_ack_tracks_dispatch_and_receipt():
    table = m.AdminStartDepartmentAck.__table__
    assert {
        "draft_id", "department_key", "status", "notified_by", "notified_at",
        "acked_by", "acked_at", "ack_note", "row_version",
    } <= set(table.c.keys())
    constraints = {constraint.name for constraint in table.constraints if constraint.name}
    assert "admin_start_department_ack_unique" in constraints
    assert "admin_start_department_ack_status" in constraints
    draft_status_constraint = next(
        c for c in m.AdminStartNoticeDraft.__table__.constraints
        if c.name == "admin_start_notice_draft_status"
    )
    assert "DEPARTMENTS_NOTIFIED" in str(draft_status_constraint.sqltext)
