import copy
import json
import ssl
import time

import httpx
import pytest
from sqlalchemy import text

import agent_core.harness as harness_module
from app.harness import run_loop
from app.model_adapter import ModelAdapter, ModelError, tls_context


def test_tls_compatibility_keeps_certificate_and_hostname_verification():
    context = tls_context(key_exchange="x25519")
    assert context.verify_mode == ssl.CERT_REQUIRED
    assert context.check_hostname
    assert context.minimum_version == ssl.TLSVersion.TLSv1_2
    assert context.maximum_version != ssl.TLSVersion.TLSv1_2
    with pytest.raises(ValueError): tls_context(key_exchange="insecure")


def test_model_request_shape_and_tool_call():
    def serve(request):
        body = json.loads(request.content)
        assert str(request.url) == 'https://model.example/v1/chat/completions'
        assert request.headers['authorization'] == 'Bearer synthetic-key'
        assert body['model'] == 'test-model'
        assert 'tools' not in body
        assert body['response_format'] == {'type': 'json_object'}
        return httpx.Response(200, json={'choices': [{'finish_reason': 'tool_calls', 'message': {
            'role': 'assistant', 'tool_calls': [{'id': 'c1', 'function': {'name': 'query_projects', 'arguments': '{}'}}]}}]})
    model = ModelAdapter('https://model.example/v1', 'synthetic-key', 'test-model', transport=httpx.MockTransport(serve))
    assert model.generate([{'role': 'user', 'content': 'query'}], [])['tool_calls'][0]['id'] == 'c1'


def test_tool_stage_does_not_force_terminal_json_mode():
    def serve(request):
        body = json.loads(request.content)
        assert 'response_format' not in body
        assert body['tools'] == [TOOL]
        return httpx.Response(200, json={'choices': [{'finish_reason': 'tool_calls', 'message': {
            'role': 'assistant', 'tool_calls': [{'id': 'c1', 'function': {
                'name': 'query_projects', 'arguments': '{}'}}]}}]})

    model = ModelAdapter('https://model.example/v1', 'synthetic-key', 'test-model',
                         transport=httpx.MockTransport(serve))
    assert model.generate([{'role': 'user', 'content': 'query'}], [TOOL])['tool_calls'][0]['id'] == 'c1'


def test_model_stream_coalesces_visible_text_and_tool_call_deltas():
    chunks = [
        {'choices': [{'delta': {'role': 'assistant', 'content': '我先查询'}, 'finish_reason': None}]},
        {'choices': [{'delta': {'content': '项目计划。', 'tool_calls': [{
            'index': 0, 'id': 'c1', 'type': 'function',
            'function': {'name': 'query_project_', 'arguments': '{"project_'}
        }]}, 'finish_reason': None}]},
        {'choices': [{'delta': {'tool_calls': [{
            'index': 0, 'function': {'name': 'plan_context', 'arguments': 'code":"SMOKE-M001"}'}
        }]}, 'finish_reason': 'tool_calls'}],
         'usage': {'prompt_tokens': 12, 'completion_tokens': 8, 'total_tokens': 20}},
    ]
    body = ''.join('data: '+json.dumps(chunk)+'\n\n' for chunk in chunks)+'data: [DONE]\n\n'

    def serve(request):
        payload = json.loads(request.content)
        assert payload['stream'] is True
        assert payload['stream_options'] == {'include_usage': True}
        return httpx.Response(200, text=body, headers={'content-type': 'text/event-stream'})

    model = ModelAdapter('https://model.example/v1', 'synthetic-key', 'test-model',
                         transport=httpx.MockTransport(serve))
    updates = []
    message = model.generate_stream([{'role': 'user', 'content': 'query'}], [TOOL],
                                    lambda value: updates.append(copy.deepcopy(value)))

    assert message['content'] == '我先查询项目计划。'
    assert message['tool_calls'][0]['id'] == 'c1'
    assert message['tool_calls'][0]['function'] == {
        'name': 'query_project_plan_context',
        'arguments': '{"project_code":"SMOKE-M001"}',
    }
    assert updates[0]['content'] == '我先查询'
    assert updates[-1] == message
    assert model.last_metrics['total_tokens'] == 20


def test_model_stream_retries_one_upstream_5xx_before_any_sse_delta():
    calls = []
    body = 'data: '+json.dumps({'choices': [{'delta': {'role': 'assistant', 'content': '恢复成功'},
                                                  'finish_reason': 'stop'}]})+'\n\ndata: [DONE]\n\n'

    def serve(request):
        calls.append(request)
        if len(calls) == 1:
            return httpx.Response(504, text='private gateway detail')
        return httpx.Response(200, text=body, headers={'content-type': 'text/event-stream'})

    model = ModelAdapter('https://model.example/v1', 'synthetic-key', 'test-model',
                         transport=httpx.MockTransport(serve))
    message = model.generate_stream([{'role': 'user', 'content': 'query'}], [], lambda _: None)

    assert message['content'] == '恢复成功'
    assert len(calls) == 2
    assert model.last_metrics['retry_count'] == 1


def test_model_stream_retries_read_timeout_before_first_sse_delta():
    calls = []
    body = 'data: '+json.dumps({'choices': [{'delta': {'role': 'assistant', 'content': '重试成功'},
                                                  'finish_reason': 'stop'}]})+'\n\ndata: [DONE]\n\n'

    def serve(request):
        calls.append(request)
        if len(calls) == 1:
            raise httpx.ReadTimeout('provider stayed silent', request=request)
        return httpx.Response(200, text=body, headers={'content-type': 'text/event-stream'})

    model = ModelAdapter('https://model.example/v1', 'synthetic-key', 'test-model',
                         transport=httpx.MockTransport(serve))
    message = model.generate_stream([{'role': 'user', 'content': 'query'}], [], lambda _: None)

    assert message['content'] == '重试成功'
    assert len(calls) == 2
    assert model.last_metrics['retry_count'] == 1
    assert model.last_metrics['retry_reason'] == 'read_timeout_before_first_chunk'
    assert model.last_metrics['retry_wait_ms'] >= 0
    assert model.last_metrics['http_status'] == 200


def test_model_stream_retries_connect_timeout_before_first_sse_delta():
    calls = []
    body = 'data: '+json.dumps({'choices': [{'delta': {'role': 'assistant', 'content': '连接恢复'},
                                                  'finish_reason': 'stop'}]})+'\n\ndata: [DONE]\n\n'

    def serve(request):
        calls.append(request)
        if len(calls) == 1:
            raise httpx.ConnectTimeout('TLS handshake stalled', request=request)
        return httpx.Response(200, text=body, headers={'content-type': 'text/event-stream'})

    model = ModelAdapter('https://model.example/v1', 'synthetic-key', 'test-model',
                         transport=httpx.MockTransport(serve))
    message = model.generate_stream([{'role': 'user', 'content': 'query'}], [], lambda _: None)

    assert message['content'] == '连接恢复'
    assert len(calls) == 2
    assert model.last_metrics['retry_count'] == 1
    assert model.last_metrics['retry_reason'] == 'connect_timeout_before_first_chunk'
    assert model.last_metrics['retry_wait_ms'] >= 0


def test_model_stream_does_not_retry_non_transient_http_error():
    calls = []

    def serve(request):
        calls.append(request)
        return httpx.Response(400, text='private validation detail')

    model = ModelAdapter('https://model.example/v1', 'synthetic-key', 'test-model',
                         transport=httpx.MockTransport(serve))
    with pytest.raises(ModelError, match='MODEL_HTTP_FAILED'):
        model.generate_stream([], [], lambda _: None)

    assert len(calls) == 1
    assert model.last_metrics['http_status'] == 400


@pytest.mark.parametrize('status,code', [(401,'MODEL_AUTH_FAILED'), (403,'MODEL_AUTH_FAILED'),
                                        (429,'MODEL_RATE_LIMITED'), (500,'MODEL_HTTP_FAILED'),
                                        (302,'MODEL_HTTP_FAILED')])
def test_upstream_errors_never_expose_response_or_credentials(status, code):
    calls = []
    def serve(request):
        calls.append(request)
        return httpx.Response(status, text='secret credential should never appear', headers={'location': 'https://elsewhere.example'})
    model = ModelAdapter('https://model.example/v1', 'synthetic-key', 'test', transport=httpx.MockTransport(serve))
    with pytest.raises(ModelError, match=code) as error: model.generate([], [])
    assert str(error.value) == code
    assert len(calls) == 1


@pytest.mark.parametrize('exception,code', [(httpx.ConnectTimeout,'MODEL_CONNECT_TIMEOUT'), (httpx.ReadTimeout,'MODEL_READ_TIMEOUT')])
def test_timeout_errors_are_specific(exception, code):
    def serve(request): raise exception('private upstream details')
    model = ModelAdapter('https://model.example/v1','key','test', transport=httpx.MockTransport(serve))
    with pytest.raises(ModelError, match=code): model.generate([], [])


@pytest.mark.parametrize('body', [{'choices': []}, {'choices': [{'message': []}]},
                                {'choices': [{'finish_reason': 'length', 'message': {'role': 'assistant', 'content': 'partial'}}]}])
def test_invalid_or_truncated_model_output_fails_closed(body):
    model = ModelAdapter('https://model.example/v1','key','test', transport=httpx.MockTransport(lambda r: httpx.Response(200,json=body)))
    with pytest.raises(ModelError): model.generate([], [])


TOOL = {'type': 'function', 'function': {'name': 'query_projects'}}
OTHER_TOOL = {'type': 'function', 'function': {'name': 'query_project_plan_context',
                                               'description': '按项目线索核对项目计划、节点进度、依赖、逾期和大节点覆盖；只读。'}}
BASELINE_TOOL = {'type': 'function', 'function': {'name': 'prepare_project_plan_baseline',
                                                  'description': '准备项目基线计划审批建议。'}}
PLAN_CHANGE_TOOL = {'type': 'function', 'function': {'name': 'prepare_project_plan_change',
                                                     'description': '准备项目计划变更审批建议。'}}
CONTACT_CASES_TOOL = {'type': 'function', 'function': {'name': 'query_contact_cases',
                                                       'description': '查询工程联络单列表和协作状态。'}}
CONTACT_CONTEXT_TOOL = {'type': 'function', 'function': {'name': 'query_contact_context',
                                                         'description': '读取指定工程联络单的办理上下文。'}}
CONTACT_RESOLUTION_TOOL = {'type': 'function', 'function': {'name': 'prepare_contact_resolution',
                                                            'description': '准备处理方案审批建议。'}}
CONTACT_REVIEW_TOOL = {'type': 'function', 'function': {'name': 'prepare_contact_review',
                                                        'description': '准备复验处理结果建议。'}}
CONTACT_CLOSE_TOOL = {'type': 'function', 'function': {'name': 'prepare_contact_close',
                                                       'description': '准备人工关闭联络单建议。'}}
CONTACT_RESPOND_TOOL = {'type': 'function', 'function': {'name': 'prepare_contact_respond',
                                                         'description': '准备提交联络反馈建议。'}}
CONTACT_ASSIGN_TOOL = {'type': 'function', 'function': {'name': 'prepare_contact_assign',
                                                        'description': '准备分派联络处理人建议。'}}
TOOL_SEARCH = {'role': 'assistant', 'tool_calls': [{'id': 'search1', 'type': 'function', 'function': {'name': 'ToolSearch', 'arguments': json.dumps({'query': 'query_projects'})}}]}
PLAN_TOOL_SEARCH = {'role': 'assistant', 'tool_calls': [{'id': 'search-plan', 'type': 'function', 'function': {'name': 'ToolSearch', 'arguments': json.dumps({'query': '项目计划'})}}]}
CONTACT_TOOL_SEARCH = {'role': 'assistant', 'tool_calls': [{'id': 'search-contact', 'type': 'function', 'function': {'name': 'ToolSearch', 'arguments': json.dumps({'query': '工程联络关闭'})}}]}
CONTRACT_TOOL_SEARCH = {'role': 'assistant', 'tool_calls': [{'id': 'search-contract', 'type': 'function', 'function': {'name': 'ToolSearch', 'arguments': json.dumps({'query': '合同登记'})}}]}
HALLUCINATED_SKILL_CALL = {'role': 'assistant', 'tool_calls': [{'id': 'bad-skill', 'type': 'function', 'function': {'name': 'business_object_matching', 'arguments': json.dumps({'query': 'SMOKE-M001'})}}]}
PROPOSAL = {'role': 'assistant', 'tool_calls': [{'id': 'call1', 'type': 'function', 'function': {'name': 'query_projects', 'arguments': '{}'}}]}
PLAN_PROPOSAL = {'role': 'assistant', 'tool_calls': [{'id': 'call-plan', 'type': 'function', 'function': {'name': 'query_project_plan_context', 'arguments': '{}'}}]}
CONTACT_PROPOSAL = {'role': 'assistant', 'tool_calls': [{'id': 'call-contact', 'type': 'function', 'function': {'name': 'query_contact_context', 'arguments': '{}'}}]}
CONTACT_CLOSE_PROPOSAL = {'role': 'assistant', 'tool_calls': [{'id': 'call-contact-close', 'type': 'function', 'function': {'name': 'prepare_contact_close', 'arguments': '{}'}}]}
CONTRACT_PROPOSAL = {'role': 'assistant', 'tool_calls': [{'id': 'call-contract', 'type': 'function', 'function': {'name': 'query_contract_context', 'arguments': '{}'}}]}
CONTRACT_RECORD_PROPOSAL = {'role': 'assistant', 'tool_calls': [{'id': 'call-contract-record', 'type': 'function', 'function': {'name': 'prepare_contract_record', 'arguments': '{}'}}]}
OUTSOURCE_PROGRESS_TOOL_SEARCH = {'role': 'assistant', 'tool_calls': [{'id': 'search-outsource-progress', 'type': 'function', 'function': {'name': 'ToolSearch', 'arguments': json.dumps({'query': '供应商节点上报'})}}]}
OUTSOURCE_POLICY_TOOL_SEARCH = {'role': 'assistant', 'tool_calls': [{'id': 'search-outsource-policy', 'type': 'function', 'function': {'name': 'ToolSearch', 'arguments': json.dumps({'query': '登记供应商上报频率和证据模板'})}}]}
OUTSOURCE_MATERIAL_VERIFY_TOOL_SEARCH = {'role': 'assistant', 'tool_calls': [{'id': 'search-outsource-material-verify', 'type': 'function', 'function': {'name': 'ToolSearch', 'arguments': json.dumps({'query': '登记供应商资料核验接受结果'})}}]}
OUTSOURCE_QUERY_PROPOSAL = {'role': 'assistant', 'tool_calls': [{'id': 'call-outsource', 'type': 'function', 'function': {'name': 'query_full_outsource_context', 'arguments': '{}'}}]}
OUTSOURCE_PROGRESS_PROPOSAL = {'role': 'assistant', 'tool_calls': [{'id': 'call-outsource-progress', 'type': 'function', 'function': {'name': 'prepare_supplier_progress_report', 'arguments': '{}'}}]}
OUTSOURCE_POLICY_PROPOSAL = {'role': 'assistant', 'tool_calls': [{'id': 'call-outsource-policy', 'type': 'function', 'function': {'name': 'prepare_supplier_progress_policy', 'arguments': '{}'}}]}
OUTSOURCE_MATERIAL_VERIFY_PROPOSAL = {'role': 'assistant', 'tool_calls': [{'id': 'call-outsource-material-verify', 'type': 'function', 'function': {'name': 'prepare_supplier_material_verification', 'arguments': '{}'}}]}
FINAL = {'role': 'assistant', 'content': json.dumps({'summary': 'one visible project', 'evidence_ids': ['e1'], 'suggestions': []})}


class Model:
    def __init__(self, replies): self.replies, self.calls = replies, 0
    def generate(self, messages, tools):
        self.calls += 1
        return copy.deepcopy(self.replies.pop(0))


class InspectingRepliesModel(Model):
    def __init__(self, replies):
        super().__init__(replies)
        self.tool_names = []
    def generate(self, messages, tools):
        self.tool_names.append([(tool.get("function") or {}).get("name") for tool in tools])
        return super().generate(messages, tools)


class TranscriptModel(Model):
    def __init__(self, replies):
        super().__init__(replies)
        self.transcripts = []
    def generate(self, messages, tools):
        self.transcripts.append(copy.deepcopy(messages))
        return super().generate(messages, tools)


class Gateway:
    def __init__(self):
        self.saved, self.receipts, self.physical_calls, self.final = {}, {}, 0, None
        self.crash_after_execution = False
        self.cancelled = False
    def check(self):
        if self.cancelled: raise RuntimeError('CANCELLED')
    def checkpoint(self, state): self.saved = copy.deepcopy(state)
    def execute(self, seq, key, arguments):
        if seq not in self.receipts:
            self.physical_calls += 1
            self.receipts[seq] = {'evidence_id': 'e1', 'data': [{'code': 'DEMO-A'}]}
        if self.crash_after_execution:
            self.crash_after_execution = False
            raise ConnectionError('response lost after committed tool receipt')
        return self.receipts[seq]
    def finish(self, result): self.check(); self.final = result


_DEFAULT_CORE = object()
def context(core_tool_names=_DEFAULT_CORE, **kwargs):
    if core_tool_names is _DEFAULT_CORE:
        core_tool_names = ['query_projects']
    return {'prompt': '查询项目', 'tools': [TOOL], 'skills': [], 'core_tool_names': core_tool_names, **kwargs}


def test_business_tools_are_deferred_and_direct_calls_are_blocked():
    gateway = Gateway()
    model = InspectingRepliesModel([PROPOSAL])
    with pytest.raises(RuntimeError, match='TOOL_FORBIDDEN'):
        run_loop(context(core_tool_names=[]), model, gateway)
    assert model.tool_names == [['ToolSearch']]
    assert gateway.physical_calls == 0


def test_tool_search_activates_deferred_business_tool_for_next_turn():
    gateway = Gateway()
    model = InspectingRepliesModel([TOOL_SEARCH, PROPOSAL, FINAL])
    result = run_loop(context(core_tool_names=[]), model, gateway)
    assert result['summary'] == 'one visible project'
    assert model.tool_names == [['ToolSearch'], ['ToolSearch', 'query_projects'], ['ToolSearch', 'query_projects']]
    assert gateway.physical_calls == 1
    assert gateway.saved['active_tool_names'] == ['query_projects']


def test_invalid_tool_arguments_request_one_repair_instead_of_failing_the_run():
    malformed = {'role': 'assistant', 'tool_calls': [{
        'id': 'bad-arguments', 'type': 'function',
        'function': {'name': 'query_projects',
                     'arguments': '{"query":"DEMO"}, "include":["items"]}'},
    }]}
    gateway = Gateway()
    model = TranscriptModel([malformed, PROPOSAL, FINAL])

    result = run_loop(context(), model, gateway)

    assert result['summary'] == 'one visible project'
    assert gateway.physical_calls == 1
    assert model.calls == 3
    assert 'arguments 不是有效 JSON 对象' in model.transcripts[1][0]['content']
    assert gateway.saved['protocol_repairs'] == 1


def test_streaming_model_progress_is_checkpointed_then_cleared_after_completion():
    class RecordingGateway(Gateway):
        def __init__(self):
            super().__init__()
            self.checkpoints = []
        def checkpoint(self, state):
            super().checkpoint(state)
            self.checkpoints.append(copy.deepcopy(state))

    class StreamingModel:
        def __init__(self):
            self.replies = [
                {**copy.deepcopy(PROPOSAL), 'content': '我先查询当前可见项目。'},
                copy.deepcopy(FINAL),
            ]
            self.last_metrics = {}
        def generate_stream(self, messages, tools, on_update):
            reply = self.replies.pop(0)
            on_update(reply)
            return reply

    gateway = RecordingGateway()
    result = run_loop(context(), StreamingModel(), gateway)

    assert result['summary'] == 'one visible project'
    assert any((checkpoint.get('streaming_model_message') or {}).get('content') ==
               '我先查询当前可见项目。' for checkpoint in gateway.checkpoints)
    assert not any((checkpoint.get('streaming_model_message') or {}).get('content') ==
                   FINAL['content'] for checkpoint in gateway.checkpoints)
    assert gateway.saved['streaming_model_message'] is None
    assert gateway.physical_calls == 1


def test_terminal_fenced_json_after_visible_preamble_is_parsed_without_repair():
    payload = json.dumps({
        'response_kind': 'BUSINESS',
        'summary': '项目满足现有证据范围内的结项条件。',
        'evidence_ids': ['e1'],
        'suggestions': [],
    }, ensure_ascii=False)
    final_with_preamble = {
        'role': 'assistant',
        'content': f'已核对完毕，依据现有证据作答。\n```json\n{payload}\n```',
    }
    gateway = Gateway()

    result = run_loop(context(), Model([PROPOSAL, final_with_preamble]), gateway)

    assert result['summary'] == '项目满足现有证据范围内的结项条件。'
    assert result['evidence_ids'] == ['e1']
    assert gateway.saved['protocol_repairs'] == 0
    assert gateway.final == result


@pytest.mark.parametrize('closing_tag', ['', '\n</tool_call>'])
def test_terminal_qwen_tool_call_envelope_is_parsed_without_natural_language_fallback(closing_tag):
    payload = json.dumps({
        'response_kind': 'BUSINESS',
        'summary': '项目仍有未完成计划任务，不具备正常结项条件。',
        'evidence_ids': ['e1'],
        'suggestions': [],
    }, ensure_ascii=False)
    final_with_qwen_envelope = {
        'role': 'assistant',
        'content': f'依据已经取得的证据汇总结论。\n<tool_call>\n{payload}{closing_tag}',
    }
    gateway = Gateway()

    result = run_loop(context(), Model([PROPOSAL, final_with_qwen_envelope]), gateway)

    assert result['summary'] == '项目仍有未完成计划任务，不具备正常结项条件。'
    assert result['evidence_ids'] == ['e1']
    assert gateway.saved['protocol_repairs'] == 0
    assert gateway.final == result


def test_recoverable_tool_error_is_reinjected_for_model_clarification_instead_of_failing_run():
    class RejectingGateway(Gateway):
        def execute(self, seq, key, arguments):
            self.physical_calls += 1
            return {'tool_error': {'code': 'WORKFLOW_MISMATCH',
                                   'message': '审批模板不可用，请重新查询流程选项'}}

    gateway = RejectingGateway()
    clarification = {'role': 'assistant', 'content': json.dumps({
        'response_kind': 'CLARIFICATION',
        'summary': '当前没有可用审批流程，无法准备暂停申请，请先配置流程。',
        'evidence_ids': [],
        'suggestions': ['配置适用的暂停审批流程后重新发起'],
    }, ensure_ascii=False)}
    model = TranscriptModel([PROPOSAL, clarification])

    result = run_loop(context(), model, gateway)

    assert result['response_kind'] == 'CLARIFICATION'
    assert gateway.physical_calls == 1
    assert gateway.final == result
    tool_result = model.transcripts[1][-1]
    assert tool_result['role'] == 'tool'
    payload = json.loads(tool_result['content'])
    assert payload['tool_error']['code'] == 'WORKFLOW_MISMATCH'
    assert gateway.saved['executed_tool_signatures']


def test_failed_formal_action_cannot_be_reported_as_a_successful_confirmation_card():
    action_tool = {'type': 'function', 'function': {'name': 'prepare_demo_action'}}
    action_call = {'role': 'assistant', 'tool_calls': [{
        'id': 'action-1', 'type': 'function',
        'function': {'name': 'prepare_demo_action', 'arguments': '{}'},
    }]}
    false_success = {'role': 'assistant', 'content': json.dumps({
        'response_kind': 'BUSINESS',
        'summary': '操作建议已经准备，请在确认卡中提交。',
        'evidence_ids': [],
        'suggestions': [],
    }, ensure_ascii=False)}
    clarification = {'role': 'assistant', 'content': json.dumps({
        'response_kind': 'CLARIFICATION',
        'summary': '操作建议尚未准备成功，请先配置可用流程。',
        'evidence_ids': [],
        'suggestions': ['配置流程后重新发起'],
    }, ensure_ascii=False)}

    class RejectingGateway(Gateway):
        def execute(self, seq, key, arguments):
            self.physical_calls += 1
            return {'tool_error': {'code': 'WORKFLOW_MISMATCH', 'message': '没有可用流程'}}

    gateway = RejectingGateway()
    model = TranscriptModel([action_call, false_success, clarification])
    result = run_loop(context(prompt='准备项目正式操作', tools=[action_tool], core_tool_names=['prepare_demo_action'],
                              tool_annotations={'prepare_demo_action': {'readOnlyHint': False}}),
                      model, gateway)

    assert result['response_kind'] == 'CLARIFICATION'
    assert result['summary'].startswith('操作建议尚未准备成功')
    assert model.calls == 3
    assert '正式操作结果协议' in model.transcripts[2][0]['content']
    assert all(message['role'] != 'system' for message in model.transcripts[2][1:])
    assert all(message['role'] != 'system' for message in gateway.saved['messages'][1:])
    assert gateway.saved['action_outcomes']['prepare_demo_action']['status'] == 'error'


def test_formal_action_request_cannot_use_read_only_evidence_to_claim_a_confirmation_card():
    action_tool = {'type': 'function', 'function': {'name': 'prepare_demo_action'}}
    false_success = {'role': 'assistant', 'content': json.dumps({
        'response_kind': 'BUSINESS', 'summary': '确认卡已经准备完成。',
        'evidence_ids': ['e1'], 'suggestions': [],
    }, ensure_ascii=False)}
    clarification = {'role': 'assistant', 'content': json.dumps({
        'response_kind': 'CLARIFICATION', 'summary': '目前只有查询证据，确认卡尚未准备。',
        'evidence_ids': ['e1'], 'suggestions': [],
    }, ensure_ascii=False)}
    gateway = Gateway()
    model = TranscriptModel([PROPOSAL, false_success, clarification])

    result = run_loop(context(prompt='请准备项目正式操作确认卡', tools=[TOOL, action_tool],
                              tool_annotations={'query_projects': {'readOnlyHint': True},
                                                'prepare_demo_action': {'readOnlyHint': False}}),
                      model, gateway)

    assert result['response_kind'] == 'CLARIFICATION'
    assert model.calls == 3
    assert '没有任何成功的正式操作工具回执' in model.transcripts[2][0]['content']
    assert all(message['role'] != 'system' for message in model.transcripts[2][1:])


@pytest.mark.parametrize('prompt', [
    '请查询项目资料；只查询分析，不准备或执行任何操作。',
    '核对项目状态，不准备也不执行。',
    '只读查询供应商节点上报规则和最近上报，不准备或执行任何操作。',
    '查询项目；do not prepare or execute action.',
])
def test_explicitly_negated_formal_action_does_not_require_an_operation_receipt(prompt):
    gateway = Gateway()
    model = TranscriptModel([PROPOSAL, FINAL])

    result = run_loop(context(prompt=prompt), model, gateway)

    assert result['response_kind'] == 'BUSINESS'
    assert result['evidence_ids'] == ['e1']
    assert model.calls == 2
    assert gateway.saved['protocol_repairs'] == 0


def test_positive_action_after_a_negated_alternative_still_requires_a_receipt():
    action_tool = {'type': 'function', 'function': {'name': 'prepare_demo_action'}}
    false_success = {'role': 'assistant', 'content': json.dumps({
        'response_kind': 'BUSINESS', 'summary': '已经提交审批。',
        'evidence_ids': ['e1'], 'suggestions': [],
    }, ensure_ascii=False)}
    clarification = {'role': 'assistant', 'content': json.dumps({
        'response_kind': 'CLARIFICATION', 'summary': '尚未提交审批。',
        'evidence_ids': ['e1'], 'suggestions': [],
    }, ensure_ascii=False)}
    gateway = Gateway()
    model = TranscriptModel([PROPOSAL, false_success, clarification])

    result = run_loop(context(
        prompt='不要准备项目草稿，直接提交项目审批',
        tools=[TOOL, action_tool],
        tool_annotations={'query_projects': {'readOnlyHint': True},
                          'prepare_demo_action': {'readOnlyHint': False}},
    ), model, gateway)

    assert result['response_kind'] == 'CLARIFICATION'
    assert model.calls == 3


def test_current_prompt_action_intent_survives_a_narrower_tool_search_query():
    query_tool = {'type': 'function', 'function': {
        'name': 'query_project_closure_context',
        'description': '读取项目终止与结项资料。',
    }}
    action_tools = [
        {'type': 'function', 'function': {'name': 'prepare_project_closure_checklist',
                                          'description': '准备正常结项核对清单。'}},
        {'type': 'function', 'function': {'name': 'prepare_project_termination',
                                          'description': '准备客户终止项目审批。'}},
        {'type': 'function', 'function': {'name': 'prepare_project_closure_item',
                                          'description': '准备更新一个结项事项。'}},
        {'type': 'function', 'function': {'name': 'prepare_project_normal_close',
                                          'description': '准备正常关闭项目审批。'}},
        {'type': 'function', 'function': {'name': 'prepare_project_settlement_close',
                                          'description': '准备终止结算关闭审批。'}},
    ]
    search = {'role': 'assistant', 'tool_calls': [{
        'id': 'search-closure', 'type': 'function',
        'function': {'name': 'ToolSearch', 'arguments': json.dumps({'query': '项目终止'})},
    }]}
    clarification = {'role': 'assistant', 'content': json.dumps({
        'response_kind': 'CLARIFICATION', 'summary': '请继续提供结项资料。',
        'evidence_ids': [], 'suggestions': [],
    }, ensure_ascii=False)}
    model = InspectingRepliesModel([search, clarification])

    run_loop(context(prompt='请为 BROWSER-OUT-001 准备正常结项核对清单', core_tool_names=[],
                     tools=[TOOL, query_tool, *action_tools],
                     skills=[{'key': 'project_termination_closure',
                              'agent_description': '项目终止与结项核对',
                              'tools': ['query_projects', 'query_project_closure_context'],
                              'optional_tools': [tool['function']['name'] for tool in action_tools],
                              'activation_queries': ['项目终止与结项资料']}]),
             model, Gateway())

    assert {'query_projects', 'query_project_closure_context',
            'prepare_project_closure_checklist'} <= set(model.tool_names[1])
    assert 'prepare_project_termination' not in model.tool_names[1]


def test_persisted_tool_signatures_are_postgresql_jsonb_safe(data):
    gateway = Gateway()
    run_loop(context(core_tool_names=[]), Model([TOOL_SEARCH, PROPOSAL, FINAL]), gateway)
    signatures = gateway.saved['executed_tool_signatures']
    assert signatures and all('\0' not in signature for signature in signatures)
    _, factory = data
    with factory() as db:
        stored = db.scalar(text('select cast(:payload as jsonb)'),
                           {'payload': json.dumps({'signatures': signatures}, ensure_ascii=False)})
    assert stored['signatures'] == signatures


def test_hallucinated_skill_name_is_repaired_through_tool_search_instead_of_failing_run():
    gateway = Gateway()
    model = InspectingRepliesModel([HALLUCINATED_SKILL_CALL, TOOL_SEARCH, PROPOSAL, FINAL])
    result = run_loop(context(prompt='查询 SMOKE-M001 的项目计划', core_tool_names=[],
                              skills=[{'key': 'business_object_matching',
                                       'agent_description': '按业务编号匹配候选对象',
                                       'tools': ['query_projects']}]), model, gateway)
    assert result['summary'] == 'one visible project'
    assert model.tool_names == [
        ['ToolSearch'],
        ['ToolSearch'],
        ['ToolSearch', 'query_projects'],
        ['ToolSearch', 'query_projects'],
    ]
    catalog_prompt = gateway.saved['messages'][0]['content']
    assert 'business_object_matching' not in catalog_prompt
    assert 'ToolSearch query="业务对象候选匹配"' in catalog_prompt
    assert gateway.saved['protocol_repairs'] == 1
    assert gateway.physical_calls == 1


def test_tool_search_activates_bounded_skill_tool_pack_for_next_turn():
    gateway = Gateway()
    model = InspectingRepliesModel([PLAN_TOOL_SEARCH, PLAN_PROPOSAL, FINAL])
    result = run_loop(context(core_tool_names=[], tools=[TOOL, OTHER_TOOL, BASELINE_TOOL, PLAN_CHANGE_TOOL],
                              skills=[{'key': 'project_plan_context_review',
                                       'agent_description': '项目计划上下文核对',
                                       'tools': ['query_project_plan_context'],
                                       'optional_tools': ['prepare_project_plan_baseline']}]),
                      model, gateway)
    assert result['summary'] == 'one visible project'
    assert model.tool_names == [
        ['ToolSearch'],
        ['ToolSearch', 'query_project_plan_context'],
        ['ToolSearch', 'query_project_plan_context'],
    ]
    assert gateway.physical_calls == 1
    assert gateway.saved['active_tool_names'] == ['query_project_plan_context']


def test_tool_search_exact_tool_name_does_not_activate_whole_skill_pack():
    exact = {'role': 'assistant', 'tool_calls': [{'id': 'search-plan-tool', 'type': 'function', 'function': {'name': 'ToolSearch', 'arguments': json.dumps({'query': 'query_project_plan_context'})}}]}
    gateway = Gateway()
    model = InspectingRepliesModel([exact, PLAN_PROPOSAL, FINAL])
    run_loop(context(core_tool_names=[], tools=[OTHER_TOOL, BASELINE_TOOL],
                     skills=[{'key': 'project_plan_context_review',
                              'agent_description': '项目计划上下文核对',
                              'tools': ['query_project_plan_context'],
                              'optional_tools': ['prepare_project_plan_baseline']}]),
             model, gateway)
    assert model.tool_names == [
        ['ToolSearch'],
        ['ToolSearch', 'query_project_plan_context'],
        ['ToolSearch', 'query_project_plan_context'],
    ]


def test_tool_search_uses_curated_activation_tools_instead_of_all_optional_tools():
    gateway = Gateway()
    model = InspectingRepliesModel([CONTACT_TOOL_SEARCH, CONTACT_PROPOSAL, FINAL])
    run_loop(context(prompt='请查询工程联络单详情和当前状态。', core_tool_names=[], tools=[
                         CONTACT_CASES_TOOL, CONTACT_CONTEXT_TOOL, CONTACT_RESOLUTION_TOOL,
                         CONTACT_REVIEW_TOOL, CONTACT_CLOSE_TOOL, CONTACT_RESPOND_TOOL,
                         CONTACT_ASSIGN_TOOL,
                     ],
                         skills=[{'key': 'contact_collaboration_review',
                              'agent_description': '工程联络协作核对',
                              'tools': ['query_contact_cases'],
                              'optional_tools': ['query_contact_context','prepare_contact_resolution',
                                                 'prepare_contact_review','prepare_contact_close',
                                                 'prepare_contact_respond','prepare_contact_assign'],
                              'activation_tools': ['query_contact_cases','query_contact_context',
                                                   'prepare_contact_resolution','prepare_contact_review',
                                                   'prepare_contact_close','prepare_contact_respond']}]),
             model, gateway)
    assert model.tool_names == [
        ['ToolSearch'],
        ['ToolSearch', 'query_contact_cases', 'query_contact_context'],
        ['ToolSearch', 'query_contact_cases', 'query_contact_context'],
    ]
    assert len(gateway.saved['active_tool_names']) <= 4
    assert 'prepare_contact_close' not in gateway.saved['active_tool_names']
    assert 'prepare_contact_resolution' not in gateway.saved['active_tool_names']
    assert 'prepare_contact_assign' not in gateway.saved['active_tool_names']


def test_tool_search_prefers_activation_alias_over_neighboring_business_mentions():
    contract_context = {'type': 'function', 'function': {'name': 'query_contract_context',
                                                         'description': '按项目或合同线索读取销售合同、整套委外合同、付款节点和替代关系上下文。'}}
    contract_prepare = {'type': 'function', 'function': {'name': 'prepare_contract_record',
                                                         'description': '准备销售合同或整套委外合同登记审批建议。'}}
    contract_signing = {'type': 'function', 'function': {'name': 'prepare_contract_signing_record',
                                                         'description': '准备整套委外合同签署文件或签署状态证据登记建议。'}}
    quote_context = {'type': 'function', 'function': {'name': 'query_quote_acceptance_context',
                                                      'description': '按项目线索读取报价、承接、拒单、正式开工和销售合同上下文。'}}
    quote_prepare = {'type': 'function', 'function': {'name': 'prepare_quote_acceptance_decision',
                                                      'description': '准备报价承接或拒单审批建议。'}}
    dossier = {'type': 'function', 'function': {'name': 'query_project_dossier',
                                                'description': '按项目编号、模具号、工程联络、合同或订单编号反查项目业务档案。'}}
    gateway = Gateway()
    model = InspectingRepliesModel([CONTRACT_TOOL_SEARCH, CONTRACT_RECORD_PROPOSAL, FINAL])
    run_loop(context(prompt='请准备登记销售合同，先读取合同上下文。', core_tool_names=[], tools=[contract_context, contract_prepare, contract_signing, quote_context, quote_prepare, dossier],
                     tool_annotations={'prepare_contract_record': {'readOnlyHint': False}},
                     skills=[{'key': 'contract_context_review',
                              'agent_description': '合同上下文核对',
                              'tools': ['query_contract_context'],
                              'optional_tools': ['prepare_contract_record','prepare_contract_signing_record'],
                              'activation_queries': ['合同登记', '销售合同', '整套委外合同', '合同号']},
                             {'key': 'quote_acceptance_review',
                              'agent_description': '报价、承接、正式开工和销售合同上下文',
                              'tools': ['query_quote_acceptance_context'],
                              'optional_tools': ['prepare_quote_acceptance_decision'],
                              'activation_queries': ['报价承接', '报价拒单', '承接', '拒单']},
                             {'key': 'project_dossier_review',
                              'agent_description': '项目业务档案核对',
                              'tools': ['query_project_dossier'],
                              'activation_queries': ['项目业务档案', '业务档案']}]),
             model, gateway)
    assert model.tool_names == [
        ['ToolSearch'],
        ['ToolSearch', 'query_contract_context', 'prepare_contract_record',
         'prepare_contract_signing_record'],
        ['ToolSearch', 'query_contract_context', 'prepare_contract_record',
         'prepare_contract_signing_record'],
    ]
    assert gateway.saved['active_tool_names'] == [
        'prepare_contract_record', 'prepare_contract_signing_record', 'query_contract_context'
    ]


def test_tool_search_narrows_supplier_progress_scene_to_query_and_report_operation():
    tools = [
        {'type': 'function', 'function': {'name': 'query_full_outsource_context',
                                          'description': '读取整套委外合同、供应商节点上报、采购跟进和验收上下文。'}},
        {'type': 'function', 'function': {'name': 'prepare_contract_signing_record',
                                          'description': '准备委外合同签署证据登记建议。'}},
        {'type': 'function', 'function': {'name': 'prepare_supplier_material_handoff',
                                          'description': '准备供应商资料交接证据登记建议。'}},
        {'type': 'function', 'function': {'name': 'prepare_supplier_progress_report',
                                          'description': '准备供应商设计采购生产质检装配试模验收节点上报证据登记建议。'}},
        {'type': 'function', 'function': {'name': 'prepare_supplier_progress_policy',
                                          'description': '准备供应商阶段填报的版本化频率与必需证据规则登记建议。'}},
        {'type': 'function', 'function': {'name': 'prepare_supplier_deduction_settlement',
                                          'description': '准备供应商扣款责任或结算依据登记建议。'}},
    ]
    gateway = Gateway()
    model = InspectingRepliesModel([OUTSOURCE_PROGRESS_TOOL_SEARCH, OUTSOURCE_PROGRESS_PROPOSAL, FINAL])
    run_loop(context(prompt='请登记 BROWSER-OUT-001 的供应商节点上报。', core_tool_names=[], tools=tools,
                     tool_annotations={'prepare_supplier_progress_report': {'readOnlyHint': False}},
                     skills=[{'key': 'full_outsource_review',
                              'agent_description': '整套委外协同上下文核对',
                              'tools': ['query_full_outsource_context'],
                              'optional_tools': ['prepare_contract_signing_record',
                                                 'prepare_supplier_material_handoff',
                                                 'prepare_supplier_progress_policy',
                                                 'prepare_supplier_progress_report',
                                                 'prepare_supplier_deduction_settlement'],
                              'activation_queries': ['整套委外执行', '供应商节点', '供应商节点上报']}]),
             model, gateway)
    assert model.tool_names == [
        ['ToolSearch'],
        ['ToolSearch', 'query_full_outsource_context', 'prepare_supplier_progress_report'],
        ['ToolSearch', 'query_full_outsource_context', 'prepare_supplier_progress_report'],
    ]
    assert gateway.saved['active_tool_names'] == ['prepare_supplier_progress_report', 'query_full_outsource_context']


def test_tool_search_selects_progress_policy_for_frequency_and_evidence_template():
    tools = [
        {'type': 'function', 'function': {'name': 'query_full_outsource_context',
                                          'description': '读取整套委外合同、供应商节点上报规则、采购跟进和验收上下文。'}},
        {'type': 'function', 'function': {'name': 'prepare_supplier_progress_policy',
                                          'description': '准备供应商阶段填报的版本化频率与必需证据规则登记建议。'}},
        {'type': 'function', 'function': {'name': 'prepare_supplier_progress_report',
                                          'description': '准备供应商阶段进度和节点上报证据登记建议。'}},
    ]
    gateway = Gateway()
    model = InspectingRepliesModel([OUTSOURCE_POLICY_TOOL_SEARCH, OUTSOURCE_POLICY_PROPOSAL, FINAL])
    run_loop(context(prompt='请为 BROWSER-OUT-001 准备供应商上报频率和证据模板规则。',
                     core_tool_names=[], tools=tools,
                     tool_annotations={'prepare_supplier_progress_policy': {'readOnlyHint': False}},
                     skills=[{'key': 'full_outsource_review',
                              'agent_description': '整套委外协同上下文核对',
                              'tools': ['query_full_outsource_context'],
                              'optional_tools': ['prepare_supplier_progress_policy',
                                                 'prepare_supplier_progress_report'],
                              'activation_queries': ['供应商上报规则', '上报频率', '证据模板']}]),
             model, gateway)
    assert model.tool_names == [
        ['ToolSearch'],
        ['ToolSearch', 'query_full_outsource_context', 'prepare_supplier_progress_policy', 'prepare_supplier_progress_report'],
        ['ToolSearch', 'query_full_outsource_context', 'prepare_supplier_progress_policy', 'prepare_supplier_progress_report'],
    ]
    assert gateway.saved['active_tool_names'] == [
        'prepare_supplier_progress_policy', 'prepare_supplier_progress_report', 'query_full_outsource_context'
    ]


def test_tool_search_selects_supplier_material_verification_without_loading_unrelated_outsource_actions():
    tools = [
        {'type': 'function', 'function': {'name': 'query_full_outsource_context',
                                          'description': '读取整套委外合同、资料交接和供应商核验上下文。'}},
        {'type': 'function', 'function': {'name': 'prepare_supplier_material_handoff',
                                          'description': '准备向供应商交接资料的证据登记建议。'}},
        {'type': 'function', 'function': {'name': 'prepare_supplier_material_verification',
                                          'description': '准备供应商对已交接资料的收到、接受、待澄清或退回核验结果。'}},
        {'type': 'function', 'function': {'name': 'prepare_supplier_progress_report',
                                          'description': '准备供应商节点进度上报。'}},
    ]
    gateway = Gateway()
    model = InspectingRepliesModel([
        OUTSOURCE_MATERIAL_VERIFY_TOOL_SEARCH,
        OUTSOURCE_MATERIAL_VERIFY_PROPOSAL,
        FINAL,
    ])
    run_loop(context(prompt='请为 BROWSER-OUT-001 登记供应商资料核验接受结果。',
                     core_tool_names=[], tools=tools,
                     tool_annotations={'prepare_supplier_material_verification': {'readOnlyHint': False}},
                     skills=[{'key': 'full_outsource_review',
                              'agent_description': '整套委外协同上下文核对',
                              'tools': ['query_full_outsource_context'],
                              'optional_tools': ['prepare_supplier_material_handoff',
                                                 'prepare_supplier_material_verification',
                                                 'prepare_supplier_progress_report'],
                              'activation_queries': ['资料交接', '资料核验', '资料接受']}]),
             model, gateway)
    assert model.tool_names == [
        ['ToolSearch'],
        ['ToolSearch', 'query_full_outsource_context', 'prepare_supplier_material_verification'],
        ['ToolSearch', 'query_full_outsource_context', 'prepare_supplier_material_verification'],
    ]
    assert gateway.saved['active_tool_names'] == [
        'prepare_supplier_material_verification', 'query_full_outsource_context'
    ]


def test_read_only_prompt_cannot_open_prepare_tool_from_action_worded_group_search():
    tools = [
        {'type': 'function', 'function': {'name': 'query_full_outsource_context',
                                          'description': '读取整套委外合同、资料交接和供应商核验上下文。'}},
        {'type': 'function', 'function': {'name': 'prepare_supplier_material_handoff',
                                          'description': '准备向供应商交接资料的证据登记建议。'}},
        {'type': 'function', 'function': {'name': 'prepare_supplier_material_verification',
                                          'description': '准备供应商资料核验结果登记建议。'}},
    ]
    gateway = Gateway()
    model = InspectingRepliesModel([OUTSOURCE_MATERIAL_VERIFY_TOOL_SEARCH, OUTSOURCE_QUERY_PROPOSAL, FINAL])
    run_loop(context(prompt='只读查询 BROWSER-OUT-001 的资料交接与供应商核验情况，不要准备或执行任何操作。',
                     core_tool_names=[], tools=tools,
                     skills=[{'key': 'full_outsource_review',
                              'agent_description': '整套委外协同上下文核对',
                              'tools': ['query_full_outsource_context'],
                              'optional_tools': ['prepare_supplier_material_handoff',
                                                 'prepare_supplier_material_verification'],
                              'activation_queries': ['资料交接', '资料核验']}]),
             model, gateway)
    assert model.tool_names == [
        ['ToolSearch'],
        ['ToolSearch', 'query_full_outsource_context'],
        ['ToolSearch', 'query_full_outsource_context'],
    ]
    assert gateway.saved['active_tool_names'] == ['query_full_outsource_context']


def test_read_only_prompt_cannot_open_exact_prepare_tool_name():
    prepare_tool = {'type': 'function', 'function': {
        'name': 'prepare_supplier_material_verification',
        'description': '准备供应商资料核验结果登记建议。',
    }}
    assert harness_module._find_deferred_tools(
        'prepare_supplier_material_verification',
        {'prepare_supplier_material_verification': prepare_tool},
        action_intent=False,
        current_prompt='只读查询资料核验情况，不要准备或执行任何操作。',
    ) == ([], [], [])


def test_logistics_tool_search_opens_only_the_requested_route_or_quote_action():
    query_tool = {'type': 'function', 'function': {
        'name': 'query_delivery_logistics_context',
        'description': '读取交付、物流路线、报价和结算价上下文。',
    }}
    route_tool = {'type': 'function', 'function': {
        'name': 'prepare_logistics_route',
        'description': '准备仓库确认固定物流路线或模具项目实际路线。',
    }}
    quote_tool = {'type': 'function', 'function': {
        'name': 'prepare_logistics_quote',
        'description': '准备采购主管确认物流报价或项目本次结算价格。',
    }}
    deferred = {tool['function']['name']: tool for tool in (query_tool, route_tool, quote_tool)}
    groups = [{'key': 'delivery_logistics_review',
               'name': '交付物流路线与价格协同',
               'description': '查询并准备物流路线、报价和项目结算价格。',
               'tools': ['query_delivery_logistics_context', 'prepare_logistics_route', 'prepare_logistics_quote'],
               'required': ['query_delivery_logistics_context'],
               'optional_tools': ['prepare_logistics_route', 'prepare_logistics_quote'],
               'activation_queries': ['物流路线', '物流报价', '本次物流结算价格', '本次结算价格']}]

    _, route_candidates, _ = harness_module._find_deferred_tools(
        '登记模具项目实际物流路线', deferred, groups,
        action_intent=True, current_prompt='请登记模具项目实际物流路线并准备确认卡。')
    assert route_candidates == ['query_delivery_logistics_context', 'prepare_logistics_route']

    _, quote_candidates, _ = harness_module._find_deferred_tools(
        '确认本次物流结算价格', deferred, groups,
        action_intent=True, current_prompt='请确认本次物流结算价格并准备确认卡。')
    assert quote_candidates == ['query_delivery_logistics_context', 'prepare_logistics_quote']


def test_read_only_logistics_query_never_activates_route_or_quote_prepare_tools():
    query_tool = {'type': 'function', 'function': {
        'name': 'query_delivery_logistics_context',
        'description': '读取交付物流上下文。',
    }}
    route_tool = {'type': 'function', 'function': {
        'name': 'prepare_logistics_route',
        'description': '准备物流路线确认。',
    }}
    quote_tool = {'type': 'function', 'function': {
        'name': 'prepare_logistics_quote',
        'description': '准备物流报价确认。',
    }}
    deferred = {tool['function']['name']: tool for tool in (query_tool, route_tool, quote_tool)}
    groups = [{'key': 'delivery_logistics_review',
               'name': '交付物流路线与价格协同',
               'description': '查询并准备物流路线和报价。',
               'tools': ['query_delivery_logistics_context', 'prepare_logistics_route', 'prepare_logistics_quote'],
               'required': ['query_delivery_logistics_context'],
               'optional_tools': ['prepare_logistics_route', 'prepare_logistics_quote'],
               'activation_queries': ['物流路线', '物流报价']}]
    _, candidates, _ = harness_module._find_deferred_tools(
        '查询物流路线和报价', deferred, groups,
        action_intent=False,
        current_prompt='只读查询 BROWSER-OUT-001 的物流路线和报价，不要准备或执行任何操作。')
    assert candidates == ['query_delivery_logistics_context']


def test_master_data_lookup_exposes_one_reader_and_hides_maintenance_until_requested():
    master = {'type': 'function', 'function': {
        'name': 'erp_design_query_master_data',
        'description': '一次查询 ERP 设计基础资料：材质密度、设计分组规则和分组关键词；只读。'}}
    density = {'type': 'function', 'function': {
        'name': 'erp_design_manage_density',
        'description': '新增、修改或删除 ERP 设计材质密度。'}}
    rule = {'type': 'function', 'function': {
        'name': 'erp_design_manage_group_rule',
        'description': '新增、修改、删除、启用或停用 ERP 设计分组规则。'}}
    keyword = {'type': 'function', 'function': {
        'name': 'erp_design_manage_group_keyword',
        'description': '新增、修改或删除 ERP 设计分组关键词。'}}
    deferred = {tool['function']['name']: tool for tool in [master, density, rule, keyword]}
    groups = harness_module._skill_tool_groups([
        {'key': 'erp_design_master_data_maintenance',
         'tools': ['erp_design_query_master_data'],
         'optional_tools': ['erp_design_manage_density', 'erp_design_manage_group_rule',
                            'erp_design_manage_group_keyword'],
         'activation_tools': ['erp_design_query_master_data', 'erp_design_manage_density',
                              'erp_design_manage_group_rule', 'erp_design_manage_group_keyword'],
         'activation_queries': ['材质密度', '分组规则', '分组关键词']},
    ], deferred)
    annotations = {name: {'readOnlyHint': False} for name in [
        'erp_design_manage_density', 'erp_design_manage_group_rule',
        'erp_design_manage_group_keyword',
    ]}

    _, read_candidates, _ = harness_module._find_deferred_tools(
        'ERP 材质密度', deferred, groups, current_prompt='查询 CR12MOV 的 ERP 材质密度',
        tool_annotations=annotations)
    _, write_candidates, _ = harness_module._find_deferred_tools(
        '维护 ERP 材质密度', deferred, groups, action_intent=True,
        current_prompt='维护 CR12MOV 的 ERP 材质密度', tool_annotations=annotations)

    assert read_candidates == ['erp_design_query_master_data']
    assert write_candidates == ['erp_design_query_master_data', 'erp_design_manage_density']


def test_unambiguous_master_data_lookup_auto_activates_the_single_reader():
    master = {'type': 'function', 'function': {
        'name': 'erp_design_query_master_data',
        'description': '一次查询 ERP 设计基础资料；只读。'}}
    unrelated = {'type': 'function', 'function': {
        'name': 'query_unrelated_business_data',
        'description': '查询无关业务资料。'}}
    call = {'role': 'assistant', 'tool_calls': [{
        'id': 'master-1', 'type': 'function',
        'function': {'name': 'erp_design_query_master_data', 'arguments': json.dumps({
            'material_mark': 'CR12MOV', 'include': ['densities'],
        })},
    }]}
    gateway = Gateway()
    model = InspectingRepliesModel([call, FINAL])

    result = run_loop(context(
        prompt='查询 CR12MOV 的 ERP 材质密度',
        core_tool_names=[],
        tools=[master, unrelated],
        skills=[{
            'key': 'erp_design_master_data_maintenance',
            'tools': ['erp_design_query_master_data'],
            'activation_tools': ['erp_design_query_master_data'],
            'activation_queries': ['材质密度'],
            'auto_activation_queries': ['材质密度'],
            'suppress_tool_search_on_auto_activation': True,
        }],
    ), model, gateway)

    assert result['summary'] == 'one visible project'
    assert model.calls == 2
    assert model.tool_names[0] == ['erp_design_query_master_data']
    assert gateway.saved['active_tool_names'] == ['erp_design_query_master_data']
    assert gateway.physical_calls == 1


def test_formal_start_handoff_query_activates_start_reader_despite_department_terms():
    start = {'type': 'function', 'function': {
        'name': 'query_internal_start_readiness',
        'description': '按项目线索核对正式开工条件、六段状态和部门交接通知；只读。'}}
    prepare = {'type': 'function', 'function': {
        'name': 'prepare_internal_start',
        'description': '准备正式开工审批建议。'}}
    purchase = {'type': 'function', 'function': {
        'name': 'query_procurement_price_context',
        'description': '读取采购价格与订单上下文；只读。'}}
    call = {'role': 'assistant', 'tool_calls': [{
        'id': 'start-1', 'type': 'function',
        'function': {'name': 'query_internal_start_readiness', 'arguments': json.dumps({
            'identifier': 'BROWSER-START-HANDOFF-001',
        })},
    }]}
    gateway = Gateway()
    model = InspectingRepliesModel([call, FINAL])

    result = run_loop(context(
        prompt=('只读查询 BROWSER-START-HANDOFF-001 的正式开工业务状态、六段状态链和设计、采购、'
                '生产制造、装配、财务五类部门交接通知投递结果。不要准备或执行任何操作。'),
        core_tool_names=[],
        tools=[start, prepare, purchase],
        skills=[{
            'key': 'internal_start_readiness',
            'tools': ['query_internal_start_readiness'],
            'optional_tools': ['prepare_internal_start'],
            'activation_queries': ['正式开工', '开工通知', '开工条件', '内部开工'],
            'auto_activation_queries': ['正式开工', '开工通知', '开工条件', '内部开工'],
            'suppress_tool_search_on_auto_activation': True,
        }, {
            'key': 'procurement_price_context_review',
            'tools': ['query_procurement_price_context'],
            'activation_queries': ['采购价格', '采购订单'],
        }],
    ), model, gateway)

    assert result['summary'] == 'one visible project'
    assert model.tool_names[0] == ['query_internal_start_readiness']
    assert gateway.saved['active_tool_names'] == ['query_internal_start_readiness']
    assert gateway.physical_calls == 1


def test_formal_start_scene_overrides_model_shortened_exact_purchase_tool_search():
    start = {'type': 'function', 'function': {
        'name': 'query_internal_start_readiness',
        'description': '按项目线索核对正式开工条件、六段状态和部门交接通知；只读。'}}
    purchase = {'type': 'function', 'function': {
        'name': 'query_purchase_orders',
        'description': '查询采购订单；只读。'}}
    prompt = ('只读查询 BROWSER-START-HANDOFF-001 的正式开工业务状态、六段状态链和设计、采购、'
              '生产制造、装配、财务五类部门交接通知投递结果。不要准备或执行任何操作。')
    deferred = {tool['function']['name']: tool for tool in (start, purchase)}
    groups = harness_module._skill_tool_groups([{
        'key': 'internal_start_readiness',
        'tools': ['query_internal_start_readiness'],
        'activation_queries': ['正式开工'],
        'priority_patterns': ['正式开工|开工通知|开工条件|内部开工'],
        'skill_layer': 'erp',
        'skill_domain': 'project',
        'route_terms': ['开工'],
    }, {
        'key': 'business_status_review',
        'tools': ['query_purchase_orders'],
        'skill_layer': 'agent',
        'skill_domain': 'procurement',
        'route_terms': ['业务状态'],
    }], deferred)

    matches, activated, matched_groups = harness_module._find_deferred_tools(
        'query_purchase_orders', deferred, groups, current_prompt=prompt)

    assert matches == ['internal_start_readiness']
    assert activated == ['query_internal_start_readiness']
    assert matched_groups == ['internal_start_readiness']


def test_current_turn_reorders_catalog_and_uses_the_most_specific_matching_alias():
    risk_tool = {'type': 'function', 'function': {'name': 'analyze_delivery_risk',
                                                  'description': '分析供应商发货延期和临期风险。'}}
    outsource_tool = {'type': 'function', 'function': {'name': 'query_full_outsource_context',
                                                       'description': '读取整套委外合同和供应商节点上报上下文。'}}

    class CatalogModel:
        def generate(self, messages, tools):
            prompt = messages[0]['content']
            relevant = 'ToolSearch query="供应商节点上报"'
            unrelated = 'ToolSearch query="供应商发货风险分析"'
            assert relevant in prompt and unrelated in prompt
            assert prompt.index(relevant) < prompt.index(unrelated)
            return {'content': json.dumps({'response_kind': 'CONVERSATION',
                                           'summary': '请提供项目编号。',
                                           'evidence_ids': [], 'suggestions': []})}

    run_loop(context(prompt='你好，帮我看看 BROWSER-OUT-001 的供应商节点上报上下文。',
                     core_tool_names=[], tools=[risk_tool, outsource_tool],
                     skills=[{'key': 'delivery_risk_analysis',
                              'agent_description': '供应商发货风险分析',
                              'tools': ['analyze_delivery_risk']},
                             {'key': 'full_outsource_review',
                              'agent_description': '整套委外协同上下文核对',
                              'tools': ['query_full_outsource_context'],
                              'activation_queries': ['整套委外执行', '供应商节点上报', '供应商节点']}]),
             CatalogModel(), Gateway())


def test_business_query_mentioning_model_still_allows_tool_search():
    class InspectingModel(Model):
        def generate(self, messages, tools):
            assert [tool['function']['name'] for tool in tools] == ['ToolSearch']
            return {'content': json.dumps({'response_kind': 'CLARIFICATION',
                                           'summary': '请提供项目编号。',
                                           'evidence_ids': [], 'suggestions': []})}
    run_loop(context(prompt='用模型查询项目计划，看看项目大节点', core_tool_names=[],
                     tools=[OTHER_TOOL], skills=[]), InspectingModel([]), Gateway())


@pytest.mark.parametrize('prompt', [
    '帮我执行解析这个钢料新模清单',
    '重新匹配历史无图的设计上传会话',
    '查询设计订单明细的闲置料决策',
    '维护材质密度和设计分组规则',
    '提交设变申请并维护设变明细',
    '审批并重新提交设计订单',
    '上传修模改模图纸并查看加工商响应',
    '导入BOM并核对缺料和采购进度',
    '上传厂内标准件图纸',
])
def test_design_business_vocabulary_allows_tool_search(prompt):
    assert harness_module._business_tool_activation_allowed({
        'prompt': prompt,
        'recent_requests': [],
    })


def test_design_upload_attachment_exposes_tool_search():
    class InspectingModel(Model):
        def generate(self, messages, tools):
            assert [tool['function']['name'] for tool in tools] == ['ToolSearch']
            assert 'ToolSearch query="上传新模钢料表"' in messages[0]['content']
            return {'content': json.dumps({'response_kind': 'CLARIFICATION',
                                           'summary': '请确认清单类型。',
                                           'evidence_ids': [], 'suggestions': []})}

    run_loop(context(prompt='帮我解析当前附件', core_tool_names=[],
                     files=[{'filename': 'M250238-P4料单.XLSX',
                             'media_type': 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'}],
                     tools=[{'type': 'function', 'function': {
                         'name': 'erp_design_parse_new_mold_upload',
                         'description': '解析并上传当前新模钢料或五金清单附件。',
                     }}],
                     skills=[{'key': 'erp_new_mold_design_upload',
                              'name': 'ERP 新模设计上传流程',
                              'agent_description': '解析新模钢料或五金设计清单',
                              'tools': ['erp_design_parse_new_mold_upload'],
                              'activation_tools': ['erp_design_parse_new_mold_upload'],
                              'activation_queries': ['上传新模钢料表', '上传新模五金表']}]),
             InspectingModel([]), Gateway())


def test_csv_design_upload_attachment_exposes_tool_search():
    assert harness_module._business_tool_activation_allowed({
        'prompt': '解析当前附件',
        'files': [{'filename': 'M250238-P4五金.csv', 'media_type': 'text/csv'}],
        'skills': [{'key': 'erp_new_mold_design_upload'}],
        'recent_requests': [],
    })


def test_attached_hardware_parse_prefers_upload_tool_over_erp_bom_queries():
    parse_tool = {'type': 'function', 'function': {
        'name': 'erp_design_parse_new_mold_upload',
        'description': '解析并上传当前新模钢料或五金清单附件。',
    }}
    order_tool = {'type': 'function', 'function': {
        'name': 'erp_design_query_orders',
        'description': '查询 ERP 设计订单。',
    }}
    bom_tool = {'type': 'function', 'function': {
        'name': 'erp_design_query_bom',
        'description': '查询 ERP BOM 或物料清单明细。',
    }}
    report_tool = {'type': 'function', 'function': {
        'name': 'erp_design_query_bom_report',
        'description': '查询 ERP BOM 物料和采购进度报表。',
    }}

    class InspectingModel(Model):
        def generate(self, messages, tools):
            names = [tool['function']['name'] for tool in tools]
            if self.calls == 0:
                assert names == ['ToolSearch']
                self.calls += 1
                return {'role': 'assistant', 'tool_calls': [{
                    'id': 'search-hardware-upload',
                    'type': 'function',
                    'function': {'name': 'ToolSearch',
                                 'arguments': json.dumps({'query': '五金清单'})},
                }]}
            assert names == ['ToolSearch', 'erp_design_parse_new_mold_upload']
            self.calls += 1
            return {'role': 'assistant', 'content': json.dumps({
                'response_kind': 'CLARIFICATION',
                'summary': '已选择附件解析上传能力。',
                'evidence_ids': [],
                'suggestions': [],
            }, ensure_ascii=False)}

    gateway = Gateway()
    run_loop(context(
        prompt='解析这个五金清单',
        core_tool_names=[],
        files=[{'id': 'file-hardware',
                'filename': 'M250238-P4-五金请购单66.xlsx',
                'media_type': 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'}],
        tools=[parse_tool, order_tool, bom_tool, report_tool],
        skills=[
            {'key': 'erp_new_mold_design_upload',
             'name': 'ERP 新模设计上传流程',
             'agent_description': '解析新模钢料或五金设计清单',
             'tools': ['erp_design_parse_new_mold_upload'],
             'activation_tools': ['erp_design_parse_new_mold_upload'],
             'activation_queries': ['解析上传附件', '上传新模钢料表', '上传新模五金表']},
            {'key': 'erp_design_workspace_review',
             'name': 'ERP 设计资料核对',
             'agent_description': '查询 ERP 设计订单、BOM 和 BOM 报表',
             'tools': ['erp_design_query_orders'],
             'optional_tools': ['erp_design_query_bom', 'erp_design_query_bom_report'],
             'activation_queries': ['设计与物料清单', '钢料清单', '五金清单']},
        ],
    ), InspectingModel([]), gateway)

    assert gateway.saved['active_tool_names'] == ['erp_design_parse_new_mold_upload']
    assert gateway.physical_calls == 0


def test_xlsx_attachment_without_design_upload_skill_does_not_activate_tools():
    assert not harness_module._business_tool_activation_allowed({
        'prompt': '解析当前附件',
        'files': [{'filename': 'M250238-P4料单.XLSX'}],
        'skills': [],
        'recent_requests': [],
    })


def test_design_elliptical_action_uses_recent_design_object():
    assert harness_module._business_tool_activation_allowed({
        'prompt': '重新匹配',
        'recent_requests': ['解析这个钢料新模清单'],
    })


def test_erp_material_list_alias_activates_erp_design_workspace():
    deferred = {
        'query_design_route_context': {'type': 'function', 'function': {
            'name': 'query_design_route_context', 'description': '读取本地设计BOM与路线上下文'}},
        'erp_design_query_orders': {'type': 'function', 'function': {
            'name': 'erp_design_query_orders', 'description': '查询 ERP 设计订单及其当前状态'}},
        'erp_design_query_bom': {'type': 'function', 'function': {
            'name': 'erp_design_query_bom', 'description': '查询 ERP BOM 或物料清单明细'}},
    }
    groups = harness_module._skill_tool_groups([
        {'key': 'design_route_context_review', 'tools': ['query_design_route_context']},
        {'key': 'erp_design_workspace_review', 'tools': ['erp_design_query_orders'],
         'optional_tools': ['erp_design_query_bom']},
    ], deferred)

    matches, activated, matched_groups = harness_module._find_deferred_tools(
        '设计与物料清单', deferred, groups,
        current_prompt='查询设计与物料清单')

    assert matches == ['erp_design_workspace_review']
    assert activated[0] == 'erp_design_query_orders'
    assert 'erp_design_query_bom' in activated
    assert 'query_design_route_context' not in activated
    assert matched_groups == ['erp_design_workspace_review']


def test_explicit_erp_design_order_view_activates_only_the_order_reader():
    from domain_packs.mold.tool_gateway import SKILLS, tool_schema

    skill = SKILLS['erp_design_workspace_review']
    names = [*skill['tools'], *skill['optional_tools']]
    deferred = {name: tool_schema(name) for name in names}
    groups = harness_module._skill_tool_groups([
        {'key': 'erp_design_workspace_review'},
    ], deferred)

    matches, activated, matched_groups = harness_module._find_deferred_tools(
        'ERP设计订单', deferred, groups,
        current_prompt='查看 M250238-P4 的设计订单')

    assert matches == ['erp_design_workspace_review']
    assert activated == ['erp_design_query_orders']
    assert matched_groups == ['erp_design_workspace_review']


def test_tolerance_search_activates_only_the_single_tolerance_tool():
    deferred = {
        'erp_design_parse_new_mold_upload': {'type': 'function', 'function': {
            'name': 'erp_design_parse_new_mold_upload', 'description': '解析 ERP 新模清单'}},
        'erp_design_evaluate_tolerances': {'type': 'function', 'function': {
            'name': 'erp_design_evaluate_tolerances', 'description': '判断 ERP 新模钢料公差'}},
    }
    groups = harness_module._skill_tool_groups([
        {'key': 'erp_new_mold_design_upload', 'tools': ['erp_design_parse_new_mold_upload'],
         'optional_tools': ['erp_design_evaluate_tolerances'],
         'activation_tools': ['erp_design_parse_new_mold_upload']},
        {'key': 'erp_design_tolerance_evaluation', 'tools': ['erp_design_evaluate_tolerances'],
         'activation_tools': ['erp_design_evaluate_tolerances']},
    ], deferred)

    matches, activated, matched_groups = harness_module._find_deferred_tools(
        '判断公差', deferred, groups, current_prompt='判断这份钢料清单的公差')

    assert matches == ['erp_design_tolerance_evaluation']
    assert activated == ['erp_design_evaluate_tolerances']
    assert matched_groups == ['erp_design_tolerance_evaluation']


def test_erp_mold_number_overrides_model_shortened_local_design_search():
    deferred = {
        'query_design_route_context': {'type': 'function', 'function': {
            'name': 'query_design_route_context', 'description': '读取本地设计BOM与路线上下文'}},
        'erp_design_query_orders': {'type': 'function', 'function': {
            'name': 'erp_design_query_orders', 'description': '查询 ERP 设计订单及其当前状态'}},
        'erp_design_query_bom': {'type': 'function', 'function': {
            'name': 'erp_design_query_bom', 'description': '查询 ERP BOM 明细'}},
        'erp_design_query_bom_report': {'type': 'function', 'function': {
            'name': 'erp_design_query_bom_report', 'description': '查询 ERP BOM 物料和采购进度报表'}},
    }
    groups = harness_module._skill_tool_groups([
        {'key': 'design_route_context_review', 'tools': ['query_design_route_context']},
        {'key': 'erp_design_workspace_review', 'tools': ['erp_design_query_orders'],
         'optional_tools': ['erp_design_query_bom', 'erp_design_query_bom_report']},
    ], deferred)

    matches, activated, _ = harness_module._find_deferred_tools(
        '设计BOM与路线上下文核对', deferred, groups,
        current_prompt='查询设计与物料清单，项目号 M250238，模具号 M250238-P4')

    assert matches == ['erp_design_workspace_review']
    assert activated[0] == 'erp_design_query_orders'
    assert 'query_design_route_context' not in activated


def test_workbench_support_request_hides_tool_search_and_business_catalog():
    many_tools = [{'type': 'function', 'function': {'name': f'query_dummy_{index}', 'description': f'虚拟工具 {index}'}} for index in range(30)]
    class PromptInspectingModel:
        def generate(self, messages, tools):
            prompt = messages[0]['content']
            assert tools == []
            assert '按需工具' not in prompt
            assert 'query_dummy_0' not in prompt
            return {'content': json.dumps({'response_kind': 'CONVERSATION',
                                           'summary': '这是技术排障，不调用业务工具。',
                                           'evidence_ids': [], 'suggestions': []})}
    result = run_loop(context(prompt='测试 500 定位', core_tool_names=['query_dummy_0'], tools=many_tools, skills=[]),
                      PromptInspectingModel(), Gateway())
    assert result['response_kind'] == 'CONVERSATION'


def test_business_request_on_demand_prompt_lists_bounded_capability_catalog_not_every_tool():
    many_tools = [{'type': 'function', 'function': {'name': f'query_dummy_{index}', 'description': f'虚拟工具 {index}'}} for index in range(30)]
    class PromptInspectingModel:
        def generate(self, messages, tools):
            prompt = messages[0]['content']
            assert [tool['function']['name'] for tool in tools] == ['ToolSearch']
            assert 'query_dummy_0' in prompt
            assert 'query_dummy_11' in prompt
            assert 'query_dummy_12' not in prompt
            assert '还有 18 个能力/工具' in prompt
            return {'content': json.dumps({'response_kind': 'CONVERSATION',
                                           'summary': '请说明要查询的项目。',
                                           'evidence_ids': [], 'suggestions': []})}
    result = run_loop(context(prompt='查询项目当前状态', core_tool_names=[], tools=many_tools, skills=[]),
                      PromptInspectingModel(), Gateway())
    assert result['response_kind'] == 'CONVERSATION'


def test_recovery_reuses_persisted_proposal_and_idempotent_receipt():
    gateway = Gateway(); gateway.crash_after_execution = True
    with pytest.raises(ConnectionError): run_loop(context(), Model([PROPOSAL]), gateway)
    assert gateway.saved['pending'][0]['id'] == 'call1'
    resumed = Model([FINAL])
    run_loop(context(**gateway.saved), resumed, gateway)
    assert gateway.physical_calls == 1
    assert resumed.calls == 1  # no regenerated tool proposal
    assert gateway.final['evidence_ids'] == ['e1']


def test_recovery_does_not_reset_deadline():
    gateway = Gateway(); model = Model([PROPOSAL])
    with pytest.raises(RuntimeError, match='BUDGET_EXCEEDED'):
        run_loop(context(deadline=time.time()-1), model, gateway)
    assert model.calls == 0 and gateway.physical_calls == 0


def test_default_main_run_has_no_arbitrary_wall_clock_deadline():
    gateway = Gateway()
    reply = {'role': 'assistant', 'content': json.dumps({
        'response_kind': 'CONVERSATION', 'summary': 'completed normally',
        'evidence_ids': [], 'suggestions': [],
    })}

    result = run_loop(context(), Model([reply]), gateway)

    assert result['summary'] == 'completed normally'
    assert gateway.saved['deadline'] is None


def test_cancel_after_model_return_blocks_tool_execution():
    gateway = Gateway()
    class CancellingModel:
        def generate(self, messages, tools): gateway.cancelled = True; return PROPOSAL
    with pytest.raises(RuntimeError, match='CANCELLED'): run_loop(context(), CancellingModel(), gateway)
    assert gateway.physical_calls == 0


def test_unregistered_tool_is_not_executed():
    bad = copy.deepcopy(PROPOSAL); bad['tool_calls'][0]['function']['name'] = 'write_database'
    gateway = Gateway()
    with pytest.raises(RuntimeError, match='TOOL_FORBIDDEN'): run_loop(context(), Model([bad]), gateway)
    assert gateway.physical_calls == 0


def test_fabricated_evidence_is_rejected():
    gateway = Gateway()
    bad = {'content': json.dumps({'summary':'fake','evidence_ids':['foreign-run']})}
    with pytest.raises(RuntimeError, match='EVIDENCE_INVALID'): run_loop(context(), Model([bad]), gateway)
    assert gateway.final is None


@pytest.mark.parametrize('kind',['CONVERSATION','CLARIFICATION'])
def test_model_decides_to_reply_or_clarify_without_forced_tools(kind):
    gateway=Gateway()
    result=run_loop(context(prompt='这边情况怎么样'),Model([{'content':json.dumps({'response_kind':kind,'summary':'请告诉我是哪套模具。','evidence_ids':[]})}]),gateway)
    assert result['summary']=='请告诉我是哪套模具。'
    assert gateway.physical_calls==0


def test_first_turn_natural_language_gets_protocol_repair_without_business_fallback():
    gateway = Gateway()
    result = run_loop(
        context(prompt='测试 500 定位'),
        Model([
            {'content': '这是一个技术排查问题，不需要查询业务数据。'},
            {'content': json.dumps({'response_kind': 'CONVERSATION',
                                    'summary': '这是一个技术排查问题，不需要查询业务数据。',
                                    'evidence_ids': [], 'suggestions': []})},
        ]),
        gateway,
    )
    assert result['response_kind'] == 'CONVERSATION'
    assert result['summary'] == '这是一个技术排查问题，不需要查询业务数据。'
    assert gateway.physical_calls == 0
    assert gateway.saved['protocol_repairs'] == 1


def test_mixed_greeting_and_business_request_can_call_tools():
    gateway=Gateway()
    run_loop(context(prompt='你好，帮我看看这套模具状态'),Model([PROPOSAL,FINAL]),gateway)
    assert gateway.physical_calls==1 and gateway.final['evidence_ids']==['e1']


@pytest.mark.parametrize('prompt', ['你好', '您好，谢谢', '好的', '收到', '对', '辛苦了'])
def test_pure_conversation_turn_hides_business_tools_even_with_recent_business_context(prompt):
    class InspectingConversationModel:
        def generate(self, messages, tools):
            assert tools == []
            assert '按需工具' not in messages[0]['content']
            return {'content': json.dumps({'response_kind': 'CONVERSATION', 'summary': '你好',
                                           'evidence_ids': [], 'suggestions': []})}
    result = run_loop(context(prompt=prompt, recent_requests=['查询 SMOKE-M001 的项目计划']),
                      InspectingConversationModel(), Gateway())
    assert result['response_kind'] == 'CONVERSATION'


@pytest.mark.parametrize('prompt', ['你好，帮我看看 SMOKE-M001 的计划', '老弟，看下这个项目'])
def test_current_turn_business_action_opens_tools_even_with_social_prefix(prompt):
    gateway = Gateway()
    run_loop(context(prompt=prompt), Model([PROPOSAL, FINAL]), gateway)
    assert gateway.physical_calls == 1


@pytest.mark.parametrize('prompt', ['帮我看看', '继续', '这个呢'])
def test_elliptical_action_can_use_recent_request_only_to_supply_business_object(prompt):
    gateway = Gateway()
    run_loop(context(prompt=prompt, recent_requests=['查询 SMOKE-M001 的项目计划']),
             Model([PROPOSAL, FINAL]), gateway)
    assert gateway.physical_calls == 1


def test_recent_business_request_does_not_open_tools_for_unrelated_current_lookup():
    class InspectingConversationModel:
        def generate(self, messages, tools):
            assert tools == []
            return {'content': json.dumps({'response_kind': 'CONVERSATION', 'summary': '这是一般问题。',
                                           'evidence_ids': [], 'suggestions': []})}
    run_loop(context(prompt='查一下天气', recent_requests=['查询 SMOKE-M001 的项目计划']),
             InspectingConversationModel(), Gateway())


def test_duplicate_tool_call_enters_finalization_without_reexecuting():
    duplicate = copy.deepcopy(PROPOSAL)
    duplicate["tool_calls"][0]["id"] = "call2"
    gateway = Gateway()
    model = Model([PROPOSAL, duplicate, FINAL])
    result = run_loop(context(), model, gateway)
    assert result["summary"] == "one visible project"
    assert gateway.physical_calls == 1
    assert gateway.saved["finalizing"] is True
    assert gateway.saved["protocol_repairs"] == 1


def test_duplicate_repair_is_transient_and_provider_receives_one_leading_system_message():
    duplicate = copy.deepcopy(PROPOSAL)
    duplicate["tool_calls"][0]["id"] = "call2"
    gateway = Gateway()
    model = TranscriptModel([PROPOSAL, duplicate, FINAL])

    run_loop(context(), model, gateway)

    repaired_request = model.transcripts[2]
    assert repaired_request[0]['role'] == 'system'
    assert '已经用相同参数返回过证据' in repaired_request[0]['content']
    assert all(message['role'] != 'system' for message in repaired_request[1:])
    assert all(message['role'] != 'system' for message in gateway.saved['messages'][1:])


def test_confirmed_proposal_resume_cannot_return_to_awaiting_approval():
    waiting_again = {'role': 'assistant', 'content': json.dumps({
        'response_kind': 'AWAITING_APPROVAL',
        'summary': '确认卡已经准备好，请再次确认。',
        'evidence_ids': ['e1'],
        'suggestions': [],
    }, ensure_ascii=False)}
    receipt_reply = {'role': 'assistant', 'content': json.dumps({
        'response_kind': 'BUSINESS',
        'proposal_decision': 'approved',
        'summary': '已收到本人确认，权威回执表明本次登记已经完成。',
        'evidence_ids': ['e1'],
        'suggestions': [],
    }, ensure_ascii=False)}
    messages = [
        {'role': 'system', 'content': '通用智能体协议'},
        {'role': 'user', 'content': '请准备正式业务操作'},
        {'role': 'assistant', 'content': '确认卡已经准备，请本人确认。'},
        {'role': 'user', 'content': '我已在可信确认界面完成本人确认，请根据执行回执继续回复。'},
        {'role': 'system', 'content': '可信人工决定：{"decision":"approved"}'},
    ]
    gateway = Gateway()
    model = TranscriptModel([waiting_again, receipt_reply])

    result = run_loop(context(
        prompt='请准备项目正式操作确认卡',
        messages=messages,
        evidence_ids=['e1'],
        finalizing=True,
        action_outcomes={'prepare_demo_action': {'status': 'success', 'evidence_id': 'e1'}},
        proposal_resolution={'decision': 'approved', 'authoritative_receipt': {'status': 'executed'}},
    ), model, gateway)

    assert result['response_kind'] == 'BUSINESS'
    assert result['proposal_decision'] == 'approved'
    assert result['summary'].startswith('已收到本人确认')
    assert model.calls == 2
    assert '不得再次输出 AWAITING_APPROVAL' in model.transcripts[1][0]['content']
    assert gateway.saved['protocol_repairs'] == 1
    assert gateway.saved['next_model_instructions'] == []


def test_legacy_mid_history_system_messages_are_consolidated_at_provider_boundary():
    class StrictProviderModel:
        def generate(self, messages, tools):
            assert messages[0]['role'] == 'system'
            assert '原始系统提示' in messages[0]['content']
            assert '旧检查点纠偏提示' in messages[0]['content']
            assert all(message['role'] != 'system' for message in messages[1:])
            return {'role': 'assistant', 'content': json.dumps({
                'response_kind': 'CONVERSATION', 'summary': '已兼容旧检查点。',
                'evidence_ids': [], 'suggestions': [],
            }, ensure_ascii=False)}

    saved_messages = [
        {'role': 'system', 'content': '原始系统提示'},
        {'role': 'user', 'content': '继续'},
        {'role': 'system', 'content': '旧检查点纠偏提示'},
    ]
    gateway = Gateway()
    result = run_loop(context(prompt='继续', messages=saved_messages), StrictProviderModel(), gateway)

    assert result['response_kind'] == 'CONVERSATION'
    assert gateway.saved['messages'] == saved_messages


def test_natural_language_final_after_evidence_gets_protocol_repair_not_wrapped():
    gateway = Gateway()
    result = run_loop(
        context(),
        Model([PROPOSAL, {"content": "根据已有证据，当前未见客户验收依据。"}, FINAL]),
        gateway,
    )
    assert result["summary"] == "one visible project"
    assert gateway.final["summary"] != "根据已有证据，当前未见客户验收依据。"
    assert gateway.physical_calls == 1
    assert gateway.saved["protocol_repairs"] == 1


def test_protocol_repair_is_bounded_and_fails_closed():
    gateway = Gateway()
    invalid = {"content": "根据已有证据，当前未见客户验收依据。"}
    with pytest.raises(RuntimeError, match="MODEL_OUTPUT_INVALID"):
        run_loop(context(), Model([PROPOSAL, invalid, invalid, invalid]), gateway)
    assert gateway.final is None


def test_evidence_loop_is_forced_to_finalize_at_model_turn_budget():
    gateway = Gateway()

    class LoopingModel:
        def __init__(self): self.tool_catalog_sizes = []
        def generate(self, messages, tools):
            self.tool_catalog_sizes.append(len(tools))
            if tools:
                index = len(self.tool_catalog_sizes)
                proposal = copy.deepcopy(PROPOSAL)
                proposal['tool_calls'][0]['id'] = f'call{index}'
                proposal['tool_calls'][0]['function']['arguments'] = json.dumps({'page': index})
                return proposal
                assert '工具调用阶段现在结束' in messages[0]['content']
                assert all(message['role'] != 'system' for message in messages[1:])
            return {'content': json.dumps({'response_kind': 'BUSINESS', 'summary': '根据已有证据回答',
                                           'evidence_ids': ['e1'], 'suggestions': []})}

    model = LoopingModel()
    result = run_loop(context(), model, gateway)

    assert result['summary'] == '根据已有证据回答'
    assert gateway.physical_calls == 11
    assert model.tool_catalog_sizes == [1] * 11 + [0]
    assert gateway.saved['finalizing'] is True


def test_model_controls_tool_batch_size_and_visible_progress_is_persisted():
    calls = []
    for index in range(6):
        calls.append({
            'id': f'batch-{index}',
            'type': 'function',
            'function': {
                'name': 'query_projects',
                'arguments': json.dumps({'page': index + 1}),
            },
        })
    progress = '我先核对六组项目记录，再汇总结论。'
    gateway = Gateway()
    result = run_loop(
        context(),
        Model([{'role': 'assistant', 'content': progress, 'tool_calls': calls}, FINAL]),
        gateway,
    )

    assert result['summary'] == 'one visible project'
    assert gateway.physical_calls == 6
    assistant = next(message for message in gateway.saved['messages']
                     if message.get('role') == 'assistant' and message.get('tool_calls'))
    assert assistant['content'] == progress
    assert len(assistant['tool_calls']) == 6


def test_tool_results_feed_back_across_as_many_model_rounds_as_needed():
    first = {'role': 'assistant', 'content': '先核对两个范围。', 'tool_calls': [
        {'id': 'round-1-a', 'type': 'function',
         'function': {'name': 'query_projects', 'arguments': json.dumps({'page': 1})}},
        {'id': 'round-1-b', 'type': 'function',
         'function': {'name': 'query_projects', 'arguments': json.dumps({'page': 2})}},
    ]}
    second = {'role': 'assistant', 'content': '还需要补查一个范围。', 'tool_calls': [
        {'id': 'round-2-a', 'type': 'function',
         'function': {'name': 'query_projects', 'arguments': json.dumps({'page': 3})}},
    ]}
    gateway = Gateway()
    result = run_loop(context(), Model([first, second, FINAL]), gateway)

    assert result['summary'] == 'one visible project'
    assert gateway.physical_calls == 3
    roles = [message['role'] for message in gateway.saved['messages']]
    assert roles == ['system', 'user', 'assistant', 'tool', 'tool', 'assistant', 'tool']


def test_failed_model_call_records_timing_and_does_not_fake_result():
    class TimeoutModel:
        last_metrics={'tool_count':1,'total_ms':60000}
        def generate(self,messages,tools): raise ModelError('MODEL_READ_TIMEOUT')
    gateway=Gateway()
    with pytest.raises(ModelError):run_loop(context(),TimeoutModel(),gateway)
    assert gateway.saved['phase']=='MODEL_FAILED'
    assert gateway.saved['model_metrics']['total_ms']==60000
    assert gateway.final is None


def test_silent_model_wait_renews_run_lease(monkeypatch):
    monkeypatch.setattr(harness_module, 'LEASE_HEARTBEAT_SECONDS', 0.01)

    class SlowModel:
        def generate(self, messages, tools):
            time.sleep(0.045)
            return {'role': 'assistant', 'content': json.dumps({
                'response_kind': 'CONVERSATION', 'summary': 'heartbeat ok',
                'evidence_ids': [], 'suggestions': [],
            })}

    class LeaseGateway(Gateway):
        def __init__(self):
            super().__init__()
            self.lease_checks = 0
        def check(self):
            self.lease_checks += 1
            super().check()

    gateway = LeaseGateway()
    result = run_loop(context(), SlowModel(), gateway)

    assert result['summary'] == 'heartbeat ok'
    assert gateway.lease_checks >= 3


def test_safe_pre_delta_retry_wait_does_not_consume_business_deadline():
    class RetriedModel:
        last_metrics = {}

        def generate(self, messages, tools):
            time.sleep(0.15)
            self.last_metrics = {'retry_count': 1, 'retry_wait_ms': 200}
            return {'role': 'assistant', 'content': json.dumps({
                'response_kind': 'CONVERSATION', 'summary': 'retry budget preserved',
                'evidence_ids': [], 'suggestions': [],
            })}

    gateway = Gateway()
    result = run_loop(context(deadline=time.time() + 0.1), RetriedModel(), gateway, max_seconds=1)

    assert result['summary'] == 'retry budget preserved'
    assert gateway.saved['deadline'] > time.time()


def test_context_budget_compacts_model_visible_tool_history_before_next_model_call():
    gateway = Gateway()

    class LargeResultGateway(Gateway):
        def execute(self, seq, key, arguments):
            self.physical_calls += 1
            return {"evidence_id": "e1", "data": [{"code": "DEMO-A", "detail": "长字段" * 5000}]}

    class InspectingModel:
        def __init__(self): self.calls = 0
        def generate(self, messages, tools):
            self.calls += 1
            if self.calls == 1:
                return copy.deepcopy(PROPOSAL)
            tool_content = next(message["content"] for message in messages if message.get("role") == "tool")
            assert "compact_summary" in tool_content
            assert len(tool_content) < 2000
            return copy.deepcopy(FINAL)

    gateway = LargeResultGateway()
    result = run_loop(context(), InspectingModel(), gateway, context_window=7000, max_output_tokens=512)
    assert result["summary"] == "one visible project"
    assert gateway.saved["context_usage"]["compaction_count"] == 1
    assert gateway.saved["context_compactions"][0]["saved_tokens"] > 0


def test_compacted_context_does_not_reuse_stale_provider_prompt_tokens():
    second_call = {'role': 'assistant', 'tool_calls': [{
        'id': 'call-2', 'type': 'function',
        'function': {'name': 'query_projects', 'arguments': '{"page":2}'},
    }]}

    class TwoStageGateway(Gateway):
        def execute(self, seq, key, arguments):
            self.physical_calls += 1
            if seq == 0:
                return {'evidence_id': 'e1', 'data': [
                    {'code': 'DEMO-A', 'detail': '长字段' * 5000},
                ]}
            return {'evidence_id': 'e2', 'data': []}

    class StaleUsageModel:
        def __init__(self):
            self.calls = 0
            self.last_metrics = {}

        def generate(self, messages, tools):
            self.calls += 1
            if self.calls == 1:
                self.last_metrics = {'prompt_tokens': 3000, 'completion_tokens': 20}
                return copy.deepcopy(PROPOSAL)
            if self.calls == 2:
                tool_content = next(message['content'] for message in messages if message.get('role') == 'tool')
                assert 'compact_summary' in tool_content
                # This count belongs only to call 2. It must not be reused for
                # the changed transcript after the next assistant message.
                self.last_metrics = {'prompt_tokens': 7446, 'completion_tokens': 27}
                return copy.deepcopy(second_call)
            self.last_metrics = {'prompt_tokens': 4200, 'completion_tokens': 60}
            return copy.deepcopy(FINAL)

    gateway = TwoStageGateway()
    model = StaleUsageModel()
    result = run_loop(context(), model, gateway, context_window=8192, max_output_tokens=2048)

    assert result['summary'] == 'one visible project'
    assert gateway.physical_calls == 2
    assert model.calls == 3
    assert gateway.saved['context_usage']['used_tokens'] <= gateway.saved['context_usage']['safe_limit']




def test_followup_user_context_is_data_and_current_request_is_last():
    class FollowupModel:
        def generate(self,messages,tools):
            text=messages[-1]['content']
            assert '同一会话近期本人请求' in text
            assert text.endswith('修改该单据方案，不提交')
            assert '联络单甲' in text
            return {'content':json.dumps({'response_kind':'CLARIFICATION','summary':'请核对本次方案','evidence_ids':[],'suggestions':[]})}
    run_loop(context(prompt='修改该单据方案，不提交',recent_requests=['查询联络单甲']),FollowupModel(),Gateway())
