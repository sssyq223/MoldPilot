<script setup lang="ts">
import {computed,ref,watch} from 'vue'
import {ArrowLeft,Settings,Wrench,Users,GitBranch,ScrollText,Search,Layers,Sun,Moon,Archive,MessageSquare,RotateCcw,BrainCircuit} from 'lucide-vue-next'
import type {ColorTheme} from '../theme'
import {api,post,shanghai} from '../api'
import {capabilityMeta,capabilityName,groupedCapabilities,permissionName,auditName} from '../uiText'
import AdminPanel from './AdminPanel.vue'
import WorkflowPanel from './WorkflowPanel.vue'
const props=defineProps<{me:any;permissions:string[];capabilities:any;modelName:string;colorTheme:ColorTheme}>()
const emit=defineEmits<{close:[];error:[message:string];themeChange:[theme:ColorTheme];openConversation:[conversation:any];modelUpdated:[model:string]}>()
const page=ref('account'),search=ref(''),audit=ref<any[]>([]),auditLoading=ref(false)
const archived=ref<any[]>([]),archivedSearch=ref(''),archivedLoading=ref(false)
const capabilitySearch=ref(''),capabilityDepartment=ref(''),capabilityType=ref(''),capabilityTab=ref<'tools'|'skills'|'all'>('tools')
const modelConfig=ref<any|null>(null),modelLoading=ref(false),modelSaving=ref(false),modelApiKey=ref(''),clearModelApiKey=ref(false),modelSaved=ref('')
const allCapabilityItems=computed(()=>([...(props.capabilities.tools||[]),...(props.capabilities.skills||[])]))
const capabilityDepartments=computed(()=>Array.from(new Map(allCapabilityItems.value.map((item:any)=>{const meta=capabilityMeta(item);return [meta.department,meta.departmentName]})).entries()))
const capabilityTypes=computed(()=>Array.from(new Map(allCapabilityItems.value.map((item:any)=>{const meta=capabilityMeta(item);return [meta.type,meta.typeName]})).entries()))
function capabilityMatches(item:any){
 const meta=capabilityMeta(item),keyword=capabilitySearch.value.trim().toLowerCase()
 if(capabilityDepartment.value&&meta.department!==capabilityDepartment.value)return false
 if(capabilityType.value&&meta.type!==capabilityType.value)return false
 if(!keyword)return true
 return [item.key,capabilityName(item.key),item.description,item.permission,permissionName(item.permission||''),meta.departmentName,meta.typeName].some(value=>String(value||'').toLowerCase().includes(keyword))
}
const filteredTools=computed(()=>(props.capabilities.tools||[]).filter(capabilityMatches))
const filteredSkills=computed(()=>(props.capabilities.skills||[]).filter(capabilityMatches))
const groupedTools=computed(()=>groupedCapabilities(filteredTools.value))
const groupedSkills=computed(()=>groupedCapabilities(filteredSkills.value))
const showCapabilityTools=computed(()=>capabilityTab.value==='tools'||capabilityTab.value==='all')
const showCapabilitySkills=computed(()=>capabilityTab.value==='skills'||capabilityTab.value==='all')
const filteredArchived=computed(()=>archived.value.filter(c=>c.title.toLowerCase().includes(archivedSearch.value.trim().toLowerCase())))
const navigation=computed(()=>[
 {key:'account',name:'账号与模型',icon:Settings,allow:true},
 {key:'model',name:'模型配置',icon:BrainCircuit,allow:props.me.super_admin},
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
  auditLoading.value=true;audit.value=[]
 try{audit.value=await api('/audit')}catch(e:any){emit('error',e.message)}finally{auditLoading.value=false}
 }
 if(key==='archived')await loadArchived()
 if(key==='model')await loadModelConfig()
}
async function loadModelConfig(){
 if(!props.me.super_admin)return
 modelLoading.value=true;modelSaved.value=''
 try{modelConfig.value=await api('/model-config');modelApiKey.value='';clearModelApiKey.value=false}catch(e:any){emit('error',e.message)}finally{modelLoading.value=false}
}
async function saveModelConfig(){
 if(!modelConfig.value||modelSaving.value)return
 modelSaving.value=true;modelSaved.value=''
 try{
  const payload={...modelConfig.value,company:{...modelConfig.value.company,api_key:modelApiKey.value,clear_api_key:clearModelApiKey.value}}
  const saved=await api('/model-config',{method:'PUT',body:JSON.stringify(payload)})
  modelConfig.value=saved;modelApiKey.value='';clearModelApiKey.value=false;modelSaved.value='模型配置已保存，下一次任务会使用新配置。'
  emit('modelUpdated',saved.model||'未配置模型')
 }catch(e:any){emit('error',e.message)}finally{modelSaving.value=false}
}
async function loadArchived(){
 archivedLoading.value=true;archived.value=[]
 try{archived.value=await api('/conversations?archived=true')}catch(e:any){emit('error',e.message)}finally{archivedLoading.value=false}
}
async function unarchiveConversation(c:any){
 try{await post(`/conversations/${c.id}/unarchive`);await loadArchived()}catch(e:any){emit('error',e.message)}
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
    <h2>账号与模型</h2><p class="muted">当前账号的信息及智能体使用的模型。</p>
    <dl class="settings-facts surface"><dt>姓名</dt><dd>{{me.display_name}}</dd><dt>登录名</dt><dd>{{me.username}}</dd><dt>部门</dt><dd>{{me.department||'未设置'}}</dd><dt>身份</dt><dd>{{me.super_admin?'超级管理员':'普通用户'}}</dd><dt>模型</dt><dd>{{modelName}}</dd><dt>系统时区</dt><dd>Asia/Shanghai</dd></dl>
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
    <div class="section-heading model-config-heading"><div><h2>模型配置</h2><p class="muted">配置工作台智能体调用的模型服务。API Key 只会保存，不会回显明文。</p></div><small class="muted">{{modelConfig?.enabled?'已启用':'未启用'}}</small></div>
    <p v-if="modelLoading" role="status">正在读取模型配置…</p>
    <form v-else-if="modelConfig" class="model-config-form surface" @submit.prevent="saveModelConfig">
     <div class="model-config-row">
      <label class="check-label model-enabled"><input v-model="modelConfig.enabled" type="checkbox"/>启用模型</label>
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
      <label>最大 ReAct 轮次<input v-model.number="modelConfig.max_turns" type="number" min="1" max="30"/></label>
      <label>连接超时（秒）<input v-model.number="modelConfig.connect_timeout" type="number" min="1" max="20" step="0.5"/></label>
      <label>读取超时（秒）<input v-model.number="modelConfig.read_timeout" type="number" min="1" max="120" step="0.5"/></label>
     </div>
     <div class="model-config-footer">
      <span class="muted small">当前生效模型：{{modelConfig.model||'未配置'}}</span>
      <button class="primary" :disabled="modelSaving">{{modelSaving?'正在保存…':'保存模型配置'}}</button>
     </div>
     <p v-if="modelSaved" class="muted small">{{modelSaved}}</p>
    </form>
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
    <h2>工具与技能</h2><p class="muted">当前账号可使用的业务能力，由管理员分配。</p>
    <div class="capability-toolbar">
     <button v-if="permissions.includes('user.manage')" class="capability-manage-button" @click="select('admin')"><Users :size="16"/>管理用户的工具与权限</button>
     <div class="capability-tabs" role="tablist" aria-label="能力类型">
      <button role="tab" :class="{active:capabilityTab==='tools'}" :aria-selected="capabilityTab==='tools'" @click="capabilityTab='tools'"><Wrench :size="15"/>工具<span>{{filteredTools.length}}</span></button>
      <button role="tab" :class="{active:capabilityTab==='skills'}" :aria-selected="capabilityTab==='skills'" @click="capabilityTab='skills'"><Layers :size="15"/>技能<span>{{filteredSkills.length}}</span></button>
      <button role="tab" :class="{active:capabilityTab==='all'}" :aria-selected="capabilityTab==='all'" @click="capabilityTab='all'">全部<span>{{filteredTools.length+filteredSkills.length}}</span></button>
     </div>
    </div>
    <div class="capability-filter surface">
     <label>查找能力<input v-model="capabilitySearch" placeholder="输入工具、权限或说明关键词"/></label>
     <label>部门<select v-model="capabilityDepartment"><option value="">全部部门</option><option v-for="[key,name] in capabilityDepartments" :key="key" :value="key">{{name}}</option></select></label>
     <label>类型<select v-model="capabilityType"><option value="">全部类型</option><option v-for="[key,name] in capabilityTypes" :key="key" :value="key">{{name}}</option></select></label>
    </div>
    <template v-if="showCapabilityTools">
    <h3 class="settings-section-title">工具</h3>
    <div class="capability-groups capability-grid" role="tabpanel" aria-label="工具">
     <section v-for="department in groupedTools" :key="department.key" class="capability-department">
      <h3>{{department.name}}</h3>
      <div v-for="type in department.types" :key="type.key" class="capability-type-block">
       <div class="capability-type-heading"><strong>{{type.name}}</strong><small class="muted">{{type.items.length}} 项</small></div>
       <article v-for="tool in type.items" :key="tool.key" class="capability-row"><div><h3><Wrench :size="15"/>{{capabilityName(tool.key)}}</h3><p class="muted">{{tool.description}}</p></div><div class="capability-row-meta"><div class="capability-tags"><span>{{capabilityMeta(tool).departmentName}}</span><span>{{capabilityMeta(tool).typeName}}</span></div><small class="muted">{{permissionName(tool.permission)}} · {{tool.key.startsWith('prepare_')?'需确认':'只读'}}</small></div></article>
      </div>
     </section>
    </div>
    <p v-if="!capabilities.tools.length" class="muted">还没有分配可用工具，请联系管理员。</p>
    <p v-else-if="!filteredTools.length" class="muted">没有匹配的工具。</p>
    </template>
    <template v-if="showCapabilitySkills">
    <h3 class="settings-section-title">技能</h3>
    <div class="capability-groups capability-grid" role="tabpanel" aria-label="技能">
     <section v-for="department in groupedSkills" :key="department.key" class="capability-department">
      <h3>{{department.name}}</h3>
      <div v-for="type in department.types" :key="type.key" class="capability-type-block">
       <div class="capability-type-heading"><strong>{{type.name}}</strong><small class="muted">{{type.items.length}} 项</small></div>
       <article v-for="skill in type.items" :key="skill.key" class="capability-row"><div><h3><Layers :size="15"/>{{capabilityName(skill.key)}}</h3><p class="muted">第 {{skill.version}} 版 · 使用当前授权工具</p></div><div class="capability-row-meta"><div class="capability-tags"><span>{{capabilityMeta(skill).departmentName}}</span><span>{{capabilityMeta(skill).typeName}}</span></div></div></article>
      </div>
     </section>
    </div>
    <p v-if="!capabilities.skills.length" class="muted">还没有分配可用技能，请联系管理员。</p>
    <p v-if="capabilities.skills.length&&!filteredSkills.length" class="muted">没有匹配的技能。</p>
    </template>
   </template>
   <AdminPanel v-else-if="page==='admin'&&permissions.includes('user.manage')" @error="emit('error',$event)"/>
   <WorkflowPanel v-else-if="page==='workflows'&&permissions.includes('workflow.design')" @error="emit('error',$event)"/>
   <template v-else-if="page==='audit'&&permissions.includes('audit.read')"><h2>操作审计</h2><p v-if="auditLoading" role="status">正在读取审计记录…</p><article v-for="entry in audit" :key="entry.id" class="audit-row"><strong>{{auditName(entry.action)}}</strong><small>{{shanghai(entry.created_at)}}</small><p class="muted small">{{entry.resource_id}}</p></article></template>
  </div>
 </section>
</main>
</template>
