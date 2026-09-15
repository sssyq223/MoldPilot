"""Conversation-first project pause/resume proposals.

Tools only prepare a proposal. A browser session must confirm it before a
draft is created and submitted to the configured Agent BPM.
"""
from collections import defaultdict
from datetime import date,timedelta
from pydantic import Field,ValidationError,model_validator
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
    project_id:str|None=Field(default=None,min_length=1,max_length=36)
    identifier:str|None=Field(default=None,min_length=1,max_length=200,
        description='项目编号/名称、模具号、工程联络标题、客户引用或暂停恢复单号。')

    @model_validator(mode='after')
    def one_locator(self):
        if bool(self.project_id)==bool(self.identifier):
            raise ValueError('project_id 和 identifier 须且只能填写一项')
        if self.identifier:
            self.identifier=self.identifier.strip()
            if not self.identifier:raise ValueError('线索不能为空')
        return self


class PauseProposalInput(ProjectContextInput):
    project_version:int=Field(ge=1)
    effective_date:date
    expected_resume_date:date|None=None
    reason:str=Field(min_length=1,max_length=4000)
    evidence:str=Field(min_length=1,max_length=4000)
    workflow_definition_id:str=Field(min_length=1,max_length=36)

    @model_validator(mode='after')
    def requires_project_id(self):
        if not self.project_id or self.identifier:
            raise ValueError('准备暂停须使用查询返回的真实 project_id，不能只用自然语言线索')
        return self


class ResumeProposalInput(ProjectContextInput):
    project_version:int=Field(ge=1)
    pause_subject_id:str=Field(min_length=1,max_length=36)
    effective_date:date
    reason:str=Field(min_length=1,max_length=4000)
    evidence:str=Field(min_length=1,max_length=4000)
    workflow_definition_id:str=Field(min_length=1,max_length=36)

    @model_validator(mode='after')
    def requires_project_id(self):
        if not self.project_id or self.identifier:
            raise ValueError('准备恢复须使用查询返回的真实 project_id，不能只用自然语言线索')
        return self


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


def _strength(value,needle):
    if value is None:return 0
    value=str(value).casefold();needle=str(needle).casefold()
    return 100 if value==needle else 50 if needle in value else 0


def _project_card(db,user,project,matched_by=()):
    require(db,user,'project.read',{'project_id':project.id})
    return {'id':project.id,'code':project.code,'name':project.name,'status':project.status,
        'row_version':project.row_version,'matched_by':sorted(set(matched_by))}


def _visible_projects(db,user):
    from .authorization import predicate
    rows=list(db.scalars(select(m.Project).where(predicate(db,user,'project.read',
        {'project_id':m.Project.id})).order_by(m.Project.code).limit(501)))
    return rows[:500],len(rows)>500


def resolve_project(db,user,data:ProjectContextInput):
    visible,truncated=_visible_projects(db,user);by_id={project.id:project for project in visible}
    if data.project_id:
        project=by_id.get(data.project_id)
        return project,([] if project else None),truncated
    scores=defaultdict(int);reasons=defaultdict(list)
    def add(project_id,value,label):
        if project_id not in by_id:return
        score=_strength(value,data.identifier)
        if score:
            scores[project_id]=max(scores[project_id],score);reasons[project_id].append(label)
    for project in visible:
        add(project.id,project.id,'项目ID');add(project.id,project.code,'项目编号');add(project.id,project.name,'项目名称')
    if by_id:
        for link,mold in db.execute(select(m.ProjectMold,m.Mold).join(m.Mold,m.Mold.id==m.ProjectMold.mold_id)
                .where(m.ProjectMold.project_id.in_(list(by_id))).limit(501)):
            add(link.project_id,mold.internal_number,'模具号');add(link.project_id,mold.name,'模具名称')
        for case in db.scalars(select(m.ContactCase).where(m.ContactCase.project_id.in_(list(by_id))).limit(501)):
            add(case.project_id,case.title,'工程联络标题');add(case.project_id,case.customer_ref,'客户引用')
            add(case.project_id,case.mold_number,'联络模具号');add(case.project_id,case.product_ref,'产品/料品号')
        for subject in db.scalars(select(m.BusinessSubject).where(m.BusinessSubject.project_id.in_(list(by_id)),
                m.BusinessSubject.kind=='pause_resume').limit(501)):
            add(subject.project_id,subject.number,'暂停恢复单号');add(subject.project_id,subject.remark,'暂停恢复原因')
    if not scores:return None,[],truncated
    best=max(scores.values());ids=[project_id for project_id,score in scores.items() if score==best]
    if len(ids)!=1:return None,[_project_card(db,user,by_id[project_id],reasons[project_id]) for project_id in ids[:20]],truncated
    return by_id[ids[0]],reasons[ids[0]],truncated


def _pause_history(db,project_id):
    rows=[]
    for record in db.scalars(select(m.PauseRecord).where(m.PauseRecord.project_id==project_id)
            .order_by(m.PauseRecord.start_date.desc(),m.PauseRecord.id).limit(50)):
        pause_detail=db.get(m.ProjectPauseDetail,record.subject_id)
        resume_detail=db.get(m.ProjectPauseDetail,record.resume_subject_id) if record.resume_subject_id else None
        shifts=[{'task_id':shift.task_id,'previous_start':shift.previous_start,'previous_end':shift.previous_end,
                 'shifted_start':shift.shifted_start,'shifted_end':shift.shifted_end,
                 'shifted_days':shift.shifted_days,'task_status':shift.task_status}
                for shift in db.scalars(select(m.PauseTaskShift).where(m.PauseTaskShift.pause_id==record.id)
                    .order_by(m.PauseTaskShift.id).limit(100))]
        rows.append({'pause_subject_id':record.subject_id,'resume_subject_id':record.resume_subject_id,
            'start_date':record.start_date,'end_date':record.end_date,'expected_resume_date':pause_detail.expected_resume_date if pause_detail else None,
            'reason':pause_detail.reason if pause_detail else None,'pause_evidence_present':bool(pause_detail and pause_detail.evidence),
            'resume_reason':resume_detail.reason if resume_detail else None,'resume_evidence_present':bool(resume_detail and resume_detail.evidence),
            'shifted_days':record.shifted_days,'shift_applied':record.shift_applied,
            'customer_due_date_snapshot':record.customer_due_date_snapshot,'task_shift_count':len(shifts),'task_shifts':shifts})
    return rows


def _pending_pause_requests(db,project_id):
    result=[]
    for subject in db.scalars(select(m.BusinessSubject).where(m.BusinessSubject.project_id==project_id,
            m.BusinessSubject.kind=='pause_resume',m.BusinessSubject.status.in_(['DRAFT','SUBMITTED']))
            .order_by(m.BusinessSubject.created_at.desc(),m.BusinessSubject.id).limit(20)):
        detail=db.get(m.ProjectPauseDetail,subject.id)
        result.append({'id':subject.id,'number':subject.number,'status':subject.status,
            'decision':detail.decision if detail else None,'effective_date':detail.effective_date if detail else None,
            'expected_resume_date':detail.expected_resume_date if detail else None,
            'source_pause_subject_id':detail.source_pause_subject_id if detail else None,
            'reason':detail.reason if detail else None,'evidence_present':bool(detail and detail.evidence)})
    return result


def project_context(db,user,project):
    require(db,user,'project.read',{'project_id':project.id})
    require(db,user,'pause_resume.read',{'project_id':project.id})
    profile=db.get(m.ProjectProfile,project.id)
    plan,tasks=domains.pause_snapshot(db,project)
    active_pause=db.scalar(select(m.PauseRecord).where(m.PauseRecord.project_id==project.id,
        m.PauseRecord.end_date.is_(None)))
    pause_detail=db.get(m.ProjectPauseDetail,active_pause.subject_id) if active_pause else None
    pending=_pending_pause_requests(db,project.id)
    history=_pause_history(db,project.id)
    return {'id':project.id,'code':project.code,'name':project.name,'status':project.status,
        'row_version':project.row_version,'customer_due_date':profile.customer_due_date if profile else None,
        'active_plan':{'id':plan.id,'number':plan.number,'revision':plan.revision} if plan else None,
        'unfinished_tasks':tasks,'active_pause':({'pause_subject_id':active_pause.subject_id,
            'start_date':active_pause.start_date,'expected_resume_date':pause_detail.expected_resume_date if pause_detail else None,
            'reason':pause_detail.reason if pause_detail else None} if active_pause else None),
        'pending_pause_requests':pending,'pause_history':history,
        'allowed_during_pause':['资料补录','沟通记录','合同与结算核对','工程联络与恢复申请'],
        'blocked_during_pause':['普通下单','报工','发料','计划执行'],
        'derived_status':{'has_active_pause':bool(active_pause),'has_pending_pause_request':bool(pending),
            'has_resume_shift_evidence':any(row['shift_applied'] and row['task_shift_count'] for row in history),
            'customer_due_date_is_independent':True},
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
        project,alternatives,truncated=resolve_project(db,user,data)
        limitations=['仅返回当前授权范围；ERP 项目执行事实尚未联调',
            '暂停通知、暂停申请、审批通过、暂停生效、恢复、节点顺延和客户交期变更是不同事实，不能相互替代',
            '暂停期只限制普通下单、报工、发料和计划执行；资料补录、沟通记录、合同结算核对、工程联络和恢复申请仍按权限办理']
        if truncated:limitations.append('最多检查前500个可见项目，结果可能未覆盖全部可见范围。')
        if project:
            return jsonable_encoder({'resolution':'RESOLVED','data':[project_context(db,user,project)],'source':'agent_db','as_of':now(),
                'limitations':limitations})
        if alternatives is None:
            return jsonable_encoder({'resolution':'NOT_FOUND_OR_FORBIDDEN','data':[],'source':'agent_db','as_of':now(),'limitations':limitations})
        if alternatives:
            return jsonable_encoder({'resolution':'MULTIPLE_CANDIDATES','data':alternatives,'source':'agent_db','as_of':now(),
                'limitations':limitations+['线索命中多个候选项目，请使用项目 ID 或更完整编号后再查询。']})
        return jsonable_encoder({'resolution':'NOT_FOUND','data':[],'source':'agent_db','as_of':now(),'limitations':limitations})
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
