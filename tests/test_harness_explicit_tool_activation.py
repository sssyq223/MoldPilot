import json

from app.harness import run_loop
from test_model_harness import Gateway, InspectingRepliesModel


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
    assert gateway.executed_tools == ['prepare_admin_start_notice_update']
