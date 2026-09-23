from domain_packs.mold import proposal_handlers, tool_gateway
from domain_packs.mold.ports.errors import DomainError
from domain_packs.mold.tools.erp.procurement import erp_outsource_warehouse_inbound_tools
from domain_packs.mold.tools.erp.procurement.outsource_queries import warehouse_inbound


class Admin:
    super_admin = True
    id = "admin"
    security_version = 1


SHIPMENT = {
    "action": "arrival",
    "actionLabel": "仓库收货",
    "shipmentId": 9,
    "shipmentNo": "PS-9",
    "orderId": 3,
    "orderNo": "EO-3",
    "moldNo": "M260063-P1",
    "outsourceType": "operation",
    "outsourceTypeLabel": "工序委外",
    "pendingArrivalQty": 1,
    "pendingInboundQty": 1,
    "inboundTargets": ["半成品库"],
    "lines": [{
        "lineId": 21,
        "partNo": "PU-06",
        "pendingArrivalQty": 1,
        "pendingInboundQty": 1,
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


def test_arrival_prepare_returns_card(monkeypatch):
    monkeypatch.setattr(warehouse_inbound, "find_shipment", lambda shipment_id: dict(SHIPMENT, shipmentId=shipment_id))
    result = erp_outsource_warehouse_inbound_tools.execute_tool(
        None,
        Admin(),
        erp_outsource_warehouse_inbound_tools.ARRIVAL_TOOL,
        {"shipment_id": 9},
    )
    assert result["proposal"]["kind"] == "erp_outsource_warehouse_arrival"
    assert result["proposal"]["display"]["操作"] == "仓库收货"
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


def test_item_prefers_arrival_then_inbound():
    arrival = warehouse_inbound.item_from_row({
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
    assert arrival["actionLabel"] == "仓库收货"
    assert inbound["actionLabel"] == "仓储入库"
    assert inbound["inboundTargets"] == ["成品库"]
    assert arrival["nextAction"]["action"] == "arrival"
    assert inbound["nextAction"]["action"] == "inbound"


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


def test_pending_inbound_is_capped_by_arrival_confirmed_quantity():
    normalized = " ".join(warehouse_inbound.SQL.split())
    assert (
        "coalesce(line.arrival_confirmed_qty, 0) "
        "- coalesce(line.inbound_received_qty, 0) "
        "- coalesce(line.return_qty, 0)"
    ) in normalized
