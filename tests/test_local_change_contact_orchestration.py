from pathlib import Path

from domain_packs.mold import proposal_handlers, tool_gateway


def test_local_contact_skill_exposes_query_then_proposal_operations():
    skill = tool_gateway.SKILLS["engineering_contact_collaboration"]
    assert skill["tools"] == ["query_contact_cases"]
    required = {
        "query_contact_context", "prepare_contact_resolution", "prepare_contact_respond",
        "prepare_contact_review", "prepare_contact_close", "prepare_contact_set_reviewer",
    }
    assert required <= set(skill["optional_tools"])
    assert all(name in tool_gateway.TOOLS for name in required)


def test_local_contact_proposals_use_existing_confirmation_handler():
    handler = proposal_handlers.handler_for_tool("prepare_contact_review")
    assert handler is not None
    assert handler.action == "contact.execute"
    assert "prepare_contact_close" in handler.tools


def test_local_contact_skill_is_not_an_erp_integration_entrypoint():
    path = Path(__file__).resolve().parents[1] / "backend" / "domain_packs" / "mold" / "skills" / "local" / "change" / "engineering_contact_collaboration" / "SKILL.md"
    text = path.read_text(encoding="utf-8")
    assert "不调用 ERP" in text
    assert "ERPClient" not in text
