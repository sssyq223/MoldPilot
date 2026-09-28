from types import SimpleNamespace

from domain_packs.mold import tool_gateway
from domain_packs.mold.tools.local import mail_tools


def test_mail_tools_are_registered_with_local_skill():
    assert "query_mail_monitor_status" in tool_gateway.TOOLS
    assert tool_gateway.tool_schema("query_mail_monitor_status")["function"]["name"] == "query_mail_monitor_status"
    skill = tool_gateway.SKILLS["mail_monitoring"]
    assert skill["tools"] == ["query_mail_monitor_status", "query_mail_processing_history"]
    assert tool_gateway.skill_paths()["mail_monitoring"]["domain"] == "mail"


def test_prepare_config_returns_confirmation_card_without_connecting():
    user = SimpleNamespace(id="u1", security_version=3)
    result = mail_tools.execute_tool(None, user, "prepare_mail_monitor_config", {
        "name": "supplier-inbox", "host": "imap.example.test", "username": "robot",
        "secret_ref": "secret://mail/supplier", "allowed_senders": ["example.test"],
    })
    assert result["source"] == "agent_proposal"
    assert result["proposal"]["requires_approval"] is True
    assert result["proposal"]["input"]["secret_ref"] == "secret://mail/supplier"
