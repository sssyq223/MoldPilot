import {labels,optionNames,stateLabels,commandNames} from './businessForms'
export const categoryNames:Record<string,string>={raw_material:'原材',hardware:'五金',outsource:'委外',auxiliary:'辅材',office_supply:'办公用品',trial_material:'试模料'}
export const currencyNames:Record<string,string>={CNY:'人民币',USD:'美元',EUR:'欧元',HKD:'港币',JPY:'日元'}
export const businessNames:Record<string,string>={file:'文件',project:'项目',purchase:'采购申请',purchase_request:'采购申请',workflow:'审批流程',user:'用户',grant:'权限分配',audit:'操作审计',design_route:'设计与物料清单',purchase_price:'采购价格',assembly_issue:'装配任务',trial_request:'试模申请',finance_reversal:'财务冲正',quotation:'报价与承接',start_notice:'开工通知',sales_contract:'销售合同',outsource_contract:'整套委外合同',project_plan:'项目计划',plan_change:'计划变更',project_control:'暂停与恢复',supplier_payment:'供应商付款',engineering_change:'工程联络单',project_close:'项目关闭',order:'采购订单',warehouse:'仓储',risk:'风险预警',master:'基础资料',finance:'财务',assembly:'装配',trial:'试模',plan:'计划',change:'工程变更',shipment:'发货',receipt:'收货',inspection:'检验',stock:'库存',exception:'异常',business:'业务',agent:'智能体'}
const verbs:Record<string,string>={read:'查询',create:'新建',submit:'发起审批',approve:'审批',design:'配置',publish:'发布',manage:'管理',execute:'执行',configure:'配置',confirm:'确认',condition:'核验条件',implement:'登记实施',recheck:'复检',close:'关闭',issue:'正式下单',report:'登记'}
Object.assign(businessNames,{finance_correction:'财务冲正',quote_acceptance:'报价与承接',internal_start:'开工通知',full_outsource_contract:'整套委外合同',pause_resume:'暂停与恢复',identity:'关联身份'})
Object.assign(verbs,{edit:'编辑',reference:'查看身份关联'})
Object.assign(businessNames,{contact:'工程联络协作'})
Object.assign(verbs,{coordinate:'组织部门协作',assign:'分派处理人',respond:'提交处理反馈',record:'追加过程记录'})
export function permissionName(value:string){if(commandNames[value])return commandNames[value];if(value==='project.dossier.read')return '项目业务档案 · 查询';const [kind,action]=value.split('.');return `${businessNames[kind]||'业务'} · ${verbs[action]||'操作权限'}`}
export const capabilityNames:Record<string,string>={query_projects:'查询项目资料',query_purchase_requests:'查询采购申请',purchase_review:'采购资料核对',project_overview:'项目概况查询',query_orders:'查询采购订单',query_business_subjects:'查询业务材料',query_business_object_candidates:'查询业务对象候选',query_quote_acceptance_context:'读取报价承接上下文',query_quote_evaluation_context:'读取报价评估上下文',query_bid_intake_context:'读取中标接收上下文',query_contract_context:'读取合同上下文',query_internal_start_readiness:'核对正式开工条件',query_project_plan_context:'读取项目计划上下文',query_design_route_context:'读取设计BOM与路线上下文',query_manufacturing_quality_context:'读取制造质检上下文',query_assembly_trial_context:'读取装配试模上下文',query_delivery_logistics_context:'读取交付物流上下文',query_full_outsource_context:'读取整套委外上下文',query_change_intake_context:'读取设变承接上下文',query_finance_context:'读取财务节点上下文',query_procurement_price_context:'读取采购价格与订单上下文',analyze_delivery_risk:'分析发货延期风险'}
Object.assign(capabilityNames,{erp_design_parse_new_mold_upload:'解析新模设计上传清单',erp_design_get_drawing_status:'查询新模图纸匹配状态',erp_design_get_upload_result:'读取新模设计上传结果',erp_design_validate_rows:'校验新模设计上传明细',erp_design_reprice_rows:'重新核算新模设计价格',erp_design_get_approval_config:'读取新模设计审批配置',erp_design_import_new_mold:'导入新模设计清单',erp_design_query_orders:'查询 ERP 设计订单',erp_design_query_drawing_versions:'查询 ERP 图纸版本',erp_design_query_bom:'查询 ERP BOM',erp_design_query_bom_report:'查询 ERP BOM 报表',erp_design_query_changes:'查询 ERP 设计变更',erp_design_query_standard_hardware:'查询 ERP 厂内标准件',erp_design_query_master_data:'查询 ERP 设计基础资料',erp_design_query_densities:'查询 ERP 材质密度',erp_design_query_group_rules:'查询 ERP 设计分组规则',erp_design_query_group_keywords:'查询 ERP 分组关键词',erp_design_get_record:'读取 ERP 设计记录',erp_design_compare_drawing_versions:'对比 ERP 图纸版本',erp_design_analyze_change:'分析 ERP 设计变更影响',erp_design_get_mold_repair_approval:'读取修模改模审批',erp_design_get_mold_repair_outsource_approval:'读取修模改模委外审批',erp_design_get_mold_repair_processor_response:'读取加工商处理反馈',erp_design_rematch_no_drawing:'重新匹配无图设计明细',erp_design_update_order_item:'修改 ERP 设计订单明细',erp_design_save_scrap_decision:'保存闲置料使用决策',erp_design_release_scrap_decision:'释放闲置料使用决策',erp_design_manage_density:'维护 ERP 材质密度',erp_design_manage_group_rule:'维护 ERP 设计分组规则',erp_design_manage_group_keyword:'维护 ERP 分组关键词',erp_design_upload_standard_hardware:'上传 ERP 厂内标准件图纸',erp_design_rename_standard_hardware:'重命名 ERP 厂内标准件图纸',erp_design_delete_standard_hardware:'删除 ERP 厂内标准件图纸目录',erp_design_manage_change:'办理 ERP 设计变更',erp_design_query_change_items:'查询 ERP 设计变更明细',erp_design_manage_change_items:'维护 ERP 设计变更明细',erp_design_manage_order:'办理 ERP 设计订单',erp_design_manage_order_draft_scrap:'维护设计订单草稿闲置料',erp_design_submit_upload_change:'提交设计上传变更请购',erp_design_manage_mold_repair:'办理修模改模图纸异常',erp_design_manage_bom:'维护 ERP BOM',erp_design_query_bom_shortage:'查询 ERP BOM 缺料',erp_design_upload_mold_repair_drawing:'上传修模改模图纸',erp_design_import_bom:'导入 ERP BOM 工艺清单',erp_design_download_file:'下载 ERP 设计资料'})
Object.assign(capabilityNames,{erp_design_evaluate_tolerances:'判断 ERP 新模钢料公差',erp_design_tolerance_evaluation:'ERP 新模钢料公差判断'})
Object.assign(capabilityNames,{query_internal_start:'查询正式开工通知',query_project_plan:'查询项目计划',query_plan_change:'查询计划变更',query_pause_resume:'查询暂停与恢复',query_project_close:'查询项目关闭',query_purchase_price:'查询采购价格',query_full_outsource_contract:'查询整套委外合同'})
Object.assign(capabilityNames,{purchase_request_review:'采购申请核对',business_object_matching:'业务对象候选匹配',quote_acceptance_review:'报价与承接上下文核对',quote_evaluation_review:'报价评估与加工方式核对',bid_intake_review:'中标接收与客户规则核对',contract_context_review:'合同上下文核对',internal_start_readiness:'正式开工条件核对',project_plan_context_review:'项目计划上下文核对',project_plan_change:'项目计划变更',prepare_project_plan_change:'准备项目计划变更',design_route_context_review:'设计BOM与路线上下文核对',manufacturing_quality_review:'制造工序与质检上下文核对',assembly_trial_review:'装配试模上下文核对',delivery_logistics_review:'交付物流上下文核对',full_outsource_review:'整套委外协同上下文核对',change_intake_review:'设变承接上下文核对',finance_context_review:'财务节点与收付款核对',governance_context_review:'治理权限与来源核对',query_governance_context:'读取治理权限与来源上下文',operations_readiness_review:'运行交付就绪核对',query_operations_readiness_context:'读取运行交付就绪上下文',procurement_price_context_review:'采购价格与订单上下文核对',delivery_risk_analysis:'供应商发货风险分析',business_status_review:'业务审批与执行核对',query_purchase_orders:'查询采购订单',prepare_contract_signing_record:'准备合同签署记录',prepare_supplier_material_handoff:'准备供应商资料交接',prepare_supplier_material_verification:'准备供应商资料核验',prepare_supplier_progress_policy:'准备供应商上报规则',prepare_supplier_progress_report:'准备供应商节点上报',prepare_supplier_deduction_settlement:'准备供应商扣款结算'})
Object.assign(capabilityNames,{prepare_logistics_route:'准备物流路线确认',prepare_logistics_quote:'准备物流报价/结算价确认'})
export function capabilityName(value:any,items:any[]=[]){
 const key=typeof value==='string'?value:value?.key||''
 const providedName=typeof value==='object'?String(value?.name||''):''
 const itemName=String(items.find(i=>i.key===key)?.name||'')
 const isKeyLike=(name:string)=>/^[a-z][a-z0-9_]*$/i.test(name)
 return capabilityNames[key]||(!isKeyLike(providedName)&&providedName)||(!isKeyLike(itemName)&&itemName)||(key.startsWith('query_')&&businessNames[key.slice(6)]?'查询'+businessNames[key.slice(6)]:'业务查询能力')
}
Object.assign(capabilityNames,{query_contact_cases:'查询工程联络协作',contact_collaboration_review:'工程联络协作核对'})
export const capabilityDepartmentNames:Record<string,string>={project:'项目管理',purchase:'采购部门',design:'设计部门',engineering:'工程部门',finance:'财务部门',warehouse:'仓储部门',assembly:'装配部门',trial:'试模部门',sales:'销售部门',system:'管理部门'}
export const capabilityTypeNames:Record<string,string>={query:'查询',operation:'操作',approval:'审批',review:'核对'}
Object.assign(businessNames,{project_plan_change:'项目计划变更'})
const departmentByBusiness:Record<string,string>={project:'project',project_control:'project',project_close:'project',pause_resume:'project',project_dossier:'project',project_plan_change:'project',purchase:'purchase',purchase_request:'purchase',order:'purchase',supplier_payment:'finance',purchase_price:'purchase',design_route:'design',engineering_change:'engineering',contact:'engineering',contact_resolution:'engineering',finance_reversal:'finance',finance_correction:'finance',warehouse:'warehouse',assembly_issue:'assembly',trial_request:'trial',quotation:'sales',quote_acceptance:'sales',sales_contract:'sales',start_notice:'project',internal_start:'project',outsource_contract:'purchase',full_outsource_contract:'purchase',project_plan:'project',plan_change:'project',shipment:'warehouse',receipt:'warehouse',inspection:'warehouse',stock:'warehouse',risk:'purchase',master:'system',file:'system',user:'system',grant:'system',workflow:'system',audit:'system',agent:'system'}
const capabilityDepartments:Record<string,string>={query_projects:'project',query_project_dossier:'project',query_business_object_candidates:'project',project_dossier_review:'project',business_object_matching:'project',query_quote_acceptance_context:'sales',query_quote_evaluation_context:'sales',query_bid_intake_context:'sales',quote_acceptance_review:'sales',quote_evaluation_review:'sales',bid_intake_review:'sales',query_contract_context:'finance',contract_context_review:'finance',query_finance_context:'finance',prepare_contract_signing_record:'finance',prepare_supplier_deduction_settlement:'finance',finance_context_review:'finance',query_governance_context:'system',governance_context_review:'system',query_operations_readiness_context:'system',operations_readiness_review:'system',query_internal_start_readiness:'project',internal_start_readiness:'project',query_project_plan_context:'project',project_plan_context_review:'project',project_plan_change:'project',prepare_project_plan_change:'project',query_design_route_context:'design',design_route_context_review:'design',query_manufacturing_quality_context:'project',manufacturing_quality_review:'project',query_assembly_trial_context:'assembly',assembly_trial_review:'assembly',query_delivery_logistics_context:'warehouse',prepare_logistics_route:'warehouse',prepare_logistics_quote:'purchase',delivery_logistics_review:'warehouse',query_full_outsource_context:'purchase',prepare_supplier_material_handoff:'purchase',prepare_supplier_material_verification:'purchase',prepare_supplier_progress_policy:'purchase',prepare_supplier_progress_report:'purchase',full_outsource_review:'purchase',query_change_intake_context:'engineering',change_intake_review:'engineering',query_procurement_price_context:'purchase',procurement_price_context_review:'purchase',query_purchase_requests:'purchase',purchase_request_review:'purchase',purchase_review:'purchase',query_orders:'purchase',query_purchase_orders:'purchase',analyze_delivery_risk:'purchase',delivery_risk_analysis:'purchase',business_status_review:'purchase',query_business_subjects:'project',project_overview:'project',query_project_control_context:'project',prepare_project_pause:'project',prepare_project_resume:'project',project_pause_resume:'project',query_project_closure_context:'project',prepare_project_closure_checklist:'project',prepare_project_termination:'project',prepare_project_closure_item:'project',prepare_project_normal_close:'project',prepare_project_settlement_close:'project',project_termination_closure:'project',query_contact_cases:'engineering',query_contact_context:'engineering',contact_collaboration_review:'engineering',query_contact_resolution:'engineering',prepare_contact_resolution:'engineering',prepare_contact_review:'engineering',prepare_contact_close:'engineering',prepare_contact_set_reviewer:'engineering',prepare_contact_cancel_task:'engineering',prepare_contact_create:'engineering',prepare_contact_note:'engineering',prepare_contact_task:'engineering',prepare_contact_assign:'engineering',prepare_contact_respond:'engineering',prepare_contact_attach:'engineering',query_uploaded_files:'system'}
const capabilityTypes:Record<string,string>={purchase_request_review:'review',business_object_matching:'review',quote_acceptance_review:'review',quote_evaluation_review:'review',bid_intake_review:'review',contract_context_review:'review',finance_context_review:'review',governance_context_review:'review',operations_readiness_review:'review',internal_start_readiness:'review',project_plan_context_review:'review',project_plan_change:'approval',prepare_project_plan_change:'approval',design_route_context_review:'review',manufacturing_quality_review:'review',full_outsource_review:'review',change_intake_review:'review',procurement_price_context_review:'review',delivery_risk_analysis:'review',contact_collaboration_review:'review',business_status_review:'review',project_dossier_review:'review',project_pause_resume:'approval',project_termination_closure:'approval',prepare_project_pause:'approval',prepare_project_resume:'approval',prepare_project_closure_checklist:'operation',prepare_project_termination:'approval',prepare_project_closure_item:'operation',prepare_project_normal_close:'approval',prepare_project_settlement_close:'approval',prepare_contact_resolution:'approval',prepare_contact_review:'review',prepare_contact_close:'operation',prepare_contact_set_reviewer:'operation',prepare_contact_cancel_task:'operation',prepare_contract_signing_record:'operation',prepare_supplier_material_handoff:'operation',prepare_supplier_material_verification:'operation',prepare_supplier_progress_policy:'operation',prepare_supplier_progress_report:'operation',prepare_supplier_deduction_settlement:'operation',prepare_logistics_route:'operation',prepare_logistics_quote:'approval'}
function capabilityBusinessKey(key:string,permission=''){
 const raw=key.startsWith('query_')?key.slice(6):key.startsWith('prepare_')?key.slice(8):key
 const fromPermission=permission.split('.')[0]
 return raw in departmentByBusiness?raw:fromPermission
}
export function capabilityMeta(item:any){
 const key=typeof item==='string'?item:item?.key||''
 const permission=typeof item==='string'?'':item?.permission||''
 const business=capabilityBusinessKey(key,permission)
 const sourceDepartment=(typeof item==='object'&&item?.department)||capabilityDepartments[key]||departmentByBusiness[business]||'system'
 const department=sourceDepartment==='agent'?'system':sourceDepartment
 const action=permission.split('.')[1]||''
 const type=(typeof item==='object'&&item?.type)||capabilityTypes[key]||(
  key.startsWith('query_')||action==='read'?'query':
  action==='approve'?'approval':
  key.includes('review')||key.endsWith('_review')?'review':
  'operation'
 )
 const sourceDepartmentName=typeof item==='object'&&item?.department_name
 return {department,type,departmentName:sourceDepartmentName&&sourceDepartment!=='agent'?sourceDepartmentName:capabilityDepartmentNames[department]||'业务部门',typeName:(typeof item==='object'&&item?.type_name)||capabilityTypeNames[type]||'操作'}
}
export function groupedCapabilities(items:any[]=[]){
 const departments:Record<string,{key:string;name:string;types:Record<string,{key:string;name:string;items:any[]}>}>={}
 for(const item of items){
  const meta=capabilityMeta(item)
  const department=departments[meta.department]||(departments[meta.department]={key:meta.department,name:meta.departmentName,types:{}})
  const type=department.types[meta.type]||(department.types[meta.type]={key:meta.type,name:meta.typeName,items:[]})
  type.items.push(item)
 }
 return Object.values(departments).map(department=>({...department,types:Object.values(department.types)}))
}
const auditNames:Record<string,string>={'auth.login':'用户登录','user.created':'创建用户','permission.changed':'变更用户权限','permission.revoked':'撤销用户权限','capability.changed':'变更工具或技能授权','workflow.draft.created':'创建审批流程草稿','workflow.draft.updated':'修改审批流程草稿','workflow.published':'发布审批流程','purchase.draft.created':'创建采购草稿','purchase.submitted':'提交采购审批','approval.assignment.blocked':'审批人员分配待处理','approval.pending':'收到待审批事项','approval.claim.available':'候选审批任务待领取','approval.seat.claimed':'领取候选审批任务','approval.decided':'提交审批决定','approval.routed':'审批流转至后续节点','approval.withdrawn':'申请人撤回审批','human.confirmed':'人工确认操作','business.draft.created':'创建业务草稿','business.submitted':'提交业务审批','business.effective':'业务正式生效','agent.run.created':'创建智能体任务','project.created':'创建项目','project.role.changed':'调整项目角色','master.created':'维护基础资料','risk.policy.published':'发布预警规则','order.execution.draft.created':'生成订单执行草稿','order.draft.updated':'修改订单草稿'}
Object.assign(auditNames,{'user.avatar.updated':'更新用户头像'})
Object.assign(auditNames,{'approval.seat.transferred':'转交审批席位'})
Object.assign(auditNames,{'approval.seat.added':'增加审批复核人'})
Object.assign(auditNames,{'approval.proxy.enabled':'启用人工审批代理','approval.proxy.revoked':'撤销人工审批代理'})
export function auditName(value:string){return auditNames[value]||commandNames[value]||'业务操作记录'}
Object.assign(auditNames,{'contact.created':'创建工程联络单','contact.note':'追加联络过程记录','contact.task_created':'新增联络协作事项','contact.assigned':'分派联络事项','contact.responded':'提交联络处理反馈'})
export function statusName(value:string){return stateLabels[value]||({ACTIVE:'进行中',COMPLETED:'已完成',PENDING:'待处理',PUBLISHED:'已发布',BLOCKED:'等待处理',APPROVE:'同意',REJECT:'驳回',RETURN:'退回修改',WAITING:'等待处理',SKIPPED:'未经过此节点'} as Record<string,string>)[value]||'待核实状态'}
const fields:Record<string,string>={...labels,code:'编号',number:'单据编号',project_id:'项目',category:'采购类别',quantity:'明细数量',amount:'总金额',remark:'备注',due_date:'需求日期',revision:'材料版本',created_by:'创建人',submitted_at:'提交时间',submitter:'提交人',department:'部门',username:'登录名',field:'判断字段',op:'比较方式',value:'比较值',target:'目标节点',all:'全部满足',any:'任一满足',reason:'原因',name:'名称',version:'版本',remaining_quantity:'未发货数量',signals:'预警信号',suggestions:'建议',limitations:'分析范围说明',source:'来源',as_of:'查询时间',description:'说明',active:'是否启用',enabled:'是否启用',mode:'审批方式',business_type:'业务类型',warehouse_id:'仓库',scope:'数据范围'}
Object.assign(fields,{expected_ship_date:'预计发货日期',replaces_id:'替代原合同',shipped_quantity:'已发货数量',received_quantity:'已收货数量',execution_status:'执行状态',definition_id:'所用流程',instance_id:'审批记录',decision:'审批决定',comment:'审批意见',round_no:'审批轮次',supplier:'供应商',material:'物料',order_number:'订单编号',rule_version:'预警规则版本',near_due_days:'临期天数',exception_reasons:'异常原因',scope_confirmed:'管理范围已确认',internal_number:'内部编号',drawing_revision:'图纸版本',lines:'明细',history:'操作记录'})
export function fieldName(key:string){return fields[key]||'业务补充信息'}
export function valueText(key:string,value:any):string {
 if(value===null||value===undefined||value==='')return '—'
 if(typeof value==='boolean')return value?'是':'否'
 if(key==='status'||key==='execution_status')return statusName(String(value))
 if(key==='permission')return permissionName(String(value))
 if(key==='business_type'||key==='kind')return businessNames[value]||({TOOL:'工具',SKILL:'技能'} as Record<string,string>)[value]||String(value)
 return currencyNames[value]||categoryNames[value]||optionNames[value]||String(value)
}
const operators:Record<string,string>={eq:'等于',ne:'不等于',gt:'大于',gte:'大于等于',lt:'小于',lte:'小于等于',in:'属于以下任一值'}
export function ruleText(rule:any):string {
 if(!rule)return '未设置条件'
 if(rule.all)return '全部满足（'+rule.all.map(ruleText).join('；')+'）'
 if(rule.any)return '任一满足（'+rule.any.map(ruleText).join('；')+'）'
 return `${fieldName(rule.field)}${operators[rule.op]||'满足条件'} ${Array.isArray(rule.value)?rule.value.map((v:any)=>valueText(rule.field,v)).join('、'):valueText(rule.field,rule.value)}`
}
export function routeName(target:string,nodes:any[]){return target==='end'?'审批结束':nodes.find(n=>n.key===target)?.name||'节点未配置'}
export function scopeText(scope:any,projects:any[]=[]){if(scope.all===true)return '全部数据范围';return Object.entries(scope).map(([key,values]:any)=>`${fieldName(key)}：${values.map((v:string)=>key==='project_id'?(projects.find(p=>p.id===v)?.name||'指定项目'):valueText(key,v)).join('、')}`).join('；')}

Object.assign(capabilityNames,{query_contact_context:"读取联络单办理资料",prepare_contact_create:"准备发起联络单",prepare_contact_note:"准备补充联络记录",prepare_contact_task:"准备部门协作事项",prepare_contact_assign:"准备分派处理人",prepare_contact_respond:"准备提交联络反馈"})

Object.assign(capabilityNames,{query_project_control_context:'读取项目暂停恢复资料',prepare_project_pause:'准备项目整体暂停',prepare_project_resume:'准备项目整体恢复',project_pause_resume:'项目暂停与恢复'})

Object.assign(capabilityNames,{query_project_dossier:'查询项目业务档案',project_dossier_review:'项目业务档案核对'})

Object.assign(capabilityNames,{query_project_closure_context:'读取项目终止与结项资料',prepare_project_closure_checklist:'准备正常结项清单',prepare_project_termination:'准备项目终止审批',prepare_project_closure_item:'准备更新结项事项',prepare_project_normal_close:'准备正常关闭审批',prepare_project_settlement_close:'准备终止结算关闭',project_termination_closure:'项目终止、结算与关闭'})

Object.assign(capabilityNames,{prepare_contact_attach:"准备关联联络单附件",query_uploaded_files:"查询当前会话附件"})

Object.assign(verbs,{attach:"关联附件",upload:"上传本人会话附件"})
Object.assign(auditNames,{"file.uploaded":"上传会话附件","file.downloaded":"下载附件原件","file.previewed":"查看附件图片","contact.attachment_added":"关联联络单附件版本"})

Object.assign(businessNames,{contact_resolution:'联络单处理方案'})
Object.assign(verbs,{plan:'提交处理方案',review:'复验处理结果',set_reviewer:'指定验收负责人',cancel_task:'撤销未反馈事项'})
Object.assign(capabilityNames,{query_contact_resolution:'查询联络单处理方案',prepare_contact_resolution:'准备处理方案审批',prepare_contact_review:'准备复验处理结果',prepare_contact_close:'准备人工关闭联络单',prepare_contact_set_reviewer:'准备指定验收负责人',prepare_contact_cancel_task:'准备撤销联络事项'})
Object.assign(fields,{case_id:'关联联络单',case_revision:'联络资料版本',solution:'处理方案',customer_due_affected:'是否影响客户交期',customer_evidence:'客户确认依据',material_snapshot:'审批材料快照',tasks:'责任事项',attachments:'附件版本',title:'名称',filename:'文件名',size:'文件大小',media_type:'文件格式',sha256:'原件摘要',department_name:'责任部门',document_id:'材料标识',previous_id:'原版本标识',linked_at:'关联时间',linked_by:'关联人',is_current:'提交时的当前版本',file_id:'原件标识',closed_at:'关闭时间',closed_by_name:'关闭人',reviewer_name:'验收负责人',resolutions:'处理方案审批',verified_plan_id:'复验方案标识',approval_templates:'可选审批模板',category_name:'流程类别',instance_id:'审批记录'})
Object.assign(fields,{expected_resume_date:'预计恢复日期',task_snapshot:'暂停时冻结的未完成任务',customer_due_date_snapshot:'客户承诺交期快照',allowed_during_pause:'暂停期保留办理事项',plan_number:'关联计划单号',source_pause_number:'来源暂停单号',shifted_days:'实际顺延天数',task_shifts:'计划节点顺延记录',previous_start:'原计划开始',previous_end:'原计划完成',shifted_start:'顺延后开始',shifted_end:'顺延后完成',task_status:'顺延时任务状态'})
Object.assign(fields,{project_code:'项目编号',project_name:'项目名称',project_status:'项目状态',project_version:'项目版本',system_facts:'系统已知事实',closure_case:'结项清单',current_stage:'当前环节',item_key:'事项标识',label:'事项',allow_not_applicable:'可否不适用',system_managed:'系统事实校验',result:'处置或核对结果',evidence:'依据',source_system:'来源系统',source_ref:'来源原记录',source_as_of:'来源核对时点',history_count:'历史修订数',blockers:'未完成事项',active_plan_number:'有效计划单号',plan_task_count:'计划任务数',unfinished_plan_tasks:'未完成计划任务',open_contact_cases:'未关闭联络事项',open_payment_reservations:'未释放付款占用',open_local_purchase_orders:'本地未关闭采购订单',closure_case_version:'结项清单版本',completed_work_summary:'已完成工作',incurred_cost_summary:'已发生费用说明',incurred_cost_amount:'已发生费用金额'})
Object.assign(auditNames,{'project.closure.opened':'发起项目结项清单','project.closure.cancelled':'取消原结项清单','project.closure.item.updated':'更新结项核对事项','project.terminated':'项目终止生效','project.closed':'项目关闭生效'})
Object.assign(auditNames,{'contact.resolution_submitted':'提交联络方案审批','contact.reviewer_set':'指定联络验收负责人','contact.task_cancelled':'撤销联络事项','contact.task_reviewed':'复验联络处理结果','contact.closed':'人工关闭联络单'})
Object.assign(auditNames,{'plan.change.effective':'项目计划变更已生效'})

export function numberText(value:string=''){return value.replace(/^CONTACT_RESOLUTI-/, '联络方案-')}
export function capabilityExample(detail:{kind:'tool'|'skill';item:any}|null){
 if(!detail)return ''
 const name=capabilityName(detail.item)
 const meta=capabilityMeta(detail.item)
 if(detail.kind==='skill')return `请帮我做${name}，项目号 M250238，模具号 M250238-P4，重点核对当前状态、依据和风险。`
 return detail.item.mode==='human_confirmed_proposal'
  ? `准备一份${meta.departmentName}相关建议，项目号 M250238，模具号 M250238-P4，先给我核对，不要直接提交。`
  : `查询${meta.departmentName}，项目号 M250238，模具号 M250238-P4，返回当前状态、来源和需要注意的问题。`
}
