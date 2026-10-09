import { describe, expect, it } from 'vitest'
import { hasErpDesignParserCapability, isErpDesignParseAttachment } from './erpDesignAttachment'

describe('ERP design attachment actions', () => {
  it.each([
    ['M250238-P4-五金请购单.CSV', 'text/csv'],
    ['M250238-P4料单.xlsx', 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'],
    ['改模钢料清单.xls', 'application/vnd.ms-excel'],
  ])('offers parsing for design list %s', (filename, media_type) => {
    expect(isErpDesignParseAttachment({ filename, media_type })).toBe(true)
  })

  it.each([
    ['M250238-P4工程联络单.xlsx', 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'],
    ['工程联络单.docx', 'application/vnd.openxmlformats-officedocument.wordprocessingml.document'],
    ['客户合同.pdf', 'application/pdf'],
    ['普通数据.xlsx', 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'],
  ])('does not offer parsing for non-design document %s', (filename, media_type) => {
    expect(isErpDesignParseAttachment({ filename, media_type })).toBe(false)
  })

  it('honors an engineering-contact classification even for a spreadsheet', () => {
    expect(isErpDesignParseAttachment({
      filename: 'M250238-P4清单.xlsx',
      media_type: 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
      classification: { document_type: 'ENGINEERING_CONTACT' },
    })).toBe(false)
  })

  it('requires one of the existing ERP parser capabilities', () => {
    expect(hasErpDesignParserCapability({ tools: [{ key: 'erp_design_parse_new_mold_upload' }] })).toBe(true)
    expect(hasErpDesignParserCapability({ tools: [{ key: 'query_contact_cases' }] })).toBe(false)
  })
})
