const ERP_DESIGN_SPREADSHEET_TYPES = new Set([
  'text/csv',
  'application/vnd.ms-excel',
  'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
])

const ERP_DESIGN_NAME_HINT = /(?:料单|清单|五金|钢料|备料|物料|请购|bom|^m?\d{6}-p\d+)/i
const NON_DESIGN_DOCUMENT_HINT = /(?:工程联络单?|联络书|engineering[\s_-]*contact)/i

/** Whether an unbound conversation attachment should offer ERP list parsing. */
export function isErpDesignParseAttachment(file: any): boolean {
  const filename = String(file?.filename ?? file?.name ?? '').trim()
  const mediaType = String(file?.media_type ?? file?.content_type ?? '').toLowerCase()
  const documentType = String(
    file?.document_type
      ?? file?.suggested_type
      ?? file?.classification?.document_type
      ?? '',
  ).toUpperCase()
  if (!filename || documentType === 'ENGINEERING_CONTACT' || NON_DESIGN_DOCUMENT_HINT.test(filename)) return false
  const spreadsheet = /\.(csv|xlsx|xls)$/i.test(filename) || ERP_DESIGN_SPREADSHEET_TYPES.has(mediaType)
  return spreadsheet && ERP_DESIGN_NAME_HINT.test(filename)
}

export function hasErpDesignParserCapability(capabilities: any): boolean {
  const keys = new Set(
    (Array.isArray(capabilities?.tools) ? capabilities.tools : [])
      .map((tool: any) => String(tool?.key ?? tool?.name ?? '')),
  )
  return keys.has('erp_design_parse_new_mold_upload')
    || keys.has('erp_design_parse_modify_mold_upload')
}
