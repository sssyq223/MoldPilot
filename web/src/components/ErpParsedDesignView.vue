<script setup lang="ts">
import {computed} from 'vue'

const props = defineProps<{value: any}>()

const rows = computed<any[]>(() => Array.isArray(props.value?.previewRows) ? props.value.previewRows : [])
const rules = computed<any[]>(() => Array.isArray(props.value?.additionalProcessingFeeRules) ? props.value.additionalProcessingFeeRules : [])
const warnings = computed<any[]>(() => Array.isArray(props.value?.warnings) ? props.value.warnings : [])
const errors = computed<any[]>(() => Array.isArray(props.value?.errors) ? props.value.errors : [])

function amount(value: unknown) {
  const number = Number(value)
  return Number.isFinite(number) ? `¥${number.toLocaleString('zh-CN', {maximumFractionDigits: 2})}` : '待核价'
}
function range(rule: any) {
  if (rule.chargeMode === 'height_interval_per_piece') {
    const lower = rule.dimensionMin ?? '不限'
    const upper = rule.dimensionMax ?? '不限'
    return `按喷涂高度 ${lower}–${upper} mm，每件计费`
  }
  if (rule.chargeMode === 'per_count') return '按加工数量计费'
  return rule.pricingNote || '按 ERP 规则计费'
}
function status(row: any) {
  return row.drawing_status_label || row.hardware_price_match_status || row.paint_pricing_status || '已解析'
}
function text(value: unknown) {
  return value === null || value === undefined || value === '' ? '—' : String(value)
}
</script>

<template>
  <div class="erp-business-view">
    <div class="erp-result-stats"><div><small>模号</small><strong>{{value?.moldCode || 'ERP 未返回'}}</strong></div><div><small>设计明细</small><strong>{{rows.length}} 项</strong></div><div><small>总数量</small><strong>{{value?.totalQuantity ?? '—'}}</strong></div><div><small>导入状态</small><strong :class="value?.canImport ? 'is-positive' : ''">{{value?.canImport ? '可继续校验' : '需处理提示'}}</strong></div></div>

    <section v-if="rules.length" class="erp-business-section">
      <div class="erp-business-heading"><div><h4>附加加工计费</h4><p>ERP 会按下列工序自动核算附加加工费用。</p></div><span>{{rules.length}} 条规则</span></div>
      <div class="erp-rule-cards"><article v-for="rule in rules" :key="rule.id" class="erp-rule-card"><div><strong>{{rule.processKeyword || '附加工序'}}</strong><small>{{range(rule)}}</small></div><b>{{amount(rule.unitPrice)}}</b><p v-if="rule.pricingNote">{{rule.pricingNote}}</p></article></div>
    </section>

    <section class="erp-business-section">
      <div class="erp-business-heading"><div><h4>设计明细预览</h4><p>仅显示导入前需要核对的主要信息。</p></div><span>{{rows.length}} 项</span></div>
      <div v-if="rows.length" class="table-scroll"><table class="erp-simple-table"><thead><tr><th>序号</th><th>物料编码</th><th>物料名称</th><th>规格</th><th>数量</th><th>单价</th><th>金额</th><th>处理状态</th></tr></thead><tbody><tr v-for="row in rows" :key="row.rowIndex"><td>{{row.rowIndex}}</td><td>{{text(row.item_code_full || row.itemCodeFull)}}</td><td>{{text(row.item_name || row.itemName)}}</td><td>{{text(row.spec_raw || row.specRaw)}}</td><td>{{text(row.qty)}} {{text(row.unit)}}</td><td>{{amount(row.accountingUnitPrice ?? row.unit_price ?? row.unitPrice)}}</td><td>{{amount(row.accountingAmount ?? row.total_price ?? row.totalPrice)}}</td><td>{{status(row)}}</td></tr></tbody></table></div>
      <p v-else class="muted">ERP 尚未返回可预览的设计明细。</p>
    </section>

    <section v-if="warnings.length || errors.length" class="erp-business-section"><h4>处理提示</h4><ul v-if="warnings.length" class="erp-message-list"><li v-for="(item, index) in warnings" :key="'warning-'+index">{{text(item)}}</li></ul><ul v-if="errors.length" class="erp-message-list is-error"><li v-for="(item, index) in errors" :key="'error-'+index">{{text(item)}}</li></ul></section>
  </div>
</template>
