<script setup lang="ts">
import {computed,ref,watch} from 'vue'
import {ArrowLeft,Settings,Wrench,Users,GitBranch,ScrollText,Search,Layers,Sun,Moon} from 'lucide-vue-next'
import type {ColorTheme} from '../theme'
import {api,shanghai} from '../api'
import {capabilityName,permissionName,auditName} from '../uiText'
import AdminPanel from './AdminPanel.vue'
import WorkflowPanel from './WorkflowPanel.vue'
const props=defineProps<{me:any;permissions:string[];capabilities:any;modelName:string;colorTheme:ColorTheme}>()
const emit=defineEmits<{close:[];error:[message:string];themeChange:[theme:ColorTheme]}>()
const page=ref('account'),search=ref(''),audit=ref<any[]>([]),auditLoading=ref(false)
const navigation=computed(()=>[
 {key:'account',name:'账号与模型',icon:Settings,allow:true},
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
   <template v-else-if="page==='capabilities'">
    <h2>工具与技能</h2><p class="muted">当前账号可使用的业务能力，由管理员分配。</p>
    <button v-if="permissions.includes('user.manage')" @click="select('admin')"><Users :size="16"/>管理用户的工具与权限</button>
    <h3 class="settings-section-title">工具</h3>
    <article v-for="tool in capabilities.tools" :key="tool.key" class="surface"><h3><Wrench :size="16"/>{{capabilityName(tool.key)}}</h3><p>{{tool.description}}</p><small class="muted">所需权限：{{permissionName(tool.permission)}} · {{tool.key.startsWith('prepare_')?'准备建议，须本人确认':'只读'}}</small></article>
    <p v-if="!capabilities.tools.length" class="muted">还没有分配可用工具，请联系管理员。</p>
    <h3 class="settings-section-title">技能</h3>
    <article v-for="skill in capabilities.skills" :key="skill.key" class="surface"><h3><Layers :size="16"/>{{capabilityName(skill.key)}}</h3><small class="muted">第 {{skill.version}} 版 · 使用当前授权工具</small></article>
   </template>
   <AdminPanel v-else-if="page==='admin'&&permissions.includes('user.manage')" @error="emit('error',$event)"/>
   <WorkflowPanel v-else-if="page==='workflows'&&permissions.includes('workflow.design')" @error="emit('error',$event)"/>
   <template v-else-if="page==='audit'&&permissions.includes('audit.read')"><h2>操作审计</h2><p v-if="auditLoading" role="status">正在读取审计记录…</p><article v-for="entry in audit" :key="entry.id" class="audit-row"><strong>{{auditName(entry.action)}}</strong><small>{{shanghai(entry.created_at)}}</small><p class="muted small">{{entry.resource_id}}</p></article></template>
  </div>
 </section>
</main>
</template>
