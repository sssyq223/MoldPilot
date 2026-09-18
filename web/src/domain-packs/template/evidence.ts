import {fieldName,valueText} from './uiText'

const hidden=new Set(['id','created_at','updated_at','analysis','detail','snapshot','raw','payload'])
export function isRecord(value:any){return value&&typeof value==='object'&&!Array.isArray(value)}
export function readableEntries(value:any,limit=12){return isRecord(value)?Object.entries(value).filter(([key,item])=>!hidden.has(key)&&item!==undefined&&item!==null&&item!==''&&typeof item!=='object').slice(0,limit).map(([key,item])=>({key,label:fieldName(key),value:valueText(key,item)})):[]}
export function compactRecordTitle(row:any){return [row?.number??row?.code,row?.name??row?.title].filter(Boolean).join(' · ')}
export function compactRecordFields(row:any){return readableEntries(row,3)}
export function evidenceSummary(row:any){return readableEntries(row,12)}
export function evidenceSections(row:any){return Object.entries(row||{}).filter(([key,value])=>!hidden.has(key)&&value&&typeof value==='object').slice(0,8).map(([key,value])=>Array.isArray(value)?{key,title:fieldName(key),kind:'array',count:value.length,rows:value.slice(0,12)}:{key,title:fieldName(key),kind:'object',pairs:readableEntries(value,10)})}
export function evidenceCardTitle(row:any,index:number,title:string){return compactRecordTitle(row)||`${title} ${index+1}`}
export function evidenceBriefTitle(item:any){const rows=Array.isArray(item?.data)?item.data:[];return rows.length?(compactRecordTitle(rows[0])||`记录 ${rows.length} 条`):'暂无可见记录'}
export function evidenceBriefSummary(item:any){const rows=Array.isArray(item?.data)?item.data:[];return rows.length?compactRecordFields(rows[0]).map((item:any)=>`${item.label} ${item.value}`).join(' · '):'本次查询未返回可见记录。'}
export function hasBusinessFactHighlights(_row:any){return false}
