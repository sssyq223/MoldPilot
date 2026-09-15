from uuid import uuid4
from sqlalchemy import select,func,update
from app.models import Grant,User,ContactCase,ContactTask,ContactRecord,ApprovalInstance,AssignmentMember
from conftest import sign_in
from test_bpm_assignments import group


def create(client,ids,mode='ONLINE',category='hardware',**overrides):
    payload={'request_key':str(uuid4()),'project_id':ids['project'],'category':category,
        'title':'模具装配问题协作（合成）','description':'核对尺寸与处理意见','mode':mode,**overrides}
    r=client.post('/api/contacts',json=payload)
    assert r.status_code==200,r.text
    return r.json(),payload


def grant(factory,ids,uid,actions,scope=None):
    with factory.begin() as db:
        for action in actions:
            db.add(Grant(user_id=uid,permission='contact.'+action,effect='ALLOW',scope=scope or {'project_id':[ids['project']],'category':['hardware']},
                fields=['*'],reason='合成联络单验证',granted_by=ids['admin']))


def operation(case,**body):return {'request_key':str(uuid4()),'revision':case['revision'],**body}


def add_task(client,c,g):
    r=client.post(f"/api/contacts/{c['id']}/tasks",json=operation(c,department_id=g['id'],title='核对图纸'))
    assert r.status_code==200,r.text
    return r.json()


def test_create_retry_and_history_never_dispatches(client,data):
    ids,factory=data;sign_in(client);c,p=create(client,ids,'HISTORY')
    assert client.post('/api/contacts',json=p).json()['id']==c['id']
    assert client.post('/api/contacts',json={**p,'title':'不同内容'}).status_code==409
    g=group(client,[ids['admin']],kind='DEPARTMENT',name='设计',heads=[ids['admin']])
    r=client.post(f"/api/contacts/{c['id']}/tasks",json=operation(c,department_id=g['id'],title='不允许派单'))
    assert r.status_code==409 and r.json()['error']['code']=='HISTORY_NO_DISPATCH'
    note=operation(c,source='OFFLINE',occurred_at='2026-09-10T10:00:00+08:00',participants='纸面记载：设计和采购',content='线下讨论记录，未代表在线审批')
    r=client.post(f"/api/contacts/{c['id']}/records",json=note);assert r.status_code==200,r.text
    assert r.json()['records'][0]['detail']['is_approval'] is False
    assert client.post(f"/api/contacts/{c['id']}/records",json=note).json()['revision']==2
    with factory() as db:
        assert db.scalar(select(func.count()).select_from(ContactTask))==0
        assert db.scalar(select(func.count()).select_from(ApprovalInstance))==0
        assert db.scalar(select(func.count()).select_from(ContactRecord))==1


def test_initiator_and_department_head_assignment_then_response(client,data):
    ids,factory=data;sign_in(client)
    g=group(client,[ids['buyer'],ids['reviewer']],kind='DEPARTMENT',name='责任部门',heads=[ids['reviewer']])
    grant(factory,ids,ids['reviewer'],['read','assign']);grant(factory,ids,ids['buyer'],['read','respond'])
    c,_=create(client,ids);c=add_task(client,c,g);tid=c['tasks'][0]['id']
    sign_in(client,'test_reviewer')
    choices=client.get(f"/api/contacts/{c['id']}/tasks/{tid}/candidates").json()
    assert choices==[{'id':ids['buyer'],'name':'测试五金采购员'}]
    body=operation(c,assignee_id=ids['buyer'],reason='负责人分派合成任务')
    r=client.post(f"/api/contacts/{c['id']}/tasks/{tid}/assign",json=body);assert r.status_code==200,r.text
    c=r.json();assert c['tasks'][0]['status']=='ASSIGNED'
    assert client.post(f"/api/contacts/{c['id']}/tasks/{tid}/assign",json=body).json()['revision']==c['revision']
    sign_in(client,'test_buyer');body=operation(c,content='已核对，建议进一步人工评审')
    r=client.post(f"/api/contacts/{c['id']}/tasks/{tid}/respond",json=body);assert r.status_code==200,r.text
    c=r.json();assert c['tasks'][0]['status']=='RESPONDED' and c['collaboration_status']=='OPEN'
    assert client.post(f"/api/contacts/{c['id']}/tasks/{tid}/respond",json=body).json()['revision']==c['revision']
    assert client.post(f"/api/contacts/{c['id']}/tasks/{tid}/respond",json=operation(c,content='覆盖')).status_code==409
    with factory() as db:assert db.scalar(select(func.count()).select_from(ApprovalInstance))==0


def test_scope_membership_and_headship_do_not_grant_access(client,data):
    ids,factory=data;sign_in(client)
    g=group(client,[ids['buyer']],kind='DEPARTMENT',name='采购部门',heads=[ids['buyer']])
    c,_=create(client,ids);c=add_task(client,c,g)
    sign_in(client,'test_buyer');assert client.get('/api/contacts').json()==[]
    assert client.get('/api/contacts/'+c['id']).status_code==403
    grant(factory,ids,ids['buyer'],['read','assign','respond'])
    assert len(client.get('/api/contacts').json())==1
    sign_in(client);other,_=create(client,ids,category='raw_material')
    sign_in(client,'test_buyer');assert client.get('/api/contacts/'+other['id']).status_code==403
    assert len(client.get('/api/contacts').json())==1


def test_ordinary_member_cannot_assign_even_with_action_grant(client,data):
    ids,factory=data;sign_in(client)
    g=group(client,[ids['buyer']],kind='DEPARTMENT',name='责任部门')
    c,_=create(client,ids);c=add_task(client,c,g);tid=c['tasks'][0]['id']
    grant(factory,ids,ids['buyer'],['read','assign','respond'])
    sign_in(client,'test_buyer')
    assert client.get(f"/api/contacts/{c['id']}/tasks/{tid}/candidates").status_code==403
    assert client.post(f"/api/contacts/{c['id']}/tasks/{tid}/assign",json=operation(c,assignee_id=ids['buyer'],reason='越权')).status_code==403
    sign_in(client)
    r=client.post(f"/api/contacts/{c['id']}/tasks/{tid}/assign",json=operation(c,assignee_id=ids['buyer'],reason='发起人获权直接指定'))
    assert r.status_code==200,r.text


def test_revoke_and_membership_change_block_processing(client,data):
    ids,factory=data;sign_in(client);grant(factory,ids,ids['buyer'],['read','respond'])
    g=group(client,[ids['buyer']],kind='DEPARTMENT',name='设计')
    c,_=create(client,ids);c=add_task(client,c,g);tid=c['tasks'][0]['id']
    c=client.post(f"/api/contacts/{c['id']}/tasks/{tid}/assign",json=operation(c,assignee_id=ids['buyer'],reason='指定')).json()
    with factory.begin() as db:db.delete(db.get(AssignmentMember,(g['id'],ids['buyer'])))
    sign_in(client,'test_buyer')
    assert client.post(f"/api/contacts/{c['id']}/tasks/{tid}/respond",json=operation(c,content='成员已移除')).status_code==403
    with factory.begin() as db:db.execute(update(Grant).where(Grant.user_id==ids['buyer'],Grant.permission=='contact.read').values(active=False))
    assert client.get('/api/contacts/'+c['id']).status_code==403


def test_version_replay_conflicts_no_duplicate_tasks(client,data):
    ids,_=data;sign_in(client);g=group(client,[],kind='DEPARTMENT',name='无负责人部门')
    c,_=create(client,ids);p=operation(c,department_id=g['id'],title='待分派任务')
    url=f"/api/contacts/{c['id']}/tasks"
    r=client.post(url,json=p);assert r.status_code==200,r.text
    assert r.json()['tasks'][0]['status']=='UNASSIGNED'
    assert len(client.post(url,json=p).json()['tasks'])==1
    assert client.post(url,json={**p,'request_key':str(uuid4())}).status_code==409
    assert client.post(url,json={**p,'title':'重用请求号不同内容'}).status_code==409


def test_note_times_and_inputs_are_not_approval_state(client,data):
    ids,_=data;sign_in(client);c,_=create(client,ids)
    url=f"/api/contacts/{c['id']}/records"
    p=operation(c,source='OFFLINE',occurred_at='2026-09-10T10:00:00+08:00',participants='负责人',content='线下同意')
    assert client.post(url,json={**p,'occurred_at':'2026-09-10T10:00:00'}).status_code==422
    assert client.post(url,json={**p,'occurred_at':'2099-01-01T00:00:00+08:00'}).status_code==400
    assert client.post(url,json={**p,'participants':''}).status_code==400
    assert client.post(url,json={**p,'is_approval':True}).status_code==422
    r=client.post(url,json=p);assert r.status_code==200,r.text
    assert r.json()['collaboration_status']=='OPEN'


def test_target_without_domain_permission_cannot_be_assigned(client,data):
    ids,factory=data;sign_in(client);g=group(client,[ids['buyer']],kind='DEPARTMENT',name='设计')
    c,_=create(client,ids);c=add_task(client,c,g);tid=c['tasks'][0]['id']
    assert client.get(f"/api/contacts/{c['id']}/tasks/{tid}/candidates").json()==[]
    assert client.post(f"/api/contacts/{c['id']}/tasks/{tid}/assign",json=operation(c,assignee_id=ids['buyer'],reason='没有授权')).status_code==403
    with factory() as db:assert db.get(ContactTask,tid).status=='UNASSIGNED'


def test_agent_contact_tool_enforces_scope_and_returns_no_approval(client,data,monkeypatch):
    from app.config import settings
    from app.models import Capability
    from test_agent_api import worker_headers
    ids,factory=data;sign_in(client)
    c,_=create(client,ids);hidden,_=create(client,ids,category='raw_material')
    grant(factory,ids,ids['buyer'],['read'])
    with factory.begin() as db:
        db.add(Capability(user_id=ids['buyer'],kind='TOOL',key='query_contact_cases'))
        db.add(Capability(user_id=ids['buyer'],kind='SKILL',key='contact_collaboration_review'))
    monkeypatch.setattr(settings(),'llm_enabled',True);sign_in(client,'test_buyer')
    run=client.post('/api/runs',json={'prompt':'帮我看看工程联络单目前由谁处理'}).json()
    context=client.post('/internal/runs/claim',headers=worker_headers()).json()['run']
    assert context['id']==run['id']
    r=client.post(f"/internal/runs/{run['id']}/tools",headers=worker_headers(),json={
        'epoch':context['epoch'],'sequence':0,'key':'query_contact_cases','arguments':{}})
    assert r.status_code==200,r.text
    assert c['id'] in r.text and hidden['id'] not in r.text
    assert 'HISTORY_RECORD' not in r.text and 'OPEN' in r.text
    with factory.begin() as db:
        db.execute(update(Grant).where(Grant.user_id==ids['buyer'],Grant.permission=='contact.read').values(active=False))
    replay=client.post(f"/internal/runs/{run['id']}/tools",headers=worker_headers(),json={
        'epoch':context['epoch'],'sequence':1,'key':'query_contact_cases','arguments':{}})
    assert replay.status_code==403


def test_contact_notification_delivery_rechecks_permissions(client,data):
    from app.models import Outbox,Notification
    from app.message_worker import deliver
    ids,factory=data;sign_in(client);grant(factory,ids,ids['buyer'],['read','respond'])
    g=group(client,[ids['buyer']],kind='DEPARTMENT',name='协作')
    c,_=create(client,ids);c=add_task(client,c,g);tid=c['tasks'][0]['id']
    r=client.post(f"/api/contacts/{c['id']}/tasks/{tid}/assign",json=operation(c,assignee_id=ids['buyer'],reason='通知验证'));assert r.status_code==200
    with factory() as db:eid=db.scalar(select(Outbox.id).where(Outbox.kind=='contact.assigned',Outbox.resource_id==c['id']))
    assert deliver(factory,eid)=='DELIVERED';assert deliver(factory,eid)=='DUPLICATE'
    sign_in(client,'test_buyer');items=client.get('/api/notifications').json()
    assert len(items)==1 and items[0]['kind']=='contact.assigned' and items[0]['resource_id']==c['id']
    with factory.begin() as db:
        assert db.scalar(select(func.count()).select_from(Notification).where(Notification.event_id==eid))==1
        db.execute(update(Grant).where(Grant.user_id==ids['buyer'],Grant.permission=='contact.read').values(active=False))
    assert client.get('/api/notifications').json()==[]
