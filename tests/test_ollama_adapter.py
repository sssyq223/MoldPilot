import json
import pytest
import httpx
from app.ollama_adapter import OllamaAdapter
from app.model_adapter import ModelError
from test_model_harness import TOOL


def reply(action='RESPOND',tool='',arguments=None):
    value={'action':action,'tool_name':tool,'arguments':{} if arguments is None else arguments,
           'response_kind':'CONVERSATION','summary':'您好，请问需要什么帮助？','evidence_ids':[],'suggestions':[]}
    return {'done':True,'done_reason':'stop','message':{'role':'assistant','content':json.dumps(value)},'load_duration':1_000_000}


def test_ollama_model_decides_actions_with_full_catalog_and_no_secret():
    captured=[]
    def serve(r):
        body=json.loads(r.content);captured.append(body)
        assert 'authorization' not in r.headers
        assert body['think'] is False
        assert 'query_projects' in body['messages'][0]['content']
        assert body['format']['properties']['action']['enum']==['CALL_TOOL','RESPOND']
        return httpx.Response(200,json=reply('CALL_TOOL','query_projects'))
    model=OllamaAdapter('http://127.0.0.1:49876','deepseek-r1:7b',transport=httpx.MockTransport(serve))
    result=model.generate([{'role':'user','content':'你好，帮我看看这套模具状态'}],[TOOL])
    assert result['tool_calls'][0]['function']=={'name':'query_projects','arguments':'{}'}
    assert model.last_metrics['load_duration_ms']==1


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
