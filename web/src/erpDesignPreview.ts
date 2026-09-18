export type ErpDesignRow = Record<string, any>

// Keep these choices in step with the ERP upload page. Heat treatment remains
// editable in the browser (the ERP page allows a newly typed value), whereas
// post-treatment is a constrained pricing choice.
export const ERP_DESIGN_HEAT_TREATMENT_OPTIONS = ['普通', '真空', '深冷'] as const
export const ERP_DESIGN_AGING_TREATMENT_OPTIONS = ['是', '否'] as const

export function erpDesignAgingTreatmentSupported(row: ErpDesignRow): boolean {
  const materialMark = String(row?.material_mark ?? row?.materialMark ?? '').trim().toUpperCase()
  const explicitShape = row?.material_shape ?? row?.materialShape ?? row?.material_type ?? row?.materialType
  const inferredShape = explicitShape ?? ((row?.length != null && row?.width != null) ? '方料' : '')
  const materialShape = String(inferredShape ?? '')
    .trim().toLowerCase()
  const businessSupported = materialMark === '45#' && ['方', '方料', 'square', 'plate'].includes(materialShape)
  const backendSupported = row?._steel_aging_supported ?? row?._steelAgingSupported
  return businessSupported && backendSupported !== false
}

export function erpDesignAgingTreatmentDisabled(row: ErpDesignRow): boolean {
  return Boolean(row?._steel_price_missing ?? row?._steelPriceMissing) || !erpDesignAgingTreatmentSupported(row)
}

export function normalizeErpDesignTreatments(row: ErpDesignRow): ErpDesignRow {
  const normalized = { ...row }
  const heat = String(normalized.heat_treatment ?? normalized.heatTreatment ?? '').trim()
  normalized.heat_treatment = heat
  normalized.heatTreatment = heat
  const selected = String(normalized.post_treatment ?? normalized.postTreatment ?? '').trim()
  const aging = erpDesignAgingTreatmentSupported(normalized) && ERP_DESIGN_AGING_TREATMENT_OPTIONS.includes(selected as '是' | '否')
    ? selected
    : '否'
  normalized.post_treatment = aging
  normalized.postTreatment = aging
  return normalized
}

export type ErpDesignPreviewSession = {
  sessionId: number
  sheetType: string
  moldCode: string
  fileName: string
  rowCount: number
  previewRows: ErpDesignRow[]
  totalQuantity: number
  drawingProcessing: boolean
  drawingProcessingStatus: string
  drawingProcessingMessage: string
  warnings: string[]
  errors: string[]
  expectedDate: string
  remark: string
  designOrderType: string
  designerName: string
  submitDate: string
  requestNo: string
  canImport: boolean
  additionalProcessingFeeRules: Record<string, any>[]
  techRequirements: Record<string, any> | null
}

export type ErpDesignImportReceipt = {
  sessionId: number
  requestNo: string
  message: string
  importedAt: string
}

export type ErpDesignColumn = {
  key: string
  label: string
  fields: string[]
  width?: number
  decimals?: number
  kind?: 'paint' | 'hardware-type' | 'preview'
}

const ERP_DESIGN_UPLOAD_PAGE = 'http://127.0.0.1:18080/design/upload/index'
const ACTIVE_RUN_STATUSES = new Set(['QUEUED', 'RUNNING'])
const TERMINAL_RUN_STATUSES = new Set(['SUCCEEDED', 'FAILED', 'CANCELLED'])
const DESIGN_UPLOAD_TOOLS = new Set([
  'erp_design_parse_new_mold_upload',
  'erp_design_get_drawing_status',
  'erp_design_get_upload_result',
  'erp_design_validate_rows',
  'erp_design_evaluate_tolerances',
])

const commonColumns: ErpDesignColumn[] = [
  { key: 'code', label: '编码', fields: ['item_code_full', 'itemCodeFull'], width: 190 },
  { key: 'name', label: '名称', fields: ['item_name', 'itemName'], width: 145 },
  { key: 'spec', label: '规格', fields: ['spec_raw', 'specRaw'], width: 180 },
  { key: 'brand', label: '品牌', fields: ['brand'], width: 100 },
  { key: 'outer', label: '外直径(∅)', fields: ['outer_diameter', 'outerDiameter'], width: 112, decimals: 4 },
  { key: 'inner', label: '内直径(∅)', fields: ['inner_diameter', 'innerDiameter'], width: 112, decimals: 4 },
  { key: 'length', label: '长(L)', fields: ['length'], width: 105, decimals: 4 },
  { key: 'width', label: '宽(W)', fields: ['width'], width: 105, decimals: 4 },
  { key: 'height', label: '厚(T)', fields: ['height', 'thickness'], width: 105, decimals: 4 },
]

const hardwareColumns: ErpDesignColumn[] = [
  ...commonColumns,
  { key: 'type', label: '类型', fields: ['hardware_type', 'hardwareType', 'drawing_part_category', 'drawingPartCategory', 'material_type', 'materialType'], width: 130, kind: 'hardware-type' },
  { key: 'paint', label: '喷漆要求', fields: ['paint_required', 'paintRequired'], width: 100, kind: 'paint' },
  { key: 'qty', label: '请购数量', fields: ['qty', 'quantity'], width: 105 },
  { key: 'idleQty', label: '闲置匹配', fields: ['idle_quantity', 'idleQuantity'], width: 105 },
  { key: 'purchaseQty', label: '采购数量', fields: ['purchase_quantity', 'purchaseQuantity', 'qty'], width: 105 },
  { key: 'preview', label: '预览', fields: ['drawing_status_label', 'drawingStatusLabel', 'drawing_flag', 'drawingFlag'], width: 90, kind: 'preview' },
  { key: 'material', label: '材质', fields: ['attached_order_material_mark', 'attachedOrderMaterialMark', 'material_mark', 'materialMark'], width: 110 },
  { key: 'approvedPrice', label: '有效已审批价', fields: ['approved_unit_price', 'approvedUnitPrice', 'unit_price', 'unitPrice'], width: 125, decimals: 4 },
  { key: 'accountingPrice', label: '核算单价', fields: ['accounting_unit_price', 'accountingUnitPrice'], width: 110, decimals: 4 },
  { key: 'accountingAmount', label: '核算金额', fields: ['accounting_amount', 'accountingAmount', 'material_amount', 'materialAmount'], width: 115, decimals: 2 },
  { key: 'totalPrice', label: '总价', fields: ['total_price', 'totalPrice', 'total_amount', 'totalAmount'], width: 105, decimals: 2 },
  { key: 'calculation', label: '计算过程', fields: ['calculation_process', 'calculationProcess', 'accounting_process', 'accountingProcess'], width: 360 },
  { key: 'remark', label: '备注', fields: ['remark'], width: 170 },
]

const steelColumns: ErpDesignColumn[] = [
  commonColumns[0],
  commonColumns[1],
  { key: 'material', label: '材质', fields: ['material_mark', 'materialMark'], width: 105 },
  ...commonColumns.slice(2),
  { key: 'shape', label: '材料类型', fields: ['material_shape', 'materialShape', 'material_type', 'materialType'], width: 115 },
  { key: 'qty', label: '请购数量', fields: ['qty', 'quantity'], width: 105 },
  { key: 'idleQty', label: '闲置匹配', fields: ['idle_quantity', 'idleQuantity'], width: 105 },
  { key: 'purchaseQty', label: '采购数量', fields: ['purchase_quantity', 'purchaseQuantity', 'qty'], width: 105 },
  { key: 'milling', label: '铣面', fields: ['milling_surface', 'millingSurface'], width: 80 },
  { key: 'grinding', label: '研磨', fields: ['grinding'], width: 80 },
  { key: 'chamfer', label: '倒角', fields: ['chamfer_c', 'chamferC'], width: 80 },
  { key: 'heat', label: '热处理', fields: ['heat_treatment', 'heatTreatment'], width: 105 },
  { key: 'aging', label: '时效处理', fields: ['post_treatment', 'postTreatment'], width: 105 },
  { key: 'hardness', label: 'HRC', fields: ['hardness'], width: 90 },
  { key: 'density', label: '密度', fields: ['density'], width: 105, decimals: 4 },
  { key: 'weight', label: '单件毛重(KG)', fields: ['unit_weight', 'unitWeight'], width: 125, decimals: 4 },
  { key: 'unitPrice', label: '核算单价(元/KG)', fields: ['unit_price', 'unitPrice', 'material_unit_price', 'materialUnitPrice'], width: 145, decimals: 4 },
  { key: 'materialAmount', label: '核算金额', fields: ['material_amount', 'materialAmount'], width: 115, decimals: 2 },
  { key: 'totalPrice', label: '总价', fields: ['total_price', 'totalPrice'], width: 105, decimals: 2 },
  { key: 'calculation', label: '计算过程', fields: ['calculation_process', 'calculationProcess'], width: 420 },
  { key: 'technology', label: '加工工艺', fields: ['processing_technology', 'processingTechnology'], width: 140 },
  { key: 'toleranceTier', label: '公差档位', fields: ['tolerance_tier', 'toleranceTier'], width: 125 },
  { key: 'lengthRange', label: '长度允许范围', fields: ['length_allowed_range', 'lengthAllowedRange'], width: 145 },
  { key: 'widthRange', label: '宽度允许范围', fields: ['width_allowed_range', 'widthAllowedRange'], width: 145 },
  { key: 'thicknessRange', label: '厚度允许范围', fields: ['thickness_allowed_range', 'thicknessAllowedRange'], width: 145 },
  { key: 'diagonalTolerance', label: '对角公差', fields: ['diagonal_tolerance', 'diagonalTolerance'], width: 115 },
  { key: 'remark', label: '备注', fields: ['remark'], width: 150 },
]

function record(value: unknown): Record<string, any> | null {
  return value && typeof value === 'object' && !Array.isArray(value)
    ? value as Record<string, any>
    : null
}

function payload(value: unknown): Record<string, any> | null {
  const source = record(value)
  const nested = record(source?.data)
  return nested && (nested.sessionId != null || nested.session_id != null) ? nested : source
}

function rows(source: Record<string, any> | null): ErpDesignRow[] {
  if (Array.isArray(source?.previewRows)) return source.previewRows
  if (Array.isArray(source?.preview_rows)) return source.preview_rows
  return []
}

function messages(value: unknown): string[] {
  return Array.isArray(value) ? value.map(item => String(item)).filter(Boolean) : []
}

export function normalizeErpDesignImportReceipt(value: unknown): ErpDesignImportReceipt | null {
  const source = record(value)
  const sessionId = Number(source?.sessionId ?? source?.session_id ?? 0)
  if (!Number.isInteger(sessionId) || sessionId < 1) return null
  return {
    sessionId,
    requestNo: String(source?.requestNo ?? source?.request_no ?? '').trim(),
    message: String(source?.message ?? '').trim(),
    importedAt: String(source?.importedAt ?? source?.imported_at ?? ''),
  }
}

export function normalizeErpDesignPreview(
  value: unknown,
  fallback?: ErpDesignPreviewSession | null,
): ErpDesignPreviewSession | null {
  const source = payload(value)
  const sessionId = Number(source?.sessionId ?? source?.session_id ?? fallback?.sessionId ?? 0)
  if (!Number.isInteger(sessionId) || sessionId < 1) return null
  const previewRows = rows(source)
  const resolvedRows = previewRows.length || !fallback ? previewRows : fallback.previewRows
  return {
    sessionId,
    sheetType: String(source?.sheetType ?? source?.sheet_type ?? fallback?.sheetType ?? 'steel'),
    moldCode: String(source?.moldCode ?? source?.mold_code ?? fallback?.moldCode ?? ''),
    fileName: String(source?.fileName ?? source?.file_name ?? fallback?.fileName ?? ''),
    rowCount: resolvedRows.length,
    previewRows: resolvedRows,
    totalQuantity: Number(source?.totalQuantity ?? source?.total_quantity ?? fallback?.totalQuantity ?? 0) || 0,
    drawingProcessing: Boolean(source?.drawingProcessing ?? source?.drawing_processing ?? false),
    drawingProcessingStatus: String(source?.drawingProcessingStatus ?? source?.drawing_processing_status ?? fallback?.drawingProcessingStatus ?? ''),
    drawingProcessingMessage: String(source?.drawingProcessingMessage ?? source?.drawing_processing_message ?? fallback?.drawingProcessingMessage ?? ''),
    warnings: messages(source?.warnings).length ? messages(source?.warnings) : (fallback?.warnings ?? []),
    errors: messages(source?.errors).length ? messages(source?.errors) : (fallback?.errors ?? []),
    expectedDate: String(source?.expectedDate ?? source?.expected_date ?? fallback?.expectedDate ?? ''),
    remark: String(source?.remark ?? fallback?.remark ?? ''),
    designOrderType: String(source?.designOrderType ?? source?.design_order_type ?? fallback?.designOrderType ?? 'new_model'),
    designerName: String(source?.designerName ?? source?.designer_name ?? source?.designerAccount ?? source?.designer_account ?? fallback?.designerName ?? ''),
    submitDate: String(source?.submitDate ?? source?.submit_date ?? fallback?.submitDate ?? ''),
    requestNo: String(source?.requestNo ?? source?.request_no ?? fallback?.requestNo ?? ''),
    canImport: Boolean(source?.canImport ?? source?.can_import ?? fallback?.canImport ?? false),
    additionalProcessingFeeRules: Array.isArray(source?.additionalProcessingFeeRules)
      ? source.additionalProcessingFeeRules
      : (Array.isArray(source?.additional_processing_fee_rules)
        ? source.additional_processing_fee_rules
        : (fallback?.additionalProcessingFeeRules ?? [])),
    techRequirements: record(source?.techRequirements ?? source?.tech_requirements) ?? fallback?.techRequirements ?? null,
  }
}

export function erpDesignSessionFromTool(item: any): ErpDesignPreviewSession | null {
  if (!DESIGN_UPLOAD_TOOLS.has(String(item?.tool || ''))) return null
  return normalizeErpDesignPreview(item?.data)
}

export function erpDesignSessionFromRun(run: any): ErpDesignPreviewSession | null {
  const trace = Array.isArray(run?.trace) ? run.trace : []
  for (let index = trace.length - 1; index >= 0; index -= 1) {
    const session = erpDesignSessionFromTool(trace[index])
    if (session) return session
  }
  return null
}

export function shouldOpenErpDesignPreview(previousRun: any, currentRun: any): boolean {
  return Boolean(
    previousRun
    && ACTIVE_RUN_STATUSES.has(String(previousRun.status || ''))
    && TERMINAL_RUN_STATUSES.has(String(currentRun?.status || ''))
    && erpDesignSessionFromRun(currentRun),
  )
}

export function erpDesignPreviewUrl(session: ErpDesignPreviewSession): string {
  const url = new URL(ERP_DESIGN_UPLOAD_PAGE)
  url.searchParams.set('sessionId', String(session.sessionId))
  url.searchParams.set('sheetType', session.sheetType || 'steel')
  url.searchParams.set('embedded', '1')
  return url.toString()
}

export function erpDesignSheetLabel(sheetType: string): string {
  return sheetType === 'hardware' ? '五金清单' : '钢料清单'
}

export function erpDesignColumns(sheetType: string): ErpDesignColumn[] {
  return sheetType === 'hardware' ? hardwareColumns : steelColumns
}

function firstPresent(row: ErpDesignRow, fields: string[]): unknown {
  for (const field of fields) {
    const value = row?.[field]
    if (value !== null && value !== undefined && value !== '') return value
  }
  return null
}

export type ErpDesignSteelTolerance = {
  tier: string
  lengthRange: string
  widthRange: string
  thicknessRange: string
  diagonalTolerance: string
}

function optionalNumber(value: unknown): number | null {
  if (value == null || value === '') return null
  const number = Number(value)
  return Number.isFinite(number) ? number : null
}

function toleranceOffsets(value: unknown): [number, number] | null {
  const values = String(value ?? '').match(/[+-]?\d+(?:\.\d+)?/g)
  if (!values || values.length < 2) return null
  const offsets = values.slice(0, 2).map(Number)
  return offsets.every(Number.isFinite) ? offsets as [number, number] : null
}

function toleranceDimension(value: number): string {
  return value.toFixed(4).replace(/\.?0+$/, '')
}

function allowedRange(nominalValue: unknown, toleranceValue: unknown): string {
  const nominal = optionalNumber(nominalValue)
  const offsets = toleranceOffsets(toleranceValue)
  if (nominal == null || nominal <= 0 || !offsets) return '-'
  return `${toleranceDimension(nominal + offsets[0])}～${toleranceDimension(nominal + offsets[1])}`
}

function steelMaterialShape(row: ErpDesignRow): string {
  const value = row?.material_shape ?? row?.materialShape ?? row?.material_type ?? row?.materialType ?? ''
  if (value === '圆环') return '圆环料'
  if (value) return String(value)
  return row?.length != null && row?.width != null ? '方料' : ''
}

export function calculateErpDesignSteelTolerance(
  row: ErpDesignRow,
  techRequirements: Record<string, any> | null | undefined,
): ErpDesignSteelTolerance {
  const empty = { tier: '-', lengthRange: '-', widthRange: '-', thicknessRange: '-', diagonalTolerance: '-' }
  if (steelMaterialShape(row) !== '方料') return empty
  const length = optionalNumber(row?.length)
  const width = optionalNumber(row?.width)
  if (length == null || width == null || length <= 0 || width <= 0) return empty
  const rawRules = techRequirements?.tolerance_table ?? techRequirements?.toleranceTable
  const rules = Array.isArray(rawRules) ? rawRules.filter(rule => record(rule)) : []
  const specification = Math.max(length, width)
  const sequence = specification <= 500 ? 1 : specification <= 800 ? 2 : 3
  const rule = rules.find(item => Number(item?.seq) === sequence) ?? rules[sequence - 1]
  if (!rule) return empty
  const lengthTolerance = rule.length_tol ?? rule.lengthTol
  return {
    tier: String(rule.spec || '-'),
    lengthRange: allowedRange(length, lengthTolerance),
    widthRange: allowedRange(width, lengthTolerance),
    thicknessRange: allowedRange(row?.height ?? row?.thickness, rule.thick_tol ?? rule.thickTol),
    diagonalTolerance: String(rule.diag_tol ?? rule.diagTol ?? '-'),
  }
}

const toleranceColumnFields: Record<string, keyof ErpDesignSteelTolerance> = {
  toleranceTier: 'tier',
  lengthRange: 'lengthRange',
  widthRange: 'widthRange',
  thicknessRange: 'thicknessRange',
  diagonalTolerance: 'diagonalTolerance',
}

export function erpDesignCell(
  row: ErpDesignRow,
  column: ErpDesignColumn,
  techRequirements?: Record<string, any> | null,
): string {
  let value = firstPresent(row, column.fields)
  if (value === null && toleranceColumnFields[column.key]) {
    value = calculateErpDesignSteelTolerance(row, techRequirements)[toleranceColumnFields[column.key]]
  }
  if (column.kind === 'hardware-type') {
    value = firstPresent(row, [
      'attached_order_material_shape', 'attachedOrderMaterialShape', 'material_shape', 'materialShape',
      ...column.fields,
    ])
  }
  if (column.kind === 'paint') {
    return value === true || value === 1 || String(value ?? '').toLowerCase() === 'true' ? '喷漆' : '-'
  }
  if (value === null) return '-'
  if (typeof value === 'number') {
    if (column.decimals != null) return value.toFixed(column.decimals)
    return Number.isInteger(value) ? String(value) : String(Number(value.toFixed(4)))
  }
  return String(value) || '-'
}
