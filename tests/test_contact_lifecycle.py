from sqlalchemy import select
from app import business, models as m
from domain_packs.mold.erp.change.contact_lifecycle import preview,CloseInput,ReviewInput,ReviewerInput
from app.errors import DomainError
from uuid import uuid4
import pytest
from conftest import sign_in
from test_contacts import create,add_task,grant,operation,response_body,task_body
from test_bpm_assignments import group
from test_contact_proposals import propose,intent,confirm
from test_agent_api import start,worker_headers
from test_domains import workflow,approve


def setup(client,data,monkeypatch):
    ids,factory=data;sign_in(client)
    grant(factory,ids,ids['buyer'],['read','respond'])
    g=group(client,[ids['buyer'],ids['admin']],kind='DEPARTMENT',name='联络责任部门',heads=[ids['admin']])
    case,_=create(client,ids);case=add_task(client,case,g);tid=case['tasks'][0]['id']
    case=client.post(f"/api/contacts/{case['id']}/tasks/{tid}/assign",json=operation(case,assignee_id=ids['buyer'],reason='指派执行')).json()
    definition=workflow(client,ids,'contact_resolution')
    _,ctx=start(client,monkeypatch,'admin')
    return case,tid,definition,ctx,g


def execute(client,ctx,case,action,sequence=0,**args):
    e=propose(client,ctx,action,{'case_id':case['id'],'revision':case['revision'],**args},sequence)
    i=intent(client,e);r=confirm(client,i)
    assert r.status_code==200,r.text
    assert confirm(client,i).json()==r.json()
    return client.get('/api/contacts/'+case['id']).json()


def plan(client,ctx,case,definition,seq=0):
    return execute(client,ctx,case,'resolution',seq,definition_id=definition,solution='核对尺寸，按确认的处理方案执行并复验',customer_due_affected=False,customer_evidence=None)


def feedback(client,case,tid):
    sign_in(client,'test_buyer')
    r=client.post(f"/api/contacts/{case['id']}/tasks/{tid}/respond",json=operation(case,**response_body()))
    assert r.status_code==200,r.text
    sign_in(client)
    return client.get('/api/contacts/'+case['id']).json()


def test_bpm_feedback_rework_verification_and_manual_close(client,data,monkeypatch):
    case,tid,definition,ctx,_=setup(client,data,monkeypatch)
    case=plan(client,ctx,case,definition)
    iid=case['resolutions'][0]['instance_id']
    assert case['resolutions'][0]['status']=='SUBMITTED'
    detail=client.get('/api/approvals/'+iid).json()
    assert detail['snapshot']['detail']['material_snapshot']['tasks'][0]['id']==tid
    assert approve(client,iid)['business_status']=='EFFECTIVE'
    case=client.get('/api/contacts/'+case['id']).json()
    case=feedback(client,case,tid)
    with data[1]() as db:
        with pytest.raises(DomainError,match='复验合格'):
            preview(db,db.get(m.User,data[0]['admin']),db.get(m.ContactCase,case['id']),'','close',CloseInput(**operation(case,evidence='不准提前关闭')))
    case=execute(client,ctx,case,'review',1,task_id=tid,decision='REWORK',evidence='补充尺寸记录')
    assert case['tasks'][0]['status']=='ASSIGNED'
    case=feedback(client,case,tid)
    case=execute(client,ctx,case,'review',2,task_id=tid,decision='PASS',evidence='已独立核对记录及实物，尺寸符合要求')
    assert case['tasks'][0]['status']=='VERIFIED' and case['collaboration_status']=='OPEN'
    case=execute(client,ctx,case,'close',3,evidence='方案批准、措施完成、独立复验合格，确认关闭')
    assert case['collaboration_status']=='CLOSED' and case['closed_by_name']=='测试管理员'
    assert client.post(f"/api/contacts/{case['id']}/tasks",json=operation(case,department_id=case['tasks'][0]['department_id'],**task_body(title='关闭后不可新增'))).status_code==409
    assert len([r for r in case['records'] if r['kind']=='CLOSED'])==1


def test_contact_resolution_proposal_carries_run_auto_mode_to_bpm_submission(client,data,monkeypatch):
    case,tid,definition,ctx,_=setup(client,data,monkeypatch)
    ids,factory=data
    with factory.begin() as db:
        run=db.get(m.Run,ctx['id'])
        run.checkpoint={**run.checkpoint,'agent_permission_mode':'delegated_auto'}
    captured={}
    original=business.submit_subject
    def capture_mode(db,user,subject_id,revision,definition_id,material_review_id=None,agent_permission_mode="ask"):
        captured['mode']=agent_permission_mode
        return original(db,user,subject_id,revision,definition_id,material_review_id,agent_permission_mode)
    monkeypatch.setattr(business,'submit_subject',capture_mode)
    evidence=propose(client,ctx,'resolution',{'case_id':case['id'],'revision':case['revision'],
        'definition_id':definition,'solution':'按授权自动审批模式提交方案',
        'customer_due_affected':False,'customer_evidence':None})
    policy=evidence['proposal']['confirmation_policy']
    assert policy['agent_permission_mode']=='delegated_auto'
    assert policy['status']=='CONFIRM_THEN_DELEGATED_APPROVAL_ALLOWED'
    prepared=intent(client,evidence)
    assert prepared['confirmation_policy']['status']=='CONFIRM_THEN_DELEGATED_APPROVAL_ALLOWED'
    assert confirm(client,prepared).status_code==200
    assert captured['mode']=='delegated_auto'


def test_changed_material_removes_approve_and_blocks_close(client,data,monkeypatch):
    case,tid,definition,ctx,g=setup(client,data,monkeypatch);case=plan(client,ctx,case,definition)
    iid=case['resolutions'][0]['instance_id'];case=add_task(client,case,g)
    d=client.get('/api/approvals/'+iid).json();assert 'APPROVE' not in d['allowed_actions']
    assert approve(client,iid,'REJECT')['status']=='REJECTED'
    case=client.get('/api/contacts/'+case['id']).json();case=plan(client,ctx,case,definition,1)
    assert len(case['resolutions'])==2 and case['resolutions'][0]['status']=='SUBMITTED'


def test_no_plan_no_close_and_history_no_dispatch(client,data,monkeypatch):
    case,tid,definition,ctx,_=setup(client,data,monkeypatch)
    for action,args in [('close',{'evidence':'没有审批不能关'}),('review',{'task_id':tid,'decision':'PASS','evidence':'不能自称已审批'})]:
        r=client.post(f"/internal/runs/{ctx['id']}/tools",headers=worker_headers(),json={'epoch':ctx['epoch'],'sequence':10,'key':'prepare_contact_'+action,'arguments':{'case_id':case['id'],'revision':case['revision'],**args}})
        assert r.status_code==409 and r.json()['error']['code']=='RESOLUTION_REQUIRED'
    history,_=create(client,data[0],mode='HISTORY')
    r=client.post(f"/internal/runs/{ctx['id']}/tools",headers=worker_headers(),json={'epoch':ctx['epoch'],'sequence':11,'key':'prepare_contact_close','arguments':{'case_id':history['id'],'revision':1,'evidence':'不应关闭线上流程'}})
    assert r.status_code==409 and r.json()['error']['code']=='HISTORY_NO_DISPATCH'


def test_designated_reviewer_requires_admin_and_current_grants(client,data,monkeypatch):
    case,tid,definition,ctx,_=setup(client,data,monkeypatch)
    ids,factory=data;grant(factory,ids,ids['reviewer'],['read','review','close'])
    case=execute(client,ctx,case,'set_reviewer',reviewer_id=ids['reviewer'],evidence='管理员指定验收负责人')
    assert case['reviewer_name']=='测试采购主管'
    with factory() as db:
        row=db.get(m.ContactCase,case['id']);admin=db.get(m.User,ids['admin']);buyer=db.get(m.User,ids['buyer'])
        with pytest.raises(DomainError):preview(db,buyer,row,'','set_reviewer',ReviewerInput(**operation(case,reviewer_id=ids['reviewer'],evidence='不是管理员')))
    with factory.begin() as db:
        db.scalar(select(m.Grant).where(m.Grant.user_id==ids['reviewer'],m.Grant.permission=='contact.close')).active=False
    with factory() as db:
        from domain_packs.mold.erp.change.contact_lifecycle import reviewer
        with pytest.raises(DomainError):reviewer(db,db.get(m.User,ids['reviewer']),db.get(m.ContactCase,case['id']),'close')


def test_cancel_keeps_history_and_blocks_assignment(client,data,monkeypatch):
    case,tid,definition,ctx,_=setup(client,data,monkeypatch)
    case=execute(client,ctx,case,'cancel_task',task_id=tid,evidence='重复事项，保留撤销记录')
    assert case['tasks'][0]['status']=='CANCELLED'
    assert client.post(f"/api/contacts/{case['id']}/tasks/{tid}/assign",json=operation(case,assignee_id=data[0]['buyer'],reason='不得重开')).status_code==409
    assert case['records'][-1]['kind']=='TASK_CANCELLED'


def test_independent_recheck_and_confirmation_revalidate(client,data,monkeypatch):
    case,tid,definition,ctx,_=setup(client,data,monkeypatch)
    case=plan(client,ctx,case,definition);approve(client,case['resolutions'][0]['instance_id']);case=client.get('/api/contacts/'+case['id']).json()
    case=feedback(client,case,tid)
    with data[1].begin() as db:
        buyer=db.get(m.User,data[0]['buyer']);buyer.super_admin=True
        row=db.get(m.ContactCase,case['id']);row.reviewer_id=buyer.id
    with data[1]() as db:
        with pytest.raises(DomainError,match='不能复验自己'):
            preview(db,db.get(m.User,data[0]['buyer']),db.get(m.ContactCase,case['id']),tid,'review',ReviewInput(**operation(case,decision='PASS',evidence='自检不能替代复验')))
    e=propose(client,ctx,'review',{'case_id':case['id'],'revision':case['revision'],'task_id':tid,'decision':'PASS','evidence':'待本人确认'},1)
    i=intent(client,e)
    r=client.post(f"/api/contacts/{case['id']}/records",json=operation(case,source='OWN',occurred_at='2026-09-10T10:00:00+08:00',content='确认期间更新资料'))
    assert r.status_code==200
    assert confirm(client,i).status_code==409
    assert client.get('/api/contacts/'+case['id']).json()['tasks'][0]['status']=='RESPONDED'


def test_plan_permission_cannot_bypass_contact_scope_and_records_hide_solution(client,data,monkeypatch):
    case,tid,definition,ctx,_=setup(client,data,monkeypatch)
    case=plan(client,ctx,case,definition)
    sign_in(client,'test_buyer')
    view=client.get('/api/contacts/'+case['id']).json()
    assert view['resolutions']==[]
    assert '处理方案' not in view['records'][-1]['detail']
    assert client.get('/api/approvals/'+case['resolutions'][0]['instance_id']).status_code==403


def test_designated_reviewer_can_close_with_required_permissions(client,data,monkeypatch):
    case,tid,definition,ctx,_=setup(client,data,monkeypatch);ids,factory=data
    grant(factory,ids,ids['reviewer'],['read','review','close'])
    case=execute(client,ctx,case,'set_reviewer',0,reviewer_id=ids['reviewer'],evidence='指定验收责任')
    case=plan(client,ctx,case,definition,1);approve(client,case['resolutions'][0]['instance_id']);case=client.get('/api/contacts/'+case['id']).json()
    case=feedback(client,case,tid)
    case=execute(client,ctx,case,'review',2,task_id=tid,decision='PASS',evidence='独立验收合格')
    with factory.begin() as db:
        db.add(m.Grant(user_id=ids['reviewer'],permission='contact_resolution.read',effect='ALLOW',scope={'all':True},fields=['*'],reason='验收材料权限',granted_by=ids['admin']))
        db.add(m.Capability(user_id=ids['reviewer'],kind='TOOL',key='prepare_contact_close',enabled=True))
        db.get(m.Run,ctx['id']).status='SUCCEEDED'
    _,ctx2=start(client,monkeypatch,'test_reviewer')
    case=execute(client,ctx2,case,'close',evidence='指定验收负责人核对并关闭')
    assert case['closed_by_name']=='测试采购主管'
