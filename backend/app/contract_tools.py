from collections import defaultdict
from decimal import Decimal, InvalidOperation
from pydantic import Field, model_validator
from sqlalchemy import select, and_
from . import models as m
from .authorization import access, predicate, select_fields
from .db import now
from .errors import DomainError
from .schemas import StrictModel


class ContractContextInput(StrictModel):
    project_id: str | None = Field(default=None, min_length=1, max_length=36)
    identifier: str | None = Field(default=None, min_length=1, max_length=200,
        description='项目编号/名称、合同号、合同业务单号、模具号或其他可见业务线索。')

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
              predicate(db,user,'project.dossier.read',{'project_id':m.Project.id}))
    rows=list(db.scalars(select(m.Project).where(gate).order_by(m.Project.code).limit(501)))
    return rows[:500],len(rows)>500


def _contract_subjects(db,user,project_ids,kind,allowed_tools):
    if 'query_'+kind not in allowed_tools:return []
    from .domains import data as subject_data
    result=[]
    for subject in db.scalars(select(m.BusinessSubject).where(m.BusinessSubject.project_id.in_(project_ids),
        m.BusinessSubject.kind==kind).order_by(m.BusinessSubject.created_at.desc(),m.BusinessSubject.id).limit(501)):
        try:result.append(subject_data(db,user,subject))
        except DomainError:continue
    return result[:500]


def _resolve(db,user,data:ContractContextInput,allowed_tools:set[str]):
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
        for kind,label in (('sales_contract','销售合同'),('full_outsource_contract','整套委外合同')):
            for subject in _contract_subjects(db,user,list(by_id),kind,allowed_tools):
                detail=subject.get('detail') if isinstance(subject.get('detail'),dict) else {}
                add(subject.get('project_id'),subject.get('id'),label+'ID')
                add(subject.get('project_id'),subject.get('number'),label+'业务单号')
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


def _records(db,user,project_id,allowed_tools):
    result={};truncated={}
    for kind in ('sales_contract','full_outsource_contract'):
        if 'query_'+kind not in allowed_tools:
            result[kind]=[];truncated[kind]=False;continue
        rows=list(db.scalars(select(m.BusinessSubject).where(m.BusinessSubject.project_id==project_id,
            m.BusinessSubject.kind==kind).order_by(m.BusinessSubject.created_at.desc(),m.BusinessSubject.id).limit(21)))
        visible=[]
        from .domains import data as subject_data
        for subject in rows[:20]:
            try:visible.append(subject_data(db,user,subject))
            except DomainError:continue
        result[kind]=visible;truncated[kind]=len(rows)>20
    return result,truncated


def _decimal(value):
    if value is None:return None
    try:return Decimal(str(value))
    except (InvalidOperation,ValueError):return None


def _totals(records):
    amount=defaultdict(Decimal);stages=defaultdict(Decimal);counts=defaultdict(int)
    for record in records:
        detail=record.get('detail') if isinstance(record.get('detail'),dict) else {}
        currency=detail.get('currency')
        value=_decimal(detail.get('amount'))
        if currency and value is not None:
            amount[currency]+=value;counts[currency]+=1
        for stage in detail.get('stages') or []:
            stage_amount=_decimal(stage.get('amount'))
            stage_currency=stage.get('currency') or currency
            if stage_currency and stage_amount is not None:stages[stage_currency]+=stage_amount
    return [{'currency':currency,'contract_amount':str(amount[currency]),'stage_amount':str(stages[currency]),
             'contract_count':counts[currency]} for currency in sorted(set(amount)|set(stages))]


def _replacement_map(records):
    by_id={record.get('id'):record for record in records}
    replaced_by=defaultdict(list)
    for record in records:
        detail=record.get('detail') if isinstance(record.get('detail'),dict) else {}
        if detail.get('replaces_id'):replaced_by[detail['replaces_id']].append(record)
    links=[]
    for record in records:
        detail=record.get('detail') if isinstance(record.get('detail'),dict) else {}
        if detail.get('replaces_id') or record.get('id') in replaced_by:
            links.append({'contract_id':record.get('id'),'contract_number':detail.get('contract_number'),
                'replaces_id':detail.get('replaces_id'),
                'replaces_number':(by_id.get(detail.get('replaces_id')) or {}).get('detail',{}).get('contract_number')
                    if isinstance((by_id.get(detail.get('replaces_id')) or {}).get('detail'),dict) else None,
                'replaced_by':[{'contract_id':row.get('id'),'contract_number':(row.get('detail') or {}).get('contract_number')}
                    for row in replaced_by.get(record.get('id'),[])]})
    return links


def _late_expected(records):
    today=now().date().isoformat()
    result=[]
    for record in records:
        detail=record.get('detail') if isinstance(record.get('detail'),dict) else {}
        expected=detail.get('expected_date')
        if expected and str(expected)<today and record.get('status') not in {'EFFECTIVE','CLOSED'}:
            result.append({'id':record.get('id'),'number':record.get('number'),
                'contract_number':detail.get('contract_number'),'status':record.get('status'),
                'expected_date':expected})
    return result


def query(db,user,data:ContractContextInput,allowed_tools:set[str]):
    project,alternatives,truncated=_resolve(db,user,data,allowed_tools)
    limitations=['只读取当前用户可见且具备项目业务档案读取权限的项目。',
                 '合同明细仅在对应销售合同或整套委外合同查询工具及业务权限同时可用时返回。',
                 '本工具只汇总合同上下文，不上传合同、不做 OCR、不确认收付款、不替代财务核对或合同审批。']
    if truncated:limitations.append('最多检查前500个可见项目，结果可能未覆盖全部可见范围。')
    if project:
        records,record_truncated=_records(db,user,project.id,allowed_tools)
        sales=records['sales_contract'];outsource=records['full_outsource_contract']
        all_records=sales+outsource
        if record_truncated['sales_contract']:limitations.append('销售合同最多返回最新20条。')
        if record_truncated['full_outsource_contract']:limitations.append('整套委外合同最多返回最新20条。')
        skipped=[]
        if 'query_sales_contract' not in allowed_tools:skipped.append('销售合同')
        if 'query_full_outsource_contract' not in allowed_tools:skipped.append('整套委外合同')
        if skipped:limitations.append('未分配对应合同查询工具，未返回：'+'、'.join(skipped))
        return {'resolution':'RESOLVED','data':[{'project':_project_card(db,user,project,alternatives or ('项目定位',)),
            'sales_contracts':sales,'full_outsource_contracts':outsource,
            'contract_totals':{'sales_contract':_totals(sales),'full_outsource_contract':_totals(outsource)},
            'replacement_links':_replacement_map(all_records),
            'late_expected_contracts':_late_expected(all_records),
            'derived_status':{
                'has_sales_contract':bool(sales),
                'has_full_outsource_contract':bool(outsource),
                'has_effective_sales_contract':any(row.get('status')=='EFFECTIVE' for row in sales),
                'has_effective_full_outsource_contract':any(row.get('status')=='EFFECTIVE' for row in outsource),
                'has_replacement_relation':bool(_replacement_map(all_records)),
                'has_late_expected_contract':bool(_late_expected(all_records))}}],
            'source':'agent_db','as_of':now().isoformat(),'limitations':limitations}
    if alternatives is None:
        return {'resolution':'NOT_FOUND_OR_FORBIDDEN','data':[],'source':'agent_db','as_of':now().isoformat(),
                'limitations':limitations}
    if alternatives:
        return {'resolution':'MULTIPLE_CANDIDATES','data':alternatives,'source':'agent_db','as_of':now().isoformat(),
                'limitations':limitations+['线索命中多个候选项目，请使用项目 ID 或更完整编号后再查询。']}
    return {'resolution':'NOT_FOUND','data':[],'source':'agent_db','as_of':now().isoformat(),
            'limitations':limitations}
