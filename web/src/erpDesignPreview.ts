export type ErpDesignRow = Record<string, any>

export type ErpDesignDueDateCommand = {
  kind: 'set' | 'date_only' | 'invalid' | 'query' | 'none'
  date?: string
  display?: string
  message?: string
}

const CHINESE_DIGITS: Record<string, number> = {
  '零': 0, '一': 1, '二': 2, '两': 2, '三': 3, '四': 4,
  '五': 5, '六': 6, '七': 7, '八': 8, '九': 9, '十': 10,
}

function chineseInteger(value: string): number | null {
  const text = String(value || '').trim()
  if (/^\d+$/.test(text)) return Number(text)
  if (!text || [...text].some(char => CHINESE_DIGITS[char] == null)) return null
  if (text === '十') return 10
  if (text.startsWith('十')) return 10 + (CHINESE_DIGITS[text.slice(1)] ?? 0)
  if (text.includes('十')) {
    const [tens, ones] = text.split('十')
    return (CHINESE_DIGITS[tens] ?? 0) * 10 + (ones ? CHINESE_DIGITS[ones] ?? 0 : 0)
  }
  return CHINESE_DIGITS[text] ?? null
}

function localDateString(value: Date): string {
  return `${value.getFullYear()}-${String(value.getMonth() + 1).padStart(2, '0')}-${String(value.getDate()).padStart(2, '0')}`
}

function parseAbsoluteDate(value: string, today: Date): string | null {
  const text = String(value || '').trim()
  const full = text.match(/^(\d{4})[-\/.年](\d{1,2})[-\/.月](\d{1,2})(?:日|号)?$/)
  const short = text.match(/^(\d{1,2})月(\d{1,2})(?:日|号)?$/)
  const year = full ? Number(full[1]) : today.getFullYear()
  const month = full ? Number(full[2]) : short ? Number(short[1]) : NaN
  const day = full ? Number(full[3]) : short ? Number(short[2]) : NaN
  if (!Number.isInteger(year) || !Number.isInteger(month) || !Number.isInteger(day)) return null
  const candidate = new Date(year, month - 1, day)
  if (candidate.getFullYear() !== year || candidate.getMonth() !== month - 1 || candidate.getDate() !== day) return null
  return localDateString(candidate)
}

function extractDueDate(value: string, today: Date): string | null {
  const text = String(value || '').trim()
  const relativeDays: Array<[RegExp, number]> = [
    [/大后天/, 3], [/后天/, 2], [/明天/, 1], [/今天/, 0],
  ]
  for (const [pattern, days] of relativeDays) {
    if (pattern.test(text)) {
      const candidate = new Date(today)
      candidate.setDate(candidate.getDate() + days)
      return localDateString(candidate)
    }
  }
  const relative = text.match(/(\d+|[零一二两三四五六七八九十]+)\s*(?:天|日)\s*(?:后|之后|以后)/)
  if (relative) {
    const days = chineseInteger(relative[1])
    if (days == null || days < 0) return null
    const candidate = new Date(today)
    candidate.setDate(candidate.getDate() + days)
    return localDateString(candidate)
  }
  const weekday = text.match(/(本|这|下|上)(?:个)?(?:周|星期|礼拜)([一二三四五六日天1-7])/)
  if (weekday) {
    const target = ({一: 1, 二: 2, 三: 3, 四: 4, 五: 5, 六: 6, 日: 7, 天: 7, '1': 1, '2': 2, '3': 3, '4': 4, '5': 5, '6': 6, '7': 7} as Record<string, number>)[weekday[2]]
    if (!target) return null
    const current = today.getDay() === 0 ? 7 : today.getDay()
    const weekOffset = weekday[1] === '下' ? 1 : weekday[1] === '上' ? -1 : 0
    const candidate = new Date(today)
    candidate.setDate(candidate.getDate() + weekOffset * 7 + target - current)
    return localDateString(candidate)
  }
  const absolute = text.match(/\d{4}[-\/.年]\d{1,2}[-\/.月]\d{1,2}(?:日|号)?|\d{1,2}月\d{1,2}(?:日|号)?/)
  return absolute ? parseAbsoluteDate(absolute[0], today) : null
}

function dueDateDisplay(value: string): string {
  return String(value || '').match(/大后天|后天|明天|今天|(?:\d+|[零一二两三四五六七八九十]+)\s*(?:天|日)\s*(?:后|之后|以后)|(?:本|这|下|上)(?:个)?(?:周|星期|礼拜)[一二三四五六日天1-7]|\d{4}[-\/.年]\d{1,2}[-\/.月]\d{1,2}(?:日|号)?|\d{1,2}月\d{1,2}(?:日|号)?/)?.[0] || String(value || '').trim()
}

/** Parse only explicit due-date changes; a bare date is deliberately not a change. */
export function parseErpDesignDueDateCommand(input: string, now = new Date()): ErpDesignDueDateCommand {
  const text = String(input || '').trim()
  if (!text) return { kind: 'none' }
  const dueDateQuery = /(?:什么|哪天|何时|什么时候|当前|现在).{0,12}(?:交期|交货期|交付日期)|(?:交期|交货期|交付日期).{0,12}(?:是什么|哪天|何时|什么时候)/.test(text)
  if (dueDateQuery) return { kind: 'query' }
  const dateOnly = /^(?:\d{4}[-\/.年]\d{1,2}[-\/.月]\d{1,2}(?:日|号)?|\d{1,2}月\d{1,2}(?:日|号)?|(本|这|下|上)(?:个)?(?:周|星期|礼拜)([一二三四五六日天1-7]))$/.test(text)
  const date = extractDueDate(text, now)
  if (dateOnly) {
    if (!date) return { kind: 'invalid', message: '日期格式或日期本身无效。' }
    return { kind: 'date_only', date, display: text }
  }
  const mentionsDueDate = /交期|交货期|交付日期/.test(text)
  const asksToChange = /改|调整|变更|设为|设置|定为|为/.test(text)
  const explicit = mentionsDueDate && asksToChange
  if (!explicit) return { kind: 'none' }
  if (!date) return { kind: 'invalid', message: '请提供有效的完整日期，或使用“几天后”的表达。' }
  const today = localDateString(now)
  if (date < today) return { kind: 'invalid', date, message: `交期不能早于今天（${today}）。` }
  return { kind: 'set', date, display: dueDateDisplay(text) }
}

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
  purchaseReason: string
  designOrderTypeOptions: Array<{ value: string; label: string }>
  purchaseReasonOptions: Array<{ value: string; label: string }>
  designerName: string
  submitDate: string
  requestNo: string
  canImport: boolean
  additionalProcessingFeeRules: Record<string, any>[]
  techRequirements: Record<string, any> | null
  toleranceEvaluation: Record<string, any> | null
}

export type ErpDesignDraft = Pick<ErpDesignPreviewSession, 'expectedDate' | 'remark' | 'designOrderType' | 'purchaseReason'>

/**
 * Apply conversational form edits to the latest ERP session snapshot.
 *
 * ERP status responses remain the source of the parsed rows and other
 * immutable session data, while the four editable form fields may be newer in
 * the local conversation draft than in that response.
 */
export function mergeErpDesignDraft(
  session: ErpDesignPreviewSession,
  draft?: Partial<ErpDesignDraft> | null,
): ErpDesignPreviewSession {
  return draft ? { ...session, ...draft } : session
}

export type ErpDesignFormCommand = {
  kind: 'update' | 'invalid' | 'query' | 'none'
  fields?: {
    designOrderType?: string
    purchaseReason?: string
    expectedDate?: string
    remark?: string
  }
  message?: string
}

/** Messages describing automatic drawing corrections are informational. */
export function erpDesignBlockingErrors(errors: readonly unknown[]): string[] {
  return (Array.isArray(errors) ? errors : []).map(value => String(value ?? '').trim()).filter(Boolean).filter(message => (
    !/(?:自动修正|图纸识别结果自动修正|双击.*(?:修正|回填)|按图纸.*(?:修正|回填)|自动回填)/.test(message)
  ))
}

type ErpDesignOption = { value: string; label: string }

const DEFAULT_DESIGN_ORDER_TYPES: ErpDesignOption[] = [
  { value: 'new_model', label: '新模' },
  { value: 'repair_other', label: '改模' },
]

const PURCHASE_REASON_ALIASES: Record<string, string[]> = {
  customer_change: ['客户设变', '客户变更', '客户更改', '客户要求'],
  design_abnormal: ['设计异常', '设计问题'],
  machining_abnormal: ['加工异常', '加工问题'],
  assembly_abnormal: ['装配异常', '装配问题', '组立异常', '组立问题'],
  trial_mold_abnormal: ['试模异常', '试模问题'],
  outsource_abnormal: ['外协异常', '外协问题'],
  process_improvement: ['工艺改善', '工艺改进', '工艺优化', '制程改善', '制程改进'],
  other_abnormal: ['其他异常', '其它异常', '其他', '其它'],
}

function compactChinese(value: unknown): string {
  return String(value ?? '').trim().toLowerCase().replace(/[\s_-]+/g, '')
}

function optionMatches(option: ErpDesignOption, value: string): boolean {
  const needle = compactChinese(value)
  return Boolean(needle) && [option.value, option.label].some(candidate => compactChinese(candidate) === needle)
}

function resolveDesignOrderType(value: string, options: ReadonlyArray<ErpDesignOption>): string | null {
  const text = compactChinese(value)
  const aliases: Array<[string, string[]]> = [
    ['new_model', ['新模', '新模型', '新模具', 'newmodel']],
    ['repair_other', ['改模', '修模', '改模型', 'repairother']],
  ]
  const canonical = aliases.find(([, names]) => names.some(name => text === compactChinese(name)))?.[0]
  const candidates = options.length ? options : DEFAULT_DESIGN_ORDER_TYPES
  const matched = candidates.find(option => optionMatches(option, value) || (canonical != null && optionMatches(option, canonical)))
  return matched?.value ?? null
}

function resolvePurchaseReason(value: string, options: ReadonlyArray<ErpDesignOption>): string | null {
  const text = compactChinese(value)
  const canonical = Object.entries(PURCHASE_REASON_ALIASES)
    .find(([, names]) => names.some(name => text.includes(compactChinese(name))))?.[0]
  const candidates = options.length
    ? options
    : Object.keys(PURCHASE_REASON_ALIASES).map(key => ({ value: key, label: key }))
  const matched = candidates.find(option => optionMatches(option, value) || (canonical != null && optionMatches(option, canonical)))
  return matched?.value ?? null
}

/**
 * Parse conversational edits for the fields in the ERP design upload form.
 * This only returns a draft update; importing still requires the explicit UI
 * confirmation button.
 */
export function parseErpDesignFormCommand(
  input: string,
  session: {
    designOrderTypeOptions: ReadonlyArray<ErpDesignOption>
    purchaseReasonOptions: ReadonlyArray<ErpDesignOption>
    designOrderType: string
  },
): ErpDesignFormCommand {
  const text = String(input || '').trim()
  if (!text) return { kind: 'none' }
  // Questions such as “这个是什么类型的” ask about the current session;
  // they are not an attempt to set the type to the word following “是”.
  const typeQuery = /(?:什么|哪种|哪一个|当前|现在|这份|这个).{0,8}(?:类型|订单类型)|(?:类型|订单类型).{0,8}(?:是什么|是哪种|什么)/.test(text)
  const hasTypeChange = /(?:改为|改成|设为|设置为|选择|选用|换成)/.test(text)
  if (typeQuery && !hasTypeChange) return { kind: 'query' }
  const fields: ErpDesignFormCommand['fields'] = {}
  const typeMention = /(?:ERP\s*)?(?:设计订单)?类型|订单类型/.test(text)
  const typeChange = /(?:改为|改成|设为|设置为|选择|选|是|为)\s*([^，。；;\s]+)/.exec(text)
  const standaloneType = /(新模(?:型|具)?|改模(?:型)?|修模)/.exec(text)
  if (typeMention || (standaloneType && /改|选|择|设|换|用/.test(text))) {
    const raw = typeChange?.[1] || standaloneType?.[1] || ''
    const type = resolveDesignOrderType(raw, session.designOrderTypeOptions || [])
    if (!type) return { kind: 'invalid', message: `未找到匹配的 ERP 类型“${raw || text}”，可选项请以表单中的类型为准。` }
    fields.designOrderType = type
    if (type === 'new_model') fields.purchaseReason = ''
  }

  const reasonMention = /请购原因|采购原因|原因/.test(text)
  const reasonChange = /(?:请购原因|采购原因|原因)\s*(?:改为|改成|设为|设置为|选择|选|是|为)?\s*([^，。；;]+)/.exec(text)
  const reasonAliases = Object.values(PURCHASE_REASON_ALIASES).flat().sort((a, b) => b.length - a.length)
  const standaloneReason = reasonAliases.find(alias => text.includes(alias))
  if (reasonMention || standaloneReason) {
    const raw = (reasonChange?.[1] || standaloneReason || '').trim()
    const reason = resolvePurchaseReason(raw, session.purchaseReasonOptions || [])
    if (!reason) return { kind: 'invalid', message: `未找到匹配的 ERP 请购原因“${raw || text}”，请按当前 ERP 选项输入。` }
    const requestedType = fields.designOrderType || session.designOrderType
    if (requestedType !== 'repair_other') {
      return { kind: 'invalid', message: 'ERP 请购原因只适用于“改模/修模”，请先把类型改为改模。' }
    }
    fields.purchaseReason = reason
  }

  const remarkMention = /备注|说明/.test(text)
  const clearRemark = /(?:备注|说明)\s*(?:清空|删除|取消|去掉)/.test(text)
  const remarkChange = /(?:备注|说明)\s*(?:改为|改成|设为|设置为|写|填写|是|为|：|:)\s*(.*)$/s.exec(text)
  if (remarkMention && (clearRemark || remarkChange)) {
    fields.remark = clearRemark ? '' : String(remarkChange?.[1] || '').trim()
  }

  return Object.keys(fields).length ? { kind: 'update', fields } : { kind: 'none' }
}

export type ErpDesignImportReceipt = {
  sessionId: number
  requestNo: string
  message: string
  status?: string
  subjectId?: string
  instanceId?: string
  importedAt: string
  requestId?: string
  processCode?: string
  processName?: string
  currentNodeName?: string
  currentApproverNames?: string[]
  approvalSteps?: Array<{nodeName: string; approverNames: string[]}>
  approvalStatus?: string
  workflowInstanceId?: string
}

export type ErpDesignParameterResult = {
  sessionId: number
  sheetType: string
  moldCode: string
  fileName: string
  rowCount: number
  matchedCount: number
  tableThreshold: number
  columns?: ErpDesignColumn[]
  selectedFields?: string[]
  renderAsTable: boolean
  previewRows: ErpDesignRow[]
}

export type ErpDesignDrawingResult = {
  source: 'upload' | 'standard_hardware'
  sessionId?: number
  moldCode: string
  totalCount: number
  previewRows: ErpDesignRow[]
}

export type ErpDesignTechnicalRequirementRow = {
  seq: number
  spec: string
  lengthTolerance: string
  thicknessTolerance: string
  diagonalTolerance: string
}

export type ErpDesignTechnicalRequirements = {
  requirements: string[]
  toleranceRows: ErpDesignTechnicalRequirementRow[]
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
const ERP_DESIGN_TOLERANCE_TOOL = 'erp_design_evaluate_tolerances'
const ERP_DESIGN_PARAMETER_TOOL = 'erp_design_query_upload_parameters'
const ERP_DESIGN_DRAWING_TOOL = 'erp_design_preview_drawing'
const ERP_DESIGN_TECHNICAL_REQUIREMENTS_TOOL = 'erp_design_get_technical_requirements'
export const ERP_READONLY_INLINE_ROW_LIMIT = 8

export function erpDesignReadOnlyTableNeedsDisclosure(
  rowCount: number,
  threshold = ERP_READONLY_INLINE_ROW_LIMIT,
): boolean {
  const count = Number.isFinite(Number(rowCount)) ? Math.max(0, Number(rowCount)) : 0
  const limit = Number.isFinite(Number(threshold)) ? Math.max(0, Number(threshold)) : ERP_READONLY_INLINE_ROW_LIMIT
  return count > limit
}

const DESIGN_UPLOAD_TOOLS = new Set([
  'erp_design_parse_new_mold_upload',
  'erp_design_parse_modify_mold_upload',
  'erp_design_get_drawing_status',
  'erp_design_get_upload_result',
  'erp_design_validate_rows',
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

const toleranceColumns: ErpDesignColumn[] = [
  commonColumns[0],
  commonColumns[1],
  { key: 'material', label: '材质', fields: ['material_mark', 'materialMark'], width: 105 },
  commonColumns[2],
  { key: 'technology', label: '加工工艺', fields: ['processing_technology', 'processingTechnology'], width: 140 },
  { key: 'toleranceTier', label: '公差档位', fields: ['tolerance_tier', 'toleranceTier'], width: 125 },
  { key: 'lengthRange', label: '长度允许范围', fields: ['length_allowed_range', 'lengthAllowedRange'], width: 145 },
  { key: 'widthRange', label: '宽度允许范围', fields: ['width_allowed_range', 'widthAllowedRange'], width: 145 },
  { key: 'thicknessRange', label: '厚度允许范围', fields: ['thickness_allowed_range', 'thicknessAllowedRange'], width: 145 },
  { key: 'diagonalTolerance', label: '对角公差', fields: ['diagonal_tolerance', 'diagonalTolerance'], width: 115 },
  { key: 'remark', label: '备注', fields: ['remark'], width: 150 },
]

const parameterColumns: ErpDesignColumn[] = [
  { key: 'code', label: '编码', fields: ['item_code_full', 'itemCodeFull'], width: 150 },
  { key: 'name', label: '名称', fields: ['item_name', 'itemName'], width: 130 },
  { key: 'material', label: '材质', fields: ['material_mark', 'materialMark'], width: 100 },
  { key: 'spec', label: '规格', fields: ['spec_raw', 'specRaw'], width: 175 },
  { key: 'shape', label: '料型', fields: ['material_shape', 'materialShape'], width: 90 },
  { key: 'purchaseQty', label: '采购数量', fields: ['purchase_quantity', 'purchaseQuantity'], width: 105 },
  { key: 'length', label: '长(L)', fields: ['length'], width: 95, decimals: 4 },
  { key: 'width', label: '宽(W)', fields: ['width'], width: 95, decimals: 4 },
  { key: 'height', label: '厚(T)', fields: ['height', 'thickness'], width: 95, decimals: 4 },
  { key: 'outer', label: '外径(Φ)', fields: ['outer_diameter', 'outerDiameter'], width: 100, decimals: 4 },
  { key: 'inner', label: '内径(Φ)', fields: ['inner_diameter', 'innerDiameter'], width: 100, decimals: 4 },
  { key: 'matchStatus', label: '图纸匹配状态', fields: ['match_status', 'matchStatus', 'drawing_match_status', 'drawingMatchStatus', 'drawing_status', 'drawingStatus'], width: 130 },
  { key: 'technology', label: '加工工艺', fields: ['processing_technology', 'processingTechnology'], width: 130 },
  { key: 'remark', label: '备注', fields: ['remark'], width: 150 },
]

const processingColumns: ErpDesignColumn[] = [
  { key: 'field', label: '处理字段', fields: ['field'], width: 150 },
  { key: 'before', label: '处理前', fields: ['before'], width: 220 },
  { key: 'after', label: '处理后', fields: ['after'], width: 220 },
  { key: 'reason', label: '原因', fields: ['reason'], width: 360 },
]

export const erpDesignProcessingColumns = processingColumns

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

function nestedPayload(value: unknown): Record<string, any> | null {
  const source = record(value)
  return record(source?.data) ?? source
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
  const receipt: ErpDesignImportReceipt = {
    sessionId,
    requestNo: String(source?.requestNo ?? source?.request_no ?? '').trim(),
    message: String(source?.message ?? '').trim(),
    status: String(source?.status ?? '').trim() || undefined,
    subjectId: String(source?.subjectId ?? source?.subject_id ?? '').trim() || undefined,
    instanceId: String(source?.instanceId ?? source?.instance_id ?? '').trim() || undefined,
    importedAt: String(source?.importedAt ?? source?.imported_at ?? ''),
  }
  const fields: Array<[keyof ErpDesignImportReceipt, string[]]> = [
    ['requestId', ['requestId', 'request_id']],
    ['processCode', ['processCode', 'process_code']],
    ['processName', ['processName', 'process_name']],
    ['currentNodeName', ['currentNodeName', 'current_node_name']],
    ['approvalStatus', ['approvalStatus', 'approval_status']],
    ['workflowInstanceId', ['workflowInstanceId', 'workflow_instance_id']],
  ]
  for (const [field, aliases] of fields) {
    const candidate = aliases.map(alias => source?.[alias]).find(item => item != null && String(item).trim())
    if (candidate != null) receipt[field] = String(candidate).trim() as never
  }
  const currentApproverNames = source?.currentApproverNames ?? source?.current_approver_names
  if (Array.isArray(currentApproverNames)) {
    receipt.currentApproverNames = [...new Set(currentApproverNames.map(item => String(item || '').trim()).filter(Boolean))]
  }
  const approvalSteps = source?.approvalSteps ?? source?.approval_steps
  if (Array.isArray(approvalSteps)) {
    receipt.approvalSteps = approvalSteps.flatMap((item: any) => {
      const nodeName = String(item?.nodeName ?? item?.node_name ?? '').trim()
      if (!nodeName) return []
      const names = item?.approverNames ?? item?.approver_names
      return [{
        nodeName,
        approverNames: Array.isArray(names)
          ? [...new Set(names.map((name: unknown) => String(name || '').trim()).filter(Boolean))]
          : [],
      }]
    })
  }
  return receipt
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
  const designOrderTypeOptions = source?.designOrderTypeOptions ?? source?.design_order_type_options
  const purchaseReasonOptions = source?.purchaseReasonOptions ?? source?.purchase_reason_options
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
    purchaseReason: String(source?.purchaseReason ?? source?.purchase_reason ?? fallback?.purchaseReason ?? ''),
    designOrderTypeOptions: Array.isArray(designOrderTypeOptions)
      ? designOrderTypeOptions
      : (Array.isArray(fallback?.designOrderTypeOptions) ? fallback.designOrderTypeOptions : []),
    purchaseReasonOptions: Array.isArray(purchaseReasonOptions)
      ? purchaseReasonOptions
      : (Array.isArray(fallback?.purchaseReasonOptions) ? fallback.purchaseReasonOptions : []),
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
    toleranceEvaluation: record(source?.toleranceEvaluation ?? source?.tolerance_evaluation)
      ?? fallback?.toleranceEvaluation ?? null,
  }
}

export function erpDesignSessionFromTool(item: any): ErpDesignPreviewSession | null {
  if (!DESIGN_UPLOAD_TOOLS.has(String(item?.tool || ''))) return null
  return normalizeErpDesignPreview(item?.data)
}

export function erpDesignSessionFromRun(run: any): ErpDesignPreviewSession | null {
  if (!['SUCCEEDED', 'COMPOSITION_FAILED'].includes(String(run?.status || ''))) return null
  const trace = Array.isArray(run?.trace) ? run.trace : []
  // Status/result follow-ups are shared by both ERP upload flows and may not
  // repeat the business type. Keep the original parser receipt as the
  // fallback so a repair_other session is not relabeled as new_model.
  const parserReceipt = trace
    .map((item: any) => erpDesignSessionFromTool(item))
    .find((session: ErpDesignPreviewSession | null, index: number) => {
      const name = String(trace[index]?.tool || '')
      return name === 'erp_design_parse_new_mold_upload'
        || name === 'erp_design_parse_modify_mold_upload'
    })
  for (let index = trace.length - 1; index >= 0; index -= 1) {
    const session = erpDesignSessionFromTool(trace[index])
    if (session) {
      return parserReceipt && parserReceipt.sessionId === session.sessionId
        ? normalizeErpDesignPreview(trace[index]?.data, parserReceipt)
        : session
    }
  }
  return parserReceipt || null
}

export function erpDesignToleranceFromTool(item: any): ErpDesignPreviewSession | null {
  if (String(item?.tool || '') !== ERP_DESIGN_TOLERANCE_TOOL) return null
  return normalizeErpDesignPreview(item?.data)
}

export function erpDesignToleranceFromRun(run: any): ErpDesignPreviewSession | null {
  // ERP tool evidence remains valid when only the later model-summary turn
  // fails. Keep cancelled/running tasks hidden, but do not discard a completed
  // read receipt merely because the provider timed out during final wording.
  if (!['SUCCEEDED', 'FAILED', 'COMPOSITION_FAILED'].includes(String(run?.status || ''))) return null
  const trace = Array.isArray(run?.trace) ? run.trace : []
  for (let index = trace.length - 1; index >= 0; index -= 1) {
    const tolerance = erpDesignToleranceFromTool(trace[index])
    if (tolerance) return tolerance
  }
  return null
}

export function normalizeErpDesignTechnicalRequirements(
  value: unknown,
): ErpDesignTechnicalRequirements | null {
  const source = nestedPayload(value)
  if (String(source?.displayMode ?? source?.display_mode ?? '') !== 'design_technical_requirements') return null
  const technical = record(source?.techRequirements ?? source?.tech_requirements)
  const requirements = Array.isArray(technical?.requirements)
    ? technical.requirements
      .flatMap(item => String(item ?? '').split(/\r?\n|(?=\d+\s*[.、．](?!\d))/g))
      .map(item => item.trim().replace(/^\d+\s*[.、．]\s*/, ''))
      .filter(Boolean)
    : []
  const sourceRows = Array.isArray(technical?.tolerance_table)
    ? technical.tolerance_table
    : (Array.isArray(technical?.toleranceTable) ? technical.toleranceTable : [])
  const toleranceRows = sourceRows
    .filter(row => record(row))
    .map((row, index) => {
      const item = record(row)!
      return {
        seq: Number(item.seq ?? index + 1) || index + 1,
        spec: String(item.spec ?? ''),
        lengthTolerance: String(item.length_tol ?? item.lengthTolerance ?? ''),
        thicknessTolerance: String(item.thick_tol ?? item.thicknessTolerance ?? ''),
        diagonalTolerance: String(item.diag_tol ?? item.diagonalTolerance ?? ''),
      }
    })
  if (!requirements.length && !toleranceRows.length) return null
  return { requirements, toleranceRows }
}

export function erpDesignTechnicalRequirementsFromTool(
  item: any,
): ErpDesignTechnicalRequirements | null {
  if (String(item?.tool || '') !== ERP_DESIGN_TECHNICAL_REQUIREMENTS_TOOL) return null
  return normalizeErpDesignTechnicalRequirements(item?.data)
}

export function erpDesignTechnicalRequirementsFromRun(
  run: any,
): ErpDesignTechnicalRequirements | null {
  if (!['SUCCEEDED', 'FAILED', 'COMPOSITION_FAILED'].includes(String(run?.status || ''))) return null
  const trace = Array.isArray(run?.trace) ? run.trace : []
  for (let index = trace.length - 1; index >= 0; index -= 1) {
    const result = erpDesignTechnicalRequirementsFromTool(trace[index])
    if (result) return result
  }
  return null
}

export function normalizeErpDesignParameterResult(value: unknown): ErpDesignParameterResult | null {
  const source = payload(value)
  if (String(source?.displayMode ?? source?.display_mode ?? '') !== 'design_parameters') return null
  const sessionId = Number(source?.sessionId ?? source?.session_id ?? 0)
  if (!Number.isInteger(sessionId) || sessionId < 1) return null
  const previewRows = rows(source)
  const tableThreshold = Number(source?.tableThreshold ?? source?.table_threshold ?? 8) || 8
  return {
    sessionId,
    sheetType: String(source?.sheetType ?? source?.sheet_type ?? 'steel'),
    moldCode: String(source?.moldCode ?? source?.mold_code ?? ''),
    fileName: String(source?.fileName ?? source?.file_name ?? ''),
    rowCount: previewRows.length,
    matchedCount: Number(source?.matchedCount ?? source?.matched_count ?? previewRows.length) || 0,
    tableThreshold,
    columns: Array.isArray(source?.columns) ? source.columns.filter((c: any) =>
      typeof c?.key === 'string' && typeof c?.label === 'string' && Array.isArray(c?.fields)
    ) : undefined,
    selectedFields: Array.isArray(source?.selectedFields) ? source.selectedFields : undefined,
    renderAsTable: Boolean(source?.renderAsTable ?? source?.render_as_table ?? previewRows.length > tableThreshold),
    previewRows,
  }
}

export function erpDesignParametersFromTool(item: any): ErpDesignParameterResult | null {
  if (String(item?.tool || '') !== ERP_DESIGN_PARAMETER_TOOL) return null
  return normalizeErpDesignParameterResult(item?.data)
}

export function erpDesignParametersFromRun(run: any): ErpDesignParameterResult | null {
  if (!['SUCCEEDED', 'FAILED', 'COMPOSITION_FAILED'].includes(String(run?.status || ''))) return null
  const trace = Array.isArray(run?.trace) ? run.trace : []
  for (let index = trace.length - 1; index >= 0; index -= 1) {
    const result = erpDesignParametersFromTool(trace[index])
    if (result) return result
  }
  return null
}

// Combine selected projections only within one authoritative upload session.
// Row positions come from ERP; names/codes may repeat and are not join keys.
export function erpDesignParameterTablesFromRun(run: any): ErpDesignParameterResult[] {
  if (!['SUCCEEDED', 'FAILED', 'COMPOSITION_FAILED'].includes(String(run?.status || ''))) return []
  const sessions = new Map<number, ErpDesignParameterResult>()
  for (const item of Array.isArray(run?.trace) ? run.trace : []) {
    const result = erpDesignParametersFromTool(item)
    if (!result) continue
    const current = sessions.get(result.sessionId)
    if (!current || !current.columns || !result.columns) {
      sessions.set(result.sessionId, { ...result, previewRows: result.previewRows.map(row => ({ ...row })),
        columns: result.columns ? [...result.columns] : undefined })
      continue
    }
    for (const column of result.columns) {
      if (!current.columns.some(c => c.key === column.key)) current.columns.push(column)
    }
    current.selectedFields = [...new Set([...(current.selectedFields ?? []), ...(result.selectedFields ?? [])])]
    for (const row of result.previewRows) {
      const index = row.rowIndex == null ? -1 : current.previewRows.findIndex(r => r.rowIndex === row.rowIndex)
      if (index < 0) current.previewRows.push({ ...row })
      else current.previewRows[index] = { ...current.previewRows[index], ...row }
    }
    current.rowCount = current.matchedCount = current.previewRows.length
  }
  // 公差判断与参数查询针对同一个上传会话时，合并到同一张表；单独询问
  // 公差时仍由 ErpDesignToleranceTable 独立展示。
  for (const item of Array.isArray(run?.trace) ? run.trace : []) {
    const tolerance = erpDesignToleranceFromTool(item)
    if (!tolerance) continue
    const current = sessions.get(tolerance.sessionId)
    if (!current) continue
    for (const column of toleranceColumns) {
      if (!current.columns?.some(c => c.key === column.key)) current.columns?.push(column)
    }
    for (const row of tolerance.previewRows) {
      const index = row.rowIndex == null ? -1 : current.previewRows.findIndex(r => r.rowIndex === row.rowIndex)
      if (index < 0) current.previewRows.push({ ...row })
      else current.previewRows[index] = { ...current.previewRows[index], ...row }
    }
    current.rowCount = current.matchedCount = current.previewRows.length
  }
  return [...sessions.values()]
}

export function erpDesignToleranceMergedIntoParameter(run: any): boolean {
  const tolerance = erpDesignToleranceFromRun(run)
  if (!tolerance) return false
  return erpDesignParameterTablesFromRun(run).some(result => result.sessionId === tolerance.sessionId)
}

export function erpDesignDrawingId(row: ErpDesignRow): number {
  const id = Number(row.drawing_resource_id ?? row.drawingResourceId ?? row.drawing_id ?? row.drawingId ?? 0)
  return Number.isInteger(id) && id > 0 ? id : 0
}

export function erpDesignDrawingsFromTool(item: any): ErpDesignDrawingResult | null {
  if (String(item?.tool || '') === 'erp_design_query_standard_hardware') {
    const source = nestedPayload(item?.data)
    if (!Array.isArray(source?.rows)) return null
    const previewRows = source.rows.filter((row: unknown) => record(row))
    return {
      source: 'standard_hardware',
      moldCode: '',
      totalCount: Number(source.total ?? previewRows.length),
      previewRows,
    }
  }
  if (String(item?.tool || '') !== ERP_DESIGN_DRAWING_TOOL) return null
  const source = payload(item?.data)
  if (String(source?.displayMode ?? source?.display_mode ?? '') !== 'design_drawings') return null
  const sessionId = Number(source?.sessionId ?? source?.session_id ?? 0)
  if (!Number.isInteger(sessionId) || sessionId < 1) return null
  return {
    source: 'upload',
    sessionId,
    moldCode: String(source?.moldCode ?? source?.mold_code ?? ''),
    totalCount: rows(source).length,
    previewRows: rows(source).filter(row => record(row) && erpDesignDrawingId(row)),
  }
}

export function erpDesignDrawingsFromRun(run: any): ErpDesignDrawingResult[] {
  if (!['SUCCEEDED', 'FAILED', 'COMPOSITION_FAILED'].includes(String(run?.status || ''))) return []
  const sessions = new Map<string, ErpDesignDrawingResult>()
  for (const item of Array.isArray(run?.trace) ? run.trace : []) {
    const result = erpDesignDrawingsFromTool(item)
    if (!result) continue
    const key = `${result.source}:${result.sessionId ?? ''}`
    const current = sessions.get(key)
    if (!current) {
      sessions.set(key, result)
      continue
    }
    for (const row of result.previewRows) {
      const rowKey = (value: ErpDesignRow) => result.source === 'standard_hardware'
        ? value.relativePath
        : erpDesignDrawingId(value)
      const index = current.previewRows.findIndex(existing => rowKey(existing) === rowKey(row))
      if (index < 0) current.previewRows.push(row)
      else current.previewRows[index] = row
    }
    current.totalCount = Math.max(current.totalCount, result.totalCount, current.previewRows.length)
  }
  return [...sessions.values()]
}

export function erpDesignDrawingColumns(source: ErpDesignDrawingResult['source'] = 'upload'): ErpDesignColumn[] {
  if (source === 'standard_hardware') return [
    { key: 'code', label: '标准件编号', fields: ['standardCode'], width: 190 },
    { key: 'file', label: '图纸文件', fields: ['fileName'], width: 300 },
    { key: 'preview', label: '预览', fields: [], width: 110 },
  ]
  return [
    { key: 'code', label: '编码', fields: ['item_code_full', 'itemCodeFull'], width: 190 },
    { key: 'name', label: '名称', fields: ['item_name', 'itemName'], width: 145 },
    { key: 'file', label: '图纸文件', fields: ['drawing_file_name', 'drawingFileName'], width: 230 },
    { key: 'preview', label: '预览', fields: [], width: 110 },
  ]
}

export function erpDesignPreviewUrl(session: ErpDesignPreviewSession): string {
  const url = new URL(ERP_DESIGN_UPLOAD_PAGE)
  url.searchParams.set('sessionId', String(session.sessionId))
  url.searchParams.set('sheetType', session.sheetType || 'steel')
  url.searchParams.set('embedded', '1')
  return url.toString()
}

export function erpDesignSheetLabel(sheetType: string): string {
  if (sheetType === 'hardware') return '五金清单'
  if (sheetType === 'stock_prepare') return '备料清单'
  return '钢料清单'
}

export function erpDesignUploadStatusLabel(sheetType: string, imported = false): string {
  const sheetLabel = erpDesignSheetLabel(sheetType)
  return imported ? `${sheetLabel}已成功导入` : `${sheetLabel}上传已就绪`
}

export function erpDesignColumns(sheetType: string): ErpDesignColumn[] {
  return sheetType === 'hardware' ? hardwareColumns : steelColumns
}

export function erpDesignToleranceColumns(): ErpDesignColumn[] {
  return toleranceColumns
}

export function erpDesignParameterColumns(): ErpDesignColumn[] {
  return parameterColumns
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

export function erpDesignToleranceCell(row: ErpDesignRow, column: ErpDesignColumn): string {
  const value = firstPresent(row, column.fields)
  if (value === null) return '-'
  if (typeof value === 'number') {
    if (column.decimals != null) return value.toFixed(column.decimals)
    return Number.isInteger(value) ? String(value) : String(Number(value.toFixed(4)))
  }
  return String(value) || '-'
}

export function erpDesignParameterCell(row: ErpDesignRow, column: ErpDesignColumn): string {
  return erpDesignToleranceCell(row, column)
}
