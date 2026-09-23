from agent_core import harness
from domain_packs.mold import tool_gateway
from domain_packs.mold.tools.erp.procurement import (
    erp_outsource_approval_tools,
    erp_outsource_buyer_tools,
    erp_outsource_processor_fulfillment_tools,
    erp_outsource_processor_ship_tools,
    erp_outsource_processor_tools,
    erp_outsource_quality_tools,
    erp_outsource_query_tools,
    erp_outsource_warehouse_inbound_tools,
    erp_outsource_warehouse_tools,
)
from domain_packs.mold.tools.erp.procurement.outsource_queries import approval_todo, processor_fulfillment


MIXED_QUERY_SKILLS = (
    (erp_outsource_approval_tools, "outsource_approval_ops"),
    (erp_outsource_processor_fulfillment_tools, "outsource_processor_fulfillment"),
    (erp_outsource_processor_ship_tools, "outsource_processor_product_ship"),
    (erp_outsource_warehouse_tools, "outsource_warehouse_ops"),
    (erp_outsource_warehouse_inbound_tools, "outsource_warehouse_inbound"),
    (erp_outsource_quality_tools, "outsource_quality_ops"),
)


def _auto(module, key):
    return set(module.SKILL_SPECS[key]["auto_activation_queries"])


def test_mixed_outsource_skills_activate_query_tools_only():
    for module, key in MIXED_QUERY_SKILLS:
        spec = module.SKILL_SPECS[key]
        assert spec["activation_tools"] == list(module.QUERY_TOOL_KEYS)
        assert spec["activation_tools"]
        assert all(name.startswith("query_") for name in spec["activation_tools"])
        for name in spec.get("optional_tools") or []:
            if name.startswith("prepare_"):
                assert name not in spec["activation_tools"]
        runtime = tool_gateway.SKILLS[key]
        assert runtime["activation_tools"] == list(module.QUERY_TOOL_KEYS)


def test_ops_skills_activate_query_before_prepare():
    buyer = erp_outsource_buyer_tools.SKILL_SPECS["outsource_buyer_ops"]
    processor = erp_outsource_processor_tools.SKILL_SPECS["outsource_processor_ops"]
    assert buyer["activation_tools"] == ["query_erp_outsource_followup_board"]
    assert processor["activation_tools"] == ["query_erp_outsource_processor_board"]
    assert all(name.startswith("prepare_") for name in buyer["optional_tools"])
    assert all(name.startswith("prepare_") for name in processor["optional_tools"])
    assert buyer["suppress_tool_search_on_auto_activation"] is False
    assert processor["suppress_tool_search_on_auto_activation"] is False


def test_same_account_skills_do_not_share_auto_aliases():
    followup = _auto(erp_outsource_query_tools, "outsource_followup_query")
    buyer_ops = _auto(erp_outsource_buyer_tools, "outsource_buyer_ops")
    approval = _auto(erp_outsource_approval_tools, "outsource_approval_ops")
    processor_query = _auto(erp_outsource_query_tools, "outsource_processor_query")
    processor_ops = _auto(erp_outsource_processor_tools, "outsource_processor_ops")
    fulfillment = _auto(erp_outsource_processor_fulfillment_tools, "outsource_processor_fulfillment")
    product_ship = _auto(erp_outsource_processor_ship_tools, "outsource_processor_product_ship")
    warehouse = _auto(erp_outsource_warehouse_tools, "outsource_warehouse_ops")
    inbound = _auto(erp_outsource_warehouse_inbound_tools, "outsource_warehouse_inbound")

    assert not followup & buyer_ops
    assert "委外审批" not in followup
    assert "委外审批" in approval
    assert not processor_query & processor_ops
    assert not fulfillment & product_ship
    assert not warehouse & inbound


def test_query_aliases_catch_count_questions_without_bare_ops_verbs():
    processor_query = _auto(erp_outsource_query_tools, "outsource_processor_query")
    processor_ops = _auto(erp_outsource_processor_tools, "outsource_processor_ops")
    buyer_ops = _auto(erp_outsource_buyer_tools, "outsource_buyer_ops")
    followup = _auto(erp_outsource_query_tools, "outsource_followup_query")

    assert {"有几个", "有没有", "待报价", "待接单"} <= processor_query
    assert {"有几个", "待报价", "待接单"} <= followup
    assert {"有几个", "有没有"} <= _auto(erp_outsource_quality_tools, "outsource_quality_ops")
    assert not {"报价", "接单", "拒单"} & processor_ops
    assert not {"待填价", "待下单", "填报价"} & buyer_ops


def test_approval_and_fulfillment_next_action():
    arrival = approval_todo.item_from_row({
        "task_id": 12,
        "node_name": "采购主管审批",
        "mold_no": "M260063-P1",
    })
    assert arrival["nextAction"]["action"] == "pass_or_reject"
    assert arrival["nextAction"]["taskId"] == 12

    receipt = processor_fulfillment.receipt_item({
        "shipment_id": 8,
        "outsource_type": "part",
        "pending_lines": [{"lineId": 1}],
    })
    assert receipt["nextAction"]["action"] == "receipt"
    wait = processor_fulfillment.wait_item({"outsource_type": "operation", "order_id": 3})
    assert wait["nextAction"]["action"] == "wait"
    product = processor_fulfillment.product_item({
        "order_id": 3,
        "outsource_type": "part",
        "parts": [{"orderPartId": 1, "remainQty": 2, "isEndOperation": True}],
    })
    assert product["nextAction"]["action"] == "product_ship"


def test_outsource_operation_phrases_are_formal_actions():
    phrases = (
        "填我方报价", "发询价", "填成交价", "重选加工商",
        "我要报价", "我要接单", "确认接单", "拒绝接单", "接这单",
        "确认发料", "确认备料", "确认原料发货", "确认收料", "确认来料",
        "发成品", "发半成品", "确认成品发货",
        "确认到货", "确认收货", "确认入库", "办入库",
        "领取质检", "判合格", "确认合格", "检验通过",
        "通过这单", "驳回这单",
        "待发询价的这单帮我发询价",
    )
    for phrase in phrases:
        assert harness._has_formal_action_intent(phrase), phrase
        assert harness._contains_any(phrase, harness.ALL_BUSINESS_OBJECT_HINTS), phrase


def test_outsource_count_questions_are_not_formal_actions():
    # Station nouns double as to-do board names.  A count or list question
    # must stay read-only, otherwise the Harness demands an operation receipt
    # and refuses to answer with the board.
    phrases = (
        "待报价有几个", "有没有待接单", "质检待办有几条", "查一下入库待办",
        "成品发货待办有几个", "备料完成的有哪些", "原料发货待办", "待发询价有几个",
        "质检合格的有几条", "检验合格了没有", "到货确认待办", "回厂入库待办有几个",
        "成品入库的有哪些", "待发成品有几个", "待领取质检有几个",
    )
    for phrase in phrases:
        assert not harness._has_formal_action_intent(phrase), phrase


def test_negated_outsource_operations_are_not_formal_actions():
    for phrase in ("不要确认接单", "不要发成品", "不确认入库", "不要判合格", "不要通过这单"):
        assert not harness._has_formal_action_intent(phrase), phrase
