import pytest
from domain_packs.mold.ports.errors import DomainError
from bid_start_db import bid_context, bid_db  # noqa: F401
from domain_packs.mold import models as m
from domain_packs.mold.skills.erp.commercial.bid_to_start_notice.orchestrator import (
    trigger_bid_to_start_notice,
)


def test_bid_confirmation_skill_creates_one_admin_only_draft(bid_context):
    context = bid_context
    draft = trigger_bid_to_start_notice(context.db, context.owner, context.event.id)
    replay = trigger_bid_to_start_notice(context.db, context.owner, context.event.id)

    assert replay.id == draft.id
    assert draft.status == "ADMIN_PENDING_INPUT"
    assert draft.decision is None
    assert context.db.query(m.AdminStartNoticeDraft).count() == 1
    assert context.db.query(m.AdminStartNoticeRevision).count() == 1
    assert context.db.query(m.StartNoticeDepartmentAck).count() == 0
    outbox = context.db.query(m.Outbox).filter(
        m.Outbox.kind == "admin_start_notice.pending",
    ).order_by(m.Outbox.created_at.desc()).first()
    assert outbox is not None
    assert {context.owner.id, context.other.id} <= set(outbox.payload['recipients'])
    assert all(context.db.get(m.User, uid).super_admin and context.db.get(m.User, uid).active
               for uid in outbox.payload['recipients'])


def test_automatic_draft_failure_is_persisted_for_admin_review(bid_context, monkeypatch):
    from domain_packs.mold.erp.commercial import admin_start_workflow
    from domain_packs.mold.ports.errors import DomainError
    c = bid_context
    monkeypatch.setattr(
        admin_start_workflow, 'consume_confirmed_bid_notice',
        lambda *args, **kwargs: (_ for _ in ()).throw(
            DomainError('BID_SOURCE_MISSING', 'synthetic failure', 409)
        ),
    )
    draft = trigger_bid_to_start_notice(c.db, c.owner, c.event.id)
    assert draft.status == 'NEEDS_REVIEW'
    assert draft.material_snapshot['failure_code'] == 'BID_SOURCE_MISSING'
    assert c.db.query(m.AdminStartNoticeRevision).count() == 2
    assert c.db.query(m.Outbox).filter(m.Outbox.kind == 'admin_start_notice.failed').count() == 1


def test_update_cannot_replace_source_or_accept_stale_project(bid_context):
    from domain_packs.mold.erp.commercial.admin_start_workflow import update_admin_start_notice_draft
    c = bid_context
    draft = trigger_bid_to_start_notice(c.db, c.owner, c.event.id)
    for fields, code in [({'source_event_id': 'forged'}, 'ADMIN_MATERIAL_INVALID'),
                         ({'project_id': c.project.id, 'project_version': 99}, 'VERSION_CONFLICT')]:
        with pytest.raises(DomainError) as error:
            update_admin_start_notice_draft(c.db, c.owner, draft.id, expected_row_version=1,
                material_snapshot=fields, reason='测试', operation_id='update')
        assert error.value.code == code
    assert draft.row_version == 1


def test_replayed_decision_still_requires_active_super_admin(bid_context):
    from domain_packs.mold.erp.commercial.admin_start_workflow import confirm_admin_start_notice
    c = bid_context
    draft = trigger_bid_to_start_notice(c.db, c.owner, c.event.id)
    args = dict(expected_row_version=1, decision='REJECTED', reason='测试拒绝',
                material_snapshot={}, operation_id='reject')
    confirm_admin_start_notice(c.db, c.owner, draft.id, **args)
    c.owner.super_admin = False
    with pytest.raises(DomainError) as error:
        confirm_admin_start_notice(c.db, c.owner, draft.id, **args)
    assert error.value.code == 'SUPER_ADMIN_REQUIRED'


def test_super_admin_decision_is_versioned_and_does_not_create_department_ack(bid_context):
    context = bid_context
    draft = trigger_bid_to_start_notice(context.db, context.owner, context.event.id)
    from domain_packs.mold.erp.commercial.admin_start_workflow import confirm_admin_start_notice

    result = confirm_admin_start_notice(
        context.db, context.owner, draft.id, expected_row_version=1,
        decision="INTERNAL_ACCEPTED", reason="超级管理员确认内部承接",
        material_snapshot={
            "project_id": context.project.id,
            "project_version": context.project.row_version,
            "internal_mold_ids": [],
            "effective_date": "2026-09-23",
        }, operation_id="admin-decision-1",
    )

    assert result["status"] == "READY_FOR_CONTRACT_MATCH"
    assert draft.project_id == context.project.id
    assert context.db.query(m.AdminStartNoticeRevision).count() == 2
    assert context.db.query(m.StartNoticeDepartmentAck).count() == 0


def test_dispatch_creates_department_receipts_and_bumps_status(bid_context):
    from domain_packs.mold.erp.commercial.admin_start_workflow import (
        dispatch_admin_start_notice,
    )
    c = bid_context
    draft = trigger_bid_to_start_notice(c.db, c.owner, c.event.id)

    result = dispatch_admin_start_notice(
        c.db, c.owner, draft.id, expected_row_version=1,
        department_keys=["DESIGN", "FINANCE"], reason="项目部分发内部通知单",
        operation_id="dispatch-1",
    )
    assert draft.status == "DEPARTMENTS_NOTIFIED"
    assert result["dispatched"] == ["DESIGN", "FINANCE"]
    assert result["row_version"] == draft.row_version == 2
    acks = {a.department_key: a for a in c.db.query(m.AdminStartDepartmentAck)}
    assert set(acks) == {"DESIGN", "FINANCE"}
    assert all(a.status == "SENT" and a.notified_by == c.owner.id for a in acks.values())

    # 重放同 operation_id 幂等
    replay = dispatch_admin_start_notice(
        c.db, c.owner, draft.id, expected_row_version=1,
        department_keys=["DESIGN", "FINANCE"], reason="项目部分发内部通知单",
        operation_id="dispatch-1",
    )
    assert replay["operation_id"] == "dispatch-1"
    assert c.db.query(m.AdminStartDepartmentAck).count() == 2

    # 未知部门被拒绝
    with pytest.raises(DomainError) as error:
        dispatch_admin_start_notice(
            c.db, c.owner, draft.id, expected_row_version=draft.row_version,
            department_keys=["UNKNOWN_DEPT"], reason="错误部门", operation_id="dispatch-2",
        )
    assert error.value.code == "ADMIN_DEPARTMENT_INVALID"


def test_department_ack_marks_received_and_is_idempotent(bid_context):
    from domain_packs.mold.erp.commercial.admin_start_workflow import (
        acknowledge_admin_start_department, dispatch_admin_start_notice,
    )
    c = bid_context
    draft = trigger_bid_to_start_notice(c.db, c.owner, c.event.id)
    dispatch_admin_start_notice(
        c.db, c.owner, draft.id, expected_row_version=1,
        department_keys=["DESIGN", "PURCHASE"], reason="项目部分发", operation_id="dispatch-1",
    )
    result = acknowledge_admin_start_department(
        c.db, c.owner, draft.id, expected_row_version=2,
        department_key="DESIGN", note="设计部门已确认收到", operation_id="ack-1",
    )
    assert result["status"] == "ACKNOWLEDGED"
    ack = c.db.query(m.AdminStartDepartmentAck).filter_by(department_key="DESIGN").one()
    assert ack.status == "ACKNOWLEDGED" and ack.acked_by == c.owner.id
    purchase = c.db.query(m.AdminStartDepartmentAck).filter_by(department_key="PURCHASE").one()
    assert purchase.status == "SENT"

    # 重复回执幂等，不产生新修订
    replay = acknowledge_admin_start_department(
        c.db, c.owner, draft.id, expected_row_version=result["row_version"],
        department_key="DESIGN", note="设计部门再次确认", operation_id="ack-2",
    )
    assert replay["status"] == "ACKNOWLEDGED"

    # 未分发部门不能回执
    with pytest.raises(DomainError) as error:
        acknowledge_admin_start_department(
            c.db, c.owner, draft.id, expected_row_version=replay["row_version"],
            department_key="ASSEMBLY", note="未分发", operation_id="ack-3",
        )
    assert error.value.code == "ADMIN_DEPARTMENT_NOT_DISPATCHED"


def test_decision_allowed_without_all_acknowledgements_option_b(bid_context):
    from domain_packs.mold.erp.commercial.admin_start_workflow import (
        acknowledge_admin_start_department, confirm_admin_start_notice,
        dispatch_admin_start_notice,
    )
    c = bid_context
    draft = trigger_bid_to_start_notice(c.db, c.owner, c.event.id)
    dispatch_admin_start_notice(
        c.db, c.owner, draft.id, expected_row_version=1,
        department_keys=["DESIGN", "PURCHASE"], reason="项目部分发", operation_id="dispatch-1",
    )
    ack = acknowledge_admin_start_department(
        c.db, c.owner, draft.id, expected_row_version=2,
        department_key="DESIGN", note="设计已收到", operation_id="ack-1",
    )
    # B 方案：未全部确认收到也允许项目部最终决定
    result = confirm_admin_start_notice(
        c.db, c.owner, draft.id, expected_row_version=ack["row_version"],
        decision="INTERNAL_ACCEPTED", reason="项目部在部分部门未回执时直接确认",
        material_snapshot={
            "project_id": c.project.id,
            "project_version": c.project.row_version,
            "internal_mold_ids": [],
            "effective_date": "2026-09-23",
        }, operation_id="decision-1",
    )
    assert result["status"] == "READY_FOR_CONTRACT_MATCH"
    assert result["acknowledged_departments"] == ["DESIGN"]
    assert result["pending_departments"] == ["PURCHASE"]
