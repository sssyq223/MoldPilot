<script setup lang="ts">
import { computed, onMounted, onUnmounted, ref } from 'vue'
import { MessageSquare, Plus, Search, Bell, Paperclip, PanelRightClose, PanelRightOpen, Maximize2, Minimize2, Send, Folder, ShoppingCart, Settings, Bot, ChevronRight, LogOut, X, Square, ArrowRight, CircleCheck } from 'lucide-vue-next'
import { api, post, shanghai } from './api'
import {capabilityName,auditName,numberText} from './uiText'
import SettingsPage from './components/SettingsPage.vue'
import BusinessFacts from './components/BusinessFacts.vue'
import ApprovalPanel from './components/ApprovalPanel.vue'
import ContactPanel from './components/ContactPanel.vue'
import ContactProposal from './components/ContactProposal.vue'
import FileMaterial from './components/FileMaterial.vue'
import {applyTheme,storedTheme,type ColorTheme} from './theme'
const colorTheme=ref<ColorTheme>(storedTheme())
function changeTheme(theme:ColorTheme){colorTheme.value=theme;applyTheme(theme)}
const modelName=ref('未配置模型')
const contactTarget=ref('')
const selectedFiles=ref<any[]>([]),uploading=ref(false),fileInput=ref<HTMLInputElement|null>(null)
let conversationEpoch=0
const me=ref<any>(null),permissions=ref<string[]>([]),loading=ref(true),error=ref(''),busy=ref(false),username=ref(''),password=ref('')
const panel=ref(''),expanded=ref(false),full=ref(false),width=ref(650),conversations=ref<any[]>([]),conversation=ref(''),runs=ref<any[]>([]),prompt=ref(''),search=ref(''),notices=ref<any[]>([]),showNotices=ref(false)
const approvals=ref<any[]>([]),detail=ref<any>(null),capabilities=ref<any>({tools:[],skills:[]})
const labels:Record<string,string>={approvals:'审批材料',contacts:'联络单材料',materials:'业务材料'}
const settingsOpen=ref(false),showProfile=ref(false),noticeLoading=ref(false)
const profileButton=ref<HTMLButtonElement|null>(null)
const workspaceOpen=computed(()=>expanded.value)
const noticeCount=computed(()=>new Set([...approvals.value.map(a=>'approval:'+a.id),...notices.value.filter(n=>!n.read).map(n=>n.kind?.startsWith('approval.')?'approval:'+n.resource_id:'notice:'+n.id)]).size)
function closeProfile(){showProfile.value=false;profileButton.value?.focus()}
function openSettings(){showProfile.value=false;showNotices.value=false;settingsOpen.value=true}
function escapeMenu(e:KeyboardEvent){if(e.key==='Escape'&&showProfile.value)closeProfile()}
onMounted(()=>window.addEventListener('keydown',escapeMenu))
onUnmounted(()=>window.removeEventListener('keydown',escapeMenu))
async function openNotices(){showProfile.value=false;showNotices.value=true;noticeLoading.value=true;try{[notices.value,approvals.value]=await Promise.all([api('/notifications'),api('/approvals')])}catch(e:any){fail(e.message)}finally{noticeLoading.value=false}}
const currentTitle=computed(()=>conversations.value.find(c=>c.id===conversation.value)?.title ?? '新对话')
const running=computed(()=>runs.value.some(r=>['QUEUED','RUNNING'].includes(r.status)))
const runStatus:Record<string,string>={QUEUED:'任务已排队',RUNNING:'正在执行',WAITING_CONFIGURATION:'等待模型配置',SUCCEEDED:'执行已完成',FAILED:'执行未完成',CANCELLED:'已停止'}
function fail(message:string){error.value=message}
async function refresh(){ [conversations.value,notices.value,approvals.value,capabilities.value]=await Promise.all([api('/conversations'),api('/notifications'),api('/approvals'),api('/capabilities')]) }
async function restore(){const response=await api('/me');me.value=response.user;permissions.value=response.permissions;modelName.value=response.model??'未配置模型';await refresh();try{const layout=JSON.parse(localStorage.getItem('mold.layout.'+me.value.id)??'{}');width.value=Math.max(560,Math.min(layout.width??650,window.innerWidth-480));expanded.value=false;panel.value=''}catch{}}
onMounted(async()=>{try{await restore()}catch{}finally{loading.value=false}})
async function login(){busy.value=true;error.value='';try{await post('/auth/login',{username:username.value,password:password.value});password.value='';await restore()}catch(e:any){fail(e.message)}finally{busy.value=false}}
function clearSessionData(){conversationEpoch++;selectedFiles.value=[];me.value=null;permissions.value=[];conversations.value=[];runs.value=[];detail.value=null;approvals.value=[];notices.value=[];capabilities.value={tools:[],skills:[]};prompt.value='';expanded.value=false;full.value=false;conversation.value='';panel.value='';password.value='';showNotices.value=false;showProfile.value=false;settingsOpen.value=false;search.value=''}
async function logout(){try{await post('/auth/logout');clearSessionData()}catch(e:any){fail(e.message)}}
function saveLayout(){if(me.value)localStorage.setItem('mold.layout.'+me.value.id,JSON.stringify({width:width.value}))}
async function openPanel(key:string){if(!['contacts','approvals'].includes(key))return;panel.value=key;expanded.value=true;saveLayout()}
function toggleWorkspace(){if(expanded.value)collapse();else{if(!panel.value)panel.value='materials';expanded.value=true}}
function collapse(){expanded.value=false;full.value=false;saveLayout()}
async function openApproval(id:string){try{showNotices.value=false;detail.value=await api('/approvals/'+id);await openPanel('approvals')}catch(e:any){fail(e.message)}}
async function changed(){try{await refresh();if(detail.value)detail.value=await api('/approvals/'+detail.value.id)}catch(e:any){fail(e.message)}}
async function selectConversation(id:string){if(conversation.value!==id){conversationEpoch++;selectedFiles.value=[];collapse();}conversation.value=id;prompt.value='';try{runs.value=await api(`/conversations/${id}/runs`)}catch(e:any){fail(e.message)}}
function newConversation(){conversationEpoch++;selectedFiles.value=[];conversation.value='';runs.value=[];prompt.value='';detail.value=null;collapse()}
async function send(){if(!prompt.value.trim()||busy.value||uploading.value)return;busy.value=true;error.value='';try{const r=await post('/runs',{prompt:prompt.value,conversation_id:conversation.value||null,file_ids:selectedFiles.value.map(f=>f.id)});selectedFiles.value=[];prompt.value='';conversation.value=r.conversation_id;await refresh();await selectConversation(r.conversation_id)}catch(e:any){fail(e.message)}finally{busy.value=false}}
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
async function cancel(id:string){try{await post('/runs/'+id+'/cancel');await selectConversation(conversation.value)}catch(e:any){fail(e.message)}}
async function notice(n:any){try{await post('/notifications/'+n.id+'/read');showNotices.value=false;await refresh();if(n.kind?.startsWith('approval.'))await openApproval(n.resource_id);else if(n.kind?.startsWith('contact.')){contactTarget.value=n.resource_id;await openPanel('contacts')}else await openNotices()}catch(e:any){fail(e.message)}}
function beginResize(e:PointerEvent){const x=e.clientX,start=width.value;const move=(ev:PointerEvent)=>{width.value=Math.max(560,Math.min(window.innerWidth-480,start+x-ev.clientX))};const end=()=>{window.removeEventListener('pointermove',move);window.removeEventListener('pointerup',end);saveLayout()};window.addEventListener('pointermove',move);window.addEventListener('pointerup',end)}
function resizeBy(delta:number){width.value=Math.max(560,Math.min(window.innerWidth-480,width.value+delta));saveLayout()}
let polling=false,pollTick=0
const timer=setInterval(async()=>{pollTick++;if(!me.value||polling||(!running.value&&pollTick%4!==0))return;polling=true;try{const info=await api('/me');if(info.user.authorization_hash!==me.value.authorization_hash){me.value=info.user;permissions.value=info.permissions;detail.value=null;runs.value=[];expanded.value=false;panel.value='';await refresh();error.value='权限已更新，相关材料已清理，请重新查询'}if(running.value&&conversation.value)runs.value=await api(`/conversations/${conversation.value}/runs`);if(pollTick%12===0)[notices.value,approvals.value]=await Promise.all([api('/notifications'),api('/approvals')])}catch(e:any){if(e.status===401){clearSessionData();error.value='登录已失效，请重新登录'}}finally{polling=false}},1000)
onUnmounted(()=>clearInterval(timer))
</script>
<template>
<div v-if="loading" class="loading-screen">正在连接工作台…</div>
<main v-else-if="!me" class="login-screen"><form class="login-box" @submit.prevent="login"><div class="brand-symbol"><Bot :size="30"/></div><h1>MoldPilot</h1><p>模具项目智能工作台 · 从一个任务开始，让业务能力协同工作。</p><label>用户名<input v-model="username" autocomplete="username" required autofocus/></label><label>密码<input v-model="password" type="password" autocomplete="current-password" required/></label><p v-if="error" class="error" role="alert">{{error}}</p><button class="primary" :disabled="busy">{{busy?'正在登录…':'登录工作台'}}<ArrowRight :size="16"/></button><small>统一智能体入口 · 你的权限决定可用能力</small></form></main>
<SettingsPage v-else-if="settingsOpen" :me="me" :permissions="permissions" :capabilities="capabilities" :model-name="modelName" :color-theme="colorTheme" @theme-change="changeTheme" @close="settingsOpen=false;refresh().catch(e=>fail(e.message))" @error="fail"/>
<main v-else class="workbench" :class="{'panel-full':full&&workspaceOpen}">
  <aside class="sidebar"><div class="brand"><Bot :size="23"/><strong>MoldPilot</strong></div><button class="new-chat" @click="newConversation"><Plus :size="18"/>新对话</button><label class="search"><Search :size="16"/><input v-model="search" placeholder="搜索历史对话" aria-label="搜索历史对话"/></label><button class="notice-entry" @click="showNotices?showNotices=false:openNotices()"><Bell :size="18"/>消息通知<span v-if="noticeCount" class="counter">{{noticeCount}}</span></button><small class="sidebar-label">最近对话</small><div class="conversation-list"><button v-for="c in conversations.filter(c=>c.title.includes(search))" :key="c.id" :class="{active:conversation===c.id}" @click="selectConversation(c.id)"><MessageSquare :size="15"/><span>{{c.title}}</span></button><p v-if="!conversations.length" class="muted small">开始一个任务，对话会保存在这里。</p></div><div class="profile-area"><button ref="profileButton" class="profile-entry" aria-label="账号菜单" aria-haspopup="menu" :aria-expanded="showProfile" @click="showProfile=!showProfile;showNotices=false"><span class="avatar">{{me.display_name[0]}}</span><span class="profile-info"><strong>{{me.display_name}}</strong><small>{{me.super_admin?'超级管理员':me.department||me.username}}</small></span></button>
<div v-if="showProfile" class="profile-dismiss" @click="closeProfile"/>
<div v-if="showProfile" class="profile-menu" role="menu" aria-label="账号选项"><p><strong>{{me.display_name}}</strong><small class="muted">{{me.username}}</small></p><button role="menuitem" @click="openSettings"><Settings :size="17"/>设置</button><button role="menuitem" @click="logout"><LogOut :size="17"/>退出登录</button></div></div></aside>
  <section class="chat"><header class="chat-header"><div class="chat-title"><strong>{{currentTitle}}</strong></div><div class="chat-header-actions"><span class="muted small">统一智能体</span><button class="icon-button" :aria-label="workspaceOpen?'收起工作区':'展开工作区'" :title="workspaceOpen?'收起工作区':'展开工作区'" :aria-expanded="workspaceOpen" @click="toggleWorkspace"><PanelRightClose v-if="workspaceOpen" :size="20"/><PanelRightOpen v-else :size="20"/></button></div></header><div class="chat-scroll" aria-live="polite">
    <div v-if="!runs.length" class="welcome"><div class="agent-mark"><Bot :size="28"/></div><h1>今天，我们一起完成什么？</h1><p>描述你的目标，我会在你的权限范围内调用工具、<br/>核对资料，并把需要你决定的事项交给你。</p><div class="suggestions"><button v-if="capabilities.tools.some((t:any)=>t.key==='query_projects')" @click="prompt='查询我有权限查看的项目及当前状态'"><Folder :size="17"/>查看我的项目<ArrowRight :size="14"/></button><button v-if="capabilities.tools.some((t:any)=>t.key==='query_purchase_requests')" @click="prompt='查询我负责范围内的采购申请，核对明细和审批进度'"><ShoppingCart :size="17"/>核对采购申请<ArrowRight :size="14"/></button></div><small>从会话开始办理，待审批事项在消息通知中查看。</small></div>
    <article v-for="run in runs" :key="run.id" class="conversation-turn"><div class="user-message">{{run.prompt}}<FileMaterial v-for="file in run.files||[]" :key="file.id" :file="file" @error="fail"/></div><div class="agent-answer"><span class="mini-agent"><Bot :size="19"/></span><div class="answer-body"><div class="run-label"><span :class="{pulse:['QUEUED','RUNNING'].includes(run.status)}"/>{{runStatus[run.status]??'任务状态待确认'}}<button v-if="['QUEUED','RUNNING'].includes(run.status)" class="icon-button" title="停止任务" aria-label="停止任务" @click="cancel(run.id)"><Square :size="12"/></button></div><p v-if="run.status==='WAITING_CONFIGURATION'" class="muted">任务已保存。模型尚未完成配置，当前不会生成业务结论。待审批事项仍可从消息通知中查看。</p><p v-if="run.status==='RUNNING'" class="muted small">{{run.progress?.phase==='MODEL_WAITING'?'正在等待模型回复':run.progress?.phase==='TOOL_RUNNING'?'正在调用业务工具':run.progress?.phase==='VALIDATING'?'正在核对模型结果':'正在准备任务'}} · 已等待 {{run.progress?.elapsed_seconds||0}} 秒<span v-if="run.progress?.tools?.length"> · 已完成 {{run.progress.tools.length}} 次工具调用</span></p><p class="preserve">{{run.result?.summary ?? run.result?.message}}</p><p v-if="run.result?.error_code" class="muted small">任务未能完成，请查看原因说明后重试。</p><div v-for="e in run.result?.evidence??[]" :key="e.id" class="evidence"><div class="section-heading"><strong><CircleCheck :size="15"/>{{capabilityName(e.tool)}}</strong><small>{{e.proposal?'业务操作建议':'业务查询结果'}}</small></div><ContactProposal v-if="e.proposal" :step-id="e.id" :proposal="e.proposal" @open="id=>{contactTarget=id;openPanel('contacts')}"/><p v-else class="muted small">{{shanghai(e.as_of)}} · {{e.data.length}} 条可见记录</p><details v-if="!e.proposal"><summary>查看工具返回的依据</summary><BusinessFacts v-for="(row,index) in e.data" :key="index" :value="row"/></details><div v-if="['query_contact_cases','query_contact_context'].includes(e.tool)" class="actions"><button v-for="row in e.data" :key="row.id" @click="contactTarget=row.id;openPanel('contacts')">查看联络材料：{{row.title}}</button></div></div><ul v-if="run.result?.suggestions?.length"><li v-for="s in run.result.suggestions" :key="s">{{s}}</li></ul></div></div></article>
  </div><form class="composer" @submit.prevent="send"><div v-if="selectedFiles.length" class="composer-files"><span v-for="file in selectedFiles" :key="file.id">{{file.filename}}<button type="button" class="icon-button" :aria-label="'取消本次关联附件：'+file.filename" @click="selectedFiles=selectedFiles.filter(f=>f.id!==file.id)"><X :size="13"/></button></span></div><p v-if="uploading" role="status" class="muted small">正在保存上传原件…</p><textarea v-model="prompt" placeholder="输入任务或问题…" aria-label="输入任务或问题" rows="3" @keydown.enter.exact.prevent="send"/><div class="composer-toolbar"><input ref="fileInput" hidden type="file" accept=".pdf,.png,.jpg,.jpeg,.docx,.xlsx" multiple aria-label="选择上传附件" @change="uploadFiles"/><button v-if="permissions.includes('file.upload')" type="button" class="icon-button" aria-label="上传附件" title="上传附件" :disabled="uploading||busy" @click="fileInput?.click()"><Paperclip :size="17"/></button><span class="muted small">{{modelName}} · 按当前权限执行</span><button class="send" type="submit" :disabled="!prompt.trim()||busy||uploading" aria-label="发送任务"><Send :size="17"/></button></div></form><small class="composer-note">结论需要业务证据，正式操作以系统回执为准。</small></section>
  <div v-if="workspaceOpen&&!full" class="resize-handle" role="separator" tabindex="0" aria-label="调整工作区宽度" aria-orientation="vertical" @pointerdown="beginResize" @keydown.left.prevent="resizeBy(20)" @keydown.right.prevent="resizeBy(-20)"/>
  <section v-if="workspaceOpen" :key="me.id+me.authorization_hash" class="workspace" :style="full?{}:{width:width+'px'}"><header class="workspace-header"><strong>工作区</strong><div><button class="icon-button" title="关闭工作区" aria-label="关闭工作区" @click="collapse"><PanelRightClose :size="18"/></button><button class="icon-button" :aria-label="full?'返回对话':'全屏工作区'" @click="full=!full"><Minimize2 v-if="full" :size="18"/><Maximize2 v-else :size="18"/></button></div></header><nav class="workspace-tabs"><button class="active">{{labels[panel]}}</button><button v-if="detail&&panel==='approvals'" class="object-tab">{{numberText(detail.snapshot.number)}}</button><span class="muted small">本地开发 · 模拟资料</span></nav><div class="workspace-content">
    <ContactPanel @approval="openApproval" v-if="panel==='contacts'" :key="contactTarget" :initial-id="contactTarget" @error="fail"/>
    <template v-else-if="panel==='approvals'&&detail"><button class="back-button" @click="collapse();openNotices()">← 返回消息通知</button><ApprovalPanel :key="detail.id" :detail="detail" @error="fail" @changed="changed"/>
</template>
    <div v-else class="empty"><h3>业务材料工作区</h3><p>从会话结果或消息通知中选择具体事项，相关材料会在这里打开。</p></div>
  </div></section>
  <aside v-if="showNotices" class="notification-popover" aria-label="消息通知"><div class="section-heading"><h2>消息通知</h2><button class="icon-button" aria-label="关闭消息通知" @click="showNotices=false"><X :size="16"/></button></div>
    <p v-if="noticeLoading" role="status">正在读取消息和待审批事项…</p>
    <h3>待我审批</h3><p v-if="!approvals.length" class="muted">当前没有待审批事项。</p>
    <button v-for="a in approvals" :key="a.id" class="task-row" @click="openApproval(a.id)"><span><strong>{{numberText(a.snapshot.number)||a.definition.name}}</strong><small>{{a.snapshot.submitter?.name||'提交人待核对'}} · {{a.nodes[a.stage_index]?.name}}</small><small>{{a.snapshot.submitted_at?shanghai(a.snapshot.submitted_at):''}}</small></span><ChevronRight :size="16"/></button>
    <h3>通知记录</h3><p v-if="!notices.length" class="muted">暂无通知记录。</p><button v-for="n in notices" :key="n.id" class="task-row" @click="notice(n)"><span><strong>{{/[\u4e00-\u9fff]/.test(n.title)?n.title:auditName(n.kind)}}</strong><small>{{shanghai(n.created_at)}}</small></span><span v-if="!n.read" class="unread-dot"/></button>
  </aside>
</main>

  <div v-if="error&&me" class="toast" role="alert">{{error}}<button class="icon-button" @click="error=''"><X :size="16"/></button></div>
</template>
