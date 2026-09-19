import {fieldName,valueText} from './uiText'

const compactHidden=new Set(['id','subject_id','plan_id','created_at','updated_at','analysis','detail','snapshot','project','subject','lines','history','tasks','attachments'])
const titleKeys=['number','code','internal_number','project_code','name','title','project_name','status','project_status']
const compactKeys=['execution_mode','customer_due_date','due_date','drawing_revision','revision','version','scope_confirmed','business_status','remark','description']
const evidenceHidden=new Set([...compactHidden,'analysis','raw','payload','material_snapshot','system_facts'])
const evidencePriority=['number','code','internal_number','project_code','project_name','name','title','status','project_status','customer_due_date','execution_mode','business_status','plan_number','active_plan_number','expected_ship_date','due_date','remark','description','reason','source_ref']

function firstValue(row:any,key:string){return row?.[key]??row?.detail?.[key]??row?.snapshot?.[key]??row?.project?.[key]??row?.subject?.[key]}
export function isRecord(value:any){return value&&typeof value==='object'&&!Array.isArray(value)}
export function readableEntries(obj:any,limit=12){
 if(!isRecord(obj))return []
 const entries=Object.entries(obj).filter(([key,value])=>!evidenceHidden.has(key)&&value!==undefined&&value!==null&&value!==''&&typeof value!=='object')
 entries.sort(([a],[b])=>{const ia=evidencePriority.indexOf(a),ib=evidencePriority.indexOf(b);return (ia<0?999:ia)-(ib<0?999:ib)})
 return entries.slice(0,limit).map(([key,value])=>({key,label:fieldName(key),value:valueText(key,value)}))
}
export function compactRecordTitle(row:any){
 const code=firstValue(row,'number')??firstValue(row,'code')??firstValue(row,'internal_number')??firstValue(row,'project_code')
 const name=firstValue(row,'name')??firstValue(row,'title')??firstValue(row,'project_name')
 return [code&&valueText('number',code),name&&valueText('name',name)].filter(Boolean).join(' · ')
}
export function compactRecordFields(row:any){
 const used=new Set(titleKeys.map(key=>firstValue(row,key)).filter(v=>v!==undefined&&v!==null&&v!=='').map(v=>String(v)))
 const pairs:{key:string;label:string;value:string}[]=[]
 const status=firstValue(row,'project_status')??firstValue(row,'status')
 if(status!==undefined&&status!==null&&status!=='')pairs.push({key:'project_status',label:fieldName('project_status'),value:valueText('status',status)})
 for(const key of compactKeys){const raw=firstValue(row,key);if(raw===undefined||raw===null||raw===''||typeof raw==='object'||used.has(String(raw)))continue;pairs.push({key,label:fieldName(key),value:valueText(key,raw)});if(pairs.length>=3)return pairs}
 for(const [key,raw] of Object.entries(row||{})){if(pairs.length>=3)break;if(compactHidden.has(key)||titleKeys.includes(key)||raw===undefined||raw===null||raw===''||typeof raw==='object'||used.has(String(raw)))continue;pairs.push({key,label:fieldName(key),value:valueText(key,raw)})}
 return pairs
}
export function evidenceSummary(row:any){
 const seen=new Set<string>(),pairs:{key:string;label:string;value:string}[]=[]
 for(const source of [row?.project,row?.subject,row,row?.detail,row?.snapshot])for(const item of readableEntries(source,16)){const sig=item.label+':'+item.value;if(seen.has(sig))continue;seen.add(sig);pairs.push(item);if(pairs.length>=12)return pairs}
 return pairs
}
export function evidenceSections(row:any){
 const sections:any[]=[],visited=new WeakSet<object>()
 for(const source of [row,row?.detail,row?.snapshot]){if(!isRecord(source)||visited.has(source))continue;visited.add(source);for(const [key,value] of Object.entries(source)){if(evidenceHidden.has(key)||value===undefined||value===null||value==='')continue;if(Array.isArray(value)){sections.push({key,title:fieldName(key),kind:'array',count:value.length,rows:value.slice(0,12)})}else if(isRecord(value)&&!visited.has(value)){visited.add(value);const pairs=readableEntries(value,10);if(pairs.length)sections.push({key,title:fieldName(key),kind:'object',pairs})}if(sections.length>=8)return sections}}
 return sections
}
export function evidenceCardTitle(row:any,index:number,title:string){return isRecord(row)?(compactRecordTitle(row)||`${title} ${Number(index)+1}`):`${title} ${Number(index)+1}`}
export function evidenceBriefTitle(item:any){const rows=Array.isArray(item?.data)?item.data:[];if(!rows.length)return '暂无可见记录';const title=compactRecordTitle(rows[0])||'业务记录';return rows.length>1?`${title} 等 ${rows.length} 条记录`:title}
export function evidenceBriefSummary(item:any){const rows=Array.isArray(item?.data)?item.data:[];if(!rows.length)return '本次查询未返回当前权限范围内的业务记录。';const facts=compactRecordFields(rows[0]).map(fact=>`${fact.label} ${fact.value}`);return facts.length?facts.join(' · '):`${rows.length} 条记录 · 按当前权限返回`}
export function hasBusinessFactHighlights(row:any){const analysis=row?.analysis;return Boolean(analysis?.project_lifecycle||analysis?.kickoff_lifecycle||analysis?.execution_lifecycle||analysis?.completion_lifecycle||analysis?.tasks?.length||analysis?.revision_impact||analysis?.plan_change_candidates?.length)}
