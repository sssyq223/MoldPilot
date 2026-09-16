<script setup lang="ts">
import {onMounted,ref} from 'vue'
import {api,post} from '../api'
const emit=defineEmits<{error:[message:string];changed:[]}>()
const groups=ref<any[]>([]),users=ref<any[]>([]),editing=ref<any>(null),reason=ref(''),busy=ref(false),notice=ref('')
async function load(){[groups.value,users.value]=await Promise.all([api('/organization/groups'),api('/users')])}
onMounted(async()=>{try{await load()}catch(e:any){emit('error',e.message)}})
function edit(g?:any,kind:'ROLE'|'DEPARTMENT'='DEPARTMENT'){reason.value='';notice.value='';editing.value=g?JSON.parse(JSON.stringify(g)):{kind,name:'',members:[],active:true}}
function toggle(uid:string,checked:boolean){editing.value.members=checked?[...editing.value.members,{user_id:uid,is_head:false}]:editing.value.members.filter((m:any)=>m.user_id!==uid)}
async function save(){busy.value=true;try{const g=editing.value;const data={kind:g.kind,name:g.name,members:g.members,active:g.active,reason:reason.value};if(g.id)await api(`/organization/groups/${g.id}`,{method:'PUT',body:JSON.stringify({...data,expected_version:g.version})});else await post('/organization/groups',data);editing.value=null;await load();notice.value='人员规则已保存；已有审批席位保持不变，后续节点使用最新成员。';emit('changed')}catch(e:any){emit('error',e.message)}finally{busy.value=false}}
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
