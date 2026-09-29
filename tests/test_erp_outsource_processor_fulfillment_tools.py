from domain_packs.mold import proposal_handlers, tool_gateway
from domain_packs.mold.ports.errors import DomainError
from domain_packs.mold.tools.erp.procurement import erp_outsource_processor_fulfillment_tools
from domain_packs.mold.tools.erp.procurement.outsource_queries import buyer_todo, processor_fulfillment


class Admin:
    super_admin = True
    id = "admin"
    security_version = 1


def test_receipt_confirm_uses_fulfillment_handler_not_quote():
    from domain_packs.mold.erp.core import business
    from domain_packs.mold.tools.erp.procurement import erp_outsource_warehouse_inbound_tools

    class Step:
        def __init__(self, tool):
            self.tool = tool

    class DB:
        def __init__(self, tool):
            self.tool = tool

        def get(self, model, ident):
            return Step(self.tool)

    receipt = business.proposal_handler(
        DB(erp_outsource_processor_fulfillment_tools.RECEIPT_TOOL),
        "erp_outsource_processor.execute",
        {"step_id": "step-receipt"},
    )
    quote = proposal_handlers.handler_for_action("erp_outsource_processor.execute")
    assert receipt.module.endswith("erp_outsource_processor_fulfillment_tools")
    assert quote.module.endswith("erp_outsource_processor_tools")
    assert receipt is not quote

    inbound = business.proposal_handler(
        DB(erp_outsource_warehouse_inbound_tools.ARRIVAL_TOOL),
        "erp_outsource_warehouse.execute",
        {"step_id": "step-arrival"},
    )
    assert inbound.module.endswith("erp_outsource_warehouse_inbound_tools")


def test_processor_fulfillment_skill_is_registered():
    paths = tool_gateway.skill_paths()
    assert "outsource_processor_fulfillment" in tool_gateway.SKILLS
    assert paths["outsource_processor_fulfillment"]["path"].is_file()
    assert tool_gateway.TOOLS[erp_outsource_processor_fulfillment_tools.TODO_TOOL]["permission"] == "erp_outsource_processor.read"
    assert tool_gateway.TOOLS[erp_outsource_processor_fulfillment_tools.RECEIPT_TOOL]["permission"] == "erp_outsource_processor.execute"
    assert proposal_handlers.handler_for_tool(erp_outsource_processor_fulfillment_tools.RECEIPT_TOOL).action == "erp_outsource_processor.execute"


def test_spoken_receipt_uses_visible_board_row():
    arguments = erp_outsource_processor_fulfillment_tools.spoken_receipt_arguments("NO.1确认收货")
    assert arguments == {"board_row": 1}
    assert erp_outsource_processor_fulfillment_tools.spoken_receipt_arguments("确认收货") == {}
    assert erp_outsource_processor_fulfillment_tools.spoken_receipt_arguments("确认到货") is None


def test_board_row_receipt_uses_visible_pending_row(monkeypatch):
    monkeypatch.setattr(buyer_todo, "run", lambda parsed, processor_tokens=None: {
        "items": [{
            "station": "待收料",
            "stationLabel": "待收料",
            "orderNo": "EO-260928-WE11",
        }],
    })
    monkeypatch.setattr(processor_fulfillment, "query_items", lambda parsed, processor_tokens=None: [{
        "action": "receipt",
        "orderNo": "EO-260928-WE11",
        "shipmentId": 36,
    }])
    monkeypatch.setattr(processor_fulfillment, "find_shipment", lambda shipment_id: {
        "shipmentId": shipment_id,
        "shipmentNo": "SU-5136-82970",
        "orderNo": "EO-260928-WE11",
        "outsourceType": "part",
        "outsourceTypeLabel": "零件委外",
        "supplierCode": "SUP000114",
        "pendingLines": [{"lineId": 11, "qty": 1, "partNo": "B1-01"}],
    })
    result = erp_outsource_processor_fulfillment_tools.execute_tool(
        None,
        Admin(),
        erp_outsource_processor_fulfillment_tools.RECEIPT_TOOL,
        {"board_row": 1},
    )
    assert result["proposal"]["input"]["shipment_id"] == 36
    assert result["proposal"]["display"]["发货单"] == "SU-5136-82970"


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


def test_product_item_is_one_shippable_order():
    item = processor_fulfillment.product_item({
        "order_id": 5136,
        "order_no": "EO-260928-WE11",
        "outsource_type": "part",
        "stage": "producing",
        "mold_no": "M260063-P1",
        "parts": [
            {"orderPartId": 1, "partNo": "B1-01", "partName": "下托板", "remainQty": 1, "orderQty": 1},
            {"orderPartId": 2, "partNo": "B2-01", "partName": "下垫脚", "remainQty": 2, "orderQty": 2},
        ],
    })
    assert item["station"] == "待成品发货"
    assert item["orderNo"] == "EO-260928-WE11"
    assert item["lineCount"] == 2
    assert item["remainQty"] == 3
    assert "B1-01" in item["partDetails"]
    assert "成品库" in item["partDetails"]


def test_spoken_product_ship_prepares_only_when_asked_to_ship():
    from domain_packs.mold.tools.erp.procurement import erp_outsource_processor_ship_tools as ship

    assert ship.spoken_product_ship_arguments("成品发货") is None
    assert ship.spoken_product_ship_arguments("有没有可以成品发货的订单？") is None
    assert ship.spoken_product_ship_arguments("确认收货") is None
    assert ship.spoken_product_ship_arguments("确认成品发货") == {}
    assert ship.spoken_product_ship_arguments("NO.1成品发货") == {"board_row": 1}
    named = ship.spoken_product_ship_arguments("确认成品发货", "上一张可发订单 EO-260928-WE11")
    assert named["order_no"] == "EO-260928-WE11"
