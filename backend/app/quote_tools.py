from collections import defaultdict
from pydantic import Field, model_validator
from sqlalchemy import select, and_
from . import models as m
from .authorization import access, predicate, select_fields
from .db import now
from .errors import DomainError
from .schemas import StrictModel


class QuoteContextInput(StrictModel):
    project_id: str | None = Field(default=None, min_length=1, max_length=36)
    identifier: str | None = Field(default=None, min_length=1, max_length=200,
        description='项目编号/名称、候选匹配线索、合同号、模具号等。')

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
              predicate(db,user,'quote_acceptance.read',{'project_id':m.Project.id}))
    rows=list(db.scalars(select(m.Project).where(gate).order_by(m.Project.code).limit(501)))
    return rows[:500],len(rows)>500


def _resolve(db,user,data:QuoteContextInput,allowed_tools:set[str]):
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


def _subjects(db,user,project_id,kind,allowed_tools):
    tool='query_'+kind
    if kind=='quote_acceptance':
        if tool not in allowed_tools and 'query_quote_acceptance_context' not in allowed_tools:return [],False
    elif tool not in allowed_tools:return [],False
    from .domains import data as subject_data
    rows=list(db.scalars(select(m.BusinessSubject).where(m.BusinessSubject.project_id==project_id,
        m.BusinessSubject.kind==kind).order_by(m.BusinessSubject.created_at.desc(),m.BusinessSubject.id).limit(21)))
    result=[]
    for subject in rows[:20]:
        try:result.append(subject_data(db,user,subject))
        except DomainError:continue
    return result,len(rows)>20


def query(db,user,data:QuoteContextInput,allowed_tools:set[str]):
    project,alternatives,truncated=_resolve(db,user,data,allowed_tools)
    limitations=['只读取当前用户可见且具备报价与承接读取权限的项目。',
                 '本工具只汇总报价/承接上下文，不创建报价、不承接、不拒单、不正式开工。',
                 '唯一项目只表示当前可见资料中的定位结果；正式承接、拒单或开工仍须业务单据与人工审批。']
    if truncated:limitations.append('最多检查前500个可见项目，结果可能未覆盖全部可见范围。')
    if project:
        matched_by=alternatives or ('项目定位',)
        quotes,quotes_truncated=_subjects(db,user,project.id,'quote_acceptance',allowed_tools)
        starts,starts_truncated=_subjects(db,user,project.id,'internal_start',allowed_tools)
        contracts,contracts_truncated=_subjects(db,user,project.id,'sales_contract',allowed_tools)
        latest_accept=next((row for row in quotes if row.get('status')=='EFFECTIVE' and row.get('detail',{}).get('decision')=='ACCEPT'),None)
        latest_reject=next((row for row in quotes if row.get('status')=='EFFECTIVE' and row.get('detail',{}).get('decision')=='REJECT'),None)
        open_drafts=[row for row in quotes if row.get('status') in {'DRAFT','SUBMITTED','RETURNED','REJECTED','APPLY_BLOCKED'}]
        if quotes_truncated:limitations.append('报价与承接决定最多返回最新20条。')
        if starts_truncated:limitations.append('正式开工通知最多返回最新20条。')
        if contracts_truncated:limitations.append('销售合同最多返回最新20条。')
        return {'resolution':'RESOLVED','data':[{'project':_project_card(db,user,project,matched_by),
            'quote_acceptance':quotes,'latest_acceptance':latest_accept,'latest_rejection':latest_reject,
            'open_quote_decisions':open_drafts,'internal_starts':starts,'sales_contracts':contracts,
            'derived_status':{
                'has_effective_acceptance':bool(latest_accept),
                'has_effective_rejection':bool(latest_reject),
                'has_formal_start':any(row.get('status')=='EFFECTIVE' for row in starts),
                'has_sales_contract':bool(contracts)}}],
            'source':'agent_db','as_of':now().isoformat(),'limitations':limitations}
    if alternatives is None:
        return {'resolution':'NOT_FOUND_OR_FORBIDDEN','data':[],'source':'agent_db','as_of':now().isoformat(),
                'limitations':limitations}
    if alternatives:
        return {'resolution':'MULTIPLE_CANDIDATES','data':alternatives,'source':'agent_db','as_of':now().isoformat(),
                'limitations':limitations+['线索命中多个候选项目，请使用项目 ID 或更完整编号后再查询。']}
    return {'resolution':'NOT_FOUND','data':[],'source':'agent_db','as_of':now().isoformat(),
            'limitations':limitations}
