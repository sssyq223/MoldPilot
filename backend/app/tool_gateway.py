"""Code-registered tools; no runtime imports, shell, arbitrary URL or write SQL."""
from pathlib import Path
from sqlalchemy import select, or_
from .authorization import grants_for, predicate, require, select_fields
from .models import Project, PurchaseRequest, Capability
from .business import visible_requests, request_data
from .errors import DomainError
from .db import now
from .bpm import content_hash

TOOLS = {
    "query_projects": {"description": "查询当前用户有权查看的本项目项目，返回事实和来源。", "permission": "project.read"},
    "query_purchase_requests": {"description": "查询本项目采购申请及审批状态。不是正式订单发货记录，不能据此判断发货延期。", "permission": "purchase.read"},
}
from .domain_schemas import CATALOG
TOOLS.update({f'query_{key}': {'description':f'查询当前人员授权范围内的{name["name"]}及真实审批、生效状态。',
                              'permission':f'{key}.read','business_kind':key} for key,name in CATALOG.items()})
TOOLS['query_engineering_change']['description']='查询已有工程联络方案审批材料及生效状态；独立联络协作、责任部门和人员进度请使用工程联络协作查询工具。'
TOOLS.update({
    'query_contact_cases':{'description':'查询当前用户可见的工程联络单、责任部门、当前处理人和协作任务状态。已反馈不是正式批准，历史补录不代表事项已关闭。','permission':'contact.read'},
    'query_purchase_orders':{'description':'查询正式订单与草稿、发货数量和供应商异常，禁止把草稿视为已下单。','permission':'order.read'},
    'analyze_delivery_risk':{'description':'用户主动询问延期风险时，按当前责任域分析上报异常或临期未发货；结论不代表整套模具总体延期。','permission':'risk.read'},
    'query_project_control_context':{'description':'查询指定项目的执行状态、有效计划、未完成任务、当前暂停区间及可选审批流程；区分内部计划与客户承诺交期。','permission':'project.read'},
    'prepare_project_pause':{'description':'准备项目整体暂停并提交 Agent BPM 的操作建议；冻结未完成任务范围，审批完成前不改变状态。','permission':'pause_resume.create'},
    'prepare_project_resume':{'description':'准备项目整体恢复并提交 Agent BPM 的操作建议；展示实际暂停天数和拟顺延节点，客户承诺交期不自动改变。','permission':'pause_resume.create'},
    'query_project_closure_context':{'description':'查询项目终止或正常关闭清单、系统已知阻塞项、历史修订及可选审批流程；不会把局部完成误判为项目关闭。','permission':'project_close.read'},
    'prepare_project_closure_checklist':{'description':'准备建立正常项目结项清单；人工确认后仅创建核对事项，不关闭项目。','permission':'project_close.create'},
    'prepare_project_termination':{'description':'准备客户终止项目的审批建议，固化当前环节、已完成工作和已发生费用；审批生效后转入终止处置。','permission':'project_close.create'},
    'prepare_project_closure_item':{'description':'准备更新一个结项处置或核对事项；保留原状态和依据修订，ERP 来源必须带原记录引用与核对时点。','permission':'project_close.execute'},
    'prepare_project_normal_close':{'description':'准备正常关闭审批；仅在交付、验收、财务、异常和归档等适用清单全部完成后允许提交。','permission':'project_close.create'},
    'prepare_project_settlement_close':{'description':'准备终止结算关闭审批；不强制不适用的交付验收，但要求处置、结算、收付款和归档清单完成。','permission':'project_close.create'},
})
from . import contact_tools
TOOLS['query_uploaded_files']={'description':'查询当前会话中本人上传且仍有权访问的文件元数据；未进行OCR或业务关联。','permission':'file.upload'}
TOOLS['query_contact_context']={'description':'读取指定工程联络单的详细材料、事项标识，以及当前人员可用的责任部门或指定事项的候选处理人。','permission':'contact.read'}
for action,(_,permission,title) in contact_tools.SPECS.items():
    TOOLS['prepare_contact_'+action]={'description':title+'的操作建议。仅在用户要求办理时使用；先查询真实项目、联络单、事项和人员标识；不执行业务，等待用户核对确认。','permission':'contact.'+permission}

SKILLS = {"purchase_request_review": {"name": "采购申请核对", "tools": ["query_purchase_requests"]}}
SKILLS.update({'delivery_risk_analysis':{'name':'供应商发货风险分析','tools':['analyze_delivery_risk']},
               'contact_collaboration_review':{'name':'工程联络协作核对','tools':['query_contact_cases']},
               'business_status_review':{'name':'业务审批与执行核对','tools':['query_purchase_orders']},
               'project_pause_resume':{'name':'项目暂停与恢复','tools':['query_projects','query_project_control_context','prepare_project_pause','prepare_project_resume']},
               'project_termination_closure':{'name':'项目终止、结算与关闭','tools':['query_projects','query_project_closure_context',
                   'prepare_project_closure_checklist','prepare_project_termination','prepare_project_closure_item',
                   'prepare_project_normal_close','prepare_project_settlement_close']}})


def assigned(db, user, kind, key):
    if user.super_admin: return True
    return bool(db.scalar(select(Capability.id).where(Capability.user_id == user.id, Capability.kind == kind, Capability.key == key, Capability.enabled.is_(True))))


def available_tools(db, user):
    return [key for key, tool in TOOLS.items() if assigned(db, user, "TOOL", key) and
            (user.super_admin or any(g.effect == "ALLOW" for g in grants_for(db, user, tool["permission"])))]


def tool_schema(key):
    if key.startswith('prepare_contact_') or key=='query_contact_context':
        schema=contact_tools.ContextInput.model_json_schema() if key=='query_contact_context' else contact_tools.schema(key.removeprefix('prepare_contact_'))
        return {'type':'function','function':{'name':key,'description':TOOLS[key]['description'],'parameters':schema}}
    from .project_closure_tools import ACTION_BY_TOOL,ClosureContextInput,schema as closure_schema
    if key in ACTION_BY_TOOL or key=='query_project_closure_context':
        parameters=ClosureContextInput.model_json_schema() if key=='query_project_closure_context' else closure_schema(ACTION_BY_TOOL[key])
        return {'type':'function','function':{'name':key,'description':TOOLS[key]['description'],'parameters':parameters}}
    if key in {'prepare_project_pause','prepare_project_resume','query_project_control_context'}:
        from .project_control_tools import ProjectContextInput,schema
        parameters=ProjectContextInput.model_json_schema() if key=='query_project_control_context' else schema(key.removeprefix('prepare_project_'))
        return {'type':'function','function':{'name':key,'description':TOOLS[key]['description'],'parameters':parameters}}
    return {"type": "function", "function": {"name": key, "description": TOOLS[key]["description"],
             "parameters": {"type": "object", "properties": {}, "additionalProperties": False}, "strict": True}}


def skill_context(db, user):
    allowed = set(available_tools(db, user))
    result = []
    for key, spec in SKILLS.items():
        if assigned(db, user, "SKILL", key) and set(spec["tools"]) <= allowed:
            path = Path(__file__).resolve().parents[1] / "skills" / key / "SKILL.md"
            content = path.read_text(encoding="utf-8")
            result.append({"key": key, "version": "1.0.0", "hash": content_hash(content), "instructions": content})
    return result


def execute(db, user, key, arguments, run=None):
    if key not in available_tools(db, user): raise DomainError("TOOL_FORBIDDEN", "工具不在当前有效能力范围内", 403)
    if key.startswith('prepare_contact_') or key=='query_contact_context':
        return contact_tools.execute_tool(db,user,key,arguments)
    from .project_closure_tools import ACTION_BY_TOOL
    if key in ACTION_BY_TOOL or key=='query_project_closure_context':
        from .project_closure_tools import execute_tool
        return execute_tool(db,user,key,arguments)
    if key in {'prepare_project_pause','prepare_project_resume','query_project_control_context'}:
        from .project_control_tools import execute_tool
        return execute_tool(db,user,key,arguments)
    if arguments: raise DomainError("INVALID_TOOL_INPUT", "该工具不接受额外参数")
    if key=='query_uploaded_files':
        from .files import conversation_files
        from fastapi.encoders import jsonable_encoder
        if not run or run.user_id!=user.id:raise DomainError("FILE_CONTEXT_INVALID","附件查询须绑定当前任务",403)
        return jsonable_encoder({"data":conversation_files(run.conversation_id,user,db),"source":"agent_db","as_of":now(),"limitations":["仅当前会话可见附件元数据；文件内容尚未解析，不能据此声称已识别文本或完成审批"]})
    if key == "query_projects":
        p = predicate(db, user, "project.read", {"project_id": Project.id})
        data = [select_fields({"id": row.id, "code": row.code, "name": row.name, "status": row.status},
                              require(db, user, "project.read", {"project_id": row.id}))
                for row in db.scalars(select(Project).where(p).order_by(Project.code).limit(100))]
    elif key=='query_purchase_requests':
        data = [request_data(db, user, r) for r in db.scalars(visible_requests(db, user).order_by(PurchaseRequest.created_at.desc()).limit(100))]
    elif key=='query_contact_cases':
        from sqlalchemy import func
        from .models import ContactCase,ContactTask,AssignmentGroup,User
        from .contacts import permitted
        q=select(ContactCase).where(predicate(db,user,'contact.read',{'project_id':ContactCase.project_id,'category':ContactCase.category})).order_by(ContactCase.created_at.desc(),ContactCase.id).limit(100)
        data=[]
        for c in db.scalars(q):
            if not permitted(db,user,'read',c):continue
            counts=dict(db.execute(select(ContactTask.status,func.count()).where(ContactTask.case_id==c.id).group_by(ContactTask.status)).all())
            tasks=[]
            for t in db.scalars(select(ContactTask).where(ContactTask.case_id==c.id).order_by(ContactTask.created_at.desc(),ContactTask.id).limit(20)):
                g=db.get(AssignmentGroup,t.department_id);p=db.get(User,t.assignee_id) if t.assignee_id else None
                tasks.append({'title':t.title,'department':g.name,'assignee':p.display_name if p else None,'status':t.status})
            data.append({'id':c.id,'title':c.title,'project_id':c.project_id,'category':c.category,'mode':c.mode,
                'revision':c.revision,'collaboration_status':'CLOSED' if c.closed_at else 'HISTORY_RECORD' if c.mode=='HISTORY' else 'OPEN',
                'task_counts':counts,'recent_tasks':tasks,'tasks_truncated':sum(counts.values())>20})
        return {'data':data,'source':'agent_db','as_of':now().isoformat(),'limit':100,
            'limitations':['仅当前用户可见范围','最多最新100张联络单，每单最多展示最近20项协作事项，计数包含全部事项',
                '反馈或历史补录不是正式审批，不据此认定整改验收或联络单关闭','本工具只查询，不分派、不审批、不执行业务动作']}
    elif key=='query_purchase_orders':
        from .procurement import visible_orders
        data=visible_orders(db,user)
    elif key=='analyze_delivery_risk':
        from .procurement import delivery_risks
        return delivery_risks(db,user)
    elif 'business_kind' in TOOLS[key]:
        from .domains import visible
        data=visible(db,user,TOOLS[key]['business_kind'])
    else:raise DomainError('TOOL_UNKNOWN','工具未实现',403)
    return {"data": data, "source": "agent_db", "as_of": now().isoformat(), "limit": 100,
            "limitations": ["仅当前用户可见范围", "采购申请不代表正式下单或供应商发货事实"] if key == "query_purchase_requests" else ["仅当前用户可见范围"]}
