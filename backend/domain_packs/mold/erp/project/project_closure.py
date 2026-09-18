"""Project termination and close checklists backed by durable, auditable facts."""
from decimal import Decimal
from sqlalchemy import select,func
from domain_packs.mold import models as m
from domain_packs.mold.authorization import require
from domain_packs.mold.ports.db import now
from domain_packs.mold.ports.errors import DomainError
from domain_packs.mold.ports.events import record


NORMAL_ITEMS = (
    ('PLAN_COMPLETION','适用计划任务全部完成',False,True),
    ('DELIVERY','交付完成并有依据',False,False),
    ('CUSTOMER_ACCEPTANCE','客户验收完成或说明不适用',True,False),
    ('INVOICE','适用发票已核对',True,False),
    ('CUSTOMER_RECEIPT','客户回款已核对',False,False),
    ('SUPPLIER_SETTLEMENT','供应商结算已完成或说明不适用',True,False),
    ('OPEN_ISSUES','异常及工程联络事项均已关闭',False,True),
    ('ARCHIVE_PROCESS','项目过程记录已归档',False,False),
    ('ARCHIVE_DESIGN','设计及版本记录已归档',False,False),
    ('ARCHIVE_PROCUREMENT','采购及合同记录已归档',False,False),
    ('ARCHIVE_QUALITY_DELIVERY','质量、交付及验收记录已归档',False,False),
    ('ARCHIVE_CHANGE','设变记录已归档',True,False),
    ('ARCHIVE_FINANCE','财务记录已归档',False,False),
)

TERMINATION_ITEMS = (
    ('TERMINATION_NOTICE','客户终止依据已核对',False,True),
    ('CURRENT_STAGE','终止时当前环节已记录',False,True),
    ('COMPLETED_WORK','已完成工作已统计',False,True),
    ('INCURRED_COST','已发生费用已统计',False,True),
    ('OPEN_PROCUREMENT_DISPOSITION','未完成采购已取消、交接或处置',True,False),
    ('WIP_DISPOSITION','在制品已交接或处置',True,False),
    ('SUPPLIER_TASK_DISPOSITION','供应商任务已取消、交接或处置',True,False),
    ('SUPPLIER_SETTLEMENT','供应商结算已核对或说明不适用',True,False),
    ('CUSTOMER_SETTLEMENT','客户终止结算金额及确认依据已核对',False,False),
    ('RECEIVABLE_PAYABLE','终止相关收付款已核对',False,False),
    ('DELIVERY_DISPOSITION','交付事项已完成或说明不适用',True,False),
    ('ACCEPTANCE_DISPOSITION','验收事项已完成或说明不适用',True,False),
    ('OPEN_ISSUES','异常及工程联络事项均已关闭',False,True),
    ('ARCHIVE','项目、设计、采购、质量、交付、设变及财务记录已归档',False,False),
)


def _pending_subject(db,project_id,exclude_id=None):
    query=select(m.BusinessSubject.id).where(m.BusinessSubject.project_id==project_id,
        m.BusinessSubject.kind=='project_close',m.BusinessSubject.status.in_(['DRAFT','SUBMITTED','APPROVED','APPLY_BLOCKED']))
    if exclude_id:query=query.where(m.BusinessSubject.id!=exclude_id)
    return db.scalar(query.limit(1))


def _open_case(db,project_id,lock=False):
    query=select(m.ProjectClosureCase).where(m.ProjectClosureCase.project_id==project_id,
        m.ProjectClosureCase.status=='OPEN').order_by(m.ProjectClosureCase.created_at.desc())
    if lock:query=query.with_for_update()
    cases=list(db.scalars(query))
    if len(cases)>1:raise DomainError('CLOSURE_STATE_INVALID','项目存在多个未关闭结项清单，请先核对',409)
    return cases[0] if cases else None


def system_facts(db,project_id):
    plan=None
    from domain_packs.mold.erp.core.domains import active_plan
    plan=active_plan(db,project_id)
    tasks=[] if not plan else list(db.scalars(select(m.PlanTask).where(m.PlanTask.plan_id==plan.id)))
    open_contacts=db.scalar(select(func.count()).select_from(m.ContactCase).where(
        m.ContactCase.project_id==project_id,m.ContactCase.closed_at.is_(None),m.ContactCase.mode=='ONLINE')) or 0
    payment_reservations=db.scalar(select(func.coalesce(func.sum(m.PaymentRequestDetail.reservation),0)).join(
        m.BusinessSubject,m.BusinessSubject.id==m.PaymentRequestDetail.subject_id).where(
        m.BusinessSubject.project_id==project_id)) or Decimal(0)
    open_orders=db.scalar(select(func.count()).select_from(m.PurchaseOrder).where(
        m.PurchaseOrder.project_id==project_id,m.PurchaseOrder.status.in_(['DRAFT','ISSUED']))) or 0
    return {'active_plan_number':plan.number if plan else None,'plan_task_count':len(tasks),
        'unfinished_plan_tasks':sum(1 for task in tasks if task.status!='DONE'),
        'open_contact_cases':open_contacts,'open_payment_reservations':str(payment_reservations),
        'open_local_purchase_orders':open_orders}


def _initial_state(key,mode,facts,seed):
    if key=='PLAN_COMPLETION':
        done=bool(facts['active_plan_number']) and facts['unfinished_plan_tasks']==0
        return ('DONE' if done else 'PENDING',
            '有效计划全部完成' if done else '未找到有效计划或仍有未完成计划任务','agent_db 当前事实')
    if key=='OPEN_ISSUES':
        done=facts['open_contact_cases']==0
        return ('DONE' if done else 'PENDING',
            '未发现未关闭的线上工程联络事项' if done else f"仍有 {facts['open_contact_cases']} 项线上工程联络事项未关闭",'agent_db 当前事实')
    if mode=='TERMINATION':
        seeded={'TERMINATION_NOTICE':('客户终止依据已随终止申请固化',seed['evidence']),
            'CURRENT_STAGE':(seed['current_stage'],'终止申请记录'),
            'COMPLETED_WORK':(seed['completed_work_summary'],'终止申请记录'),
            'INCURRED_COST':(seed['incurred_cost_summary'],'终止申请记录')}
        if key in seeded:return ('DONE',*seeded[key])
    return ('PENDING','','')


def _add_revision(db,user,item,from_status=None):
    db.add(m.ProjectClosureItemRevision(item_id=item.id,revision=item.revision,from_status=from_status,
        to_status=item.status,result=item.result,evidence=item.evidence,source_system=item.source_system,
        source_ref=item.source_ref,source_as_of=item.source_as_of,changed_by=user.id))


def create_case(db,user,project,mode,current_stage,seed=None,source_subject_id=None):
    seed=seed or {}
    existing=_open_case(db,project.id,True)
    if existing:
        if mode!='TERMINATION':raise DomainError('CLOSURE_CASE_EXISTS','项目已有未关闭结项清单',409)
        existing.status='CANCELLED';existing.version+=1
        record(db,user,'project.closure.cancelled',existing.id,{'reason':'项目转入终止处置'})
    case=m.ProjectClosureCase(project_id=project.id,mode=mode,status='OPEN',current_stage=current_stage,
        opened_by=user.id,source_termination_subject_id=source_subject_id)
    db.add(case);db.flush()
    facts=system_facts(db,project.id)
    for key,label,allow_na,system_managed in (NORMAL_ITEMS if mode=='NORMAL' else TERMINATION_ITEMS):
        status,result,evidence=_initial_state(key,mode,facts,seed)
        item=m.ProjectClosureItem(case_id=case.id,item_key=key,label=label,status=status,
            allow_not_applicable=allow_na,system_managed=system_managed,result=result,evidence=evidence,
            source_system='AGENT' if system_managed else 'MANUAL',source_as_of=now() if system_managed else None,
            updated_by=user.id,updated_at=now())
        db.add(item);db.flush();_add_revision(db,user,item)
    record(db,user,'project.closure.opened',case.id,{'project_id':project.id,'mode':mode})
    return case


def open_normal_case(db,user,project_id,project_version,current_stage,reason):
    project=db.scalar(select(m.Project).where(m.Project.id==project_id).with_for_update())
    if not project:raise DomainError('NOT_FOUND','项目不存在',404)
    require(db,user,'project.read',{'project_id':project.id});require(db,user,'project_close.create',{'project_id':project.id})
    if project.status!='ACTIVE':raise DomainError('CLOSE_STATE','只有执行中的项目可发起正常结项清单',409)
    if project.row_version!=project_version:raise DomainError('VERSION_CONFLICT','项目状态已变化，请重新查询',409)
    if _pending_subject(db,project.id):raise DomainError('CLOSE_REQUEST_EXISTS','项目已有待处理的终止或关闭申请',409)
    case=create_case(db,user,project,'NORMAL',current_stage)
    record(db,user,'project.closure.reason',case.id,{'reason':reason})
    return case


def update_item(db,user,case_id,case_version,item_key,status,result,evidence,source_system,source_ref,source_as_of):
    case=db.scalar(select(m.ProjectClosureCase).where(m.ProjectClosureCase.id==case_id).with_for_update())
    if not case:raise DomainError('NOT_FOUND','结项清单不存在',404)
    require(db,user,'project_close.execute',{'project_id':case.project_id})
    if case.status!='OPEN':raise DomainError('CLOSURE_FINAL','结项清单已结束，不能覆盖历史',409)
    if case.version!=case_version:raise DomainError('VERSION_CONFLICT','结项清单已变化，请重新查询',409)
    if _pending_subject(db,case.project_id):raise DomainError('CLOSE_REQUEST_EXISTS','关闭申请已提交，清单暂不可修改',409)
    item=db.scalar(select(m.ProjectClosureItem).where(m.ProjectClosureItem.case_id==case.id,
        m.ProjectClosureItem.item_key==item_key).with_for_update())
    if not item:raise DomainError('NOT_FOUND','结项事项不存在',404)
    if item.system_managed:raise DomainError('SYSTEM_CHECK','该事项由系统事实校验，不能人工覆盖',409)
    if status=='NOT_APPLICABLE' and not item.allow_not_applicable:
        raise DomainError('NOT_APPLICABLE_FORBIDDEN','该关闭条件不能标记为不适用',409)
    if source_system=='ERP' and (not source_ref or not source_as_of):
        raise DomainError('ERP_REFERENCE_REQUIRED','ERP 来源须填写原记录引用和核对时点',409)
    previous=item.status;item.status=status;item.result=result;item.evidence=evidence
    item.source_system=source_system;item.source_ref=source_ref;item.source_as_of=source_as_of
    item.updated_by=user.id;item.updated_at=now();item.revision+=1;case.version+=1
    _add_revision(db,user,item,previous)
    record(db,user,'project.closure.item.updated',item.id,{'case_id':case.id,'item_key':item_key,
        'from':previous,'to':status,'revision':item.revision})
    return case,item


def items(db,case_id):
    return list(db.scalars(select(m.ProjectClosureItem).where(m.ProjectClosureItem.case_id==case_id).order_by(
        m.ProjectClosureItem.created_at,m.ProjectClosureItem.item_key)))


def _live_system_state(item,mode,facts):
    if not item.system_managed or item.item_key not in {'PLAN_COMPLETION','OPEN_ISSUES'}:
        return item.status,item.result,item.evidence
    return _initial_state(item.item_key,mode,facts,{})


def refresh_system_items(db,user,case):
    facts=system_facts(db,case.project_id)
    for item in items(db,case.id):
        if not item.system_managed:continue
        status,result,evidence=_live_system_state(item,case.mode,facts)
        if (item.status,item.result,item.evidence)==(status,result,evidence):continue
        previous=item.status;item.status=status;item.result=result;item.evidence=evidence
        item.source_system='AGENT';item.source_as_of=now();item.updated_by=user.id;item.updated_at=now();item.revision+=1
        case.version+=1;_add_revision(db,user,item,previous)


def blockers(db,case):
    result=[]
    facts=system_facts(db,case.project_id)
    for item in items(db,case.id):
        status,_,_=_live_system_state(item,case.mode,facts)
        if status=='PENDING':result.append(item.label)
        elif status=='NOT_APPLICABLE' and not item.allow_not_applicable:result.append(item.label+'（不得标记为不适用）')
    if case.mode=='NORMAL' and (not facts['active_plan_number'] or facts['unfinished_plan_tasks']):
        result.append('有效计划仍有未完成任务')
    if facts['open_contact_cases']:result.append('仍有未关闭的工程联络事项')
    if Decimal(facts['open_payment_reservations'])!=0:result.append('仍有未释放的付款占用')
    if facts['open_local_purchase_orders']:result.append('仍有未关闭的 Agent 本地采购订单')
    return list(dict.fromkeys(result))


def assert_ready(db,project,case_id,case_version,mode):
    case=db.scalar(select(m.ProjectClosureCase).where(m.ProjectClosureCase.id==case_id,
        m.ProjectClosureCase.project_id==project.id).with_for_update())
    if not case or case.mode!=mode or case.status!='OPEN':raise DomainError('CLOSURE_CASE_INVALID','结项清单不存在、类型不符或已结束',409)
    if case.version!=case_version:raise DomainError('VERSION_CONFLICT','结项清单已变化，请重新核对',409)
    pending=blockers(db,case)
    if pending:raise DomainError('CLOSE_BLOCKED','仍有未完成关闭事项：'+'；'.join(pending[:8]),409)
    return case


def validate_close_detail(db,project,detail,subject_id=None):
    if detail.effective_date>now().date():raise DomainError('DATE_INVALID','终止或关闭日期不能晚于当前日期')
    if project.row_version!=detail.project_version:raise DomainError('VERSION_CONFLICT','项目状态已变化，请重新查询',409)
    if _pending_subject(db,project.id,subject_id):raise DomainError('CLOSE_REQUEST_EXISTS','项目已有待处理的终止或关闭申请',409)
    if bool(detail.incurred_cost_amount is not None)!=bool(detail.currency):
        raise DomainError('CURRENCY_REQUIRED','已发生费用金额与币种须同时填写')
    if detail.decision=='TERMINATE':
        if project.status not in {'ACTIVE','PAUSED'}:raise DomainError('CLOSE_STATE','只有执行中或暂停中的项目可终止',409)
        if detail.closure_case_id or detail.closure_case_version:
            raise DomainError('CLOSURE_CASE_INVALID','终止申请不能复用正常关闭清单')
        if not all([detail.current_stage,detail.completed_work_summary,detail.incurred_cost_summary]):
            raise DomainError('TERMINATION_INCOMPLETE','终止须记录当前环节、已完成工作和已发生费用')
        return None
    if not detail.closure_case_id or not detail.closure_case_version:
        raise DomainError('CLOSURE_CASE_REQUIRED','最终关闭须关联已核对的结项清单')
    expected='NORMAL' if detail.decision=='NORMAL_CLOSE' else 'TERMINATION'
    if expected=='NORMAL' and project.status!='ACTIVE':raise DomainError('CLOSE_STATE','正常关闭要求项目处于执行状态',409)
    if expected=='TERMINATION' and project.status!='TERMINATED':raise DomainError('CLOSE_STATE','终止结算关闭要求项目已终止',409)
    return assert_ready(db,project,detail.closure_case_id,detail.closure_case_version,expected)


def apply_subject(db,user,subject):
    project=db.scalar(select(m.Project).where(m.Project.id==subject.project_id).with_for_update())
    detail=db.get(m.ProjectClosureDetail,subject.id)
    validate_close_detail(db,project,detail,subject.id)
    if detail.decision=='TERMINATE':
        from domain_packs.mold.erp.core.domains import active_plan
        plan=active_plan(db,project.id)
        stopped=0
        if plan:
            for task in db.scalars(select(m.PlanTask).where(m.PlanTask.plan_id==plan.id).with_for_update()):
                if task.status!='DONE':task.status='STOPPED';stopped+=1
        case=create_case(db,user,project,'TERMINATION',detail.current_stage,{
            'evidence':detail.evidence,'current_stage':detail.current_stage,
            'completed_work_summary':detail.completed_work_summary,
            'incurred_cost_summary':detail.incurred_cost_summary},subject.id)
        detail.closure_case_id=case.id;detail.closure_case_version=case.version
        project.status='TERMINATED'
        profile=db.get(m.ProjectProfile,project.id)
        if profile:profile.settlement_status='TERMINATION_PENDING'
        record(db,user,'project.terminated',project.id,{'subject_id':subject.id,'stopped_local_tasks':stopped,'closure_case_id':case.id})
    else:
        mode='NORMAL' if detail.decision=='NORMAL_CLOSE' else 'TERMINATION'
        case=assert_ready(db,project,detail.closure_case_id,detail.closure_case_version,mode)
        refresh_system_items(db,user,case)
        case.status='CLOSED';case.closed_by=user.id;case.closed_at=now();case.version+=1
        project.status='CLOSED'
        profile=db.get(m.ProjectProfile,project.id)
        if profile:profile.settlement_status='CLOSED_NORMAL' if mode=='NORMAL' else 'CLOSED_TERMINATION'
        record(db,user,'project.closed',project.id,{'subject_id':subject.id,'mode':mode,'closure_case_id':case.id})
    project.row_version+=1


def context(db,user,project_id):
    project=db.get(m.Project,project_id)
    if not project:raise DomainError('NOT_FOUND','项目不存在',404)
    require(db,user,'project.read',{'project_id':project.id});require(db,user,'project_close.read',{'project_id':project.id})
    case=_open_case(db,project.id)
    if not case:
        case=db.scalar(select(m.ProjectClosureCase).where(m.ProjectClosureCase.project_id==project.id).order_by(
            m.ProjectClosureCase.created_at.desc()).limit(1))
    item_data=[]
    if case:
        facts=system_facts(db,project.id)
        for item in items(db,case.id):
            revision_count=db.scalar(select(func.count()).select_from(m.ProjectClosureItemRevision).where(
                m.ProjectClosureItemRevision.item_id==item.id)) or 0
            live_status,live_result,live_evidence=_live_system_state(item,case.mode,facts)
            item_data.append({'item_key':item.item_key,'label':item.label,'status':live_status,
                'allow_not_applicable':item.allow_not_applicable,'system_managed':item.system_managed,
                'result':live_result,'evidence':live_evidence,'source_system':item.source_system,
                'source_ref':item.source_ref,'source_as_of':item.source_as_of,'revision':item.revision,
                'history_count':revision_count})
    return {'project_id':project.id,'project_code':project.code,'project_name':project.name,
        'project_status':project.status,'project_version':project.row_version,'system_facts':system_facts(db,project.id),
        'closure_case':None if not case else {'case_id':case.id,'mode':case.mode,'status':case.status,
            'current_stage':case.current_stage,'version':case.version,'source_termination_subject_id':case.source_termination_subject_id,
            'items':item_data,'blockers':blockers(db,case) if case.status=='OPEN' else []}}
