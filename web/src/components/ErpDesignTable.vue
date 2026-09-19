<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import {
  ERP_DESIGN_AGING_TREATMENT_OPTIONS,
  ERP_DESIGN_HEAT_TREATMENT_OPTIONS,
  erpDesignAgingTreatmentDisabled,
  erpDesignAgingTreatmentSupported,
  erpDesignCell,
  erpDesignColumns,
  normalizeErpDesignTreatments,
  type ErpDesignColumn,
  type ErpDesignPreviewSession,
  type ErpDesignRow,
} from '../erpDesignPreview'

const props = defineProps<{
  preview: ErpDesignPreviewSession
  loading: boolean
  error: string
  repricing: boolean
  importing: boolean
  notice: string
  duplicateNotice: { message: string; requestNo: string } | null
}>()

const emit = defineEmits<{
  retry: []
  reprice: [rows: ErpDesignRow[]]
  repriceRow: [row: ErpDesignRow]
  previewDrawing: [row: ErpDesignRow]
  updateDraft: [draft: { expectedDate: string; remark: string }]
  updateRows: [rows: ErpDesignRow[]]
  import: [payload: { previewRows: ErpDesignRow[]; expectedDate: string; remark: string; allowDuplicate: boolean }]
}>()

const localRows = ref<ErpDesignRow[]>([])
const expectedDate = ref('')
const remark = ref('')

const columns = computed(() => erpDesignColumns(props.preview.sheetType))
const displayCell = (row: ErpDesignRow, column: ErpDesignColumn) => (
  erpDesignCell(row, column, props.preview.techRequirements)
)
const quantity = computed(() => sumField(['qty', 'quantity']))
const idleQuantity = computed(() => sumField(['idle_quantity', 'idleQuantity']))
const purchaseQuantity = computed(() => sumField(['purchase_quantity', 'purchaseQuantity'], true))
const autoCorrectedCount = computed(() => localRows.value.filter(row => Boolean(
  row.item_code_auto_corrected || row.itemCodeAutoCorrected
  || row.drawing_dimension_auto_corrected || row.drawingDimensionAutoCorrected
  || row.drawing_dimension_defaulted || row.drawingDimensionDefaulted,
)).length)
const drawingCorrectableCount = computed(() => localRows.value.filter(row => (
  materialShapeMismatch(row)
  || ['outer_diameter', 'inner_diameter', 'length', 'width', 'height'].some(field => drawingFieldMismatch(row, field))
  || quantityFieldMismatch(row)
)).length)

watch(() => props.preview.previewRows, value => {
  localRows.value = (value || []).map(row => props.preview.sheetType === 'steel'
    ? normalizeErpDesignTreatments(row)
    : ({ ...row }))
}, { immediate: true })

watch(() => props.preview.sessionId, () => {
  expectedDate.value = props.preview.expectedDate || defaultExpectedDate(props.preview)
  remark.value = props.preview.remark || ''
  updateDraft()
}, { immediate: true })

function sumField(fields: string[], fallbackToQty = false): number {
  return localRows.value.reduce((total, row) => {
    let value: unknown = null
    for (const field of fields) {
      if (row[field] !== null && row[field] !== undefined && row[field] !== '') {
        value = row[field]
        break
      }
    }
    if (value == null && fallbackToQty) value = row.qty ?? row.quantity ?? 0
    return total + (Number(value) || 0)
  }, 0)
}

function localDate(days: number): string {
  const date = new Date()
  date.setHours(12, 0, 0, 0)
  date.setDate(date.getDate() + days)
  return `${date.getFullYear()}-${String(date.getMonth() + 1).padStart(2, '0')}-${String(date.getDate()).padStart(2, '0')}`
}

function defaultExpectedDate(preview: ErpDesignPreviewSession): string {
  const count = preview.previewRows.length
  if (preview.sheetType === 'steel') return localDate(count >= 10 ? 3 : 2)
  if (preview.sheetType === 'hardware' && count >= 10) return localDate(15)
  return ''
}

function updateDraft() {
  emit('updateDraft', { expectedDate: expectedDate.value, remark: remark.value })
}

function updateRows() {
  const rows = localRows.value.map(row => ({ ...row }))
  emit('updateRows', rows)
}

function editableNumber(columnKey: string): boolean {
  return ['outer', 'inner', 'length', 'width', 'height', 'qty'].includes(columnKey)
}

function editField(columnKey: string): string {
  return ({ outer: 'outer_diameter', inner: 'inner_diameter', height: 'height', qty: 'qty' } as Record<string, string>)[columnKey] || columnKey
}

function isAttachedOrderRow(row: ErpDesignRow): boolean {
  return String(row.material_type ?? row.materialType ?? '').includes('附图订购')
}

function materialShape(row: ErpDesignRow): string {
  const attached = isAttachedOrderRow(row)
    ? String(row.attached_order_material_shape ?? row.attachedOrderMaterialShape ?? '')
    : ''
  const value = attached || String(row.material_shape ?? row.materialShape
    ?? (props.preview.sheetType === 'steel' ? row.material_type ?? row.materialType ?? '' : ''))
  if (value === '圆环') return '圆环料'
  if (['方料', '圆料', '圆环料'].includes(value)) return value
  if (row.length != null && row.width != null) return '方料'
  if ((row.outer_diameter ?? row.outerDiameter) != null) {
    return (row.inner_diameter ?? row.innerDiameter) != null ? '圆环料' : '圆料'
  }
  return value
}

function drawingExpectedDimensions(row: ErpDesignRow): ErpDesignRow | null {
  const value = row.drawing_dimension_value ?? row.drawingDimensionValue
  return value && typeof value === 'object' ? value : null
}

function drawingExpectedMaterialShape(row: ErpDesignRow): string {
  const explicit = String(row.drawing_material_shape ?? row.drawingMaterialShape ?? '')
  if (['方料', '圆料', '圆环料'].includes(explicit)) return explicit
  const dimensions = drawingExpectedDimensions(row)
  if (dimensions?.length != null && dimensions?.width != null) return '方料'
  if ((dimensions?.outer_diameter ?? dimensions?.outerDiameter) != null) return '圆料'
  return ''
}

function drawingExpectedQuantity(row: ErpDesignRow): number | null {
  const value = row.drawing_quantity ?? row.drawingQuantity
  if (value == null || value === '') return null
  const number = Number(value)
  return Number.isFinite(number) ? number : null
}

function numbersClose(left: unknown, right: unknown): boolean {
  if (left == null || left === '' || right == null || right === '') return false
  return Math.abs(Number(left) - Number(right)) <= 0.001
}

function expectedDimension(row: ErpDesignRow, field: string): unknown {
  const expected = drawingExpectedDimensions(row)
  if (!expected) return null
  if (field === 'outer_diameter') return expected.outer_diameter ?? expected.outerDiameter
  if (field === 'inner_diameter') return expected.inner_diameter ?? expected.innerDiameter
  return expected[field]
}

function currentDimension(row: ErpDesignRow, field: string): unknown {
  if (field === 'outer_diameter') return row.outer_diameter ?? row.outerDiameter
  if (field === 'inner_diameter') return row.inner_diameter ?? row.innerDiameter
  return row[field]
}

function isRoundMaterial(row: ErpDesignRow): boolean {
  return ['圆料', '圆环料'].includes(materialShape(row))
}

function materialShapeMismatch(row: ErpDesignRow): boolean {
  const expected = drawingExpectedMaterialShape(row)
  return Boolean(expected) && materialShape(row) !== expected
}

function drawingFieldMismatch(row: ErpDesignRow, field: string): boolean {
  const expectedShape = drawingExpectedMaterialShape(row)
  const currentShape = materialShape(row)
  if (!expectedShape) return false
  if (field === 'length' || field === 'width') {
    return expectedShape === '方料'
      ? currentShape !== '方料' || !numbersClose(currentDimension(row, field), expectedDimension(row, field))
      : currentShape === '方料'
  }
  if (field === 'outer_diameter') {
    return expectedShape === '圆料' || expectedShape === '圆环料'
      ? !isRoundMaterial(row) || !numbersClose(currentDimension(row, field), expectedDimension(row, field))
      : isRoundMaterial(row)
  }
  if (field === 'inner_diameter') {
    const expected = expectedDimension(row, field)
    return expectedShape === '圆环料'
      ? currentShape !== '圆环料' || (expected != null && !numbersClose(currentDimension(row, field), expected))
      : currentShape === '圆环料'
  }
  if (field === 'height') {
    const expected = expectedDimension(row, field)
    return expected != null && !numbersClose(currentDimension(row, field), expected)
  }
  return false
}

function quantityFieldMismatch(row: ErpDesignRow): boolean {
  const expected = drawingExpectedQuantity(row)
  return expected != null && !numbersClose(row.qty ?? row.quantity, expected)
}

function withDrawingAction(message: string): string {
  return message.includes('双击') ? message : `${message}；双击将料型和全部尺寸替换为图纸参数`
}

function drawingMaterialShapeTip(row: ErpDesignRow): string {
  const expected = drawingExpectedMaterialShape(row)
  const message = String(row.drawing_material_shape_correction_message
    ?? row.drawingMaterialShapeCorrectionMessage
    ?? `料单料型与图纸不一致：${materialShape(row) || '空'}→${expected}`)
  return withDrawingAction(message)
}

function drawingFieldTip(row: ErpDesignRow, field: string): string {
  if (field === 'qty') {
    const expected = drawingExpectedQuantity(row)
    const message = String(row.drawing_quantity_correction_message
      ?? row.drawingQuantityCorrectionMessage
      ?? '料单数量与图纸加工说明 PCS 不一致，请核对')
    return expected == null ? message : `${message}；双击填入图纸值 ${expected}`
  }
  const expected = expectedDimension(row, field)
  const message = String(row.drawing_dimension_correction_message
    ?? row.drawingDimensionCorrectionMessage
    ?? '料单尺寸与图纸加工说明不一致，请核对')
  if (materialShapeMismatch(row)) return withDrawingAction(message)
  return expected == null ? `${message}；双击按图纸修正全部尺寸` : `${message}；双击填入全部图纸尺寸（当前值 ${expected}）`
}

function syncDrawingMatchFlags(row: ErpDesignRow) {
  const expectedShape = drawingExpectedMaterialShape(row)
  if (expectedShape) {
    const matched = !materialShapeMismatch(row)
    row.drawing_material_shape_match = matched
    row.drawingMaterialShapeMatch = matched
    if (matched) {
      row.drawing_material_shape_correction_message = ''
      row.drawingMaterialShapeCorrectionMessage = ''
      delete row.drawing_material_shape_original
      delete row.drawingMaterialShapeOriginal
    }
  }
  const dimensions = drawingExpectedDimensions(row)
  if (dimensions) {
    const matched = !materialShapeMismatch(row)
      && !['outer_diameter', 'inner_diameter', 'length', 'width', 'height'].some(field => drawingFieldMismatch(row, field))
    row.drawing_dimension_match = matched
    row.drawingDimensionMatch = matched
    if (matched) {
      row.drawing_dimension_correction_message = ''
      row.drawingDimensionCorrectionMessage = ''
      delete row.drawing_dimension_original
      delete row.drawingDimensionOriginal
    }
  }
  if (drawingExpectedQuantity(row) != null) {
    const matched = !quantityFieldMismatch(row)
    row.drawing_quantity_match = matched
    row.drawingQuantityMatch = matched
    if (matched) {
      row.drawing_quantity_correction_message = ''
      row.drawingQuantityCorrectionMessage = ''
      delete row.drawing_quantity_original
      delete row.drawingQuantityOriginal
    }
  }
}

function setShape(row: ErpDesignRow, value: string) {
  if (isAttachedOrderRow(row)) {
    row.attached_order_material_shape = value
    row.attachedOrderMaterialShape = value
    row.attachedOrderShapeManuallyEdited = true
  } else {
    row.material_shape = value
    row.materialShape = value
    if (props.preview.sheetType === 'steel') {
      row.material_type = value
      row.materialType = value
    }
  }
  if (value === '方料') {
    row.outer_diameter = null
    row.inner_diameter = null
  } else {
    row.length = null
    row.width = null
    if (value === '圆料') row.inner_diameter = null
  }
  if (props.preview.sheetType === 'hardware' && isAttachedOrderRow(row) && value !== '圆料') {
    for (const key of [
      'attached_order_material_price', 'attachedOrderMaterialPrice', 'accounting_unit_price', 'accountingUnitPrice',
      'unit_price', 'unitPrice', 'attached_order_total_price', 'attachedOrderTotalPrice',
      'material_amount', 'materialAmount', 'accounting_amount', 'accountingAmount',
      'process_unit_price', 'processUnitPrice', 'process_amount', 'processAmount',
      'total_price', 'totalPrice', 'density', 'unit_weight', 'unitWeight', 'weight',
      'final_price_source', 'finalPriceSource',
    ]) row[key] = null
    if (value === '方料') {
      row.calculation_process = '方料不参与自动核价'
      row.calculationProcess = row.calculation_process
      row.remark = '方料不参与自动核价'
    }
  }
  if (props.preview.sheetType === 'steel' && !erpDesignAgingTreatmentSupported(row)) {
    row.post_treatment = '否'
    row.postTreatment = '否'
  }
}

function handleShapeChange(row: ErpDesignRow, event: Event) {
  setShape(row, (event.target as HTMLSelectElement).value)
  finishRowChange(row, true)
}

function dimensionField(columnKey: string): string {
  return ({ outer: 'outer_diameter', inner: 'inner_diameter' } as Record<string, string>)[columnKey] || columnKey
}

function isDimensionColumn(columnKey: string): boolean {
  return ['outer', 'inner', 'length', 'width', 'height'].includes(columnKey)
}

function showDimensionInput(row: ErpDesignRow, columnKey: string): boolean {
  if (columnKey === 'outer') return isRoundMaterial(row)
  if (columnKey === 'inner') return materialShape(row) === '圆环料'
  if (columnKey === 'length' || columnKey === 'width') return materialShape(row) === '方料'
  if (columnKey === 'height') return Boolean(materialShape(row) || drawingExpectedDimensions(row))
  return true
}

function dimensionText(value: unknown): string {
  const number = Number(value || 0)
  return number.toFixed(4).replace(/\.?0+$/, '')
}

function rowQuantity(row: ErpDesignRow): number {
  return Number(row.qty ?? row.quantity ?? 0) || 0
}

function purchaseQuantityValue(row: ErpDesignRow): number {
  const value = row._scrap_purchase_quantity_after_deduction
    ?? row.purchaseQuantityAfterDeduction
    ?? row.purchase_quantity_after_deduction
  return value == null || value === '' ? rowQuantity(row) : Number(value)
}

function syncQuantityDecision(row: ErpDesignRow) {
  const status = row._scrap_decision_status ?? row.scrapDecisionStatus ?? row.scrap_decision_status
  if (status === 'active') {
    const idle = Number(row._scrap_reserved_quantity ?? row.scrapReservedQuantity ?? row.scrap_reserved_quantity ?? 0) || 0
    row._scrap_reserved_quantity = Math.min(idle, rowQuantity(row))
    row._scrap_purchase_quantity_after_deduction = Math.max(0, rowQuantity(row) - row._scrap_reserved_quantity)
  } else if (status === 'skipped') {
    row._scrap_purchase_quantity_after_deduction = rowQuantity(row)
  }
  row.purchase_quantity = purchaseQuantityValue(row)
  row.purchaseQuantity = row.purchase_quantity
}

function clearSteelPricing(row: ErpDesignRow) {
  for (const key of [
    'unit_price', 'unitPrice', 'material_unit_price', 'materialUnitPrice',
    'material_amount', 'materialAmount', 'total_price', 'totalPrice',
    'final_price_source', 'finalPriceSource', 'steel_surcharge_amount', 'steelSurchargeAmount',
    'steel_base_unit_price', 'steelBaseUnitPrice', 'steel_surcharge_piece_fee', 'steelSurchargePieceFee',
    'steel_surcharge_rule_ids', 'steelSurchargeRuleIds',
    'steel_aging_price_id', 'steelAgingPriceId', 'steel_aging_unit_price', 'steelAgingUnitPrice',
    'steel_aging_partner_id', 'steelAgingPartnerId', 'steel_aging_partner_name', 'steelAgingPartnerName',
  ]) row[key] = null
}

function recalculateSteelRow(row: ErpDesignRow) {
  const shape = materialShape(row)
  const height = Number(row.height || 0)
  const density = Number(row.density || 0)
  let volume = 0
  if (shape === '方料') {
    volume = Number(row.length || 0) * Number(row.width || 0) * height
    row.spec_raw = `${dimensionText(row.length)}L*${dimensionText(row.width)}W*${dimensionText(row.height)}T`
  } else if (shape === '圆料') {
    const outer = Number(row.outer_diameter || 0)
    volume = Math.PI * Math.pow(outer / 2, 2) * height
    row.spec_raw = `Φ${dimensionText(row.outer_diameter)}*${dimensionText(row.height)}`
  } else if (shape === '圆环料') {
    const outer = Number(row.outer_diameter || 0)
    const inner = Number(row.inner_diameter || 0)
    volume = Math.PI * (Math.pow(outer / 2, 2) - Math.pow(inner / 2, 2)) * height
    row.spec_raw = `Φ${dimensionText(row.outer_diameter)}*${dimensionText(row.inner_diameter)}*${dimensionText(row.height)}`
  }
  const unitWeight = Math.max(0, volume * density / 1_000_000)
  row.unit_weight = Number(unitWeight.toFixed(4))
  row.unitWeight = row.unit_weight
  row.weight = Number((unitWeight * rowQuantity(row)).toFixed(4))
  clearSteelPricing(row)
}

function firstPresentValue(row: ErpDesignRow, ...keys: string[]): unknown {
  for (const key of keys) {
    if (row[key] !== undefined && row[key] !== null && row[key] !== '') return row[key]
  }
  return null
}

function optionalNumber(value: unknown): number | null {
  if (value == null || value === '') return null
  const number = Number(value)
  return Number.isFinite(number) ? number : null
}

function isPaintRequired(row: ErpDesignRow): boolean {
  const value = firstPresentValue(row, 'paint_required', 'paintRequired')
  return value === true || value === 1 || String(value ?? '').toLowerCase() === 'true'
}

function hasAttachedInternalAccounting(row: ErpDesignRow): boolean {
  if (!isAttachedOrderRow(row)) return false
  const source = String(firstPresentValue(row, 'final_price_source', 'finalPriceSource') ?? '')
  if (!['attached_order_round_auto', 'attached_manual_costing'].includes(source)) return false
  const price = optionalNumber(firstPresentValue(row, 'accounting_unit_price', 'accountingUnitPrice', 'unit_price', 'unitPrice'))
  return price != null && price > 0
}

function effectiveApprovedUnitPrice(row: ErpDesignRow): number | null {
  if (hasAttachedInternalAccounting(row)) return null
  const explicit = optionalNumber(firstPresentValue(row, 'approved_unit_price', 'approvedUnitPrice'))
  if (explicit != null) return explicit
  const status = String(firstPresentValue(row, 'hardware_price_match_status', 'hardwarePriceMatchStatus') ?? '').toLowerCase()
  const priceId = firstPresentValue(row, 'hardware_price_id', 'hardwarePriceId')
  if (status !== 'matched' && priceId == null) return null
  if (status && status !== 'matched') return null
  return optionalNumber(firstPresentValue(row, 'unit_price', 'unitPrice'))
}

function setAmount(row: ErpDesignRow, snake: string, camel: string, value: number | null) {
  row[snake] = value
  row[camel] = value
}

function recalculateHardwareAmounts(row: ErpDesignRow) {
  const qty = purchaseQuantityValue(row)
  if (isPaintRequired(row)) {
    setAmount(row, 'accounting_amount', 'accountingAmount', null)
    setAmount(row, 'total_price', 'totalPrice', null)
    row.paint_pricing_status = 'pending_reprice'
    row.paintPricingStatus = 'pending_reprice'
    row.calculation_process = '喷漆件信息已调整，确认导入时由 ERP 重新识别并计价'
    row.calculationProcess = row.calculation_process
    return
  }
  const approved = effectiveApprovedUnitPrice(row)
  if (approved != null) {
    for (const [snake, camel] of [
      ['accounting_unit_price', 'accountingUnitPrice'], ['accounting_amount', 'accountingAmount'],
      ['attached_order_material_price', 'attachedOrderMaterialPrice'], ['attached_order_total_price', 'attachedOrderTotalPrice'],
      ['material_amount', 'materialAmount'], ['process_unit_price', 'processUnitPrice'], ['process_amount', 'processAmount'],
    ]) setAmount(row, snake, camel, null)
    setAmount(row, 'total_price', 'totalPrice', Number((approved * qty).toFixed(2)))
    return
  }
  if (isAttachedOrderRow(row)) {
    const materialPrice = optionalNumber(firstPresentValue(row, 'attached_order_material_price', 'attachedOrderMaterialPrice'))
    const accountingPrice = optionalNumber(firstPresentValue(row, 'accounting_unit_price', 'accountingUnitPrice'))
    const processPrice = optionalNumber(firstPresentValue(row, 'process_unit_price', 'processUnitPrice'))
    const totalUnitPrice = optionalNumber(firstPresentValue(row, 'attached_order_total_price', 'attachedOrderTotalPrice'))
    setAmount(row, 'material_amount', 'materialAmount', materialPrice == null ? null : Number((materialPrice * qty).toFixed(2)))
    setAmount(row, 'accounting_amount', 'accountingAmount', accountingPrice == null ? null : Number((accountingPrice * qty).toFixed(2)))
    setAmount(row, 'process_amount', 'processAmount', processPrice == null ? null : Number((processPrice * qty).toFixed(2)))
    setAmount(row, 'total_price', 'totalPrice', totalUnitPrice == null ? null : Number((totalUnitPrice * qty).toFixed(2)))
    let calculation = String(firstPresentValue(row, 'calculation_process', 'calculationProcess') ?? '')
    for (const [label, value] of [
      ['核算金额', row.accounting_amount], ['加工金额', row.process_amount], ['总价', row.total_price],
    ] as Array<[string, unknown]>) {
      if (value == null || !calculation) continue
      const replacement = `${label}${Number(value).toFixed(2).replace(/\.?0+$/, '')}`
      const pattern = new RegExp(`${label}\\s*[-+]?\\d+(?:\\.\\d+)?`, 'g')
      calculation = pattern.test(calculation) ? calculation.replace(pattern, replacement) : `${calculation}；${replacement}`
    }
    row.calculation_process = calculation
    row.calculationProcess = calculation
    return
  }
  const unitPrice = optionalNumber(firstPresentValue(row, 'unit_price', 'unitPrice'))
  if (unitPrice != null) setAmount(row, 'total_price', 'totalPrice', Number((unitPrice * qty).toFixed(2)))
}

function finishRowChange(row: ErpDesignRow, reprice: boolean) {
  syncQuantityDecision(row)
  syncDrawingMatchFlags(row)
  if (props.preview.sheetType === 'steel') recalculateSteelRow(row)
  else recalculateHardwareAmounts(row)
  const shouldReprice = reprice && props.preview.sheetType === 'steel'
  if (shouldReprice) {
    row._erp_reprice_loading = true
    row.calculation_process = '正在按 ERP 当前规则重新核算...'
    row.calculationProcess = row.calculation_process
  }
  updateRows()
  if (shouldReprice) emit('repriceRow', { ...row })
}

function handleDimensionChange(row: ErpDesignRow) {
  finishRowChange(row, true)
}

function handleQuantityChange(row: ErpDesignRow) {
  finishRowChange(row, true)
}

function heatTreatment(row: ErpDesignRow): string {
  return String(row.heat_treatment ?? row.heatTreatment ?? '')
}

function agingTreatment(row: ErpDesignRow): string {
  return String(row.post_treatment ?? row.postTreatment ?? '否')
}

function handleHeatTreatmentChange(row: ErpDesignRow, event: Event) {
  const value = (event.target as HTMLInputElement).value.trim()
  row.heat_treatment = value
  row.heatTreatment = value
  updateRows()
}

function handleAgingTreatmentChange(row: ErpDesignRow, event: Event) {
  if (erpDesignAgingTreatmentDisabled(row)) return
  const value = (event.target as HTMLSelectElement).value
  row.post_treatment = ERP_DESIGN_AGING_TREATMENT_OPTIONS.includes(value as '是' | '否') ? value : '否'
  row.postTreatment = row.post_treatment
  finishRowChange(row, true)
}

function submitImport(allowDuplicate: boolean) {
  updateDraft()
  emit('import', {
    previewRows: localRows.value.map(row => ({ ...row })),
    expectedDate: expectedDate.value,
    remark: remark.value,
    allowDuplicate,
  })
}

const canImport = computed(() => Boolean(
  localRows.value.length
  && expectedDate.value
  && !props.loading
  && !props.repricing
  && !props.importing
  && !props.preview.drawingProcessing
  && !props.preview.errors.length,
))

function applyDrawingDimensionField(row: ErpDesignRow) {
  const expected = drawingExpectedDimensions(row)
  if (!expected) return
  const shape = drawingExpectedMaterialShape(row)
  if (shape) setShape(row, shape)
  if (shape === '方料') {
    row.length = expected.length == null ? null : Number(expected.length)
    row.width = expected.width == null ? null : Number(expected.width)
    row.outer_diameter = null
    row.inner_diameter = null
  } else if (shape === '圆料' || shape === '圆环料') {
    const outer = expected.outer_diameter ?? expected.outerDiameter
    const inner = expected.inner_diameter ?? expected.innerDiameter
    row.outer_diameter = outer == null ? null : Number(outer)
    row.inner_diameter = shape === '圆环料' && inner != null ? Number(inner) : null
    row.length = null
    row.width = null
  }
  if (expected.height != null) row.height = Number(expected.height)
  finishRowChange(row, true)
}

function applyDrawingQuantity(row: ErpDesignRow) {
  const expected = drawingExpectedQuantity(row)
  if (expected == null) return
  row.qty = expected
  row.quantity = expected
  finishRowChange(row, true)
}

function canPreviewDrawing(row: ErpDesignRow): boolean {
  const id = Number(row.drawing_resource_id ?? row.drawingResourceId ?? row.drawing_id ?? row.drawingId ?? 0)
  return id > 0 && String(row.drawing_access_status ?? row.drawingAccessStatus ?? 'available') !== 'unavailable'
}

function summaryValue(key: string): string {
  if (key === 'qty') return String(quantity.value)
  if (key === 'idleQty') return String(idleQuantity.value)
  if (key === 'purchaseQty') return String(purchaseQuantity.value)
  return ''
}

const hardwareRules = computed(() => {
  const fixed = [
    '附图订购：根据名称、编号及模具号规则判断。',
    '尺寸、孔槽及加工数量按《附图计算.py》的规则从 DXF 提取。',
    '圆料仅对 45#、CR12 按《计算圆的逻辑.xls》匹配材料价格。',
    '方料不参与自动核价，不带出核算单价、核算金额和总价。',
  ]
  return fixed
})

const hardwareRoundRules = [
  '有上下两个主要视图时，以最上方视图的最外轮廓判断；外轮廓为圆则直接判断为圆料。',
  '只有一个主要视图时，外形直径标注命中才判断为圆料；孔、沉头和攻牙直径不参与判断。',
  '识别到完整 L/W/T，或 A3、SECC 等板料特征时判断为方料。',
  '证据冲突时显示待确认，证据不足时显示未识别，均不自动核价。',
]

const hardwareExtraFees = computed(() => props.preview.additionalProcessingFeeRules.map(rule => ({
  name: String(rule.processKeyword ?? rule.process_keyword ?? rule.name ?? '-'),
  unitPrice: rule.unitPrice ?? rule.unit_price ?? '-',
  remark: String(rule.pricingNote ?? rule.pricing_note ?? rule.remark ?? '-'),
})))

const technicalLines = computed(() => {
  const source = props.preview.techRequirements || {}
  for (const key of ['lines', 'requirements', 'items', 'technicalRequirements']) {
    if (Array.isArray(source[key])) return source[key].map((item: unknown) => String(item)).filter(Boolean)
  }
  return []
})

const toleranceRows = computed(() => {
  const source = props.preview.techRequirements || {}
  const value = source.tolerance_table ?? source.toleranceTable
  return Array.isArray(value) ? value : []
})
</script>

<template>
  <div class="erp-design-table-view">
    <div class="erp-design-order-form">
      <label><span>模具号</span><input :value="preview.moldCode||'-'" readonly></label>
      <label><span>类型</span><input value="新模" readonly></label>
      <label><span><b>*</b> 交期</span><input v-model="expectedDate" type="date" @change="updateDraft"></label>
      <label class="erp-design-remark"><span>备注</span><textarea v-model="remark" maxlength="1000" placeholder="请输入备注" @input="updateDraft"></textarea><small>{{remark.length}} / 1000</small></label>
    </div>

    <div v-if="autoCorrectedCount||drawingCorrectableCount" class="erp-design-correction-summary">
      <span v-if="autoCorrectedCount"><strong>自动修正</strong><b>{{autoCorrectedCount}} 条</b><em>已自动完成</em></span>
      <span v-if="drawingCorrectableCount"><strong>双击图纸修正</strong><b>{{drawingCorrectableCount}} 条</b><em>双击橙色提示可按图纸回填料型、长宽厚与数量</em></span>
    </div>

    <div v-if="duplicateNotice" class="erp-design-duplicate-warning" role="alert">
      <div>
        <strong>检测到重复上传<span v-if="duplicateNotice.requestNo">：{{duplicateNotice.requestNo}}</span></strong>
        <p>{{duplicateNotice.message}} 该提示不会阻止导入，请核对后确认是否继续。</p>
      </div>
      <button type="button" :disabled="!canImport" @click="submitImport(true)">
        {{importing ? '正在导入…' : '确认重复导入'}}
      </button>
    </div>

    <div class="erp-design-table-toolbar">
      <div>
        <strong>明细表</strong>
        <span>共 {{localRows.length}} 项，请购数量 {{quantity}}</span>
      </div>
      <div class="erp-design-table-actions">
        <button v-if="preview.sheetType==='steel'" type="button" :disabled="repricing||loading||!localRows.length" @click="$emit('reprice',localRows)">
          {{repricing?'正在核算…':'重新核算价格'}}
        </button>
        <button class="erp-design-import-button" type="button" :disabled="!canImport" :title="expectedDate?'确认后将提交 ERP 导入':'请先填写交期'" @click="submitImport(false)">
          {{importing ? '正在导入…' : '确认导入'}}
        </button>
      </div>
    </div>

    <div v-if="loading&&!localRows.length" class="erp-design-table-state">正在从 ERP 读取解析明细…</div>
    <div v-else-if="error&&!localRows.length" class="erp-design-table-state is-error">
      <span>{{error}}</span>
      <button type="button" @click="$emit('retry')">重试</button>
    </div>
    <div v-else-if="!localRows.length" class="erp-design-table-state">未读取到解析明细。</div>

    <div v-else class="erp-design-table-scroll">
      <table>
        <thead>
          <tr>
            <th class="erp-design-index">NO.</th>
            <th v-for="column in columns" :key="column.key" :style="{minWidth:`${column.width||100}px`}">{{column.label}}</th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="(row,index) in localRows" :key="row.drawing_row_key||row.rowIndex||index">
            <td class="erp-design-index">{{row.rowIndex??row.row_index??index+1}}</td>
            <td v-for="column in columns" :key="column.key" :title="displayCell(row,column)" :class="{'erp-design-calculation-cell':column.key==='calculation'}">
              <button v-if="column.kind==='preview'&&canPreviewDrawing(row)" class="erp-design-preview-button" type="button" @click="$emit('previewDrawing',row)">✓ 查看</button>
              <span v-else-if="column.kind==='preview'">{{displayCell(row,column)}}</span>
              <div
                v-else-if="column.key==='qty'"
                class="erp-design-drawing-field"
                :class="{'is-mismatch':quantityFieldMismatch(row),'is-loading':row._erp_reprice_loading}"
                :title="quantityFieldMismatch(row)?drawingFieldTip(row,'qty'):displayCell(row,column)"
                @dblclick.stop="applyDrawingQuantity(row)"
              >
                <input v-model.number="row.qty" type="number" min="1" step="1" @change="handleQuantityChange(row)">
              </div>
              <div
                v-else-if="isDimensionColumn(column.key)"
                class="erp-design-drawing-field"
                :class="{'is-mismatch':drawingFieldMismatch(row,dimensionField(column.key)),'is-placeholder':!showDimensionInput(row,column.key),'is-loading':row._erp_reprice_loading}"
                :title="drawingFieldMismatch(row,dimensionField(column.key))?drawingFieldTip(row,dimensionField(column.key)):displayCell(row,column)"
                @dblclick.stop="applyDrawingDimensionField(row)"
              >
                <input v-if="showDimensionInput(row,column.key)" v-model.number="row[editField(column.key)]" type="number" min="0" step="any" @change="handleDimensionChange(row)">
                <span v-else>-</span>
              </div>
              <span v-else-if="column.kind==='hardware-type'&&!isAttachedOrderRow(row)">{{displayCell(row,column)}}</span>
              <div
                v-else-if="(column.kind==='hardware-type'&&isAttachedOrderRow(row))||column.key==='shape'"
                class="erp-design-drawing-field"
                :class="{'is-mismatch':materialShapeMismatch(row),'is-loading':row._erp_reprice_loading}"
                :title="materialShapeMismatch(row)?drawingMaterialShapeTip(row):materialShape(row)"
                @dblclick.stop="applyDrawingDimensionField(row)"
              >
                <select :value="materialShape(row)" @change="handleShapeChange(row,$event)">
                  <option value="">未识别</option><option>方料</option><option>圆料</option><option>圆环料</option>
                </select>
              </div>
              <input
                v-else-if="column.key==='heat'"
                :value="heatTreatment(row)"
                list="erp-design-heat-treatment-options"
                placeholder="-"
                aria-label="热处理"
                @change="handleHeatTreatmentChange(row,$event)"
              >
              <select
                v-else-if="column.key==='aging'"
                :class="['erp-design-aging-select', erpDesignAgingTreatmentDisabled(row)?'is-aging-disabled':'is-aging-enabled']"
                :value="agingTreatment(row)"
                :disabled="erpDesignAgingTreatmentDisabled(row)"
                :title="erpDesignAgingTreatmentDisabled(row)?'时效处理仅支持有效的 45# 方料':'选择后将按 ERP 当前规则重新核价'"
                aria-label="时效处理"
                @change="handleAgingTreatmentChange(row,$event)"
              >
                <option v-for="item in ERP_DESIGN_AGING_TREATMENT_OPTIONS" :key="item" :value="item">{{item}}</option>
              </select>
              <input v-else-if="editableNumber(column.key)" v-model.number="row[editField(column.key)]" type="number" min="0" step="any" @change="updateRows">
              <span v-else-if="column.key==='code'&&row.item_code_auto_corrected" class="erp-design-code-cell">
                {{displayCell(row,column)}}<small>已自动规范</small>
              </span>
              <span v-else>{{displayCell(row,column)}}</span>
            </td>
          </tr>
        </tbody>
        <tfoot>
          <tr>
            <td>合计</td>
            <td v-for="column in columns" :key="column.key">{{summaryValue(column.key)}}</td>
          </tr>
        </tfoot>
      </table>
      <datalist id="erp-design-heat-treatment-options">
        <option v-for="item in ERP_DESIGN_HEAT_TREATMENT_OPTIONS" :key="item" :value="item"/>
      </datalist>
    </div>

    <div v-if="notice" class="erp-design-inline-notice">{{notice}}</div>
    <div v-if="preview.drawingProcessing" class="erp-design-processing">
      <span class="erp-design-processing-dot"></span>
      {{preview.drawingProcessingMessage||'图纸匹配与归档处理中，表格将自动刷新…'}}
    </div>
    <div v-else-if="error" class="erp-design-inline-error">
      <span>{{error}}</span><button type="button" @click="$emit('retry')">重试</button>
    </div>

    <section class="erp-design-accounting-rules">
      <strong>{{preview.sheetType==='hardware'?'自动核算条件':'技术要求'}}</strong>
      <template v-if="preview.sheetType==='hardware'">
        <ol><li v-for="item in hardwareRules" :key="item">{{item}}</li></ol>
        <div class="erp-design-rule-grid">
          <div><b>圆料判断依据</b><ul><li v-for="item in hardwareRoundRules" :key="item">{{item}}</li></ul></div>
          <div><b>根据加工说明判断</b><table><thead><tr><th>额外加工费</th><th>单价</th><th>说明</th></tr></thead><tbody><tr v-for="item in hardwareExtraFees" :key="item.name+item.unitPrice"><td>{{item.name}}</td><td>{{item.unitPrice}}</td><td>{{item.remark}}</td></tr><tr v-if="!hardwareExtraFees.length"><td colspan="3">暂无有效规则</td></tr></tbody></table></div>
        </div>
      </template>
      <template v-else>
        <ol v-if="technicalLines.length"><li v-for="item in technicalLines" :key="item">{{item}}</li></ol>
        <p v-else>价格、重量和加工费用均使用 ERP 当前配置与本次解析行计算；点击“重新核算价格”可刷新核算结果。</p>
        <table v-if="toleranceRows.length" class="erp-design-tolerance-table"><thead><tr><th>序号</th><th>规格</th><th>长度公差</th><th>厚度公差</th><th>对角公差</th></tr></thead><tbody><tr v-for="(item,index) in toleranceRows" :key="index"><td>{{item.seq??index+1}}</td><td>{{item.spec??'-'}}</td><td>{{item.length_tol??item.lengthTol??'-'}}</td><td>{{item.thick_tol??item.thickTol??'-'}}</td><td>{{item.diag_tol??item.diagTol??'-'}}</td></tr></tbody></table>
      </template>
    </section>
  </div>
</template>
