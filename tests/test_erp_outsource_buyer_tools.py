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


def test_spoken_board_row_tolerates_extra_punctuation():
    assert buyer_todo.spoken_board_row_number("NO.2填写报价") == 2
    assert buyer_todo.spoken_board_row_number("NO.,2填写报价 总价20000上限66666") == 2
    assert buyer_todo.spoken_board_row_number("NO,2填写报价") == 2
    assert buyer_todo.spoken_board_row_number("NO：2填写报价") == 2
    assert buyer_todo.spoken_board_row_number("NO. 2填写报价") == 2
    assert buyer_todo.spoken_board_row_number("NOTE 2填写报价") is None


def test_buyer_quote_prepare_requires_matching_station(monkeypatch):
    monkeypatch.setattr(buyer_todo, "find_item_by_identity", lambda **kwargs: {
        "inquiryId": 9,
        "station": "inquiry_send",
        "stationLabel": "待发询价",
        "outsourceType": "part",
        "outsourceTypeLabel": "零件委外",
        "moldNo": "M260063-P1",
        "partDetails": "PU-06 上垫板",
    })
    try:
        erp_outsource_buyer_tools.execute_tool(None, Admin(), erp_outsource_buyer_tools.QUOTE_TOOL, {
            "mold": "M260063",
            "batch": "M260063-P1",
            "our_quote_amount": 320,
            "auto_accept_max_amount": 400,
        })
    except DomainError as error:
        assert error.code == "STATE_BLOCKED"
    else:
        raise AssertionError("expected STATE_BLOCKED")


def test_buyer_quote_accepts_spoken_total_price_aliases():
    data = erp_outsource_buyer_tools.parse(erp_outsource_buyer_tools.QUOTE_TOOL, {
        "mold": "M260063",
        "batch": "M260063-P1",
        "ourQuote": 3000,
        "upperLimit": 4000,
        "parts": "PH-01 上夹板",
        "reason": "ignored extra",
    })
    assert data.our_quote_amount == 3000
    assert data.auto_accept_max_amount == 4000
    assert data.mold == "M260063"
    assert data.batch == "M260063-P1"
    assert data.part == "PH-01"


def test_buyer_quote_prepare_returns_confirmation_card(monkeypatch):
    monkeypatch.setattr(buyer_todo, "find_item_by_identity", lambda **kwargs: {
        "inquiryId": 9,
        "station": "buyer_quote",
        "stationLabel": "待采购填报价",
        "outsourceType": "part",
        "outsourceTypeLabel": "零件委外",
        "moldNo": "M260063-P1",
        "partDetails": "PU-06 上垫板",
    })
    result = erp_outsource_buyer_tools.execute_tool(None, Admin(), erp_outsource_buyer_tools.QUOTE_TOOL, {
        "mold": "M260063",
        "batch": "M260063-P1",
        "our_quote_amount": 320,
        "auto_accept_max_amount": 400,
    })
    assert result["proposal"]["kind"] == "erp_outsource_buyer_quote"
    assert result["proposal"]["display"]["我方报价"] == 320
    assert result["proposal"]["display"]["订单号"] == "尚未下单"
    assert result["proposal"]["display"]["模具号"] == "M260063"
    assert result["proposal"]["display"]["批次号"] == "M260063-P1"
    assert result["proposal"]["display"]["报价表"][0]["项目"] == "核算价"
    assert result["proposal"]["display"]["报价表"][1]["金额"] == 320


def test_spoken_quote_requires_mold_and_order_or_batch_part(monkeypatch):
    monkeypatch.setattr(buyer_todo, "query_items", lambda parsed: [
        {
            "parts": [{"partNo": "PH-01"}],
            "partDetails": "PH-01 上夹板",
            "moldNo": "M260063-P1",
            "moldFamily": "M260063",
            "moldBatch": "M260063-P1",
            "orderNo": "",
        },
    ])
    locked = buyer_todo.parse_spoken_buyer_quote(
        "模具 M260063 批次 M260063-P1 零件 PH-01 准备填写我方报价 300、上限 380，出确认卡",
    )
    assert locked["mold"] == "M260063"
    assert locked["batch"] == "M260063-P1"
    assert locked["part"] == "PH-01"
    assert locked["our_quote_amount"] == 300
    assert locked["auto_accept_max_amount"] == 380
    follow_up = buyer_todo.parse_spoken_buyer_quote(
        "就第1行零件 PH-01 这一张，准备填写我方报价 300、上限 380，出确认卡",
        "ERP 委外待办 M260063 · M260063-P1 共 4 条",
    )
    assert follow_up["mold"] == "M260063"
    assert follow_up["part"] == "PH-01"
    monkeypatch.setattr(buyer_todo, "query_items", lambda parsed: [])
    assert buyer_todo.parse_spoken_buyer_quote(
        "就第1行零件 PH-01 这一张，准备填写我方报价 300、上限 380，出确认卡",
    ) is None
    assert buyer_todo.parse_spoken_buyer_quote("待采购填报价有几个") is None
    assert buyer_todo.is_spoken_buyer_quote(
        "M260063-P4 有零件是B1-01的 这笔订单的报报价：总价150000上限300000",
    )
    named = buyer_todo.parse_spoken_buyer_quote(
        "M260063-P4 B1-01下托板（S-Z 1040×1290×30）；这笔订单的报报价：总价150000上限300000",
    )
    assert named["mold"] == "M260063"
    assert named["batch"] == "M260063-P4"
    assert named["part"] == "B1-01"
    assert named["our_quote_amount"] == 150000
    assert named["auto_accept_max_amount"] == 300000
    part_spoken = buyer_todo.parse_spoken_buyer_quote(
        "M260063-P4 有零件是B1-01的 这笔订单的报报价：总价150000上限300000",
    )
    assert part_spoken["mold"] == "M260063"
    assert part_spoken["part"] == "B1-01"


def test_spoken_quote_locks_first_board_row_without_repeating_part(monkeypatch):
    monkeypatch.setattr(buyer_todo, "query_items", lambda parsed: [
        {
            "parts": [{"partNo": "PH-01"}],
            "partDetails": "PH-01 上夹板",
            "moldNo": "M260063-P1",
            "orderNo": "",
        },
        {
            "parts": [{"partNo": "PU-01"}],
            "partDetails": "PU-01 成型冲头",
            "moldNo": "M260063-P1",
            "orderNo": "",
        },
    ])
    locked = buyer_todo.parse_spoken_buyer_quote(
        "就第1行这一张，准备填写我方报价 300、上限 380，出确认卡",
        "ERP 委外待办 M260063 · M260063-P1 共 4 条",
    )
    assert locked["mold"] == "M260063"
    assert locked["batch"] == "M260063-P1"
    assert locked["part"] == "PH-01"


def test_spoken_quote_locks_first_pending_row_from_board_speech(monkeypatch):
    monkeypatch.setattr(buyer_todo, "query_items", lambda parsed: [
        {
            "parts": [{"partNo": "B1-01"}],
            "partDetails": "B1-01 下托板",
            "moldNo": "M260063-P4",
            "moldFamily": "M260063",
            "moldBatch": "M260063-P4",
            "orderNo": "",
            "station": "buyer_quote",
            "stationLabel": "待采购填报价",
        },
        {
            "parts": [{"partNo": "PH-01"}],
            "partDetails": "PH-01 上夹板",
            "moldNo": "M260063-P1",
            "moldFamily": "M260063",
            "moldBatch": "M260063-P1",
            "orderNo": "",
            "station": "buyer_quote",
            "stationLabel": "待采购填报价",
        },
    ])
    locked = buyer_todo.parse_spoken_buyer_quote(
        "把待排列第一的这个订单报报价：总价150000上限300000",
    )
    assert locked["mold"] == "M260063"
    assert locked["batch"] == "M260063-P4"
    assert locked["part"] == "B1-01"
    assert locked["our_quote_amount"] == 150000
    assert locked["auto_accept_max_amount"] == 300000
    from_board = buyer_todo.quote_arguments_from_board(
        "把待排列第一的这个订单报报价：总价150000上限300000",
        [
            {
                "station": "待采购填报价",
                "mold": "M260063",
                "batch": "M260063-P4",
                "parts": [{"partNo": "B1-01"}],
                "partDetails": "B1-01 下托板",
            },
            {
                "station": "待采购填报价",
                "mold": "M260063",
                "batch": "M260063-P1",
                "parts": [{"partNo": "PH-01"}],
            },
        ],
    )
    assert from_board["batch"] == "M260063-P4"
    assert from_board["part"] == "B1-01"


def test_normalize_keeps_hyphenated_part_codes():
    assert buyer_todo.normalize_part_token("B1-01 下托板") == "B1-01"
    assert buyer_todo.spoken_part_token("待办第1行 B1-01 下托板") == "B1-01"
    assert buyer_todo.spoken_part_token("模具 M260063-P1 零件 PH-01") == "PH-01"
    assert buyer_todo.spoken_part_token("M260063-P4 B1-01下托板") == "B1-01"
    assert buyer_todo.spoken_part_token("有零件是B1-01的") == "B1-01"


def test_spoken_quote_row_uses_full_board_batch(monkeypatch):
    monkeypatch.setattr(buyer_todo, "query_items", lambda parsed: [
        {
            "parts": [{"partNo": "B1-01"}],
            "partDetails": "B1-01 下托板",
            "moldNo": "M260063-P1、M260063-P2",
            "moldFamily": "M260063",
            "moldBatch": "M260063-P1、M260063-P2",
            "orderNo": "",
        },
        {
            "parts": [{"partNo": "B1-01"}],
            "partDetails": "B1-01 下托板",
            "moldNo": "M260063-P1、M260063-P2、M260063-P3",
            "moldFamily": "M260063",
            "moldBatch": "M260063-P1、M260063-P2、M260063-P3",
            "orderNo": "",
        },
    ])
    locked = buyer_todo.parse_spoken_buyer_quote(
        "模具 M260063 批次 M260063-P1，待办第1行 B1-01 下托板，准备填写我方报价 2100、上限 2600",
    )
    assert locked["part"] == "B1-01"
    assert locked["batch"] == "M260063-P1、M260063-P2"
    assert locked["board_row"] == 1
    assert locked["our_quote_amount"] == 2100


def test_operation_order_cannot_prepare_buyer_quote(monkeypatch):
    monkeypatch.setattr(buyer_todo, "find_item_by_identity", lambda **kwargs: {
        "inquiryId": 3,
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
            {"batch": "M260063-P1", "our_quote_amount": 1, "auto_accept_max_amount": 2},
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
    monkeypatch.setattr(buyer_todo, "find_item_by_identity", lambda **kwargs: item)
    monkeypatch.setattr(erp_outsource_buyer_tools, "source", lambda db, user, step_id: {
        "kind": "erp_outsource_reselect",
        "input": {"batch": "M260063-P2", "note": "换铂锐"},
        "display": erp_outsource_buyer_tools.preview(
            erp_outsource_buyer_tools.RESELECT_TOOL,
            erp_outsource_buyer_tools.parse(erp_outsource_buyer_tools.RESELECT_TOOL, {
                "batch": "M260063-P2", "note": "换铂锐",
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


def test_single_batch_does_not_match_combined_inquiry():
    combined = {
        "orderNo": "",
        "moldNo": "M260063-P1、M260063-P2",
        "moldFamily": "M260063",
        "moldBatch": "M260063-P1、M260063-P2",
        "parts": [{"moldCode": "M260063-P1"}, {"moldCode": "M260063-P2"}],
    }
    single = {
        "orderNo": "",
        "moldNo": "M260063-P1",
        "moldFamily": "M260063",
        "moldBatch": "M260063-P1",
        "parts": [{"moldCode": "M260063-P1"}],
    }
    assert buyer_todo.item_matches_identity(single, mold="M260063", batch="M260063-P1")
    assert not buyer_todo.item_matches_identity(combined, mold="M260063", batch="M260063-P1")
    assert buyer_todo.item_matches_identity(combined, batch="M260063-P1、M260063-P2")


def test_board_row_locks_duplicate_part_quote(monkeypatch):
    rows = [
        {
            "inquiryId": 41,
            "station": "buyer_quote",
            "stationLabel": "待采购填报价",
            "outsourceType": "part",
            "outsourceTypeLabel": "零件委外",
            "orderNo": "",
            "moldNo": "M260063-P1",
            "moldFamily": "M260063",
            "moldBatch": "M260063-P1",
            "parts": [{"partNo": "B1-01"}],
            "partDetails": "B1-01 下托板",
            "referenceTotal": 104576.58,
        },
        {
            "inquiryId": 42,
            "station": "buyer_quote",
            "stationLabel": "待采购填报价",
            "outsourceType": "part",
            "outsourceTypeLabel": "零件委外",
            "orderNo": "",
            "moldNo": "M260063-P1",
            "moldFamily": "M260063",
            "moldBatch": "M260063-P1",
            "parts": [{"partNo": "B1-01"}],
            "partDetails": "B1-01 下托板",
            "referenceTotal": 103206.82,
        },
    ]
    monkeypatch.setattr(buyer_todo, "query_items", lambda parsed: rows)
    monkeypatch.setattr(buyer_todo, "_scan_items", lambda mold=None: rows)
    result = erp_outsource_buyer_tools.execute_tool(None, Admin(), erp_outsource_buyer_tools.QUOTE_TOOL, {
        "mold": "M260063",
        "batch": "M260063-P1",
        "part": "B1-01",
        "board_row": 1,
        "our_quote_amount": 2100,
        "auto_accept_max_amount": 2600,
    })
    assert result["proposal"]["display"]["报价表"][0]["金额"] == 104576.58
    assert result["proposal"]["input"]["board_row"] == 1


def test_visible_no_locks_duplicate_batch(monkeypatch):
    def row(inquiry_id, batch, station="buyer_quote"):
        return {
            "inquiryId": inquiry_id,
            "station": station,
            "stationLabel": "待报价" if station == "supplier_quote" else "待采购填报价",
            "outsourceType": "part",
            "outsourceTypeLabel": "零件委外",
            "orderNo": "",
            "moldFamily": "M260063",
            "moldBatch": batch,
            "moldNo": batch,
            "parts": [{"partNo": "B1-01"}],
            "partDetails": "B1-01 下托板",
            "referenceTotal": 17995.66,
        }

    full = [row(1, "M260063-P4", "supplier_quote")]
    full.append(row(2, "M260063-P1"))
    for inquiry_id in range(3, 9):
        full.append(row(inquiry_id, "M260063-P2"))
    full.append(row(9, "M260063-P1"))

    def fake_query(parsed):
        station = parsed.get("station") or ""
        if not station:
            return list(full)
        return [item for item in full if item["station"] == station]

    monkeypatch.setattr(buyer_todo, "query_items", fake_query)
    monkeypatch.setattr(buyer_todo, "_scan_items", lambda mold=None: list(full))
    data = erp_outsource_buyer_tools.parse(erp_outsource_buyer_tools.QUOTE_TOOL, {
        "mold": "M260063",
        "batch": "M260063-P1",
        "part": "B1-01",
        "board_row": 9,
        "our_quote_amount": 300,
        "auto_accept_max_amount": 40000,
    })
    item, display = erp_outsource_buyer_tools.preview(erp_outsource_buyer_tools.QUOTE_TOOL, data)
    assert item["inquiryId"] == 9
    assert display["行号"] == "NO.9"
    spoken = buyer_todo.parse_spoken_buyer_quote("NO.2填写报价，总价300，上限40000")
    assert spoken["board_row"] == 2
    assert spoken["batch"] == "M260063-P1"
    extra_mark = buyer_todo.parse_spoken_buyer_quote("NO.,2填写报价 总价20000上限66666")
    assert extra_mark["board_row"] == 2
    assert extra_mark["our_quote_amount"] == 20000
    assert extra_mark["auto_accept_max_amount"] == 66666
    bare = buyer_todo.parse_spoken_buyer_quote("第9行报价300上限40000")
    assert bare["board_row"] == 9
    assert bare["batch"] == "M260063-P1"
    assert buyer_todo.parse_spoken_buyer_quote("第1行报价300上限40000") is None


def test_shared_batch_is_disambiguated_by_part(monkeypatch):
    items = [
        {
            "inquiryId": 11,
            "orderNo": "",
            "moldNo": "M260063-P1",
            "moldFamily": "M260063",
            "moldBatch": "M260063-P1",
            "parts": [{"partNo": "PH-01", "partName": "上夹板"}],
            "partDetails": "PH-01 上夹板（S-Z-M-WZ 166×128×20）",
        },
        {
            "inquiryId": 12,
            "orderNo": "",
            "moldNo": "M260063-P1",
            "moldFamily": "M260063",
            "moldBatch": "M260063-P1",
            "parts": [{"partNo": "PU-01", "partName": "成型冲头"}],
            "partDetails": "PU-01 成型冲头",
        },
        {
            "inquiryId": 13,
            "orderNo": "",
            "moldNo": "M260063-P1",
            "moldFamily": "M260063",
            "moldBatch": "M260063-P1",
            "parts": [{"partNo": "UB-01", "partName": "上垫板"}],
            "partDetails": "UB-01 上垫板",
        },
    ]
    monkeypatch.setattr(buyer_todo, "_scan_items", lambda mold=None: items)
    hit = buyer_todo.find_item_by_identity(
        mold="M260063",
        batch="M260063-P1",
        part="PH-01 上夹板（S-Z-M-WZ 166×128×20）",
    )
    assert hit["inquiryId"] == 11
    assert buyer_todo.find_item_by_identity(part="PH-01")["inquiryId"] == 11
    try:
        buyer_todo.find_item_by_identity(mold="M260063", batch="M260063-P1")
    except DomainError as error:
        assert error.code == "AMBIGUOUS"
        assert "并不是这个零件出现在多张工单里" in error.message
        assert "PH-01" in error.message
        assert "PU-01" in error.message
    else:
        raise AssertionError("expected AMBIGUOUS")


def test_buyer_inquiry_send_uses_supplier_names(monkeypatch):
    monkeypatch.setattr(buyer_todo, "find_item_by_identity", lambda **kwargs: {
        "inquiryId": 9,
        "station": "inquiry_send",
        "stationLabel": "待发询价",
        "outsourceType": "part",
        "outsourceTypeLabel": "零件委外",
        "moldNo": "M260063-P1",
        "partDetails": "PU-06",
        "invitations": [{"supplierId": 11, "supplierCode": "SUP000001", "supplierName": "铂锐"}],
    })
    result = erp_outsource_buyer_tools.execute_tool(None, Admin(), erp_outsource_buyer_tools.SEND_TOOL, {
        "batch": "M260063-P1",
        "suppliers": ["铂锐"],
    })
    assert result["proposal"]["kind"] == "erp_outsource_inquiry_send"
    assert result["proposal"]["display"]["加工商"] == "铂锐"
    assert "supplier_ids" not in result["proposal"]["input"]
    assert result["proposal"]["resolved_supplier_ids"] == [11]


def test_buyer_inquiry_send_uses_matched_list_when_user_does_not_name_supplier(monkeypatch):
    monkeypatch.setattr(buyer_todo, "find_item_by_identity", lambda **kwargs: {
        "inquiryId": 9,
        "station": "inquiry_send",
        "stationLabel": "待发询价",
        "outsourceType": "part",
        "outsourceTypeLabel": "零件委外",
        "moldNo": "M260063-P5",
        "partDetails": "B1-01 下托板",
        "invitations": [{
            "supplierId": 22,
            "supplierCode": "HX001",
            "supplierName": "青岛和兴金属制品有限公司",
        }],
    })
    result = erp_outsource_buyer_tools.execute_tool(None, Admin(), erp_outsource_buyer_tools.SEND_TOOL, {
        "batch": "M260063-P5",
    })
    assert result["proposal"]["display"]["加工商"] == "青岛和兴金属制品有限公司"
    assert result["proposal"]["input"].get("suppliers") == []
    assert result["proposal"]["resolved_supplier_ids"] == [22]


def test_attach_match_candidates_uses_erp_match_when_invitations_empty(monkeypatch):
    monkeypatch.setattr(buyer_todo, "fetch_match_candidates", lambda project_id: [{
        "supplierId": 22,
        "supplierCode": "HX001",
        "supplierName": "青岛和兴金属制品有限公司",
        "status": "matched",
    }] if project_id == 4367 else [])
    item = {
        "station": "inquiry_send",
        "projectId": 4367,
        "invitations": [],
    }
    attached = buyer_todo.attach_match_candidates(item)
    assert attached["pendingQuoteSuppliers"] == "青岛和兴金属制品有限公司"
    ids, labels = buyer_todo.resolve_suppliers(attached, None)
    assert ids == [22]
    assert labels == ["青岛和兴金属制品有限公司"]


def test_invitations_from_match_payload_reads_group_a():
    invitations = buyer_todo.invitations_from_match_payload({
        "data": {
            "groups": {
                "A": [{
                    "supplier_id": 22,
                    "supplier_name": "青岛和兴金属制品有限公司",
                    "supplier_code": "HX001",
                }],
            },
        },
    })
    assert invitations == [{
        "supplierId": 22,
        "supplierCode": "HX001",
        "supplierName": "青岛和兴金属制品有限公司",
        "status": "matched",
    }]


def test_spoken_inquiry_send_locks_batch_without_inventing_supplier(monkeypatch):
    monkeypatch.setattr(buyer_todo, "query_items", lambda parsed: [
        {"station": "inquiry_send", "moldFamily": "M260063", "moldBatch": "M260063-P1"},
        {"station": "inquiry_send", "moldFamily": "M260063", "moldBatch": "M260063-P5"},
    ])
    assert buyer_todo.parse_spoken_inquiry_send("确认办理发送询价吧") is None
    parsed = buyer_todo.parse_spoken_inquiry_send(
        "确认办理发送询价吧",
        "已经查到待发询价 M260063 M260063-P5",
    )
    assert parsed == {"mold": "M260063", "batch": "M260063-P5"}
    from_board = buyer_todo.send_arguments_from_board("确认办理发送询价吧", [
        {"station": "待采购填报价", "mold": "M260063", "batch": "M260063-P1"},
        {"station": "待发询价", "mold": "M260063", "batch": "M260063-P5"},
        {"station": "待发询价"},
    ])
    assert from_board == {"mold": "M260063", "batch": "M260063-P5", "board_row": 2}


def test_visible_board_row_locks_send_and_quote_separately(monkeypatch):
    rows = [
        {
            "inquiryId": 160,
            "station": "inquiry_send",
            "stationLabel": "待发询价",
            "moldFamily": "M260063",
            "moldBatch": "M260063-P4",
            "moldNo": "M260063-P4",
            "parts": [{"partNo": "B1-01"}],
            "partDetails": "B1-01 下托板（S-Z 1040×1290×30）",
            "orderNo": "",
        },
        {
            "inquiryId": 11,
            "station": "buyer_quote",
            "stationLabel": "待采购填报价",
            "moldFamily": "M260063",
            "moldBatch": "M260063-P1",
            "moldNo": "M260063-P1",
            "parts": [{"partNo": "B1-01"}],
            "partDetails": "B1-01 下托板（S-Z 850×1200×30）",
            "orderNo": "",
        },
        {
            "inquiryId": 12,
            "station": "buyer_quote",
            "stationLabel": "待采购填报价",
            "moldFamily": "M260063",
            "moldBatch": "M260063-P4",
            "moldNo": "M260063-P4",
            "parts": [{"partNo": "B1-01"}],
            "partDetails": "B1-01 下托板（S-Z-WC 1040×1290×30）",
            "orderNo": "",
        },
    ]
    monkeypatch.setattr(buyer_todo, "query_items", lambda parsed: rows)
    send = buyer_todo.parse_spoken_inquiry_send("第一行的订单给加工商发送询价吧")
    assert send["batch"] == "M260063-P4"
    assert send["board_row"] == 1
    quote = buyer_todo.parse_spoken_buyer_quote("第二行订单填写报价总价150000上限200000")
    assert quote["batch"] == "M260063-P1"
    assert quote["our_quote_amount"] == 150000
    assert quote["auto_accept_max_amount"] == 200000
    assert quote["board_row"] == 2
    board_send = buyer_todo.send_arguments_from_board("第一行的订单给加工商发送询价吧", rows)
    assert board_send["batch"] == "M260063-P4"
    assert board_send["board_row"] == 1
    board_quote = buyer_todo.quote_arguments_from_board("第二行订单填写报价总价150000上限200000", rows)
    assert board_quote["batch"] == "M260063-P1"
    assert board_quote["board_row"] == 2


def test_spoken_send_reads_already_filled_price_as_inquiry_send(monkeypatch):
    rows = [
        {
            "inquiryId": 160,
            "station": "inquiry_send",
            "stationLabel": "待发询价",
            "moldFamily": "M260063",
            "moldBatch": "M260063-P2",
            "moldNo": "M260063",
            "parts": [{"partNo": "B1-01"}],
            "partDetails": "B1-01 下托板",
            "orderNo": "",
        },
        {
            "inquiryId": 11,
            "station": "buyer_quote",
            "stationLabel": "待采购填报价",
            "moldFamily": "M260063",
            "moldBatch": "M260063-P1、M260063-P2",
            "orderNo": "",
        },
    ]
    monkeypatch.setattr(buyer_todo, "query_items", lambda parsed: rows)
    prompt = "NO.1已经填价格了呀 可以发给加工商了"
    assert buyer_todo.is_spoken_inquiry_send(prompt)
    assert not buyer_todo.is_spoken_buyer_quote(prompt)
    assert buyer_todo.parse_spoken_buyer_quote(prompt) is None
    parsed = buyer_todo.parse_spoken_inquiry_send(prompt)
    assert parsed["board_row"] == 1
    assert parsed["batch"] == "M260063-P2"
    assert parsed["part"] == "B1-01"
    for paraphrase in (
        "第一行价格填好了可以发了",
        "NO.1填完了发给厂家吧",
        "这单已经填过价了，给加工商发一下",
    ):
        assert buyer_todo.is_spoken_inquiry_send(paraphrase), paraphrase
        assert not buyer_todo.is_spoken_buyer_quote(paraphrase), paraphrase
    assert buyer_todo.is_spoken_buyer_quote("PH-01这一笔订单帮我填价格：400，上限是600")
    assert buyer_todo.is_spoken_buyer_quote("帮我填价格")
    assert not buyer_todo.is_spoken_buyer_quote("待采购填报价有几个")
    assert not buyer_todo.is_spoken_inquiry_send("确认发料")
    assert not buyer_todo.is_spoken_inquiry_send("不可以发给加工商")


def test_spoken_inquiry_send_locks_first_pending_row(monkeypatch):
    monkeypatch.setattr(buyer_todo, "query_items", lambda parsed: [
        {
            "station": "inquiry_send",
            "stationLabel": "待发询价",
            "moldNo": "M260063-P5",
            "moldFamily": "M260063",
            "moldBatch": "M260063-P5",
            "orderNo": "",
            "parts": [{"partNo": "B1-01"}],
            "partDetails": "B1-01 下托板",
        },
    ])
    locked = buyer_todo.parse_spoken_inquiry_send("第一行发送询价")
    assert locked["mold"] == "M260063"
    assert locked["batch"] == "M260063-P5"
    assert locked["part"] == "B1-01"
    assert locked["board_row"] == 1
    assert buyer_todo.parse_spoken_inquiry_send("发送询价") is None
    from_board = buyer_todo.send_arguments_from_board("第一行发送询价", [
        {"station": "待采购填报价", "mold": "M260063", "batch": "M260063-P4"},
        {"station": "待发询价", "mold": "M260063", "batch": "M260063-P5"},
    ])
    assert from_board["mold"] == "M260063"
    assert from_board["batch"] == "M260063-P5"
    assert from_board["board_row"] == 1
    named = buyer_todo.parse_spoken_inquiry_send(
        "进度是待发询价的这个订单发送询价吧",
        "ERP 委外待办 M260063 · M260063-P4 共 20 条",
    )
    assert named["batch"] == "M260063-P5"
    assert named["part"] == "B1-01"
    this_order = buyer_todo.parse_spoken_inquiry_send(
        "这个订单发送询价",
        "已经查到待采购填报价 M260063 M260063-P4",
    )
    assert this_order["batch"] == "M260063-P5"
    monkeypatch.setattr(buyer_todo, "query_items", lambda parsed: [
        {"station": "inquiry_send", "moldFamily": "M260063", "moldBatch": "M260063-P4"},
        {"station": "inquiry_send", "moldFamily": "M260063", "moldBatch": "M260063-P5"},
    ])
    assert buyer_todo.parse_spoken_inquiry_send(
        "进度是待发询价的这个订单发送询价吧",
        "已经查到待发询价 M260063 M260063-P4",
    ) is None


def test_named_send_station_locks_full_table_row_not_filtered_first(monkeypatch):
    rows = [
        {
            "inquiryId": 11,
            "station": "buyer_quote",
            "stationLabel": "待采购填报价",
            "moldFamily": "M260063",
            "moldBatch": "M260063-P4",
            "moldNo": "M260063",
            "parts": [{"partNo": "B1-01"}],
        },
        {
            "inquiryId": 160,
            "station": "inquiry_send",
            "stationLabel": "待发询价",
            "moldFamily": "M260063",
            "moldBatch": "M260063-P2",
            "moldNo": "M260063",
            "parts": [{"partNo": "B1-01"}],
        },
        {
            "inquiryId": 12,
            "station": "buyer_quote",
            "stationLabel": "待采购填报价",
            "moldFamily": "M260063",
            "moldBatch": "M260063-P5",
            "moldNo": "M260063",
            "parts": [{"partNo": "B1-01"}],
        },
    ]
    monkeypatch.setattr(buyer_todo, "query_items", lambda parsed: rows)
    prompt = "待发询价这个订单帮我给加工商发询价单"
    assert buyer_todo.is_spoken_inquiry_send(prompt)
    assert not buyer_todo.is_spoken_inquiry_send("待发询价")
    parsed = buyer_todo.parse_spoken_inquiry_send(prompt)
    assert parsed["board_row"] == 2
    assert parsed["batch"] == "M260063-P2"
    assert parsed["part"] == "B1-01"
    from_board = buyer_todo.send_arguments_from_board(prompt, rows)
    assert from_board["board_row"] == 2
    assert from_board["batch"] == "M260063-P2"


def test_send_identity_uses_station_not_same_part_on_other_tabs(monkeypatch):
    send_item = {
        "inquiryId": 160,
        "station": "inquiry_send",
        "stationLabel": "待发询价",
        "moldFamily": "M260063",
        "moldBatch": "M260063-P4",
        "moldNo": "M260063-P4",
        "parts": [{"partNo": "B1-01"}],
        "partDetails": "B1-01 下托板（S-Z 1040×1290×30）",
    }
    quote_sz = {
        **send_item,
        "inquiryId": 11,
        "station": "buyer_quote",
        "stationLabel": "待采购填报价",
        "partDetails": "B1-01 下托板（S-Z 1040×1290×30）",
    }
    quote_wc = {
        **quote_sz,
        "inquiryId": 12,
        "partDetails": "B1-01 下托板（S-Z-WC 1040×1290×30）",
    }

    def fake_query(parsed):
        if parsed.get("station") == "inquiry_send":
            return [send_item]
        return [send_item, quote_sz, quote_wc]

    monkeypatch.setattr(buyer_todo, "query_items", fake_query)
    monkeypatch.setattr(buyer_todo, "_scan_items", lambda mold=None: [send_item, quote_sz, quote_wc])
    hit = buyer_todo.find_item_by_identity(
        mold="M260063",
        batch="M260063-P4",
        part="B1-01",
        require_inquiry=True,
        station="inquiry_send",
    )
    assert hit["inquiryId"] == 160
    leftover = buyer_todo.find_item_by_identity(
        mold="M260063",
        batch="M260063-P1",
        require_inquiry=True,
        station="inquiry_send",
    )
    assert leftover["inquiryId"] == 160
    try:
        buyer_todo.find_item_by_identity(mold="M260063", batch="M260063-P4", part="B1-01")
    except DomainError as error:
        assert error.code == "AMBIGUOUS"
        assert error.message.count("B1-01") >= 1
    else:
        raise AssertionError("expected AMBIGUOUS across stations")


def test_send_lookup_ignores_same_batch_fill_quote_rows(monkeypatch):
    send_item = {
        "inquiryId": 160,
        "station": "inquiry_send",
        "stationLabel": "待发询价",
        "outsourceType": "part",
        "outsourceTypeLabel": "零件委外",
        "moldNo": "M260063-P5",
        "moldFamily": "M260063",
        "moldBatch": "M260063-P5",
        "orderNo": "",
        "parts": [{"partNo": "B1-01"}],
        "partDetails": "B1-01 下托板",
        "invitations": [{
            "supplierId": 22,
            "supplierCode": "HX001",
            "supplierName": "青岛和兴金属制品有限公司",
        }],
    }
    quote_item = {
        **send_item,
        "inquiryId": 11,
        "station": "buyer_quote",
        "stationLabel": "待采购填报价",
        "moldNo": "M260063-P4",
        "moldBatch": "M260063-P4",
        "invitations": [],
    }

    def fake_query(parsed):
        if parsed.get("station") == "inquiry_send":
            return [send_item]
        return [quote_item, {**quote_item, "inquiryId": 12}, send_item]

    monkeypatch.setattr(buyer_todo, "query_items", fake_query)
    monkeypatch.setattr(buyer_todo, "_scan_items", lambda mold=None: [quote_item, {**quote_item, "inquiryId": 12}, send_item])
    monkeypatch.setattr(buyer_todo, "attach_match_candidates", lambda item: item)
    data = erp_outsource_buyer_tools.parse(erp_outsource_buyer_tools.SEND_TOOL, {
        "mold": "M260063",
        "batch": "M260063-P5",
        "part": "B1-01",
        "board_row": 3,
    })
    item, display = erp_outsource_buyer_tools.preview(erp_outsource_buyer_tools.SEND_TOOL, data)
    assert item["inquiryId"] == 160
    assert display["当前分站"] == "待发询价"
    assert display["行号"] == "NO.3"
    wrong_row = erp_outsource_buyer_tools.parse(erp_outsource_buyer_tools.SEND_TOOL, {
        "mold": "M260063",
        "batch": "M260063-P5",
        "board_row": 1,
    })
    try:
        erp_outsource_buyer_tools.preview(erp_outsource_buyer_tools.SEND_TOOL, wrong_row)
    except DomainError as error:
        assert error.code == "STATE_BLOCKED"
        assert "NO.1" in error.message
    else:
        raise AssertionError("NO.1 is a fill-quote row and must not be sent")
    stale = erp_outsource_buyer_tools.parse(erp_outsource_buyer_tools.SEND_TOOL, {
        "mold": "M260063",
        "batch": "M260063-P4",
        "part": "B1-01",
    })
    stale_item, _display = erp_outsource_buyer_tools.preview(erp_outsource_buyer_tools.SEND_TOOL, stale)
    assert stale_item["inquiryId"] == 160


def test_buyer_inquiry_send_rejects_invented_supplier_and_lists_matched():
    item = {
        "invitations": [{
            "supplierId": 22,
            "supplierCode": "HX001",
            "supplierName": "青岛和兴金属制品有限公司",
        }],
    }
    ids, labels = buyer_todo.resolve_suppliers(item, ["和兴"])
    assert ids == [22]
    assert labels == ["青岛和兴金属制品有限公司"]
    try:
        buyer_todo.resolve_suppliers(item, ["华兴机械"])
    except DomainError as error:
        assert error.code == "NOT_FOUND"
        assert "华兴机械" in error.message
        assert "青岛和兴金属制品有限公司" in error.message
    else:
        raise AssertionError("expected NOT_FOUND")


def test_send_intent_keeps_prepared_card_when_live_match_http_fails(monkeypatch):
    item = {
        "inquiryId": 9,
        "station": "inquiry_send",
        "stationLabel": "待发询价",
        "outsourceType": "part",
        "outsourceTypeLabel": "零件委外",
        "moldNo": "M260063-P5",
        "moldFamily": "M260063",
        "moldBatch": "M260063-P5",
        "projectId": 4367,
        "partDetails": "B1-01 下托板",
        "invitations": [{
            "supplierId": 22,
            "supplierCode": "HX001",
            "supplierName": "青岛和兴金属制品有限公司",
        }],
    }
    monkeypatch.setattr(buyer_todo, "find_item_by_identity", lambda **kwargs: item)
    data = erp_outsource_buyer_tools.parse(erp_outsource_buyer_tools.SEND_TOOL, {"batch": "M260063-P5"})
    _, display = erp_outsource_buyer_tools.preview(erp_outsource_buyer_tools.SEND_TOOL, data)
    stored = {
        "kind": "erp_outsource_inquiry_send",
        "input": {"batch": "M260063-P5"},
        "display": display,
        "resolved_supplier_ids": [22],
    }
    monkeypatch.setattr(buyer_todo, "find_item_by_identity", lambda **kwargs: {**item, "invitations": []})
    monkeypatch.setattr(buyer_todo, "attach_match_candidates", lambda row: row)
    monkeypatch.setattr(erp_outsource_buyer_tools, "source", lambda db, user, step_id: stored)
    from domain_packs.mold.ports.bpm import content_hash

    proposal, key, parsed = erp_outsource_buyer_tools.validate_intent(None, Admin(), {
        "step_id": "step-1",
        "proposal_hash": content_hash(stored),
    })
    assert key == erp_outsource_buyer_tools.SEND_TOOL
    assert proposal["display"]["加工商"] == "青岛和兴金属制品有限公司"
    assert parsed.batch == "M260063-P5"


def test_send_confirm_uses_locked_supplier_ids_when_live_match_empty(monkeypatch):
    item = {
        "inquiryId": 160,
        "station": "inquiry_send",
        "stationLabel": "待发询价",
        "outsourceType": "part",
        "outsourceTypeLabel": "零件委外",
        "moldNo": "M260063-P5",
        "moldFamily": "M260063",
        "moldBatch": "M260063-P5",
        "orderNo": "",
        "partDetails": "B1-01 下托板",
        "invitations": [],
    }
    stored = {
        "kind": "erp_outsource_inquiry_send",
        "input": {"batch": "M260063-P5"},
        "display": {"操作": "向选定加工商发出询价", "加工商": "青岛和兴金属制品有限公司"},
        "resolved_supplier_ids": [921159],
    }
    captured = {}
    monkeypatch.setattr(buyer_todo, "find_item_by_identity", lambda **kwargs: item)
    monkeypatch.setattr(buyer_todo, "attach_match_candidates", lambda row: row)
    monkeypatch.setattr(erp_outsource_buyer_tools, "source", lambda db, user, step_id: stored)
    monkeypatch.setattr(
        erp_outsource_buyer_tools,
        "post_erp",
        lambda *args, **kwargs: captured.update({"path": args[2], "body": args[3]}) or {"code": 200},
    )
    from domain_packs.mold.ports.bpm import content_hash

    result = erp_outsource_buyer_tools.confirm(None, Admin(), {
        "step_id": "step-1",
        "proposal_hash": content_hash(stored),
        "_intent_id": "intent-1",
    })
    assert result["status"] == "CONFIRMED"
    assert result["action"] == "inquiry_send"
    assert captured["path"] == "entrust/inquiry/160/send"
    assert captured["body"] == {"supplier_ids": [921159]}


def test_spoken_final_deal_locks_full_table_row(monkeypatch):
    rows = [
        {
            "inquiryId": 11,
            "station": "buyer_quote",
            "stationLabel": "待采购填报价",
            "moldFamily": "M260063",
            "moldBatch": "M260063-P4",
        },
        {
            "inquiryId": 177,
            "station": "place_order",
            "stationLabel": "待填成交价",
            "moldFamily": "M260063",
            "moldBatch": "M260063-P2",
            "moldNo": "M260063-P2",
            "parts": [{"partNo": "B1-01"}],
            "autoAcceptMaxAmount": 55555,
            "invitations": [{
                "supplierName": "青岛和兴嘉业金属制品有限公司",
                "status": "quoted",
                "quoteAmount": 66666,
                "quoteId": 88,
            }],
        },
    ]
    monkeypatch.setattr(buyer_todo, "query_items", lambda parsed: rows)
    prompt = "NO.2成交价填写50000"
    assert buyer_todo.is_spoken_final_deal(prompt)
    assert not buyer_todo.is_spoken_buyer_quote(prompt)
    parsed = buyer_todo.parse_spoken_final_deal(prompt)
    assert parsed["board_row"] == 2
    assert parsed["batch"] == "M260063-P2"
    assert parsed["final_deal_amount"] == 50000
    assert parsed["quotation_id"] == 88
    from_board = buyer_todo.deal_arguments_from_board(prompt, rows)
    assert from_board["board_row"] == 2
    assert from_board["final_deal_amount"] == 50000
    colon = buyer_todo.parse_spoken_final_deal("NO.2填成交价:50000")
    assert colon["board_row"] == 2
    assert colon["final_deal_amount"] == 50000
    written = buyer_todo.parse_spoken_final_deal("NO.2填写成交价:50000")
    assert written["board_row"] == 2
    assert written["final_deal_amount"] == 50000
    bare = buyer_todo.parse_spoken_final_deal("填写成交价:50000")
    assert bare["final_deal_amount"] == 50000


def test_spoken_final_deal_locks_row_without_live_quote_id(monkeypatch):
    monkeypatch.setattr(buyer_todo, "item_from_visible_board_row", lambda row_no: None)
    parsed = buyer_todo.parse_spoken_final_deal("NO.2填成交价:50000")
    assert parsed == {"board_row": 2, "final_deal_amount": 50000.0}
    monkeypatch.setattr(buyer_todo, "find_unique_station_item", lambda station: None)
    bare = buyer_todo.parse_spoken_final_deal("填写成交价:50000")
    assert bare == {"final_deal_amount": 50000.0}


def test_board_row_final_deal_prepares_confirmation_card(monkeypatch):
    item = {
        "inquiryId": 177,
        "station": "place_order",
        "stationLabel": "待填成交价",
        "outsourceType": "part",
        "outsourceTypeLabel": "零件委外",
        "moldFamily": "M260063",
        "moldBatch": "M260063-P2",
        "moldNo": "M260063-P2",
        "partDetails": "B1-01 下托板",
        "supplierQuotes": "66666.00（超上限）",
        "pendingQuoteSuppliers": "青岛和兴嘉业金属制品有限公司",
        "autoAcceptMaxAmount": 55555,
        "invitations": [{
            "supplierName": "青岛和兴嘉业金属制品有限公司",
            "status": "quoted",
            "quoteAmount": 66666,
            "quoteId": 88,
        }],
    }
    monkeypatch.setattr(buyer_todo, "item_from_visible_board_row", lambda row: item if row == 2 else None)
    result = erp_outsource_buyer_tools.execute_tool(None, Admin(), erp_outsource_buyer_tools.DEAL_TOOL, {
        "board_row": 2,
        "final_deal_amount": 50000,
    })
    display = result["proposal"]["display"]
    assert result["proposal"]["kind"] == "erp_outsource_final_deal"
    assert display["成交价"] == 50000
    assert display["行号"] == "NO.2"
    assert "青岛和兴嘉业金属制品有限公司" in display["加工商"]
    assert result["proposal"]["input"]["quotation_id"] == 88
    assert result["proposal"]["input"]["board_row"] == 2
