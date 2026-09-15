from io import BytesIO
from types import SimpleNamespace
from uuid import uuid4
from zipfile import ZipFile
from hashlib import sha256
import pytest
from sqlalchemy import select,func,text
from sqlalchemy.exc import DBAPIError
from app.config import settings
from app.models import FileObject,ContactAttachment,ContactCase,Grant,Capability,RunFile
from app import object_storage
from app.errors import DomainError
from conftest import sign_in
from test_contacts import create,grant
from test_agent_api import start,worker_headers
from test_contact_proposals import propose,intent,confirm

PDF=b'%PDF-1.4\n% synthetic material\n%%EOF'


@pytest.fixture(autouse=True)
def isolated_storage(tmp_path,monkeypatch):
    monkeypatch.setattr(settings(),'file_backend','local')
    monkeypatch.setattr(settings(),'file_local_root',str(tmp_path/'objects'))
    monkeypatch.setattr(settings(),'environment','test')


def upload(client,content=PDF,filename='合成材料.pdf',cid=None,key=None):
    params={'filename':filename,'request_key':key or str(uuid4())}
    if cid:params['conversation_id']=cid
    return client.post('/api/files',params=params,content=content,headers={'Content-Type':'application/octet-stream'})


def link(client,context,case,file,sequence=0,previous=None):
    e=propose(client,context,'attach',{'case_id':case['id'],'revision':case['revision'],
        'file_id':file['id'],'title':'设计讨论材料','previous_id':previous},sequence)
    r=confirm(client,intent(client,e));assert r.status_code==200,r.text
    return client.get('/api/contacts/'+case['id']).json()


def test_upload_private_original_retry_and_download(client,data):
    sign_in(client);key=str(uuid4());r=upload(client,key=key);assert r.status_code==200,r.text
    blob=r.json();assert blob['sha256']==sha256(PDF).hexdigest()
    assert 'object_key' not in blob and 'storage_namespace' not in blob
    assert upload(client,key=key).json()['id']==blob['id']
    assert upload(client,PDF+b'changed',key=key).status_code==409
    r=client.get('/api/files/'+blob['id']+'/content');assert r.content==PDF
    assert r.headers['content-disposition'].startswith('attachment;') and r.headers['x-content-type-options']=='nosniff'
    assert client.get('/api/files/'+blob['id']+'/content?preview=true').status_code==400
    sign_in(client,'test_buyer')
    assert client.get('/api/files/'+blob['id']+'/content').status_code==404
    assert client.get('/api/conversations/'+blob['conversation_id']+'/files').status_code==404
    assert upload(client).status_code==403
    with data[1]() as db:assert db.scalar(select(func.count()).select_from(FileObject))==1


def test_upload_rejects_path_type_size_and_macro(client,data,monkeypatch):
    sign_in(client)
    for name,body in [('../合同.pdf',PDF),('x.html',b'<script>'),('x.png',b'<html>'),('empty.pdf',b'')]:
        assert upload(client,body,name).status_code==400
    package=BytesIO()
    with ZipFile(package,'w') as archive:
        archive.writestr('[Content_Types].xml','test');archive.writestr('word/document.xml','test');archive.writestr('word/vbaProject.bin','macro')
    assert upload(client,package.getvalue(),'x.docx').status_code==400
    monkeypatch.setattr(settings(),'file_max_bytes',10)
    assert upload(client).status_code==413
    with data[1]() as db:assert db.scalar(select(func.count()).select_from(FileObject))==0


def test_upload_quota_and_cross_conversation_rejected(client,data,monkeypatch):
    sign_in(client);first=upload(client).json()
    monkeypatch.setattr(settings(),'file_daily_bytes',len(PDF))
    assert upload(client).status_code==429
    monkeypatch.setattr(settings(),'file_daily_bytes',200*1024*1024)
    with data[1].begin() as db:db.add(Grant(user_id=data[0]['buyer'],permission='file.upload',effect='ALLOW',scope={'all':True},fields=['*'],reason='合成上传',granted_by=data[0]['admin']))
    sign_in(client,'test_buyer')
    assert upload(client,cid=first['conversation_id']).status_code==404


def test_attachment_confirmation_versions_and_immutable_originals(client,data,monkeypatch):
    sign_in(client);case,_=create(client,data[0]);first=upload(client).json()
    _,ctx=start(client,monkeypatch,'admin')
    e=propose(client,ctx,'attach',{'case_id':case['id'],'revision':1,'file_id':first['id'],'title':'设计讨论材料','previous_id':None})
    assert client.get('/api/contacts/'+case['id']).json()['attachments']==[]
    assert confirm(client,intent(client,e)).status_code==200
    case=client.get('/api/contacts/'+case['id']).json();previous=case['attachments'][0]['id']
    second=upload(client,PDF+b'\nupdated').json();case=link(client,ctx,case,second,1,previous)
    assert [(f['version'],f['is_current']) for f in case['attachments']]==[(2,True),(1,False)]
    assert all('conversation_id' not in f for f in case['attachments'])
    assert len(case['records'])==2 and case['records'][0]['kind']=='ATTACHMENT_ADDED'
    assert client.get('/api/files/'+first['id']+'/content').content==PDF
    for table in ('file_object','contact_attachment'):
        with pytest.raises(DBAPIError):
            with data[1].begin() as db:db.execute(text('DELETE FROM '+table))
    third=upload(client,PDF+b'\nthird').json()
    r=client.post(f"/internal/runs/{ctx['id']}/tools",headers=worker_headers(),json={'epoch':ctx['epoch'],'sequence':2,'key':'prepare_contact_attach','arguments':{
        'case_id':case['id'],'revision':case['revision'],'file_id':third['id'],'title':'旧版本替换','previous_id':previous}})
    assert r.status_code==409 and r.json()['error']['code']=='VERSION_CONFLICT'


def test_uploader_cannot_bypass_business_revocation(client,data,monkeypatch):
    ids,factory=data;sign_in(client);case,_=create(client,ids)
    grant(factory,ids,ids['buyer'],['read','attach'])
    with factory.begin() as db:
        db.add(Grant(user_id=ids['buyer'],permission='file.upload',effect='ALLOW',scope={'all':True},fields=['*'],reason='合成',granted_by=ids['admin']))
        db.add(Capability(user_id=ids['buyer'],kind='TOOL',key='prepare_contact_attach',enabled=True))
    sign_in(client,'test_buyer');blob=upload(client).json();_,ctx=start(client,monkeypatch)
    case=link(client,ctx,case,blob)
    assert client.get('/api/files/'+blob['id']+'/content').status_code==200
    with factory.begin() as db:
        db.scalar(select(Grant).where(Grant.user_id==ids['buyer'],Grant.permission=='contact.read')).active=False
    assert client.get('/api/files/'+blob['id']+'/content').status_code==404
    assert client.get('/api/conversations/'+blob['conversation_id']+'/files').json()==[]
    sign_in(client)
    assert client.get('/api/files/'+blob['id']+'/content').status_code==200


def test_run_files_and_query_bound_to_current_conversation(client,data,monkeypatch):
    sign_in(client);first=upload(client).json();other=upload(client).json()
    monkeypatch.setattr(settings(),'llm_enabled',True)
    bad=client.post('/api/runs',json={'prompt':'不要跨会话','conversation_id':first['conversation_id'],'file_ids':[other['id']]})
    assert bad.status_code==403
    r=client.post('/api/runs',json={'prompt':'关联这个原件，待本人确认','conversation_id':first['conversation_id'],'file_ids':[first['id']]})
    assert r.status_code==200,r.text
    ctx=client.post('/internal/runs/claim',headers=worker_headers()).json()['run']
    assert [file['id'] for file in ctx['files']]==[first['id']]
    assert 'object_key' not in str(ctx['files'])
    result=client.post(f"/internal/runs/{ctx['id']}/tools",headers=worker_headers(),json={'epoch':ctx['epoch'],'sequence':0,'key':'query_uploaded_files','arguments':{}})
    assert result.status_code==200 and [f['id'] for f in result.json()['data']]==[first['id']]
    assert client.get('/api/conversations/'+first['conversation_id']+'/runs').json()[0]['files'][0]['id']==first['id']


def test_missing_or_corrupt_object_never_downloads(client,data):
    sign_in(client);blob=upload(client).json()
    with data[1]() as db:
        row=db.get(FileObject,blob['id']);path=object_storage.local_path(row.object_key)
    path.write_bytes(b'corrupt')
    r=client.get('/api/files/'+blob['id']+'/content')
    assert r.status_code==503 and r.json()['error']['code']=='FILE_INTEGRITY'


def test_failed_storage_does_not_publish_metadata(client,data,monkeypatch):
    sign_in(client)
    def fail(*args):raise DomainError('STORAGE_UNAVAILABLE','合成故障',503)
    monkeypatch.setattr(object_storage,'put',fail)
    assert upload(client).status_code==503
    with data[1]() as db:assert db.scalar(select(func.count()).select_from(FileObject))==0


def test_s3_uses_versioned_private_objects_and_verifies_digest(monkeypatch):
    digest=sha256(PDF).hexdigest();key=uuid4().hex+'/'+digest;seen={}
    class FakeS3:
        def get_bucket_versioning(self,**kw):return {'Status':'Enabled'}
        def put_object(self,**kw):seen['put']=kw;return {'VersionId':'version-1'}
        def get_object(self,**kw):seen['get']=kw;return {'Body':BytesIO(PDF)}
    monkeypatch.setattr(settings(),'file_backend','s3');monkeypatch.setattr(settings(),'file_s3_bucket','private-test')
    monkeypatch.setattr(object_storage,'s3_client',lambda:FakeS3())
    saved=object_storage.put(key,PDF,'application/pdf')
    assert saved['storage_version']=='version-1' and seen['put']['IfNoneMatch']=='*' and 'ACL' not in seen['put']
    blob=SimpleNamespace(object_key=key,size=len(PDF),sha256=digest,**saved)
    assert object_storage.read(blob)==PDF and seen['get']['VersionId']=='version-1'
    monkeypatch.setattr(FakeS3,'get_bucket_versioning',lambda self,**kw:{})
    with pytest.raises(DomainError,match='文件存储桶须启用版本保留'):object_storage.put(key,PDF,'application/pdf')


def test_production_local_storage_is_blocked(monkeypatch):
    monkeypatch.setattr(settings(),'environment','production')
    with pytest.raises(DomainError):object_storage.put(uuid4().hex+'/'+sha256(PDF).hexdigest(),PDF,'application/pdf')
