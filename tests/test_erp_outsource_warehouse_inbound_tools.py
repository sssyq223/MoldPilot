from domain_packs.mold import proposal_handlers, tool_gateway
from domain_packs.mold.ports.errors import DomainError
from domain_packs.mold.tools.erp.procurement import erp_outsource_warehouse_inbound_tools
from domain_packs.mold.tools.erp.procurement.outsource_queries import warehouse_inbound


class Admin:
    super_admin = True
    id = "admin"
    security_version = 1


SHIPMENT = {
    "action": "inbound",
    "actionLabel": "仓储入库",
    "shipmentId": 9,
    "shipmentNo": "PS-9",
    "orderId": 3,
    "orderNo": "EO-3",
    "moldNo": "M260063-P1",
    "outsourceType": "operation",
    "outsourceTypeLabel": "工序委外",
    "supplierId": 8,
    "supplierName": "青岛和兴嘉业",
    "pendingArrivalQty": 1,
    "pendingInboundQty": 1,
    "inboundTargets": ["半成品库"],
    "lines": [{
        "lineId": 21,
        "partNo": "PU-06",
        "partName": "冲头",
        "moldNo": "M260063-P1",
        "pendingArrivalQty": 1,
        "pendingInboundQty": 1,
        "isEndOperation": False,
        "isEndOperationLabel": "F",
        "inboundTarget": "semi_finished",
        "inboundTargetLabel": "半成品库",
        "inboundRuleVersion": "processor-inbound-v1",
    }],
}


def test_warehouse_inbound_skill_is_registered():
    paths = tool_gateway.skill_paths()
    assert "outsource_warehouse_inbound" in tool_gateway.SKILLS
    assert paths["outsource_warehouse_inbound"]["path"].is_file()
    assert tool_gateway.TOOLS[erp_outsource_warehouse_inbound_tools.TODO_TOOL]["permission"] == "erp_outsource_warehouse.read"
    assert tool_gateway.TOOLS[erp_outsource_warehouse_inbound_tools.ARRIVAL_TOOL]["permission"] == "erp_outsource_warehouse.execute"
    assert proposal_handlers.handler_for_tool(erp_outsource_warehouse_inbound_tools.ARRIVAL_TOOL).action == "erp_outsource_warehouse.execute"
    assert proposal_handlers.handler_for_tool(erp_outsource_warehouse_inbound_tools.INBOUND_TOOL).action == "erp_outsource_warehouse.execute"


def test_spoken_return_always_prepares_inbound(monkeypatch):
    monkeypatch.setattr(warehouse_inbound, "find_pending_by_identity", lambda **kwargs: [{
        **SHIPMENT,
        "pendingArrivalQty": 30,
        "pendingInboundQty": 30,
    }])
    name, arguments = erp_outsource_warehouse_inbound_tools.spoken_return_invoke(
        "入库确认吧",
        "当前有1条待办：订单 EO-260928-WE11，发货单号 PS-5136-441118，待入库。",
        {
            erp_outsource_warehouse_inbound_tools.ARRIVAL_TOOL,
            erp_outsource_warehouse_inbound_tools.INBOUND_TOOL,
        },
    )
    assert name == erp_outsource_warehouse_inbound_tools.INBOUND_TOOL
    assert arguments["shipment_id"] == 9
    assert arguments["order_no"] == "EO-260928-WE11"
    assert arguments["shipment_no"] == "PS-5136-441118"


def test_inbound_prepare_does_not_redirect_to_arrival(monkeypatch):
    monkeypatch.setattr(warehouse_inbound, "find_shipment", lambda shipment_id: {
        **SHIPMENT,
        "pendingArrivalQty": 30,
        "pendingInboundQty": 30,
        "lines": [{**SHIPMENT["lines"][0], "pendingArrivalQty": 30, "pendingInboundQty": 30}],
    })
    result = erp_outsource_warehouse_inbound_tools.execute_tool(
        None,
        Admin(),
        erp_outsource_warehouse_inbound_tools.INBOUND_TOOL,
        {"shipment_id": 9},
    )
    assert result["proposal"]["kind"] == "erp_outsource_warehouse_inbound"
    assert result["proposal"]["display"]["操作"] == "仓储入库"


def test_parse_inbound_confirm_speech_does_not_hide_arrival():
    parsed = warehouse_inbound.parse_question("入库确认吧")
    assert parsed["tab"] == ""


def test_arrival_prepare_returns_inbound_card(monkeypatch):
    monkeypatch.setattr(warehouse_inbound, "find_shipment", lambda shipment_id: dict(SHIPMENT, shipmentId=shipment_id))
    result = erp_outsource_warehouse_inbound_tools.execute_tool(
        None,
        Admin(),
        erp_outsource_warehouse_inbound_tools.ARRIVAL_TOOL,
        {"shipment_id": 9},
    )
    assert result["proposal"]["kind"] == "erp_outsource_warehouse_inbound"
    assert result["proposal"]["display"]["操作"] == "仓储入库"
    assert "半成品库" in result["proposal"]["display"]["入库目标"]


def test_inbound_prepare_returns_card(monkeypatch):
    monkeypatch.setattr(warehouse_inbound, "find_shipment", lambda shipment_id: {
        **SHIPMENT,
        "action": "inbound",
        "pendingArrivalQty": 0,
        "lines": [{**SHIPMENT["lines"][0], "pendingArrivalQty": 0}],
    })
    result = erp_outsource_warehouse_inbound_tools.execute_tool(
        None,
        Admin(),
        erp_outsource_warehouse_inbound_tools.INBOUND_TOOL,
        {"shipment_id": 9, "lines": [{"shipment_line_id": 21, "qty": 1}]},
    )
    assert result["proposal"]["kind"] == "erp_outsource_warehouse_inbound"
    assert result["proposal"]["display"]["操作"] == "仓储入库"
    assert result["proposal"]["display"]["入库目标"] == "半成品库"
    assert result["proposal"]["display"]["规则版本"] == "processor-inbound-v1"


def test_inbound_blocks_over_pending(monkeypatch):
    monkeypatch.setattr(warehouse_inbound, "find_shipment", lambda shipment_id: SHIPMENT)
    try:
        erp_outsource_warehouse_inbound_tools.preview(
            None,
            Admin(),
            erp_outsource_warehouse_inbound_tools.INBOUND_TOOL,
            erp_outsource_warehouse_inbound_tools.parse(
                erp_outsource_warehouse_inbound_tools.INBOUND_TOOL,
                {"shipment_id": 9, "lines": [{"shipment_line_id": 21, "qty": 3}]},
            ),
        )
    except DomainError as error:
        assert error.code == "STATE_BLOCKED"
    else:
        raise AssertionError("expected STATE_BLOCKED")


def test_item_shows_inbound_after_shipment():
    shipped = warehouse_inbound.item_from_row({
        "shipment_id": 9,
        "outsource_type": "part",
        "arrival_status": "pending_arrival_confirm",
        "mold_no": "M260063-P1",
        "lines": [{"lineId": 1, "pendingArrivalQty": 2, "pendingInboundQty": 2, "isEndOperation": True}],
    })
    inbound = warehouse_inbound.item_from_row({
        "shipment_id": 9,
        "outsource_type": "part",
        "arrival_status": "arrival_confirmed",
        "mold_no": "M260063-P1",
        "lines": [{"lineId": 1, "pendingArrivalQty": 0, "pendingInboundQty": 2, "isEndOperation": True}],
    })
    empty = warehouse_inbound.item_from_row({
        "shipment_id": 9,
        "outsource_type": "part",
        "lines": [{"lineId": 1, "pendingArrivalQty": 2, "pendingInboundQty": 0, "isEndOperation": True}],
    })
    assert shipped["actionLabel"] == "仓储入库"
    assert shipped["stationLabel"] == "待入库"
    assert inbound["actionLabel"] == "仓储入库"
    assert inbound["stationLabel"] == "待入库"
    assert inbound["inboundTargets"] == ["成品库"]
    assert shipped["nextAction"]["action"] == "inbound"
    assert inbound["nextAction"]["action"] == "inbound"
    assert empty is None


def test_inbound_item_follows_business_target_matrix():
    mold = warehouse_inbound.item_from_row({
        "shipment_id": 10,
        "outsource_type": "mold",
        "arrival_status": "arrival_confirmed",
        "lines": [{"lineId": 2, "pendingArrivalQty": 0, "pendingInboundQty": 1, "isEndOperation": False}],
    })
    end_op = warehouse_inbound.item_from_row({
        "shipment_id": 11,
        "outsource_type": "operation",
        "arrival_status": "arrival_confirmed",
        "lines": [{"lineId": 3, "pendingArrivalQty": 0, "pendingInboundQty": 1, "isEndOperation": True}],
    })
    mid_op = warehouse_inbound.item_from_row({
        "shipment_id": 12,
        "outsource_type": "operation",
        "arrival_status": "arrival_confirmed",
        "lines": [{"lineId": 4, "pendingArrivalQty": 0, "pendingInboundQty": 1, "isEndOperation": False}],
    })
    unknown_op = warehouse_inbound.item_from_row({
        "shipment_id": 13,
        "outsource_type": "operation",
        "arrival_status": "arrival_confirmed",
        "lines": [{"lineId": 5, "pendingArrivalQty": 0, "pendingInboundQty": 1, "isEndOperation": None}],
    })
    assert mold["inboundTargets"] == ["成品库"]
    assert end_op["inboundTargets"] == ["成品库"]
    assert mid_op["inboundTargets"] == ["半成品库"]
    assert unknown_op["inboundTargets"] == ["半成品库"]
    assert unknown_op["inboundTargetWarnings"]
    assert unknown_op["inboundRuleVersion"] == "processor-inbound-v1"


def test_inbound_prepare_blocks_when_erp_target_missing(monkeypatch):
    monkeypatch.setattr(warehouse_inbound, "find_shipment", lambda shipment_id: {
        **SHIPMENT,
        "action": "inbound",
        "pendingArrivalQty": 0,
        "lines": [{
            **SHIPMENT["lines"][0],
            "pendingArrivalQty": 0,
            "inboundTarget": None,
            "inboundTargetLabel": None,
        }],
    })
    try:
        erp_outsource_warehouse_inbound_tools.execute_tool(
            None,
            Admin(),
            erp_outsource_warehouse_inbound_tools.INBOUND_TOOL,
            {"shipment_id": 9, "lines": [{"shipment_line_id": 21, "qty": 1}]},
        )
    except DomainError as error:
        assert error.code == "STATE_BLOCKED"
        assert "未返回入库目标" in error.message
    else:
        raise AssertionError("expected STATE_BLOCKED")


def test_inbound_prepare_blocks_mixed_targets(monkeypatch):
    monkeypatch.setattr(warehouse_inbound, "find_shipment", lambda shipment_id: {
        **SHIPMENT,
        "pendingInboundQty": 2,
        "inboundTargets": ["成品库", "半成品库"],
        "lines": [
            {**SHIPMENT["lines"][0], "lineId": 21, "inboundTarget": "finished", "inboundTargetLabel": "成品库"},
            {**SHIPMENT["lines"][0], "lineId": 22, "inboundTarget": "semi_finished", "inboundTargetLabel": "半成品库"},
        ],
    })
    try:
        erp_outsource_warehouse_inbound_tools.execute_tool(
            None,
            Admin(),
            erp_outsource_warehouse_inbound_tools.INBOUND_TOOL,
            {"shipment_id": 9},
        )
    except DomainError as error:
        assert error.code == "STATE_BLOCKED"
        assert "同一目标库" in error.message
    else:
        raise AssertionError("expected STATE_BLOCKED")


def test_erp_inbound_confirm_lines_read_processor_inbound_v1():
    lines = warehouse_inbound.erp_inbound_confirm_lines({
        "code": 200,
        "data": {
            "inboundDetails": [
                {
                    "shipmentLineId": 21,
                    "processorInboundTarget": "finished",
                    "targetWarehouseName": "成品库",
                    "targetRuleVersion": "processor-inbound-v1",
                    "isEndOperation": True,
                    "inboundQty": 1,
                },
                {
                    "shipmentLineId": 22,
                    "processorInboundTarget": "semi_finished",
                    "targetWarehouseName": "半成品库",
                    "targetRuleVersion": "processor-inbound-v1",
                    "targetWarning": "工序委外缺少末道工序标志，已按半成品库处理，请核对排产数据",
                    "isEndOperation": None,
                    "inboundQty": 2,
                },
            ]
        },
    })
    assert [line["inboundTarget"] for line in lines] == ["finished", "semi_finished"]
    assert lines[1]["inboundTargetWarning"]
    assert lines[0]["inboundRuleVersion"] == "processor-inbound-v1"


def test_erp_inbound_confirm_lines_read_material_inbound_detail_list():
    lines = warehouse_inbound.erp_inbound_confirm_lines({
        "data": {
            "processorInboundTarget": "finished",
            "detailList": [{
                "processorDeliveryDetailId": 21,
                "processorInboundTarget": "finished",
                "inboundQuantity": 1,
            }],
        },
    })
    assert lines == [{
        "shipmentLineId": 21,
        "inboundTarget": "finished",
        "inboundTargetLabel": "成品库",
        "inboundRuleVersion": None,
        "inboundTargetWarning": None,
        "isEndOperation": None,
        "inboundQty": 1,
    }]


def test_pending_inbound_follows_erp_delivery_minus_received():
    normalized = " ".join(warehouse_inbound.SQL.split())
    assert (
        "coalesce(line.qty, 0) "
        "- coalesce(line.inbound_received_qty, 0) "
        "- coalesce(line.return_qty, 0) "
        "- coalesce(line.arrival_exception_qty, 0)"
    ) in normalized
    assert "arrival_confirmed_qty, 0) - coalesce(line.inbound_received_qty" not in normalized


def test_material_inbound_payload_uses_type_3():
    body = erp_outsource_warehouse_inbound_tools.build_material_inbound_payload(
        SHIPMENT,
        [{"shipment_line_id": 21, "qty": 1}],
        "ok",
    )
    assert body["inboundType"] == 3
    assert body["processorInboundTarget"] == "semi_finished"
    assert body["warehouse"] == "半成品仓库"
    assert body["deliveryId"] == 9
    assert body["detailList"][0]["processorDeliveryDetailId"] == 21
    assert body["detailList"][0]["inboundQuantity"] == 1
    assert body["detailList"][0]["isLastOperation"] == 0


def test_confirm_posts_material_inbound(monkeypatch):
    captured = {}

    def fake_post(db, user, path, body, **kwargs):
        captured["path"] = path
        captured["body"] = body
        captured["kwargs"] = kwargs
        return {
            "data": {
                "processorInboundTarget": "semi_finished",
                "detailList": [{
                    "processorDeliveryDetailId": 21,
                    "processorInboundTarget": "semi_finished",
                    "inboundQuantity": 1,
                }],
            }
        }

    monkeypatch.setattr(
        erp_outsource_warehouse_inbound_tools,
        "validate_intent",
        lambda db, user, payload: (
            {},
            erp_outsource_warehouse_inbound_tools.parse(
                erp_outsource_warehouse_inbound_tools.INBOUND_TOOL,
                {"shipment_id": 9, "remark": "ok"},
            ),
            erp_outsource_warehouse_inbound_tools.INBOUND_TOOL,
        ),
    )
    monkeypatch.setattr(warehouse_inbound, "find_shipment", lambda shipment_id: dict(SHIPMENT))
    monkeypatch.setattr(erp_outsource_warehouse_inbound_tools, "post_erp", fake_post)
    result = erp_outsource_warehouse_inbound_tools.confirm(None, Admin(), {"_intent_id": "intent-1"})
    assert captured["path"] == "material/inbound"
    assert captured["body"]["inboundType"] == 3
    assert captured["kwargs"]["action"] == "warehouse_inbound"
    assert result["action"] == "warehouse_inbound"
    assert "质检领取" in result["nextHint"]
    assert result["inboundTargets"] == ["半成品库"]
