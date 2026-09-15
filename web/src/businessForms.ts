export const labels:Record<string,string>={
 design_type:'设计类型',categories:'适用采购类别',design_types:'适用设计类型',reject_rules:'必须驳回条件',routes:'条件分支',default_target:'后续节点',
 drawing_revision:'图纸版本',drawing_evidence:'图纸及复核依据',reviewer_id:'设计复核人员',items:'BOM 明细',route:'加工路线',valid_from:'有效期开始',valid_to:'有效期结束',quote_evidence:'报价依据',design_id:'已生效设计',supervisor_id:'钳工主管',prerequisites_evidence:'齐套与装配条件核验',planned_date:'计划日期',execution_status:'实际执行状态',assembly_id:'已完成装配任务',location:'试模地点',acceptance_criteria:'验收标准',responsible_id:'试模责任人',results:'试模结果',execution:'实际执行记录',findings:'发现的问题与结论',change_id:'关联工程联络单',original_payment_id:'原付款记录',reversal_evidence:'实际冲正依据',reversal_date:'实际冲正日期',payments:'实付与冲正记录',price_subject_id:'生效价格版本',
 customer_id:'客户',supplier_id:'供应商',amount:'金额',currency:'币种',contract_number:'合同编号',expected_date:'预计日期',replaces_id:'替代原合同',
 stages:'付款阶段',name:'名称',condition:'适用条件',stage_id:'合同付款阶段',previous_id:'原计划版本',reason:'原因',tasks:'计划任务',key:'任务标识',
 owner_user_id:'责任人',planned_start:'计划开始',planned_end:'计划完成',prerequisites:'前置任务标识',source_subject_id:'前置业务单据',decision:'业务决定',
 execution_mode:'加工方式',effective_date:'生效日期',evidence:'人工核实依据',problem:'问题描述',solution:'处理方案',customer_due_affected:'影响客户承诺交期',
 customer_evidence:'客户确认依据',impacts:'影响清单',task_id:'受影响任务',action:'执行动作',quantity:'数量',reference:'凭据编号',shipped_date:'实际发货日',
 warehouse_id:'仓库',accepted_quantity:'合格数量',rejected_quantity:'不合格数量',paid_date:'实际付款日期',actual_date:'实际执行日期',passed:'复检合格',
 start_date:'库存起算日',version:'已核对版本',reservation:'待付占用',status:'状态',actual_start:'实际开始',actual_end:'实际完成',
 material_id:'物料',material_name:'物料名称',unit:'单位',category:'责任域',agreed_ship_date:'约定发货日',unit_price:'单价',
 contract_id:'关联合同',condition_confirmed:'条件已人工确认',condition_evidence:'条件核验依据',implemented_by:'实施人员',rechecked_by:'复检人员',
 implementation_evidence:'实施依据',recheck_passed:'复检结论',customer_due_date:'客户承诺交期',problem_id:'关联问题',detail:'业务材料',
}
export const stateLabels:Record<string,string>={DRAFT:'草稿',SUBMITTED:'审批中',APPROVED:'审批通过',APPLY_BLOCKED:'审批通过 · 待业务生效',EFFECTIVE:'已生效',REJECTED:'已驳回',RETURNED:'退回修改',CANCELLED:'已取消',CLOSED:'已关闭',ISSUED:'已正式下单',PLANNED:'计划中',RUNNING:'执行中',DONE:'已完成',STOPPED:'已停止',PAUSED:'已暂停'}
export const commandNames:Record<string,string>={'assembly.execute':'确认装配执行','trial.confirm':'确认试模结论','order.issue':'确认正式下单','shipment.confirm':'确认供应商发货','exception.report':'登记供应商异常','exception.close':'确认异常关闭','receipt.confirm':'确认仓库实收','inspection.confirm':'确认检验与合格入库','stock.issue':'确认生产发料','warehouse.configure':'确认库存管理范围','finance.condition':'确认付款阶段条件','finance.confirm':'确认实际付款','plan.execute':'确认任务执行','change.implement':'确认变更实施','change.recheck':'确认变更复检','change.close':'确认工程联络单关闭','business.retry_apply':'重新核对并生效'}
export const optionNames:Record<string,string>={NEW_MOLD:'新模',MOLD_CHANGE:'改模',raw_material:'原材',hardware:'五金',outsource:'委外',auxiliary:'辅材',office_supply:'办公用品',trial_material:'试模料',PURCHASE:'外购',OUTSOURCE:'委外加工',ACCEPT:'承接',REJECT:'拒单',START:'开始',DONE:'完成',INTERNAL:'内部制造',FULL_OUTSOURCE:'整套委外',PAUSE:'暂停',RESUME:'恢复',NORMAL_CLOSE:'正常关闭',TERMINATE:'终止项目',SETTLEMENT_CLOSE:'终止结算关闭',KEEP:'保持',CANCEL:'取消',REWORK:'返工'}
export function clean(value:any):any{if(Array.isArray(value))return value.map(clean);if(value&&typeof value==='object')return Object.fromEntries(Object.entries(value).filter(([,v])=>v!==''&&v!==undefined).map(([k,v])=>[k,clean(v)]));return value}
export function resolve(schema:any,root:any):any{if(schema?.$ref)return resolve(root.$defs?.[schema.$ref.split('/').pop()]??{},root);if(schema?.anyOf)return resolve(schema.anyOf.find((x:any)=>x.type!=='null')??{},root);return schema??{}}
