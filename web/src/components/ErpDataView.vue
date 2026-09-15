<script setup lang="ts">
import {computed} from 'vue'

defineOptions({name: 'ErpDataView'})

const props = withDefaults(defineProps<{value: unknown; maxRows?: number}>(), {maxRows: 20})

const labels: Record<string, string> = {
  sessionId: '上传会话 ID', filename: '文件名称', sheetType: '清单类型', moldCode: '模号',
  canImport: '允许导入', valid: '校验通过', rowCount: '明细行数', totalQuantity: '总数量',
  phase: '处理阶段', status: '状态', progressPercent: '处理进度', drawingProcessing: '图纸处理中',
  drawingProcessingStatus: '图纸处理状态', drawingProcessingMessage: '图纸处理说明',
  requestId: 'ERP 申请 ID', requestNo: '申请编号', createdMode: '创建方式',
  processName: '审批流程', currentNodeName: '当前审批节点', message: '处理结果',
  warnings: '提示信息', errors: '错误信息', previewRows: '解析明细', validation: '校验结果',
  approvalConfig: '审批配置', rules: '适用规则', ruleScope: '适用范围',
  processKeyword: '流程关键字', unitPrice: '单价', pricingNote: '核价说明',
  chargeMode: '计费方式', dimensionField: '尺寸字段', dimensionMax: '最大尺寸',
  dimensionMinInclusive: '最小尺寸含边界', dimensionMaxInclusive: '最大尺寸含边界',
  supplierId: '供应商 ID', supplierCode: '供应商编码', supplierName: '供应商名称',
  sourceReference: '来源依据', remark: '备注', expectedDate: '交期', urgencyLevel: '紧急程度',
  importPerformed: '已执行导入', importMode: '导入方式', createdAt: '创建时间', updatedAt: '更新时间',
}

function isObject(value: unknown): value is Record<string, unknown> {
  return Boolean(value) && typeof value === 'object' && !Array.isArray(value)
}
function label(key: string) {
  return labels[key] ?? key.replace(/([a-z])([A-Z])/g, '$1 $2')
}
function format(value: unknown) {
  if (value === null || value === undefined || value === '') return '—'
  if (value === true) return '是'
  if (value === false) return '否'
  if (Array.isArray(value)) return value.length ? `共 ${value.length} 项` : '无'
  if (isObject(value)) return `包含 ${Object.keys(value).length} 项`
  return String(value)
}

const objectValue = computed(() => isObject(props.value) ? props.value : {})
const facts = computed(() => Object.entries(objectValue.value).filter(([, value]) => !Array.isArray(value) && !isObject(value)))
const sections = computed(() => Object.entries(objectValue.value).filter(([, value]) => Array.isArray(value) || isObject(value)))
function rows(value: unknown) {
  return Array.isArray(value) && value.every(isObject) ? value.slice(0, props.maxRows) : []
}
function columns(value: unknown) {
  const names: string[] = []
  for (const row of rows(value)) {
    for (const key of Object.keys(row)) if (!names.includes(key)) names.push(key)
  }
  return names.slice(0, 12)
}
</script>

<template>
  <div class="erp-data-view">
    <dl v-if="facts.length" class="erp-data-facts">
      <template v-for="[key, value] in facts" :key="key"><dt>{{label(key)}}</dt><dd>{{format(value)}}</dd></template>
    </dl>
    <section v-for="[key, value] in sections" :key="key" class="erp-data-section">
      <h4>{{label(key)}}</h4>
      <template v-if="Array.isArray(value)">
        <p v-if="!value.length" class="muted">暂无内容</p>
        <ul v-else-if="!rows(value).length" class="erp-data-list"><li v-for="(item, index) in value" :key="index">{{format(item)}}</li></ul>
        <div v-else class="table-scroll"><table><thead><tr><th v-for="column in columns(value)" :key="column">{{label(column)}}</th></tr></thead><tbody><tr v-for="(row, index) in rows(value)" :key="index"><td v-for="column in columns(value)" :key="column">{{format(row[column])}}</td></tr></tbody></table><p v-if="value.length > maxRows" class="muted small">仅显示前 {{maxRows}} 项，共 {{value.length}} 项。</p></div>
      </template>
      <ErpDataView v-else :value="value" :max-rows="maxRows"/>
    </section>
  </div>
</template>
