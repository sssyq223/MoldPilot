<script setup lang="ts">
import { computed, nextTick, onMounted, onUnmounted, ref, watch, watchEffect } from 'vue'
import { MessageSquare, Plus, Search, Bell, Paperclip, PanelRight, Maximize2, Minimize2, ArrowUp, ArrowRight, Settings, Bot, ChevronRight, LogOut, X, Square, Pin, Archive, Copy, Check, ShieldCheck, Wrench } from 'lucide-vue-next'
import { api, post, shanghai } from './api'
import {capabilityName,auditName,numberText,fieldName,valueText} from '@domain-pack/uiText'
import {initialProduct} from '@domain-pack/product'
import SettingsPage from './components/SettingsPage.vue'
import ApprovalPanel from './components/ApprovalPanel.vue'
import WelcomePanel from '@domain-pack/components/WelcomePanel.vue'
import DomainWorkspacePanel from '@domain-pack/components/DomainWorkspacePanel.vue'
import ProposalCard from './components/ProposalCard.vue'
import FileMaterial from './components/FileMaterial.vue'
import BusinessFacts from '@domain-pack/components/BusinessFacts.vue'
import {legacyStorageKeys,notificationWorkspaceTarget,toolEvidenceLinks} from '@domain-pack/uiPolicy'
import ErpDesignTable from './components/ErpDesignTable.vue'
import ErpDrawingPreview from './components/ErpDrawingPreview.vue'
import ErpDesignOrdersDialog from './components/ErpDesignOrdersDialog.vue'
import {applyTheme,storedTheme,type ColorTheme} from './theme'
import {erpDesignSessionFromRun,erpDesignSessionFromTool,normalizeErpDesignImportReceipt,normalizeErpDesignPreview,shouldOpenErpDesignPreview,type ErpDesignImportReceipt,type ErpDesignPreviewSession,type ErpDesignRow} from './erpDesignPreview'
const colorTheme=ref<ColorTheme>(storedTheme())
function changeTheme(theme:ColorTheme){colorTheme.value=theme;applyTheme(theme)}
const product=ref<any>(initialProduct)
const modelName=ref('未配置模型')
const modelLimits=ref<any>({context_window:8192,max_output_tokens:2048})
const modelProfiles=ref<any[]>([]),activeModelProfileId=ref(''),modelSwitchingId=ref('')
const DEFAULT_SIDEBAR_WIDTH=248
const DEFAULT_WORKSPACE_WIDTH=650
const MIN_WORKSPACE_WIDTH=420
type WorkspaceTarget={target:string;id:string}
const workspaceTargets=ref<Record<string,string>>({})
const selectedEvidence=ref<any|null>(null)
const erpDesignOrdersDialog=ref<any|null>(null)
const erpDesignPreview=ref<ErpDesignPreviewSession|null>(null)
const erpDesignPreviewLoading=ref(false)
const erpDesignPreviewError=ref('')
const erpDesignRepricing=ref(false)
const erpDesignImporting=ref(false)
const erpDesignPreviewNotice=ref('')
const erpDesignDuplicateNotice=ref<{message:string;requestNo:string}|null>(null)
const erpDesignImportReceipts=ref<Record<string,ErpDesignImportReceipt>>({})
const erpDrawingPreviewRow=ref<ErpDesignRow|null>(null)
const erpDesignDrafts=new Map<number,{expectedDate:string;remark:string}>()
const erpDesignRowRepriceTokens=new Map<string,number>()
let erpDesignPreviewPoll:number|null=null
let erpDesignPreviewRequest=0
const openedErpDesignRunIds=new Set<string>()
const loadedErpDesignImportStatuses=new Set<number>()
const selectedFiles=ref<any[]>([]),uploading=ref(false),fileInput=ref<HTMLInputElement|null>(null)
let conversationEpoch=0
const me=ref<any>(null),permissions=ref<string[]>([]),loading=ref(true),conversationLoading=ref(false),error=ref(''),busy=ref(false),username=ref(''),password=ref('')
const panel=ref(''),expanded=ref(false),full=ref(false),width=ref(DEFAULT_WORKSPACE_WIDTH),sidebarWidth=ref(DEFAULT_SIDEBAR_WIDTH),conversations=ref<any[]>([]),conversation=ref(''),activeConversationTitle=ref(''),activeConversationArchived=ref(false),runs=ref<any[]>([]),prompt=ref(''),search=ref(''),notices=ref<any[]>([]),showNotices=ref(false)
const approvals=ref<any[]>([]),initiatedApprovals=ref<any[]>([]),approvalWorkItems=ref<any>({copied:[],overdue:[]}),detail=ref<any>(null),capabilities=ref<any>({tools:[],skills:[]})
const runEventsReady=ref(false)
let runEvents:EventSource|null=null
const runProcessOpen=ref<Record<string,boolean>>({})
const streamedText=ref<Record<string,string>>({})
const streamedTextTargets=new Map<string,string>()
const streamedTextFrames=new Map<string,number>()
const streamedTextNextRevealAt=new Map<string,number>()
const streamedTextLiveKeys=new Set<string>()
const streamedTextCompletedKeys=new Set<string>()
const confirmedProposalSteps=ref<Record<string,boolean>>({})
const sidebarCollapsed=ref(false)
const copiedMessage=ref('')
const workspaceTabs=computed(()=>Array.isArray(product.value?.workspace_tabs)?product.value.workspace_tabs:[])
const settingsOpen=ref(false),settingsInitialPage=ref('account'),showProfile=ref(false),noticeLoading=ref(false),showSidebarSearch=ref(false)
const contextPopoverOpen=ref(false),modelPopoverOpen=ref(false),approvalModePopoverOpen=ref(false)
const approvalPermissionMode=ref<'ask'|'delegated_auto'>('ask')
const approvalPermissionLabel=computed(()=>approvalPermissionMode.value==='delegated_auto'?'按授权自动审批':'每次询问')
function productStoragePrefix(){return String(product.value?.id||'agent')}
function approvalModeStorageKey(){return productStoragePrefix()+'.agentPermissionMode.'+(me.value?.id||'anonymous')}
function setApprovalPermissionMode(mode:'ask'|'delegated_auto'){approvalPermissionMode.value=mode;if(me.value)localStorage.setItem(approvalModeStorageKey(),mode);approvalModePopoverOpen.value=false}
const profileButton=ref<HTMLButtonElement|null>(null),noticeButton=ref<HTMLButtonElement|null>(null),sidebarSearchInput=ref<HTMLInputElement|null>(null)
const chatScroll=ref<HTMLElement|null>(null)
const notificationPopoverStyle=ref<Record<string,string>>({left:'96px',top:'44px'})
const workspaceOpen=computed(()=>expanded.value)
const noticeCount=computed(()=>new Set(notices.value.filter(n=>!n.read).map(n=>n.kind?.startsWith('approval.')&&n.resource_id?'approval:'+n.resource_id:'notice:'+n.id)).size)
function closeProfile(){showProfile.value=false;profileButton.value?.focus()}
function closeRunEvents(){runEvents?.close();runEvents=null;runEventsReady.value=false}
function connectRunEvents(id:string){
 closeRunEvents()
 if(!id||!me.value||typeof EventSource==='undefined')return
 const source=new EventSource(`/api/conversations/${encodeURIComponent(id)}/runs/events`)
 runEvents=source
 source.onopen=()=>{if(runEvents===source)runEventsReady.value=true}
 source.onerror=()=>{if(runEvents===source)runEventsReady.value=false}
 source.addEventListener('runs',(event:MessageEvent)=>{
  if(runEvents!==source||conversation.value!==id)return
  try{const payload=JSON.parse(event.data);if(Array.isArray(payload))runs.value=payload}catch{}
 })
 source.addEventListener('transport',()=>{if(runEvents===source)runEventsReady.value=false})
}
watch([()=>me.value?.id,conversation],([userId,id])=>connectRunEvents(userId?String(id||''):''))
function openSettings(page:any='account'){settingsInitialPage.value=typeof page==='string'?page:'account';showProfile.value=false;showNotices.value=false;settingsOpen.value=true}
function toggleSidebarSearch(){
 if(showSidebarSearch.value||search.value){showSidebarSearch.value=false;search.value='';return}
 showSidebarSearch.value=true;nextTick(()=>sidebarSearchInput.value?.focus())
}
function updateNotificationPosition(){
 const rect=noticeButton.value?.getBoundingClientRect()
 if(!rect)return
 const width=340,gap=8
 let left=Math.round(rect.left-16)
 left=Math.max(10,Math.min(left,window.innerWidth-width-10))
 notificationPopoverStyle.value={left:left+'px',top:Math.round(rect.bottom+gap)+'px'}
}
function escapeMenu(e:KeyboardEvent){if(e.key==='Escape'){if(showProfile.value)closeProfile();showNotices.value=false;contextPopoverOpen.value=false;modelPopoverOpen.value=false;approvalModePopoverOpen.value=false;if(erpDesignOrdersDialog.value)erpDesignOrdersDialog.value=null;else if(erpDrawingPreviewRow.value)erpDrawingPreviewRow.value=null;else closeErpDesignPreview()}}
function closeFloatingPanels(e:MouseEvent){
 const target=e.target as Element|null
 if(showNotices.value&&!target?.closest('.notification-popover')&&!(target&&noticeButton.value?.contains(target)))showNotices.value=false
 if(contextPopoverOpen.value&&!target?.closest('.context-meter-wrap'))contextPopoverOpen.value=false
 if(modelPopoverOpen.value&&!target?.closest('.model-selector-wrap'))modelPopoverOpen.value=false
 if(approvalModePopoverOpen.value&&!target?.closest('.approval-mode-wrap'))approvalModePopoverOpen.value=false
}
onMounted(()=>{window.addEventListener('keydown',escapeMenu);window.addEventListener('click',closeFloatingPanels);window.addEventListener('resize',updateNotificationPosition)})
onUnmounted(()=>{window.removeEventListener('keydown',escapeMenu);window.removeEventListener('click',closeFloatingPanels);window.removeEventListener('resize',updateNotificationPosition);stopErpDesignPreviewPolling();for(const frame of streamedTextFrames.values())window.cancelAnimationFrame(frame);streamedTextFrames.clear()})
async function openNotices(){showProfile.value=false;showNotices.value=true;await nextTick();updateNotificationPosition();noticeLoading.value=true;try{[notices.value,approvals.value,initiatedApprovals.value,approvalWorkItems.value]=await Promise.all([api('/notifications'),api('/approvals'),api('/approvals/initiated'),api('/approval-work-items')])}catch(e:any){fail(e.message)}finally{noticeLoading.value=false}}
const currentTitle=computed(()=>conversations.value.find(c=>c.id===conversation.value)?.title || activeConversationTitle.value || '新对话')
const filteredConversations=computed(()=>conversations.value.filter(c=>c.title.includes(search.value)))
const running=computed(()=>runs.value.some(r=>['QUEUED','RUNNING'].includes(r.status)))
const activeRun=computed(()=>[...runs.value].reverse().find(r=>['QUEUED','RUNNING'].includes(r.status)))
const latestContextUsage=computed(()=>{
 const withUsage=[...runs.value].reverse().find((run:any)=>run.context_usage||run.progress?.context_usage)
 if(withUsage)return withUsage.context_usage||withUsage.progress?.context_usage
 const inputTokens=Math.ceil((prompt.value||'').length/2)
 const windowTokens=Number(modelLimits.value?.context_window||8192)
 const remaining=Math.max(0,windowTokens-inputTokens)
 return {context_window:windowTokens,max_output_tokens:Number(modelLimits.value?.max_output_tokens||2048),used_tokens:inputTokens,remaining_tokens:remaining,used_percent:windowTokens?Math.round(inputTokens/windowTokens*1000)/10:0,input_tokens:0,input_tokens_estimated:inputTokens,output_tokens:0,reasoning_tokens:0,total_tokens:0,tool_count:0,tool_schema_count:0,tool_message_tokens_estimated:0,compaction_count:0,token_source:'estimate',remaining_label:formatTokenCount(remaining),used_label:formatTokenCount(inputTokens),window_label:formatTokenCount(windowTokens)}
})
watchEffect(()=>{if(typeof document!=='undefined')document.documentElement.style.setProperty('--context-percent',`${Math.min(100,Math.max(0,Number(latestContextUsage.value?.used_percent||0)))}%`)})
const runStatus:Record<string,string>={QUEUED:'任务已排队',RUNNING:'正在执行',WAITING_CONFIGURATION:'等待模型配置',SUCCEEDED:'执行已完成',FAILED:'执行未完成',CANCELLED:'已停止'}
const runErrorMessages:Record<string,string>={
 HTTPStatusError:'Harness 保存运行状态或调用内部接口时发生 HTTP 异常。',
 HARNESS_BACKEND_REJECTED:'Harness 的内部请求被后端拒绝。',
 TOOL_FORBIDDEN:'模型请求了本轮未激活或当前无权使用的工具。',
 MODEL_OUTPUT_INVALID:'模型返回的工具调用或最终结果不符合协议。',
 MODEL_HTTP_FAILED:'模型服务返回异常状态。',
 MODEL_CONNECT_TIMEOUT:'连接模型服务超时。',
 MODEL_READ_TIMEOUT:'等待模型回复超时。',
 MODEL_NETWORK_ERROR:'模型服务网络连接异常。',
 CONTEXT_BUDGET_EXCEEDED:'上下文压缩后仍超过模型安全窗口。',
 BUDGET_EXCEEDED:'本轮执行已达到时间、回合或工具预算上限。',
}
function runFailureReason(code:any){return runErrorMessages[String(code||'')]||'执行链路发生未分类异常，请按错误码核对服务日志。'}
const compactHidden=new Set(['id','subject_id','plan_id','created_at','updated_at','analysis','detail','snapshot','project','subject','lines','history','tasks','attachments'])
const titleKeys=['number','code','internal_number','project_code','name','title','project_name','status','project_status']
const compactKeys=['execution_mode','customer_due_date','due_date','drawing_revision','revision','version','scope_confirmed','business_status','remark','description']
function firstValue(row:any,key:string){return row?.[key]??row?.detail?.[key]??row?.snapshot?.[key]??row?.project?.[key]??row?.subject?.[key]}
function compactRecordTitle(row:any){
 const code=firstValue(row,'number')??firstValue(row,'code')??firstValue(row,'internal_number')??firstValue(row,'project_code')
 const name=firstValue(row,'name')??firstValue(row,'title')??firstValue(row,'project_name')
 return [code&&valueText('number',code),name&&valueText('name',name)].filter(Boolean).join(' · ')
}
function compactRecordFields(row:any){
 const used=new Set(titleKeys.map(key=>firstValue(row,key)).filter(v=>v!==undefined&&v!==null&&v!=='').map(v=>String(v)))
 const pairs:{key:string;label:string;value:string}[]=[]
 const status=firstValue(row,'project_status')??firstValue(row,'status')
 if(status!==undefined&&status!==null&&status!=='')pairs.push({key:'project_status',label:'项目状态',value:valueText('status',status)})
 for(const key of compactKeys){
  const raw=firstValue(row,key)
  if(raw===undefined||raw===null||raw===''||typeof raw==='object'||used.has(String(raw)))continue
  pairs.push({key,label:fieldName(key),value:valueText(key,raw)})
  if(pairs.length>=3)return pairs
 }
 for(const [key,raw] of Object.entries(row||{})){
  if(pairs.length>=3)break
  if(compactHidden.has(key)||titleKeys.includes(key)||raw===undefined||raw===null||raw===''||typeof raw==='object'||used.has(String(raw)))continue
  pairs.push({key,label:fieldName(key),value:valueText(key,raw)})
 }
 return pairs
}
const evidenceHidden=new Set([...compactHidden,'analysis','raw','payload','material_snapshot','system_facts'])
const evidencePriority=['number','code','internal_number','project_code','project_name','name','title','status','project_status','customer_due_date','execution_mode','business_status','plan_number','active_plan_number','expected_ship_date','due_date','remark','description','reason','source_ref']
function isRecord(value:any){return value&&typeof value==='object'&&!Array.isArray(value)}
function readableEntries(obj:any,limit=12){
 if(!isRecord(obj))return []
 const entries=Object.entries(obj).filter(([key,value])=>!evidenceHidden.has(key)&&value!==undefined&&value!==null&&value!==''&&typeof value!=='object')
 entries.sort(([a],[b])=>{
  const ia=evidencePriority.indexOf(a),ib=evidencePriority.indexOf(b)
  return (ia<0?999:ia)-(ib<0?999:ib)
 })
 return entries.slice(0,limit).map(([key,value])=>({key,label:fieldName(key),value:valueText(key,value)}))
}
function evidenceSummary(row:any){
 const seen=new Set<string>(),pairs:{key:string;label:string;value:string}[]=[]
 for(const source of [row?.project,row?.subject,row,row?.detail,row?.snapshot]){
  for(const item of readableEntries(source,16)){
   const sig=item.label+':'+item.value
   if(seen.has(sig))continue
   seen.add(sig);pairs.push(item)
   if(pairs.length>=12)return pairs
  }
 }
 return pairs
}
function evidenceSections(row:any){
 const sections:any[]=[]
 const visited=new WeakSet<object>()
 for(const source of [row,row?.detail,row?.snapshot]){
  if(!isRecord(source)||visited.has(source))continue
  visited.add(source)
  for(const [key,value] of Object.entries(source)){
   if(evidenceHidden.has(key)||value===undefined||value===null||value==='')continue
   if(Array.isArray(value)){
    const rows=value.slice(0,12)
    sections.push({key,title:fieldName(key),kind:'array',count:value.length,rows})
   }else if(isRecord(value)&&!visited.has(value)){
    visited.add(value)
    const pairs=readableEntries(value,10)
    if(pairs.length)sections.push({key,title:fieldName(key),kind:'object',pairs})
   }
   if(sections.length>=8)return sections
  }
 }
 return sections
}
function evidenceCardTitle(row:any,index:number,title:string){
 return isRecord(row)?(compactRecordTitle(row)||`${title} ${Number(index)+1}`):`${title} ${Number(index)+1}`
}
function evidenceBriefTitle(item:any){
 const rows=toolEvidenceRows(item)
 if(!rows.length)return '暂无可见记录'
 const title=compactRecordTitle(rows[0])||'业务记录'
 return rows.length>1?`${title} 等 ${rows.length} 条记录`:title
}
function evidenceBriefSummary(item:any){
 const rows=toolEvidenceRows(item)
 if(!rows.length)return '本次查询未返回当前权限范围内的业务记录。'
 const facts=compactRecordFields(rows[0]).map(fact=>`${fact.label} ${fact.value}`)
 return facts.length?facts.join(' · '):`${rows.length} 条记录 · 按当前权限返回`
}
function toolEvidenceRows(item:any){
 const data=item?.data
 if(Array.isArray(data))return data
 if(!isRecord(data))return []
 if(Array.isArray(data.rows))return data.rows
 return Object.values(data).flatMap((value:any)=>isRecord(value)&&Array.isArray(value.rows)?value.rows:[])
}
function toolEvidenceCount(item:any){return toolEvidenceRows(item).length}
function firstToolEvidenceRow(item:any){return toolEvidenceRows(item)[0]}
function openToolEvidence(item:any){selectedEvidence.value={...item,data:toolEvidenceRows(item)}}
function isErpDesignOrdersEvidence(item:any){return item?.tool==='erp_design_query_orders'}
function openErpDesignOrders(item:any){erpDesignOrdersDialog.value={...item,data:toolEvidenceRows(item)}}
function erpDesignOrdersFromRun(run:any){
 const traceEvidence=runTrace(run).filter((item:any)=>item?.type==='tool')
 const resultEvidence=Array.isArray(run?.result?.evidence)?run.result.evidence:[]
 const evidence=[...traceEvidence,...resultEvidence]
 for(let index=evidence.length-1;index>=0;index--)if(isErpDesignOrdersEvidence(evidence[index]))return evidence[index]
 return null
}
function toolSearchStatus(item:any){
 if(item?.activated?.length)return {label:'本轮已启用',summary:item.activated.map((name:string)=>capabilityName(name)).join('、')}
 if(item?.matches?.length)return {label:'能力已就绪',summary:'匹配能力已处于启用状态'}
 return {label:'能力匹配',summary:'未找到匹配能力'}
}
function hasBusinessFactHighlights(row:any){
 const analysis=row?.analysis
 return Boolean(analysis?.kickoff_lifecycle||analysis?.tasks?.length||analysis?.revision_impact||analysis?.plan_change_candidates?.length)
}
function runTrace(run:any){
 if(Array.isArray(run.trace)&&run.trace.length)return run.trace
 const items:any[]=[]
 for(const e of run.result?.evidence??[])items.push({type:'tool',...e})
 if(run.result?.summary||run.result?.message||run.result?.suggestions?.length||run.result?.error_code){
  items.push({type:'final',summary:run.result?.summary,message:run.result?.message,suggestions:run.result?.suggestions??[],error_code:run.result?.error_code})
 }
 return items
}
function runProcessTrace(run:any){return runTrace(run).filter((item:any)=>!['final','proposal_resolution'].includes(item.type))}
function streamedTextKey(run:any,item:any,index:number){return `${run.id}:${item?.message_key||`index:${index}`}`}
function stopStreamedTextAnimation(key:string){
 const frame=streamedTextFrames.get(key)
 if(frame!==undefined)window.cancelAnimationFrame(frame)
 streamedTextFrames.delete(key)
 streamedTextNextRevealAt.delete(key)
}
const graphemeSegmenter=typeof Intl!=='undefined'&&(Intl as any).Segmenter?new (Intl as any).Segmenter('zh-CN',{granularity:'grapheme'}):null
function firstGrapheme(value:string){
 if(!value)return ''
 if(graphemeSegmenter){
  const part=graphemeSegmenter.segment(value)[Symbol.iterator]().next().value
  if(part?.segment)return String(part.segment)
 }
 return Array.from(value)[0]??''
}
function animateStreamedText(key:string){
 if(streamedTextFrames.has(key)||typeof window==='undefined')return
 const reveal=(timestamp:number)=>{
  streamedTextFrames.delete(key)
  const target=streamedTextTargets.get(key)??''
  const current=streamedText.value[key]??''
  if(current===target){
   streamedTextNextRevealAt.delete(key)
   if(streamedTextCompletedKeys.has(key)){streamedTextCompletedKeys.delete(key);streamedTextLiveKeys.delete(key)}
   return
  }
  if(!target.startsWith(current)){
   streamedText.value={...streamedText.value,[key]:''}
   streamedTextNextRevealAt.set(key,timestamp)
   streamedTextFrames.set(key,window.requestAnimationFrame(reveal))
   return
  }
  const due=streamedTextNextRevealAt.get(key)??timestamp
  if(timestamp+.5<due){streamedTextFrames.set(key,window.requestAnimationFrame(reveal));return}
  const next=firstGrapheme(target.slice(current.length))
  streamedText.value={...streamedText.value,[key]:current+next}
  streamedTextNextRevealAt.set(key,timestamp+16)
  streamedTextFrames.set(key,window.requestAnimationFrame(reveal))
 }
 streamedTextFrames.set(key,window.requestAnimationFrame(reveal))
}
function processMessageText(run:any,item:any,index:number){
 const key=streamedTextKey(run,item,index)
 if(!item.streaming&&!streamedTextLiveKeys.has(key)&&!streamedTextCompletedKeys.has(key))return item.text??''
 return streamedText.value[key]??''
}
watch(runs,(currentRuns)=>{
 const activeKeys=new Set<string>()
 for(const run of currentRuns){
  runProcessTrace(run).forEach((item:any,index:number)=>{
   if(item.type!=='message')return
   const key=streamedTextKey(run,item,index)
   activeKeys.add(key)
   const target=String(item.text??'')
   if(!item.streaming){
    streamedTextTargets.set(key,target)
    if(streamedTextLiveKeys.has(key)){
     streamedTextCompletedKeys.add(key)
     animateStreamedText(key)
    }else{
     if(streamedText.value[key]!==target)streamedText.value={...streamedText.value,[key]:target}
     stopStreamedTextAnimation(key)
    }
    return
   }
   streamedTextLiveKeys.add(key)
   streamedTextCompletedKeys.delete(key)
   const shown=streamedText.value[key]??''
   if(!target.startsWith(shown))streamedText.value={...streamedText.value,[key]:''}
   streamedTextTargets.set(key,target)
   animateStreamedText(key)
  })
 }
 for(const key of streamedTextTargets.keys())if(!activeKeys.has(key)){
  streamedTextTargets.delete(key);streamedTextLiveKeys.delete(key);streamedTextCompletedKeys.delete(key);stopStreamedTextAnimation(key)
 }
},{deep:true})
watch(runs,(currentRuns,previousRuns=[])=>{
 const previousById=new Map(previousRuns.map((run:any)=>[run.id,run]))
 for(const run of currentRuns){
  if(openedErpDesignRunIds.has(run.id)||!shouldOpenErpDesignPreview(previousById.get(run.id),run))continue
  const session=erpDesignSessionFromRun(run)
  if(!session)continue
  openedErpDesignRunIds.add(run.id)
  openErpDesignPreview(session)
 }
},{deep:true})
watch(runs,currentRuns=>{void loadErpDesignImportStatuses(currentRuns)},{deep:true,immediate:true})
function runFinalTrace(run:any){
 const trace=runTrace(run)
 for(let i=trace.length-1;i>=0;i--)if(trace[i]?.type==='final')return trace[i]
 return null
}
function runPendingProposal(run:any){
 if(['QUEUED','RUNNING'].includes(run.status))return undefined
 return runProcessTrace(run).find((item:any)=>item.proposal&&item.id&&!item.proposal_decision&&!confirmedProposalSteps.value[item.id])
}
function runPendingProposals(run:any){
 if(['QUEUED','RUNNING'].includes(run.status))return []
 return runProcessTrace(run).filter((item:any)=>item.proposal&&item.id&&!item.proposal_decision&&!confirmedProposalSteps.value[item.id])
}
function runResolvedProposals(run:any){
 return runProcessTrace(run).filter((item:any)=>item.proposal&&item.id&&(item.proposal_decision||confirmedProposalSteps.value[item.id]))
}
function proposalResolutionFor(run:any,stepId:string){
 return runTrace(run).find((item:any)=>item.type==='proposal_resolution'&&item.proposal_step_id===stepId)
}
function runFinalTraces(run:any){return runTrace(run).filter((item:any)=>item.type==='final')}
function visibleRunFinalTrace(run:any){return runFinalTrace(run)}
const pendingApprovalContext=computed(()=>{
 for(const run of [...runs.value].reverse()){
  const item=runPendingProposal(run)
  if(item)return {run,item}
 }
 return null
})
function setProposalConfirmed(stepId:string,confirmed=true){
 if(!stepId)return
 confirmedProposalSteps.value={...confirmedProposalSteps.value,[stepId]:confirmed}
}
function runProcessExpanded(run:any){
 if(['QUEUED','RUNNING'].includes(run.status))return true
 const chosen=runProcessOpen.value[run.id]
 if(chosen!==undefined)return chosen
 return false
}
function toggleRunProcess(run:any){runProcessOpen.value={...runProcessOpen.value,[run.id]:!runProcessExpanded(run)}}
function runDurationSeconds(run:any){
 if(run.status==='RUNNING'||run.status==='QUEUED')return Number(run.progress?.elapsed_seconds||0)
 if(Number.isFinite(Number(run.duration_seconds)))return Math.max(0,Number(run.duration_seconds))
 const ms=Number(run.progress?.model_elapsed_ms||0)
 return ms>0?Math.max(1,Math.round(ms/1000)):0
}
function durationText(seconds:number){
 const total=Math.max(0,Math.floor(seconds||0)),m=Math.floor(total/60),s=total%60
 if(m>0)return `${m}分${s}秒`
 return `${s}秒`
}
function formatTokenCount(value:any){
 const tokens=Math.max(0,Number(value)||0)
 if(tokens>=1000000)return `${(tokens/1000000).toFixed(1)}M`
 if(tokens>=1000)return `${(tokens/1000).toFixed(1)}k`
 return String(Math.round(tokens))
}
function contextPercentText(usage:any){return `${Math.min(100,Math.max(0,Number(usage?.used_percent||0))).toFixed(Number(usage?.used_percent||0)%1?1:0)}%`}
function contextUsageTitle(usage:any){return `剩余 ${formatTokenCount(usage?.remaining_tokens)} tokens · 已用 ${contextPercentText(usage)}`}
function runDurationLabel(run:any){return `用时 ${durationText(runDurationSeconds(run))}`}
function messageTime(value:any,offsetSeconds=0){
 const date=new Date(value)
 if(!Number.isFinite(date.getTime()))return ''
 date.setSeconds(date.getSeconds()+Math.max(0,Math.floor(offsetSeconds||0)))
 return new Intl.DateTimeFormat('zh-CN',{timeZone:'Asia/Shanghai',hour:'2-digit',minute:'2-digit',hour12:false}).format(date)
}
function assistantCopyText(run:any){
 const final=visibleRunFinalTrace(run)
 const parts:string[]=[]
 if(final?.summary||final?.message)parts.push(final.summary??final.message)
 if(final?.suggestions?.length)parts.push(final.suggestions.map((s:string)=>`• ${s}`).join('\n'))
 if(parts.length)return parts.join('\n\n')
 return runProcessTrace(run).filter((item:any)=>item.type==='message'&&item.text).map((item:any)=>item.text).join('\n\n')
}
async function copyMessage(text:string,key:string){
 const value=(text||'').trim()
 if(!value)return
 try{await navigator.clipboard.writeText(value)}catch{
  const area=document.createElement('textarea')
  area.value=value;area.setAttribute('readonly','');area.style.position='fixed';area.style.opacity='0'
  document.body.appendChild(area);area.select();document.execCommand('copy');document.body.removeChild(area)
 }
 copiedMessage.value=key
 window.setTimeout(()=>{if(copiedMessage.value===key)copiedMessage.value=''},1400)
}
function evidenceTitle(item:any){return item?.proposal?'操作建议':capabilityName(item?.tool||'')}
function stopErpDesignPreviewPolling(){if(erpDesignPreviewPoll!=null){window.clearTimeout(erpDesignPreviewPoll);erpDesignPreviewPoll=null}}
function closeErpDesignPreview(){erpDesignPreviewRequest++;stopErpDesignPreviewPolling();erpDrawingPreviewRow.value=null;erpDesignPreview.value=null;erpDesignPreviewLoading.value=false;erpDesignPreviewError.value='';erpDesignPreviewNotice.value='';erpDesignRepricing.value=false;erpDesignImporting.value=false;erpDesignDuplicateNotice.value=null}
function erpDesignImportReceipt(session:ErpDesignPreviewSession|null|undefined){return session?erpDesignImportReceipts.value[String(session.sessionId)]||null:null}
function setErpDesignImportReceipt(receipt:ErpDesignImportReceipt){erpDesignImportReceipts.value={...erpDesignImportReceipts.value,[String(receipt.sessionId)]:receipt}}
async function loadErpDesignImportStatuses(currentRuns:any[]){
 const sessionIds=[...new Set(currentRuns.flatMap(run=>runTrace(run).map((item:any)=>erpDesignSessionFromTool(item)?.sessionId)).filter((value):value is number=>Boolean(value)))]
 const pending=sessionIds.filter(sessionId=>!loadedErpDesignImportStatuses.has(sessionId))
 if(!pending.length)return
 pending.forEach(sessionId=>loadedErpDesignImportStatuses.add(sessionId))
 try{
  const result=await post('/erp-design-uploads/import-statuses',{session_ids:pending})
  for(const value of Array.isArray(result?.receipts)?result.receipts:[]){
   const receipt=normalizeErpDesignImportReceipt(value)
   if(receipt)setErpDesignImportReceipt(receipt)
  }
 }catch{
  // Import status restoration is supplementary; the order remains usable if it cannot be read.
 }
}
async function loadErpDesignPreview(){
 const current=erpDesignPreview.value
 if(!current)return
 stopErpDesignPreviewPolling()
 const request=++erpDesignPreviewRequest
 erpDesignPreviewLoading.value=!current.previewRows.length
 erpDesignPreviewError.value=''
 try{
  const result=await post('/erp-design-uploads/status',{session_id:current.sessionId,include_result:true})
  if(request!==erpDesignPreviewRequest||erpDesignPreview.value?.sessionId!==current.sessionId)return
  const merged=normalizeErpDesignPreview(result,erpDesignPreview.value)
  if(!merged)throw new Error('ERP 未返回可读取的清单数据')
  erpDesignPreview.value=merged
  if(merged.drawingProcessing){
   erpDesignPreviewPoll=window.setTimeout(()=>void loadErpDesignPreview(),1500)
  }
 }catch(e:any){
  if(request===erpDesignPreviewRequest)erpDesignPreviewError.value=e?.message||'读取 ERP 清单失败'
 }finally{
  if(request===erpDesignPreviewRequest)erpDesignPreviewLoading.value=false
 }
}
function openErpDesignPreview(session:ErpDesignPreviewSession|null){
 if(!session)return
 erpDesignPreviewRequest++
 stopErpDesignPreviewPolling()
 erpDrawingPreviewRow.value=null
 erpDesignPreviewNotice.value=''
 erpDesignDuplicateNotice.value=null
 const draft=erpDesignDrafts.get(session.sessionId)
 erpDesignPreview.value=draft?{...session,...draft}:session
 erpDesignPreviewError.value=''
 void loadErpDesignPreview()
}
function openErpDesignPreviewFromTool(item:any){openErpDesignPreview(erpDesignSessionFromTool(item))}
function updateErpDesignDraft(draft:{expectedDate:string;remark:string}){
 const current=erpDesignPreview.value
 if(!current)return
 erpDesignDrafts.set(current.sessionId,draft)
 erpDesignPreview.value={...current,...draft}
}
function updateErpDesignRows(rows:ErpDesignRow[]){
 const current=erpDesignPreview.value
 if(!current)return
 erpDesignDuplicateNotice.value=null
 erpDesignPreview.value={...current,previewRows:rows,rowCount:rows.length}
}
async function repriceErpDesignRows(rows:ErpDesignRow[]){
 const current=erpDesignPreview.value
 if(!current||erpDesignRepricing.value)return
 erpDesignRepricing.value=true
 erpDesignPreviewNotice.value=''
 erpDesignDuplicateNotice.value=null
 try{
  const result=await post('/erp-design-uploads/reprice',{
   session_id:current.sessionId,
   sheet_type:current.sheetType,
   mold_code:current.moldCode||null,
   preview_rows:rows,
  })
  const normalized=normalizeErpDesignPreview(result,{...current,previewRows:rows,rowCount:rows.length})
  if(!normalized)throw new Error('ERP 未返回有效核算结果')
  erpDesignPreview.value=normalized
  erpDesignPreviewNotice.value='已按 ERP 当前规则重新核算价格。'
 }catch(e:any){erpDesignPreviewError.value=e?.message||'重新核算价格失败'}
 finally{erpDesignRepricing.value=false}
}
function erpDesignRowKey(row:ErpDesignRow,index=-1){return String(row.drawing_row_key??row.drawingRowKey??row.rowIndex??row.row_index??row.item_code_full??row.itemCodeFull??index)}
async function repriceErpDesignRow(row:ErpDesignRow){
 const current=erpDesignPreview.value
 if(!current)return
 const rows=current.previewRows.map(item=>({...item}))
 let index=rows.findIndex((item,rowIndex)=>erpDesignRowKey(item,rowIndex)===erpDesignRowKey(row,rowIndex))
 if(index<0)index=rows.findIndex(item=>String(item.item_code_full??item.itemCodeFull??'')===String(row.item_code_full??row.itemCodeFull??''))
 if(index<0)return
 const key=`${current.sessionId}:${erpDesignRowKey(rows[index],index)}`
 const token=(erpDesignRowRepriceTokens.get(key)||0)+1
 erpDesignRowRepriceTokens.set(key,token)
 rows[index]={...rows[index],...row,_erp_reprice_loading:true}
 erpDesignPreview.value={...current,previewRows:rows}
 erpDesignPreviewError.value=''
 erpDesignDuplicateNotice.value=null
 try{
  const result=await post('/erp-design-uploads/reprice',{
   session_id:current.sessionId,
   sheet_type:current.sheetType,
   mold_code:current.moldCode||null,
   preview_rows:[rows[index]],
  })
  if(erpDesignRowRepriceTokens.get(key)!==token||erpDesignPreview.value?.sessionId!==current.sessionId)return
  const normalized=normalizeErpDesignPreview(result,{...current,previewRows:[rows[index]],rowCount:1})
  const priced=normalized?.previewRows?.[0]
  if(!priced)throw new Error('ERP 未返回有效核价结果')
  const latest=erpDesignPreview.value
  if(!latest)return
  const mergedRows=latest.previewRows.map((item,rowIndex)=>rowIndex===index?{...item,...priced,_erp_reprice_loading:false}:item)
  erpDesignPreview.value={...latest,previewRows:mergedRows,rowCount:mergedRows.length}
  erpDesignPreviewNotice.value='已按 ERP 当前规则更新该行的规格、重量和价格。'
 }catch(e:any){
  if(erpDesignRowRepriceTokens.get(key)!==token)return
  const latest=erpDesignPreview.value
  if(latest)erpDesignPreview.value={...latest,previewRows:latest.previewRows.map((item,rowIndex)=>rowIndex===index?{...item,_erp_reprice_loading:false}:item)}
 erpDesignPreviewError.value=e?.message||'该行重新核算失败'
 }
}
function erpDesignImportResult(value:any):Record<string,any>{
 const data=value?.data
 return data&&typeof data==='object'&&!Array.isArray(data)?data:(value&&typeof value==='object'?value:{})
}
function erpDesignDuplicateRequestNo(message:string):string{
 return String(message||'').match(/重复上传[：:]\s*([^，,\s]+)/)?.[1]||''
}
function erpDesignDisplayMessage(message:unknown,fallback:string):string{
 const value=String(message||'').trim()
 return value.replace(/^ERP\s+(?:GET|POST|PUT|PATCH|DELETE)\s+\S+\s+failed\s*\(\d+\)\s*:\s*/i,'').trim()||fallback
}
function erpDesignDuplicateFromError(error:any):{message:string;requestNo:string}|null{
 const rawMessage=String(error?.message||'')
 const duplicate=error?.code==='ERP_DUPLICATE_CONFIRMATION_REQUIRED'||/检测到同类型、同模号且明细相同的重复上传|重复上传/.test(rawMessage)
 const message=erpDesignDisplayMessage(rawMessage,'检测到同类型、同模号且明细相同的重复上传。')
 return duplicate?{message,requestNo:erpDesignDuplicateRequestNo(message)}:null
}
function erpDesignDuplicateFromResult(value:any):{message:string;requestNo:string}|null{
 const result=erpDesignImportResult(value)
 if(result.duplicateUpload!==true&&result.duplicate_upload!==true)return null
 const message=erpDesignDisplayMessage(result.message||result.errorMessage||result.error_message,'检测到同类型、同模号且明细相同的重复上传。')
 return {message,requestNo:String(result.requestNo||result.request_no||erpDesignDuplicateRequestNo(message)||'')}
}
async function importErpDesign(payload:{previewRows:ErpDesignRow[];expectedDate:string;remark:string;allowDuplicate:boolean}){
 const current=erpDesignPreview.value
 if(!current||erpDesignImporting.value)return
 if(!payload.expectedDate){erpDesignPreviewError.value='请先填写交期后再确认导入';return}
 erpDesignImporting.value=true
 erpDesignPreviewError.value=''
 erpDesignPreviewNotice.value=''
 try{
  const result=await post('/erp-design-uploads/import',{
   session_id:current.sessionId,
   sheet_type:current.sheetType,
   mold_code:current.moldCode||null,
   preview_rows:payload.previewRows,
   confirm_import:true,
   urgency_level:'normal',
   expected_date:payload.expectedDate,
   remark:payload.remark||null,
   allow_duplicate:payload.allowDuplicate,
  })
  const duplicate=erpDesignDuplicateFromResult(result)
  if(duplicate&&!payload.allowDuplicate){
   erpDesignDuplicateNotice.value=duplicate
   erpDesignPreviewNotice.value='检测到重复上传，请核对后决定是否继续导入。'
   return
  }
  const response=erpDesignImportResult(result)
  const requestNo=String(response.requestNo??response.request_no??'').trim()
  erpDesignDuplicateNotice.value=null
  erpDesignDrafts.delete(current.sessionId)
  setErpDesignImportReceipt({
   sessionId:current.sessionId,
   requestNo,
   message:requestNo?`已成功导入 ERP，回执单号：${requestNo}`:'已成功导入 ERP，并收到导入回执。',
   importedAt:new Date().toISOString(),
  })
  closeErpDesignPreview()
  await nextTick()
  chatScroll.value?.scrollTo({top:chatScroll.value.scrollHeight,behavior:'smooth'})
 }catch(e:any){
  const duplicate=payload.allowDuplicate?null:erpDesignDuplicateFromError(e)
  if(duplicate){
   erpDesignDuplicateNotice.value=duplicate
   erpDesignPreviewNotice.value='检测到重复上传，请核对后决定是否继续导入。'
   return
  }
  erpDesignPreviewError.value=e?.message||'确认导入失败'
 }finally{erpDesignImporting.value=false}
}
function openErpDrawingPreview(row:ErpDesignRow){erpDrawingPreviewRow.value=row}
function fail(message:string){error.value=message}
async function refresh(){
 const [conversationResult,noticeResult,approvalResult,approvalWorkItemResult,capabilityResult]=await Promise.allSettled([
  api('/conversations'),api('/notifications'),api('/approvals'),api('/approval-work-items'),api('/capabilities')
 ])
 if(conversationResult.status==='fulfilled') conversations.value=conversationResult.value
 if(noticeResult.status==='fulfilled') notices.value=noticeResult.value
 if(approvalResult.status==='fulfilled') approvals.value=approvalResult.value
 if(approvalWorkItemResult.status==='fulfilled') approvalWorkItems.value=approvalWorkItemResult.value
 if(capabilityResult.status==='fulfilled') capabilities.value=capabilityResult.value
 const failed=[conversationResult,noticeResult,approvalResult,approvalWorkItemResult,capabilityResult].find(result=>result.status==='rejected')
 if(failed?.status==='rejected') throw failed.reason
}
async function loadProduct(){
 const loaded=await api('/product')
 if(loaded.id!==__DOMAIN_PACK_ID__)throw new Error(`业务包配置不一致：前端 ${__DOMAIN_PACK_ID__}，后端 ${loaded.id||'unknown'}`)
 const installedTabs=new Set(initialProduct.workspace_tabs.map((tab:any)=>tab.key))
 product.value={...loaded,workspace_tabs:Array.isArray(loaded.workspace_tabs)?loaded.workspace_tabs.filter((tab:any)=>installedTabs.has(tab.key)):[]}
 document.title=`${product.value.product_name} · ${product.value.display_name}`
}
async function loadModelProfiles(){
 if(!me.value?.super_admin){modelProfiles.value=[];activeModelProfileId.value='';return}
 const config=await api('/model-config')
 modelProfiles.value=Array.isArray(config.profiles)?config.profiles:[]
 activeModelProfileId.value=String(config.active_profile_id||'')
 modelName.value=config.model||'未配置模型'
 modelLimits.value={context_window:config.context_window,max_output_tokens:config.max_output_tokens}
}
async function switchModelProfile(profile:any){
 if(modelSwitchingId.value)return
 if(String(profile.id)===activeModelProfileId.value){modelPopoverOpen.value=false;return}
 modelSwitchingId.value=String(profile.id)
 try{
  const config=await post(`/model-profiles/${profile.id}/activate`)
  modelProfiles.value=config.profiles||[];activeModelProfileId.value=String(config.active_profile_id||'')
  modelName.value=config.model||'未配置模型';modelLimits.value={context_window:config.context_window,max_output_tokens:config.max_output_tokens}
  modelPopoverOpen.value=false
 }catch(e:any){fail(e.message)}finally{modelSwitchingId.value=''}
}
async function handleProposalConfirmed(stepId:string,runId=''){
 setProposalConfirmed(stepId,true)
 if(runId)runProcessOpen.value={...runProcessOpen.value,[runId]:false}
 await refresh()
 if(conversation.value)runs.value=await api(`/conversations/${conversation.value}/runs`)
}
async function handleProposalDismissed(stepId:string,runId=''){
 await handleProposalConfirmed(stepId,runId)
}
async function handleCurrentProposalDecision(dismissed=false){
 const context=pendingApprovalContext.value
 if(!context)return
 if(dismissed)await handleProposalDismissed(context.item.id,context.run.id)
 else await handleProposalConfirmed(context.item.id,context.run.id)
}
async function restore(){const response=await api('/me');me.value=response.user;permissions.value=response.permissions;modelName.value=response.model??'未配置模型';modelLimits.value=response.model_limits||modelLimits.value;const legacy=legacyStorageKeys(me.value.id);const savedMode=localStorage.getItem(approvalModeStorageKey())??(legacy.approvalMode?localStorage.getItem(legacy.approvalMode):null);approvalPermissionMode.value=savedMode==='delegated_auto'?'delegated_auto':'ask';await Promise.all([refresh(),loadModelProfiles()]);try{const savedLayout=localStorage.getItem(productStoragePrefix()+'.layout.'+me.value.id)??(legacy.layout?localStorage.getItem(legacy.layout):null);const layout=JSON.parse(savedLayout??'{}');width.value=Math.max(MIN_WORKSPACE_WIDTH,Math.min(layout.width??DEFAULT_WORKSPACE_WIDTH,window.innerWidth-480));sidebarWidth.value=Math.max(190,Math.min(layout.sidebarWidth??DEFAULT_SIDEBAR_WIDTH,420));expanded.value=false;panel.value=''}catch{}}
onMounted(async()=>{try{await loadProduct();await restore()}catch(e:any){fail(e.message||'工作台初始化失败')}finally{loading.value=false}})
async function login(){busy.value=true;error.value='';try{await post('/auth/login',{username:username.value,password:password.value});password.value='';await restore()}catch(e:any){fail(e.message)}finally{busy.value=false}}
function clearSessionData(){closeRunEvents();conversationEpoch++;selectedFiles.value=[];workspaceTargets.value={};me.value=null;permissions.value=[];conversations.value=[];runs.value=[];conversationLoading.value=false;detail.value=null;approvals.value=[];initiatedApprovals.value=[];approvalWorkItems.value={copied:[],overdue:[]};notices.value=[];capabilities.value={tools:[],skills:[]};modelProfiles.value=[];activeModelProfileId.value='';modelSwitchingId.value='';prompt.value='';expanded.value=false;full.value=false;conversation.value='';activeConversationTitle.value='';activeConversationArchived.value=false;panel.value='';password.value='';showNotices.value=false;showProfile.value=false;settingsOpen.value=false;erpDesignOrdersDialog.value=null;showSidebarSearch.value=false;contextPopoverOpen.value=false;modelPopoverOpen.value=false;approvalModePopoverOpen.value=false;approvalPermissionMode.value='ask';closeErpDesignPreview();openedErpDesignRunIds.clear();loadedErpDesignImportStatuses.clear();erpDesignImportReceipts.value={};search.value=''}
async function logout(){try{await post('/auth/logout');clearSessionData()}catch(e:any){fail(e.message)}}
function saveLayout(){if(me.value)localStorage.setItem(productStoragePrefix()+'.layout.'+me.value.id,JSON.stringify({width:width.value,sidebarWidth:sidebarWidth.value}))}
async function openPanel(key:string){if(!workspaceTabs.value.some((tab:any)=>tab.key===key))return;panel.value=key;expanded.value=true;saveLayout()}
async function openWorkspaceTarget(link:WorkspaceTarget){
 if(!link?.target||!link?.id)return
 workspaceTargets.value={...workspaceTargets.value,[link.target]:link.id}
 await openPanel(link.target)
}
async function openEvidenceTarget(link:WorkspaceTarget){selectedEvidence.value=null;await openWorkspaceTarget(link)}
function toggleWorkspace(){if(!workspaceTabs.value.length)return;if(expanded.value)collapse();else{if(!panel.value)panel.value=String(workspaceTabs.value[0].key);expanded.value=true}}
function collapse(){expanded.value=false;full.value=false;saveLayout()}
async function markApprovalNotificationsRead(id:string){
 const unread=notices.value.filter(n=>!n.read&&n.kind?.startsWith('approval.')&&n.resource_id===id)
 if(!unread.length)return
 await Promise.all(unread.map(n=>post('/notifications/'+n.id+'/read')))
 const readIds=new Set(unread.map(n=>n.id))
 notices.value=notices.value.map(n=>readIds.has(n.id)?{...n,read:true}:n)
}
async function openApproval(id:string){try{showNotices.value=false;detail.value=await api('/approvals/'+id);await openPanel('approvals');await markApprovalNotificationsRead(id)}catch(e:any){fail(e.message)}}
async function changed(){try{await refresh();if(detail.value)detail.value=await api('/approvals/'+detail.value.id)}catch(e:any){fail(e.message)}}
async function selectConversation(id:string,title='',archived=false){
 erpDesignOrdersDialog.value=null
 const changed=conversation.value!==id
 if(changed){conversationEpoch++;selectedFiles.value=[];runs.value=[];closeErpDesignPreview();collapse()}
 const epoch=conversationEpoch
 conversation.value=id;activeConversationTitle.value=title;activeConversationArchived.value=archived;prompt.value='';conversationLoading.value=true
 try{
  const [loadedRuns,conversationFiles]=await Promise.all([api(`/conversations/${id}/runs`),api(`/conversations/${id}/files`)])
  if(epoch!==conversationEpoch||conversation.value!==id)return
  runs.value=loadedRuns
  const attachedFileIds=new Set(loadedRuns.flatMap((run:any)=>(run.files||[]).map((file:any)=>file.id)))
  selectedFiles.value=conversationFiles.filter((file:any)=>!attachedFileIds.has(file.id))
 }catch(e:any){if(epoch===conversationEpoch&&conversation.value===id)fail(e.message)}
 finally{if(epoch===conversationEpoch&&conversation.value===id)conversationLoading.value=false}
}
async function toggleConversationPin(c:any,event?:Event){event?.stopPropagation();try{await post(`/conversations/${c.id}/pin`);await refresh()}catch(e:any){fail(e.message)}}
async function archiveConversation(c:any,event?:Event){event?.stopPropagation();try{await post(`/conversations/${c.id}/archive`);if(conversation.value===c.id)newConversation();await refresh()}catch(e:any){fail(e.message)}}
async function openArchivedConversation(c:any){settingsOpen.value=false;showNotices.value=false;await selectConversation(c.id,c.title,true)}
function newConversation(){erpDesignOrdersDialog.value=null;conversationEpoch++;selectedFiles.value=[];conversation.value='';activeConversationTitle.value='';activeConversationArchived.value=false;runs.value=[];conversationLoading.value=false;prompt.value='';detail.value=null;closeErpDesignPreview();collapse()}
async function send(){if(activeConversationArchived.value){fail('归档会话只可查看，请先在设置中取消归档再继续发送');return}if(!prompt.value.trim()||busy.value||uploading.value)return;busy.value=true;error.value='';try{const r=await post('/runs',{prompt:prompt.value,conversation_id:conversation.value||null,file_ids:selectedFiles.value.map(f=>f.id),agent_permission_mode:approvalPermissionMode.value});selectedFiles.value=[];prompt.value='';conversation.value=r.conversation_id;activeConversationArchived.value=false;await refresh();await selectConversation(r.conversation_id)}catch(e:any){fail(e.message)}finally{busy.value=false}}
async function stopActiveRun(){const run=activeRun.value;if(!run||busy.value)return;busy.value=true;error.value='';try{await post('/runs/'+run.id+'/cancel');if(conversation.value)runs.value=await api(`/conversations/${conversation.value}/runs`)}catch(e:any){fail(e.message)}finally{busy.value=false}}
async function uploadFiles(event:Event){
 const input=event.target as HTMLInputElement,files=Array.from(input.files||[]);input.value=''
 if(!files.length||uploading.value)return
 if(files.length+selectedFiles.value.length>10){fail('每次任务最多关联10个附件');return}
 uploading.value=true;const epoch=conversationEpoch;let target=conversation.value
 try{for(const file of files){const query=new URLSearchParams({filename:file.name,request_key:crypto.randomUUID()});if(target)query.set('conversation_id',target)
  const result=await api('/files?'+query.toString(),{method:'POST',headers:{'Content-Type':'application/octet-stream'},body:file});target=result.conversation_id
  if(epoch===conversationEpoch){conversation.value=target;selectedFiles.value.push(result)}
 }await refresh()}catch(e:any){fail(e.message)}finally{uploading.value=false}
}
async function notice(n:any){try{await post('/notifications/'+n.id+'/read');showNotices.value=false;await refresh();if(n.kind?.startsWith('approval.'))await openApproval(n.resource_id);else{const target=notificationWorkspaceTarget(n);if(target)await openWorkspaceTarget(target);else await openNotices()}}catch(e:any){fail(e.message)}}
function resizeSession(move:(event:PointerEvent)=>void){document.body.classList.add('layout-resizing');const end=()=>{document.body.classList.remove('layout-resizing');window.removeEventListener('pointermove',move);window.removeEventListener('pointerup',end);window.removeEventListener('pointercancel',end);saveLayout()};window.addEventListener('pointermove',move);window.addEventListener('pointerup',end);window.addEventListener('pointercancel',end)}
function beginResize(e:PointerEvent){if(e.button!==0)return;const x=e.clientX,start=width.value;resizeSession((ev)=>{width.value=Math.max(MIN_WORKSPACE_WIDTH,Math.min(window.innerWidth-480,start+x-ev.clientX))})}
function resizeBy(delta:number){width.value=Math.max(MIN_WORKSPACE_WIDTH,Math.min(window.innerWidth-480,width.value+delta));saveLayout()}
function resetWorkspaceWidth(){width.value=DEFAULT_WORKSPACE_WIDTH;saveLayout()}
function beginSidebarResize(e:PointerEvent){if(e.button!==0||sidebarCollapsed.value)return;const x=e.clientX,start=sidebarWidth.value;resizeSession((ev)=>{sidebarWidth.value=Math.max(190,Math.min(420,start+ev.clientX-x))})}
function resizeSidebarBy(delta:number){sidebarWidth.value=Math.max(190,Math.min(420,sidebarWidth.value+delta));saveLayout()}
function resetSidebarWidth(){sidebarWidth.value=DEFAULT_SIDEBAR_WIDTH;saveLayout()}
let polling=false,pollTick=0,runPolling=false
const timer=setInterval(async()=>{pollTick++;if(!me.value||polling||(!running.value&&pollTick%4!==0))return;polling=true;try{const info=await api('/me');if(info.user.authorization_hash!==me.value.authorization_hash){me.value=info.user;permissions.value=info.permissions;detail.value=null;runs.value=[];expanded.value=false;panel.value='';await refresh();error.value='权限已更新，相关材料已清理，请重新查询'}if(pollTick%12===0)[notices.value,approvals.value,initiatedApprovals.value,approvalWorkItems.value]=await Promise.all([api('/notifications'),api('/approvals'),api('/approvals/initiated'),api('/approval-work-items')])}catch(e:any){if(e.status===401){clearSessionData();error.value='登录已失效，请重新登录'}}finally{polling=false}},1000)
const runTimer=setInterval(async()=>{if(!me.value||runEventsReady.value||!running.value||!conversation.value||runPolling)return;runPolling=true;const id=conversation.value,epoch=conversationEpoch;try{const latestRuns=await api(`/conversations/${id}/runs`);if(conversation.value===id&&conversationEpoch===epoch)runs.value=latestRuns}catch(e:any){if(e.status===401){clearSessionData();error.value='登录已失效，请重新登录'}}finally{runPolling=false}},1000)
onUnmounted(()=>{clearInterval(timer);clearInterval(runTimer);closeRunEvents()})
</script>
<template>
<div v-if="loading" class="loading-screen">正在连接工作台…</div>
<main v-else-if="!me" class="login-screen"><form class="login-box" @submit.prevent="login"><div class="brand-symbol"><Bot :size="30"/></div><h1>{{product.product_name}}</h1><p>{{product.display_name}} · {{product.tagline}}</p><label>用户名<input v-model="username" autocomplete="username" required autofocus/></label><label>密码<input v-model="password" type="password" autocomplete="current-password" required/></label><p v-if="error" class="error" role="alert">{{error}}</p><button class="primary" :disabled="busy">{{busy?'正在登录…':'登录工作台'}}<ArrowRight :size="16"/></button><small>统一智能体入口 · 你的权限决定可用能力</small></form></main>
<SettingsPage v-else-if="settingsOpen" :me="me" :permissions="permissions" :capabilities="capabilities" :model-name="modelName" :color-theme="colorTheme" :initial-page="settingsInitialPage" @theme-change="changeTheme" @model-updated="modelName=$event" @open-conversation="openArchivedConversation" @close="settingsOpen=false;restore().catch(e=>fail(e.message))" @error="fail"/>
<main v-else class="workbench" :class="{'panel-full':full&&workspaceOpen,'sidebar-collapsed':sidebarCollapsed,'workspace-open':workspaceOpen&&!full}" :style="{'--sidebar-width':sidebarWidth+'px'}">
  <aside class="sidebar"><div class="brand"><div class="brand-title"><strong>{{product.product_name}}</strong></div><div class="brand-actions"><button class="brand-action" title="搜索历史对话" aria-label="搜索历史对话" :aria-expanded="showSidebarSearch||!!search" @click="toggleSidebarSearch"><Search :size="15"/></button><button ref="noticeButton" class="brand-action" title="待处理" aria-label="待处理" :aria-expanded="showNotices" @click="showNotices?showNotices=false:openNotices()"><Bell :size="15"/><span v-if="noticeCount" class="brand-dot"/></button><button class="brand-action sidebar-toggle-button" :title="sidebarCollapsed?'展开左侧会话':'折叠左侧会话'" :aria-label="sidebarCollapsed?'展开左侧会话':'折叠左侧会话'" :aria-pressed="sidebarCollapsed" @click="sidebarCollapsed=!sidebarCollapsed"><PanelRight :size="15"/></button></div></div><label v-if="showSidebarSearch||search" class="search sidebar-search"><Search :size="16"/><input ref="sidebarSearchInput" v-model="search" placeholder="搜索历史对话" aria-label="搜索历史对话"/></label><button class="new-chat" @click="newConversation"><Plus :size="18"/><span>新对话</span></button><small class="sidebar-label">最近对话</small><div class="conversation-list"><div v-for="c in filteredConversations" :key="c.id" class="conversation-row" :class="{active:conversation===c.id,pinned:c.pinned}"><button class="conversation-main" :aria-current="conversation===c.id?'page':undefined" @click="selectConversation(c.id,c.title)"><MessageSquare :size="15"/><span>{{c.title}}</span></button><span v-if="c.status==='WAITING_APPROVAL'" class="conversation-status">等待批准</span><div class="conversation-actions"><button type="button" class="conversation-action" :title="c.pinned?'取消置顶':'置顶聊天'" :aria-label="c.pinned?'取消置顶：'+c.title:'置顶聊天：'+c.title" @click.stop="toggleConversationPin(c,$event)"><Pin :size="13"/></button><button type="button" class="conversation-action" :title="'归档聊天'" :aria-label="'归档聊天：'+c.title" @click.stop="archiveConversation(c,$event)"><Archive :size="13"/></button></div></div><p v-if="!conversations.length" class="muted small">开始一个任务，对话会保存在这里。</p><p v-else-if="!filteredConversations.length" class="muted small">没有匹配的对话。</p></div><div class="profile-area"><button ref="profileButton" class="profile-entry" aria-label="账号菜单" aria-haspopup="menu" :aria-expanded="showProfile" @click="showProfile=!showProfile;showNotices=false"><span class="avatar"><img v-if="me.avatar_url" :src="me.avatar_url" alt=""/><template v-else>{{me.display_name[0]}}</template></span><span class="profile-info"><strong>{{me.display_name}}</strong><small>{{me.department||'未设置部门'}}</small></span></button>
<div v-if="showProfile" class="profile-dismiss" @click="closeProfile"/>
<div v-if="showProfile" class="profile-menu" role="menu" aria-label="账号选项"><p><strong>{{me.display_name}}</strong><small class="muted">{{me.username}}</small></p><button role="menuitem" @click="openSettings"><Settings :size="17"/>设置</button><button role="menuitem" @click="logout"><LogOut :size="17"/>退出登录</button></div></div></aside>
  <div v-if="!sidebarCollapsed" class="sidebar-resize-handle" role="separator" tabindex="0" aria-label="调整左侧边栏宽度" aria-orientation="vertical" title="拖拽调整宽度，双击恢复默认宽度" @pointerdown="beginSidebarResize" @dblclick="resetSidebarWidth" @keydown.left.prevent="resizeSidebarBy(-10)" @keydown.right.prevent="resizeSidebarBy(10)"/>
  <section class="chat"><header class="chat-header"><div class="chat-title"><button v-if="sidebarCollapsed" class="icon-button chat-sidebar-toggle" aria-label="展开左侧会话" title="展开左侧会话" @click="sidebarCollapsed=false"><PanelRight :size="15"/></button><strong>{{currentTitle}}</strong></div><div class="chat-header-actions"><button v-if="workspaceTabs.length&&!workspaceOpen" class="icon-button" aria-label="展开工作区" title="展开工作区" :aria-expanded="workspaceOpen" @click="toggleWorkspace"><PanelRight :size="15"/></button></div></header><div ref="chatScroll" class="chat-scroll" aria-live="polite">
    <div v-if="conversationLoading" class="conversation-loading" role="status"><span class="pulse"/>正在打开会话…</div>
    <section v-else-if="!runs.length&&selectedFiles.length&&conversation" class="draft-conversation" aria-label="待发送附件会话">
      <div class="draft-conversation-card">
        <div class="draft-conversation-head"><span><Paperclip :size="18"/></span><div><strong>附件已进入当前会话</strong><p class="muted">文件已保存。请在下方输入需要核对的问题，然后发送任务。</p></div></div>
        <FileMaterial v-for="file in selectedFiles" :key="file.id" :file="file" @error="fail"/>
      </div>
    </section>
    <WelcomePanel v-else-if="!runs.length" :capabilities="capabilities" @prompt="prompt=$event"/>
    <article v-for="run in runs" :key="run.id" class="conversation-turn">
      <div class="message-block user-message-block">
        <div class="user-message">{{run.prompt}}<FileMaterial v-for="file in run.files||[]" :key="file.id" :file="file" @error="fail"/></div>
        <div class="message-actions user-message-actions">
          <button class="message-action" :class="{copied:copiedMessage==='user:'+run.id}" :title="copiedMessage==='user:'+run.id?'已复制':'复制消息'" :aria-label="copiedMessage==='user:'+run.id?'用户消息已复制':'复制用户消息'" @click="copyMessage(run.prompt,'user:'+run.id)"><Check v-if="copiedMessage==='user:'+run.id" :size="14"/><Copy v-else :size="14"/></button>
          <span v-if="copiedMessage==='user:'+run.id" class="copy-feedback">已复制</span>
          <span>{{messageTime(run.created_at)}}</span>
        </div>
      </div>
      <div class="agent-answer message-block">
        <div class="answer-body">
          <div v-if="runPendingProposal(run)||run.status!=='SUCCEEDED'&&!(run.status==='FAILED'&&visibleRunFinalTrace(run))" class="run-label"><span :class="{pulse:['QUEUED','RUNNING'].includes(run.status)||runPendingProposal(run)}"/>{{runPendingProposal(run)?'等待批准':(runStatus[run.status]??'任务状态待确认')}}<template v-if="['QUEUED','RUNNING'].includes(run.status)"> · 用时 {{durationText(runDurationSeconds(run))}}</template></div>
          <p v-if="run.status==='WAITING_CONFIGURATION'" class="muted">任务已保存。模型尚未完成配置，当前不会生成业务结论。待审批事项仍可从消息通知中查看。</p>
          <div v-if="runTrace(run).length" class="react-trace" aria-label="执行链路">
            <button v-if="runProcessTrace(run).length&&!['QUEUED','RUNNING'].includes(run.status)" type="button" class="run-duration-toggle" :aria-expanded="runProcessExpanded(run)" @click="toggleRunProcess(run)">
              {{runDurationLabel(run)}}<ChevronRight class="run-duration-caret" :size="13"/>
            </button>
            <div v-if="runProcessTrace(run).length" class="run-process" :class="{open:runProcessExpanded(run)}" :aria-hidden="!runProcessExpanded(run)">
              <div class="run-process-inner">
                <template v-for="(item,index) in runProcessTrace(run)" :key="item.id||item.call_id||item.message_key||index">
                  <div v-if="item.type==='message'" class="assistant-prose process-text">
                    <p class="preserve">{{processMessageText(run,item,Number(index))}}</p>
                  </div>
                  <div v-else-if="item.type==='tool_search'" class="agent-tool-row tool-search-row">
                    <span class="agent-tool-icon"><Wrench :size="13"/></span>
                    <span class="agent-tool-name">{{toolSearchStatus(item).label}}</span>
                    <span class="agent-tool-summary">{{toolSearchStatus(item).summary}}</span>
                  </div>
                  <details v-else-if="item.type==='tool'" class="agent-tool-row">
                    <summary class="agent-tool-head">
                      <span class="agent-tool-icon"><Search :size="13"/></span>
                      <span class="agent-tool-name">{{item.proposal?'已准备':'已查询'}} {{capabilityName(item.tool)}}</span>
                      <span class="agent-tool-summary">{{item.proposal?'操作建议':(erpDesignSessionFromTool(item)?`ERP 会话 ${erpDesignSessionFromTool(item)?.sessionId} · ${erpDesignSessionFromTool(item)?.rowCount} 行`:(toolEvidenceCount(item)+' 条记录'))}}<template v-if="!item.proposal&&firstToolEvidenceRow(item)"> · {{compactRecordTitle(firstToolEvidenceRow(item))}}</template></span>
                      <span class="agent-tool-caret"><ChevronRight :size="12"/></span>
                    </summary>
                    <div class="agent-tool-body">
                      <p v-if="item.proposal" class="agent-tool-meta">已生成待确认卡，请在下方正文区域处理。</p>
                      <div v-else-if="erpDesignSessionFromTool(item)" class="agent-tool-main agent-evidence-brief">
                        <span>
                          <strong>{{erpDesignImportReceipt(erpDesignSessionFromTool(item))?'ERP 清单已成功导入':(erpDesignSessionFromTool(item)?.moldCode||erpDesignSessionFromTool(item)?.fileName||'ERP 设计上传清单')}}</strong>
                          <small v-if="erpDesignImportReceipt(erpDesignSessionFromTool(item))">{{erpDesignImportReceipt(erpDesignSessionFromTool(item))?.requestNo?'回执单号 '+erpDesignImportReceipt(erpDesignSessionFromTool(item))?.requestNo:'ERP 已返回成功回执'}}</small>
                          <small v-else>ERP 上传会话 {{erpDesignSessionFromTool(item)?.sessionId}} · {{erpDesignSessionFromTool(item)?.rowCount}} 行解析明细</small>
                        </span>
                        <button :disabled="Boolean(erpDesignImportReceipt(erpDesignSessionFromTool(item)))" :title="erpDesignImportReceipt(erpDesignSessionFromTool(item))?'该订单已成功导入，不能重复提交':'查看订单'" @click="openErpDesignPreviewFromTool(item)">{{erpDesignImportReceipt(erpDesignSessionFromTool(item))?'已导入':'查看订单'}}</button>
                      </div>
                      <div v-else-if="isErpDesignOrdersEvidence(item)" class="agent-tool-main agent-evidence-brief">
                        <span>
                          <strong>ERP 设计订单已就绪</strong>
                          <small>本次查询返回 {{toolEvidenceCount(item)}} 条设计订单 · 数据直接来自 D 盘 ERP</small>
                        </span>
                        <button :disabled="!toolEvidenceCount(item)" @click="openErpDesignOrders(item)">{{toolEvidenceCount(item)?'查看订单':'暂无订单'}}</button>
                      </div>
                      <div v-else class="agent-tool-main agent-evidence-brief">
                        <span>
                          <strong>{{evidenceBriefTitle(item)}}</strong>
                          <small>{{evidenceBriefSummary(item)}}</small>
                        </span>
                        <button v-if="toolEvidenceCount(item)" @click="openToolEvidence(item)">查看详情</button>
                      </div>
                      <div v-if="toolEvidenceLinks(item).length" class="actions">
                        <button v-for="link in toolEvidenceLinks(item)" :key="link.target+':'+link.id" @click="openWorkspaceTarget(link)">{{link.label}}</button>
                      </div>
                    </div>
                  </details>
                  <div v-else-if="item.type==='tool_pending'||item.type==='tool_interrupted'" class="agent-tool-row pending" :class="{interrupted:item.type==='tool_interrupted'}">
                    <span class="agent-tool-icon"><span class="pulse-dot"/></span>
                    <span class="agent-tool-name">{{item.type==='tool_interrupted'?'调用中断':'正在调用'}} {{capabilityName(item.tool)}}</span>
                  </div>
                  <div v-else-if="item.type==='tool_error'" class="agent-tool-row interrupted tool-error-row">
                    <span class="agent-tool-icon"><X :size="13"/></span>
                    <span class="agent-tool-name">未通过 {{capabilityName(item.tool)}}</span>
                    <span class="agent-tool-summary">{{item.message}} · {{item.code}}</span>
                  </div>
                </template>
              </div>
            </div>
            <div v-if="runResolvedProposals(run).length" class="resolved-proposals" aria-label="已处理确认卡">
              <div v-for="item in runResolvedProposals(run)" :key="'resolved:'+item.id" class="resolved-proposal">
                <ProposalCard placement="message" :product-name="product.product_name" :presentation="product.proposal_presentation" :step-id="item.id" :proposal="item.proposal" :decision="item.proposal_decision||'approved'" @status="confirmed=>setProposalConfirmed(item.id,confirmed)" @open="openWorkspaceTarget"/>
                <div v-if="proposalResolutionFor(run,item.id)" class="agent-tool-row proposal-resolution-row">
                  <span class="agent-tool-icon"><Check :size="13"/></span>
                  <span class="proposal-resolution-copy">{{proposalResolutionFor(run,item.id).decision==='approved'?'本人已确认':'本人已取消'}} · {{proposalResolutionFor(run,item.id).receipt?.status==='CONFIRMED'?'执行回执已确认':(proposalResolutionFor(run,item.id).receipt?.status||'决定已记录')}}</span>
                </div>
              </div>
            </div>
            <div v-for="(finalItem,finalIndex) in runFinalTraces(run)" :key="'final:'+finalIndex" class="assistant-prose final">
              <p v-if="finalItem.summary || finalItem.message" class="preserve">{{finalItem.summary ?? finalItem.message}}</p>
              <div v-if="finalItem.error_code" class="run-error-detail" role="note">
                <strong>失败原因</strong><span>{{runFailureReason(finalItem.error_code)}}</span><code>错误码 {{finalItem.error_code}}</code>
              </div>
              <ul v-if="finalItem.suggestions?.length">
                <li v-for="s in finalItem.suggestions" :key="s">{{s}}</li>
              </ul>
            </div>
            <div v-if="erpDesignSessionFromRun(run)" class="erp-design-result-action" :class="{imported:Boolean(erpDesignImportReceipt(erpDesignSessionFromRun(run)))}" :role="erpDesignImportReceipt(erpDesignSessionFromRun(run))?'status':undefined">
              <span>
                <strong>{{erpDesignImportReceipt(erpDesignSessionFromRun(run))?'ERP 清单已成功导入':'ERP 清单已就绪'}}</strong>
                <small v-if="erpDesignImportReceipt(erpDesignSessionFromRun(run))">{{erpDesignImportReceipt(erpDesignSessionFromRun(run))?.requestNo?'回执单号 '+erpDesignImportReceipt(erpDesignSessionFromRun(run))?.requestNo:'ERP 已返回成功回执'}} · 上传会话 {{erpDesignSessionFromRun(run)?.sessionId}}</small>
                <small v-else>上传会话 {{erpDesignSessionFromRun(run)?.sessionId}} · {{erpDesignSessionFromRun(run)?.rowCount}} 行解析明细</small>
              </span>
              <button type="button" :disabled="Boolean(erpDesignImportReceipt(erpDesignSessionFromRun(run)))" :title="erpDesignImportReceipt(erpDesignSessionFromRun(run))?'该订单已成功导入，不能重复提交':'查看订单'" @click="openErpDesignPreview(erpDesignSessionFromRun(run))">{{erpDesignImportReceipt(erpDesignSessionFromRun(run))?'已导入':'查看订单'}}</button>
            </div>
            <div v-if="erpDesignOrdersFromRun(run)" class="erp-design-result-action">
              <span>
                <strong>ERP 设计订单已就绪</strong>
                <small>本次查询返回 {{toolEvidenceCount(erpDesignOrdersFromRun(run))}} 条设计订单 · 点击查看完整表格</small>
              </span>
              <button type="button" :disabled="!toolEvidenceCount(erpDesignOrdersFromRun(run))" @click="openErpDesignOrders(erpDesignOrdersFromRun(run))">{{toolEvidenceCount(erpDesignOrdersFromRun(run))?'查看订单':'暂无订单'}}</button>
            </div>
            <template v-if="!runFinalTraces(run).length&&!runProcessTrace(run).length" v-for="(item,index) in runTrace(run)" :key="item.id||item.call_id||index">
              <div v-if="item.type==='message'" class="assistant-prose">
                <p class="preserve">{{item.text}}</p>
              </div>
            </template>
          </div>
          <div v-if="assistantCopyText(run)" class="message-actions assistant-message-actions">
            <button class="message-action" :class="{copied:copiedMessage==='assistant:'+run.id}" :title="copiedMessage==='assistant:'+run.id?'已复制':'复制消息'" :aria-label="copiedMessage==='assistant:'+run.id?'助手消息已复制':'复制助手消息'" @click="copyMessage(assistantCopyText(run),'assistant:'+run.id)"><Check v-if="copiedMessage==='assistant:'+run.id" :size="14"/><Copy v-else :size="14"/></button>
            <span v-if="copiedMessage==='assistant:'+run.id" class="copy-feedback">已复制</span>
            <span>{{messageTime(run.created_at,run.status==='SUCCEEDED'?runDurationSeconds(run):0)}}</span>
          </div>
        </div>
      </div>
    </article>
  </div>
  <ProposalCard v-if="pendingApprovalContext" placement="composer" :product-name="product.product_name" :presentation="product.proposal_presentation" :step-id="pendingApprovalContext.item.id" :proposal="pendingApprovalContext.item.proposal" @confirmed="handleCurrentProposalDecision(false)" @dismissed="handleCurrentProposalDecision(true)" @open="openWorkspaceTarget"/>
  <form v-else class="composer" @submit.prevent="send">
    <div v-if="selectedFiles.length" class="composer-files"><span v-for="file in selectedFiles" :key="file.id">{{file.filename}}<button type="button" class="icon-button" :aria-label="'取消本次关联附件：'+file.filename" @click="selectedFiles=selectedFiles.filter(f=>f.id!==file.id)"><X :size="13"/></button></span></div>
    <p v-if="uploading" role="status" class="muted small">正在保存上传原件…</p>
    <textarea v-model="prompt" placeholder="输入任务或问题…" aria-label="输入任务或问题" rows="3" @keydown.enter.exact.prevent="send"/>
    <div class="composer-toolbar">
      <input ref="fileInput" hidden type="file" accept=".pdf,.png,.jpg,.jpeg,.docx,.xlsx,.xls,.csv,.dxf,.dwg,.prt" multiple aria-label="选择上传附件" @change="uploadFiles"/>
      <button v-if="permissions.includes('file.upload')" type="button" class="icon-button" aria-label="上传附件" title="上传附件" :disabled="uploading||busy" @click="fileInput?.click()"><Paperclip :size="17"/></button>
      <div class="approval-mode-wrap">
        <button type="button" class="approval-mode-button" title="Agent 权限模式" :aria-expanded="approvalModePopoverOpen" aria-haspopup="menu" @click="approvalModePopoverOpen=!approvalModePopoverOpen;contextPopoverOpen=false;modelPopoverOpen=false"><ShieldCheck :size="15"/><span>{{approvalPermissionLabel}}</span></button>
        <div v-if="approvalModePopoverOpen" class="approval-mode-popover" role="menu" aria-label="Agent 权限模式">
          <button type="button" class="approval-mode-row" :class="{active:approvalPermissionMode==='ask'}" role="menuitemradio" :aria-checked="approvalPermissionMode==='ask'" @click="setApprovalPermissionMode('ask')"><span><strong>每次询问</strong><small>正式动作先生成待确认请求。</small></span><Check v-if="approvalPermissionMode==='ask'" :size="14"/></button>
          <button type="button" class="approval-mode-row" :class="{active:approvalPermissionMode==='delegated_auto'}" role="menuitemradio" :aria-checked="approvalPermissionMode==='delegated_auto'" @click="setApprovalPermissionMode('delegated_auto')"><span><strong>按授权自动审批</strong><small>只对已授权且流程允许的节点自动同意。</small></span><Check v-if="approvalPermissionMode==='delegated_auto'" :size="14"/></button>
          <p class="approval-mode-hint">授权节点在设置里的 Agent 自动审批中维护。</p>
        </div>
      </div>
      <div class="composer-meta-group">
        <div class="context-meter-wrap">
          <button type="button" class="context-meter" :title="contextUsageTitle(latestContextUsage)" :aria-expanded="contextPopoverOpen" aria-label="查看上下文窗口用量" @click="contextPopoverOpen=!contextPopoverOpen;modelPopoverOpen=false"><span class="context-dot" :class="{warn:Number(latestContextUsage.used_percent)>75,danger:Number(latestContextUsage.used_percent)>92}"/>{{contextPercentText(latestContextUsage)}}</button>
          <div v-if="contextPopoverOpen" class="context-popover" role="dialog" aria-label="上下文窗口">
            <div class="context-popover-head"><strong>剩余 {{formatTokenCount(latestContextUsage.remaining_tokens)}} tokens</strong><span>{{contextPercentText(latestContextUsage)}}</span></div>
            <dl>
              <dt>上下文窗口</dt><dd>{{formatTokenCount(latestContextUsage.used_tokens)}} / {{formatTokenCount(latestContextUsage.context_window)}} tokens</dd>
              <dt>本轮合计</dt><dd>{{formatTokenCount(latestContextUsage.input_tokens||latestContextUsage.input_tokens_estimated)}} tokens <span v-if="latestContextUsage.token_source==='estimate'">估算</span></dd>
              <dt>生成速度</dt><dd>{{latestContextUsage.generation_tokens_per_second?latestContextUsage.generation_tokens_per_second+' tokens/s':'—'}}</dd>
              <dt>模型用量</dt><dd>输入 {{formatTokenCount(latestContextUsage.input_tokens||latestContextUsage.input_tokens_estimated)}}　输出 {{formatTokenCount(latestContextUsage.output_tokens)}}　推理 {{formatTokenCount(latestContextUsage.reasoning_tokens)}}</dd>
              <dt>工具上下文</dt><dd>{{latestContextUsage.tool_count?`已记录 ${latestContextUsage.tool_count} 次工具调用，约 ${formatTokenCount(latestContextUsage.tool_message_tokens_estimated)} tokens`:'本轮没有调用工具'}}</dd>
              <dt>压缩</dt><dd>{{latestContextUsage.compaction_count?`已自动压缩 ${latestContextUsage.compaction_count} 次`:'未触发压缩'}}</dd>
            </dl>
            <p v-if="latestContextUsage.latest_compaction" class="muted small">{{latestContextUsage.latest_compaction.summary}}</p>
          </div>
        </div>
        <div class="model-selector-wrap">
          <button type="button" class="muted small composer-model-label" title="选择模型" :aria-expanded="modelPopoverOpen" aria-haspopup="menu" @click="modelPopoverOpen=!modelPopoverOpen;contextPopoverOpen=false"><Bot :size="16"/><span>{{modelName}}</span></button>
          <div v-if="modelPopoverOpen" class="model-popover" role="menu" aria-label="选择模型">
           <button v-for="profile in modelProfiles" :key="profile.id" type="button" class="model-popover-row"
            :class="{active:String(profile.id)===activeModelProfileId}" role="menuitemradio"
            :aria-checked="String(profile.id)===activeModelProfileId" :disabled="Boolean(modelSwitchingId)" @click="switchModelProfile(profile)">
            <span class="model-popover-label">
             <Bot :size="15"/>
             <span class="model-popover-copy">
              <span>{{profile.name}}</span>
              <small v-if="(profile.model||'未配置')!==profile.name">{{profile.model||'未配置'}}</small>
             </span>
            </span>
            <Check v-if="String(profile.id)===activeModelProfileId" :size="14"/>
           </button>
          </div>
        </div>
      </div>
      <button class="send" :class="{stopping:running}" :type="running?'button':'submit'" :disabled="uploading||busy||(!running&&!prompt.trim())" :aria-label="running?'停止当前任务':'发送任务'" :title="running?'停止当前任务':'发送任务'" @click="running&&stopActiveRun()"><Square v-if="running" :size="13" fill="currentColor"/><ArrowUp v-else :size="17"/></button>
    </div>
  </form>
  <small class="composer-note">结论需要业务证据，正式操作以系统回执为准。按当前权限执行。</small>
</section>
  <div v-if="workspaceOpen&&!full" class="resize-handle" role="separator" tabindex="0" aria-label="调整工作区宽度" aria-orientation="vertical" title="拖拽调整宽度，双击恢复默认宽度" @pointerdown="beginResize" @dblclick="resetWorkspaceWidth" @keydown.left.prevent="resizeBy(20)" @keydown.right.prevent="resizeBy(-20)"/>
  <section :key="me.id+me.authorization_hash" class="workspace" :class="{'workspace-collapsed':!workspaceOpen}" :aria-hidden="!workspaceOpen" :style="workspaceOpen?(full?{}:{width:width+'px'}):{}"><template v-if="workspaceOpen"><header class="workspace-header"><strong>工作区</strong><div><button class="icon-button" title="关闭工作区" aria-label="关闭工作区" @click="collapse"><X :size="15"/></button><button class="icon-button" :aria-label="full?'返回对话':'全屏工作区'" @click="full=!full"><Minimize2 v-if="full" :size="15"/><Maximize2 v-else :size="15"/></button></div></header><nav class="workspace-tabs" aria-label="工作区页签"><div class="workspace-tab-list"><button v-for="tab in workspaceTabs" :key="tab.key" class="workspace-tab" :class="{active:panel===tab.key}" :aria-current="panel===tab.key?'page':undefined" :title="tab.hint" @click="openPanel(tab.key)">{{tab.name}}</button></div><span v-if="detail&&panel==='approvals'" class="object-tab" title="当前审批材料编号">{{numberText(detail.snapshot.number)}}</span></nav><div class="workspace-content">
    <template v-if="panel==='approvals'&&detail"><ApprovalPanel :key="detail.id" :detail="detail" @error="fail" @changed="changed"/>
</template>
    <DomainWorkspacePanel v-else :panel="panel" :target-id="workspaceTargets[panel]||''" @approval="openApproval" @error="fail" @open="openPanel"/>
  </div></template></section>
  <aside v-if="showNotices" class="notification-popover" :style="notificationPopoverStyle" aria-label="待处理"><div class="section-heading notification-head"><div><h2>待处理</h2><small class="muted">{{noticeCount?noticeCount+' 项未读':'暂无未读通知'}}</small></div><button class="icon-button" aria-label="关闭待处理" @click="showNotices=false"><X :size="16"/></button></div>
    <p v-if="noticeLoading" role="status" class="muted small">正在读取待处理事项…</p>
    <h3>待我审批</h3><p v-if="!approvals.length" class="muted notification-empty">当前没有待审批。</p>
    <button v-for="a in approvals" :key="a.id" class="task-row" @click="openApproval(a.id)"><span class="notification-item-copy"><strong>{{numberText(a.snapshot.number)||a.definition.name}}</strong><small class="notification-item-meta"><span>{{a.snapshot.submitter?.name||'提交人待核对'}} · {{a.nodes[a.stage_index]?.name}}{{a.claim_allowed?' · 待领取':''}}</span><time>{{a.snapshot.submitted_at?shanghai(a.snapshot.submitted_at):''}}</time></small></span><ChevronRight :size="15"/></button>
    <h3>我发起的审批</h3><p v-if="!initiatedApprovals.length" class="muted notification-empty">尚未发起审批。</p><button v-for="a in initiatedApprovals" :key="'initiated:'+a.id" class="task-row" @click="openApproval(a.id)"><span class="notification-item-copy"><strong>{{numberText(a.snapshot.number)||a.definition.name}}</strong><small class="notification-item-meta"><span>{{a.status==='RUNNING'?'审批中':a.status==='COMPLETED'?'审批已完成':a.status==='CANCELLED'?'已撤回':a.status==='RETURNED'?'退回修改':'已结束'}} · 第 {{a.revision}} 版</span><time>{{a.snapshot.submitted_at?shanghai(a.snapshot.submitted_at):''}}</time></small></span><ChevronRight :size="15"/></button>
    <h3>抄送我的</h3><p v-if="!approvalWorkItems.copied?.length" class="muted notification-empty">当前没有抄送事项。</p><button v-for="a in approvalWorkItems.copied||[]" :key="'copied:'+a.id" class="task-row" @click="openApproval(a.id)"><span class="notification-item-copy"><strong>{{numberText(a.number)||a.definition.name}}</strong><small class="notification-item-meta"><span>{{a.node.name}} · 到期抄送</span><time>{{a.due_at?shanghai(a.due_at):''}}</time></small></span><ChevronRight :size="15"/></button>
    <h3>逾期关注</h3><p v-if="!approvalWorkItems.overdue?.length" class="muted notification-empty">当前没有逾期关注事项。</p><button v-for="a in approvalWorkItems.overdue||[]" :key="'overdue:'+a.id" class="task-row" @click="openApproval(a.id)"><span class="notification-item-copy"><strong>{{numberText(a.number)||a.definition.name}}</strong><small class="notification-item-meta"><span>{{a.node.name}} · {{a.roles.includes('ESCALATION')?'升级跟进':a.roles.includes('APPROVER')?'我的审批已逾期':'候选任务已逾期'}}</span><time>{{a.due_at?shanghai(a.due_at):''}}</time></small></span><ChevronRight :size="15"/></button>
    <h3>通知记录</h3><p v-if="!notices.length" class="muted notification-empty">暂无通知。</p><button v-for="n in notices" :key="n.id" class="task-row" @click="notice(n)"><span class="notification-item-copy"><strong>{{/[\u4e00-\u9fff]/.test(n.title)?n.title:auditName(n.kind)}}</strong><small class="notification-item-meta"><time>{{shanghai(n.created_at)}}</time></small></span><span v-if="!n.read" class="unread-dot"/></button>
  </aside>
</main>

  <ErpDesignOrdersDialog v-if="erpDesignOrdersDialog" :evidence="erpDesignOrdersDialog" @close="erpDesignOrdersDialog=null"/>

  <div v-if="erpDesignPreview" class="modal-shade erp-design-modal-shade" @click.self="closeErpDesignPreview">
    <section class="modal erp-design-modal" role="dialog" aria-modal="true" aria-label="查看订单明细">
      <header class="erp-design-modal-head">
        <div>
          <h2>查看订单明细</h2>
          <p><template v-if="erpDesignPreview.moldCode">{{erpDesignPreview.moldCode}} · </template>ERP 会话 {{erpDesignPreview.sessionId}}<template v-if="erpDesignPreview.fileName"> · {{erpDesignPreview.fileName}}</template></p>
        </div>
        <div class="erp-design-modal-actions">
          <button class="icon-button" aria-label="关闭订单明细" @click="closeErpDesignPreview"><X :size="17"/></button>
        </div>
      </header>
      <ErpDesignTable
        :preview="erpDesignPreview"
        :loading="erpDesignPreviewLoading"
        :error="erpDesignPreviewError"
        :repricing="erpDesignRepricing"
        :importing="erpDesignImporting"
        :notice="erpDesignPreviewNotice"
        :duplicate-notice="erpDesignDuplicateNotice"
        @retry="loadErpDesignPreview"
        @reprice="repriceErpDesignRows"
        @reprice-row="repriceErpDesignRow"
        @preview-drawing="openErpDrawingPreview"
        @update-draft="updateErpDesignDraft"
        @update-rows="updateErpDesignRows"
        @import="importErpDesign"
      />
    </section>
    <ErpDrawingPreview
      v-if="erpDrawingPreviewRow"
      :session-id="erpDesignPreview.sessionId"
      :row="erpDrawingPreviewRow"
      @close="erpDrawingPreviewRow=null"
    />
  </div>

  <div v-if="selectedEvidence" class="modal-shade evidence-modal-shade" @click.self="selectedEvidence=null">
    <section class="modal evidence-modal" role="dialog" aria-modal="true" aria-label="工具完整依据">
      <div class="evidence-modal-head">
        <div>
          <h2>{{evidenceTitle(selectedEvidence)}}</h2>
          <p class="evidence-modal-meta">{{selectedEvidence.as_of?shanghai(selectedEvidence.as_of)+' · ':''}}{{selectedEvidence.data?.length??0}} 条记录 · 按当前权限返回</p>
        </div>
        <button class="icon-button" aria-label="关闭完整依据" @click="selectedEvidence=null"><X :size="16"/></button>
      </div>
      <div class="evidence-modal-content">
        <article v-for="(row,index) in selectedEvidence.data||[]" :key="index" class="evidence-full-record">
          <div class="evidence-record-head">
            <strong>{{compactRecordTitle(row)||`业务记录 ${Number(index)+1}`}}</strong>
            <span>第 {{Number(index)+1}} 条</span>
          </div>
          <div v-if="evidenceSummary(row).length" class="evidence-summary-grid">
            <div v-for="fact in evidenceSummary(row)" :key="fact.label+fact.value" class="evidence-fact">
              <span>{{fact.label}}</span>
              <strong>{{fact.value}}</strong>
            </div>
          </div>
          <p v-else class="muted small">暂无可见摘要字段。</p>
          <BusinessFacts v-if="hasBusinessFactHighlights(row)" :value="row" :highlights-only="true"/>
          <section v-for="section in evidenceSections(row)" :key="section.key" class="evidence-detail-section">
            <div class="evidence-section-title">
              <h3>{{section.title}}</h3>
              <span v-if="section.count!==undefined">{{section.count}} 条</span>
            </div>
            <div v-if="section.kind==='object'" class="evidence-summary-grid compact">
              <div v-for="fact in section.pairs" :key="fact.label+fact.value" class="evidence-fact">
                <span>{{fact.label}}</span>
                <strong>{{fact.value}}</strong>
              </div>
            </div>
            <div v-else class="evidence-card-grid">
              <article v-for="(child,childIndex) in section.rows" :key="childIndex" class="evidence-mini-card">
                <strong>{{evidenceCardTitle(child,Number(childIndex),section.title)}}</strong>
                <template v-if="isRecord(child)">
                  <div v-for="fact in readableEntries(child,6)" :key="fact.label+fact.value" class="evidence-mini-fact">
                    <span>{{fact.label}}</span>
                    <em>{{fact.value}}</em>
                  </div>
                  <p v-if="!readableEntries(child,6).length" class="muted small">暂无可见字段。</p>
                </template>
                <p v-else>{{valueText(section.key,child)}}</p>
              </article>
            </div>
          </section>
        </article>
        <ProposalCard v-if="selectedEvidence.proposal" :product-name="product.product_name" :presentation="product.proposal_presentation" :step-id="selectedEvidence.id" :proposal="selectedEvidence.proposal" @status="confirmed=>setProposalConfirmed(selectedEvidence.id,confirmed)" @confirmed="()=>handleProposalConfirmed(selectedEvidence.id)" @open="openEvidenceTarget"/>
      </div>
    </section>
  </div>
  <div v-if="error&&me" class="toast" role="alert">{{error}}<button class="icon-button" @click="error=''"><X :size="16"/></button></div>
</template>
