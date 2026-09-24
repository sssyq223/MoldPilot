import { describe, expect, it } from 'vitest'
import { erpOutsourceResultTablesFromRun } from './erpOutsourceResultTables'

describe('ERP outsource result tables', () => {
  it('renders one follow-up board without project numbers or extra catalogues', () => {
    const tables = erpOutsourceResultTablesFromRun({
      status: 'SUCCEEDED',
      trace: [
        {
          type: 'tool',
          tool: 'query_erp_outsource_followup_board',
          data: {
            scope: '模具 M260063',
            items: [
              {
                stationLabel: '待采购填报价',
                outsourceTypeLabel: '零件委外',
                moldNo: 'M260063-P1',
                partDetails: 'B1-01 下托板',
                pendingQuoteSuppliers: '',
              },
              {
                stationLabel: '待接单',
                outsourceTypeLabel: '工序委外',
                moldNo: 'M260063-P5',
                partDetails: 'DIE-BL1',
              },
            ],
          },
        },
        {
          type: 'tool',
          tool: 'query_fulfillment_gap',
          data: { scope: '模具 M260063', items: [] },
        },
      ],
    })
    expect(tables).toHaveLength(1)
    expect(tables[0].title).toBe('ERP 委外待办')
    expect(tables[0].rows).toHaveLength(2)
    expect(tables[0].columns.map((column) => column.label)).toEqual([
      '进度', '委外类型', '订单号', '模具号', '批次号', '零件明细', '核算价', '我方报价', '接单上限', '加工商报价', '成交价', '待报价加工商',
    ])
    expect(tables[0].columns.some((column) => /项目|工单/.test(column.label))).toBe(false)
  })

  it('keeps one board table when the same tool returns an empty pass then rows', () => {
    const tables = erpOutsourceResultTablesFromRun({
      status: 'SUCCEEDED',
      trace: [
        { type: 'tool', id: 'a', tool: 'query_erp_outsource_followup_board', data: { items: [] } },
        {
          type: 'tool',
          id: 'b',
          tool: 'query_erp_outsource_followup_board',
          data: { items: [{ stationLabel: '待接单', moldNo: 'M260063-P1' }] },
        },
      ],
    })
    expect(tables).toHaveLength(1)
    expect(tables[0].rows).toHaveLength(1)
  })

  it('does not render a board when the tool still needs a mold code', () => {
    expect(erpOutsourceResultTablesFromRun({
      status: 'SUCCEEDED',
      trace: [{
        type: 'tool',
        tool: 'query_erp_outsource_followup_board',
        data: { status: 'NEED_MOLD_CODE', summary: '请先提供模具号' },
      }],
    })).toEqual([])
  })

  it('keeps a single progress table for a timeline lookup', () => {
    const tables = erpOutsourceResultTablesFromRun({
      status: 'SUCCEEDED',
      trace: [{
        type: 'tool',
        tool: 'query_erp_outsource_order_progress',
        data: {
          moldBatch: 'M260063-P2',
          currentStep: '委外工单',
          steps: [
            { step: '排产工单', state: '已完成', detail: '有 7 张工单。' },
            { step: '委外工单', state: '进行中', detail: '审批中' },
          ],
        },
      }],
    })
    expect(tables).toHaveLength(1)
    expect(tables[0].title).toBe('委外进度')
    expect(tables[0].summary).toBe('M260063-P2 当前停在「委外工单」')
  })

  it('does not invent tables when the outsource query was not called', () => {
    expect(erpOutsourceResultTablesFromRun({
      status: 'SUCCEEDED',
      trace: [{ type: 'final', summary: '没有查到' }],
    })).toEqual([])
  })
})
