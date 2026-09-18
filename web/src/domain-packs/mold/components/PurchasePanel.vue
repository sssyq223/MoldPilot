<script setup lang="ts">
import { onMounted, ref, watch } from 'vue'
import { Plus, Trash2 } from 'lucide-vue-next'
import { api, post } from '../../../api'
import WorkflowSubmit from './WorkflowSubmit.vue'
const selectedRow=ref<any>(null)
const emit = defineEmits<{ changed: []; error: [message: string]; approval: [id: string] }>()
const props = defineProps<{ permissions:string[] }>()
const rows=ref<any[]>([]), projects=ref<any[]>([]), materials=ref<any[]>([])
const editing=ref(false), busy=ref(false), project=ref(''), remark=ref('')
const lines=ref([{material_id:'',quantity:'1',due_date:''}])
async function load() { try { [rows.value,projects.value] = await Promise.all([api('/purchases'),api('/projects')]) } catch(e:any){emit('error',e.message)} }
watch(project, async value=>{ if(value) try{ materials.value=await api(`/materials?project_id=${encodeURIComponent(value)}`); lines.value=[{material_id:'',quantity:'1',due_date:''}] }catch(e:any){emit('error',e.message)} })
onMounted(load)
async function save() {
  busy.value=true
  try{ await post('/purchases',{project_id:project.value,remark:remark.value,lines:lines.value}); editing.value=false; remark.value=''; await load(); emit('changed') }
  catch(e:any){emit('error',e.message)} finally{busy.value=false}
}
const stateName:Record<string,string>={DRAFT:'草稿',SUBMITTED:'审批中',APPROVED:'申请已通过',REJECTED:'已驳回',RETURNED:'待修改'}
</script>
<template>
  <div class="section-heading"><div><h2>采购申请</h2><p class="muted">准备材料，提交审批，查看业务回执。</p></div><button v-if="permissions.includes('purchase.create')" class="primary" @click="editing=!editing"><Plus :size="16"/>新建申请</button></div>
  <form v-if="editing" class="surface form-stack" @submit.prevent="save"><h3>新建采购草稿</h3><label>项目<select v-model="project" required><option value="" disabled>选择项目</option><option v-for="p in projects" :key="p.id" :value="p.id">{{p.code}} · {{p.name}}</option></select></label>
    <div class="line-editor" v-for="(line,i) in lines" :key="i"><label>物料<select v-model="line.material_id" required><option value="" disabled>选择已登记物料</option><option v-for="m in materials" :key="m.id" :value="m.id">{{m.name}} · {{m.unit}}</option></select></label><label>数量<input v-model="line.quantity" type="number" min="0.000001" step="0.000001" required/></label><label>需求日期<input v-model="line.due_date" type="date" required/></label><button v-if="lines.length>1" type="button" class="icon-button" aria-label="删除明细" @click="lines.splice(i,1)"><Trash2 :size="16"/></button></div>
    <button class="subtle" type="button" @click="lines.push({material_id:'',quantity:'1',due_date:''})"><Plus :size="14"/>增加明细</button><label>申请备注<textarea v-model="remark" rows="3" placeholder="说明用途、依据和需要注意的事项"/></label><div class="actions"><button type="button" @click="editing=false">取消</button><button class="primary" :disabled="busy">保存草稿</button></div>
  </form>

  <div v-if="!rows.length && !editing" class="empty"><h3>还没有可见的采购申请</h3><p>从新建申请开始，或在会话中查询已登记资料。</p></div>
  <article v-for="row in rows" :key="row.id" class="surface purchase-card"><div class="section-heading"><strong>{{row.number}}</strong><span class="status">{{stateName[row.status]}}</span></div><table><thead><tr><th>物料</th><th>数量</th><th>需求日期</th></tr></thead><tbody><tr v-for="(line,i) in row.lines" :key="i"><td>{{line.material_name ?? '无字段权限'}}</td><td>{{line.quantity ?? '—'}} {{line.unit}}</td><td>{{line.due_date ?? '—'}}</td></tr></tbody></table><p class="muted">{{row.remark || '未填写备注'}}</p><div class="actions"><button v-if="['DRAFT','REJECTED','RETURNED'].includes(row.status) && permissions.includes('purchase.submit')" @click="selectedRow=row">核对并提交审批</button></div></article>
  <WorkflowSubmit v-if="selectedRow" resource-type="purchase_request" :row="selectedRow" @close="selectedRow=null" @error="emit('error',$event)" @submitted="selectedRow=null;load();emit('changed');emit('approval',$event)"/>
</template>
