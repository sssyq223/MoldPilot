"""Freeze the referenced payment terms for a reviewer, rather than only an ID."""
from domain_packs.mold import models as m, domains, domain_schemas as s
from domain_packs.mold.ports.errors import DomainError


def approval_material(db, actor, subject):
    request = db.get(m.PaymentRequestDetail, subject.id)
    stage = db.get(m.PaymentStage, request.stage_id, populate_existing=True)
    contract = db.get(m.BusinessSubject, stage.contract_id, populate_existing=True)
    domains.require_source(db, contract.id, subject.project_id, {'full_outsource_contract'})
    fields = domains.authorize(db, actor, contract, 'read')
    if '*' not in fields and not set(s.READ_FIELDS) <= set(fields):
        raise DomainError('PAYMENT_MATERIAL_FORBIDDEN', '提交付款审批需要完整合同材料读取权限', 403)
    detail = db.get(m.ContractDetail, contract.id, populate_existing=True)
    supplier = db.get(m.Supplier, detail.supplier_id)
    return {'contract_id':contract.id, 'contract_number':detail.contract_number,
            'contract_subject_number':contract.number, 'contract_revision':contract.revision,
            'material_version':detail.material_version, 'supplier_id':detail.supplier_id,
            'supplier_name':supplier.name if supplier else None,
            'stage_id':stage.id, 'stage_name':stage.name, 'stage_amount':str(stage.amount),
            'currency':stage.currency, 'condition':stage.condition,
            'condition_confirmed':stage.condition_confirmed, 'condition_evidence':stage.condition_evidence,
            'condition_profile':stage.condition_profile or {},
            'condition_evidence_map':stage.condition_evidence_map or {},
            'special_approval_reference':stage.special_approval_reference}
