from collections import defaultdict
from datetime import date
from decimal import Decimal
from typing import Literal
from pydantic import Field, ValidationError, model_validator
from fastapi import APIRouter, Depends
from sqlalchemy import select, and_
from . import models as m, domains, domain_schemas as s, workflow_selection
from .authorization import access, predicate, require, select_fields, fingerprint
from .bpm import content_hash
from .db import get_db, now
from .errors import DomainError
from .security import current_user
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


class QuoteDecisionProposalInput(StrictModel):
    project_id: str = Field(min_length=1, max_length=36,
        description='query_quote_acceptance_context 返回的真实 project_id。')
    project_version: int = Field(ge=1,
        description='query_quote_acceptance_context 返回的项目 row_version。')
    decision: Literal['ACCEPT','REJECT']
    execution_mode: Literal['INTERNAL','FULL_OUTSOURCE'] | None = Field(default=None,
        description='承接时必填；拒单时保持 null。')
    effective_date: date
    evidence: str = Field(min_length=1, max_length=4000,
        description='承接或拒单依据，例如客户确认、评估结论、邮件/合同线索或人工说明。')
    amount: Decimal | None = Field(default=None, gt=0, max_digits=18, decimal_places=2,
        description='可选：承接/报价金额，未知时保持 null。')
    currency: str | None = Field(default=None, pattern=r'^[A-Z]{3}$',
        description='金额币种；amount 有值时必填。')
    workflow_definition_id: str = Field(min_length=1, max_length=36,
        description='query_quote_acceptance_context 返回或管理员配置的报价承接审批流程 ID。')

    @model_validator(mode='after')
    def decision_requirements(self):
        if self.decision=='ACCEPT' and not self.execution_mode:
            raise ValueError('承接时必须确认最终加工方式')
        if self.decision=='REJECT' and self.execution_mode:
            raise ValueError('拒单不填写最终加工方式')
        if bool(self.amount)!=bool(self.currency):
            raise ValueError('金额与币种须同时填写')
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


def workflow_options(db,user,project):
    scope={'project_id':project.id}
    require(db,user,'quote_acceptance.read',scope)
    require(db,user,'quote_acceptance.submit',scope)
    rows=db.scalars(select(m.WorkflowDefinition).where(m.WorkflowDefinition.status=='PUBLISHED').order_by(
        m.WorkflowDefinition.process_key,m.WorkflowDefinition.version.desc()))
    result=[]
    for row in rows:
        if not workflow_selection.matches(row.config,{'business_type':'quote_acceptance','categories':set(),'design_type':None}):continue
        if row.config.get('material_contract') is not None:continue
        result.append(workflow_selection.metadata(row,db))
    return result


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
        workflows=[]
        if 'prepare_quote_acceptance_decision' in allowed_tools:
            try:workflows=workflow_options(db,user,project)
            except DomainError as error:limitations.append('当前人员缺少报价承接提交权限，未返回可选审批流程：'+error.message)
        return {'resolution':'RESOLVED','data':[{'project':_project_card(db,user,project,matched_by),
            'quote_acceptance':quotes,'latest_acceptance':latest_accept,'latest_rejection':latest_reject,
            'open_quote_decisions':open_drafts,'internal_starts':starts,'sales_contracts':contracts,
            'derived_status':{
                'has_effective_acceptance':bool(latest_accept),
                'has_effective_rejection':bool(latest_reject),
                'has_formal_start':any(row.get('status')=='EFFECTIVE' for row in starts),
                'has_sales_contract':bool(contracts)},
            'workflow_options':workflows}],
            'source':'agent_db','as_of':now().isoformat(),'limitations':limitations}
    if alternatives is None:
        return {'resolution':'NOT_FOUND_OR_FORBIDDEN','data':[],'source':'agent_db','as_of':now().isoformat(),
                'limitations':limitations}
    if alternatives:
        return {'resolution':'MULTIPLE_CANDIDATES','data':alternatives,'source':'agent_db','as_of':now().isoformat(),
                'limitations':limitations+['线索命中多个候选项目，请使用项目 ID 或更完整编号后再查询。']}
    return {'resolution':'NOT_FOUND','data':[],'source':'agent_db','as_of':now().isoformat(),
            'limitations':limitations}


def quote_decision_schema():
    return QuoteDecisionProposalInput.model_json_schema()


def parse_quote_decision(arguments):
    try:return QuoteDecisionProposalInput.model_validate(arguments or {})
    except ValidationError as error:raise DomainError('INVALID_TOOL_INPUT','报价承接/拒单参数不完整或不符合要求：'+error.errors()[0]['msg']) from None


def _existing_decisions(db,project_id):
    return list(db.scalars(select(m.BusinessSubject).where(m.BusinessSubject.project_id==project_id,
        m.BusinessSubject.kind=='quote_acceptance').order_by(m.BusinessSubject.created_at.desc(),m.BusinessSubject.id).limit(50)))


def preview_quote_decision(db,user,data:QuoteDecisionProposalInput):
    project=db.get(m.Project,data.project_id)
    if not project:raise DomainError('NOT_FOUND','项目不存在',404)
    scope={'project_id':project.id}
    require(db,user,'project.read',scope)
    require(db,user,'quote_acceptance.read',scope)
    require(db,user,'quote_acceptance.create',scope)
    require(db,user,'quote_acceptance.submit',scope)
    if project.row_version!=data.project_version:
        raise DomainError('VERSION_CONFLICT','项目状态已变化，请重新查询后准备',409)
    if project.status!='DRAFT':
        raise DomainError('QUOTE_STATE','只有未开工项目可准备承接或拒单决定',409)
    existing=_existing_decisions(db,project.id)
    effective=[row for row in existing if row.status=='EFFECTIVE']
    pending=[row for row in existing if row.status in {'DRAFT','SUBMITTED','RETURNED','APPLY_BLOCKED'}]
    if effective:
        raise DomainError('QUOTE_DECISION_EXISTS','当前项目已有有效承接或拒单决定，请勿重复准备',409)
    if pending:
        raise DomainError('QUOTE_DECISION_PENDING','当前项目已有待处理承接或拒单申请，请先处理原申请',409)
    detail=s.DecisionInput(decision=data.decision,execution_mode=data.execution_mode,
        effective_date=data.effective_date,evidence=data.evidence,amount=data.amount,currency=data.currency)
    options=workflow_options(db,user,project)
    selected=next((item for item in options if item['id']==data.workflow_definition_id),None)
    if not selected:raise DomainError('WORKFLOW_MISMATCH','审批模板不可用，请重新查询流程选项',409)
    display={'操作':'确认承接' if data.decision=='ACCEPT' else '确认拒单',
        '项目':project.code+' · '+project.name,
        '项目版本':project.row_version,
        '业务决定':'承接' if data.decision=='ACCEPT' else '拒单',
        '最终加工方式':data.execution_mode or '不适用',
        '生效日期':data.effective_date.isoformat(),
        '依据':data.evidence,
        '金额':(str(data.amount)+' '+data.currency if data.amount else '未登记'),
        '审批流程':selected['name']+' · 第'+str(selected['version'])+'版',
        '说明':'本人确认后仅创建报价承接/拒单材料并提交 Agent BPM；审批生效前不会正式承接、拒单、开工或修改合同。'}
    return detail,display


def execute_quote_tool(db,user,key,arguments,run=None):
    if key!='prepare_quote_acceptance_decision':raise DomainError('TOOL_UNKNOWN','工具未实现',403)
    data=parse_quote_decision(arguments)
    _,display=preview_quote_decision(db,user,data)
    from .confirmation_policy import proposal_confirmation_policy
    proposal={'kind':'quote_acceptance','action':'quote_decision','requires_approval':True,
        'input':data.model_dump(mode='json'),'display':display,
        'confirmation_policy':proposal_confirmation_policy(run,requires_approval=True)}
    return {'data':[],'source':'agent_proposal','as_of':now().isoformat(),'proposal':proposal,
        'limitations':['仅准备报价承接/拒单建议；本人确认后才创建业务材料并提交审批，审批完成前不改变项目或合同。']}


def source(db,user,step_id):
    from .tool_gateway import available_tools
    step=db.get(m.Step,step_id);run=db.get(m.Run,step.run_id) if step else None
    if not run or run.user_id!=user.id:raise DomainError('NOT_FOUND','操作建议不存在或无权访问',404)
    if run.status not in {'RUNNING','SUCCEEDED'}:raise DomainError('PROPOSAL_STOPPED','任务已停止，请重新准备操作',409)
    if run.security_version!=user.security_version or run.checkpoint.get('authorization_hash')!=fingerprint(db,user):
        raise DomainError('AUTHORIZATION_CHANGED','授权已变化，请重新准备操作',403)
    proposal=step.result.get('proposal')
    if step.tool not in available_tools(db,user) or step.tool!='prepare_quote_acceptance_decision' or not proposal:
        raise DomainError('TOOL_FORBIDDEN','操作能力不可用',403)
    return proposal


def validate_intent(db,user,payload):
    proposal=source(db,user,payload['step_id'])
    if content_hash(proposal)!=payload['proposal_hash']:raise DomainError('CONFIRMATION_INVALID','操作建议内容已变化',409)
    data=parse_quote_decision(proposal['input'])
    _,display=preview_quote_decision(db,user,data)
    if content_hash(display)!=content_hash(proposal['display']):
        raise DomainError('VERSION_CONFLICT','项目或流程资料已变化，请重新准备',409)
    return proposal,data


def confirm(db,user,payload):
    from .confirmation_policy import agent_permission_mode_from_proposal
    proposal,data=validate_intent(db,user,payload)
    detail,_=preview_quote_decision(db,user,data)
    subject=domains.create(db,user,s.SubjectInput(kind='quote_acceptance',project_id=data.project_id,
        remark=data.evidence,detail=detail.model_dump(mode='json')))
    from .business import submit_subject
    submitted=submit_subject(db,user,subject.id,subject.revision,data.workflow_definition_id,
        agent_permission_mode=agent_permission_mode_from_proposal(proposal))
    return {'project_id':data.project_id,'subject_id':subject.id,'instance_id':submitted['instance_id'],
        'action':'quote_decision','status':'SUBMITTED'}


router=APIRouter()


@router.get('/api/quote-acceptance-proposals/{step_id}')
def proposal_status(step_id:str,user=Depends(current_user),db=Depends(get_db)):
    source(db,user,step_id)
    intent=db.scalar(select(m.HumanIntent).where(m.HumanIntent.user_id==user.id,
        m.HumanIntent.action=='quote_acceptance.execute',m.HumanIntent.resource_id==step_id,
        m.HumanIntent.receipt['status'].as_string()=='SUBMITTED').order_by(m.HumanIntent.created_at.desc()))
    return {'receipt':intent.receipt if intent else None}


@router.post('/api/quote-acceptance-proposals/{step_id}/intent')
def intent(step_id:str,user=Depends(current_user),db=Depends(get_db)):
    from .business import create_intent
    proposal=source(db,user,step_id)
    payload={'step_id':step_id,'proposal_hash':content_hash(proposal)}
    result=create_intent(db,user,'quote_acceptance.execute',step_id,payload)
    result['display']=proposal['display']
    result['confirmation_policy']=proposal.get('confirmation_policy')
    db.commit();return result
