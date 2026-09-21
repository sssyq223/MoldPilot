<script setup lang="ts">
import { computed, nextTick, ref } from 'vue'
import {
  erpDesignDrawingColumns,
  erpDesignToleranceCell,
  type ErpDesignDrawingResult,
  type ErpDesignRow,
} from '../erpDesignPreview'
import ErpReadOnlyDetailCard from './ErpReadOnlyDetailCard.vue'
import ErpDrawingPreview from './ErpDrawingPreview.vue'

const props = defineProps<{ result: ErpDesignDrawingResult }>()

const columns = computed(() => erpDesignDrawingColumns(props.result.source))
const title = computed(() => props.result.source === 'standard_hardware' ? '厂内标准件图纸' : '图纸明细')
const summary = computed(() => props.result.totalCount > props.result.previewRows.length
  ? `本次 ${props.result.previewRows.length} 项，共 ${props.result.totalCount} 项图纸`
  : `共 ${props.result.previewRows.length} 项图纸`)
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
    :title="title"
    :ready-title="result.previewRows.length?'图纸明细已就绪':'暂无可预览图纸'"
    view-label="查看图纸"
    :context="result.moldCode"
    :summary="summary"
    source-note="点击预览查看图纸"
    table-label="ERP 图纸明细表"
    empty-text="当前筛选范围内暂无已匹配的图纸。"
    :rows="result.previewRows"
    :columns="columns"
    :cell="erpDesignToleranceCell"
    defer
  >
    <template #cell="{row,column}">
      <button v-if="column.key==='preview'" type="button" class="drawing-preview-button" :disabled="result.source==='standard_hardware'&&!row.relativePath" @click="openDrawing(row,$event)">预览图纸</button>
      <template v-else>{{erpDesignToleranceCell(row,column)}}</template>
    </template>
  </ErpReadOnlyDetailCard>
  <Teleport to="body">
    <ErpDrawingPreview v-if="selectedRow" :source="result.source" :session-id="result.sessionId" :row="selectedRow" @close="closeDrawing"/>
  </Teleport>
</template>

<style scoped>
.drawing-preview-button{height:28px;padding:0 10px;font-size:12px;border-radius:7px}
</style>
