from datetime import timedelta
import pytest
from sqlalchemy import select, func, text
from app.models import Grant, User, PurchaseRequest, ApprovalInstance, ApprovalAction, HumanIntent, WorkflowDefinition, Outbox
from app.authorization import access, PERMISSIONS
from app.db import now
from app import bpm
from app.rules import evaluate
from app.query_guard import validate_sql
from app.errors import DomainError
from conftest import sign_in, draft, submit, PASSWORD


def test_health_timezone(client):
    assert client.get('/api/health').json()['timezone']=='Asia/Shanghai'


def test_login_password_not_returned(client):
    user=sign_in(client)
    assert 'password_hash' not in user
    assert client.get('/api/me').json()['user']['id']==user['id']


def test_invalid_login(client):
    assert client.post('/api/auth/login',json={'username':'admin','password':'bad'}).status_code==401


def test_new_user_without_grants(client,data):
    ids,_=data;sign_in(client)
    department=client.post('/api/organization/groups',json={
        'kind':'DEPARTMENT','name':'空权限部门','members':[],'active':True,'reason':'验证新用户默认没有业务授权'})
    assert department.status_code==200
    r=client.post('/api/users',json={'username':'empty_user','display_name':'空权限用户','department':'空权限部门','password':PASSWORD})
    assert r.status_code==200
    sign_in(client,'empty_user')
    assert client.get('/api/projects').json()==[]
    assert client.get('/api/capabilities').json()['tools']==[]
    r=client.post('/api/purchases',json={'project_id':ids['project'],'lines':[{'material_id':ids['hardware'],'quantity':'1','due_date':'2026-09-16'}]})
    assert r.status_code==403


def test_csrf_required(client):
    sign_in(client);client.headers.pop('X-CSRF-Token')
    assert client.post('/api/auth/logout').status_code==403


def test_forged_human_source_cannot_replace_session(client):
    r=client.post('/api/human-actions/fake/confirm',json={'challenge':'x'*40},headers={'actor_type':'HUMAN','Authorization':'Bearer fake'})
    assert r.status_code==401


def test_user_cannot_create_privileged_user(client):
    sign_in(client)
    r=client.post('/api/users',json={'username':'attacker','display_name':'test','password':PASSWORD,'super_admin':True})
    assert r.status_code==422


def test_buyer_cannot_administer(client):
    sign_in(client,'test_buyer')
    assert client.get('/api/users').status_code==403
    assert client.post('/api/workflows',json={'process_key':'test_flow','name':'test','config':{}}).status_code==403


def test_no_scope_cartesian_product(data):
    ids,factory=data
    with factory.begin() as db:
        user=db.get(User,ids['buyer'])
        db.add(Grant(user_id=user.id,permission='purchase.read',effect='ALLOW',scope={'project_id':[ids['other_project']],'category':['raw_material']},fields=['quantity'],reason='test',granted_by=ids['admin']))
        db.flush()
        assert access(db,user,'purchase.read',{'project_id':ids['project'],'category':'hardware'}).allowed
        assert access(db,user,'purchase.read',{'project_id':ids['other_project'],'category':'raw_material'}).allowed
        assert not access(db,user,'purchase.read',{'project_id':ids['project'],'category':'raw_material'}).allowed
        assert not access(db,user,'purchase.read',{'project_id':ids['other_project'],'category':'hardware'}).allowed


def test_deny_overrides_allow_and_expiration(data):
    ids,factory=data
    with factory.begin() as db:
        user=db.get(User,ids['buyer']);obj={'project_id':ids['project'],'category':'hardware'}
        g=Grant(user_id=user.id,permission='purchase.read',effect='DENY',scope={'category':['hardware']},fields=['quantity'],reason='test',granted_by=ids['admin'],valid_to=now()-timedelta(seconds=1))
        db.add(g);db.flush();assert access(db,user,'purchase.read',obj).allowed
        g.valid_to=now()+timedelta(hours=1);db.flush();assert not access(db,user,'purchase.read',obj).allowed


def test_cross_scope_create_is_atomic(client,data):
    ids,factory=data;sign_in(client,'test_buyer')
    r=client.post('/api/purchases',json={'project_id':ids['project'],'lines':[{'material_id':ids['hardware'],'quantity':'1','due_date':'2026-09-16'},{'material_id':ids['steel'],'quantity':'1','due_date':'2026-09-16'}]})
    assert r.status_code==403
    with factory() as db: assert db.scalar(select(func.count()).select_from(PurchaseRequest))==0


def test_list_filters_before_return(client,data):
    ids,_=data;sign_in(client)
    visible=draft(client,ids)
    hidden=draft(client,ids,material=ids['steel'])
    sign_in(client,'test_buyer')
    rows=client.get('/api/purchases').json()
    assert [x['id'] for x in rows]==[visible]
    assert hidden not in str(rows)


def test_mixed_document_does_not_leak_header(client,data):
    ids,_=data;sign_in(client)
    r=client.post('/api/purchases',json={'project_id':ids['project'],'remark':'secret mixed header','lines':[{'material_id':ids['hardware'],'quantity':'1','due_date':'2026-09-16'},{'material_id':ids['steel'],'quantity':'1','due_date':'2026-09-16'}]})
    assert r.status_code==200
    sign_in(client,'test_buyer');assert client.get('/api/purchases').json()==[]


def test_submit_persists_spiff_snapshot_and_seats(client,data):
    ids,factory=data;sign_in(client,'test_buyer');iid=submit(client,ids,draft(client,ids))
    with factory() as db:
        inst=db.get(ApprovalInstance,iid)
        assert inst.status=='RUNNING' and inst.snapshot['submitter']['id']==ids['buyer']
        assert bpm.serializer.from_dict(inst.engine_state).get_tasks(manual=True)
    sign_in(client,'test_reviewer');assert client.get('/api/approvals').json()[0]['id']==iid


def decision_payload(detail,decision='APPROVE'):
    return {k:detail[k] for k in ['seat_id','seat_version','version','snapshot_hash']} | {'instance_id':detail['id'],'decision':decision,'comment':'已核对本轮模拟资料'}


def confirm_decision(client,iid,decision='APPROVE'):
    detail=client.get('/api/approvals/'+iid).json()
    r=client.post('/api/approvals/decision-intent',json=decision_payload(detail,decision));assert r.status_code==200,r.text
    intent=r.json();r=client.post('/api/human-actions/'+intent['id']+'/confirm',json={'challenge':intent['challenge']});assert r.status_code==200,r.text
    return intent,r


def test_full_approval_does_not_mean_order_or_shipment(client,data):
    ids,factory=data;sign_in(client,'test_buyer');iid=submit(client,ids,draft(client,ids))
    sign_in(client,'test_reviewer');_,r=confirm_decision(client,iid);assert r.json()['status']=='RUNNING'
    sign_in(client);intent,r=confirm_decision(client,iid);assert r.json()['business_status']=='APPROVED'
    again=client.post('/api/human-actions/'+intent['id']+'/confirm',json={'challenge':intent['challenge']})
    assert again.json()==r.json()
    with factory() as db: assert db.scalar(select(func.count()).select_from(ApprovalAction))==2


def test_mandatory_rejection_cannot_approve_or_return(client,data):
    ids,factory=data
    sign_in(client)
    config=client.get('/api/workflows').json()[0]['config']
    config['nodes'][0]['reject_rules']=[{'condition':{'field':'quantity','op':'gt','value':'99'},'reason':'超过已配置范围'}]
    version=client.post('/api/workflows',json={'process_key':'mandatory_reject','name':'必须驳回测试','config':config}).json()
    assert client.post('/api/workflows/'+version['id']+'/publish').status_code==200
    ids={**ids,'definition':version['id']}
    sign_in(client,'test_buyer');iid=submit(client,ids,draft(client,ids))
    sign_in(client,'test_reviewer');detail=client.get('/api/approvals/'+iid).json()
    assert detail['allowed_actions']==['REJECT']
    for decision in ['APPROVE','RETURN']:
        assert client.post('/api/approvals/decision-intent',json=decision_payload(detail,decision)).status_code==409
    _,r=confirm_decision(client,iid,'REJECT');assert r.json()['status']=='REJECTED'


def test_revocation_between_preview_and_confirm(client,data):
    ids,factory=data;sign_in(client,'test_buyer');iid=submit(client,ids,draft(client,ids))
    sign_in(client,'test_reviewer');detail=client.get('/api/approvals/'+iid).json()
    intent=client.post('/api/approvals/decision-intent',json=decision_payload(detail)).json()
    with factory.begin() as db:
        for g in db.scalars(select(Grant).where(Grant.user_id==ids['reviewer'],Grant.permission=='purchase.approve')):g.active=False
        db.get(User,ids['reviewer']).security_version+=1
    r=client.post('/api/human-actions/'+intent['id']+'/confirm',json={'challenge':intent['challenge']})
    assert r.status_code==403
    with factory() as db:
        assert db.get(HumanIntent,intent['id']).receipt is None
        assert db.scalar(select(func.count()).select_from(ApprovalAction))==0


def test_template_version_pinned(client,data):
    ids,_=data;sign_in(client,'test_buyer');iid=submit(client,ids,draft(client,ids));sign_in(client)
    original=client.get('/api/workflows').json()[0]
    r=client.post('/api/workflows',json={'process_key':original['process_key'],'name':'新版','config':original['config']});assert r.status_code==200
    assert client.post('/api/workflows/'+r.json()['id']+'/publish').status_code==200
    detail=client.get('/api/approvals/'+iid).json();assert detail['definition']['version']==1


def test_unknown_rule_or_script_rejected():
    with pytest.raises(DomainError): evaluate({'field':'__import__','op':'eq','value':'os'}, {})
    assert evaluate({'field':'amount','op':'gt','value':'10'}, {'amount':'11'}) is None
    assert evaluate({'field':'quantity','op':'gte','value':'0.1'}, {'quantity':'0.100000'}) is True


@pytest.mark.parametrize('sql',[
    'DELETE FROM orders', 'SELECT id FROM orders; DELETE FROM orders',
    'WITH x AS (DELETE FROM orders RETURNING id) SELECT id FROM x',
    'SELECT id INTO new_table FROM orders','SELECT id FROM orders FOR UPDATE',
    'SELECT pg_sleep(10) FROM orders', 'SELECT secret FROM orders',
    'SELECT id FROM orders ORDER BY secret','SELECT * FROM orders',
    'SELECT id FROM pg_catalog.pg_class','SELECT id FROM orders JOIN secret ON true',
])
def test_sql_attack_subset_rejected(sql):
    with pytest.raises(DomainError):validate_sql(sql,'orders',{'id','quantity'})


def test_scoped_sql_requires_named_columns_and_limit():
    assert 'LIMIT 100' in validate_sql('SELECT id, quantity FROM orders WHERE quantity > 5','orders',{'id','quantity'}).sql()


def test_worker_cannot_enter_human_api(client,data):
    from app.config import settings
    assert client.post('/api/human-actions/fake/confirm',json={'challenge':'x'*40},headers={'Authorization':'Bearer '+settings().worker_secret}).status_code==401
