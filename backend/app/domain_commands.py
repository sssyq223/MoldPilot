"""Human-only fact confirmation commands; never registered as Agent tools."""
from datetime import date
from typing import Literal
from decimal import Decimal
from pydantic import Field, ValidationError
from sqlalchemy import select, func
from . import models as m
from .schemas import StrictModel
from .authorization import require
from .errors import DomainError
from .events import record
from .db import now
from .domains import rows, require_source, authorize
from .procurement import line_context, order_access, move_stock


class Evidence(StrictModel):
    evidence: str = Field(min_length=1,max_length=4000)


class OrderIssue(Evidence):
    version: int


class QuantityEvidence(Evidence):
    quantity: Decimal = Field(gt=0,max_digits=18,decimal_places=6)
    reference: str = Field(min_length=1,max_length=150)


class Shipment(QuantityEvidence):
    shipped_date: date


class Receipt(QuantityEvidence):
    warehouse_id: str


class Inspection(Evidence):
    accepted_quantity: Decimal = Field(ge=0,max_digits=18,decimal_places=6)
    rejected_quantity: Decimal = Field(ge=0,max_digits=18,decimal_places=6)


class ExceptionReport(Evidence):
    reason: str = Field(min_length=1,max_length=4000)
    expected_ship_date: date | None = None


class Payment(Evidence):
    amount: Decimal = Field(gt=0,max_digits=18,decimal_places=2)
    currency: str = Field(pattern=r'^[A-Z]{3}$')
    paid_date: date
    reference: str = Field(min_length=1,max_length=100)


class TaskExecution(Evidence):
    action: Literal['START','DONE']
    actual_date: date


class Recheck(Evidence):
    passed: bool


class WarehouseScope(Evidence):
    start_date: date


class TrialConclusion(Evidence):
    passed: bool
    actual_date: date
    findings: str = Field(min_length=1,max_length=4000)
    change_id: str | None = None


COMMANDS={
    'assembly.execute':(m.BusinessSubject,TaskExecution,'assembly.execute'),
    'trial.confirm':(m.BusinessSubject,TrialConclusion,'trial.confirm'),
    'order.issue':(m.PurchaseOrder,OrderIssue,'order.issue'),
    'shipment.confirm':(m.OrderLine,Shipment,'shipment.confirm'),
    'exception.report':(m.OrderLine,ExceptionReport,'exception.report'),
    'exception.close':(m.DeliveryException,Evidence,'exception.close'),
    'receipt.confirm':(m.SupplierShipment,Receipt,'receipt.confirm'),
    'inspection.confirm':(m.GoodsReceipt,Inspection,'inspection.confirm'),
    'stock.issue':(m.StockBalance,QuantityEvidence,'stock.issue'),
    'warehouse.configure':(m.Warehouse,WarehouseScope,'warehouse.configure'),
    'finance.condition':(m.PaymentStage,Evidence,'finance.condition'),
    'finance.confirm':(m.BusinessSubject,Payment,'finance.confirm'),
    'plan.execute':(m.PlanTask,TaskExecution,'plan.execute'),
    'change.implement':(m.ChangeImpact,Evidence,'change.implement'),
    'change.recheck':(m.ChangeImpact,Recheck,'change.recheck'),
    'change.close':(m.BusinessSubject,Evidence,'engineering_change.execute'),
    'business.retry_apply':(m.BusinessSubject,Evidence,None),
}


def get_context(db,resource):
    if isinstance(resource,m.BusinessSubject):return {'project_id':resource.project_id,'category':resource.category,'warehouse_id':resource.warehouse_id}
    if isinstance(resource,m.PurchaseOrder):return {'project_id':resource.project_id}
    if isinstance(resource,m.OrderLine):return line_context(db,resource)[2]
    if isinstance(resource,m.DeliveryException):return get_context(db,db.get(m.OrderLine,resource.order_line_id))
    if isinstance(resource,m.SupplierShipment):return get_context(db,db.get(m.OrderLine,resource.order_line_id))
    if isinstance(resource,m.GoodsReceipt):return {**get_context(db,db.get(m.SupplierShipment,resource.shipment_id)),'warehouse_id':resource.warehouse_id}
    if isinstance(resource,m.StockBalance):return {'project_id':resource.project_id,'warehouse_id':resource.warehouse_id,'category':db.get(m.Material,resource.material_id).category}
    if isinstance(resource,m.Warehouse):return {'warehouse_id':resource.id}
    if isinstance(resource,m.PaymentStage):return get_context(db,db.get(m.BusinessSubject,resource.contract_id))
    if isinstance(resource,m.PlanTask):return get_context(db,db.get(m.BusinessSubject,resource.plan_id))
    if isinstance(resource,m.ChangeImpact):return get_context(db,db.get(m.BusinessSubject,resource.change_id))
    raise DomainError('RESOURCE_UNKNOWN','业务资源未登记')


def validate_command(db,user,key,resource_id,payload,lock=False):
    if key not in COMMANDS:raise DomainError('ACTION_UNKNOWN','人工操作未登记')
    model,schema,permission=COMMANDS[key]
    try:data=schema.model_validate(payload)
    except ValidationError as e:raise DomainError('FORM_INVALID','确认内容不合法：'+e.errors()[0]['msg']) from None
    q=select(model).where(model.id==resource_id)
    # Stock mutations lock warehouse then balance in move_stock, including a first insert.
    resource=db.scalar(q.with_for_update() if lock and key!='stock.issue' else q)
    if not resource:raise DomainError('NOT_FOUND','资源不存在或无权访问',404)
    context=get_context(db,resource)
    if isinstance(data,Receipt):context['warehouse_id']=data.warehouse_id
    if isinstance(resource,m.PurchaseOrder):order_access(db,user,resource,permission)
    else:require(db,user,permission or f'{resource.kind}.execute',context)
    return resource,data,context


def execute_command(db,user,key,resource_id,payload):
    resource,data,context=validate_command(db,user,key,resource_id,payload,lock=True)
    result={'resource_id':resource_id,'action':key}
    if key=='order.issue':
        if resource.version!=data.version or resource.status!='DRAFT':raise DomainError('VERSION_CONFLICT','订单版本或状态已变化',409)
        if not resource.supplier_id:raise DomainError('ORDER_INCOMPLETE','尚未选定供应商')
        if db.get(m.Project,resource.project_id).status!='ACTIVE':raise DomainError('PROJECT_BLOCKED','项目未处于执行状态')
        if any(l.unit_price is None for l in rows(db,m.OrderLine,order_id=resource.id)):raise DomainError('PRICE_REQUIRED','正式下单前须核对全部价格')
        for line in rows(db,m.OrderLine,order_id=resource.id):
            snapshot=db.get(m.OrderPriceSnapshot,line.id)
            price=db.get(m.PriceDetail,snapshot.price_subject_id) if snapshot else None
            subject=db.get(m.BusinessSubject,snapshot.price_subject_id) if snapshot else None
            if not price or subject.status!='EFFECTIVE' or not price.valid_from<=now().date()<=price.valid_to:
                raise DomainError('PRICE_NOT_APPROVED','正式下单必须使用仍有效的审批价格')
            if snapshot.unit_price!=line.unit_price or snapshot.currency!=resource.currency:
                raise DomainError('PRICE_MISMATCH','价格快照与成交方案不一致')
        resource.status='ISSUED';resource.version+=1;resource.issued_by=user.id;resource.issued_at=now()
    elif key in {'shipment.confirm','exception.report'}:
        order,material,scope=line_context(db,resource)
        if order.status!='ISSUED':raise DomainError('ORDER_NOT_ISSUED','只有正式订单可登记供应商发货或异常')
        if key=='shipment.confirm':
            shipped=db.scalar(select(func.coalesce(func.sum(m.SupplierShipment.quantity),0)).where(m.SupplierShipment.order_line_id==resource.id))
            if shipped+data.quantity>resource.quantity:raise DomainError('QUANTITY_OVERFLOW','累计发货超过订单数量',409)
            event=m.SupplierShipment(order_line_id=resource.id,confirmed_by=user.id,**data.model_dump());db.add(event);db.flush();result['shipment_id']=event.id
        else:
            event=m.DeliveryException(order_line_id=resource.id,reported_by=user.id,**data.model_dump());db.add(event);db.flush();result['exception_id']=event.id
    elif key=='exception.close':
        if resource.status!='OPEN':raise DomainError('EXCEPTION_CLOSED','异常已关闭')
        resource.status='CLOSED';resource.closed_by=user.id;resource.resolution=data.evidence
    elif key=='receipt.confirm':
        warehouse=db.get(m.Warehouse,data.warehouse_id)
        if not warehouse or not warehouse.active or not warehouse.scope_confirmed:raise DomainError('STOCK_SCOPE_UNCONFIRMED','仓库管理范围未确认')
        received=db.scalar(select(func.coalesce(func.sum(m.GoodsReceipt.quantity),0)).where(m.GoodsReceipt.shipment_id==resource.id))
        if received+data.quantity>resource.quantity:raise DomainError('QUANTITY_OVERFLOW','累计实收超过发货数量',409)
        receipt=m.GoodsReceipt(shipment_id=resource.id,received_by=user.id,**data.model_dump());db.add(receipt);db.flush();result['receipt_id']=receipt.id
    elif key=='inspection.confirm':
        if db.scalar(select(m.ReceiptInspection.id).where(m.ReceiptInspection.receipt_id==resource.id)):raise DomainError('ALREADY_CONFIRMED','此收货记录已经确认检验',409)
        if data.accepted_quantity+data.rejected_quantity!=resource.quantity:raise DomainError('QUANTITY_MISMATCH','合格和不合格数量合计必须等于实收数量')
        inspection=m.ReceiptInspection(receipt_id=resource.id,inspector_id=user.id,**data.model_dump());db.add(inspection);db.flush()
        shipment=db.get(m.SupplierShipment,resource.shipment_id);line=db.get(m.OrderLine,shipment.order_line_id)
        if data.accepted_quantity:
            move_stock(db,user,resource.warehouse_id,line.material_id,context['project_id'],data.accepted_quantity,'RECEIPT_ACCEPTED','inspection:'+inspection.id,data.evidence)
        result['inspection_id']=inspection.id
    elif key=='stock.issue':
        if db.get(m.Project,resource.project_id).status!='ACTIVE':raise DomainError('PROJECT_BLOCKED','项目当前不能进行生产发料')
        event=move_stock(db,user,resource.warehouse_id,resource.material_id,resource.project_id,-data.quantity,'ISSUE','issue:'+data.reference,data.evidence)
        result['movement_id']=event.id
    elif key=='warehouse.configure':
        if resource.scope_confirmed:raise DomainError('SCOPE_ALREADY_CONFIRMED','已启用的库存管理范围不能覆盖修改')
        resource.scope_confirmed=True;resource.start_date=data.start_date;resource.opening_evidence=data.evidence
    elif key=='finance.condition':
        require_source(db,resource.contract_id,context['project_id'],{'full_outsource_contract'})
        resource.condition_confirmed=True;resource.condition_evidence=data.evidence
    elif key=='finance.confirm':
        if resource.kind!='supplier_payment' or resource.status!='EFFECTIVE':raise DomainError('PAYMENT_NOT_AUTHORIZED','付款申请尚未审批生效')
        detail=db.get(m.PaymentRequestDetail,resource.id)
        db.scalar(select(m.PaymentStage).where(m.PaymentStage.id==detail.stage_id).with_for_update())
        if detail.currency!=data.currency or data.amount>detail.reservation:raise DomainError('PAYMENT_OVERFLOW','币种不一致或实付超出本次授权余额',409)
        confirmation=m.PaymentConfirmation(request_id=resource.id,confirmed_by=user.id,**data.model_dump());db.add(confirmation);db.flush()
        detail.reservation-=data.amount;result['payment_confirmation_id']=confirmation.id
    elif key=='plan.execute':
        if data.actual_date>now().date():raise DomainError('DATE_INVALID','实际执行日期不能在未来')
        plan=require_source(db,resource.plan_id,context['project_id'],{'project_plan','plan_change'})
        if db.get(m.Project,plan.project_id).status!='ACTIVE':raise DomainError('PROJECT_BLOCKED','项目当前不能执行计划任务')
        if data.action=='START':
            if resource.status!='PLANNED':raise DomainError('TASK_STATE','当前任务不能开工')
            for dependency in rows(db,m.TaskDependency,task_id=resource.id):
                if db.get(m.PlanTask,dependency.prerequisite_id).status!='DONE':raise DomainError('DEPENDENCY_INCOMPLETE','前置任务尚未完成')
            resource.actual_start=data.actual_date;resource.status='RUNNING'
        elif data.action=='DONE':
            if resource.status!='RUNNING' or data.actual_date<resource.actual_start:raise DomainError('TASK_STATE','完工状态或日期无效')
            resource.actual_end=data.actual_date;resource.status='DONE'
        else:raise DomainError('ACTION_UNKNOWN','任务仅允许开始或完成')
    elif key=='assembly.execute':
        if resource.kind!='assembly_issue' or resource.status!='EFFECTIVE':raise DomainError('ASSEMBLY_NOT_APPROVED','装配任务尚未审批生效')
        if db.get(m.Project,resource.project_id).status!='ACTIVE':raise DomainError('PROJECT_BLOCKED','项目当前不能装配')
        detail=db.get(m.AssemblyDetail,resource.id)
        if user.id!=detail.supervisor_id:raise DomainError('SUPERVISOR_REQUIRED','须由本装配任务指定的钳工主管确认')
        if data.actual_date>now().date():raise DomainError('DATE_INVALID','实际装配日期不能在未来')
        if data.action=='START':
            if detail.execution_status!='NOT_STARTED':raise DomainError('ASSEMBLY_STATE','当前装配状态不能开始')
            detail.execution_status='RUNNING'
        else:
            start=db.scalar(select(m.AssemblyExecution).where(m.AssemblyExecution.assembly_id==resource.id,m.AssemblyExecution.action=='START'))
            if detail.execution_status!='RUNNING' or not start or data.actual_date<start.actual_date:raise DomainError('ASSEMBLY_STATE','装配完成状态或日期无效')
            detail.execution_status='DONE'
        db.add(m.AssemblyExecution(assembly_id=resource.id,confirmed_by=user.id,**data.model_dump()))
    elif key=='trial.confirm':
        if resource.kind!='trial_request' or resource.status!='EFFECTIVE':raise DomainError('TRIAL_NOT_APPROVED','试模尚未审批生效')
        if db.get(m.Project,resource.project_id).status!='ACTIVE':raise DomainError('PROJECT_BLOCKED','项目当前不能登记试模')
        if data.actual_date>now().date():raise DomainError('DATE_INVALID','实际试模日期不能在未来')
        if db.scalar(select(m.TrialResult.id).where(m.TrialResult.trial_id==resource.id)):raise DomainError('ALREADY_CONFIRMED','试模结论已经登记；再次试模须重新申请')
        if not data.passed:
            require_source(db,data.change_id,resource.project_id,{'engineering_change'},('DRAFT','SUBMITTED','EFFECTIVE','CLOSED','APPLY_BLOCKED'))
        elif data.change_id:require_source(db,data.change_id,resource.project_id,{'engineering_change'},('CLOSED',))
        outcome=m.TrialResult(trial_id=resource.id,confirmed_by=user.id,**data.model_dump());db.add(outcome);db.flush();result['trial_result_id']=outcome.id
    elif key=='change.implement':
        require_source(db,resource.change_id,context['project_id'],{'engineering_change'})
        if resource.implemented_by:raise DomainError('ALREADY_CONFIRMED','影响项已实施')
        task=db.scalar(select(m.PlanTask).where(m.PlanTask.id==resource.task_id).with_for_update())
        if resource.action=='CANCEL':task.status='STOPPED'
        elif resource.action in {'PAUSE','REWORK'}:task.status='PAUSED' if resource.action=='PAUSE' else 'REWORK_PENDING'
        resource.implemented_by=user.id;resource.implementation_evidence=data.evidence
    elif key=='change.recheck':
        if not resource.implemented_by:raise DomainError('IMPLEMENTATION_REQUIRED','须先记录实施证据')
        if resource.implemented_by==user.id:raise DomainError('INDEPENDENT_RECHECK','实施人与复检人须分离')
        if resource.recheck_passed is True:raise DomainError('ALREADY_CONFIRMED','复检合格记录不能覆盖')
        resource.rechecked_by=user.id;resource.recheck_passed=data.passed
    elif key=='change.close':
        if resource.kind!='engineering_change' or resource.status!='EFFECTIVE':raise DomainError('CHANGE_STATE','工程联络单尚未审批生效')
        impacts=rows(db,m.ChangeImpact,change_id=resource.id)
        if not impacts or any(not i.implemented_by or not i.recheck_passed for i in impacts):raise DomainError('RECHECK_REQUIRED','全部影响项实施并复检合格后才能关闭')
        resource.status='CLOSED'
    elif key=='business.retry_apply':
        if resource.status!='APPLY_BLOCKED':raise DomainError('INVALID_STATE','只有待业务生效单据可重试')
        from .domains import apply
        apply(db,user,resource)
    record(db,user,key,resource_id,{'evidence':data.evidence,'result':result})
    return result
