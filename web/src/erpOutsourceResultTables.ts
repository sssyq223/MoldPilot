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
  { key: 'orderNo', label: '订单号', fields: ['orderNo', 'order_no'], width: 150 },
  { key: 'moldFamily', label: '模具号', fields: ['moldFamily', 'mold_family'], width: 120 },
  { key: 'moldBatch', label: '批次号', fields: ['moldBatch', 'moldNo', 'mold_no'], width: 140 },
  { key: 'partDetails', label: '零件明细', fields: ['partDetails', 'part_details'], width: 240 },
  { key: 'referenceTotal', label: '核算价', fields: ['referenceTotal', 'reference_total'], width: 100, decimals: 2 },
  { key: 'ourQuote', label: '我方报价', fields: ['ourQuoteAmount', 'our_quote_amount', 'ourQuote'], width: 100, decimals: 2 },
  { key: 'ceiling', label: '接单上限', fields: ['autoAcceptMaxAmount', 'auto_accept_max_amount', 'ceiling'], width: 100, decimals: 2 },
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

type BuyerQuotePatch = {
  mold: string
  batch: string
  part: string
  ourQuote: number | string
  ceiling: number | string
  referenceTotal: number | null
}

const PART_TOKEN = /(?<![A-Z0-9])([A-Z]{1,8}-\d{1,4}[A-Z]?)(?![A-Z0-9])/i

function partToken(value: unknown): string {
  const match = PART_TOKEN.exec(String(value || ''))
  return match ? match[1].toUpperCase() : ''
}

function numericAmount(value: unknown): number | null {
  if (typeof value === 'number' && Number.isFinite(value)) return value
  if (typeof value === 'object' && value !== null && '金额' in value) return numericAmount((value as { 金额?: unknown }).金额)
  const text = String(value ?? '').replace(/,/g, '').trim()
  if (!text) return null
  const parsed = Number(text)
  return Number.isFinite(parsed) ? parsed : null
}

function quoteAmount(value: unknown): number | string | null {
  if (value == null || value === '') return null
  const numeric = numericAmount(value)
  if (numeric != null) return numeric
  const text = String(value).trim()
  return text || null
}

function rowPartText(row: ErpDesignRow): string {
  const parts = Array.isArray(row.parts)
    ? row.parts.map((item: any) => [item?.partNo, item?.part_no, item?.partName, item?.part_name].filter(Boolean).join(' ')).join(' ')
    : ''
  return `${row.partDetails || ''} ${row.parts || ''} ${parts}`
}

export function erpOutsourceQuotePatchesFromRuns(runs: any[], _confirmed: Record<string, boolean> = {}): BuyerQuotePatch[] {
  const patches: BuyerQuotePatch[] = []
  for (const run of Array.isArray(runs) ? runs : []) {
    for (const item of Array.isArray(run?.trace) ? run.trace : []) {
      if (String(item?.tool || '') !== 'prepare_erp_outsource_buyer_quote') continue
      if (item.proposal_decision === 'dismissed') continue
      const display = record(item.proposal?.display)
      if (!display) continue
      const table = Array.isArray(display['报价表']) ? display['报价表'] : []
      const quoteRow = table.find((row: any) => String(row?.项目 || '') === '我方报价')
      const ceilingRow = table.find((row: any) => String(row?.项目 || '').includes('上限'))
      const referenceRow = table.find((row: any) => String(row?.项目 || '') === '核算价')
      const ourQuote = quoteAmount(display['我方报价']) ?? quoteAmount(quoteRow?.金额)
      const ceiling = quoteAmount(display['直接接单上限']) ?? quoteAmount(ceilingRow?.金额)
      if (ourQuote == null || ceiling == null) continue
      patches.push({
        mold: String(display['模具号'] || ''),
        batch: String(display['批次号'] || ''),
        part: partToken(display['零件']),
        ourQuote,
        ceiling,
        referenceTotal: numericAmount(referenceRow?.金额) ?? numericAmount(display['核算价']),
      })
    }
  }
  return patches
}

function rowMatchesQuote(row: ErpDesignRow, patch: BuyerQuotePatch): boolean {
  const mold = String(row.moldFamily || row.mold || '')
  const batch = String(row.moldBatch || row.moldNo || row.batch || '')
  const details = rowPartText(row)
  if (patch.batch && batch && patch.batch !== batch && !String(batch).includes(patch.batch)) return false
  if (patch.mold && mold && patch.mold !== mold && !String(mold).includes(patch.mold)) return false
  const rowReference = numericAmount(row.referenceTotal ?? row.reference_total)
  if (patch.referenceTotal != null) {
    return rowReference != null && Math.abs(rowReference - patch.referenceTotal) < 0.009
  }
  if (!patch.part) return false
  const escaped = patch.part.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')
  return new RegExp(`(?<![A-Z0-9])${escaped}(?![A-Z0-9])`, 'i').test(details)
}

function applyQuotePatches(rows: ErpDesignRow[], patches: BuyerQuotePatch[]): ErpDesignRow[] {
  if (!patches.length) return rows
  const next = rows.map((row) => ({ ...row }))
  for (const patch of patches) {
    const hits = next.map((row, index) => ({ row, index })).filter(({ row }) => rowMatchesQuote(row, patch))
    if (hits.length !== 1) continue
    const { row, index } = hits[0]
    const details = String(row.partDetails || row.parts || '')
    const labeled = patch.part && !details.toUpperCase().includes(patch.part)
      ? `${patch.part} ${details}`.trim()
      : details
    next[index] = {
      ...row,
      partDetails: labeled,
      ourQuoteAmount: patch.ourQuote,
      autoAcceptMaxAmount: patch.ceiling,
      ourQuote: patch.ourQuote,
      ceiling: patch.ceiling,
    }
  }
  return next
}

const BOARD_TOOLS = new Set([
  'query_erp_outsource_followup_board',
  'query_erp_outsource_processor_board',
  'query_buyer_todo',
])
const PROGRESS_TOOLS = new Set(['query_erp_outsource_order_progress', 'query_outsource_timeline'])

export function erpOutsourceResultTablesFromRun(run: any, quotePatches: BuyerQuotePatch[] = []): ErpDesignResultTable[] {
  const result: ErpDesignResultTable[] = []
  const patches = quotePatches.length ? quotePatches : erpOutsourceQuotePatchesFromRuns([run])
  for (const item of toolItems(run)) {
    const tool = String(item.tool ?? '')
    const data = payload(item.data)
    if (String(data.status || '') === 'NEED_MOLD_CODE') continue
    if (BOARD_TOOLS.has(tool)) {
      const rows = applyQuotePatches(list(data.items), patches)
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
