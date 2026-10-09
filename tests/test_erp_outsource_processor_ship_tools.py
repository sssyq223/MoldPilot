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
    assert item["moldFamily"] == "M260063"
    assert item["moldBatch"] == "M260063-P1"
    assert item["nextAction"]["orderNo"] == "EO-3"
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
    monkeypatch.setattr(processor_fulfillment, "find_product_order_by_identity", lambda **kwargs: {
        "action": "product_ship",
        "orderId": 3,
        "orderNo": kwargs.get("order_no") or "EO-3",
        "moldNo": "M260063-P1",
        "moldFamily": "M260063",
        "moldBatch": "M260063-P1",
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
        {"order_no": "EO-3", "mold": "M260063", "batch": "M260063-P1", "lines": [{"order_part_id": 21, "qty": 1}]},
    )
    assert result["proposal"]["kind"] == "erp_outsource_processor_product_ship"
    assert result["proposal"]["display"]["订单号"] == "EO-3"
    assert result["proposal"]["display"]["模具号"] == "M260063"
    assert result["proposal"]["display"]["批次号"] == "M260063-P1"
    assert "半成品库" in result["proposal"]["display"]["入库目标"]
    assert "已收1" in result["proposal"]["display"]["明细"]


def test_product_ship_empty_lines_uses_remain(monkeypatch):
    monkeypatch.setattr(processor_fulfillment, "find_product_order_by_identity", lambda **kwargs: {
        "orderId": 3,
        "orderNo": "EO-3",
        "moldNo": "M260063-P1",
        "moldFamily": "M260063",
        "moldBatch": "M260063-P1",
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
        {"order_no": "EO-3"},
    )
    assert result["proposal"]["input"]["lines"] == [{"order_part_id": 21, "qty": 1}]
    assert "可发1" in result["proposal"]["display"]["明细"]
    assert "成品库" in result["proposal"]["display"]["入库目标"]


def test_product_ship_table_no_is_not_order_part_id(monkeypatch):
    monkeypatch.setattr(processor_fulfillment, "find_product_order_by_identity", lambda **kwargs: {
        "orderId": 5161,
        "orderNo": "EO-261008-5A0M",
        "moldNo": "M260063-P4",
        "outsourceType": "part",
        "supplierName": "铂锐",
        "inboundTargets": ["成品库"],
        "parts": [{
            "orderPartId": 4122,
            "partNo": "B1-01",
            "orderQty": 1,
            "receivedQty": 1,
            "shippedQty": 0,
            "remainQty": 1,
            "inboundTargetLabel": "成品库",
        }],
    })
    result = erp_outsource_processor_ship_tools.execute_tool(
        None,
        Admin(),
        erp_outsource_processor_ship_tools.SHIP_TOOL,
        {"order_no": "EO-261008-5A0M", "lines": [{"order_part_id": 1, "qty": 1}]},
    )
    assert result["proposal"]["input"]["lines"] == [{"order_part_id": 4122, "qty": 1}]
    assert "B1-01" in result["proposal"]["display"]["明细"]


def _shippable_item(order_no: str, order_id: int, part_id: int, part_no: str) -> dict:
    return {
        "orderId": order_id,
        "orderNo": order_no,
        "moldNo": "M260063-P1",
        "moldFamily": "M260063",
        "moldBatch": "M260063-P1",
        "outsourceType": "operation",
        "outsourceTypeLabel": "工序委外",
        "supplierName": "铂锐",
        "inboundTargets": ["半成品库"],
        "parts": [{
            "orderPartId": part_id,
            "partNo": part_no,
            "orderQty": 1,
            "receivedQty": 1,
            "shippedQty": 0,
            "remainQty": 1,
            "isFirstOperationLabel": "F",
            "isEndOperationLabel": "F",
            "inboundTargetLabel": "半成品库",
        }],
    }


def test_product_ship_all_prepares_one_card(monkeypatch):
    items = [
        _shippable_item("EO-261009-L6ON", 6101, 4101, "DIE-01"),
        _shippable_item("EO-261009-IPBI", 6102, 4102, "DIE-02"),
    ]
    monkeypatch.setattr(processor_fulfillment, "query_product_items", lambda *args, **kwargs: items)
    result = erp_outsource_processor_ship_tools.execute_tool(
        None,
        Admin(),
        erp_outsource_processor_ship_tools.SHIP_TOOL,
        {"ship_all": True},
    )
    proposal = result["proposal"]
    assert proposal["display"]["操作"] == "成品发货（2张）"
    assert "EO-261009-L6ON" in proposal["display"]["订单"]
    assert "EO-261009-IPBI" in proposal["display"]["订单"]
    assert proposal["input"]["ship_all"] is True
    assert proposal["input"]["order_nos"] == ["EO-261009-L6ON", "EO-261009-IPBI"]
    assert proposal["input"]["lines"] == []
    assert proposal["input"]["order_no"] is None


def test_product_ship_prepare_blocks_before_card_when_stage_forbids(monkeypatch):
    monkeypatch.setattr(processor_fulfillment, "find_product_order_by_identity", lambda **kwargs: {
        "orderId": 5371,
        "orderNo": "EO-261009-JEJY",
        "stage": "material_receiving",
        "supplierName": "铂锐",
        "parts": [{"orderPartId": 4226, "remainQty": 1, "receivedQty": 1, "partNo": "PU-02"}],
    })
    try:
        erp_outsource_processor_ship_tools.execute_tool(
            None,
            Admin(),
            erp_outsource_processor_ship_tools.SHIP_TOOL,
            {"order_no": "EO-261009-JEJY"},
        )
    except DomainError as error:
        assert error.code == "STATE_BLOCKED"
        assert "EO-261009-JEJY" in error.message
        assert "当前阶段不允许成品发货" in error.message
    else:
        raise AssertionError("expected STATE_BLOCKED before confirmation card")


def test_product_ship_blocks_over_received(monkeypatch):
    monkeypatch.setattr(processor_fulfillment, "find_product_order_by_identity", lambda **kwargs: {
        "orderId": 3,
        "orderNo": "EO-3",
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
                {"order_no": "EO-3", "lines": [{"order_part_id": 21, "qty": 2}]},
            ),
        )
    except DomainError as error:
        assert error.code == "STATE_BLOCKED"
        assert "已收" in error.message
    else:
        raise AssertionError("expected STATE_BLOCKED")


def test_clip_ship_erp_drops_qr_fields():
    clipped = erp_outsource_processor_ship_tools.clip_ship_erp({
        "msg": "成品发货已成功提交至ERP，二维码已生成，下一步入库",
        "data": {
            "shipmentNo": "PS-5408-38216",
            "qrCode": "payload",
            "qr_url": "https://example/qr",
            "二维码": "x",
        },
    })
    assert clipped["data"]["shipmentNo"] == "PS-5408-38216"
    assert "qrCode" not in clipped["data"]
    assert "qr_url" not in clipped["data"]
    assert "二维码" not in clipped["data"]
    assert "二维码" not in clipped["msg"]
    assert "PS-5408-38216" == erp_outsource_processor_ship_tools._shipment_no_from_erp({
        "data": {"shipment_no": "PS-5408-38216", "qrCode": "x"},
    })


def test_product_ship_receipt_hides_qr_from_model(monkeypatch):
    item = _shippable_item("EO-261009-XG96", 6201, 4301, "B1-01")
    item["inboundTargets"] = ["成品库"]
    data = erp_outsource_processor_ship_tools.parse(
        erp_outsource_processor_ship_tools.SHIP_TOOL,
        {"order_no": "EO-261009-XG96"},
    )
    monkeypatch.setattr(
        erp_outsource_processor_ship_tools,
        "validate_intent",
        lambda db, user, payload: ({}, data),
    )
    monkeypatch.setattr(
        erp_outsource_processor_ship_tools,
        "_resolve_ship_items",
        lambda data, tokens: [(item, [{"order_part_id": 4301, "qty": 1}])],
    )
    monkeypatch.setattr(erp_outsource_processor_ship_tools, "_tokens", lambda db, user: ["铂锐"])
    monkeypatch.setattr(
        erp_outsource_processor_ship_tools,
        "post_erp",
        lambda *args, **kwargs: {
            "msg": "发货成功，二维码已生成",
            "data": {"shipmentNo": "PS-5408-38216", "qrCode": "secret"},
        },
    )
    receipt = erp_outsource_processor_ship_tools.confirm(None, Admin(), {"_intent_id": "intent-1"})
    assert receipt["shipment_no"] == "PS-5408-38216"
    assert receipt["model_context"]["shipmentNo"] == "PS-5408-38216"
    assert "二维码" not in str(receipt)
    assert "qrCode" not in str(receipt)
    assert "二维码" not in receipt["nextHint"]
    assert receipt["erp"]["data"]["shipmentNo"] == "PS-5408-38216"


def test_sanitize_followup_summary_strips_qr_talk():
    from domain_packs.mold.harness_policy import sanitize_followup_summary

    result = sanitize_followup_summary(
        {
            "summary": "成品发货已成功提交至ERP，发货单号：PS-5408-38216，二维码已生成，下一步请仓库入库。",
            "suggestions": ["二维码已生成", "请仓库入库确认"],
        },
        {"authoritative_receipt": {"action": "processor_product_ship"}},
    )
    assert "二维码" not in result["summary"]
    assert "PS-5408-38216" in result["summary"]
    assert result["suggestions"] == ["请仓库入库确认"]
