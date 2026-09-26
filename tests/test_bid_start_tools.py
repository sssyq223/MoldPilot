import json
from pathlib import Path

import pytest

from bid_start_db import bid_context, bid_db  # noqa: F401
from domain_packs.mold import models as m, proposal_handlers, tool_gateway
from domain_packs.mold.tools.erp.commercial.bid_start_tools import execute_tool, query_confirmed_bid_notices


EXPECTED_TOOLS = {
    "prepare_bid_notice_match",
    "prepare_bid_project_match",
    "prepare_bid_intake_confirmation",
    "prepare_start_notice",
    "prepare_department_ack",
    "prepare_project_start_decision",
    "prepare_post_start_binding",
}


def test_bid_to_start_skill_tools_are_registered_and_confirmable():
    assert EXPECTED_TOOLS <= set(tool_gateway.TOOLS)
    for tool in EXPECTED_TOOLS:
        assert proposal_handlers.handler_for_tool(tool) is not None


def test_confirmed_event_is_consumed_through_tool_proposal(bid_context):
    context = bid_context
    rows = query_confirmed_bid_notices(context.db, context.owner)["data"]
    assert rows and rows[0]["event_id"] == context.event.id
    result = execute_tool(
        context.db, context.owner, "prepare_bid_notice_match",
        {"event_id": context.event.id},
    )
    proposal = result["proposal"]
    assert proposal["action"] == "bid_notice_match"
    assert proposal["input"] == {"event_id": context.event.id}
    assert context.db.query(m.BidNoticeMatch).count() == 0


def test_consumed_event_is_not_returned_as_pending(bid_context):
    from domain_packs.mold.erp.commercial.bid_start_workflow import consume_confirmed_bid_notice

    context = bid_context
    consume_confirmed_bid_notice(context.db, context.owner, context.event.id)

    assert query_confirmed_bid_notices(context.db, context.owner)["data"] == []


def test_skill_contract_matches_registered_entry_tools():
    contract = json.loads(Path("contracts/bid-to-start-notice.skill.json").read_text(encoding="utf-8"))

    assert contract["required_tools"] == [
        "query_confirmed_bid_notices",
        "prepare_bid_notice_match",
    ]
    assert set(contract["required_tools"]) <= set(tool_gateway.TOOLS)
    assert contract["max_steps"] == 10


def test_replay_cannot_bypass_current_authorization(bid_context):
    """无权用户即使持有旧 operation_id 也必须在幂等回放前被拒。"""
    from domain_packs.mold.erp.commercial.bid_start_workflow import (
        consume_confirmed_bid_notice, confirm_project_match,
    )
    from domain_packs.mold.ports.errors import DomainError
    context = bid_context
    match = consume_confirmed_bid_notice(context.db, context.owner, context.event.id)
    # 降为普通用户并只授文件上传权限：文件归属校验通过，
    # 但 project.read 必须在幂等事件查询前拒绝，不能凭旧 operation_id 读回结果。
    context.owner.super_admin = False
    context.db.add(m.Grant(
        user_id=context.owner.id, permission="file.upload", effect="ALLOW",
        scope={"all": True}, fields=["*"], active=True, reason="合成回归", granted_by=context.owner.id,
    ))
    context.db.flush()
    with pytest.raises(DomainError) as error:
        confirm_project_match(
            context.db, context.owner, match.id, expected_row_version=1,
            project_id=context.project.id, project_version=context.project.row_version,
            match_evidence="无权项目匹配", operation_id="any-op",
        )
    assert error.value.code == "FORBIDDEN"


def test_bid_to_start_skill_is_exposed_as_a_tool_sequence():
    skill = tool_gateway.SKILLS["bid_to_start_notice"]
    assert skill["tools"] == ["query_confirmed_bid_notices", "prepare_bid_notice_match"]
    assert "prepare_start_notice" in skill["optional_tools"]
    assert "prepare_post_start_binding" in skill["optional_tools"]
