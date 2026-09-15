import json

import httpx
import pytest

from app.agent_worker import create_model
from app.config import Settings
from app.model_adapter import ModelAdapter, ModelError

ORIGIN = 'http://192.168.50.10:8000'


def test_configured_worker_uses_lan_without_credentials_and_preserves_full_tools(monkeypatch):
    monkeypatch.setenv('HTTP_PROXY', 'http://127.0.0.1:9')
    config = Settings(_env_file=None, llm_enabled=True, worker_secret='test',
                      llm_base_url=ORIGIN+'/v1', llm_trusted_http_origin=ORIGIN,
                      llm_model='Qwen3-30B-A3B-Instruct')
    model = create_model(config)
    from app.tool_gateway import TOOLS, tool_schema
    tools = [tool_schema(key) for key in TOOLS]
    def serve(request):
        assert str(request.url) == ORIGIN+'/v1/chat/completions'
        assert 'authorization' not in request.headers
        assert json.loads(request.content)['tools'] == tools
        return httpx.Response(200, json={'choices':[{'message':{'role':'assistant','content':'ok'}}]})
    model.transport = httpx.MockTransport(serve)
    assert model.proxy is None
    assert model.generate([{'role':'user','content':'你好，帮我看看模具状态'}], tools)['content']=='ok'


@pytest.mark.parametrize('url,trusted,key,proxy', [
    (ORIGIN+'/v1','','',None),
    (ORIGIN+'/v1','http://192.168.0.23:8000','',None),
    (ORIGIN+'/v1','http://192.168.50.10:8001','',None),
    ('http://8.8.8.8/v1','http://8.8.8.8','',None),
    ('http://model.example/v1','http://model.example','',None),
    (ORIGIN+'/v1',ORIGIN,'public-secret',None),
    (ORIGIN+'/v1',ORIGIN,'','http://127.0.0.1:7890'),
    (ORIGIN+'/v1?token=secret',ORIGIN,'',None),
    (ORIGIN+'/v1',ORIGIN+'/v1','',None),
])
def test_http_boundary_fails_closed(url,trusted,key,proxy):
    with pytest.raises(ValueError):
        ModelAdapter(url,key,'test',trusted_http_origin=trusted,proxy=proxy)


def test_lan_redirect_does_not_fall_back_to_public_service():
    calls=[]
    def serve(request):
        calls.append(request)
        return httpx.Response(307,headers={'location':'https://public.example/v1/chat/completions'})
    model=ModelAdapter(ORIGIN+'/v1','','test',trusted_http_origin=ORIGIN,
                       transport=httpx.MockTransport(serve))
    with pytest.raises(ModelError,match='MODEL_HTTP_FAILED'): model.generate([],[])
    assert len(calls)==1
