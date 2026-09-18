<script setup lang="ts">
import BusinessFacts from './BusinessFacts.vue'
defineProps<{detail:any}>()
</script>
<template><section v-if="detail.business_type==='purchase_request'" class="surface"><h3>采购申请明细</h3><div class="table-scroll"><table><thead><tr><th>物料名称</th><th>数量</th><th>单位</th><th>需求日期</th></tr></thead><tbody><tr v-for="(line,i) in detail.snapshot.lines" :key="i"><td>{{line.material_name ?? '无字段权限'}}</td><td>{{line.quantity ?? '—'}}</td><td>{{line.unit ?? '—'}}</td><td>{{line.due_date ?? '—'}}</td></tr></tbody></table></div></section><section v-else class="surface"><h3>提交审批的业务材料</h3><BusinessFacts :value="detail.snapshot.detail"/></section></template>
