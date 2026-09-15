"""Local domain documents, typed persistence, and transactional approval effects."""
from datetime import timedelta
from decimal import Decimal
import secrets
from sqlalchemy import select, func
from pydantic import ValidationError
from . import models as m, domain_schemas as s
from .authorization import require, predicate, select_fields
from .errors import DomainError
from .db import now
from .events import record


def scope(subject):
    return {'project_id':subject.project_id,'category':subject.category,'warehouse_id':subject.warehouse_id}


def authorize(db,user,subject,action):
    if subject.kind=='contact_resolution' and subject.id:
        from . import contacts
        detail=db.get(m.ContactResolution,subject.id)
        if detail:contacts.load(db,user,detail.case_id)
    fields=require(db,user,f'{subject.kind}.{action}',scope(subject))
    if subject.kind=='contact_resolution' and action=='read' and '*' not in fields and not {'project_id','detail','remark'}<=fields:
        raise DomainError('FORBIDDEN','处理方案需要完整材料读取权限',403)
    return fields


def rows(db,table,**filters):
    return list(db.scalars(select(table).filter_by(**filters)))


def values(row, exclude=()):
    result={}
    for column in row.__table__.columns:
        if column.name in exclude:continue
        value=getattr(row,column.name)
        if isinstance(value,Decimal):value=str(value)
        elif hasattr(value,'isoformat'):value=value.isoformat()
        result[column.name]=value
    return result


def typed_detail(db,subject):
    from . import domain_extensions as ext
    kind=subject.kind
    if kind in ext.TABLES:return ext.detail_data(db,subject)
    if kind in {'sales_contract','full_outsource_contract'}:
        detail=values(db.get(m.ContractDetail,subject.id),('subject_id',))
        detail['stages']=[values(stage) for stage in rows(db,m.PaymentStage,contract_id=subject.id)]
    elif kind=='supplier_payment':
        detail=values(db.get(m.PaymentRequestDetail,subject.id),('subject_id',))
        detail['payments']=[values(p) for p in rows(db,m.PaymentConfirmation,request_id=subject.id)]
    elif kind in {'project_plan','plan_change'}:
        detail=values(db.get(m.PlanDetail,subject.id),('subject_id',))
        tasks=rows(db,m.PlanTask,plan_id=subject.id); keys={t.id:t.key for t in tasks}
        detail['tasks']=[{**values(t),'prerequisites':[keys[d.prerequisite_id] for d in rows(db,m.TaskDependency,task_id=t.id)]} for t in tasks]
    elif kind=='pause_resume':
        detail=values(db.get(m.ProjectPauseDetail,subject.id),('subject_id',))
        plan=db.get(m.BusinessSubject,detail.get('plan_subject_id')) if detail.get('plan_subject_id') else None
        source=db.get(m.BusinessSubject,detail.get('source_pause_subject_id')) if detail.get('source_pause_subject_id') else None
        detail['plan_number']=plan.number if plan else None
        detail['source_pause_number']=source.number if source else None
        detail['allowed_during_pause']=['资料补录','沟通记录','合同与结算核对','工程联络与恢复申请']
        if detail.get('source_pause_subject_id'):
            pause=db.scalar(select(m.PauseRecord).where(m.PauseRecord.subject_id==detail['source_pause_subject_id']))
            detail['shifted_days']=pause.shifted_days if pause else 0
            detail['task_shifts']=[values(row) for row in rows(db,m.PauseTaskShift,pause_id=pause.id)] if pause else []
    elif kind=='project_close':
        detail=values(db.get(m.ProjectClosureDetail,subject.id),('subject_id',))
        if detail.get('closure_case_id'):
            case=db.get(m.ProjectClosureCase,detail['closure_case_id'])
            detail['closure_case']={**values(case),'items':[
                values(item) for item in rows(db,m.ProjectClosureItem,case_id=case.id)]} if case else None
    elif kind=='engineering_change':
        detail=values(db.get(m.EngineeringChangeDetail,subject.id),('subject_id',))
        detail['impacts']=[values(i) for i in rows(db,m.ChangeImpact,change_id=subject.id)]
    else:detail=values(db.get(m.BusinessDecisionDetail,subject.id),('subject_id',))
    return detail


def data(db,user,subject):
    allowed=authorize(db,user,subject,'read')
    return select_fields({**values(subject),'detail':typed_detail(db,subject)},allowed)


def visible(db,user,kind=None):
    kinds=[kind] if kind else list(s.CATALOG)
    result=[]
    for key in kinds:
        if key not in s.CATALOG:raise DomainError('KIND_UNKNOWN','业务类型未登记')
        q=select(m.BusinessSubject).where(m.BusinessSubject.kind==key,predicate(db,user,f'{key}.read',{
            'project_id':m.BusinessSubject.project_id,'category':m.BusinessSubject.category,'warehouse_id':m.BusinessSubject.warehouse_id}))
        result.extend(data(db,user,row) for row in db.scalars(q.order_by(m.BusinessSubject.created_at.desc()).limit(100)))
    return result


def require_source(db,source_id,project_id,kinds,statuses=('EFFECTIVE',)):
    source=db.get(m.BusinessSubject,source_id) if source_id else None
    if not source or source.project_id!=project_id or source.kind not in kinds or source.status not in statuses:
        raise DomainError('SOURCE_INVALID','前置业务记录、项目或生效状态不符合要求',409)
    return source


def validate_plan(db,project_id,detail):
    tasks={t.key:t for t in detail.tasks}
    if len(tasks)!=len(detail.tasks):raise DomainError('TASK_DUPLICATE','任务标识重复')
    for t in tasks.values():
        if t.planned_end<t.planned_start:raise DomainError('DATE_INVALID','任务结束日早于开始日')
        user=db.get(m.User,t.owner_user_id)
        if not user or not user.active:raise DomainError('ASSIGNMENT_BLOCKED','计划责任人不存在或已停用')
        if not set(t.prerequisites)<=tasks.keys() or t.key in t.prerequisites:
            raise DomainError('DEPENDENCY_INVALID','任务前置关联无效')
        for key in t.prerequisites:
            if tasks[key].planned_end>t.planned_start:raise DomainError('DEPENDENCY_DATE','前置任务计划结束晚于后续任务开始')
    visiting,done=set(),set()
    def visit(key):
        if key in visiting:raise DomainError('PLAN_CYCLE','计划依赖不能成环')
        if key in done:return
        visiting.add(key)
        for previous in tasks[key].prerequisites:visit(previous)
        visiting.remove(key);done.add(key)
    for key in tasks:visit(key)
    if detail.previous_id:
        require_source(db,detail.previous_id,project_id,{'project_plan','plan_change'})
        for prior in rows(db,m.PlanTask,plan_id=detail.previous_id):
            if prior.actual_start and prior.key not in tasks:raise DomainError('TASK_HISTORY_PROTECTED','已开工任务不能在新计划中删除')
            if prior.actual_end:
                new=tasks[prior.key]
                if (new.planned_start,new.planned_end)!=(prior.planned_start,prior.planned_end):
                    raise DomainError('TASK_HISTORY_PROTECTED','已完成任务不能重排')


def active_plan(db,project_id):
    plans=list(db.scalars(select(m.BusinessSubject).where(
        m.BusinessSubject.project_id==project_id,
        m.BusinessSubject.kind.in_(['project_plan','plan_change']),
        m.BusinessSubject.status=='EFFECTIVE').order_by(m.BusinessSubject.created_at.desc())))
    if len(plans)>1:raise DomainError('PLAN_STATE_INVALID','项目存在多个有效计划，请先核对计划版本',409)
    return plans[0] if plans else None


def pause_snapshot(db,project):
    plan=active_plan(db,project.id)
    tasks=[] if not plan else list(db.scalars(select(m.PlanTask).where(
        m.PlanTask.plan_id==plan.id,m.PlanTask.status!='DONE').order_by(m.PlanTask.key)))
    snapshot=[{'id':task.id,'key':task.key,'name':task.name,'status':task.status,
               'planned_start':task.planned_start.isoformat(),'planned_end':task.planned_end.isoformat()}
              for task in tasks]
    return plan,snapshot


def validate_pause_detail(db,project,detail,subject_id=None):
    if detail.effective_date>now().date():raise DomainError('DATE_INVALID','暂停或恢复日期不能晚于当前日期')
    profile=db.get(m.ProjectProfile,project.id)
    pending=select(m.BusinessSubject.id).where(m.BusinessSubject.project_id==project.id,
        m.BusinessSubject.kind=='pause_resume',m.BusinessSubject.status.in_(['DRAFT','SUBMITTED']))
    if subject_id:pending=pending.where(m.BusinessSubject.id!=subject_id)
    if db.scalar(pending.limit(1)):
        raise DomainError('PAUSE_REQUEST_EXISTS','项目已有待处理的暂停或恢复申请',409)
    if detail.decision=='PAUSE':
        if project.status!='ACTIVE':raise DomainError('PAUSE_STATE','只能为执行中的项目准备暂停',409)
        if detail.source_pause_subject_id:raise DomainError('SOURCE_INVALID','暂停申请不能关联恢复来源')
        if detail.expected_resume_date and detail.expected_resume_date<detail.effective_date:
            raise DomainError('DATE_INVALID','预计恢复日不能早于暂停日')
        plan,snapshot=pause_snapshot(db,project)
        return {'plan':plan,'task_snapshot':snapshot,'customer_due_date':profile.customer_due_date if profile else None}
    if project.status!='PAUSED':raise DomainError('RESUME_STATE','只能为暂停中的项目准备恢复',409)
    if detail.expected_resume_date:raise DomainError('DATE_INVALID','恢复申请不填写预计恢复日')
    pause=db.scalar(select(m.PauseRecord).where(m.PauseRecord.project_id==project.id,
        m.PauseRecord.end_date.is_(None),m.PauseRecord.subject_id==detail.source_pause_subject_id))
    if not pause:raise DomainError('SOURCE_INVALID','恢复申请须关联当前有效暂停记录',409)
    if detail.effective_date<pause.start_date:raise DomainError('DATE_INVALID','恢复日早于暂停日')
    source=db.get(m.ProjectPauseDetail,pause.subject_id)
    return {'plan':db.get(m.BusinessSubject,source.plan_subject_id) if source and source.plan_subject_id else None,
            'task_snapshot':source.task_snapshot if source else [],'customer_due_date':profile.customer_due_date if profile else None}


def create(db,user,payload):
    if payload.kind not in s.CATALOG:raise DomainError('KIND_UNKNOWN','业务类型未登记')
    project=db.get(m.Project,payload.project_id)
    if not project:raise DomainError('NOT_FOUND','项目不存在',404)
    require(db,user,f'{payload.kind}.create',payload.model_dump())
    if payload.category and payload.category not in {'hardware','raw_material','outsource','auxiliary','office_supply','trial_material'}:
        raise DomainError('CATEGORY_UNKNOWN','责任域尚未登记')
    try:detail=s.CATALOG[payload.kind]['schema'].model_validate(payload.detail)
    except ValidationError as error:raise DomainError('FORM_INVALID','业务字段校验失败：'+error.errors()[0]['msg']) from None
    subject=m.BusinessSubject(kind=payload.kind,number=payload.kind.upper()[:16]+'-'+secrets.token_hex(5).upper(),
                              project_id=project.id,category=payload.category,warehouse_id=payload.warehouse_id,
                              created_by=user.id,remark=payload.remark)
    db.add(subject);db.flush()
    kind=subject.kind
    from . import domain_extensions as ext
    if kind in ext.TABLES:ext.persist(db,user,subject,detail)
    elif isinstance(detail,s.ContractInput):
        if kind=='sales_contract':
            if not detail.customer_id or not db.get(m.Customer,detail.customer_id) or detail.supplier_id:
                raise DomainError('PARTY_INVALID','销售合同须关联有效客户')
        else:
            supplier=db.get(m.Supplier,detail.supplier_id) if detail.supplier_id else None
            if not supplier or not supplier.active or supplier.category!='outsource' or subject.category!='outsource':
                raise DomainError('PARTY_INVALID','委外合同须关联委外责任域与有效委外供应商')
        if sum((stage.amount for stage in detail.stages),Decimal(0))>detail.amount:
            raise DomainError('STAGE_OVERFLOW','合同阶段金额合计超出合同金额')
        if detail.replaces_id:
            require_source(db,detail.replaces_id,project.id,{kind})
            raise DomainError('ALLOCATION_REQUIRED','替代合同须先完成财务归属核对，当前禁止直接覆盖旧合同')
        db.add(m.ContractDetail(subject_id=subject.id,**detail.model_dump(exclude={'stages'})))
        for stage in detail.stages:db.add(m.PaymentStage(contract_id=subject.id,currency=detail.currency,**stage.model_dump()))
    elif isinstance(detail,s.PaymentInput):
        stage=db.get(m.PaymentStage,detail.stage_id)
        if not stage:raise DomainError('STAGE_UNKNOWN','付款阶段不存在')
        contract=require_source(db,stage.contract_id,project.id,{'full_outsource_contract'})
        if subject.category!=contract.category or detail.currency!=stage.currency:
            raise DomainError('CURRENCY_OR_SCOPE','付款币种或责任域与合同不一致')
        db.add(m.PaymentRequestDetail(subject_id=subject.id,**detail.model_dump()))
    elif isinstance(detail,s.PlanInput):
        if kind=='plan_change' and not detail.previous_id:raise DomainError('SOURCE_REQUIRED','变更计划须关联原计划')
        validate_plan(db,project.id,detail)
        db.add(m.PlanDetail(subject_id=subject.id,reason=detail.reason,previous_id=detail.previous_id))
        tasks={}
        prior={t.key:t for t in rows(db,m.PlanTask,plan_id=detail.previous_id)} if detail.previous_id else {}
        for item in detail.tasks:
            task=m.PlanTask(plan_id=subject.id,**item.model_dump(exclude={'prerequisites'}))
            if item.key in prior:
                old=prior[item.key];task.actual_start=old.actual_start;task.actual_end=old.actual_end;task.status=old.status
            db.add(task);db.flush();tasks[item.key]=task
        for item in detail.tasks:
            for key in item.prerequisites:db.add(m.TaskDependency(task_id=tasks[item.key].id,prerequisite_id=tasks[key].id))
    elif isinstance(detail,s.PauseResumeInput):
        context=validate_pause_detail(db,project,detail,subject.id)
        db.add(m.ProjectPauseDetail(subject_id=subject.id,decision=detail.decision,
            effective_date=detail.effective_date,expected_resume_date=detail.expected_resume_date,
            reason=detail.reason,evidence=detail.evidence,source_pause_subject_id=detail.source_pause_subject_id,
            plan_subject_id=context['plan'].id if context['plan'] else None,
            task_snapshot=context['task_snapshot'],customer_due_date_snapshot=context['customer_due_date']))
    elif isinstance(detail,s.ProjectCloseInput):
        from .project_closure import validate_close_detail
        validate_close_detail(db,project,detail,subject.id)
        db.add(m.ProjectClosureDetail(subject_id=subject.id,**detail.model_dump()))
    elif isinstance(detail,s.ChangeInput):
        if detail.customer_due_affected and not detail.customer_evidence:
            raise DomainError('CUSTOMER_CONFIRMATION_REQUIRED','影响客户交期时必须提供独立客户确认依据')
        if len({i.task_id for i in detail.impacts})!=len(detail.impacts):raise DomainError('IMPACT_DUPLICATE','影响对象重复')
        db.add(m.EngineeringChangeDetail(subject_id=subject.id,**detail.model_dump(exclude={'impacts'})))
        for item in detail.impacts:
            task=db.get(m.PlanTask,item.task_id)
            if not task:raise DomainError('TASK_UNKNOWN','受影响任务不存在')
            require_source(db,task.plan_id,project.id,{'project_plan','plan_change'})
            db.add(m.ChangeImpact(change_id=subject.id,**item.model_dump()))
    else:
        if detail.decision not in s.CATALOG[kind]['decisions']:raise DomainError('DECISION_INVALID','业务决定不适用于此类型')
        if bool(detail.amount)!=bool(detail.currency):raise DomainError('CURRENCY_REQUIRED','金额与币种须同时填写')
        if kind=='quote_acceptance' and detail.decision=='ACCEPT' and not detail.execution_mode:
            raise DomainError('MODE_REQUIRED','承接时须确认最终加工方式')
        if detail.source_subject_id:require_source(db,detail.source_subject_id,project.id,set(s.CATALOG))
        db.add(m.BusinessDecisionDetail(subject_id=subject.id,**detail.model_dump()))
    record(db,user,'business.draft.created',subject.id,{'kind':kind});db.flush()
    return subject


def payment_reserve(db,subject):
    detail=db.get(m.PaymentRequestDetail,subject.id)
    stage=db.scalar(select(m.PaymentStage).where(m.PaymentStage.id==detail.stage_id).with_for_update())
    require_source(db,stage.contract_id,subject.project_id,{'full_outsource_contract'})
    if not stage.condition_confirmed:raise DomainError('PAYMENT_CONDITION','付款阶段条件尚未由人员核实',409)
    reservations=db.scalar(select(func.coalesce(func.sum(m.PaymentRequestDetail.reservation),0)).where(m.PaymentRequestDetail.stage_id==stage.id))
    paid=db.scalar(select(func.coalesce(func.sum(m.PaymentConfirmation.amount),0)).join(m.PaymentRequestDetail,m.PaymentRequestDetail.subject_id==m.PaymentConfirmation.request_id).where(m.PaymentRequestDetail.stage_id==stage.id))
    if detail.amount>stage.amount-reservations-paid:raise DomainError('PAYMENT_OVERFLOW','阶段可申请余额不足',409)
    detail.reservation=detail.amount


def before_submit(db,user,subject):
    project=db.scalar(select(m.Project).where(m.Project.id==subject.project_id).with_for_update())
    allowed={'pause_resume','supplier_payment','engineering_change','contact_resolution','project_close','sales_contract','full_outsource_contract'}
    if project.status in {'CLOSED','TERMINATED'} and subject.kind not in {'supplier_payment','project_close'}:
        raise DomainError('PROJECT_BLOCKED','项目已关闭或终止，禁止普通业务提交',409)
    if project.status=='PAUSED' and subject.kind not in allowed:raise DomainError('PROJECT_BLOCKED','项目暂停，禁止普通执行业务',409)
    if subject.kind=='contact_resolution':
        from .contact_lifecycle import ensure_materials
        ensure_materials(db,db.get(m.ContactResolution,subject.id))
    if subject.kind=='supplier_payment':payment_reserve(db,subject)
    elif subject.kind=='project_close':
        from .project_closure import validate_close_detail
        validate_close_detail(db,project,db.get(m.ProjectClosureDetail,subject.id),subject.id)
    elif subject.kind=='internal_start':
        detail=db.get(m.BusinessDecisionDetail,subject.id)
        source=require_source(db,detail.source_subject_id,subject.project_id,{'quote_acceptance'})
        if db.get(m.BusinessDecisionDetail,source.id).decision!='ACCEPT':raise DomainError('NOT_ACCEPTED','报价尚未确认承接')
    elif subject.kind in {'project_plan','plan_change'}:
        if project.status!='ACTIVE':raise DomainError('START_REQUIRED','计划提交要求项目正式开工')


def release_reservation(db,subject):
    if subject.kind=='supplier_payment':
        detail=db.get(m.PaymentRequestDetail,subject.id)
        db.scalar(select(m.PaymentStage).where(m.PaymentStage.id==detail.stage_id).with_for_update())
        detail.reservation=Decimal(0)


def apply(db,user,subject):
    """Called inside the same local transaction as the final human approval."""
    project=db.scalar(select(m.Project).where(m.Project.id==subject.project_id).with_for_update())
    kind=subject.kind
    from . import domain_extensions as ext
    if kind in ext.TABLES:
        if kind not in {'finance_correction','contact_resolution'} and project.status!='ACTIVE':raise DomainError('PROJECT_BLOCKED','业务生效要求项目执行中')
        ext.apply(db,user,subject)
    if project.status=='PAUSED' and kind not in {'pause_resume','supplier_payment','engineering_change','contact_resolution','project_close','sales_contract','full_outsource_contract'}:
        raise DomainError('APPLY_BLOCKED','项目已暂停，审批通过但业务暂不能生效',409)
    if kind=='quote_acceptance':
        detail=db.get(m.BusinessDecisionDetail,subject.id)
        profile=db.get(m.ProjectProfile,project.id)
        if detail.decision=='ACCEPT':
            if profile:profile.execution_mode=detail.execution_mode
            else:db.add(m.ProjectProfile(project_id=project.id,owner_user_id=user.id,execution_mode=detail.execution_mode))
        # Acceptance is separate from the formal start notice.
    elif kind=='internal_start':
        if project.status!='DRAFT':raise DomainError('START_STATE','只有未开工项目可正式开工',409)
        detail=db.get(m.BusinessDecisionDetail,subject.id)
        source=require_source(db,detail.source_subject_id,project.id,{'quote_acceptance'})
        if db.get(m.BusinessDecisionDetail,source.id).decision!='ACCEPT':raise DomainError('NOT_ACCEPTED','未确认承接')
        project.status='ACTIVE';project.row_version+=1
    elif kind=='full_outsource_contract':
        profile=db.get(m.ProjectProfile,project.id)
        if not profile or profile.execution_mode!='FULL_OUTSOURCE':raise DomainError('MODE_CONFLICT','整套委外合同要求项目确认为整套委外',409)
    elif kind in {'project_plan','plan_change'}:
        if project.status!='ACTIVE':raise DomainError('PROJECT_BLOCKED','项目未处于执行状态',409)
        prior=db.get(m.PlanDetail,subject.id).previous_id
        active=list(db.scalars(select(m.BusinessSubject).where(m.BusinessSubject.project_id==project.id,
                    m.BusinessSubject.kind.in_(['project_plan','plan_change']),m.BusinessSubject.status=='EFFECTIVE')))
        if prior:
            old=require_source(db,prior,project.id,{'project_plan','plan_change'});old.status='CLOSED'
        elif active:raise DomainError('PLAN_EXISTS','项目已有基线，请通过计划变更生成新版本',409)
    elif kind=='pause_resume':
        detail=db.get(m.ProjectPauseDetail,subject.id)
        if detail.decision=='PAUSE':
            if project.status!='ACTIVE':raise DomainError('PAUSE_STATE','只能暂停执行中的项目',409)
            if db.scalar(select(m.PauseRecord.id).where(m.PauseRecord.project_id==project.id,m.PauseRecord.end_date.is_(None))):
                raise DomainError('PAUSE_STATE','项目已有未结束暂停区间',409)
            plan,snapshot=pause_snapshot(db,project)
            if (plan.id if plan else None)!=detail.plan_subject_id or snapshot!=detail.task_snapshot:
                raise DomainError('PAUSE_SCOPE_CHANGED','计划或未完成任务已变化，请重新准备暂停申请',409)
            db.add(m.PauseRecord(project_id=project.id,start_date=detail.effective_date,subject_id=subject.id,
                customer_due_date_snapshot=detail.customer_due_date_snapshot));project.status='PAUSED'
        else:
            pause=db.scalar(select(m.PauseRecord).where(m.PauseRecord.project_id==project.id,
                m.PauseRecord.end_date.is_(None),m.PauseRecord.subject_id==detail.source_pause_subject_id).with_for_update())
            if project.status!='PAUSED' or not pause:raise DomainError('RESUME_STATE','没有可恢复的暂停区间',409)
            if detail.effective_date<pause.start_date:raise DomainError('DATE_INVALID','恢复日早于暂停日')
            source=db.get(m.ProjectPauseDetail,pause.subject_id)
            if not source:raise DomainError('PAUSE_SCOPE_MISSING','暂停影响范围缺失，不能自动顺延',409)
            days=(detail.effective_date-pause.start_date).days
            for item in source.task_snapshot:
                task=db.scalar(select(m.PlanTask).where(m.PlanTask.id==item['id']).with_for_update())
                if not task or task.plan_id!=source.plan_subject_id:
                    raise DomainError('PAUSE_SCOPE_CHANGED','暂停影响任务已变化，不能自动顺延',409)
                if task.status=='DONE':continue
                previous_start,previous_end=task.planned_start,task.planned_end
                task.planned_start=previous_start+timedelta(days=days)
                task.planned_end=previous_end+timedelta(days=days)
                db.add(m.PauseTaskShift(pause_id=pause.id,task_id=task.id,
                    previous_start=previous_start,previous_end=previous_end,
                    shifted_start=task.planned_start,shifted_end=task.planned_end,
                    shifted_days=days,task_status=task.status))
            pause.end_date=detail.effective_date;pause.resume_subject_id=subject.id
            pause.shifted_days=days;pause.shift_applied=True;project.status='ACTIVE'
            # Customer commitment is intentionally untouched; changing it needs separate confirmed evidence.
        project.row_version+=1
    elif kind=='project_close':
        from .project_closure import apply_subject
        apply_subject(db,user,subject)
    elif kind=='supplier_payment':
        detail=db.get(m.PaymentRequestDetail,subject.id)
        stage=db.scalar(select(m.PaymentStage).where(m.PaymentStage.id==detail.stage_id).with_for_update())
        if not stage.condition_confirmed or detail.reservation!=detail.amount:
            raise DomainError('APPLY_BLOCKED','付款条件或预留金额已变化')
        # Reservation remains occupied until finance confirms actual payment.
    subject.status='EFFECTIVE'
    record(db,user,'business.effective',subject.id,{'kind':kind},[subject.created_by])
