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
    assert inspecting["actionLabel"] == "提交合格"
    assert pending["inboundTargetLabel"] == "成品库"
    assert pending["nextAction"]["action"] == "claim"
    assert inspecting["nextAction"]["action"] == "pass"
