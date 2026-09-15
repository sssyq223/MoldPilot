from collections import defaultdict
from pydantic import Field, model_validator
from sqlalchemy import select, and_
from . import models as m
from .authorization import access, predicate, select_fields
from .db import now
from .errors import DomainError
from .schemas import StrictModel


class StartReadinessInput(StrictModel):
    project_id: str | None = Field(default=None, min_length=1, max_length=36)
    identifier: str | None = Field(default=None, min_length=1, max_length=200,
        description='项目编号/名称、承接单号、开工通知单号、合同号、模具号等。')

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
              predicate(db,user,'internal_start.read',{'project_id':m.Project.id}))
    rows=list(db.scalars(select(m.Project).where(gate).order_by(m.Project.code).limit(501)))
    return rows[:500],len(rows)>500


def _visible_subjects(db,user,project_ids,kind,allowed_tools):
    if kind=='internal_start':
        if 'query_internal_start_readiness' not in allowed_tools and 'query_internal_start' not in allowed_tools:return []
    elif 'query_'+kind not in allowed_tools:return []
    from .domains import data as subject_data
    result=[]
    for subject in db.scalars(select(m.BusinessSubject).where(m.BusinessSubject.project_id.in_(project_ids),
        m.BusinessSubject.kind==kind).order_by(m.BusinessSubject.created_at.desc(),m.BusinessSubject.id).limit(501)):
        try:result.append(subject_data(db,user,subject))
        except DomainError:continue
    return result[:500]


def _resolve(db,user,data:StartReadinessInput,allowed_tools:set[str]):
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
        for kind,label in (('internal_start','开工通知'),('quote_acceptance','承接决定'),
                           ('sales_contract','销售合同'),('full_outsource_contract','整套委外合同')):
            for subject in _visible_subjects(db,user,list(by_id),kind,allowed_tools):
                detail=subject.get('detail') if isinstance(subject.get('detail'),dict) else {}
                add(subject.get('project_id'),subject.get('id'),label+'ID')
                add(subject.get('project_id'),subject.get('number'),label+'业务单号')
                if kind in {'sales_contract','full_outsource_contract'}:
                    add(subject.get('project_id'),detail.get('contract_number'),label+'号')
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


def _project_profile(db,user,project_id):
    fields=access(db,user,'project.read',{'project_id':project_id}).fields
    profile=db.get(m.ProjectProfile,project_id)
    if not profile:return None
    return select_fields({'customer_id':profile.customer_id,
        'execution_mode':profile.execution_mode,
        'customer_due_date':profile.customer_due_date.isoformat() if profile.customer_due_date else None,
        'settlement_status':profile.settlement_status},fields | {'execution_mode','customer_due_date','settlement_status'})


def _records(db,user,project_id,allowed_tools):
    data={}
    for kind in ('quote_acceptance','internal_start','sales_contract','full_outsource_contract','project_plan','plan_change'):
        rows=_visible_subjects(db,user,[project_id],kind,allowed_tools)
        data[kind]=rows[:20]
    return data


def _latest_effective(records,kind,decision=None):
    for row in records.get(kind,[]):
        detail=row.get('detail') if isinstance(row.get('detail'),dict) else {}
        if row.get('status')=='EFFECTIVE' and (decision is None or detail.get('decision')==decision):
            return row
    return None


def _open_records(records,kind):
    return [row for row in records.get(kind,[]) if row.get('status') in {'DRAFT','SUBMITTED','RETURNED','REJECTED','APPLY_BLOCKED'}]


def _readiness(project,records,allowed_tools):
    blockers=[];warnings=[];hints=[]
    latest_accept=_latest_effective(records,'quote_acceptance','ACCEPT')
    latest_reject=_latest_effective(records,'quote_acceptance','REJECT')
    latest_start=_latest_effective(records,'internal_start','START')
    if latest_reject:blockers.append('当前可见范围存在有效拒单记录，不能据此准备正式开工。')
    if 'query_quote_acceptance' not in allowed_tools and 'query_quote_acceptance_context' not in allowed_tools:
        warnings.append('未分配报价承接查询工具，不能核对承接依据。')
    elif not latest_accept:blockers.append('当前可见范围未见有效承接记录。')
    if latest_start:
        hints.append('当前可见范围已有有效正式开工通知，后续应核对计划审批和执行状态。')
    elif project.status=='DRAFT':
        hints.append('项目仍为未开工状态；若承接依据和客户开工条件均已人工确认，可准备正式开工申请。')
    elif project.status=='ACTIVE':
        warnings.append('项目已处于执行中，但当前可见范围未见有效正式开工通知，请核对历史开工依据。')
    elif project.status in {'CLOSED','TERMINATED'}:
        blockers.append('项目已关闭或终止，不应准备普通正式开工。')
    elif project.status=='PAUSED':
        blockers.append('项目已暂停，须先按暂停恢复流程处理。')
    if not records.get('sales_contract'):
        warnings.append('当前可见范围未见销售合同；合同晚到不必然阻塞开工，但需保留开工依据和后续补合同核对。')
    if records.get('project_plan') or records.get('plan_change'):
        hints.append('已存在计划记录；计划审批与正式开工仍须分别核对。')
    else:
        warnings.append('当前可见范围未见项目计划；正式开工后仍需按项目计划审批结果执行。')
    return {'known_blockers':blockers,'warnings':warnings,'hints':hints,
        'can_prepare_start_from_known_facts':bool(latest_accept) and not blockers and not latest_start and project.status=='DRAFT',
        'has_effective_acceptance':bool(latest_accept),
        'has_effective_rejection':bool(latest_reject),
        'has_effective_internal_start':bool(latest_start),
        'project_status':project.status}


def query(db,user,data:StartReadinessInput,allowed_tools:set[str]):
    project,alternatives,truncated=_resolve(db,user,data,allowed_tools)
    limitations=['只读取当前用户可见且具备正式开工读取权限的项目。',
                 '承接、合同和计划上下文仅在对应查询工具及业务权限可用时返回。',
                 '本工具只做正式开工条件核对，不创建开工通知、不下达任务、不执行采购/生产/装配。']
    if truncated:limitations.append('最多检查前500个可见项目，结果可能未覆盖全部可见范围。')
    if project:
        records=_records(db,user,project.id,allowed_tools)
        skipped=[]
        if 'query_quote_acceptance' not in allowed_tools and 'query_quote_acceptance_context' not in allowed_tools:skipped.append('承接依据')
        if 'query_sales_contract' not in allowed_tools:skipped.append('销售合同')
        if 'query_project_plan' not in allowed_tools and 'query_plan_change' not in allowed_tools:skipped.append('项目计划')
        if skipped:limitations.append('未分配对应查询工具，无法返回：'+'、'.join(skipped))
        return {'resolution':'RESOLVED','data':[{'project':_project_card(db,user,project,alternatives or ('项目定位',)),
            'profile':_project_profile(db,user,project.id),
            'quote_acceptance':records['quote_acceptance'],
            'latest_acceptance':_latest_effective(records,'quote_acceptance','ACCEPT'),
            'latest_rejection':_latest_effective(records,'quote_acceptance','REJECT'),
            'internal_starts':records['internal_start'],
            'latest_internal_start':_latest_effective(records,'internal_start','START'),
            'open_start_requests':_open_records(records,'internal_start'),
            'sales_contracts':records['sales_contract'],
            'full_outsource_contracts':records['full_outsource_contract'],
            'plans':records['project_plan']+records['plan_change'],
            'readiness':_readiness(project,records,allowed_tools)}],
            'source':'agent_db','as_of':now().isoformat(),'limitations':limitations}
    if alternatives is None:
        return {'resolution':'NOT_FOUND_OR_FORBIDDEN','data':[],'source':'agent_db','as_of':now().isoformat(),
                'limitations':limitations}
    if alternatives:
        return {'resolution':'MULTIPLE_CANDIDATES','data':alternatives,'source':'agent_db','as_of':now().isoformat(),
                'limitations':limitations+['线索命中多个候选项目，请使用项目 ID 或更完整编号后再查询。']}
    return {'resolution':'NOT_FOUND','data':[],'source':'agent_db','as_of':now().isoformat(),
            'limitations':limitations}
