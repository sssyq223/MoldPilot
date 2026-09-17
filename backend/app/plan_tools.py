from collections import defaultdict
from datetime import date
from pydantic import Field, ValidationError
from fastapi import APIRouter, Depends
from sqlalchemy import select, and_
from . import models as m, domains, domain_schemas as s, workflow_selection
from .authorization import access, predicate, require, select_fields, fingerprint
from .bpm import content_hash
from .db import get_db, now
from .errors import DomainError
from .security import current_user
from .schemas import StrictModel
from agent_core.domain_pack import component


_contracts = component("contracts")
ProjectPlanContextInput = _contracts.ProjectPlanContextInput
_strength = _contracts.match_strength

class PlanChangeProposalInput(StrictModel):
    project_id: str = Field(min_length=1, max_length=36)
    project_version: int = Field(ge=1)
    previous_id: str = Field(min_length=1, max_length=36,
        description='查询返回的当前有效 project_plan 或 plan_change 业务材料 ID。')
    reason: str = Field(min_length=1, max_length=4000)
    tasks: list[s.TaskInput] = Field(min_length=1, max_length=200)
    workflow_definition_id: str = Field(min_length=1, max_length=36)
    material_review_id: str | None = Field(default=None, min_length=1, max_length=36,
        description='当审批流程绑定资料模板时，填写本人已确认的资料核对包 ID；无资料模板流程保持 null。')


class PlanBaselineProposalInput(StrictModel):
    project_id: str = Field(min_length=1, max_length=36)
    project_version: int = Field(ge=1)
    reason: str = Field(min_length=1, max_length=4000)
    tasks: list[s.TaskInput] = Field(min_length=1, max_length=200)
    workflow_definition_id: str = Field(min_length=1, max_length=36)
    material_review_id: str | None = Field(default=None, min_length=1, max_length=36,
        description='当审批流程绑定资料模板时，填写本人已确认的资料核对包 ID；无资料模板流程保持 null。')


class PlanDepartmentConfirmationProposalInput(StrictModel):
    confirmation_id: str = Field(min_length=1, max_length=36,
        description='query_project_plan_context 返回的待确认部门影响项 ID。')
    expected_version: int = Field(ge=1,
        description='query_project_plan_context 返回的部门确认项 version。')
    note: str = Field(min_length=1, max_length=1000,
        description='本部门已核对计划变更影响的说明或依据。')


def _project_card(db,user,project,matched_by=()):
    fields=access(db,user,'project.read',{'project_id':project.id}).fields
    card=select_fields({'id':project.id,'code':project.code,'name':project.name,'status':project.status,
                        'row_version':project.row_version},fields)
    card['matched_by']=sorted(set(matched_by))
    return card


def _visible_projects(db,user):
    gate=and_(predicate(db,user,'project.read',{'project_id':m.Project.id}),
              predicate(db,user,'project_plan.read',{'project_id':m.Project.id}))
    rows=list(db.scalars(select(m.Project).where(gate).order_by(m.Project.code).limit(501)))
    return rows[:500],len(rows)>500


def _visible_plan_subjects(db,user,project_ids,kind,allowed_tools):
    if kind=='project_plan':
        if 'query_project_plan_context' not in allowed_tools and 'query_project_plan' not in allowed_tools:return []
    elif kind=='plan_change':
        if 'query_plan_change' not in allowed_tools and 'prepare_project_plan_change' not in allowed_tools:return []
    elif 'query_'+kind not in allowed_tools:return []
    from .domains import data as subject_data
    result=[]
    for subject in db.scalars(select(m.BusinessSubject).where(m.BusinessSubject.project_id.in_(project_ids),
        m.BusinessSubject.kind==kind).order_by(m.BusinessSubject.created_at.desc(),m.BusinessSubject.id).limit(501)):
        try:result.append(subject_data(db,user,subject))
        except DomainError:continue
    return result[:500]


def _resolve(db,user,data:ProjectPlanContextInput,allowed_tools:set[str]):
    visible,truncated=_visible_projects(db,user)
    by_id={project.id:project for project in visible}
    if data.project_id:
        project=by_id.get(data.project_id)
        return project,([] if project else None),truncated
    scores=defaultdict(int);reasons=defaultdict(list)
    def add(project_id,value,label):
        if project_id not in by_id:return
        score=_strength(value,data.identifier)
        if score:
            scores[project_id]=max(scores[project_id],score)
            reasons[project_id].append(label)
    for project in visible:
        add(project.id,project.id,'项目ID');add(project.id,project.code,'项目编号');add(project.id,project.name,'项目名称')
    if by_id:
        for kind,label in (('project_plan','项目计划'),('plan_change','计划变更')):
            for subject in _visible_plan_subjects(db,user,list(by_id),kind,allowed_tools):
                add(subject.get('project_id'),subject.get('id'),label+'ID')
                add(subject.get('project_id'),subject.get('number'),label+'业务单号')
                for task in (subject.get('detail') or {}).get('tasks',[]) if isinstance(subject.get('detail'),dict) else []:
                    add(subject.get('project_id'),task.get('key'),label+'任务标识')
                    add(subject.get('project_id'),task.get('name'),label+'任务名称')
        if 'query_business_object_candidates' in allowed_tools:
            from .business_matching import BusinessMatchInput,query as match_query
            matches=match_query(db,user,BusinessMatchInput(identifier=data.identifier),allowed_tools)
            for row in matches.get('data',[]):
                project=row.get('project',{})
                project_id=project.get('id')
                if project_id in by_id:
                    scores[project_id]=max(scores[project_id],100 if row.get('match_quality')=='EXACT' else 50)
                    reasons[project_id].extend('候选匹配：'+e.get('label','线索') for e in row.get('evidence',[])[:5])
    if not scores:return None,[],truncated
    best=max(scores.values());ids=[pid for pid,score in scores.items() if score==best]
    if len(ids)!=1:return None,[_project_card(db,user,by_id[pid],reasons[pid]) for pid in ids[:20]],truncated
    return by_id[ids[0]],reasons[ids[0]],truncated


def _records(db,user,project_id,allowed_tools):
    return {
        'project_plan':_visible_plan_subjects(db,user,[project_id],'project_plan',allowed_tools)[:20],
        'plan_change':_visible_plan_subjects(db,user,[project_id],'plan_change',allowed_tools)[:20],
    }


def _profile(db,user,project_id):
    fields=access(db,user,'project.read',{'project_id':project_id}).fields
    profile=db.get(m.ProjectProfile,project_id)
    if not profile:return None
    return select_fields({'customer_due_date':profile.customer_due_date.isoformat() if profile.customer_due_date else None,
        'execution_mode':profile.execution_mode,'settlement_status':profile.settlement_status},fields | {'customer_due_date','execution_mode','settlement_status'})


MILESTONES={
    'design':['设计','工艺','结构','出图','drawing','design'],
    'purchase':['采购','原材料','五金','委外','purchase','material','outsource'],
    'machining':['加工','工序','生产','manufactur','machin'],
    'assembly':['装配','assembly'],
    'trial':['试模','调试','trial','debug'],
    'delivery':['交付','出库','验收','delivery','shipment','acceptance'],
}


def _milestone_coverage(tasks):
    matched=defaultdict(list)
    for task in tasks:
        text=(str(task.get('key',''))+' '+str(task.get('name',''))).casefold()
        for group,keywords in MILESTONES.items():
            if any(keyword.casefold() in text for keyword in keywords):
                matched[group].append({'id':task.get('id'),'key':task.get('key'),'name':task.get('name')})
    return {'covered':dict(matched),
        'missing':[group for group in MILESTONES if group not in matched],
        'note':'大节点覆盖按任务名称/标识作辅助核对，不能替代项目负责人按实际模具类型确认。'}


def _parse_date(value):
    if not value:return None
    if isinstance(value,date):return value
    try:return date.fromisoformat(str(value)[:10])
    except ValueError:return None


def _duration_days(start,end):
    start_date=_parse_date(start);end_date=_parse_date(end)
    if not start_date or not end_date:return None
    return max((end_date-start_date).days+1,0)


def _task_lane(task):
    if task.get('status')=='DONE' or task.get('actual_end'):return 'done'
    if task.get('status')=='RUNNING' or task.get('actual_start'):return 'running'
    return 'not_started'


def _compact_plan_card(row):
    return {'id':row.get('id'),'key':row.get('key'),'name':row.get('name'),'status':row.get('status'),
        'planned_start':row.get('planned_start'),'planned_end':row.get('planned_end'),
        'risk_flags':row.get('risk_flags',[])}


def _plan_visualization(analysis,erp_execution_progress=None):
    tasks=analysis.get('tasks') or []
    blocked_waits={row.get('key'):row.get('waiting_for',[]) for row in analysis.get('dependency_blocked_tasks',[])}
    overdue_keys={row.get('key') for row in analysis.get('overdue_tasks',[])}
    due_risk_keys={row.get('key') for row in analysis.get('customer_due_risk_tasks',[])}
    columns={'not_started':[],'running':[],'done':[]}
    risk_lanes={'overdue':[],'blocked':[],'customer_due_risk':[]}
    timeline=[]
    for task in sorted(tasks,key=lambda item:(str(item.get('planned_start') or ''),str(item.get('planned_end') or ''),str(item.get('key') or ''))):
        key=task.get('key')
        risk_flags=[]
        if key in overdue_keys:risk_flags.append('OVERDUE')
        if key in blocked_waits:risk_flags.append('BLOCKED_BY_PREREQUISITE')
        if key in due_risk_keys:risk_flags.append('CUSTOMER_DUE_RISK')
        row={'id':task.get('id'),'key':key,'name':task.get('name'),'status':task.get('status'),
            'lane':_task_lane(task),'owner_user_id':task.get('owner_user_id'),
            'planned_start':task.get('planned_start'),'planned_end':task.get('planned_end'),
            'actual_start':task.get('actual_start'),'actual_end':task.get('actual_end'),
            'duration_days':_duration_days(task.get('planned_start'),task.get('planned_end')),
            'prerequisites':task.get('prerequisites',[]),
            'waiting_for':blocked_waits.get(key,[]),
            'risk_flags':risk_flags}
        timeline.append(row)
        columns[row['lane']].append(_compact_plan_card(row))
        if 'OVERDUE' in risk_flags:risk_lanes['overdue'].append(_compact_plan_card(row))
        if 'BLOCKED_BY_PREREQUISITE' in risk_flags:risk_lanes['blocked'].append(_compact_plan_card(row))
        if 'CUSTOMER_DUE_RISK' in risk_flags:risk_lanes['customer_due_risk'].append(_compact_plan_card(row))
    erp_status=(erp_execution_progress or {}).get('status')
    return {'kind':'project_plan_visualization_v1','timeline':timeline,
        'kanban':{'columns':columns,'risk_lanes':risk_lanes},
        'external_progress':{'source':'ERP','status':erp_status or 'NOT_QUERIED',
            'available':bool(erp_status=='RESOLVED' and (erp_execution_progress or {}).get('records'))},
        'legend':{'lanes':{'not_started':'未开始','running':'进行中','done':'已完成'},
            'risk_flags':{'OVERDUE':'计划结束日早于今天且未完成',
                'BLOCKED_BY_PREREQUISITE':'前置节点未完成',
                'CUSTOMER_DUE_RISK':'计划结束日晚于客户承诺日期且未完成'}},
        'limitations':['该结构用于对话和右侧面板渲染时间线/看板，不等同于最终交互式甘特图。',
            '工作日、节假日、资源负荷、齐套率和拖拽改期规则仍需按项目适配确认。']}


def _analysis(project,profile,records):
    today=now().date().isoformat()
    plans=records['project_plan']+records['plan_change']
    effective=[row for row in plans if row.get('status')=='EFFECTIVE']
    open_changes=[row for row in records['plan_change'] if row.get('status') in {'DRAFT','SUBMITTED','RETURNED','APPLY_BLOCKED'}]
    active=max(effective,key=lambda row:row.get('created_at','')) if effective else None
    tasks=(active.get('detail') or {}).get('tasks',[]) if active and isinstance(active.get('detail'),dict) else []
    by_key={task.get('key'):task for task in tasks}
    running=[task for task in tasks if task.get('status')=='RUNNING']
    overdue=[task for task in tasks if task.get('status')!='DONE' and task.get('planned_end') and str(task['planned_end'])<today]
    blocked=[]
    for task in tasks:
        if task.get('status')=='DONE':continue
        missing=[key for key in task.get('prerequisites',[]) if (by_key.get(key) or {}).get('status')!='DONE']
        if missing:blocked.append({'id':task.get('id'),'key':task.get('key'),'name':task.get('name'),'waiting_for':missing})
    customer_due=(profile or {}).get('customer_due_date')
    due_risk=[]
    if customer_due:
        due_risk=[task for task in tasks if task.get('status')!='DONE' and task.get('planned_end') and str(task['planned_end'])>customer_due]
    warnings=[]
    if len(effective)>1:warnings.append('当前可见范围存在多个有效计划/变更记录，请先核对唯一有效版本。')
    if not active:warnings.append('当前可见范围未见有效项目计划，不能判断实际节点进度。')
    if open_changes:warnings.append('存在未完成的计划变更申请，当前计划可能即将变化。')
    if due_risk:warnings.append('存在计划结束晚于客户承诺日期的未完成节点，需项目负责人核对交期风险。')
    analysis={'active_plan':active,'tasks':tasks,'running_tasks':running[:20],'overdue_tasks':overdue[:20],
        'dependency_blocked_tasks':blocked[:20],'open_plan_changes':open_changes[:20],
        'customer_due_risk_tasks':due_risk[:20],'milestone_coverage':_milestone_coverage(tasks),
        'warnings':warnings,'derived_status':{
            'has_effective_plan':bool(active),
            'has_open_plan_change':bool(open_changes),
            'has_overdue_task':bool(overdue),
            'has_customer_due_risk':bool(due_risk),
            'project_status':project.status}}
    analysis['visualization']=_plan_visualization(analysis)
    return analysis


def query(db,user,data:ProjectPlanContextInput,allowed_tools:set[str]):
    project,alternatives,truncated=_resolve(db,user,data,allowed_tools)
    limitations=['只读取当前用户可见且具备项目计划读取权限的项目。',
                 '本工具只核对计划、节点、依赖和部门确认上下文，不重排计划、不生成甘特图、不下达 ERP 执行任务。',
                 '55天周期、自然日/工作日、节假日和齐套率口径仍须按项目适配确认，不能由本工具默认承诺。']
    if truncated:limitations.append('最多检查前500个可见项目，结果可能未覆盖全部可见范围。')
    if project:
        records=_records(db,user,project.id,allowed_tools)
        profile=_profile(db,user,project.id)
        skipped=[]
        if 'query_plan_change' not in allowed_tools and 'prepare_project_plan_change' not in allowed_tools:skipped.append('计划变更')
        if skipped:limitations.append('未分配对应查询工具，未返回：'+'、'.join(skipped))
        workflows=[];baseline_workflows=[]
        if 'prepare_project_plan_change' in allowed_tools:
            try:workflows=workflow_options(db,user,project,'plan_change')
            except DomainError as error:limitations.append('当前人员缺少计划变更读取或提交权限，未返回可选计划变更审批流程：'+error.message)
        if 'prepare_project_plan_baseline' in allowed_tools:
            try:baseline_workflows=workflow_options(db,user,project,'project_plan')
            except DomainError as error:limitations.append('当前人员缺少项目计划读取或提交权限，未返回可选基线计划审批流程：'+error.message)
        analysis=_analysis(project,profile,records)
        from . import plan_confirmations
        confirmations=[]
        try:confirmations=plan_confirmations.visible_for_project(db,user,project.id)
        except DomainError as error:limitations.append('当前人员缺少计划变更读取权限，未返回部门确认状态：'+error.message)
        from . import erp_progress
        erp_execution_progress=erp_progress.query_project_progress(db,user,project)
        analysis['visualization']=_plan_visualization(analysis,erp_execution_progress)
        limitations.extend(erp_execution_progress.get('limitations',[]))
        return {'resolution':'RESOLVED','data':[{'project':_project_card(db,user,project,alternatives or ('项目定位',)),
            'profile':profile,'project_plans':records['project_plan'],'plan_changes':records['plan_change'],
            'department_confirmations':confirmations,
            'erp_execution_progress':erp_execution_progress,
            'analysis':analysis,'workflow_options':workflows,'baseline_workflow_options':baseline_workflows}],
            'source':'agent_db','as_of':now().isoformat(),'limitations':limitations}
    if alternatives is None:
        return {'resolution':'NOT_FOUND_OR_FORBIDDEN','data':[],'source':'agent_db','as_of':now().isoformat(),
                'limitations':limitations}
    if alternatives:
        return {'resolution':'MULTIPLE_CANDIDATES','data':alternatives,'source':'agent_db','as_of':now().isoformat(),
                'limitations':limitations+['线索命中多个候选项目，请使用项目 ID 或更完整编号后再查询。']}
    return {'resolution':'NOT_FOUND','data':[],'source':'agent_db','as_of':now().isoformat(),
            'limitations':limitations}


def workflow_options(db,user,project,business_type='plan_change'):
    scope={'project_id':project.id}
    if business_type=='project_plan':
        require(db,user,'project_plan.read',scope)
        require(db,user,'project_plan.submit',scope)
    else:
        require(db,user,'plan_change.read',scope)
        require(db,user,'plan_change.submit',scope)
    rows=db.scalars(select(m.WorkflowDefinition).where(m.WorkflowDefinition.status=='PUBLISHED').order_by(
        m.WorkflowDefinition.process_key,m.WorkflowDefinition.version.desc()))
    result=[]
    for row in rows:
        if not workflow_selection.matches(row.config,{'business_type':business_type,'categories':set(),'design_type':None}):continue
        item=workflow_selection.metadata(row,db)
        item['material_required']=row.config.get('material_contract') is not None
        item['material_template_id']=row.material_template_id
        if row.material_template_id:
            template=db.get(m.MaterialTemplate,row.material_template_id)
            item['material_template_name']=template.name if template else None
        result.append(item)
    return result


def plan_change_schema():
    return PlanChangeProposalInput.model_json_schema()


def plan_baseline_schema():
    return PlanBaselineProposalInput.model_json_schema()


def department_confirmation_schema():
    return PlanDepartmentConfirmationProposalInput.model_json_schema()


def parse_plan_baseline(arguments):
    try:return PlanBaselineProposalInput.model_validate(arguments or {})
    except ValidationError as error:raise DomainError('INVALID_TOOL_INPUT','基线计划参数不完整或不符合要求：'+error.errors()[0]['msg']) from None


def parse_plan_change(arguments):
    try:return PlanChangeProposalInput.model_validate(arguments or {})
    except ValidationError as error:raise DomainError('INVALID_TOOL_INPUT','计划变更参数不完整或不符合要求：'+error.errors()[0]['msg']) from None


def parse_department_confirmation(arguments):
    try:return PlanDepartmentConfirmationProposalInput.model_validate(arguments or {})
    except ValidationError as error:raise DomainError('INVALID_TOOL_INPUT','部门确认参数不完整或不符合要求：'+error.errors()[0]['msg']) from None


def _task_label(task):
    return task.name+'（'+task.key+'：'+task.planned_start.isoformat()+' 至 '+task.planned_end.isoformat()+'）'


def preview_plan_baseline(db,user,data:PlanBaselineProposalInput):
    project=db.get(m.Project,data.project_id)
    if not project:raise DomainError('NOT_FOUND','项目不存在',404)
    scope={'project_id':project.id}
    require(db,user,'project.read',scope)
    require(db,user,'project_plan.read',scope)
    require(db,user,'project_plan.create',scope)
    require(db,user,'project_plan.submit',scope)
    if project.row_version!=data.project_version:
        raise DomainError('VERSION_CONFLICT','项目状态已变化，请重新查询后准备',409)
    if project.status!='ACTIVE':
        raise DomainError('START_REQUIRED','项目正式开工后才能准备基线计划',409)
    existing=db.scalar(select(m.BusinessSubject.id).where(m.BusinessSubject.project_id==project.id,
        m.BusinessSubject.kind.in_(['project_plan','plan_change']),
        m.BusinessSubject.status.in_(['DRAFT','SUBMITTED','RETURNED','APPLY_BLOCKED','EFFECTIVE'])).limit(1))
    if existing:
        raise DomainError('PLAN_EXISTS','项目已有计划或待处理计划申请，请通过计划变更或处理原申请',409)
    detail=s.PlanInput(previous_id=None,reason=data.reason,tasks=data.tasks)
    domains.validate_plan(db,project.id,detail)
    options=workflow_options(db,user,project,'project_plan')
    selected=next((item for item in options if item['id']==data.workflow_definition_id),None)
    if not selected:raise DomainError('WORKFLOW_MISMATCH','审批模板不可用，请重新查询流程选项',409)
    definition=db.get(m.WorkflowDefinition,data.workflow_definition_id)
    review=workflow_selection.validate_material_review(db,user,definition,data.material_review_id)
    coverage=_milestone_coverage([task.model_dump(mode='json') for task in data.tasks])
    display={'操作':'项目基线计划','项目':project.code+' · '+project.name,'项目版本':project.row_version,
        '计划原因':data.reason,'计划任务数':len(data.tasks),
        '计划节点':[_task_label(task) for task in data.tasks],
        '大节点覆盖':'已覆盖：'+('、'.join(coverage['covered'].keys()) or '无')+'；缺少：'+('、'.join(coverage['missing']) or '无'),
        '审批流程':selected['name']+' · 第'+str(selected['version'])+'版',
        '说明':'本人确认后仅创建基线计划材料并提交 Agent BPM；审批生效前不会下达 ERP 执行任务，也不会把内部计划变更为客户承诺交期。'}
    if selected.get('material_required'):
        display['资料核对包']='已确认 · '+review.review_hash[:12] if review else '未绑定'
    return detail,display


def preview_plan_change(db,user,data:PlanChangeProposalInput):
    project=db.get(m.Project,data.project_id)
    if not project:raise DomainError('NOT_FOUND','项目不存在',404)
    scope={'project_id':project.id}
    require(db,user,'project.read',scope)
    require(db,user,'project_plan.read',scope)
    require(db,user,'plan_change.read',scope)
    require(db,user,'plan_change.create',scope)
    require(db,user,'plan_change.submit',scope)
    if project.row_version!=data.project_version:
        raise DomainError('VERSION_CONFLICT','项目状态已变化，请重新查询后准备',409)
    previous=domains.require_source(db,data.previous_id,project.id,{'project_plan','plan_change'})
    detail=s.PlanInput(previous_id=data.previous_id,reason=data.reason,tasks=data.tasks)
    domains.validate_plan(db,project.id,detail)
    options=workflow_options(db,user,project,'plan_change')
    selected=next((item for item in options if item['id']==data.workflow_definition_id),None)
    if not selected:raise DomainError('WORKFLOW_MISMATCH','审批模板不可用，请重新查询流程选项',409)
    definition=db.get(m.WorkflowDefinition,data.workflow_definition_id)
    review=workflow_selection.validate_material_review(db,user,definition,data.material_review_id)
    previous_tasks={task.key:task for task in db.scalars(select(m.PlanTask).where(m.PlanTask.plan_id==previous.id))}
    changed=[];new=[];removed=[]
    incoming={task.key:task for task in data.tasks}
    for task in data.tasks:
        old=previous_tasks.get(task.key)
        if not old:new.append(_task_label(task));continue
        if old.name!=task.name or old.owner_user_id!=task.owner_user_id or old.planned_start!=task.planned_start or old.planned_end!=task.planned_end:
            changed.append(task.name+'（'+task.key+'：'+old.planned_start.isoformat()+'~'+old.planned_end.isoformat()+
                ' → '+task.planned_start.isoformat()+'~'+task.planned_end.isoformat()+'）')
    for key,old in previous_tasks.items():
        if key not in incoming:removed.append(old.name+'（'+old.key+'）')
    impact,_=domains.plan_change_impact_from_tasks(db,previous_tasks,incoming,data.previous_id,data.reason)
    affected_departments=[' · '.join([row['department'],
        '节点：'+('、'.join(row['task_keys']) or '无'),
        '责任人：'+'、'.join(user['name'] for user in row['users'])]) for row in impact.get('affected_departments',[])]
    display={'操作':'项目计划变更','项目':project.code+' · '+project.name,'项目版本':project.row_version,
        '原计划':previous.number+' · 第'+str(previous.revision)+'版','变更原因':data.reason,
        '计划任务数':len(data.tasks),'变更节点':changed or ['未调整已有节点日期或名称'],
        '新增节点':new or ['无'],'删除节点':removed or ['无'],
        '受影响部门':affected_departments or ['无'],
        '审批流程':selected['name']+' · 第'+str(selected['version'])+'版',
        '说明':'本人确认后仅创建计划变更材料并提交 Agent BPM；审批生效前不会关闭原计划、不会重排执行任务，也不会修改客户承诺交期。'}
    if selected.get('material_required'):
        display['资料核对包']='已确认 · '+review.review_hash[:12] if review else '未绑定'
    return detail,display


def preview_department_confirmation(db,user,data:PlanDepartmentConfirmationProposalInput):
    from . import plan_confirmations
    subject,row=plan_confirmations.require_confirmable(db,user,data.confirmation_id,data.expected_version)
    project=db.get(m.Project,row.project_id)
    return {'操作':'确认计划变更影响已核对',
        '项目':(project.code+' · '+project.name) if project else row.project_id,
        '计划变更单':subject.number+' · 第'+str(subject.revision)+'版',
        '确认部门':row.department,
        '确认项版本':row.version,
        '影响节点':row.task_keys or ['未列出'],
        '影响类型':row.change_types or ['未列出'],
        '当前候选确认人':'、'.join(person['name'] for person in plan_confirmations.serialize(db,row)['assigned_people']) or '部门负责人或超级管理员',
        '核对说明':data.note,
        '说明':'本人确认后只记录本部门影响已核对，不修改计划、不替代计划变更审批，也不修改 ERP 执行进度。'}


def execute_plan_tool(db,user,key,arguments,run=None):
    from .confirmation_policy import proposal_confirmation_policy
    if key=='prepare_project_plan_baseline':
        data=parse_plan_baseline(arguments)
        _,display=preview_plan_baseline(db,user,data)
        proposal={'kind':'project_plan_baseline','action':'project_plan','requires_approval':True,
            'input':data.model_dump(mode='json'),'display':display,
            'confirmation_policy':proposal_confirmation_policy(run,requires_approval=True)}
        return {'data':[],'source':'agent_proposal','as_of':now().isoformat(),'proposal':proposal,
            'limitations':['仅准备基线计划操作建议；本人确认后才创建业务材料并提交审批，审批完成前不改变计划或执行任务。']}
    if key=='prepare_project_plan_change':
        data=parse_plan_change(arguments)
        _,display=preview_plan_change(db,user,data)
        proposal={'kind':'project_plan_change','action':'plan_change','requires_approval':True,
            'input':data.model_dump(mode='json'),'display':display,
            'confirmation_policy':proposal_confirmation_policy(run,requires_approval=True)}
        return {'data':[],'source':'agent_proposal','as_of':now().isoformat(),'proposal':proposal,
            'limitations':['仅准备计划变更操作建议；本人确认后才创建业务材料并提交审批，审批完成前不改变原计划或执行任务。']}
    if key=='prepare_plan_department_confirmation':
        data=parse_department_confirmation(arguments)
        display=preview_department_confirmation(db,user,data)
        proposal={'kind':'plan_department_confirmation','action':'department_confirmation','requires_approval':False,
            'input':data.model_dump(mode='json'),'display':display,
            'confirmation_policy':proposal_confirmation_policy(run,requires_approval=False)}
        return {'data':[],'source':'agent_proposal','as_of':now().isoformat(),'proposal':proposal,
            'limitations':['仅准备部门影响确认建议；本人确认后才记录确认结果，不修改计划、不提交 ERP。']}
    raise DomainError('TOOL_UNKNOWN','工具未实现',403)


def source(db,user,step_id):
    from .tool_gateway import available_tools
    step=db.get(m.Step,step_id);run=db.get(m.Run,step.run_id) if step else None
    if not run or run.user_id!=user.id:raise DomainError('NOT_FOUND','操作建议不存在或无权访问',404)
    if run.status not in {'RUNNING','SUCCEEDED'}:raise DomainError('PROPOSAL_STOPPED','任务已停止，请重新准备操作',409)
    if run.security_version!=user.security_version or run.checkpoint.get('authorization_hash')!=fingerprint(db,user):
        raise DomainError('AUTHORIZATION_CHANGED','授权已变化，请重新准备操作',403)
    proposal=step.result.get('proposal')
    allowed={'prepare_project_plan_baseline','prepare_project_plan_change','prepare_plan_department_confirmation'}
    if step.tool not in available_tools(db,user) or step.tool not in allowed or not proposal:
        raise DomainError('TOOL_FORBIDDEN','操作能力不可用',403)
    return proposal


def validate_intent(db,user,payload):
    proposal=source(db,user,payload['step_id'])
    if content_hash(proposal)!=payload['proposal_hash']:raise DomainError('CONFIRMATION_INVALID','操作建议内容已变化',409)
    if proposal.get('action')=='department_confirmation':
        data=parse_department_confirmation(proposal['input'])
        display=preview_department_confirmation(db,user,data)
    elif proposal.get('action')=='project_plan':
        data=parse_plan_baseline(proposal['input'])
        _,display=preview_plan_baseline(db,user,data)
    else:
        data=parse_plan_change(proposal['input'])
        _,display=preview_plan_change(db,user,data)
    if content_hash(display)!=content_hash(proposal['display']):
        raise DomainError('VERSION_CONFLICT','项目、计划或流程资料已变化，请重新准备',409)
    return proposal,data


def confirm(db,user,payload):
    from .confirmation_policy import agent_permission_mode_from_proposal
    proposal,data=validate_intent(db,user,payload)
    if proposal.get('action')=='department_confirmation':
        from . import plan_confirmations
        result=plan_confirmations.confirm(db,user,data.confirmation_id,data.expected_version,data.note)
        return {'project_id':result['project_id'],'confirmation_id':result['id'],
            'plan_change_id':result['plan_change_id'],'department':result['department'],
            'action':'department_confirmation','status':result['status']}
    if proposal.get('action')=='project_plan':
        subject=domains.create(db,user,s.SubjectInput(kind='project_plan',project_id=data.project_id,
            remark=data.reason,detail={'previous_id':None,'reason':data.reason,
                'tasks':[task.model_dump(mode='json') for task in data.tasks]}))
        from .business import submit_subject
        submitted=submit_subject(db,user,subject.id,subject.revision,data.workflow_definition_id,
            material_review_id=data.material_review_id,
            agent_permission_mode=agent_permission_mode_from_proposal(proposal))
        return {'project_id':data.project_id,'subject_id':subject.id,'instance_id':submitted['instance_id'],
            'action':'project_plan','status':'SUBMITTED'}
    subject=domains.create(db,user,s.SubjectInput(kind='plan_change',project_id=data.project_id,
        remark=data.reason,detail={'previous_id':data.previous_id,'reason':data.reason,
            'tasks':[task.model_dump(mode='json') for task in data.tasks]}))
    from .business import submit_subject
    submitted=submit_subject(db,user,subject.id,subject.revision,data.workflow_definition_id,
        material_review_id=data.material_review_id,
        agent_permission_mode=agent_permission_mode_from_proposal(proposal))
    return {'project_id':data.project_id,'subject_id':subject.id,'instance_id':submitted['instance_id'],
        'action':'plan_change','status':'SUBMITTED'}


router=APIRouter()


@router.get('/api/project-plan-proposals/{step_id}')
def proposal_status(step_id:str,user=Depends(current_user),db=Depends(get_db)):
    source(db,user,step_id)
    intent=db.scalar(select(m.HumanIntent).where(m.HumanIntent.user_id==user.id,
        m.HumanIntent.action=='project_plan.execute',m.HumanIntent.resource_id==step_id,
        m.HumanIntent.receipt.is_not(None)).order_by(m.HumanIntent.created_at.desc()))
    return {'receipt':intent.receipt if intent else None}


@router.post('/api/project-plan-proposals/{step_id}/intent')
def intent(step_id:str,user=Depends(current_user),db=Depends(get_db)):
    from .business import create_intent
    proposal=source(db,user,step_id)
    payload={'step_id':step_id,'proposal_hash':content_hash(proposal)}
    result=create_intent(db,user,'project_plan.execute',step_id,payload)
    result['display']=proposal['display']
    result['confirmation_policy']=proposal.get('confirmation_policy')
    db.commit();return result
