import type { ErpDesignColumn, ErpDesignRow } from './erpDesignPreview'

export type ErpDesignGroupKeywords = {
  rows: ErpDesignRow[]
  total: number
  pageNum: number
  pageSize: number
  hasNext: boolean
  keywordText: string
  asOf: string
}

function record(value: any): Record<string, any> | null {
  return value && typeof value === 'object' && !Array.isArray(value) ? value : null
}

export function erpDesignGroupKeywordsFromTool(item: any): ErpDesignGroupKeywords | null {
  const direct = item?.tool === 'erp_design_query_group_keywords'
  const aggregate = item?.tool === 'erp_design_query_master_data'
  if (!direct && !aggregate) return null
  const receipt = record(item?.data)
  const data = record(receipt?.data) ?? receipt
  const source = aggregate ? record(data?.group_keywords) : data
  if (!source || source.success === false || (source.code != null && source.code !== 200)
      || !Array.isArray(source.rows) || !source.rows.every((row: unknown) => record(row))
      || !Number.isInteger(source.total) || source.total < 0) return null
  const pageNum = Number(source.pageNum ?? 1)
  const pageSize = Number(source.pageSize ?? source.rows.length)
  return {
    rows: source.rows,
    total: source.total,
    pageNum,
    pageSize,
    hasNext: source.hasNext ?? pageNum * pageSize < source.total,
    keywordText: String(item?.model_context?.query?.keywordText ?? ''),
    asOf: String(item?.as_of ?? source.time ?? ''),
  }
}

export function erpDesignGroupKeywordsFromRun(run: any): ErpDesignGroupKeywords[] {
  if (!['SUCCEEDED', 'FAILED'].includes(String(run?.status ?? ''))) return []
  return (Array.isArray(run?.trace) ? run.trace : [])
    .map(erpDesignGroupKeywordsFromTool)
    .filter((result: ErpDesignGroupKeywords | null): result is ErpDesignGroupKeywords => result !== null)
}

export const erpDesignGroupKeywordColumns: ErpDesignColumn[] = [
  { key: 'keyword', label: '关键词', fields: ['keywordText', 'keyword_text'], width: 210 },
  { key: 'remark', label: '备注', fields: ['remark'], width: 220 },
  { key: 'created', label: '创建时间', fields: ['createdAt', 'created_at'], width: 190 },
  { key: 'updated', label: '更新时间', fields: ['updatedAt', 'updated_at'], width: 190 },
]
