from domain_packs.mold import proposal_handlers, tool_gateway
from domain_packs.mold.ports.errors import DomainError
from domain_packs.mold.tools.erp.procurement import erp_outsource_quality_tools
from domain_packs.mold.tools.erp.procurement.outsource_queries import quality_todo


class Admin:
    super_admin = True
    id = "admin"
    security_version = 1


TASK = {
    "action": "claim",
    "actionLabel": "领取质检",
    "taskId": 15,
    "inspectionNo": "QC202609230001",
    "inboundNo": "RK-1",
    "orderNo": "EO-3",
    "partnerName": "铂锐",
    "warehouse": "成品仓库",
    "inboundTargetLabel": "成品库",
    "status": "pending",
    "statusLabel": "待领取",
    "details": [{"inboundDetailId": 8, "partNo": "PU-06", "inboundQty": 1}],
}


def test_quality_skill_is_registered():
    paths = tool_gateway.skill_paths()
    assert "outsource_quality_ops" in tool_gateway.SKILLS
    assert paths["outsource_quality_ops"]["path"].is_file()
    assert tool_gateway.TOOLS[erp_outsource_quality_tools.TODO_TOOL]["permission"] == "erp_outsource_quality.read"
    assert tool_gateway.TOOLS[erp_outsource_quality_tools.CLAIM_TOOL]["permission"] == "erp_outsource_quality.execute"
    assert proposal_handlers.handler_for_tool(erp_outsource_quality_tools.CLAIM_TOOL).action == "erp_outsource_quality.execute"
    assert proposal_handlers.handler_for_tool(erp_outsource_quality_tools.PASS_TOOL).action == "erp_outsource_quality.execute"


def test_spoken_claim_locks_task_from_context(monkeypatch):
    monkeypatch.setattr(quality_todo, "find_pending_by_identity", lambda **kwargs: [dict(TASK)])
    name, arguments = erp_outsource_quality_tools.spoken_quality_invoke(
        "领取质检任务",
        "已经查到待领取 EO-260928-WE11。质检单 QC202609290001。",
        {
            erp_outsource_quality_tools.CLAIM_TOOL,
            erp_outsource_quality_tools.PASS_TOOL,
        },
    )
    assert name == erp_outsource_quality_tools.CLAIM_TOOL
    assert arguments["task_id"] == 15
    assert arguments["order_no"] == "EO-260928-WE11"
    assert arguments["inspection_no"] == "QC202609290001"


def test_spoken_claim_accepts_this_task_and_board_row(monkeypatch):
    monkeypatch.setattr(quality_todo, "query_items", lambda parsed: [dict(TASK)])
    monkeypatch.setattr(quality_todo, "find_pending_by_identity", lambda **kwargs: [dict(TASK)])
    this_name, this_arguments = erp_outsource_quality_tools.spoken_quality_invoke(
        "领取这个质检任务",
        "已经查到待领取 EO-260928-WE11。质检单 QC202609290001。",
        {erp_outsource_quality_tools.CLAIM_TOOL},
    )
    assert this_name == erp_outsource_quality_tools.CLAIM_TOOL
    assert this_arguments["task_id"] == 15
    row_name, row_arguments = erp_outsource_quality_tools.spoken_quality_invoke(
        "领取NO.1的任务",
        "",
        {erp_outsource_quality_tools.CLAIM_TOOL},
    )
    assert row_name == erp_outsource_quality_tools.CLAIM_TOOL
    assert row_arguments["board_row"] == 1
    assert row_arguments["task_id"] == 15


def test_claim_accepts_order_no_as_task_id(monkeypatch):
    monkeypatch.setattr(quality_todo, "find_pending_by_identity", lambda **kwargs: [dict(TASK)])
    result = erp_outsource_quality_tools.execute_tool(
        None,
        Admin(),
        erp_outsource_quality_tools.CLAIM_TOOL,
        {"task_id": "EO-260928-WE11"},
    )
    assert result["proposal"]["kind"] == "erp_outsource_quality_claim"
    assert result["proposal"]["input"]["task_id"] == 15
    assert result["proposal"]["input"]["order_no"] == "EO-260928-WE11"


def test_spoken_pass_uses_pass_tool(monkeypatch):
    monkeypatch.setattr(quality_todo, "find_pending_by_identity", lambda **kwargs: [dict(TASK)])
    name, arguments = erp_outsource_quality_tools.spoken_quality_invoke(
        "提交合格",
        "质检单 QC202609230001 订单 EO-3",
        {
            erp_outsource_quality_tools.CLAIM_TOOL,
            erp_outsource_quality_tools.PASS_TOOL,
        },
    )
    assert name == erp_outsource_quality_tools.PASS_TOOL
    assert arguments["task_id"] == 15


def test_spoken_pass_accepts_submit_quality_qualified(monkeypatch):
    inspecting = {**TASK, "action": "pass", "status": "inspecting", "statusLabel": "质检中"}
    monkeypatch.setattr(quality_todo, "find_pending_by_identity", lambda **kwargs: [dict(inspecting)])
    name, arguments = erp_outsource_quality_tools.spoken_quality_invoke(
        "提交质检合格",
        "已经查到质检中 EO-260928-WE11。质检单 QC202609290001。",
        {
            erp_outsource_quality_tools.CLAIM_TOOL,
            erp_outsource_quality_tools.PASS_TOOL,
        },
    )
    assert name == erp_outsource_quality_tools.PASS_TOOL
    assert arguments["task_id"] == 15
    assert arguments["inspection_no"] == "QC202609290001"
    assert arguments["order_no"] == "EO-260928-WE11"
    assert erp_outsource_quality_tools.spoken_quality_arguments("质检合格") is None


def test_spoken_pass_prefers_inspecting_row(monkeypatch):
    pending = dict(TASK)
    inspecting = {
        **TASK, "taskId": 16, "action": "pass", "status": "inspecting",
        "inspectionNo": "QC202609290001",
    }
    monkeypatch.setattr(quality_todo, "find_pending_by_identity", lambda **kwargs: [pending, inspecting])
    name, arguments = erp_outsource_quality_tools.spoken_quality_invoke(
        "提交质检合格",
        "质检单 QC202609290001 订单 EO-3",
        {
            erp_outsource_quality_tools.CLAIM_TOOL,
            erp_outsource_quality_tools.PASS_TOOL,
        },
    )
    assert name == erp_outsource_quality_tools.PASS_TOOL
    assert arguments["task_id"] == 16


def test_pass_falls_back_when_task_id_misses(monkeypatch):
    inspecting = {
        **TASK, "action": "pass", "status": "inspecting", "statusLabel": "质检中",
    }
    monkeypatch.setattr(quality_todo, "find_task", lambda task_id: None)
    monkeypatch.setattr(quality_todo, "find_pending_by_identity", lambda **kwargs: [dict(inspecting)])
    result = erp_outsource_quality_tools.execute_tool(
        None,
        Admin(),
        erp_outsource_quality_tools.PASS_TOOL,
        {"task_id": 202609290001, "inspection_no": "QC202609290001"},
    )
    assert result["proposal"]["kind"] == "erp_outsource_quality_pass"
    assert result["proposal"]["input"]["task_id"] == 15


def test_claim_prepare_returns_card(monkeypatch):
    monkeypatch.setattr(quality_todo, "find_task", lambda task_id: dict(TASK, taskId=task_id))
    result = erp_outsource_quality_tools.execute_tool(
        None, Admin(), erp_outsource_quality_tools.CLAIM_TOOL, {"task_id": 15},
    )
    assert result["proposal"]["kind"] == "erp_outsource_quality_claim"
    assert result["proposal"]["display"]["操作"] == "领取质检"


def test_pass_prepare_returns_card(monkeypatch):
    monkeypatch.setattr(quality_todo, "find_task", lambda task_id: {
        **TASK, "action": "pass", "status": "inspecting", "statusLabel": "质检中",
    })
    result = erp_outsource_quality_tools.execute_tool(
        None, Admin(), erp_outsource_quality_tools.PASS_TOOL, {"task_id": 15},
    )
    assert result["proposal"]["kind"] == "erp_outsource_quality_pass"
    assert result["proposal"]["display"]["操作"] == "提交全检合格"


def test_claim_blocks_already_inspecting(monkeypatch):
    monkeypatch.setattr(quality_todo, "find_task", lambda task_id: {**TASK, "status": "inspecting"})
    try:
        erp_outsource_quality_tools.preview(
            None,
            Admin(),
            erp_outsource_quality_tools.CLAIM_TOOL,
            erp_outsource_quality_tools.parse(erp_outsource_quality_tools.CLAIM_TOOL, {"task_id": 15}),
        )
    except DomainError as error:
        assert error.code == "STATE_BLOCKED"
    else:
        raise AssertionError("expected STATE_BLOCKED")


def test_pass_confirm_sends_line_quantities(monkeypatch):
    inspecting = {
        **TASK, "action": "pass", "status": "inspecting", "statusLabel": "质检中",
        "details": [
            {"detailId": 774, "inboundDetailId": 696, "partNo": "B1-01", "inboundQty": 1},
            {"detailId": 775, "inboundDetailId": 697, "partNo": "B2-01", "inboundQty": 2},
        ],
    }
    captured = {}
    monkeypatch.setattr(quality_todo, "find_task", lambda task_id: dict(inspecting, taskId=task_id))
    monkeypatch.setattr(
        erp_outsource_quality_tools,
        "validate_intent",
        lambda db, user, payload: (
            {},
            erp_outsource_quality_tools.parse(erp_outsource_quality_tools.PASS_TOOL, {"task_id": 15}),
            erp_outsource_quality_tools.PASS_TOOL,
        ),
    )

    def fake_put(db, user, path, body, **kwargs):
        captured["path"] = path
        captured["body"] = body
        captured["kwargs"] = kwargs
        return {"code": 200}

    monkeypatch.setattr(erp_outsource_quality_tools, "put_erp", fake_put)
    result = erp_outsource_quality_tools.confirm(None, Admin(), {"_intent_id": "intent-1"})
    assert result["action"] == "quality_pass"
    assert captured["path"] == "quality/inspection/15/submit"
    assert captured["body"]["inspectionType"] == "full"
    assert captured["body"]["result"] == "qualified"
    assert captured["body"]["details"][0] == {
        "id": 774,
        "inboundDetailId": 696,
        "qualifiedQty": 1,
        "unqualifiedQty": 0,
        "sampleQty": 1,
        "result": "qualified",
        "handlingAction": "inbound",
    }
    assert captured["body"]["details"][1]["qualifiedQty"] == 2
    assert "remark" not in captured["body"]


def test_pass_payload_requires_details():
    try:
        erp_outsource_quality_tools._pass_payload({**TASK, "details": []}, None)
    except DomainError as error:
        assert error.code == "STATE_BLOCKED"
    else:
        raise AssertionError("expected STATE_BLOCKED")


def test_quality_item_labels():
    pending = quality_todo.item_from_row({
        "task_id": 1,
        "status": "pending",
        "inspection_no": "QC1",
        "processor_inbound_target": "finished",
        "details": [{"inboundDetailId": 8, "inboundQty": 1}],
    })
    inspecting = quality_todo.item_from_row({
        "task_id": 2,
        "status": "inspecting",
        "inspection_no": "QC2",
        "processor_inbound_target": "semi_finished",
        "details": [],
    })
    assert pending["actionLabel"] == "领取质检"
    assert pending["stationLabel"] == "待领取"
    assert inspecting["actionLabel"] == "提交合格"
    assert inspecting["stationLabel"] == "待检验"
    assert pending["inboundTargetLabel"] == "成品库"
    assert pending["nextAction"]["action"] == "claim"
    assert inspecting["nextAction"]["action"] == "pass"
