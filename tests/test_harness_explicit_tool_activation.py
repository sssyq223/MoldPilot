import json

from app.harness import run_loop
from test_model_harness import Gateway, InspectingRepliesModel


def test_post_proposal_continuation_activates_required_tool_without_tool_search():
    attachment = {
        'type': 'function',
        'function': {'name': 'prepare_contact_attach', 'description': '准备关联联络单附件'},
    }
    model = InspectingRepliesModel([
        {'role': 'assistant', 'tool_calls': [{
            'id': 'attach-1', 'type': 'function',
            'function': {'name': 'prepare_contact_attach', 'arguments': '{}'},
        }]},
        {'role': 'assistant', 'content': json.dumps({
            'response_kind': 'AWAITING_APPROVAL', 'summary': '已准备附件关联建议，等待本人确认',
            'evidence_ids': ['e1'], 'suggestions': [], 'proposal_decision': 'approved',
        }, ensure_ascii=False)},
    ])

    class ProposalGateway(Gateway):
        def execute(self, seq, key, arguments):
            self.physical_calls += 1
            return {'evidence_id': 'e1', 'source': 'agent_proposal',
                    'proposal': {'action': 'attach'}}

    gateway = ProposalGateway()
    context = {
        'prompt': '工程联络单附件关联',
        'tools': [attachment],
        'skills': [],
        'core_tool_names': [],
        'post_proposal_continuation': True,
        'continuation_tool': 'prepare_contact_attach',
        'proposal_resolution': {
            'decision': 'approved',
            'authoritative_receipt': {'status': 'CONFIRMED'},
        },
    }
    result = run_loop(context, model, gateway)
    assert model.tool_names[0] == ['prepare_contact_attach']
    assert result['response_kind'] == 'AWAITING_APPROVAL'
    assert gateway.physical_calls == 1


def test_agent_proposal_closes_tool_stage_and_prevents_duplicate_execution():
    proposal_tool = {
        'type': 'function',
        'function': {'name': 'prepare_demo_action', 'description': '准备演示操作'},
    }
    first_call = {'role': 'assistant', 'tool_calls': [{
        'id': 'proposal-1', 'type': 'function',
        'function': {'name': 'prepare_demo_action', 'arguments': '{}'},
    }]}
    duplicate_call = {'role': 'assistant', 'tool_calls': [{
        'id': 'proposal-2', 'type': 'function',
        'function': {'name': 'prepare_demo_action', 'arguments': '{}'},
    }]}
    final = {'role': 'assistant', 'content': json.dumps({
        'response_kind': 'AWAITING_APPROVAL',
        'summary': '操作建议已生成，等待本人确认。',
        'evidence_ids': ['proposal-e1'], 'suggestions': [],
    }, ensure_ascii=False)}

    class ProposalGateway(Gateway):
        def execute(self, seq, key, arguments):
            self.physical_calls += 1
            return {'evidence_id': 'proposal-e1', 'source': 'agent_proposal',
                    'proposal': {'action': 'demo'}}

    model = InspectingRepliesModel([first_call, duplicate_call, final])
    gateway = ProposalGateway()
    result = run_loop({
        'prompt': '请准备工程联络单操作',
        'tools': [proposal_tool],
        'skills': [],
        'core_tool_names': ['prepare_demo_action'],
        'active_tool_names': ['prepare_demo_action'],
        'tool_annotations': {'prepare_demo_action': {'readOnlyHint': False}},
    }, model, gateway)

    assert result['response_kind'] == 'AWAITING_APPROVAL'
    assert gateway.physical_calls == 1
    assert model.tool_names[1] == []
    assert gateway.saved['finalizing'] is True
    assert gateway.saved['protocol_repairs'] == 1


def test_exact_tool_name_in_ui_action_prompt_is_activated_without_tool_search():
    readiness = {
        'type': 'function',
        'function': {'name': 'query_internal_start_readiness', 'description': '核对正式开工条件'},
    }
    admin_update = {
        'type': 'function',
        'function': {'name': 'prepare_admin_start_notice_update', 'description': '准备补充内部开工通知资料'},
    }
    model = InspectingRepliesModel([
        {'role': 'assistant', 'tool_calls': [{
            'id': 'update-1', 'type': 'function',
            'function': {'name': 'prepare_admin_start_notice_update', 'arguments': '{}'},
        }]},
        {'role': 'assistant', 'content': json.dumps({
            'response_kind': 'CLARIFICATION', 'summary': '已准备操作建议', 'evidence_ids': ['e1'], 'suggestions': [],
        }, ensure_ascii=False)},
        {'role': 'assistant', 'content': json.dumps({
            'response_kind': 'CLARIFICATION', 'summary': '操作建议已生成', 'evidence_ids': ['e1'], 'suggestions': [],
        }, ensure_ascii=False)},
    ])
    gateway = Gateway()
    context = {
        'prompt': '请准备内部开工通知，并使用 prepare_admin_start_notice_update 工具处理草稿。',
        'tools': [readiness, admin_update],
        'skills': [{
            'key': 'internal_start_readiness', 'tools': ['query_internal_start_readiness'],
            'auto_activation_queries': ['内部开工通知'],
            'suppress_tool_search_on_auto_activation': True,
        }],
        'core_tool_names': [],
    }
    run_loop(context, model, gateway)
    assert 'prepare_admin_start_notice_update' in model.tool_names[0]
    assert gateway.physical_calls == 1
