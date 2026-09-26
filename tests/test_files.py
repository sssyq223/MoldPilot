from io import BytesIO
from types import SimpleNamespace
from uuid import uuid4
from zipfile import ZipFile
from hashlib import sha256
import pytest
from sqlalchemy import select,func,text
from sqlalchemy.exc import DBAPIError
from app.config import settings
from app.models import FileObject,ContactAttachment,ContactCase,Grant,Capability,RunFile,Outbox,Notification,AuditEvent
from app import document_preview, object_storage, models as m, files as file_routes
from app.errors import DomainError
from conftest import sign_in
from test_contacts import create,grant,add_task,operation
from test_bpm_assignments import group
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


def test_upload_worker_receives_scalar_user_context_not_request_session(client, data, monkeypatch):
    sign_in(client)
    captured = {}

    async def fake_run_in_threadpool(function, *args):
        captured['function'] = function
        captured['args'] = args
        return {'id': 'file-1', 'conversation_id': 'conversation-1', 'filename': '合成材料.pdf'}

    monkeypatch.setattr(file_routes, 'run_in_threadpool', fake_run_in_threadpool)
    response = upload(client)

    assert response.status_code == 200
    assert captured['args'][0] == data[0]['admin']
    assert not hasattr(captured['args'][0], 'execute')


def test_batch_failure_removes_objects_after_db_rollback(data, monkeypatch, tmp_path):
    monkeypatch.setattr(settings(), 'file_local_root', str(tmp_path / 'objects'))
    monkeypatch.setattr(file_routes, '_after_upload', lambda *args: (_ for _ in ()).throw(RuntimeError('callback failed')))
    with pytest.raises(RuntimeError, match='callback failed'):
        with data[1].begin() as db:
            user = db.get(m.User, data[0]['admin'])
            file_routes.persist_batch(
                db, user,
                [('one.pdf', PDF), ('two.pdf', PDF + b'2')],
                uuid4(), None,
            )
    assert not list((tmp_path / 'objects').rglob('*'))


def test_upload_private_original_retry_and_download(client,data):
    sign_in(client);key=str(uuid4());r=upload(client,key=key);assert r.status_code==200,r.text
    blob=r.json();assert blob['sha256']==sha256(PDF).hexdigest()
    assert 'object_key' not in blob and 'storage_namespace' not in blob
    assert upload(client,key=key).json()['id']==blob['id']
    assert upload(client,PDF+b'changed',key=key).status_code==409
    r=client.get('/api/files/'+blob['id']+'/content');assert r.content==PDF
    assert r.headers['content-disposition'].startswith('attachment;') and r.headers['x-content-type-options']=='nosniff'
    preview=client.get('/api/files/'+blob['id']+'/content?preview=true')
    assert preview.status_code==200 and preview.content==PDF
    assert preview.headers['content-disposition'].startswith('inline;')
    sign_in(client,'test_buyer')
    assert client.get('/api/files/'+blob['id']+'/content').status_code==404
    assert client.get('/api/conversations/'+blob['conversation_id']+'/files').status_code==404
    assert upload(client).status_code==403
    with data[1]() as db:assert db.scalar(select(func.count()).select_from(FileObject))==1


def test_upload_accepts_csv_attachment(client):
    sign_in(client)
    content='物料编号,数量\nA-001,12\n'.encode('utf-8')
    response=upload(client,content,'物料清单.csv')
    assert response.status_code==200,response.text
    blob=response.json()
    assert blob['filename']=='物料清单.csv' and blob['media_type']=='text/csv'
    assert client.get('/api/files/'+blob['id']+'/content').content==content


def test_docx_attachment_can_be_previewed_inline(client):
    sign_in(client)
    package=BytesIO()
    with ZipFile(package,'w') as archive:
        archive.writestr('[Content_Types].xml','<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types"/>')
        archive.writestr('word/document.xml','<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"/>')
    content=package.getvalue()
    response=upload(client,content,'工程变更联络单.docx')
    assert response.status_code==200,response.text
    preview=client.get('/api/files/'+response.json()['id']+'/content?preview=true')
    assert preview.status_code==200 and preview.content==content
    assert preview.headers['content-disposition'].startswith('inline;')


def test_docx_attachment_can_be_rendered_as_pdf(client,monkeypatch):
    sign_in(client)
    package=BytesIO()
    with ZipFile(package,'w') as archive:
        archive.writestr('[Content_Types].xml','<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types"/>')
        archive.writestr('word/document.xml','<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"/>')
    response=upload(client,package.getvalue(),'工程变更联络单.docx')
    monkeypatch.setattr(document_preview,'docx_to_pdf',lambda _data,_digest=None:PDF)
    preview=client.get('/api/files/'+response.json()['id']+'/content?preview=true&render=pdf')
    assert preview.status_code==200 and preview.content==PDF
    assert preview.headers['content-type'].startswith('application/pdf')
    assert preview.headers['content-disposition'].endswith('.pdf')


def test_docx_pdf_preview_reuses_digest_cache(tmp_path,monkeypatch):
    monkeypatch.setattr(settings(),'file_local_root',str(tmp_path/'objects'))
    calls=[]
    def convert(_source,target,_work):
        calls.append(target);target.write_bytes(PDF)
    monkeypatch.setattr(document_preview,'_word_to_pdf',convert)
    monkeypatch.setattr(document_preview,'_libreoffice_to_pdf',convert)
    digest=sha256(b'docx-source').hexdigest()
    assert document_preview.docx_to_pdf(b'docx-source',digest)==PDF
    assert document_preview.docx_to_pdf(b'docx-source',digest)==PDF
    assert len(calls)==1


def test_upload_accepts_legacy_xls_and_standard_hardware_sources(client):
    sign_in(client)
    legacy=b'\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1'+b'\x00'*504
    response=upload(client,legacy,'设计清单.xls')
    assert response.status_code==200,response.text
    assert response.json()['media_type']=='application/vnd.ms-excel'

    dwg=b'AC1032'+b'\x00'*128
    response=upload(client,dwg,'R-BZ-001.dwg')
    assert response.status_code==200,response.text
    assert response.json()['media_type']=='application/acad'

    response=upload(client,b'NX-PRT'+b'\x00'*128,'R-BZ-001.prt')
    assert response.status_code==200,response.text
    assert response.json()['media_type']=='application/octet-stream'


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


def test_attachment_link_notifies_collaboration_participants(client,data,monkeypatch):
    from app.message_worker import deliver
    ids,factory=data;sign_in(client)
    g=group(client,[ids['buyer'],ids['reviewer']],kind='DEPARTMENT',name='附件责任部门',heads=[ids['reviewer']])
    grant(factory,ids,ids['buyer'],['read','respond'])
    grant(factory,ids,ids['reviewer'],['read','assign'])
    case,_=create(client,ids);case=add_task(client,case,g)
    tid=case['tasks'][0]['id']
    case=client.post(f"/api/contacts/{case['id']}/tasks/{tid}/assign",
        json=operation(case,assignee_id=ids['buyer'],reason='附件核对通知验证')).json()
    blob=upload(client).json();_,ctx=start(client,monkeypatch,'admin')
    case=link(client,ctx,case,blob)
    with factory() as db:
        event=db.scalar(select(Outbox).where(Outbox.kind=='contact.attachment_added',Outbox.resource_id==case['id']))
        assert event and set(event.payload['recipients'])=={ids['buyer'],ids['reviewer']}
        event_id=event.id
        audit=db.scalar(select(AuditEvent).where(AuditEvent.action=='contact.attachment_added',AuditEvent.resource_id==case['id']))
        assert audit.detail['revision']==case['revision']
        assert audit.detail['detail']['filename']=='合成材料.pdf'
        assert audit.detail['detail']['sha256']==blob['sha256']
        assert set(audit.detail['detail']['recipients'])=={ids['buyer'],ids['reviewer']}
    assert deliver(factory,event_id)=='DELIVERED'
    with factory() as db:
        delivered=set(db.scalars(select(Notification.user_id).where(Notification.event_id==event_id)))
        assert delivered=={ids['buyer'],ids['reviewer']}
        assert db.scalar(select(Notification.user_id).where(Notification.event_id==event_id,Notification.user_id==ids['admin'])) is None


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


def test_uploader_cannot_bypass_sales_contract_read_revocation(client, data):
    ids, factory = data
    with factory.begin() as db:
        admin = db.get(m.User, ids['admin'])
        buyer = db.get(m.User, ids['buyer'])
        db.add(Grant(user_id=buyer.id, permission='file.upload', effect='ALLOW', scope={'all': True},
            fields=['*'], reason='contract upload', granted_by=admin.id))
        db.add(Grant(user_id=buyer.id, permission='sales_contract.read', effect='ALLOW',
            scope={'project_id': [ids['project']]}, fields=['*'], reason='contract read', granted_by=admin.id))
    sign_in(client, 'test_buyer')
    blob = upload(client, PDF, '待撤权合同.pdf').json()
    with factory.begin() as db:
        subject = m.BusinessSubject(kind='sales_contract', number='SC-FILE-REVOCATION',
            project_id=ids['project'], created_by=ids['admin'], status='EFFECTIVE')
        db.add(subject); db.flush()
        intake_file = db.scalar(select(m.DocumentIntakeFile).where(m.DocumentIntakeFile.file_id==blob['id']))
        intake_file.confirmed_type='SALES_CONTRACT'
        intake_file.confirmed_role='MAIN'
        db.get(m.DocumentIntake,intake_file.intake_id).status='CONTRACT_DRAFT_CREATED'
        db.flush()
        db.add(m.ContractAttachment(contract_subject_id=subject.id, file_id=blob['id'],
            document_id=str(uuid4()), version=1, title=blob['filename'], source_kind='ELECTRONIC',
            previous_id=None, uploaded_by=ids['admin'], intake_file_id=intake_file.id, role='MAIN'))
    assert client.get(f"/api/files/{blob['id']}/content").status_code == 200
    with factory.begin() as db:
        db.query(Grant).filter(Grant.user_id == ids['buyer'],
            Grant.permission == 'sales_contract.read').delete()
    assert client.get(f"/api/files/{blob['id']}/content").status_code == 404


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
