from collections import defaultdict
from pydantic import Field, model_validator
from sqlalchemy import select, and_
from . import models as m
from .authorization import access, predicate, select_fields
from .db import now
from .errors import DomainError
from .schemas import StrictModel


class ProjectPlanContextInput(StrictModel):
    project_id: str | None = Field(default=None, min_length=1, max_length=36)
    identifier: str | None = Field(default=None, min_length=1, max_length=200,
        description='项目编号/名称、计划单号、计划任务关键字、模具号或其他可见业务线索。')

    @model_validator(mode='after')
    def one_locator(self):
        if bool(self.project_id)==bool(self.identifier):
            raise ValueError('project_id 和 identifier 须且只能填写一项')
        if self.identifier:
            self.identifier=self.identifier.strip()
            if not self.identifier:raise ValueError('线索不能为空')
        return self


def _strength(value,needle):
    if value is None:return 0
    value=str(value).casefold();needle=str(needle).casefold()
    return 100 if value==needle else 50 if needle in value else 0


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
    return {'active_plan':active,'tasks':tasks,'running_tasks':running[:20],'overdue_tasks':overdue[:20],
        'dependency_blocked_tasks':blocked[:20],'open_plan_changes':open_changes[:20],
        'customer_due_risk_tasks':due_risk[:20],'milestone_coverage':_milestone_coverage(tasks),
        'warnings':warnings,'derived_status':{
            'has_effective_plan':bool(active),
            'has_open_plan_change':bool(open_changes),
            'has_overdue_task':bool(overdue),
            'has_customer_due_risk':bool(due_risk),
            'project_status':project.status}}


def query(db,user,data:ProjectPlanContextInput,allowed_tools:set[str]):
    project,alternatives,truncated=_resolve(db,user,data,allowed_tools)
    limitations=['只读取当前用户可见且具备项目计划读取权限的项目。',
                 '本工具只核对计划、节点和依赖上下文，不重排计划、不生成甘特图、不下达部门任务。',
                 '55天周期、自然日/工作日、节假日和齐套率口径仍须按项目适配确认，不能由本工具默认承诺。']
    if truncated:limitations.append('最多检查前500个可见项目，结果可能未覆盖全部可见范围。')
    if project:
        records=_records(db,user,project.id,allowed_tools)
        profile=_profile(db,user,project.id)
        skipped=[]
        if 'query_plan_change' not in allowed_tools:skipped.append('计划变更')
        if skipped:limitations.append('未分配对应查询工具，未返回：'+'、'.join(skipped))
        analysis=_analysis(project,profile,records)
        return {'resolution':'RESOLVED','data':[{'project':_project_card(db,user,project,alternatives or ('项目定位',)),
            'profile':profile,'project_plans':records['project_plan'],'plan_changes':records['plan_change'],
            'analysis':analysis}],
            'source':'agent_db','as_of':now().isoformat(),'limitations':limitations}
    if alternatives is None:
        return {'resolution':'NOT_FOUND_OR_FORBIDDEN','data':[],'source':'agent_db','as_of':now().isoformat(),
                'limitations':limitations}
    if alternatives:
        return {'resolution':'MULTIPLE_CANDIDATES','data':alternatives,'source':'agent_db','as_of':now().isoformat(),
                'limitations':limitations+['线索命中多个候选项目，请使用项目 ID 或更完整编号后再查询。']}
    return {'resolution':'NOT_FOUND','data':[],'source':'agent_db','as_of':now().isoformat(),
            'limitations':limitations}
