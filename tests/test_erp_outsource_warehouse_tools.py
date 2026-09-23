from domain_packs.mold import proposal_handlers, tool_gateway
from domain_packs.mold.ports.errors import DomainError
from domain_packs.mold.tools.erp.procurement import erp_outsource_warehouse_tools
from domain_packs.mold.tools.erp.procurement.outsource_queries import warehouse_todo


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
    monkeypatch.setattr(warehouse_todo, "find_tasks", lambda task_ids: [dict(TASK, taskId=task_ids[0])])
    result = erp_outsource_warehouse_tools.execute_tool(None, Admin(), erp_outsource_warehouse_tools.SHIP_TOOL, {
        "task_ids": [7],
    })
    assert result["proposal"]["kind"] == "erp_outsource_warehouse_ship"
    assert result["proposal"]["display"]["操作"] == "原料发货"
    assert result["proposal"]["display"]["模具号"] == "M260063-P1"


def test_warehouse_operation_card_is_prepare(monkeypatch):
    monkeypatch.setattr(warehouse_todo, "find_tasks", lambda task_ids: [{
        **TASK,
        "outsourceType": "operation",
        "outsourceTypeLabel": "工序委外",
        "nextHint": "确认备料完成后 ERP 视为发货=收货，加工商不用再确认来料，可直接成品发货。",
    }])
    result = erp_outsource_warehouse_tools.execute_tool(None, Admin(), erp_outsource_warehouse_tools.SHIP_TOOL, {
        "task_ids": [7],
    })
    assert result["proposal"]["display"]["操作"] == "备料完成"
    assert "不用再确认" in result["proposal"]["display"]["说明"]


def test_heat_treatment_requires_weight(monkeypatch):
    monkeypatch.setattr(warehouse_todo, "find_tasks", lambda task_ids: [{
        **TASK,
        "outsourceType": "operation",
        "processName": "热处理",
        "needsWeight": True,
    }])
    try:
        erp_outsource_warehouse_tools.preview(
            erp_outsource_warehouse_tools.SHIP_TOOL,
            erp_outsource_warehouse_tools.parse(erp_outsource_warehouse_tools.SHIP_TOOL, {"task_ids": [7]}),
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
