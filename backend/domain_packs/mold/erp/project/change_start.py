"""Repeated starts reuse an approved contact plan and the original mold identities."""
from datetime import date
from decimal import Decimal
from typing import Literal

from pydantic import Field, model_validator
from sqlalchemy import select

from domain_packs.mold import models as m
from domain_packs.mold.authorization import require
from domain_packs.mold.ports.errors import DomainError
from domain_packs.mold.ports.schemas import StrictModel


class SupplierChangeTerms(StrictModel):
    supplier_id: str = Field(min_length=1,max_length=36)
    contract_subject_id: str | None = Field(default=None,max_length=36)
    amount: Decimal = Field(ge=0,max_digits=18,decimal_places=2,
        description='采购与供应商确认的本次新增费用；与客户收费独立，免费也明确为零。')
    currency: str = Field(pattern=r'^[A-Z]{3}$')
    evidence: str = Field(min_length=1,max_length=4000)
    file_id: str = Field(min_length=1,max_length=36)


class ChangeStartInput(StrictModel):
    mold_ids: list[str] = Field(min_length=1, max_length=100,
        description='本次设变的原项目内部模具 ID；只选择受影响模具，不新建或重新编号。')
    charge_kind: Literal['FREE', 'CHARGED'] = Field(description='本次对客户收费或免费，与供应商费用分开核对。')
    amount: Decimal = Field(ge=0, max_digits=18, decimal_places=2)
    currency: str = Field(pattern=r'^[A-Z]{3}$')
    commercial_evidence: str = Field(min_length=1, max_length=4000,
        description='本次对客户免费的依据或客户与我方确认收费的书面记录。')
    commercial_file_id: str = Field(min_length=1, max_length=36,
        description='已批准工程联络方案中的当前附件文件 ID，承载免费/收费确认依据。')
    notice_file_id: str = Field(min_length=1, max_length=36,
        description='已批准工程联络方案中的当前开工通知/书面指令附件文件 ID。')
    contract_mode: Literal['NO_NEW_CONTRACT', 'EXISTING_CONTRACT', 'NEW_CONTRACT_PENDING']
    contract_subject_id: str | None = Field(default=None, max_length=36)
    customer_mold_number: str | None = Field(default=None, max_length=200,
        description='本次人工确认的客户模号；只存入本次快照，保留旧版，不覆盖内部模号。')
    requested_due_date: date
    supplier_terms: SupplierChangeTerms | None = Field(default=None,
        description='委外设变必须独立填写采购与供应商确认的本次费用和书面附件；内部执行不填写。')

    @model_validator(mode='after')
    def coherent(self):
        if len(set(self.mold_ids)) != len(self.mold_ids):
            raise ValueError('本次模具 ID 不能重复')
        if (self.charge_kind == 'FREE' and self.amount != 0) or (self.charge_kind == 'CHARGED' and self.amount <= 0):
            raise ValueError('免费设变金额须为零，收费设变须明确正金额')
        if not self.commercial_evidence.strip():
            raise ValueError('须提供免费或收费的书面核对依据')
        if bool(self.contract_subject_id) != (self.contract_mode == 'EXISTING_CONTRACT'):
            raise ValueError('沿用合同必须填写合同 ID，其他合同方式不能夹带合同 ID')
        return self


def source_resolution(db, user, project_id, source_id):
    from domain_packs.mold import contacts
    from domain_packs.mold.erp.change import contact_lifecycle
    from domain_packs.mold.erp.core.domains import require_source
    source = require_source(db, source_id, project_id, {'contact_resolution'})
    fields = require(db,user,'contact_resolution.read',{'project_id':project_id,'category':source.category})
    if '*' not in fields and 'detail' not in fields:
        raise DomainError('FORBIDDEN', '设变开工需要完整处理方案读取权限', 403)
    resolution = db.get(m.ContactResolution, source.id)
    if not resolution:
        raise DomainError('CHANGE_PLAN_REQUIRED', '本次设变必须关联有效工程联络处理方案', 409)
    case = contacts.load(db, user, resolution.case_id, True)
    if case.project_id != project_id or case.mode != 'ONLINE' or case.change_type != 'CHANGE':
        raise DomainError('CHANGE_CASE_MISMATCH', '须引用原项目内明确分类为设变的线上工程联络单', 409)
    approved = contact_lifecycle.approved(db, user, case)
    if approved.subject_id != source.id:
        raise DomainError('CHANGE_PLAN_STALE', '处理方案已有新版本，请重新核对', 409)
    return source, resolution, case


def ensure_unique(db, project_id, source_id, exclude_id=None):
    query = select(m.BusinessSubject.id).join(
        m.BusinessDecisionDetail, m.BusinessDecisionDetail.subject_id == m.BusinessSubject.id
    ).where(m.BusinessSubject.project_id == project_id,
        m.BusinessSubject.kind == 'internal_start',
        m.BusinessDecisionDetail.source_subject_id == source_id,
        m.BusinessSubject.status.in_(['DRAFT', 'SUBMITTED', 'RETURNED', 'APPLY_BLOCKED', 'EFFECTIVE']))
    if exclude_id:
        query = query.where(m.BusinessSubject.id != exclude_id)
    if db.scalar(query.limit(1)):
        raise DomainError('CHANGE_START_EXISTS', '本方案已有待处理或生效开工通知，不能重复下达', 409)


def build(db, user, project, data, exclude_id=None):
    from domain_packs.mold.erp.core.domains import require_source
    if project.status != 'ACTIVE':
        raise DomainError('CHANGE_START_STATE', '原模再次设变要求原项目处于执行中；暂停、关闭或终止项目须先按相应流程处理', 409)
    source, resolution, case = source_resolution(db, user, project.id, data.source_subject_id)
    ensure_unique(db, project.id, source.id, exclude_id)
    change = data.change
    if not change or not data.execution_mode:
        raise DomainError('CHANGE_START_INCOMPLETE', '再次设变须明确执行方式、原模具和收费/合同核对资料', 409)
    attachment_ids = {row['file_id'] for row in resolution.material_snapshot.get('attachments', [])}
    if not {change.commercial_file_id, change.notice_file_id} <= attachment_ids:
        raise DomainError('CHANGE_START_EVIDENCE_REQUIRED', '开工及免费/收费依据须来自当前已批准方案的原件附件', 409)
    supplier_terms=change.supplier_terms
    supplier_mapping=None
    if data.execution_mode=='FULL_OUTSOURCE':
        if not supplier_terms or not supplier_terms.evidence.strip() or supplier_terms.file_id not in attachment_ids:
            raise DomainError('SUPPLIER_CHANGE_TERMS_REQUIRED', '委外设变须单独核对采购与供应商确认的费用及方案内书面附件，不能用客户收费替代',409)
        supplier=db.get(m.Supplier,supplier_terms.supplier_id)
        if not supplier or not supplier.active or supplier.category!='outsource':
            raise DomainError('SUPPLIER_UNAVAILABLE','请选择有效的委外供应商',409)
        supplier_mapping={'id':supplier.id,'code':supplier.code,'name':supplier.name,'contract':None}
        if supplier_terms.contract_subject_id:
            supplier_contract=require_source(db,supplier_terms.contract_subject_id,project.id,{'full_outsource_contract'})
            require(db,user,'full_outsource_contract.read',{'project_id':project.id,'category':supplier_contract.category})
            if db.get(m.ContractDetail,supplier_contract.id).supplier_id!=supplier.id:
                raise DomainError('CHANGE_SUPPLIER_MISMATCH','原采购合同与本次供应商不一致',409)
            supplier_mapping['contract']={'id':supplier_contract.id,'number':supplier_contract.number,
                'contract_number':db.get(m.ContractDetail,supplier_contract.id).contract_number}
    elif supplier_terms:
        raise DomainError('SUPPLIER_TERMS_NOT_APPLICABLE','内部执行不能夹带委外供应商费用',409)
    if case.problem_source == 'CUSTOMER_CHANGE' and not (resolution.customer_evidence or '').strip():
        raise DomainError('CUSTOMER_CONFIRMATION_REQUIRED', '客户设变须有书面客户确认依据', 409)
    molds = list(db.scalars(select(m.Mold).join(m.ProjectMold, m.ProjectMold.mold_id == m.Mold.id)
        .where(m.ProjectMold.project_id == project.id, m.Mold.id.in_(change.mold_ids))
        .order_by(m.Mold.internal_number).with_for_update().execution_options(populate_existing=True)))
    if {row.id for row in molds} != set(change.mold_ids) or any(row.status != 'ACTIVE' for row in molds):
        raise DomainError('CHANGE_MOLD_MISMATCH', '本次模具须为原项目已关联且可用的内部模具', 409)
    if change.contract_mode == 'NEW_CONTRACT_PENDING':
        if not data.expected_contract_date:
            raise DomainError('EXPECTED_CONTRACT_DATE_REQUIRED', '新增合同待到时须填写预计到达日期', 409)
    elif data.expected_contract_date:
        raise DomainError('EXPECTED_CONTRACT_DATE_NOT_APPLICABLE', '无新增合同或沿用合同时不填写合同预计到达日期', 409)
    contract = None
    if change.contract_subject_id:
        contract = require_source(db, change.contract_subject_id, project.id, {'sales_contract'})
        require(db, user, contract.kind + '.read', {'project_id': project.id})
    return {
        'project': {'id': project.id, 'code': project.code, 'name': project.name, 'row_version': project.row_version},
        'processing_kind': 'EXISTING_MOLD_CHANGE',
        'internal_molds': [{'id': row.id, 'internal_number': row.internal_number, 'name': row.name, 'status': row.status} for row in molds],
        'change_source': {'subject_id': source.id, 'number': source.number, 'revision': source.revision,
            'case_id': case.id, 'case_revision': resolution.case_revision, 'solution': resolution.solution,
            'customer_evidence': resolution.customer_evidence, 'material_snapshot': resolution.material_snapshot},
        'change_terms': change.model_dump(mode='json'),
        'supplier_mapping':supplier_mapping,
        'customer_mold_number': change.customer_mold_number,
        'formal_start_date': data.effective_date.isoformat(),
        'customer_due_date': change.requested_due_date.isoformat(),
        'execution_mode': data.execution_mode,
        'contract_state_at_issue': change.contract_mode,
        'sales_contracts_at_issue': [{'id': contract.id, 'number': contract.number}] if contract else [],
        'expected_contract_date': data.expected_contract_date.isoformat() if data.expected_contract_date else None,
    }


def validate_frozen(db, user, subject, snapshot):
    from types import SimpleNamespace
    from domain_packs.mold.ports.bpm import content_hash
    material = snapshot.linked_business
    detail = db.get(m.BusinessDecisionDetail, subject.id)
    if detail.source_subject_id != material['change_source']['subject_id']:
        raise DomainError('CHANGE_PLAN_STALE', '开工来源与冻结方案不一致', 409)
    current = build(db, user, db.get(m.Project, subject.project_id), SimpleNamespace(
        source_subject_id=detail.source_subject_id, change=ChangeStartInput.model_validate(material['change_terms']),
        execution_mode=detail.execution_mode, expected_contract_date=snapshot.expected_contract_date,
        effective_date=detail.effective_date), exclude_id=subject.id)
    # Project versions can advance through unrelated approved work. Only the
    # identities and exact approved source/terms of this notice must be stable.
    for key in ('internal_molds', 'change_source', 'change_terms', 'execution_mode', 'formal_start_date'):
        if content_hash(current[key]) != content_hash(material[key]):
            raise DomainError('CHANGE_START_MATERIALS_CHANGED', '设变开工所引用模具、方案或条款已变化，请重新核对', 409)


def context(db, user, project):
    """Only authorized plan candidates; no automatic selection or execution."""
    from domain_packs.mold import contacts
    from domain_packs.mold.erp.core.domains import authorize
    candidates = []
    molds=[]
    try:
        require(db,user,'project.dossier.read',{'project_id':project.id})
        molds=[{'id':row.id,'internal_number':row.internal_number,'name':row.name,'status':row.status}
            for row in db.scalars(select(m.Mold).join(m.ProjectMold,m.ProjectMold.mold_id==m.Mold.id)
                .where(m.ProjectMold.project_id==project.id).order_by(m.Mold.internal_number))]
    except DomainError:
        pass
    for source, resolution, case in db.execute(select(m.BusinessSubject, m.ContactResolution, m.ContactCase)
        .join(m.ContactResolution, m.ContactResolution.subject_id == m.BusinessSubject.id)
        .join(m.ContactCase, m.ContactCase.id == m.ContactResolution.case_id)
        .where(m.BusinessSubject.project_id == project.id, m.ContactCase.change_type == 'CHANGE')
        .order_by(m.BusinessSubject.created_at.desc()).limit(100)):
        try:
            contacts.load(db, user, case.id)
            fields=authorize(db, user, source, 'read')
            if '*' not in fields and 'detail' not in fields:
                continue
        except DomainError:
            continue
        candidates.append({'source_subject_id': source.id, 'number': source.number, 'status': source.status,
            'case_id': case.id, 'title': case.title,
            'attachments': resolution.material_snapshot.get('attachments', [])})
    available = project.status == 'ACTIVE' and bool(candidates) and bool(molds)
    return {
        'available': available,
        'processing_kind': 'EXISTING_MOLD_CHANGE' if available else None,
        'project_active': project.status == 'ACTIVE',
        'plan_candidates': candidates, 'original_molds':molds,
        'requirements': ['原项目与原模具', '当前已批准的设变处理方案', '书面开工及免费/收费依据', '明确合同方式', '正式开工审批'],
        'semantics': (
            '仅当项目处于执行中且同时存在原项目模具与已批准设变方案时，'
            '才允许选择 EXISTING_MOLD_CHANGE；否则不能按原模再次设变办理。'
        )}


def effective_notice(db, case, resolution):
    if case.mode != 'ONLINE' or case.change_type != 'CHANGE':
        return None
    return db.scalar(select(m.BusinessSubject.id).join(
        m.BusinessDecisionDetail, m.BusinessDecisionDetail.subject_id == m.BusinessSubject.id
    ).join(m.InternalStartSnapshot, m.InternalStartSnapshot.start_subject_id == m.BusinessSubject.id)
        .where(m.BusinessSubject.project_id == case.project_id,
            m.BusinessSubject.kind == 'internal_start', m.BusinessSubject.status == 'EFFECTIVE',
            m.BusinessDecisionDetail.source_subject_id == resolution.subject_id,
            m.InternalStartSnapshot.linked_business['processing_kind'].as_string() == 'EXISTING_MOLD_CHANGE')
        .limit(1))


def require_effective_notice(db, case, resolution):
    if case.mode == 'ONLINE' and case.change_type == 'CHANGE' and not effective_notice(db,case,resolution):
        raise DomainError('CHANGE_START_REQUIRED', '本次设变尚无已审批生效的正式开工通知，不能认定复验/关闭条件齐备；免费或无新增合同也须开工审批', 409)
