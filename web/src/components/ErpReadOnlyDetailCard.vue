<script setup lang="ts">
import { nextTick, onMounted, onUnmounted, ref } from 'vue'
import { X } from 'lucide-vue-next'
import type { ErpDesignColumn, ErpDesignRow } from '../erpDesignPreview'
import ErpReadOnlyTable from './ErpReadOnlyTable.vue'

const props = withDefaults(defineProps<{
  title: string
  readyTitle: string
  context?: string
  summary: string
  sourceNote: string
  tableLabel: string
  emptyText: string
  viewLabel?: string
  rows: ErpDesignRow[]
  columns: ErpDesignColumn[]
  cell: (row: ErpDesignRow, column: ErpDesignColumn) => string
  defer?: boolean
}>(), {
  context: '',
  defer: false,
  viewLabel: '查看明细',
})

const open = ref(false)
const trigger = ref<HTMLButtonElement | null>(null)

function openDetails() {
  open.value = true
}

function closeDetails() {
  open.value = false
  nextTick(() => trigger.value?.focus())
}

function onEscape(event: KeyboardEvent) {
  if (open.value && event.key === 'Escape') closeDetails()
}

onMounted(() => window.addEventListener('keydown', onEscape))
onUnmounted(() => window.removeEventListener('keydown', onEscape))
</script>

<template>
  <template v-if="defer">
    <section class="erp-readonly-result-action" :aria-label="`${title}摘要`">
      <span>
        <strong>{{readyTitle}}</strong>
        <small><template v-if="context">{{context}} · </template>{{summary}} · 点击查看完整表格</small>
      </span>
      <button ref="trigger" type="button" :disabled="!rows.length" @click="openDetails">
        {{rows.length?viewLabel:'暂无明细'}}
      </button>
    </section>
    <Teleport to="body">
      <div v-if="open" class="modal-shade erp-readonly-detail-shade" @click.self="closeDetails">
        <section class="modal erp-readonly-detail-modal" role="dialog" aria-modal="true" :aria-label="tableLabel">
          <header class="erp-readonly-detail-head">
            <div>
              <h2>{{title}}</h2>
              <p><template v-if="context">{{context}} · </template>{{summary}} · {{sourceNote}}</p>
            </div>
            <button type="button" class="icon-button" :aria-label="`关闭${title}`" @click="closeDetails"><X :size="17"/></button>
          </header>
          <div class="erp-readonly-detail-body">
            <ErpReadOnlyTable
              :title="props.title"
              :context="props.context"
              :summary="props.summary"
              :source-note="props.sourceNote"
              :table-label="props.tableLabel"
              :empty-text="props.emptyText"
              :rows="props.rows"
              :columns="props.columns"
              :cell="props.cell"
              :show-header="false"
            >
              <template v-if="$slots.cell" #cell="scope"><slot name="cell" v-bind="scope"/></template>
            </ErpReadOnlyTable>
            <slot name="footer" />
          </div>
        </section>
      </div>
    </Teleport>
  </template>
  <template v-else>
    <ErpReadOnlyTable
      :title="props.title"
      :context="props.context"
      :summary="props.summary"
      :source-note="props.sourceNote"
      :table-label="props.tableLabel"
      :empty-text="props.emptyText"
      :rows="props.rows"
      :columns="props.columns"
      :cell="props.cell"
    >
      <template v-if="$slots.cell" #cell="scope"><slot name="cell" v-bind="scope"/></template>
    </ErpReadOnlyTable>
    <slot name="footer" />
  </template>
</template>

<style scoped>
.erp-readonly-result-action{display:flex;align-items:center;justify-content:space-between;gap:18px;width:100%;max-width:100%;min-width:0;margin-top:12px;padding:12px 14px;border:1px solid color-mix(in srgb,var(--accent) 18%,var(--border));border-radius:11px;background:color-mix(in srgb,var(--accent) 6%,var(--surface));overflow:hidden}
.erp-readonly-result-action span{min-width:0}.erp-readonly-result-action strong{display:block;font-size:13px;line-height:1.4}.erp-readonly-result-action small{margin-top:3px;color:var(--muted);font-size:11px;line-height:1.4;overflow-wrap:anywhere}.erp-readonly-result-action button{flex:0 0 auto;height:32px;padding:0 12px;border-radius:8px;font-size:12px}
.erp-readonly-detail-shade{z-index:125;padding:18px}.erp-readonly-detail-modal{width:min(1480px,calc(100vw - 36px));height:min(900px,calc(100dvh - 36px));max-width:none;display:flex;flex-direction:column;padding:0;overflow:hidden;background:var(--surface)}
.erp-readonly-detail-head{min-height:68px;display:flex;align-items:center;justify-content:space-between;gap:18px;padding:12px 18px;border-bottom:1px solid color-mix(in srgb,var(--border) 72%,transparent)}.erp-readonly-detail-head>div{min-width:0}.erp-readonly-detail-head h2{margin:0;font-size:18px;line-height:1.35}.erp-readonly-detail-head p{margin:3px 0 0;color:var(--muted);font-size:12px;line-height:1.45;overflow-wrap:anywhere}.erp-readonly-detail-body{min-width:0;min-height:0;flex:1;padding:14px;overflow:hidden}.erp-readonly-detail-body :deep(.erp-readonly-table){display:flex;flex-direction:column;width:100%;max-width:100%;min-width:0;min-height:0;height:100%;margin:0;border:0}.erp-readonly-detail-body :deep(.erp-readonly-table-scroll){flex:1 1 auto;width:100%;max-width:100%;min-width:0;min-height:0;max-height:none;height:auto;overflow:auto}.erp-readonly-detail-body :deep(.erp-readonly-table.is-processing-diff .erp-readonly-table-scroll){scrollbar-width:thin;scrollbar-color:#8291a2 #252b32}.erp-readonly-detail-body :deep(.erp-readonly-table.is-processing-diff .erp-readonly-table-scroll::-webkit-scrollbar){width:12px;height:12px}.erp-readonly-detail-body :deep(.erp-readonly-table.is-processing-diff .erp-readonly-table-scroll::-webkit-scrollbar-thumb){border:3px solid #252b32;border-radius:8px;background:#8291a2}
@media(max-width:700px){.erp-readonly-result-action{align-items:flex-start;flex-direction:column}.erp-readonly-result-action button{width:100%}.erp-readonly-detail-shade{padding:0}.erp-readonly-detail-modal{width:100vw;height:100dvh;border:0;border-radius:0}.erp-readonly-detail-head{align-items:flex-start}.erp-readonly-detail-body{padding:8px}}
</style>
