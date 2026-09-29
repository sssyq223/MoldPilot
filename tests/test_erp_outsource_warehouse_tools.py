from domain_packs.mold import proposal_handlers, tool_gateway
from domain_packs.mold.ports.errors import DomainError
from domain_packs.mold.tools.erp.procurement import erp_outsource_warehouse_tools
from domain_packs.mold.tools.erp.procurement.outsource_queries import warehouse_inbound, warehouse_todo


class Admin:
    super_admin = True
    id = "admin"
    security_version = 1


TASK = {
    "taskId": 7,
    "orderId": 3,
    "orderNo": "EO-3",
    "moldNo": "M260063-P1",
    "partNo": "PU-06",
    "qty": 1,
    "status": "pending",
    "sourceType": "material_stock",
    "outsourceType": "part",
    "outsourceTypeLabel": "零件委外",
    "processName": "",
    "processorName": "铂锐",
    "needsWeight": False,
    "nextHint": "确认发货后由加工商确认原料收货，再进入生产。",
}


def test_warehouse_skill_is_registered():
    paths = tool_gateway.skill_paths()
    assert "outsource_warehouse_ops" in tool_gateway.SKILLS
    assert paths["outsource_warehouse_ops"]["path"].is_file()
    assert tool_gateway.TOOLS[erp_outsource_warehouse_tools.TODO_TOOL]["permission"] == "erp_outsource_warehouse.read"
    assert tool_gateway.TOOLS[erp_outsource_warehouse_tools.SHIP_TOOL]["permission"] == "erp_outsource_warehouse.execute"
    assert proposal_handlers.handler_for_tool(erp_outsource_warehouse_tools.SHIP_TOOL).action == "erp_outsource_warehouse.execute"


def test_warehouse_ship_prepare_returns_card(monkeypatch):
    monkeypatch.setattr(warehouse_todo, "find_pending_by_identity", lambda **kwargs: [dict(TASK)])
    result = erp_outsource_warehouse_tools.execute_tool(None, Admin(), erp_outsource_warehouse_tools.SHIP_TOOL, {
        "order_no": "EO-3",
        "mold": "M260063",
        "batch": "M260063-P1",
    })
    assert result["proposal"]["kind"] == "erp_outsource_warehouse_ship"
    assert result["proposal"]["display"]["操作"] == "原料发货"
    assert result["proposal"]["display"]["订单号"] == "EO-3"
    assert result["proposal"]["display"]["模具号"] == "M260063"
    assert result["proposal"]["display"]["批次号"] == "M260063-P1"


def test_warehouse_operation_card_is_prepare(monkeypatch):
    monkeypatch.setattr(warehouse_todo, "find_pending_by_identity", lambda **kwargs: [{
        **TASK,
        "outsourceType": "operation",
        "outsourceTypeLabel": "工序委外",
        "nextHint": "确认备料完成后 ERP 视为发货=收货，加工商不用再确认来料，可直接成品发货。",
    }])
    result = erp_outsource_warehouse_tools.execute_tool(None, Admin(), erp_outsource_warehouse_tools.SHIP_TOOL, {
        "orderNo": "EO-3",
    })
    assert result["proposal"]["display"]["操作"] == "备料完成"
    assert "不用再确认" in result["proposal"]["display"]["说明"]


def test_heat_treatment_requires_weight(monkeypatch):
    monkeypatch.setattr(warehouse_todo, "find_pending_by_identity", lambda **kwargs: [{
        **TASK,
        "outsourceType": "operation",
        "processName": "热处理",
        "needsWeight": True,
    }])
    try:
        erp_outsource_warehouse_tools.preview(
            erp_outsource_warehouse_tools.SHIP_TOOL,
            erp_outsource_warehouse_tools.parse(erp_outsource_warehouse_tools.SHIP_TOOL, {"order_no": "EO-3"}),
        )
    except DomainError as error:
        assert error.code == "STATE_BLOCKED"
    else:
        raise AssertionError("expected STATE_BLOCKED")


def test_warehouse_item_labels_part_vs_operation():
    part = warehouse_todo.item_from_row({
        "task_id": 1,
        "outsource_type": "part",
        "source_type": "material_stock",
        "mold_code": "M260063-P1",
        "part_no": "PU-06",
        "qty": 2,
        "status": "pending",
        "process_name": "",
    })
    operation = warehouse_todo.item_from_row({
        "task_id": 2,
        "outsource_type": "operation",
        "source_type": "semi_finished_stock",
        "mold_code": "M260063-P1",
        "part_no": "PU-06",
        "qty": 1,
        "status": "pending",
        "process_name": "CNC",
    })
    assert part["actionLabel"] == "原料发货"
    assert operation["actionLabel"] == "备料完成"
    assert part["nextAction"]["action"] == "ship"
    assert operation["nextAction"]["action"] == "prepare"
    assert warehouse_todo.needs_weight({
        "outsourceType": "operation",
        "processName": "真空热处理",
    })


def test_warehouse_query_accepts_order_and_part_filters(monkeypatch):
    parsed = erp_outsource_warehouse_tools.parse(erp_outsource_warehouse_tools.TODO_TOOL, {
        "orderNo": "EO-260924-A5SS",
        "mold": "M260063",
        "batch": "M260063-P4",
        "partNo": "PU-03",
        "question": "确认备料",
    })
    assert parsed.order_no == "EO-260924-A5SS"
    assert parsed.batch == "M260063-P4"
    assert parsed.part == "PU-03"

    captured = {}

    def fake_query_items(**kwargs):
        captured.update(kwargs)
        return [{
            **TASK,
            "orderNo": "EO-260924-A5SS",
            "moldNo": "M260063-P4",
            "moldFamily": "M260063",
            "moldBatch": "M260063-P4",
            "partNo": "PU-03",
            "partName": "冲头",
            "outsourceType": "operation",
            "outsourceTypeLabel": "工序委外",
            "actionLabel": "备料完成",
        }]

    monkeypatch.setattr(warehouse_todo, "query_items", fake_query_items)
    result = erp_outsource_warehouse_tools.execute_query(None, Admin(), {
        "question": "确认备料",
        "order_no": "EO-260924-A5SS",
        "mold": "M260063",
        "batch": "M260063-P4",
        "part": "PU-03",
    })
    assert captured["mold_batch"] == "M260063-P4"
    assert result["data"]["items"][0]["partNo"] == "PU-03"
    assert result["model_context"]["item_count"] == 1
    assert "项目号" in result["model_context"]["summary"]


def test_warehouse_permission_includes_supply_tools(monkeypatch):
    class Grant:
        effect = "ALLOW"

    class User:
        super_admin = False
        id = "warehouse"

    def grants(db, user, permission):
        del db, user
        if permission in {"erp_outsource_warehouse.read", "erp_outsource_warehouse.execute"}:
            return [Grant()]
        return []

    monkeypatch.setattr(tool_gateway, "assigned", lambda *args, **kwargs: False)
    monkeypatch.setattr(tool_gateway, "grants_for", grants)
    allowed = set(tool_gateway.available_tools(None, User()))
    assert "query_erp_outsource_warehouse_tasks" in allowed
    assert "prepare_erp_outsource_warehouse_ship" in allowed
    assert "query_erp_outsource_warehouse_inbound" in allowed
    assert "query_erp_outsource_followup_board" not in allowed
    skills = {item["key"] for item in tool_gateway.skill_context(None, User())}
    assert {"outsource_warehouse_ops", "outsource_warehouse_inbound"} <= skills


def test_present_counts_orders_not_part_lines():
    first = {
        "action": "ship",
        "actionLabel": "原料发货",
        "outsourceType": "part",
        "outsourceTypeLabel": "零件委外",
        "orderNo": "EO-260928-WE11",
        "moldFamily": "M260063",
        "moldBatch": "M260063-P1",
        "moldNo": "M260063-P1",
        "partNo": "UP-01",
        "partName": "上模座",
        "qty": 1,
        "sourceType": "material_stock",
        "sourceTypeLabel": "物料库",
        "processorName": "青岛和兴嘉业金属制品有限公司",
        "status": "pending",
    }
    second = {**first, "partNo": "U2-03", "partName": "防护垫脚", "qty": 6}
    payload = warehouse_todo.present([first, second])
    assert payload["orderCount"] == 1
    assert payload["lineCount"] == 2
    assert payload["orders"][0]["lineCount"] == 2
    assert payload["orders"][0]["qty"] == 7
    assert "UP-01 上模座" in payload["orders"][0]["partDetails"]
    assert "1 单，2 个零件明细" in payload["summary"]


INBOUND_SHIPMENT = {
    "action": "inbound",
    "actionLabel": "仓储入库",
    "station": "待入库",
    "stationLabel": "待入库",
    "shipmentId": 9,
    "shipmentNo": "PS-5136-441118",
    "orderNo": "EO-260928-WE11",
    "moldNo": "M260063-P5",
    "outsourceTypeLabel": "零件委外",
    "supplierName": "青岛和兴嘉业金属制品有限公司",
    "pendingArrivalQty": 0,
    "pendingInboundQty": 19,
    "inboundTargets": ["成品库"],
    "lines": [{"partNo": "B1-01", "partName": "下托板", "pendingInboundQty": 1}],
}


def test_generic_warehouse_todo_includes_inbound(monkeypatch):
    monkeypatch.setattr(warehouse_todo, "query_items", lambda **kwargs: [])
    monkeypatch.setattr(warehouse_inbound, "query_items", lambda parsed: [dict(INBOUND_SHIPMENT)])
    result = erp_outsource_warehouse_tools.execute_query(None, Admin(), {"question": "查询待办"})
    assert result["data"]["inboundCount"] == 1
    assert result["data"]["inboundItems"][0]["shipmentNo"] == "PS-5136-441118"
    assert result["model_context"]["item_count"] == 1
    assert result["model_context"]["inbound_count"] == 1
    assert "回厂收货入库" in result["model_context"]["summary"]
    assert "不要说没有待办" in result["model_context"]["summary"]


def test_supply_only_question_skips_inbound(monkeypatch):
    called = {"inbound": False}

    def fake_inbound(parsed):
        called["inbound"] = True
        return [dict(INBOUND_SHIPMENT)]

    monkeypatch.setattr(warehouse_todo, "query_items", lambda **kwargs: [])
    monkeypatch.setattr(warehouse_inbound, "query_items", fake_inbound)
    result = erp_outsource_warehouse_tools.execute_query(None, Admin(), {"question": "待发料有几个"})
    assert called["inbound"] is False
    assert "inboundItems" not in result["data"]
    assert result["model_context"]["item_count"] == 0
    assert "回厂收货入库" not in result["model_context"]["summary"]


def test_spoken_ship_arguments_lock_order_identity():
    prompt = "订单 EO-260924-A5SS，模具 M260063 批次 M260063-P4，零件 PU-03 冲头，确认备料"
    arguments = erp_outsource_warehouse_tools.spoken_ship_arguments(prompt)
    assert arguments == {
        "order_no": "EO-260924-A5SS",
        "mold": "M260063",
        "batch": "M260063-P4",
    }
    assert erp_outsource_warehouse_tools.spoken_ship_arguments("备料完成的有哪些") is None


def test_repeat_ship_explains_that_material_already_left(monkeypatch):
    monkeypatch.setattr(warehouse_todo, "find_pending_by_identity", lambda **kwargs: [])
    monkeypatch.setattr(warehouse_todo, "supply_status_counts", lambda order_no: {"shipped": 19})
    data = erp_outsource_warehouse_tools.parse(
        erp_outsource_warehouse_tools.SHIP_TOOL,
        {"order_no": "EO-260928-WE11"},
    )
    try:
        erp_outsource_warehouse_tools.preview(erp_outsource_warehouse_tools.SHIP_TOOL, data)
    except DomainError as error:
        assert error.code == "STATE_BLOCKED"
        assert "已经发出" in error.message
    else:
        raise AssertionError("expected STATE_BLOCKED")
