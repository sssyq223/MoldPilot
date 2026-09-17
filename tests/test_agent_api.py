from datetime import timedelta

from sqlalchemy import select

from app.config import settings
from app.db import now
from app.models import Grant, Step, User
from conftest import sign_in


def worker_headers(): return {'Authorization': 'Bearer '+settings().worker_secret}


def start(client, monkeypatch, username='test_buyer'):
    monkeypatch.setattr(settings(), 'llm_enabled', True)
    sign_in(client, username)
    run = client.post('/api/runs', json={'prompt': '查询我的采购申请'}).json()
    claimed = client.post('/internal/runs/claim', headers=worker_headers()).json()['run']
    assert claimed['id'] == run['id']
    return run, claimed


def execute(client, context):
    return client.post(f"/internal/runs/{context['id']}/tools", headers=worker_headers(),
                       json={'epoch': context['epoch'], 'sequence': 0, 'key': 'query_purchase_requests', 'arguments': {}})


def test_run_persists_agent_permission_mode_for_worker_context(client, data, monkeypatch):
    monkeypatch.setattr(settings(), 'llm_enabled', True)
    sign_in(client, 'test_buyer')
    run = client.post('/api/runs', json={
        'prompt': '查询我的采购申请',
        'agent_permission_mode': 'delegated_auto'
    }).json()
    assert run['agent_permission_mode'] == 'delegated_auto'
    history = client.get(f"/api/conversations/{run['conversation_id']}/runs").json()
    assert history[0]['agent_permission_mode'] == 'delegated_auto'
    assert history[0]['progress'] is not None
    claimed = client.post('/internal/runs/claim', headers=worker_headers()).json()['run']
    assert claimed['id'] == run['id']
    assert claimed['agent_permission_mode'] == 'delegated_auto'
    assert client.post(f"/internal/runs/{run['id']}/checkpoint", headers=worker_headers(),
                       json={'epoch': claimed['epoch'], 'checkpoint': {'turn': 1}}).status_code == 200
    history = client.get(f"/api/conversations/{run['conversation_id']}/runs").json()
    assert history[0]['agent_permission_mode'] == 'delegated_auto'


def test_failed_run_marks_unfinished_tool_call_as_interrupted(client, data, monkeypatch):
    run, claimed = start(client, monkeypatch)
    checkpoint = {
        'messages': [{'role': 'assistant', 'content': None, 'tool_calls': [{
            'id': 'tool-search-1', 'type': 'function',
            'function': {'name': 'ToolSearch', 'arguments': '{"query":"项目计划"}'},
        }]}],
        'turn': 1, 'phase': 'TOOL_RUNNING',
    }
    assert client.post(f"/internal/runs/{run['id']}/checkpoint", headers=worker_headers(),
                       json={'epoch': claimed['epoch'], 'checkpoint': checkpoint}).status_code == 200
    assert client.post(f"/internal/runs/{run['id']}/fail", headers=worker_headers(),
                       json={'epoch': claimed['epoch'], 'code': 'MODEL_OUTPUT_INVALID'}).status_code == 200
    history = client.get(f"/api/conversations/{run['conversation_id']}/runs").json()
    failed = history[0]
    assert failed['status'] == 'FAILED'
    assert failed['trace'][0]['type'] == 'tool_interrupted'
    assert failed['trace'][0]['tool'] == 'ToolSearch'
    assert failed['trace'][-1]['error_code'] == 'MODEL_OUTPUT_INVALID'


def test_tool_search_result_is_projected_as_harness_activity(client, data, monkeypatch):
    run, claimed = start(client, monkeypatch)
    messages = [
        {'role': 'assistant', 'content': None, 'tool_calls': [{
            'id': 'tool-search-1', 'type': 'function',
            'function': {'name': 'ToolSearch', 'arguments': '{"query":"项目计划"}'},
        }]},
        {'role': 'tool', 'tool_call_id': 'tool-search-1', 'content': '{"source":"harness","as_of":"2026-09-16T16:46:45+0800","query":"项目计划","matches":["project_plan_context_review"],"activated":["query_project_plan_context"],"message":"已激活按需工具"}'},
    ]
    assert client.post(f"/internal/runs/{run['id']}/checkpoint", headers=worker_headers(),
                       json={'epoch': claimed['epoch'], 'checkpoint': {'messages': messages, 'turn': 1}}).status_code == 200
    history = client.get(f"/api/conversations/{run['conversation_id']}/runs").json()
    activity = history[0]['trace'][0]
    assert activity['type'] == 'tool_search'
    assert activity['activated'] == ['query_project_plan_context']
    assert activity['as_of'] == '2026-09-16T16:46:45+0800'


def test_visible_model_progress_and_tool_activity_keep_provider_order(client, data, monkeypatch):
    run, claimed = start(client, monkeypatch)
    messages = [
        {'role': 'assistant', 'content': '我先读取项目计划，再核对关键节点。', 'tool_calls': [{
            'id': 'tool-search-progress', 'type': 'function',
            'function': {'name': 'ToolSearch', 'arguments': '{"query":"项目计划"}'},
        }]},
        {'role': 'tool', 'tool_call_id': 'tool-search-progress', 'content': '{"source":"harness","as_of":"2026-09-16T16:46:45+0800","query":"项目计划","matches":["project_plan_context_review"],"activated":["query_project_plan_context"],"message":"已激活按需工具"}'},
    ]
    assert client.post(f"/internal/runs/{run['id']}/checkpoint", headers=worker_headers(),
                       json={'epoch': claimed['epoch'], 'checkpoint': {'messages': messages, 'turn': 1}}).status_code == 200
    trace = client.get(f"/api/conversations/{run['conversation_id']}/runs").json()[0]['trace']

    assert [item['type'] for item in trace[:2]] == ['message', 'tool_search']
    assert trace[0]['text'] == '我先读取项目计划，再核对关键节点。'


def test_tool_assignment_cannot_grant_business_data_access(client, data):
    ids, _ = data; sign_in(client)
    department = client.post('/api/organization/groups', json={
        'kind': 'DEPARTMENT', 'name': '无业务权限部门', 'members': [], 'active': True, 'reason': '测试创建用户需选择有效部门'
    }).json()
    assert department['name'] == '无业务权限部门'
    user = client.post('/api/users', json={'username':'empty_user','display_name':'无业务权限测试',
        'department':'无业务权限部门','password':'SyntheticPassword-2026!'}).json()
    r = client.post(f"/api/users/{user['id']}/capabilities", json={'kind':'TOOL','key':'query_projects','enabled':True,'reason':'测试能力与数据权限取交集','expected_security_version':user['security_version']})
    assert r.status_code == 200
    client.post('/api/auth/logout')
    login = client.post('/api/auth/login', json={'username':'empty_user','password':'SyntheticPassword-2026!'}).json()
    client.headers['X-CSRF-Token'] = login['csrf']
    assert client.get('/api/capabilities').json()['tools'] == []
    assert client.get('/api/projects').json() == []


def test_capabilities_include_backend_catalog_metadata(client, data):
    sign_in(client, 'test_buyer')
    payload = client.get('/api/capabilities').json()
    purchase_tool = next(item for item in payload['tools'] if item['key'] == 'query_purchase_requests')
    assert purchase_tool['name'] == '查询采购申请'
    assert purchase_tool['department'] == 'purchase'
    assert purchase_tool['department_name'] == '采购部门'
    assert purchase_tool['type'] == 'query'
    assert purchase_tool['type_name'] == '查询'
    assert purchase_tool['mode'] == 'read_only'
    assert purchase_tool['business_key'] == 'purchase'
    assert purchase_tool['dependencies'] == []


def test_ordinary_user_cannot_change_tools_or_skills(client,data):
    ids,_=data;sign_in(client,'test_buyer')
    assert client.get(f"/api/users/{ids['buyer']}/capabilities").status_code == 403
    assert client.post(f"/api/users/{ids['buyer']}/capabilities",json={'kind':'TOOL','key':'query_projects','enabled':True,'reason':'self grant','expected_security_version':1}).status_code == 403


def test_tool_revocation_stops_running_task_and_disables_dependent_skill(client,data,monkeypatch):
    ids,_=data;run,context=start(client,monkeypatch)
    assert execute(client,context).status_code == 200
    sign_in(client)
    target=next(u for u in client.get('/api/users').json() if u['id']==ids['buyer'])
    r=client.post(f"/api/users/{ids['buyer']}/capabilities",json={'kind':'TOOL','key':'query_purchase_requests','enabled':False,'reason':'测试立即撤权','expected_security_version':target['security_version']})
    assert r.status_code==200
    assert execute(client,context).status_code==403
    sign_in(client,'test_buyer')
    assert client.get('/api/capabilities').json()=={'tools':[],'skills':[]}
    history=client.get(f"/api/conversations/{run['conversation_id']}/runs").json()
    assert 'evidence' not in history[0]['result']
    assert history[0]['progress'] is None


def test_expired_grant_fences_cached_result_without_version_change(client,data,monkeypatch):
    ids,factory=data;run,context=start(client,monkeypatch)
    assert execute(client,context).status_code==200
    with factory.begin() as db:
        g=db.scalar(select(Grant).where(Grant.user_id==ids['buyer'],Grant.permission=='purchase.read'))
        g.valid_to=now()-timedelta(seconds=1)
    assert execute(client,context).status_code==403
    history=client.get(f"/api/conversations/{run['conversation_id']}/runs").json()
    assert history[0]['progress'] is None


def test_duplicate_tool_step_has_one_persisted_receipt(client,data,monkeypatch):
    _,factory=data;_,context=start(client,monkeypatch)
    first=execute(client,context);second=execute(client,context)
    assert first.status_code==second.status_code==200
    assert first.json()==second.json()
    with factory() as db: assert len(list(db.scalars(select(Step))))==1


def test_cancel_blocks_worker_finish(client,data,monkeypatch):
    run,context=start(client,monkeypatch)
    assert client.post(f"/api/runs/{run['id']}/cancel").status_code==200
    assert execute(client,context).status_code==409
    assert client.post(f"/internal/runs/{run['id']}/finish",headers=worker_headers(),json={'epoch':context['epoch'],'result':{'summary':'late','evidence_ids':[]}}).status_code==409


def test_project_fields_filtered_for_ui_and_agent(client,data):
    ids,factory=data;sign_in(client,'test_buyer')
    with factory.begin() as db:
        g=db.scalar(select(Grant).where(Grant.user_id==ids['buyer'],Grant.permission=='project.read'))
        g.fields=['code']
    assert client.get('/api/projects').json()==[{'code':'TEST-M001'}]
    from app.tool_gateway import execute as tool_execute
    from app.models import Capability
    with factory.begin() as db:
        db.add(Capability(user_id=ids['buyer'],kind='TOOL',key='query_projects'))
    with factory() as db:
        result=tool_execute(db,db.get(User,ids['buyer']),'query_projects',{})
        assert result['data']==[{'code':'TEST-M001'}]


def test_followup_context_keeps_only_own_requests_in_same_conversation(data):
    from app.models import Conversation,Run
    from app.internal import recent_requests
    ids,factory=data
    with factory.begin() as db:
        user=db.get(User,ids['admin'])
        conv=Conversation(user_id=user.id,title='联络续问');other=Conversation(user_id=user.id,title='另一会话')
        db.add_all([conv,other]);db.flush()
        base=now()-timedelta(minutes=20)
        for index in range(6):
            db.add(Run(conversation_id=conv.id,user_id=user.id,security_version=user.security_version,
                prompt='本人请求'+str(index),created_at=base+timedelta(seconds=index),result={'summary':'旧业务事实不得复用'}))
        db.add(Run(conversation_id=other.id,user_id=user.id,security_version=user.security_version,prompt='其他会话不可混入',created_at=base))
        db.add(Run(conversation_id=conv.id,user_id=ids['buyer'],security_version=1,prompt='其他用户不可混入',created_at=base))
        current=Run(conversation_id=conv.id,user_id=user.id,security_version=user.security_version,prompt='修正上一份方案')
        db.add(current);db.flush()
        assert recent_requests(db,user,current)==['本人请求2','本人请求3','本人请求4','本人请求5']
        db.add(Run(conversation_id=conv.id,user_id=user.id,security_version=user.security_version,prompt='x'*6001,created_at=now()-timedelta(seconds=1)))
        db.flush();assert recent_requests(db,user,current)==[]
