from copy import deepcopy
import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
from app.models import WorkflowDefinition
from conftest import sign_in
from test_material_rules import CONTRACT

def create(client,contract=None):
    r=client.post('/api/material-templates',json={'template_key':'design_sheet','name':'设计清单（合成模板）','contract':contract or CONTRACT})
    assert r.status_code==200,r.text
    return r.json()

def test_draft_optimistic_update_published_immutable_and_versions(client,data):
    ids,factory=data;sign_in(client)
    t=create(client)
    body={'name':'设计清单核对','contract':CONTRACT,'expected_hash':t['edit_hash']}
    r=client.put('/api/material-templates/'+t['id'],json=body);assert r.status_code==200
    assert r.json()['version']==1
    assert client.put('/api/material-templates/'+t['id'],json=body).status_code==409
    published=client.post('/api/material-templates/'+t['id']+'/publish');assert published.status_code==200
    assert client.post('/api/material-templates/'+t['id']+'/publish').json()['package_hash']==published.json()['package_hash']
    assert client.put('/api/material-templates/'+t['id'],json=body).status_code==409
    with pytest.raises(DBAPIError):
        with factory.begin() as db:db.execute(text('UPDATE material_template SET name=:name WHERE id=:id'),{'name':'绕过API','id':t['id']})
    t2=create(client);assert t2['version']==2 and t2['status']=='DRAFT'
    history=client.get('/api/material-templates?template_key=design_sheet').json()
    assert [(x['version'],x['status']) for x in history]==[(2,'DRAFT'),(1,'PUBLISHED')]

def test_workflow_pins_published_material_version_and_reuses_it(client,data):
    ids,factory=data;sign_in(client)
    t=create(client)
    category=client.post('/api/workflow-categories',json={'name':'清单核对'}).json()
    cfg={'business_type':'generic','nodes':[{'key':'review','name':'核对','users':[ids['admin']],'mode':'ALL'}]}
    body={'process_key':'bound_material','name':'清单审批','category_id':category['id'],'material_template_id':t['id'],'config':cfg}
    assert client.post('/api/workflows',json=body).json()['error']['code']=='MATERIAL_TEMPLATE_REQUIRED'
    assert client.post('/api/material-templates/'+t['id']+'/publish').status_code==200
    definitions=[]
    for key in ['bound_material','bound_material_other']:
        body['process_key']=key
        r=client.post('/api/workflows',json=body);assert r.status_code==200,r.text
        definitions.append(r.json()['id'])
        assert client.post('/api/workflows/'+definitions[-1]+'/publish').status_code==200
    changed=deepcopy(CONTRACT);changed['tables'][0]['fields'][1]['label']='数量新名称'
    t2=create(client,changed);assert client.post('/api/material-templates/'+t2['id']+'/publish').status_code==200
    with factory() as db:
        for did in definitions:
            d=db.get(WorkflowDefinition,did)
            assert d.material_template_id==t['id'] and d.config['material_contract']==CONTRACT
    malicious={**body,'config':{**cfg,'material_contract':changed}}
    assert client.post('/api/workflows',json=malicious).json()['error']['code']=='MATERIAL_CONTRACT_MISMATCH'

def test_template_authorization_and_empty_schema(client,data):
    sign_in(client)
    assert client.post('/api/material-templates',json={'template_key':'empty','name':'空模板','contract':{'fields':[],'tables':[]}}).status_code==400
    sign_in(client,'test_buyer')
    assert client.get('/api/material-templates').status_code==403
    assert client.post('/api/material-templates',json={'template_key':'forbidden','name':'越权','contract':CONTRACT}).status_code==403

def test_bound_schema_used_by_simulation_and_preserved_by_draft_edit(client,data):
    ids,_=data;sign_in(client);t=create(client)
    client.post('/api/material-templates/'+t['id']+'/publish')
    category=client.post('/api/workflow-categories',json={'name':'资料版本复用'}).json()
    cfg={'business_type':'generic','nodes':[{'key':'review','name':'核对','users':[ids['admin']],'mode':'ALL','reject_rules':[{'condition':{'field':'urgent','op':'eq','value':True},'reason':'紧急资料需退回核对'}]}]}
    result=client.post('/api/workflows/simulate',json={'config':cfg,'material_template_id':t['id'],'snapshot':{'material_data':{'fields':{'urgent':True},'tables':{}}}})
    assert result.status_code==200 and result.json()['outcome']=='MUST_REJECT'
    d=client.post('/api/workflows',json={'process_key':'bound_edit','name':'绑定编辑','category_id':category['id'],'material_template_id':t['id'],'config':cfg}).json()
    current=client.get('/api/workflows/'+d['id']).json()
    edit=client.put('/api/workflows/'+d['id'],json={'name':'绑定编辑更新','category_id':category['id'],'config':current['config'],'expected_hash':current['edit_hash']})
    assert edit.status_code==200 and edit.json()['material_template_id']==t['id']
