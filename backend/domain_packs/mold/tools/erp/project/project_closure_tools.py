"""Conversation proposals for project termination, disposal and final closure."""
from datetime import date,datetime
from decimal import Decimal
from typing import Literal
from fastapi import APIRouter,Depends
from fastapi.encoders import jsonable_encoder
from pydantic import Field,ValidationError
from sqlalchemy import select
from domain_packs.mold import models as m,domain_schemas as s,domains,project_closure as closure,workflow_selection
from domain_packs.mold.authorization import require,fingerprint
from domain_packs.mold.ports.bpm import content_hash
from domain_packs.mold.ports.db import get_db,now
from domain_packs.mold.ports.errors import DomainError
from domain_packs.mold.ports.schemas import StrictModel
from domain_packs.mold.ports.security import current_user


class ClosureContextInput(StrictModel):
    project_id:str=Field(min_length=1,max_length=36)


class ChecklistProposalInput(ClosureContextInput):
    project_version:int=Field(ge=1)
    current_stage:str=Field(min_length=1,max_length=200)
    reason:str=Field(min_length=1,max_length=4000)


class TerminationProposalInput(ChecklistProposalInput):
    effective_date:date
    evidence:str=Field(min_length=1,max_length=4000)
    completed_work_summary:str=Field(min_length=1,max_length=10000)
    incurred_cost_summary:str=Field(min_length=1,max_length=10000)
    incurred_cost_amount:Decimal|None=Field(default=None,ge=0,max_digits=18,decimal_places=2)
    currency:str|None=Field(default=None,pattern=r'^[A-Z]{3}$')
    workflow_definition_id:str=Field(min_length=1,max_length=36)


class ClosureItemProposalInput(StrictModel):
    case_id:str=Field(min_length=1,max_length=36)
    case_version:int=Field(ge=1)
    item_key:str=Field(pattern=r'^[A-Z][A-Z0-9_]{1,79}$')
    status:Literal['DONE','NOT_APPLICABLE']
    result:str=Field(min_length=1,max_length=10000)
    evidence:str=Field(min_length=1,max_length=10000)
    source_system:Literal['AGENT','ERP','MANUAL']
    source_ref:str|None=Field(default=None,max_length=300)
    source_as_of:datetime|None=None


class FinalCloseProposalInput(ClosureContextInput):
    project_version:int=Field(ge=1)
    case_id:str=Field(min_length=1,max_length=36)
    case_version:int=Field(ge=1)
    effective_date:date
    reason:str=Field(min_length=1,max_length=4000)
    evidence:str=Field(min_length=1,max_length=4000)
    workflow_definition_id:str=Field(min_length=1,max_length=36)


SCHEMAS={'checklist':ChecklistProposalInput,'termination':TerminationProposalInput,
    'item':ClosureItemProposalInput,'normal_close':FinalCloseProposalInput,
    'settlement_close':FinalCloseProposalInput}

ACTION_BY_TOOL={'prepare_project_closure_checklist':'checklist','prepare_project_termination':'termination',
    'prepare_project_closure_item':'item','prepare_project_normal_close':'normal_close',
    'prepare_project_settlement_close':'settlement_close'}


def schema(action):return SCHEMAS[action].model_json_schema()


def parse(action,arguments):
    try:return SCHEMAS[action].model_validate(arguments)
    except (KeyError,ValidationError):raise DomainError('INVALID_TOOL_INPUT','项目终止或关闭参数不完整或不符合要求') from None


def workflow_options(db,user,project):
    scope={'project_id':project.id}
    try:require(db,user,'project_close.read',scope);require(db,user,'project_close.submit',scope)
    except DomainError:return []
    result=[]
    for row in db.scalars(select(m.WorkflowDefinition).where(m.WorkflowDefinition.status=='PUBLISHED').order_by(
        m.WorkflowDefinition.process_key,m.WorkflowDefinition.version.desc())):
        if not workflow_selection.matches(row.config,{'business_type':'project_close','categories':set(),'design_type':None}):continue
        if row.config.get('material_contract') is not None:continue
        result.append(workflow_selection.metadata(row,db))
    return result


def _project(db,user,project_id,create=False):
    project=db.get(m.Project,project_id)
    if not project:raise DomainError('NOT_FOUND','项目不存在',404)
    scope={'project_id':project.id};require(db,user,'project.read',scope);require(db,user,'project_close.read',scope)
    if create:require(db,user,'project_close.create',scope)
    return project


def _workflow(db,user,project,definition_id):
    selected=next((item for item in workflow_options(db,user,project) if item['id']==definition_id),None)
    if not selected:raise DomainError('WORKFLOW_MISMATCH','项目终止/关闭审批模板不可用，请重新查询',409)
    return selected


def _item_preview(db,user,data):
    case=db.get(m.ProjectClosureCase,data.case_id)
    if not case:raise DomainError('NOT_FOUND','结项清单不存在',404)
    require(db,user,'project_close.execute',{'project_id':case.project_id})
    if case.status!='OPEN' or case.version!=data.case_version:
        raise DomainError('VERSION_CONFLICT','结项清单已变化或结束，请重新查询',409)
    if closure._pending_subject(db,case.project_id):raise DomainError('CLOSE_REQUEST_EXISTS','关闭申请已提交，清单暂不可修改',409)
    item=db.scalar(select(m.ProjectClosureItem).where(m.ProjectClosureItem.case_id==case.id,
        m.ProjectClosureItem.item_key==data.item_key))
    if not item:raise DomainError('NOT_FOUND','结项事项不存在',404)
    if item.system_managed:raise DomainError('SYSTEM_CHECK','该事项由系统事实校验，不能人工覆盖',409)
    if data.status=='NOT_APPLICABLE' and not item.allow_not_applicable:
        raise DomainError('NOT_APPLICABLE_FORBIDDEN','该关闭条件不能标记为不适用',409)
    if data.source_system=='ERP' and (not data.source_ref or not data.source_as_of):
        raise DomainError('ERP_REFERENCE_REQUIRED','ERP 来源须填写原记录引用和核对时点',409)
    return case,item


def preview(db,user,action,data):
    if action=='item':
        case,item=_item_preview(db,user,data)
        return {'操作':'更新结项事项','清单类型':'终止结算' if case.mode=='TERMINATION' else '正常关闭',
            '事项':item.label,'原状态':item.status,'新状态':data.status,'处置或核对结果':data.result,
            '依据':data.evidence,'来源系统':data.source_system,'来源引用':data.source_ref or '人工上传/说明',
            '来源核对时点':data.source_as_of.isoformat() if data.source_as_of else '本次人工确认',
            '历史保护':'确认后生成新修订，原状态和原依据继续保留。'}
    project=_project(db,user,data.project_id,True)
    if project.row_version!=data.project_version:raise DomainError('VERSION_CONFLICT','项目状态已变化，请重新查询',409)
    if action=='checklist':
        if project.status!='ACTIVE':raise DomainError('CLOSE_STATE','只有执行中的项目可发起正常结项清单',409)
        if closure._open_case(db,project.id):raise DomainError('CLOSURE_CASE_EXISTS','项目已有未关闭结项清单',409)
        if closure._pending_subject(db,project.id):raise DomainError('CLOSE_REQUEST_EXISTS','项目已有待处理的终止或关闭申请',409)
        facts=closure.system_facts(db,project.id)
        return {'操作':'发起正常结项核对','项目':project.code+' · '+project.name,'项目版本':project.row_version,
            '当前环节':data.current_stage,'发起说明':data.reason,'系统已知事实':facts,
            '核对范围':'交付、验收、发票、回款、供应商结算、异常关闭及全过程归档。',
            '说明':'本人确认后只建立结项事项清单，不会关闭项目或修改 ERP。'}
    decision={'termination':'TERMINATE','normal_close':'NORMAL_CLOSE','settlement_close':'SETTLEMENT_CLOSE'}[action]
    detail=s.ProjectCloseInput(decision=decision,effective_date=data.effective_date,reason=data.reason,
        evidence=data.evidence,project_version=data.project_version,
        closure_case_id=getattr(data,'case_id',None),closure_case_version=getattr(data,'case_version',None),
        current_stage=getattr(data,'current_stage',None),completed_work_summary=getattr(data,'completed_work_summary',None),
        incurred_cost_summary=getattr(data,'incurred_cost_summary',None),
        incurred_cost_amount=getattr(data,'incurred_cost_amount',None),currency=getattr(data,'currency',None))
    closure.validate_close_detail(db,project,detail)
    selected=_workflow(db,user,project,data.workflow_definition_id)
    display={'操作':{'termination':'终止项目并转入终止结算','normal_close':'正常关闭项目','settlement_close':'终止结算关闭项目'}[action],
        '项目':project.code+' · '+project.name,'项目版本':project.row_version,
        '生效日期':data.effective_date.isoformat(),'原因':data.reason,'依据':data.evidence,
        '审批流程':selected['name']+' · 第'+str(selected['version'])+'版'}
    if action=='termination':
        display.update({'终止时当前环节':data.current_stage,'已完成工作':data.completed_work_summary,
            '已发生费用':data.incurred_cost_summary,
            '费用金额':(str(data.incurred_cost_amount)+' '+data.currency) if data.incurred_cost_amount is not None else '金额待财务在终止清单核对',
            '执行限制':'审批生效后停止 Agent 本地未完成计划任务，ERP 原任务不由本系统擅自改为完成；随后进入处置与终止结算清单。'})
    else:
        ctx=closure.context(db,user,project.id)['closure_case']
        display.update({'结项清单版本':data.case_version,'清单类型':'正常关闭' if action=='normal_close' else '终止结算',
            '已核对事项数':len(ctx['items']),'未完成事项':ctx['blockers']})
    display['说明']='本人确认后仅提交 Agent BPM；审批通过并成功应用业务命令后才改变项目状态。'
    return display


def execute_tool(db,user,key,arguments,run=None):
    if key=='query_project_closure_context':
        try:data=ClosureContextInput.model_validate(arguments)
        except ValidationError:raise DomainError('INVALID_TOOL_INPUT','请提供有效项目标识') from None
        project=_project(db,user,data.project_id)
        result=closure.context(db,user,project.id);result['workflow_options']=workflow_options(db,user,project)
        return jsonable_encoder({'data':[result],'source':'agent_db','as_of':now(),
            'limitations':['清单引用 ERP 原生记录及核对时点，不复制 ERP 财务或执行台账',
                '系统事实仅覆盖 Agent 本地对象；未联调的 ERP 事项必须人工关联原记录后才能完成清单',
                '终止、清单事项完成、BPM 批准和最终关闭是不同事实']})
    action=ACTION_BY_TOOL[key]
    data=parse(action,arguments);display=preview(db,user,action,data)
    requires_approval=action in {'termination','normal_close','settlement_close'}
    from domain_packs.mold.ports.confirmation_policy import proposal_confirmation_policy
    return {'data':[],'source':'agent_proposal','as_of':now().isoformat(),
        'proposal':{'kind':'project_closure','action':action,'requires_approval':requires_approval,
            'input':data.model_dump(mode='json'),'display':display,
            'confirmation_policy':proposal_confirmation_policy(run,requires_approval=requires_approval)},
        'limitations':['仅准备操作建议；必须由当前人员在会话中核对确认',
            '提交审批不等于项目状态已生效；清单更新不修改 ERP 原记录']}


def source(db,user,step_id):
    from domain_packs.mold.tool_gateway import available_tools
    step=db.get(m.Step,step_id);run=db.get(m.Run,step.run_id) if step else None
    if not run or run.user_id!=user.id:raise DomainError('NOT_FOUND','操作建议不存在或无权访问',404)
    if run.status not in {'RUNNING','RUNNING_SCOPED','SUCCEEDED'}:raise DomainError('PROPOSAL_STOPPED','任务已停止，请重新准备操作',409)
    if run.security_version!=user.security_version or run.checkpoint.get('authorization_hash')!=fingerprint(db,user):
        raise DomainError('AUTHORIZATION_CHANGED','授权已变化，请重新准备操作',403)
    proposal=step.result.get('proposal')
    allowed={'prepare_project_closure_checklist','prepare_project_termination','prepare_project_closure_item',
        'prepare_project_normal_close','prepare_project_settlement_close'}
    if step.tool not in allowed or step.tool not in available_tools(db,user) or not proposal or proposal.get('kind')!='project_closure':
        raise DomainError('TOOL_FORBIDDEN','操作能力不可用',403)
    return proposal


def validate_intent(db,user,payload):
    proposal=source(db,user,payload['step_id'])
    if content_hash(proposal)!=payload['proposal_hash']:raise DomainError('CONFIRMATION_INVALID','操作建议内容已变化',409)
    data=parse(proposal['action'],proposal['input']);display=preview(db,user,proposal['action'],data)
    if content_hash(display)!=content_hash(proposal['display']):
        raise DomainError('VERSION_CONFLICT','项目、清单、来源事实或流程资料已变化，请重新准备',409)
    return proposal,data


def confirm(db,user,payload):
    from domain_packs.mold.ports.confirmation_policy import agent_permission_mode_from_proposal
    proposal,data=validate_intent(db,user,payload);action=proposal['action']
    if action=='checklist':
        case=closure.open_normal_case(db,user,data.project_id,data.project_version,data.current_stage,data.reason)
        return {'project_id':data.project_id,'case_id':case.id,'action':action,'status':'RECORDED'}
    if action=='item':
        case,item=closure.update_item(db,user,data.case_id,data.case_version,data.item_key,data.status,
            data.result,data.evidence,data.source_system,data.source_ref,data.source_as_of)
        return {'project_id':case.project_id,'case_id':case.id,'item_key':item.item_key,
            'revision':item.revision,'action':action,'status':'RECORDED'}
    decision={'termination':'TERMINATE','normal_close':'NORMAL_CLOSE','settlement_close':'SETTLEMENT_CLOSE'}[action]
    detail={'decision':decision,'effective_date':data.effective_date,'reason':data.reason,'evidence':data.evidence,
        'project_version':data.project_version,'closure_case_id':getattr(data,'case_id',None),
        'closure_case_version':getattr(data,'case_version',None),'current_stage':getattr(data,'current_stage',None),
        'completed_work_summary':getattr(data,'completed_work_summary',None),
        'incurred_cost_summary':getattr(data,'incurred_cost_summary',None),
        'incurred_cost_amount':getattr(data,'incurred_cost_amount',None),'currency':getattr(data,'currency',None)}
    subject=domains.create(db,user,s.SubjectInput(kind='project_close',project_id=data.project_id,
        remark=data.reason,detail=detail))
    from domain_packs.mold.erp.core.business import submit_subject
    submitted=submit_subject(db,user,subject.id,subject.revision,data.workflow_definition_id,
        agent_permission_mode=agent_permission_mode_from_proposal(proposal))
    return {'project_id':data.project_id,'subject_id':subject.id,'instance_id':submitted['instance_id'],
        'action':action,'status':'SUBMITTED'}


router=APIRouter()


@router.get('/api/project-closure-proposals/{step_id}')
def proposal_status(step_id:str,user=Depends(current_user),db=Depends(get_db)):
    source(db,user,step_id)
    intent=db.scalar(select(m.HumanIntent).where(m.HumanIntent.user_id==user.id,
        m.HumanIntent.action=='project_closure.execute',m.HumanIntent.resource_id==step_id,
        m.HumanIntent.receipt.is_not(None)).order_by(m.HumanIntent.created_at.desc()))
    return {'receipt':intent.receipt if intent else None}


@router.post('/api/project-closure-proposals/{step_id}/intent')
def intent(step_id:str,user=Depends(current_user),db=Depends(get_db)):
    from domain_packs.mold.erp.core.business import create_intent
    proposal=source(db,user,step_id);payload={'step_id':step_id,'proposal_hash':content_hash(proposal)}
    result=create_intent(db,user,'project_closure.execute',step_id,payload);result['display']=proposal['display']
    result['confirmation_policy']=proposal.get('confirmation_policy')
    db.commit();return result
