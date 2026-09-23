import type { ErpDesignColumn, ErpDesignRow } from './erpDesignPreview'
import { erpDesignResultCell, type ErpDesignResultTable } from './erpDesignResultTables'

function record(value: unknown): Record<string, any> | null {
  return value && typeof value === 'object' && !Array.isArray(value) ? value as Record<string, any> : null
}

function payload(value: unknown): Record<string, any> {
  const source = record(value)
  return record(source?.data) ?? source ?? {}
}

function toolItems(run: any) {
  if (!['SUCCEEDED', 'FAILED'].includes(String(run?.status ?? ''))) return []
  const seen = new Set<string>()
  const items: any[] = []
  for (const item of Array.isArray(run?.trace) ? run.trace : []) {
    if (!item || !(item.type === 'tool' || item.tool)) continue
    const tool = String(item.tool ?? '')
    const key = String(item.id ?? item.call_id ?? tool)
    if (!tool || seen.has(key)) continue
    seen.add(key)
    items.push(item)
  }
  return items
}

function table(
  key: string,
  title: string,
  rows: ErpDesignRow[],
  columns: ErpDesignColumn[],
  summary: string,
  context = '',
): ErpDesignResultTable {
  return {
    key,
    title,
    context,
    summary,
    sourceNote: '数据来自 ERP 委外待办，仅供展示',
    rows,
    columns,
    defer: rows.length > 8,
  }
}

const boardColumns: ErpDesignColumn[] = [
  { key: 'station', label: '进度', fields: ['stationLabel', 'station'], width: 110 },
  { key: 'outsourceType', label: '委外类型', fields: ['outsourceTypeLabel', 'outsourceType'], width: 100 },
  { key: 'moldNo', label: '模具号', fields: ['moldNo', 'mold_no'], width: 160 },
  { key: 'partDetails', label: '零件明细', fields: ['partDetails', 'part_details'], width: 240 },
  { key: 'referenceTotal', label: '核算价', fields: ['referenceTotal', 'reference_total'], width: 100, decimals: 2 },
  { key: 'ourQuote', label: '我方报价', fields: ['ourQuoteAmount', 'our_quote_amount'], width: 100, decimals: 2 },
  { key: 'ceiling', label: '接单上限', fields: ['autoAcceptMaxAmount', 'auto_accept_max_amount'], width: 100, decimals: 2 },
  { key: 'supplierQuotes', label: '加工商报价', fields: ['supplierQuotes', 'supplier_quotes'], width: 160 },
  { key: 'finalDeal', label: '成交价', fields: ['finalDealAmount', 'final_deal_amount'], width: 100, decimals: 2 },
  { key: 'pending', label: '待报价加工商', fields: ['pendingQuoteSuppliers', 'pending_quote_suppliers'], width: 160 },
]

const stepColumns: ErpDesignColumn[] = [
  { key: 'step', label: '步骤', fields: ['step'], width: 140 },
  { key: 'state', label: '状态', fields: ['state'], width: 110 },
  { key: 'detail', label: '说明', fields: ['detail'], width: 420 },
]

function list(value: unknown): ErpDesignRow[] {
  return Array.isArray(value) ? value.filter(record) as ErpDesignRow[] : []
}

const BOARD_TOOLS = new Set(['query_erp_outsource_followup_board', 'query_buyer_todo'])
const PROGRESS_TOOLS = new Set(['query_erp_outsource_order_progress', 'query_outsource_timeline'])

export function erpOutsourceResultTablesFromRun(run: any): ErpDesignResultTable[] {
  const result: ErpDesignResultTable[] = []
  for (const item of toolItems(run)) {
    const tool = String(item.tool ?? '')
    const data = payload(item.data)
    if (String(data.status || '') === 'NEED_MOLD_CODE') continue
    if (BOARD_TOOLS.has(tool)) {
      const rows = list(data.items)
      if (!rows.length) continue
      const scope = String(data.scope || '')
      const next = table(
        `${tool}:board`,
        'ERP 委外待办',
        rows,
        boardColumns,
        `${scope || 'ERP 委外待办'} 共 ${rows.length} 条`,
        scope,
      )
      const existing = result.findIndex((item) => item.title === 'ERP 委外待办')
      if (existing >= 0) result[existing] = next
      else result.push(next)
      continue
    }
    if (PROGRESS_TOOLS.has(tool)) {
      const steps = list(data.steps)
      if (!steps.length) continue
      const moldBatch = String(data.moldBatch || data.mold_batch || '')
      const current = String(data.currentStep || '')
      result.push(table(
        `${tool}:steps`,
        '委外进度',
        steps,
        stepColumns,
        current ? `${moldBatch} 当前停在「${current}」` : String(data.summary || ''),
        moldBatch,
      ))
    }
  }
  return result
}

export { erpDesignResultCell as erpOutsourceResultCell }
