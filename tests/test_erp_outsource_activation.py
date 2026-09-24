import json

from app.harness import run_loop
from agent_core import harness
from domain_packs.mold import tool_gateway
from tests.test_model_harness import Gateway, InspectingRepliesModel, context
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
        assert spec.get("host_auto_invoke_empty_arguments") is True


def test_ops_skills_activate_query_before_prepare():
    buyer = erp_outsource_buyer_tools.SKILL_SPECS["outsource_buyer_ops"]
    processor = erp_outsource_processor_tools.SKILL_SPECS["outsource_processor_ops"]
    assert buyer["activation_tools"] == ["query_erp_outsource_followup_board"]
    assert processor["activation_tools"] == ["query_erp_outsource_processor_board"]
    assert all(name.startswith("prepare_") for name in buyer["optional_tools"])
    assert all(name.startswith("prepare_") for name in processor["optional_tools"])
    assert buyer["suppress_tool_search_on_auto_activation"] is False
    assert processor["suppress_tool_search_on_auto_activation"] is False
    assert buyer.get("activation_route") == "authorized"
    assert processor.get("activation_route") == "authorized"


def test_all_outsource_skills_use_authorized_route():
    modules = (
        erp_outsource_query_tools,
        erp_outsource_buyer_tools,
        erp_outsource_approval_tools,
        erp_outsource_processor_tools,
        erp_outsource_processor_fulfillment_tools,
        erp_outsource_processor_ship_tools,
        erp_outsource_warehouse_tools,
        erp_outsource_warehouse_inbound_tools,
        erp_outsource_quality_tools,
    )
    for module in modules:
        for key, spec in module.SKILL_SPECS.items():
            assert spec.get("activation_route") == "authorized", key
            assert spec.get("suppress_tool_search_on_auto_activation") is False, key
            assert tool_gateway.SKILLS[key].get("activation_route") == "authorized", key


def test_formal_accept_ranks_processor_prepare_from_optional_catalog():
    board = "query_erp_outsource_processor_board"
    accept = "prepare_erp_outsource_processor_accept"
    all_tools = {
        board: {"function": {"name": board, "description": "查询本加工商委外待办"}},
        accept: {"function": {"name": accept, "description": "准备确认接单。必须已锁定 orderId，且当前分站是待接单。"}},
        "prepare_erp_outsource_processor_quote": {
            "function": {"name": "prepare_erp_outsource_processor_quote", "description": "准备提交本加工商报价"},
        },
        "prepare_erp_outsource_processor_reject": {
            "function": {"name": "prepare_erp_outsource_processor_reject", "description": "准备拒绝接单"},
        },
    }
    skills = [{
        "key": "outsource_processor_ops",
        "tools": [board],
        "optional_tools": list(erp_outsource_processor_tools.TOOL_KEYS),
        "activation_tools": [board],
        "auto_activation_queries": ["确认接单", "我要接单"],
    }]
    groups = harness._skill_tool_groups(skills, all_tools)
    assert groups and accept in groups[0]["tools"]
    selected = harness._rank_group_tools(
        "确认接单工单3141",
        groups[0],
        all_tools,
        action_intent=True,
        current_prompt="确认接单工单3141",
    )
    assert board in selected
    assert accept in selected
    listing = harness._rank_group_tools(
        "我的委外待办",
        groups[0],
        all_tools,
        action_intent=False,
        current_prompt="我的委外待办",
    )
    assert listing == [board]


def test_write_tools_follow_spoken_context_not_whitelist_verbs():
    assert harness._allows_write_tools("把PH-01这一笔的价钱写成400，上限600")
    assert harness._allows_write_tools("这一单按400和上限600填进去")
    assert harness._allows_write_tools("PH-01这一笔订单帮我填价格：400，上限是600")
    assert not harness._has_formal_action_intent("把PH-01这一笔的价钱写成400，上限600")
    assert not harness._allows_write_tools("待填价有几个")
    assert not harness._allows_write_tools("帮我看一下委外待办")
    assert not harness._allows_write_tools("只读查询 BROWSER-OUT-001 的资料交接，不要准备或执行任何操作。")


def test_spoken_fill_price_ranks_buyer_quote_prepare():
    board = "query_erp_outsource_followup_board"
    quote = "prepare_erp_outsource_buyer_quote"
    send = "prepare_erp_outsource_inquiry_send"
    all_tools = {
        board: {"function": {"name": board, "description": "查询委外采购待办"}},
        quote: {"function": {"name": quote, "description": "准备填写我方报价与直接接单上限"}},
        send: {"function": {"name": send, "description": "准备向选定加工商发出询价"}},
    }
    skills = [{
        "key": "outsource_buyer_ops",
        "tools": [board],
        "optional_tools": list(erp_outsource_buyer_tools.TOOL_KEYS),
        "activation_tools": [board],
        "auto_activation_queries": ["填价格", "帮我填报价"],
    }]
    groups = harness._skill_tool_groups(skills, all_tools)
    prompt = "PH-01这一笔订单帮我填价格：400，上限是600"
    selected = harness._rank_group_tools(
        prompt,
        groups[0],
        all_tools,
        action_intent=True,
        current_prompt=prompt,
    )
    assert board in selected
    assert quote in selected
    listing = harness._rank_group_tools(
        "待填价有几个",
        groups[0],
        all_tools,
        action_intent=False,
        current_prompt="待填价有几个",
    )
    assert listing == [board]


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
    assert {"填价格", "帮我填报价", "帮我填价格"} <= buyer_ops


def test_approval_and_fulfillment_next_action():
    arrival = approval_todo.item_from_row({
        "task_id": 12,
        "node_name": "采购主管审批",
        "mold_no": "M260063-P1",
    })
    assert arrival["nextAction"]["action"] == "pass_or_reject"
    assert arrival["nextAction"]["orderNo"] == ""
    assert "taskId" not in arrival["nextAction"]

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
    spoken_fill = ("填价格", "帮我填价格", "帮我填报价")
    phrases = (
        "填我方报价",
        "PH-01这一笔订单帮我填价格：400，上限是600",
        "发询价", "填成交价", "重选加工商",
        "我要报价", "我要接单", "确认接单", "拒绝接单", "接这单",
        "确认发料", "确认备料", "确认原料发货", "确认收料", "确认来料",
        "发成品", "发半成品", "确认成品发货",
        "确认到货", "确认收货", "确认入库", "办入库",
        "领取质检", "判合格", "确认合格", "检验通过",
        "通过这单", "驳回这单",
        "待发询价的这单帮我发询价",
        *spoken_fill,
    )
    for phrase in phrases:
        assert harness._has_formal_action_intent(phrase), phrase
        if phrase in spoken_fill:
            continue
        assert harness._has_business_object(phrase), phrase


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


def _authorized_run(prompt, tools, skills, annotations=None):
    query_name = next((tool['function']['name'] for tool in tools
                       if str(tool['function']['name']).startswith('query_')), None)
    replies = []
    if query_name:
        replies.append({'role': 'assistant', 'tool_calls': [{
            'id': 'q1', 'type': 'function',
            'function': {'name': query_name, 'arguments': '{}'},
        }]})
    final = {'role': 'assistant', 'content': json.dumps({
        'response_kind': 'CLARIFICATION',
        'summary': '请继续提供单据号。',
        'evidence_ids': ['e1'] if query_name else [],
        'suggestions': [],
    }, ensure_ascii=False)}
    replies.extend((final, final))
    model = InspectingRepliesModel(replies)
    run_loop(context(
        prompt=prompt,
        core_tool_names=[],
        tools=tools,
        skills=skills,
        tool_annotations=annotations or {},
    ), model, Gateway())
    return model.tool_names[0]


def test_authorized_buyer_ops_load_without_whitelist_phrase():
    board = {'type': 'function', 'function': {
        'name': 'query_erp_outsource_followup_board',
        'description': '查询委外采购待办',
    }}
    quote = {'type': 'function', 'function': {
        'name': 'prepare_erp_outsource_buyer_quote',
        'description': '准备填写我方报价与直接接单上限',
    }}
    send = {'type': 'function', 'function': {
        'name': 'prepare_erp_outsource_inquiry_send',
        'description': '准备向选定加工商发出询价',
    }}
    names = _authorized_run(
        '把PH-01这一笔的价钱写成400，上限600',
        [board, quote, send],
        [{
            'key': 'outsource_buyer_ops',
            'activation_route': 'authorized',
            'tools': ['query_erp_outsource_followup_board'],
            'optional_tools': [
                'prepare_erp_outsource_buyer_quote',
                'prepare_erp_outsource_inquiry_send',
            ],
            'auto_activation_queries': ['填我方报价'],
        }],
        {'prepare_erp_outsource_buyer_quote': {'readOnlyHint': False},
         'prepare_erp_outsource_inquiry_send': {'readOnlyHint': False}},
    )
    assert 'query_erp_outsource_followup_board' in names
    assert 'prepare_erp_outsource_buyer_quote' in names
    assert 'prepare_erp_outsource_inquiry_send' in names


def test_hallucinated_project_quote_name_is_rewritten_to_buyer_quote():
    quote = {'type': 'function', 'function': {
        'name': 'prepare_erp_outsource_buyer_quote',
        'description': '准备填写我方报价与直接接单上限',
    }}
    fake = {'role': 'assistant', 'content': json.dumps({
        'name': 'prepare_project_quote',
        'arguments': {
            'mold': 'M260063',
            'batch': 'M260063-P1',
            'ourQuote': 3000,
            'upperLimit': 4000,
            'parts': 'PH-01 上夹板',
        },
    }, ensure_ascii=False)}
    awaiting = {'role': 'assistant', 'content': json.dumps({
        'response_kind': 'AWAITING_APPROVAL',
        'summary': '请确认将填写总价格 3000、上限 4000。',
        'evidence_ids': ['e1'],
        'suggestions': [],
    }, ensure_ascii=False)}

    class RecordingGateway(Gateway):
        def __init__(self):
            super().__init__()
            self.names = []

        def execute(self, seq, key, arguments):
            self.names.append(key)
            return super().execute(seq, key, arguments)

    gateway = RecordingGateway()
    model = InspectingRepliesModel([fake, awaiting])
    result = run_loop(context(
        prompt='零件有PH-01的这个订单我想填个总价格：3000，上限区间填4000',
        core_tool_names=[],
        tools=[quote],
        skills=[{
            'key': 'outsource_buyer_ops',
            'activation_route': 'authorized',
            'tools': ['prepare_erp_outsource_buyer_quote'],
            'activation_tools': ['prepare_erp_outsource_buyer_quote'],
            'optional_tools': [],
            'auto_activation_queries': ['填我方报价'],
        }],
        tool_annotations={'prepare_erp_outsource_buyer_quote': {'readOnlyHint': False}},
    ), model, gateway)

    assert gateway.names == ['prepare_erp_outsource_buyer_quote']
    assert result['response_kind'] == 'AWAITING_APPROVAL'


def test_authorized_processor_and_approval_load_spoken_intents():
    accept = {'type': 'function', 'function': {
        'name': 'prepare_erp_outsource_processor_accept',
        'description': '准备确认接单',
    }}
    board = {'type': 'function', 'function': {
        'name': 'query_erp_outsource_processor_board',
        'description': '查询本加工商委外待办',
    }}
    names = _authorized_run(
        'PH-01这单我接了',
        [board, accept],
        [{
            'key': 'outsource_processor_ops',
            'activation_route': 'authorized',
            'tools': ['query_erp_outsource_processor_board'],
            'optional_tools': ['prepare_erp_outsource_processor_accept'],
            'auto_activation_queries': ['确认接单'],
        }],
        {'prepare_erp_outsource_processor_accept': {'readOnlyHint': False}},
    )
    assert 'prepare_erp_outsource_processor_accept' in names

    todos = {'type': 'function', 'function': {
        'name': 'query_erp_outsource_approval_todos',
        'description': '查询委外下单审批待办',
    }}
    passed = {'type': 'function', 'function': {
        'name': 'prepare_erp_outsource_approval_pass',
        'description': '准备通过下单审批',
    }}
    names = _authorized_run(
        'M260063这单先过了吧',
        [todos, passed],
        [{
            'key': 'outsource_approval_ops',
            'activation_route': 'authorized',
            'tools': ['query_erp_outsource_approval_todos'],
            'optional_tools': ['prepare_erp_outsource_approval_pass'],
            'auto_activation_queries': ['通过这单'],
        }],
        {'prepare_erp_outsource_approval_pass': {'readOnlyHint': False}},
    )
    assert 'prepare_erp_outsource_approval_pass' in names


def test_authorized_write_turn_does_not_preload_progress():
    board = {'type': 'function', 'function': {
        'name': 'query_erp_outsource_followup_board',
        'description': '查询委外采购待办',
    }}
    quote = {'type': 'function', 'function': {
        'name': 'prepare_erp_outsource_buyer_quote',
        'description': '准备填写我方报价与直接接单上限',
    }}
    progress = {'type': 'function', 'function': {
        'name': 'query_erp_outsource_order_progress',
        'description': '查询委外单进度',
    }}
    names = _authorized_run(
        '零件有PU-01的这个订单我想填个总价格：300，上限区间填400',
        [board, quote, progress],
        [
            {
                'key': 'outsource_buyer_ops',
                'activation_route': 'authorized',
                'tools': ['query_erp_outsource_followup_board'],
                'optional_tools': ['prepare_erp_outsource_buyer_quote'],
                'auto_activation_queries': ['填我方报价'],
            },
            {
                'key': 'outsource_followup_query',
                'activation_route': 'authorized',
                'tools': ['query_erp_outsource_followup_board'],
                'optional_tools': ['query_erp_outsource_order_progress'],
                'auto_activation_queries': ['委外待办'],
            },
        ],
        {'prepare_erp_outsource_buyer_quote': {'readOnlyHint': False}},
    )
    assert 'query_erp_outsource_followup_board' in names
    assert 'prepare_erp_outsource_buyer_quote' in names
    assert 'query_erp_outsource_order_progress' not in names
