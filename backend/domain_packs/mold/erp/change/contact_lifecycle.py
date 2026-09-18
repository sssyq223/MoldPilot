"""Human-led resolution: configured BPM -> feedback -> verification -> closure."""
from typing import Literal
from pydantic import Field, field_validator
from sqlalchemy import select
from domain_packs.mold import contacts as c, models as m, authorization as auth
from domain_packs.mold.ports.db import now
from domain_packs.mold.ports.errors import DomainError


class ResolutionInput(c.Mutation):
    definition_id:str=Field(min_length=1,max_length=36)
    solution:str=Field(min_length=1,max_length=10000)
    customer_due_affected:bool
    customer_evidence:str|None=Field(default=None,max_length=4000)

    @field_validator('solution')
    @classmethod
    def nonblank(cls,v):
        if not v.strip():raise ValueError('方案内容不能为空')
        return v.strip()


class ReviewInput(c.Mutation):
    decision:Literal['PASS','REWORK']
    evidence:str=Field(min_length=1,max_length=4000)

    @field_validator('evidence')
    @classmethod
    def nonblank(cls,v):
        if not v.strip():raise ValueError('核对依据不能为空')
        return v.strip()


class CloseInput(c.Mutation):
    evidence:str=Field(min_length=1,max_length=4000)

    @field_validator('evidence')
    @classmethod
    def nonblank(cls,v):
        if not v.strip():raise ValueError('操作依据不能为空')
        return v.strip()


class ReviewerInput(CloseInput):
    reviewer_id:str=Field(min_length=1,max_length=36)


SPECS={'resolution':(ResolutionInput,'plan','提交联络单处理方案审批'),
       'review':(ReviewInput,'review','复验联络处理结果'),
       'close':(CloseInput,'close','关闭工程联络单'),
       'set_reviewer':(ReviewerInput,'set_reviewer','指定联络单验收负责人'),
       'cancel_task':(CloseInput,'cancel_task','撤销未反馈的联络事项')}

AFFECTED_NAMES={'DRAWING':'图纸','MATERIAL':'物料','PURCHASE_ORDER':'采购单','WIP_TASK':'在制任务',
                'SUPPLIER_TASK':'供应商任务','PLAN_NODE':'计划节点','CONTRACT':'合同',
                'FINANCE':'财务事项','LOGISTICS':'物流','OTHER':'其他对象'}
ACTION_NAMES={'CONTINUE':'继续执行','PAUSE':'暂停','CANCEL':'取消','REWORK':'返工','REISSUE':'重新下达'}


def ensure_open(case):
    if case.closed_at:raise DomainError('CONTACT_CLOSED','联络单已关闭，不能继续修改',409)


def materials(db,case):
    from domain_packs.mold.ports.files import case_attachments
    return {'case_id':case.id,'title':case.title,'description':case.description,
            'customer_ref':case.customer_ref,'customer_name':case.customer_name,'mold_number':case.mold_number,
            'product_ref':case.product_ref,'application_date':case.application_date.isoformat() if case.application_date else None,
            'problem_source':case.problem_source,'current_stage':case.current_stage,
            'change_type':case.change_type,'urgency':case.urgency,
            'tasks':[{'id':t.id,'title':t.title,'department_id':t.department_id,
                      'department_name':db.get(m.AssignmentGroup,t.department_id).name,
                      'affected_type':t.affected_type,'affected_ref':t.affected_ref,
                      'impact_description':t.impact_description,'planned_action':t.planned_action,
                      'delivery_impact_days':t.delivery_impact_days,
                      'estimated_amount':str(t.estimated_amount) if t.estimated_amount is not None else None,
                      'currency':t.currency,'source_system':t.source_system,'source_ref':t.source_ref,
                      'source_as_of':t.source_as_of.isoformat() if t.source_as_of else None}
                     for t in db.scalars(select(m.ContactTask).where(m.ContactTask.case_id==case.id,
                         m.ContactTask.status!='CANCELLED').order_by(m.ContactTask.id))],
            'attachments':sorted([f for f in case_attachments(db,case) if f['is_current']],key=lambda f:f['id'])}


def ensure_materials(db,resolution):
    case=db.get(m.ContactCase,resolution.case_id)
    ensure_open(case)
    from domain_packs.mold.ports.bpm import content_hash
    # Timestamps are converted to canonical strings in both snapshots.
    from fastapi.encoders import jsonable_encoder
    if content_hash(jsonable_encoder(materials(db,case)))!=content_hash(resolution.material_snapshot):
        raise DomainError('RESOLUTION_STALE','方案关联的事项或附件已变化，须重新核对并审批方案',409)


def activate_resolution(db,user,resolution):
    """Record a durable effective-plan handoff; never pretend to execute referenced ERP work."""
    from domain_packs.mold.ports.bpm import content_hash
    ensure_materials(db,resolution)
    case=db.scalar(select(m.ContactCase).where(m.ContactCase.id==resolution.case_id).with_for_update())
    existing=db.scalar(select(m.ContactRecord.id).where(m.ContactRecord.case_id==case.id,
        m.ContactRecord.kind=='RESOLUTION_EFFECTIVE',m.ContactRecord.request_key==resolution.subject_id))
    if existing:return
    subject=db.get(m.BusinessSubject,resolution.subject_id);recipients=set()
    tasks=list(db.scalars(select(m.ContactTask).where(m.ContactTask.case_id==case.id,m.ContactTask.status!='CANCELLED')))
    for task in tasks:
        if task.assignee_id:
            person=db.get(m.User,task.assignee_id)
            if person and c.permitted(db,person,'read',case):recipients.add(person.id)
        group=db.get(m.AssignmentGroup,task.department_id)
        if group:
            for member in db.scalars(select(m.AssignmentMember).where(m.AssignmentMember.group_id==group.id,m.AssignmentMember.is_head.is_(True))):
                person=db.get(m.User,member.user_id)
                if person and person.active and c.permitted(db,person,'read',case):recipients.add(person.id)
    recipients.discard(user.id);case.revision+=1
    detail={'subject_id':subject.id,'number':subject.number,'case_revision':case.revision,
        'task_ids':[task.id for task in tasks],
        'limitations':'方案已生效并通知相关人员；引用的ERP任务、订单和合同仍须通过各自业务回执确认执行。'}
    db.add(m.ContactRecord(case_id=case.id,author_id=user.id,request_key=subject.id,
        request_hash=content_hash(detail),kind='RESOLUTION_EFFECTIVE',occurred_at=now(),detail=detail))
    from domain_packs.mold.ports.events import record
    record(db,user,'contact.resolution_effective',case.id,{'subject_id':subject.id,'revision':case.revision},list(recipients))


def latest(db,case):
    return db.scalar(select(m.ContactResolution).join(m.BusinessSubject).where(m.ContactResolution.case_id==case.id)
        .order_by(m.BusinessSubject.created_at.desc(),m.BusinessSubject.id.desc()).limit(1))


def approved(db,user,case):
    from domain_packs.mold.erp.core.domains import authorize
    resolution=latest(db,case)
    if not resolution:raise DomainError('RESOLUTION_REQUIRED','须先提交并通过处理方案审批',409)
    subject=db.get(m.BusinessSubject,resolution.subject_id)
    authorize(db,user,subject,'read')
    instance=db.scalar(select(m.ApprovalInstance).where(
        m.ApprovalInstance.resource_type=='business_subject',
        m.ApprovalInstance.resource_id==subject.id,
        m.ApprovalInstance.round_no==subject.round_no,m.ApprovalInstance.revision==subject.revision))
    if subject.status!='EFFECTIVE' or not instance or instance.status!='COMPLETED':
        raise DomainError('RESOLUTION_NOT_APPROVED','最新处理方案尚未审批通过并生效',409)
    ensure_materials(db,resolution)
    return resolution


def reviewer(db,user,case,action):
    c.require(db,user,action,case)
    if user.id not in {case.created_by,case.reviewer_id}:
        raise DomainError('CONTACT_REVIEWER_REQUIRED','须由发起人或管理员指定的验收负责人确认',403)


def plan_resource(case):
    return m.BusinessSubject(kind='contact_resolution',project_id=case.project_id,category=case.category)


def validate_plan(db,user,case,data):
    from domain_packs.mold import domains, workflow_selection
    ensure_open(case);c.require(db,user,'plan',case)
    if case.mode!='ONLINE':raise DomainError('HISTORY_NO_DISPATCH','历史补录不能触发线上方案审批',409)
    if user.id!=case.created_by:raise DomainError('FORBIDDEN','由联络单发起人提交处理方案',403)
    if not data.solution.strip():raise DomainError('INCOMPLETE','请填写完整处理方案')
    if data.customer_due_affected and not (data.customer_evidence or '').strip():
        raise DomainError('CUSTOMER_CONFIRMATION_REQUIRED','影响客户交期时须提供人工核实的客户确认依据')
    if not materials(db,case)['tasks']:raise DomainError('TASK_REQUIRED','先明确责任部门和处理事项，再提交方案',409)
    previous=latest(db,case)
    if previous and db.get(m.BusinessSubject,previous.subject_id).status=='SUBMITTED':
        raise DomainError('RESOLUTION_PENDING','已有方案正在审批，请先完成本轮审批',409)
    resource=plan_resource(case)
    for action in ('create','read','submit'):domains.authorize(db,user,resource,action)
    return workflow_selection.require_template(db,user,resource,data.definition_id)


def persist_resolution(db,user,subject,data):
    from fastapi.encoders import jsonable_encoder
    case=c.load(db,user,data.case_id,True);ensure_open(case);c.require(db,user,'plan',case)
    if case.mode!='ONLINE' or case.created_by!=user.id:raise DomainError('FORBIDDEN','仅线上联络单发起人可创建方案',403)
    if case.revision!=data.case_revision:raise DomainError('VERSION_CONFLICT','联络单资料已变化',409)
    if (subject.project_id,subject.category)!=(case.project_id,case.category):raise DomainError('SCOPE_MISMATCH','方案与联络单责任范围不一致',403)
    if not data.solution.strip() or (data.customer_due_affected and not (data.customer_evidence or '').strip()):
        raise DomainError('INCOMPLETE','方案或客户交期确认依据不完整')
    snapshot=jsonable_encoder(materials(db,case))
    if not snapshot['tasks']:raise DomainError('TASK_REQUIRED','方案须关联责任事项',409)
    db.add(m.ContactResolution(subject_id=subject.id,**data.model_dump(),material_snapshot=snapshot))


def preview(db,user,case,tid,action,data):
    ensure_open(case)
    if case.mode!='ONLINE':raise DomainError('HISTORY_NO_DISPATCH','历史补录仅记录已发生过程，不能派发或关闭线上流程',409)
    if action=='resolution':
        definition=validate_plan(db,user,case,data)
        task_materials=materials(db,case)['tasks']
        return {'处理方案':data.solution,'审批流程':definition.name,'流程版本':definition.version,
                '影响客户交期':data.customer_due_affected,'客户确认依据':data.customer_evidence or '不适用',
                '责任事项':'; '.join(t['department_name']+'：'+t['title'] for t in task_materials),
                '影响与动作':'\n'.join(AFFECTED_NAMES.get(t['affected_type'],t['affected_type'])+' '+t['affected_ref']+' → '+ACTION_NAMES.get(t['planned_action'],t['planned_action'])+'：'+t['impact_description'] for t in task_materials),
                '预计交期影响天数':max((t['delivery_impact_days'] for t in task_materials),default=0),
                '附件版本':'; '.join(f["filename"]+' 第'+str(f["version"])+'版' for f in materials(db,case)['attachments']) or '无关联附件','说明':'本人确认后创建正式审批待办，不会自动执行方案。'}
    if action=='set_reviewer':
        auth.require(db,user,'grant.manage');person=db.get(m.User,data.reviewer_id)
        if not person or not person.active or not all(c.permitted(db,person,a,case) for a in ('read','review','close')):
            raise DomainError('REVIEWER_UNAVAILABLE','验收负责人须有效且具有联络查询、复验和关闭权限',403)
        return {'验收负责人':person.display_name,'指定依据':data.evidence}
    if action=='cancel_task':
        if case.created_by!=user.id:raise DomainError('FORBIDDEN','由发起人撤销未反馈事项',403)
        task=db.get(m.ContactTask,tid)
        if not task or task.case_id!=case.id:raise DomainError('NOT_FOUND','事项不存在',404)
        if task.status not in {'UNASSIGNED','ASSIGNED'}:raise DomainError('TASK_FINISHED','已反馈或已验收事项不能撤销，请保留证据另建事项',409)
        return {'撤销事项':task.title,'撤销依据':data.evidence,'说明':'保留原责任和过程记录；方案范围改变须重新审批。'}
    reviewer(db,user,case,action)
    resolution=approved(db,user,case)
    if action=='review':
        task=db.get(m.ContactTask,tid)
        if not task or task.case_id!=case.id:raise DomainError('NOT_FOUND','事项不存在',404)
        if task.status not in {'RESPONDED','VERIFIED'}:raise DomainError('RESPONSE_REQUIRED','处理人须先提交完成反馈',409)
        if task.assignee_id==user.id:raise DomainError('INDEPENDENT_RECHECK','处理人不能复验自己的处理结果',403)
        if task.status=='VERIFIED' and task.verified_plan_id==resolution.subject_id:raise DomainError('ALREADY_VERIFIED','本方案下的合格复验不能覆盖',409)
        return {'复验事项':task.title,'处理反馈':task.response,'结论':'合格' if data.decision=='PASS' else '退回整改',
                '复验依据':data.evidence,'方案编号':db.get(m.BusinessSubject,resolution.subject_id).number}
    tasks=list(db.scalars(select(m.ContactTask).where(m.ContactTask.case_id==case.id,m.ContactTask.status!='CANCELLED')))
    if not tasks or any(t.status!='VERIFIED' or t.verified_plan_id!=resolution.subject_id for t in tasks):
        raise DomainError('RECHECK_REQUIRED','所有有效责任事项须在最新批准方案下复验合格',409)
    return {'关闭依据':data.evidence,'复验合格事项数':len(tasks),'方案编号':db.get(m.BusinessSubject,resolution.subject_id).number,
            '说明':'人工关闭联络协作事项；不会自动修改 ERP、采购订单或客户承诺。'}


def execute(db,user,cid,tid,action,data,agent_permission_mode="ask"):
    case=c.load(db,user,cid,True);c.require(db,user,SPECS[action][1],case)
    digest,done=c.replay(db,user,case,data,action.upper()+':'+tid)
    if done:return c.serialize(db,case,True,user)
    display=preview(db,user,case,tid,action,data);detail={'task_id':tid,**display}
    recipients=[]
    if action=='resolution':
        from domain_packs.mold import domains,business,domain_schemas
        subject=domains.create(db,user,domain_schemas.SubjectInput(kind='contact_resolution',project_id=case.project_id,
            category=case.category,remark=case.title,detail={'case_id':case.id,'case_revision':case.revision,
            'solution':data.solution,'customer_due_affected':data.customer_due_affected,'customer_evidence':data.customer_evidence}))
        result=business.submit_subject(db,user,subject.id,1,data.definition_id,agent_permission_mode=agent_permission_mode)
        detail.update(result)
    elif action=='set_reviewer':
        detail.update(previous_reviewer_id=case.reviewer_id,reviewer_id=data.reviewer_id)
        case.reviewer_id=data.reviewer_id;recipients=[data.reviewer_id]
    elif action=='cancel_task':
        task=db.get(m.ContactTask,tid);task.status='CANCELLED'
        if task.assignee_id and c.permitted(db,db.get(m.User,task.assignee_id),'read',case):recipients=[task.assignee_id]
    elif action=='review':
        task=db.get(m.ContactTask,tid);resolution=approved(db,user,case)
        detail.update(plan_id=resolution.subject_id,response=task.response,decision=data.decision,evidence=data.evidence)
        task.status='VERIFIED' if data.decision=='PASS' else 'ASSIGNED'
        task.verified_plan_id=resolution.subject_id if data.decision=='PASS' else None
        if task.assignee_id and c.permitted(db,db.get(m.User,task.assignee_id),'read',case):recipients=[task.assignee_id]
    else:
        case.closed_by=user.id;case.closed_at=now()
        detail['plan_id']=latest(db,case).subject_id
        recipients=[case.created_by] if c.permitted(db,db.get(m.User,case.created_by),'read',case) else []
    return c.append(db,user,case,data,{'resolution':'RESOLUTION_SUBMITTED','set_reviewer':'REVIEWER_SET',
        'cancel_task':'TASK_CANCELLED','review':'TASK_REVIEWED','close':'CLOSED'}[action],digest,detail,recipients=recipients)


def context(db,user,case):
    from domain_packs.mold import domains,workflow_selection
    result={'reviewer_name':db.get(m.User,case.reviewer_id).display_name if case.reviewer_id else None,
            'closed_at':case.closed_at,'closed_by_name':db.get(m.User,case.closed_by).display_name if case.closed_by else None}
    resource=plan_resource(case)
    try:result['approval_templates']=[workflow_selection.metadata(d,db) for d in workflow_selection.available(db,user,resource)]
    except DomainError:result['approval_templates']=[]
    if auth.access(db,user,'grant.manage',{}).allowed and c.permitted(db,user,'set_reviewer',case):
        result['reviewer_candidates']=[{'id':p.id,'name':p.display_name} for p in db.scalars(select(m.User).where(m.User.active.is_(True)))
            if all(c.permitted(db,p,a,case) for a in ('read','review','close'))]
    result['resolutions']=[]
    for resolution in db.scalars(select(m.ContactResolution).join(m.BusinessSubject).where(m.ContactResolution.case_id==case.id).order_by(m.BusinessSubject.created_at.desc())):
        subject=db.get(m.BusinessSubject,resolution.subject_id)
        try:domains.authorize(db,user,subject,'read')
        except DomainError:continue
        instance=db.scalar(select(m.ApprovalInstance).where(
            m.ApprovalInstance.resource_type=='business_subject',
            m.ApprovalInstance.resource_id==subject.id,
        ).order_by(m.ApprovalInstance.created_at.desc()).limit(1))
        result['resolutions'].append({'id':subject.id,'number':subject.number,'status':subject.status,'solution':resolution.solution,
            'instance_id':instance.id if instance else None,'snapshot':resolution.material_snapshot})
    return result


def record_detail(db,user,record):
    if record.kind!='RESOLUTION_SUBMITTED':return record.detail
    subject=db.get(m.BusinessSubject,record.detail.get('subject_id'))
    if not subject:return {'说明':'方案记录不存在或暂不可用'}
    from domain_packs.mold.erp.core.domains import authorize
    try:authorize(db,user,subject,'read')
    except DomainError:return {'说明':'已提交处理方案，当前无方案资料读取权限'}
    return record.detail
