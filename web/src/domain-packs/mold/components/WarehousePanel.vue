<script setup lang="ts">
import {onMounted,ref} from 'vue'
import {api} from '../../../api'
import HumanCommand from '../../../components/HumanCommand.vue'
const props=defineProps<{permissions:string[]}>();const emit=defineEmits<{error:[message:string]}>()
const receipts=ref<any[]>([]),stock=ref<any[]>([]),warehouses=ref<any[]>([]),commands=ref<any[]>([]),active=ref<any>(null)
async function load(){try{[receipts.value,stock.value,warehouses.value,commands.value]=await Promise.all([api('/warehouse/receipts'),api('/warehouse/stock'),api('/master/warehouses'),api('/business/commands')])}catch(e:any){emit('error',e.message)}}
onMounted(load)
function command(action:string,resource:any,label:string){active.value={action,resourceId:resource.id,schema:commands.value.find(c=>c.key===action).schema,context:label}}
</script>
<template><h2>仓库与质量记录</h2><p class="muted">实收、检验和合格库存分别保留。库存启用前确认本地管理范围及期初依据。</p><HumanCommand v-if="active" :key="active.action+active.resourceId" v-bind="active" @close="active=null" @changed="load" @error="emit('error',$event)"/>
<div v-for="w in warehouses" :key="w.id" class="surface"><h3>{{w.name}}</h3><p>{{w.scope_confirmed?'管理范围已确认':'尚未确认管理范围'}}</p><button v-if="!w.scope_confirmed&&permissions.includes('warehouse.configure')" @click="command('warehouse.configure',w,w.name)">核对启用条件</button></div><h3>收货与检验</h3><article v-for="r in receipts" :key="r.id" class="surface"><strong>{{r.material}} · {{r.quantity}}</strong><p>{{r.reference}} · {{r.evidence}}</p><p v-for="i in r.inspections" :key="i.id">合格 {{i.accepted_quantity}} · 不合格 {{i.rejected_quantity}}</p><button v-if="!r.inspections.length&&permissions.includes('inspection.confirm')" @click="command('inspection.confirm',r,r.material+' / '+r.reference)">检验并确认合格入库</button></article><p v-if="!receipts.length" class="muted">暂无可见收货记录</p><h3>合格可用库存</h3><article v-for="b in stock" :key="b.id" class="surface"><strong>{{b.material}} · {{b.quantity}} {{b.unit}}</strong><button v-if="permissions.includes('stock.issue')" @click="command('stock.issue',b,b.material+' / 当前数量 '+b.quantity)">核对并发料</button></article><p v-if="!stock.length" class="muted">暂无可见库存</p></template>
