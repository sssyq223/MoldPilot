"""Mold-specific file attachment policy built on the generic host file service."""
from uuid import uuid4
from pydantic import Field, field_validator
from sqlalchemy import select, func

from app.files import *  # noqa: F401,F403
from domain_packs.mold import contacts as c, models as m
from domain_packs.mold.ports.errors import DomainError
class AttachInput(c.Mutation):
    file_id:str=Field(min_length=1,max_length=36)
    title:str=Field(min_length=1,max_length=150)
    previous_id:str|None=Field(default=None,max_length=36,description='替换某个附件时传入其当前版本标识；新增附件为 null')

    @field_validator('title')
    @classmethod
    def nonblank(cls,v):
        if not v.strip():raise ValueError('材料名称不能为空')
        return v.strip()


def validate_attachment(db,user,case,data):
    c.require(db,user,'attach',case)
    blob=uploaded_file(db,user,data.file_id)
    duplicate=db.scalar(select(m.ContactAttachment).join(m.FileObject).where(m.ContactAttachment.case_id==case.id,m.FileObject.sha256==blob.sha256))
    if duplicate:raise DomainError('ATTACHMENT_DUPLICATE','该原件已关联当前联络单，请查看已有版本',409)
    previous=None
    if data.previous_id:
        previous=db.get(m.ContactAttachment,data.previous_id)
        if not previous or previous.case_id!=case.id:raise DomainError('NOT_FOUND','被替换的附件不存在或不属于当前联络单',404)
        latest=db.scalar(select(func.max(m.ContactAttachment.version)).where(m.ContactAttachment.case_id==case.id,m.ContactAttachment.document_id==previous.document_id))
        if latest!=previous.version:raise DomainError('VERSION_CONFLICT','附件已有新版本，请重新核对',409)
    return blob,previous


def attach_preview(db,user,case,data):
    blob,previous=validate_attachment(db,user,case,data)
    return {'材料名称':data.title,'原始文件':blob.filename,'文件大小（字节）':blob.size,'原件摘要':blob.sha256,
            '关联方式':'保存为新版本，保留原版本' if previous else '新增附件','附件版本':previous.version+1 if previous else 1}


def contact_attachment_recipients(db,user,case):
    """Notify active collaboration participants without exposing file content."""
    ids={case.created_by}
    if case.reviewer_id:ids.add(case.reviewer_id)
    tasks=list(db.scalars(select(m.ContactTask).where(m.ContactTask.case_id==case.id,m.ContactTask.status!='CANCELLED')))
    for task in tasks:
        ids.add(task.created_by)
        if task.assignee_id:ids.add(task.assignee_id)
        heads=db.scalars(select(m.User.id).join(m.AssignmentMember,m.AssignmentMember.user_id==m.User.id).where(
            m.AssignmentMember.group_id==task.department_id,m.AssignmentMember.is_head.is_(True),m.User.active.is_(True)))
        ids.update(heads)
    ids.discard(user.id)
    recipients=[]
    for uid in ids:
        person=db.get(m.User,uid) if uid else None
        if person and c.permitted(db,person,'read',case):recipients.append(person.id)
    return sorted(set(recipients))


def attach(db,user,case_id,data):
    case=c.load(db,user,case_id,True);c.require(db,user,'attach',case)
    digest,done=c.replay(db,user,case,data,'ATTACHMENT_ADDED')
    if done:return c.serialize(db,case,True,user)
    blob,previous=validate_attachment(db,user,case,data)
    link=m.ContactAttachment(case_id=case.id,file_id=blob.id,document_id=previous.document_id if previous else str(uuid4()),
        version=previous.version+1 if previous else 1,title=data.title,previous_id=previous.id if previous else None,created_by=user.id)
    db.add(link);db.flush()
    recipients=contact_attachment_recipients(db,user,case)
    return c.append(db,user,case,data,'ATTACHMENT_ADDED',digest,{'attachment_id':link.id,'file_id':blob.id,
        'filename':blob.filename,'title':link.title,'version':link.version,'previous_id':link.previous_id,
        'sha256':blob.sha256,'recipients':recipients},recipients=recipients)


def case_attachments(db,case):
    rows=list(db.execute(select(m.ContactAttachment,m.FileObject).join(m.FileObject).where(m.ContactAttachment.case_id==case.id)
        .order_by(m.ContactAttachment.created_at.desc())))
    latest={}
    for link,_ in rows:latest[link.document_id]=max(latest.get(link.document_id,0),link.version)
    return [{**{k:v for k,v in metadata(blob).items() if k!='conversation_id'},'file_id':blob.id,'id':link.id,'title':link.title,'version':link.version,
             'document_id':link.document_id,'previous_id':link.previous_id,'linked_at':link.created_at,
             'linked_by':db.get(m.User,link.created_by).display_name,'is_current':latest[link.document_id]==link.version} for link,blob in rows]
