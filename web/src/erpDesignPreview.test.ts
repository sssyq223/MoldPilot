import { describe, expect, it } from 'vitest'
import {
  calculateErpDesignSteelTolerance,
  erpDesignCell,
  erpDesignColumns,
  erpDesignAgingTreatmentDisabled,
  erpDesignAgingTreatmentSupported,
  erpDesignPreviewUrl,
  erpDesignSessionFromRun,
  erpDesignSessionFromTool,
  normalizeErpDesignTreatments,
  normalizeErpDesignImportReceipt,
  normalizeErpDesignPreview,
  shouldOpenErpDesignPreview,
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

  it('opens only when a live run becomes terminal', () => {
    const previous = { id: 'run-1', status: 'RUNNING', trace: [] }
    const current = { id: 'run-1', status: 'SUCCEEDED', trace: [tool] }
    expect(shouldOpenErpDesignPreview(previous, current)).toBe(true)
    expect(shouldOpenErpDesignPreview(undefined, current)).toBe(false)
    expect(erpDesignSessionFromRun(current)?.sessionId).toBe(268)
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
