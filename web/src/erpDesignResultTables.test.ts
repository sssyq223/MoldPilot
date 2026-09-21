import { describe, expect, it } from 'vitest'
import {
  erpDesignDrawingVersionTablesFromRun,
  erpDesignIdleMaterialTablesFromRun,
  erpDesignMasterDataTablesFromRun,
  erpDesignProcessingTablesFromRun,
} from './erpDesignResultTables'

describe('ERP design result tables', () => {
  it('renders processing before/after/reason rows', () => {
    const tables = erpDesignProcessingTablesFromRun({
      status: 'SUCCEEDED',
      trace: [{ tool: 'erp_design_reprice_rows', data: {
        processingDiff: [{ field: '长', before: 100, after: 101, reason: 'ERP 重新核算' }],
      } }],
    })
    expect(tables[0].columns.map(column => column.label)).toEqual(['处理字段', '处理前', '处理后', '原因'])
    expect(tables[0].rows[0]).toMatchObject({ before: 100, after: 101, reason: 'ERP 重新核算' })
  })

  it('turns ERP drawing comparison payload into one old/new table row', () => {
    const tables = erpDesignDrawingVersionTablesFromRun({
      status: 'SUCCEEDED',
      trace: [{ tool: 'erp_design_compare_drawing_versions', data: {
        moldCode: 'M250238-P4', partCode: 'DIE-01',
        fromVersion: { versionNo: 1, fileName: 'old.dxf' },
        toVersion: { versionNo: 2, fileName: 'new.dxf' },
        changeDetail: '长尺寸变更',
      } }],
    })
    expect(tables).toHaveLength(1)
    expect(tables[0].rows[0]).toMatchObject({ fromVersion: 1, toVersion: 2, fromFile: 'old.dxf', toFile: 'new.dxf' })
    expect(tables[0].columns.map(column => column.label)).toEqual(expect.arrayContaining(['零件号', '旧版本', '新版本', '旧图', '新图', '差异字段']))
  })

  it('uses the same table adapter for ERP idle matching and saved decisions', () => {
    const tables = erpDesignIdleMaterialTablesFromRun({
      status: 'SUCCEEDED',
      trace: [
        { tool: 'erp_design_query_idle_material', data: { rows: [{ id: 31, availableQuantity: 18, suggestedQuantity: 2 }], total: 1 } },
        { tool: 'erp_design_save_scrap_decision', data: { detailId: 77, decision: 'partial', usedQuantity: 2 } },
      ],
    })
    expect(tables.map(table => table.title)).toEqual(['ERP 闲置料匹配表', 'ERP 闲置料决策明细表'])
    expect(tables[1].rows[0]).toMatchObject({ detailId: 77, decision: 'partial', usedQuantity: 2 })
    expect(tables[0].columns.map(column => column.label)).toEqual(expect.arrayContaining(['可用量', '匹配量', '使用量', '释放量', '剩余量', '决策状态']))
  })

  it('keeps density and group rules in the same ERP catalogue table style', () => {
    const tables = erpDesignMasterDataTablesFromRun({
      status: 'SUCCEEDED',
      trace: [{ tool: 'erp_design_query_master_data', data: {
        densities: { rows: [{ materialMark: 'CR12MOV', density: 7.8 }], total: 1 },
        group_rules: { rows: [{ keywordText: '导柱', status: 'active' }], total: 1 },
      } }],
    })
    expect(tables.map(table => table.title)).toEqual(['ERP 材质密度表', 'ERP 设计分组规则表'])
    expect(tables.every(table => table.sourceNote.includes('management-system ERP'))).toBe(true)
  })
})
