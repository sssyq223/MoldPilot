export type QuotationFormValues = Record<string, any>

const commonFields = [
  'quotation_number', 'version', 'quoted_amount', 'currency',
  'promised_delivery_date', 'payment_terms', 'customer_company_snapshot',
  'customer_contact_snapshot', 'owner_user_id', 'source_kind', 'source_ref',
  'workflow_definition_id',
]

const modeFields: Record<string, string[]> = {
  INTERNAL: ['cost_amount', 'cost_evidence', 'process_analysis', 'duration_days', 'duration_evidence'],
  FULL_OUTSOURCE: ['supplier_quote_amount', 'supplier_delivery_date', 'supplier_requirements', 'supplier_quote_evidence'],
}

export function quotationFormMissingFields(values: QuotationFormValues): string[] {
  const required = [...commonFields, ...(modeFields[values.preliminary_execution_mode] || [])]
  return required.filter((key) => {
    const value = values[key]
    return value === undefined || value === null || String(value).trim() === ''
  })
}

export function quotationFormIsValid(values: QuotationFormValues): boolean {
  return quotationFormMissingFields(values).length === 0
}
