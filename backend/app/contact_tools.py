"""Contact proposals are inert data. Only a human session can obtain a challenge."""
from uuid import uuid4
from pydantic import Field, ValidationError
from fastapi import APIRouter, Depends
from fastapi.encoders import jsonable_encoder
from . import contacts as c, models as m
from .schemas import StrictModel
from .errors import DomainError
from .db import get_db, now
from .security import current_user
from .bpm import content_hash
from .authorization import fingerprint

PROBLEM_NAMES={'CUSTOMER_CHANGE':'客户设变','DESIGN_ISSUE':'设计异常','ASSEMBLY_ISSUE':'组立异常','MACHINING_ISSUE':'加工异常',
    'PROCUREMENT_ISSUE':'采购异常','QUALITY_ISSUE':'质检异常','TRIAL_ISSUE':'试模异常','OUTSOURCE_DEFECT':'外协不良',
    'COST_REDUCTION':'降低成本','PROCESS_IMPROVEMENT':'制程改善','OTHER':'其他'}
CHANGE_NAMES={'CHANGE':'设变','EXCEPTION':'异常','IMPROVEMENT':'改善'}
URGENCY_NAMES={'NORMAL':'普通','URGENT':'紧急','CRITICAL':'重大紧急'}
AFFECTED_NAMES={'DRAWING':'图纸','MATERIAL':'物料','PURCHASE_ORDER':'采购单','WIP_TASK':'在制任务','SUPPLIER_TASK':'供应商任务',
    'PLAN_NODE':'计划节点','CONTRACT':'合同','FINANCE':'财务事项','LOGISTICS':'物流','OTHER':'其他'}
ACTION_NAMES={'CONTINUE':'继续执行','PAUSE':'暂停','CANCEL':'取消','REWORK':'返工','REISSUE':'重新下达'}
SOURCE_NAMES={'AGENT':'Agent本地','ERP':'ERP原生','MANUAL':'人工核对'}


class ContextInput(StrictModel):
    case_id:str=Field(min_length=1,max_length=36)
    task_id:str|None=Field(default=None,max_length=36)


from .files import AttachInput

SPECS={
    'create':(c.CreateInput,'create','发起工程联络单'),
    'note':(c.NoteInput,'record','补充联络记录'),
    'task':(c.TaskInput,'coordinate','交给责任部门'),
    'assign':(c.AssignInput,'assign','分派联络处理人'),
    'respond':(c.ResponseInput,'respond','提交联络反馈'),
    'attach':(AttachInput,'attach','关联联络单附件'),
}


from . import contact_lifecycle as lifecycle
SPECS.update(lifecycle.SPECS)
TASK_ACTIONS={'assign','respond','review','cancel_task'}


def schema(action):
    result=SPECS[action][0].model_json_schema()
    result['properties'].pop('request_key')
    result['required'].remove('request_key')
    if action!='create':
        result['properties']['case_id']={'type':'string','minLength':1,'maxLength':36}
        result['required'].append('case_id')
    if action in TASK_ACTIONS:
        result['properties']['task_id']={'type':'string','minLength':1,'maxLength':36}
        result['required'].append('task_id')
    return result


def parse(action,body):
    try:return SPECS[action][0].model_validate(body)
    except (ValidationError,KeyError):raise DomainError('INVALID_TOOL_INPUT','联络操作参数不完整或不符合要求')


def preview(db,user,action,cid,tid,data):
    """Validate without writing; display the exact scope and recipients to the human."""
    if action=='create':
        case=m.ContactCase(project_id=data.project_id,category=data.category)
        c.require(db,user,'create',case);c.require(db,user,'read',case)
        project=db.get(m.Project,data.project_id)
        if not project:raise DomainError('NOT_FOUND','项目不存在',404)
        return {'操作':SPECS[action][2],'项目':project.code,'客户':data.customer_name,'客户引用':data.customer_ref,
                '模具号':data.mold_number,'产品或料品':data.product_ref,'申请日期':data.application_date.isoformat(),
                '问题来源':PROBLEM_NAMES[data.problem_source],'当前环节':data.current_stage,
                '变更类别':CHANGE_NAMES[data.change_type],'紧急程度':URGENCY_NAMES[data.urgency],
                '标题':data.title,'内容':data.description,
                '方式':'补录线下过程' if data.mode=='HISTORY' else '继续线上办理',
                '责任域':c.CATEGORY_NAMES.get(data.category,data.category or '未指定')}
    case=c.load(db,user,cid,True)
    c.require(db,user,SPECS[action][1],case)
    if case.revision!=data.revision:raise DomainError('VERSION_CONFLICT','联络单已更新，请重新准备并核对',409)
    lifecycle.ensure_open(case)
    result={'操作':SPECS[action][2],'联络单':case.title,'资料版本':case.revision}
    if action in lifecycle.SPECS:
        result.update(lifecycle.preview(db,user,case,tid,action,data))
        return result
    if action=='note':
        if data.occurred_at>now():raise DomainError('INVALID_TIME','发生时间不能晚于当前时间')
        if data.source=='OFFLINE' and not data.participants.strip():raise DomainError('PARTICIPANTS_REQUIRED','线下记录需说明实际参与人')
        if case.mode=='HISTORY' and data.source!='OFFLINE':raise DomainError('HISTORY_SOURCE','历史补录应明确线下来源')
        result.update({'记录来源':'线下补录' if data.source=='OFFLINE' else '本人记录','实际发生时间':data.occurred_at.isoformat(),
                       '参与人员':data.participants,'内容':data.content})
    elif action=='attach':
        from .files import attach_preview
        result.update(attach_preview(db,user,case,data))
    elif action=='task':
        if case.created_by!=user.id:raise DomainError('FORBIDDEN','由发起人组织协作事项',403)
        if case.mode!='ONLINE':raise DomainError('HISTORY_NO_DISPATCH','历史补录不能派发线上任务',409)
        group=c.department(db,data.department_id)
        result.update({'责任部门':group.name,'部门版本':group.version,'事项':data.title,'影响对象类型':AFFECTED_NAMES[data.affected_type],
                       '影响对象引用':data.affected_ref,'影响说明':data.impact_description,'计划动作':ACTION_NAMES[data.planned_action],
                       '预计交期影响天数':data.delivery_impact_days,
                       '预计金额':(str(data.estimated_amount)+' '+data.currency) if data.estimated_amount is not None else '不涉及',
                       '事实来源':SOURCE_NAMES[data.source_system],'来源引用':data.source_ref or 'Agent本地记录',
                       '核对时点':data.source_as_of.isoformat() if data.source_as_of else '本次确认'})
    else:
        task=db.get(m.ContactTask,tid)
        if not task or task.case_id!=case.id:raise DomainError('NOT_FOUND','协作事项不存在',404)
        group=c.department(db,task.department_id)
        result.update({'事项':task.title,'责任部门':group.name,'部门版本':group.version})
        if action=='assign':
            c.task_and_assigner(db,user,case,tid)
            if task.status not in {'UNASSIGNED','ASSIGNED'}:raise DomainError('TASK_FINISHED','已有反馈不能改派',409)
            person=db.get(m.User,data.assignee_id)
            if not c.assignee_eligible(db,person,group,case):raise DomainError('ASSIGNEE_UNAVAILABLE','处理人缺少有效成员身份或办理权限',403)
            result.update({'处理人':person.display_name,'分派说明':data.reason})
        else:
            if task.assignee_id!=user.id or not c.assignee_eligible(db,user,group,case):raise DomainError('FORBIDDEN','只能由当前有权处理人提交反馈',403)
            if task.status!='ASSIGNED':raise DomainError('TASK_FINISHED','该事项已有反馈，不能覆盖',409)
            if data.actual_completed_at>now():raise DomainError('INVALID_TIME','实际完成时间不能晚于当前时间')
            result.update({'处理人':user.display_name,'反馈':data.content,'实际完成时间':data.actual_completed_at.isoformat(),
                           '实际工时':str(data.actual_hours),'实际金额':(str(data.actual_amount)+' '+data.currency) if data.actual_amount is not None else '不涉及',
                           '执行依据':data.execution_evidence,'事实来源':SOURCE_NAMES[data.source_system],'来源引用':data.source_ref or 'Agent本地记录',
                           '核对时点':data.source_as_of.isoformat() if data.source_as_of else '本次确认'})
    result['说明']='协作记录和反馈不等于正式审批，也不代表事项已验收或关闭。'
    return result


def execute_tool(db,user,key,arguments):
    if key=='query_contact_context':
        try:data=ContextInput.model_validate(arguments)
        except ValidationError:raise DomainError('INVALID_TOOL_INPUT','请提供有效联络单标识')
        case=c.load(db,user,data.case_id)
        details=c.serialize(db,case,True,user)
        if details['can_coordinate']:details['departments']=c.departments(case.id,user,db)
        if data.task_id:details['candidates']=c.candidates(case.id,data.task_id,user,db)
        return jsonable_encoder({'data':[details],'source':'agent_db','as_of':now(),
            'limitations':['只有当前有权查询的资料；讨论、反馈和历史补录不是正式审批']})
    action=key.removeprefix('prepare_contact_')
    allowed=set(schema(action)['properties'])
    if set(arguments)-allowed:raise DomainError('INVALID_TOOL_INPUT','操作包含未登记参数')
    args=dict(arguments);cid=args.pop('case_id','');tid=args.pop('task_id','')
    if action!='create':
        try:ContextInput.model_validate({'case_id':cid,'task_id':tid or None})
        except ValidationError:raise DomainError('INVALID_TOOL_INPUT','请先查询有效联络单和事项标识')
    if action in TASK_ACTIONS and not tid:raise DomainError('INVALID_TOOL_INPUT','缺少协作事项标识')
    # Never accept a model-generated idempotency token or identity.
    if 'request_key' in args:raise DomainError('INVALID_TOOL_INPUT','操作标识由系统生成')
    data=parse(action,{**args,'request_key':str(uuid4())})
    display=preview(db,user,action,cid,tid,data)
    proposal={'kind':'contact','action':action,'case_id':cid,'task_id':tid,'input':data.model_dump(mode='json'),'display':display}
    return {'data':[],'source':'agent_proposal','as_of':now().isoformat(),'proposal':proposal,
        'limitations':['仅准备操作建议，尚未创建联络单、分派或保存反馈；必须由本人核对卡片并确认']}


def source(db,user,step_id):
    from .tool_gateway import available_tools
    step=db.get(m.Step,step_id);run=db.get(m.Run,step.run_id) if step else None
    if not run or run.user_id!=user.id:raise DomainError('NOT_FOUND','操作建议不存在或无权访问',404)
    if run.status not in {'RUNNING','SUCCEEDED'}:raise DomainError('PROPOSAL_STOPPED','任务已停止，请重新准备操作',409)
    if run.security_version!=user.security_version or run.checkpoint.get('authorization_hash')!=fingerprint(db,user):
        raise DomainError('AUTHORIZATION_CHANGED','授权已变化，请重新准备操作',403)
    proposal=step.result.get('proposal')
    if step.tool not in available_tools(db,user) or not step.tool.startswith('prepare_contact_') or not proposal:
        raise DomainError('TOOL_FORBIDDEN','操作能力不可用',403)
    return proposal


def validate_intent(db,user,payload):
    proposal=source(db,user,payload['step_id'])
    if content_hash(proposal)!=payload['proposal_hash']:raise DomainError('CONFIRMATION_INVALID','操作建议内容已变化',409)
    data=parse(proposal['action'],proposal['input'])
    display=preview(db,user,proposal['action'],proposal['case_id'],proposal['task_id'],data)
    if content_hash(display)!=content_hash(proposal['display']):raise DomainError('VERSION_CONFLICT','操作对象或责任人员资料已变化，请重新准备',409)
    return proposal,data


def confirm(db,user,payload):
    proposal,data=validate_intent(db,user,payload)
    action=proposal['action'];cid=proposal['case_id'];tid=proposal['task_id']
    if action in lifecycle.SPECS:result=lifecycle.execute(db,user,cid,tid,action,data)
    elif action=='create':result=c.create(data,user,db)
    elif action=='note':result=c.add_note(cid,data,user,db)
    elif action=='task':result=c.add_task(cid,data,user,db)
    elif action=='assign':result=c.assign(cid,tid,data,user,db)
    elif action=='attach':
        from .files import attach
        result=attach(db,user,cid,data)
    else:result=c.respond(cid,tid,data,user,db)
    return {'case_id':result['id'],'revision':result['revision'],'action':action,'status':'CONFIRMED'}


router=APIRouter()


@router.get('/api/contact-proposals/{step_id}')
def proposal_status(step_id:str,user=Depends(current_user),db=Depends(get_db)):
    from sqlalchemy import select
    source(db,user,step_id)
    intent=db.scalar(select(m.HumanIntent).where(m.HumanIntent.user_id==user.id,
        m.HumanIntent.action=='contact.execute',m.HumanIntent.resource_id==step_id,
        m.HumanIntent.receipt['status'].as_string()=='CONFIRMED').order_by(m.HumanIntent.created_at.desc()))
    return {'receipt':intent.receipt if intent else None}


@router.post('/api/contact-proposals/{step_id}/intent')
def intent(step_id:str,user=Depends(current_user),db=Depends(get_db)):
    from .business import create_intent
    proposal=source(db,user,step_id)
    payload={'step_id':step_id,'proposal_hash':content_hash(proposal)}
    result=create_intent(db,user,'contact.execute',step_id,payload)
    result['display']=proposal['display']
    db.commit();return result
