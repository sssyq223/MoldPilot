import { describe, expect, it } from 'vitest'
import { collapsedPartPreview } from './erpDesignPreview'
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
      '进度', '委外类型', '订单号', '模具号', '批次号', '交期', '零件明细', '核算价', '我方报价', '接单上限', '加工商报价', '成交价', '加工商',
    ])
    expect(tables[0].columns.find((column) => column.key === 'supplierQuotes')?.kind).toBe('wrap')
    expect(tables[0].columns.find((column) => column.key === 'pending')?.kind).toBe('wrap')
    expect(tables[0].columns.some((column) => /项目|工单/.test(column.label))).toBe(false)
  })

  it('renders the processor board without buyer price columns', () => {
    const tables = erpOutsourceResultTablesFromRun({
      status: 'SUCCEEDED',
      trace: [{
        type: 'tool',
        tool: 'query_erp_outsource_processor_board',
        data: {
          scope: 'ERP 委外待办',
          items: [{
            stationLabel: '待报价',
            outsourceTypeLabel: '零件委外',
            moldFamily: 'M260063',
            moldBatch: 'M260063-P1',
            partDetails: 'B1-01 下托板 (S-Z 850×1200×30)',
            processNames: '',
            buyerQuoteAmount: 18000,
            referenceTotal: null,
            ourQuoteAmount: null,
            autoAcceptMaxAmount: null,
            finalDealAmount: 50000,
            supplierQuotes: '',
            supplierName: '青岛和兴嘉业金属制品有限公司',
          }, {
            stationLabel: '待接单',
            outsourceTypeLabel: '工序委外',
            moldFamily: 'M260063',
            moldBatch: 'M260063-P5',
            partDetails: 'DIE-BL1',
            processNames: 'CNC',
            referenceTotal: 267,
            buyerQuoteAmount: null,
          }],
        },
      }],
    })
    expect(tables).toHaveLength(1)
    expect(tables[0].title).toBe('我的委外待办')
    expect(tables[0].columns.map((column) => column.label)).toEqual([
      '进度', '委外类型', '订单号', '模具号', '批次号', '交期', '零件明细', '工序', '核算价', '采购报价', '我的报价', '成交价',
    ])
    expect(tables[0].sourceNote).toContain('采购报价')
    expect(tables[0].rows[0].buyerQuoteAmount).toBe(18000)
    expect(tables[0].rows[0].finalDealAmount).toBe(50000)
    expect(tables[0].rows[1].buyerQuoteAmount).toBeNull()
    expect(tables[0].rows[1].referenceTotal).toBe(267)
    expect(tables[0].rows[0].referenceTotal ?? null).toBeNull()
    expect(tables[0].columns.find((column) => column.key === 'partDetails')?.kind).toBe('parts')
  })

  it('keeps processor part details compact and expandable', () => {
    const tables = erpOutsourceResultTablesFromRun({
      status: 'SUCCEEDED',
      trace: [{
        type: 'tool',
        tool: 'query_erp_outsource_processor_board',
        data: {
          scope: 'ERP 委外待办',
          items: [{
            stationLabel: '待接单',
            outsourceTypeLabel: '工序委外',
            orderNo: 'EO-261009-WZU0',
            moldFamily: 'M260063',
            moldBatch: 'M260063-P1',
            partDetails: 'GU-01 抬料板（ZKR）；GU-02 抬料板（ZKR）',
            processNames: 'ZKR',
            parts: [
              { partNo: 'GU-01', partName: '抬料板', processName: 'ZKR', qty: 1 },
              { partNo: 'GU-02', partName: '抬料板', processName: 'ZKR', qty: 1 },
            ],
          }],
        },
      }],
    })
    expect(tables[0].rows[0].partDetails).toBe('GU-01 抬料板\nGU-02 抬料板')
    expect(tables[0].columns.find((column) => column.key === 'partDetails')?.kind).toBe('parts')
    const fromText = erpOutsourceResultTablesFromRun({
      status: 'SUCCEEDED',
      trace: [{
        type: 'tool',
        tool: 'query_erp_outsource_processor_board',
        data: {
          items: [{
            stationLabel: '待接单',
            partDetails: 'DIE-02 下模板（深冷）；DIE-03 下模板（深冷）',
            processNames: '深冷',
          }],
        },
      }],
    })
    expect(fromText[0].rows[0].partDetails).toBe('DIE-02 下模板\nDIE-03 下模板')
  })

  it('collapses extra parts until expanded', () => {
    expect(collapsedPartPreview('GU-01 抬料板\nGU-02 抬料板')).toMatchObject({
      preview: 'GU-01 抬料板 · GU-02 抬料板',
      hidden: 0,
      total: 2,
    })
    expect(collapsedPartPreview('A\nB\nC\nD')).toMatchObject({
      preview: 'A',
      hidden: 3,
      total: 4,
    })
  })

  it('drops accepted orders from a leftover processor board', () => {
    const tables = erpOutsourceResultTablesFromRun({
      status: 'SUCCEEDED',
      result: {
        proposal_resolution: {
          decision: 'approved',
          authoritative_receipt: { order_no: 'EO-261009-RIVQ' },
        },
      },
      trace: [
        {
          type: 'tool',
          id: 'board',
          tool: 'query_erp_outsource_processor_board',
          data: {
            scope: 'M260063-P1',
            items: [
              { stationLabel: '待接单', orderNo: 'EO-261009-YMTD', moldFamily: 'M260063' },
              { stationLabel: '待接单', orderNo: 'EO-261009-RIVQ', moldFamily: 'M260063' },
              { stationLabel: '待接单', orderNo: 'EO-261009-VDN2', moldFamily: 'M260063' },
            ],
          },
        },
        {
          type: 'tool',
          id: 'accept-ymtd',
          tool: 'prepare_erp_outsource_processor_accept',
          proposal_decision: 'approved',
          proposal: { input: { order_no: 'EO-261009-YMTD' }, display: { 订单号: 'EO-261009-YMTD' } },
        },
        {
          type: 'tool',
          id: 'accept-rivq',
          tool: 'prepare_erp_outsource_processor_accept',
          proposal_decision: 'approved',
          proposal: { input: { order_no: 'EO-261009-RIVQ' }, display: { 订单号: 'EO-261009-RIVQ' } },
        },
      ],
    })
    expect(tables).toHaveLength(1)
    expect(tables[0].rows.map((row) => row.orderNo)).toEqual(['EO-261009-VDN2'])
  })

  it('keeps the fuller board when a later query narrows to one mold', () => {
    const tables = erpOutsourceResultTablesFromRun({
      status: 'SUCCEEDED',
      trace: [
        {
          type: 'tool',
          id: 'all',
          tool: 'query_erp_outsource_followup_board',
          data: {
            scope: 'ERP 委外待办',
            items: [
              { stationLabel: '审批中', moldFamily: 'M260063', moldBatch: 'M260063-P3' },
              { stationLabel: '待采购填报价', moldFamily: 'M260063', moldBatch: 'M260063-P5' },
              { stationLabel: '待采购填报价', moldFamily: 'M260063', moldBatch: 'M260063-P1' },
              { stationLabel: '待接单', moldFamily: 'M210236', moldBatch: 'M210236-P1' },
            ],
          },
        },
        {
          type: 'tool',
          id: 'one',
          tool: 'query_erp_outsource_followup_board',
          data: {
            scope: 'M210236',
            items: [{ stationLabel: '待接单', moldFamily: 'M210236', moldBatch: 'M210236-P1' }],
          },
        },
      ],
    })
    expect(tables).toHaveLength(1)
    expect(tables[0].rows).toHaveLength(4)
    expect(tables[0].summary).toBe('ERP 委外待办 共 4 条')
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

  it('renders one warehouse order instead of one row per part', () => {
    const tables = erpOutsourceResultTablesFromRun({
      status: 'SUCCEEDED',
      trace: [{
        type: 'tool',
        tool: 'query_erp_outsource_warehouse_tasks',
        data: {
          scope: '仓库委外待办',
          items: [
            {
              action: 'ship',
              actionLabel: '原料发货',
              outsourceTypeLabel: '零件委外',
              orderNo: 'EO-260928-WE11',
              moldFamily: 'M260063',
              moldBatch: 'M260063-P1',
              partNo: 'UP-01',
              partName: '上模座',
              qty: 1,
              sourceType: 'material_stock',
              sourceTypeLabel: '物料库',
              processorName: '青岛和兴嘉业金属制品有限公司',
            },
            {
              action: 'ship',
              actionLabel: '原料发货',
              outsourceTypeLabel: '零件委外',
              orderNo: 'EO-260928-WE11',
              moldFamily: 'M260063',
              moldBatch: 'M260063-P1',
              partNo: 'U2-03',
              partName: '防护垫脚',
              qty: 6,
              sourceType: 'material_stock',
              sourceTypeLabel: '物料库',
              processorName: '青岛和兴嘉业金属制品有限公司',
            },
            {
              action: 'ship',
              actionLabel: '备料完成',
              outsourceTypeLabel: '工序委外',
              orderNo: 'EO-260928-8L2U',
              moldFamily: 'M260063',
              moldBatch: 'M260063-P5',
              partNo: 'DIE-BL1',
              partName: '下模',
              qty: 1,
              sourceType: 'semi_finished_stock',
              sourceTypeLabel: '半成品库',
              processorName: '另一家',
            },
          ],
        },
      }],
    })
    expect(tables).toHaveLength(1)
    expect(tables[0].title).toBe('仓库委外待发货')
    expect(tables[0].summary).toBe('仓库委外待办 共 2 单，3 个零件明细')
    expect(tables[0].columns.map((column) => column.label)).toEqual([
      '办理', '委外类型', '订单号', '模具号', '批次号', '零件明细', '明细数', '数量合计', '来源', '加工商',
    ])
    expect(tables[0].rows).toHaveLength(2)
    expect(tables[0].rows[0].orderNo).toBe('EO-260928-WE11')
    expect(tables[0].rows[0].lineCount).toBe(2)
    expect(tables[0].rows[0].qty).toBe(7)
    expect(String(tables[0].rows[0].partDetails)).toContain('UP-01 上模座')
    expect(String(tables[0].rows[0].partDetails)).toContain('U2-03 防护垫脚')
    expect(tables[0].rows[1].orderNo).toBe('EO-260928-8L2U')
    expect(tables[0].rows[1].lineCount).toBe(1)
  })

  it('renders inbound todos from a generic warehouse query', () => {
    const tables = erpOutsourceResultTablesFromRun({
      status: 'SUCCEEDED',
      trace: [{
        type: 'tool',
        tool: 'query_erp_outsource_warehouse_tasks',
        data: {
          scope: '仓库委外待办',
          orders: [],
          inboundItems: [{
            actionLabel: '仓储入库',
            stationLabel: '待入库',
            outsourceTypeLabel: '零件委外',
            orderNo: 'EO-260928-WE11',
            moldNo: 'M260063-P5',
            shipmentNo: 'PS-5136-441118',
            pendingArrivalQty: 0,
            pendingInboundQty: 19,
            inboundTargets: ['成品库'],
            supplierName: '青岛和兴嘉业金属制品有限公司',
            lines: [{ partNo: 'B1-01', partName: '下托板', pendingInboundQty: 1 }],
          }],
        },
      }],
    })
    expect(tables).toHaveLength(1)
    expect(tables[0].title).toBe('仓库回厂收货入库')
    expect(tables[0].rows).toHaveLength(1)
    expect(tables[0].rows[0].shipmentNo).toBe('PS-5136-441118')
    expect(tables[0].rows[0].inboundTargetText).toBe('成品库')
    expect(String(tables[0].rows[0].partDetails)).toContain('B1-01')
  })

  it('renders outsource approval todos as a table', () => {
    const tables = erpOutsourceResultTablesFromRun({
      status: 'SUCCEEDED',
      trace: [{
        type: 'tool',
        tool: 'query_erp_outsource_approval_todos',
        data: {
          nodeLens: ['采购主管'],
          summary: '委外下单审批待办（采购主管）共 1 条。',
          items: [{
            actionLabel: '通过或驳回',
            stationLabel: '审批中',
            nodeName: '采购主管审批',
            orderNo: 'EO-260930-R77L',
            moldFamily: 'M260063',
            moldBatch: 'M260063-P2',
            supplierName: '青岛和兴嘉业金属制品有限公司',
            amount: 50000,
            assigneeName: '王群',
          }],
        },
      }],
    })
    expect(tables).toHaveLength(1)
    expect(tables[0].title).toBe('委外下单审批待办')
    expect(tables[0].rows).toHaveLength(1)
    expect(tables[0].rows[0].orderNo).toBe('EO-260930-R77L')
    expect(tables[0].rows[0].nodeName).toBe('采购主管审批')
    expect(tables[0].columns.map((column) => column.label)).toContain('当前节点')
    expect(tables[0].columns.find((column) => column.key === 'processor')?.kind).toBe('wrap')
  })

  it('renders quality inspection todos as a table', () => {
    const tables = erpOutsourceResultTablesFromRun({
      status: 'SUCCEEDED',
      trace: [{
        type: 'tool',
        tool: 'query_erp_outsource_quality_tasks',
        data: {
          scope: '委外质检待办',
          summary: '委外质检待办共 1 条。',
          items: [{
            actionLabel: '领取质检',
            stationLabel: '待领取',
            statusLabel: '待领取',
            inspectionNo: 'QC202609290001',
            orderNo: 'EO-260928-WE11',
            inboundNo: 'IN202609290001',
            inboundTargetLabel: '成品库',
            partnerName: '青岛和兴嘉业金属制品有限公司',
            details: [
              { partNo: 'B1-01', partName: '下托板', inboundQty: 1 },
              { partNo: 'B2-01', partName: '下垫脚', inboundQty: 2 },
            ],
          }],
        },
      }],
    })
    expect(tables).toHaveLength(1)
    expect(tables[0].title).toBe('委外质检待办')
    expect(tables[0].rows).toHaveLength(1)
    expect(tables[0].rows[0].inspectionNo).toBe('QC202609290001')
    expect(tables[0].rows[0].inboundNo).toBe('IN202609290001')
    expect(tables[0].rows[0].lineCount).toBe(2)
    expect(String(tables[0].rows[0].partDetails)).toContain('B1-01')
    expect(String(tables[0].rows[0].partDetails)).toContain('下垫脚')
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

  it('renders shippable product orders as one row, not a todo board', () => {
    const tables = erpOutsourceResultTablesFromRun({
      status: 'SUCCEEDED',
      trace: [{
        type: 'tool',
        tool: 'query_erp_outsource_processor_product_ship',
        data: {
          scope: '加工商履约待办',
          items: [{
            orderNo: 'EO-260928-WE11',
            moldNo: 'M260063-P1',
            outsourceTypeLabel: '零件委外',
            inboundTargets: ['成品库'],
            parts: [
              { partNo: 'B1-01', partName: '下托板', remainQty: 1, inboundTargetLabel: '成品库' },
              { partNo: 'B2-01', partName: '下垫脚', remainQty: 2, inboundTargetLabel: '成品库' },
            ],
          }],
        },
      }],
    })
    expect(tables).toHaveLength(1)
    expect(tables[0].title).toBe('可成品发货')
    expect(tables[0].rows).toHaveLength(1)
    expect(tables[0].rows[0].orderNo).toBe('EO-260928-WE11')
    expect(tables[0].rows[0].stationLabel).toBe('待成品发货')
    expect(String(tables[0].rows[0].partDetails)).toContain('B1-01')
    expect(tables[0].rows[0].remainQty).toBe(3)
    expect(tables[0].rows[0].inboundTargetText).toBe('成品库')
  })
})
