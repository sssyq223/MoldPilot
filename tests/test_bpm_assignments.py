from sqlalchemy import select,func
from app.models import AssignmentMember,ApprovalInstance,ApprovalSeat,Grant,User
from app.assignments import resolve_users
from app import bpm
from conftest import sign_in,draft,submit
from test_core import confirm_decision

def group(client,ids,kind='ROLE',name='审批主管',heads=()):
    r=client.post('/api/organization/groups',json={'kind':kind,'name':name,'members':[{'user_id':uid,'is_head':uid in heads} for uid in ids],'reason':'合成测试配置'})
    assert r.status_code==200,r.text
    return r.json()

def change(client,g,ids,active=True):
    data={'kind':g['kind'],'name':g['name'],'members':[{'user_id':uid} for uid in ids],
          'active':active,'reason':'变更合成成员','expected_version':g['version']}
    return client.put('/api/organization/groups/'+g['id'],json=data)

def node(role=None,users=None,departments=None,heads=False,key='review_1'):
    n={'key':key,'name':'审批人员规则测试','users':users or [],'mode':'ALL','reject_rules':[]}
    if role or departments:n['assignment']={'roles':[role] if role else [],'departments':departments or [],'department_heads_only':heads}
    return n

def publish(client,nodes):
    r=client.post('/api/workflows',json={'process_key':'assignment_test','name':'动态人员合成流程','config':{'business_type':'purchase_request','nodes':nodes}})
    assert r.status_code==200,r.text
    did=r.json()['id'];p=client.post(f'/api/workflows/{did}/publish')
    return did,p

def test_group_membership_does_not_grant_business_permissions(client,data):
    ids,factory=data;sign_in(client)
    with factory() as db:before=db.scalar(select(func.count()).select_from(Grant));version=db.get(User,ids['buyer']).security_version
    g=group(client,[ids['buyer']])
    with factory() as db:
        assert db.scalar(select(func.count()).select_from(Grant))==before
        assert db.get(User,ids['buyer']).security_version==version+1
    sign_in(client,'test_buyer')
    assert client.get('/api/organization/groups').status_code==403
    assert client.get('/api/workflows/assignment-catalog').status_code==403
    assert change(client,g,[ids['buyer'],ids['admin']]).status_code==403
    assert len(client.get('/api/projects').json())==1

def test_role_and_department_intersection_and_heads(client,data):
    ids,factory=data;sign_in(client)
    role=group(client,[ids['admin'],ids['reviewer']])
    dept=group(client,[ids['reviewer'],ids['buyer']],kind='DEPARTMENT',name='设计部门',heads=[ids['reviewer']])
    n=node(role['id'],departments=[dept['id']],heads=True)
    with factory() as db:
        candidates,sources=resolve_users(db,n)
        assert candidates==[ids['reviewer']]
        assert len(sources)==2
    did,r=publish(client,[n]);assert r.status_code==200,r.text
    iid=submit(client,{**ids,'definition':did},draft(client,ids))
    with factory() as db:
        assert list(db.scalars(select(ApprovalSeat.user_id).where(ApprovalSeat.instance_id==iid)))==[ids['reviewer']]

def test_current_seats_frozen_next_node_resolves_new_members(client,data):
    ids,factory=data;sign_in(client)
    g=group(client,[ids['admin']])
    did,r=publish(client,[node(g['id']),node(g['id'],key='review_2')]);assert r.status_code==200
    iid=submit(client,{**ids,'definition':did},draft(client,ids))
    assert change(client,g,[ids['reviewer']]).status_code==200
    with factory() as db:
        assert list(db.scalars(select(ApprovalSeat.user_id).where(ApprovalSeat.instance_id==iid)))==[ids['admin']]
        assert db.get(ApprovalInstance,iid).assignment_snapshots['0']['sources'][0]['version']==1
    _,r=confirm_decision(client,iid);assert r.status_code==200,r.text
    with factory() as db:
        current=db.get(ApprovalInstance,iid)
        assert current.stage_index==1
        assert current.assignment_snapshots['1']['eligible']==[ids['reviewer']]
        assert current.assignment_snapshots['1']['sources'][0]['version']==2
    sign_in(client,'test_reviewer')
    _,r=confirm_decision(client,iid);assert r.json()['status']=='COMPLETED'

def test_empty_members_after_publish_block_instead_of_skip(client,data):
    ids,factory=data;sign_in(client)
    g=group(client,[ids['admin']]);did,r=publish(client,[node(g['id'])]);assert r.status_code==200
    assert change(client,g,[]).status_code==200
    iid=submit(client,{**ids,'definition':did},draft(client,ids))
    with factory() as db:
        ins=db.get(ApprovalInstance,iid)
        assert ins.status=='RUNNING' and ins.incident=='ASSIGNMENT_BLOCKED'
        assert ins.assignment_snapshots['0']['blocked'] is True
        assert not list(db.scalars(select(ApprovalSeat).where(ApprovalSeat.instance_id==iid)))

def test_all_requires_every_candidate_permission_and_complete_materials(client,data):
    ids,factory=data;sign_in(client)
    g=group(client,[ids['admin'],ids['buyer']]);did,r=publish(client,[node(g['id'])]);assert r.status_code==200
    iid=submit(client,{**ids,'definition':did},draft(client,ids))
    with factory() as db:
        ins=db.get(ApprovalInstance,iid)
        assert ins.incident=='ASSIGNMENT_BLOCKED'
        assert ins.assignment_snapshots['0']['eligible']==[ids['admin']]
        assert not list(db.scalars(select(ApprovalSeat).where(ApprovalSeat.instance_id==iid)))

def test_stale_group_write_and_empty_publish_rejected(client,data):
    ids,_=data;sign_in(client)
    g=group(client,[ids['admin']]);assert change(client,g,[]).status_code==200
    assert change(client,g,[ids['buyer']]).status_code==409
    _,r=publish(client,[node(g['id'])]);assert r.status_code==400
    assert r.json()['error']['code']=='ASSIGNMENT_BLOCKED'

def test_assignment_simulation_preserves_rules_without_creating_seats(client,data):
    ids,factory=data;sign_in(client)
    g=group(client,[ids['admin']])
    r=client.post('/api/workflows/simulate',json={'config':{'business_type':'purchase_request','nodes':[node(g['id'])]},'snapshot':{}})
    assert r.status_code==200 and r.json()['outcome']=='ROUTE_VALID'
    assert r.json()['path'][0]['assignment']['roles']==[g['id']]
    with factory() as db:assert db.scalar(select(func.count()).select_from(ApprovalSeat))==0
