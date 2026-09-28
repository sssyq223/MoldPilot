import json
import pytest
import httpx
from app.ollama_adapter import OllamaAdapter
from app.model_adapter import ModelError
from test_model_harness import TOOL


def reply(action='RESPOND',tool='',arguments=None,response_kind='CONVERSATION',proposal_decision=None):
    value={'action':action,'tool_name':tool,'arguments':{} if arguments is None else arguments,
           'response_kind':response_kind,'summary':'您好，请问需要什么帮助？','evidence_ids':[],'suggestions':[]}
    if proposal_decision is not None:
        value['proposal_decision'] = proposal_decision
    return {'done':True,'done_reason':'stop','message':{'role':'assistant','content':json.dumps(value)},'load_duration':1_000_000}


def test_ollama_model_decides_actions_with_full_catalog_and_no_secret():
    captured=[]
    def serve(r):
        body=json.loads(r.content);captured.append(body)
        assert 'authorization' not in r.headers
        assert body['think'] is False
        assert 'query_projects' in body['messages'][0]['content']
        assert body['format']['properties']['action']['enum']==['CALL_TOOL','RESPOND']
        assert 'AWAITING_APPROVAL' in body['format']['properties']['response_kind']['enum']
        assert body['format']['properties']['proposal_decision']['enum'] == ['approved', 'dismissed']
        return httpx.Response(200,json=reply('CALL_TOOL','query_projects'))
    model=OllamaAdapter('http://127.0.0.1:49876','deepseek-r1:7b',transport=httpx.MockTransport(serve))
    result=model.generate([{'role':'user','content':'你好，帮我看看这套模具状态'}],[TOOL])
    assert result['tool_calls'][0]['function']=={'name':'query_projects','arguments':'{}'}
    assert model.last_metrics['load_duration_ms']==1


def test_ollama_model_can_supply_arguments_declared_by_tool_schema():
    tool={'type':'function','function':{'name':'query_project','parameters':{
        'type':'object','properties':{'project_code':{'type':'string'}},
        'required':['project_code'],'additionalProperties':False}}}
    body=reply('CALL_TOOL','query_project',{'project_code':'BROWSER-OUT-001'})
    model=OllamaAdapter('http://127.0.0.1:49876','qwen3:8b',
        transport=httpx.MockTransport(lambda r:httpx.Response(200,json=body)))

    result=model.generate([{'role':'user','content':'查询项目'}],[tool])

    assert json.loads(result['tool_calls'][0]['function']['arguments']) == {
        'project_code':'BROWSER-OUT-001'}


@pytest.mark.parametrize('url',['http://example.com','https://example.com','http://127.0.0.1@evil.example','http://127.0.0.1/?key=secret'])
def test_local_model_http_exception_cannot_target_remote_server(url):
    with pytest.raises(ValueError):OllamaAdapter(url,'model')


@pytest.mark.parametrize('body',[reply('CALL_TOOL','write_database'),reply('CALL_TOOL','query_projects',{'sql':'delete'}),
                               {**reply(),'done_reason':'length'},reply('UNREGISTERED')])
def test_invalid_local_model_actions_do_not_become_tool_calls(body):
    model=OllamaAdapter('http://127.0.0.1:49876','test',transport=httpx.MockTransport(lambda r:httpx.Response(200,json=body)))
    with pytest.raises(ModelError):model.generate([{'role':'user','content':'test'}],[TOOL])


def test_model_can_respond_without_any_tool_execution():
    model=OllamaAdapter('http://127.0.0.1:49876','test',transport=httpx.MockTransport(lambda r:httpx.Response(200,json=reply())))
    result=model.generate([{'role':'user','content':'随便聊聊'}],[TOOL])
    assert 'tool_calls' not in result
    assert json.loads(result['content'])['response_kind']=='CONVERSATION'


def test_ollama_model_can_emit_waiting_approval_and_resolution_decision():
    waiting = reply(response_kind='AWAITING_APPROVAL')
    model=OllamaAdapter(
        'http://127.0.0.1:49876',
        'test',
        transport=httpx.MockTransport(lambda r: httpx.Response(200, json=waiting)),
    )
    result=model.generate([{'role':'user','content':'请准备操作建议'}], [TOOL])
    assert json.loads(result['content'])['response_kind'] == 'AWAITING_APPROVAL'

    resolved = reply(response_kind='BUSINESS', proposal_decision='approved')
    model=OllamaAdapter(
        'http://127.0.0.1:49876',
        'test',
        transport=httpx.MockTransport(lambda r: httpx.Response(200, json=resolved)),
    )
    result=model.generate([{'role':'user','content':'已确认操作建议'}], [TOOL])
    assert json.loads(result['content'])['proposal_decision'] == 'approved'
