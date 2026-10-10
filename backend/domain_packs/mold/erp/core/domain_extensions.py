"""Typed manufacturing and finance extensions of the local approval envelope."""
from decimal import Decimal
from sqlalchemy import select,func
from domain_packs.mold import models as m,domain_schemas as s
from domain_packs.mold.ports.errors import DomainError
from domain_packs.mold.authorization import require
from domain_packs.mold.ports.db import now
from domain_packs.mold.ports.events import record

TABLES={'contact_resolution':m.ContactResolution,'design_route':m.DesignDetail,'purchase_price':m.PriceDetail,'assembly_issue':m.AssemblyDetail,
        'trial_request':m.TrialDetail,'finance_correction':m.FinanceCorrectionDetail}


def active_user(db,user_id):
    user=db.get(m.User,user_id)
    if not user or not user.active:raise DomainError('ASSIGNMENT_BLOCKED','业务责任人员不存在或已停用')


def detail_data(db,subject):
    from domain_packs.mold.erp.core.domains import values,rows
    detail=values(db.get(TABLES[subject.kind],subject.id),('subject_id',))
    if subject.kind=='design_route':
        detail['items']=[values(r) for r in rows(db,m.DesignItem,design_id=subject.id)]
        from domain_packs.mold.erp.design import design_documents
        detail['attachments']=design_documents.cards(db,subject.id)
        if detail.get('source_snapshot'):
            detail['erp_order_material']={
                'source_system':detail.get('source_system'),
                'resource_type':detail.get('source_resource_type'),
                'resource_id':detail.get('source_resource_id'),
                'resource_version':detail.get('source_resource_version'),
                'as_of':detail.get('source_as_of'),
                'snapshot_hash':detail.get('source_snapshot_hash'),
                'document_revision':subject.revision,
                **(detail.get('source_summary') or {}),
            }
    if subject.kind=='trial_request':
        detail['results']=[]
        for result in rows(db,m.TrialResult,trial_id=subject.id):
            card=values(result)
            card['attachments']=[
                {
                    **values(link, exclude=('trial_result_id',)),
                    'filename': blob.filename,
                    'media_type': blob.media_type,
                    'size': blob.size,
                    'sha256': blob.sha256,
                }
                for link, blob in db.execute(
                    select(m.TrialResultAttachment, m.FileObject)
                    .join(m.FileObject, m.FileObject.id == m.TrialResultAttachment.file_id)
                    .where(m.TrialResultAttachment.trial_result_id == result.id)
                    .order_by(m.TrialResultAttachment.version, m.TrialResultAttachment.id)
                )
            ]
            detail['results'].append(card)
    if subject.kind=='assembly_issue':detail['execution']=[values(r) for r in rows(db,m.AssemblyExecution,assembly_id=subject.id)]
    if subject.kind=='finance_correction':
        original=db.get(m.PaymentConfirmation,detail['original_payment_id'])
        if original:
            detail['amount']=str(original.amount)
            detail['currency']=original.currency
            detail['reversal_amount']=str(-original.amount)
            detail['original_payment']={'id':original.id,'request_id':original.request_id,
                'amount':str(original.amount),'currency':original.currency,
                'paid_date':original.paid_date.isoformat(),'reference':original.reference}
            from domain_packs.mold.erp.finance.correction_allocations import detail_effects
            detail['allocation_effects']=detail_effects(db,subject,original)
    return detail


def persist(db,user,subject,detail):
    from domain_packs.mold.erp.core.domains import require_source,authorize
    kind=subject.kind
    if kind=='contact_resolution':
        from domain_packs.mold.erp.change.contact_lifecycle import persist_resolution
        persist_resolution(db,user,subject,detail)
    elif kind=='design_route':
        active_user(db,detail.reviewer_id)
        if len({i.material_id for i in detail.items})!=len(detail.items):raise DomainError('BOM_DUPLICATE','BOM 中物料不能重复')
        profile=db.get(m.ProjectProfile,subject.project_id)
        for item in detail.items:
            if not db.get(m.Material,item.material_id):raise DomainError('MATERIAL_UNKNOWN','BOM 物料无效')
            if profile and profile.execution_mode=='FULL_OUTSOURCE' and item.route=='INTERNAL':
                raise DomainError('MODE_CONFLICT','整套委外项目不能下发内部加工路线')
            if item.task_id:
                task=db.get(m.PlanTask,item.task_id)
                if not task:raise DomainError('TASK_UNKNOWN','关联计划任务不存在')
                plan=require_source(db,task.plan_id,subject.project_id,{'project_plan','plan_change'})
                authorize(db,user,plan,'read')
            db.add(m.DesignItem(design_id=subject.id,**item.model_dump()))
        db.add(m.DesignDetail(subject_id=subject.id,**detail.model_dump(exclude={'items'})))
    elif kind=='purchase_price':
        material=db.get(m.Material,detail.material_id);supplier=db.get(m.Supplier,detail.supplier_id)
        if not material or not supplier or not supplier.active or supplier.category!=material.category or subject.category!=material.category:
            raise DomainError('PRICE_SCOPE','物料、供应商与价格责任域必须一致')
        if detail.valid_to<detail.valid_from:raise DomainError('DATE_INVALID','价格有效期无效')
        db.add(m.PriceDetail(subject_id=subject.id,**detail.model_dump()))
    elif kind=='assembly_issue':
        source=require_source(db,detail.design_id,subject.project_id,{'design_route'});authorize(db,user,source,'read')
        active_user(db,detail.supervisor_id)
        db.add(m.AssemblyDetail(subject_id=subject.id,**detail.model_dump()))
    elif kind=='trial_request':
        source=require_source(db,detail.assembly_id,subject.project_id,{'assembly_issue'});authorize(db,user,source,'read')
        active_user(db,detail.responsible_id)
        if db.get(m.AssemblyDetail,source.id).execution_status!='DONE':raise DomainError('ASSEMBLY_INCOMPLETE','装配尚未人工确认完成')
        db.add(m.TrialDetail(subject_id=subject.id,**detail.model_dump()))
    elif kind=='finance_correction':
        original=db.get(m.PaymentConfirmation,detail.original_payment_id)
        if not original or original.amount<=0 or original.reversal_of_id:raise DomainError('PAYMENT_INVALID','只能对有效正向付款记录申请冲正')
        source=require_source(db,original.request_id,subject.project_id,{'supplier_payment'});authorize(db,user,source,'read')
        if source.category!=subject.category:raise DomainError('SCOPE_MISMATCH','财务冲正责任域不一致')
        if detail.reversal_date<original.paid_date or detail.reversal_date>now().date():raise DomainError('DATE_INVALID','冲正日期须在原付款日至今天之间')
        db.add(m.FinanceCorrectionDetail(subject_id=subject.id,**detail.model_dump()))


def apply(db,user,subject):
    from domain_packs.mold.erp.core.domains import rows,require_source
    kind=subject.kind
    if kind=='contact_resolution':
        from domain_packs.mold.erp.change.contact_lifecycle import activate_resolution
        activate_resolution(db,user,db.get(m.ContactResolution,subject.id))
    elif kind=='design_route':
        detail=db.get(m.DesignDetail,subject.id)
        # The migrated ERP reviewer field is legacy reference data.  MoldPilot
        # BPM owns this approval now, so the ERP write must be gated by an
        # approval action on the current BPM design-review stage rather than
        # by a possibly different legacy reviewer_id.
        design_review_approved=db.scalar(select(m.ApprovalAction.id).join(
            m.ApprovalInstance, m.ApprovalInstance.id==m.ApprovalAction.instance_id
        ).join(m.ApprovalSeat, m.ApprovalSeat.id==m.ApprovalAction.seat_id).where(
            m.ApprovalInstance.resource_type=='business_subject',
            m.ApprovalInstance.resource_id==subject.id,
            m.ApprovalInstance.revision==subject.revision,
            m.ApprovalInstance.round_no==subject.round_no,
            m.ApprovalSeat.stage_index==0,
            m.ApprovalAction.decision=='APPROVE').limit(1))
        if not design_review_approved:raise DomainError('DESIGN_REVIEW_REQUIRED','本轮须有 MoldPilot 设计主管审批同意记录')
        existing=db.scalar(select(m.BusinessSubject.id).where(m.BusinessSubject.project_id==subject.project_id,
            m.BusinessSubject.kind=='design_route',m.BusinessSubject.status=='EFFECTIVE',m.BusinessSubject.id!=subject.id).limit(1))
        if existing:raise DomainError('DESIGN_CHANGE_REQUIRED','项目已有生效设计；新设计须关联工程变更，不能直接替换')
        integration_payload = detail.source_snapshot if isinstance(detail.source_snapshot, dict) else {}
        if integration_payload.get('integration_type') == 'erp_design_upload':
            # The local BPM decision is the only gate for the ERP write.  Keep
            # the call idempotent by reusing a prior receipt if an operator is
            # retrying a completed local instance.
            session_id = str(integration_payload.get('session_id') or '')
            prior = db.scalar(select(m.AuditEvent).where(
                m.AuditEvent.action == 'erp_design_upload.synced',
                m.AuditEvent.resource_id == session_id,
            ).order_by(m.AuditEvent.created_at.desc())) if session_id else None
            if not prior:
                from domain_packs.mold import erp_direct
                try:
                    result = erp_direct.import_design_upload(
                        payload={
                            'session_id': int(integration_payload['session_id']),
                            'sheet_type': integration_payload.get('sheet_type'),
                            'mold_code': integration_payload.get('mold_code'),
                            'preview_rows': integration_payload.get('preview_rows') or [],
                            'design_order_type': integration_payload.get('design_order_type') or 'new_model',
                            'urgency_level': integration_payload.get('urgency_level') or 'normal',
                            'expected_date': integration_payload.get('expected_date'),
                            'purchase_reason': integration_payload.get('purchase_reason'),
                            'remark': integration_payload.get('remark'),
                            'allow_duplicate': bool(integration_payload.get('allow_duplicate')),
                            'import_mode': 'new_request',
                        },
                        mold_user_name=getattr(user, 'username', None) or 'admin',
                    )
                except Exception as error:
                    raise DomainError('ERP_SYNC_FAILED', f'审批已完成，但写入 ERP 失败：{error}', 502) from error
                nested = result.get('result') if isinstance(result, dict) else {}
                nested = nested if isinstance(nested, dict) else {}
                request_no = str(nested.get('requestNo') or nested.get('request_no') or '').strip()
                approval_instance = db.scalar(select(m.ApprovalInstance).where(
                    m.ApprovalInstance.resource_type == 'business_subject',
                    m.ApprovalInstance.resource_id == subject.id,
                ).order_by(m.ApprovalInstance.created_at.desc()))
                record(db, user, 'erp_design_upload.synced', session_id, {
                    'status': 'IMPORTED', 'request_no': request_no,
                    'request_id': nested.get('requestId') or nested.get('request_id'),
                    'message': str(nested.get('message') or nested.get('successMessage') or '已写入 ERP'),
                    'subject_id': subject.id,
                    'instance_id': approval_instance.id if approval_instance else None,
                    'submitted_by': integration_payload.get('submitted_by'),
                }, recipients=[integration_payload.get('submitted_by')] if integration_payload.get('submitted_by') else None)
    elif kind=='purchase_price':
        price=db.get(m.PriceDetail,subject.id)
        # Parent project lock in domains.apply serializes overlapping versions in this project.
        overlap=db.scalar(select(m.PriceDetail.subject_id).join(m.BusinessSubject).where(
            m.BusinessSubject.project_id==subject.project_id,m.BusinessSubject.kind==kind,m.BusinessSubject.status=='EFFECTIVE',
            m.PriceDetail.supplier_id==price.supplier_id,m.PriceDetail.material_id==price.material_id,
            m.PriceDetail.currency==price.currency,m.PriceDetail.valid_from<=price.valid_to,m.PriceDetail.valid_to>=price.valid_from).limit(1))
        if overlap:raise DomainError('PRICE_OVERLAP','同一项目、供应商、物料及币种的价格有效期不能重叠')
    elif kind=='assembly_issue':
        detail=db.get(m.AssemblyDetail,subject.id)
        source=require_source(db,detail.design_id,subject.project_id,{'design_route'})
        for item in rows(db,m.DesignItem,design_id=source.id):
            if item.task_id and db.get(m.PlanTask,item.task_id).status!='DONE':
                raise DomainError('ASSEMBLY_PREREQUISITE','装配前置加工任务尚未完成')
    elif kind=='trial_request':
        detail=db.get(m.TrialDetail,subject.id)
        require_source(db,detail.assembly_id,subject.project_id,{'assembly_issue'})
        if db.get(m.AssemblyDetail,detail.assembly_id).execution_status!='DONE':raise DomainError('ASSEMBLY_INCOMPLETE','装配未完成')
    elif kind=='finance_correction':
        detail=db.get(m.FinanceCorrectionDetail,subject.id)
        original=db.get(m.PaymentConfirmation,detail.original_payment_id)
        request=db.get(m.PaymentRequestDetail,original.request_id)
        db.scalar(select(m.PaymentStage).where(m.PaymentStage.id==request.stage_id).with_for_update())
        db.refresh(request)
        if detail.reversal_id or db.scalar(select(m.PaymentConfirmation.id).where(m.PaymentConfirmation.reversal_of_id==original.id)):
            raise DomainError('ALREADY_REVERSED','原付款已经冲正，禁止重复冲正')
        from domain_packs.mold.erp.finance.correction_allocations import approved_effects, append_reversal
        allocation_effects=approved_effects(db,subject,original)
        # Approval attests to supplied actual reversal evidence; never call a bank from here.
        reversal=m.PaymentConfirmation(request_id=original.request_id,amount=-original.amount,currency=original.currency,
            paid_date=detail.reversal_date,reference='REV-'+subject.id,evidence=detail.reversal_evidence,
            confirmed_by=user.id,reversal_of_id=original.id)
        db.add(reversal);db.flush();detail.reversal_id=reversal.id
        # Restore the same authorization reservation. Net paid decreases; total encumbrance is unchanged.
        request.reservation+=original.amount
        if request.reservation>request.amount:raise DomainError('RESERVATION_CONFLICT','冲正后的授权占用超限')
        append_reversal(db,user,subject,original,reversal,allocation_effects)
