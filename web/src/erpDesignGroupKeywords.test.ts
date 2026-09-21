import { describe, expect, it } from 'vitest'
import { erpDesignGroupKeywordsFromRun, erpDesignGroupKeywordsFromTool } from './erpDesignGroupKeywords'

const page = { code: 200, rows: [{ id: 19, keywordText: '导柱', remark: null }], total: 21, pageNum: 2, pageSize: 10, hasNext: true }
const tool = { tool: 'erp_design_query_group_keywords', data: page, as_of: '2026-09-20T12:00:00Z', model_context: { query: { keywordText: '导柱' } } }

describe('ERP group keyword tables', () => {
  it('preserves actual ERP fields, filter, timestamp and pagination', () => {
    expect(erpDesignGroupKeywordsFromTool(tool)).toEqual({
      rows: page.rows, total: 21, pageNum: 2, pageSize: 10, hasNext: true,
      keywordText: '导柱', asOf: tool.as_of,
    })
  })

  it('also shows keyword receipts from the aggregate reader', () => {
    expect(erpDesignGroupKeywordsFromTool({
      tool: 'erp_design_query_master_data', data: { group_keywords: page, densities: { rows: [] } },
    })?.rows).toEqual(page.rows)
  })

  it('renders a confirmed empty result but does not turn errors into empty tables', () => {
    expect(erpDesignGroupKeywordsFromTool({ ...tool, data: { ...page, rows: [], total: 0, hasNext: false } })?.total).toBe(0)
    for (const data of [{}, { ...page, code: 500 }, { ...page, success: false }, { rows: [], total: null }]) {
      expect(erpDesignGroupKeywordsFromTool({ ...tool, data })).toBeNull()
    }
    expect(erpDesignGroupKeywordsFromTool({ ...tool, tool: 'erp_design_query_densities' })).toBeNull()
  })

  it('keeps separate pages and filters without merging unrelated query results', () => {
    const next = { ...tool, data: { ...page, pageNum: 3, hasNext: false } }
    const run = { status: 'SUCCEEDED', trace: [tool, next] }
    expect(erpDesignGroupKeywordsFromRun(run).map(result => result.pageNum)).toEqual([2, 3])
    expect(erpDesignGroupKeywordsFromRun({ ...run, status: 'FAILED' })).toHaveLength(2)
    expect(erpDesignGroupKeywordsFromRun({ ...run, status: 'RUNNING' })).toEqual([])
  })
})
