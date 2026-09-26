"""验证实际 Adapter 请求参数，不用温度或 token 上限冒充思考档位。"""
import json
import httpx
import pytest
from agent_core.model_adapter import ModelAdapter
from agent_core.ollama_adapter import OllamaAdapter


@pytest.mark.parametrize('effort', ['low','high','max'])
@pytest.mark.parametrize('streaming', [False,True])
def test_glm_reasoning_is_present_in_real_request_payload(effort, streaming):
    def serve(request):
        body=json.loads(request.content)
        assert body['reasoning_effort']==effort
        assert body['temperature']==0.2 and body['max_tokens']==2048
        if streaming:
            events=[{'choices':[{'delta':{'content':'{}'},'finish_reason':None}]},
                    {'choices':[{'delta':{},'finish_reason':'stop'}]}]
            return httpx.Response(200,headers={'content-type':'text/event-stream'},
                content=''.join('data: '+json.dumps(e)+'\n\n' for e in events)+'data: [DONE]\n\n')
        return httpx.Response(200,json={'choices':[{'message':{'role':'assistant','content':'{}'},'finish_reason':'stop'}]})
    adapter=ModelAdapter('https://model.example/v1','synthetic','GLM-5.3-Flash',
                         reasoning_effort=effort,transport=httpx.MockTransport(serve))
    try:
        response=adapter.generate_stream([],[],lambda event:None) if streaming else adapter.generate([],[])
        assert response['content']=='{}'
    finally:adapter.close()


@pytest.mark.parametrize('model,effort,policy', [('GLM-5.3-Flash','medium','default'),
    ('GLM-5.3-Flash','off','default'),('unknown-model','high','default'),('unknown-model','arbitrary','openai')])
def test_unsupported_effort_is_rejected_before_any_request(model, effort, policy):
    with pytest.raises(ValueError,match='MODEL_REASONING_UNSUPPORTED'):
        ModelAdapter('https://model.example/v1','synthetic',model,
                     reasoning_effort=effort,reasoning_policy=policy)


def test_unknown_model_default_does_not_send_reasoning_parameter():
    def serve(request):
        assert 'reasoning_effort' not in json.loads(request.content)
        return httpx.Response(200,json={'choices':[{'message':{'role':'assistant','content':'{}'},'finish_reason':'stop'}]})
    adapter=ModelAdapter('https://model.example/v1','synthetic','unknown',transport=httpx.MockTransport(serve))
    try:assert adapter.generate([],[])['content']=='{}'
    finally:adapter.close()


def test_explicit_openai_policy_uses_reasoning_model_token_parameter():
    def serve(request):
        body=json.loads(request.content)
        assert body['reasoning_effort']=='medium'
        assert body['max_completion_tokens']==2048
        assert 'max_tokens' not in body and 'temperature' not in body
        return httpx.Response(200,json={'choices':[{'message':{'role':'assistant','content':'{}'},'finish_reason':'stop'}]})
    adapter=ModelAdapter('https://model.example/v1','synthetic','configured-model',
                         reasoning_effort='medium',reasoning_policy='openai',transport=httpx.MockTransport(serve))
    try:assert adapter.generate([],[])['content']=='{}'
    finally:adapter.close()


def test_ollama_think_switch_uses_ollama_parameter_not_reasoning_effort():
    def serve(request):
        body=json.loads(request.content)
        assert body['think'] is True and 'reasoning_effort' not in body
        return httpx.Response(200,json={'done':True,'message':{'role':'assistant','content':json.dumps({
            'action':'RESPOND','tool_name':'','arguments':{},'response_kind':'CONVERSATION',
            'summary':'合成答复','evidence_ids':[],'suggestions':[]})}})
    adapter=OllamaAdapter('http://127.0.0.1:11434','configured-local',reasoning_policy='ollama',
                         reasoning_effort='on',transport=httpx.MockTransport(serve))
    assert json.loads(adapter.generate([],[])['content'])['summary']=='合成答复'
