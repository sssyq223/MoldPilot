import copy
import json
import ssl
import time

import httpx
import pytest

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
        return httpx.Response(200, json={'choices': [{'finish_reason': 'tool_calls', 'message': {
            'role': 'assistant', 'tool_calls': [{'id': 'c1', 'function': {'name': 'query_projects', 'arguments': '{}'}}]}}]})
    model = ModelAdapter('https://model.example/v1', 'synthetic-key', 'test-model', transport=httpx.MockTransport(serve))
    assert model.generate([{'role': 'user', 'content': 'query'}], [])['tool_calls'][0]['id'] == 'c1'


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
PROPOSAL = {'role': 'assistant', 'tool_calls': [{'id': 'call1', 'type': 'function', 'function': {'name': 'query_projects', 'arguments': '{}'}}]}
FINAL = {'role': 'assistant', 'content': json.dumps({'summary': 'one visible project', 'evidence_ids': ['e1'], 'suggestions': []})}


class Model:
    def __init__(self, replies): self.replies, self.calls = replies, 0
    def generate(self, messages, tools):
        self.calls += 1
        return copy.deepcopy(self.replies.pop(0))


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


def context(**kwargs): return {'prompt': 'query', 'tools': [TOOL], 'skills': [], **kwargs}


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


def test_mixed_greeting_and_business_request_can_call_tools():
    gateway=Gateway()
    run_loop(context(prompt='你好，帮我看看这套模具状态'),Model([PROPOSAL,FINAL]),gateway)
    assert gateway.physical_calls==1 and gateway.final['evidence_ids']==['e1']


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


def test_evidence_loop_is_forced_to_finalize_before_budget_exhaustion():
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
            assert '工具调用阶段现在结束' in messages[-1]['content']
            return {'content': json.dumps({'response_kind': 'BUSINESS', 'summary': '根据已有证据回答',
                                           'evidence_ids': ['e1'], 'suggestions': []})}

    model = LoopingModel()
    result = run_loop(context(), model, gateway)

    assert result['summary'] == '根据已有证据回答'
    assert gateway.physical_calls == 8
    assert model.tool_catalog_sizes == [1] * 8 + [0]
    assert gateway.saved['finalizing'] is True


def test_failed_model_call_records_timing_and_does_not_fake_result():
    class TimeoutModel:
        last_metrics={'tool_count':1,'total_ms':60000}
        def generate(self,messages,tools): raise ModelError('MODEL_READ_TIMEOUT')
    gateway=Gateway()
    with pytest.raises(ModelError):run_loop(context(),TimeoutModel(),gateway)
    assert gateway.saved['phase']=='MODEL_FAILED'
    assert gateway.saved['model_metrics']['total_ms']==60000
    assert gateway.final is None


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




def test_followup_user_context_is_data_and_current_request_is_last():
    class FollowupModel:
        def generate(self,messages,tools):
            text=messages[-1]['content']
            assert '同一会话近期本人请求' in text
            assert text.endswith('修改该单据方案，不提交')
            assert '联络单甲' in text
            return {'content':json.dumps({'response_kind':'CLARIFICATION','summary':'请核对本次方案','evidence_ids':[],'suggestions':[]})}
    run_loop(context(prompt='修改该单据方案，不提交',recent_requests=['查询联络单甲']),FollowupModel(),Gateway())
