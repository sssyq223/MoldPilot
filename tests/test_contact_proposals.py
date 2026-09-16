from uuid import uuid4
import pytest
from sqlalchemy import select,func
from app.models import ContactCase,ContactRecord,HumanIntent,User,Step,ApprovalInstance
from app import business
from test_agent_api import start,worker_headers
from test_contacts import create,operation,add_task,grant
from test_bpm_assignments import group
from conftest import sign_in


def propose(client,ctx,action,args,sequence=0):
    r=client.post(f"/internal/runs/{ctx['id']}/tools",headers=worker_headers(),json={
        'epoch':ctx['epoch'],'sequence':sequence,'key':'prepare_contact_'+action,'arguments':args})
    assert r.status_code==200,r.text
    return r.json()


def intent(client,evidence):
    r=client.post('/api/contact-proposals/'+evidence['evidence_id']+'/intent')
    assert r.status_code==200,r.text
    return r.json()


def confirm(client,i):return client.post('/api/human-actions/'+i['id']+'/confirm',json={'challenge':i['challenge']})


def create_args(ids):return {'project_id':ids['project'],'category':'hardware','mode':'ONLINE','title':'会话发起合成验证','description':'本人核对后记录',
    'customer_ref':'ERP-CUSTOMER-001','customer_name':'合成客户','mold_number':'MOLD-T001','product_ref':'PART-T001',
    'application_date':'2026-09-10','problem_source':'QUALITY_ISSUE','current_stage':'质量检验阶段','change_type':'EXCEPTION','urgency':'URGENT'}


def test_proposal_no_business_write_confirmation_and_retry(client,data,monkeypatch):
    ids,factory=data;_,ctx=start(client,monkeypatch,'admin')
    args={**create_args(ids),'category':'五金'};e=propose(client,ctx,'create',args)
    assert e['proposal']['input']['category']=='hardware'
    assert e['proposal']['confirmation_policy']['status']=='HUMAN_CONFIRMATION_REQUIRED'
    assert e['proposal']['confirmation_policy']['requires_human_confirmation'] is True
    assert e['proposal']['confirmation_policy']['requires_approval'] is False
    assert 'challenge' not in str(e) and e['data']==[]
    assert propose(client,ctx,'create',args)['evidence_id']==e['evidence_id']
    with factory() as db:
        assert db.scalar(select(func.count()).select_from(ContactCase))==0
        assert db.scalar(select(func.count()).select_from(HumanIntent))==0
    i=intent(client,e)
    with factory() as db:assert db.scalar(select(func.count()).select_from(ContactCase))==0
    r=confirm(client,i);assert r.status_code==200,r.text
    assert confirm(client,i).json()==r.json()
    assert client.get('/api/contact-proposals/'+e['evidence_id']).json()['receipt']==r.json()
    with factory() as db:
        assert db.scalar(select(func.count()).select_from(ContactCase))==1
        assert db.scalar(select(ContactCase)).category=='hardware'
        assert db.scalar(select(func.count()).select_from(ApprovalInstance))==0


def test_confirmation_cannot_be_cross_user_and_cancellation_blocks(client,data,monkeypatch):
    _,ctx=start(client,monkeypatch,'admin');e=propose(client,ctx,'create',create_args(data[0]));i=intent(client,e)
    sign_in(client,'test_buyer')
    assert client.post('/api/contact-proposals/'+e['evidence_id']+'/intent').status_code==404
    assert confirm(client,i).status_code==403
    sign_in(client);client.post('/api/runs/'+ctx['id']+'/cancel')
    assert confirm(client,i).status_code==409
    with data[1]() as db:assert db.scalar(select(func.count()).select_from(ContactCase))==0


def test_worker_cannot_obtain_human_challenge_or_confirm(client,data,monkeypatch):
    _,ctx=start(client,monkeypatch,'admin');e=propose(client,ctx,'create',create_args(data[0]));i=intent(client,e)
    client.cookies.clear()
    assert client.post('/api/contact-proposals/'+e['evidence_id']+'/intent',headers=worker_headers()).status_code==401
    assert client.post('/api/human-actions/'+i['id']+'/confirm',headers=worker_headers(),json={'challenge':i['challenge']}).status_code==401
    with data[1]() as db:assert db.scalar(select(func.count()).select_from(ContactCase))==0


def test_revocation_and_changed_material_require_new_proposal(client,data,monkeypatch):
    ids,factory=data;sign_in(client);case,_=create(client,ids)
    _,ctx=start(client,monkeypatch,'admin')
    args={'case_id':case['id'],'revision':case['revision'],'source':'OWN','occurred_at':'2026-09-10T10:00:00+08:00','participants':'','content':'建议补记'}
    e=propose(client,ctx,'note',args);i=intent(client,e)
    other=client.post('/api/contacts/'+case['id']+'/records',json=operation(case,source='OWN',occurred_at=args['occurred_at'],content='实际资料变化'))
    assert other.status_code==200
    assert confirm(client,i).status_code==409
    e2=propose(client,ctx,'note',{**args,'revision':other.json()['revision']},1);i2=intent(client,e2)
    with factory.begin() as db:db.get(User,ids['admin']).security_version+=1
    sign_in(client)
    assert confirm(client,i2).status_code==403
    with factory() as db:assert db.scalar(select(func.count()).select_from(ContactRecord))==1


def test_business_and_confirmation_receipt_roll_back_together(client,data,monkeypatch):
    ids,factory=data;_,ctx=start(client,monkeypatch,'admin');e=propose(client,ctx,'create',create_args(ids));i=intent(client,e)
    original=business.record
    def fail(*args,**kwargs):raise RuntimeError('synthetic receipt transaction failure')
    monkeypatch.setattr(business,'record',fail)
    with pytest.raises(RuntimeError):confirm(client,i)
    with factory() as db:
        assert db.scalar(select(func.count()).select_from(ContactCase))==0
        assert db.get(HumanIntent,i['id']).receipt is None
    monkeypatch.setattr(business,'record',original)
    assert confirm(client,i).status_code==200


def test_department_dispatch_assignment_and_feedback_via_proposals(client,data,monkeypatch):
    ids,factory=data;sign_in(client);case,_=create(client,ids)
    g=group(client,[ids['admin']],kind='DEPARTMENT',name='合成设计部门',heads=[ids['admin']])
    _,ctx=start(client,monkeypatch,'admin')
    e=propose(client,ctx,'task',{'case_id':case['id'],'revision':1,'department_id':g['id'],'title':'核对规格',
        'affected_type':'DRAWING','affected_ref':'DRAWING-T001-R2','impact_description':'核对变更尺寸',
        'planned_action':'REWORK','delivery_impact_days':2,'estimated_amount':'1200.00','currency':'CNY',
        'source_system':'AGENT','source_ref':None,'source_as_of':None})
    assert confirm(client,intent(client,e)).status_code==200
    case=client.get('/api/contacts/'+case['id']).json();tid=case['tasks'][0]['id']
    e=propose(client,ctx,'assign',{'case_id':case['id'],'task_id':tid,'revision':case['revision'],'assignee_id':ids['admin'],'reason':'本人确认分派'},1)
    assert confirm(client,intent(client,e)).status_code==200
    case=client.get('/api/contacts/'+case['id']).json()
    e=propose(client,ctx,'respond',{'case_id':case['id'],'task_id':tid,'revision':case['revision'],'content':'尺寸复核与返工已经完成',
        'actual_completed_at':'2026-09-10T12:00:00+08:00','actual_hours':'3.50','actual_amount':'1180.00','currency':'CNY',
        'execution_evidence':'返工记录与尺寸复测报告','source_system':'AGENT','source_ref':None,'source_as_of':None},2)
    assert confirm(client,intent(client,e)).status_code==200
    case=client.get('/api/contacts/'+case['id']).json()
    assert case['tasks'][0]['status']=='RESPONDED' and case['collaboration_status']=='OPEN'
    assert len(case['records'])==3


def test_tools_validate_real_identifiers_scope_and_no_hidden_commands(client,data,monkeypatch):
    ids,factory=data;sign_in(client);case,_=create(client,ids,'HISTORY')
    g=group(client,[ids['admin']],kind='DEPARTMENT',name='测试')
    _,ctx=start(client,monkeypatch,'admin')
    for key,args in [('prepare_contact_create',{**create_args(ids),'user_id':ids['buyer']}),
                     ('prepare_contact_create',{**create_args(ids),'request_key':str(uuid4())}),
                     ('prepare_contact_note',{'case_id':{},'revision':1}),
                     ('prepare_contact_task',{'case_id':case['id'],'revision':1,'department_id':g['id'],'title':'不能派发'})]:
        r=client.post(f"/internal/runs/{ctx['id']}/tools",headers=worker_headers(),json={'epoch':ctx['epoch'],'sequence':0,'key':key,'arguments':args})
        assert r.status_code in {400,409},r.text
    with factory() as db:assert db.scalar(select(func.count()).select_from(Step))==0
    _,ctx2=start(client,monkeypatch)
    r=client.post(f"/internal/runs/{ctx2['id']}/tools",headers=worker_headers(),json={'epoch':ctx2['epoch'],'sequence':0,'key':'query_contact_context','arguments':{'case_id':case['id']}})
    assert r.status_code==403
