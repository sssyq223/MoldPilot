import json

from domain_packs.mold import tool_gateway
from domain_packs.mold.erp.procurement import erp_outsource_scope
from domain_packs.mold.ports.errors import DomainError
from domain_packs.mold.tools.erp.procurement import erp_outsource_query_tools
from domain_packs.mold.tools.erp.procurement.outsource_queries import buyer_todo, fulfillment_gap, timeline, unoutsourced


def test_outsource_query_skills_are_registered_under_erp_procurement():
    paths = tool_gateway.skill_paths()
    assert list(erp_outsource_query_tools.SKILL_SPECS) == [
        "outsource_followup_query",
        "outsource_processor_query",
    ]
    spec = erp_outsource_query_tools.SKILL_SPECS["outsource_followup_query"]
    assert spec["tools"] == [erp_outsource_query_tools.BOARD_TOOL]
    assert spec["optional_tools"] == [erp_outsource_query_tools.PROGRESS_TOOL]
    assert spec["activation_tools"] == list(erp_outsource_query_tools.BUYER_TOOL_KEYS)
    processor = erp_outsource_query_tools.SKILL_SPECS["outsource_processor_query"]
    assert processor["tools"] == [erp_outsource_query_tools.PROCESSOR_BOARD_TOOL]
    assert processor["activation_tools"] == list(erp_outsource_query_tools.PROCESSOR_TOOL_KEYS)
    for key in erp_outsource_query_tools.SKILL_SPECS:
        assert key in tool_gateway.SKILLS
        assert paths[key]["layer"] == "erp"
        assert paths[key]["domain"] == "procurement"
        assert paths[key]["path"].is_file()
    for retired in (
        "buyer_todo_station_query",
        "unoutsourced_part_query",
        "outsource_fulfillment_gap",
        "outsource_timeline_query",
        "outsource_quote_compare",
    ):
        assert retired not in tool_gateway.SKILLS
        assert retired not in paths
    for retired in erp_outsource_query_tools.RETIRED_TOOLS:
        assert retired not in tool_gateway.TOOLS


def test_followup_skill_catalog_keeps_query_tools_optional():
    item = tool_gateway.capability_descriptor(
        "SKILL",
        "outsource_followup_query",
        tool_gateway.SKILLS["outsource_followup_query"],
    )
    assert item["name"] == "委外跟单进度查询"
    assert item["department"] == "purchase"
    assert item["dependencies"] == [erp_outsource_query_tools.BOARD_TOOL]
    assert item["optional_dependencies"] == [erp_outsource_query_tools.PROGRESS_TOOL]
    assert item["activation_dependencies"] == list(erp_outsource_query_tools.BUYER_TOOL_KEYS)


def test_outsource_query_tools_use_role_read_permission():
    for key in erp_outsource_query_tools.BUYER_TOOL_KEYS:
        assert erp_outsource_query_tools.TOOL_SPECS[key]["permission"] == "erp_outsource_buyer.read"
        assert tool_gateway.CAPABILITY_DEPARTMENTS[key] == "purchase"
    for key in erp_outsource_query_tools.PROCESSOR_TOOL_KEYS:
        assert erp_outsource_query_tools.TOOL_SPECS[key]["permission"] == "erp_outsource_processor.read"
        assert tool_gateway.CAPABILITY_DEPARTMENTS[key] == "processor"
        assert key in tool_gateway.TOOLS


def test_board_input_accepts_order_no():
    data = erp_outsource_query_tools.FollowupBoardInput.model_validate({
        "order_no": "EO-260924-DJ9X",
        "mold": "M260063",
        "batch": "M260063-P2",
        "todo_tab": "accept",
        "reason": "ignored extra",
    })
    assert data.order_no == "EO-260924-DJ9X"
    assert data.mold == "M260063"
    assert data.batch == "M260063-P2"
    assert data.todo_tab == "accept"


def test_board_question_extracts_eo_order_no():
    parsed = buyer_todo.parse_question(
        "EO-260924-DJ9X M260063 M260063-P2 订单和模具号是这个的帮我接单"
    )
    assert parsed["order_no"] == "EO-260924-DJ9X"
    assert parsed["mold_batch"] == "M260063-P2"
    assert parsed["station"] == "accept"


def test_progress_input_accepts_empty_order_no():
    data = erp_outsource_query_tools.FollowupProgressInput.model_validate({
        "mold": "M260063",
        "batch": "M260063-P1",
        "orderNo": "",
        "reason": "ignored extra",
    })
    assert data.mold == "M260063"
    assert data.batch == "M260063-P1"
    assert data.order_no is None


def test_buyer_todo_question_treats_spoken_accept_as_station():
    parsed = buyer_todo.parse_question("需要我接单的工单有几个")
    assert parsed["station"] == "accept"


def test_model_context_keeps_board_short():
    payload = {
        "summary": "ERP 委外待办 的零件/工序委外全部分站共 10 条。\n- 待接单：10 条\n" + "\n".join(
            f"- 待接单 工序委外 EO-{index} M260063 M260063-P1 零件很长很长很长"
            for index in range(10)
        ),
        "counts": {"待接单": 10, "待报价": 0},
        "items": [{
            "orderNo": f"EO-{index}",
            "moldFamily": "M260063",
            "moldBatch": "M260063-P1",
            "stationLabel": "待接单",
            "outsourceTypeLabel": "工序委外",
            "partDetails": "PH-01 上夹板（很长很长的规格说明）",
            "ourQuoteAmount": 300,
            "finalDealAmount": 400,
        } for index in range(10)],
    }
    context = erp_outsource_query_tools._model_context(payload)
    assert context["item_count"] == 10
    assert len(context["items"]) == 8
    assert "ourQuote" not in context["items"][0]
    assert "\n-" not in context["summary"]
    assert len(json.dumps(context, ensure_ascii=False)) < 1200


def test_buyer_todo_question_picks_station():
    parsed = buyer_todo.parse_question("M260063-P4 现在有哪些待填价")
    assert parsed["station"] == "buyer_quote"
    assert parsed["mold_batch"] == "M260063-P4"


def test_buyer_todo_question_picks_operation_type():
    parsed = buyer_todo.parse_question("查一下 M260063 工序委外待报价")
    assert parsed["outsource_type"] == "operation"
    assert parsed["station"] == "supplier_quote"
    assert parsed["mold_family"] == "M260063"


def test_buyer_todo_item_hides_project_and_shows_pending_quoters():
    item = buyer_todo.item_from_row(
        {
            "outsource_type": "part",
            "mold_no": "M260063-P1",
            "our_quote_amount": 320,
            "auto_accept_max_amount": 400,
            "reference_total": 276.1,
            "final_deal_amount": None,
            "parts": [
                {"partNo": "PU-06", "partName": "上垫板", "qty": 1, "processName": "S-M"},
            ],
            "invitations": [
                {"supplierName": "铂锐", "status": "sent", "quoteAmount": None},
                {"supplierName": "精工", "status": "quoted", "quoteAmount": 330},
            ],
        },
        "supplier_quote",
    )
    assert "projectNo" not in item
    assert item["orderNo"] == ""
    assert item["moldFamily"] == "M260063"
    assert item["moldBatch"] == "M260063-P1"
    assert item["stationLabel"] == "待报价"
    assert item["outsourceTypeLabel"] == "零件委外"
    assert "PU-06" in item["partDetails"]
    assert item["pendingQuoteSuppliers"] == "铂锐"
    assert "精工 330" in item["supplierQuotes"]
    assert item["ourQuoteAmount"] == 320


def test_buyer_todo_classify_keeps_operation_on_inquiry_path():
    assert buyer_todo.classify({
        "awarded_stage": "",
        "awarded_status": "",
        "outsource_type": "operation",
        "inquiry_id": 9,
        "inquiry_status": "sent",
        "our_quote_amount": None,
        "auto_accept_max_amount": None,
        "final_deal_amount": None,
        "invitation_count": 2,
        "quoted_count": 0,
    }) == "supplier_quote"


def test_listing_question_does_not_keep_model_station_from_run_prompt():
    class Run:
        prompt = "现在有几个委外订单"

    parsed = erp_outsource_query_tools.INPUT_MODELS[erp_outsource_query_tools.BOARD_TOOL].model_validate({
        "mold": "M260063",
        "todo_tab": "buyer_quote",
    })
    scoped = erp_outsource_query_tools._scope(
        parsed,
        buyer_todo.parse_question(parsed.question or Run.prompt),
        spoken=parsed.question or Run.prompt,
    )
    assert scoped["station"] == ""
    assert scoped["mold_family"] == "M260063"


def test_listing_question_does_not_keep_model_station():
    parsed = erp_outsource_query_tools.INPUT_MODELS[erp_outsource_query_tools.BOARD_TOOL].model_validate({
        "question": "现在有几个委外订单",
        "mold": "M260063",
        "todo_tab": "buyer_quote",
    })
    scoped = erp_outsource_query_tools._scope(parsed, buyer_todo.parse_question(parsed.question))
    assert scoped["station"] == ""
    assert scoped["mold_family"] == "M260063"


def test_buyer_todo_question_picks_approval_station():
    parsed = buyer_todo.parse_question("查看现在委外项目中有哪些项目在审批中")
    assert parsed["station"] == "order_approval"
    assert parsed["mold_family"] == ""
    assert parsed["mold_batch"] == ""


def test_buyer_todo_classify_splits_approval_from_place_order():
    assert buyer_todo.classify({
        "awarded_stage": "pending_approval",
        "awarded_status": "open",
        "outsource_type": "part",
        "inquiry_id": 1,
        "inquiry_status": "awarded",
        "our_quote_amount": 3800,
        "auto_accept_max_amount": 8888,
        "final_deal_amount": 7888,
        "invitation_count": 1,
        "quoted_count": 1,
    }) == "order_approval"
    assert buyer_todo.classify({
        "awarded_stage": "",
        "awarded_status": "",
        "outsource_type": "part",
        "inquiry_id": 1,
        "inquiry_status": "quoted",
        "our_quote_amount": 666,
        "auto_accept_max_amount": 66666,
        "final_deal_amount": None,
        "invitation_count": 1,
        "quoted_count": 1,
    }) == "place_order"
    assert buyer_todo.classify({
        "awarded_stage": "material_receiving",
        "awarded_status": "open",
        "outsource_type": "part",
        "inquiry_id": 1,
        "inquiry_status": "awarded",
        "our_quote_amount": 4599,
        "auto_accept_max_amount": 7777,
        "final_deal_amount": None,
        "invitation_count": 1,
        "quoted_count": 1,
    }) is None


def test_buyer_todo_execute_reads_run_prompt_when_question_omitted():
    class Run:
        prompt = "查看现在委外项目中有哪些项目在审批中"

    parsed = erp_outsource_query_tools.INPUT_MODELS[erp_outsource_query_tools.BOARD_TOOL].model_validate({})
    scoped = erp_outsource_query_tools._scope(
        parsed,
        buyer_todo.parse_question(parsed.question or Run.prompt),
    )
    assert scoped["station"] == "order_approval"


def test_all_board_question_does_not_need_mold():
    assert erp_outsource_query_tools._asks_full_board("你把现在所有的委外项目展示出来")
    assert erp_outsource_query_tools._asks_full_board("把全部委外待办拉出来")
    assert erp_outsource_query_tools._asks_full_board("现在有委外单子吗")
    assert erp_outsource_query_tools._asks_full_board("现在有没有委外")
    assert not erp_outsource_query_tools._asks_full_board("这一票到哪一步了")


def test_progress_without_mold_returns_need_mold_code():
    class Admin:
        super_admin = True

    result = erp_outsource_query_tools.execute_tool(
        None, Admin(), erp_outsource_query_tools.PROGRESS_TOOL, {"question": "这一票到哪一步了"},
    )
    assert result["data"]["status"] == "NEED_MOLD_CODE"
    assert "模具号" in result["data"]["summary"]


def test_mold_batch_counts_as_mold_scope():
    parsed = erp_outsource_query_tools.INPUT_MODELS[erp_outsource_query_tools.BOARD_TOOL].model_validate({
        "question": "M260063-P4 现在有哪些待填价",
    })
    scoped = erp_outsource_query_tools._scope(parsed, buyer_todo.parse_question(parsed.question))
    assert erp_outsource_query_tools._has_mold_scope(scoped)
    assert scoped["mold_batch"] == "M260063-P4"


def test_unoutsourced_present_parts_without_database():
    from decimal import Decimal
    import json

    payload = unoutsourced.present(
        {"mode": "parts", "mold_family": "M260063", "mold_batch": "", "order_no": ""},
        [{
            "mold_no": "M260063-P4",
            "part_no": "P-1",
            "part_name": "型芯",
            "order_no": "WO1",
            "project_no": "E1",
            "qty": Decimal("2"),
            "total_amount": Decimal("1706.13"),
        }],
    )
    assert payload["parts"][0]["partNo"] == "P-1"
    assert payload["parts"][0]["qty"] == 2
    assert payload["parts"][0]["totalAmount"] == 1706.13
    assert payload["total"] == 1
    assert payload["batchCounts"]["M260063-P4"] == 1
    assert "1 个零件未委外" in payload["summary"]
    assert "待填价" in payload["definition"]
    json.dumps(payload)


def test_fulfillment_gap_combined_question_does_not_lock_to_overdue():
    parsed = fulfillment_gap.parse_question("M260063 还有哪些未发料 / 还没回来 / 超期")
    assert parsed["gap"] == ""
    assert parsed["mold_family"] == "M260063"
    assert fulfillment_gap.parse_question("M260063 还有哪些未发料")["gap"] == "material"


def test_outsource_scope_ignores_truncated_batch_fragment():
    parsed = erp_outsource_query_tools.INPUT_MODELS[erp_outsource_query_tools.PROGRESS_TOOL].model_validate({
        "question": "M260063-P2哪家报价最低，有没有超上限",
        "mold": "P2",
    })
    scoped = erp_outsource_query_tools._scope(parsed, {
        "mold_family": "",
        "mold_batch": "M260063-P2",
        "project_no": "",
    })
    assert scoped["mold_batch"] == "M260063-P2"
    assert scoped["mold_family"] == ""


def test_timeline_steps_use_chinese_states():
    steps = timeline.build_steps({
        "moldBatch": "M260063-P2",
        "work": {"work_order_count": 7},
        "intent": {"active_count": 1},
        "projects": [{"project_no": "ENT-1", "status": "confirmed"}],
        "inquiry": {"project_no": "ENT-1", "status": "awarded", "our_quote_amount": 3800, "auto_accept_max_amount": 8888},
        "quotes": {"invitation_count": 1, "quoted_count": 1},
        "orders": [{"order_no": "EO-1", "status": "open", "stage": "pending_approval", "partner_name": "铂锐"}],
        "material": {"shipped_count": 0, "pending_count": 0},
        "product": {"shipped_qty": 0, "received_qty": 0},
    })
    assert [step["state"] for step in steps] == ["已完成", "已完成", "已完成", "已完成", "已完成", "进行中", "未开始", "未开始"]
    assert "审批中" in steps[5]["detail"]


def test_unoutsourced_question_picks_batch_and_part():
    parsed = unoutsourced.parse_question("帮我查一下M260063-P2的PU-06的详细信息")
    assert parsed["mode"] == "parts"
    assert parsed["mold_batch"] == "M260063-P2"
    assert parsed["part_no"] == "PU-06"


def test_processor_item_hides_internal_prices_and_other_suppliers():
    visible = {
        "station": "accept",
        "supplierCode": "SUP000001",
        "supplierName": "铂锐",
        "pendingQuoteSuppliers": "铂锐",
        "supplierQuotes": "铂锐 330",
        "referenceTotal": 276.1,
        "ourQuoteAmount": 320,
        "autoAcceptMaxAmount": 400,
        "finalDealAmount": 330,
    }
    hidden = {
        "station": "accept",
        "supplierCode": "SUP000002",
        "supplierName": "精工",
        "pendingQuoteSuppliers": "精工",
        "supplierQuotes": "精工 410",
        "ourQuoteAmount": 320,
    }
    assert buyer_todo.item_mentions_processor(visible, ["SUP000001"])
    assert not buyer_todo.item_mentions_processor(hidden, ["SUP000001"])
    stripped = buyer_todo.strip_internal_prices(visible)
    assert stripped["ourQuoteAmount"] is None
    assert stripped["autoAcceptMaxAmount"] is None
    assert stripped["referenceTotal"] is None
    assert stripped["finalDealAmount"] is None


def test_awarded_processor_scope_ignores_historical_invitations():
    item = {
        "station": "accept",
        "supplierCode": "SUP000002",
        "invitations": [
            {"supplierCode": "SUP000001", "status": "quoted"},
            {"supplierCode": "SUP000002", "status": "quoted"},
        ],
    }
    assert not buyer_todo.item_mentions_processor(item, ["SUP000001"])
    assert buyer_todo.item_mentions_processor(item, ["SUP000002"])


def test_awarded_station_without_order_row_falls_back_to_invitations():
    # classify() can return “accept” / “order_approval” from the inquiry status
    # before ERP has an awarded order row; the quoted supplier must still see it.
    item = {
        "station": "order_approval",
        "supplierCode": "",
        "supplierId": None,
        "invitations": [{"supplierCode": "SUP000001", "status": "quoted"}],
    }
    assert buyer_todo.item_mentions_processor(item, ["SUP000001"])
    assert not buyer_todo.item_mentions_processor(item, ["SUP000002"])


def test_processor_identifier_match_is_exact_not_substring():
    invitation = {"supplierCode": "SUP0000010", "supplierId": 110}
    assert not buyer_todo.invitation_matches_processor(invitation, ["SUP000001"])


def test_processor_empty_tokens_hide_all_rows():
    payload = buyer_todo.present(
        {"station": "", "mold_family": "", "mold_batch": "", "project_no": "", "outsource_type": ""},
        [buyer_todo.strip_internal_prices(item) for item in [{
            "station": "supplier_quote",
            "stationLabel": "待报价",
            "outsourceTypeLabel": "零件委外",
            "moldNo": "M260063-P1",
            "partDetails": "PU-06",
            "supplierName": "铂锐",
        }] if buyer_todo.item_mentions_processor(item, [])],
    )
    assert payload["items"] == []
    assert "0 条" in payload["summary"] or "没有查到" in payload["summary"]


def test_buyer_followup_board_query_does_not_require_erp_identity(monkeypatch):
    class Buyer:
        id = "xuguili"
        super_admin = False

    monkeypatch.setattr(erp_outsource_query_tools, "require_allow", lambda *args, **kwargs: None)
    monkeypatch.setattr(
        buyer_todo,
        "run",
        lambda scoped, processor_tokens=None: {"status": "OK", "summary": "20 条委外待办", "items": [{
            "orderNo": "EO-260923-0KKR",
            "moldFamily": "M260063",
            "moldBatch": "M260063-P1",
            "stationLabel": "待接单",
        }]},
    )
    result = erp_outsource_query_tools.execute_tool(
        object(),
        Buyer(),
        erp_outsource_query_tools.BOARD_TOOL,
        {"question": "现在有委外单子吗"},
    )
    assert result["data"]["items"][0]["orderNo"] == "EO-260923-0KKR"
    assert result["role_lens"] == "buyer"
    assert result["model_context"]["item_count"] == 1
    assert result["model_context"]["items"][0]["orderNo"] == "EO-260923-0KKR"
    assert result["model_context"]["items"][0]["mold"] == "M260063"
    assert result["model_context"]["items"][0]["batch"] == "M260063-P1"
    assert "inquiryId" not in result["model_context"]["items"][0]


def test_followup_query_host_auto_invokes_empty_board():
    spec = erp_outsource_query_tools.SKILL_SPECS["outsource_followup_query"]
    schema = erp_outsource_query_tools.tool_schema(erp_outsource_query_tools.BOARD_TOOL)
    assert spec["host_auto_invoke_empty_arguments"] is True
    assert spec["tools"] == [erp_outsource_query_tools.BOARD_TOOL]
    assert schema["function"]["parameters"].get("required", []) == []


def test_buyer_scope_uses_erp_identity_and_outsource_category(monkeypatch):
    class User:
        id = "buyer"
        super_admin = False

    class Identity:
        erp_user_id = "42"

    class DB:
        @staticmethod
        def get(model, key):
            return Identity()

    captured = {}
    monkeypatch.setattr(
        erp_outsource_scope,
        "fetch_one",
        lambda sql, params: captured.update(params) or {"id": 1},
    )
    erp_outsource_scope.require_outsource_buyer_scope(DB(), User())
    assert captured == {"erp_user_id": 42}
    assert "purchase_buyer_scope" in erp_outsource_scope.OUTSOURCE_BUYER_SCOPE_SQL


def test_buyer_scope_without_active_erp_scope_is_forbidden(monkeypatch):
    class User:
        id = "buyer"
        super_admin = False

    class DB:
        @staticmethod
        def get(model, key):
            return None

    monkeypatch.setattr(erp_outsource_scope, "fetch_one", lambda sql, params: None)
    try:
        erp_outsource_scope.require_outsource_buyer_scope(DB(), User())
    except DomainError as error:
        assert error.code == "FORBIDDEN"
    else:
        raise AssertionError("expected FORBIDDEN")
