<script setup lang="ts">
import {onMounted,ref} from 'vue'
import {api,post} from '../api'
const emit=defineEmits<{error:[message:string];changed:[]}>()
const groups=ref<any[]>([]),users=ref<any[]>([]),editing=ref<any>(null),reason=ref(''),busy=ref(false),notice=ref('')
const domainRoles=ref<any>({roles:[],scopes:[]}),domainScopeId=ref(''),domainBindings=ref<any>({version:0,entries:[]}),domainReason=ref(''),domainBusy=ref(false)
async function load(){const [loadedGroups,loadedUsers,catalog]=await Promise.all([api('/organization/groups'),api('/users'),api('/workflows/assignment-catalog')]);groups.value=loadedGroups;users.value=loadedUsers;domainRoles.value=catalog.domain||{roles:[],scopes:[]};if(domainRoles.value.scopes?.length){if(!domainRoles.value.scopes.some((scope:any)=>scope.id===domainScopeId.value))domainScopeId.value=domainRoles.value.scopes[0].id;await loadDomainBindings()}}
onMounted(async()=>{try{await load()}catch(e:any){emit('error',e.message)}})
function edit(g?:any,kind:'ROLE'|'DEPARTMENT'='DEPARTMENT'){reason.value='';notice.value='';editing.value=g?JSON.parse(JSON.stringify(g)):{kind,name:'',members:[],active:true}}
function toggle(uid:string,checked:boolean){editing.value.members=checked?[...editing.value.members,{user_id:uid,is_head:false}]:editing.value.members.filter((m:any)=>m.user_id!==uid)}
async function save(){busy.value=true;try{const g=editing.value;const data={kind:g.kind,name:g.name,members:g.members,active:g.active,reason:reason.value};if(g.id)await api(`/organization/groups/${g.id}`,{method:'PUT',body:JSON.stringify({...data,expected_version:g.version})});else await post('/organization/groups',data);editing.value=null;await load();notice.value='人员规则已保存；已有审批席位保持不变，后续节点使用最新成员。';emit('changed')}catch(e:any){emit('error',e.message)}finally{busy.value=false}}
async function loadDomainBindings(){if(!domainScopeId.value)return;domainBindings.value=await api(`/organization/domain-role-bindings/${domainScopeId.value}`);domainReason.value=''}
function roleMembers(roleKey:string){return domainBindings.value.entries.filter((entry:any)=>entry.role_key===roleKey)}
function addRoleMember(role:any,event:Event){const select=event.target as HTMLSelectElement,userId=select.value;if(!userId)return;const entries=domainBindings.value.entries.filter((entry:any)=>!(role.max_members===1&&entry.role_key===role.key));if(!entries.some((entry:any)=>entry.role_key===role.key&&entry.user_id===userId))entries.push({role_key:role.key,user_id:userId});domainBindings.value={...domainBindings.value,entries};select.value=''}
function removeRoleMember(roleKey:string,userId:string){domainBindings.value={...domainBindings.value,entries:domainBindings.value.entries.filter((entry:any)=>!(entry.role_key===roleKey&&entry.user_id===userId))}}
async function saveDomainBindings(){const label=domainRoles.value.label||'领域角色';if(!domainReason.value.trim())return emit('error',`请填写${label}变更原因`);domainBusy.value=true;try{domainBindings.value=await api(`/organization/domain-role-bindings/${domainScopeId.value}`,{method:'PUT',body:JSON.stringify({entries:domainBindings.value.entries,expected_version:domainBindings.value.version,reason:domainReason.value})});domainReason.value='';notice.value=`${label}已保存；已有审批席位保持不变，后续节点按新版本解析。`;emit('changed')}catch(e:any){emit('error',e.message)}finally{domainBusy.value=false}}
</script>
<template>
<section class="surface form-stack organization-compact" aria-label="部门与角色管理">
  <div class="organization-board">
    <section class="organization-column">
      <div class="organization-column-head"><h3>部门</h3><button type="button" aria-label="新增部门" title="新增部门" @click="edit(undefined,'DEPARTMENT')">+</button></div>
      <p v-if="!groups.filter(g=>g.kind==='DEPARTMENT').length&&!editing" class="admin-empty compact">暂无部门</p>
      <div v-for="g in groups.filter(g=>g.kind==='DEPARTMENT')" :key="g.id" class="grant-row organization-row"><div><strong>{{g.name}}</strong><small>{{g.members.length}} 位成员 · {{g.active?'启用':'停用'}}</small></div><button title="维护" aria-label="维护" @click="edit(g)">···</button></div>
    </section>
    <section class="organization-column">
      <div class="organization-column-head"><h3>角色</h3><button type="button" aria-label="新增角色" title="新增角色" @click="edit(undefined,'ROLE')">+</button></div>
      <p v-if="!groups.filter(g=>g.kind==='ROLE').length&&!editing" class="admin-empty compact">暂无角色</p>
      <div v-for="g in groups.filter(g=>g.kind==='ROLE')" :key="g.id" class="grant-row organization-row"><div><strong>{{g.name}}</strong><small>{{g.members.length}} 位成员 · {{g.active?'启用':'停用'}}</small></div><button title="维护" aria-label="维护" @click="edit(g)">···</button></div>
    </section>
  </div>
  <section v-if="domainRoles.roles?.length" class="form-stack domain-role-board" aria-label="领域角色管理">
    <div class="section-heading"><div><h3>{{domainRoles.label}}</h3><p class="muted">领域角色只参与流程选人，不授予业务数据读取或审批权限。</p></div><label>{{domainRoles.scope_label}}<select v-model="domainScopeId" @change="loadDomainBindings"><option v-for="scope in domainRoles.scopes" :key="scope.id" :value="scope.id">{{scope.code}} · {{scope.name}}</option></select></label></div>
    <p v-if="!domainRoles.scopes?.length" class="admin-empty compact">当前权限内没有可配置的业务对象。</p>
    <div v-else class="domain-role-grid">
      <article v-for="role in domainRoles.roles" :key="role.key" class="domain-role-card"><div><strong>{{role.name}}</strong><small>{{role.description}}</small></div><div class="domain-role-members"><button v-for="entry in roleMembers(role.key)" :key="entry.user_id" type="button" :aria-label="'移除'+role.name+(users.find((user:any)=>user.id===entry.user_id)?.display_name||'人员')" @click="removeRoleMember(role.key,entry.user_id)">{{users.find((user:any)=>user.id===entry.user_id)?.display_name||'人员不可用'}} ×</button><small v-if="!roleMembers(role.key).length" class="muted">尚未配置</small></div><select aria-label="添加领域角色成员" @change="addRoleMember(role,$event)"><option value="">添加成员…</option><option v-for="user in users.filter((item:any)=>item.active&&!roleMembers(role.key).some((entry:any)=>entry.user_id===item.id))" :key="user.id" :value="user.id">{{user.display_name}} · {{user.department||user.username}}</option></select></article>
    </div>
    <div v-if="domainRoles.scopes?.length" class="domain-role-actions"><label>变更原因<input v-model="domainReason" maxlength="500" placeholder="说明人员职责调整依据"/></label><button type="button" class="primary" :disabled="domainBusy||!domainReason.trim()" @click="saveDomainBindings">{{domainBusy?'正在保存…':'保存'+domainRoles.label}}</button></div>
  </section>
  <p v-if="notice" class="admin-inline-notice" role="status">{{notice}}</p>
  <form v-if="editing" class="form-stack organization-editor" @submit.prevent="save">
    <label>类型<select v-model="editing.kind" :disabled="!!editing.id"><option value="ROLE">角色</option><option value="DEPARTMENT">部门</option></select></label>
    <label>名称<input v-model="editing.name" required maxlength="100"/></label>
    <label class="check-label"><input v-model="editing.active" type="checkbox"/>启用</label>
    <fieldset><legend>成员</legend><div v-for="u in users.filter(x=>x.active)" :key="u.id" class="grant-row">
      <label class="check-label"><input type="checkbox" :checked="editing.members.some((m:any)=>m.user_id===u.id)" @change="toggle(u.id,($event.target as HTMLInputElement).checked)"/>{{u.display_name}}（{{u.username}}）</label>
      <label v-if="editing.kind==='DEPARTMENT'&&editing.members.some((m:any)=>m.user_id===u.id)" class="check-label"><input type="checkbox" v-model="editing.members.find((m:any)=>m.user_id===u.id).is_head"/>部门负责人</label>
    </div></fieldset>
    <label>变更原因<input v-model="reason" required maxlength="500"/></label>
    <div class="actions"><button type="button" @click="editing=null">取消</button><button class="primary" :disabled="busy">保存人员规则</button></div>
  </form>
</section>
</template>
