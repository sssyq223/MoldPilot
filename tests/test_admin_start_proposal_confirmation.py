"""确认卡入口回归：建议内容不能混入 Run 对象参与哈希。"""
from copy import deepcopy

import pytest

from bid_start_db import bid_context, bid_db  # noqa: F401
from app.proposal_api import proposal_intent
from domain_packs.mold import models as m
from domain_packs.mold.erp.core.business import confirm_intent
from domain_packs.mold.ports.errors import DomainError
from domain_packs.mold.skills.erp.commercial.bid_to_start_notice.orchestrator import trigger_bid_to_start_notice
from domain_packs.mold.tools.erp.commercial import admin_start_notice_tools as tools


@pytest.fixture
def dispatch_proposal(bid_context):
    c = bid_context
    draft = trigger_bid_to_start_notice(c.db, c.owner, c.event.id)
    run = m.Run(conversation_id=c.conversation.id, user_id=c.owner.id,
                security_version=c.owner.security_version, prompt="分发内部通知单",
                status="SUCCEEDED", checkpoint={"agent_permission_mode": "ask"})
    c.db.add(run)
    c.db.flush()
    key = "prepare_admin_start_department_dispatch"
    evidence = tools.execute_tool(c.db, c.owner, key, {
        "draft_id": draft.id, "expected_row_version": 1,
        "department_keys": ["DESIGN", "FINANCE"], "reason": "合成确认卡回归",
    }, run=run)
    step = m.Step(run_id=run.id, sequence=0, tool=key,
                  request_hash="synthetic-admin-dispatch", result=evidence)
    c.db.add(step)
    c.db.flush()
    return c, draft, step


def test_review_then_confirm_dispatches_once(dispatch_proposal):
    c, draft, step = dispatch_proposal
    # 直接走确认卡路由实现，不能手工构造 hash 绕过 source 契约。
    intent = proposal_intent(step.id, user=c.owner, db=c.db)
    assert intent["display"]["department_keys"] == ["DESIGN", "FINANCE"]
    assert intent["confirmation_policy"]["requires_human_confirmation"] is True
    assert draft.status == "ADMIN_PENDING_INPUT"
    assert c.db.query(m.AdminStartDepartmentAck).count() == 0

    receipt = confirm_intent(c.db, c.owner, intent["id"], intent["challenge"])
    assert receipt["dispatched"] == ["DESIGN", "FINANCE"]
    assert draft.status == "DEPARTMENTS_NOTIFIED"
    assert draft.row_version == 2
    assert c.db.query(m.AdminStartDepartmentAck).count() == 2
    assert confirm_intent(c.db, c.owner, intent["id"], intent["challenge"]) == receipt
    assert c.db.query(m.AdminStartDepartmentAck).count() == 2
    assert c.db.query(m.AdminStartNoticeRevision).count() == 1


def test_changed_proposal_is_still_rejected(dispatch_proposal):
    c, draft, step = dispatch_proposal
    intent = proposal_intent(step.id, user=c.owner, db=c.db)
    changed = deepcopy(step.result)
    changed["proposal"]["input"]["department_keys"] = ["PURCHASE"]
    step.result = changed
    c.db.flush()
    with pytest.raises(DomainError) as error:
        confirm_intent(c.db, c.owner, intent["id"], intent["challenge"])
    assert error.value.code == "CONFIRMATION_INVALID"
    assert draft.row_version == 1
    assert c.db.query(m.AdminStartDepartmentAck).count() == 0


def test_stale_draft_version_is_still_rejected(dispatch_proposal):
    c, draft, step = dispatch_proposal
    intent = proposal_intent(step.id, user=c.owner, db=c.db)
    draft.row_version += 1
    c.db.flush()
    with pytest.raises(DomainError) as error:
        confirm_intent(c.db, c.owner, intent["id"], intent["challenge"])
    assert error.value.code == "STALE_VERSION"
    assert c.db.query(m.AdminStartDepartmentAck).count() == 0


def test_other_user_cannot_review_dispatch(dispatch_proposal):
    c, draft, step = dispatch_proposal
    with pytest.raises(DomainError) as error:
        proposal_intent(step.id, user=c.other, db=c.db)
    assert error.value.code == "NOT_FOUND"
    assert c.db.query(m.HumanIntent).filter_by(resource_id=step.id).count() == 0
