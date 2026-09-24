import { erpDesignCell, erpDesignColumns, type ErpDesignColumn, type ErpDesignRow } from './erpDesignPreview'

export type ErpDesignResultTable = {
  key: string
  title: string
  context?: string
  summary: string
  sourceNote: string
  rows: ErpDesignRow[]
  columns: ErpDesignColumn[]
  renderCell?: (row: ErpDesignRow, column: ErpDesignColumn) => string
  defer?: boolean
}

function record(value: unknown): Record<string, any> | null {
  return value && typeof value === 'object' && !Array.isArray(value) ? value as Record<string, any> : null
}

function sourceData(value: unknown): Record<string, any> {
  const source = record(value)
  return record(source?.data) ?? source ?? {}
}

function rows(value: unknown): ErpDesignRow[] {
  if (Array.isArray(value)) return value.filter(record) as ErpDesignRow[]
  const source = record(value)
  if (!source) return []
  for (const key of ['rows', 'records', 'items', 'details', 'differences', 'list', 'previewRows', 'preview_rows']) {
    const candidate = source[key]
    if (Array.isArray(candidate)) return candidate.filter(record) as ErpDesignRow[]
    if (record(candidate)) {
      const nested = rows(candidate)
      if (nested.length) return nested
    }
  }
  return []
}

function meta(value: unknown, count: number) {
  const source = sourceData(value)
  const total = Number(source.total ?? source.totalCount ?? count)
  const pageNum = Number(source.pageNum ?? source.page_num ?? 1) || 1
  const pageSize = Number(source.pageSize ?? source.page_size ?? Math.max(count, 1)) || Math.max(count, 1)
  const hasNext = source.hasNext ?? source.has_next ?? pageNum * pageSize < total
  return { total: Math.max(total, count), pageNum, pageSize, hasNext: Boolean(hasNext) }
}

const commonColumns: ErpDesignColumn[] = [
  { key: 'id', label: 'ID', fields: ['id'], width: 80 },
  { key: 'code', label: '编码/编号', fields: ['code', 'materialNo', 'material_no', 'partCode', 'part_code'], width: 170 },
  { key: 'name', label: '名称', fields: ['name', 'materialName', 'material_name', 'itemName', 'item_name'], width: 160 },
  { key: 'status', label: '状态', fields: ['status', 'statusLabel', 'historyStatus', 'history_status'], width: 130 },
  { key: 'created', label: '创建时间', fields: ['createdAt', 'created_at', 'createTime', 'create_time'], width: 180 },
]

const densityColumns: ErpDesignColumn[] = [
  { key: 'id', label: 'ID', fields: ['id'], width: 80 },
  { key: 'material', label: '材质牌号', fields: ['materialMark', 'material_mark', 'materialName', 'material_name'], width: 170 },
  { key: 'density', label: '密度', fields: ['density'], width: 110 },
  { key: 'unit', label: '单位', fields: ['unit'], width: 100 },
  { key: 'status', label: '状态', fields: ['status', 'statusLabel'], width: 130 },
]

const groupRuleColumns: ErpDesignColumn[] = [
  { key: 'id', label: 'ID', fields: ['id'], width: 80 },
  { key: 'keyword', label: '关键词', fields: ['keywordText', 'keyword_text'], width: 190 },
  { key: 'category', label: '分类', fields: ['categoryName', 'category_name', 'materialCategory', 'material_category'], width: 150 },
  { key: 'scope', label: '匹配范围', fields: ['matchScope', 'match_scope'], width: 140 },
  { key: 'prefix', label: '分组前缀', fields: ['groupPrefix', 'group_prefix'], width: 140 },
  { key: 'priority', label: '优先级', fields: ['priority'], width: 90 },
  { key: 'status', label: '状态', fields: ['status', 'statusLabel'], width: 120 },
]

const drawingVersionColumns: ErpDesignColumn[] = [
  { key: 'id', label: '版本ID', fields: ['id', 'drawingId', 'drawing_id'], width: 90 },
  { key: 'mold', label: '模具号', fields: ['moldCode', 'mold_code'], width: 150 },
  { key: 'part', label: '零件号', fields: ['partCode', 'part_code', 'drawingNo', 'drawing_no'], width: 180 },
  { key: 'version', label: '版本', fields: ['version', 'versionNo', 'version_no'], width: 80 },
  { key: 'oldVersion', label: '旧版本', fields: ['fromVersion', 'from_version', 'baseVersionNo', 'base_version_no'], width: 90 },
  { key: 'newVersion', label: '新版本', fields: ['toVersion', 'to_version', 'targetVersionNo', 'target_version_no'], width: 90 },
  { key: 'status', label: '发布状态', fields: ['historyStatus', 'history_status', 'status', 'statusLabel'], width: 130 },
  { key: 'oldDrawing', label: '旧图', fields: ['fromFile', 'from_file', 'fromVersionFileName'], width: 220 },
  { key: 'newDrawing', label: '新图', fields: ['toFile', 'to_file', 'toVersionFileName'], width: 220 },
  { key: 'difference', label: '差异字段', fields: ['differences', 'changeDetail', 'changeSummary', 'changeReason'], width: 320 },
  { key: 'submitted', label: '提交人', fields: ['submittedName', 'submitted_name'], width: 130 },
  { key: 'time', label: '提交时间', fields: ['submittedAt', 'submitted_at', 'createdAt', 'created_at'], width: 180 },
]

const idleColumns: ErpDesignColumn[] = [
  { key: 'detail', label: '设计明细ID', fields: ['detail_id', 'detailId'], width: 100 },
  { key: 'code', label: '设计物料', fields: ['item_code_full', 'itemCodeFull', 'materialNo', 'material_no'], width: 170 },
  { key: 'name', label: '设计名称', fields: ['item_name', 'itemName', 'materialName', 'material_name'], width: 150 },
  { key: 'scrap', label: '闲置料编号', fields: ['scrapNo', 'scrap_no', 'id'], width: 140 },
  { key: 'mark', label: '闲置料材质', fields: ['materialMark', 'material_mark'], width: 130 },
  { key: 'spec', label: '闲置料规格', fields: ['specification'], width: 180 },
  { key: 'size', label: '闲置料尺寸', fields: ['size', 'dimensions'], width: 180 },
  { key: 'available', label: '可用量', fields: ['availableQuantity', 'available_quantity'], width: 100 },
  { key: 'matchedQuantity', label: '匹配量', fields: ['matchedQuantity', 'matched_quantity', 'suggestedQuantity', 'suggested_quantity'], width: 100 },
  { key: 'usedQuantity', label: '使用量', fields: ['usedQuantity', 'used_quantity', 'used_quantity_after_deduction'], width: 100 },
  { key: 'releasedQuantity', label: '释放量', fields: ['releasedQuantity', 'released_quantity', 'releaseQuantity', 'release_quantity'], width: 100 },
  { key: 'remainingQuantity', label: '剩余量', fields: ['remainingQuantity', 'remaining_quantity', 'residualQuantity', 'residual_quantity'], width: 100 },
  { key: 'matched', label: '匹配状态', fields: ['matchStatus', 'match_status', 'scrapMatchAvailable', 'scrap_match_available'], width: 130 },
  { key: 'decision', label: '决策状态', fields: ['decisionStatus', 'decision_status', '_scrap_decision_status', 'scrapDecisionStatus'], width: 130 },
  { key: 'version', label: '明细版本', fields: ['detail_version', 'detailVersion'], width: 200 },
]

function cell(row: ErpDesignRow, column: ErpDesignColumn): string {
  for (const field of column.fields) {
    const value = row[field]
    if (value === undefined || value === null || value === '') continue
    if (typeof value === 'object') return JSON.stringify(value)
    return String(value)
  }
  return '—'
}

function tableFrom(source: unknown, key: string, title: string, columns: ErpDesignColumn[], context = ''): ErpDesignResultTable | null {
  const tableRows = rows(source)
  if (!tableRows.length && !source) return null
  const page = meta(source, tableRows.length)
  return {
    key,
    title,
    context,
    summary: `共 ${page.total} 条 · 第 ${page.pageNum} 页，已返回 ${tableRows.length} 条${page.hasNext ? ' · 还有下一页' : ''}`,
    sourceNote: '数据来自 management-system ERP，仅供只读展示',
    rows: tableRows,
    columns,
    defer: tableRows.length > 8,
  }
}

export function erpDesignMasterDataTablesFromRun(run: any): ErpDesignResultTable[] {
  if (!['SUCCEEDED', 'FAILED'].includes(String(run?.status ?? ''))) return []
  const result: ErpDesignResultTable[] = []
  for (const item of Array.isArray(run?.trace) ? run.trace : []) {
    const tool = String(item?.tool ?? '')
    if (!['erp_design_query_master_data', 'erp_design_query_densities', 'erp_design_query_group_rules'].includes(tool)) continue
    const payload = sourceData(item?.data)
    const resources = tool === 'erp_design_query_master_data'
      ? Object.entries(payload).filter(([key]) => ['densities', 'group_rules'].includes(key))
      : [[tool.endsWith('densities') ? 'densities' : 'group_rules', payload]] as [string, any][]
    for (const [resource, value] of resources) {
      const table = tableFrom(value, `${tool}:${resource}`, resource === 'densities' ? 'ERP 材质密度表' : 'ERP 设计分组规则表', resource === 'densities' ? densityColumns : groupRuleColumns)
      if (table) result.push(table)
    }
  }
  return result
}

export function erpDesignProcessingTablesFromRun(run: any): ErpDesignResultTable[] {
  if (!['SUCCEEDED', 'FAILED'].includes(String(run?.status ?? ''))) return []
  const result: ErpDesignResultTable[] = []
  for (const item of Array.isArray(run?.trace) ? run.trace : []) {
    const payload = sourceData(item?.data)
    if (!Array.isArray(payload.processingDiff)) continue
    const changes = payload.processingDiff.filter((row: unknown) => record(row)) as ErpDesignRow[]
    const previewRows = rows(payload.previewRows)
    const fallbackRows = new Map<string, ErpDesignRow>()
    for (const [index, change] of changes.entries()) {
      const rowIndex = change.rowIndex ?? change.row_index ?? index + 1
      const key = String(rowIndex)
      const row = fallbackRows.get(key) ?? {
        rowIndex,
        item_code_full: change.item_code_full ?? change.itemCodeFull,
        item_name: change.item_name ?? change.itemName,
      }
      const fieldKey = ({
        '材质': 'material_mark', '规格': 'spec_raw', '料型': 'material_shape', '长': 'length', '宽': 'width',
        '厚/高': 'height', '外径': 'outer_diameter', '内径': 'inner_diameter', '数量': 'qty',
        '采购数量': 'purchase_quantity', '单价': 'unit_price', '核算单价': 'accounting_unit_price',
        '核算金额': 'material_amount', '总价': 'total_price', '公差档位': 'tolerance_tier',
        '长度允许范围': 'length_allowed_range', '宽度允许范围': 'width_allowed_range',
        '厚度允许范围': 'thickness_allowed_range', '对角公差': 'diagonal_tolerance',
      } as Record<string, string>)[String(change.field)] ?? String(change.field)
      row[fieldKey] = change.after
      const reasons = String(row.calculation_process ?? '').split('\n').filter(Boolean)
      if (change.reason && !reasons.includes(String(change.reason))) reasons.push(String(change.reason))
      row.calculation_process = reasons.join('\n')
      fallbackRows.set(key, row)
    }
    const rowsValue = previewRows.length ? previewRows : [...fallbackRows.values()]
    const sheetType = String(payload.sheetType ?? payload.sheet_type ?? 'steel')
    result.push({
      key: `processing:${item?.id ?? item?.call_id ?? result.length}`,
      title: 'ERP 处理后的设计明细',
      context: String(payload.moldCode ?? payload.mold_code ?? ''),
      summary: `共 ${rowsValue.length} 条明细 · ${changes.length} 项参数变化`,
      sourceNote: '处理结果来自 ERP 当前设计明细',
      rows: rowsValue,
      columns: erpDesignColumns(sheetType),
      renderCell: (row, column) => erpDesignCell(row, column, payload.techRequirements ?? payload.tech_requirements ?? null),
      defer: false,
    })
  }
  return result
}

export function erpDesignDrawingVersionTablesFromRun(run: any): ErpDesignResultTable[] {
  if (!['SUCCEEDED', 'FAILED'].includes(String(run?.status ?? ''))) return []
  const result: ErpDesignResultTable[] = []
  for (const item of Array.isArray(run?.trace) ? run.trace : []) {
    const tool = String(item?.tool ?? '')
    if (!['erp_design_query_drawing_versions', 'erp_design_compare_drawing_versions'].includes(tool)) continue
    const payload = sourceData(item?.data)
    const source = tool.endsWith('compare_drawing_versions') && (record(payload.fromVersion) || record(payload.toVersion))
      ? {
          rows: [{
            moldCode: payload.moldCode,
            partCode: payload.partCode,
            fromVersion: record(payload.fromVersion)?.versionNo ?? record(payload.fromVersion)?.versionId,
            toVersion: record(payload.toVersion)?.versionNo ?? record(payload.toVersion)?.versionId,
            fromFile: record(payload.fromVersion)?.fileName,
            toFile: record(payload.toVersion)?.fileName,
            changeSummary: payload.changeSummary,
            changeDetail: payload.changeDetail,
            changeReason: payload.changeReason,
            submittedName: payload.submittedName,
            decidedAt: payload.decidedAt,
          }],
          total: 1,
        }
      : item?.data
    const table = tableFrom(source, tool, tool.endsWith('compare_drawing_versions') ? 'ERP 图纸版本差异表' : 'ERP 图纸版本表', drawingVersionColumns)
    if (table) result.push(table)
  }
  return result
}

export function erpDesignIdleMaterialTablesFromRun(run: any): ErpDesignResultTable[] {
  if (!['SUCCEEDED', 'FAILED'].includes(String(run?.status ?? ''))) return []
  const result: ErpDesignResultTable[] = []
  for (const item of Array.isArray(run?.trace) ? run.trace : []) {
    const tool = String(item?.tool ?? '')
    if (!['erp_design_query_idle_material', 'erp_design_save_scrap_decision', 'erp_design_release_scrap_decision'].includes(tool)) continue
    const payload = sourceData(item?.data)
    const source = tool === 'erp_design_query_idle_material'
      ? item?.data
      : { rows: [payload], total: 1 }
    const table = tableFrom(source, `idle-material:${tool}`, tool === 'erp_design_query_idle_material' ? 'ERP 闲置料匹配表' : 'ERP 闲置料决策明细表', idleColumns)
    if (table) result.push(table)
  }
  return result
}

export function erpDesignResultCell(row: ErpDesignRow, column: ErpDesignColumn): string {
  return cell(row, column)
}
