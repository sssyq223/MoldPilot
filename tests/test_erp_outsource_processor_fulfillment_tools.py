from domain_packs.mold import proposal_handlers, tool_gateway
from domain_packs.mold.ports.errors import DomainError
from domain_packs.mold.tools.erp.procurement import erp_outsource_processor_fulfillment_tools
from domain_packs.mold.tools.erp.procurement.outsource_queries import processor_fulfillment


class Admin:
    super_admin = True
    id = "admin"
    security_version = 1


def test_processor_fulfillment_skill_is_registered():
    paths = tool_gateway.skill_paths()
    assert "outsource_processor_fulfillment" in tool_gateway.SKILLS
    assert paths["outsource_processor_fulfillment"]["path"].is_file()
    assert tool_gateway.TOOLS[erp_outsource_processor_fulfillment_tools.TODO_TOOL]["permission"] == "erp_outsource_processor.read"
    assert tool_gateway.TOOLS[erp_outsource_processor_fulfillment_tools.RECEIPT_TOOL]["permission"] == "erp_outsource_processor.execute"
    assert proposal_handlers.handler_for_tool(erp_outsource_processor_fulfillment_tools.RECEIPT_TOOL).action == "erp_outsource_processor.execute"


def test_processor_receipt_prepare_returns_card(monkeypatch):
    monkeypatch.setattr(processor_fulfillment, "find_shipment", lambda shipment_id: {
        "action": "receipt",
        "shipmentId": shipment_id,
        "shipmentNo": "SU-1",
        "orderId": 3,
        "orderNo": "EO-3",
        "moldNo": "M260063-P1",
        "outsourceType": "part",
        "outsourceTypeLabel": "零件委外",
        "supplierName": "铂锐",
        "pendingLines": [{"lineId": 11, "qty": 1, "partNo": "PU-06"}],
    })
    result = erp_outsource_processor_fulfillment_tools.execute_tool(
        None, Admin(), erp_outsource_processor_fulfillment_tools.RECEIPT_TOOL, {"shipment_id": 8},
    )
    assert result["proposal"]["kind"] == "erp_outsource_processor_receipt"
    assert result["proposal"]["display"]["发货单"] == "SU-1"


def test_operation_cannot_prepare_receipt(monkeypatch):
    monkeypatch.setattr(processor_fulfillment, "find_shipment", lambda shipment_id: {
        "shipmentId": shipment_id,
        "outsourceType": "operation",
        "outsourceTypeLabel": "工序委外",
        "supplierName": "铂锐",
        "pendingLines": [{"lineId": 11}],
    })
    try:
        erp_outsource_processor_fulfillment_tools.preview(
            None,
            Admin(),
            erp_outsource_processor_fulfillment_tools.RECEIPT_TOOL,
            erp_outsource_processor_fulfillment_tools.parse(
                erp_outsource_processor_fulfillment_tools.RECEIPT_TOOL, {"shipment_id": 8},
            ),
        )
    except DomainError as error:
        assert error.code == "STATE_BLOCKED"
    else:
        raise AssertionError("expected STATE_BLOCKED")


def test_fulfillment_question_picks_receipt_tab():
    parsed = processor_fulfillment.parse_question("M260063 确认来料")
    assert parsed["tab"] == "receipt"
    assert parsed["mold_family"] == "M260063"


def test_wait_sql_requires_warehouse_pending_supply():
    assert "entrust_material_supply_tasks" in processor_fulfillment.WAIT_SQL
    assert "responsible_type" in processor_fulfillment.WAIT_SQL
    assert "pending" in processor_fulfillment.WAIT_SQL.lower()


def test_product_sql_does_not_block_operation_without_warehouse_pending():
    sql = processor_fulfillment.PRODUCT_SQL.lower()
    assert "outsource_type" in sql
    assert "entrust_material_supply_tasks" in sql
    assert "operation" in sql
