from copy import deepcopy
from sqlalchemy import select
from app import models as m
from conftest import sign_in, draft, submit
from test_core import confirm_decision
from test_bpm_routes import config


def test_version_edit_publish_keeps_original_instance(client, data):
    ids,factory=data; sign_in(client)
    cfg=config([ids['admin']])
    def create(c):
        r=client.post('/api/workflows',json={'process_key':'version_test','name':'版本测试','config':c})
        assert r.status_code==200,r.text
        return r.json()['id']
    v1=create(cfg)
    assert client.post(f'/api/workflows/{v1}/publish').status_code==200
    iid=submit(client,{**ids,'definition':v1},draft(client,ids,quantity='5'))
    original=client.get(f'/api/workflows/{v1}').json()
    assert original['instance_count']==1
    edit={'name':'不可覆盖','config':cfg,'expected_hash':original['edit_hash']}
    assert client.put(f'/api/workflows/{v1}',json=edit).json()['error']['code']=='PUBLISHED_IMMUTABLE'
    changed=deepcopy(cfg);changed['nodes'][0]['name']='V2节点'
    v2=create(changed)
    detail=client.get(f'/api/workflows/{v2}').json()
    edit={'name':'V2草稿修改','config':changed,'expected_hash':detail['edit_hash']}
    assert client.put(f'/api/workflows/{v2}',json=edit).json()['version']==2
    assert client.put(f'/api/workflows/{v2}',json=edit).status_code==409
    assert client.post(f'/api/workflows/{v2}/publish').status_code==200
    history=client.get('/api/workflows/history/version_test').json()['items']
    assert [(v['version'],v['status']) for v in history]==[(2,'PUBLISHED'),(1,'PUBLISHED')]
    assert client.get(f'/api/workflows/{v1}').json()['package_hash']==original['package_hash']
    rid=draft(client,ids)
    available=client.get(f'/api/workflows/available?resource_type=purchase_request&resource_id={rid}').json()
    assert {v1,v2} <= {d['id'] for d in available}
    assert client.post(f'/api/purchases/{rid}/submit-intent',json={'revision':1,'definition_id':v1}).status_code==200
    with factory() as db: assert db.get(m.ApprovalInstance,iid).definition_id==v1
    for _ in range(2): _,result=confirm_decision(client,iid)
    assert result.json()['status']=='COMPLETED'


def test_workflow_designer_endpoints_require_permission(client,data):
    ids,_=data;sign_in(client,'test_buyer')
    assert client.get(f"/api/workflows/{ids['definition']}").status_code==403
    assert client.get('/api/workflows/history/purchase_request').status_code==403
    assert client.put(f"/api/workflows/{ids['definition']}",json={'name':'bad','config':config(),'expected_hash':'0'*64}).status_code==403


def test_scope_is_checked_from_actual_materials(client,data):
    ids,_=data;sign_in(client)
    definition_ids={}
    for category in ['hardware','raw_material']:
        cfg=config([ids['admin']]);cfg['applicability']={'categories':[category]}
        did=client.post('/api/workflows',json={'process_key':category+'_scope','name':category,'config':cfg}).json()['id']
        assert client.post(f'/api/workflows/{did}/publish').status_code==200
        definition_ids[category]=did
    rid=draft(client,ids)
    rows=client.get(f'/api/workflows/available?resource_type=purchase_request&resource_id={rid}').json()
    assert definition_ids['hardware'] in [d['id'] for d in rows]
    assert definition_ids['raw_material'] not in [d['id'] for d in rows]
    response=client.post(f'/api/purchases/{rid}/submit-intent',json={'revision':1,'definition_id':definition_ids['raw_material']})
    assert response.status_code==409
