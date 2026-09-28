"""Current contract material pointer; older approval materials remain immutable."""
from sqlalchemy import select
from domain_packs.mold import models as m
from domain_packs.mold.ports.errors import DomainError


def version(db, contract_id):
    detail = db.get(m.ContractDetail, contract_id)
    return detail.material_version if detail else 1


def terms(db, contract_id):
    return db.get(m.ContractBusinessTerms, (version(db,contract_id),contract_id))


def receipt(db, contract_id):
    return db.get(m.ContractReceiptEvidence, (version(db,contract_id),contract_id))


def stages(db, contract_id):
    return list(db.scalars(select(m.PaymentStage).where(m.PaymentStage.contract_id == contract_id,
        m.PaymentStage.material_version == version(db,contract_id))))


def current_allocation():
    return m.ContractSettlementAllocation.material_version == select(m.ContractDetail.material_version).where(
        m.ContractDetail.subject_id == m.ContractSettlementAllocation.target_contract_id).scalar_subquery()


def require_current_stage(db, stage):
    if stage.material_version != version(db,stage.contract_id):
        raise DomainError('CONTRACT_STAGE_SUPERSEDED','该付款节点属于旧材料版本，请重新查询当前合同节点',409)
