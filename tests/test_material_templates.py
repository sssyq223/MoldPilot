from copy import deepcopy
from io import BytesIO
import pytest
from uuid import uuid4
from zipfile import ZipFile
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
from app.config import settings
from app.models import WorkflowDefinition
from conftest import sign_in
from test_material_rules import CONTRACT

XLSX_CONTRACT={
    'fields':[{'key':'urgent','label':'是否紧急','type':'boolean'},
              {'key':'needed_on','label':'要求日期','type':'date'}],
    'tables':[{'key':'design','label':'设计清单','fields':[
        {'key':'item','label':'料号','type':'text'},
        {'key':'quantity','label':'数量','type':'decimal','unit':'件'},
        {'key':'currency','label':'币种','type':'text'},
        {'key':'unit_price','label':'单价','type':'money','currency_field':'currency'}]}]}


@pytest.fixture
def local_file_storage(tmp_path,monkeypatch):
    monkeypatch.setattr(settings(),'file_backend','local')
    monkeypatch.setattr(settings(),'file_local_root',str(tmp_path/'objects'))
    monkeypatch.setattr(settings(),'environment','test')


def inline(value):
    return f'<is><t>{value}</t></is>'


def xlsx(rows):
    cells=[]
    for r,c,value in rows:
        if isinstance(value,tuple) and value[0]=='formula':
            body=f'<f>{value[1]}</f><v>{value[2]}</v>';kind=''
        elif isinstance(value,bool):
            body=f'<v>{1 if value else 0}</v>';kind=' t="b"'
        elif isinstance(value,(int,float)):
            body=f'<v>{value}</v>';kind=''
        else:
            body=inline(value);kind=' t="inlineStr"'
        cells.append(f'<c r="{c}{r}"{kind}>{body}</c>')
    sheet='<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"><sheetData>'+''.join(cells)+'</sheetData></worksheet>'
    package=BytesIO()
    with ZipFile(package,'w') as archive:
        archive.writestr('[Content_Types].xml','<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types"/>')
        archive.writestr('xl/workbook.xml','<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships"><sheets><sheet name="数据" sheetId="1" r:id="rId1"/></sheets></workbook>')
        archive.writestr('xl/_rels/workbook.xml.rels','<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"><Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" Target="worksheets/sheet1.xml"/></Relationships>')
        archive.writestr('xl/worksheets/sheet1.xml',sheet)
    return package.getvalue()


def upload_xlsx(client,content):
    return client.post('/api/files',params={'filename':'设计清单.xlsx','request_key':str(uuid4())},
        content=content,headers={'Content-Type':'application/octet-stream'})

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


def test_xlsx_preview_parses_template_mapping_without_binding_materials(client,data,local_file_storage):
    sign_in(client);template=create(client,XLSX_CONTRACT)
    client.post('/api/material-templates/'+template['id']+'/publish')
    file=upload_xlsx(client,xlsx([
        (1,'A','是否紧急'),(1,'B',True),(2,'A','要求日期'),(2,'B','2026-09-20'),
        (4,'A','料号'),(4,'B','数量'),(4,'C','币种'),(4,'D','单价'),
        (5,'A','MAT-A'),(5,'B',20),(5,'C','CNY'),(5,'D','1000.50')])).json()
    mapping={'fields':{'urgent':'数据!B1','needed_on':'数据!B2'},'tables':{'design':{'sheet':'数据','header_row':4,'first_data_row':5}}}
    result=client.post(f"/api/material-templates/{template['id']}/xlsx-preview",json={'file_id':file['id'],'mapping':mapping})
    assert result.status_code==200,result.text
    body=result.json()
    assert body['status']=='READY_FOR_REVIEW' and body['issues']==[]
    assert body['material_data']['fields']=={'urgent':True,'needed_on':'2026-09-20'}
    assert body['material_data']['tables']['design']==[{'id':'MAT-A','values':{'item':'MAT-A','quantity':'20','currency':'CNY','unit_price':'1000.50'}}]
    assert 'MATERIALS_NOT_BOUND' in ' '.join(body['limitations'])


def test_xlsx_preview_flags_formula_and_unstable_or_duplicate_rows(client,data,local_file_storage):
    sign_in(client);template=create(client,XLSX_CONTRACT)
    client.post('/api/material-templates/'+template['id']+'/publish')
    file=upload_xlsx(client,xlsx([
        (1,'B',False),(2,'B','2026-09-20'),(4,'A','料号'),(4,'B','数量'),(4,'C','币种'),(4,'D','单价'),
        (5,'A','MAT-DUP'),(5,'B',('formula','SUM(10,10)','20')),(5,'C','CNY'),(5,'D','100'),
        (6,'A','MAT-DUP'),(6,'B',5),(6,'C','CNY'),(6,'D','50')])).json()
    mapping={'fields':{'urgent':'数据!B1','needed_on':'数据!B2'},'tables':{'design':{'sheet':'数据','header_row':4,'first_data_row':5}}}
    body=client.post(f"/api/material-templates/{template['id']}/xlsx-preview",json={'file_id':file['id'],'mapping':mapping}).json()
    codes={issue['code'] for issue in body['issues']}
    assert body['status']=='NEEDS_REVIEW'
    assert {'FORMULA_NOT_ACCEPTED','ROW_ID_DUPLICATE'} <= codes
    assert body['material_data']['tables']['design'][0]['values']['item']=='MAT-DUP'
    assert 'quantity' not in body['material_data']['tables']['design'][0]['values']
