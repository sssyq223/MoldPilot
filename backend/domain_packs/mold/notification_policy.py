"""Mold event titles and revocation-aware notification visibility."""
from domain_packs.mold import models as m
from domain_packs.mold.ports.errors import DomainError

TITLES = {
    "approval.pending": "有新的审批待办",
    "approval.assignment.blocked": "审批人员配置需要处理",
    "approval.reminder": "审批办理即将到期",
    "approval.overdue": "审批办理已经超时",
    "approval.timer.failed": "审批定时事件需要处理",
    "approval.incident.retried": "审批事件已重新处理",
    "business.effective": "业务单据已生效",
    "internal_start.department_handoff": "项目已正式开工，请核对计划交接",
    "start_notice.department_handoff": "开工通知待部门核对",
    "admin_start_notice.pending": "中标后内部开工通知待超级管理员处理",
    "document.ocr.completed": "PDF 识别阶段已完成，请查看结果",
    "document.ocr.failed": "PDF 识别失败，需要重试或人工处理",
    "document.type.confirmation.pending": "PDF 类型等待人工确认",
    "order.execution.draft.created": "采购执行草稿已生成",
    "contact.created": "有新的工程联络单待协调",
    "contact.task_created": "工程联络事项待分派",
    "contact.assigned": "有新的工程联络事项待处理",
    "contact.responded": "工程联络事项已有反馈",
    "contact.attachment_added": "工程联络单有新附件待核对",
    "contact.resolution_effective": "工程联络处理方案已批准，请按影响项落实",
    "plan.change.effective": "项目计划变更已生效，请核对受影响节点",
    "plan.department_confirmation.pending": "项目计划变更影响范围待部门确认",
    "plan.department_confirmation.confirmed": "项目计划变更部门影响已确认",
}


def _document_intake(db, user, event):
    from agent_core.host_ports import host_ports
    from domain_packs.mold.erp.commercial import contract_intake
    job = db.get(m.DocumentOcrJob,event.resource_id)
    row = db.get(m.DocumentIntakeFile,job.intake_file_id) if job else None
    if not row:
        raise DomainError('NOT_FOUND','文档任务不存在',404)
    intake = contract_intake.load(db,user,row.intake_id)
    for item in contract_intake._intake_files(db,intake.id):
        host_ports().uploaded_file(db,user,item.file_id)
    return intake


def target(db, user, event):
    if event.kind.startswith('document.ocr.'):
        return {'conversation_id':_document_intake(db,user,event).conversation_id}
    return {}


def title(kind):
    return TITLES.get(kind, "业务处理状态已更新")


def permitted(db, user, event):
    from domain_packs.mold.erp.core.business import approval_detail, request_access
    from domain_packs.mold.erp.change.contacts import require
    from domain_packs.mold.erp.core.domains import authorize
    from domain_packs.mold.erp.procurement.procurement import order_access
    if not user or not user.active:
        return False
    try:
        if event.kind.startswith('document.ocr.'):
            _document_intake(db,user,event)
        elif event.kind.startswith("approval."):
            instance = db.get(m.ApprovalInstance, event.resource_id)
            if not instance:
                return False
            approval_detail(db, user, instance)
        elif event.kind.startswith("contact."):
            case = db.get(m.ContactCase, event.resource_id)
            if not case:
                return False
            require(db, user, "read", case)
        elif event.kind == "start_notice.department_handoff":
            notice = db.get(m.StartNotice, event.resource_id)
            if not notice:
                return False
            require(db, user, "project.read", {"project_id": notice.project_id})
        elif event.kind == "admin_start_notice.pending":
            draft = db.get(m.AdminStartNoticeDraft, event.resource_id)
            if not draft or not user.super_admin:
                return False
        elif (subject := db.get(m.BusinessSubject, event.resource_id)) is not None:
            authorize(db, user, subject, "read")
        elif (order := db.get(m.PurchaseOrder, event.resource_id)) is not None:
            order_access(db, user, order, "order.read")
        elif (request := db.get(m.PurchaseRequest, event.resource_id)) is not None:
            request_access(db, user, request, "purchase.read")
        else:
            return False
        return True
    except DomainError:
        return False
