<script setup lang="ts">
import { computed } from 'vue'
import {
  erpDesignParameterCell,
  erpDesignParameterColumns,
  type ErpDesignParameterResult,
} from '../erpDesignPreview'
import ErpReadOnlyDetailCard from './ErpReadOnlyDetailCard.vue'

const props = defineProps<{
  result: ErpDesignParameterResult | null
}>()

const columns = erpDesignParameterColumns()
const rows = computed(() => props.result?.previewRows ?? [])
const summary = computed(() => `共 ${props.result?.matchedCount ?? rows.value.length} 项`)
</script>

<template>
  <ErpReadOnlyDetailCard
    v-if="result?.renderAsTable"
    title="设计参数明细"
    ready-title="设计参数明细已就绪"
    :context="result.moldCode"
    :summary="summary"
    source-note="数据来自 ERP 当前上传结果，仅供展示"
    table-label="ERP 设计参数明细表"
    empty-text="ERP 未返回可展示的设计参数。"
    :rows="rows"
    :columns="columns"
    :cell="erpDesignParameterCell"
    defer
  />
</template>
