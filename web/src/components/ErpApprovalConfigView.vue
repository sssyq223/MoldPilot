<script setup lang="ts">
import {computed} from 'vue'

const props = defineProps<{value: any}>()
const process = computed(() => props.value?.process || {})
const nodes = computed<any[]>(() => Array.isArray(props.value?.nodes) ? props.value.nodes : [])
const coverage = computed(() => props.value?.attachedSquarePriceCoverage || {})
const coverageItems = computed<any[]>(() => Array.isArray(coverage.value?.items) ? coverage.value.items : [])

function people(value: unknown) {
  if (!Array.isArray(value) || !value.length) return ''
  return value.map((item: any) => item?.name || item?.displayName || item?.roleName || item?.username || item?.code || String(item)).filter(Boolean).join('、')
}
function approver(node: any) {
  return people(node.resolvedAssigneeUsers) || people(node.candidateUsers) || people(node.roles) || '由 ERP 按流程配置确定'
}
function amount(value: unknown) {
  const number = Number(value)
  return Number.isFinite(number) ? `¥${number.toLocaleString('zh-CN', {maximumFractionDigits: 2})}` : '—'
}
</script>

<template>
  <div class="erp-business-view">
    <section class="erp-business-section erp-process-card"><div><small>将启动的审批流程</small><h4>{{process.processName || 'ERP 审批流程'}}</h4><p>{{process.description || '审批流程由 ERP 当前有效配置决定。'}}</p></div><dl><dt>流程版本</dt><dd>{{process.version || '—'}}</dd><dt>状态</dt><dd>{{process.status || '—'}}</dd></dl></section>
    <section v-if="nodes.length" class="erp-business-section"><div class="erp-business-heading"><div><h4>审批步骤</h4><p>导入后申请将依次经过下列节点。</p></div><span>{{nodes.length}} 步</span></div><ol class="erp-approval-nodes"><li v-for="node in nodes" :key="node.nodeId"><span>{{node.nodeOrder}}</span><div><strong>{{node.nodeName}}</strong><p>审批人：{{approver(node)}}</p><small>{{node.commentRequired ? '需要填写审批意见' : '审批意见可选'}}</small></div></li></ol></section>
    <section v-if="coverage.targetCount !== undefined" class="erp-business-section"><div class="erp-business-heading"><div><h4>价格覆盖情况</h4><p>ERP 对本次设计明细的价格匹配结果。</p></div><span :class="coverage.fullyPriced ? 'is-positive' : ''">{{coverage.fullyPriced ? '已覆盖' : '待补充'}}</span></div><div class="erp-coverage-stats"><div><small>明细总数</small><strong>{{coverage.targetCount ?? 0}}</strong></div><div><small>已匹配价格</small><strong>{{coverage.pricedCount ?? 0}}</strong></div><div><small>待补充价格</small><strong>{{coverage.missingCount ?? 0}}</strong></div></div><div v-if="coverageItems.length" class="table-scroll"><table class="erp-simple-table"><thead><tr><th>物料编码</th><th>物料名称</th><th>匹配结果</th><th>单价</th><th>供应商</th><th>说明</th></tr></thead><tbody><tr v-for="item in coverageItems" :key="item.detailId"><td>{{item.materialNo || '—'}}</td><td>{{item.materialName || '—'}}</td><td>{{item.matched ? '已匹配' : '待补充'}}</td><td>{{amount(item.unitPrice)}}</td><td>{{item.supplierName || '—'}}</td><td>{{item.reason || '—'}}</td></tr></tbody></table></div></section>
  </div>
</template>
