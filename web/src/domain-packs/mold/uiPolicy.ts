import {categoryNames,currencyNames} from './uiText'

export const approvalConfirmationNotice='提交后将记录你的正式审批决定。审批通过不代表已下单或已发货。'
export const authorizationUi:any={
 defaultPermission:'purchase.read',defaultCategory:'hardware',resourceEndpoint:'/projects',resourceScopeKey:'project_id',resourceLabel:'项目',resourcePlaceholder:'选择项目',resourceCodeField:'code',
 unrestrictedPermissions:['file.upload'],categoryScopeKey:'category',categoryPermissionPrefixes:['purchase.','contact.'],categoryLabel:'采购责任域',categoryOptions:categoryNames,
 permissionHints:{'file.upload':'允许向本人会话上传附件；关联联络单仍须另外授予联络单附件权限和数据范围。'} as Record<string,string>,
 allScopeLabel:'明确授予全部数据范围',scopeHelp:'每条授权独立限定动作和范围；新增原材权限不会与另一项目的五金权限交叉扩大。',
}
export const workflowUi:any={
 defaultField:'quantity',fields:[['quantity','明细数量'],['amount','总金额'],['currency','币种'],['category','采购类别'],['remark','备注'],['project_id','项目标识']],
 numericFields:['amount','quantity'],selectOptions:{category:categoryNames,currency:currencyNames} as Record<string,Record<string,string>>,
 simulationFields:[{key:'quantity',label:'测试数量',value:''},{key:'amount',label:'测试金额',value:''},{key:'currency',label:'测试币种',value:'CNY',options:currencyNames},{key:'category',label:'测试采购类别',value:'',options:categoryNames,emptyLabel:'未提供'},{key:'project_id',label:'测试项目标识',value:''},{key:'remark',label:'测试备注',value:''}],
 categoryHelp:'类别由管理员自行命名，例如加工、采购、委外。类别用于整理模板，不授予业务权限。',
}
export const capabilityUi:any={
 queryUsage:'用法：在对话里提供项目、单号、模号或关键词，系统只查询资料并返回依据。',
 pageHelp:'当前账号可使用的业务能力，由管理员分配。使用时在对话里说“查询/准备 + 项目、单号、模号或关键词”；需确认的工具会先生成建议，不会直接提交。',
 delegationReasonPlaceholder:'例如：低风险辅材采购金额小、资料齐全时允许自动同意',
 termReplacements:{Agent:'智能体',BOM:'物料清单'},
}
export function notificationWorkspaceTarget(notification:any){return notification?.kind?.startsWith('contact.')?{target:'contacts',id:notification.resource_id}:null}
export function toolEvidenceLinks(item:any){return ['query_contact_cases','query_contact_context'].includes(item?.tool)?(item.data||[]).map((row:any)=>({target:'contacts',id:row.id,label:`查看联络材料：${row.title}`})):[]}
export function legacyStorageKeys(userId:string){return {approvalMode:`mold.agentPermissionMode.${userId}`,layout:`mold.layout.${userId}`}}
