"""Private conversation uploads; explicit, human-confirmed business attachment links."""
from datetime import timedelta
from hashlib import sha256
from io import BytesIO
from pathlib import PurePath
from urllib.parse import quote
from uuid import UUID,uuid4
from zipfile import ZipFile,BadZipFile
import unicodedata
from fastapi import APIRouter,Depends,Request,Query
from fastapi.responses import Response
from fastapi.encoders import jsonable_encoder
from starlette.concurrency import run_in_threadpool
from pydantic import Field,field_validator
from sqlalchemy import select,func,text
from . import models as m,authorization as auth,object_storage
from agent_core.domain_pack import component
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


def persist_upload(db,user,filename,data,request_key,conversation_id):
    require_upload(db,user);filename,media_type=validate_file(filename,data)
    digest=sha256(data).hexdigest()
    db.execute(text('SELECT pg_advisory_xact_lock(hashtextextended(:key,0))'),{'key':'upload:'+user.id})
    old=db.scalar(select(m.FileObject).where(m.FileObject.owner_id==user.id,m.FileObject.request_key==str(request_key)))
    if old:
        if old.filename!=filename or old.sha256!=digest or (conversation_id and old.conversation_id!=str(conversation_id)):
            raise DomainError('IDEMPOTENCY_CONFLICT','同一次上传的文件或目标会话已变化',409)
        load(db,user,old.id);return metadata(old)
    used=db.scalar(select(func.coalesce(func.sum(m.FileObject.size),0)).where(m.FileObject.owner_id==user.id,
        m.FileObject.created_at>=now()-timedelta(days=1)))
    if used+len(data)>settings().file_daily_bytes:raise DomainError('UPLOAD_QUOTA','已达到当前账号的每日文件上传额度',429)
    if conversation_id:
        conversation=db.get(m.Conversation,str(conversation_id))
        if not conversation or conversation.user_id!=user.id:raise DomainError('NOT_FOUND','会话不存在或无权访问',404)
    else:
        conversation=m.Conversation(user_id=user.id,title='附件：'+filename[:55]);db.add(conversation);db.flush()
    key=uuid4().hex+'/'+digest
    storage=object_storage.put(key,data,media_type)
    # Object write precedes the transaction commit. A failed DB commit may leave an
    # unreferenced private object, never a visible record pointing to an absent upload.
    blob=m.FileObject(owner_id=user.id,conversation_id=conversation.id,request_key=str(request_key),filename=filename,
        media_type=media_type,size=len(data),sha256=digest,object_key=key,**storage)
    db.add(blob);db.flush();record(db,user,'file.uploaded',blob.id,{'size':blob.size,'sha256':blob.sha256})
    db.commit();return metadata(blob)


@router.post('/api/files')
async def upload(request:Request,filename:str=Query(min_length=1,max_length=200),request_key:UUID=Query(),
                 conversation_id:UUID|None=None,user=Depends(current_user),db=Depends(get_db)):
    require_upload(db,user)
    body=bytearray()
    async for chunk in request.stream():
        if len(body)+len(chunk)>settings().file_max_bytes:raise DomainError('FILE_TOO_LARGE','文件超过允许的上传大小',413)
        body.extend(chunk)
    return await run_in_threadpool(persist_upload,db,user,filename,bytes(body),request_key,conversation_id)


@router.get('/api/conversations/{cid}/files')
def conversation_files(cid:str,user=Depends(current_user),db=Depends(get_db)):
    conversation=db.get(m.Conversation,cid)
    if not conversation or conversation.user_id!=user.id:raise DomainError('NOT_FOUND','会话不存在或无权访问',404)
    return [metadata(blob) for blob in db.scalars(select(m.FileObject).where(m.FileObject.conversation_id==cid)
        .order_by(m.FileObject.created_at.desc()).limit(100)) if readable(db,user,blob)]


@router.get('/api/files/{fid}/content')
def content(fid:str,preview:bool=False,user=Depends(current_user),db=Depends(get_db)):
    blob=load(db,user,fid)
    if preview and blob.media_type not in {'application/pdf','image/png','image/jpeg'}:raise DomainError('PREVIEW_UNSUPPORTED','此格式暂不支持在线预览，请下载原件查看')
    data=object_storage.read(blob)
    record(db,user,'file.previewed' if preview else 'file.downloaded',blob.id);db.commit()
    return Response(data,media_type=blob.media_type,headers={
        'Content-Disposition':('inline' if preview else 'attachment')+"; filename*=UTF-8''"+quote(blob.filename,safe=''),
        'Content-Security-Policy':"sandbox; default-src 'none'",'Cache-Control':'no-store','X-Content-Type-Options':'nosniff'})


def bind_run_files(db,user,run,file_ids):
    for fid in set(file_ids):
        blob=load(db,user,str(fid))
        if blob.owner_id!=user.id or blob.conversation_id!=run.conversation_id:raise DomainError('FILE_CONTEXT_INVALID','附件须属于当前用户和当前会话',403)
        db.add(m.RunFile(run_id=run.id,file_id=blob.id))


def run_files(db,user,run):
    return jsonable_encoder([metadata(blob) for blob in db.scalars(select(m.FileObject).join(m.RunFile).where(m.RunFile.run_id==run.id)) if readable(db,user,blob)])
