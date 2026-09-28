from app.tool_gateway import tool_schema
from domain_packs.mold import proposal_handlers, tool_gateway
from domain_packs.mold.tools.erp.procurement import migration_tools


MIGRATED_TOOLS = {
    "query_raw_material_purchase_context",
    "query_hardware_purchase_context",
    "query_supplier_procurement_context",
    "query_purchase_decision_context",
    "prepare_raw_material_split",
    "prepare_purchase_decision",
    "prepare_hardware_quote",
    "prepare_raw_material_order",
    "prepare_hardware_order",
    "prepare_supplier_delivery_change",
}


def test_procurement_migration_tools_are_registered_with_typed_schemas():
    assert MIGRATED_TOOLS <= set(tool_gateway.TOOLS)
    for key in MIGRATED_TOOLS:
        schema = tool_schema(key)["function"]
        assert schema["name"] == key
        assert schema["parameters"]["type"] == "object"
        assert schema["parameters"].get("additionalProperties") is False


def test_procurement_migration_skills_have_skill_documents_and_confirmation_handlers():
    tool_gateway.skill_paths.cache_clear()
    paths = tool_gateway.skill_paths()
    assert {"steel_purchase", "hardware_purchase", "supplier_collaboration", "purchase_decision_governance"} <= set(paths)
    handled = set()
    for handler in proposal_handlers.HANDLERS:
        if handler.action == "procurement_erp.execute":
            handled |= set(handler.tools)
    assert {
        "prepare_raw_material_split", "prepare_purchase_decision", "prepare_hardware_quote",
        "prepare_raw_material_order", "prepare_hardware_order", "prepare_supplier_delivery_change",
    } <= handled


def test_prepare_hardware_quote_is_a_confirmation_card_and_does_not_call_erp(monkeypatch):
    monkeypatch.setattr(
        migration_tools,
        "_group_snapshot",
        lambda db, user, group_id: {"groupId": group_id, "groupStatus": "READY", "version": 3},
    )
    result = migration_tools.execute_tool(
        None,
        None,
        "prepare_hardware_quote",
        {
            "group_id": 7,
            "supplier_id": 21,
            "unit_price": "12.50",
            "delivery_date": "2026-10-15",
            "approver_id": 9,
            "expected_status": "READY",
            "expected_version": 3,
        },
    )
    assert result["source"] == "agent_proposal"
    assert result["proposal"]["action"] == "procurement_erp.execute"
    assert result["proposal"]["tool"] == "prepare_hardware_quote"
    assert result["proposal"]["snapshot"]["version"] == 3
