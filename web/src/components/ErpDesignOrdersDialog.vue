<script setup lang="ts">
import { computed, ref } from 'vue'
import { Search, X } from 'lucide-vue-next'
import { shanghai } from '../api'

const props=defineProps<{evidence:any}>()
defineEmits<{close:[]}>()

const keyword=ref('')
const status=ref('')
const selected=ref<any|null>(null)
const rows=computed<any[]>(()=>Array.isArray(props.evidence?.data)?props.evidence.data:[])
const statusOptions=computed(()=>[...new Set(rows.value.map(row=>String(row.statusLabel||row.approvalStatus||'').trim()).filter(Boolean))])
const visibleRows=computed(()=>rows.value.filter(row=>{
 const needle=keyword.value.trim().toLowerCase()
 const searchable=[row.requestNo,row.orderNo,row.moldNo,row.typeLabel,row.sourceLabel].filter(Boolean).join(' ').toLowerCase()
 const rowStatus=String(row.statusLabel??row.approvalStatus??row.status??row.statusCode??'')
 return (!needle||searchable.includes(needle))&&(!status.value||rowStatus===status.value)
}))

function text(value:any){return value===undefined||value===null||value===''?'-':String(value)}
function date(value:any){return value?shanghai(String(value)):'-'}
function amount(row:any){return text(row.amountDisplay??row.totalAmount??row.total_amount)}
const detailKeys=['requestNo','orderNo','moldNo','typeLabel','itemCount','sourceLabel','statusLabel','currentStageLabel','currentApprovalNodeName','currentApprovalRoleLabel','workflowInstanceId','canDesignApprove','purchaseOrderCount','totalAmount','createdAt'] as const
const labels:Record<(typeof detailKeys)[number],string>={requestNo:'请购单号',orderNo:'订单号',moldNo:'模具号',typeLabel:'类型',itemCount:'明细数',sourceLabel:'来源',statusLabel:'状态',currentStageLabel:'当前节点',currentApprovalNodeName:'审批节点',currentApprovalRoleLabel:'审批角色',workflowInstanceId:'流程实例',canDesignApprove:'可由设计审批',purchaseOrderCount:'采购订单数',totalAmount:'总金额',createdAt:'创建时间'}
function detailEntries(row:any){
 return detailKeys.filter(key=>{
  const value=row?.[key]
  if(key==='orderNo'&&value===row?.requestNo)return false
  return value!==undefined&&value!==null&&value!==''&&typeof value!=='object'
 }).map(key=>[key,row[key]] as const)
}
function label(key:(typeof detailKeys)[number]){return labels[key]}
function detailValue(key:string,value:any){
 if(key==='canDesignApprove')return value===true||value===1?'是':'否'
 if(key==='createdAt')return date(value)
 return text(value)
}
</script>

<template>
 <div class="order-shade" @click.self="$emit('close')">
  <section class="order-dialog" role="dialog" aria-modal="true" aria-label="ERP 设计订单">
   <header>
    <div><h2>ERP 设计订单</h2></div>
    <button class="close" aria-label="关闭设计订单" @click="$emit('close')"><X :size="18"/></button>
   </header>
   <div class="filters">
    <label><Search :size="15"/><input v-model="keyword" placeholder="请购单号、订单号、模具号或类型"/></label>
    <select v-model="status" aria-label="筛选订单状态"><option value="">全部状态</option><option v-for="option in statusOptions" :key="option" :value="option">{{option}}</option></select>
    <button @click="keyword='';status=''">重置</button>
   </div>
   <div class="table-scroll">
    <table>
     <thead><tr><th>请购/订单号</th><th>模具号</th><th>类型</th><th>明细</th><th>状态</th><th>当前节点</th><th>金额</th><th>创建时间</th><th>操作</th></tr></thead>
     <tbody>
      <tr v-for="row in visibleRows" :key="row.requestId||row.id||row.requestNo||row.orderNo">
       <td><strong>{{text(row.requestNo||row.orderNo)}}</strong></td><td>{{text(row.moldNo)}}</td><td>{{text(row.typeLabel||row.sourceLabel)}}</td><td>{{text(row.itemCount)}}</td><td><span class="status">{{text(row.statusLabel||row.approvalStatus)}}</span></td><td>{{text(row.currentStageLabel||row.currentApprovalNodeName)}}</td><td>{{amount(row)}}</td><td>{{date(row.createdAt)}}</td><td><button class="detail" @click="selected=row">详情</button></td>
      </tr>
     </tbody>
    </table>
    <p v-if="!visibleRows.length" class="empty">没有符合条件的设计订单。</p>
   </div>
   <footer><span>当前显示 {{visibleRows.length}} / {{rows.length}} 条</span></footer>
   <div v-if="selected" class="detail-layer" @click.self="selected=null">
    <aside class="detail-panel" role="dialog" aria-modal="true" aria-label="设计订单详情">
     <div class="detail-head"><div><strong>{{selected.requestNo||selected.orderNo||'设计订单详情'}}</strong><small>{{selected.moldNo||'模具号待确认'}} · {{selected.statusLabel||'状态待确认'}}</small></div><button class="close" aria-label="关闭订单详情" @click="selected=null"><X :size="16"/></button></div>
     <div class="detail-grid"><div v-for="entry in detailEntries(selected)" :key="entry[0]"><span>{{label(entry[0])}}</span><strong>{{detailValue(entry[0],entry[1])}}</strong></div></div>
    </aside>
   </div>
  </section>
 </div>
</template>

<style scoped>
.order-shade{position:fixed;inset:0;z-index:88;display:grid;place-items:center;padding:24px;background:#11182778}.order-dialog{position:relative;isolation:isolate;display:flex;flex-direction:column;width:min(1500px,calc(100vw - 48px));max-height:calc(100dvh - 48px);overflow:hidden;border:1px solid var(--border);border-radius:14px;background:var(--surface);box-shadow:0 28px 90px #0005;color:var(--text)}header{display:flex;align-items:flex-start;justify-content:space-between;padding:20px 22px 16px;border-bottom:1px solid var(--border)}h2{margin:0;font-size:19px}header p{margin:3px 0 0;color:var(--muted);font-size:11px}.close{width:32px;height:32px;padding:0;border:0;background:transparent}.filters{display:flex;gap:9px;padding:14px 18px;border-bottom:1px solid var(--border)}.filters label{display:flex;flex-direction:row;align-items:center;gap:7px;min-width:330px;height:36px;padding:0 10px;border:1px solid var(--border);border-radius:8px;color:var(--muted)}.filters input{height:34px;padding:0;border:0;background:transparent;box-shadow:none}.filters select{width:170px;height:36px}.filters>button{height:36px;padding:0 14px}.table-scroll{min-height:260px;overflow:auto;background:var(--surface)}table{width:100%;border-collapse:collapse;font-size:12px;white-space:nowrap}th,td{padding:11px 12px;border-right:1px solid var(--border);border-bottom:1px solid var(--border);text-align:left}th{position:sticky;top:0;z-index:1;background:color-mix(in srgb,var(--bg) 80%,var(--surface));color:var(--muted);font-weight:600}tbody tr:hover{background:color-mix(in srgb,var(--accent) 5%,var(--surface))}.status{display:inline-flex;padding:2px 8px;border-radius:999px;background:color-mix(in srgb,var(--accent) 12%,var(--surface));color:color-mix(in srgb,var(--accent) 72%,var(--text));font-size:11px}.detail{height:28px;padding:0 9px;border:0;background:transparent;color:var(--accent)}.empty{padding:70px 20px;text-align:center;color:var(--muted)}footer{display:flex;justify-content:space-between;padding:11px 18px;color:var(--muted);font-size:11px;border-top:1px solid var(--border)}.detail-layer{position:absolute;inset:0;z-index:4;display:flex;justify-content:flex-end;padding:70px 18px 18px;background:#11182735}.detail-panel{position:relative;width:min(480px,calc(100% - 36px));overflow:auto;padding:16px;border:1px solid var(--border);border-radius:12px;background:var(--surface);box-shadow:0 18px 55px #0005}.detail-head{position:sticky;top:-16px;z-index:1;display:flex;justify-content:space-between;gap:14px;margin:-16px -16px 0;padding:16px 16px 12px;border-bottom:1px solid var(--border);background:var(--surface)}.detail-head strong,.detail-head small{display:block}.detail-head small{margin-top:3px;color:var(--muted);font-size:11px}.detail-grid{display:grid;grid-template-columns:1fr 1fr;gap:8px;margin-top:12px}.detail-grid>div{min-width:0;padding:9px;border:1px solid var(--border);border-radius:8px}.detail-grid span,.detail-grid strong{display:block}.detail-grid span{color:var(--muted);font-size:10px}.detail-grid strong{margin-top:3px;overflow-wrap:anywhere;font-size:12px}@media(max-width:760px){.order-shade{padding:10px}.order-dialog{width:calc(100vw - 20px);max-height:calc(100dvh - 20px)}.filters{flex-wrap:wrap}.filters label{width:100%;min-width:0}.filters select{flex:1}.detail-layer{padding:58px 10px 10px}.detail-panel{width:100%}.detail-grid{grid-template-columns:1fr}}
</style>
