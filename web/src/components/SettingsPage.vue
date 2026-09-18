<script setup lang="ts">
import {computed,onBeforeUnmount,onMounted,ref,watch} from 'vue'
import {ArrowLeft,Settings,Wrench,Users,GitBranch,ScrollText,Search,Layers,Sun,Moon,Archive,BrainCircuit,ShieldCheck,ShieldOff,Trash2,MessageSquare,RotateCcw} from 'lucide-vue-next'
import type {ColorTheme} from '../theme'
import {api,post,shanghai} from '../api'
import {capabilityMeta,capabilityName,capabilityNames,groupedCapabilities,permissionName,auditName,capabilityExample as domainCapabilityExample} from '@domain-pack/uiText'
import {capabilityUi} from '@domain-pack/uiPolicy'
import AdminPanel from './AdminPanel.vue'
import WorkflowPanel from './WorkflowPanel.vue'
const props=defineProps<{me:any;permissions:string[];capabilities:any;modelName:string;colorTheme:ColorTheme;initialPage?:string}>()
const emit=defineEmits<{close:[];error:[message:string];themeChange:[theme:ColorTheme];openConversation:[conversation:any];modelUpdated:[model:string]}>()
const page=ref(props.initialPage||'account'),search=ref(''),audit=ref<any[]>([]),auditLoading=ref(false),auditPage=ref(1),auditTotal=ref(0)
const auditPageSize=8
const archived=ref<any[]>([]),archivedSearch=ref(''),archivedLoading=ref(false)
const capabilitySearch=ref(''),capabilityDepartment=ref(''),capabilityType=ref(''),capabilityTab=ref<'tools'|'skills'|'all'>('all')
const capabilityDropdown=ref<'department'|'type'|''>(''),capabilityToolbar=ref<HTMLElement|null>(null)
const modelConfig=ref<any|null>(null),modelProfiles=ref<any[]>([]),activeModelProfileId=ref(''),creatingModelProfile=ref(false)
const modelLoading=ref(false),modelSaving=ref(false),modelApiKey=ref(''),clearModelApiKey=ref(false),modelSaved=ref(''),modelDetailsOpen=ref(false)
const delegationOptions=ref<any[]>([]),delegations=ref<any[]>([]),delegationsLoading=ref(false),delegationSaving=ref(false),delegationNotice=ref('')
const delegationNode=ref(''),delegationReason=ref(''),delegationValidTo=ref('')
const proxyOptions=ref<any[]>([]),proxyUsers=ref<any[]>([]),proxyDelegations=ref<any[]>([]),proxyLoading=ref(false),proxySaving=ref(false),proxyNotice=ref('')
const proxyNode=ref(''),proxyPrincipal=ref(''),proxyAgent=ref(''),proxyReason=ref(''),proxyValidFrom=ref(''),proxyValidTo=ref(''),proxyDecisions=ref<string[]>(['APPROVE'])
const avatarInput=ref<HTMLInputElement|null>(null),avatarUploading=ref(false)
const selectedCapability=ref<{kind:'tool'|'skill';item:any}|null>(null)
const allCapabilityItems=computed(()=>([...(props.capabilities.tools||[]),...(props.capabilities.skills||[])]))
const capabilityDepartments=computed(()=>Array.from(new Map(allCapabilityItems.value.map((item:any)=>{const meta=capabilityMeta(item);return [meta.department,meta.departmentName]})).entries()))
const capabilityTypes=computed(()=>Array.from(new Map(allCapabilityItems.value.map((item:any)=>{const meta=capabilityMeta(item);return [meta.type,meta.typeName]})).entries()))
const capabilityDepartmentLabel=computed(()=>String(capabilityDepartments.value.find(([key])=>key===capabilityDepartment.value)?.[1]||'全部部门'))
const capabilityTypeLabel=computed(()=>String(capabilityTypes.value.find(([key])=>key===capabilityType.value)?.[1]||'全部类型'))
function toggleCapabilityDropdown(kind:'department'|'type'){capabilityDropdown.value=capabilityDropdown.value===kind?'':kind}
function setCapabilityDepartment(value:string){capabilityDepartment.value=value;capabilityDropdown.value=''}
function setCapabilityType(value:string){capabilityType.value=value;capabilityDropdown.value=''}
function closeCapabilityDropdown(event:PointerEvent){
 if(capabilityToolbar.value&&!capabilityToolbar.value.contains(event.target as Node))capabilityDropdown.value=''
}
onMounted(()=>document.addEventListener('pointerdown',closeCapabilityDropdown))
onBeforeUnmount(()=>document.removeEventListener('pointerdown',closeCapabilityDropdown))
function capabilityMatches(item:any){
 const meta=capabilityMeta(item),keyword=capabilitySearch.value.trim().toLowerCase()
 if(capabilityDepartment.value&&meta.department!==capabilityDepartment.value)return false
 if(capabilityType.value&&meta.type!==capabilityType.value)return false
 if(!keyword)return true
 return [item.key,capabilityName(item),item.description,item.permission,permissionName(item.permission||''),meta.departmentName,meta.typeName].some(value=>String(value||'').toLowerCase().includes(keyword))
}
const filteredTools=computed(()=>(props.capabilities.tools||[]).filter(capabilityMatches))
const filteredSkills=computed(()=>(props.capabilities.skills||[]).filter(capabilityMatches))
const groupedTools=computed(()=>groupedCapabilities(filteredTools.value))
const groupedSkills=computed(()=>groupedCapabilities(filteredSkills.value))
const showCapabilityTools=computed(()=>capabilityTab.value==='tools'||capabilityTab.value==='all')
const showCapabilitySkills=computed(()=>capabilityTab.value==='skills'||capabilityTab.value==='all')
const capabilityGroupTabs=ref<Record<string,string>>({})
function capabilityGroupKey(kind:'tools'|'skills',department:any){return kind+':'+department.key}
function capabilityGroupTab(kind:'tools'|'skills',department:any){return capabilityGroupTabs.value[capabilityGroupKey(kind,department)]||'all'}
function setCapabilityGroupTab(kind:'tools'|'skills',department:any,typeKey:string){capabilityGroupTabs.value={...capabilityGroupTabs.value,[capabilityGroupKey(kind,department)]:typeKey}}
function visibleCapabilityTypes(kind:'tools'|'skills',department:any){const active=capabilityGroupTab(kind,department);return active==='all'?department.types:department.types.filter((type:any)=>type.key===active)}
function capabilityCategoryName(item:any){return item.permission?permissionName(item.permission).split(' · ')[0]:capabilityMeta(item).typeName}
function visibleCapabilityCategoryGroups(kind:'tools'|'skills',department:any){
 const map=new Map<string,any[]>()
 for(const type of visibleCapabilityTypes(kind,department))for(const item of type.items){
  const name=capabilityCategoryName(item)
  if(!map.has(name))map.set(name,[])
  map.get(name)!.push(item)
 }
 return Array.from(map.entries()).map(([name,items])=>({name,items}))
}
function toolUsageText(tool:any){
 return tool.mode==='human_confirmed_proposal'
  ? '用法：在对话里说清要办理的对象和目标，系统先生成操作建议，确认后才提交。'
  : capabilityUi.queryUsage
}
function skillUsageText(){
 return '用法：在对话里描述任务目标，系统会按当前授权工具组合处理。'
}
function capabilityUsageText(detail:{kind:'tool'|'skill';item:any}|null){
 if(!detail)return ''
 return detail.kind==='tool'?toolUsageText(detail.item):skillUsageText()
}
function escapePattern(value:string){return value.replace(/[.*+?^${}()|[\]\\]/g,'\\$&')}
function localizeCapabilityText(value:string){
 let text=value
 for(const [term,replacement] of Object.entries(capabilityUi.termReplacements||{}))text=text.replace(new RegExp(`\\b${escapePattern(term)}\\b`,'g'),String(replacement))
 for(const [key,name] of Object.entries(capabilityNames))text=text.replace(new RegExp(`\\b${escapePattern(key)}\\b`,'g'),name)
 text=text.replace(/\b[a-z][a-z0-9_]{2,}\b/gi,word=>capabilityName({key:word}))
 return text
}
function capabilityDescription(detail:{kind:'tool'|'skill';item:any}|null){
 if(!detail)return ''
 if(detail.kind==='skill')return localizeCapabilityText(String(detail.item.agent_description||detail.item.description||'该技能会按当前授权工具组合完成任务。')
  .split('\n')
  .filter(line=>!line.trim().startsWith('版本：')&&!/^第\s*[\d.]+\s*版/.test(line.trim())&&!/^#\s+/.test(line.trim()))
  .join('\n')
  .replace(/\n{3,}/g,'\n\n')
  .trim())
 return detail.item.description||''
}
function dependencyNames(item:any,optional=false){
 const keys=optional?(item.optional_dependencies||[]):(item.dependencies||[])
 return keys.map((key:string)=>capabilityName({key})).join('、')
}
function capabilityExample(detail:{kind:'tool'|'skill';item:any}|null){
 return domainCapabilityExample(detail)
}
function capabilityDetailMeta(detail:{kind:'tool'|'skill';item:any}|null){
 if(!detail)return ''
 const item=detail.item
 return detail.kind==='tool'
  ? `${permissionName(item.permission)} · ${capabilityMeta(item).typeName} · ${item.mode==='human_confirmed_proposal'?'需确认':'只读'}`
  : `技能 · 第 ${item.version} 版`
}
function auditSummary(entry:any){
 if(entry.summary)return entry.summary
 return entry.resource_id?`记录编号：${String(entry.resource_id)}`:'系统记录'
}
function auditBody(entry:any){
 const text=auditSummary(entry)
 return text.startsWith('记录编号：')?'系统记录':text
}
const auditPageCount=computed(()=>Math.max(1,Math.ceil(auditTotal.value/auditPageSize)))
async function loadAudit(nextPage=auditPage.value){
 auditLoading.value=true
 auditPage.value=Math.max(1,nextPage)
 try{
  const offset=(auditPage.value-1)*auditPageSize
  const result=await api(`/audit?offset=${offset}&limit=${auditPageSize}`)
  const records=Array.isArray(result)?result:result.items
  audit.value=(records||[]).length>auditPageSize?(records||[]).slice(offset,offset+auditPageSize):(records||[])
  auditTotal.value=Array.isArray(result)?result.length:result.total
 }catch(e:any){emit('error',e.message)}finally{auditLoading.value=false}
}
const filteredArchived=computed(()=>archived.value.filter(c=>c.title.toLowerCase().includes(archivedSearch.value.trim().toLowerCase())))
const delegationOptionMap=computed(()=>Object.fromEntries(delegationOptions.value.map((item:any)=>[item.process_key+'::'+item.node_key,item])))
const selectedDelegationOption=computed(()=>delegationOptionMap.value[delegationNode.value])
const proxyOptionMap=computed(()=>Object.fromEntries(proxyOptions.value.map((item:any)=>[item.process_key+'::'+item.node_key,item])))
const selectedProxyOption=computed(()=>proxyOptionMap.value[proxyNode.value])
const proxyAgentOptions=computed(()=>proxyUsers.value.filter((item:any)=>item.id!==proxyPrincipal.value))
watch(proxyPrincipal,value=>{if(proxyAgent.value===value)proxyAgent.value=proxyAgentOptions.value[0]?.id||''})
function modelProviderLabel(profile:any){return profile?.provider==='ollama'?'本机 Ollama':'OpenAI 兼容接口'}
function modelProfileCredential(profile:any){
 if(profile?.provider==='ollama')return '本机服务'
 if(profile?.company?.trusted_http_origin)return '内网可信服务'
 return profile?.company?.api_key_configured?'API Key 已配置':'API Key 未配置'
}
function modelProfileEndpoint(profile:any){return (profile?.provider==='ollama'?profile?.ollama?.base_url:profile?.company?.base_url)||'未配置服务地址'}
function cloneModelProfile(profile:any){return JSON.parse(JSON.stringify(profile))}
function applyModelConfig(result:any,preferredId?:string){
 modelProfiles.value=Array.isArray(result?.profiles)?result.profiles:result?[result]:[]
 activeModelProfileId.value=String(result?.active_profile_id||result?.id||'')
 const selected=modelProfiles.value.find(profile=>String(profile.id)===String(preferredId||activeModelProfileId.value))||result
 modelConfig.value=selected?cloneModelProfile(selected):null
 modelApiKey.value='';clearModelApiKey.value=false;creatingModelProfile.value=false
}
const navigation=computed(()=>[
 {key:'account',name:'账号信息',icon:Settings,allow:true},
 {key:'model',name:'模型配置',icon:BrainCircuit,allow:props.me.super_admin},
 {key:'agent-approvals',name:'审批授权',icon:ShieldCheck,allow:true},
 {key:'archived',name:'已归档的聊天',icon:Archive,allow:true},
 {key:'capabilities',name:'工具与技能',icon:Wrench,allow:true},
 {key:'admin',name:'人员与权限',icon:Users,allow:props.permissions.includes('user.manage')},
 {key:'workflows',name:'审批流程配置',icon:GitBranch,allow:props.permissions.includes('workflow.design')},
 {key:'audit',name:'操作审计',icon:ScrollText,allow:props.permissions.includes('audit.read')},
].filter(item=>item.allow))
watch(navigation,items=>{if(!items.some(item=>item.key===page.value))page.value='account'})
async function select(key:string){
 if(!navigation.value.some(item=>item.key===key))return
 page.value=key
 if(key==='audit'){
  audit.value=[];auditTotal.value=0;await loadAudit(1)
 }
 if(key==='archived')await loadArchived()
 if(key==='model')await loadModelConfig()
 if(key==='agent-approvals'){
  await loadAgentDelegations()
  if(props.permissions.includes('user.manage'))await loadApprovalProxies()
 }
}
watch(()=>props.initialPage,key=>{if(key)select(key)},{immediate:true})
async function loadModelConfig(){
 if(!props.me.super_admin)return
 modelLoading.value=true;modelSaved.value=''
 try{applyModelConfig(await api('/model-config'));modelDetailsOpen.value=false}catch(e:any){emit('error',e.message)}finally{modelLoading.value=false}
}
function modelPayload(){
 const config=modelConfig.value
 return {name:String(config.name||config.model||'模型配置').trim(),enabled:Boolean(config.enabled),provider:config.provider,
  company:{base_url:config.company?.base_url||'',model:config.company?.model||'',trusted_http_origin:config.company?.trusted_http_origin||'',
   proxy_url:config.company?.proxy_url||'',api_key:modelApiKey.value,clear_api_key:clearModelApiKey.value},
  ollama:{base_url:config.ollama?.base_url||'http://127.0.0.1:11434',model:config.ollama?.model||''},
  max_output_tokens:config.max_output_tokens,context_window:config.context_window,max_turns:config.max_turns,
  connect_timeout:config.connect_timeout,read_timeout:config.read_timeout}
}
async function saveModelConfig(){
 if(!modelConfig.value||modelSaving.value)return
 modelSaving.value=true;modelSaved.value=''
 try{
  const endpoint=creatingModelProfile.value?'/model-profiles':`/model-profiles/${modelConfig.value.id}`
  const saved=await api(endpoint,{method:creatingModelProfile.value?'POST':'PUT',body:JSON.stringify(modelPayload())})
  applyModelConfig(saved);modelDetailsOpen.value=false;modelSaved.value='模型配置已保存并生效，下一次任务会使用该模型。'
  emit('modelUpdated',saved.model||'未配置模型')
 }catch(e:any){emit('error',e.message)}finally{modelSaving.value=false}
}
async function activateModelProfile(profile:any){
 if(modelSaving.value)return
 if(String(profile.id)===activeModelProfileId.value){
  modelConfig.value=cloneModelProfile(profile);creatingModelProfile.value=false;modelApiKey.value='';clearModelApiKey.value=false;modelDetailsOpen.value=!modelDetailsOpen.value
  return
 }
 modelSaving.value=true;modelSaved.value=''
 try{
  const saved=await post(`/model-profiles/${profile.id}/activate`)
  applyModelConfig(saved,profile.id);modelDetailsOpen.value=false;modelSaved.value=`已切换到 ${profile.name}，下一次任务生效。`
  emit('modelUpdated',saved.model||'未配置模型')
 }catch(e:any){emit('error',e.message)}finally{modelSaving.value=false}
}
function newModelProfile(){
 creatingModelProfile.value=true;modelDetailsOpen.value=true;modelSaved.value='';modelApiKey.value='';clearModelApiKey.value=false
 modelConfig.value={id:'',name:'新模型配置',enabled:true,provider:'company',model:'',
  company:{base_url:'',model:'',trusted_http_origin:'',proxy_url:'',api_key_configured:false},
  ollama:{base_url:'http://127.0.0.1:11434',model:''},max_output_tokens:2048,context_window:32768,max_turns:12,connect_timeout:10,read_timeout:60}
}
async function deleteModelProfile(){
 if(!modelConfig.value?.id||modelSaving.value)return
 modelSaving.value=true;modelSaved.value=''
 try{
  const saved=await api(`/model-profiles/${modelConfig.value.id}`,{method:'DELETE'})
  applyModelConfig(saved);modelDetailsOpen.value=false;modelSaved.value='模型配置已删除。';emit('modelUpdated',saved.model||'未配置模型')
 }catch(e:any){emit('error',e.message)}finally{modelSaving.value=false}
}
async function loadArchived(){
 archivedLoading.value=true;archived.value=[]
 try{archived.value=await api('/conversations?archived=true')}catch(e:any){emit('error',e.message)}finally{archivedLoading.value=false}
}
async function unarchiveConversation(c:any){
 try{await post(`/conversations/${c.id}/unarchive`);await loadArchived()}catch(e:any){emit('error',e.message)}
}
async function loadAgentDelegations(){
 delegationsLoading.value=true;delegationNotice.value=''
 try{
  const [options,rows]=await Promise.all([api('/agent-approval-delegations/options'),api('/agent-approval-delegations')])
  delegationOptions.value=options;delegations.value=rows
  if(!delegationNode.value&&options.length)delegationNode.value=options[0].process_key+'::'+options[0].node_key
 }catch(e:any){emit('error',e.message)}finally{delegationsLoading.value=false}
}
function apiDate(value:string){
 if(!value)return null
 const date=new Date(value)
 return Number.isNaN(date.getTime())?null:date.toISOString()
}
async function saveAgentDelegation(){
 const option=selectedDelegationOption.value
 if(!option||delegationSaving.value)return
 delegationSaving.value=true;delegationNotice.value=''
 try{
  await post('/agent-approval-delegations',{process_key:option.process_key,node_key:option.node_key,decision:'APPROVE',
   reason:delegationReason.value||'用户在设置中授权 Agent 对该节点自动同意',valid_to:apiDate(delegationValidTo.value)})
  delegationReason.value='';delegationValidTo.value='';delegationNotice.value='自动审批授权已保存。'
  await loadAgentDelegations()
 }catch(e:any){emit('error',e.message)}finally{delegationSaving.value=false}
}
async function revokeDelegation(row:any){
 if(delegationSaving.value)return
 delegationSaving.value=true;delegationNotice.value=''
 try{await post(`/agent-approval-delegations/${row.id}/revoke`,{reason:'用户在设置中撤销 Agent 自动审批授权'});delegationNotice.value='自动审批授权已撤销。';await loadAgentDelegations()}catch(e:any){emit('error',e.message)}finally{delegationSaving.value=false}
}
function delegationLabel(row:any){
 const option=delegationOptionMap.value[row.process_key+'::'+row.node_key]
 return option?`${option.process_name} · ${option.node_name}`:`${row.process_key} · ${row.node_key}`
}
async function loadApprovalProxies(){
 proxyLoading.value=true;proxyNotice.value=''
 try{
  const [options,rows]=await Promise.all([api('/approval-proxies/options'),api('/approval-proxies')])
  proxyOptions.value=options.nodes||[];proxyUsers.value=options.users||[];proxyDelegations.value=rows||[]
  if(!proxyNode.value&&proxyOptions.value.length)proxyNode.value=proxyOptions.value[0].process_key+'::'+proxyOptions.value[0].node_key
  if(!proxyPrincipal.value&&proxyUsers.value.length)proxyPrincipal.value=proxyUsers.value[0].id
  if(!proxyAgent.value&&proxyUsers.value.length>1)proxyAgent.value=proxyUsers.value.find((item:any)=>item.id!==proxyPrincipal.value)?.id||''
 }catch(e:any){emit('error',e.message)}finally{proxyLoading.value=false}
}
async function saveApprovalProxy(){
 const option=selectedProxyOption.value
 if(!option||!proxyPrincipal.value||!proxyAgent.value||!proxyDecisions.value.length||!proxyReason.value.trim()||proxySaving.value)return
 proxySaving.value=true;proxyNotice.value=''
 try{
  await post('/approval-proxies',{principal_user_id:proxyPrincipal.value,proxy_user_id:proxyAgent.value,
   process_key:option.process_key,node_key:option.node_key,allowed_decisions:proxyDecisions.value,
   reason:proxyReason.value,valid_from:apiDate(proxyValidFrom.value),valid_to:apiDate(proxyValidTo.value)})
  await loadApprovalProxies()
  proxyReason.value='';proxyValidFrom.value='';proxyValidTo.value='';proxyNotice.value='人工审批代理已保存。'
 }catch(e:any){emit('error',e.message)}finally{proxySaving.value=false}
}
async function revokeApprovalProxy(row:any){
 if(proxySaving.value)return
 proxySaving.value=true;proxyNotice.value=''
 try{await post(`/approval-proxies/${row.id}/revoke`,{reason:'管理员在审批授权设置中撤销人工代理'});await loadApprovalProxies();proxyNotice.value='人工审批代理已撤销。'}catch(e:any){emit('error',e.message)}finally{proxySaving.value=false}
}
function proxyLabel(row:any){
 const option=proxyOptionMap.value[row.process_key+'::'+row.node_key]
 return option?`${option.process_name} · ${option.node_name}`:`${row.process_key} · ${row.node_key}`
}
function decisionNames(values:string[]){const labels:Record<string,string>={APPROVE:'同意',REJECT:'驳回',RETURN:'退回修改'};return values.map(value=>labels[value]||value).join('、')}
async function avatarDataUrl(file:File){
 if(!/^image\/(png|jpeg|webp)$/.test(file.type))throw new Error('头像只支持 PNG、JPG 或 WebP 图片')
 if(file.size>5*1024*1024)throw new Error('头像图片不能超过 5MB')
 const url=URL.createObjectURL(file)
 try{
  const image=await new Promise<HTMLImageElement>((resolve,reject)=>{const img=new Image();img.onload=()=>resolve(img);img.onerror=()=>reject(new Error('头像图片无法读取'));img.src=url})
  const size=Math.min(image.naturalWidth,image.naturalHeight)
  if(!size)throw new Error('头像图片尺寸无效')
  const canvas=document.createElement('canvas'),target=128
  canvas.width=target;canvas.height=target
  const ctx=canvas.getContext('2d')
  if(!ctx)throw new Error('当前浏览器不支持头像处理')
  ctx.imageSmoothingQuality='high'
  ctx.drawImage(image,(image.naturalWidth-size)/2,(image.naturalHeight-size)/2,size,size,0,0,target,target)
  return canvas.toDataURL('image/png')
 }finally{URL.revokeObjectURL(url)}
}
async function uploadAvatar(event:Event){
 const input=event.target as HTMLInputElement,file=input.files?.[0];input.value=''
 if(!file||avatarUploading.value)return
 avatarUploading.value=true
 try{const avatar_url=await avatarDataUrl(file);const updated=await api('/me/avatar',{method:'PUT',body:JSON.stringify({avatar_url})});Object.assign(props.me,updated)}
 catch(e:any){emit('error',e.message)}
 finally{avatarUploading.value=false}
}
async function clearAvatar(){
 if(avatarUploading.value)return
 avatarUploading.value=true
 try{const updated=await api('/me/avatar',{method:'PUT',body:JSON.stringify({avatar_url:''})});Object.assign(props.me,updated)}
 catch(e:any){emit('error',e.message)}
 finally{avatarUploading.value=false}
}
</script>
<template>
<main class="settings-page">
 <aside class="settings-sidebar">
  <button class="settings-back" @click="emit('close')"><ArrowLeft :size="18"/>返回对话</button>
  <h1>设置</h1>
  <label class="search"><Search :size="16"/><input v-model="search" aria-label="搜索设置" placeholder="搜索设置…"/></label>
  <nav aria-label="设置分类"><button v-for="item in navigation.filter(item=>item.name.includes(search))" :key="item.key" :class="{active:page===item.key}" :aria-current="page===item.key?'page':undefined" @click="select(item.key)"><component :is="item.icon" :size="18"/>{{item.name}}</button></nav>
  <p v-if="!navigation.some(item=>item.name.includes(search))" class="muted small">没有匹配的设置</p>
  <p class="settings-owner muted">{{me.display_name}}</p>
 </aside>
 <section class="settings-content" :key="me.id+me.authorization_hash" aria-label="设置内容">
  <div class="settings-inner">
   <template v-if="page==='account'">
    <h2>账号信息</h2><p class="muted">维护当前登录账号的基础信息。</p>
    <div class="account-center-layout">
     <section class="surface account-person-card">
      <input ref="avatarInput" class="avatar-file-input" type="file" accept="image/png,image/jpeg,image/webp" @change="uploadAvatar"/>
      <button type="button" class="account-avatar-button" :disabled="avatarUploading" :title="avatarUploading?'正在处理头像':'点击上传头像'" @click="avatarInput?.click()">
       <span class="account-avatar"><img v-if="me.avatar_url" :src="me.avatar_url" alt=""/><template v-else>{{me.display_name[0]}}</template></span>
       <small>{{avatarUploading?'正在处理…':'点击头像上传'}}</small>
      </button>
      <strong>{{me.display_name}}</strong>
      <span class="muted small">{{me.department||'未设置部门'}}</span>
      <button v-if="me.avatar_url" type="button" class="account-remove-avatar" :disabled="avatarUploading" @click="clearAvatar"><Trash2 :size="15"/>移除头像</button>
     </section>
     <section class="surface account-info-card">
      <div class="account-info-head"><strong>基本资料</strong></div>
      <dl class="account-profile-facts"><dt>姓名</dt><dd>{{me.display_name}}</dd><dt>登录名</dt><dd>{{me.username}}</dd><dt>部门</dt><dd>{{me.department||'未设置'}}</dd><dt>身份</dt><dd>{{me.super_admin?'超级管理员':'普通用户'}}</dd><dt>系统时区</dt><dd>Asia/Shanghai</dd></dl>
     </section>
    </div>
    <h3 class="settings-section-title">外观</h3>
   <section class="surface appearance-setting" aria-labelledby="appearance-title">
     <div><strong id="appearance-title">颜色模式</strong><small class="muted">选择更适合当前环境的工作台明暗外观，设置会保存在本机。</small></div>
     <div class="theme-options" role="group" aria-label="颜色模式">
      <button :class="{active:colorTheme==='light'}" :aria-pressed="colorTheme==='light'" @click="emit('themeChange','light')"><Sun :size="17"/>浅色</button>
      <button :class="{active:colorTheme==='dark'}" :aria-pressed="colorTheme==='dark'" @click="emit('themeChange','dark')"><Moon :size="17"/>深色</button>
     </div>
   </section>
   </template>
   <template v-else-if="page==='model'&&me.super_admin">
    <div class="section-heading model-config-heading"><div><h2>模型配置</h2><p class="muted">保存多个模型服务并一键切换。API Key 只会保存，不会回显明文。</p></div><button type="button" class="model-add-button" @click="newModelProfile">新增模型配置</button></div>
    <p v-if="modelLoading" role="status">正在读取模型配置…</p>
    <form v-else-if="modelConfig" class="model-config-form surface" @submit.prevent="saveModelConfig">
     <div class="model-selection-heading"><strong>选择模型配置</strong><small class="muted">点击模型即可切换，新的任务会使用当前生效模型。</small></div>
     <div class="model-profile-list">
      <button v-for="profile in modelProfiles" :key="profile.id" type="button" class="model-summary-card"
       :class="{active:String(profile.id)===activeModelProfileId}" :aria-pressed="String(profile.id)===activeModelProfileId"
       @click="activateModelProfile(profile)">
       <span class="model-summary-icon"><BrainCircuit :size="19"/></span>
       <span class="model-summary-main">
        <strong>{{profile.name}}</strong>
        <small>{{profile.model||'未配置模型'}} · {{modelProviderLabel(profile)}}</small>
       </span>
       <span class="model-profile-status" :class="{active:String(profile.id)===activeModelProfileId}">{{String(profile.id)===activeModelProfileId?'当前使用':'可切换'}}</span>
       <span class="model-summary-meta">
        <span>{{modelProfileCredential(profile)}}</span>
        <small>{{modelProfileEndpoint(profile)}}</small>
       </span>
       <span class="model-summary-action">{{String(profile.id)===activeModelProfileId?(modelDetailsOpen?'收起配置':'编辑配置'):'切换'}}</span>
      </button>
     </div>
     <div v-if="modelDetailsOpen" class="model-config-details">
      <div class="form-grid compact">
       <label>配置名称<input v-model.trim="modelConfig.name" placeholder="例如：生产模型 / 本机模型"/></label>
       <label class="check-label model-enabled-check"><input v-model="modelConfig.enabled" type="checkbox"/>启用该模型配置</label>
      </div>
      <div class="model-config-row service-only">
       <label>服务类型<select v-model="modelConfig.provider"><option value="company">OpenAI 兼容接口</option><option value="ollama">本机 Ollama</option></select></label>
      </div>
      <template v-if="modelConfig.provider==='company'">
       <div class="form-grid compact">
        <label>Base URL<input v-model="modelConfig.company.base_url" placeholder="https://api.example.com/v1"/></label>
        <label>模型名称<input v-model="modelConfig.company.model" placeholder="Qwen3-30B-A3B-Instruct"/></label>
       </div>
       <div class="form-grid compact">
        <label>可信 HTTP Origin<input v-model="modelConfig.company.trusted_http_origin" placeholder="仅内网 HTTP 模型需要填写"/></label>
        <label>代理地址<input v-model="modelConfig.company.proxy_url" placeholder="可选"/></label>
       </div>
       <div class="form-grid compact">
        <label>API Key<input v-model="modelApiKey" type="password" autocomplete="new-password" :placeholder="modelConfig.company.api_key_configured?'已配置，留空则不修改':'请输入 API Key（可为空）'"/></label>
        <label class="check-label model-clear-key"><input v-model="clearModelApiKey" type="checkbox"/>清空已保存 API Key</label>
       </div>
      </template>
      <template v-else>
       <div class="form-grid compact">
        <label>Ollama 地址<input v-model="modelConfig.ollama.base_url" placeholder="http://127.0.0.1:11434"/></label>
        <label>模型名称<input v-model="modelConfig.ollama.model" placeholder="qwen2.5:7b"/></label>
       </div>
      </template>
      <div class="form-grid compact">
       <label>最大输出 token<input v-model.number="modelConfig.max_output_tokens" type="number" min="256" max="8192"/></label>
       <label>上下文窗口 token<input v-model.number="modelConfig.context_window" type="number" min="4096" max="2000000" step="1024"/></label>
       <label>最大 ReAct 轮次<input v-model.number="modelConfig.max_turns" type="number" min="1" max="30"/></label>
       <label>连接超时（秒）<input v-model.number="modelConfig.connect_timeout" type="number" min="1" max="20" step="0.5"/></label>
       <label>读取超时（秒）<input v-model.number="modelConfig.read_timeout" type="number" min="1" max="120" step="0.5"/></label>
      </div>
     </div>
     <div v-if="modelDetailsOpen" class="model-config-footer">
      <button v-if="!creatingModelProfile&&modelProfiles.length>1" type="button" class="danger ghost" :disabled="modelSaving" @click="deleteModelProfile">删除此配置</button>
      <span v-else class="muted small">{{creatingModelProfile?'保存后将自动切换到新模型':'至少保留一个模型配置'}}</span>
      <button class="primary" :disabled="modelSaving||!modelConfig.name">{{modelSaving?'正在保存…':creatingModelProfile?'保存并启用':'保存模型配置'}}</button>
     </div>
     <p v-if="modelSaved" class="muted small">{{modelSaved}}</p>
   </form>
   </template>
   <template v-else-if="page==='agent-approvals'">
    <div class="agent-approval-head"><div><h2>审批授权</h2><p class="muted">分别维护本人授予 Agent 的自动审批，以及管理员配置的人工审批代理。</p></div><span>{{delegations.filter(d=>d.active).length+(permissions.includes('user.manage')?proxyDelegations.filter(d=>d.active).length:0)}} 个有效授权</span></div>
    <p v-if="delegationsLoading" role="status">正在读取自动审批授权…</p>
    <template v-else>
     <div class="agent-approval-grid">
     <section class="surface agent-approval-card">
      <div class="agent-card-title"><span><ShieldCheck :size="18"/></span><div><h3>授权节点</h3><p class="muted">仅显示流程里已开启自动审批的低风险节点。</p></div></div>
      <div v-if="delegationOptions.length" class="agent-delegation-form">
       <label>可授权节点<select v-model="delegationNode"><option v-for="option in delegationOptions" :key="option.process_key+'::'+option.node_key" :value="option.process_key+'::'+option.node_key">{{option.process_name}} · {{option.node_name}}（第 {{option.version}} 版{{option.has_auto_policy?' · 已设安全条件':''}}）</option></select></label>
       <label>授权原因<textarea v-model="delegationReason" rows="2" :placeholder="capabilityUi.delegationReasonPlaceholder"/></label>
       <label>有效期至<input v-model="delegationValidTo" type="datetime-local"/><small class="muted">留空表示长期有效，撤销后立即失效。</small></label>
       <button class="primary" :disabled="delegationSaving||!selectedDelegationOption" @click="saveAgentDelegation">{{delegationSaving?'正在保存…':'授权自动同意'}}</button>
      </div>
      <div v-else class="agent-empty-state"><ShieldOff :size="22"/><strong>暂无可授权节点</strong><p class="muted">先在审批流程里为低风险节点开启自动审批，再回到这里授权。</p><button v-if="permissions.includes('workflow.design')" type="button" @click="select('workflows')">去配置流程</button></div>
     </section>
     <p v-if="delegationNotice" class="muted small">{{delegationNotice}}</p>
     <section class="surface agent-delegation-list" aria-label="我的自动审批授权">
      <div class="section-heading"><h3>授权记录</h3><small class="muted">{{delegations.length}} 条记录</small></div>
      <article v-for="row in delegations" :key="row.id" class="agent-delegation-row" :class="{inactive:!row.active}">
       <div><strong>{{delegationLabel(row)}}</strong><p class="muted small">{{row.reason}}</p><small class="muted">创建于 {{shanghai(row.created_at)}}<span v-if="row.valid_to"> · 有效期至 {{shanghai(row.valid_to)}}</span><span v-if="row.revoked_at"> · 已于 {{shanghai(row.revoked_at)}} 撤销</span></small></div>
       <span v-if="row.active" class="status-pill ok"><ShieldCheck :size="14"/>有效</span>
       <span v-else class="status-pill muted-pill"><ShieldOff :size="14"/>已撤销</span>
      <button v-if="row.active" class="danger-outline" :disabled="delegationSaving" @click="revokeDelegation(row)">撤销授权</button>
     </article>
      <div v-if="!delegations.length" class="agent-empty-state record"><ShieldOff :size="20"/><strong>还没有授权记录</strong><p class="muted">授权后会显示节点、有效期和撤销状态。</p></div>
     </section>
     </div>
     <template v-if="permissions.includes('user.manage')">
      <div class="section-heading approval-subheading"><div><h2>人工审批代理</h2><p class="muted">代理人以本人身份办理委托人的既有席位；不改变席位负责人、不增加票数，也不会形成多级代办。</p></div><small class="muted">{{proxyDelegations.filter(d=>d.active).length}} 个有效代理</small></div>
      <p v-if="proxyLoading" role="status">正在读取人工审批代理…</p>
      <div v-else class="agent-approval-grid">
       <section class="surface agent-approval-card">
        <div class="agent-card-title"><span><Users :size="18"/></span><div><h3>配置代理范围</h3><p class="muted">只显示流程模板中明确允许人工代理的节点。</p></div></div>
        <div v-if="proxyOptions.length&&proxyUsers.length>1" class="agent-delegation-form">
         <label>流程节点<select v-model="proxyNode"><option v-for="option in proxyOptions" :key="option.process_key+'::'+option.node_key" :value="option.process_key+'::'+option.node_key">{{option.process_name}} · {{option.node_name}}（第 {{option.version}} 版）</option></select></label>
         <label>原审批责任人<select v-model="proxyPrincipal"><option v-for="person in proxyUsers" :key="person.id" :value="person.id">{{person.display_name}} · {{person.department||'未设置部门'}}</option></select></label>
         <label>代理审批人<select v-model="proxyAgent"><option value="" disabled>请选择不同人员</option><option v-for="person in proxyAgentOptions" :key="person.id" :value="person.id">{{person.display_name}} · {{person.department||'未设置部门'}}</option></select></label>
         <fieldset><legend>允许决定</legend><label class="check-label"><input v-model="proxyDecisions" type="checkbox" value="APPROVE"/>同意</label><label class="check-label"><input v-model="proxyDecisions" type="checkbox" value="REJECT"/>驳回</label><label class="check-label"><input v-model="proxyDecisions" type="checkbox" value="RETURN"/>退回修改</label></fieldset>
         <label>代理原因<textarea v-model="proxyReason" rows="2" placeholder="说明请假、职责覆盖或临时代理依据"/></label>
         <div class="form-grid"><label>生效时间<input v-model="proxyValidFrom" type="datetime-local"/><small class="muted">留空表示立即生效。</small></label><label>有效期至<input v-model="proxyValidTo" type="datetime-local"/><small class="muted">留空表示撤销前有效。</small></label></div>
         <button class="primary" :disabled="proxySaving||!selectedProxyOption||!proxyPrincipal||!proxyAgent||proxyPrincipal===proxyAgent||!proxyDecisions.length||!proxyReason.trim()" @click="saveApprovalProxy">{{proxySaving?'正在保存…':'保存人工代理'}}</button>
        </div>
        <div v-else class="agent-empty-state"><ShieldOff :size="22"/><strong>暂无可配置节点</strong><p class="muted">先在审批流程节点开启人工代理，并确保至少有两名有效用户。</p><button v-if="permissions.includes('workflow.design')" type="button" @click="select('workflows')">去配置流程</button></div>
       </section>
       <section class="surface agent-delegation-list" aria-label="人工审批代理记录">
        <div class="section-heading"><h3>代理记录</h3><small class="muted">{{proxyDelegations.length}} 条记录</small></div>
        <p v-if="proxyNotice" class="muted small">{{proxyNotice}}</p>
        <article v-for="row in proxyDelegations" :key="row.id" class="agent-delegation-row" :class="{inactive:!row.active}">
         <div><strong>{{row.principal_user.display_name}} → {{row.proxy_user.display_name}}</strong><p class="muted small">{{proxyLabel(row)}} · {{decisionNames(row.allowed_decisions)}}</p><p class="muted small">{{row.reason}}</p><small class="muted">创建于 {{shanghai(row.created_at)}}<span v-if="row.valid_from"> · 生效于 {{shanghai(row.valid_from)}}</span><span v-if="row.valid_to"> · 有效期至 {{shanghai(row.valid_to)}}</span><span v-if="row.revoked_at"> · 已于 {{shanghai(row.revoked_at)}} 撤销</span></small></div>
         <span v-if="row.active" class="status-pill ok"><ShieldCheck :size="14"/>有效</span><span v-else class="status-pill muted-pill"><ShieldOff :size="14"/>已撤销</span>
         <button v-if="row.active" class="danger-outline" :disabled="proxySaving" @click="revokeApprovalProxy(row)">撤销代理</button>
        </article>
        <div v-if="!proxyDelegations.length" class="agent-empty-state record"><ShieldOff :size="20"/><strong>还没有人工代理记录</strong><p class="muted">代理启用、到期和撤销都会保留审计。</p></div>
       </section>
      </div>
     </template>
    </template>
   </template>
   <template v-else-if="page==='archived'">
    <div class="section-heading archived-heading"><div><h2>已归档的聊天</h2><p class="muted">归档后的会话会从最近对话隐藏，但仍可在这里查看或取消归档。</p></div><small class="muted">{{archived.length}} 个聊天</small></div>
    <label class="archived-search"><Search :size="16"/><input v-model="archivedSearch" placeholder="搜索已归档聊天" aria-label="搜索已归档聊天"/></label>
    <p v-if="archivedLoading" role="status">正在读取归档聊天…</p>
    <div v-else class="archived-chat-list surface">
     <article v-for="c in filteredArchived" :key="c.id" class="archived-chat-row">
      <button class="archived-chat-main" @click="emit('openConversation',c)"><MessageSquare :size="16"/><span><strong>{{c.title}}</strong><small class="muted">{{shanghai(c.created_at)}} · 只读查看</small></span></button>
      <button class="archived-chat-action" @click="unarchiveConversation(c)"><RotateCcw :size="15"/>取消归档</button>
     </article>
     <p v-if="!archived.length" class="muted archived-empty">还没有归档聊天。</p>
     <p v-else-if="!filteredArchived.length" class="muted">没有匹配的归档聊天。</p>
   </div>
   </template>
   <template v-else-if="page==='capabilities'">
    <div class="capability-page-head"><div><h2>工具与技能</h2><p class="muted">{{capabilityUi.pageHelp}}</p></div><small class="muted">{{filteredTools.length}} 个工具 · {{filteredSkills.length}} 个技能</small></div>
    <div ref="capabilityToolbar" class="capability-toolbar capability-toolbar-v2">
     <div class="capability-tabs" role="tablist" aria-label="能力类型">
      <button role="tab" :class="{active:capabilityTab==='all'}" :aria-selected="capabilityTab==='all'" @click="capabilityTab='all'">全部<span>{{filteredTools.length+filteredSkills.length}}</span></button>
      <button role="tab" :class="{active:capabilityTab==='tools'}" :aria-selected="capabilityTab==='tools'" @click="capabilityTab='tools'"><Wrench :size="15"/>工具<span>{{filteredTools.length}}</span></button>
      <button role="tab" :class="{active:capabilityTab==='skills'}" :aria-selected="capabilityTab==='skills'" @click="capabilityTab='skills'"><Layers :size="15"/>技能<span>{{filteredSkills.length}}</span></button>
     </div>
     <label class="capability-search-pill"><Search :size="15"/><input v-model="capabilitySearch" placeholder="查询工具、权限或说明" @focus="capabilityDropdown=''"/></label>
     <div class="capability-select" :class="{open:capabilityDropdown==='department'}">
      <button type="button" class="capability-select-button" @click="toggleCapabilityDropdown('department')"><span>部门</span><strong>{{capabilityDepartmentLabel}}</strong><i aria-hidden="true"></i></button>
      <div v-if="capabilityDropdown==='department'" class="capability-select-menu">
       <button type="button" :class="{active:!capabilityDepartment}" @click="setCapabilityDepartment('')">全部部门</button>
       <button v-for="[key,name] in capabilityDepartments" :key="key" type="button" :class="{active:capabilityDepartment===key}" @click="setCapabilityDepartment(String(key))">{{name}}</button>
      </div>
     </div>
     <div class="capability-select" :class="{open:capabilityDropdown==='type'}">
      <button type="button" class="capability-select-button" @click="toggleCapabilityDropdown('type')"><span>类型</span><strong>{{capabilityTypeLabel}}</strong><i aria-hidden="true"></i></button>
      <div v-if="capabilityDropdown==='type'" class="capability-select-menu">
       <button type="button" :class="{active:!capabilityType}" @click="setCapabilityType('')">全部类型</button>
       <button v-for="[key,name] in capabilityTypes" :key="key" type="button" :class="{active:capabilityType===key}" @click="setCapabilityType(String(key))">{{name}}</button>
      </div>
     </div>
    </div>
    <template v-if="showCapabilityTools">
    <h3 class="settings-section-title">工具</h3>
    <div class="capability-groups capability-grid capability-section-list" role="tabpanel" aria-label="工具">
     <section v-for="department in groupedTools" :key="department.key" class="capability-department">
      <h3 class="capability-department-title"><span>{{department.name}}</span><button type="button" class="capability-type-pill total" :class="{active:capabilityGroupTab('tools',department)==='all'}" @click="setCapabilityGroupTab('tools',department,'all')">{{department.types.reduce((sum,type)=>sum+type.items.length,0)}} 项</button><button v-for="type in department.types" :key="type.key" type="button" class="capability-type-pill" :class="{active:capabilityGroupTab('tools',department)===type.key}" @click="setCapabilityGroupTab('tools',department,type.key)">{{type.name}}<small>{{type.items.length}} 项</small></button></h3>
      <div v-for="category in visibleCapabilityCategoryGroups('tools',department)" :key="category.name" class="capability-category-block">
       <div class="capability-category-heading"><strong>{{category.name}}</strong><small class="muted">{{category.items.length}} 项</small></div>
       <article v-for="tool in category.items" :key="tool.key" class="capability-row" role="button" tabindex="0" @click="selectedCapability={kind:'tool',item:tool}" @keydown.enter.prevent="selectedCapability={kind:'tool',item:tool}" @keydown.space.prevent="selectedCapability={kind:'tool',item:tool}"><div><h3><Wrench :size="15"/>{{capabilityName(tool)}}</h3></div><div class="capability-row-meta"><small class="muted">{{capabilityMeta(tool).typeName}} · {{tool.mode==='human_confirmed_proposal'?'需确认':'只读'}}</small></div></article>
      </div>
     </section>
    </div>
    <p v-if="!capabilities.tools.length" class="muted">还没有分配可用工具，请联系管理员。</p>
    <p v-else-if="!filteredTools.length" class="muted">没有匹配的工具。</p>
    </template>
    <template v-if="showCapabilitySkills">
    <h3 class="settings-section-title">技能</h3>
    <div class="capability-groups capability-grid capability-section-list" role="tabpanel" aria-label="技能">
     <section v-for="department in groupedSkills" :key="department.key" class="capability-department">
      <h3 class="capability-department-title"><span>{{department.name}}</span><button type="button" class="capability-type-pill total" :class="{active:capabilityGroupTab('skills',department)==='all'}" @click="setCapabilityGroupTab('skills',department,'all')">{{department.types.reduce((sum,type)=>sum+type.items.length,0)}} 项</button><button v-for="type in department.types" :key="type.key" type="button" class="capability-type-pill" :class="{active:capabilityGroupTab('skills',department)===type.key}" @click="setCapabilityGroupTab('skills',department,type.key)">{{type.name}}<small>{{type.items.length}} 项</small></button></h3>
      <div v-for="category in visibleCapabilityCategoryGroups('skills',department)" :key="category.name" class="capability-category-block">
       <div class="capability-category-heading"><strong>{{category.name}}</strong><small class="muted">{{category.items.length}} 项</small></div>
       <article v-for="skill in category.items" :key="skill.key" class="capability-row" role="button" tabindex="0" @click="selectedCapability={kind:'skill',item:skill}" @keydown.enter.prevent="selectedCapability={kind:'skill',item:skill}" @keydown.space.prevent="selectedCapability={kind:'skill',item:skill}"><div><h3><Layers :size="15"/>{{capabilityName(skill)}}</h3></div><div class="capability-row-meta"><small class="muted">技能 · 可用</small></div></article>
      </div>
     </section>
    </div>
    <p v-if="!capabilities.skills.length" class="muted">还没有分配可用技能，请联系管理员。</p>
    <p v-if="capabilities.skills.length&&!filteredSkills.length" class="muted">没有匹配的技能。</p>
   </template>
   <div v-if="selectedCapability" class="modal-shade capability-detail-shade" @click.self="selectedCapability=null">
    <section class="modal capability-detail-modal" role="dialog" aria-modal="true" aria-label="能力详情">
     <div class="capability-detail-head"><div><h2>{{capabilityName(selectedCapability.item)}}</h2><p class="muted">{{capabilityDetailMeta(selectedCapability)}}</p></div><button type="button" class="icon-button" aria-label="关闭详情" @click="selectedCapability=null">×</button></div>
     <div class="capability-detail-body">
      <dl class="capability-detail-facts"><dt>所属部门</dt><dd>{{capabilityMeta(selectedCapability.item).departmentName}}</dd><dt>业务类别</dt><dd>{{capabilityCategoryName(selectedCapability.item)}}</dd><dt>能力类型</dt><dd>{{selectedCapability.kind==='tool'?'工具':'技能'}}</dd></dl>
      <section><h3>{{selectedCapability.kind==='skill'?'智能体技能说明':'说明'}}</h3><p class="preserve">{{capabilityDescription(selectedCapability)}}</p></section>
      <section v-if="selectedCapability.kind==='skill'&&selectedCapability.item.dependencies?.length"><h3>会调用的工具</h3><p>{{dependencyNames(selectedCapability.item)}}</p></section>
      <section v-if="selectedCapability.kind==='skill'&&selectedCapability.item.optional_dependencies?.length"><h3>可选工具</h3><p>{{dependencyNames(selectedCapability.item,true)}}</p></section>
      <section><h3>怎么用</h3><p>{{capabilityUsageText(selectedCapability)}}</p></section>
      <section><h3>使用示例</h3><p class="capability-example">{{capabilityExample(selectedCapability)}}</p></section>
     </div>
    </section>
   </div>
   </template>
   <AdminPanel v-else-if="page==='admin'&&permissions.includes('user.manage')" @error="emit('error',$event)"/>
   <WorkflowPanel v-else-if="page==='workflows'&&permissions.includes('workflow.design')" @error="emit('error',$event)"/>
   <template v-else-if="page==='audit'&&permissions.includes('audit.read')"><div class="audit-page-head"><div><h2>操作审计</h2><p class="muted">记录关键登录、授权、流程和智能体任务操作。</p></div><span class="audit-count">{{auditTotal}} 条记录</span></div><p v-if="auditLoading" role="status" class="audit-loading">正在读取审计记录…</p><section v-else class="audit-list" aria-label="操作审计记录"><article v-for="entry in audit" :key="entry.id" class="audit-card"><div class="audit-card-main"><span class="audit-dot"/><div><div class="audit-card-title"><strong>{{auditName(entry.action)}}</strong><span v-if="entry.actor_name">操作人：{{entry.actor_name}}</span></div><p>{{auditBody(entry)}}</p><small class="muted">记录编号：{{String(entry.resource_id||entry.id)}}</small></div></div><time>{{shanghai(entry.created_at)}}</time></article><p v-if="!audit.length" class="audit-empty muted">暂无审计记录。</p></section><div class="audit-pagination" v-if="auditTotal>auditPageSize"><span>本页 {{audit.length}} 条，共 {{auditTotal}} 条</span><div><button :disabled="auditLoading||auditPage<=1" @click="loadAudit(auditPage-1)">上一页</button><span>{{auditPage}} / {{auditPageCount}}</span><button :disabled="auditLoading||auditPage>=auditPageCount" @click="loadAudit(auditPage+1)">下一页</button></div></div></template>
  </div>
 </section>
</main>
</template>
