<script setup lang="ts">
import { computed } from 'vue'
import {
  erpDesignToleranceCell,
  erpDesignToleranceColumns,
  type ErpDesignPreviewSession,
} from '../erpDesignPreview'
import ErpReadOnlyDetailCard from './ErpReadOnlyDetailCard.vue'

const props = defineProps<{
  preview: ErpDesignPreviewSession | null
}>()

const columns = erpDesignToleranceColumns()
const rows = computed(() => props.preview?.previewRows ?? [])
const evaluatedCount = computed(() => Number(
  props.preview?.toleranceEvaluation?.evaluatedCount
  ?? props.preview?.toleranceEvaluation?.evaluated_count
  ?? rows.value.filter(row => String(row.toleranceTier ?? row.tolerance_tier ?? '-') !== '-').length,
))
const summary = computed(() => `共 ${rows.value.length} 项，适用公差 ${evaluatedCount.value} 项`)
</script>

<template>
  <ErpReadOnlyDetailCard
    v-if="preview"
    title="公差明细"
    ready-title="公差明细已就绪"
    :context="preview.moldCode"
    :summary="summary"
    source-note="数据来自 ERP 当前上传会话，仅供展示"
    table-label="ERP 公差明细表"
    empty-text="ERP 未返回可展示的钢料明细。"
    :rows="rows"
    :columns="columns"
    :cell="erpDesignToleranceCell"
    defer
  />
</template>
