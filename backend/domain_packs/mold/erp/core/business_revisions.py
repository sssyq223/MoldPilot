"""Version and audit boundary for domain adapters that revise unexecuted forms."""
from sqlalchemy import select

from domain_packs.mold import models as m, domains
from domain_packs.mold.erp.core.domain_schemas import READ_FIELDS
from domain_packs.mold.ports.errors import DomainError
from domain_packs.mold.ports.events import record


REVISION_STATES = frozenset({'DRAFT', 'RETURNED', 'REJECTED', 'APPLY_BLOCKED'})


def validate(db, actor, subject, expected_revision):
    fields = domains.authorize(db, actor, subject, 'read')
    if '*' not in fields and not set(READ_FIELDS) <= set(fields):
        raise DomainError('FORM_REVISION_FORBIDDEN', '修订需要完整业务材料读取权限', 403)
    for action in ('create', 'submit'):
        domains.authorize(db, actor, subject, action)
    if subject.created_by != actor.id and not actor.super_admin:
        raise DomainError('FORM_REVISION_FORBIDDEN', '只有申请人或有业务权限的管理员可以修订原申请', 403)
    if subject.revision != expected_revision:
        raise DomainError('VERSION_CONFLICT', '原申请版本已变化，请重新查询并核对', 409)
    if subject.status not in REVISION_STATES:
        raise DomainError('FORM_REVISION_NOT_ALLOWED', '只能修订草稿、退回、驳回或尚未业务生效的阻断申请', 409)
    if db.scalar(select(m.ApprovalInstance.id).where(
            m.ApprovalInstance.resource_type == 'business_subject',
            m.ApprovalInstance.resource_id == subject.id,
            m.ApprovalInstance.status == 'RUNNING')):
        raise DomainError('FORM_REVISION_NOT_ALLOWED', '原申请仍有进行中的审批，须先退回或撤回', 409)


def revise(db, actor, subject, expected_revision, reason, update_detail):
    """Caller holds subject/project locks; its adapter validates all new fields.

    The original approval instances remain unchanged. Domain-specific updates
    and the new submission share the caller's transaction, so a failed new
    submission cannot leave a partially revised form behind.
    """
    validate(db, actor, subject, expected_revision)
    previous = db.scalar(select(m.ApprovalInstance).where(
        m.ApprovalInstance.resource_type == 'business_subject',
        m.ApprovalInstance.resource_id == subject.id,
        m.ApprovalInstance.round_no == subject.round_no).order_by(m.ApprovalInstance.revision.desc()))
    before = {**domains.values(subject), 'detail':domains.typed_detail(db, subject)}
    update_detail()
    subject.revision += 1
    subject.status = 'DRAFT'
    db.flush()
    after = {**domains.values(subject), 'detail':domains.typed_detail(db, subject)}
    record(db, actor, 'business.revised', subject.id, {
        'reason':reason, 'from_revision':expected_revision, 'to_revision':subject.revision,
        'previous_status':before['status'], 'previous_instance_id':previous.id if previous else None,
        'before':before, 'after':after,
        '_detail_access':[{'permission':subject.kind+'.read', 'scope':domains.scope(subject), 'fields':READ_FIELDS}],
    })
