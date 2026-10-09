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
from domain_packs.mold.harness_policy import spoken_write_auto_invoke
from domain_packs.mold.tools.erp.procurement.outsource_queries import approval_todo, buyer_todo, processor_fulfillment


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
    assert not harness._allows_write_tools("查看待办任务")
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
    assert {"填价格", "帮我填报价", "帮我填价格", "报报价", "报个价", "帮我报价"} <= buyer_ops


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
        "发询价", "发送询价", "确认办理发送询价吧", "填成交价", "重选加工商",
        "发给加工商", "NO.1已经填价格了呀 可以发给加工商了",
        "我要报价", "我要接单", "帮我接单", "确认接单", "拒绝接单", "接这单", "订单 EO-1 我接了",
        "确认发料", "确认备料", "确认原料发货", "确认收料", "确认来料",
        "发成品", "发半成品", "确认成品发货", "成品发货吧", "发货把", "发货吧",
        "确认到货", "确认收货", "确认入库", "入库确认", "入库确认吧", "办入库",
        "领取质检", "判合格", "确认合格", "检验通过",
        "通过这单", "驳回这单", "审批通过", "审批驳回",
        "待发询价的这单帮我发询价",
        *spoken_fill,
    )
    for phrase in phrases:
        assert harness._has_formal_action_intent(phrase), phrase
        if phrase in spoken_fill:
            continue
        assert harness._has_business_object(phrase), phrase


def test_spoken_processor_accept_row_and_all_are_writes():
    from domain_packs.mold.harness_policy import is_spoken_write, spoken_write_auto_invoke
    catalog = [
        "query_erp_outsource_processor_board",
        "prepare_erp_outsource_processor_accept",
    ]
    for spoken, expected in (
        ("NO.1接单", {"board_row": 1}),
        ("全部接单", {"accept_all": True}),
    ):
        assert is_spoken_write(spoken)
        assert not harness._is_read_only_request(spoken)
        invoked = spoken_write_auto_invoke(spoken, catalog)
        assert invoked is not None
        assert invoked[0] == "prepare_erp_outsource_processor_accept"
        for key, value in expected.items():
            assert invoked[1][key] == value
    assert spoken_write_auto_invoke("待接单有几个", catalog) is None


def test_spoken_processor_quote_is_a_write_not_a_lookup():
    prompt = "EO-260930-R77L报价88888元"
    assert harness._has_formal_action_intent(prompt)
    assert not harness._is_read_only_request(prompt)
    from domain_packs.mold.harness_policy import spoken_write_ensure_tools, spoken_write_auto_invoke
    catalog = [
        "query_erp_outsource_processor_board",
        "prepare_erp_outsource_processor_quote",
    ]
    assert "prepare_erp_outsource_processor_quote" in spoken_write_ensure_tools(prompt, catalog)
    invoked = spoken_write_auto_invoke(prompt, catalog)
    assert invoked is not None
    assert invoked[0] == "prepare_erp_outsource_processor_quote"
    assert invoked[1]["order_no"] == "EO-260930-R77L"
    assert invoked[1]["unit_price"] == 88888
    for spoken in ("M260063-P4报价66666", "报价66666"):
        assert harness._has_formal_action_intent(spoken)
        assert not harness._is_read_only_request(spoken)
        assert "prepare_erp_outsource_processor_quote" in spoken_write_ensure_tools(spoken, catalog)
        locked = spoken_write_auto_invoke(spoken, catalog)
        assert locked is not None
        assert locked[0] == "prepare_erp_outsource_processor_quote"
        assert locked[1]["unit_price"] == 66666


def test_host_auto_invokes_processor_quote_from_order_speech():
    board = {'type': 'function', 'function': {
        'name': 'query_erp_outsource_processor_board',
        'description': '查询本加工商委外待办',
    }}
    quote = {'type': 'function', 'function': {
        'name': 'prepare_erp_outsource_processor_quote',
        'description': '准备提交本加工商报价',
        'parameters': {
            'type': 'object',
            'properties': {
                'order_no': {'type': 'string'},
                'unit_price': {'type': 'number'},
            },
            'required': ['unit_price'],
        },
    }}
    awaiting = {'role': 'assistant', 'content': json.dumps({
        'response_kind': 'AWAITING_APPROVAL',
        'summary': '请核对报价确认卡后确认。',
        'evidence_ids': ['e1'],
        'suggestions': [],
    }, ensure_ascii=False)}

    class RecordingGateway(Gateway):
        def __init__(self):
            super().__init__()
            self.calls = []

        def execute(self, seq, key, arguments):
            self.calls.append((key, arguments))
            if seq not in self.receipts:
                self.physical_calls += 1
                self.receipts[seq] = {
                    'evidence_id': 'e1',
                    'proposal': {
                        'kind': 'erp_outsource_processor_quote',
                        'action': 'confirm_erp_outsource_processor_quote',
                    },
                }
            return self.receipts[seq]

    gateway = RecordingGateway()
    result = run_loop(context(
        prompt='EO-260930-R77L报价88888元',
        core_tool_names=[],
        tools=[board, quote],
        skills=[{
            'key': 'outsource_processor_query',
            'activation_route': 'authorized',
            'tools': ['query_erp_outsource_processor_board'],
            'optional_tools': [],
            'host_auto_invoke_empty_arguments': True,
        }, {
            'key': 'outsource_processor_ops',
            'activation_route': 'authorized',
            'tools': ['query_erp_outsource_processor_board'],
            'optional_tools': ['prepare_erp_outsource_processor_quote'],
            'activation_tools': ['query_erp_outsource_processor_board'],
            'auto_activation_queries': ['提交报价', '我要报价'],
        }],
        tool_annotations={'prepare_erp_outsource_processor_quote': {'readOnlyHint': False}},
    ), InspectingRepliesModel([awaiting]), gateway)

    assert gateway.calls, result
    name, arguments = gateway.calls[0]
    assert name == 'prepare_erp_outsource_processor_quote'
    assert arguments['order_no'] == 'EO-260930-R77L'
    assert arguments['unit_price'] == 88888
    assert result['response_kind'] == 'AWAITING_APPROVAL'
    assert all(call[0] != 'query_erp_outsource_processor_board' for call in gateway.calls)


def test_host_auto_invokes_processor_quote_from_batch_speech_even_with_board_evidence():
    board = {'type': 'function', 'function': {
        'name': 'query_erp_outsource_processor_board',
        'description': '查询本加工商委外待办',
    }}
    quote = {'type': 'function', 'function': {
        'name': 'prepare_erp_outsource_processor_quote',
        'description': '准备提交本加工商报价',
        'parameters': {
            'type': 'object',
            'properties': {
                'batch': {'type': 'string'},
                'unit_price': {'type': 'number'},
            },
            'required': ['unit_price'],
        },
    }}
    awaiting = {'role': 'assistant', 'content': json.dumps({
        'response_kind': 'AWAITING_APPROVAL',
        'summary': '请核对报价确认卡后确认。',
        'evidence_ids': ['e1'],
        'suggestions': [],
    }, ensure_ascii=False)}

    class RecordingGateway(Gateway):
        def __init__(self):
            super().__init__()
            self.calls = []

        def execute(self, seq, key, arguments):
            self.calls.append((key, arguments))
            if seq not in self.receipts:
                self.physical_calls += 1
                self.receipts[seq] = {
                    'evidence_id': 'e1',
                    'proposal': {
                        'kind': 'erp_outsource_processor_quote',
                        'action': 'confirm_erp_outsource_processor_quote',
                    },
                }
            return self.receipts[seq]

    gateway = RecordingGateway()
    result = run_loop(context(
        prompt='M260063-P4报价66666',
        evidence_ids=['prev-board'],
        core_tool_names=[],
        tools=[board, quote],
        skills=[{
            'key': 'outsource_processor_query',
            'activation_route': 'authorized',
            'tools': ['query_erp_outsource_processor_board'],
            'optional_tools': [],
            'host_auto_invoke_empty_arguments': True,
        }, {
            'key': 'outsource_processor_ops',
            'activation_route': 'authorized',
            'tools': ['query_erp_outsource_processor_board'],
            'optional_tools': ['prepare_erp_outsource_processor_quote'],
            'activation_tools': ['query_erp_outsource_processor_board'],
            'auto_activation_queries': ['提交报价', '我要报价'],
        }],
        tool_annotations={'prepare_erp_outsource_processor_quote': {'readOnlyHint': False}},
    ), InspectingRepliesModel([awaiting]), gateway)

    assert gateway.calls, result
    name, arguments = gateway.calls[0]
    assert name == 'prepare_erp_outsource_processor_quote'
    assert arguments['batch'] == 'M260063-P4'
    assert arguments['unit_price'] == 66666
    assert result['response_kind'] == 'AWAITING_APPROVAL'


def test_outsource_count_questions_are_not_formal_actions():
    # Station nouns double as to-do board names.  A count or list question
    # must stay read-only, otherwise the Harness demands an operation receipt
    # and refuses to answer with the board.
    phrases = (
        "待报价有几个", "有没有待接单", "质检待办有几条", "查一下入库待办",
        "有需要我处理的待办任务吗", "查看待办",
        "成品发货待办有几个", "有没有可以成品发货的订单", "成品发货",
        "备料完成的有哪些", "原料发货待办", "待发询价有几个",
        "质检合格的有几条", "检验合格了没有", "到货确认待办", "回厂入库待办有几个",
        "成品入库的有哪些", "待发成品有几个", "待领取质检有几个",
    )
    for phrase in phrases:
        assert not harness._has_formal_action_intent(phrase), phrase
        if phrase in {"有没有可以成品发货的订单", "成品发货", "成品发货待办有几个"}:
            assert harness._is_read_only_request(phrase), phrase


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


def test_host_auto_invokes_buyer_quote_form_when_mold_and_batch_are_locked(monkeypatch):
    quote = {'type': 'function', 'function': {
        'name': 'prepare_erp_outsource_buyer_quote',
        'description': '准备填写我方报价与直接接单上限',
        'parameters': {
            'type': 'object',
            'properties': {
                'mold': {'type': 'string'},
                'batch': {'type': 'string'},
                'part': {'type': 'string'},
                'our_quote_amount': {'type': 'number'},
                'auto_accept_max_amount': {'type': 'number'},
            },
            'required': ['our_quote_amount', 'auto_accept_max_amount'],
        },
    }}
    awaiting = {'role': 'assistant', 'content': json.dumps({
        'response_kind': 'AWAITING_APPROVAL',
        'summary': '请核对报价表后确认。',
        'evidence_ids': ['e1'],
        'suggestions': [],
    }, ensure_ascii=False)}

    class RecordingGateway(Gateway):
        def __init__(self):
            super().__init__()
            self.calls = []

        def execute(self, seq, key, arguments):
            self.calls.append((key, arguments))
            if seq not in self.receipts:
                self.physical_calls += 1
                self.receipts[seq] = {
                    'evidence_id': 'e1',
                    'proposal': {
                        'kind': 'erp_outsource_buyer_quote',
                        'action': 'confirm_erp_outsource_buyer_quote',
                    },
                }
            return self.receipts[seq]

    gateway = RecordingGateway()
    model = InspectingRepliesModel([awaiting])
    monkeypatch.setattr(
        "domain_packs.mold.tools.erp.procurement.outsource_queries.buyer_todo.query_items",
        lambda parsed: [{
            "parts": [{"partNo": "PH-01"}],
            "partDetails": "PH-01 上夹板",
            "moldNo": "M260063-P1",
            "moldFamily": "M260063",
            "moldBatch": "M260063-P1",
            "orderNo": "",
        }],
    )
    result = run_loop(context(
        prompt='就第1行零件 PH-01 这一张，准备填写我方报价 300、上限 380，出确认卡',
        core_tool_names=[],
        tools=[quote],
        messages=[{'role': 'assistant', 'content': 'ERP 委外待办 M260063 · M260063-P1 共 4 条'}],
        skills=[{
            'key': 'outsource_buyer_ops',
            'activation_route': 'authorized',
            'tools': ['query_erp_outsource_followup_board'],
            'optional_tools': ['prepare_erp_outsource_buyer_quote'],
            'activation_tools': ['query_erp_outsource_followup_board'],
            'auto_activation_queries': ['填我方报价'],
        }],
        tool_annotations={'prepare_erp_outsource_buyer_quote': {'readOnlyHint': False}},
    ), model, gateway)

    assert gateway.calls
    name, arguments = gateway.calls[0]
    assert name == 'prepare_erp_outsource_buyer_quote'
    assert arguments['mold'] == 'M260063'
    assert arguments['batch'] == 'M260063-P1'
    assert arguments['part'] == 'PH-01'
    assert arguments['our_quote_amount'] == 300
    assert arguments['auto_accept_max_amount'] == 380
    assert result['response_kind'] == 'AWAITING_APPROVAL'
    assert gateway.physical_calls == 1


def test_host_auto_invokes_final_deal_for_board_row_speech(monkeypatch):
    rows = [
        {"inquiryId": 11, "station": "buyer_quote", "stationLabel": "待采购填报价", "moldFamily": "M260063", "moldBatch": "M260063-P4"},
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
    monkeypatch.setattr(
        buyer_todo,
        "item_from_visible_board_row",
        lambda row_no: rows[row_no - 1] if 1 <= row_no <= len(rows) else None,
    )
    invoked = spoken_write_auto_invoke(
        "NO.2填成交价:50000",
        ["prepare_erp_outsource_final_deal"],
    )
    assert invoked is not None
    name, arguments = invoked
    assert name == "prepare_erp_outsource_final_deal"
    assert arguments["board_row"] == 2
    assert arguments["final_deal_amount"] == 50000
    assert arguments["quotation_id"] == 88
    written = spoken_write_auto_invoke(
        "NO.2填写成交价:50000",
        ["prepare_erp_outsource_final_deal"],
    )
    assert written is not None
    assert written[0] == "prepare_erp_outsource_final_deal"
    assert written[1]["board_row"] == 2
    assert written[1]["final_deal_amount"] == 50000
    bare = spoken_write_auto_invoke(
        "填写成交价:50000",
        ["prepare_erp_outsource_final_deal"],
    )
    assert bare is not None
    assert bare[0] == "prepare_erp_outsource_final_deal"
    assert bare[1]["final_deal_amount"] == 50000
    assert harness._has_formal_action_intent("填写成交价:50000")
    assert not harness._is_read_only_request("NO.2填写成交价:50000")


def test_host_auto_invokes_approval_pass_from_spoken_order(monkeypatch):
    invoked = spoken_write_auto_invoke(
        "通过这单",
        [
            "query_erp_outsource_approval_todos",
            "prepare_erp_outsource_approval_pass",
            "prepare_erp_outsource_approval_reject",
        ],
        "委外下单审批待办（采购主管）共 1 条。采购主管审批 M260063-P2 EO-260930-R77L",
    )
    assert invoked is not None
    name, arguments = invoked
    assert name == "prepare_erp_outsource_approval_pass"
    assert arguments["order_no"] == "EO-260930-R77L"
    assert spoken_write_auto_invoke(
        "查看待办",
        [
            "query_erp_outsource_approval_todos",
            "prepare_erp_outsource_approval_pass",
        ],
    ) is None


def test_host_auto_invokes_buyer_quote_for_spoken_bao_quote(monkeypatch):
    quote = {'type': 'function', 'function': {
        'name': 'prepare_erp_outsource_buyer_quote',
        'description': '准备填写我方报价与直接接单上限',
        'parameters': {
            'type': 'object',
            'properties': {
                'mold': {'type': 'string'},
                'batch': {'type': 'string'},
                'part': {'type': 'string'},
                'our_quote_amount': {'type': 'number'},
                'auto_accept_max_amount': {'type': 'number'},
            },
            'required': ['our_quote_amount', 'auto_accept_max_amount'],
        },
    }}
    awaiting = {'role': 'assistant', 'content': json.dumps({
        'response_kind': 'AWAITING_APPROVAL',
        'summary': '请核对报价表后确认。',
        'evidence_ids': ['e1'],
        'suggestions': [],
    }, ensure_ascii=False)}

    class RecordingGateway(Gateway):
        def __init__(self):
            super().__init__()
            self.calls = []

        def execute(self, seq, key, arguments):
            self.calls.append((key, arguments))
            return super().execute(seq, key, arguments)

    gateway = RecordingGateway()
    model = InspectingRepliesModel([awaiting])
    monkeypatch.setattr(
        "domain_packs.mold.tools.erp.procurement.outsource_queries.buyer_todo.query_items",
        lambda parsed: [{
            "parts": [{"partNo": "B1-01"}],
            "partDetails": "B1-01 下托板",
            "moldNo": "M260063-P4",
            "moldFamily": "M260063",
            "moldBatch": "M260063-P4",
            "orderNo": "",
            "station": "buyer_quote",
            "stationLabel": "待采购填报价",
        }],
    )
    result = run_loop(context(
        prompt='M260063-P4 有零件是B1-01的 这笔订单的报报价：总价150000上限300000',
        core_tool_names=[],
        tools=[quote],
        skills=[{
            'key': 'outsource_buyer_ops',
            'activation_route': 'authorized',
            'tools': ['query_erp_outsource_followup_board'],
            'optional_tools': ['prepare_erp_outsource_buyer_quote'],
            'activation_tools': ['query_erp_outsource_followup_board'],
            'auto_activation_queries': ['填我方报价', '报报价'],
        }],
        tool_annotations={'prepare_erp_outsource_buyer_quote': {'readOnlyHint': False}},
    ), model, gateway)

    assert gateway.calls
    name, arguments = gateway.calls[0]
    assert name == 'prepare_erp_outsource_buyer_quote'
    assert arguments['mold'] == 'M260063'
    assert arguments['batch'] == 'M260063-P4'
    assert arguments['part'] == 'B1-01'
    assert arguments['our_quote_amount'] == 150000
    assert arguments['auto_accept_max_amount'] == 300000
    assert result['response_kind'] == 'AWAITING_APPROVAL'


def test_host_auto_invokes_buyer_quote_for_first_pending_row(monkeypatch):
    quote = {'type': 'function', 'function': {
        'name': 'prepare_erp_outsource_buyer_quote',
        'description': '准备填写我方报价与直接接单上限',
        'parameters': {
            'type': 'object',
            'properties': {
                'mold': {'type': 'string'},
                'batch': {'type': 'string'},
                'part': {'type': 'string'},
                'our_quote_amount': {'type': 'number'},
                'auto_accept_max_amount': {'type': 'number'},
            },
            'required': ['our_quote_amount', 'auto_accept_max_amount'],
        },
    }}
    awaiting = {'role': 'assistant', 'content': json.dumps({
        'response_kind': 'AWAITING_APPROVAL',
        'summary': '请核对报价表后确认。',
        'evidence_ids': ['e1'],
        'suggestions': [],
    }, ensure_ascii=False)}

    class RecordingGateway(Gateway):
        def __init__(self):
            super().__init__()
            self.calls = []

        def execute(self, seq, key, arguments):
            self.calls.append((key, arguments))
            return super().execute(seq, key, arguments)

    gateway = RecordingGateway()
    model = InspectingRepliesModel([awaiting])
    monkeypatch.setattr(
        "domain_packs.mold.tools.erp.procurement.outsource_queries.buyer_todo.query_items",
        lambda parsed: [{
            "parts": [{"partNo": "B1-01"}],
            "partDetails": "B1-01 下托板",
            "moldNo": "M260063-P4",
            "moldFamily": "M260063",
            "moldBatch": "M260063-P4",
            "orderNo": "",
            "station": "buyer_quote",
            "stationLabel": "待采购填报价",
        }],
    )
    result = run_loop(context(
        prompt='把待排列第一的这个订单报报价：总价150000上限300000',
        core_tool_names=[],
        tools=[quote],
        skills=[{
            'key': 'outsource_buyer_ops',
            'activation_route': 'authorized',
            'tools': ['query_erp_outsource_followup_board'],
            'optional_tools': ['prepare_erp_outsource_buyer_quote'],
            'activation_tools': ['query_erp_outsource_followup_board'],
            'auto_activation_queries': ['填我方报价', '报报价'],
        }],
        tool_annotations={'prepare_erp_outsource_buyer_quote': {'readOnlyHint': False}},
    ), model, gateway)

    assert gateway.calls
    name, arguments = gateway.calls[0]
    assert name == 'prepare_erp_outsource_buyer_quote'
    assert arguments['mold'] == 'M260063'
    assert arguments['batch'] == 'M260063-P4'
    assert arguments['part'] == 'B1-01'
    assert arguments['our_quote_amount'] == 150000
    assert result['response_kind'] == 'AWAITING_APPROVAL'


def test_host_prepares_inquiry_send_after_board_hit_without_asking_again():
    board = {'type': 'function', 'function': {
        'name': 'query_erp_outsource_followup_board',
        'description': '查询委外采购待办',
        'parameters': {'type': 'object', 'properties': {'question': {'type': 'string'}}},
    }}
    send = {'type': 'function', 'function': {
        'name': 'prepare_erp_outsource_inquiry_send',
        'description': '准备向选定加工商发出询价',
        'parameters': {
            'type': 'object',
            'properties': {
                'mold': {'type': 'string'},
                'batch': {'type': 'string'},
                'suppliers': {'type': 'array', 'items': {'type': 'string'}},
            },
        },
    }}

    class RecordingGateway(Gateway):
        def __init__(self):
            super().__init__()
            self.calls = []

        def execute(self, seq, key, arguments):
            self.calls.append((key, arguments))
            if seq not in self.receipts:
                self.physical_calls += 1
                if key == 'query_erp_outsource_followup_board':
                    self.receipts[seq] = {
                        'evidence_id': 'e1',
                        'data': {
                            'counts': {'待发询价': 1, '待采购填报价': 1},
                            'items': [
                                {'stationLabel': '待发询价', 'moldFamily': 'M260063', 'moldBatch': 'M260063-P5'},
                                {'stationLabel': '待采购填报价', 'moldFamily': 'M260063', 'moldBatch': 'M260063-P1'},
                            ],
                        },
                        'model_context': {
                            'item_count': 2,
                            'counts': {'待发询价': 1, '待采购填报价': 1},
                            'items': [
                                {'station': '待发询价', 'mold': 'M260063', 'batch': 'M260063-P5'},
                                {'station': '待采购填报价', 'mold': 'M260063', 'batch': 'M260063-P1'},
                            ],
                        },
                    }
                else:
                    self.receipts[seq] = {
                        'evidence_id': 'e2',
                        'proposal': {
                            'kind': 'erp_outsource_inquiry_send',
                            'action': 'confirm_erp_outsource_inquiry_send',
                            'display': {'加工商': '青岛和兴金属制品有限公司'},
                        },
                    }
            return self.receipts[seq]

    gateway = RecordingGateway()
    model = InspectingRepliesModel([{
        'role': 'assistant',
        'tool_calls': [{
            'id': 'q1', 'type': 'function',
            'function': {'name': 'query_erp_outsource_followup_board', 'arguments': '{}'},
        }],
    }])
    result = run_loop(context(
        prompt='确认办理发送询价吧',
        core_tool_names=[],
        tools=[board, send],
        skills=[{
            'key': 'outsource_buyer_ops',
            'activation_route': 'authorized',
            'tools': ['query_erp_outsource_followup_board'],
            'optional_tools': ['prepare_erp_outsource_inquiry_send'],
            'activation_tools': ['query_erp_outsource_followup_board'],
            'auto_activation_queries': ['发询价', '发送询价', '确认办理发送询价'],
        }],
        tool_annotations={
            'query_erp_outsource_followup_board': {'readOnlyHint': True},
            'prepare_erp_outsource_inquiry_send': {'readOnlyHint': False},
        },
    ), model, gateway)

    assert [name for name, _arguments in gateway.calls] == [
        'query_erp_outsource_followup_board',
        'prepare_erp_outsource_inquiry_send',
    ]
    assert gateway.calls[1][1]['mold'] == 'M260063'
    assert gateway.calls[1][1]['batch'] == 'M260063-P5'
    assert result['response_kind'] == 'AWAITING_APPROVAL'


def test_host_auto_invokes_inquiry_send_for_first_row_speech(monkeypatch):
    send = {'type': 'function', 'function': {
        'name': 'prepare_erp_outsource_inquiry_send',
        'description': '准备向选定加工商发出询价',
        'parameters': {
            'type': 'object',
            'properties': {
                'mold': {'type': 'string'},
                'batch': {'type': 'string'},
            },
        },
    }}
    awaiting = {'role': 'assistant', 'content': json.dumps({
        'response_kind': 'AWAITING_APPROVAL',
        'summary': '请核对发询价确认表。',
        'evidence_ids': ['e1'],
        'suggestions': [],
    }, ensure_ascii=False)}

    class RecordingGateway(Gateway):
        def __init__(self):
            super().__init__()
            self.calls = []

        def execute(self, seq, key, arguments):
            self.calls.append((key, arguments))
            if seq not in self.receipts:
                self.physical_calls += 1
                self.receipts[seq] = {
                    'evidence_id': 'e1',
                    'proposal': {
                        'kind': 'erp_outsource_inquiry_send',
                        'action': 'confirm_erp_outsource_inquiry_send',
                    },
                }
            return self.receipts[seq]

    gateway = RecordingGateway()
    model = InspectingRepliesModel([awaiting])
    monkeypatch.setattr(
        "domain_packs.mold.tools.erp.procurement.outsource_queries.buyer_todo.query_items",
        lambda parsed: [{
            "station": "inquiry_send",
            "stationLabel": "待发询价",
            "moldNo": "M260063-P5",
            "moldFamily": "M260063",
            "moldBatch": "M260063-P5",
            "orderNo": "",
        }],
    )
    result = run_loop(context(
        prompt='第一行发送询价',
        core_tool_names=[],
        tools=[send],
        skills=[{
            'key': 'outsource_buyer_ops',
            'activation_route': 'authorized',
            'tools': ['query_erp_outsource_followup_board'],
            'optional_tools': ['prepare_erp_outsource_inquiry_send'],
            'activation_tools': ['query_erp_outsource_followup_board'],
            'auto_activation_queries': ['发询价', '发送询价'],
        }],
        tool_annotations={'prepare_erp_outsource_inquiry_send': {'readOnlyHint': False}},
    ), model, gateway)

    assert gateway.calls
    name, arguments = gateway.calls[0]
    assert name == 'prepare_erp_outsource_inquiry_send'
    assert arguments['mold'] == 'M260063'
    assert arguments['batch'] == 'M260063-P5'
    assert result['response_kind'] == 'AWAITING_APPROVAL'


def test_host_auto_invokes_processor_accept_when_order_is_locked():
    accept = {'type': 'function', 'function': {
        'name': 'prepare_erp_outsource_processor_accept',
        'description': '准备确认接单',
        'parameters': {
            'type': 'object',
            'properties': {
                'order_no': {'type': 'string'},
                'mold': {'type': 'string'},
                'batch': {'type': 'string'},
            },
            'required': ['order_no'],
        },
    }}
    awaiting = {'role': 'assistant', 'content': json.dumps({
        'response_kind': 'AWAITING_APPROVAL',
        'summary': '请核对接单确认表后确认。',
        'evidence_ids': ['e1'],
        'suggestions': [],
    }, ensure_ascii=False)}

    class RecordingGateway(Gateway):
        def __init__(self):
            super().__init__()
            self.calls = []

        def execute(self, seq, key, arguments):
            self.calls.append((key, arguments))
            return super().execute(seq, key, arguments)

    gateway = RecordingGateway()
    model = InspectingRepliesModel([awaiting])
    result = run_loop(context(
        prompt='订单 EO-260924-A5SS，模具 M260063 批次 M260063-P4，零件 PU-03 冲头，我接了',
        core_tool_names=[],
        tools=[accept],
        skills=[{
            'key': 'outsource_processor_ops',
            'activation_route': 'authorized',
            'tools': ['query_erp_outsource_processor_board'],
            'optional_tools': ['prepare_erp_outsource_processor_accept'],
            'activation_tools': ['query_erp_outsource_processor_board'],
            'auto_activation_queries': ['确认接单', '我接了'],
        }],
        tool_annotations={'prepare_erp_outsource_processor_accept': {'readOnlyHint': False}},
    ), model, gateway)

    assert gateway.calls
    name, arguments = gateway.calls[0]
    assert name == 'prepare_erp_outsource_processor_accept'
    assert arguments['order_no'] == 'EO-260924-A5SS'
    assert arguments['mold'] == 'M260063'
    assert arguments['batch'] == 'M260063-P4'
    assert result['response_kind'] == 'AWAITING_APPROVAL'
    assert gateway.physical_calls == 1


def test_host_spoken_accept_closes_without_board_or_model_when_proposal_ready():
    board = {'type': 'function', 'function': {
        'name': 'query_erp_outsource_processor_board',
        'description': '查询加工商委外待办',
        'parameters': {'type': 'object', 'properties': {'question': {'type': 'string'}}},
    }}
    accept = {'type': 'function', 'function': {
        'name': 'prepare_erp_outsource_processor_accept',
        'description': '准备确认接单',
        'parameters': {
            'type': 'object',
            'properties': {
                'order_no': {'type': 'string'},
                'mold': {'type': 'string'},
                'batch': {'type': 'string'},
            },
            'required': ['order_no'],
        },
    }}

    class ProposalGateway(Gateway):
        def execute(self, seq, key, arguments):
            if seq not in self.receipts:
                self.physical_calls += 1
                self.receipts[seq] = {
                    'evidence_id': 'e1',
                    'proposal': {
                        'kind': 'erp_outsource_processor_accept',
                        'action': 'confirm_erp_outsource_processor_accept',
                    },
                }
            return self.receipts[seq]

    gateway = ProposalGateway()
    model = InspectingRepliesModel([])
    result = run_loop(context(
        prompt='订单 EO-260924-A5SS，模具 M260063 批次 M260063-P4，零件 PU-03 冲头，我接了',
        core_tool_names=[],
        tools=[board, accept],
        skills=[{
            'key': 'outsource_processor_query',
            'activation_route': 'authorized',
            'tools': ['query_erp_outsource_processor_board'],
            'activation_tools': ['query_erp_outsource_processor_board'],
            'requires_tool_evidence': True,
            'auto_activation_queries': ['待接单', '我接了'],
        }, {
            'key': 'outsource_processor_ops',
            'activation_route': 'authorized',
            'tools': ['query_erp_outsource_processor_board'],
            'optional_tools': ['prepare_erp_outsource_processor_accept'],
            'activation_tools': ['query_erp_outsource_processor_board'],
            'auto_activation_queries': ['确认接单', '我接了'],
        }],
        tool_annotations={
            'query_erp_outsource_processor_board': {'readOnlyHint': True},
            'prepare_erp_outsource_processor_accept': {'readOnlyHint': False},
        },
    ), model, gateway)

    assert result['response_kind'] == 'AWAITING_APPROVAL'
    assert result['evidence_ids'] == ['e1']
    assert gateway.physical_calls == 1
    assert model.tool_names == []


def test_host_spoken_warehouse_ship_closes_without_board_or_model():
    board = {'type': 'function', 'function': {
        'name': 'query_erp_outsource_warehouse_tasks',
        'description': '查询仓库委外待发料',
        'parameters': {'type': 'object', 'properties': {'question': {'type': 'string'}}},
    }}
    ship = {'type': 'function', 'function': {
        'name': 'prepare_erp_outsource_warehouse_ship',
        'description': '准备确认仓库发料或备料',
        'parameters': {
            'type': 'object',
            'properties': {
                'order_no': {'type': 'string'},
                'mold': {'type': 'string'},
                'batch': {'type': 'string'},
            },
            'required': ['order_no'],
        },
    }}

    class ProposalGateway(Gateway):
        def execute(self, seq, key, arguments):
            if seq not in self.receipts:
                self.physical_calls += 1
                self.receipts[seq] = {
                    'evidence_id': 'e1',
                    'proposal': {
                        'kind': 'erp_outsource_warehouse_ship',
                        'action': 'confirm_erp_outsource_warehouse_ship',
                    },
                }
            return self.receipts[seq]

    gateway = ProposalGateway()
    model = InspectingRepliesModel([])
    result = run_loop(context(
        prompt='订单 EO-260924-A5SS，模具 M260063 批次 M260063-P4，零件 PU-03 冲头，确认备料',
        core_tool_names=[],
        tools=[board, ship],
        skills=[{
            'key': 'outsource_warehouse_ops',
            'activation_route': 'authorized',
            'tools': ['query_erp_outsource_warehouse_tasks'],
            'optional_tools': ['prepare_erp_outsource_warehouse_ship'],
            'activation_tools': ['query_erp_outsource_warehouse_tasks'],
            'requires_tool_evidence': True,
            'auto_activation_queries': ['确认备料', '待发料'],
        }],
        tool_annotations={
            'query_erp_outsource_warehouse_tasks': {'readOnlyHint': True},
            'prepare_erp_outsource_warehouse_ship': {'readOnlyHint': False},
        },
    ), model, gateway)

    assert result['response_kind'] == 'AWAITING_APPROVAL'
    assert result['evidence_ids'] == ['e1']
    assert gateway.physical_calls == 1
    assert model.tool_names == []


def test_host_spoken_warehouse_inbound_confirm_prepares_inbound(monkeypatch):
    from domain_packs.mold.tools.erp.procurement.outsource_queries import warehouse_inbound

    monkeypatch.setattr(warehouse_inbound, "find_pending_by_identity", lambda **kwargs: [{
        "shipmentId": 9,
        "shipmentNo": "PS-5136-441118",
        "orderNo": "EO-260928-WE11",
        "pendingArrivalQty": 30,
        "pendingInboundQty": 30,
    }])
    arrival = {'type': 'function', 'function': {
        'name': 'prepare_erp_outsource_warehouse_arrival',
        'description': '准备仓库到货确认',
        'parameters': {'type': 'object', 'properties': {'shipment_id': {'type': 'integer'}}},
    }}
    inbound = {'type': 'function', 'function': {
        'name': 'prepare_erp_outsource_warehouse_inbound',
        'description': '准备仓储入库确认',
        'parameters': {'type': 'object', 'properties': {'shipment_id': {'type': 'integer'}}},
    }}

    class ProposalGateway(Gateway):
        def execute(self, seq, key, arguments):
            self.calls = getattr(self, 'calls', [])
            self.calls.append((key, arguments))
            if seq not in self.receipts:
                self.physical_calls += 1
                self.receipts[seq] = {
                    'evidence_id': 'e1',
                    'proposal': {
                        'kind': 'erp_outsource_warehouse_inbound',
                        'action': 'confirm_erp_outsource_warehouse_inbound',
                    },
                }
            return self.receipts[seq]

    gateway = ProposalGateway()
    model = InspectingRepliesModel([])
    result = run_loop(context(
        prompt='入库确认吧',
        core_tool_names=[],
        tools=[arrival, inbound],
        conversation_history=[{
            'user': {'content': '有多少待办任务'},
            'assistant': {'summary': '当前有1条待办：订单 EO-260928-WE11，发货单号 PS-5136-441118，待入库。'},
        }],
        skills=[{
            'key': 'outsource_warehouse_inbound',
            'activation_route': 'authorized',
            'tools': ['query_erp_outsource_warehouse_inbound'],
            'optional_tools': [
                'prepare_erp_outsource_warehouse_arrival',
                'prepare_erp_outsource_warehouse_inbound',
            ],
            'priority_patterns': ['入库确认|确认入库'],
            'auto_activation_queries': ['入库确认', '确认入库'],
            'requires_tool_evidence': True,
        }],
        tool_annotations={
            'prepare_erp_outsource_warehouse_arrival': {'readOnlyHint': False},
            'prepare_erp_outsource_warehouse_inbound': {'readOnlyHint': False},
        },
    ), model, gateway)

    assert result['response_kind'] == 'AWAITING_APPROVAL'
    assert gateway.calls[0][0] == 'prepare_erp_outsource_warehouse_inbound'
    assert gateway.physical_calls == 1
    assert model.tool_names == []


def test_host_spoken_quality_claim_prepares_card(monkeypatch):
    from domain_packs.mold.tools.erp.procurement.outsource_queries import quality_todo

    monkeypatch.setattr(quality_todo, "find_pending_by_identity", lambda **kwargs: [{
        "taskId": 15,
        "inspectionNo": "QC202609290001",
        "orderNo": "EO-260928-WE11",
        "status": "pending",
    }])
    claim = {'type': 'function', 'function': {
        'name': 'prepare_erp_outsource_quality_claim',
        'description': '准备领取质检任务',
        'parameters': {'type': 'object', 'properties': {'task_id': {'type': 'integer'}}},
    }}

    class ProposalGateway(Gateway):
        def execute(self, seq, key, arguments):
            self.calls = getattr(self, 'calls', [])
            self.calls.append((key, arguments))
            if seq not in self.receipts:
                self.physical_calls += 1
                self.receipts[seq] = {
                    'evidence_id': 'e1',
                    'proposal': {
                        'kind': 'erp_outsource_quality_claim',
                        'action': 'confirm_erp_outsource_quality_claim',
                    },
                }
            return self.receipts[seq]

    gateway = ProposalGateway()
    model = InspectingRepliesModel([])
    result = run_loop(context(
        prompt='领取质检任务',
        core_tool_names=[],
        tools=[claim],
        conversation_history=[{
            'user': {'content': '查询待办任务'},
            'assistant': {'summary': '已经查到待领取 EO-260928-WE11。质检单 QC202609290001。'},
        }],
        skills=[{
            'key': 'outsource_quality_ops',
            'activation_route': 'authorized',
            'tools': ['query_erp_outsource_quality_tasks'],
            'optional_tools': ['prepare_erp_outsource_quality_claim', 'prepare_erp_outsource_quality_pass'],
            'priority_patterns': ['领取质检|质检任务'],
            'auto_activation_queries': ['领取质检', '质检任务'],
            'requires_tool_evidence': True,
        }],
        tool_annotations={
            'prepare_erp_outsource_quality_claim': {'readOnlyHint': False},
        },
    ), model, gateway)

    assert result['response_kind'] == 'AWAITING_APPROVAL'
    assert gateway.calls[0][0] == 'prepare_erp_outsource_quality_claim'
    assert gateway.calls[0][1]['task_id'] == 15
    assert model.tool_names == []


def test_host_spoken_quality_pass_prepares_card(monkeypatch):
    from domain_packs.mold.tools.erp.procurement.outsource_queries import quality_todo

    monkeypatch.setattr(quality_todo, "find_pending_by_identity", lambda **kwargs: [{
        "taskId": 15,
        "inspectionNo": "QC202609290001",
        "orderNo": "EO-260928-WE11",
        "status": "inspecting",
    }])
    claim = {'type': 'function', 'function': {
        'name': 'prepare_erp_outsource_quality_claim',
        'description': '准备领取质检任务',
        'parameters': {'type': 'object', 'properties': {'task_id': {'type': 'integer'}}},
    }}
    pass_tool = {'type': 'function', 'function': {
        'name': 'prepare_erp_outsource_quality_pass',
        'description': '准备提交质检合格',
        'parameters': {'type': 'object', 'properties': {'task_id': {'type': 'integer'}}},
    }}

    class ProposalGateway(Gateway):
        def execute(self, seq, key, arguments):
            self.calls = getattr(self, 'calls', [])
            self.calls.append((key, arguments))
            if seq not in self.receipts:
                self.physical_calls += 1
                self.receipts[seq] = {
                    'evidence_id': 'e1',
                    'proposal': {
                        'kind': 'erp_outsource_quality_pass',
                        'action': 'confirm_erp_outsource_quality_pass',
                    },
                }
            return self.receipts[seq]

    gateway = ProposalGateway()
    model = InspectingRepliesModel([])
    result = run_loop(context(
        prompt='提交质检合格',
        core_tool_names=[],
        tools=[claim, pass_tool],
        conversation_history=[{
            'user': {'content': '领取质检任务'},
            'assistant': {'summary': '已成功领取质检任务：QC202609290001，订单号 EO-260928-WE11。当前状态为已领取。'},
        }],
        skills=[{
            'key': 'outsource_quality_ops',
            'activation_route': 'authorized',
            'tools': ['query_erp_outsource_quality_tasks'],
            'optional_tools': ['prepare_erp_outsource_quality_claim', 'prepare_erp_outsource_quality_pass'],
            'priority_patterns': ['领取质检|质检任务|提交质检合格|提交合格'],
            'auto_activation_queries': ['领取质检', '质检任务', '提交质检合格'],
            'requires_tool_evidence': True,
        }],
        tool_annotations={
            'prepare_erp_outsource_quality_claim': {'readOnlyHint': False},
            'prepare_erp_outsource_quality_pass': {'readOnlyHint': False},
        },
    ), model, gateway)

    assert result['response_kind'] == 'AWAITING_APPROVAL'
    assert gateway.calls[0][0] == 'prepare_erp_outsource_quality_pass'
    assert gateway.calls[0][1]['task_id'] == 15
    assert model.tool_names == []


def test_warehouse_todo_phrase_opens_supply_tasks_not_inbound():
    warehouse = {"priority_patterns": erp_outsource_warehouse_tools.SKILL_SPECS["outsource_warehouse_ops"]["priority_patterns"]}
    inbound = {"priority_patterns": erp_outsource_warehouse_inbound_tools.SKILL_SPECS["outsource_warehouse_inbound"]["priority_patterns"]}
    prompt = "查看有没有待办任务"
    assert harness._group_priority_matches(prompt, warehouse)
    assert not harness._group_priority_matches(prompt, inbound)
    assert harness._group_priority_matches("委外待发货", warehouse)
    assert harness._group_priority_matches("入库待办", inbound)
    assert not harness._group_priority_matches("入库待办", warehouse)
    assert not harness._group_priority_matches("收货待办", warehouse)

    supply = {'type': 'function', 'function': {
        'name': 'query_erp_outsource_warehouse_tasks',
        'description': '查询仓库委外待发料',
        'parameters': {'type': 'object', 'properties': {}, 'required': []},
    }}
    inbound_tool = {'type': 'function', 'function': {
        'name': 'query_erp_outsource_warehouse_inbound',
        'description': '查询仓库回厂收货入库待办',
        'parameters': {'type': 'object', 'properties': {}, 'required': []},
    }}
    final = {'role': 'assistant', 'content': json.dumps({
        'response_kind': 'BUSINESS',
        'summary': '仓库委外待发货 1 条。',
        'evidence_ids': ['e1'],
        'suggestions': [],
    }, ensure_ascii=False)}

    class RecordingGateway(Gateway):
        def __init__(self):
            super().__init__()
            self.calls = []

        def execute(self, seq, key, arguments):
            self.calls.append((key, arguments))
            return super().execute(seq, key, arguments)

    gateway = RecordingGateway()
    model = InspectingRepliesModel([final])
    result = run_loop(context(
        prompt=prompt,
        core_tool_names=[],
        tools=[supply, inbound_tool],
        skills=[{
            'key': 'outsource_warehouse_ops',
            'tools': ['query_erp_outsource_warehouse_tasks'],
        }, {
            'key': 'outsource_warehouse_inbound',
            'tools': ['query_erp_outsource_warehouse_inbound'],
        }],
    ), model, gateway)

    assert result['response_kind'] == 'BUSINESS'
    assert gateway.calls == [('query_erp_outsource_warehouse_tasks', {})]
    assert gateway.physical_calls == 1


def test_view_warehouse_todo_does_not_prepare_inbound():
    supply = {'type': 'function', 'function': {
        'name': 'query_erp_outsource_warehouse_tasks',
        'description': '查询仓库委外待办',
        'parameters': {'type': 'object', 'properties': {}, 'required': []},
    }}
    inbound = {'type': 'function', 'function': {
        'name': 'prepare_erp_outsource_warehouse_inbound',
        'description': '准备仓储入库确认',
        'parameters': {'type': 'object', 'properties': {'shipment_id': {'type': 'integer'}}},
    }}
    inbound_row = {
        'station': '待入库',
        'stationLabel': '待入库',
        'actionLabel': '仓储入库',
        'orderNo': 'EO-260928-WE11',
        'shipmentNo': 'PS-5136-441118',
        'pendingInboundQty': 30,
    }

    class BoardGateway(Gateway):
        def execute(self, seq, key, arguments):
            self.calls = getattr(self, 'calls', [])
            self.calls.append((key, arguments))
            if key == 'query_erp_outsource_warehouse_tasks':
                self.physical_calls += 1
                return {
                    'evidence_id': 'e1',
                    'data': {'items': [inbound_row], 'inboundItems': [inbound_row], 'inboundCount': 1},
                    'model_context': {'items': [inbound_row], 'item_count': 1},
                }
            self.physical_calls += 1
            return {'evidence_id': 'e2', 'proposal': {'kind': 'erp_outsource_warehouse_inbound'}}

    class GreedyPrepareModel(InspectingRepliesModel):
        def generate(self, messages, tools):
            names = [(tool.get('function') or {}).get('name') for tool in tools]
            self.tool_names.append(names)
            if 'prepare_erp_outsource_warehouse_inbound' in names:
                return {'role': 'assistant', 'tool_calls': [{
                    'id': 'p1', 'type': 'function',
                    'function': {
                        'name': 'prepare_erp_outsource_warehouse_inbound',
                        'arguments': '{"shipment_id":"PS-5136-441118"}',
                    },
                }]}
            return {'role': 'assistant', 'content': json.dumps({
                'response_kind': 'BUSINESS',
                'summary': '回厂待办 1 条。',
                'evidence_ids': ['e1'],
                'suggestions': [],
            }, ensure_ascii=False)}

    gateway = BoardGateway()
    model = GreedyPrepareModel([])
    result = run_loop(context(
        prompt='查看待办任务',
        core_tool_names=[],
        tools=[supply, inbound],
        skills=[{
            'key': 'outsource_warehouse_ops',
            'activation_route': 'authorized',
            'tools': ['query_erp_outsource_warehouse_tasks'],
            'optional_tools': [],
            'activation_tools': ['query_erp_outsource_warehouse_tasks'],
            'auto_activation_queries': ['查看待办', '待办任务'],
            'requires_tool_evidence': True,
            'host_auto_invoke_empty_arguments': True,
        }, {
            'key': 'outsource_warehouse_inbound',
            'activation_route': 'authorized',
            'tools': ['query_erp_outsource_warehouse_inbound'],
            'optional_tools': ['prepare_erp_outsource_warehouse_inbound'],
            'activation_tools': ['query_erp_outsource_warehouse_inbound'],
            'requires_tool_evidence': True,
            'host_auto_invoke_empty_arguments': True,
        }],
        tool_annotations={
            'query_erp_outsource_warehouse_tasks': {'readOnlyHint': True},
            'prepare_erp_outsource_warehouse_inbound': {'readOnlyHint': False},
        },
    ), model, gateway)

    assert result['response_kind'] == 'BUSINESS'
    assert [name for name, _arguments in gateway.calls] == ['query_erp_outsource_warehouse_tasks']
    assert all('prepare_erp_outsource_warehouse_inbound' not in names for names in model.tool_names)


def test_host_spoken_processor_receipt_uses_visible_row():
    receipt = {'type': 'function', 'function': {
        'name': 'prepare_erp_outsource_processor_receipt',
        'description': '准备确认原料收货',
        'parameters': {
            'type': 'object',
            'properties': {'board_row': {'type': 'integer'}},
        },
    }}

    class ProposalGateway(Gateway):
        def execute(self, seq, key, arguments):
            self.calls = getattr(self, 'calls', [])
            self.calls.append((key, arguments))
            if seq not in self.receipts:
                self.physical_calls += 1
                self.receipts[seq] = {
                    'evidence_id': 'e1',
                    'proposal': {
                        'kind': 'erp_outsource_processor_receipt',
                        'action': 'confirm_erp_outsource_processor_receipt',
                    },
                }
            return self.receipts[seq]

    gateway = ProposalGateway()
    model = InspectingRepliesModel([])
    result = run_loop(context(
        prompt='NO.1确认收货',
        core_tool_names=[],
        tools=[receipt],
        skills=[{
            'key': 'outsource_processor_fulfillment',
            'activation_route': 'authorized',
            'tools': ['query_erp_outsource_processor_fulfillment'],
            'optional_tools': ['prepare_erp_outsource_processor_receipt'],
        }],
        tool_annotations={
            'prepare_erp_outsource_processor_receipt': {'readOnlyHint': False},
        },
    ), model, gateway)

    assert result['response_kind'] == 'AWAITING_APPROVAL'
    assert gateway.calls == [('prepare_erp_outsource_processor_receipt', {'board_row': 1})]
    assert model.tool_names == []


def test_host_spoken_receipt_followup_without_row_prepares_card():
    receipt = {'type': 'function', 'function': {
        'name': 'prepare_erp_outsource_processor_receipt',
        'description': '准备确认原料收货',
        'parameters': {'type': 'object', 'properties': {'order_no': {'type': 'string'}}},
    }}
    board = {'type': 'function', 'function': {
        'name': 'query_erp_outsource_processor_board',
        'description': '查询本加工商委外待办',
        'parameters': {'type': 'object', 'properties': {}},
    }}

    class ProposalGateway(Gateway):
        def execute(self, seq, key, arguments):
            self.calls = getattr(self, 'calls', [])
            self.calls.append((key, arguments))
            if seq not in self.receipts:
                self.physical_calls += 1
                self.receipts[seq] = {
                    'evidence_id': 'e1',
                    'proposal': {'kind': 'erp_outsource_processor_receipt'},
                }
            return self.receipts[seq]

    gateway = ProposalGateway()
    model = InspectingRepliesModel([])
    result = run_loop(context(
        prompt='确认收货吧',
        core_tool_names=[],
        tools=[board, receipt],
        conversation_history=[{
            'user': {'content': '查看待办任务'},
            'assistant': {'summary': '当前有1条待办任务：零件委外订单 EO-260928-WE11，状态为待收料。仓库已发料，请确认收货。'},
        }],
        skills=[{
            'key': 'outsource_processor_query',
            'activation_route': 'authorized',
            'tools': ['query_erp_outsource_processor_board'],
            'optional_tools': [],
        }, {
            'key': 'outsource_processor_fulfillment',
            'activation_route': 'authorized',
            'tools': ['query_erp_outsource_processor_fulfillment'],
            'optional_tools': ['prepare_erp_outsource_processor_receipt'],
            'priority_patterns': ['确认收货'],
            'auto_activation_queries': ['确认收货'],
            'requires_tool_evidence': True,
            'host_auto_invoke_empty_arguments': True,
        }],
        tool_annotations={'prepare_erp_outsource_processor_receipt': {'readOnlyHint': False}},
    ), model, gateway)

    assert result['response_kind'] == 'AWAITING_APPROVAL', result
    assert gateway.calls[0][0] == 'prepare_erp_outsource_processor_receipt'
    assert model.tool_names == []


def test_receipt_followup_fits_small_context_window():
    """确认收货把 must not load every processor write schema into an 8k window."""
    def tool(name, description):
        return {'type': 'function', 'function': {
            'name': name,
            'description': description,
            'parameters': {'type': 'object', 'properties': {}},
        }}

    receipt = tool('prepare_erp_outsource_processor_receipt', '准备确认原料收货')
    extras = [
        tool(name, 'schema ' * 2000)
        for name in (
            'prepare_erp_outsource_processor_accept',
            'prepare_erp_outsource_processor_quote',
            'prepare_erp_outsource_processor_reject',
            'prepare_erp_outsource_processor_product_ship',
            'query_erp_outsource_processor_board',
            'query_erp_outsource_processor_fulfillment',
            'query_erp_outsource_processor_product_ship',
        )
    ]

    class ProposalGateway(Gateway):
        def execute(self, seq, key, arguments):
            self.calls = getattr(self, 'calls', [])
            self.calls.append((key, arguments))
            if seq not in self.receipts:
                self.physical_calls += 1
                self.receipts[seq] = {
                    'evidence_id': 'e1',
                    'proposal': {'kind': 'erp_outsource_processor_receipt'},
                }
            return self.receipts[seq]

    gateway = ProposalGateway()
    result = run_loop(context(
        prompt='确认收货把',
        core_tool_names=[],
        tools=[receipt, *extras],
        skills=[{
            'key': 'outsource_processor_ops',
            'activation_route': 'authorized',
            'tools': ['query_erp_outsource_processor_board'],
            'optional_tools': [
                'prepare_erp_outsource_processor_accept',
                'prepare_erp_outsource_processor_quote',
                'prepare_erp_outsource_processor_reject',
            ],
        }, {
            'key': 'outsource_processor_fulfillment',
            'activation_route': 'authorized',
            'tools': ['query_erp_outsource_processor_fulfillment'],
            'optional_tools': ['prepare_erp_outsource_processor_receipt'],
            'priority_patterns': ['确认收货'],
            'auto_activation_queries': ['确认收货'],
            'requires_tool_evidence': True,
            'host_auto_invoke_empty_arguments': True,
        }, {
            'key': 'outsource_processor_product_ship',
            'activation_route': 'authorized',
            'tools': ['query_erp_outsource_processor_product_ship'],
            'optional_tools': ['prepare_erp_outsource_processor_product_ship'],
        }],
        tool_annotations={'prepare_erp_outsource_processor_receipt': {'readOnlyHint': False}},
    ), InspectingRepliesModel([]), gateway, context_window=8192, max_output_tokens=2048)

    assert result['response_kind'] == 'AWAITING_APPROVAL', result
    assert gateway.calls == [('prepare_erp_outsource_processor_receipt', {})]


def test_product_ship_lookup_is_not_told_to_reread_the_todo_board():
    result = harness._situational_protocol_close(
        prompt='有没有可以成品发货的订单？',
        messages=[{
            'role': 'tool',
            'content': json.dumps({
                'model_context': {
                    'summary': '成品发货 1 条',
                    'items': [{
                        'station': '待成品发货',
                        'orderNo': 'EO-260928-WE11',
                        'moldNo': 'M260063-P1',
                    }],
                },
            }, ensure_ascii=False),
        }],
        evidence_ids=['e1'],
        attempted_tools={'query_erp_outsource_processor_product_ship'},
        formal_action_requested=False,
    )
    assert '待成品发货' in result['summary']
    assert 'EO-260928-WE11' in result['summary']
    assert result['summary'] != harness.QUERIED_FALLBACK_SUMMARY
    assert '确认成品发货' in result['suggestions']


def test_product_ship_close_replaces_generic_lookup_sentence():
    result = harness._situational_protocol_close(
        prompt='成品发货',
        messages=[{
            'role': 'tool',
            'content': json.dumps({
                'data': {
                    'items': [{
                        'action': 'product_ship',
                        'actionLabel': '成品发货',
                        'orderNo': 'EO-260928-WE11',
                        'moldNo': 'M260063-P1',
                    }],
                },
            }, ensure_ascii=False),
        }],
        evidence_ids=['e1'],
        attempted_tools={'query_erp_outsource_processor_product_ship'},
        formal_action_requested=False,
        last_model_message={'content': json.dumps({'summary': harness.QUERIED_FALLBACK_SUMMARY}, ensure_ascii=False)},
    )
    assert result['summary'] == '已经查到待成品发货 EO-260928-WE11 M260063-P1。请确认下一步办理。'
    assert '确认成品发货' in result['suggestions']


def test_host_spoken_product_ship_prepares_card():
    prepare = {'type': 'function', 'function': {
        'name': 'prepare_erp_outsource_processor_product_ship',
        'description': '准备成品发货',
        'parameters': {'type': 'object', 'properties': {'order_no': {'type': 'string'}}},
    }}
    query = {'type': 'function', 'function': {
        'name': 'query_erp_outsource_processor_product_ship',
        'description': '查询可成品发货',
        'parameters': {'type': 'object', 'properties': {}},
    }}

    class ProposalGateway(Gateway):
        def execute(self, seq, key, arguments):
            self.calls = getattr(self, 'calls', [])
            self.calls.append((key, arguments))
            if seq not in self.receipts:
                self.physical_calls += 1
                self.receipts[seq] = {
                    'evidence_id': 'e1',
                    'proposal': {'kind': 'erp_outsource_processor_product_ship'},
                }
            return self.receipts[seq]

    gateway = ProposalGateway()
    model = InspectingRepliesModel([])
    result = run_loop(context(
        prompt='确认成品发货',
        core_tool_names=[],
        tools=[query, prepare],
        conversation_history=[{
            'user': {'content': '成品发货'},
            'assistant': {'summary': '可成品发货 EO-260928-WE11 M260063-P1'},
        }],
        skills=[{
            'key': 'outsource_processor_product_ship',
            'activation_route': 'authorized',
            'tools': ['query_erp_outsource_processor_product_ship'],
            'optional_tools': ['prepare_erp_outsource_processor_product_ship'],
            'priority_patterns': ['成品发货'],
            'auto_activation_queries': ['成品发货'],
            'requires_tool_evidence': True,
            'host_auto_invoke_empty_arguments': True,
        }],
        tool_annotations={'prepare_erp_outsource_processor_product_ship': {'readOnlyHint': False}},
    ), model, gateway)

    assert result['response_kind'] == 'AWAITING_APPROVAL', result
    assert gateway.calls[0][0] == 'prepare_erp_outsource_processor_product_ship'
    assert gateway.calls[0][1]['order_no'] == 'EO-260928-WE11'
    assert model.tool_names == []


def test_host_spoken_ship_it_prepares_product_ship_card():
    prepare = {'type': 'function', 'function': {
        'name': 'prepare_erp_outsource_processor_product_ship',
        'description': '准备成品发货',
        'parameters': {'type': 'object', 'properties': {'order_no': {'type': 'string'}}},
    }}
    query = {'type': 'function', 'function': {
        'name': 'query_erp_outsource_processor_product_ship',
        'description': '查询可成品发货',
        'parameters': {'type': 'object', 'properties': {}, 'additionalProperties': False},
    }}
    board = {'type': 'function', 'function': {
        'name': 'query_erp_outsource_processor_board',
        'description': '查询本加工商委外待办',
        'parameters': {'type': 'object', 'properties': {}, 'additionalProperties': False},
    }}

    class ProposalGateway(Gateway):
        def execute(self, seq, key, arguments):
            self.calls = getattr(self, 'calls', [])
            self.calls.append((key, arguments))
            if seq not in self.receipts:
                self.physical_calls += 1
                self.receipts[seq] = {
                    'evidence_id': 'e1',
                    'proposal': {'kind': 'erp_outsource_processor_product_ship'},
                }
            return self.receipts[seq]

    gateway = ProposalGateway()
    result = run_loop(context(
        prompt='发货把',
        core_tool_names=[],
        tools=[board, query, prepare],
        conversation_history=[{
            'user': {'content': '有没有可以成品发货的订单'},
            'assistant': {'summary': '可成品发货 EO-261008-5A0M M260063-P4'},
        }],
        skills=[{
            'key': 'outsource_processor_query',
            'activation_route': 'authorized',
            'tools': ['query_erp_outsource_processor_board'],
            'optional_tools': [],
            'auto_activation_queries': ['待办', '有没有'],
            'host_auto_invoke_empty_arguments': True,
        }, {
            'key': 'outsource_processor_product_ship',
            'activation_route': 'authorized',
            'tools': ['query_erp_outsource_processor_product_ship'],
            'optional_tools': ['prepare_erp_outsource_processor_product_ship'],
            'priority_patterns': ['成品发货|发货把'],
            'auto_activation_queries': ['成品发货', '发货把'],
            'requires_tool_evidence': True,
            'host_auto_invoke_empty_arguments': True,
        }],
        tool_annotations={'prepare_erp_outsource_processor_product_ship': {'readOnlyHint': False}},
    ), InspectingRepliesModel([]), gateway)

    assert result['response_kind'] == 'AWAITING_APPROVAL', result
    assert gateway.calls[0][0] == 'prepare_erp_outsource_processor_product_ship'
    assert gateway.calls[0][1]['order_no'] == 'EO-261008-5A0M'


def test_host_spoken_product_ship_ba_prepares_card():
    prepare = {'type': 'function', 'function': {
        'name': 'prepare_erp_outsource_processor_product_ship',
        'description': '准备成品发货',
        'parameters': {'type': 'object', 'properties': {'order_no': {'type': 'string'}}},
    }}
    query = {'type': 'function', 'function': {
        'name': 'query_erp_outsource_processor_product_ship',
        'description': '查询可成品发货',
        'parameters': {'type': 'object', 'properties': {}, 'additionalProperties': False},
    }}

    class ProposalGateway(Gateway):
        def execute(self, seq, key, arguments):
            self.calls = getattr(self, 'calls', [])
            self.calls.append((key, arguments))
            if seq not in self.receipts:
                self.physical_calls += 1
                self.receipts[seq] = {
                    'evidence_id': 'e1',
                    'proposal': {'kind': 'erp_outsource_processor_product_ship'},
                }
            return self.receipts[seq]

    gateway = ProposalGateway()
    result = run_loop(context(
        prompt='成品发货吧。',
        core_tool_names=[],
        tools=[query, prepare],
        conversation_history=[{
            'user': {'content': '有没有可以成品发货的订单'},
            'assistant': {'summary': '可成品发货 EO-261008-5A0M M260063-P4'},
        }],
        skills=[{
            'key': 'outsource_processor_product_ship',
            'activation_route': 'authorized',
            'tools': ['query_erp_outsource_processor_product_ship'],
            'optional_tools': ['prepare_erp_outsource_processor_product_ship'],
            'priority_patterns': ['成品发货吧|成品发货'],
            'auto_activation_queries': ['成品发货', '成品发货吧'],
            'requires_tool_evidence': True,
            'host_auto_invoke_empty_arguments': True,
        }],
        tool_annotations={'prepare_erp_outsource_processor_product_ship': {'readOnlyHint': False}},
    ), InspectingRepliesModel([]), gateway)

    assert result['response_kind'] == 'AWAITING_APPROVAL', result
    assert [name for name, _arguments in gateway.calls] == ['prepare_erp_outsource_processor_product_ship']
    assert gateway.calls[0][1]['order_no'] == 'EO-261008-5A0M'


def test_host_spoken_ship_all_does_not_grab_stale_order():
    prepare = {'type': 'function', 'function': {
        'name': 'prepare_erp_outsource_processor_product_ship',
        'description': '准备成品发货',
        'parameters': {'type': 'object', 'properties': {'order_no': {'type': 'string'}}},
    }}
    query = {'type': 'function', 'function': {
        'name': 'query_erp_outsource_processor_product_ship',
        'description': '查询可成品发货',
        'parameters': {'type': 'object', 'properties': {}, 'additionalProperties': False},
    }}

    class ProposalGateway(Gateway):
        def execute(self, seq, key, arguments):
            self.calls = getattr(self, 'calls', [])
            self.calls.append((key, arguments))
            if seq not in self.receipts:
                self.physical_calls += 1
                self.receipts[seq] = {
                    'evidence_id': 'e1',
                    'proposal': {'kind': 'erp_outsource_processor_product_ship'},
                }
            return self.receipts[seq]

    gateway = ProposalGateway()
    result = run_loop(context(
        prompt='发货吧',
        core_tool_names=[],
        tools=[query, prepare],
        conversation_history=[{
            'user': {'content': '查看待办'},
            'assistant': {'summary': '待接单 EO-260928-WE11 M210236-P1'},
        }, {
            'user': {'content': '成品发货'},
            'assistant': {'summary': '可成品发货 EO-261009-L6ON、EO-261009-IPBI M260063-P1'},
        }],
        skills=[{
            'key': 'outsource_processor_product_ship',
            'activation_route': 'authorized',
            'tools': ['query_erp_outsource_processor_product_ship'],
            'optional_tools': ['prepare_erp_outsource_processor_product_ship'],
            'priority_patterns': ['发货吧|成品发货'],
            'auto_activation_queries': ['成品发货', '发货吧'],
            'requires_tool_evidence': True,
            'host_auto_invoke_empty_arguments': True,
        }],
        tool_annotations={'prepare_erp_outsource_processor_product_ship': {'readOnlyHint': False}},
    ), InspectingRepliesModel([]), gateway)

    assert result['response_kind'] == 'AWAITING_APPROVAL', result
    assert gateway.calls[0][0] == 'prepare_erp_outsource_processor_product_ship'
    assert gateway.calls[0][1] == {'ship_all': True}


def test_buyer_account_does_not_query_board_on_processor_accept_speech():
    board = {'type': 'function', 'function': {
        'name': 'query_erp_outsource_followup_board',
        'description': '查询委外采购待办',
        'parameters': {'type': 'object', 'properties': {'question': {'type': 'string'}}},
    }}
    quote = {'type': 'function', 'function': {
        'name': 'prepare_erp_outsource_buyer_quote',
        'description': '准备填写我方报价',
        'parameters': {'type': 'object', 'properties': {}},
    }}
    clarification = {'role': 'assistant', 'content': json.dumps({
        'response_kind': 'CLARIFICATION',
        'summary': '接单请用加工商账号登录后再说同一句话。',
        'evidence_ids': [],
        'suggestions': [],
    }, ensure_ascii=False)}

    class RecordingGateway(Gateway):
        def __init__(self):
            super().__init__()
            self.calls = []

        def execute(self, seq, key, arguments):
            self.calls.append((key, arguments))
            return super().execute(seq, key, arguments)

    gateway = RecordingGateway()
    result = run_loop(context(
        prompt='订单 EO-260924-A5SS，模具 M260063 批次 M260063-P4，零件 PU-03 冲头，我接了',
        core_tool_names=[],
        tools=[board, quote],
        skills=[{
            'key': 'outsource_followup_query',
            'activation_route': 'authorized',
            'tools': ['query_erp_outsource_followup_board'],
            'optional_tools': [],
            'host_auto_invoke_empty_arguments': True,
            'auto_activation_queries': ['委外待办'],
        }],
        tool_annotations={'prepare_erp_outsource_buyer_quote': {'readOnlyHint': False}},
    ), InspectingRepliesModel([clarification]), gateway)

    assert gateway.calls == []
    assert result['response_kind'] == 'CLARIFICATION'


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

    catalog = {
        'query_erp_outsource_approval_todos',
        'prepare_erp_outsource_approval_pass',
    }
    from domain_packs.mold.harness_policy import spoken_write_ensure_tools
    assert 'prepare_erp_outsource_approval_pass' in spoken_write_ensure_tools(
        'M260063这单先过了吧',
        catalog,
    )
    invoked = spoken_write_auto_invoke('M260063这单先过了吧', catalog)
    assert invoked is not None
    assert invoked[0] == 'prepare_erp_outsource_approval_pass'


def test_authorized_processor_todo_question_loads_board_without_order_keyword():
    board = {'type': 'function', 'function': {
        'name': 'query_erp_outsource_processor_board',
        'description': '查询本加工商委外待办',
    }}
    quote = {'type': 'function', 'function': {
        'name': 'prepare_erp_outsource_processor_quote',
        'description': '准备提交本加工商报价',
    }}
    names = _authorized_run(
        '有需要处理的待办吗',
        [board, quote],
        [{
            'key': 'outsource_processor_query',
            'activation_route': 'authorized',
            'tools': ['query_erp_outsource_processor_board'],
            'optional_tools': [],
            'auto_activation_queries': ['我的委外', '待办'],
            'host_auto_invoke_empty_arguments': False,
        }],
        {'prepare_erp_outsource_processor_quote': {'readOnlyHint': False}},
    )
    assert 'query_erp_outsource_processor_board' in names
    assert 'prepare_erp_outsource_processor_quote' not in names


def test_host_auto_invokes_product_ship_not_processor_board():
    board = {'type': 'function', 'function': {
        'name': 'query_erp_outsource_processor_board',
        'description': '查询本加工商委外待办',
        'parameters': {'type': 'object', 'properties': {}, 'additionalProperties': False},
    }}
    product = {'type': 'function', 'function': {
        'name': 'query_erp_outsource_processor_product_ship',
        'description': '查询可成品发货',
        'parameters': {'type': 'object', 'properties': {}, 'additionalProperties': False},
    }}
    final = {'role': 'assistant', 'content': json.dumps({
        'response_kind': 'BUSINESS',
        'summary': '可成品发货 1 条。',
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
    run_loop(context(
        prompt='有没有可以成品发货的订单',
        core_tool_names=[],
        tools=[board, product],
        skills=[
            {
                'key': 'outsource_processor_query',
                'activation_route': 'authorized',
                'tools': ['query_erp_outsource_processor_board'],
                'optional_tools': [],
                'auto_activation_queries': ['我的委外', '待办', '查看待办'],
                'host_auto_invoke_empty_arguments': True,
                'requires_tool_evidence': True,
            },
            {
                'key': 'outsource_processor_product_ship',
                'activation_route': 'authorized',
                'tools': ['query_erp_outsource_processor_product_ship'],
                'optional_tools': [],
                'auto_activation_queries': ['成品发货', '有没有发货'],
                'priority_patterns': ['成品发货'],
                'host_auto_invoke_empty_arguments': True,
                'requires_tool_evidence': True,
            },
        ],
    ), InspectingRepliesModel([final, final, final, final]), gateway)
    assert gateway.names == ['query_erp_outsource_processor_product_ship']
    assert harness._choose_host_auto_invoke(
        '有没有可以成品发货的订单',
        ['query_erp_outsource_processor_board', 'query_erp_outsource_processor_product_ship'],
    ) == 'query_erp_outsource_processor_product_ship'


def test_listing_question_host_invokes_board_not_progress():
    board = {'type': 'function', 'function': {
        'name': 'query_erp_outsource_followup_board',
        'description': '查询委外采购待办',
        'parameters': {'type': 'object', 'properties': {}, 'additionalProperties': False},
    }}
    progress = {'type': 'function', 'function': {
        'name': 'query_erp_outsource_order_progress',
        'description': '查询委外单进度',
        'parameters': {'type': 'object', 'properties': {}, 'additionalProperties': False},
    }}
    processor = {'type': 'function', 'function': {
        'name': 'query_erp_outsource_processor_board',
        'description': '查询本加工商委外待办',
        'parameters': {'type': 'object', 'properties': {}, 'additionalProperties': False},
    }}
    final = {'role': 'assistant', 'content': json.dumps({
        'response_kind': 'BUSINESS',
        'summary': '当前委外待办已列出。',
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
    model = InspectingRepliesModel([final, final, final, final])
    run_loop(context(
        prompt='现在有委外单子吗',
        core_tool_names=[],
        tools=[board, progress, processor],
        skills=[
            {
                'key': 'outsource_followup_query',
                'activation_route': 'authorized',
                'tools': ['query_erp_outsource_followup_board'],
                'optional_tools': ['query_erp_outsource_order_progress'],
                'auto_activation_queries': ['委外单子', '有委外', '有几个'],
                'priority_patterns': ['委外单子|有几个委外'],
                'host_auto_invoke_empty_arguments': True,
                'requires_tool_evidence': True,
            },
            {
                'key': 'outsource_processor_query',
                'activation_route': 'authorized',
                'tools': ['query_erp_outsource_processor_board'],
                'optional_tools': [],
                'auto_activation_queries': ['我的委外', '待办'],
                'host_auto_invoke_empty_arguments': True,
            },
        ],
    ), model, gateway)
    assert gateway.names == ['query_erp_outsource_followup_board']
    assert 'query_erp_outsource_order_progress' not in (model.tool_names[0] if model.tool_names else [])


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
