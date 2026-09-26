from sqlalchemy import inspect

from domain_packs.mold import models as m


def test_bid_start_workflow_models_are_registered_with_explicit_tables():
    expected = {
        "bid_notice_match",
        "start_notice",
        "start_notice_department_ack",
        "project_start_decision",
        "post_start_binding",
    }
    actual = {table.name for table in m.Base.metadata.sorted_tables}
    assert expected <= actual


def test_start_notice_has_version_and_status_constraints():
    table = m.StartNotice.__table__
    assert {"bid_match_id", "version", "status", "project_id"} <= set(table.c.keys())
    checks = {constraint.name for constraint in table.constraints if constraint.name}
    assert "start_notice_status" in checks
    assert "start_notice_version_positive" in checks


def test_department_ack_is_unique_per_notice_version_and_department():
    table = m.StartNoticeDepartmentAck.__table__
    assert {"start_notice_id", "start_notice_version", "department_key", "status"} <= set(table.c.keys())
    constraints = {constraint.name for constraint in table.constraints if constraint.name}
    assert "start_notice_department_ack_unique" in constraints


def test_post_start_binding_requires_decision_and_target_version():
    table = m.PostStartBinding.__table__
    assert {
        "project_decision_id",
        "start_notice_id",
        "start_notice_version",
        "target_type",
        "target_id",
        "target_version",
    } <= set(table.c.keys())
    constraints = {constraint.name for constraint in table.constraints if constraint.name}
    assert "post_start_binding_target_version_positive" in constraints
