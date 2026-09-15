"""Conversation-first project pause/resume proposals.

Tools only prepare a proposal. A browser session must confirm it before a
draft is created and submitted to the configured Agent BPM.
"""
from datetime import date,timedelta
from pydantic import Field,ValidationError
from fastapi import APIRouter,Depends
from fastapi.encoders import jsonable_encoder
from sqlalchemy import select
from . import models as m,domains,domain_schemas as s,workflow_selection
from .schemas import StrictModel
from .authorization import require,fingerprint
from .bpm import content_hash
from .db import get_db,now
from .errors import DomainError
from .security import current_user


class ProjectContextInput(StrictModel):
    project_id:str=Field(min_length=1,max_length=36)


class PauseProposalInput(ProjectContextInput):
    project_version:int=Field(ge=1)
    effective_date:date
    expected_resume_date:date|None=None
    reason:str=Field(min_length=1,max_length=4000)
    evidence:str=Field(min_length=1,max_length=4000)
    workflow_definition_id:str=Field(min_length=1,max_length=36)


class ResumeProposalInput(ProjectContextInput):
    project_version:int=Field(ge=1)
    pause_subject_id:str=Field(min_length=1,max_length=36)
    effective_date:date
    reason:str=Field(min_length=1,max_length=4000)
    evidence:str=Field(min_length=1,max_length=4000)
    workflow_definition_id:str=Field(min_length=1,max_length=36)


def workflow_options(db,user,project):
    scope={'project_id':project.id}
    require(db,user,'pause_resume.read',scope)
    require(db,user,'pause_resume.submit',scope)
    rows=db.scalars(select(m.WorkflowDefinition).where(m.WorkflowDefinition.status=='PUBLISHED').order_by(
        m.WorkflowDefinition.process_key,m.WorkflowDefinition.version.desc()))
    result=[]
    for row in rows:
        if not workflow_selection.matches(row.config,{'business_type':'pause_resume','categories':set(),'design_type':None}):continue
        if row.config.get('material_contract') is not None:continue
        result.append(workflow_selection.metadata(row,db))
    return result


def project_context(db,user,project_id):
    project=db.get(m.Project,project_id)
    if not project:raise DomainError('NOT_FOUND','项目不存在',404)
    require(db,user,'project.read',{'project_id':project.id})
    require(db,user,'pause_resume.read',{'project_id':project.id})
    profile=db.get(m.ProjectProfile,project.id)
    plan,tasks=domains.pause_snapshot(db,project)
    active_pause=db.scalar(select(m.PauseRecord).where(m.PauseRecord.project_id==project.id,
        m.PauseRecord.end_date.is_(None)))
    pause_detail=db.get(m.ProjectPauseDetail,active_pause.subject_id) if active_pause else None
    return {'id':project.id,'code':project.code,'name':project.name,'status':project.status,
        'row_version':project.row_version,'customer_due_date':profile.customer_due_date if profile else None,
        'active_plan':{'id':plan.id,'number':plan.number,'revision':plan.revision} if plan else None,
        'unfinished_tasks':tasks,'active_pause':({'pause_subject_id':active_pause.subject_id,
            'start_date':active_pause.start_date,'expected_resume_date':pause_detail.expected_resume_date if pause_detail else None,
            'reason':pause_detail.reason if pause_detail else None} if active_pause else None),
        'workflow_options':workflow_options(db,user,project)}


def schema(action):
    return (PauseProposalInput if action=='pause' else ResumeProposalInput).model_json_schema()


def parse(action,arguments):
    try:return (PauseProposalInput if action=='pause' else ResumeProposalInput).model_validate(arguments)
    except ValidationError:raise DomainError('INVALID_TOOL_INPUT','项目暂停或恢复参数不完整或不符合要求')


def preview(db,user,action,data):
    project=db.get(m.Project,data.project_id)
    if not project:raise DomainError('NOT_FOUND','项目不存在',404)
    scope={'project_id':project.id}
    require(db,user,'project.read',scope);require(db,user,'pause_resume.read',scope)
    require(db,user,'pause_resume.create',scope);require(db,user,'pause_resume.submit',scope)
    if project.row_version!=data.project_version:
        raise DomainError('VERSION_CONFLICT','项目状态已变化，请重新查询后准备',409)
    detail=s.PauseResumeInput(decision='PAUSE' if action=='pause' else 'RESUME',
        effective_date=data.effective_date,expected_resume_date=getattr(data,'expected_resume_date',None),
        reason=data.reason,evidence=data.evidence,
        source_pause_subject_id=getattr(data,'pause_subject_id',None))
    context=domains.validate_pause_detail(db,project,detail)
    options=workflow_options(db,user,project)
    selected=next((item for item in options if item['id']==data.workflow_definition_id),None)
    if not selected:raise DomainError('WORKFLOW_MISMATCH','审批模板不可用，请重新查询流程选项',409)
    tasks=context['task_snapshot']
    display={'操作':'项目整体暂停' if action=='pause' else '项目整体恢复',
        '项目':project.code+' · '+project.name,'项目版本':project.row_version,
        '生效日期':data.effective_date.isoformat(),'原因':data.reason,'依据':data.evidence,
        '客户承诺交期':context['customer_due_date'].isoformat() if context['customer_due_date'] else '未登记（恢复不会自动填写）',
        '审批流程':selected['name']+' · 第'+str(selected['version'])+'版'}
    if action=='pause':
        display['预计恢复日期']=data.expected_resume_date.isoformat() if data.expected_resume_date else '待确认'
        display['冻结的未完成任务']=[task['name']+'（'+task['planned_start']+' 至 '+task['planned_end']+'）' for task in tasks]
        display['执行限制']='暂停生效后阻止普通下单、报工、发料及计划执行；资料、沟通、工程联络和结算核对按权限保留。'
    else:
        days=(data.effective_date-db.scalar(select(m.PauseRecord.start_date).where(
            m.PauseRecord.subject_id==data.pause_subject_id,m.PauseRecord.end_date.is_(None)))).days
        display['实际暂停天数']=days
        display['拟顺延任务']=[task['name']+'：'+task['planned_start']+' → '+
            (date.fromisoformat(task['planned_start'])+timedelta(days=days)).isoformat()+'；'+task['planned_end']+' → '+
            (date.fromisoformat(task['planned_end'])+timedelta(days=days)).isoformat() for task in tasks]
        display['客户交期处理']='保持客户承诺交期不变；如需变更，必须走独立确认流程。'
    display['说明']='本人确认后仅提交 Agent BPM；审批完成前不会改变项目状态或计划日期。'
    return detail,display


def execute_tool(db,user,key,arguments):
    if key=='query_project_control_context':
        try:data=ProjectContextInput.model_validate(arguments)
        except ValidationError:raise DomainError('INVALID_TOOL_INPUT','请提供有效项目标识')
        return jsonable_encoder({'data':[project_context(db,user,data.project_id)],'source':'agent_db','as_of':now(),
            'limitations':['仅返回当前授权范围；ERP 项目执行事实尚未联调','暂停申请、审批通过、恢复和客户交期变更是不同事实']})
    action=key.removeprefix('prepare_project_')
    data=parse(action,arguments)
    _,display=preview(db,user,action,data)
    proposal={'kind':'project_control','action':action,'input':data.model_dump(mode='json'),'display':display}
    return {'data':[],'source':'agent_proposal','as_of':now().isoformat(),'proposal':proposal,
        'limitations':['仅准备操作建议；本人确认后才创建单据并提交审批，审批完成前不改变项目或计划']}


def source(db,user,step_id):
    from .tool_gateway import available_tools
    step=db.get(m.Step,step_id);run=db.get(m.Run,step.run_id) if step else None
    if not run or run.user_id!=user.id:raise DomainError('NOT_FOUND','操作建议不存在或无权访问',404)
    if run.status not in {'RUNNING','SUCCEEDED'}:raise DomainError('PROPOSAL_STOPPED','任务已停止，请重新准备操作',409)
    if run.security_version!=user.security_version or run.checkpoint.get('authorization_hash')!=fingerprint(db,user):
        raise DomainError('AUTHORIZATION_CHANGED','授权已变化，请重新准备操作',403)
    proposal=step.result.get('proposal')
    if step.tool not in available_tools(db,user) or not step.tool.startswith('prepare_project_') or not proposal:
        raise DomainError('TOOL_FORBIDDEN','操作能力不可用',403)
    return proposal


def validate_intent(db,user,payload):
    proposal=source(db,user,payload['step_id'])
    if content_hash(proposal)!=payload['proposal_hash']:raise DomainError('CONFIRMATION_INVALID','操作建议内容已变化',409)
    data=parse(proposal['action'],proposal['input'])
    _,display=preview(db,user,proposal['action'],data)
    if content_hash(display)!=content_hash(proposal['display']):
        raise DomainError('VERSION_CONFLICT','项目、计划或流程资料已变化，请重新准备',409)
    return proposal,data


def confirm(db,user,payload):
    proposal,data=validate_intent(db,user,payload)
    detail={'decision':'PAUSE' if proposal['action']=='pause' else 'RESUME','effective_date':data.effective_date,
        'expected_resume_date':getattr(data,'expected_resume_date',None),'reason':data.reason,'evidence':data.evidence,
        'source_pause_subject_id':getattr(data,'pause_subject_id',None)}
    subject=domains.create(db,user,s.SubjectInput(kind='pause_resume',project_id=data.project_id,
        remark=data.reason,detail=detail))
    from .business import submit_subject
    submitted=submit_subject(db,user,subject.id,subject.revision,data.workflow_definition_id)
    return {'project_id':data.project_id,'subject_id':subject.id,'instance_id':submitted['instance_id'],
        'action':proposal['action'],'status':'SUBMITTED'}


router=APIRouter()


@router.get('/api/project-control-proposals/{step_id}')
def proposal_status(step_id:str,user=Depends(current_user),db=Depends(get_db)):
    source(db,user,step_id)
    intent=db.scalar(select(m.HumanIntent).where(m.HumanIntent.user_id==user.id,
        m.HumanIntent.action=='project_control.execute',m.HumanIntent.resource_id==step_id,
        m.HumanIntent.receipt['status'].as_string()=='SUBMITTED').order_by(m.HumanIntent.created_at.desc()))
    return {'receipt':intent.receipt if intent else None}


@router.post('/api/project-control-proposals/{step_id}/intent')
def intent(step_id:str,user=Depends(current_user),db=Depends(get_db)):
    from .business import create_intent
    proposal=source(db,user,step_id)
    payload={'step_id':step_id,'proposal_hash':content_hash(proposal)}
    result=create_intent(db,user,'project_control.execute',step_id,payload)
    result['display']=proposal['display']
    db.commit();return result
