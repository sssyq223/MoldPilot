from domain_packs.mold import proposal_handlers, tool_gateway
from domain_packs.mold.ports.errors import DomainError
from domain_packs.mold.tools.erp.procurement import erp_outsource_processor_ship_tools
from domain_packs.mold.tools.erp.procurement.outsource_queries import processor_fulfillment


class Admin:
    super_admin = True
    id = "admin"
    security_version = 1


def test_product_ship_skill_is_registered():
    paths = tool_gateway.skill_paths()
    assert "outsource_processor_product_ship" in tool_gateway.SKILLS
    assert paths["outsource_processor_product_ship"]["path"].is_file()
    assert tool_gateway.TOOLS[erp_outsource_processor_ship_tools.TODO_TOOL]["permission"] == "erp_outsource_processor.read"
    assert tool_gateway.TOOLS[erp_outsource_processor_ship_tools.SHIP_TOOL]["permission"] == "erp_outsource_processor.execute"
    assert proposal_handlers.handler_for_tool(erp_outsource_processor_ship_tools.SHIP_TOOL).action == "erp_outsource_processor.execute"


def test_inbound_target_follows_confirmed_business_matrix():
    part = processor_fulfillment.inbound_target(outsource_type="part", is_end_operation=False)
    mold = processor_fulfillment.inbound_target(outsource_type="mold", is_end_operation=None)
    end_op = processor_fulfillment.inbound_target(outsource_type="operation", is_end_operation=True)
    mid_op = processor_fulfillment.inbound_target(outsource_type="operation", is_end_operation=False)
    missing = processor_fulfillment.inbound_target(outsource_type="operation", is_end_operation=None)
    unknown = processor_fulfillment.inbound_target(outsource_type=None, is_end_operation=True)
    assert part["label"] == "成品库" and part["warning"] is None
    assert mold["label"] == "成品库"
    assert end_op["label"] == "成品库"
    assert mid_op["label"] == "半成品库" and mid_op["warning"] is None
    assert missing["label"] == "半成品库"
    assert missing["warning"]
    assert missing["ruleVersion"] == processor_fulfillment.PROCESSOR_INBOUND_RULE_VERSION
    assert unknown["label"] == "半成品库"
    assert unknown["warning"] is None
    assert processor_fulfillment.flag_tf(True) == "T"
    assert processor_fulfillment.flag_tf(False) == "F"


def test_product_item_shows_target_and_partial_qty():
    item = processor_fulfillment.product_item({
        "order_id": 3,
        "order_no": "EO-3",
        "outsource_type": "operation",
        "stage": "producing",
        "supplier_name": "铂锐",
        "mold_no": "M260063-P1",
        "parts": [{
            "orderPartId": 21,
            "partNo": "PU-06",
            "orderQty": 2,
            "receivedQty": 1,
            "shippedQty": 0,
            "remainQty": 1,
            "isFirstOperation": False,
            "isEndOperation": False,
        }],
    })
    assert item["inboundTargets"] == ["半成品库"]
    assert item["parts"][0]["isFirstOperationLabel"] == "F"
    assert item["parts"][0]["isEndOperationLabel"] == "F"
    assert item["parts"][0]["inboundTargetLabel"] == "半成品库"


def test_product_item_mold_and_end_operation_go_to_finished():
    mold = processor_fulfillment.product_item({
        "order_id": 4,
        "outsource_type": "mold",
        "stage": "producing",
        "parts": [{
            "orderPartId": 31,
            "partNo": "MD-01",
            "remainQty": 1,
            "isEndOperation": False,
        }],
    })
    end_op = processor_fulfillment.product_item({
        "order_id": 5,
        "outsource_type": "operation",
        "stage": "producing",
        "parts": [{
            "orderPartId": 32,
            "partNo": "PU-06",
            "remainQty": 1,
            "isEndOperation": True,
        }],
    })
    assert mold["inboundTargets"] == ["成品库"]
    assert mold["parts"][0]["inboundTargetLabel"] == "成品库"
    assert end_op["inboundTargets"] == ["成品库"]
    assert end_op["parts"][0]["inboundTargetLabel"] == "成品库"


def test_product_ship_prepare_returns_card(monkeypatch):
    monkeypatch.setattr(processor_fulfillment, "find_product_order", lambda order_id, mold=None: {
        "action": "product_ship",
        "orderId": order_id,
        "orderNo": "EO-3",
        "moldNo": "M260063-P1",
        "outsourceType": "operation",
        "outsourceTypeLabel": "工序委外",
        "supplierName": "铂锐",
        "inboundTargets": ["半成品库"],
        "parts": [{
            "orderPartId": 21,
            "partNo": "PU-06",
            "orderQty": 2,
            "receivedQty": 1,
            "shippedQty": 0,
            "remainQty": 1,
            "isFirstOperationLabel": "F",
            "isEndOperationLabel": "F",
            "inboundTargetLabel": "半成品库",
        }],
    })
    result = erp_outsource_processor_ship_tools.execute_tool(
        None,
        Admin(),
        erp_outsource_processor_ship_tools.SHIP_TOOL,
        {"order_id": 3, "lines": [{"order_part_id": 21, "qty": 1}]},
    )
    assert result["proposal"]["kind"] == "erp_outsource_processor_product_ship"
    assert "半成品库" in result["proposal"]["display"]["入库目标"]
    assert "已收1" in result["proposal"]["display"]["明细"]


def test_product_ship_empty_lines_uses_remain(monkeypatch):
    monkeypatch.setattr(processor_fulfillment, "find_product_order", lambda order_id, mold=None: {
        "orderId": order_id,
        "orderNo": "EO-3",
        "moldNo": "M260063-P1",
        "outsourceType": "part",
        "outsourceTypeLabel": "零件委外",
        "supplierName": "铂锐",
        "inboundTargets": ["成品库"],
        "parts": [{
            "orderPartId": 21,
            "partNo": "PU-06",
            "orderQty": 2,
            "receivedQty": 1,
            "shippedQty": 0,
            "remainQty": 1,
            "isFirstOperationLabel": "T",
            "isEndOperationLabel": "T",
            "inboundTargetLabel": "成品库",
        }],
    })
    result = erp_outsource_processor_ship_tools.execute_tool(
        None,
        Admin(),
        erp_outsource_processor_ship_tools.SHIP_TOOL,
        {"order_id": 3},
    )
    assert result["proposal"]["input"]["lines"] == []
    assert "可发1" in result["proposal"]["display"]["明细"]
    assert "成品库" in result["proposal"]["display"]["入库目标"]


def test_product_ship_blocks_over_received(monkeypatch):
    monkeypatch.setattr(processor_fulfillment, "find_product_order", lambda order_id, mold=None: {
        "orderId": order_id,
        "supplierName": "铂锐",
        "parts": [{"orderPartId": 21, "remainQty": 1, "receivedQty": 1, "partNo": "PU-06"}],
    })
    try:
        erp_outsource_processor_ship_tools.preview(
            None,
            Admin(),
            erp_outsource_processor_ship_tools.SHIP_TOOL,
            erp_outsource_processor_ship_tools.parse(
                erp_outsource_processor_ship_tools.SHIP_TOOL,
                {"order_id": 3, "lines": [{"order_part_id": 21, "qty": 2}]},
            ),
        )
    except DomainError as error:
        assert error.code == "STATE_BLOCKED"
        assert "已收" in error.message
    else:
        raise AssertionError("expected STATE_BLOCKED")
