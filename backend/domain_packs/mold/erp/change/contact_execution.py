"""Bind reported change work to its approved plan and formal start evidence."""
from sqlalchemy import select, func

from domain_packs.mold import models as m
from domain_packs.mold.ports.errors import DomainError


def preview_basis(db,user,case,data):
    plan_id=data.execution_plan_id
    start_id=data.execution_start_id
    if not plan_id and not start_id:
        return {'status':'UNLINKED_FACT','plan_id':None,'start_subject_id':None,
                'meaning':'只记录已发生反馈；未证明在本次已批准方案及开工下执行。'}
    if not plan_id or not start_id:
        raise DomainError('EXECUTION_BASIS_INCOMPLETE','设变执行反馈须同时关联方案和正式开工通知',409)
    if case.mode!='ONLINE' or case.change_type!='CHANGE':
        raise DomainError('EXECUTION_BASIS_NOT_APPLICABLE','方案及开工关联用于线上设变，不能把历史补录或普通协作伪装为线上执行',409)
    from domain_packs.mold.erp.change.contact_lifecycle import approved
    from domain_packs.mold.erp.project.change_start import effective_notice
    resolution=approved(db,user,case)
    if resolution.subject_id!=plan_id or effective_notice(db,case,resolution)!=start_id:
        raise DomainError('EXECUTION_BASIS_MISMATCH','反馈须引用本联络单当前已批准方案及对应生效开工通知',409)
    issued_at=db.scalar(select(func.min(m.InternalStartDispatch.dispatched_at)).where(
        m.InternalStartDispatch.start_subject_id==start_id))
    if not issued_at or data.actual_completed_at<issued_at:
        raise DomainError('EXECUTION_PRECEDES_START','实际完成时间早于本次正式开工生效，不能追认成该通知下执行；可保留为未关联的事实反馈',409)
    subject=db.get(m.BusinessSubject,plan_id)
    return {'status':'LINKED','plan_id':plan_id,'plan_revision':subject.revision,
            'plan_number':subject.number,'start_subject_id':start_id,
            'start_number':db.get(m.BusinessSubject,start_id).number,'start_issued_at':issued_at.isoformat()}


def latest_basis(db,task):
    record=db.scalar(select(m.ContactRecord).where(m.ContactRecord.case_id==task.case_id,
        m.ContactRecord.kind=='RESPONDED',m.ContactRecord.detail['task_id'].as_string()==task.id)
        .order_by(m.ContactRecord.created_at.desc(),m.ContactRecord.id.desc()).limit(1))
    return record.detail.get('execution_basis') if record else None


def require_current_basis(db,case,task,resolution):
    if case.mode!='ONLINE' or case.change_type!='CHANGE':
        return
    from domain_packs.mold.erp.project.change_start import effective_notice
    basis=latest_basis(db,task) or {}
    start_id=effective_notice(db,case,resolution)
    if (basis.get('status')!='LINKED' or basis.get('plan_id')!=resolution.subject_id
            or not start_id or basis.get('start_subject_id')!=start_id):
        raise DomainError('EXECUTION_BASIS_STALE','反馈未关联本次生效方案与开工，不能直接复验合格或关闭；须保留原反馈并重新核对执行结果',409)
