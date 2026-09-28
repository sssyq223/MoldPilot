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
    "query_purchase_workbench_context",
    "query_supplier_portal_context",
    "query_purchase_adjustment_context",
    "query_purchase_repurchase_context",
    "query_purchase_supplier_ranking_context",
    "prepare_purchase_claim",
    "prepare_hardware_inquiry",
    "prepare_supplier_order_decision",
    "prepare_supplier_quote_submit",
    "prepare_supplier_delivery_create",
    "prepare_supplier_exception",
    "prepare_purchase_split_adjustment",
    "prepare_purchase_split_adjustment_submit",
    "prepare_purchase_temporary_group_save",
    "prepare_purchase_temporary_group_delete",
    "prepare_purchase_repurchase_submit",
    "prepare_purchase_supplier_rank_adjustment",
    "query_purchase_order_quantity_change_context",
    "query_purchase_hardware_award_context",
    "prepare_purchase_order_quantity_change",
    "prepare_purchase_hardware_award_submit",
    "prepare_purchase_hardware_award_draft",
    "query_purchase_price_compare_context", "query_purchase_repurchase_system_price",
    "prepare_purchase_price_compare_approval",
    "query_supplier_price_access_policy", "prepare_supplier_price_access_decision",
    "prepare_purchase_hardware_award_final_approve",
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
        "prepare_purchase_claim", "prepare_hardware_inquiry", "prepare_supplier_order_decision",
        "prepare_supplier_quote_submit", "prepare_supplier_delivery_create", "prepare_supplier_exception",
        "prepare_purchase_split_adjustment", "prepare_purchase_split_adjustment_submit",
        "prepare_purchase_temporary_group_save", "prepare_purchase_temporary_group_delete",
        "prepare_purchase_repurchase_submit",
        "prepare_purchase_supplier_rank_adjustment",
        "prepare_purchase_order_quantity_change", "prepare_purchase_hardware_award_submit",
        "prepare_purchase_hardware_award_draft", "prepare_purchase_hardware_award_final_approve",
        "prepare_purchase_price_compare_approval",
        "prepare_supplier_price_access_decision",
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


def test_phase2_supplier_quote_prepare_is_typed_and_confirmation_only(monkeypatch):
    monkeypatch.setattr(
        migration_tools,
        "_supplier_portal_context",
        lambda db, user, data: {"status": "RESOLVED", "task_id": data.task_id},
    )
    monkeypatch.setattr(
        migration_tools,
        "_fresh_group_check",
        lambda db, user, proposal: None,
    )
    result = migration_tools.execute_tool(
        None,
        None,
        "prepare_supplier_quote_submit",
        {
            "task_id": 12,
            "expected_version": 4,
            "lines": [{
                "material_id": 91,
                "unit_price": "12.50",
                "tax_rate": "13%",
                "delivery_date": "2026-10-20",
            }],
        },
    )
    assert result["source"] == "agent_proposal"
    assert result["proposal"]["tool"] == "prepare_supplier_quote_submit"
    assert result["proposal"]["input"]["lines"][0]["material_id"] == 91


def test_phase3_split_adjustment_prepare_is_typed_and_confirmation_only(monkeypatch):
    result = migration_tools.execute_tool(
        None,
        None,
        "prepare_purchase_split_adjustment",
        {
            "split_group_id": 7,
            "expected_group_version": 3,
            "expected_snapshot_hash": "a" * 64,
            "reason_type": "quantity_adjust",
            "reason": "按最新需求调整采购拆组",
            "groups": [
                {"group_key": "A", "group_name": "主供应商", "allocations": [
                    {"group_detail_id": 101, "quantity": "2.500"},
                ]},
                {"group_key": "B", "group_name": "备选供应商", "allocations": [
                    {"group_detail_id": 102, "quantity": "1"},
                ]},
            ],
        },
    )
    assert result["source"] == "agent_proposal"
    assert result["proposal"]["tool"] == "prepare_purchase_split_adjustment"
    assert result["proposal"]["input"]["groups"][0]["allocations"][0]["quantity"] == "2.500"


def test_phase3_repurchase_submit_requires_version_and_unique_lines():
    result = migration_tools.execute_tool(
        None,
        None,
        "prepare_purchase_repurchase_submit",
        {
            "batch_id": 29,
            "expected_version": 2,
            "lines": [{
                "line_id": 101,
                "supplier_id": 31,
                "unit_price": "18.50",
            }],
            "approver_overrides": [],
        },
    )
    assert result["source"] == "agent_proposal"
    assert result["proposal"]["tool"] == "prepare_purchase_repurchase_submit"
    assert result["proposal"]["input"]["lines"][0]["line_id"] == 101


def test_phase4_quantity_change_supports_single_and_batch_typed_inputs():
    single = migration_tools.execute_tool(None, None, "prepare_purchase_order_quantity_change", {
        "order_id": 29, "order_detail_id": 301, "target_quantity": "12.500", "reason": "客户需求变更",
    })
    assert single["proposal"]["input"]["target_quantity"] == "12.500"
    batch = migration_tools.execute_tool(None, None, "prepare_purchase_order_quantity_change", {
        "order_id": 29, "changes": [
            {"order_detail_id": 301, "target_quantity": "12.500"},
            {"order_detail_id": 302, "target_quantity": "3"},
        ], "reason": "批量核对后调整",
    })
    assert len(batch["proposal"]["input"]["changes"]) == 2


def test_phase4_hardware_award_final_approval_requires_explicit_price_source():
    result = migration_tools.execute_tool(None, None, "prepare_purchase_hardware_award_final_approve", {
        "todo_id": 18, "expected_version": 4, "comment": "按报价定标", "lines": [{
            "request_detail_id": 901, "final_supplier_id": 77, "final_unit_price": "8.80",
            "source_type": "current_quote", "source_dispatch_id": 31, "source_submission_id": 32,
        }],
    })
    assert result["proposal"]["input"]["lines"][0]["source_type"] == "current_quote"
