from domain_packs.mold import proposal_handlers, tool_gateway
from domain_packs.mold.ports.errors import DomainError
from domain_packs.mold.tools.erp.procurement import erp_outsource_buyer_tools
from domain_packs.mold.tools.erp.procurement.outsource_queries import buyer_todo


class Admin:
    super_admin = True
    id = "admin"
    security_version = 1


def test_buyer_ops_skill_is_registered():
    paths = tool_gateway.skill_paths()
    assert "outsource_buyer_ops" in tool_gateway.SKILLS
    assert paths["outsource_buyer_ops"]["path"].is_file()
    for key in erp_outsource_buyer_tools.TOOL_KEYS:
        assert key in tool_gateway.TOOLS
        assert tool_gateway.TOOLS[key]["permission"] == "erp_outsource_buyer.execute"
        assert proposal_handlers.handler_for_tool(key).action == "erp_outsource_buyer.execute"


def test_buyer_quote_prepare_requires_matching_station(monkeypatch):
    monkeypatch.setattr(buyer_todo, "find_item", lambda inquiry_id, mold=None: {
        "inquiryId": inquiry_id,
        "station": "inquiry_send",
        "stationLabel": "待发询价",
        "outsourceType": "part",
        "outsourceTypeLabel": "零件委外",
        "moldNo": "M260063-P1",
        "partDetails": "PU-06 上垫板",
    })
    try:
        erp_outsource_buyer_tools.execute_tool(None, Admin(), erp_outsource_buyer_tools.QUOTE_TOOL, {
            "inquiry_id": 9,
            "our_quote_amount": 320,
            "auto_accept_max_amount": 400,
        })
    except DomainError as error:
        assert error.code == "STATE_BLOCKED"
    else:
        raise AssertionError("expected STATE_BLOCKED")


def test_buyer_quote_prepare_returns_confirmation_card(monkeypatch):
    monkeypatch.setattr(buyer_todo, "find_item", lambda inquiry_id, mold=None: {
        "inquiryId": inquiry_id,
        "station": "buyer_quote",
        "stationLabel": "待采购填报价",
        "outsourceType": "part",
        "outsourceTypeLabel": "零件委外",
        "moldNo": "M260063-P1",
        "partDetails": "PU-06 上垫板",
    })
    result = erp_outsource_buyer_tools.execute_tool(None, Admin(), erp_outsource_buyer_tools.QUOTE_TOOL, {
        "inquiry_id": 9,
        "our_quote_amount": 320,
        "auto_accept_max_amount": 400,
    })
    assert result["proposal"]["kind"] == "erp_outsource_buyer_quote"
    assert result["proposal"]["display"]["我方报价"] == 320
    assert result["proposal"]["display"]["模具号"] == "M260063-P1"


def test_operation_order_cannot_prepare_buyer_quote(monkeypatch):
    monkeypatch.setattr(buyer_todo, "find_item", lambda inquiry_id, mold=None: {
        "inquiryId": inquiry_id,
        "station": "buyer_quote",
        "stationLabel": "待采购填报价",
        "outsourceType": "operation",
        "outsourceTypeLabel": "工序委外",
        "moldNo": "M260063-P1",
        "partDetails": "CNC",
    })
    try:
        erp_outsource_buyer_tools.preview(erp_outsource_buyer_tools.QUOTE_TOOL, erp_outsource_buyer_tools.parse(
            erp_outsource_buyer_tools.QUOTE_TOOL,
            {"inquiry_id": 3, "our_quote_amount": 1, "auto_accept_max_amount": 2},
        ))
    except DomainError as error:
        assert error.code == "STATE_BLOCKED"
        assert "工序" in error.message
    else:
        raise AssertionError("expected STATE_BLOCKED")


def test_reselect_confirm_is_prepare_only(monkeypatch):
    item = {
        "inquiryId": 4,
        "station": "exhausted",
        "stationLabel": "全部拒单",
        "outsourceType": "part",
        "outsourceTypeLabel": "零件委外",
        "moldNo": "M260063-P2",
        "partDetails": "PU-06",
    }
    monkeypatch.setattr(buyer_todo, "find_item", lambda inquiry_id, mold=None: item)
    monkeypatch.setattr(erp_outsource_buyer_tools, "source", lambda db, user, step_id: {
        "kind": "erp_outsource_reselect",
        "input": {"inquiry_id": 4, "note": "换铂锐"},
        "display": erp_outsource_buyer_tools.preview(
            erp_outsource_buyer_tools.RESELECT_TOOL,
            erp_outsource_buyer_tools.parse(erp_outsource_buyer_tools.RESELECT_TOOL, {
                "inquiry_id": 4, "note": "换铂锐",
            }),
        )[1],
    })
    from domain_packs.mold.ports.bpm import content_hash

    proposal = erp_outsource_buyer_tools.source(None, Admin(), "step-1")
    result = erp_outsource_buyer_tools.confirm(None, Admin(), {
        "step_id": "step-1",
        "proposal_hash": content_hash(proposal),
    })
    assert result["status"] == "PREPARE_ONLY"
    assert "发询价" in result["message"]
