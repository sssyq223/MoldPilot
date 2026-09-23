from domain_packs.mold import proposal_handlers, tool_gateway
from domain_packs.mold.ports.errors import DomainError
from domain_packs.mold.tools.erp.procurement import erp_outsource_processor_tools
from domain_packs.mold.tools.erp.procurement.outsource_queries import buyer_todo


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
    assert result["proposal"]["display"]["模具号"] == "M260063-P1"


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
    monkeypatch.setattr(buyer_todo, "find_item_by_order", lambda order_id, mold=None: {
        "orderId": order_id,
        "orderNo": "EO-1",
        "station": "accept",
        "stationLabel": "待接单",
        "outsourceType": "part",
        "outsourceTypeLabel": "零件委外",
        "moldNo": "M260063-P1",
        "partDetails": "PU-06 上垫板",
        "supplierName": "铂锐",
    })
    result = erp_outsource_processor_tools.execute_tool(None, Admin(), erp_outsource_processor_tools.ACCEPT_TOOL, {
        "order_id": 44,
    })
    assert result["proposal"]["kind"] == "erp_outsource_processor_accept"
    assert result["proposal"]["display"]["工单ID"] == 44


def test_processor_reject_operation_card_mentions_auto_next(monkeypatch):
    monkeypatch.setattr(buyer_todo, "find_item_by_order", lambda order_id, mold=None: {
        "orderId": order_id,
        "orderNo": "EO-9",
        "station": "accept",
        "stationLabel": "待接单",
        "outsourceType": "operation",
        "outsourceTypeLabel": "工序委外",
        "moldNo": "M260063-P1",
        "partDetails": "CNC",
        "supplierName": "铂锐",
    })
    result = erp_outsource_processor_tools.execute_tool(None, Admin(), erp_outsource_processor_tools.REJECT_TOOL, {
        "order_id": 9,
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
    assert clipped["nextAction"]["action"] == "quote"
    assert "精工" not in (clipped["supplierQuotes"] or "")


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
    assert action["orderId"] == 9
    assert "自动" in action["hint"]
