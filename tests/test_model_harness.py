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
PROPOSAL = {'role': 'assistant', 'tool_calls': [{'id': 'call1', 'type': 'function', 'function': {'name': 'query_projects', 'arguments': '{}'}}]}
PLAN_PROPOSAL = {'role': 'assistant', 'tool_calls': [{'id': 'call-plan', 'type': 'function', 'function': {'name': 'query_project_plan_context', 'arguments': '{}'}}]}
CONTACT_PROPOSAL = {'role': 'assistant', 'tool_calls': [{'id': 'call-contact', 'type': 'function', 'function': {'name': 'query_contact_context', 'arguments': '{}'}}]}
CONTRACT_PROPOSAL = {'role': 'assistant', 'tool_calls': [{'id': 'call-contract', 'type': 'function', 'function': {'name': 'query_contract_context', 'arguments': '{}'}}]}
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
    return {'prompt': 'query', 'tools': [TOOL], 'skills': [], 'core_tool_names': core_tool_names, **kwargs}


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
        ['ToolSearch', 'query_project_plan_context', 'prepare_project_plan_baseline'],
        ['ToolSearch', 'query_project_plan_context', 'prepare_project_plan_baseline'],
    ]
    assert gateway.physical_calls == 1
    assert gateway.saved['active_tool_names'] == ['prepare_project_plan_baseline', 'query_project_plan_context']


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
    run_loop(context(core_tool_names=[], tools=[
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
        ['ToolSearch', 'query_contact_cases', 'query_contact_context',
         'prepare_contact_resolution', 'prepare_contact_review',
         'prepare_contact_close', 'prepare_contact_respond'],
        ['ToolSearch', 'query_contact_cases', 'query_contact_context',
         'prepare_contact_resolution', 'prepare_contact_review',
         'prepare_contact_close', 'prepare_contact_respond'],
    ]
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
    model = InspectingRepliesModel([CONTRACT_TOOL_SEARCH, CONTRACT_PROPOSAL, FINAL])
    run_loop(context(core_tool_names=[], tools=[contract_context, contract_prepare, contract_signing, quote_context, quote_prepare, dossier],
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
        ['ToolSearch', 'query_contract_context', 'prepare_contract_record', 'prepare_contract_signing_record'],
        ['ToolSearch', 'query_contract_context', 'prepare_contract_record', 'prepare_contract_signing_record'],
    ]
    assert gateway.saved['active_tool_names'] == ['prepare_contract_record', 'prepare_contract_signing_record', 'query_contract_context']


def test_business_query_mentioning_model_still_allows_tool_search():
    class InspectingModel(Model):
        def generate(self, messages, tools):
            assert [tool['function']['name'] for tool in tools] == ['ToolSearch']
            return {'content': json.dumps({'response_kind': 'CLARIFICATION',
                                           'summary': '请提供项目编号。',
                                           'evidence_ids': [], 'suggestions': []})}
    run_loop(context(prompt='用模型查询项目计划，看看项目大节点', core_tool_names=[],
                     tools=[OTHER_TOOL], skills=[]), InspectingModel([]), Gateway())


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
