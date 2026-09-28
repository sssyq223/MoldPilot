"""Human condition checks and application/revision of existing payment BPM."""
from decimal import Decimal

from pydantic import Field, ValidationError, model_validator
from sqlalchemy import select, func

from domain_packs.mold import models as m, domains, domain_schemas as s, workflow_selection
from domain_packs.mold.authorization import require, access
from domain_packs.mold.ports.errors import DomainError
from domain_packs.mold.ports.schemas import StrictModel
from domain_packs.mold.ports.bpm import content_hash
from domain_packs.mold.ports.confirmation_policy import agent_permission_mode_from_proposal
from domain_packs.mold.erp.core.project_locator import ResolvedProjectId


class ConditionInput(StrictModel):
    project_id: ResolvedProjectId
    project_version: int = Field(ge=1)
    stage_id: str = Field(min_length=1, max_length=36)
    evidence: str = Field(min_length=1, max_length=4000)
    evidence_by_rule: dict[str, str] = Field(default_factory=dict)
    special_approval_reference: str | None = Field(default=None, min_length=1, max_length=160)


class RequestInput(s.PaymentInput):
    project_id: ResolvedProjectId
    project_version: int = Field(ge=1)
    reason: str = Field(min_length=1, max_length=4000)
    workflow_definition_id: str = Field(min_length=1, max_length=36)
    material_review_id: str | None = Field(default=None, min_length=1, max_length=36)
    existing_subject_id: str | None = Field(default=None, min_length=1, max_length=36)
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


def parse(kind, arguments):
    try:
        return (ConditionInput if kind == 'supplier_payment_condition' else RequestInput).model_validate(arguments or {})
    except ValidationError as error:
        raise DomainError('INVALID_TOOL_INPUT', '供应商付款参数无效：'+error.errors()[0]['msg']) from None


def full_read(db, actor, subject):
    fields = domains.authorize(db, actor, subject, 'read')
    if '*' not in fields and not set(s.READ_FIELDS) <= set(fields):
        raise DomainError('PAYMENT_MATERIAL_FORBIDDEN', '须有完整合同及付款材料读取权限', 403)


def stage_context(db, actor, project_id, stage_id):
    stage = db.get(m.PaymentStage, stage_id)
    if not stage:
        raise DomainError('NOT_FOUND', '付款节点不存在或无权访问', 404)
    contract = domains.require_source(db, stage.contract_id, project_id, {'full_outsource_contract'})
    full_read(db, actor, contract)
    from domain_packs.mold.erp.commercial.contract_materials import require_current_stage
    require_current_stage(db, stage)
    return stage, contract


def workflow_options(db, actor, project_id, category, resource=None):
    resource = resource or m.BusinessSubject(kind='supplier_payment', project_id=project_id, category=category)
    if not all(access(db, actor, 'supplier_payment.'+action, domains.scope(resource)).allowed
               for action in ('read', 'create', 'submit')):
        return []
    full_read(db, actor, resource)
    return [{**workflow_selection.metadata(row, db),
             'requires_material_review':row.config.get('material_contract') is not None,
             'supports_special_approval':bool(row.config.get('supports_special_approval', False))}
            for row in workflow_selection.available(db, actor, resource)]


def workflow_mismatch_details(options):
    """Return only the authorized choices needed to recover a stale workflow id.

    A model may have copied an id from a neighboring contract query.  The
    business layer must reject it, but the structured observation should also
    expose the current payment choices so the generic harness can recover by
    selecting an explicitly returned id.  It never executes or rewrites the
    caller's arguments.
    """
    return {
        'expected_business_type': 'supplier_payment',
        'available_workflows': [
            {key: row[key] for key in
             ('id', 'name', 'version', 'process_key', 'business_type', 'category_id')}
            for row in options
        ],
    }


def balance(db, stage, existing=None):
    reservations = db.scalar(select(func.coalesce(func.sum(m.PaymentRequestDetail.reservation), 0))
                             .where(m.PaymentRequestDetail.stage_id == stage.id))
    own = db.get(m.PaymentRequestDetail, existing.id) if existing else None
    if own and own.stage_id == stage.id:
        reservations -= own.reservation
    paid = db.scalar(select(func.coalesce(func.sum(m.PaymentConfirmation.amount), 0))
                     .join(m.PaymentRequestDetail, m.PaymentRequestDetail.subject_id == m.PaymentConfirmation.request_id)
                     .where(m.PaymentRequestDetail.stage_id == stage.id))
    from domain_packs.mold.erp.commercial.contract_relations import allocated_total
    allocated = allocated_total(db, stage.contract_id, 'SUPPLIER_PAYMENT', stage_id=stage.id)
    return {'节点金额':str(stage.amount), '其他申请占用':str(reservations),
            '节点实付':str(paid), '历史实付分配':str(allocated),
            '可申请金额':str(stage.amount-reservations-paid-allocated)}


def preview(db, actor, kind, data):
    project = db.get(m.Project, data.project_id)
    if not project:
        raise DomainError('NOT_FOUND', '项目不存在或无权访问', 404)
    require(db, actor, 'project.read', {'project_id':project.id})
    if project.row_version != data.project_version:
        raise DomainError('VERSION_CONFLICT', '项目状态已变化，请重新查询', 409)
    stage, contract = stage_context(db, actor, project.id, data.stage_id)
    detail = db.get(m.ContractDetail, contract.id)
    supplier = db.get(m.Supplier, detail.supplier_id)
    display = {'项目':project.code+' · '+project.name, '项目版本':project.row_version,
               '合同':contract.number, '合同编号':detail.contract_number,
               '合同版本':contract.revision, '合同材料版本':detail.material_version,
               '供应商':supplier.name if supplier else '供应商资料缺失', '供应商标识':detail.supplier_id,
               '付款节点':stage.name, '节点标识':stage.id,
               '付款条件':stage.condition, '付款类型':(stage.condition_profile or {}).get('payment_type','PROGRESS'),
               '适用条件明细':(stage.condition_profile or {}).get('rules',[]),
               '已核验':stage.condition_confirmed,
               '核验依据':stage.condition_evidence or '尚未记录',
               '分项核验依据':stage.condition_evidence_map or {}, '币种':stage.currency}
    if kind == 'supplier_payment_condition':
        require(db, actor, 'finance.condition', domains.scope(contract))
        if stage.condition_confirmed:
            raise DomainError('PAYMENT_CONDITION_CONFIRMED', '付款条件已核验，不能覆盖原依据', 409)
        missing = [row.get('key') for row in (stage.condition_profile or {}).get('rules',[])
                   if row.get('applicable',True) and not data.evidence_by_rule.get(row.get('key'))]
        special = [row.get('key') for row in (stage.condition_profile or {}).get('rules',[])
                   if row.get('applicable',True) and row.get('special_approval_required')]
        if missing:
            raise DomainError('PAYMENT_CONDITION_MISSING','仍缺少适用付款条件依据：'+'、'.join(missing),409)
        if special and not data.special_approval_reference:
            raise DomainError('PAYMENT_SPECIAL_APPROVAL_REQUIRED','付款条件偏离约定，须提供特殊审批引用：'+'、'.join(special),409)
        if data.special_approval_reference and not special:
            raise DomainError('PAYMENT_SPECIAL_APPROVAL_NOT_APPLICABLE','当前付款条件没有声明需要特殊审批，不能挂接特殊审批引用',409)
        display.update({'操作':'核验供应商付款节点条件', '本次依据':data.evidence,
                        '本次分项依据':data.evidence_by_rule,
                        '特殊审批引用':data.special_approval_reference or '无',
                        '说明':'本人确认后记录条件核验；不提交付款申请、不审批、不登记实付。'})
        return display
    existing = db.get(m.BusinessSubject, data.existing_subject_id) if data.existing_subject_id else None
    if data.existing_subject_id:
        if not existing or existing.kind != 'supplier_payment' or existing.project_id != project.id:
            raise DomainError('NOT_FOUND', '原付款申请不存在或不属于本项目', 404)
        from domain_packs.mold.erp.core import business_revisions
        business_revisions.validate(db, actor, existing, data.subject_revision)
        if existing.category != contract.category:
            raise DomainError('SCOPE_MISMATCH', '原申请不能跨责任域修订', 409)
        if db.scalar(select(m.PaymentConfirmation.id).where(m.PaymentConfirmation.request_id == existing.id)):
            raise DomainError('PAYMENT_ALREADY_EXECUTED', '原申请已有实付历史，不能修订付款授权', 409)
    resource = existing or m.BusinessSubject(kind='supplier_payment', project_id=project.id, category=contract.category)
    full_read(db, actor, resource)
    for action in ('create', 'submit'):
        domains.authorize(db, actor, resource, action)
    if data.currency != stage.currency:
        raise DomainError('CURRENCY_OR_SCOPE', '付款币种与合同节点不一致')
    if not stage.condition_confirmed:
        raise DomainError('PAYMENT_CONDITION', '付款条件尚未由人员核验', 409)
    amounts = balance(db, stage, existing)
    if data.amount > Decimal(amounts['可申请金额']):
        raise DomainError('PAYMENT_OVERFLOW', '付款节点可申请余额不足', 409)
    available_workflows = workflow_options(db, actor, project.id, contract.category, existing)
    selected = next((row for row in available_workflows
                     if row['id'] == data.workflow_definition_id), None)
    if not selected:
        raise DomainError(
            'WORKFLOW_MISMATCH', '所选流程不是当前供应商付款审批流程，请使用本次返回的付款流程 ID', 409,
            details=workflow_mismatch_details(available_workflows),
        )
    special_reference = stage.special_approval_reference
    if special_reference and not selected['supports_special_approval']:
        raise DomainError(
            'PAYMENT_SPECIAL_APPROVAL_WORKFLOW_REQUIRED',
            '本付款节点存在特殊审批依据，所选流程未声明支持特殊审批，请选择标记为支持特殊审批的付款流程',
            409,
            details={
                'special_approval_reference': special_reference,
                'available_workflows': [
                    {key: row[key] for key in
                     ('id', 'name', 'version', 'process_key', 'business_type', 'category_id',
                      'supports_special_approval')}
                    for row in available_workflows if row['supports_special_approval']
                ],
            },
        )
    workflow_selection.validate_material_review(db, actor, db.get(m.WorkflowDefinition, data.workflow_definition_id), data.material_review_id)
    display.update({'操作':'提交供应商付款申请审批', '本次金额':str(data.amount), '申请原因':data.reason,
                    '余额核对':amounts, '审批流程':selected['name']+' · 第'+str(selected['version'])+'版',
                    '特殊审批依据':special_reference or '无',
                    '说明':'本人确认后提交 Agent BPM 并占用节点额度；审批生效才形成付款授权，不登记实付或操作 ERP 对账。'})
    if existing:
        display.update({'操作':'修订并重新提交供应商付款申请', '原申请':existing.number,
                        '原申请版本':existing.revision, '原申请状态':existing.status,
                        '原申请材料':domains.typed_detail(db, existing), '修订原因':data.revision_reason})
    return display


def confirm(db, actor, kind, data, proposal):
    if kind == 'supplier_payment_request' and data.existing_subject_id:
        db.scalar(select(m.BusinessSubject).where(m.BusinessSubject.id == data.existing_subject_id)
                  .with_for_update().execution_options(populate_existing=True))
    db.scalar(select(m.Project).where(m.Project.id == data.project_id).with_for_update().execution_options(populate_existing=True))
    stage = db.scalar(select(m.PaymentStage).where(m.PaymentStage.id == data.stage_id).with_for_update().execution_options(populate_existing=True))
    # Intent validation may have populated these before waiting for the project
    # lock. A concurrently replaced contract must not remain effective in memory.
    if stage:
        db.get(m.BusinessSubject, stage.contract_id, populate_existing=True)
        db.get(m.ContractDetail, stage.contract_id, populate_existing=True)
    if kind == 'supplier_payment_request' and data.existing_subject_id:
        db.get(m.PaymentRequestDetail, data.existing_subject_id, populate_existing=True)
    display = preview(db, actor, kind, data)
    if content_hash(display) != content_hash(proposal['display']):
        raise DomainError('VERSION_CONFLICT', '合同条件或付款余额已变化，请重新核对', 409)
    if kind == 'supplier_payment_condition':
        from domain_packs.mold.erp.core.domain_commands import execute_command
        execute_command(db, actor, 'finance.condition', data.stage_id, {
            'evidence':data.evidence,
            'evidence_by_rule':data.evidence_by_rule,
            'special_approval_reference':data.special_approval_reference,
        })
        return {'project_id':data.project_id, 'stage_id':data.stage_id, 'action':kind, 'status':'CONFIRMED'}
    detail = data.model_dump(include=set(s.PaymentInput.model_fields))
    if data.existing_subject_id:
        from domain_packs.mold.erp.core import business_revisions
        subject = db.get(m.BusinessSubject, data.existing_subject_id)
        current = db.get(m.PaymentRequestDetail, subject.id)
        def update_detail():
            domains.release_reservation(db, subject)
            for key, value in detail.items():
                setattr(current, key, value)
            subject.remark = data.reason
        business_revisions.revise(db, actor, subject, data.subject_revision, data.revision_reason, update_detail)
    else:
        _, contract = stage_context(db, actor, data.project_id, data.stage_id)
        subject = domains.create(db, actor, s.SubjectInput(kind='supplier_payment', project_id=data.project_id,
            category=contract.category, remark=data.reason, detail=detail))
    from domain_packs.mold.erp.core.business import submit_subject
    submitted = submit_subject(db, actor, subject.id, subject.revision, data.workflow_definition_id,
        material_review_id=data.material_review_id, agent_permission_mode=agent_permission_mode_from_proposal(proposal))
    return {'project_id':data.project_id, 'subject_id':subject.id, 'instance_id':submitted['instance_id'],
            'subject_revision':subject.revision, 'round_no':subject.round_no, 'action':kind, 'status':'SUBMITTED'}
