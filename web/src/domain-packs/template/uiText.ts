export const categoryNames:Record<string,string>={}
export const currencyNames:Record<string,string>={}
export const businessNames:Record<string,string>={}
export const capabilityNames:Record<string,string>={}
export const capabilityDepartmentNames:Record<string,string>={agent:'智能体'}
export const capabilityTypeNames:Record<string,string>={query:'查询',operation:'操作',approval:'审批',review:'核对'}
export function permissionName(value:string){return value}
export function capabilityName(value:any){return typeof value==='string'?value:String(value?.name||value?.key||'能力')}
export function capabilityMeta(item:any){return {department:'agent',type:'operation',departmentName:'智能体',typeName:'操作',...item}}
export function groupedCapabilities(items:any[]=[]){return [{key:'agent',name:'智能体',types:[{key:'operation',name:'操作',items}]}]}
export function auditName(value:string){return value}
export function statusName(value:string){return value}
export function fieldName(key:string){return key}
export function valueText(_key:string,value:any){return value==null?'—':typeof value==='object'?JSON.stringify(value):String(value)}
export function ruleText(rule:any){return JSON.stringify(rule)}
export function routeName(target:string){return target}
export function scopeText(scope:any){return JSON.stringify(scope)}
export function numberText(value:string=''){return value}
export function capabilityExample(detail:{kind:'tool'|'skill';item:any}|null){
  if(!detail)return ''
  return `请使用${capabilityName(detail.item)}处理当前任务，并说明依据与结果。`
}
