from types import SimpleNamespace

import pytest

from agent_core.confirmation_policy import proposal_run_is_open
from domain_packs.mold import proposal_handlers, tool_gateway
from domain_packs.mold.ports.errors import DomainError
from domain_packs.mold.tools.erp.procurement import erp_outsource_processor_tools
from domain_packs.mold.tools.erp.procurement import erp_outsource_processor_tools as tools
from domain_packs.mold.tools.erp.procurement.outsource_queries import buyer_todo


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
    item = dict(QUOTE_ITEM, station="place_order", stationLabel="待下单")
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


def test_processor_reject_operation_card_mentions_auto_next(monkeypatch):
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
    assert "自动转下一家" in result["proposal"]["display"]["说明"]


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
    assert tools.spoken_quote_arguments("待办有几个") is None
    confirmed = tools.spoken_quote_arguments(
        "确认",
        "no.1报价：3000吧\n请确认交期是否为2026-10-15。",
    )
    assert confirmed["board_row"] == 1
    assert confirmed["unit_price"] == 3000
    assert "delivery_date" not in confirmed


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
