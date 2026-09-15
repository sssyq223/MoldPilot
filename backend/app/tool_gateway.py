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
    'query_business_object_candidates':{'description':'按项目号、项目名、模具号、合同号、订单号等线索查询当前权限内候选业务对象；只返回候选和来源，不自动匹配或创建。','permission':'project.dossier.read'},
    'query_quote_acceptance_context':{'description':'按项目线索读取报价、承接、拒单、正式开工和销售合同上下文；只读，不自动承接或开工。','permission':'quote_acceptance.read'},
    'query_quote_evaluation_context':{'description':'按项目线索核对报价阶段成本/工艺/工期依据、加工方式、客户反馈和后续合同上下文；只读，不生成报价或切换加工方式。','permission':'quote_acceptance.read'},
    'query_bid_intake_context':{'description':'按项目线索核对中标接收、客户分类、合同线索、模具关联、承接/拒单和开工上下文；只读，不读取邮箱或客户平台。','permission':'quote_acceptance.read'},
    'query_contract_context':{'description':'按项目或合同线索读取销售合同、整套委外合同、付款节点和替代关系上下文；只读，不上传、不OCR、不确认收付款。','permission':'project.dossier.read'},
    'query_internal_start_readiness':{'description':'按项目线索核对正式开工条件、承接依据、合同和计划上下文；只读，不创建开工通知或执行任务。','permission':'internal_start.read'},
    'query_project_plan_context':{'description':'按项目线索核对项目计划、节点进度、依赖、逾期和大节点覆盖；只读，不重排计划或下达任务。','permission':'project_plan.read'},
    'query_design_route_context':{'description':'按项目、设计单、图纸、BOM物料或任务线索核对设计、BOM、加工路线、计划任务和工程联络影响；只读，不生成图纸或重复ERP设计模块。','permission':'design_route.read'},
    'query_manufacturing_quality_context':{'description':'按项目或工序线索核对制造计划任务、报工事实、设计路线、装配/试模、质检和整改上下文；只读，不登记报工或检验。','permission':'project_plan.read'},
    'query_assembly_trial_context':{'description':'按项目、装配任务或试模线索核对齐套前置、装配工单、完工确认、试模资源、试模报告和异常整改上下文；只读，不替代 ERP 装配/试模执行。','permission':'assembly_issue.read'},
    'query_delivery_logistics_context':{'description':'按项目、发货、物流、签收或验收线索核对供应商发货、仓库收货、检验、出库、客户签收、客户验收和异常整改上下文；只读，不确认交付或维护物流报价。','permission':'warehouse.read'},
    'query_procurement_price_context':{'description':'按项目、料号、价格单、供应商、采购申请或订单线索核对料品、采购价格、设计采购需求和订单跟踪上下文；只读，不询价、不下单、不入库。','permission':'purchase_price.read'},
    'query_project_dossier':{'description':'按项目编号/名称、模具号、工程联络、合同或订单编号反查并汇总当前可见的项目业务档案；只读，不复制 ERP 单据。','permission':'project.dossier.read'},
    'query_contact_cases':{'description':'查询当前用户可见的工程联络单、客户/模具/当前环节、结构化影响项、责任部门、处理人和协作状态。已反馈不是正式批准，历史补录不代表事项已关闭。','permission':'contact.read'},
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
TOOLS['query_contact_context']={'description':'读取指定工程联络单的主信息、结构化影响与动作、实际执行/复验材料、事项标识，以及可用责任部门或候选处理人。','permission':'contact.read'}
for action,(_,permission,title) in contact_tools.SPECS.items():
    TOOLS['prepare_contact_'+action]={'description':title+'的操作建议。仅在用户要求办理时使用；先查询真实项目、联络单、事项和人员标识；不执行业务，等待用户核对确认。','permission':'contact.'+permission}

SKILLS = {"purchase_request_review": {"name": "采购申请核对", "tools": ["query_purchase_requests"]}}
SKILLS.update({'delivery_risk_analysis':{'name':'供应商发货风险分析','tools':['analyze_delivery_risk']},
               'business_object_matching':{'name':'业务对象候选匹配','tools':['query_business_object_candidates']},
               'quote_acceptance_review':{'name':'报价与承接上下文核对','tools':['query_quote_acceptance_context']},
               'quote_evaluation_review':{'name':'报价评估与加工方式核对','tools':['query_quote_evaluation_context']},
               'bid_intake_review':{'name':'中标接收与客户规则核对','tools':['query_bid_intake_context']},
               'contract_context_review':{'name':'合同上下文核对','tools':['query_contract_context']},
               'internal_start_readiness':{'name':'正式开工条件核对','tools':['query_internal_start_readiness']},
               'project_plan_context_review':{'name':'项目计划上下文核对','tools':['query_project_plan_context']},
               'design_route_context_review':{'name':'设计BOM与路线上下文核对','tools':['query_design_route_context']},
               'manufacturing_quality_review':{'name':'制造工序与质检上下文核对','tools':['query_manufacturing_quality_context']},
               'assembly_trial_review':{'name':'装配试模上下文核对','tools':['query_assembly_trial_context']},
               'delivery_logistics_review':{'name':'交付物流上下文核对','tools':['query_delivery_logistics_context']},
               'procurement_price_context_review':{'name':'采购价格与订单上下文核对','tools':['query_procurement_price_context']},
               'contact_collaboration_review':{'name':'工程联络协作核对','tools':['query_contact_cases']},
               'business_status_review':{'name':'业务审批与执行核对','tools':['query_purchase_orders']},
               'project_dossier_review':{'name':'项目业务档案核对','tools':['query_project_dossier']},
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
    if key=='query_project_dossier':
        from .project_dossier import ProjectDossierInput
        return {'type':'function','function':{'name':key,'description':TOOLS[key]['description'],'parameters':ProjectDossierInput.model_json_schema()}}
    if key=='query_business_object_candidates':
        from .business_matching import BusinessMatchInput
        return {'type':'function','function':{'name':key,'description':TOOLS[key]['description'],'parameters':BusinessMatchInput.model_json_schema()}}
    if key=='query_quote_acceptance_context':
        from .quote_tools import QuoteContextInput
        return {'type':'function','function':{'name':key,'description':TOOLS[key]['description'],'parameters':QuoteContextInput.model_json_schema()}}
    if key=='query_quote_evaluation_context':
        from .quote_tools import QuoteContextInput
        return {'type':'function','function':{'name':key,'description':TOOLS[key]['description'],'parameters':QuoteContextInput.model_json_schema()}}
    if key=='query_bid_intake_context':
        from .quote_tools import QuoteContextInput
        return {'type':'function','function':{'name':key,'description':TOOLS[key]['description'],'parameters':QuoteContextInput.model_json_schema()}}
    if key=='query_contract_context':
        from .contract_tools import ContractContextInput
        return {'type':'function','function':{'name':key,'description':TOOLS[key]['description'],'parameters':ContractContextInput.model_json_schema()}}
    if key=='query_internal_start_readiness':
        from .start_tools import StartReadinessInput
        return {'type':'function','function':{'name':key,'description':TOOLS[key]['description'],'parameters':StartReadinessInput.model_json_schema()}}
    if key=='query_project_plan_context':
        from .plan_tools import ProjectPlanContextInput
        return {'type':'function','function':{'name':key,'description':TOOLS[key]['description'],'parameters':ProjectPlanContextInput.model_json_schema()}}
    if key=='query_design_route_context':
        from .design_tools import DesignRouteContextInput
        return {'type':'function','function':{'name':key,'description':TOOLS[key]['description'],'parameters':DesignRouteContextInput.model_json_schema()}}
    if key=='query_manufacturing_quality_context':
        from .plan_tools import ProjectPlanContextInput
        return {'type':'function','function':{'name':key,'description':TOOLS[key]['description'],'parameters':ProjectPlanContextInput.model_json_schema()}}
    if key=='query_assembly_trial_context':
        from .plan_tools import ProjectPlanContextInput
        return {'type':'function','function':{'name':key,'description':TOOLS[key]['description'],'parameters':ProjectPlanContextInput.model_json_schema()}}
    if key=='query_delivery_logistics_context':
        from .plan_tools import ProjectPlanContextInput
        return {'type':'function','function':{'name':key,'description':TOOLS[key]['description'],'parameters':ProjectPlanContextInput.model_json_schema()}}
    if key=='query_procurement_price_context':
        from .procurement_tools import ProcurementPriceContextInput
        return {'type':'function','function':{'name':key,'description':TOOLS[key]['description'],'parameters':ProcurementPriceContextInput.model_json_schema()}}
    if key=='analyze_delivery_risk':
        from .procurement import DeliveryRiskInput
        return {'type':'function','function':{'name':key,'description':TOOLS[key]['description'],'parameters':DeliveryRiskInput.model_json_schema()}}
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
    if key=='query_project_dossier':
        from pydantic import ValidationError
        from .project_dossier import ProjectDossierInput,query
        try:data=ProjectDossierInput.model_validate(arguments or {})
        except ValidationError as error:raise DomainError('INVALID_TOOL_INPUT','项目档案查询参数无效：'+error.errors()[0]['msg']) from None
        return query(db,user,data,set(available_tools(db,user)))
    if key=='query_business_object_candidates':
        from pydantic import ValidationError
        from .business_matching import BusinessMatchInput,query
        try:data=BusinessMatchInput.model_validate(arguments or {})
        except ValidationError as error:raise DomainError('INVALID_TOOL_INPUT','业务对象候选匹配参数无效：'+error.errors()[0]['msg']) from None
        return query(db,user,data,set(available_tools(db,user)))
    if key=='query_quote_acceptance_context':
        from pydantic import ValidationError
        from .quote_tools import QuoteContextInput,query
        try:data=QuoteContextInput.model_validate(arguments or {})
        except ValidationError as error:raise DomainError('INVALID_TOOL_INPUT','报价与承接上下文参数无效：'+error.errors()[0]['msg']) from None
        return query(db,user,data,set(available_tools(db,user)))
    if key=='query_quote_evaluation_context':
        from pydantic import ValidationError
        from .quote_tools import QuoteContextInput
        from .quote_evaluation_tools import query
        try:data=QuoteContextInput.model_validate(arguments or {})
        except ValidationError as error:raise DomainError('INVALID_TOOL_INPUT','报价评估上下文参数无效：'+error.errors()[0]['msg']) from None
        return query(db,user,data,set(available_tools(db,user)))
    if key=='query_bid_intake_context':
        from pydantic import ValidationError
        from .quote_tools import QuoteContextInput
        from .bid_intake_tools import query
        try:data=QuoteContextInput.model_validate(arguments or {})
        except ValidationError as error:raise DomainError('INVALID_TOOL_INPUT','中标接收上下文参数无效：'+error.errors()[0]['msg']) from None
        return query(db,user,data,set(available_tools(db,user)))
    if key=='query_contract_context':
        from pydantic import ValidationError
        from .contract_tools import ContractContextInput,query
        try:data=ContractContextInput.model_validate(arguments or {})
        except ValidationError as error:raise DomainError('INVALID_TOOL_INPUT','合同上下文参数无效：'+error.errors()[0]['msg']) from None
        return query(db,user,data,set(available_tools(db,user)))
    if key=='query_internal_start_readiness':
        from pydantic import ValidationError
        from .start_tools import StartReadinessInput,query
        try:data=StartReadinessInput.model_validate(arguments or {})
        except ValidationError as error:raise DomainError('INVALID_TOOL_INPUT','正式开工条件参数无效：'+error.errors()[0]['msg']) from None
        return query(db,user,data,set(available_tools(db,user)))
    if key=='query_project_plan_context':
        from pydantic import ValidationError
        from .plan_tools import ProjectPlanContextInput,query
        try:data=ProjectPlanContextInput.model_validate(arguments or {})
        except ValidationError as error:raise DomainError('INVALID_TOOL_INPUT','项目计划上下文参数无效：'+error.errors()[0]['msg']) from None
        return query(db,user,data,set(available_tools(db,user)))
    if key=='query_design_route_context':
        from pydantic import ValidationError
        from .design_tools import DesignRouteContextInput,query
        try:data=DesignRouteContextInput.model_validate(arguments or {})
        except ValidationError as error:raise DomainError('INVALID_TOOL_INPUT','设计BOM与路线上下文参数无效：'+error.errors()[0]['msg']) from None
        return query(db,user,data,set(available_tools(db,user)))
    if key=='query_manufacturing_quality_context':
        from pydantic import ValidationError
        from .plan_tools import ProjectPlanContextInput
        from .manufacturing_quality_tools import query
        try:data=ProjectPlanContextInput.model_validate(arguments or {})
        except ValidationError as error:raise DomainError('INVALID_TOOL_INPUT','制造与质检上下文参数无效：'+error.errors()[0]['msg']) from None
        return query(db,user,data,set(available_tools(db,user)))
    if key=='query_assembly_trial_context':
        from pydantic import ValidationError
        from .plan_tools import ProjectPlanContextInput
        from .assembly_trial_tools import query
        try:data=ProjectPlanContextInput.model_validate(arguments or {})
        except ValidationError as error:raise DomainError('INVALID_TOOL_INPUT','装配试模上下文参数无效：'+error.errors()[0]['msg']) from None
        return query(db,user,data,set(available_tools(db,user)))
    if key=='query_delivery_logistics_context':
        from pydantic import ValidationError
        from .plan_tools import ProjectPlanContextInput
        from .delivery_logistics_tools import query
        try:data=ProjectPlanContextInput.model_validate(arguments or {})
        except ValidationError as error:raise DomainError('INVALID_TOOL_INPUT','交付物流上下文参数无效：'+error.errors()[0]['msg']) from None
        return query(db,user,data,set(available_tools(db,user)))
    if key=='query_procurement_price_context':
        from pydantic import ValidationError
        from .procurement_tools import ProcurementPriceContextInput,query
        try:data=ProcurementPriceContextInput.model_validate(arguments or {})
        except ValidationError as error:raise DomainError('INVALID_TOOL_INPUT','采购价格与订单上下文参数无效：'+error.errors()[0]['msg']) from None
        return query(db,user,data,set(available_tools(db,user)))
    if key=='analyze_delivery_risk':
        from pydantic import ValidationError
        from .procurement import DeliveryRiskInput,analyze_delivery_risk
        try:data=DeliveryRiskInput.model_validate(arguments or {})
        except ValidationError as error:raise DomainError('INVALID_TOOL_INPUT','发货风险分析参数无效：'+error.errors()[0]['msg']) from None
        return analyze_delivery_risk(db,user,data)
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
                tasks.append({'id':t.id,'title':t.title,'department':g.name,'assignee':p.display_name if p else None,'status':t.status,
                    'affected_type':t.affected_type,'affected_ref':t.affected_ref,'planned_action':t.planned_action,
                    'delivery_impact_days':t.delivery_impact_days,'estimated_amount':str(t.estimated_amount) if t.estimated_amount is not None else None,
                    'currency':t.currency,'actual_completed_at':t.actual_completed_at,'actual_hours':str(t.actual_hours) if t.actual_hours is not None else None,
                    'actual_amount':str(t.actual_amount) if t.actual_amount is not None else None,'actual_currency':t.actual_currency})
            data.append({'id':c.id,'title':c.title,'project_id':c.project_id,'category':c.category,'mode':c.mode,
                'customer_name':c.customer_name,'mold_number':c.mold_number,'product_ref':c.product_ref,
                'problem_source':c.problem_source,'current_stage':c.current_stage,'change_type':c.change_type,'urgency':c.urgency,
                'revision':c.revision,'collaboration_status':'CLOSED' if c.closed_at else 'HISTORY_RECORD' if c.mode=='HISTORY' else 'OPEN',
                'task_counts':counts,'recent_tasks':tasks,'tasks_truncated':sum(counts.values())>20})
        return {'data':data,'source':'agent_db','as_of':now().isoformat(),'limit':100,
            'limitations':['仅当前用户可见范围','最多最新100张联络单，每单最多展示最近20项协作事项，计数包含全部事项',
                '反馈或历史补录不是正式审批，不据此认定整改验收或联络单关闭','本工具只查询，不分派、不审批、不执行业务动作']}
    elif key=='query_purchase_orders':
        from .procurement import visible_orders
        data=visible_orders(db,user)
    elif 'business_kind' in TOOLS[key]:
        from .domains import visible
        data=visible(db,user,TOOLS[key]['business_kind'])
    else:raise DomainError('TOOL_UNKNOWN','工具未实现',403)
    return {"data": data, "source": "agent_db", "as_of": now().isoformat(), "limit": 100,
            "limitations": ["仅当前用户可见范围", "采购申请不代表正式下单或供应商发货事实"] if key == "query_purchase_requests" else ["仅当前用户可见范围"]}
