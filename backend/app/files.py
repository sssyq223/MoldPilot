"""Private conversation uploads; explicit, human-confirmed business attachment links."""
from datetime import timedelta
from hashlib import sha256
from io import BytesIO
from pathlib import PurePath
from urllib.parse import quote
from uuid import UUID,uuid4,uuid5
from zipfile import ZipFile,BadZipFile
import unicodedata
from fastapi import APIRouter,Depends,Request,Query
from fastapi.responses import Response
from fastapi.encoders import jsonable_encoder
from starlette.concurrency import run_in_threadpool
from starlette.datastructures import UploadFile
from starlette.formparsers import MultiPartParser, MultiPartException
from pydantic import Field,field_validator
from sqlalchemy import select,func,text
from sqlalchemy.orm import sessionmaker
from . import models as m,authorization as auth,object_storage,document_preview
from agent_core.domain_pack import component, manifest
from .db import get_db,now
from .config import settings
from .security import current_user
from .schemas import StrictModel
from .errors import DomainError
from .events import record

router=APIRouter()
TYPES={'.pdf':'application/pdf','.png':'image/png','.jpg':'image/jpeg','.jpeg':'image/jpeg',
       '.docx':'application/vnd.openxmlformats-officedocument.wordprocessingml.document',
       '.xlsx':'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet','.xls':'application/vnd.ms-excel',
       '.csv':'text/csv','.dxf':'application/dxf','.dwg':'application/acad','.prt':'application/octet-stream',
       '.zip':'application/zip'}


def validate_file(filename,data):
    filename=unicodedata.normalize('NFKC',filename).strip()
    if not filename or len(filename)>200 or any(ch in filename for ch in '/\\:') or any(unicodedata.category(ch) in {'Cc','Cf'} for ch in filename):
        raise DomainError('FILE_NAME_INVALID','文件名无效，请使用不含路径和控制字符的名称')
    ext=PurePath(filename).suffix.lower()
    if ext not in TYPES:raise DomainError('FILE_TYPE_UNSUPPORTED','当前支持 PDF、PNG、JPG、DOCX、XLSX、XLS、CSV、DXF、DWG 和 PRT 原件')
    if not data:raise DomainError('FILE_EMPTY','不能上传空文件')
    valid=(ext=='.pdf' and data.startswith(b'%PDF-') or ext=='.png' and data.startswith(b'\x89PNG\r\n\x1a\n')
           or ext in {'.jpg','.jpeg'} and data.startswith(b'\xff\xd8\xff') or ext=='.dxf' and len(data)<=20*1024*1024)
    if ext=='.csv':
        valid=b'\x00' not in data
    if ext=='.xls':
        # Legacy Excel workbooks use the OLE Compound File signature.  Keep
        # validation deliberately structural here; the ERP remains the parser.
        valid=data.startswith(b'\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1')
    if ext=='.dwg':
        valid=len(data)>=64 and data.startswith(b'AC10')
    if ext=='.prt':
        # NX/Creo PRT files have several binary container generations.  The
        # ERP validates the package pair and performs the actual conversion.
        valid=len(data)>=64
    if ext in {'.docx','.xlsx','.zip'}:
        try:
            with ZipFile(BytesIO(data)) as archive:
                info=archive.infolist();names={item.filename for item in info}
                if len(info)>2000 or sum(item.file_size for item in info)>100*1024*1024 or any(
                    item.file_size>max(1024*1024,item.compress_size*200) or item.flag_bits&1 for item in info):
                    raise DomainError('FILE_ARCHIVE_LIMIT','文件解压体积或压缩结构不符合限制')
                if any('vbaproject' in name.lower() or name.startswith('/') or '..' in name.split('/') for name in names):
                    raise DomainError('FILE_ARCHIVE_INVALID','不能上传含宏或非法路径的文档包')
                valid=(ext=='.zip' or '[Content_Types].xml' in names and ('word/document.xml' if ext=='.docx' else 'xl/workbook.xml'))
        except BadZipFile:valid=False
    if not valid:raise DomainError('FILE_TYPE_MISMATCH','文件内容与扩展名不一致')
    return filename,TYPES[ext]


def require_upload(db,user):auth.require(db,user,'file.upload')


def metadata(blob):
    return {'id':blob.id,'filename':blob.filename,'media_type':blob.media_type,'size':blob.size,
            'sha256':blob.sha256,'created_at':blob.created_at,'conversation_id':blob.conversation_id}


def readable(db,user,blob):
    domain_decision = component("file_policy").readable(db, user, blob)
    if domain_decision is not None:
        # A linked business document cannot bypass later domain revocation.
        return domain_decision
    return blob.owner_id==user.id and auth.access(db,user,'file.upload',{}).allowed


def load(db,user,fid):
    blob=db.get(m.FileObject,fid)
    if not blob or not readable(db,user,blob):raise DomainError('NOT_FOUND','文件不存在或无权访问',404)
    return blob


def uploaded_file(db,user,fid):
    blob=load(db,user,fid);require_upload(db,user)
    if blob.owner_id!=user.id:raise DomainError('FORBIDDEN','只能关联自己上传并有权访问的原件',403)
    return blob


def _after_upload(db, user, blobs, request_key):
    callback = getattr(manifest(), 'after_files_uploaded', None)
    if callback:
        callback(db, user, blobs, str(request_key))


def _discard_storage(storage, key):
    object_storage.discard(
        storage.get('backend'), key,
        storage.get('storage_namespace'), storage.get('storage_version'),
    )


def persist_upload(db,user,filename,data,request_key,conversation_id,*,finalize=True):
    require_upload(db,user);filename,media_type=validate_file(filename,data)
    if len(data)>settings().file_max_bytes:
        raise DomainError('FILE_TOO_LARGE','文件超过允许的上传大小',413)
    digest=sha256(data).hexdigest()
    db.execute(text('SELECT pg_advisory_xact_lock(hashtextextended(:key,0))'),{'key':'upload:'+user.id})
    old=db.scalar(select(m.FileObject).where(m.FileObject.owner_id==user.id,m.FileObject.request_key==str(request_key)))
    if old:
        if old.filename!=filename or old.sha256!=digest or (conversation_id and old.conversation_id!=str(conversation_id)):
            raise DomainError('IDEMPOTENCY_CONFLICT','同一次上传的文件或目标会话已变化',409)
        load(db,user,old.id)
        return metadata(old)
    used=db.scalar(select(func.coalesce(func.sum(m.FileObject.size),0)).where(m.FileObject.owner_id==user.id,
        m.FileObject.created_at>=now()-timedelta(days=1)))
    if used+len(data)>settings().file_daily_bytes:raise DomainError('UPLOAD_QUOTA','已达到当前账号的每日文件上传额度',429)
    if conversation_id:
        conversation=db.get(m.Conversation,str(conversation_id))
        if not conversation or conversation.user_id!=user.id:raise DomainError('NOT_FOUND','会话不存在或无权访问',404)
        if conversation.archived:raise DomainError('CONVERSATION_ARCHIVED','归档会话只可查看',409)
    else:
        conversation=m.Conversation(user_id=user.id,title='附件：'+filename[:55]);db.add(conversation);db.flush()
    key=uuid4().hex+'/'+digest
    storage=object_storage.put(key,data,media_type)
    try:
        blob=m.FileObject(owner_id=user.id,conversation_id=conversation.id,request_key=str(request_key),filename=filename,
            media_type=media_type,size=len(data),sha256=digest,object_key=key,**storage)
        db.add(blob);db.flush();record(db,user,'file.uploaded',blob.id,{'size':blob.size,'sha256':blob.sha256})
        if finalize:
            _after_upload(db, user, [blob], request_key)
            db.commit()
        return metadata(blob)
    except Exception:
        _discard_storage(storage, key)
        raise


class _UploadLimit(MultiPartException):
    pass


class _UploadParser(MultiPartParser):
    """在写临时文件之前限制每一部分体积，而不是上传完才检查。"""
    def on_part_begin(self):
        super().on_part_begin()
        self.part_bytes = 0

    def on_part_data(self, data, start, end):
        self.part_bytes += end - start
        if self.part_bytes > settings().file_max_bytes:
            raise _UploadLimit('文件超过允许的上传大小')
        super().on_part_data(data, start, end)


def persist_batch(db, user, items, request_key, conversation_id):
    require_upload(db, user)
    if not 1 <= len(items) <= 10:
        raise DomainError('FILE_BATCH_INVALID', '每次可上传1至10个文件')
    # 先校验整个批次；文件记录、接收记录和作业只在最后一起提交。
    items = [(validate_file(name, body)[0], body) for name, body in items]
    manifest_value = [{'filename': name, 'sha256': sha256(body).hexdigest()} for name, body in items]
    db.execute(text('SELECT pg_advisory_xact_lock(hashtextextended(:key,0))'), {'key': 'upload:'+user.id})
    old = db.scalar(select(m.AuditEvent).where(
        m.AuditEvent.user_id == user.id, m.AuditEvent.action == 'file.batch.uploaded',
        m.AuditEvent.resource_id == str(request_key)))
    if old:
        if old.detail['files'] != manifest_value or (conversation_id and old.detail['conversation_id'] != str(conversation_id)):
            raise DomainError('IDEMPOTENCY_CONFLICT', '同一次上传的文件或目标会话已变化', 409)
        return {'conversation_id': old.detail['conversation_id'],
                'files': [metadata(load(db,user,fid)) for fid in old.detail['file_ids']]}
    uploaded = []
    new_storage = []
    target = conversation_id
    try:
        for index, (name, body) in enumerate(items):
            item_key = uuid5(request_key,str(index))
            existing = db.scalar(select(m.FileObject.id).where(
                m.FileObject.owner_id == user.id,
                m.FileObject.request_key == str(item_key),
            ))
            value = persist_upload(db,user,name,body,item_key,target,finalize=False)
            target = value['conversation_id']
            blob = db.get(m.FileObject,value['id'])
            uploaded.append(blob)
            if existing is None:
                new_storage.append(blob)
        _after_upload(db, user, uploaded, request_key)
        record(db,user,'file.batch.uploaded',str(request_key),{
            'files':manifest_value, 'conversation_id':str(target), 'file_ids':[blob.id for blob in uploaded]})
        db.commit()
        return {'conversation_id':str(target), 'files':[metadata(blob) for blob in uploaded]}
    except Exception:
        for blob in new_storage:
            _discard_storage({
                'backend': blob.backend,
                'storage_namespace': blob.storage_namespace,
                'storage_version': blob.storage_version,
            }, blob.object_key)
        raise


def _persist_upload_in_worker(user_id, bind, filename, data, request_key, conversation_id):
    worker_session = sessionmaker(bind=bind, expire_on_commit=False)
    with worker_session() as db:
        user = db.get(m.User, str(user_id))
        if not user:
            raise DomainError('NOT_FOUND', '用户不存在或已停用', 404)
        return persist_upload(db, user, filename, data, request_key, conversation_id)


def _persist_batch_in_worker(user_id, bind, items, request_key, conversation_id):
    worker_session = sessionmaker(bind=bind, expire_on_commit=False)
    with worker_session() as db:
        user = db.get(m.User, str(user_id))
        if not user:
            raise DomainError('NOT_FOUND', '用户不存在或已停用', 404)
        return persist_batch(db, user, items, request_key, conversation_id)


@router.post('/api/files/batch')
async def upload_batch(request:Request,request_key:UUID=Query(),conversation_id:UUID|None=None,
                       user=Depends(current_user),db=Depends(get_db)):
    require_upload(db,user)
    async def limited_stream():
        received = 0
        maximum = min(settings().file_daily_bytes, settings().file_max_bytes * 10) + 65536
        async for chunk in request.stream():
            received += len(chunk)
            if received > maximum:
                raise _UploadLimit('上传批次超过允许的大小')
            yield chunk
    parser = _UploadParser(request.headers, limited_stream(), max_files=10, max_fields=0)
    try:
        form = await parser.parse()
    except _UploadLimit as error:
        raise DomainError('FILE_TOO_LARGE',str(error),413) from None
    except MultiPartException:
        raise DomainError('FILE_BATCH_INVALID','上传批次格式无效，最多10个文件') from None
    try:
        files = form.getlist('files')
        if set(form.keys()) != {'files'} or not files or any(not isinstance(f,UploadFile) for f in files):
            raise DomainError('FILE_BATCH_INVALID','请选择待上传文件')
        items = [(f.filename or '', await f.read()) for f in files]
        return await run_in_threadpool(
            _persist_batch_in_worker, user.id, db.get_bind(), items, request_key, conversation_id,
        )
    finally:
        await form.close()


@router.post('/api/files')
async def upload(request:Request,filename:str=Query(min_length=1,max_length=200),request_key:UUID=Query(),
                 conversation_id:UUID|None=None,user=Depends(current_user),db=Depends(get_db)):
    require_upload(db,user)
    body=bytearray()
    async for chunk in request.stream():
        if len(body)+len(chunk)>settings().file_max_bytes:raise DomainError('FILE_TOO_LARGE','文件超过允许的上传大小',413)
        body.extend(chunk)
    return await run_in_threadpool(
        _persist_upload_in_worker, user.id, db.get_bind(), filename, bytes(body), request_key, conversation_id,
    )


@router.get('/api/conversations/{cid}/files')
def conversation_files(cid:str,user=Depends(current_user),db=Depends(get_db)):
    conversation=db.get(m.Conversation,cid)
    if not conversation or conversation.user_id!=user.id:raise DomainError('NOT_FOUND','会话不存在或无权访问',404)
    return [metadata(blob) for blob in db.scalars(select(m.FileObject).where(m.FileObject.conversation_id==cid)
        .order_by(m.FileObject.created_at.desc()).limit(100)) if readable(db,user,blob)]


@router.get('/api/files/{fid}/content')
def content(fid:str,preview:bool=False,render:str|None=Query(default=None),user=Depends(current_user),db=Depends(get_db)):
    blob=load(db,user,fid)
    if preview and blob.media_type not in {'application/pdf','image/png','image/jpeg',
            'application/vnd.openxmlformats-officedocument.wordprocessingml.document'}:
        raise DomainError('PREVIEW_UNSUPPORTED','此格式暂不支持在线预览，请下载原件查看')
    data=object_storage.read(blob);media_type=blob.media_type;filename=blob.filename
    if render:
        if not preview or render!='pdf' or blob.media_type!='application/vnd.openxmlformats-officedocument.wordprocessingml.document':
            raise DomainError('PREVIEW_RENDER_UNSUPPORTED','当前文件不支持此在线预览格式')
        data=document_preview.docx_to_pdf(data,blob.sha256);media_type='application/pdf';filename=PurePath(blob.filename).stem+'.pdf'
    record(db,user,'file.previewed' if preview else 'file.downloaded',blob.id);db.commit()
    return Response(data,media_type=media_type,headers={
        'Content-Disposition':('inline' if preview else 'attachment')+"; filename*=UTF-8''"+quote(filename,safe=''),
        'Content-Security-Policy':"sandbox; default-src 'none'",'Cache-Control':'no-store','X-Content-Type-Options':'nosniff'})


def bind_run_files(db,user,run,file_ids):
    for fid in set(file_ids):
        blob=load(db,user,str(fid))
        if blob.owner_id!=user.id or blob.conversation_id!=run.conversation_id:raise DomainError('FILE_CONTEXT_INVALID','附件须属于当前用户和当前会话',403)
        db.add(m.RunFile(run_id=run.id,file_id=blob.id))


def run_files(db,user,run):
    return jsonable_encoder([metadata(blob) for blob in db.scalars(select(m.FileObject).join(m.RunFile).where(m.RunFile.run_id==run.id)) if readable(db,user,blob)])
