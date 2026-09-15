<script setup lang="ts">
import {ref} from 'vue'
import {api,post} from '../api'
defineProps<{categories:any[]}>()
const emit=defineEmits<{changed:[];error:[message:string]}>()
const opened=ref(false),name=ref(''),busy=ref(false),editing=ref<any>(null),reason=ref('')
async function create(){busy.value=true;try{await post('/workflow-categories',{name:name.value});name.value='';emit('changed')}catch(e:any){emit('error',e.message)}finally{busy.value=false}}
async function save(){busy.value=true;try{const c=editing.value;await api(`/workflow-categories/${c.id}`,{method:'PUT',body:JSON.stringify({name:c.name,active:c.active,expected_version:c.version,reason:reason.value})});editing.value=null;emit('changed')}catch(e:any){emit('error',e.message)}finally{busy.value=false}}
</script>
<template>
<button @click="opened=!opened">{{opened?'收起类别管理':'维护流程类别'}}</button>
<section v-if="opened" class="surface form-stack" aria-label="流程类别管理">
  <p class="muted">类别由管理员自行命名，例如加工、采购、委外。类别用于整理模板，不授予业务权限。</p>
  <form @submit.prevent="create" class="form-stack"><label>新增类别名称<input v-model="name" required maxlength="100"/></label><button :disabled="busy">新增类别</button></form>
  <div v-for="c in categories" :key="c.id" class="grant-row"><span>{{c.name}} · {{c.active?'启用':'停用'}}</span><button @click="editing={...c};reason=''">修改类别</button></div>
  <form v-if="editing" @submit.prevent="save" class="form-stack"><label>类别名称<input v-model="editing.name" required maxlength="100"/></label><label class="check-label"><input v-model="editing.active" type="checkbox"/>启用类别</label><label>变更原因<input v-model="reason" required/></label><p class="muted">停用后不能新发起该类别的流程，已有审批继续办理。</p><div class="actions"><button type="button" @click="editing=null">取消</button><button :disabled="busy">保存类别</button></div></form>
</section>
</template>
