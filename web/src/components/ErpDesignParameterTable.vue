<script setup lang="ts">
import { computed, nextTick, ref } from 'vue'
import {
  erpDesignParameterCell,
  erpDesignParameterColumns,
  type ErpDesignParameterResult,
  type ErpDesignRow,
} from '../erpDesignPreview'
import ErpReadOnlyDetailCard from './ErpReadOnlyDetailCard.vue'
import ErpDrawingPreview from './ErpDrawingPreview.vue'

const props = defineProps<{
  result: ErpDesignParameterResult | null
}>()

const columns = computed(() => props.result?.columns?.length ? props.result.columns : erpDesignParameterColumns())
const rows = computed(() => props.result?.previewRows ?? [])
const summary = computed(() => `共 ${props.result?.matchedCount ?? rows.value.length} 项`)
const selectedRow = ref<ErpDesignRow | null>(null)
let trigger: HTMLButtonElement | null = null

function openDrawing(row: ErpDesignRow, event: MouseEvent) {
  trigger = event.currentTarget as HTMLButtonElement
  selectedRow.value = row
}

function closeDrawing() {
  selectedRow.value = null
  nextTick(() => trigger?.focus())
}
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
  >
    <template #cell="{row,column}">
      <button v-if="column.key==='drawing'" type="button" class="drawing-preview-button" :disabled="!row.drawing_resource_id" @click="openDrawing(row,$event)">预览图纸</button>
      <template v-else>{{erpDesignParameterCell(row,column)}}</template>
    </template>
  </ErpReadOnlyDetailCard>
  <Teleport to="body">
    <ErpDrawingPreview v-if="selectedRow" source="upload" :session-id="result?.sessionId" :row="selectedRow" @close="closeDrawing"/>
  </Teleport>
</template>

<style scoped>
.drawing-preview-button{height:28px;padding:0 10px;font-size:12px;border-radius:7px}
</style>
