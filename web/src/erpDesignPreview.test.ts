import { describe, expect, it } from 'vitest'
import {
  calculateErpDesignSteelTolerance,
  erpDesignCell,
  erpDesignColumns,
  erpDesignDrawingColumns,
  erpDesignDrawingsFromRun,
  erpDesignDrawingsFromTool,
  erpDesignAgingTreatmentDisabled,
  erpDesignAgingTreatmentSupported,
  erpDesignPreviewUrl,
  erpDesignParameterCell,
  erpDesignParameterColumns,
  erpDesignReadOnlyTableNeedsDisclosure,
  erpDesignParametersFromRun,
  erpDesignParametersFromTool,
  erpDesignUploadStatusLabel,
  erpDesignSessionFromRun,
  erpDesignSessionFromTool,
  erpDesignTechnicalRequirementsFromRun,
  erpDesignTechnicalRequirementsFromTool,
  erpDesignToleranceCell,
  erpDesignToleranceColumns,
  erpDesignToleranceFromRun,
  erpDesignToleranceFromTool,
  normalizeErpDesignTreatments,
  normalizeErpDesignImportReceipt,
  normalizeErpDesignPreview,
  normalizeErpDesignParameterResult,
  normalizeErpDesignTechnicalRequirements,
  parseErpDesignDueDateCommand,
} from './erpDesignPreview'

const tool = {
  type: 'tool',
  tool: 'erp_design_parse_new_mold_upload',
  data: {
    sessionId: 268,
    sheetType: 'steel',
    moldCode: 'M250238-P4',
    fileName: 'M250238-P4料单.XLSX',
    previewRows: [{ rowIndex: 1 }, { rowIndex: 2 }],
  },
}

describe('ERP design preview', () => {
  it('parses explicit and relative due-date changes, asks for confirmation on a bare date, and rejects past dates', () => {
    const today = new Date(2026, 8, 23)
    expect(parseErpDesignDueDateCommand('帮我把交期改为2026-10-15', today)).toMatchObject({
      kind: 'set', date: '2026-10-15',
    })
    expect(parseErpDesignDueDateCommand('交期改为三天之后', today)).toMatchObject({
      kind: 'set', date: '2026-09-26',
    })
    expect(parseErpDesignDueDateCommand('10月15号', today)).toMatchObject({
      kind: 'date_only', date: '2026-10-15',
    })
    expect(parseErpDesignDueDateCommand('交期改为2026-09-22', today)).toMatchObject({
      kind: 'invalid', date: '2026-09-22',
    })
  })

  it('labels an upload-ready result with its actual sheet type', () => {
    expect(erpDesignUploadStatusLabel('hardware')).toBe('五金清单上传已就绪')
    expect(erpDesignUploadStatusLabel('steel')).toBe('钢料清单上传已就绪')
    expect(erpDesignUploadStatusLabel('hardware', true)).toBe('五金清单已成功导入')
  })

  it('extracts the ERP upload session and initial rows from a tool result', () => {
    expect(erpDesignSessionFromTool(tool)).toMatchObject({
      sessionId: 268,
      sheetType: 'steel',
      moldCode: 'M250238-P4',
      fileName: 'M250238-P4料单.XLSX',
      rowCount: 2,
      previewRows: [{ rowIndex: 1 }, { rowIndex: 2 }],
    })
  })

  it('extracts the independent modify-mold ERP upload session', () => {
    expect(erpDesignSessionFromTool({
      ...tool,
      tool: 'erp_design_parse_modify_mold_upload',
      data: { ...tool.data, designOrderType: 'repair_other' },
    })).toMatchObject({
      sessionId: 268,
      designOrderType: 'repair_other',
    })
  })

  it('publishes the ERP session only when a run succeeds', () => {
    const current = { id: 'run-1', status: 'SUCCEEDED', trace: [tool] }
    const failed = { id: 'run-2', status: 'FAILED', trace: [tool] }
    const cancelled = { id: 'run-3', status: 'CANCELLED', trace: [tool] }
    expect(erpDesignSessionFromRun(current)?.sessionId).toBe(268)
    expect(erpDesignSessionFromRun(failed)).toBeNull()
    expect(erpDesignSessionFromRun(cancelled)).toBeNull()
  })

  it('keeps repair_other type when a later ERP status omits the type', () => {
    const current = {
      id: 'run-modify-1',
      status: 'SUCCEEDED',
      trace: [
        { ...tool, tool: 'erp_design_parse_modify_mold_upload', data: { ...tool.data, designOrderType: 'repair_other' } },
        { type: 'tool', tool: 'erp_design_get_drawing_status', data: { ...tool.data, designOrderType: undefined } },
      ],
    }
    expect(erpDesignSessionFromRun(current)?.designOrderType).toBe('repair_other')
  })

  it('keeps tolerance evidence separate from the editable upload preview', () => {
    const toleranceTool = {
      type: 'tool',
      tool: 'erp_design_evaluate_tolerances',
      data: {
        sessionId: 316,
        sheetType: 'steel',
        moldCode: 'M250238-P4',
        previewRows: [{
          rowIndex: 2,
          item_code_full: 'DIE-01',
          item_name: '下模板',
          material_mark: 'CR12MOV',
          spec_raw: '706.526L*705.79W*70.46T',
          processing_technology: '-',
          toleranceTier: '500-800(含)',
          lengthAllowedRange: '706.826～707.326',
          widthAllowedRange: '706.09～706.59',
          thicknessAllowedRange: '71.06～71.36',
          diagonalTolerance: '0~0.5',
        }],
        toleranceEvaluation: { evaluatedCount: 1, rowCount: 1 },
      },
    }
    const run = { id: 'run-tolerance', status: 'SUCCEEDED', trace: [toleranceTool] }
    const preview = erpDesignToleranceFromTool(toleranceTool)!
    const columns = erpDesignToleranceColumns()

    expect(erpDesignSessionFromTool(toleranceTool)).toBeNull()
    expect(erpDesignSessionFromRun(run)).toBeNull()
    expect(erpDesignToleranceFromRun(run)).toMatchObject({ sessionId: 316, rowCount: 1 })
    expect(erpDesignToleranceFromRun({ ...run, status: 'FAILED' })).toMatchObject({
      sessionId: 316, rowCount: 1,
    })
    expect(columns.map(column => column.label)).toEqual([
      '编码', '名称', '材质', '规格', '加工工艺', '公差档位', '长度允许范围',
      '宽度允许范围', '厚度允许范围', '对角公差', '备注',
    ])
    expect(columns.map(column => column.label)).not.toEqual(expect.arrayContaining(['核算单价', '总价', '确认导入']))
    expect(erpDesignToleranceCell(preview.previewRows[0], columns[5])).toBe('500-800(含)')
  })

  it('extracts fixed ERP technical requirements as separate lines and table rows', () => {
    const requirementsTool = {
      type: 'tool',
      tool: 'erp_design_get_technical_requirements',
      data: {
        displayMode: 'design_technical_requirements',
        techRequirements: {
          requirements: [
            '\n1.铣六面平面度0.2以内；\n2.注明倒角的四周按注明数倒角；',
          ],
          tolerance_table: [{
            seq: 1,
            spec: '500(含)以下',
            length_tol: '+0.3~+0.6',
            thick_tol: '+0.3~+0.5',
            diag_tol: '0~0.5',
          }],
        },
      },
    }

    expect(normalizeErpDesignTechnicalRequirements({ data: requirementsTool.data })).toEqual({
      requirements: ['铣六面平面度0.2以内；', '注明倒角的四周按注明数倒角；'],
      toleranceRows: [{
        seq: 1,
        spec: '500(含)以下',
        lengthTolerance: '+0.3~+0.6',
        thicknessTolerance: '+0.3~+0.5',
        diagonalTolerance: '0~0.5',
      }],
    })
    expect(erpDesignTechnicalRequirementsFromTool(requirementsTool)?.requirements).toHaveLength(2)
    expect(erpDesignTechnicalRequirementsFromRun({
      status: 'SUCCEEDED',
      trace: [requirementsTool],
    })?.toleranceRows).toHaveLength(1)
    expect(erpDesignTechnicalRequirementsFromRun({
      status: 'FAILED',
      trace: [requirementsTool],
    })?.requirements).toHaveLength(2)
    expect(erpDesignTechnicalRequirementsFromRun({
      status: 'RUNNING',
      trace: [requirementsTool],
    })).toBeNull()
  })

  it('shows a read-only parameter table only when more than eight ERP rows match', () => {
    const parameterTool = {
      type: 'tool',
      tool: 'erp_design_query_upload_parameters',
      data: {
        sessionId: 417,
        sheetType: 'steel',
        moldCode: 'M250238-P4',
        displayMode: 'design_parameters',
        tableThreshold: 8,
        renderAsTable: true,
        matchedCount: 9,
        previewRows: Array.from({ length: 9 }, (_, index) => ({
          rowIndex: index + 1,
          item_code_full: `DIE-${index + 1}`,
          item_name: '下模板',
          material_mark: 'CR12MOV',
          material_shape: '方料',
          purchase_quantity: 2,
          length: 706.526,
          width: 705.79,
          height: 70.46,
        })),
      },
    }
    const result = erpDesignParametersFromTool(parameterTool)!
    const columns = erpDesignParameterColumns()

    expect(erpDesignSessionFromTool(parameterTool)).toBeNull()
    expect(erpDesignParametersFromRun({ status: 'SUCCEEDED', trace: [parameterTool] })).toMatchObject({
      sessionId: 417, matchedCount: 9, rowCount: 9, renderAsTable: true,
    })
    expect(erpDesignParametersFromRun({ status: 'FAILED', trace: [parameterTool] })).toMatchObject({
      sessionId: 417, matchedCount: 9, rowCount: 9, renderAsTable: true,
    })
    expect(columns.map(column => column.label)).toEqual([
      '编码', '名称', '材质', '规格', '料型', '采购数量', '长(L)', '宽(W)', '厚(T)',
      '外径(Φ)', '内径(Φ)', '图纸匹配状态', '加工工艺', '备注',
    ])
    expect(columns.map(column => column.label)).not.toEqual(expect.arrayContaining([
      '核算单价', '核算金额', '总价', '计算过程', '确认导入',
    ]))
    expect(erpDesignParameterCell(result.previewRows[0], columns[6])).toBe('706.5260')

    const short = normalizeErpDesignParameterResult({
      ...parameterTool.data,
      matchedCount: 8,
      renderAsTable: false,
      previewRows: parameterTool.data.previewRows.slice(0, 8),
    })
    expect(short).toMatchObject({ matchedCount: 8, rowCount: 8, renderAsTable: false })
    expect(erpDesignReadOnlyTableNeedsDisclosure(8)).toBe(false)
    expect(erpDesignReadOnlyTableNeedsDisclosure(9)).toBe(true)
    expect(erpDesignReadOnlyTableNeedsDisclosure(10, 12)).toBe(false)
  })

  it('collects drawing previews in their own table across tool calls and sessions', () => {
    const drawing = (sessionId: number, id: number, fileName = `${id}.dxf`) => ({
      tool: 'erp_design_preview_drawing',
      data: {
        displayMode: 'design_drawings', sessionId, moldCode: 'M250238-P4',
        previewRows: [{ drawing_resource_id: id, drawing_file_name: fileName }],
      },
    })
    const trace = [drawing(271, 1), drawing(271, 2), drawing(271, 1, 'latest.dxf'), drawing(272, 1)]
    const results = erpDesignDrawingsFromRun({ status: 'SUCCEEDED', trace })
    expect(results).toHaveLength(2)
    expect(results[0].previewRows.map(row => row.drawing_file_name)).toEqual(['latest.dxf', '2.dxf'])
    expect(erpDesignDrawingsFromRun({ status: 'FAILED', trace })).toEqual(results)
    expect(erpDesignDrawingsFromRun({ status: 'RUNNING', trace })).toEqual([])
    expect(erpDesignDrawingsFromRun({ status: 'CANCELLED', trace })).toEqual([])
    expect(erpDesignSessionFromTool(trace[0])).toBeNull()
    expect(erpDesignDrawingColumns().map(column => column.label)).toEqual(['编码', '名称', '图纸文件', '预览'])
    expect(erpDesignDrawingsFromTool(drawing(0, 1))).toBeNull()
    expect(erpDesignDrawingsFromTool(drawing(271, 0))?.previewRows).toEqual([])
    expect(erpDesignDrawingsFromTool({ tool: 'erp_design_preview_drawing', data: { file: {} } })).toBeNull()
  })

  it('uses ERP standard hardware rows and paths without needing an upload session', () => {
    const row = { standardCode: 'R-BZ-001', fileName: '标准件.dxf', relativePath: 'R-BZ-001/标准件.dxf', previewUrl: '/design/standard-hardware/preview?relativePath=R-BZ-001%2F标准件.dxf' }
    const item = { tool: 'erp_design_query_standard_hardware', data: { data: { rows: [row], total: 23 } } }
    const result = erpDesignDrawingsFromTool(item)!
    expect(result).toMatchObject({ source: 'standard_hardware', totalCount: 23, previewRows: [row] })
    expect(result.sessionId).toBeUndefined()
    expect(erpDesignDrawingColumns(result.source).map(column => column.label)).toEqual(['标准件编号', '图纸文件', '预览'])
    expect(erpDesignDrawingsFromRun({ status: 'SUCCEEDED', trace: [item, item] })[0].previewRows).toHaveLength(1)
    expect(erpDesignDrawingsFromTool({ ...item, data: { rows: [], total: 0 } })?.previewRows).toEqual([])
  })

  it('merges a proxied ERP status result into the existing session', () => {
    const initial = erpDesignSessionFromTool(tool)!
    const result = normalizeErpDesignPreview({
      data: {
        sessionId: 268,
        sheetType: 'hardware',
        drawingProcessing: false,
        totalQuantity: 51,
        previewRows: [{ item_code_full: 'M250238-P4-A01', qty: 12 }],
      },
    }, initial)
    expect(result).toMatchObject({
      sheetType: 'hardware',
      rowCount: 1,
      totalQuantity: 51,
      drawingProcessing: false,
    })
  })

  it('normalizes a persisted import receipt used to disable the conversation order', () => {
    expect(normalizeErpDesignImportReceipt({
      session_id: 268,
      request_no: 'REQ-20260918-01',
      message: '导入成功',
      imported_at: '2026-09-18T10:30:00+08:00',
    })).toEqual({
      sessionId: 268,
      requestNo: 'REQ-20260918-01',
      message: '导入成功',
      importedAt: '2026-09-18T10:30:00+08:00',
    })
  })

  it('uses the ERP hardware columns and formats drawing dimensions', () => {
    const columns = erpDesignColumns('hardware')
    expect(columns.map(column => column.label).slice(0, 12)).toEqual([
      '编码', '名称', '规格', '品牌', '外直径(∅)', '内直径(∅)', '长(L)', '宽(W)', '厚(T)', '类型', '喷漆要求', '请购数量',
    ])
    expect(columns.map(column => column.label)).toEqual(expect.arrayContaining([
      '闲置匹配', '采购数量', '预览', '材质', '有效已审批价', '核算单价', '核算金额', '总价', '计算过程',
    ]))
    expect(erpDesignCell({ outer_diameter: 18 }, columns[4])).toBe('18.0000')
    expect(erpDesignCell({ paint_required: false }, columns[10])).toBe('-')
  })

  it('keeps delivery, calculation rules, and drawing identifiers in the preview session', () => {
    const result = normalizeErpDesignPreview({
      sessionId: 268,
      expectedDate: '2026-09-21',
      additionalProcessingFeeRules: [{ processKeyword: '精磨', unitPrice: 12 }],
      techRequirements: { requirements: ['长度公差按表执行'] },
      previewRows: [{ drawing_resource_id: 991 }],
    })
    expect(result).toMatchObject({
      expectedDate: '2026-09-21',
      additionalProcessingFeeRules: [{ processKeyword: '精磨', unitPrice: 12 }],
      techRequirements: { requirements: ['长度公差按表执行'] },
      previewRows: [{ drawing_resource_id: 991 }],
    })
  })

  it('matches ERP treatment choices and only enables aging for eligible 45# square stock', () => {
    const eligible = normalizeErpDesignTreatments({ material_mark: '45#', material_shape: '方料', post_treatment: '是' })
    const unsupported = normalizeErpDesignTreatments({ material_mark: 'CR12MOV', material_shape: '方料', post_treatment: '是' })
    expect(erpDesignAgingTreatmentSupported(eligible)).toBe(true)
    expect(erpDesignAgingTreatmentDisabled(eligible)).toBe(false)
    expect(eligible.post_treatment).toBe('是')
    expect(erpDesignAgingTreatmentSupported(unsupported)).toBe(false)
    expect(erpDesignAgingTreatmentDisabled(unsupported)).toBe(true)
    expect(unsupported.post_treatment).toBe('否')
  })

  it('uses the ERP tolerance table to render square-stock allowed ranges', () => {
    const requirements = { tolerance_table: [
      { seq: 1, spec: '500(含)以下', length_tol: '+0.3~+0.6', thick_tol: '+0.3~+0.5', diag_tol: '0~0.5' },
      { seq: 2, spec: '500-800(含)', length_tol: '+0.3~+0.8', thick_tol: '+0.6~+0.9', diag_tol: '0~0.5' },
      { seq: 3, spec: '800以上', length_tol: '+0.3~+1.0', thick_tol: '+0.7~+1.0', diag_tol: '0~0.5' },
    ] }
    const row = { material_shape: '方料', length: 706.526, width: 705.79, height: 70.46 }
    expect(calculateErpDesignSteelTolerance(row, requirements)).toEqual({
      tier: '500-800(含)',
      lengthRange: '706.826～707.326',
      widthRange: '706.09～706.59',
      thicknessRange: '71.06～71.36',
      diagonalTolerance: '0~0.5',
    })
    const columns = erpDesignColumns('steel')
    const tier = columns.find(column => column.key === 'toleranceTier')!
    const length = columns.find(column => column.key === 'lengthRange')!
    expect(erpDesignCell(row, tier, requirements)).toBe('500-800(含)')
    expect(erpDesignCell(row, length, requirements)).toBe('706.826～707.326')
    expect(calculateErpDesignSteelTolerance({ material_shape: '圆料' }, requirements).tier).toBe('-')
  })

  it('builds the existing ERP page URL with the session context', () => {
    const session = erpDesignSessionFromTool(tool)!
    const url = new URL(erpDesignPreviewUrl(session))
    expect(url.origin).toBe('http://127.0.0.1:18080')
    expect(url.pathname).toBe('/design/upload/index')
    expect(url.searchParams.get('sessionId')).toBe('268')
    expect(url.searchParams.get('sheetType')).toBe('steel')
    expect(url.searchParams.get('embedded')).toBe('1')
  })
})
