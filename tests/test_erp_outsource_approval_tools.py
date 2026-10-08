from domain_packs.mold import proposal_handlers, tool_gateway
from domain_packs.mold.ports.errors import DomainError
from domain_packs.mold.tools.erp.procurement import erp_outsource_approval_tools
from domain_packs.mold.tools.erp.procurement.outsource_queries import approval_todo


class Admin:
    super_admin = True
    id = "admin"
    security_version = 1


def test_approval_skill_is_registered():
    paths = tool_gateway.skill_paths()
    assert "outsource_approval_ops" in tool_gateway.SKILLS
    assert paths["outsource_approval_ops"]["path"].is_file()
    assert tool_gateway.TOOLS[erp_outsource_approval_tools.TODO_TOOL]["permission"] == "erp_outsource_approval.read"
    for key in erp_outsource_approval_tools.PREPARE_TOOL_KEYS:
        assert tool_gateway.TOOLS[key]["permission"] == "erp_outsource_approval.approve"
        assert proposal_handlers.handler_for_tool(key).action == "erp_outsource_approval.approve"


def test_approval_pass_prepare_returns_card(monkeypatch):
    monkeypatch.setattr(approval_todo, "find_task_by_identity", lambda **kwargs: {
        "taskId": 12,
        "nodeName": "采购主管审批",
        "moldNo": "M260063-P1",
        "moldFamily": "M260063",
        "moldBatch": "M260063-P1",
        "orderNo": "EO-1",
        "supplierName": "铂锐",
        "amount": 7888,
    })
    result = erp_outsource_approval_tools.execute_tool(None, Admin(), erp_outsource_approval_tools.PASS_TOOL, {
        "order_no": "EO-1",
        "mold": "M260063",
        "batch": "M260063-P1",
        "comment": "同意下单",
    })
    assert result["proposal"]["kind"] == "erp_outsource_approval_pass"
    assert result["proposal"]["display"]["当前节点"] == "采购主管审批"
    assert result["proposal"]["display"]["订单号"] == "EO-1"
    assert result["proposal"]["display"]["模具号"] == "M260063"
    assert result["proposal"]["display"]["批次号"] == "M260063-P1"


def test_approval_node_mismatch_is_forbidden(monkeypatch):
    monkeypatch.setattr(approval_todo, "find_task_by_identity", lambda **kwargs: {
        "taskId": 12,
        "nodeName": "总经理审批",
        "moldNo": "M260063-P1",
        "orderNo": "EO-1",
        "supplierName": "铂锐",
        "amount": 7888,
    })
    monkeypatch.setattr(
        erp_outsource_approval_tools,
        "approval_node_tokens",
        lambda db, user: ["采购主管"],
    )
    try:
        erp_outsource_approval_tools.preview(
            None,
            Admin(),
            erp_outsource_approval_tools.PASS_TOOL,
            erp_outsource_approval_tools.parse(erp_outsource_approval_tools.PASS_TOOL, {"order_no": "EO-1"}),
        )
    except DomainError as error:
        assert error.code == "FORBIDDEN"
    else:
        raise AssertionError("expected FORBIDDEN")


def test_approval_present_empty_board():
    payload = approval_todo.present([], node_tokens=["采购主管"])
    assert payload["items"] == []
    assert "0 条" in payload["summary"]


def test_approval_without_mapped_role_is_fail_closed(monkeypatch):
    monkeypatch.setattr(approval_todo, "fetch_all", lambda sql, params: (_ for _ in ()).throw(
        AssertionError("empty node scope must not query all approval tasks")
    ))
    assert approval_todo.query_items(node_tokens=[]) == []
    payload = approval_todo.present([], node_tokens=[])
    assert payload["nodeLens"] == []
    assert "未分配审批节点" in payload["summary"]


def test_approval_empty_node_scope_cannot_prepare(monkeypatch):
    monkeypatch.setattr(approval_todo, "find_task_by_identity", lambda **kwargs: {
        "taskId": 12,
        "nodeName": "采购主管审批",
        "orderNo": "EO-1",
    })
    monkeypatch.setattr(erp_outsource_approval_tools, "approval_node_tokens", lambda db, user: [])
    try:
        erp_outsource_approval_tools.preview(
            None,
            Admin(),
            erp_outsource_approval_tools.PASS_TOOL,
            erp_outsource_approval_tools.parse(erp_outsource_approval_tools.PASS_TOOL, {"order_no": "EO-1"}),
        )
    except DomainError as error:
        assert error.code == "FORBIDDEN"
    else:
        raise AssertionError("expected FORBIDDEN")


def test_approval_pass_accepts_order_no_alias():
    data = erp_outsource_approval_tools.parse(erp_outsource_approval_tools.PASS_TOOL, {
        "orderNo": "EO-260922-2RHX",
        "comment": "同意",
    })
    assert data.order_no == "EO-260922-2RHX"


def test_spoken_view_todos_does_not_prepare():
    assert erp_outsource_approval_tools.spoken_approval_arguments("查看待办") is None
    assert erp_outsource_approval_tools.spoken_approval_invoke(
        "查看待办",
        "",
        set(erp_outsource_approval_tools.PREPARE_TOOL_KEYS),
    ) is None


def test_spoken_pass_locks_order_from_context():
    name, arguments = erp_outsource_approval_tools.spoken_approval_invoke(
        "通过这单",
        "委外下单审批待办（采购主管）共 1 条。\n- 采购主管审批 M260063-P2 EO-260930-R77L 青岛和兴嘉业金属制品有限公司",
        set(erp_outsource_approval_tools.PREPARE_TOOL_KEYS),
    )
    assert name == erp_outsource_approval_tools.PASS_TOOL
    assert arguments["order_no"] == "EO-260930-R77L"
    assert arguments["comment"] == "同意"
    assert arguments["mold"] == "M260063"
    assert arguments["batch"] == "M260063-P2"


def test_spoken_pass_accepts_board_row_and_bare_pass():
    row_name, row_arguments = erp_outsource_approval_tools.spoken_approval_invoke(
        "NO.1通过",
        "",
        set(erp_outsource_approval_tools.PREPARE_TOOL_KEYS),
    )
    assert row_name == erp_outsource_approval_tools.PASS_TOOL
    assert row_arguments["board_row"] == 1
    name, arguments = erp_outsource_approval_tools.spoken_approval_invoke(
        "通过",
        "采购主管审批 EO-260930-R77L",
        set(erp_outsource_approval_tools.PREPARE_TOOL_KEYS),
    )
    assert name == erp_outsource_approval_tools.PASS_TOOL
    assert arguments["order_no"] == "EO-260930-R77L"


def test_spoken_reject_defaults_comment():
    name, arguments = erp_outsource_approval_tools.spoken_approval_invoke(
        "驳回这单，价格超了",
        "订单 EO-260930-R77L",
        set(erp_outsource_approval_tools.PREPARE_TOOL_KEYS),
    )
    assert name == erp_outsource_approval_tools.REJECT_TOOL
    assert arguments["order_no"] == "EO-260930-R77L"
    assert "价格超了" in arguments["comment"]


def test_spoken_pass_from_unique_board_row():
    invoked = erp_outsource_approval_tools.spoken_approval_from_board(
        "通过这单",
        [{
            "station": "审批中",
            "orderNo": "EO-260930-R77L",
            "moldFamily": "M260063",
            "moldBatch": "M260063-P2",
        }],
        set(erp_outsource_approval_tools.PREPARE_TOOL_KEYS),
    )
    assert invoked is not None
    name, arguments = invoked
    assert name == erp_outsource_approval_tools.PASS_TOOL
    assert arguments["order_no"] == "EO-260930-R77L"


def test_approval_pass_prepare_by_board_row(monkeypatch):
    monkeypatch.setattr(approval_todo, "item_from_visible_row", lambda row_no, **kwargs: {
        "taskId": 12,
        "nodeName": "采购主管审批",
        "moldNo": "M260063-P2",
        "moldFamily": "M260063",
        "moldBatch": "M260063-P2",
        "orderNo": "EO-260930-R77L",
        "supplierName": "青岛和兴嘉业金属制品有限公司",
        "amount": 50000,
    })
    monkeypatch.setattr(erp_outsource_approval_tools, "approval_node_tokens", lambda db, user: ["采购主管"])
    result = erp_outsource_approval_tools.execute_tool(None, Admin(), erp_outsource_approval_tools.PASS_TOOL, {
        "board_row": 1,
    })
    assert result["proposal"]["kind"] == "erp_outsource_approval_pass"
    assert result["proposal"]["display"]["订单号"] == "EO-260930-R77L"


def test_approval_pass_unique_pending_without_order(monkeypatch):
    monkeypatch.setattr(approval_todo, "query_items", lambda **kwargs: [{
        "taskId": 12,
        "nodeName": "采购主管审批",
        "moldNo": "M260063-P2",
        "moldFamily": "M260063",
        "moldBatch": "M260063-P2",
        "orderNo": "EO-260930-R77L",
        "supplierName": "青岛和兴嘉业金属制品有限公司",
        "amount": 50000,
    }])
    monkeypatch.setattr(erp_outsource_approval_tools, "approval_node_tokens", lambda db, user: ["采购主管"])
    result = erp_outsource_approval_tools.execute_tool(None, Admin(), erp_outsource_approval_tools.PASS_TOOL, {
        "comment": "同意",
    })
    assert result["proposal"]["display"]["订单号"] == "EO-260930-R77L"


def test_approval_pass_ambiguous_without_identity(monkeypatch):
    monkeypatch.setattr(approval_todo, "query_items", lambda **kwargs: [
        {"taskId": 12, "nodeName": "采购主管审批", "orderNo": "EO-1"},
        {"taskId": 13, "nodeName": "采购主管审批", "orderNo": "EO-2"},
    ])
    monkeypatch.setattr(erp_outsource_approval_tools, "approval_node_tokens", lambda db, user: ["采购主管"])
    try:
        erp_outsource_approval_tools.execute_tool(None, Admin(), erp_outsource_approval_tools.PASS_TOOL, {})
    except DomainError as error:
        assert error.code == "AMBIGUOUS"
    else:
        raise AssertionError("expected AMBIGUOUS")


def test_approval_item_fills_mold_from_title():
    item = approval_todo.item_from_row({
        "task_id": 1,
        "title": "下单审批 EO-1 M260063-P2",
        "business_no": "EO-1",
        "order_no": "EO-1",
        "mold_no": None,
        "node_name": "总经理审批",
    })
    assert item["moldNo"] == "M260063-P2"
    assert item["moldFamily"] == "M260063"
    assert item["moldBatch"] == "M260063-P2"
    assert item["nextAction"]["orderNo"] == "EO-1"
