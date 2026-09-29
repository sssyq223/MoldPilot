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
  sourceNote = '数据来自 ERP 委外待办，仅供展示',
): ErpDesignResultTable {
  return {
    key,
    title,
    context,
    summary,
    sourceNote,
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
  { key: 'pending', label: '加工商', fields: ['pendingQuoteSuppliers', 'pending_quote_suppliers', 'supplierName', 'supplier_name'], width: 200 },
]

// 加工商看板对齐 ERP：零件/模具能看见采购报价；工序委外没有询价，不展示采购报价。
// 核算价、接单上限、成交价仍只在采购员看板上。
const processorBoardColumns: ErpDesignColumn[] = [
  ...boardColumns.filter((column) => (
    !['referenceTotal', 'ourQuote', 'ceiling', 'supplierQuotes', 'finalDeal', 'pending'].includes(column.key)
  )),
  { key: 'process', label: '工序', fields: ['processNames', 'process_names'], width: 140 },
  { key: 'referenceTotal', label: '核算价', fields: ['referenceTotal', 'reference_total'], width: 100, decimals: 2 },
  { key: 'buyerQuote', label: '采购报价', fields: ['buyerQuoteAmount', 'buyer_quote_amount'], width: 110, decimals: 2 },
  { key: 'myQuote', label: '我的报价', fields: ['supplierQuotes', 'supplier_quotes'], width: 140 },
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

const WAREHOUSE_TOOLS = new Set(['query_erp_outsource_warehouse_tasks'])
const WAREHOUSE_INBOUND_TOOLS = new Set(['query_erp_outsource_warehouse_inbound'])
const PRODUCT_SHIP_TOOLS = new Set(['query_erp_outsource_processor_product_ship'])
const QUALITY_TOOLS = new Set(['query_erp_outsource_quality_tasks'])

const warehouseColumns: ErpDesignColumn[] = [
  { key: 'action', label: '办理', fields: ['actionLabel', 'action'], width: 110 },
  { key: 'outsourceType', label: '委外类型', fields: ['outsourceTypeLabel', 'outsourceType'], width: 100 },
  { key: 'orderNo', label: '订单号', fields: ['orderNo', 'order_no'], width: 150 },
  { key: 'mold', label: '模具号', fields: ['moldFamily', 'moldNo', 'mold_no'], width: 120 },
  { key: 'moldBatch', label: '批次号', fields: ['moldBatch', 'mold_batch'], width: 140 },
  { key: 'partDetails', label: '零件明细', fields: ['partDetails', 'part_details'], width: 420 },
  { key: 'lineCount', label: '明细数', fields: ['lineCount', 'line_count'], width: 80 },
  { key: 'qty', label: '数量合计', fields: ['qty'], width: 90 },
  { key: 'source', label: '来源', fields: ['sourceTypeLabel', 'sourceType'], width: 110 },
  { key: 'processor', label: '加工商', fields: ['processorName', 'processor_name'], width: 180 },
]

const inboundColumns: ErpDesignColumn[] = [
  { key: 'action', label: '办理', fields: ['actionLabel', 'action'], width: 110 },
  { key: 'station', label: '进度', fields: ['stationLabel', 'station'], width: 110 },
  { key: 'outsourceType', label: '委外类型', fields: ['outsourceTypeLabel', 'outsourceType'], width: 100 },
  { key: 'orderNo', label: '订单号', fields: ['orderNo', 'order_no'], width: 150 },
  { key: 'mold', label: '模具号', fields: ['moldNo', 'mold_no'], width: 140 },
  { key: 'shipment', label: '发货单', fields: ['shipmentNo', 'shipment_no'], width: 160 },
  { key: 'pendingInbound', label: '待入', fields: ['pendingInboundQty'], width: 80 },
  { key: 'inbound', label: '入库目标', fields: ['inboundTargetText', 'inboundTargets'], width: 140 },
  { key: 'processor', label: '加工商', fields: ['supplierName', 'supplier_name'], width: 180 },
]

function joinUnique(values: unknown[]): string {
  const seen: string[] = []
  for (const value of values) {
    const text = String(value ?? '').trim()
    if (text && !seen.includes(text)) seen.push(text)
  }
  return seen.join('、')
}

function supplyLineLabel(line: ErpDesignRow): string {
  const head = [line.partNo, line.partName].filter(Boolean).join(' ') || '零件'
  const extras = [
    line.qty != null && line.qty !== '' ? `×${line.qty}` : '',
    String(line.processName || '').trim(),
  ].filter(Boolean)
  return extras.length ? `${head}（${extras.join(' ')}）` : head
}

function productPartLabel(line: ErpDesignRow): string {
  const head = [line.partNo, line.partName].filter(Boolean).join(' ') || '零件'
  const remain = line.remainQty != null && line.remainQty !== '' ? `可发${line.remainQty}` : ''
  const target = String(line.inboundTargetLabel || '').trim()
  const extra = [remain, target ? `→${target}` : ''].filter(Boolean).join('')
  return extra ? `${head}（${extra}）` : head
}

function productShipRows(data: Record<string, any>): ErpDesignRow[] {
  return list(data.items).map((item) => {
    const parts = list(item.parts)
    const targets = Array.isArray(item.inboundTargets)
      ? item.inboundTargets.map((value: unknown) => String(value || '').trim()).filter(Boolean)
      : []
    return {
      ...item,
      stationLabel: item.stationLabel || item.station || '待成品发货',
      partDetails: item.partDetails || parts.map(productPartLabel).join('；'),
      lineCount: item.lineCount || parts.length,
      remainQty: item.remainQty ?? parts.reduce((sum, line) => sum + Number(line.remainQty || 0), 0),
      inboundTargetText: targets.join('、'),
    }
  })
}

const productShipColumns: ErpDesignColumn[] = [
  { key: 'station', label: '进度', fields: ['stationLabel', 'station'], width: 110 },
  { key: 'outsourceType', label: '委外类型', fields: ['outsourceTypeLabel', 'outsourceType'], width: 100 },
  { key: 'orderNo', label: '订单号', fields: ['orderNo', 'order_no'], width: 150 },
  { key: 'mold', label: '模具号', fields: ['moldFamily', 'moldNo', 'mold_no'], width: 120 },
  { key: 'moldBatch', label: '批次号', fields: ['moldBatch', 'mold_batch'], width: 140 },
  { key: 'partDetails', label: '零件明细', fields: ['partDetails', 'part_details'], width: 420 },
  { key: 'lineCount', label: '明细数', fields: ['lineCount', 'line_count'], width: 80 },
  { key: 'remainQty', label: '可发合计', fields: ['remainQty', 'remain_qty'], width: 90 },
  { key: 'inbound', label: '入库目标', fields: ['inboundTargetText', 'inboundTargets'], width: 120 },
]

const qualityColumns: ErpDesignColumn[] = [
  { key: 'action', label: '办理', fields: ['actionLabel', 'action'], width: 110 },
  { key: 'station', label: '进度', fields: ['stationLabel', 'statusLabel', 'station'], width: 110 },
  { key: 'inspectionNo', label: '质检单', fields: ['inspectionNo', 'inspection_no'], width: 160 },
  { key: 'orderNo', label: '订单号', fields: ['orderNo', 'order_no'], width: 150 },
  { key: 'inboundNo', label: '入库单', fields: ['inboundNo', 'inbound_no'], width: 150 },
  { key: 'inbound', label: '入库目标', fields: ['inboundTargetLabel'], width: 110 },
  { key: 'partDetails', label: '零件明细', fields: ['partDetails', 'part_details'], width: 360 },
  { key: 'lineCount', label: '明细数', fields: ['lineCount', 'line_count'], width: 80 },
  { key: 'warehouse', label: '仓库', fields: ['warehouse'], width: 110 },
  { key: 'processor', label: '供应商', fields: ['partnerName', 'partner_name'], width: 180 },
  { key: 'inspector', label: '领取人', fields: ['inspectorName', 'inspector_name'], width: 110 },
]

function qualityPartLabel(line: ErpDesignRow): string {
  const head = [line.partNo, line.partName].filter(Boolean).join(' ') || '物料'
  const qty = line.inboundQty || line.qty
  return qty != null && qty !== '' ? `${head}×${qty}` : head
}

function qualityRows(items: unknown): ErpDesignRow[] {
  return list(items).map((item) => {
    const details = list(item.details)
    return {
      ...item,
      stationLabel: item.stationLabel || item.station || item.statusLabel || item.actionLabel,
      partDetails: item.partDetails || details.map(qualityPartLabel).join('；'),
      lineCount: item.lineCount || details.length,
    }
  })
}

function inboundPartLabel(line: ErpDesignRow): string {
  const head = [line.partNo, line.partName].filter(Boolean).join(' ') || '零件'
  const qty = line.pendingInboundQty || line.pendingArrivalQty || line.qty
  return qty != null && qty !== '' ? `${head}×${qty}` : head
}

function inboundRows(items: unknown): ErpDesignRow[] {
  return list(items).map((item) => {
    const lines = list(item.lines)
    const targets = Array.isArray(item.inboundTargets)
      ? item.inboundTargets.map((value: unknown) => String(value || '').trim()).filter(Boolean)
      : []
    return {
      ...item,
      stationLabel: item.stationLabel || item.station || item.actionLabel,
      partDetails: item.partDetails || lines.map(inboundPartLabel).join('；'),
      inboundTargetText: targets.join('、'),
    }
  })
}

function warehouseOrderRows(data: Record<string, any>): ErpDesignRow[] {
  if (Array.isArray(data.orders)) return data.orders
  const grouped = new Map<string, ErpDesignRow[]>()
  for (const item of list(data.items)) {
    const key = [item.orderNo || '', item.sourceType || '', item.action || ''].join('|')
    const bucket = grouped.get(key) ?? []
    bucket.push(item)
    grouped.set(key, bucket)
  }
  return [...grouped.values()].map((lines) => {
    const first = lines[0]
    return {
      ...first,
      moldFamily: joinUnique(lines.map((line) => line.moldFamily || line.moldNo)),
      moldBatch: joinUnique(lines.map((line) => line.moldBatch)),
      partDetails: lines.map(supplyLineLabel).join('；'),
      lineCount: lines.length,
      qty: lines.reduce((sum, line) => sum + Number(line.qty || 0), 0),
      processName: joinUnique(lines.map((line) => line.processName)),
    }
  })
}

export function erpOutsourceResultTablesFromRun(run: any, quotePatches: BuyerQuotePatch[] = []): ErpDesignResultTable[] {
  const result: ErpDesignResultTable[] = []
  const patches = quotePatches.length ? quotePatches : erpOutsourceQuotePatchesFromRuns([run])
  for (const item of toolItems(run)) {
    const tool = String(item.tool ?? '')
    const data = payload(item.data)
    if (String(data.status || '') === 'NEED_MOLD_CODE') continue
    if (BOARD_TOOLS.has(tool)) {
      const processor = tool === 'query_erp_outsource_processor_board'
      const title = processor ? '我的委外待办' : 'ERP 委外待办'
      const rows = processor ? list(data.items) : applyQuotePatches(list(data.items), patches)
      if (!rows.length) continue
      const scope = String(data.scope || '')
      const next = table(
        `${tool}:board`,
        title,
        rows,
        processor ? processorBoardColumns : boardColumns,
        `${scope || title} 共 ${rows.length} 条`,
        scope,
        processor
          ? '工序委外显示核算价，没有采购报价。零件/模具显示采购报价，不显示核算价、接单上限和成交价'
          : '数据来自 ERP 委外待办，仅供展示',
      )
      const existing = result.findIndex((item) => item.title === title)
      if (existing >= 0) result[existing] = next
      else result.push(next)
      continue
    }
    if (WAREHOUSE_TOOLS.has(tool)) {
      const rows = warehouseOrderRows(data)
      const inbound = inboundRows(data.inboundItems)
      if (rows.length) {
        const scope = String(data.scope || '仓库委外待办')
        const lineCount = rows.reduce((sum, row) => sum + Number(row.lineCount || 1), 0)
        result.push(table(
          `${tool}:tasks`,
          '仓库委外待发货',
          rows,
          warehouseColumns,
          `${scope} 共 ${rows.length} 单，${lineCount} 个零件明细`,
          scope,
          '一行是一张待发货订单。零件明细是这张订单里要发的零件，不是多张待办。采购直发不在本表。',
        ))
      }
      if (inbound.length) {
        result.push(table(
          `${tool}:inbound`,
          '仓库回厂收货入库',
          inbound,
          inboundColumns,
          `回厂待办共 ${inbound.length} 条`,
          '仓库回厂待办',
          '加工商成品发货后的到货确认和仓储入库。与待发料/待备料不是同一张表。',
        ))
      }
      continue
    }
    if (WAREHOUSE_INBOUND_TOOLS.has(tool)) {
      const rows = inboundRows(data.items)
      if (!rows.length) continue
      result.push(table(
        `${tool}:inbound`,
        '仓库回厂收货入库',
        rows,
        inboundColumns,
        String(data.summary || `回厂待办共 ${rows.length} 条`),
        String(data.scope || '仓库回厂待办'),
        '加工商成品发货后的到货确认和仓储入库。',
      ))
      continue
    }
    if (QUALITY_TOOLS.has(tool)) {
      const rows = qualityRows(data.items)
      if (!rows.length) continue
      result.push(table(
        `${tool}:quality`,
        '委外质检待办',
        rows,
        qualityColumns,
        String(data.summary || `质检待办共 ${rows.length} 条`),
        String(data.scope || '委外质检待办'),
        '加工商成品回厂入库后的质检领取与合格。厂内工序质检不在本表。',
      ))
      continue
    }
    if (PRODUCT_SHIP_TOOLS.has(tool)) {
      const rows = productShipRows(data)
      if (!rows.length) continue
      const lineCount = rows.reduce((sum, row) => sum + Number(row.lineCount || 1), 0)
      result.push(table(
        `${tool}:ship`,
        '可成品发货',
        rows,
        productShipColumns,
        `共 ${rows.length} 单，${lineCount} 个零件可发`,
        '可成品发货',
        '一行是一张可发货订单，不是待办任务。零件明细里的可发数量不超过已收原料。',
      ))
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
