from sqlalchemy import select,func
from app.agent_worker import Gateway
from app.models import Step,Grant
from test_agent_api import start,worker_headers
from conftest import sign_in,draft


def headers(context):
    return {**worker_headers(),'Accept':'application/json, text/event-stream',
        'MCP-Protocol-Version':'2025-06-18','X-Agent-Run-Epoch':str(context['epoch'])}


def rpc(client,c,method,params=None,**changes):
    body={'jsonrpc':'2.0','id':1,'method':method,'params':params or {},**changes}
    return client.post(f"/internal/runs/{c['id']}/mcp",headers=headers(c),json=body)


def test_harness_mcp_discovery_and_execution_use_same_receipt(client,data,monkeypatch):
    sign_in(client);draft(client,data[0])
    run,c=start(client,monkeypatch)
    client.headers.update(worker_headers())
    gateway=Gateway(client,c)
    catalog=gateway.discover()
    assert {t['function']['name'] for t in catalog}=={'query_purchase_requests'}
    assert gateway.tool_annotations['query_purchase_requests']['readOnlyHint'] is True
    first=gateway.execute(0,'query_purchase_requests',{})
    assert first['source']=='agent_db' and len(first['data'])==1
    assert gateway.execute(0,'query_purchase_requests',{})['evidence_id']==first['evidence_id']
    with data[1]() as db:assert db.scalar(select(func.count()).select_from(Step).where(Step.run_id==run['id']))==1


def test_mcp_transport_and_protocol_validation(client,data,monkeypatch):
    _,c=start(client,monkeypatch)
    path=f"/internal/runs/{c['id']}/mcp"
    assert client.get(path,headers=headers(c)).status_code==405
    assert client.post(path,json={'jsonrpc':'2.0','id':1,'method':'ping'}).status_code==401
    assert client.post(path,headers={**headers(c),'Origin':'https://untrusted.invalid'},json={}).status_code==403
    assert client.post(path,headers={**headers(c),'MCP-Protocol-Version':'unsupported'},json={}).status_code==400
    assert client.post(path,headers={**headers(c),'Accept':'application/json'},json={}).status_code==406
    assert rpc(client,c,'unknown').json()['error']['code']==-32601
    assert rpc(client,c,'tools/call',{'name':'query_purchase_requests','arguments':{},'_meta':{'agent/sequence':True}}).json()['error']['code']==-32602
    assert rpc(client,c,'initialize',{'protocolVersion':'2025-06-18','capabilities':{},'clientInfo':{'name':'test','version':'1'}}).json()['result']['protocolVersion']=='2025-06-18'
    r=client.post(path,headers=headers(c),json={'jsonrpc':'2.0','method':'notifications/initialized'})
    assert r.status_code==202 and r.content==b''


def test_mcp_cannot_expand_actor_tools_and_reports_tool_errors(client,data,monkeypatch):
    _,c=start(client,monkeypatch)
    assert rpc(client,c,'tools/call',{'name':'query_contact_cases','arguments':{},'_meta':{'agent/sequence':0}}).json()['error']['code']==-32602
    r=rpc(client,c,'tools/call',{'name':'query_purchase_requests','arguments':{'user_id':data[0]['admin']},'_meta':{'agent/sequence':0}})
    assert r.status_code==200 and r.json()['result']['isError'] is True
    error_result=r.json()['result']
    assert error_result['errorCode']=='INVALID_TOOL_INPUT'
    assert error_result['structuredContent']['tool_error']['code']=='INVALID_TOOL_INPUT'
    client.headers.update(worker_headers())
    gateway=Gateway(client,c)
    observed=gateway.execute(0,'query_purchase_requests',{'user_id':data[0]['admin']})
    assert observed['tool_error']['code']=='INVALID_TOOL_INPUT'
    with data[1]() as db:assert db.scalar(select(func.count()).select_from(Step).where(Step.run_id==c['id']))==0


def test_mcp_unexpected_tool_failure_is_recoverable(client,data,monkeypatch,caplog):
    _,c=start(client,monkeypatch)
    from app import mcp_api

    def fail(*_args, **_kwargs):
        raise RuntimeError('private upstream response must not reach the user')

    monkeypatch.setattr(mcp_api.tools,'execute',fail)
    response=rpc(client,c,'tools/call',{'name':'query_purchase_requests','arguments':{},'_meta':{'agent/sequence':0}})

    assert response.status_code==200
    result=response.json()['result']
    assert result['isError'] is True
    assert result['errorCode']=='TOOL_EXECUTION_FAILED'
    assert result['structuredContent']['tool_error']=={
        'code':'TOOL_EXECUTION_FAILED',
        'message':'工具执行时发生内部错误，已记录诊断信息；请稍后重试。',
    }
    assert 'RuntimeError' in caplog.text
    assert 'private upstream response' not in caplog.text


def test_mcp_revocation_and_cancel_fence_cached_receipts(client,data,monkeypatch):
    _,c=start(client,monkeypatch);ids,factory=data
    args={'name':'query_purchase_requests','arguments':{},'_meta':{'agent/sequence':0}}
    assert rpc(client,c,'tools/call',args).json()['result']['isError'] is False
    with factory.begin() as db:
        db.scalar(select(Grant).where(Grant.user_id==ids['buyer'],Grant.permission=='purchase.read')).active=False
    assert rpc(client,c,'tools/list').status_code==403
    assert rpc(client,c,'tools/call',args).status_code==403
    client.post(f"/api/runs/{c['id']}/cancel")
    assert rpc(client,c,'tools/call',args).status_code==409
