import {labels,optionNames,stateLabels,commandNames} from './businessForms'
export const categoryNames:Record<string,string>={raw_material:'原材',hardware:'五金',outsource:'委外',auxiliary:'辅材',office_supply:'办公用品',trial_material:'试模料'}
export const currencyNames:Record<string,string>={CNY:'人民币',USD:'美元',EUR:'欧元',HKD:'港币',JPY:'日元'}
export const businessNames:Record<string,string>={file:'文件',project:'项目',purchase:'采购申请',purchase_request:'采购申请',workflow:'审批流程',user:'用户',grant:'权限分配',audit:'操作审计',design_route:'设计与物料清单',purchase_price:'采购价格',assembly_issue:'装配任务',trial_request:'试模申请',finance_reversal:'财务冲正',quotation:'报价与承接',start_notice:'开工通知',sales_contract:'销售合同',outsource_contract:'整套委外合同',project_plan:'项目计划',plan_change:'计划变更',project_control:'暂停与恢复',supplier_payment:'供应商付款',engineering_change:'工程联络单',project_close:'项目关闭',order:'采购订单',warehouse:'仓储',risk:'风险预警',master:'基础资料',finance:'财务',assembly:'装配',trial:'试模',plan:'计划',change:'工程变更',shipment:'发货',receipt:'收货',inspection:'检验',stock:'库存',exception:'异常',business:'业务',agent:'智能体'}
const verbs:Record<string,string>={read:'查询',create:'新建',submit:'发起审批',approve:'审批',design:'配置',publish:'发布',manage:'管理',execute:'执行',configure:'配置',confirm:'确认',condition:'核验条件',implement:'登记实施',recheck:'复检',close:'关闭',issue:'正式下单',report:'登记'}
Object.assign(businessNames,{finance_correction:'财务冲正',quote_acceptance:'报价与承接',internal_start:'开工通知',full_outsource_contract:'整套委外合同',pause_resume:'暂停与恢复',identity:'关联身份'})
Object.assign(verbs,{edit:'编辑',reference:'查看身份关联'})
Object.assign(businessNames,{contact:'工程联络协作'})
Object.assign(verbs,{coordinate:'组织部门协作',assign:'分派处理人',respond:'提交处理反馈',record:'追加过程记录'})
export function permissionName(value:string){if(commandNames[value])return commandNames[value];const [kind,action]=value.split('.');return `${businessNames[kind]||'业务'} · ${verbs[action]||'操作权限'}`}
export const capabilityNames:Record<string,string>={query_projects:'查询项目资料',query_purchase_requests:'查询采购申请',purchase_review:'采购资料核对',project_overview:'项目概况查询',query_orders:'查询采购订单',query_business_subjects:'查询业务材料',analyze_delivery_risk:'分析发货延期风险'}
Object.assign(capabilityNames,{purchase_request_review:'采购申请核对',delivery_risk_analysis:'供应商发货风险分析',business_status_review:'业务审批与执行核对',query_purchase_orders:'查询采购订单'})
export function capabilityName(key:string,items:any[]=[]){return items.find(i=>i.key===key)?.name||capabilityNames[key]||(key.startsWith('query_')&&businessNames[key.slice(6)]?'查询'+businessNames[key.slice(6)]:'业务查询能力')}
Object.assign(capabilityNames,{query_contact_cases:'查询工程联络协作',contact_collaboration_review:'工程联络协作核对'})
const auditNames:Record<string,string>={'auth.login':'用户登录','user.created':'创建用户','permission.changed':'变更用户权限','permission.revoked':'撤销用户权限','capability.changed':'变更工具或技能授权','workflow.draft.created':'创建审批流程草稿','workflow.draft.updated':'修改审批流程草稿','workflow.published':'发布审批流程','purchase.draft.created':'创建采购草稿','purchase.submitted':'提交采购审批','approval.assignment.blocked':'审批人员分配待处理','approval.pending':'收到待审批事项','approval.decided':'提交审批决定','approval.routed':'审批流转至后续节点','human.confirmed':'人工确认操作','business.draft.created':'创建业务草稿','business.submitted':'提交业务审批','business.effective':'业务正式生效','agent.run.created':'创建智能体任务','project.created':'创建项目','master.created':'维护基础资料','risk.policy.published':'发布预警规则','order.execution.draft.created':'生成订单执行草稿','order.draft.updated':'修改订单草稿'}
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

Object.assign(capabilityNames,{prepare_contact_attach:"准备关联联络单附件",query_uploaded_files:"查询当前会话附件"})

Object.assign(verbs,{attach:"关联附件",upload:"上传本人会话附件"})
Object.assign(auditNames,{"file.uploaded":"上传会话附件","file.downloaded":"下载附件原件","file.previewed":"查看附件图片","contact.attachment_added":"关联联络单附件版本"})

Object.assign(businessNames,{contact_resolution:'联络单处理方案'})
Object.assign(verbs,{plan:'提交处理方案',review:'复验处理结果',set_reviewer:'指定验收负责人',cancel_task:'撤销未反馈事项'})
Object.assign(capabilityNames,{query_contact_resolution:'查询联络单处理方案',prepare_contact_resolution:'准备处理方案审批',prepare_contact_review:'准备复验处理结果',prepare_contact_close:'准备人工关闭联络单',prepare_contact_set_reviewer:'准备指定验收负责人',prepare_contact_cancel_task:'准备撤销联络事项'})
Object.assign(fields,{case_id:'关联联络单',case_revision:'联络资料版本',solution:'处理方案',customer_due_affected:'是否影响客户交期',customer_evidence:'客户确认依据',material_snapshot:'审批材料快照',tasks:'责任事项',attachments:'附件版本',title:'名称',filename:'文件名',size:'文件大小',media_type:'文件格式',sha256:'原件摘要',department_name:'责任部门',document_id:'材料标识',previous_id:'原版本标识',linked_at:'关联时间',linked_by:'关联人',is_current:'提交时的当前版本',file_id:'原件标识',closed_at:'关闭时间',closed_by_name:'关闭人',reviewer_name:'验收负责人',resolutions:'处理方案审批',verified_plan_id:'复验方案标识',approval_templates:'可选审批模板',category_name:'流程类别',instance_id:'审批记录'})
Object.assign(fields,{expected_resume_date:'预计恢复日期',task_snapshot:'暂停时冻结的未完成任务',customer_due_date_snapshot:'客户承诺交期快照',allowed_during_pause:'暂停期保留办理事项',plan_number:'关联计划单号',source_pause_number:'来源暂停单号',shifted_days:'实际顺延天数',task_shifts:'计划节点顺延记录',previous_start:'原计划开始',previous_end:'原计划完成',shifted_start:'顺延后开始',shifted_end:'顺延后完成',task_status:'顺延时任务状态'})
Object.assign(auditNames,{'contact.resolution_submitted':'提交联络方案审批','contact.reviewer_set':'指定联络验收负责人','contact.task_cancelled':'撤销联络事项','contact.task_reviewed':'复验联络处理结果','contact.closed':'人工关闭联络单'})

export function numberText(value:string=''){return value.replace(/^CONTACT_RESOLUTI-/, '联络方案-')}
