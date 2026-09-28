"""Carry an approved reversal through its explicit current contract allocation."""
from sqlalchemy import select

from domain_packs.mold import models as m
from domain_packs.mold.ports.errors import DomainError
from domain_packs.mold.ports.events import record
from domain_packs.mold.erp.commercial.contract_relations import lineage_ids
from domain_packs.mold.erp.commercial.contract_materials import current_allocation, version


def effects(db, original):
    request = db.get(m.PaymentRequestDetail, original.request_id)
    source_stage = db.get(m.PaymentStage, request.stage_id)
    source_contract = db.get(m.BusinessSubject, source_stage.contract_id)
    allocations = list(db.scalars(select(m.ContractSettlementAllocation)
        .join(m.BusinessSubject, m.BusinessSubject.id == m.ContractSettlementAllocation.target_contract_id)
        .where(m.ContractSettlementAllocation.record_type == 'SUPPLIER_PAYMENT',
               current_allocation(),
               m.ContractSettlementAllocation.source_record_id == original.id,
               m.BusinessSubject.status == 'EFFECTIVE')
        .order_by(m.ContractSettlementAllocation.id)))
    if len(allocations) > 1:
        raise DomainError('CORRECTION_ALLOCATION_INVALID', '原付款存在多个当前有效分配，请核对合同版本关系', 409)
    result = []
    for allocation in allocations:
        target = db.get(m.BusinessSubject, allocation.target_contract_id)
        detail = db.get(m.ContractDetail, target.id)
        stage = db.get(m.PaymentStage, allocation.target_stage_id)
        if (target.kind != 'full_outsource_contract' or target.project_id != source_contract.project_id
                or source_contract.status == 'EFFECTIVE'
                or source_contract.id not in lineage_ids(db, target)
                or allocation.source_contract_id != source_contract.id
                or allocation.amount != original.amount or allocation.currency != original.currency
                or not stage or stage.contract_id != target.id or stage.material_version != detail.material_version
                or stage.currency != original.currency):
            raise DomainError('CORRECTION_ALLOCATION_INVALID', '原付款与当前合同分配依据不一致，不能冲正分配', 409)
        result.append({'allocation_id':allocation.id, 'source_contract_id':source_contract.id,
            'target_contract_id':target.id, 'contract_number':detail.contract_number,
            'target_stage_id':stage.id, 'stage_name':stage.name,
            'original_amount':str(allocation.amount), 'reversal_amount':str(-original.amount),
            'currency':original.currency})
    return result


def approval_instance(db, subject):
    return db.scalar(select(m.ApprovalInstance).where(
        m.ApprovalInstance.resource_type == 'business_subject',
        m.ApprovalInstance.resource_id == subject.id,
        m.ApprovalInstance.revision == subject.revision,
        m.ApprovalInstance.round_no == subject.round_no))


def detail_effects(db, subject, original):
    instance = approval_instance(db, subject)
    if instance:
        return (instance.snapshot.get('detail') or {}).get('allocation_effects', [])
    return effects(db, original)


def approved_effects(db, subject, original):
    current = effects(db, original)
    instance = approval_instance(db, subject)
    frozen = ((instance.snapshot.get('detail') or {}).get('allocation_effects', []) if instance else [])
    # Legacy approvals with no allocation effects can only retain that scope.
    if current != frozen:
        raise DomainError('CORRECTION_ALLOCATION_CHANGED', '冲正审批期间合同分配依据已变化，请重新核对并提交审批', 409)
    return current


def append_reversal(db, actor, subject, original, reversal, allocation_effects):
    created = []
    for effect in allocation_effects:
        row = m.ContractSettlementAllocation(
            material_version=version(db,effect['target_contract_id']),
            target_contract_id=effect['target_contract_id'], target_stage_id=effect['target_stage_id'],
            source_contract_id=effect['source_contract_id'], record_type='SUPPLIER_PAYMENT',
            source_record_id=reversal.id, amount=reversal.amount, currency=reversal.currency,
            evidence=reversal.evidence, recorded_by=actor.id)
        db.add(row);db.flush()
        created.append({'allocation_id':row.id,'original_allocation_id':effect['allocation_id'],
                        'target_contract_id':row.target_contract_id,'target_stage_id':row.target_stage_id})
    if created:
        record(db,actor,'finance.correction.allocations',subject.id,
               {'original_payment_id':original.id,'reversal_id':reversal.id,'allocations':created})
