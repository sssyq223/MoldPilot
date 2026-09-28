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
      '进度', '委外类型', '订单号', '模具号', '批次号', '零件明细', '核算价', '我方报价', '接单上限', '加工商报价', '成交价', '加工商',
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

  it('fills confirmed buyer quote amounts onto the matching board row', () => {
    const tables = erpOutsourceResultTablesFromRun({
      status: 'SUCCEEDED',
      trace: [
        {
          type: 'tool',
          tool: 'query_erp_outsource_followup_board',
          data: {
            scope: 'M260063-P1',
            items: [
              { moldFamily: 'M260063', moldBatch: 'M260063-P1', partDetails: 'PH-01 上夹板', ourQuoteAmount: null, autoAcceptMaxAmount: null, referenceTotal: 358.74 },
              { moldFamily: 'M260063', moldBatch: 'M260063-P1', partDetails: 'PU-01 成型冲头', ourQuoteAmount: null, autoAcceptMaxAmount: null, referenceTotal: 7451.29 },
            ],
          },
        },
        {
          type: 'tool',
          id: 'quote-1',
          tool: 'prepare_erp_outsource_buyer_quote',
          proposal_decision: 'approved',
          proposal: {
            display: {
              模具号: 'M260063',
              批次号: 'M260063-P1',
              零件: 'PH-01 上夹板',
              我方报价: 300,
              直接接单上限: 380,
            },
          },
        },
      ],
    })
    expect(tables[0].rows[0].ourQuoteAmount).toBe(300)
    expect(tables[0].rows[0].autoAcceptMaxAmount).toBe(380)
    expect(tables[0].rows[1].ourQuoteAmount).toBeNull()
  })

  it('fills pending buyer quote amounts by accounting price when part code is hidden', () => {
    const tables = erpOutsourceResultTablesFromRun({
      status: 'SUCCEEDED',
      trace: [
        {
          type: 'tool',
          tool: 'query_erp_outsource_followup_board',
          data: {
            scope: 'M260063-P1',
            items: [
              { moldFamily: 'M260063', moldBatch: 'M260063-P1', partDetails: '成型冲头 (S-Z-M-WZ-SS 19×1…)', ourQuoteAmount: null, autoAcceptMaxAmount: null, referenceTotal: 7451.29 },
              { moldFamily: 'M260063', moldBatch: 'M260063-P1', partDetails: '下托板', ourQuoteAmount: null, autoAcceptMaxAmount: null, referenceTotal: 104576.58 },
            ],
          },
        },
        {
          type: 'tool',
          id: 'quote-2',
          tool: 'prepare_erp_outsource_buyer_quote',
          proposal: {
            display: {
              模具号: 'M260063',
              批次号: 'M260063-P1',
              零件: 'PU-01 成型冲头',
              我方报价: 400,
              直接接单上限: 500,
              报价表: [
                { 项目: '核算价', 金额: 7451.29 },
                { 项目: '我方报价', 金额: 400 },
                { 项目: '直接接单上限', 金额: 500 },
              ],
            },
          },
        },
      ],
    })
    expect(tables[0].rows[0].ourQuoteAmount).toBe(400)
    expect(tables[0].rows[0].autoAcceptMaxAmount).toBe(500)
    expect(String(tables[0].rows[0].partDetails)).toContain('PU-01')
    expect(tables[0].rows[1].ourQuoteAmount).toBeNull()
  })

  it('fills only the board row whose accounting price matches the confirmation card', () => {
    const tables = erpOutsourceResultTablesFromRun({
      status: 'SUCCEEDED',
      trace: [
        {
          type: 'tool',
          tool: 'query_erp_outsource_followup_board',
          data: {
            scope: 'M260063-P1',
            items: [
              { moldFamily: 'M260063', moldBatch: 'M260063-P1、M260063-P2', partDetails: 'B1-01 下托板', ourQuoteAmount: null, autoAcceptMaxAmount: null, referenceTotal: 104576.58 },
              { moldFamily: 'M260063', moldBatch: 'M260063-P1', partDetails: 'B1-01 下托板', ourQuoteAmount: null, autoAcceptMaxAmount: null, referenceTotal: 17995.66 },
              { moldFamily: 'M260063', moldBatch: 'M260063-P1、M260063-P2', partDetails: 'B1-01 下托板', ourQuoteAmount: null, autoAcceptMaxAmount: null, referenceTotal: 103206.82 },
            ],
          },
        },
        {
          type: 'tool',
          tool: 'prepare_erp_outsource_buyer_quote',
          proposal_decision: 'approved',
          proposal: {
            display: {
              模具号: 'M260063',
              批次号: 'M260063-P1',
              零件: 'B1-01 下托板',
              我方报价: 12000,
              直接接单上限: 15000,
              报价表: [
                { 项目: '核算价', 金额: 17995.66 },
                { 项目: '我方报价', 金额: 12000 },
                { 项目: '直接接单上限', 金额: 15000 },
              ],
            },
          },
        },
      ],
    })
    expect(tables[0].rows[0].ourQuoteAmount).toBeNull()
    expect(tables[0].rows[1].ourQuoteAmount).toBe(12000)
    expect(tables[0].rows[1].autoAcceptMaxAmount).toBe(15000)
    expect(tables[0].rows[2].ourQuoteAmount).toBeNull()
  })
})
