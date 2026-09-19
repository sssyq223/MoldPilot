from datetime import timedelta
from types import SimpleNamespace

from sqlalchemy import select

from app.config import settings
from app.db import now
from app.models import Grant, Run, Step, User
from app.agent_resume import queue_after_proposal_decision
from agent_core.run_status import SCOPED_QUEUED
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


def test_new_run_is_only_claimed_by_its_creating_worker_scope(client, data, monkeypatch):
    _, factory = data
    monkeypatch.setattr(settings(), 'llm_enabled', True)
    monkeypatch.setattr(settings(), 'worker_scope', 'desktop-a')
    sign_in(client, 'test_buyer')

    created = client.post('/api/runs', json={'prompt': '解析本会话附件'}).json()
    assert created['status'] == 'QUEUED'
    with factory() as db:
        stored = db.get(Run, created['id'])
        assert stored.status == SCOPED_QUEUED
        assert stored.checkpoint['worker_scope'] == 'desktop-a'

    monkeypatch.setattr(settings(), 'worker_scope', 'desktop-b')
    assert client.post('/internal/runs/claim', headers=worker_headers()).json()['run'] is None

    monkeypatch.setattr(settings(), 'worker_scope', 'desktop-a')
    claimed = client.post('/internal/runs/claim', headers=worker_headers()).json()['run']
    assert claimed['id'] == created['id']
    assert claimed['worker_scope'] == 'desktop-a'


def test_worker_checkpoint_preserves_proposal_resume_history(client, data, monkeypatch):
    ids, factory = data
    monkeypatch.setattr(settings(), 'llm_enabled', True)
    sign_in(client, 'test_buyer')
    created = client.post('/api/runs', json={'prompt': '确认后继续说明'}).json()
    with factory.begin() as db:
        run = db.get(Run, created['id'])
        run.checkpoint = {
            **(run.checkpoint or {}),
            'proposal_decisions': {'proposal-step': 'approved'},
            'proposal_resolution': {
                'decision': 'approved',
                'proposal_step_id': 'proposal-step',
                'authoritative_receipt': {'status': 'executed'},
            },
            'prior_finals': [{
                'response_kind': 'AWAITING_APPROVAL',
                'summary': '确认前的业务结论与确认卡说明',
                'suggestions': [],
            }],
        }

    claimed = client.post('/internal/runs/claim', headers=worker_headers()).json()['run']
    assert claimed['id'] == created['id']
    response = client.post(
        f"/internal/runs/{created['id']}/checkpoint",
        headers=worker_headers(),
        json={'epoch': claimed['epoch'], 'checkpoint': {
            'messages': [{'role': 'assistant', 'content': '确认前的业务结论与确认卡说明'}],
            'turn': 3,
            'phase': 'MODEL_WAITING',
        }},
    )
    assert response.status_code == 200

    history = client.get(f"/api/conversations/{created['conversation_id']}/runs").json()[0]
    assert history['trace'][-1]['historical'] is True
    assert history['trace'][-1]['type'] == 'message'
    assert history['trace'][-1]['text'] == '确认前的业务结论与确认卡说明'
    assert sum(item.get('text') == '确认前的业务结论与确认卡说明'
               for item in history['trace']) == 1
    with factory() as db:
        checkpoint = db.get(Run, created['id']).checkpoint
        assert checkpoint['proposal_decisions'] == {'proposal-step': 'approved'}
        assert checkpoint['proposal_resolution']['decision'] == 'approved'
        assert checkpoint['prior_finals'][0]['response_kind'] == 'AWAITING_APPROVAL'


def test_confirmed_proposal_is_requeued_as_a_new_model_turn(monkeypatch):
    run = Run(
        id='resume-run', conversation_id='conversation', user_id='user-1', security_version=1,
        prompt='准备操作', status='SUCCEEDED',
        checkpoint={'messages': [{'role': 'user', 'content': '准备操作'}],
                    'completed_at': '2026-09-17T10:00:00+08:00', 'protocol_repairs': 2},
        result={'response_kind': 'AWAITING_APPROVAL', 'summary': '确认卡已准备，请确认。',
                'evidence_ids': ['proposal-step'], 'suggestions': ['请核对后确认']},
    )
    step = Step(id='proposal-step', run_id=run.id, sequence=0, tool='prepare_demo',
                request_hash='hash', result={})

    class FakeDb:
        def get(self, model, identity):
            if model is Step and identity == step.id:
                return step
            if model is Run and identity == run.id:
                return run
            return None

    monkeypatch.setattr('app.agent_resume.model_settings', lambda: SimpleNamespace(llm_enabled=True))
    receipt = {'status': 'executed', 'resource_id': 'resource-1'}

    assert queue_after_proposal_decision(FakeDb(), SimpleNamespace(id='user-1'),
                                         step.id, 'approved', receipt) is True
    assert run.status == SCOPED_QUEUED
    assert run.checkpoint['worker_scope'] == settings().worker_scope
    assert run.result is None
    assert 'completed_at' not in run.checkpoint
    assert run.checkpoint['protocol_repairs'] == 0
    assert run.checkpoint['next_model_instructions'] == []
    assert run.checkpoint['streaming_model_message'] is None
    assert run.checkpoint['proposal_resolution']['decision'] == 'approved'
    assert run.checkpoint['proposal_resolution']['authoritative_receipt'] == receipt
    assert run.checkpoint['prior_finals'][0]['response_kind'] == 'AWAITING_APPROVAL'
    assert [message['role'] for message in run.checkpoint['messages'][-5:]] == [
        'assistant', 'user', 'assistant', 'tool', 'system'
    ]
    assert '完成本人确认' in run.checkpoint['messages'][-4]['content']
    tool_receipt = run.checkpoint['messages'][-2]
    assert tool_receipt['tool_call_id'].startswith('proposal_resolution_')
    assert '"event": "proposal_resolved"' in tool_receipt['content']


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


def test_trusted_proposal_resolution_is_projected_as_confirmation_activity(client, data, monkeypatch):
    run, claimed = start(client, monkeypatch)
    messages = [
        {'role': 'assistant', 'content': None, 'tool_calls': [{
            'id': 'proposal-resolution-1', 'type': 'function',
            'function': {'name': 'ProposalResolution', 'arguments': '{"decision":"approved"}'},
        }]},
        {'role': 'tool', 'tool_call_id': 'proposal-resolution-1', 'content': (
            '{"source":"trusted_host","event":"proposal_resolved","decision":"approved",'
            '"proposal_step_id":"proposal-step","authoritative_receipt":{"status":"CONFIRMED"}}'
        )},
    ]
    assert client.post(f"/internal/runs/{run['id']}/checkpoint", headers=worker_headers(),
                       json={'epoch': claimed['epoch'], 'checkpoint': {'messages': messages, 'turn': 1}}).status_code == 200

    history = client.get(f"/api/conversations/{run['conversation_id']}/runs").json()
    activity = history[0]['trace'][0]
    assert activity['type'] == 'proposal_resolution'
    assert activity['decision'] == 'approved'
    assert activity['receipt']['status'] == 'CONFIRMED'


def test_tool_error_is_projected_as_recoverable_activity_not_interrupted_run(client, data, monkeypatch):
    run, claimed = start(client, monkeypatch)
    messages = [
        {'role': 'assistant', 'content': None, 'tool_calls': [{
            'id': 'prepare-1', 'type': 'function',
            'function': {'name': 'prepare_project_pause', 'arguments': '{}'},
        }]},
        {'role': 'tool', 'tool_call_id': 'prepare-1', 'content': '{"tool_error":{"code":"WORKFLOW_MISMATCH","message":"审批模板不可用，请重新查询流程选项"}}'},
    ]
    assert client.post(f"/internal/runs/{run['id']}/checkpoint", headers=worker_headers(),
                       json={'epoch': claimed['epoch'], 'checkpoint': {'messages': messages, 'turn': 1}}).status_code == 200

    trace = client.get(f"/api/conversations/{run['conversation_id']}/runs").json()[0]['trace']

    assert trace[0] == {'type': 'tool_error', 'tool': 'prepare_project_pause',
                        'code': 'WORKFLOW_MISMATCH', 'message': '审批模板不可用，请重新查询流程选项'}


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


def test_streaming_model_snapshot_is_visible_before_tool_execution_starts(client, data, monkeypatch):
    run, claimed = start(client, monkeypatch)
    checkpoint = {
        'messages': [],
        'turn': 1,
        'phase': 'MODEL_STREAMING',
        'streaming_model_message': {
            'role': 'assistant',
            'content': '我先读取项目计划，再核对关键节点。',
            'tool_calls': [{
                'id': 'stream-call-1', 'type': 'function',
                'function': {'name': 'query_project_plan_context',
                             'arguments': '{"project_code":"SMOKE-M001"}'},
            }],
        },
    }
    assert client.post(f"/internal/runs/{run['id']}/checkpoint", headers=worker_headers(),
                       json={'epoch': claimed['epoch'], 'checkpoint': checkpoint}).status_code == 200

    active = client.get(f"/api/conversations/{run['conversation_id']}/runs").json()[0]

    assert active['progress']['phase'] == 'MODEL_STREAMING'
    assert [item['type'] for item in active['trace']] == ['message', 'tool_pending']
    assert active['trace'][0]['streaming'] is True
    assert active['trace'][0]['message_key'] == 'assistant:0'
    assert active['trace'][1]['tool'] == 'query_project_plan_context'


def test_streaming_message_key_survives_trace_insertions_and_persistence(client, data, monkeypatch):
    run, claimed = start(client, monkeypatch)
    first_turn = [
        {'role': 'assistant', 'content': None, 'tool_calls': [{
            'id': 'search-1', 'type': 'function',
            'function': {'name': 'ToolSearch', 'arguments': '{"query":"项目计划"}'},
        }]},
        {'role': 'tool', 'tool_call_id': 'search-1',
         'content': '{"source":"harness","activated":["query_project_plan_context"]}'},
    ]
    streaming = {
        'role': 'assistant', 'content': '我继续读取项目计划。',
        'tool_calls': [{'id': 'query-1', 'type': 'function',
                        'function': {'name': 'query_project_plan_context', 'arguments': '{}'}}],
    }
    checkpoint = {'messages': first_turn, 'turn': 2, 'phase': 'MODEL_STREAMING',
                  'streaming_model_message': streaming}
    assert client.post(f"/internal/runs/{run['id']}/checkpoint", headers=worker_headers(),
                       json={'epoch': claimed['epoch'], 'checkpoint': checkpoint}).status_code == 200
    trace = client.get(f"/api/conversations/{run['conversation_id']}/runs").json()[0]['trace']
    live = next(item for item in trace if item['type'] == 'message')
    assert live['streaming'] is True
    assert live['message_key'] == 'assistant:1'

    persisted = first_turn + [streaming]
    checkpoint = {'messages': persisted, 'turn': 2, 'phase': 'TOOL_RUNNING'}
    assert client.post(f"/internal/runs/{run['id']}/checkpoint", headers=worker_headers(),
                       json={'epoch': claimed['epoch'], 'checkpoint': checkpoint}).status_code == 200
    trace = client.get(f"/api/conversations/{run['conversation_id']}/runs").json()[0]['trace']
    message = next(item for item in trace if item['type'] == 'message')
    assert message.get('streaming') is None
    assert message['message_key'] == 'assistant:1'


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
