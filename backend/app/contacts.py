"""Human collaboration API. No endpoint here grants approvals or executes ERP writes."""
from typing import Literal
from uuid import UUID
from fastapi import APIRouter, Depends, Query
from pydantic import Field, AwareDatetime, field_validator
from sqlalchemy import select, text
from . import models as m, authorization as auth
from .schemas import StrictModel
from .db import get_db, now
from .security import current_user
from .bpm import content_hash
from .events import record
from .errors import DomainError

router=APIRouter(prefix='/api/contacts')
CATEGORY_NAMES={'hardware':'五金','raw_material':'原材','outsource':'委外','auxiliary':'辅材',
                'office_supply':'办公用品','trial_material':'试模料'}


class CreateInput(StrictModel):
    request_key:UUID
    project_id:str=Field(min_length=1,max_length=36)
    category:str|None=Field(default=None,min_length=1,max_length=60,
        description='责任域标识：hardware 五金，raw_material 原材，outsource 委外，auxiliary 辅材，office_supply 办公用品，trial_material 试模料；未指定为 null。')
    title:str=Field(min_length=1,max_length=150)
    description:str=Field(min_length=1,max_length=10000)
    mode:Literal['HISTORY','ONLINE']

    @field_validator('category')
    @classmethod
    def canonical_category(cls,v):
        # Normalize business data labels, never route a user's conversational intent.
        return {name:key for key,name in CATEGORY_NAMES.items()}.get(v,v)

    @field_validator('title','description')
    @classmethod
    def nonblank(cls,v):
        if not v.strip():raise ValueError('内容不能为空')
        return v.strip()


class Mutation(StrictModel):
    request_key:UUID
    revision:int=Field(ge=1)


class NoteInput(Mutation):
    source:Literal['OWN','OFFLINE']
    occurred_at:AwareDatetime
    participants:str=Field(default='',max_length=1000)
    content:str=Field(min_length=1,max_length=10000)

    @field_validator('content')
    @classmethod
    def nonblank(cls,v):
        if not v.strip():raise ValueError('内容不能为空')
        return v.strip()


class TaskInput(Mutation):
    department_id:str=Field(min_length=1,max_length=36)
    title:str=Field(min_length=1,max_length=150)

    @field_validator('title')
    @classmethod
    def nonblank(cls,v):
        if not v.strip():raise ValueError('事项不能为空')
        return v.strip()


class AssignInput(Mutation):
    assignee_id:str=Field(min_length=1,max_length=36)
    reason:str=Field(min_length=1,max_length=1000)

    @field_validator('reason')
    @classmethod
    def nonblank(cls,v):
        if not v.strip():raise ValueError('分派说明不能为空')
        return v.strip()


class ResponseInput(Mutation):
    content:str=Field(min_length=1,max_length=10000)

    @field_validator('content')
    @classmethod
    def nonblank(cls,v):
        if not v.strip():raise ValueError('反馈不能为空')
        return v.strip()


def scope(c):return {'project_id':c.project_id,'category':c.category}


def permitted(db,user,action,c):
    access=auth.access(db,user,'contact.'+action,scope(c))
    return access.allowed and '*' in access.fields


def require(db,user,action,c):
    if not permitted(db,user,action,c):raise DomainError('FORBIDDEN','资源不存在或无权访问',403)


def load(db,user,cid,write=False):
    q=select(m.ContactCase).where(m.ContactCase.id==cid)
    c=db.scalar(q.with_for_update() if write else q)
    if not c:raise DomainError('NOT_FOUND','联络单不存在或无权访问',404)
    require(db,user,'read',c)
    return c


def department(db,did):
    g=db.scalar(select(m.AssignmentGroup).where(m.AssignmentGroup.id==did).with_for_update(read=True))
    if not g or not g.active or g.kind!='DEPARTMENT':raise DomainError('DEPARTMENT_UNAVAILABLE','责任部门不存在或已停用')
    return g


def is_head(db,user,g):
    member=db.get(m.AssignmentMember,(g.id,user.id))
    return bool(member and member.is_head)


def assignee_eligible(db,person,g,c):
    return bool(person and person.active and db.get(m.AssignmentMember,(g.id,person.id))
                and permitted(db,person,'read',c) and permitted(db,person,'respond',c))


def serialize(db,c,details=False,user=None):
    creator=db.get(m.User,c.created_by)
    result={'id':c.id,'project_id':c.project_id,'category':c.category,'title':c.title,
            'description':c.description,'mode':c.mode,'revision':c.revision,'created_at':c.created_at,
            'created_by':c.created_by,'creator_name':creator.display_name,
            'collaboration_status':'CLOSED' if c.closed_at else 'HISTORY_RECORD' if c.mode=='HISTORY' else 'OPEN'}
    if details:
        from .contact_lifecycle import context,record_detail
        if user:result.update(context(db,user,c))
        from .files import case_attachments
        result['attachments']=case_attachments(db,c)
        result['can_coordinate']=bool(user and user.id==c.created_by and c.mode=='ONLINE' and not c.closed_at and permitted(db,user,'coordinate',c))
        result['can_record']=bool(user and not c.closed_at and permitted(db,user,'record',c))
        result['tasks']=[]
        for t in db.scalars(select(m.ContactTask).where(m.ContactTask.case_id==c.id).order_by(m.ContactTask.created_at,m.ContactTask.id)):
            g=db.get(m.AssignmentGroup,t.department_id);person=db.get(m.User,t.assignee_id) if t.assignee_id else None
            result['tasks'].append({'id':t.id,'title':t.title,'department_id':g.id,'department_name':g.name,
                'department_active':g.active,'assignee_id':t.assignee_id,'assignee_name':person.display_name if person else None,
                'status':t.status,'response':t.response,'verified_plan_id':t.verified_plan_id,
                'can_assign':bool(user and not c.closed_at and g.active and t.status in {'UNASSIGNED','ASSIGNED'} and permitted(db,user,'assign',c)
                    and (user.id==c.created_by or is_head(db,user,g))),
                'can_respond':bool(user and not c.closed_at and g.active and t.status=='ASSIGNED' and t.assignee_id==user.id
                    and assignee_eligible(db,user,g,c))})
        result['records']=[{'id':r.id,'kind':r.kind,'author_id':r.author_id,
            'author_name':db.get(m.User,r.author_id).display_name,'occurred_at':r.occurred_at,
            'recorded_at':r.created_at,'detail':record_detail(db,user,r) if user else {}} for r in db.scalars(select(m.ContactRecord).where(
                m.ContactRecord.case_id==c.id).order_by(m.ContactRecord.created_at,m.ContactRecord.id))]
    return result


def replay(db,user,c,data,action):
    digest=content_hash({'action':action,'input':data.model_dump(mode='json')})
    old=db.scalar(select(m.ContactRecord).where(m.ContactRecord.case_id==c.id,m.ContactRecord.author_id==user.id,
        m.ContactRecord.request_key==str(data.request_key)))
    if old:
        if old.request_hash!=digest:raise DomainError('IDEMPOTENCY_CONFLICT','同一次操作的内容已变化，请重新确认',409)
        return digest,True
    from .contact_lifecycle import ensure_open
    ensure_open(c)
    if data.revision!=c.revision:raise DomainError('VERSION_CONFLICT','联络单已更新，请刷新后重新核对',409)
    return digest,False


def append(db,user,c,data,kind,digest,detail,occurred_at=None,recipients=None):
    c.revision+=1
    db.add(m.ContactRecord(case_id=c.id,author_id=user.id,request_key=str(data.request_key),request_hash=digest,
        kind=kind,occurred_at=occurred_at or now(),detail=detail))
    record(db,user,'contact.'+kind.lower(),c.id,{'revision':c.revision},recipients)
    db.flush()
    return serialize(db,c,True,user)


@router.get('')
def list_cases(offset:int=Query(0,ge=0),limit:int=Query(50,ge=1,le=100),user=Depends(current_user),db=Depends(get_db)):
    q=select(m.ContactCase).where(auth.predicate(db,user,'contact.read',{
        'project_id':m.ContactCase.project_id,'category':m.ContactCase.category})).order_by(m.ContactCase.created_at.desc(),m.ContactCase.id).offset(offset).limit(limit)
    return [serialize(db,c) for c in db.scalars(q) if permitted(db,user,'read',c)]


def create(data:CreateInput,user=Depends(current_user),db=Depends(get_db)):
    c=m.ContactCase(**data.model_dump(exclude={'request_key'}),created_by=user.id,request_key=str(data.request_key),request_hash=content_hash(data.model_dump(mode='json')))
    require(db,user,'create',c);require(db,user,'read',c)
    if not db.get(m.Project,data.project_id):raise DomainError('NOT_FOUND','项目不存在',404)
    # Serialize retries for this actor/request key, including concurrent creates.
    db.execute(text('SELECT pg_advisory_xact_lock(hashtextextended(:key,0))'),{'key':'contact:'+user.id+str(data.request_key)})
    old=db.scalar(select(m.ContactCase).where(m.ContactCase.created_by==user.id,m.ContactCase.request_key==str(data.request_key)))
    if old:
        require(db,user,'read',old)
        if old.request_hash!=c.request_hash:raise DomainError('IDEMPOTENCY_CONFLICT','同一次创建的内容已变化',409)
        return serialize(db,old,True,user)
    db.add(c);db.flush()
    record(db,user,'contact.created',c.id,{'mode':c.mode})
    db.flush();return serialize(db,c,True,user)


@router.get('/{cid}')
def detail(cid:str,user=Depends(current_user),db=Depends(get_db)):
    return serialize(db,load(db,user,cid),True,user)


@router.get('/{cid}/departments')
def departments(cid:str,user=Depends(current_user),db=Depends(get_db)):
    c=load(db,user,cid)
    require(db,user,'coordinate',c)
    if user.id!=c.created_by:raise DomainError('FORBIDDEN','由发起人组织责任部门',403)
    return [{'id':g.id,'name':g.name} for g in db.scalars(select(m.AssignmentGroup).where(
        m.AssignmentGroup.kind=='DEPARTMENT',m.AssignmentGroup.active.is_(True)).order_by(m.AssignmentGroup.name))]


def add_note(cid:str,data:NoteInput,user=Depends(current_user),db=Depends(get_db)):
    c=load(db,user,cid,True);require(db,user,'record',c)
    digest,done=replay(db,user,c,data,'NOTE')
    if done:return serialize(db,c,True,user)
    if data.occurred_at>now():raise DomainError('INVALID_TIME','发生时间不能晚于当前时间')
    if data.source=='OFFLINE' and not data.participants.strip():raise DomainError('PARTICIPANTS_REQUIRED','线下记录需说明实际参与人')
    if c.mode=='HISTORY' and data.source!='OFFLINE':raise DomainError('HISTORY_SOURCE','历史补录应明确线下来源')
    return append(db,user,c,data,'NOTE',digest,{'source':data.source,'participants':data.participants,
        'content':data.content,'is_approval':False},data.occurred_at)


def add_task(cid:str,data:TaskInput,user=Depends(current_user),db=Depends(get_db)):
    c=load(db,user,cid,True);require(db,user,'coordinate',c)
    if user.id!=c.created_by:raise DomainError('FORBIDDEN','由发起人组织协作事项',403)
    if c.mode!='ONLINE':raise DomainError('HISTORY_NO_DISPATCH','历史补录不能派发线上任务',409)
    digest,done=replay(db,user,c,data,'TASK_CREATED')
    if done:return serialize(db,c,True,user)
    g=department(db,data.department_id)
    t=m.ContactTask(case_id=c.id,department_id=g.id,title=data.title,created_by=user.id)
    db.add(t);db.flush()
    heads=list(db.scalars(select(m.User).join(m.AssignmentMember,m.AssignmentMember.user_id==m.User.id).where(
        m.AssignmentMember.group_id==g.id,m.AssignmentMember.is_head.is_(True),m.User.active.is_(True))))
    recipients=[u.id for u in heads if permitted(db,u,'read',c) and permitted(db,u,'assign',c)]
    return append(db,user,c,data,'TASK_CREATED',digest,{'task_id':t.id,'title':t.title,
        'department_id':g.id,'department_name':g.name,'department_version':g.version,'eligible_heads':recipients},recipients=recipients)


def task_and_assigner(db,user,c,tid):
    t=db.scalar(select(m.ContactTask).where(m.ContactTask.id==tid,m.ContactTask.case_id==c.id))
    if not t:raise DomainError('NOT_FOUND','协作事项不存在',404)
    require(db,user,'assign',c);g=department(db,t.department_id)
    if user.id!=c.created_by and not is_head(db,user,g):raise DomainError('FORBIDDEN','须由责任部门负责人或有权限的发起人分派',403)
    return t,g


@router.get('/{cid}/tasks/{tid}/candidates')
def candidates(cid:str,tid:str,user=Depends(current_user),db=Depends(get_db)):
    c=load(db,user,cid);t,g=task_and_assigner(db,user,c,tid)
    people=db.scalars(select(m.User).join(m.AssignmentMember,m.AssignmentMember.user_id==m.User.id).where(
        m.AssignmentMember.group_id==g.id,m.User.active.is_(True)).order_by(m.User.display_name))
    return [{'id':u.id,'name':u.display_name} for u in people if assignee_eligible(db,u,g,c)]


def assign(cid:str,tid:str,data:AssignInput,user=Depends(current_user),db=Depends(get_db)):
    c=load(db,user,cid,True);t,g=task_and_assigner(db,user,c,tid)
    digest,done=replay(db,user,c,data,'ASSIGN:'+tid)
    if done:return serialize(db,c,True,user)
    if t.status not in {'UNASSIGNED','ASSIGNED'}:raise DomainError('TASK_FINISHED','已有反馈不能改派，请另建协作事项',409)
    person=db.scalar(select(m.User).where(m.User.id==data.assignee_id).with_for_update(read=True))
    if not assignee_eligible(db,person,g,c):raise DomainError('ASSIGNEE_UNAVAILABLE','处理人须为该部门有效成员并具有资料读取和反馈权限',403)
    before=t.assignee_id;t.assignee_id=person.id;t.status='ASSIGNED'
    return append(db,user,c,data,'ASSIGNED',digest,{'task_id':t.id,'previous_assignee_id':before,
        'assignee_id':person.id,'assignee_name':person.display_name,'department_version':g.version,'reason':data.reason},recipients=[person.id])


def respond(cid:str,tid:str,data:ResponseInput,user=Depends(current_user),db=Depends(get_db)):
    c=load(db,user,cid,True);require(db,user,'respond',c)
    t=db.scalar(select(m.ContactTask).where(m.ContactTask.id==tid,m.ContactTask.case_id==c.id))
    if not t or t.assignee_id!=user.id:raise DomainError('FORBIDDEN','只能由当前处理人提交反馈',403)
    g=department(db,t.department_id)
    if not assignee_eligible(db,user,g,c):raise DomainError('ASSIGNEE_UNAVAILABLE','当前成员或办理权限已变化',403)
    digest,done=replay(db,user,c,data,'RESPOND:'+tid)
    if done:return serialize(db,c,True,user)
    if t.status!='ASSIGNED':raise DomainError('TASK_FINISHED','该事项已有反馈，不能覆盖',409)
    t.status='RESPONDED';t.response=data.content
    recipients=[c.created_by] if permitted(db,db.get(m.User,c.created_by),'read',c) else []
    return append(db,user,c,data,'RESPONDED',digest,{'task_id':t.id,'content':data.content,'is_approval':False},recipients=recipients)


# Transaction ownership stays at the HTTP/confirmation boundary. The same services
# can be used by human confirmations without prematurely committing a receipt.
@router.post('')
def create_endpoint(data:CreateInput,user=Depends(current_user),db=Depends(get_db)):
    result=create(data,user,db);db.commit();return result

@router.post('/{cid}/records')
def note_endpoint(cid:str,data:NoteInput,user=Depends(current_user),db=Depends(get_db)):
    result=add_note(cid,data,user,db);db.commit();return result

@router.post('/{cid}/tasks')
def task_endpoint(cid:str,data:TaskInput,user=Depends(current_user),db=Depends(get_db)):
    result=add_task(cid,data,user,db);db.commit();return result

@router.post('/{cid}/tasks/{tid}/assign')
def assign_endpoint(cid:str,tid:str,data:AssignInput,user=Depends(current_user),db=Depends(get_db)):
    result=assign(cid,tid,data,user,db);db.commit();return result

@router.post('/{cid}/tasks/{tid}/respond')
def respond_endpoint(cid:str,tid:str,data:ResponseInput,user=Depends(current_user),db=Depends(get_db)):
    result=respond(cid,tid,data,user,db);db.commit();return result
