"""Human collaboration API. No endpoint here grants approvals or executes ERP writes."""
from typing import Literal
from decimal import Decimal
from datetime import date
from uuid import UUID
from fastapi import APIRouter, Depends, Query
from pydantic import Field, AwareDatetime, field_validator, model_validator
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
    customer_ref:str=Field(min_length=1,max_length=200)
    customer_name:str=Field(min_length=1,max_length=200)
    mold_number:str=Field(min_length=1,max_length=100)
    product_ref:str=Field(min_length=1,max_length=200)
    application_date:date
    problem_source:Literal['CUSTOMER_CHANGE','DESIGN_ISSUE','ASSEMBLY_ISSUE','MACHINING_ISSUE','PROCUREMENT_ISSUE','QUALITY_ISSUE','TRIAL_ISSUE','OUTSOURCE_DEFECT','COST_REDUCTION','PROCESS_IMPROVEMENT','OTHER']
    current_stage:str=Field(min_length=1,max_length=200)
    change_type:Literal['CHANGE','EXCEPTION','IMPROVEMENT']
    urgency:Literal['NORMAL','URGENT','CRITICAL']

    @field_validator('category')
    @classmethod
    def canonical_category(cls,v):
        # Normalize business data labels, never route a user's conversational intent.
        return {name:key for key,name in CATEGORY_NAMES.items()}.get(v,v)

    @field_validator('title','description','customer_ref','customer_name','mold_number','product_ref','current_stage')
    @classmethod
    def nonblank(cls,v):
        if not v.strip():raise ValueError('内容不能为空')
        return v.strip()

    @model_validator(mode='after')
    def application_not_future(self):
        if self.application_date>now().date():raise ValueError('申请日期不能晚于当前日期')
        return self


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
    affected_type:Literal['DRAWING','MATERIAL','PURCHASE_ORDER','WIP_TASK','SUPPLIER_TASK','PLAN_NODE','CONTRACT','FINANCE','LOGISTICS','OTHER']
    affected_ref:str=Field(min_length=1,max_length=300)
    impact_description:str=Field(min_length=1,max_length=4000)
    planned_action:Literal['CONTINUE','PAUSE','CANCEL','REWORK','REISSUE']
    delivery_impact_days:int=Field(ge=0,le=3650)
    estimated_amount:Decimal|None=Field(default=None,ge=0,max_digits=18,decimal_places=2)
    currency:str|None=Field(default=None,pattern=r'^[A-Z]{3}$')
    source_system:Literal['AGENT','ERP','MANUAL']
    source_ref:str|None=Field(default=None,max_length=300)
    source_as_of:AwareDatetime|None=None

    @field_validator('title','affected_ref','impact_description')
    @classmethod
    def nonblank(cls,v):
        if not v.strip():raise ValueError('事项不能为空')
        return v.strip()

    @model_validator(mode='after')
    def source_and_amount(self):
        if (self.estimated_amount is None)!=(self.currency is None):raise ValueError('预计金额与币种须同时填写')
        if self.source_system=='ERP' and (not (self.source_ref or '').strip() or self.source_as_of is None):
            raise ValueError('ERP影响事实须包含原记录引用和核对时点')
        if self.source_as_of and self.source_as_of>now():raise ValueError('影响事实核对时点不能晚于当前时间')
        return self


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
    actual_completed_at:AwareDatetime
    actual_hours:Decimal=Field(ge=0,max_digits=12,decimal_places=2)
    actual_amount:Decimal|None=Field(default=None,ge=0,max_digits=18,decimal_places=2)
    currency:str|None=Field(default=None,pattern=r'^[A-Z]{3}$')
    execution_evidence:str=Field(min_length=1,max_length=4000)
    source_system:Literal['AGENT','ERP','MANUAL']
    source_ref:str|None=Field(default=None,max_length=300)
    source_as_of:AwareDatetime|None=None

    @field_validator('content','execution_evidence')
    @classmethod
    def nonblank(cls,v):
        if not v.strip():raise ValueError('反馈不能为空')
        return v.strip()

    @model_validator(mode='after')
    def source_and_amount(self):
        if (self.actual_amount is None)!=(self.currency is None):raise ValueError('实际金额与币种须同时填写')
        if self.source_system=='ERP' and (not (self.source_ref or '').strip() or self.source_as_of is None):
            raise ValueError('ERP执行结果须包含原记录引用和核对时点')
        if self.actual_completed_at>now():raise ValueError('实际完成时间不能晚于当前时间')
        if self.source_as_of and self.source_as_of>now():raise ValueError('执行结果核对时点不能晚于当前时间')
        return self


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


def progress_summary(db,c):
    tasks=list(db.scalars(select(m.ContactTask).where(m.ContactTask.case_id==c.id).order_by(m.ContactTask.created_at,m.ContactTask.id)))
    records=list(db.scalars(select(m.ContactRecord).where(m.ContactRecord.case_id==c.id).order_by(m.ContactRecord.created_at,m.ContactRecord.id)))
    counts={state:sum(1 for task in tasks if task.status==state) for state in ('UNASSIGNED','ASSIGNED','RESPONDED','VERIFIED','CANCELLED')}
    active=[task for task in tasks if task.status!='CANCELLED']
    latest_resolution=None
    resolution=db.scalar(select(m.ContactResolution).join(m.BusinessSubject).where(m.ContactResolution.case_id==c.id)
        .order_by(m.BusinessSubject.created_at.desc(),m.BusinessSubject.id.desc()).limit(1))
    if resolution:
        subject=db.get(m.BusinessSubject,resolution.subject_id)
        latest_resolution={'id':subject.id,'number':subject.number,'status':subject.status,'case_revision':resolution.case_revision}
    blockers=[]
    next_actions=[]
    if c.closed_at:
        state='CLOSED'
        next_actions.append('联络单已人工关闭，仅可查看历史过程和附件版本。')
    elif c.mode=='HISTORY':
        state='HISTORY_RECORD'
        offline=sum(1 for record in records if record.kind=='NOTE' and record.detail.get('source')=='OFFLINE')
        if offline==0:
            blockers.append('历史补录尚未登记线下过程记录。')
            next_actions.append('补充线下发生时间、参与人员、内容和原始附件。')
        next_actions.append('历史补录只归档已发生事实，不派发线上事项、不冒充审批、不自动认定最终关闭。')
    elif not active:
        state='DRAFTING'
        blockers.append('尚未明确责任部门和处理事项。')
        next_actions.append('由发起人选择责任部门并创建协作事项。')
    elif counts['UNASSIGNED']:
        state='WAITING_ASSIGNMENT'
        blockers.append(f"{counts['UNASSIGNED']} 个事项尚未分派处理人。")
        next_actions.append('由责任部门负责人或有权发起人分派处理人。')
    elif counts['ASSIGNED']:
        state='WAITING_FEEDBACK'
        blockers.append(f"{counts['ASSIGNED']} 个事项等待处理反馈。")
        next_actions.append('处理人提交执行结果、工时费用和执行依据。')
    elif counts['RESPONDED']:
        state='WAITING_REVIEW'
        blockers.append(f"{counts['RESPONDED']} 个事项已反馈但尚未独立复验。")
        next_actions.append('发起人或指定验收负责人按最新生效方案复验。')
    elif not latest_resolution or latest_resolution['status']!='EFFECTIVE':
        state='WAITING_RESOLUTION'
        blockers.append('尚无已审批生效的最新处理方案。')
        next_actions.append('发起人提交处理方案审批，审批生效后才能作为关闭依据。')
    elif any(task.verified_plan_id!=latest_resolution['id'] for task in active):
        state='REVIEW_STALE'
        blockers.append('存在事项未在最新生效方案下复验合格。')
        next_actions.append('按最新方案重新反馈或复验受影响事项。')
    else:
        state='READY_TO_CLOSE'
        next_actions.append('所有有效事项已在最新生效方案下复验合格，可由发起人或指定验收负责人准备关闭。')
    return {'state':state,'task_counts':counts,'active_task_count':len(active),'latest_resolution':latest_resolution,
            'blockers':blockers,'next_actions':next_actions,
            'limitations':['办理状态由当前联络记录、事项、方案和复验结果派生；不读取或修改 ERP 异常流程。',
                           '线下记录、处理反馈或方案审批通过都不单独等同于整改完成、复验合格或联络单关闭。']}


def serialize(db,c,details=False,user=None):
    creator=db.get(m.User,c.created_by)
    result={'id':c.id,'project_id':c.project_id,'category':c.category,'title':c.title,
            'description':c.description,'mode':c.mode,'revision':c.revision,'created_at':c.created_at,
            'created_by':c.created_by,'creator_name':creator.display_name,
            'customer_ref':c.customer_ref,'customer_name':c.customer_name,'mold_number':c.mold_number,
            'product_ref':c.product_ref,'application_date':c.application_date,'problem_source':c.problem_source,
            'current_stage':c.current_stage,'change_type':c.change_type,'urgency':c.urgency,
            'collaboration_status':'CLOSED' if c.closed_at else 'HISTORY_RECORD' if c.mode=='HISTORY' else 'OPEN',
            'progress_summary':progress_summary(db,c)}
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
                'affected_type':t.affected_type,'affected_ref':t.affected_ref,'impact_description':t.impact_description,
                'planned_action':t.planned_action,'delivery_impact_days':t.delivery_impact_days,
                'estimated_amount':t.estimated_amount,'currency':t.currency,'source_system':t.source_system,
                'source_ref':t.source_ref,'source_as_of':t.source_as_of,'actual_completed_at':t.actual_completed_at,
                'actual_hours':t.actual_hours,'actual_amount':t.actual_amount,'actual_currency':t.actual_currency,
                'execution_evidence':t.execution_evidence,'execution_source_system':t.execution_source_system,
                'execution_source_ref':t.execution_source_ref,'execution_source_as_of':t.execution_source_as_of,
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
    profile=db.get(m.ProjectProfile,c.project_id)
    recipients=[]
    if profile and profile.owner_user_id!=user.id:
        owner=db.get(m.User,profile.owner_user_id)
        if owner and permitted(db,owner,'read',c):recipients.append(owner.id)
    record(db,user,'contact.created',c.id,{'mode':c.mode,'problem_source':c.problem_source,
        'current_stage':c.current_stage,'urgency':c.urgency},recipients)
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
    t=m.ContactTask(case_id=c.id,department_id=g.id,created_by=user.id,**data.model_dump(exclude={'request_key','revision','department_id'}))
    db.add(t);db.flush()
    heads=list(db.scalars(select(m.User).join(m.AssignmentMember,m.AssignmentMember.user_id==m.User.id).where(
        m.AssignmentMember.group_id==g.id,m.AssignmentMember.is_head.is_(True),m.User.active.is_(True))))
    recipients=[u.id for u in heads if permitted(db,u,'read',c) and permitted(db,u,'assign',c)]
    return append(db,user,c,data,'TASK_CREATED',digest,{'task_id':t.id,'title':t.title,
        'department_id':g.id,'department_name':g.name,'department_version':g.version,'eligible_heads':recipients,
        'affected_type':t.affected_type,'affected_ref':t.affected_ref,'planned_action':t.planned_action,
        'impact_description':t.impact_description,'delivery_impact_days':t.delivery_impact_days,
        'estimated_amount':str(t.estimated_amount) if t.estimated_amount is not None else None,'currency':t.currency,
        'source_system':t.source_system,'source_ref':t.source_ref,'source_as_of':t.source_as_of.isoformat() if t.source_as_of else None},recipients=recipients)


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
    if data.actual_completed_at>now():raise DomainError('INVALID_TIME','实际完成时间不能晚于当前时间')
    t.status='RESPONDED';t.response=data.content;t.actual_completed_at=data.actual_completed_at;t.actual_hours=data.actual_hours
    t.actual_amount=data.actual_amount;t.actual_currency=data.currency;t.execution_evidence=data.execution_evidence
    t.execution_source_system=data.source_system;t.execution_source_ref=data.source_ref;t.execution_source_as_of=data.source_as_of
    recipients=[c.created_by] if permitted(db,db.get(m.User,c.created_by),'read',c) else []
    return append(db,user,c,data,'RESPONDED',digest,{'task_id':t.id,'content':data.content,'actual_completed_at':data.actual_completed_at.isoformat(),
        'actual_hours':str(data.actual_hours),'actual_amount':str(data.actual_amount) if data.actual_amount is not None else None,
        'currency':data.currency,'execution_evidence':data.execution_evidence,'source_system':data.source_system,
        'source_ref':data.source_ref,'source_as_of':data.source_as_of.isoformat() if data.source_as_of else None,'is_approval':False},recipients=recipients)


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
