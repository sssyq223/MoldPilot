<script setup lang="ts">
import { computed, nextTick, onMounted, onUnmounted, ref, watchEffect } from 'vue'
import { MessageSquare, Plus, Search, Bell, Paperclip, PanelRight, Maximize2, Minimize2, ArrowUp, Folder, ShoppingCart, Settings, Bot, ChevronRight, LogOut, X, Square, ArrowRight, Pin, Archive, Copy, Check, ShieldCheck, Wrench } from 'lucide-vue-next'
import { api, post, shanghai } from './api'
import {capabilityName,auditName,numberText,fieldName,valueText} from './uiText'
import SettingsPage from './components/SettingsPage.vue'
import ApprovalPanel from './components/ApprovalPanel.vue'
import ContactPanel from './components/ContactPanel.vue'
import ContactProposal from './components/ContactProposal.vue'
import FileMaterial from './components/FileMaterial.vue'
import BusinessFacts from './components/BusinessFacts.vue'
import {applyTheme,storedTheme,type ColorTheme} from './theme'
const colorTheme=ref<ColorTheme>(storedTheme())
function changeTheme(theme:ColorTheme){colorTheme.value=theme;applyTheme(theme)}
const modelName=ref('未配置模型')
const modelLimits=ref<any>({context_window:8192,max_output_tokens:2048})
const modelProfiles=ref<any[]>([]),activeModelProfileId=ref(''),modelSwitchingId=ref('')
const DEFAULT_SIDEBAR_WIDTH=248
const DEFAULT_WORKSPACE_WIDTH=650
const contactTarget=ref('')
const selectedEvidence=ref<any|null>(null)
const selectedFiles=ref<any[]>([]),uploading=ref(false),fileInput=ref<HTMLInputElement|null>(null)
let conversationEpoch=0
const me=ref<any>(null),permissions=ref<string[]>([]),loading=ref(true),error=ref(''),busy=ref(false),username=ref(''),password=ref('')
const panel=ref(''),expanded=ref(false),full=ref(false),width=ref(DEFAULT_WORKSPACE_WIDTH),sidebarWidth=ref(DEFAULT_SIDEBAR_WIDTH),conversations=ref<any[]>([]),conversation=ref(''),activeConversationTitle=ref(''),activeConversationArchived=ref(false),runs=ref<any[]>([]),prompt=ref(''),search=ref(''),notices=ref<any[]>([]),showNotices=ref(false)
const approvals=ref<any[]>([]),detail=ref<any>(null),capabilities=ref<any>({tools:[],skills:[]})
const runProcessOpen=ref<Record<string,boolean>>({})
const confirmedProposalSteps=ref<Record<string,boolean>>({})
const sidebarCollapsed=ref(false)
const copiedMessage=ref('')
const labels:Record<string,string>={materials:'材料总览',approvals:'审批材料',contacts:'联络单材料'}
const workspaceTabs=[
 {key:'materials',name:'材料总览',hint:'说明工作区会展示哪些业务材料'},
 {key:'approvals',name:'审批材料',hint:'查看待审批事项、节点和依据'},
 {key:'contacts',name:'联络单材料',hint:'查看工程联络单、附件和协作进度'},
]
const settingsOpen=ref(false),settingsInitialPage=ref('account'),showProfile=ref(false),noticeLoading=ref(false),showSidebarSearch=ref(false)
const contextPopoverOpen=ref(false),modelPopoverOpen=ref(false),approvalModePopoverOpen=ref(false)
const approvalPermissionMode=ref<'ask'|'delegated_auto'>('ask')
const approvalPermissionLabel=computed(()=>approvalPermissionMode.value==='delegated_auto'?'按授权自动审批':'每次询问')
function approvalModeStorageKey(){return 'mold.agentPermissionMode.'+(me.value?.id||'anonymous')}
function setApprovalPermissionMode(mode:'ask'|'delegated_auto'){approvalPermissionMode.value=mode;if(me.value)localStorage.setItem(approvalModeStorageKey(),mode);approvalModePopoverOpen.value=false}
const profileButton=ref<HTMLButtonElement|null>(null),noticeButton=ref<HTMLButtonElement|null>(null),sidebarSearchInput=ref<HTMLInputElement|null>(null)
const notificationPopoverStyle=ref<Record<string,string>>({left:'96px',top:'44px'})
const workspaceOpen=computed(()=>expanded.value)
const noticeCount=computed(()=>new Set([...approvals.value.map(a=>'approval:'+a.id),...notices.value.filter(n=>!n.read).map(n=>n.kind?.startsWith('approval.')?'approval:'+n.resource_id:'notice:'+n.id)]).size)
function closeProfile(){showProfile.value=false;profileButton.value?.focus()}
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
function escapeMenu(e:KeyboardEvent){if(e.key==='Escape'){if(showProfile.value)closeProfile();showNotices.value=false;contextPopoverOpen.value=false;modelPopoverOpen.value=false;approvalModePopoverOpen.value=false}}
function closeFloatingPanels(e:MouseEvent){
 const target=e.target as Element|null
 if(showNotices.value&&!target?.closest('.notification-popover')&&!(target&&noticeButton.value?.contains(target)))showNotices.value=false
 if(contextPopoverOpen.value&&!target?.closest('.context-meter-wrap'))contextPopoverOpen.value=false
 if(modelPopoverOpen.value&&!target?.closest('.model-selector-wrap'))modelPopoverOpen.value=false
 if(approvalModePopoverOpen.value&&!target?.closest('.approval-mode-wrap'))approvalModePopoverOpen.value=false
}
onMounted(()=>{window.addEventListener('keydown',escapeMenu);window.addEventListener('click',closeFloatingPanels);window.addEventListener('resize',updateNotificationPosition)})
onUnmounted(()=>{window.removeEventListener('keydown',escapeMenu);window.removeEventListener('click',closeFloatingPanels);window.removeEventListener('resize',updateNotificationPosition)})
async function openNotices(){showProfile.value=false;showNotices.value=true;await nextTick();updateNotificationPosition();noticeLoading.value=true;try{[notices.value,approvals.value]=await Promise.all([api('/notifications'),api('/approvals')])}catch(e:any){fail(e.message)}finally{noticeLoading.value=false}}
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
function hasBusinessFactHighlights(row:any){
 const analysis=row?.analysis
 return Boolean(analysis?.tasks?.length||analysis?.revision_impact||analysis?.plan_change_candidates?.length)
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
function runProcessTrace(run:any){return runTrace(run).filter((item:any)=>item.type!=='final')}
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
 const chosen=runProcessOpen.value[run.id]
 if(chosen!==undefined)return chosen
 return ['QUEUED','RUNNING'].includes(run.status)
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
function fail(message:string){error.value=message}
async function refresh(){ [conversations.value,notices.value,approvals.value,capabilities.value]=await Promise.all([api('/conversations'),api('/notifications'),api('/approvals'),api('/capabilities')]) }
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
async function restore(){const response=await api('/me');me.value=response.user;permissions.value=response.permissions;modelName.value=response.model??'未配置模型';modelLimits.value=response.model_limits||modelLimits.value;const savedMode=localStorage.getItem(approvalModeStorageKey());approvalPermissionMode.value=savedMode==='delegated_auto'?'delegated_auto':'ask';await Promise.all([refresh(),loadModelProfiles()]);try{const layout=JSON.parse(localStorage.getItem('mold.layout.'+me.value.id)??'{}');width.value=Math.max(560,Math.min(layout.width??DEFAULT_WORKSPACE_WIDTH,window.innerWidth-480));sidebarWidth.value=Math.max(190,Math.min(layout.sidebarWidth??DEFAULT_SIDEBAR_WIDTH,420));expanded.value=false;panel.value=''}catch{}}
onMounted(async()=>{try{await restore()}catch{}finally{loading.value=false}})
async function login(){busy.value=true;error.value='';try{await post('/auth/login',{username:username.value,password:password.value});password.value='';await restore()}catch(e:any){fail(e.message)}finally{busy.value=false}}
function clearSessionData(){conversationEpoch++;selectedFiles.value=[];me.value=null;permissions.value=[];conversations.value=[];runs.value=[];detail.value=null;approvals.value=[];notices.value=[];capabilities.value={tools:[],skills:[]};modelProfiles.value=[];activeModelProfileId.value='';modelSwitchingId.value='';prompt.value='';expanded.value=false;full.value=false;conversation.value='';activeConversationTitle.value='';activeConversationArchived.value=false;panel.value='';password.value='';showNotices.value=false;showProfile.value=false;settingsOpen.value=false;showSidebarSearch.value=false;contextPopoverOpen.value=false;modelPopoverOpen.value=false;approvalModePopoverOpen.value=false;approvalPermissionMode.value='ask';search.value=''}
async function logout(){try{await post('/auth/logout');clearSessionData()}catch(e:any){fail(e.message)}}
function saveLayout(){if(me.value)localStorage.setItem('mold.layout.'+me.value.id,JSON.stringify({width:width.value,sidebarWidth:sidebarWidth.value}))}
async function openPanel(key:string){if(!['materials','contacts','approvals'].includes(key))return;panel.value=key;expanded.value=true;saveLayout()}
function workspaceEmptyTitle(){return panel.value==='approvals'?'暂无审批材料':panel.value==='contacts'?'暂无联络单材料':'材料总览'}
function workspaceEmptyText(){return panel.value==='approvals'?'从消息通知或会话中的审批建议打开具体审批，节点、依据和操作记录会显示在这里。':panel.value==='contacts'?'从会话结果中选择联络单，附件、处理方案和协作进度会显示在这里。':'工作区用于承载会话中打开的业务材料，目前包含审批材料和联络单材料；从会话结果或消息通知选择具体事项后会自动切换。'}
function toggleWorkspace(){if(expanded.value)collapse();else{if(!panel.value)panel.value='materials';expanded.value=true}}
function collapse(){expanded.value=false;full.value=false;saveLayout()}
async function openApproval(id:string){try{showNotices.value=false;detail.value=await api('/approvals/'+id);await openPanel('approvals')}catch(e:any){fail(e.message)}}
async function changed(){try{await refresh();if(detail.value)detail.value=await api('/approvals/'+detail.value.id)}catch(e:any){fail(e.message)}}
async function selectConversation(id:string,title='',archived=false){if(conversation.value!==id){conversationEpoch++;selectedFiles.value=[];collapse();}conversation.value=id;activeConversationTitle.value=title;activeConversationArchived.value=archived;prompt.value='';try{runs.value=await api(`/conversations/${id}/runs`)}catch(e:any){fail(e.message)}}
async function toggleConversationPin(c:any,event?:Event){event?.stopPropagation();try{await post(`/conversations/${c.id}/pin`);await refresh()}catch(e:any){fail(e.message)}}
async function archiveConversation(c:any,event?:Event){event?.stopPropagation();try{await post(`/conversations/${c.id}/archive`);if(conversation.value===c.id)newConversation();await refresh()}catch(e:any){fail(e.message)}}
async function openArchivedConversation(c:any){settingsOpen.value=false;showNotices.value=false;await selectConversation(c.id,c.title,true)}
function newConversation(){conversationEpoch++;selectedFiles.value=[];conversation.value='';activeConversationTitle.value='';activeConversationArchived.value=false;runs.value=[];prompt.value='';detail.value=null;collapse()}
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
async function notice(n:any){try{await post('/notifications/'+n.id+'/read');showNotices.value=false;await refresh();if(n.kind?.startsWith('approval.'))await openApproval(n.resource_id);else if(n.kind?.startsWith('contact.')){contactTarget.value=n.resource_id;await openPanel('contacts')}else await openNotices()}catch(e:any){fail(e.message)}}
function resizeSession(move:(event:PointerEvent)=>void){document.body.classList.add('layout-resizing');const end=()=>{document.body.classList.remove('layout-resizing');window.removeEventListener('pointermove',move);window.removeEventListener('pointerup',end);window.removeEventListener('pointercancel',end);saveLayout()};window.addEventListener('pointermove',move);window.addEventListener('pointerup',end);window.addEventListener('pointercancel',end)}
function beginResize(e:PointerEvent){if(e.button!==0)return;const x=e.clientX,start=width.value;resizeSession((ev)=>{width.value=Math.max(560,Math.min(window.innerWidth-480,start+x-ev.clientX))})}
function resizeBy(delta:number){width.value=Math.max(560,Math.min(window.innerWidth-480,width.value+delta));saveLayout()}
function resetWorkspaceWidth(){width.value=DEFAULT_WORKSPACE_WIDTH;saveLayout()}
function beginSidebarResize(e:PointerEvent){if(e.button!==0||sidebarCollapsed.value)return;const x=e.clientX,start=sidebarWidth.value;resizeSession((ev)=>{sidebarWidth.value=Math.max(190,Math.min(420,start+ev.clientX-x))})}
function resizeSidebarBy(delta:number){sidebarWidth.value=Math.max(190,Math.min(420,sidebarWidth.value+delta));saveLayout()}
function resetSidebarWidth(){sidebarWidth.value=DEFAULT_SIDEBAR_WIDTH;saveLayout()}
let polling=false,pollTick=0,runPolling=false
const timer=setInterval(async()=>{pollTick++;if(!me.value||polling||(!running.value&&pollTick%4!==0))return;polling=true;try{const info=await api('/me');if(info.user.authorization_hash!==me.value.authorization_hash){me.value=info.user;permissions.value=info.permissions;detail.value=null;runs.value=[];expanded.value=false;panel.value='';await refresh();error.value='权限已更新，相关材料已清理，请重新查询'}if(pollTick%12===0)[notices.value,approvals.value]=await Promise.all([api('/notifications'),api('/approvals')])}catch(e:any){if(e.status===401){clearSessionData();error.value='登录已失效，请重新登录'}}finally{polling=false}},1000)
const runTimer=setInterval(async()=>{if(!me.value||!running.value||!conversation.value||runPolling)return;runPolling=true;try{runs.value=await api(`/conversations/${conversation.value}/runs`)}catch(e:any){if(e.status===401){clearSessionData();error.value='登录已失效，请重新登录'}}finally{runPolling=false}},200)
onUnmounted(()=>{clearInterval(timer);clearInterval(runTimer)})
</script>
<template>
<div v-if="loading" class="loading-screen">正在连接工作台…</div>
<main v-else-if="!me" class="login-screen"><form class="login-box" @submit.prevent="login"><div class="brand-symbol"><Bot :size="30"/></div><h1>MoldPilot</h1><p>模具项目智能工作台 · 从一个任务开始，让业务能力协同工作。</p><label>用户名<input v-model="username" autocomplete="username" required autofocus/></label><label>密码<input v-model="password" type="password" autocomplete="current-password" required/></label><p v-if="error" class="error" role="alert">{{error}}</p><button class="primary" :disabled="busy">{{busy?'正在登录…':'登录工作台'}}<ArrowRight :size="16"/></button><small>统一智能体入口 · 你的权限决定可用能力</small></form></main>
<SettingsPage v-else-if="settingsOpen" :me="me" :permissions="permissions" :capabilities="capabilities" :model-name="modelName" :color-theme="colorTheme" :initial-page="settingsInitialPage" @theme-change="changeTheme" @model-updated="modelName=$event" @open-conversation="openArchivedConversation" @close="settingsOpen=false;restore().catch(e=>fail(e.message))" @error="fail"/>
<main v-else class="workbench" :class="{'panel-full':full&&workspaceOpen,'sidebar-collapsed':sidebarCollapsed,'workspace-open':workspaceOpen&&!full}" :style="{'--sidebar-width':sidebarWidth+'px'}">
  <aside class="sidebar"><div class="brand"><div class="brand-title"><strong>MoldPilot</strong></div><div class="brand-actions"><button class="brand-action" title="搜索历史对话" aria-label="搜索历史对话" :aria-expanded="showSidebarSearch||!!search" @click="toggleSidebarSearch"><Search :size="15"/></button><button ref="noticeButton" class="brand-action" title="待处理" aria-label="待处理" :aria-expanded="showNotices" @click="showNotices?showNotices=false:openNotices()"><Bell :size="15"/><span v-if="noticeCount" class="brand-dot"/></button><button class="brand-action sidebar-toggle-button" :title="sidebarCollapsed?'展开左侧会话':'折叠左侧会话'" :aria-label="sidebarCollapsed?'展开左侧会话':'折叠左侧会话'" :aria-pressed="sidebarCollapsed" @click="sidebarCollapsed=!sidebarCollapsed"><PanelRight :size="15"/></button></div></div><label v-if="showSidebarSearch||search" class="search sidebar-search"><Search :size="16"/><input ref="sidebarSearchInput" v-model="search" placeholder="搜索历史对话" aria-label="搜索历史对话"/></label><button class="new-chat" @click="newConversation"><Plus :size="18"/><span>新对话</span></button><small class="sidebar-label">最近对话</small><div class="conversation-list"><div v-for="c in filteredConversations" :key="c.id" class="conversation-row" :class="{active:conversation===c.id,pinned:c.pinned}"><button class="conversation-main" @click="selectConversation(c.id)"><MessageSquare :size="15"/><span>{{c.title}}</span></button><span v-if="c.status==='WAITING_APPROVAL'" class="conversation-status">等待批准</span><div class="conversation-actions"><button type="button" class="conversation-action" :title="c.pinned?'取消置顶':'置顶聊天'" :aria-label="c.pinned?'取消置顶：'+c.title:'置顶聊天：'+c.title" @click.stop="toggleConversationPin(c,$event)"><Pin :size="13"/></button><button type="button" class="conversation-action" :title="'归档聊天'" :aria-label="'归档聊天：'+c.title" @click.stop="archiveConversation(c,$event)"><Archive :size="13"/></button></div></div><p v-if="!conversations.length" class="muted small">开始一个任务，对话会保存在这里。</p><p v-else-if="!filteredConversations.length" class="muted small">没有匹配的对话。</p></div><div class="profile-area"><button ref="profileButton" class="profile-entry" aria-label="账号菜单" aria-haspopup="menu" :aria-expanded="showProfile" @click="showProfile=!showProfile;showNotices=false"><span class="avatar"><img v-if="me.avatar_url" :src="me.avatar_url" alt=""/><template v-else>{{me.display_name[0]}}</template></span><span class="profile-info"><strong>{{me.display_name}}</strong><small>{{me.department||'未设置部门'}}</small></span></button>
<div v-if="showProfile" class="profile-dismiss" @click="closeProfile"/>
<div v-if="showProfile" class="profile-menu" role="menu" aria-label="账号选项"><p><strong>{{me.display_name}}</strong><small class="muted">{{me.username}}</small></p><button role="menuitem" @click="openSettings"><Settings :size="17"/>设置</button><button role="menuitem" @click="logout"><LogOut :size="17"/>退出登录</button></div></div></aside>
  <div v-if="!sidebarCollapsed" class="sidebar-resize-handle" role="separator" tabindex="0" aria-label="调整左侧边栏宽度" aria-orientation="vertical" title="拖拽调整宽度，双击恢复默认宽度" @pointerdown="beginSidebarResize" @dblclick="resetSidebarWidth" @keydown.left.prevent="resizeSidebarBy(-10)" @keydown.right.prevent="resizeSidebarBy(10)"/>
  <section class="chat"><header class="chat-header"><div class="chat-title"><button v-if="sidebarCollapsed" class="icon-button chat-sidebar-toggle" aria-label="展开左侧会话" title="展开左侧会话" @click="sidebarCollapsed=false"><PanelRight :size="15"/></button><strong>{{currentTitle}}</strong></div><div class="chat-header-actions"><button v-if="!workspaceOpen" class="icon-button" aria-label="展开工作区" title="展开工作区" :aria-expanded="workspaceOpen" @click="toggleWorkspace"><PanelRight :size="15"/></button></div></header><div class="chat-scroll" aria-live="polite">
    <div v-if="!runs.length" class="welcome"><div class="agent-mark"><Bot :size="28"/></div><h1>今天，我们一起完成什么？</h1><p>描述你的目标，我会在你的权限范围内调用工具、<br/>核对资料，并把需要你决定的事项交给你。</p><div class="suggestions"><button v-if="capabilities.tools.some((t:any)=>t.key==='query_projects')" @click="prompt='查询我有权限查看的项目及当前状态'"><Folder :size="17"/>查看我的项目<ArrowRight :size="14"/></button><button v-if="capabilities.tools.some((t:any)=>t.key==='query_purchase_requests')" @click="prompt='查询我负责范围内的采购申请，核对明细和审批进度'"><ShoppingCart :size="17"/>核对采购申请<ArrowRight :size="14"/></button></div><small>从会话开始办理，待审批事项在消息通知中查看。</small></div>
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
          <p v-if="run.status==='RUNNING'" class="muted small">{{run.progress?.phase==='MODEL_STREAMING'?'正在接收模型回复':run.progress?.phase==='MODEL_WAITING'?'正在等待模型回复':run.progress?.phase==='TOOL_RUNNING'?'正在调用业务工具':run.progress?.phase==='VALIDATING'?'正在核对模型结果':'正在准备任务'}}<span v-if="run.progress?.tools?.length"> · 已完成 {{run.progress.tools.length}} 次工具调用</span></p>
          <div v-if="runTrace(run).length" class="react-trace" aria-label="执行链路">
            <button v-if="runProcessTrace(run).length&&!['QUEUED','RUNNING'].includes(run.status)" type="button" class="run-duration-toggle" :aria-expanded="runProcessExpanded(run)" @click="toggleRunProcess(run)">
              {{runDurationLabel(run)}}<ChevronRight class="run-duration-caret" :size="13"/>
            </button>
            <div v-if="runProcessTrace(run).length" class="run-process" :class="{open:runProcessExpanded(run)}" :aria-hidden="!runProcessExpanded(run)">
              <div class="run-process-inner">
                <template v-for="(item,index) in runProcessTrace(run)" :key="item.id||item.call_id||index">
                  <div v-if="item.type==='message'" class="assistant-prose process-text">
                    <p class="preserve">{{item.text}}</p>
                  </div>
                  <div v-else-if="item.type==='tool_search'" class="agent-tool-row tool-search-row">
                    <span class="agent-tool-icon"><Wrench :size="13"/></span>
                    <span class="agent-tool-name">本轮已启用</span>
                    <span class="agent-tool-summary">{{item.activated?.length?item.activated.map((name:string)=>capabilityName(name)).join('、'):'未找到匹配能力'}}</span>
                  </div>
                  <details v-else-if="item.type==='tool'" class="agent-tool-row">
                    <summary class="agent-tool-head">
                      <span class="agent-tool-icon"><Search :size="13"/></span>
                      <span class="agent-tool-name">{{item.proposal?'已准备':'已查询'}} {{capabilityName(item.tool)}}</span>
                      <span class="agent-tool-summary">{{item.proposal?'操作建议':((item.data?.length??0)+' 条记录')}}<template v-if="!item.proposal&&item.data?.[0]"> · {{compactRecordTitle(item.data[0])}}</template></span>
                      <span class="agent-tool-caret"><ChevronRight :size="12"/></span>
                    </summary>
                    <div class="agent-tool-body">
                      <p class="agent-tool-meta">{{item.proposal?'已生成待确认卡，请在下方正文区域处理。':(shanghai(item.as_of)+' · 按当前权限返回')}}</p>
                      <div v-if="!item.proposal" class="agent-tool-main agent-records">
                        <p v-if="!item.data?.length" class="muted small">暂无可见记录。</p>
                        <article v-for="(row,rowIndex) in (item.data||[]).slice(0,3)" :key="rowIndex" class="agent-evidence-record">
                          <div class="agent-record-title">{{compactRecordTitle(row)||`业务记录 ${Number(rowIndex)+1}`}}</div>
                          <div v-if="compactRecordFields(row).length" class="agent-record-fields">
                            <span v-for="fact in compactRecordFields(row)" :key="fact.key"><b>{{fact.label}}</b>{{fact.value}}</span>
                          </div>
                        </article>
                        <button v-if="item.data?.length" class="agent-inline-action" @click="selectedEvidence=item">查看完整依据</button>
                      </div>
                      <div v-if="['query_contact_cases','query_contact_context'].includes(item.tool)" class="actions">
                        <button v-for="row in item.data" :key="row.id" @click="contactTarget=row.id;openPanel('contacts')">查看联络材料：{{row.title}}</button>
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
            <div v-for="(finalItem,finalIndex) in runFinalTraces(run)" :key="'final:'+finalIndex" class="assistant-prose final">
              <p v-if="finalItem.summary || finalItem.message" class="preserve">{{finalItem.summary ?? finalItem.message}}</p>
              <div v-if="finalItem.error_code" class="run-error-detail" role="note">
                <strong>失败原因</strong><span>{{runFailureReason(finalItem.error_code)}}</span><code>错误码 {{finalItem.error_code}}</code>
              </div>
              <ul v-if="finalItem.suggestions?.length">
                <li v-for="s in finalItem.suggestions" :key="s">{{s}}</li>
              </ul>
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
  <ContactProposal v-if="pendingApprovalContext" placement="composer" :step-id="pendingApprovalContext.item.id" :proposal="pendingApprovalContext.item.proposal" @confirmed="handleCurrentProposalDecision(false)" @dismissed="handleCurrentProposalDecision(true)" @open="id=>{contactTarget=id;openPanel('contacts')}"/>
  <form v-else class="composer" @submit.prevent="send">
    <div v-if="selectedFiles.length" class="composer-files"><span v-for="file in selectedFiles" :key="file.id">{{file.filename}}<button type="button" class="icon-button" :aria-label="'取消本次关联附件：'+file.filename" @click="selectedFiles=selectedFiles.filter(f=>f.id!==file.id)"><X :size="13"/></button></span></div>
    <p v-if="uploading" role="status" class="muted small">正在保存上传原件…</p>
    <textarea v-model="prompt" placeholder="输入任务或问题…" aria-label="输入任务或问题" rows="3" @keydown.enter.exact.prevent="send"/>
    <div class="composer-toolbar">
      <input ref="fileInput" hidden type="file" accept=".pdf,.png,.jpg,.jpeg,.docx,.xlsx" multiple aria-label="选择上传附件" @change="uploadFiles"/>
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
            <span class="model-popover-label"><Bot :size="15"/>{{profile.name}}</span><strong>{{profile.model||'未配置'}}</strong><Check v-if="String(profile.id)===activeModelProfileId" :size="14"/>
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
  <section :key="me.id+me.authorization_hash" class="workspace" :class="{'workspace-collapsed':!workspaceOpen}" :aria-hidden="!workspaceOpen" :style="workspaceOpen?(full?{}:{width:width+'px'}):{}"><template v-if="workspaceOpen"><header class="workspace-header"><strong>工作区</strong><div><button class="icon-button" title="关闭工作区" aria-label="关闭工作区" @click="collapse"><X :size="15"/></button><button class="icon-button" :aria-label="full?'返回对话':'全屏工作区'" @click="full=!full"><Minimize2 v-if="full" :size="15"/><Maximize2 v-else :size="15"/></button></div></header><nav class="workspace-tabs" aria-label="工作区页签"><button v-for="tab in workspaceTabs" :key="tab.key" :class="{active:panel===tab.key}" :aria-current="panel===tab.key?'page':undefined" :title="tab.hint" @click="openPanel(tab.key)">{{tab.name}}</button><button v-if="detail&&panel==='approvals'" class="object-tab">{{numberText(detail.snapshot.number)}}</button></nav><div class="workspace-content">
    <ContactPanel @approval="openApproval" v-if="panel==='contacts'" :key="contactTarget" :initial-id="contactTarget" @error="fail"/>
    <template v-else-if="panel==='approvals'&&detail"><button class="back-button" @click="collapse();openNotices()">← 返回消息通知</button><ApprovalPanel :key="detail.id" :detail="detail" @error="fail" @changed="changed"/>
</template>
    <div v-else class="empty workspace-empty"><h3>{{workspaceEmptyTitle()}}</h3><p>{{workspaceEmptyText()}}</p><div class="workspace-empty-cards"><button type="button" :class="{active:panel==='approvals'}" @click="openPanel('approvals')"><strong>审批材料</strong><small>待审批事项、流程节点、附件和审批记录</small></button><button type="button" :class="{active:panel==='contacts'}" @click="openPanel('contacts')"><strong>联络单材料</strong><small>工程联络单、处理方案、附件和协作进度</small></button></div></div>
  </div></template></section>
  <aside v-if="showNotices" class="notification-popover" :style="notificationPopoverStyle" aria-label="待处理"><div class="section-heading notification-head"><div><h2>待处理</h2><small class="muted">{{noticeCount?noticeCount+' 项需要查看':'暂无需要处理'}}</small></div><button class="icon-button" aria-label="关闭待处理" @click="showNotices=false"><X :size="16"/></button></div>
    <p v-if="noticeLoading" role="status" class="muted small">正在读取待处理事项…</p>
    <h3>待我审批</h3><p v-if="!approvals.length" class="muted notification-empty">当前没有待审批。</p>
    <button v-for="a in approvals" :key="a.id" class="task-row" @click="openApproval(a.id)"><span><strong>{{numberText(a.snapshot.number)||a.definition.name}}</strong><small>{{a.snapshot.submitter?.name||'提交人待核对'}} · {{a.nodes[a.stage_index]?.name}}</small><small>{{a.snapshot.submitted_at?shanghai(a.snapshot.submitted_at):''}}</small></span><ChevronRight :size="16"/></button>
    <h3>通知记录</h3><p v-if="!notices.length" class="muted notification-empty">暂无通知。</p><button v-for="n in notices" :key="n.id" class="task-row" @click="notice(n)"><span><strong>{{/[\u4e00-\u9fff]/.test(n.title)?n.title:auditName(n.kind)}}</strong><small>{{shanghai(n.created_at)}}</small></span><span v-if="!n.read" class="unread-dot"/></button>
  </aside>
</main>

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
        <ContactProposal v-if="selectedEvidence.proposal" :step-id="selectedEvidence.id" :proposal="selectedEvidence.proposal" @status="confirmed=>setProposalConfirmed(selectedEvidence.id,confirmed)" @confirmed="()=>handleProposalConfirmed(selectedEvidence.id)" @open="id=>{selectedEvidence=null;contactTarget=id;openPanel('contacts')}"/>
      </div>
    </section>
  </div>
  <div v-if="error&&me" class="toast" role="alert">{{error}}<button class="icon-button" @click="error=''"><X :size="16"/></button></div>
</template>
