"""Conversation adapter for the existing, human-approved payment reversal flow."""
from pydantic import Field, ValidationError, model_validator
from sqlalchemy import select

from domain_packs.mold import models as m, domains, domain_schemas as s, workflow_selection
from domain_packs.mold.authorization import require, access
from domain_packs.mold.ports.errors import DomainError
from domain_packs.mold.ports.db import now
from domain_packs.mold.ports.confirmation_policy import agent_permission_mode_from_proposal
from domain_packs.mold.erp.core.project_locator import ResolvedProjectId


class CorrectionProposalInput(s.CorrectionInput):
    project_id: ResolvedProjectId
    project_version: int = Field(ge=1)
    workflow_definition_id: str = Field(min_length=1, max_length=36)
    material_review_id: str | None = Field(default=None, min_length=1, max_length=36)
    existing_subject_id: str | None = Field(default=None, min_length=1, max_length=36,
        description='修订并重提已有冲正申请时填写查询返回的原申请 ID；新申请留空。')
    subject_revision: int | None = Field(default=None, ge=1)
    revision_reason: str | None = Field(default=None, min_length=1, max_length=4000)

    @model_validator(mode='after')
    def revision_fields(self):
        if self.existing_subject_id:
            if self.subject_revision is None or not (self.revision_reason or '').strip():
                raise ValueError('修订原申请必须填写 subject_revision 和 revision_reason')
        elif self.subject_revision is not None or self.revision_reason is not None:
            raise ValueError('新申请不能填写原申请版本或修订原因')
        return self


def parse(arguments):
    try:
        return CorrectionProposalInput.model_validate(arguments or {})
    except ValidationError as error:
        raise DomainError('INVALID_TOOL_INPUT', '付款冲正申请参数无效：'+error.errors()[0]['msg']) from None


def workflow_options(db, actor, project_id, category, resource=None):
    scope = {'project_id':project_id, 'category':category}
    if not all(access(db, actor, 'finance_correction.'+action, scope).allowed
               for action in ('read','create','submit')):
        return []
    resource = resource or m.BusinessSubject(kind='finance_correction', project_id=project_id, category=category)
    return [{**workflow_selection.metadata(row, db),
             'requires_material_review':row.config.get('material_contract') is not None}
            for row in workflow_selection.available(db, actor, resource)]


def preview(db, actor, data):
    project = db.get(m.Project, data.project_id)
    if not project:
        raise DomainError('NOT_FOUND', '项目不存在', 404)
    require(db, actor, 'project.read', {'project_id':project.id})
    if project.row_version != data.project_version:
        raise DomainError('VERSION_CONFLICT', '项目状态已变化，请重新查询', 409)
    existing = None
    if data.existing_subject_id:
        existing = db.get(m.BusinessSubject, data.existing_subject_id)
        if not existing or existing.project_id != project.id or existing.kind != 'finance_correction':
            raise DomainError('NOT_FOUND', '原冲正申请不存在或不属于该项目', 404)
        from domain_packs.mold.erp.core import business_revisions
        business_revisions.validate(db, actor, existing, data.subject_revision)
        old_detail = db.get(m.FinanceCorrectionDetail, existing.id)
        if old_detail.original_payment_id != data.original_payment_id:
            raise DomainError('CORRECTION_SOURCE_CHANGED', '修订必须保留原付款关联，不能换成另一笔付款', 409)
        if old_detail.reversal_id:
            raise DomainError('ALREADY_REVERSED', '该冲正已产生实际反向记录，不能修订', 409)
    original = db.get(m.PaymentConfirmation, data.original_payment_id)
    if not original or original.amount <= 0 or original.reversal_of_id:
        raise DomainError('PAYMENT_INVALID', '只能对有效正向供应商付款记录申请冲正')
    request = domains.require_source(db, original.request_id, project.id, {'supplier_payment'})
    domains.authorize(db, actor, request, 'read')
    if existing and existing.category != request.category:
        raise DomainError('SCOPE_MISMATCH', '原冲正申请与原付款责任域不一致，不能跨域修订', 409)
    for action in ('read','create','submit'):
        require(db, actor, 'finance_correction.'+action,
                {'project_id':project.id, 'category':request.category})
    if data.reversal_date < original.paid_date or data.reversal_date > now().date():
        raise DomainError('DATE_INVALID', '冲正日期须在原付款日至今天之间')
    if db.scalar(select(m.PaymentConfirmation.id).where(m.PaymentConfirmation.reversal_of_id == original.id)):
        raise DomainError('ALREADY_REVERSED', '原付款已冲正，不得重复申请', 409)
    pending = select(m.BusinessSubject.id).join(m.FinanceCorrectionDetail).where(
            m.FinanceCorrectionDetail.original_payment_id == original.id,
            m.BusinessSubject.status.in_(['DRAFT','SUBMITTED','RETURNED','APPROVED','APPLY_BLOCKED']))
    if existing:
        pending = pending.where(m.BusinessSubject.id != existing.id)
    if db.scalar(pending):
        raise DomainError('CORRECTION_PENDING', '该付款已有待处理冲正申请，请先查询原申请', 409)
    selected = next((row for row in workflow_options(db, actor, project.id, request.category, existing)
                     if row['id'] == data.workflow_definition_id), None)
    if not selected:
        raise DomainError('WORKFLOW_MISMATCH', '请选择当前可用的财务冲正审批流程', 409)
    workflow_selection.validate_material_review(db, actor,
        db.get(m.WorkflowDefinition, data.workflow_definition_id), data.material_review_id)
    from domain_packs.mold.erp.finance.correction_allocations import effects
    allocation_effects = effects(db, original)
    for effect in allocation_effects:
        domains.authorize(db, actor, db.get(m.BusinessSubject, effect['target_contract_id']), 'read')
    display = {'操作':'提交供应商付款冲正审批', '项目':project.code+' · '+project.name,
        '项目版本':project.row_version, '原付款记录':original.id, '付款申请':request.number,
        '原付款单号':original.reference, '原付款日期':original.paid_date.isoformat(),
        '原付款金额':str(original.amount)+' '+original.currency,
        '拟冲正金额':str(-original.amount)+' '+original.currency,
        '冲正日期':data.reversal_date.isoformat(), '原因':data.reason, '实际冲回依据':data.reversal_evidence,
        '审批流程':selected['name']+' · 第'+str(selected['version'])+'版',
        '当前合同分配影响':allocation_effects,
        '说明':'本人确认后仅创建并提交 Agent BPM；审批生效才登记原付款的反向记录并恢复原授权占用，不执行银行退款或 ERP 对账。'}
    if existing:
        display.update({'操作':'修订并重新提交供应商付款冲正审批', '原申请':existing.number,
            '原申请版本':existing.revision, '原申请状态':existing.status, '修订原因':data.revision_reason,
            '原申请材料':domains.typed_detail(db, existing),
            '说明':'本人确认后保存修订审计并建立新审批轮，重新完整审核；旧审批意见、阻断记录及快照保留，不沿用旧批准直接冲正。'})
    return request, display


def confirm(db, actor, data, proposal):
    if data.existing_subject_id:
        db.scalar(select(m.BusinessSubject).where(m.BusinessSubject.id == data.existing_subject_id)
                  .with_for_update().execution_options(populate_existing=True))
    db.scalar(select(m.Project).where(m.Project.id == data.project_id).with_for_update().execution_options(populate_existing=True))
    request, _ = preview(db, actor, data)
    detail = data.model_dump(include=set(s.CorrectionInput.model_fields))
    if data.existing_subject_id:
        from domain_packs.mold.erp.core import business_revisions
        subject = db.get(m.BusinessSubject, data.existing_subject_id)
        current = db.get(m.FinanceCorrectionDetail, subject.id)
        def update_detail():
            for key, value in detail.items():
                setattr(current, key, value)
            subject.remark = data.reason
        business_revisions.revise(db, actor, subject, data.subject_revision, data.revision_reason, update_detail)
    else:
        subject = domains.create(db, actor, s.SubjectInput(kind='finance_correction', project_id=data.project_id,
            category=request.category, remark=data.reason, detail=detail))
    from domain_packs.mold.erp.core.business import submit_subject
    submitted = submit_subject(db, actor, subject.id, subject.revision, data.workflow_definition_id,
        material_review_id=data.material_review_id,
        agent_permission_mode=agent_permission_mode_from_proposal(proposal))
    return {'project_id':data.project_id, 'subject_id':subject.id, 'instance_id':submitted['instance_id'],
        'subject_revision':subject.revision, 'round_no':subject.round_no,
        'original_payment_id':data.original_payment_id, 'action':'finance_correction_submit', 'status':'SUBMITTED'}
