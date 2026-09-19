<script setup lang="ts">
import type { ErpDesignColumn, ErpDesignRow } from '../erpDesignPreview'

withDefaults(defineProps<{
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

function rowKey(row: ErpDesignRow, index: number) {
  return row.drawing_row_key
    ?? row.drawingRowKey
    ?? row.rowIndex
    ?? row.row_index
    ?? row.item_code_full
    ?? index
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
            <td v-for="column in columns" :key="column.key" :title="cell(row,column)">
              {{cell(row,column)}}
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
.erp-readonly-table-scroll{width:100%;max-width:100%;max-height:420px;overflow:auto}.erp-readonly-table-scroll table{width:max-content;min-width:100%;border-collapse:collapse;font-size:12px}.erp-readonly-table-scroll th{position:sticky;top:0;z-index:2;padding:9px 10px;border:1px solid var(--border);background:color-mix(in srgb,var(--surface) 82%,var(--bg));color:var(--muted);font-weight:500;text-align:left;white-space:nowrap}.erp-readonly-table-scroll td{height:42px;max-width:220px;padding:8px 10px;border:1px solid var(--border);color:var(--text);overflow:hidden;text-overflow:ellipsis;white-space:nowrap}.erp-readonly-table-scroll tbody tr:hover td{background:color-mix(in srgb,var(--accent) 5%,var(--surface))}.erp-readonly-table-scroll .erp-readonly-index{position:sticky;left:0;z-index:1;width:52px;min-width:52px;max-width:52px;background:color-mix(in srgb,var(--surface) 92%,var(--bg));text-align:center}.erp-readonly-table-scroll th.erp-readonly-index{z-index:3}.erp-readonly-empty{margin:0;padding:18px;color:var(--muted);font-size:12px}
@media(max-width:700px){.erp-readonly-table-head{align-items:flex-start;flex-direction:column;gap:3px}.erp-readonly-table-scroll{max-height:360px}}
</style>
