<script setup lang="ts">
import {computed,onMounted,ref,watch} from 'vue'
import {api,post} from '../../../api'
const props=defineProps<{resourceType:string;row:any}>()
const emit=defineEmits<{close:[];error:[message:string];submitted:[id:string]}>()
const templates=ref<any[]>([]),category=ref(''),process=ref(''),definition=ref(''),intent=ref<any>(null),busy=ref(false)
const categoryKey=(t:any)=>t.category_id||'legacy'
const categories=computed(()=>[...new Map(templates.value.map(t=>[categoryKey(t),{id:categoryKey(t),name:t.category_name}])).values()])
const processes=computed(()=>[...new Map(templates.value.filter(t=>categoryKey(t)===category.value).slice().reverse().map(t=>[t.process_key,{id:t.process_key,name:t.name}])).values()])
const versions=computed(()=>templates.value.filter(t=>categoryKey(t)===category.value&&t.process_key===process.value).sort((a,b)=>b.version-a.version))
const chosen=computed(()=>templates.value.find(t=>t.id===definition.value))
watch(category,()=>{process.value='';definition.value=''})
watch(process,()=>{definition.value=''})
onMounted(async()=>{try{templates.value=await api(`/workflows/available?resource_type=${props.resourceType}&resource_id=${encodeURIComponent(props.row.id)}`)}catch(e:any){emit('error',e.message)}})
async function prepare(){busy.value=true;try{intent.value=await post(`${props.resourceType==='purchase_request'?'/purchases':'/business/subjects'}/${props.row.id}/submit-intent`,{revision:props.row.revision,definition_id:definition.value})}catch(e:any){emit('error',e.message)}finally{busy.value=false}}
async function confirm(){busy.value=true;try{const r=await post(`/human-actions/${intent.value.id}/confirm`,{challenge:intent.value.challenge});emit('submitted',r.instance_id)}catch(e:any){emit('error',e.message)}finally{busy.value=false}}
</script>
<template><Teleport to="body"><div class="modal-shade"><section class="modal form-stack" role="dialog" aria-modal="true" aria-label="选择并提交审批流程">
<h2>选择并提交审批流程</h2><p>{{row.number}} · 材料第 {{row.revision}} 版</p>
<label>流程类别<select v-model="category" :disabled="!!intent||busy"><option value="">请选择类别</option><option v-for="c in categories" :key="c.id" :value="c.id">{{c.name}}</option></select></label>
<label>审批模板<select v-model="process" :disabled="!category||!!intent||busy"><option value="">请选择模板</option><option v-for="p in processes" :key="p.id" :value="p.id">{{p.name}}</option></select></label>
<label>已发布版本<select v-model="definition" :disabled="!process||!!intent||busy"><option value="">请选择版本</option><option v-for="t in versions" :key="t.id" :value="t.id">第 {{t.version}} 版 · {{t.name}}</option></select></label>
<p v-if="!templates.length" class="muted">暂无可用的已发布流程，请联系流程管理员配置。</p>
<p v-if="intent&&chosen">将使用“{{chosen.category_name}} / {{chosen.name}} / 第 {{chosen.version}} 版”发起审批。确认后冻结本轮材料，后续业务操作仍按业务规则办理。</p>
<div class="actions"><button :disabled="busy" @click="emit('close')">取消</button><button v-if="!intent" :disabled="busy||!definition" @click="prepare">核对所选流程</button><button v-else class="primary" :disabled="busy" @click="confirm">确认提交审批</button></div>
</section></div></Teleport></template>
