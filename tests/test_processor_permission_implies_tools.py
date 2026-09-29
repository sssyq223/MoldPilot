"""Processor grants include the packaged skills and tools."""
from domain_packs.mold import tool_gateway


class _Grant:
    effect = "ALLOW"


class _User:
    super_admin = False
    id = "processor"


def _grants(db, user, permission):
    del db, user
    if permission in {"erp_outsource_processor.read", "erp_outsource_processor.execute"}:
        return [_Grant()]
    return []


def test_processor_permission_includes_query_and_action_tools(monkeypatch):
    monkeypatch.setattr(tool_gateway, "assigned", lambda *args, **kwargs: False)
    monkeypatch.setattr(tool_gateway, "grants_for", _grants)
    allowed = set(tool_gateway.available_tools(None, _User()))
    assert "query_erp_outsource_processor_board" in allowed
    assert "prepare_erp_outsource_processor_accept" in allowed
    assert "query_erp_outsource_followup_board" not in allowed
    skills = {item["key"] for item in tool_gateway.skill_context(None, _User())}
    assert "outsource_processor_query" in skills
    assert "outsource_processor_ops" in skills


def test_processor_read_permission_does_not_include_execute_tools(monkeypatch):
    def grants(db, user, permission):
        del db, user
        if permission == "erp_outsource_processor.read":
            return [_Grant()]
        return []

    monkeypatch.setattr(tool_gateway, "assigned", lambda *args, **kwargs: False)
    monkeypatch.setattr(tool_gateway, "grants_for", grants)
    allowed = set(tool_gateway.available_tools(None, _User()))
    assert "query_erp_outsource_processor_board" in allowed
    assert "prepare_erp_outsource_processor_accept" not in allowed
