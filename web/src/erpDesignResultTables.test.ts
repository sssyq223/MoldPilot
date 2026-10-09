import { describe, expect, it } from 'vitest'
import {
  erpDesignDrawingVersionTablesFromRun,
  erpDesignIdleMaterialTablesFromRun,
  erpDesignMasterDataTablesFromRun,
  erpDesignMoldRepairTablesFromRun,
  erpDesignProcessingTablesFromRun,
} from './erpDesignResultTables'

describe('ERP design result tables', () => {
  it('shows ERP post-processing details in the design table format, one row per source row', () => {
    const tables = erpDesignProcessingTablesFromRun({
      status: 'SUCCEEDED',
      trace: [{ tool: 'erp_design_reprice_rows', data: {
        processingDiff: [
          { rowIndex: 3, field: '长', before: 100, after: 101, reason: 'ERP 重新核算' },
          { rowIndex: 3, field: '宽', before: 20, after: 21, reason: 'ERP 重新核算' },
        ],
      } }],
    })
    expect(tables[0].columns.map(column => column.label)).toEqual(expect.arrayContaining(['总价', '计算过程', '加工工艺', '公差档位']))
    expect(tables[0].columns.map(column => column.label)).not.toContain('处理前')
    expect(tables[0].rows).toHaveLength(1)
    expect(tables[0].rows[0]).toMatchObject({ rowIndex: 3, length: 101, width: 21, calculation_process: 'ERP 重新核算' })
    expect(tables[0].defer).toBe(false)
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

  it('expands the ERP mold-repair receipt into a read-only change detail table', () => {
    const tables = erpDesignMoldRepairTablesFromRun({
      status: 'SUCCEEDED',
      trace: [{ tool: 'erp_design_upload_mold_repair_drawing', data: {
        factoryModel: 'M250238-P4',
        exceptions: [{
          exceptionId: 42,
          partNo: 'P01',
          changeType: '修改',
          receiver: { businessCategory: 'hardware', receiverName: '五金供应商' },
          inbound: { isInbound: false, checkStatus: 'ok' },
          deliveryStatus: 'waiting_approval',
          quantityRecognition: { oldQty: 1, newQty: 2, unit: 'PCS' },
          quantityComparison: { orderedQty: 1, shortageQty: 1 },
        }],
      } }],
    })
    expect(tables).toHaveLength(1)
    expect(tables[0].title).toBe('ERP 修模/改模变更明细')
    expect(tables[0].sourceNote).toContain('management-system ERP')
    expect(tables[0].columns.map(column => column.label)).toEqual(expect.arrayContaining(['变更类型', '旧图数量', '新图数量', '已下单', '缺少', '业务来源', '接收方', '入库判断', '处理方式']))
    expect(tables[0].rows[0]).toMatchObject({
      changeType: '修改', oldDrawingCount: 1, newDrawingCount: 2,
      orderedQty: 1, shortageQty: 1, businessSource: '五金', receiver: '五金供应商',
      inbound: '未入库', processingMethod: '可审批', unit: 'PCS',
    })

    const unresolved = erpDesignMoldRepairTablesFromRun({
      status: 'SUCCEEDED',
      trace: [{ tool: 'erp_design_upload_mold_repair_drawing', data: {
        exceptions: [{ exceptionId: 43, changeType: '删除', receiver: { businessCategory: 'unknown', receiverType: null } }],
      } }],
    })
    expect(unresolved[0].rows[0]).toMatchObject({ businessSource: '', receiver: '' })
  })

  it('reads the authoritative receipt attached to a confirmed proposal resolution', () => {
    const tables = erpDesignMoldRepairTablesFromRun({
      status: 'FAILED',
      trace: [{
        type: 'proposal_resolution',
        receipt: {
          tool: 'erp_design_upload_mold_repair_drawing',
          result: { data: { exceptions: [{ exceptionId: 7, partNo: 'P07', changeType: '删除', quantityRecognition: { oldQty: 1, newQty: 1 } }] } },
        },
      }],
    })
    expect(tables).toHaveLength(1)
    expect(tables[0].rows[0]).toMatchObject({ exceptionId: 7, partNo: 'P07', changeType: '删除', oldDrawingCount: 1, newDrawingCount: 1 })
  })

  it('does not duplicate a part when ERP returns exceptions and parsed items together', () => {
    const tables = erpDesignMoldRepairTablesFromRun({
      status: 'SUCCEEDED',
      trace: [{ tool: 'erp_design_upload_mold_repair_drawing', data: {
        exceptions: [{ exceptionId: 27, partNo: 'U2-06', changeType: '删除' }],
        items: [{ part_no: 'U2-06', change_type: '删除' }],
      } }],
    })
    expect(tables[0].rows).toHaveLength(1)
    expect(tables[0].rows[0]).toMatchObject({ exceptionId: 27, partNo: 'U2-06' })
  })
})
