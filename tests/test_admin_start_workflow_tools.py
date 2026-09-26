from domain_packs.mold import proposal_handlers, tool_gateway


def test_admin_start_workflow_is_exposed_as_skill_tools():
    expected = {
        "query_admin_start_notices",
        "prepare_admin_start_notice_update",
        "prepare_admin_start_notice_decision",
        "prepare_contract_match_confirmation",
    }
    assert expected <= set(tool_gateway.TOOLS)
    for tool in expected - {"query_admin_start_notices"}:
        assert proposal_handlers.handler_for_tool(tool) is not None


def test_bid_to_start_skill_keeps_automatic_draft_and_admin_confirmation_boundary():
    skill = tool_gateway.SKILLS["bid_to_start_notice"]
    assert "prepare_admin_start_notice_update" in skill["optional_tools"]
    assert "prepare_admin_start_notice_decision" in skill["optional_tools"]
    assert "prepare_contract_match_confirmation" in skill["optional_tools"]
    assert skill.get("activation_triggers") == ["bid_notice.confirmed"]


def test_department_dispatch_and_ack_are_exposed_as_skill_tools():
    expected = {
        "prepare_admin_start_department_dispatch",
        "prepare_admin_start_department_ack",
    }
    assert expected <= set(tool_gateway.TOOLS)
    for tool in expected:
        assert proposal_handlers.handler_for_tool(tool) is not None
    skill = tool_gateway.SKILLS["bid_to_start_notice"]
    assert expected <= set(skill["optional_tools"])
