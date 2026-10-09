from types import SimpleNamespace

import pytest

from agent_core.confirmation_policy import proposal_run_is_open
from domain_packs.mold import proposal_handlers, tool_gateway
from domain_packs.mold.ports.errors import DomainError
from domain_packs.mold.tools.erp.procurement import erp_outsource_processor_tools
from domain_packs.mold.tools.erp.procurement import erp_outsource_processor_tools as tools
from domain_packs.mold.tools.erp.procurement.outsource_queries import buyer_todo, processor_reject_reason


class _Db:
    def __init__(self, step, run):
        self.step = step
        self.run = run

    def get(self, model, key):
        if key == self.step.id:
            return self.step
        if key == self.run.id:
            return self.run
        return None


class Admin:
    super_admin = True
    id = "admin"
    security_version = 1


QUOTE_ITEM = {
    "inquiryId": 9,
    "station": "supplier_quote",
    "stationLabel": "待报价",
    "outsourceType": "part",
    "outsourceTypeLabel": "零件委外",
    "moldNo": "M260063-P1",
    "partDetails": "PU-06 上垫板",
    "orderId": None,
    "orderNo": "",
    "invitations": [
        {"invitationId": 21, "supplierName": "铂锐", "supplierCode": "SUP000001", "status": "sent"},
    ],
}

QUOTE_INVITATION = QUOTE_ITEM["invitations"][0]


def test_proposal_run_is_open_keeps_failed_cards_confirmable():
    assert proposal_run_is_open(SimpleNamespace(status="FAILED"))
    assert proposal_run_is_open(SimpleNamespace(status="SUCCEEDED"))
    assert not proposal_run_is_open(SimpleNamespace(status="CANCELLED"))
    assert not proposal_run_is_open(None)


def test_processor_source_allows_failed_run_with_unused_proposal(monkeypatch):
    user = SimpleNamespace(id="u1", security_version=1)
    run = SimpleNamespace(
        id="r1",
        user_id="u1",
        status="FAILED",
        security_version=1,
        checkpoint={"authorization_hash": "h"},
    )
    step = SimpleNamespace(
        id="s1",
        run_id="r1",
        tool=tools.ACCEPT_TOOL,
        result={"proposal": {"kind": "erp_outsource_processor_accept"}},
    )
    monkeypatch.setattr(tools, "fingerprint", lambda db, current: "h")
    monkeypatch.setattr(
        "domain_packs.mold.tool_gateway.available_tools",
        lambda db, current: {tools.ACCEPT_TOOL},
    )

    proposal = tools.source(_Db(step, run), user, "s1")
    assert proposal["kind"] == "erp_outsource_processor_accept"

    run.status = "CANCELLED"
    with pytest.raises(DomainError) as error:
        tools.source(_Db(step, run), user, "s1")
    assert error.value.code == "PROPOSAL_STOPPED"


def test_processor_ops_skill_is_registered():
    paths = tool_gateway.skill_paths()
    assert "outsource_processor_ops" in tool_gateway.SKILLS
    assert paths["outsource_processor_ops"]["path"].is_file()
    for key in erp_outsource_processor_tools.TOOL_KEYS:
        assert key in tool_gateway.TOOLS
        assert tool_gateway.TOOLS[key]["permission"] == "erp_outsource_processor.execute"
        assert proposal_handlers.handler_for_tool(key).action == "erp_outsource_processor.execute"


def test_processor_quote_prepare_returns_confirmation_card(monkeypatch):
    monkeypatch.setattr(buyer_todo, "find_invitation", lambda invitation_id, mold=None: (QUOTE_ITEM, QUOTE_INVITATION))
    result = erp_outsource_processor_tools.execute_tool(None, Admin(), erp_outsource_processor_tools.QUOTE_TOOL, {
        "invitation_id": 21,
        "unit_price": 330,
        "delivery_date": "2026-10-01",
        "tax_included": True,
    })
    assert result["proposal"]["kind"] == "erp_outsource_processor_quote"
    assert result["proposal"]["display"]["报价金额"] == 330
    assert result["proposal"]["display"]["模具号"] == "M260063"
    assert result["proposal"]["display"]["批次号"] == "M260063-P1"


def test_processor_quote_requires_quote_station(monkeypatch):
    item = dict(QUOTE_ITEM, station="place_order", stationLabel="待填成交价")
    monkeypatch.setattr(buyer_todo, "find_invitation", lambda invitation_id, mold=None: (item, QUOTE_INVITATION))
    try:
        erp_outsource_processor_tools.execute_tool(None, Admin(), erp_outsource_processor_tools.QUOTE_TOOL, {
            "invitation_id": 21,
            "unit_price": 330,
            "delivery_date": "2026-10-01",
            "tax_included": True,
        })
    except DomainError as error:
        assert error.code == "STATE_BLOCKED"
    else:
        raise AssertionError("expected STATE_BLOCKED")


def test_operation_order_cannot_prepare_processor_quote(monkeypatch):
    item = dict(QUOTE_ITEM, outsourceType="operation", outsourceTypeLabel="工序委外")
    monkeypatch.setattr(buyer_todo, "find_invitation", lambda invitation_id, mold=None: (item, QUOTE_INVITATION))
    try:
        erp_outsource_processor_tools.preview(
            None,
            Admin(),
            erp_outsource_processor_tools.QUOTE_TOOL,
            erp_outsource_processor_tools.parse(erp_outsource_processor_tools.QUOTE_TOOL, {
                "invitation_id": 21,
                "unit_price": 1,
                "delivery_date": "2026-10-01",
                "tax_included": True,
            }),
        )
    except DomainError as error:
        assert error.code == "STATE_BLOCKED"
    else:
        raise AssertionError("expected STATE_BLOCKED")


def test_clip_accept_erp_drops_stale_reject_remark():
    clipped = tools.clip_accept_erp({
        "status": "accepted",
        "orderNo": "EO-260930-R77L",
        "rejectReason": "产能不足，无法接单",
        "reject_reason_name": "产能不足，无法接单",
        "reasonCode": "custom_dd9603ae4e5146b0ac4131557013f422",
        "remark": "产能不足，无法接单",
        "message": "已确认接单",
        "data": {
            "note": "无法接单",
            "stage": "pending_accept",
        },
    })
    assert clipped["status"] == "accepted"
    assert clipped["orderNo"] == "EO-260930-R77L"
    assert clipped["message"] == "已确认接单"
    assert "rejectReason" not in clipped
    assert "reject_reason_name" not in clipped
    assert "reasonCode" not in clipped
    assert "remark" not in clipped
    assert "note" not in clipped["data"]
    assert clipped["data"]["stage"] == "pending_accept"


def test_processor_accept_prepare_from_board_row(monkeypatch):
    monkeypatch.setattr(erp_outsource_processor_tools, "_item_from_processor_board", lambda tokens, row_no: {
        "orderId": 5166,
        "orderNo": "EO-261009-L6ON",
        "station": "accept",
        "stationLabel": "待接单",
        "outsourceType": "operation",
        "outsourceTypeLabel": "工序委外",
        "moldNo": "M260063-P1",
        "moldFamily": "M260063",
        "moldBatch": "M260063-P1",
        "partDetails": "DIE-41 下模板",
    })
    result = erp_outsource_processor_tools.execute_tool(None, Admin(), erp_outsource_processor_tools.ACCEPT_TOOL, {
        "board_row": 1,
    })
    assert result["proposal"]["input"]["order_no"] == "EO-261009-L6ON"
    assert result["proposal"]["display"]["订单号"] == "EO-261009-L6ON"


def test_processor_accept_prepare_returns_card(monkeypatch):
    monkeypatch.setattr(buyer_todo, "find_item_by_identity", lambda **kwargs: {
        "orderId": 44,
        "orderNo": kwargs.get("order_no") or "EO-1",
        "station": "accept",
        "stationLabel": "待接单",
        "outsourceType": "part",
        "outsourceTypeLabel": "零件委外",
        "moldNo": "M260063-P1",
        "moldFamily": "M260063",
        "moldBatch": "M260063-P1",
        "partDetails": "PU-06 上垫板",
        "supplierName": "铂锐",
    })
    result = erp_outsource_processor_tools.execute_tool(None, Admin(), erp_outsource_processor_tools.ACCEPT_TOOL, {
        "order_no": "EO-1",
        "mold": "M260063",
        "batch": "M260063-P1",
    })
    assert result["proposal"]["kind"] == "erp_outsource_processor_accept"
    assert result["proposal"]["display"]["订单号"] == "EO-1"
    assert result["proposal"]["display"]["模具号"] == "M260063"
    assert result["proposal"]["display"]["批次号"] == "M260063-P1"
    assert "工单ID" not in result["proposal"]["display"]


def test_spoken_accept_locks_order_no():
    locked = erp_outsource_processor_tools.spoken_accept_arguments(
        "订单 EO-260924-A5SS，模具 M260063 批次 M260063-P4，零件 PU-03 冲头，我接了",
    )
    assert locked["order_no"] == "EO-260924-A5SS"
    assert locked["mold"] == "M260063"
    assert locked["batch"] == "M260063-P4"
    assert erp_outsource_processor_tools.spoken_accept_arguments("待接单有几个") is None
    assert erp_outsource_processor_tools.spoken_accept_arguments(
        "订单 EO-260924-A5SS 这单我拒了",
    ) is None
    assert erp_outsource_processor_tools.spoken_accept_arguments("NO.1接单") == {"board_row": 1}
    assert erp_outsource_processor_tools.spoken_accept_arguments("全部接单") == {"accept_all": True}
    scoped = erp_outsource_processor_tools.spoken_accept_arguments("M260063-P1接单")
    assert scoped["accept_all"] is True
    assert scoped["batch"] == "M260063-P1"


def test_processor_reject_operation_card_mentions_auto_next(monkeypatch):
    monkeypatch.setattr(processor_reject_reason, "list_active", lambda: [
        {"reason_code": "CAPACITY", "reason_label": "产能不足，无法接单"},
    ])
    monkeypatch.setattr(buyer_todo, "find_item_by_identity", lambda **kwargs: {
        "orderId": 9,
        "orderNo": kwargs.get("order_no") or "EO-9",
        "station": "accept",
        "stationLabel": "待接单",
        "outsourceType": "operation",
        "outsourceTypeLabel": "工序委外",
        "moldNo": "M260063-P1",
        "moldFamily": "M260063",
        "moldBatch": "M260063-P1",
        "partDetails": "CNC",
        "supplierName": "铂锐",
    })
    result = erp_outsource_processor_tools.execute_tool(None, Admin(), erp_outsource_processor_tools.REJECT_TOOL, {
        "order_no": "EO-9",
        "reason_code": "CAPACITY",
    })
    assert result["proposal"]["kind"] == "erp_outsource_processor_reject"
    assert result["proposal"]["display"]["拒单原因"] == "产能不足，无法接单"
    assert result["proposal"]["input"]["reason_code"] == "CAPACITY"
    assert "自动转下一家" in result["proposal"]["display"]["说明"]


def test_processor_reject_maps_disabled_label_to_only_active_reason(monkeypatch):
    monkeypatch.setattr(processor_reject_reason, "list_active", lambda: [
        {"reason_code": "custom_dd9603ae4e5146b0ac4131557013f422", "reason_label": "产能不足，无法接单"},
    ])
    monkeypatch.setattr(buyer_todo, "find_item_by_identity", lambda **kwargs: {
        "orderId": 9,
        "orderNo": "EO-260930-R77L",
        "station": "accept",
        "stationLabel": "待接单",
        "outsourceType": "part",
        "outsourceTypeLabel": "零件委外",
        "moldNo": "M260063-P2",
        "moldFamily": "M260063",
        "moldBatch": "M260063-P2",
        "partDetails": "B1-01",
        "supplierName": "铂锐",
    })
    result = erp_outsource_processor_tools.execute_tool(None, Admin(), erp_outsource_processor_tools.REJECT_TOOL, {
        "order_no": "EO-260930-R77L",
        "reason_code": "报价过高",
    })
    assert result["proposal"]["input"]["reason_code"] == "custom_dd9603ae4e5146b0ac4131557013f422"
    assert result["proposal"]["display"]["拒单原因"] == "产能不足，无法接单"


def test_spoken_reject_locks_order_and_omits_dead_reason():
    locked = erp_outsource_processor_tools.spoken_reject_arguments(
        "拒单",
        "待接单 EO-260930-R77L 模具 M260063 批次 M260063-P2",
    )
    assert locked["order_no"] == "EO-260930-R77L"
    assert locked["mold"] == "M260063"
    assert "reason_code" not in locked
    assert erp_outsource_processor_tools.spoken_reject_arguments("待接单有几个") is None
    assert erp_outsource_processor_tools.spoken_accept_arguments("拒单") is None


def test_processor_reject_confirm_posts_reason_in_body(monkeypatch):
    monkeypatch.setattr(processor_reject_reason, "list_active", lambda: [
        {"reason_code": "custom_dd9603ae4e5146b0ac4131557013f422", "reason_label": "产能不足，无法接单"},
    ])
    monkeypatch.setattr(processor_reject_reason, "list_from_erp", lambda db, user: [])
    monkeypatch.setattr(buyer_todo, "find_item_by_identity", lambda **kwargs: {
        "orderId": 9,
        "orderNo": "EO-260930-R77L",
        "station": "accept",
        "stationLabel": "待接单",
        "outsourceType": "part",
        "moldFamily": "M260063",
        "moldBatch": "M260063-P2",
        "partDetails": "B1-01",
    })
    captured = {}

    def fake_post(db, user, path, body=None, params=None, **kwargs):
        captured["path"] = path
        captured["body"] = body
        captured["params"] = params
        return {"ok": True}

    monkeypatch.setattr(erp_outsource_processor_tools, "post_erp", fake_post)
    monkeypatch.setattr(erp_outsource_processor_tools, "validate_intent", lambda db, user, payload: (
        {},
        erp_outsource_processor_tools.REJECT_TOOL,
        erp_outsource_processor_tools.parse(erp_outsource_processor_tools.REJECT_TOOL, {
            "order_no": "EO-260930-R77L",
            "reason_code": "报价过高",
        }),
    ))
    result = erp_outsource_processor_tools.confirm(None, Admin(), {"_intent_id": "intent-1"})
    assert result["status"] == "CONFIRMED"
    assert captured["path"].endswith("/reject")
    assert captured["body"]["reasonCode"] == "custom_dd9603ae4e5146b0ac4131557013f422"
    assert captured["params"]["reasonCode"] == "custom_dd9603ae4e5146b0ac4131557013f422"


def test_resolve_reject_reason_requires_active_catalog(monkeypatch):
    monkeypatch.setattr(processor_reject_reason, "list_active", lambda: [])
    try:
        processor_reject_reason.resolve("报价过高")
    except DomainError as error:
        assert error.code == "STATE_BLOCKED"
    else:
        raise AssertionError("expected STATE_BLOCKED")


def test_processor_cannot_quote_other_supplier(monkeypatch):
    monkeypatch.setattr(buyer_todo, "find_invitation", lambda invitation_id, mold=None: (QUOTE_ITEM, QUOTE_INVITATION))
    monkeypatch.setattr(erp_outsource_processor_tools, "supplier_codes_for", lambda db, user: ["精工"])
    try:
        erp_outsource_processor_tools.preview(
            None,
            Admin(),
            erp_outsource_processor_tools.QUOTE_TOOL,
            erp_outsource_processor_tools.parse(erp_outsource_processor_tools.QUOTE_TOOL, {
                "invitation_id": 21,
                "unit_price": 330,
                "delivery_date": "2026-10-01",
                "tax_included": True,
            }),
        )
    except DomainError as error:
        assert error.code == "FORBIDDEN"
    else:
        raise AssertionError("expected FORBIDDEN")


def test_clip_for_processor_keeps_own_invitation_and_next_action():
    item = buyer_todo.item_from_row(
        {
            "outsource_type": "part",
            "mold_no": "M260063-P1",
            "our_quote_amount": 320,
            "auto_accept_max_amount": 400,
            "parts": [{"partNo": "PU-06", "partName": "上垫板", "qty": 1}],
            "invitations": [
                {"invitationId": 21, "supplierName": "铂锐", "supplierCode": "SUP000001", "status": "sent"},
                {"invitationId": 22, "supplierName": "精工", "supplierCode": "SUP000002", "status": "quoted", "quoteAmount": 410},
            ],
        },
        "supplier_quote",
    )
    clipped = buyer_todo.clip_for_processor(item, ["SUP000001"])
    assert [inv["invitationId"] for inv in clipped["invitations"]] == [21]
    assert clipped["ourQuoteAmount"] is None
    assert clipped["autoAcceptMaxAmount"] is None
    assert clipped["buyerQuoteAmount"] == 320
    assert clipped["finalDealAmount"] is None
    assert clipped["nextAction"]["action"] == "quote"
    assert "精工" not in (clipped["supplierQuotes"] or "")
    operation = buyer_todo.item_from_row(
        {
            "outsource_type": "operation",
            "mold_no": "M260063-P5",
            "our_quote_amount": 320,
            "auto_accept_max_amount": 400,
            "reference_total": 267,
            "parts": [{"partNo": "DIE-BL1", "processName": "CNC"}],
            "invitations": [
                {"invitationId": 31, "supplierName": "铂锐", "supplierCode": "SUP000001", "status": "sent"},
            ],
        },
        "accept",
    )
    operation_view = buyer_todo.clip_for_processor(operation, ["SUP000001"])
    assert operation_view["buyerQuoteAmount"] is None
    assert operation_view["referenceTotal"] == 267
    assert operation_view["autoAcceptMaxAmount"] is None
    assert operation_view["supplierQuotes"] in ("", None)
    assert clipped["referenceTotal"] is None
    assert operation_view["nextAction"]["action"] == "accept_or_reject"


def test_clip_for_processor_keeps_approved_final_deal_on_accept():
    item = buyer_todo.item_from_row(
        {
            "outsource_type": "part",
            "mold_no": "M260063-P2",
            "awarded_order_no": "EO-260930-R77L",
            "our_quote_amount": 30000,
            "auto_accept_max_amount": 55555,
            "final_deal_amount": 50000,
            "parts": [{"partNo": "B1-01", "partName": "下托板", "qty": 1}],
            "invitations": [{
                "supplierName": "青岛和兴嘉业金属制品有限公司",
                "supplierCode": "SUP000001",
                "status": "quoted",
                "quoteAmount": 66666,
            }],
        },
        "accept",
    )
    clipped = buyer_todo.clip_for_processor(item, ["SUP000001"])
    assert clipped["buyerQuoteAmount"] == 30000
    assert clipped["autoAcceptMaxAmount"] is None
    assert clipped["finalDealAmount"] == 50000
    assert clipped["nextAction"]["action"] == "accept_or_reject"


def test_operation_reject_next_action_mentions_auto_next():
    item = buyer_todo.item_from_row(
        {
            "outsource_type": "operation",
            "mold_no": "M260063-P1",
            "awarded_order_id": 9,
            "awarded_order_no": "EO-9",
            "parts": [{"partNo": "PU-06", "processName": "CNC"}],
        },
        "accept",
    )
    action = buyer_todo.processor_next_action(item)
    assert action["action"] == "accept_or_reject"
    assert action["orderNo"] == "EO-9"
    assert action["mold"] == "M260063"
    assert action["batch"] == "M260063-P1"
    assert "orderId" not in action
    assert "自动" in action["hint"]


def test_spoken_processor_quote_locks_visible_no_and_amount():
    locked = tools.spoken_quote_arguments("no.1报价：3000吧")
    assert locked["board_row"] == 1
    assert locked["unit_price"] == 3000
    assert locked["tax_included"] is True
    assert "delivery_date" not in locked
    dated = tools.spoken_quote_arguments("NO.1报价3000，交期2026-10-15，不含税")
    assert dated["delivery_date"] == "2026-10-15"
    assert dated["tax_included"] is False
    filled = tools.spoken_quote_arguments("no.1填报价 3000")
    assert filled["board_row"] == 1
    assert filled["unit_price"] == 3000
    follow = tools.spoken_quote_arguments("确认办理提交报价吧。", "no.1填报价 3000")
    assert follow["board_row"] == 1
    assert follow["unit_price"] == 3000
    assert tools.spoken_quote_arguments("第2行填写报价300上限40000") is None
    by_order = tools.spoken_quote_arguments("EO-260930-R77L报价88888元")
    assert by_order["order_no"] == "EO-260930-R77L"
    assert by_order["unit_price"] == 88888
    assert "board_row" not in by_order
    dashed = tools.spoken_quote_arguments("EO－260930－R77L报价88888元")
    assert dashed["order_no"] == "EO-260930-R77L"
    assert dashed["unit_price"] == 88888
    by_batch = tools.spoken_quote_arguments("M260063-P4报价66666")
    assert by_batch["batch"] == "M260063-P4"
    assert by_batch["mold"] == "M260063"
    assert by_batch["unit_price"] == 66666
    assert "board_row" not in by_batch
    bare = tools.spoken_quote_arguments("报价66666")
    assert bare["unit_price"] == 66666
    unique = tools.spoken_quote_from_board("报价88888元", [{
        **QUOTE_ITEM,
        "rejectedOrderNo": "EO-260930-R77L",
    }])
    assert unique["order_no"] == "EO-260930-R77L"
    assert unique["unit_price"] == 88888
    visible = tools.spoken_quote_from_board("报价66666", [{
        "station": "待报价",
        "orderNo": "",
        "mold": "M260063",
        "batch": "M260063-P4",
    }])
    assert visible["batch"] == "M260063-P4"
    assert visible["mold"] == "M260063"
    assert visible["unit_price"] == 66666
    assert tools.spoken_quote_arguments("待办有几个") is None
    confirmed = tools.spoken_quote_arguments(
        "确认",
        "no.1报价：3000吧\n请确认交期是否为2026-10-15。",
    )
    assert confirmed["board_row"] == 1
    assert confirmed["unit_price"] == 3000
    assert "delivery_date" not in confirmed


def test_processor_quote_resolves_rejected_order_no(monkeypatch):
    item = {
        **QUOTE_ITEM,
        "rejectedOrderNo": "EO-260930-R77L",
        "deliveryDate": "2026-10-15",
    }
    monkeypatch.setattr(buyer_todo, "find_item_by_identity", lambda **kwargs: item)
    data = erp_outsource_processor_tools._resolve_processor_quote(
        erp_outsource_processor_tools.parse(erp_outsource_processor_tools.QUOTE_TOOL, {
            "order_no": "EO-260930-R77L",
            "unit_price": 88888,
        }),
        ["SUP000001"],
    )
    assert data.invitation_id == 21
    assert data.delivery_date == "2026-10-15"
    batch_data = erp_outsource_processor_tools._resolve_processor_quote(
        erp_outsource_processor_tools.parse(erp_outsource_processor_tools.QUOTE_TOOL, {
            "batch": "M260063-P4",
            "unit_price": 66666,
        }),
        ["SUP000001"],
    )
    assert batch_data.invitation_id == 21
    monkeypatch.setattr(
        buyer_todo,
        "run",
        lambda parsed, processor_tokens=None: {"items": [item]},
    )
    unique_data = erp_outsource_processor_tools._resolve_processor_quote(
        erp_outsource_processor_tools.parse(erp_outsource_processor_tools.QUOTE_TOOL, {
            "unit_price": 66666,
        }),
        ["SUP000001"],
    )
    assert unique_data.invitation_id == 21


def test_board_row_quote_prepares_confirmation_card(monkeypatch):
    item = dict(QUOTE_ITEM, deliveryDate="2026-10-20", moldFamily="M260063", moldBatch="M260063-P1")
    monkeypatch.setattr(buyer_todo, "run", lambda parsed, processor_tokens=None: {"items": [item]})
    monkeypatch.setattr(buyer_todo, "find_invitation", lambda invitation_id, mold=None: (item, QUOTE_INVITATION))
    result = tools.execute_tool(None, Admin(), tools.QUOTE_TOOL, {
        "board_row": 1,
        "unit_price": 3000,
    })
    proposal = result["proposal"]
    assert proposal["kind"] == "erp_outsource_processor_quote"
    assert proposal["input"]["invitation_id"] == 21
    assert proposal["input"]["board_row"] == 1
    assert proposal["input"]["delivery_date"] == "2026-10-20"
    assert proposal["input"]["tax_included"] is True
    assert proposal["display"]["报价金额"] == 3000
    assert proposal["display"]["承诺交期"] == "2026-10-20"
    assert proposal["display"]["是否含税"] == "含税"


def test_board_row_quote_does_not_invent_a_missing_due_date(monkeypatch):
    item = dict(QUOTE_ITEM, deliveryDate=None)
    monkeypatch.setattr(buyer_todo, "run", lambda parsed, processor_tokens=None: {"items": [item]})
    try:
        tools.execute_tool(None, Admin(), tools.QUOTE_TOOL, {"board_row": 1, "unit_price": 3000})
    except DomainError as error:
        assert error.code == "INVALID_TOOL_INPUT"
        assert "交期" in error.message
    else:
        raise AssertionError("expected missing due date")


def test_host_invokes_processor_quote_card_from_row_speech():
    from domain_packs.mold.harness_policy import spoken_write_auto_invoke, spoken_write_ensure_tools

    catalog = [
        "query_erp_outsource_processor_board",
        "prepare_erp_outsource_processor_quote",
    ]
    ensured = spoken_write_ensure_tools("no.1报价：3000吧", catalog)
    assert "prepare_erp_outsource_processor_quote" in ensured
    name, arguments = spoken_write_auto_invoke("no.1报价：3000吧", catalog)
    assert name == "prepare_erp_outsource_processor_quote"
    assert arguments["board_row"] == 1
    assert arguments["unit_price"] == 3000
    ensured_fill = spoken_write_ensure_tools("no.1填报价 3000", catalog)
    assert "prepare_erp_outsource_processor_quote" in ensured_fill
    fill_name, fill_arguments = spoken_write_auto_invoke("no.1填报价 3000", catalog)
    assert fill_name == "prepare_erp_outsource_processor_quote"
    assert fill_arguments["board_row"] == 1
    assert fill_arguments["unit_price"] == 3000
    order_name, order_arguments = spoken_write_auto_invoke("EO-260930-R77L报价88888元", catalog)
    assert order_name == "prepare_erp_outsource_processor_quote"
    assert order_arguments["order_no"] == "EO-260930-R77L"
    assert order_arguments["unit_price"] == 88888
    batch_name, batch_arguments = spoken_write_auto_invoke("M260063-P4报价66666", catalog)
    assert batch_name == "prepare_erp_outsource_processor_quote"
    assert batch_arguments["batch"] == "M260063-P4"
    assert batch_arguments["unit_price"] == 66666
    bare_name, bare_arguments = spoken_write_auto_invoke("报价66666", catalog)
    assert bare_name == "prepare_erp_outsource_processor_quote"
    assert bare_arguments["unit_price"] == 66666


def test_quote_input_accepts_no_label_and_blank_invitation_id():
    parsed = tools.parse(tools.QUOTE_TOOL, {
        "invitation_id": "NO.1",
        "unit_price": "3000",
        "delivery_date": "",
    })
    assert parsed.board_row == 1
    assert parsed.invitation_id is None
    assert parsed.unit_price == 3000
    assert parsed.delivery_date is None
    blank = tools.parse(tools.QUOTE_TOOL, {
        "board_row": "1",
        "invitation_id": "",
        "quoteAmount": 3000,
    })
    assert blank.board_row == 1
    assert blank.invitation_id is None
    assert blank.unit_price == 3000


def _accepted_item():
    return {
        "orderId": 5176,
        "orderNo": "EO-261009-YMTD",
        "station": "accept",
        "stationLabel": "待接单",
        "outsourceType": "operation",
        "outsourceTypeLabel": "工序委外",
        "moldNo": "M260063-P1",
        "moldFamily": "M260063",
        "moldBatch": "M260063-P1",
        "partDetails": "UB-02 上垫板（PTR）",
        "supplierCode": "SUP000114",
        "awardedStage": "material_receiving",
        "awardedStatus": "open",
    }


def test_find_awarded_order_keeps_post_accept_stage(monkeypatch):
    monkeypatch.setattr(buyer_todo, "fetch_all", lambda sql, params: [{
        "project_id": 5298,
        "outsource_type": "operation",
        "mold_no": "M260063-P1",
        "inquiry_id": None,
        "our_quote_amount": None,
        "auto_accept_max_amount": None,
        "final_deal_amount": None,
        "delivery_date": None,
        "invitation_count": 0,
        "quoted_count": 0,
        "invitations": [],
        "awarded_stage": "material_receiving",
        "awarded_status": "open",
        "awarded_order_id": 5176,
        "awarded_order_no": "EO-261009-YMTD",
        "supplier_id": 4,
        "supplier_code": "SUP000114",
        "supplier_name": "青岛和兴嘉业金属制品有限公司",
        "awarded_amount": 1,
        "process_name": "PTR",
        "dispatch_exhausted": False,
        "pending_dispatch_suppliers": None,
        "parts": [{"partNo": "UB-02", "partName": "上垫板", "moldCode": "M260063-P1", "qty": 1}],
        "reference_total": None,
    }])
    item = buyer_todo.find_awarded_order(
        order_no="EO-261009-YMTD", mold="M260063", batch="M260063-P1",
    )
    assert item["orderId"] == 5176
    assert item["awardedStage"] == "material_receiving"
    assert buyer_todo.find_awarded_order(
        order_no="EO-261009-YMTD", mold="M210236", batch="M210236-P1",
    ) is None


def test_validate_intent_allows_already_accepted_after_timeout(monkeypatch):
    user = SimpleNamespace(id="u1", security_version=1, super_admin=True)
    run = SimpleNamespace(
        id="r1", user_id="u1", status="SUCCEEDED", security_version=1,
        checkpoint={"authorization_hash": "h"},
    )
    proposal = {
        "kind": "erp_outsource_processor_accept",
        "input": {"order_no": "EO-261009-YMTD", "mold": "M260063", "batch": "M260063-P1"},
        "display": {"操作": "确认接单"},
    }
    step = SimpleNamespace(id="s1", run_id="r1", tool=tools.ACCEPT_TOOL, result={"proposal": proposal})
    monkeypatch.setattr(tools, "fingerprint", lambda db, current: "h")
    monkeypatch.setattr(
        "domain_packs.mold.tool_gateway.available_tools",
        lambda db, current: {tools.ACCEPT_TOOL},
    )
    monkeypatch.setattr(buyer_todo, "find_item_by_identity", lambda **kwargs: None)
    monkeypatch.setattr(buyer_todo, "find_awarded_order", lambda **kwargs: _accepted_item())
    monkeypatch.setattr(tools, "content_hash", lambda value: "hash")
    monkeypatch.setattr(tools, "_tokens", lambda db, user: ["SUP000114"])
    found, key, data = tools.validate_intent(_Db(step, run), user, {
        "step_id": "s1",
        "proposal_hash": "hash",
    })
    assert key == tools.ACCEPT_TOOL
    assert found is proposal
    assert data.order_no == "EO-261009-YMTD"


def test_confirm_observes_already_accepted_without_repost(monkeypatch):
    item = _accepted_item()
    marked = {}

    def boom(*args, **kwargs):
        raise AssertionError("must not post accept again")

    monkeypatch.setattr(tools, "validate_intent", lambda db, user, payload: (
        {},
        tools.ACCEPT_TOOL,
        tools.parse(tools.ACCEPT_TOOL, {
            "order_no": "EO-261009-YMTD",
            "mold": "M260063",
            "batch": "M260063-P1",
        }),
    ))
    monkeypatch.setattr(buyer_todo, "find_item_by_identity", lambda **kwargs: None)
    monkeypatch.setattr(buyer_todo, "find_awarded_order", lambda **kwargs: item)
    monkeypatch.setattr(tools, "_tokens", lambda db, user: ["SUP000114"])
    monkeypatch.setattr(tools, "post_erp", boom)
    monkeypatch.setattr(
        tools,
        "_mark_accept_observed",
        lambda db, order_id, response: marked.update({"order_id": order_id, "response": response}),
    )
    result = tools.confirm(None, Admin(), {"_intent_id": "intent-timeout"})
    assert result["status"] == "CONFIRMED"
    assert result["order_no"] == "EO-261009-YMTD"
    assert result["erp"]["observed"] is True
    assert marked["order_id"] == 5176
    assert "不要确认原料收货" in result["nextHint"]
