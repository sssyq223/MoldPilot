<script setup lang="ts">
import {onMounted,onUnmounted,ref} from 'vue'
import {FileText,X} from 'lucide-vue-next'
import BusinessFacts from './BusinessFacts.vue'
defineProps<{detail:any}>()
const open=ref(false)
function close(){open.value=false}
function escape(event:KeyboardEvent){if(event.key==='Escape')close()}
onMounted(()=>window.addEventListener('keydown',escape))
onUnmounted(()=>window.removeEventListener('keydown',escape))
</script>
<template>
 <button type="button" class="approval-detail-trigger" @click="open=true"><FileText :size="15"/>{{detail.business_type==='purchase_request'?'查看申请明细':'查看业务材料'}}</button>
 <Teleport to="body"><div v-if="open" class="modal-shade" @click.self="close"><section class="modal approval-detail-modal" role="dialog" aria-modal="true" :aria-label="detail.business_type==='purchase_request'?'采购申请明细':'提交审批的业务材料'">
  <header class="approval-detail-modal-head"><div><small>审批材料</small><h2>{{detail.business_type==='purchase_request'?'采购申请明细':'提交审批的业务材料'}}</h2></div><button type="button" class="icon-button" aria-label="关闭申请明细" @click="close"><X :size="18"/></button></header>
  <div v-if="detail.business_type==='purchase_request'" class="table-scroll"><table><thead><tr><th>物料名称</th><th>数量</th><th>单位</th><th>需求日期</th></tr></thead><tbody><tr v-for="(line,i) in detail.snapshot.lines" :key="i"><td>{{line.material_name ?? '无字段权限'}}</td><td>{{line.quantity ?? '—'}}</td><td>{{line.unit ?? '—'}}</td><td>{{line.due_date ?? '—'}}</td></tr></tbody></table></div>
  <BusinessFacts v-else :value="detail.snapshot.detail"/>
 </section></div></Teleport>
</template>
