import { describe, expect, it } from 'vitest'
import { quotationFormMissingFields } from './quotationForm'

describe('quotation form', () => {
  it('requires internal evaluation fields for internal processing', () => {
    expect(quotationFormMissingFields({
      preliminary_execution_mode: 'INTERNAL',
      quotation_number: 'Q-001',
      version: 1,
      quoted_amount: '100.00',
      currency: 'CNY',
      promised_delivery_date: '2026-12-01',
      payment_terms: '30%',
      cost_amount: '',
      cost_evidence: '',
      process_analysis: '',
      duration_days: '',
      duration_evidence: '',
      source_kind: 'UPLOAD',
      source_ref: 'MAIL-001',
      owner_user_id: 'user-1',
      workflow_definition_id: 'workflow-1',
    })).toEqual(expect.arrayContaining([
      'cost_amount', 'cost_evidence', 'process_analysis', 'duration_days', 'duration_evidence',
    ]))
  })

  it('requires supplier evaluation fields for full outsourcing', () => {
    expect(quotationFormMissingFields({
      preliminary_execution_mode: 'FULL_OUTSOURCE',
      quotation_number: 'Q-001',
      version: 1,
      quoted_amount: '100.00',
      currency: 'CNY',
      promised_delivery_date: '2026-12-01',
      payment_terms: '30%',
      supplier_quote_amount: '',
      supplier_delivery_date: '',
      supplier_requirements: '',
      supplier_quote_evidence: '',
      source_kind: 'UPLOAD',
      source_ref: 'MAIL-001',
      owner_user_id: 'user-1',
      workflow_definition_id: 'workflow-1',
    })).toEqual(expect.arrayContaining([
      'supplier_quote_amount', 'supplier_delivery_date', 'supplier_requirements', 'supplier_quote_evidence',
    ]))
  })
})
