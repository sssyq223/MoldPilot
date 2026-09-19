"""Code-registered tools; no runtime imports, shell, arbitrary URL or write SQL."""
from pathlib import Path
from functools import lru_cache
import re
from sqlalchemy import select, or_
from domain_packs.mold.erp.core.business import visible_requests, request_data
from agent_core.errors import DomainError
from agent_core.host_ports import host_ports
from domain_packs.mold.erp.core.contracts import ProjectPlanContextInput


_host = host_ports()
m = _host.models
Project, PurchaseRequest, Capability = m.Project, m.PurchaseRequest, m.Capability
grants_for = _host.grants_for
predicate = _host.predicate
require = _host.require
select_fields = _host.select_fields
now = _host.now
content_hash = _host.content_hash


def skill_agent_description(content: str) -> str:
    lines = [line.strip() for line in content.splitlines()]
    useful = []
    capture = False
    for line in lines:
        if not line or line.startswith('#') or line.startswith('版本：'):
            continue
        if line.startswith(('目标：', '适用条件：', '边界：')):
            useful.append(line)
            capture = False
            continue
        if line.startswith('步骤：'):
            useful.append('步骤：')
            capture = True
            continue
        if capture and line[:2] in {'1.', '2.', '3.', '4.', '5.'}:
            useful.append(line)
            continue
        if useful and len(' '.join(useful)) >= 520:
            break
    text = '\n'.join(useful).strip()
    return text[:700] if text else content.strip()[:700]

TOOLS = {
    "query_projects": {"description": "查询当前用户有权查看的本项目项目，返回事实和来源。", "permission": "project.read"},
    "query_purchase_requests": {"description": "查询本项目采购申请及审批状态。不是正式订单发货记录，不能据此判断发货延期。", "permission": "purchase.read"},
}
from domain_packs.mold.erp.core.domain_schemas import CATALOG
TOOLS.update({f'query_{key}': {'description':f'查询当前人员授权范围内的{name["name"]}及真实审批、生效状态。',
                              'permission':f'{key}.read','business_kind':key} for key,name in CATALOG.items()})
TOOLS['query_engineering_change']['description']='查询已有工程联络方案审批材料及生效状态；独立联络协作、责任部门和人员进度请使用工程联络协作查询工具。'
TOOLS.update({
    'query_business_object_candidates':{'description':'按项目号、项目名、模具号、合同号、订单号等线索查询当前权限内候选业务对象；只返回候选和来源，不自动匹配或创建。','permission':'project.dossier.read'},
    'query_project_lifecycle_context':{'description':'按项目线索读取从启动、执行到收尾关闭的全生命周期三段摘要与当前分段；只读，后续按总览逐层展开，不一次暴露全部业务工具。','permission':'project.read'},
    'query_project_kickoff_context':{'description':'按项目线索一次核对承接、销售合同、正式开工和基线计划四个独立阶段，返回阻塞项与下一步工具；只读，不自动跨阶段办理。','permission':'project.read'},
    'query_project_execution_context':{'description':'按项目线索一次核对基线计划、设计/BOM、采购或整套委外、制造质检、装配试模、交付签收与客户验收，返回当前执行焦点；只读，不自动写回或跨阶段办理。','permission':'project.read'},
    'query_project_completion_context':{'description':'按项目线索一次核对交付验收、发票回款、供应商结算、异常关闭、全过程归档和最终关闭，区分正常关闭与终止结算；只读，不登记或关闭项目。','permission':'project.read'},
    'query_quote_acceptance_context':{'description':'按项目线索读取报价、承接、拒单、正式开工和销售合同上下文；只读，不自动承接或开工。','permission':'quote_acceptance.read'},
    'prepare_quote_acceptance_decision':{'description':'准备报价承接或拒单审批建议；必须使用查询返回的真实项目、项目版本和流程 ID，本人确认后才提交 Agent BPM。','permission':'quote_acceptance.create'},
    'query_quote_evaluation_context':{'description':'按项目线索核对报价阶段成本/工艺/工期依据、加工方式、客户反馈和后续合同上下文；只读，不生成报价或切换加工方式。','permission':'quote_acceptance.read'},
    'prepare_quotation_version':{'description':'使用本轮客户资料准备版本化客户报价；必须结构化填写成本/工艺/工期、初步加工方式、价格、交期和收款条件，本人确认后冻结资料并提交 Agent BPM。','permission':'quotation.create'},
    'prepare_quotation_feedback':{'description':'准备登记指定生效报价版本的客户反馈；本人确认后仅追加反馈事实，不自动承接、拒单或生成新报价。','permission':'quotation.execute'},
    'query_bid_intake_context':{'description':'按项目线索核对中标接收、客户分类、合同线索、模具关联、承接/拒单和开工上下文；只读，不读取邮箱或客户平台。','permission':'quote_acceptance.read'},
    'prepare_bid_intake_draft':{'description':'使用本轮明确上传的中标、外部开工、合同参考或模具图片资料，准备登记或补充同一条中标接收草稿；可人工登记客户工艺确认、外部订单、开工日期和交期，确认后追加不可变版本，不自动承接、拒单、建正式合同或内部开工。','permission':'quote_acceptance.create'},
    'query_contract_context':{'description':'按项目或合同线索读取销售合同、整套委外合同、付款节点和替代关系上下文；只读，不上传、不OCR、不确认收付款。','permission':'project.dossier.read'},
    'prepare_contract_record':{'description':'使用本轮明确上传的 PDF、图片或 DOCX 原件，准备销售合同或整套委外合同的原始、替代或追加登记审批建议；替代合同必须逐条把前序版本链的历史实收实付归属到新付款节点，追加合同保持独立；必须使用真实项目、项目版本、附件 ID 和流程 ID，本人确认后才冻结附件并提交 Agent BPM。','permission':'project.dossier.read'},
    'prepare_contract_signing_record':{'description':'准备整套委外合同签署文件或签署状态证据登记建议；必须使用真实项目版本、已生效整套委外合同和签署依据，本人确认后才写入签署记录，不发起电子签署。','permission':'full_outsource_contract.execute'},
    'query_internal_start_readiness':{'description':'按项目线索核对正式开工条件、承接依据、合同和计划上下文；只读，不创建开工通知或执行任务。','permission':'internal_start.read'},
    'prepare_internal_start':{'description':'准备正式内部开工通知审批建议；必须使用查询返回的真实项目、项目版本、已生效承接记录和流程 ID，本人确认后才提交 Agent BPM。','permission':'internal_start.create'},
    'query_project_plan_context':{'description':'按项目线索核对项目计划、节点进度、依赖、逾期和大节点覆盖；只读，不重排计划或下达任务。','permission':'project_plan.read'},
    'prepare_project_plan_baseline':{'description':'准备项目基线计划审批建议；必须使用查询返回的真实项目、项目版本、完整节点清单和流程 ID，本人确认后才提交 Agent BPM。','permission':'project_plan.create'},
    'prepare_project_plan_change':{'description':'准备项目计划变更审批建议；必须使用查询返回的真实项目、当前计划和节点清单，本人确认后才提交 Agent BPM。','permission':'plan_change.create'},
    'prepare_plan_department_confirmation':{'description':'准备计划变更生效后的部门影响确认；只能使用计划上下文返回的待确认项 ID 和版本，本人确认后仅记录本部门已核对。','permission':'plan_change.execute'},
    'query_design_route_context':{'description':'按项目、设计单、图纸、BOM物料或任务线索核对设计、BOM、加工路线、计划任务和工程联络影响；只读，不生成图纸或重复ERP设计模块。','permission':'design_route.read'},
    'prepare_design_order_approval':{'description':'读取指定 ERP 设计订单的当前详情，冻结来源版本、证据快照及本轮附件，并准备提交 Agent 通用 BPM；不调用 ERP 设计审批。','permission':'design_route.create'},
    'query_manufacturing_quality_context':{'description':'按项目或工序线索核对制造计划任务、报工事实、设计路线、装配/试模、质检和整改上下文；只读，不登记报工或检验。','permission':'project_plan.read'},
    'query_assembly_trial_context':{'description':'按项目、装配任务或试模线索核对齐套前置、装配工单、完工确认、试模资源、试模报告和异常整改上下文；只读，不替代 ERP 装配/试模执行。','permission':'assembly_issue.read'},
    'query_delivery_logistics_context':{'description':'按项目、发货、物流、签收或验收线索核对供应商发货、仓库收货、检验、出库、客户签收、客户验收和异常整改上下文；只读，不确认交付或维护物流报价。','permission':'warehouse.read'},
    'prepare_logistics_route':{'description':'准备登记仓库已确认的固定物流路线或模具项目实际路线；包含地点、承运商、车型、重量、运输方式、计价单位、税制、有效期与来源依据，本人确认后才写入。','permission':'warehouse.configure'},
    'prepare_logistics_quote':{'description':'准备由采购价格审批人确认的物流有效报价或项目本次结算价格；包含有效期、询比议价方式、比较摘要、报价依据和对账依据，本人确认后才登记生效。','permission':'purchase_price.approve'},
    'query_full_outsource_context':{'description':'按项目、合同、供应商、委外节点、质量延期或扣款线索核对整套委外加工方式、合同、供应商执行、验收、整改和结算上下文；只读，不创建供应商门户或重复 ERP 委外执行。','permission':'full_outsource_contract.read'},
    'prepare_supplier_material_handoff':{'description':'准备向供应商提供客户资料、设计图纸或技术标准的交接证据登记建议；必须使用真实项目版本、供应商、已生效整套委外合同和交接依据，本人确认后才写入资料交接记录。','permission':'full_outsource_contract.execute'},
    'prepare_supplier_material_verification':{'description':'准备登记供应商对一条已批准资料交接的收到、接受、待澄清或退回核验结果；必须使用查询返回的真实交接记录，本人确认后才追加核验事实。','permission':'full_outsource_contract.execute'},
    'prepare_supplier_progress_policy':{'description':'准备供应商阶段填报的版本化频率与必需证据规则；必须使用真实项目、供应商、已生效整套委外合同和可选计划节点，本人确认后才生效。','permission':'full_outsource_contract.execute'},
    'prepare_supplier_progress_report':{'description':'准备供应商设计、采购、生产、质检、装配、试模或验收节点上报证据登记建议；必须使用真实项目版本、供应商、已生效整套委外合同和可选计划节点，本人确认后才写入。','permission':'full_outsource_contract.execute'},
    'query_change_intake_context':{'description':'按项目、模具、客户设变、工程联络或合同线索核对设变承接、收费/合同/开工依据、原模具/原项目、影响任务、执行复验和关闭上下文；只读，不替代 ERP 执行。','permission':'engineering_change.read'},
    'query_finance_context':{'description':'按项目、合同、付款节点、供应商付款、回款、发票、费用或结项线索核对财务节点与收付款上下文；只读，不确认回款付款、不生成财务台账。','permission':'project.dossier.read'},
    'prepare_customer_receivable_schedule':{'description':'准备由财务确认客户收款节点的结构化触发事件、账期、预计到期日及特殊标记；不从条件文字猜测日期，本人确认后才更新节点。','permission':'customer_receipt.confirm'},
    'prepare_customer_receipt_confirmation':{'description':'准备客户实际回款确认登记建议；必须使用查询返回的真实项目版本、已生效销售合同和收款节点，本人确认后才写入回款确认台账。','permission':'customer_receipt.confirm'},
    'prepare_supplier_payment_confirmation':{'description':'准备供应商实际付款确认登记建议；必须使用查询返回的真实项目版本、已审批供应商付款申请和授权余额，本人确认后才写入付款确认记录。','permission':'finance.confirm'},
    'prepare_supplier_deduction_settlement':{'description':'准备供应商扣款责任或结算依据登记建议；必须使用查询返回的真实项目版本、供应商、委外合同/工程联络线索和正式依据，本人确认后才写入扣款结算记录。','permission':'finance.confirm'},
    'prepare_mold_transfer_receipt':{'description':'准备由财务按客户签收日期登记移模时间；必须使用真实项目版本、签收单号、签收人和依据，本人确认后才写入，客户签收不等于质量验收。','permission':'customer_receipt.confirm'},
    'query_governance_context':{'description':'按项目线索核对权限矩阵、审计事件、站内通知、附件版本和 ERP 来源操作状态；只读，不授权、不下载、不导出。','permission':'audit.read'},
    'query_operations_readiness_context':{'description':'核对部署、容量、响应时间、可用性、备份恢复和日志保留等运行交付验收缺口；只读，不承诺未经确认的 SLA 或性能指标。','permission':'audit.read'},
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
from domain_packs.mold import contact_tools
from domain_packs.mold import erp_design_mcp

TOOLS.update(erp_design_mcp.TOOL_SPECS)
TOOLS['query_uploaded_files']={'description':'查询当前会话中本人上传且仍有权访问的文件元数据；未进行OCR或业务关联。','permission':'file.upload'}
TOOLS['query_contact_context']={'description':'读取指定工程联络单的主信息、结构化影响与动作、实际执行/复验材料、事项标识，以及可用责任部门或候选处理人。','permission':'contact.read'}
for action,(_,permission,title) in contact_tools.SPECS.items():
    TOOLS['prepare_contact_'+action]={'description':title+'的操作建议。仅在用户要求办理时使用；先查询真实项目、联络单、事项和人员标识；不执行业务，等待用户核对确认。','permission':'contact.'+permission}

SKILLS = {"purchase_request_review": {"name": "采购申请核对", "tools": ["query_purchase_requests"]}}
SKILLS.update({'delivery_risk_analysis':{'name':'供应商发货风险分析','tools':['analyze_delivery_risk']},
               'business_object_matching':{'name':'业务对象候选匹配','tools':['query_business_object_candidates']},
               'project_lifecycle_orchestration':{'name':'项目全生命周期协调','tools':['query_project_lifecycle_context'],
                   'optional_tools':['query_project_kickoff_context','query_project_execution_context',
                       'query_project_completion_context','query_project_control_context'],
                   'activation_tools':['query_project_lifecycle_context'],
                   'activation_queries':['项目全生命周期','项目全流程','从接单到结项','从接单到关闭',
                       '从承接到收尾','整个项目到哪一步','合同开工计划执行收尾','全流程进度'],
                   'auto_activation_queries':['项目全生命周期','项目全流程','从接单到结项','从接单到关闭',
                       '从承接到收尾','整个项目到哪一步','合同开工计划执行收尾','全流程进度'],
                   'suppress_tool_search_on_auto_activation':True},
               'quote_acceptance_review':{'name':'报价与承接上下文核对','tools':['query_quote_acceptance_context'],
                   'optional_tools':['query_quote_evaluation_context','prepare_quotation_version',
                       'prepare_quotation_feedback','prepare_quote_acceptance_decision'],
                   'activation_queries':['报价承接','报价拒单','承接','拒单','客户反馈']},
               'quote_evaluation_review':{'name':'报价评估与加工方式核对','tools':['query_quote_evaluation_context'],
                   'optional_tools':['prepare_quotation_version','prepare_quotation_feedback',
                       'query_quote_acceptance_context','prepare_quote_acceptance_decision'],
                   'activation_queries':['报价评估','加工方式','成本工艺工期','报价成本','最终加工方式']},
               'bid_intake_review':{'name':'中标接收与客户规则核对','tools':['query_bid_intake_context'],
                   'optional_tools':['prepare_bid_intake_draft','query_business_object_candidates'],
                   'activation_queries':['中标接收','客户分类','客户规则','模具关联','开工依据']},
               'contract_context_review':{'name':'合同上下文核对','tools':['query_contract_context'],
                   'optional_tools':['prepare_contract_record','prepare_contract_signing_record'],
                   'activation_queries':['合同','合同登记','销售合同','整套委外合同','合同号','付款节点','补齐合同','替代合同','合同签署','签署文件']},
               'internal_start_readiness':{'name':'正式开工条件核对','tools':['query_internal_start_readiness'],
                   'optional_tools':['prepare_internal_start'],
                   'activation_queries':['正式开工','开工通知','开工条件','内部开工'],
                   # A formal-start question has one authoritative read boundary.
                   # Activate it before the model sees other department terms such
                   # as procurement or finance in the same handoff question.  A
                   # positive operation request still keeps ToolSearch available
                   # so prepare_internal_start remains strictly on demand.
                   'auto_activation_queries':['正式开工','开工通知','开工条件','内部开工'],
                   'suppress_tool_search_on_auto_activation':True,
                   'priority_patterns':['正式开工|开工通知|开工条件|内部开工']},
               'project_kickoff_orchestration':{'name':'项目启动链路协调','tools':['query_project_kickoff_context'],
                   'optional_tools':['query_bid_intake_context','prepare_bid_intake_draft',
                       'query_quote_evaluation_context','prepare_quotation_version',
                       'prepare_quotation_feedback','query_quote_acceptance_context','prepare_quote_acceptance_decision',
                       'query_contract_context','prepare_contract_record','query_internal_start_readiness',
                       'prepare_internal_start','query_project_plan_context','prepare_project_plan_baseline'],
                   'activation_tools':['query_project_kickoff_context'],
                   'activation_queries':['项目启动链路','接单到计划','合同开工计划','项目推进到哪一步',
                       '项目下一步','继续推进项目','从承接到开工','从开工到计划']},
                'project_execution_orchestration':{'name':'项目执行链路协调','tools':['query_project_execution_context'],
                   'optional_tools':['query_project_plan_context','query_design_route_context',
                       'query_procurement_price_context','query_full_outsource_context',
                       'query_manufacturing_quality_context','query_assembly_trial_context',
                       'query_delivery_logistics_context'],
                   'activation_tools':['query_project_execution_context'],
                   'activation_queries':['项目执行链路','计划之后各环节','设计采购制造装配交付',
                       '项目执行到哪里','项目执行卡在哪','继续推进项目执行','完整执行进度'],
                   # A full-chain question has one unambiguous read boundary. Expose
                   # only the coordinator first; it can recommend one stage reader
                   # after it has established the project's actual branch and focus.
                    'auto_activation_queries':['项目执行链路','计划之后各环节','设计采购制造装配交付',
                        '项目执行到哪里','项目执行卡在哪','继续推进项目执行','完整执行进度'],
                    'suppress_tool_search_on_auto_activation':True},
                'project_completion_orchestration':{'name':'项目收尾链路协调','tools':['query_project_completion_context'],
                    'optional_tools':['query_delivery_logistics_context','query_finance_context',
                        'query_project_closure_context','prepare_customer_receivable_schedule','prepare_customer_receipt_confirmation',
                        'prepare_supplier_payment_confirmation','prepare_supplier_deduction_settlement',
                        'prepare_project_closure_checklist','prepare_project_closure_item',
                        'prepare_project_normal_close','prepare_project_settlement_close'],
                    'activation_tools':['query_project_completion_context'],
                    'activation_queries':['项目收尾链路','交付后结算关闭','项目收尾到哪里','能否关闭项目',
                        '客户验收后还有什么','终止结算完成了吗','结算到归档关闭'],
                    'auto_activation_queries':['项目收尾链路','交付后结算关闭','项目收尾到哪里','能否关闭项目',
                        '客户验收后还有什么','终止结算完成了吗','结算到归档关闭'],
                    'suppress_tool_search_on_auto_activation':True},
               'project_plan_context_review':{'name':'项目计划上下文核对','tools':['query_project_plan_context'],
                   'optional_tools':['prepare_project_plan_baseline'],
                   'activation_queries':['项目计划','大节点','基线计划','计划任务','节点进度']},
               'project_plan_change':{'name':'项目计划变更','tools':['query_project_plan_context'],
                   'optional_tools':['prepare_project_plan_change','prepare_plan_department_confirmation'],
                   'activation_queries':['项目计划变更','计划变更','节点顺延','部门影响确认']},
               'design_route_context_review':{'name':'设计BOM与路线上下文核对','tools':['query_design_route_context'],
                   'optional_tools':['query_project_plan_context','prepare_project_plan_change','prepare_design_order_approval'],
                   'activation_queries':['设计BOM','加工路线','图纸','设计路线','BOM路线']},
               'manufacturing_quality_review':{'name':'制造工序与质检上下文核对','tools':['query_manufacturing_quality_context'],
                   'activation_queries':['制造工序','现场报工','质检报告','整改复检','生产进度']},
               'assembly_trial_review':{'name':'装配试模上下文核对','tools':['query_assembly_trial_context'],
                   'activation_queries':['装配齐套','装配工单','试模安排','试模报告','装配试模']},
               'delivery_logistics_review':{'name':'交付物流路线与价格协同','tools':['query_delivery_logistics_context'],
                   'optional_tools':['prepare_logistics_route','prepare_logistics_quote'],
                   'activation_queries':['出库发货','物流路线','固定路线','实际路线','物流报价','物流询价','物流比价','物流议价','本次物流结算价格','本次结算价格','物流对账','客户签收','客户验收','发货物流']},
               'full_outsource_review':{'name':'整套委外协同上下文核对','tools':['query_full_outsource_context'],
                   'optional_tools':['prepare_contract_signing_record','prepare_supplier_material_handoff','prepare_supplier_material_verification','prepare_supplier_progress_policy','prepare_supplier_progress_report','prepare_supplier_deduction_settlement'],
                   'activation_queries':['整套委外执行','整套委外加工','供应商节点上报','供应商上报规则','上报频率','证据模板','供应商节点','供应商上报','进度上报','委外验收','委外扣款','委外合同签署','签署扫描件','资料交接','资料核验','资料接受','资料退回','供应商资料']},
               'change_intake_review':{'name':'设变承接上下文核对','tools':['query_change_intake_context'],
                   'optional_tools':['query_project_plan_context','prepare_project_plan_change'],
                   'activation_queries':['客户设变','设变承接','工程设变','收费变更','原模具']},
               'finance_context_review':{'name':'财务节点与收付款核对','tools':['query_finance_context'],
                   'optional_tools':['prepare_customer_receivable_schedule','prepare_customer_receipt_confirmation','prepare_supplier_payment_confirmation','prepare_supplier_deduction_settlement','prepare_mold_transfer_receipt'],
                   'activation_queries':['财务节点','收付款','回款','付款','发票','结算','移模时间','移模签收']},
               'governance_context_review':{'name':'治理权限与来源核对','tools':['query_governance_context'],
                   'activation_queries':['治理上下文','权限矩阵','审计','附件版本','来源治理']},
               'operations_readiness_review':{'name':'运行交付就绪核对','tools':['query_operations_readiness_context'],
                   'activation_queries':['运行交付','部署验收','备份恢复','日志保留','容量']},
               'procurement_price_context_review':{'name':'采购价格与订单上下文核对','tools':['query_procurement_price_context'],
                   'activation_queries':['采购价格','价格单','采购订单','试模料','料号价格']},
               'contact_collaboration_review':{'name':'工程联络协作核对','tools':['query_contact_cases'],
                   'optional_tools':['query_contact_context','prepare_contact_resolution','prepare_contact_review',
                                     'prepare_contact_close','prepare_contact_respond','prepare_contact_task',
                                     'prepare_contact_assign','prepare_contact_set_reviewer',
                                     'prepare_contact_cancel_task','prepare_contact_note',
                                     'prepare_contact_attach','prepare_contact_create'],
                   'activation_tools':['query_contact_cases','query_contact_context','prepare_contact_resolution',
                                       'prepare_contact_review','prepare_contact_close','prepare_contact_respond'],
                   'activation_queries':['工程联络','联络单','联络协作','工程联络关闭','联络单关闭','联络反馈']},
               'business_status_review':{'name':'业务审批与执行核对','tools':['query_purchase_orders']},
               'project_dossier_review':{'name':'项目业务档案核对','tools':['query_project_dossier'],
                   'activation_queries':['项目业务档案','业务档案','项目档案','反查项目']},
               'project_pause_resume':{'name':'项目暂停与恢复','tools':['query_projects','query_project_control_context'],
                   'optional_tools':['prepare_project_pause','prepare_project_resume'],
                   'activation_queries':['项目暂停','项目恢复','暂停恢复','恢复项目']},
               'project_termination_closure':{'name':'项目终止、结算与关闭','tools':['query_projects','query_project_closure_context'],
                   'optional_tools':['prepare_project_closure_checklist','prepare_project_termination',
                   'prepare_project_closure_item','prepare_project_normal_close','prepare_project_settlement_close'],
                   'activation_queries':['项目终止','项目结项','项目关闭','终止结算','正常关闭']}})

SKILLS.update({
    'erp_new_mold_design_upload': {
        'name': 'ERP 新模设计上传流程',
        'tools': ['erp_design_parse_new_mold_upload', 'erp_design_get_drawing_status',
                  'erp_design_get_upload_result', 'erp_design_validate_rows'],
        'optional_tools': ['erp_design_reprice_rows', 'erp_design_get_approval_config',
                           'erp_design_rematch_no_drawing', 'erp_design_import_new_mold',
                           'erp_design_submit_upload_change', 'erp_design_download_file',
                           'erp_design_preview_drawing', 'erp_design_auto_correct_rows',
                           'erp_design_evaluate_tolerances'],
        # An attachment-parse request starts with exactly one capability. The
        # status/result tools remain discoverable later by exact ToolSearch,
        # but a generic “五金清单” search cannot fan out into BOM readers.
        'activation_tools': ['erp_design_parse_new_mold_upload'],
        'activation_queries': ['解析上传附件', '上传新模钢料表', '上传新模五金表', '新模设计上传', '设计清单导入',
                               '查看上传订单', '查看订单明细', '核算价格', '价格核算', '图纸预览', '预览图纸',
                               '自动修正参数', '按图纸修正', '修正数量', '修正长宽厚'],
    },
    'erp_design_modify_mold_upload': {
        'name': 'ERP 修模改模清单上传流程（类型：改模）',
        'tools': ['erp_design_parse_modify_mold_upload', 'erp_design_get_drawing_status',
                  'erp_design_get_upload_result', 'erp_design_validate_rows'],
        'optional_tools': ['erp_design_reprice_rows', 'erp_design_get_modify_mold_approval_config',
                           'erp_design_rematch_no_drawing', 'erp_design_import_modify_mold',
                           'erp_design_download_file', 'erp_design_preview_drawing',
                           'erp_design_auto_correct_rows', 'erp_design_evaluate_tolerances'],
        # The upload parser is the single initial capability. Follow-up tools
        # are enabled after ERP returns the owned repair_other session.
        'activation_tools': ['erp_design_parse_modify_mold_upload'],
        'activation_queries': ['上传改模钢料清单', '上传改模五金清单', '上传修模改模采购清单',
                               '改模采购清单', '改模设计上传', '类型选择改模', '上传时选择改模',
                               '修模改模清单上传', '解析改模清单附件'],
    },
    'erp_design_tolerance_evaluation': {
        'name': 'ERP 新模钢料公差判断',
        'tools': ['erp_design_evaluate_tolerances'],
        'activation_tools': ['erp_design_evaluate_tolerances'],
        'activation_queries': ['判断公差', '公差判断', '公差档位', '公差范围',
                               '长度允许范围', '宽度允许范围', '厚度允许范围', '对角公差'],
    },
    'erp_design_price_calculation': {
        'name': 'ERP 新模钢料价格核算',
        'tools': ['erp_design_get_upload_result', 'erp_design_reprice_rows'],
        'activation_tools': ['erp_design_get_upload_result', 'erp_design_reprice_rows'],
        'activation_queries': ['算价格', '核算价格', '价格核算', '重新核价', '钢料核价', '核算单价'],
    },
    'erp_design_drawing_preview': {
        'name': 'ERP 新模图纸预览',
        'tools': ['erp_design_get_upload_result', 'erp_design_preview_drawing'],
        'activation_tools': ['erp_design_get_upload_result', 'erp_design_preview_drawing'],
        'activation_queries': ['看图纸', '图纸预览', '预览图纸', '查看图纸', '打开图纸'],
    },
    'erp_design_drawing_auto_correction': {
        'name': 'ERP 新模图纸参数自动修正',
        'tools': ['erp_design_get_upload_result', 'erp_design_auto_correct_rows'],
        'optional_tools': ['erp_design_reprice_rows'],
        'activation_tools': ['erp_design_get_upload_result', 'erp_design_auto_correct_rows', 'erp_design_reprice_rows'],
        'activation_queries': ['自动修正', '自动修正参数', '按图纸修正', '修正数量', '修正长宽厚', '修正料型', '图纸回填'],
    },
    'erp_design_workspace_review': {
        'name': 'ERP 设计资料核对',
        'tools': ['erp_design_query_orders'],
        # Keep a plain order-table request bounded to the order reader.  BOM
        # readers remain available for the explicit combined material-list
        # aliases below; drawing versions, changes and master data have their
        # own intent groups so an order ID is never guessed as another resource.
        'optional_tools': ['erp_design_query_bom', 'erp_design_query_bom_report'],
        'activation_queries': ['ERP设计订单', '查看设计订单', '打开设计订单', '设计订单表格', '设计订单可视化',
                               'ERP BOM', 'ERP设计资料',
                               '设计与物料清单', '钢料清单', '五金清单', '模具物料'],
        # management-system uses this mold-number shape as its stable design
        # order lookup key. The Harness treats the matching ERP workspace as
        # authoritative before considering the local design-route context.
        'priority_patterns': [r'(?i)(?<![A-Z0-9])M\d{5,}-P\d+(?![A-Z0-9])'],
    },
    'erp_design_drawing_version_review': {
        'name': 'ERP 图纸版本查询与对比',
        'tools': ['erp_design_query_drawing_versions'],
        'optional_tools': ['erp_design_get_record', 'erp_design_compare_drawing_versions'],
        'activation_queries': ['ERP图纸版本', '查询图纸版本', '查看图纸版本', '图纸版本对比'],
    },
    'erp_design_order_approval': {
        'name': 'ERP 设计订单 Agent 审批',
        'tools': ['erp_design_query_orders', 'erp_design_get_record'],
        'optional_tools': ['prepare_design_order_approval'],
        'activation_tools': ['erp_design_query_orders', 'erp_design_get_record', 'prepare_design_order_approval'],
        'activation_queries': ['设计订单去审批', '提交设计订单审批', '新模设计审批', '改模设计审批',
                               '设计上传审批', '设计订单审批材料'],
    },
    'erp_design_order_adjustment': {
        'name': 'ERP 设计订单明细调整',
        'tools': ['erp_design_query_orders', 'erp_design_get_record'],
        'optional_tools': ['erp_design_update_order_item', 'erp_design_save_scrap_decision',
                           'erp_design_release_scrap_decision'],
        'activation_queries': ['设计订单明细', '闲置料', '修改设计订单'],
    },
    'erp_design_master_data_maintenance': {
        'name': 'ERP 设计基础资料维护',
        'tools': ['erp_design_query_master_data'],
        'optional_tools': ['erp_design_get_record', 'erp_design_manage_density',
                           'erp_design_manage_group_rule', 'erp_design_manage_group_keyword'],
        # A normal lookup exposes one read-only aggregation.  Individual write
        # operations stay searchable only when the current user request itself
        # has formal action intent; the Harness ranks them by resource.
        'activation_tools': ['erp_design_query_master_data', 'erp_design_manage_density', 'erp_design_manage_group_rule',
                             'erp_design_manage_group_keyword'],
        'activation_queries': ['ERP设计基础资料', '设计基础资料', '材质密度', '分组规则', '分组关键词'],
        # These aliases identify this small read boundary without another
        # model round trip through ToolSearch.
        'auto_activation_queries': ['材质密度', '设计分组规则', '设计分组关键词'],
        'suppress_tool_search_on_auto_activation': True,
    },
    'erp_design_standard_hardware_maintenance': {
        'name': 'ERP 厂内标准件图纸维护',
        'tools': ['erp_design_query_standard_hardware'],
        'optional_tools': ['erp_design_upload_standard_hardware',
                           'erp_design_rename_standard_hardware',
                           'erp_design_delete_standard_hardware'],
        'activation_queries': ['厂内标准件图纸', '标准件目录', '标准件图纸维护'],
    },
    'erp_design_change_management': {
        'name': 'ERP 设计变更办理',
        'tools': ['erp_design_query_changes', 'erp_design_get_record',
                  'erp_design_query_change_items'],
        'optional_tools': ['erp_design_analyze_change', 'erp_design_manage_change',
                           'erp_design_manage_change_items'],
        'activation_queries': ['ERP设计变更', 'ERP设变', 'ERP设变申请', 'ERP设变明细'],
    },
    'erp_design_order_lifecycle': {
        'name': 'ERP 设计订单生命周期办理',
        'tools': ['erp_design_query_orders', 'erp_design_get_record'],
        'optional_tools': ['erp_design_manage_order', 'erp_design_manage_order_draft_scrap',
                           'erp_design_update_order_item', 'erp_design_save_scrap_decision',
                           'erp_design_release_scrap_decision'],
        'activation_queries': ['设计订单审批', '设计订单删除', '设计订单重提', '设计订单生命周期'],
    },
    'erp_design_mold_repair': {
        'name': 'ERP 设计修模改模图纸办理',
        'tools': ['erp_design_get_mold_repair_approval'],
        'optional_tools': ['erp_design_get_mold_repair_outsource_approval',
                           'erp_design_get_mold_repair_processor_response',
                           'erp_design_upload_mold_repair_drawing',
                           'erp_design_confirm_mold_repair_quantity',
                           'erp_design_submit_mold_repair_approval_batches',
                           'erp_design_confirm_mold_repair_order_link',
                           'erp_design_respond_mold_repair_processor',
                           'erp_design_download_file'],
        'activation_tools': ['erp_design_get_mold_repair_approval',
                             'erp_design_get_mold_repair_outsource_approval',
                             'erp_design_get_mold_repair_processor_response',
                             'erp_design_upload_mold_repair_drawing',
                             'erp_design_confirm_mold_repair_quantity',
                             'erp_design_submit_mold_repair_approval_batches',
                             'erp_design_confirm_mold_repair_order_link',
                             'erp_design_respond_mold_repair_processor',
                             'erp_design_download_file'],
        'activation_queries': ['设计修模', '设计改模', '修模改模图纸', '修改图纸异常',
                               '确认新图数量', '修模审批', '改模审批', '修改图纸审批',
                               '修模订单关联', '改模订单关联', '加工商响应', '同意改图',
                               '已加工反馈', '下载修模图纸'],
    },
    'erp_design_bom_maintenance': {
        'name': 'ERP BOM 维护与导入',
        'tools': ['erp_design_query_bom', 'erp_design_query_bom_report',
                  'erp_design_query_bom_shortage'],
        'optional_tools': ['erp_design_manage_bom', 'erp_design_import_bom'],
        'activation_queries': ['ERP BOM维护', 'ERP BOM导入', 'BOM缺料'],
    },
})

DEPARTMENT_NAMES = {
    'project': '项目管理', 'purchase': '采购部门', 'design': '设计部门', 'engineering': '工程部门',
    'finance': '财务部门', 'warehouse': '仓储部门', 'assembly': '装配部门', 'trial': '试模部门',
    'sales': '销售部门', 'system': '管理部门',
}

TYPE_NAMES = {'query': '查询', 'operation': '操作', 'approval': '审批', 'review': '核对'}

BUSINESS_DEPARTMENTS = {
    'project': 'project', 'project_control': 'project', 'project_close': 'project', 'pause_resume': 'project',
    'project_dossier': 'project', 'purchase': 'purchase', 'purchase_request': 'purchase', 'order': 'purchase',
    'supplier_payment': 'finance', 'purchase_price': 'purchase', 'design_route': 'design',
    'engineering_change': 'engineering', 'contact': 'engineering', 'contact_resolution': 'engineering',
    'finance_reversal': 'finance', 'finance_correction': 'finance', 'warehouse': 'warehouse',
    'assembly_issue': 'assembly', 'trial_request': 'trial', 'quotation': 'sales', 'quote_acceptance': 'sales',
    'sales_contract': 'sales', 'start_notice': 'project', 'internal_start': 'project',
    'outsource_contract': 'purchase', 'full_outsource_contract': 'purchase', 'project_plan': 'project',
    'project_plan_change': 'project',
    'plan_change': 'project', 'shipment': 'warehouse', 'receipt': 'warehouse', 'inspection': 'warehouse',
    'stock': 'warehouse', 'risk': 'purchase', 'master': 'system', 'file': 'system', 'user': 'system',
    'grant': 'system', 'workflow': 'system', 'audit': 'system', 'agent': 'system',
}

CAPABILITY_NAMES = {
    'query_projects': '查询项目资料',
    'query_purchase_requests': '查询采购申请',
    'query_business_object_candidates': '查询业务对象候选',
    'query_project_lifecycle_context': '读取项目全生命周期',
    'query_project_kickoff_context': '读取项目启动链路',
    'query_project_execution_context': '读取项目执行链路',
    'query_project_completion_context': '读取项目收尾链路',
    'query_quote_acceptance_context': '读取报价承接上下文',
    'prepare_quote_acceptance_decision': '准备报价承接/拒单',
    'query_quote_evaluation_context': '读取报价评估上下文',
    'prepare_quotation_version': '准备客户报价版本',
    'prepare_quotation_feedback': '准备客户报价反馈',
    'query_bid_intake_context': '读取中标接收上下文',
    'prepare_bid_intake_draft': '准备中标接收草稿',
    'query_contract_context': '读取合同上下文',
    'prepare_contract_record': '准备合同登记',
    'prepare_contract_signing_record': '准备合同签署记录',
    'query_internal_start_readiness': '核对正式开工条件',
    'prepare_internal_start': '准备正式开工通知',
    'query_project_plan_context': '读取项目计划上下文',
    'prepare_project_plan_baseline': '准备项目基线计划',
    'prepare_project_plan_change': '准备项目计划变更',
    'prepare_plan_department_confirmation': '准备计划部门影响确认',
    'query_design_route_context': '读取设计BOM与路线上下文',
    'prepare_design_order_approval': '准备设计订单 Agent 审批',
    'query_manufacturing_quality_context': '读取制造质检上下文',
    'query_assembly_trial_context': '读取装配试模上下文',
    'query_delivery_logistics_context': '读取交付物流上下文',
    'prepare_logistics_route': '准备物流路线确认',
    'prepare_logistics_quote': '准备物流报价/结算价确认',
    'query_full_outsource_context': '读取整套委外上下文',
    'prepare_supplier_material_handoff': '准备供应商资料交接',
    'prepare_supplier_material_verification': '准备供应商资料核验',
    'prepare_supplier_progress_policy': '准备供应商上报规则',
    'prepare_supplier_progress_report': '准备供应商节点上报',
    'query_change_intake_context': '读取设变承接上下文',
    'query_finance_context': '读取财务节点上下文',
    'prepare_customer_receivable_schedule': '准备客户收款节点账期确认',
    'prepare_customer_receipt_confirmation': '准备客户回款确认',
    'prepare_supplier_payment_confirmation': '准备供应商实付确认',
    'prepare_supplier_deduction_settlement': '准备供应商扣款结算',
    'prepare_mold_transfer_receipt': '准备移模客户签收登记',
    'query_governance_context': '读取治理权限与来源上下文',
    'query_operations_readiness_context': '读取运行交付就绪上下文',
    'query_procurement_price_context': '读取采购价格与订单上下文',
    'query_project_dossier': '查询项目业务档案',
    'query_contact_cases': '查询工程联络协作',
    'query_contact_context': '读取联络单办理资料',
    'query_purchase_orders': '查询采购订单',
    'query_project_control_context': '读取项目暂停恢复资料',
    'query_project_closure_context': '读取项目终止与结项资料',
    'query_uploaded_files': '查询当前会话附件',
    'analyze_delivery_risk': '分析发货延期风险',
    'prepare_project_pause': '准备项目整体暂停',
    'prepare_project_resume': '准备项目整体恢复',
    'prepare_project_closure_checklist': '准备正常结项清单',
    'prepare_project_termination': '准备项目终止审批',
    'prepare_project_closure_item': '准备更新结项事项',
    'prepare_project_normal_close': '准备正常关闭审批',
    'prepare_project_settlement_close': '准备终止结算关闭',
    'prepare_contact_create': '准备发起联络单',
    'prepare_contact_note': '准备补充联络记录',
    'prepare_contact_task': '准备部门协作事项',
    'prepare_contact_assign': '准备分派处理人',
    'prepare_contact_respond': '准备提交联络反馈',
    'prepare_contact_attach': '准备关联联络单附件',
    'prepare_contact_resolution': '准备处理方案审批',
    'prepare_contact_review': '准备复验处理结果',
    'prepare_contact_close': '准备人工关闭联络单',
    'prepare_contact_set_reviewer': '准备指定验收负责人',
    'prepare_contact_cancel_task': '准备撤销联络事项',
    **{key: value['name'] for key, value in SKILLS.items()},
}

CAPABILITY_DEPARTMENTS = {
    'query_projects': 'project', 'query_project_dossier': 'project', 'query_business_object_candidates': 'project',
    'query_project_lifecycle_context': 'project', 'project_lifecycle_orchestration': 'project',
    'query_project_kickoff_context': 'project', 'project_kickoff_orchestration': 'project',
    'query_project_execution_context': 'project', 'project_execution_orchestration': 'project',
    'query_project_completion_context': 'project', 'project_completion_orchestration': 'project',
    'project_dossier_review': 'project', 'business_object_matching': 'project',
    'query_quote_acceptance_context': 'sales', 'prepare_quote_acceptance_decision': 'sales',
    'query_quote_evaluation_context': 'sales', 'prepare_quotation_version': 'sales',
    'prepare_quotation_feedback': 'sales',
    'query_bid_intake_context': 'sales', 'prepare_bid_intake_draft': 'sales',
    'quote_acceptance_review': 'sales', 'quote_evaluation_review': 'sales',
    'bid_intake_review': 'sales', 'query_contract_context': 'finance', 'prepare_contract_record': 'finance',
    'prepare_contract_signing_record': 'finance',
    'contract_context_review': 'finance',
    'query_finance_context': 'finance', 'prepare_customer_receivable_schedule': 'finance', 'prepare_supplier_deduction_settlement': 'finance',
    'prepare_mold_transfer_receipt': 'finance',
    'finance_context_review': 'finance', 'query_governance_context': 'system',
    'governance_context_review': 'system', 'query_operations_readiness_context': 'system',
    'operations_readiness_review': 'system', 'query_internal_start_readiness': 'project',
    'prepare_internal_start': 'project',
    'internal_start_readiness': 'project', 'query_project_plan_context': 'project',
    'prepare_project_plan_baseline': 'project',
    'prepare_project_plan_change': 'project', 'prepare_plan_department_confirmation': 'project',
    'project_plan_context_review': 'project', 'project_plan_change': 'project',
    'query_design_route_context': 'design', 'prepare_design_order_approval': 'design',
    'design_route_context_review': 'design', 'query_manufacturing_quality_context': 'project',
    'manufacturing_quality_review': 'project', 'query_assembly_trial_context': 'assembly',
    'assembly_trial_review': 'assembly', 'query_delivery_logistics_context': 'warehouse',
    'prepare_logistics_route': 'warehouse', 'prepare_logistics_quote': 'purchase',
    'delivery_logistics_review': 'warehouse', 'query_full_outsource_context': 'purchase',
    'prepare_supplier_material_handoff': 'purchase', 'prepare_supplier_material_verification': 'purchase',
    'prepare_supplier_progress_policy': 'purchase',
    'prepare_supplier_progress_report': 'purchase',
    'full_outsource_review': 'purchase', 'query_change_intake_context': 'engineering',
    'change_intake_review': 'engineering', 'query_procurement_price_context': 'purchase',
    'procurement_price_context_review': 'purchase', 'query_purchase_requests': 'purchase',
    'purchase_request_review': 'purchase', 'query_purchase_orders': 'purchase', 'analyze_delivery_risk': 'purchase',
    'delivery_risk_analysis': 'purchase', 'business_status_review': 'purchase',
    'query_project_control_context': 'project', 'prepare_project_pause': 'project',
    'prepare_project_resume': 'project', 'project_pause_resume': 'project',
    'query_project_closure_context': 'project', 'prepare_project_closure_checklist': 'project',
    'prepare_project_termination': 'project', 'prepare_project_closure_item': 'project',
    'prepare_project_normal_close': 'project', 'prepare_project_settlement_close': 'project',
    'project_termination_closure': 'project', 'query_contact_cases': 'engineering',
    'query_contact_context': 'engineering', 'contact_collaboration_review': 'engineering',
    'prepare_contact_resolution': 'engineering', 'prepare_contact_review': 'engineering',
    'prepare_contact_close': 'engineering', 'prepare_contact_set_reviewer': 'engineering',
    'prepare_contact_cancel_task': 'engineering', 'prepare_contact_create': 'engineering',
    'prepare_contact_note': 'engineering', 'prepare_contact_task': 'engineering',
    'prepare_contact_assign': 'engineering', 'prepare_contact_respond': 'engineering',
    'prepare_contact_attach': 'engineering', 'query_uploaded_files': 'system',
}
# ERP design skills are the guided entry points for the same design capabilities
# as the registered ERP design tools.  They do not carry a permission field of
# their own, so the generic descriptor cannot infer their department from a
# permission prefix.  Keep them with the design tools in Settings instead of
# falling back to the generic "agent" department.
CAPABILITY_DEPARTMENTS.update({
    key: 'design' for key in SKILLS
    if key == 'erp_new_mold_design_upload' or key.startswith('erp_design_')
})

CAPABILITY_TYPES = {
    'purchase_request_review': 'review', 'business_object_matching': 'review', 'quote_acceptance_review': 'review',
    'prepare_quote_acceptance_decision': 'approval',
    'prepare_quotation_version': 'approval', 'prepare_quotation_feedback': 'operation',
    'prepare_bid_intake_draft': 'operation',
    'quote_evaluation_review': 'review', 'bid_intake_review': 'review', 'contract_context_review': 'review',
    'prepare_contract_record': 'approval', 'prepare_contract_signing_record': 'operation',
    'finance_context_review': 'review', 'governance_context_review': 'review',
    'operations_readiness_review': 'review', 'internal_start_readiness': 'review',
    'project_kickoff_orchestration': 'review', 'project_execution_orchestration': 'review',
    'project_completion_orchestration': 'review',
    'prepare_internal_start': 'approval',
    'project_plan_context_review': 'review', 'project_plan_change': 'approval',
    'prepare_project_plan_baseline': 'approval',
    'design_route_context_review': 'review', 'prepare_design_order_approval': 'approval',
    'manufacturing_quality_review': 'review', 'assembly_trial_review': 'review',
    'delivery_logistics_review': 'review', 'prepare_logistics_route': 'operation',
    'prepare_logistics_quote': 'approval', 'full_outsource_review': 'review',
    'prepare_supplier_material_handoff': 'operation', 'prepare_supplier_material_verification': 'operation',
    'prepare_supplier_progress_policy': 'operation',
    'prepare_supplier_progress_report': 'operation',
    'change_intake_review': 'review', 'procurement_price_context_review': 'review',
    'delivery_risk_analysis': 'review', 'contact_collaboration_review': 'review',
    'business_status_review': 'review', 'project_dossier_review': 'review',
    'project_pause_resume': 'approval', 'project_termination_closure': 'approval',
    'prepare_project_plan_change': 'approval', 'prepare_plan_department_confirmation': 'operation',
    'prepare_project_pause': 'approval', 'prepare_project_resume': 'approval',
    'prepare_project_closure_checklist': 'operation', 'prepare_project_termination': 'approval',
    'prepare_project_closure_item': 'operation', 'prepare_project_normal_close': 'approval',
    'prepare_project_settlement_close': 'approval', 'prepare_contact_resolution': 'approval',
    'prepare_contact_review': 'review', 'prepare_contact_close': 'operation',
    'prepare_contact_set_reviewer': 'operation', 'prepare_contact_cancel_task': 'operation',
    'prepare_customer_receivable_schedule': 'operation', 'prepare_supplier_deduction_settlement': 'operation',
    'prepare_mold_transfer_receipt': 'operation',
}


def capability_business_key(key, permission=''):
    raw = key.removeprefix('query_').removeprefix('prepare_')
    source = raw if raw in BUSINESS_DEPARTMENTS else (permission or '').split('.')[0]
    return source or 'system'


def capability_descriptor(kind, key, spec):
    permission = spec.get('permission', '')
    business_key = capability_business_key(key, permission)
    department = CAPABILITY_DEPARTMENTS.get(key) or BUSINESS_DEPARTMENTS.get(business_key, 'system')
    action = permission.split('.')[1] if '.' in permission else ''
    capability_type = CAPABILITY_TYPES.get(key) or (
        'query' if key.startswith('query_') or action == 'read' else
        'approval' if action == 'approve' or key.startswith('prepare_project_') else
        'review' if kind == 'SKILL' or key.endswith('_review') or 'review' in key else
        'operation'
    )
    mode = (
        'read_only' if capability_type in {'query', 'review'} and not key.startswith('prepare_') else
        'human_confirmed_proposal' if key.startswith('prepare_') else
        'assigned_skill'
    )
    return {
        'kind': kind,
        'key': key,
        'name': CAPABILITY_NAMES.get(key, spec.get('name', key)),
        'description': spec.get('description', ''),
        'permission': permission,
        'business_key': business_key,
        'department': department,
        'department_name': DEPARTMENT_NAMES.get(department, '业务部门'),
        'type': capability_type,
        'type_name': TYPE_NAMES.get(capability_type, '操作'),
        'mode': mode,
        'dependencies': spec.get('tools', []),
        'optional_dependencies': spec.get('optional_tools', []),
        'activation_dependencies': spec.get('activation_tools', spec.get('tools', []) + spec.get('optional_tools', [])),
    }


def assigned(db, user, kind, key):
    if user.super_admin: return True
    return bool(db.scalar(select(Capability.id).where(Capability.user_id == user.id, Capability.kind == kind, Capability.key == key, Capability.enabled.is_(True))))


def available_tools(db, user):
    allowed = [key for key, tool in TOOLS.items() if assigned(db, user, "TOOL", key) and
               (user.super_admin or any(g.effect == "ALLOW" for g in grants_for(db, user, tool["permission"])))]
    # Existing accounts can already have the three read-only master-data
    # catalogues assigned.  Treat that exact prior grant as authorization for
    # the new aggregate reader, so this display/selection simplification does
    # not require a disruptive permission migration.
    legacy_master_reads = {
        "erp_design_query_densities",
        "erp_design_query_group_rules",
        "erp_design_query_group_keywords",
    }
    if ("erp_design_query_master_data" not in allowed
            and legacy_master_reads <= set(allowed)):
        allowed.append("erp_design_query_master_data")
    return allowed


def tool_schema(key):
    if key in erp_design_mcp.TOOL_SPECS:
        return erp_design_mcp.tool_schema(key)
    if key.startswith('prepare_contact_') or key=='query_contact_context':
        schema=contact_tools.ContextInput.model_json_schema() if key=='query_contact_context' else contact_tools.schema(key.removeprefix('prepare_contact_'))
        return {'type':'function','function':{'name':key,'description':TOOLS[key]['description'],'parameters':schema}}
    from domain_packs.mold.tools.erp.project.project_closure_tools import ACTION_BY_TOOL,ClosureContextInput,schema as closure_schema
    if key in ACTION_BY_TOOL or key=='query_project_closure_context':
        parameters=ClosureContextInput.model_json_schema() if key=='query_project_closure_context' else closure_schema(ACTION_BY_TOOL[key])
        return {'type':'function','function':{'name':key,'description':TOOLS[key]['description'],'parameters':parameters}}
    if key in {'prepare_project_pause','prepare_project_resume','query_project_control_context'}:
        from domain_packs.mold.tools.erp.project.project_control_tools import ProjectContextInput,schema
        parameters=ProjectContextInput.model_json_schema() if key=='query_project_control_context' else schema(key.removeprefix('prepare_project_'))
        return {'type':'function','function':{'name':key,'description':TOOLS[key]['description'],'parameters':parameters}}
    if key=='query_project_dossier':
        from domain_packs.mold.erp.project.project_dossier import ProjectDossierInput
        return {'type':'function','function':{'name':key,'description':TOOLS[key]['description'],'parameters':ProjectDossierInput.model_json_schema()}}
    if key=='query_business_object_candidates':
        from domain_packs.mold.erp.core.business_matching import BusinessMatchInput
        return {'type':'function','function':{'name':key,'description':TOOLS[key]['description'],'parameters':BusinessMatchInput.model_json_schema()}}
    if key=='query_project_lifecycle_context':
        from domain_packs.mold.tools.erp.project.lifecycle_overview_tools import ProjectLifecycleContextInput
        return {'type':'function','function':{'name':key,'description':TOOLS[key]['description'],'parameters':ProjectLifecycleContextInput.model_json_schema()}}
    if key=='query_project_kickoff_context':
        from domain_packs.mold.tools.erp.project.kickoff_lifecycle_tools import ProjectKickoffContextInput
        return {'type':'function','function':{'name':key,'description':TOOLS[key]['description'],'parameters':ProjectKickoffContextInput.model_json_schema()}}
    if key=='query_project_execution_context':
        from domain_packs.mold.tools.erp.project.execution_lifecycle_tools import ProjectExecutionContextInput
        return {'type':'function','function':{'name':key,'description':TOOLS[key]['description'],'parameters':ProjectExecutionContextInput.model_json_schema()}}
    if key=='query_project_completion_context':
        from domain_packs.mold.tools.erp.project.completion_lifecycle_tools import ProjectCompletionContextInput
        return {'type':'function','function':{'name':key,'description':TOOLS[key]['description'],'parameters':ProjectCompletionContextInput.model_json_schema()}}
    if key in {'query_quote_acceptance_context','prepare_quote_acceptance_decision'}:
        from domain_packs.mold.tools.erp.commercial.quote_tools import QuoteContextInput, quote_decision_schema
        parameters=quote_decision_schema() if key=='prepare_quote_acceptance_decision' else QuoteContextInput.model_json_schema()
        return {'type':'function','function':{'name':key,'description':TOOLS[key]['description'],'parameters':parameters}}
    if key=='query_quote_evaluation_context':
        from domain_packs.mold.tools.erp.commercial.quote_tools import QuoteContextInput
        return {'type':'function','function':{'name':key,'description':TOOLS[key]['description'],'parameters':QuoteContextInput.model_json_schema()}}
    if key in {'prepare_quotation_version','prepare_quotation_feedback'}:
        from domain_packs.mold.tools.erp.commercial.quotation_tools import quotation_schema, quotation_feedback_schema
        parameters=quotation_schema() if key=='prepare_quotation_version' else quotation_feedback_schema()
        return {'type':'function','function':{'name':key,'description':TOOLS[key]['description'],'parameters':parameters}}
    if key in {'query_bid_intake_context','prepare_bid_intake_draft'}:
        from domain_packs.mold.tools.erp.commercial.quote_tools import QuoteContextInput
        from domain_packs.mold.tools.erp.commercial.bid_intake_tools import bid_intake_schema
        parameters=bid_intake_schema() if key=='prepare_bid_intake_draft' else QuoteContextInput.model_json_schema()
        return {'type':'function','function':{'name':key,'description':TOOLS[key]['description'],'parameters':parameters}}
    if key in {'query_contract_context','prepare_contract_record','prepare_contract_signing_record'}:
        from domain_packs.mold.tools.erp.commercial.contract_tools import ContractContextInput, contract_schema, contract_signing_record_schema
        parameters=contract_schema() if key=='prepare_contract_record' else (
            contract_signing_record_schema() if key=='prepare_contract_signing_record' else ContractContextInput.model_json_schema())
        return {'type':'function','function':{'name':key,'description':TOOLS[key]['description'],'parameters':parameters}}
    if key in {'query_internal_start_readiness','prepare_internal_start'}:
        from domain_packs.mold.tools.erp.project.start_tools import StartReadinessInput, start_schema
        parameters=start_schema() if key=='prepare_internal_start' else StartReadinessInput.model_json_schema()
        return {'type':'function','function':{'name':key,'description':TOOLS[key]['description'],'parameters':parameters}}
    if key=='query_project_plan_context':
        return {'type':'function','function':{'name':key,'description':TOOLS[key]['description'],'parameters':ProjectPlanContextInput.model_json_schema()}}
    if key in {'prepare_project_plan_baseline','prepare_project_plan_change','prepare_plan_department_confirmation'}:
        from domain_packs.mold.tools.erp.project.plan_tools import department_confirmation_schema, plan_baseline_schema, plan_change_schema
        parameters=department_confirmation_schema() if key=='prepare_plan_department_confirmation' else (
            plan_baseline_schema() if key=='prepare_project_plan_baseline' else plan_change_schema())
        return {'type':'function','function':{'name':key,'description':TOOLS[key]['description'],'parameters':parameters}}
    if key=='query_design_route_context':
        from domain_packs.mold.tools.erp.design.design_tools import DesignRouteContextInput
        return {'type':'function','function':{'name':key,'description':TOOLS[key]['description'],'parameters':DesignRouteContextInput.model_json_schema()}}
    if key=='prepare_design_order_approval':
        from domain_packs.mold.tools.erp.design.design_approval_tools import schema
        return {'type':'function','function':{'name':key,'description':TOOLS[key]['description'],'parameters':schema()}}
    if key=='query_manufacturing_quality_context':
        return {'type':'function','function':{'name':key,'description':TOOLS[key]['description'],'parameters':ProjectPlanContextInput.model_json_schema()}}
    if key=='query_assembly_trial_context':
        return {'type':'function','function':{'name':key,'description':TOOLS[key]['description'],'parameters':ProjectPlanContextInput.model_json_schema()}}
    if key in {'query_delivery_logistics_context','prepare_logistics_route','prepare_logistics_quote'}:
        if key == 'prepare_logistics_route':
            from domain_packs.mold.erp.procurement.delivery_logistics import logistics_route_schema
            parameters = logistics_route_schema()
        elif key == 'prepare_logistics_quote':
            from domain_packs.mold.erp.procurement.delivery_logistics import logistics_quote_schema
            parameters = logistics_quote_schema()
        else:
            parameters = ProjectPlanContextInput.model_json_schema()
        return {'type':'function','function':{'name':key,'description':TOOLS[key]['description'],'parameters':parameters}}
    if key in {'query_full_outsource_context','prepare_supplier_material_handoff','prepare_supplier_material_verification','prepare_supplier_progress_policy','prepare_supplier_progress_report'}:
        if key=='prepare_supplier_material_handoff':
            from domain_packs.mold.tools.erp.procurement.full_outsource_tools import supplier_material_handoff_schema
            parameters=supplier_material_handoff_schema()
        elif key=='prepare_supplier_material_verification':
            from domain_packs.mold.tools.erp.procurement.full_outsource_tools import supplier_material_verification_schema
            parameters=supplier_material_verification_schema()
        elif key=='prepare_supplier_progress_report':
            from domain_packs.mold.tools.erp.procurement.full_outsource_tools import supplier_progress_report_schema
            parameters=supplier_progress_report_schema()
        elif key=='prepare_supplier_progress_policy':
            from domain_packs.mold.tools.erp.procurement.full_outsource_tools import supplier_progress_policy_schema
            parameters=supplier_progress_policy_schema()
        else:
            parameters=ProjectPlanContextInput.model_json_schema()
        return {'type':'function','function':{'name':key,'description':TOOLS[key]['description'],'parameters':parameters}}
    if key=='query_change_intake_context':
        return {'type':'function','function':{'name':key,'description':TOOLS[key]['description'],'parameters':ProjectPlanContextInput.model_json_schema()}}
    if key=='query_finance_context':
        from domain_packs.mold.erp.project.project_dossier import ProjectDossierInput
        return {'type':'function','function':{'name':key,'description':TOOLS[key]['description'],'parameters':ProjectDossierInput.model_json_schema()}}
    if key in {'prepare_customer_receivable_schedule','prepare_customer_receipt_confirmation','prepare_supplier_payment_confirmation','prepare_supplier_deduction_settlement','prepare_mold_transfer_receipt'}:
        from domain_packs.mold.tools.erp.finance.finance_context_tools import customer_receivable_schedule_schema, customer_receipt_schema, mold_transfer_receipt_schema, supplier_deduction_settlement_schema, supplier_payment_confirmation_schema
        parameters=customer_receivable_schedule_schema() if key=='prepare_customer_receivable_schedule' else (
            customer_receipt_schema() if key=='prepare_customer_receipt_confirmation' else (
            supplier_payment_confirmation_schema() if key=='prepare_supplier_payment_confirmation' else (
            supplier_deduction_settlement_schema() if key=='prepare_supplier_deduction_settlement' else mold_transfer_receipt_schema())))
        return {'type':'function','function':{'name':key,'description':TOOLS[key]['description'],'parameters':parameters}}
    if key=='query_governance_context':
        from domain_packs.mold.tools.erp.governance.governance_context_tools import GovernanceContextInput
        return {'type':'function','function':{'name':key,'description':TOOLS[key]['description'],'parameters':GovernanceContextInput.model_json_schema()}}
    if key=='query_operations_readiness_context':
        from domain_packs.mold.tools.agent.operations.operations_readiness_tools import OperationsReadinessInput
        return {'type':'function','function':{'name':key,'description':TOOLS[key]['description'],'parameters':OperationsReadinessInput.model_json_schema()}}
    if key=='query_procurement_price_context':
        from domain_packs.mold.tools.erp.procurement.procurement_tools import ProcurementPriceContextInput
        return {'type':'function','function':{'name':key,'description':TOOLS[key]['description'],'parameters':ProcurementPriceContextInput.model_json_schema()}}
    if key=='analyze_delivery_risk':
        from domain_packs.mold.erp.procurement.procurement import DeliveryRiskInput
        return {'type':'function','function':{'name':key,'description':TOOLS[key]['description'],'parameters':DeliveryRiskInput.model_json_schema()}}
    return {"type": "function", "function": {"name": key, "description": TOOLS[key]["description"],
             "parameters": {"type": "object", "properties": {}, "additionalProperties": False}, "strict": True}}


def skill_context(db, user):
    allowed = set(available_tools(db, user))
    result = []
    for key, spec in SKILLS.items():
        if assigned(db, user, "SKILL", key) and set(spec["tools"]) <= allowed:
            route = skill_paths()[key]
            path = route["path"]
            content = path.read_text(encoding="utf-8")
            result.append({"key": key, "version": "1.0.0", "hash": content_hash(content), "instructions": content,
                           "agent_description": skill_agent_description(content),
                           "tools": spec["tools"], "optional_tools": spec.get("optional_tools", []),
                           "activation_tools": spec.get("activation_tools"),
                           "activation_queries": spec.get("activation_queries", []),
                           "auto_activation_queries": spec.get("auto_activation_queries", []),
                           "suppress_tool_search_on_auto_activation": bool(
                               spec.get("suppress_tool_search_on_auto_activation", False)
                           ),
                           "priority_patterns": spec.get("priority_patterns", []),
                           "skill_layer": route["layer"], "skill_domain": route["domain"],
                           "route_terms": route["route_terms"]})
    return result


@lru_cache
def skill_paths():
    """Index categorized Agent/ERP skills by their stable capability key."""
    root = Path(__file__).resolve().parent / "skills"
    route_terms = {
        ("agent", "project"): ["候选匹配", "项目定位", "对象匹配"],
        ("agent", "procurement"): ["业务状态", "审批状态", "执行状态"],
        ("agent", "governance"): ["权限", "审计", "来源治理", "授权"],
        ("agent", "operations"): ["部署", "容量", "备份", "恢复", "运行交付", "日志保留"],
        ("erp", "project"): ["项目计划", "开工", "启动链路", "执行链路", "收尾链路", "项目收尾", "最终关闭", "项目推进", "执行进度", "暂停", "恢复", "结项", "终止", "项目档案"],
        ("erp", "design"): ["设计", "图纸", "BOM", "工艺", "修模", "改模"],
        ("erp", "procurement"): ["采购", "供应商", "委外", "采购价格", "采购订单"],
        ("erp", "manufacturing"): ["制造", "加工", "质检", "装配", "试模"],
        ("erp", "commercial"): ["中标", "报价", "合同", "承接", "拒单"],
        ("erp", "change"): ["设变", "工程变更", "工程联络", "联络单", "协作事项"],
        ("erp", "delivery"): ["交付", "物流", "发货", "签收", "客户验收"],
        ("erp", "finance"): ["财务", "回款", "付款", "发票", "结算", "扣款"],
    }
    paths = {}
    for path in root.rglob("SKILL.md"):
        key = path.parent.name
        if key in paths:
            raise RuntimeError(f"Duplicate skill key: {key}")
        relative = path.relative_to(root)
        if len(relative.parts) < 4:
            raise RuntimeError(f"Skill must be categorized as layer/domain/key: {relative}")
        layer, domain = relative.parts[0], relative.parts[1]
        paths[key] = {"path": path, "layer": layer, "domain": domain,
                      "route_terms": route_terms.get((layer, domain), [])}
    missing = set(SKILLS) - set(paths)
    if missing:
        raise RuntimeError(f"Missing skill documents: {', '.join(sorted(missing))}")
    return paths


_ERP_MOLD_NUMBER = re.compile(r"(?i)(?<![A-Z0-9])M\d{5,}-P\d+(?![A-Z0-9])")


def _local_design_context_is_empty(result):
    if not isinstance(result, dict):
        return True
    if result.get("resolution") in {"NOT_FOUND", "NOT_FOUND_OR_FORBIDDEN"}:
        return True
    rows = result.get("data")
    if not isinstance(rows, list) or not rows:
        return True
    if result.get("resolution") != "RESOLVED":
        return False
    return all(not isinstance(row, dict) or not row.get("design_routes") for row in rows)


def _erp_result_rows(result):
    payload = result.get("data") if isinstance(result, dict) else None
    if not isinstance(payload, dict):
        return []
    for key in ("rows", "records", "list"):
        rows = payload.get(key)
        if isinstance(rows, list):
            return rows
    return []


def _fallback_empty_design_context_to_erp(db, user, data, allowed, local_result, run=None):
    """Use ERP design orders when an ERP-shaped mold number has no local facts."""
    identifier = str(data.identifier or "").strip()
    match = _ERP_MOLD_NUMBER.search(identifier)
    if not match or not _local_design_context_is_empty(local_result):
        return local_result
    mold_number = match.group(0).upper()
    limitations = list(local_result.get("limitations") or [])
    if "erp_design_query_orders" not in allowed:
        limitations.append("当前会话未分配 ERP 设计订单查询工具，不能自动回退到 ERP。")
        return {**local_result, "limitations": limitations}

    order_result = erp_design_mcp.execute_tool(
        db, user, "erp_design_query_orders", {"query": {"moldNo": mold_number}}, run=run)
    rows = _erp_result_rows(order_result)
    fallback = {
        "attempted": True,
        "from": "agent_db",
        "reason": "LOCAL_DESIGN_CONTEXT_EMPTY",
        "identifier": identifier,
        "mold_number": mold_number,
        "matched": bool(rows),
    }
    if not rows:
        limitations.append("本地设计上下文为空，已按模具号自动查询 ERP，但 ERP 也未返回设计订单。")
        return {**local_result, "limitations": limitations, "erp_fallback": fallback}

    exact = next((row for row in rows if isinstance(row, dict)
                  and str(row.get("moldNo") or "").casefold() == mold_number.casefold()), None)
    detail = None
    detail_error = False
    record_id = (exact or {}).get("id") or (exact or {}).get("requestId")
    if record_id and "erp_design_get_record" in allowed:
        try:
            record_result = erp_design_mcp.execute_tool(
                db, user, "erp_design_get_record",
                {"resource": "design_order", "id": int(record_id)}, run=run)
            detail = record_result.get("data") if isinstance(record_result, dict) else None
        except (DomainError, TypeError, ValueError):
            detail_error = True

    limitations.extend(order_result.get("limitations") or [])
    limitations.append("本地设计上下文为空，结果已自动回退到 management-system ERP。")
    if detail_error:
        limitations.append("已找到 ERP 设计订单，但订单详情读取失败；当前仅返回订单列表事实。")
    return {
        "resolution": "RESOLVED",
        "data": [{"mold_number": mold_number, "erp_design_orders": rows,
                  "erp_design_order_detail": detail}],
        "source": order_result.get("source") or "management-system ERP via erp-design-upload MCP",
        "as_of": order_result.get("as_of") or now().isoformat(),
        "limitations": list(dict.fromkeys(limitations)),
        "erp_fallback": fallback,
    }


def execute(db, user, key, arguments, run=None):
    if key not in available_tools(db, user): raise DomainError("TOOL_FORBIDDEN", "工具不在当前有效能力范围内", 403)
    if key in erp_design_mcp.TOOL_SPECS:
        return erp_design_mcp.execute_tool(db, user, key, arguments, run=run)
    if key.startswith('prepare_contact_') or key=='query_contact_context':
        return contact_tools.execute_tool(db,user,key,arguments,run=run)
    from domain_packs.mold.tools.erp.project.project_closure_tools import ACTION_BY_TOOL
    if key in ACTION_BY_TOOL or key=='query_project_closure_context':
        from domain_packs.mold.tools.erp.project.project_closure_tools import execute_tool
        return execute_tool(db,user,key,arguments,run=run)
    if key in {'prepare_project_pause','prepare_project_resume','query_project_control_context'}:
        from domain_packs.mold.tools.erp.project.project_control_tools import execute_tool
        return execute_tool(db,user,key,arguments,run=run)
    if key in {'prepare_project_plan_baseline','prepare_project_plan_change','prepare_plan_department_confirmation'}:
        from domain_packs.mold.tools.erp.project.plan_tools import execute_plan_tool
        return execute_plan_tool(db,user,key,arguments,run=run)
    if key=='prepare_internal_start':
        from domain_packs.mold.tools.erp.project.start_tools import execute_start_tool
        return execute_start_tool(db,user,key,arguments,run=run)
    if key=='prepare_quote_acceptance_decision':
        from domain_packs.mold.tools.erp.commercial.quote_tools import execute_quote_tool
        return execute_quote_tool(db,user,key,arguments,run=run)
    if key in {'prepare_quotation_version','prepare_quotation_feedback'}:
        from domain_packs.mold.tools.erp.commercial.quotation_tools import execute_quotation_tool
        return execute_quotation_tool(db,user,key,arguments,run=run)
    if key=='prepare_bid_intake_draft':
        from domain_packs.mold.tools.erp.commercial.bid_intake_tools import execute_bid_intake_tool
        return execute_bid_intake_tool(db,user,key,arguments,run=run)
    if key in {'prepare_contract_record','prepare_contract_signing_record'}:
        from domain_packs.mold.tools.erp.commercial.contract_tools import execute_contract_tool
        return execute_contract_tool(db,user,key,arguments,run=run)
    if key=='prepare_design_order_approval':
        from domain_packs.mold.tools.erp.design.design_approval_tools import execute_tool
        return execute_tool(db,user,key,arguments,run=run)
    if key in {'prepare_customer_receivable_schedule','prepare_customer_receipt_confirmation','prepare_supplier_payment_confirmation','prepare_supplier_deduction_settlement','prepare_mold_transfer_receipt'}:
        from domain_packs.mold.tools.erp.finance.finance_context_tools import execute_finance_tool
        return execute_finance_tool(db,user,key,arguments,run=run)
    if key in {'prepare_supplier_material_handoff','prepare_supplier_material_verification','prepare_supplier_progress_policy','prepare_supplier_progress_report'}:
        from domain_packs.mold.tools.erp.procurement.full_outsource_tools import execute_full_outsource_tool
        return execute_full_outsource_tool(db,user,key,arguments,run=run)
    if key in {'prepare_logistics_route','prepare_logistics_quote'}:
        from domain_packs.mold.erp.procurement.delivery_logistics import execute_delivery_logistics_tool
        return execute_delivery_logistics_tool(db,user,key,arguments,run=run)
    if key=='query_project_dossier':
        from pydantic import ValidationError
        from domain_packs.mold.erp.project.project_dossier import ProjectDossierInput,query
        try:data=ProjectDossierInput.model_validate(arguments or {})
        except ValidationError as error:raise DomainError('INVALID_TOOL_INPUT','项目档案查询参数无效：'+error.errors()[0]['msg']) from None
        return query(db,user,data,set(available_tools(db,user)))
    if key=='query_project_lifecycle_context':
        from domain_packs.mold.tools.erp.project.lifecycle_overview_tools import parse, query
        data=parse(arguments)
        return query(db,user,data,set(available_tools(db,user)))
    if key=='query_project_kickoff_context':
        from domain_packs.mold.tools.erp.project.kickoff_lifecycle_tools import parse, query
        data=parse(arguments)
        return query(db,user,data,set(available_tools(db,user)))
    if key=='query_project_execution_context':
        from domain_packs.mold.tools.erp.project.execution_lifecycle_tools import parse, query
        data=parse(arguments)
        return query(db,user,data,set(available_tools(db,user)))
    if key=='query_project_completion_context':
        from domain_packs.mold.tools.erp.project.completion_lifecycle_tools import parse, query
        data=parse(arguments)
        return query(db,user,data,set(available_tools(db,user)))
    if key=='query_business_object_candidates':
        from pydantic import ValidationError
        from domain_packs.mold.erp.core.business_matching import BusinessMatchInput,query
        try:data=BusinessMatchInput.model_validate(arguments or {})
        except ValidationError as error:raise DomainError('INVALID_TOOL_INPUT','业务对象候选匹配参数无效：'+error.errors()[0]['msg']) from None
        return query(db,user,data,set(available_tools(db,user)))
    if key=='query_quote_acceptance_context':
        from pydantic import ValidationError
        from domain_packs.mold.tools.erp.commercial.quote_tools import QuoteContextInput,query
        try:data=QuoteContextInput.model_validate(arguments or {})
        except ValidationError as error:raise DomainError('INVALID_TOOL_INPUT','报价与承接上下文参数无效：'+error.errors()[0]['msg']) from None
        return query(db,user,data,set(available_tools(db,user)))
    if key=='query_quote_evaluation_context':
        from pydantic import ValidationError
        from domain_packs.mold.tools.erp.commercial.quote_tools import QuoteContextInput
        from domain_packs.mold.tools.erp.commercial.quote_evaluation_tools import query
        try:data=QuoteContextInput.model_validate(arguments or {})
        except ValidationError as error:raise DomainError('INVALID_TOOL_INPUT','报价评估上下文参数无效：'+error.errors()[0]['msg']) from None
        return query(db,user,data,set(available_tools(db,user)))
    if key=='query_bid_intake_context':
        from pydantic import ValidationError
        from domain_packs.mold.tools.erp.commercial.quote_tools import QuoteContextInput
        from domain_packs.mold.tools.erp.commercial.bid_intake_tools import query
        try:data=QuoteContextInput.model_validate(arguments or {})
        except ValidationError as error:raise DomainError('INVALID_TOOL_INPUT','中标接收上下文参数无效：'+error.errors()[0]['msg']) from None
        return query(db,user,data,set(available_tools(db,user)))
    if key=='query_contract_context':
        from pydantic import ValidationError
        from domain_packs.mold.tools.erp.commercial.contract_tools import ContractContextInput,query
        try:data=ContractContextInput.model_validate(arguments or {})
        except ValidationError as error:raise DomainError('INVALID_TOOL_INPUT','合同上下文参数无效：'+error.errors()[0]['msg']) from None
        return query(db,user,data,set(available_tools(db,user)))
    if key=='query_internal_start_readiness':
        from pydantic import ValidationError
        from domain_packs.mold.tools.erp.project.start_tools import StartReadinessInput,query
        try:data=StartReadinessInput.model_validate(arguments or {})
        except ValidationError as error:raise DomainError('INVALID_TOOL_INPUT','正式开工条件参数无效：'+error.errors()[0]['msg']) from None
        return query(db,user,data,set(available_tools(db,user)))
    if key=='query_project_plan_context':
        from pydantic import ValidationError
        from domain_packs.mold.tools.erp.project.plan_tools import query
        try:data=ProjectPlanContextInput.model_validate(arguments or {})
        except ValidationError as error:raise DomainError('INVALID_TOOL_INPUT','项目计划上下文参数无效：'+error.errors()[0]['msg']) from None
        return query(db,user,data,set(available_tools(db,user)))
    if key=='query_design_route_context':
        from pydantic import ValidationError
        from domain_packs.mold.tools.erp.design.design_tools import DesignRouteContextInput,query
        try:data=DesignRouteContextInput.model_validate(arguments or {})
        except ValidationError as error:raise DomainError('INVALID_TOOL_INPUT','设计BOM与路线上下文参数无效：'+error.errors()[0]['msg']) from None
        allowed=set(available_tools(db,user))
        local_result=query(db,user,data,allowed)
        return _fallback_empty_design_context_to_erp(db,user,data,allowed,local_result,run=run)
    if key=='query_manufacturing_quality_context':
        from pydantic import ValidationError
        from domain_packs.mold.tools.erp.manufacturing.manufacturing_quality_tools import query
        try:data=ProjectPlanContextInput.model_validate(arguments or {})
        except ValidationError as error:raise DomainError('INVALID_TOOL_INPUT','制造与质检上下文参数无效：'+error.errors()[0]['msg']) from None
        return query(db,user,data,set(available_tools(db,user)))
    if key=='query_assembly_trial_context':
        from pydantic import ValidationError
        from domain_packs.mold.tools.erp.manufacturing.assembly_trial_tools import query
        try:data=ProjectPlanContextInput.model_validate(arguments or {})
        except ValidationError as error:raise DomainError('INVALID_TOOL_INPUT','装配试模上下文参数无效：'+error.errors()[0]['msg']) from None
        return query(db,user,data,set(available_tools(db,user)))
    if key=='query_delivery_logistics_context':
        from pydantic import ValidationError
        from domain_packs.mold.erp.procurement.delivery_logistics import query
        try:data=ProjectPlanContextInput.model_validate(arguments or {})
        except ValidationError as error:raise DomainError('INVALID_TOOL_INPUT','交付物流上下文参数无效：'+error.errors()[0]['msg']) from None
        return query(db,user,data,set(available_tools(db,user)))
    if key=='query_full_outsource_context':
        from pydantic import ValidationError
        from domain_packs.mold.tools.erp.procurement.full_outsource_tools import query
        try:data=ProjectPlanContextInput.model_validate(arguments or {})
        except ValidationError as error:raise DomainError('INVALID_TOOL_INPUT','整套委外上下文参数无效：'+error.errors()[0]['msg']) from None
        return query(db,user,data,set(available_tools(db,user)))
    if key=='query_change_intake_context':
        from pydantic import ValidationError
        from domain_packs.mold.tools.erp.change.change_intake_tools import query
        try:data=ProjectPlanContextInput.model_validate(arguments or {})
        except ValidationError as error:raise DomainError('INVALID_TOOL_INPUT','设变承接上下文参数无效：'+error.errors()[0]['msg']) from None
        return query(db,user,data,set(available_tools(db,user)))
    if key=='query_finance_context':
        from pydantic import ValidationError
        from domain_packs.mold.erp.project.project_dossier import ProjectDossierInput
        from domain_packs.mold.tools.erp.finance.finance_context_tools import query
        try:data=ProjectDossierInput.model_validate(arguments or {})
        except ValidationError as error:raise DomainError('INVALID_TOOL_INPUT','财务节点上下文参数无效：'+error.errors()[0]['msg']) from None
        return query(db,user,data,set(available_tools(db,user)))
    if key=='query_governance_context':
        from pydantic import ValidationError
        from domain_packs.mold.tools.erp.governance.governance_context_tools import GovernanceContextInput,query
        try:data=GovernanceContextInput.model_validate(arguments or {})
        except ValidationError as error:raise DomainError('INVALID_TOOL_INPUT','治理上下文参数无效：'+error.errors()[0]['msg']) from None
        return query(db,user,data,set(available_tools(db,user)))
    if key=='query_operations_readiness_context':
        from pydantic import ValidationError
        from domain_packs.mold.tools.agent.operations.operations_readiness_tools import OperationsReadinessInput,query
        try:data=OperationsReadinessInput.model_validate(arguments or {})
        except ValidationError as error:raise DomainError('INVALID_TOOL_INPUT','运行交付核对参数无效：'+error.errors()[0]['msg']) from None
        return query(db,user,data)
    if key=='query_procurement_price_context':
        from pydantic import ValidationError
        from domain_packs.mold.tools.erp.procurement.procurement_tools import ProcurementPriceContextInput,query
        try:data=ProcurementPriceContextInput.model_validate(arguments or {})
        except ValidationError as error:raise DomainError('INVALID_TOOL_INPUT','采购价格与订单上下文参数无效：'+error.errors()[0]['msg']) from None
        return query(db,user,data,set(available_tools(db,user)))
    if key=='analyze_delivery_risk':
        from pydantic import ValidationError
        from domain_packs.mold.erp.procurement.procurement import DeliveryRiskInput,analyze_delivery_risk
        try:data=DeliveryRiskInput.model_validate(arguments or {})
        except ValidationError as error:raise DomainError('INVALID_TOOL_INPUT','发货风险分析参数无效：'+error.errors()[0]['msg']) from None
        return analyze_delivery_risk(db,user,data)
    if arguments: raise DomainError("INVALID_TOOL_INPUT", "该工具不接受额外参数")
    if key=='query_uploaded_files':
        from fastapi.encoders import jsonable_encoder
        if not run or run.user_id!=user.id:raise DomainError("FILE_CONTEXT_INVALID","附件查询须绑定当前任务",403)
        return jsonable_encoder({"data":_host.conversation_files(run.conversation_id,user,db),"source":"agent_db","as_of":now(),"limitations":["仅当前会话可见附件元数据；文件内容尚未解析，不能据此声称已识别文本或完成审批"]})
    if key == "query_projects":
        p = predicate(db, user, "project.read", {"project_id": Project.id})
        data = [select_fields({"id": row.id, "code": row.code, "name": row.name, "status": row.status},
                              require(db, user, "project.read", {"project_id": row.id}))
                for row in db.scalars(select(Project).where(p).order_by(Project.code).limit(100))]
    elif key=='query_purchase_requests':
        data = [request_data(db, user, r) for r in db.scalars(visible_requests(db, user).order_by(PurchaseRequest.created_at.desc()).limit(100))]
    elif key=='query_contact_cases':
        from sqlalchemy import func
        ContactCase,ContactTask,AssignmentGroup,User = m.ContactCase,m.ContactTask,m.AssignmentGroup,m.User
        from domain_packs.mold.erp.change.contacts import permitted, progress_summary
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
                'task_counts':counts,'progress_summary':progress_summary(db,c),'recent_tasks':tasks,'tasks_truncated':sum(counts.values())>20})
        return {'data':data,'source':'agent_db','as_of':now().isoformat(),'limit':100,
            'limitations':['仅当前用户可见范围','最多最新100张联络单，每单最多展示最近20项协作事项，计数包含全部事项',
                '反馈或历史补录不是正式审批，不据此认定整改验收或联络单关闭','本工具只查询，不分派、不审批、不执行业务动作']}
    elif key=='query_purchase_orders':
        from domain_packs.mold.erp.procurement.procurement import visible_orders
        data=visible_orders(db,user)
    elif 'business_kind' in TOOLS[key]:
        from domain_packs.mold.erp.core.domains import visible
        data=visible(db,user,TOOLS[key]['business_kind'])
    else:raise DomainError('TOOL_UNKNOWN','工具未实现',403)
    return {"data": data, "source": "agent_db", "as_of": now().isoformat(), "limit": 100,
            "limitations": ["仅当前用户可见范围", "采购申请不代表正式下单或供应商发货事实"] if key == "query_purchase_requests" else ["仅当前用户可见范围"]}
