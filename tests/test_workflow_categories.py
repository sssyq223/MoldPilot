from sqlalchemy import select
from app.models import ApprovalInstance
from conftest import sign_in,draft,submit
from test_core import confirm_decision

def test_generic_category_versions_and_retirement(client,data):
    ids,factory=data;sign_in(client)
    r=client.post('/api/workflow-categories',json={'name':'加工资料审核'});assert r.status_code==200
    category=r.json()
    cfg={'business_type':'generic','nodes':[{'key':'review','name':'资料审核','users':[ids['admin']],'mode':'ALL'}]}
    versions=[]
    for version in range(1,4):
        r=client.post('/api/workflows',json={'process_key':'free_category','name':'资料审核流程','category_id':category['id'],'config':cfg})
        assert r.status_code==200,r.text
        versions.append(r.json()['id'])
        if version<3:assert client.post(f'/api/workflows/{versions[-1]}/publish').status_code==200
    rid=draft(client,ids)
    rows=client.get(f'/api/workflows/available?resource_type=purchase_request&resource_id={rid}').json()
    chosen=[d for d in rows if d['category_id']==category['id']]
    assert [d['version'] for d in chosen]==[2,1]
    assert all(d['category_name']=='加工资料审核' and d['process_key']=='free_category' for d in chosen)
    # Classification does not impose a purchase-only business type. User explicitly chooses V1.
    iid=submit(client,{**ids,'definition':versions[0]},rid)
    with factory() as db:assert db.get(ApprovalInstance,iid).definition_id==versions[0]
    r=client.put('/api/workflow-categories/'+category['id'],json={'name':category['name'],'active':False,'expected_version':1,'reason':'停止新申请'})
    assert r.status_code==200
    rid2=draft(client,ids)
    assert client.post(f'/api/purchases/{rid2}/submit-intent',json={'revision':1,'definition_id':versions[1]}).status_code==409
    _,r=confirm_decision(client,iid);assert r.json()['status']=='COMPLETED'

def test_categories_neither_grant_access_nor_accept_missing_reference(client,data):
    ids,_=data;sign_in(client)
    cfg={'business_type':'generic','nodes':[{'key':'review','name':'审核','users':[ids['admin']],'mode':'ALL'}]}
    assert client.post('/api/workflows',json={'process_key':'no_category','name':'缺类别','config':cfg}).status_code==400
    category=client.post('/api/workflow-categories',json={'name':'任意自定义类别'}).json()
    sign_in(client,'test_buyer')
    assert client.post('/api/workflow-categories',json={'name':'越权新类别'}).status_code==403
    assert client.get('/api/workflow-categories').status_code==403
    assert client.get(f"/api/workflows/available?resource_type=purchase_request&resource_id=unknown").status_code==404
