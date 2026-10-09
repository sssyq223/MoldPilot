<script setup lang="ts">
import { ChevronDown, ChevronUp } from 'lucide-vue-next'
import { ref } from 'vue'
import type { ErpDesignColumn, ErpDesignRow } from '../erpDesignPreview'
import { collapsedPartPreview } from '../erpDesignPreview'

const props = withDefaults(defineProps<{
  title: string
  context?: string
  summary: string
  sourceNote: string
  tableLabel: string
  emptyText: string
  rows: ErpDesignRow[]
  columns: ErpDesignColumn[]
  cell: (row: ErpDesignRow, column: ErpDesignColumn) => string
  showHeader?: boolean
}>(), {
  context: '',
  showHeader: true,
})

const expandedParts = ref<Record<string, boolean>>({})

function rowKey(row: ErpDesignRow, index: number) {
  return String(
    row.drawing_row_key
    ?? row.drawingRowKey
    ?? row.rowIndex
    ?? row.row_index
    ?? row.item_code_full
    ?? row.orderNo
    ?? index,
  )
}

function partsKey(row: ErpDesignRow, index: number, column: ErpDesignColumn) {
  return `${rowKey(row, index)}:${column.key}`
}

function partsView(row: ErpDesignRow, index: number, column: ErpDesignColumn) {
  const raw = props.cell(row, column)
  const { preview, hidden, lines, total } = collapsedPartPreview(raw)
  const open = Boolean(expandedParts.value[partsKey(row, index, column)])
  return {
    open,
    hidden,
    total,
    preview,
    lines,
    full: lines.join('；'),
  }
}

function toggleParts(row: ErpDesignRow, index: number, column: ErpDesignColumn) {
  const key = partsKey(row, index, column)
  expandedParts.value = { ...expandedParts.value, [key]: !expandedParts.value[key] }
}
</script>

<template>
  <section class="erp-readonly-table" :aria-label="tableLabel">
    <div v-if="showHeader" class="erp-readonly-table-head">
      <div>
        <strong>{{title}}</strong>
        <span v-if="context">{{context}} · </span>
        <span>{{summary}}</span>
      </div>
      <small>{{sourceNote}}</small>
    </div>
    <div v-if="rows.length" class="erp-readonly-table-scroll">
      <table>
        <thead>
          <tr>
            <th class="erp-readonly-index">NO.</th>
            <th v-for="column in columns" :key="column.key" :style="{minWidth:`${column.width||100}px`}">
              {{column.label}}
            </th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="(row,index) in rows" :key="rowKey(row,Number(index))">
            <td class="erp-readonly-index">{{row.rowIndex??row.row_index??Number(index)+1}}</td>
            <td
              v-for="column in columns"
              :key="column.key"
              :class="{
                'erp-readonly-wrap': column.kind === 'wrap',
                'erp-readonly-parts': column.kind === 'parts',
                'erp-readonly-parts-open': column.kind === 'parts' && partsView(row, Number(index), column).open,
              }"
              :title="column.kind === 'parts' ? partsView(row, Number(index), column).full : cell(row, column)"
            >
              <template v-if="column.kind === 'parts'">
                <div class="erp-readonly-parts-cell">
                  <template v-if="partsView(row, Number(index), column).open">
                    <ul class="erp-readonly-parts-list">
                      <li v-for="line in partsView(row, Number(index), column).lines" :key="line">{{line}}</li>
                    </ul>
                    <button type="button" class="erp-readonly-parts-more" @click="toggleParts(row, Number(index), column)">
                      收起
                      <ChevronUp :size="12"/>
                    </button>
                  </template>
                  <template v-else>
                    <span class="erp-readonly-parts-preview">{{partsView(row, Number(index), column).preview}}</span>
                    <button
                      v-if="partsView(row, Number(index), column).hidden"
                      type="button"
                      class="erp-readonly-parts-more"
                      :aria-label="`展开全部 ${partsView(row, Number(index), column).total} 项零件`"
                      @click="toggleParts(row, Number(index), column)"
                    >
                      等{{partsView(row, Number(index), column).total}}项
                      <ChevronDown :size="12"/>
                    </button>
                  </template>
                </div>
              </template>
              <slot v-else name="cell" :row="row" :column="column">{{cell(row,column)}}</slot>
            </td>
          </tr>
        </tbody>
      </table>
    </div>
    <p v-else class="erp-readonly-empty">{{emptyText}}</p>
  </section>
</template>

<style scoped>
.erp-readonly-table{width:100%;max-width:100%;min-width:0;margin-top:12px;border:1px solid color-mix(in srgb,var(--accent) 18%,var(--border));border-radius:11px;background:var(--surface);overflow:hidden}
.erp-readonly-table-head{display:flex;align-items:center;justify-content:space-between;gap:16px;padding:11px 13px;border-bottom:1px solid var(--border);background:color-mix(in srgb,var(--accent) 5%,var(--surface))}
.erp-readonly-table-head>div{display:flex;align-items:baseline;gap:8px;min-width:0}.erp-readonly-table-head strong{font-size:13px}.erp-readonly-table-head span,.erp-readonly-table-head small{color:var(--muted);font-size:11px}.erp-readonly-table-head small{flex:0 0 auto}
.erp-readonly-table-scroll{width:100%;max-width:100%;max-height:420px;overflow:auto}.erp-readonly-table-scroll table{width:max-content;min-width:100%;border-collapse:collapse;font-size:12px}.erp-readonly-table-scroll th{position:sticky;top:0;z-index:2;padding:9px 10px;border:1px solid var(--border);background:color-mix(in srgb,var(--surface) 82%,var(--bg));color:var(--muted);font-weight:500;text-align:left;white-space:nowrap}.erp-readonly-table-scroll td{height:42px;max-width:220px;padding:8px 10px;border:1px solid var(--border);color:var(--text);overflow:hidden;text-overflow:ellipsis;white-space:nowrap}.erp-readonly-table-scroll td.erp-readonly-wrap{height:auto;min-height:42px;max-width:280px;white-space:normal;overflow:visible;text-overflow:unset;line-height:1.45;overflow-wrap:anywhere}
.erp-readonly-table-scroll td.erp-readonly-parts{max-width:360px;min-width:220px;overflow:visible}
.erp-readonly-table-scroll td.erp-readonly-parts-open{height:auto;min-height:42px;white-space:normal}
.erp-readonly-parts-cell{display:flex;align-items:center;gap:8px;min-width:0}
.erp-readonly-parts-preview{min-width:0;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.erp-readonly-parts-list{margin:0;padding:0;list-style:none;min-width:0}
.erp-readonly-parts-list li{line-height:1.45;white-space:normal}
.erp-readonly-parts-list li+li{margin-top:3px}
.erp-readonly-table-scroll td.erp-readonly-parts-open .erp-readonly-parts-cell{align-items:flex-start;flex-direction:column;gap:6px}
.erp-readonly-parts-more{flex:0 0 auto;height:22px;padding:0 8px;border:1px solid color-mix(in srgb,var(--border) 80%,transparent);border-radius:999px;background:color-mix(in srgb,var(--bg) 42%,var(--surface));color:var(--muted);font-size:11px;line-height:1;gap:3px;white-space:nowrap}
.erp-readonly-parts-more:hover{color:var(--text);border-color:color-mix(in srgb,var(--accent) 28%,var(--border));background:color-mix(in srgb,var(--accent) 10%,var(--surface))}
.erp-readonly-table-scroll tbody tr:hover td{background:color-mix(in srgb,var(--accent) 5%,var(--surface))}.erp-readonly-table-scroll .erp-readonly-index{position:sticky;left:0;z-index:1;width:52px;min-width:52px;max-width:52px;background:color-mix(in srgb,var(--surface) 92%,var(--bg));text-align:center}.erp-readonly-table-scroll th.erp-readonly-index{z-index:3}.erp-readonly-empty{margin:0;padding:18px;color:var(--muted);font-size:12px}
@media(max-width:700px){.erp-readonly-table-head{align-items:flex-start;flex-direction:column;gap:3px}.erp-readonly-table-scroll{max-height:360px}}
</style>
