from collections import Counter, defaultdict
from decimal import Decimal
from pydantic import Field, model_validator
from sqlalchemy import select, func
from . import models as m
from .authorization import access, predicate, select_fields
from .db import now
from .errors import DomainError
from .schemas import StrictModel


class ProcurementPriceContextInput(StrictModel):
    project_id: str | None = Field(default=None, min_length=1, max_length=36)
    identifier: str | None = Field(default=None, min_length=1, max_length=200,
        description='项目编号/名称、料号/料品名称、价格单号、供应商、采购申请或订单线索。')

    @model_validator(mode='after')
    def one_locator(self):
        if bool(self.project_id)==bool(self.identifier):
            raise ValueError('project_id 和 identifier 须且只能填写一项')
        if self.identifier:
            self.identifier=self.identifier.strip()
            if not self.identifier:raise ValueError('线索不能为空')
        return self


def _strength(value,needle):
    if value is None:return 0
    value=str(value).casefold();needle=str(needle).casefold()
    return 100 if value==needle else 50 if needle in value else 0


def _project_card(db,user,project,matched_by=()):
    fields=access(db,user,'project.read',{'project_id':project.id}).fields
    card=select_fields({'id':project.id,'code':project.code,'name':project.name,'status':project.status,
                        'row_version':project.row_version},fields)
    card['matched_by']=sorted(set(matched_by))
    return card


def _visible_projects(db,user):
    rows=list(db.scalars(select(m.Project).where(predicate(db,user,'project.read',{'project_id':m.Project.id}))
                         .order_by(m.Project.code).limit(501)))
    return rows[:500],len(rows)>500


def _subject_data(db,user,subjects):
    from .domains import data as subject_data
    result=[]
    for subject in subjects:
        try:result.append(subject_data(db,user,subject))
        except DomainError:continue
    return result


def _price_subjects(db,user,project_ids,allowed_tools):
    if 'query_procurement_price_context' not in allowed_tools and 'query_purchase_price' not in allowed_tools:return []
    q=select(m.BusinessSubject).where(m.BusinessSubject.project_id.in_(project_ids),
        m.BusinessSubject.kind=='purchase_price').order_by(m.BusinessSubject.created_at.desc(),m.BusinessSubject.id).limit(501)
    return _subject_data(db,user,db.scalars(q))


def _resolve(db,user,data:ProcurementPriceContextInput,allowed_tools:set[str]):
    visible,truncated=_visible_projects(db,user)
    by_id={project.id:project for project in visible}
    if data.project_id:
        project=by_id.get(data.project_id)
        return project,([] if project else None),truncated
    scores=defaultdict(int);reasons=defaultdict(list)
    def add(project_id,value,label):
        if project_id not in by_id:return
        score=_strength(value,data.identifier)
        if score:
            scores[project_id]=max(scores[project_id],score)
            reasons[project_id].append(label)
    for project in visible:
        add(project.id,project.id,'项目ID');add(project.id,project.code,'项目编号');add(project.id,project.name,'项目名称')
    if by_id:
        prices=_price_subjects(db,user,list(by_id),allowed_tools)
        material_ids=set();supplier_ids=set()
        for subject in prices:
            add(subject.get('project_id'),subject.get('id'),'价格单ID')
            add(subject.get('project_id'),subject.get('number'),'价格单号')
            detail=subject.get('detail') if isinstance(subject.get('detail'),dict) else {}
            add(subject.get('project_id'),detail.get('quote_evidence'),'报价依据')
            if detail.get('material_id'):material_ids.add(detail['material_id'])
            if detail.get('supplier_id'):supplier_ids.add(detail['supplier_id'])
        for material in db.scalars(select(m.Material).where(m.Material.id.in_(material_ids)).limit(501)) if material_ids else []:
            for subject in prices:
                if (subject.get('detail') or {}).get('material_id')==material.id:
                    add(subject.get('project_id'),material.code,'料号')
                    add(subject.get('project_id'),material.name,'料品名称')
                    add(subject.get('project_id'),material.category,'采购类别')
        for supplier in db.scalars(select(m.Supplier).where(m.Supplier.id.in_(supplier_ids)).limit(501)) if supplier_ids else []:
            for subject in prices:
                if (subject.get('detail') or {}).get('supplier_id')==supplier.id:
                    add(subject.get('project_id'),supplier.code,'供应商编号')
                    add(subject.get('project_id'),supplier.name,'供应商名称')
        if 'query_purchase_requests' in allowed_tools:
            for req in db.scalars(select(m.PurchaseRequest).where(m.PurchaseRequest.project_id.in_(list(by_id))).limit(501)):
                add(req.project_id,req.number,'采购申请号')
        if 'query_purchase_orders' in allowed_tools or 'query_orders' in allowed_tools:
            for order in db.scalars(select(m.PurchaseOrder).where(m.PurchaseOrder.project_id.in_(list(by_id))).limit(501)):
                add(order.project_id,order.number,'采购订单号')
    if not scores:return None,[],truncated
    best=max(scores.values());ids=[pid for pid,score in scores.items() if score==best]
    if len(ids)!=1:return None,[_project_card(db,user,by_id[pid],reasons[pid]) for pid in ids[:20]],truncated
    return by_id[ids[0]],reasons[ids[0]],truncated


def _profile(db,user,project_id):
    fields=access(db,user,'project.read',{'project_id':project_id}).fields
    profile=db.get(m.ProjectProfile,project_id)
    if not profile:return None
    return select_fields({'execution_mode':profile.execution_mode,
        'customer_due_date':profile.customer_due_date.isoformat() if profile.customer_due_date else None,
        'settlement_status':profile.settlement_status},fields | {'execution_mode','customer_due_date','settlement_status'})


def _material(id_,material):
    if not material:return {'id':id_}
    return {'id':material.id,'code':material.code,'name':material.name,'category':material.category,'unit':material.unit}


def _price_rows(db,price_subjects):
    result=[]
    for subject in price_subjects:
        detail=subject.get('detail') if isinstance(subject.get('detail'),dict) else {}
        material=db.get(m.Material,detail.get('material_id')) if detail.get('material_id') else None
        supplier=db.get(m.Supplier,detail.get('supplier_id')) if detail.get('supplier_id') else None
        result.append({'subject_id':subject.get('id'),'number':subject.get('number'),'status':subject.get('status'),
            'category':subject.get('category'),'material':_material(detail.get('material_id'),material),
            'supplier':{'id':supplier.id,'code':supplier.code,'name':supplier.name,'category':supplier.category,'active':supplier.active} if supplier else {'id':detail.get('supplier_id')},
            'unit_price':detail.get('unit_price'),'currency':detail.get('currency'),
            'valid_from':detail.get('valid_from'),'valid_to':detail.get('valid_to'),
            'quote_evidence':detail.get('quote_evidence')})
    return result


def _design_procurement_needs(db,user,project_id,allowed_tools):
    if 'query_design_route_context' not in allowed_tools and 'query_design_route' not in allowed_tools:return []
    subjects=list(db.scalars(select(m.BusinessSubject).where(m.BusinessSubject.project_id==project_id,
        m.BusinessSubject.kind=='design_route').order_by(m.BusinessSubject.created_at.desc()).limit(100)))
    needs=[]
    for subject in _subject_data(db,user,subjects):
        detail=subject.get('detail') if isinstance(subject.get('detail'),dict) else {}
        for item in detail.get('items') or []:
            if item.get('route') not in {'PURCHASE','OUTSOURCE'}:continue
            material=db.get(m.Material,item.get('material_id')) if item.get('material_id') else None
            needs.append({'design_id':subject.get('id'),'design_number':subject.get('number'),
                'drawing_revision':detail.get('drawing_revision'),'route':item.get('route'),
                'quantity':item.get('quantity'),'task_id':item.get('task_id'),
                'material':_material(item.get('material_id'),material)})
    return needs[:100]


def _purchase_requests(db,user,project_id,allowed_tools):
    if 'query_purchase_requests' not in allowed_tools:return []
    from .business import request_data
    rows=[]
    q=select(m.PurchaseRequest).where(m.PurchaseRequest.project_id==project_id,
        predicate(db,user,'purchase.read',{'project_id':m.PurchaseRequest.project_id})).order_by(m.PurchaseRequest.created_at.desc()).limit(100)
    for req in db.scalars(q):
        try:rows.append(request_data(db,user,req))
        except DomainError:continue
    return rows


def _order_rows(db,user,project_id,allowed_tools):
    if 'query_purchase_orders' not in allowed_tools and 'query_orders' not in allowed_tools:return []
    from .procurement import order_data
    result=[]
    q=select(m.PurchaseOrder).where(m.PurchaseOrder.project_id==project_id).order_by(m.PurchaseOrder.created_at.desc()).limit(100)
    for order in db.scalars(q):
        try:result.append(order_data(db,user,order))
        except DomainError:continue
    return result


def _order_tracking(orders):
    lines=[]
    totals=Counter()
    for order in orders:
        for line in order.get('lines') or []:
            qty=Decimal(str(line.get('quantity') or '0'))
            shipped=Decimal(str(line.get('shipped_quantity') or '0'))
            remaining=qty-shipped
            receipts=sum((Decimal(str(receipt.get('quantity') or '0'))
                          for shipment in line.get('shipments') or []
                          for receipt in shipment.get('receipts') or []),Decimal(0))
            totals['lines']+=1
            if remaining>0:totals['unshipped_lines']+=1
            if line.get('exceptions'):totals['exception_lines']+=1
            lines.append({'order_id':order.get('id'),'order_number':order.get('number'),'order_status':order.get('status'),
                'line_id':line.get('id'),'material_id':line.get('material_id'),'material_name':line.get('material_name'),
                'category':line.get('category'),'quantity':line.get('quantity'),'unit':line.get('unit'),
                'unit_price':line.get('unit_price'),'price_subject_id':line.get('price_subject_id'),
                'agreed_ship_date':line.get('agreed_ship_date'),'shipped_quantity':str(shipped),
                'remaining_quantity':str(remaining),'received_quantity':str(receipts),
                'exceptions':line.get('exceptions') or []})
    return {'totals':dict(totals),'lines':lines[:100]}


def _augment_order_receipts(db,orders):
    for order in orders:
        for line in order.get('lines') or []:
            for shipment in line.get('shipments') or []:
                receipts=[]
                for receipt in db.scalars(select(m.GoodsReceipt).where(m.GoodsReceipt.shipment_id==shipment.get('id')).limit(100)):
                    inspection=db.scalar(select(m.ReceiptInspection).where(m.ReceiptInspection.receipt_id==receipt.id))
                    receipts.append({'id':receipt.id,'quantity':str(receipt.quantity),'warehouse_id':receipt.warehouse_id,
                        'reference':receipt.reference,'evidence':receipt.evidence,
                        'inspection':({'id':inspection.id,'accepted_quantity':str(inspection.accepted_quantity),
                            'rejected_quantity':str(inspection.rejected_quantity),'evidence':inspection.evidence} if inspection else None)})
                shipment['receipts']=receipts


def _analysis(prices,needs,requests,orders):
    today=now().date().isoformat()
    effective=[row for row in prices if row.get('status')=='EFFECTIVE']
    open_prices=[row for row in prices if row.get('status') in {'DRAFT','SUBMITTED','RETURNED','APPLY_BLOCKED'}]
    expired=[row for row in effective if row.get('valid_to') and str(row['valid_to'])<today]
    no_price=[]
    priced_materials={((row.get('material') or {}).get('id'),(row.get('supplier') or {}).get('id')) for row in effective}
    material_has_price={mid for mid,_ in priced_materials if mid}
    for need in needs:
        mid=(need.get('material') or {}).get('id')
        if mid and mid not in material_has_price:no_price.append(need)
    tracking=_order_tracking(orders)
    warnings=[]
    if not effective:warnings.append('当前可见范围未见已生效采购价格，不能据此带出正式下单价格依据。')
    if open_prices:warnings.append('存在未完成的采购价格审批，价格依据可能即将变化。')
    if expired:warnings.append('存在已过有效期的价格记录，下单前需重新核价或审批。')
    if no_price:warnings.append('存在设计BOM采购/委外需求未匹配到当前可见有效价格。')
    if tracking['totals'].get('unshipped_lines'):warnings.append('存在正式订单未完全发货的明细，需采购跟踪供应商生产/发货。')
    if tracking['totals'].get('exception_lines'):warnings.append('存在供应商发货异常，需关联整改、退换货、扣款或工程联络处理。')
    return {'effective_prices':effective[:50],'open_price_reviews':open_prices[:50],'expired_prices':expired[:50],
        'design_procurement_needs_without_visible_price':no_price[:50],
        'order_tracking':tracking,'warnings':warnings,'derived_status':{
            'has_effective_price':bool(effective),
            'has_open_price_review':bool(open_prices),
            'has_design_procurement_need':bool(needs),
            'has_unpriced_design_need':bool(no_price),
            'has_purchase_request':bool(requests),
            'has_purchase_order':bool(orders),
            'has_unshipped_order_line':bool(tracking['totals'].get('unshipped_lines')),
            'has_order_exception':bool(tracking['totals'].get('exception_lines'))}}


def query(db,user,data:ProcurementPriceContextInput,allowed_tools:set[str]):
    project,alternatives,truncated=_resolve(db,user,data,allowed_tools)
    limitations=['只读取当前用户可见且具备采购价格读取权限的项目。',
                 '本工具只核对料品、价格、采购需求、采购申请和订单跟踪上下文，不创建料品、不询价、不议价、不下单、不入库。',
                 'ERP 已有采购、仓储或物流执行记录时应以对应正式回执为准；没有记录不能推断供应商未生产、未发货或质量合格。']
    if truncated:limitations.append('最多检查前500个可见项目，结果可能未覆盖全部可见范围。')
    if project:
        price_subjects=_price_subjects(db,user,[project.id],allowed_tools)[:100]
        prices=_price_rows(db,price_subjects)
        needs=_design_procurement_needs(db,user,project.id,allowed_tools)
        requests=_purchase_requests(db,user,project.id,allowed_tools)
        orders=_order_rows(db,user,project.id,allowed_tools)
        _augment_order_receipts(db,orders)
        skipped=[]
        if 'query_design_route_context' not in allowed_tools and 'query_design_route' not in allowed_tools:skipped.append('设计BOM采购/委外需求')
        if 'query_purchase_requests' not in allowed_tools:skipped.append('采购申请')
        if 'query_purchase_orders' not in allowed_tools and 'query_orders' not in allowed_tools:skipped.append('正式采购订单/发货收货跟踪')
        if skipped:limitations.append('未分配对应查询工具，未返回：'+'、'.join(skipped))
        return {'resolution':'RESOLVED','data':[{'project':_project_card(db,user,project,alternatives or ('项目定位',)),
            'profile':_profile(db,user,project.id),'purchase_prices':prices,'design_procurement_needs':needs,
            'purchase_requests':requests,'purchase_orders':orders,'analysis':_analysis(prices,needs,requests,orders)}],
            'source':'agent_db','as_of':now().isoformat(),'limitations':limitations}
    if alternatives is None:
        return {'resolution':'NOT_FOUND_OR_FORBIDDEN','data':[],'source':'agent_db','as_of':now().isoformat(),
            'limitations':limitations}
    if alternatives:
        return {'resolution':'MULTIPLE_CANDIDATES','data':alternatives,'source':'agent_db','as_of':now().isoformat(),
            'limitations':limitations+['线索命中多个候选项目，请使用项目 ID 或更完整编号后再查询。']}
    return {'resolution':'NOT_FOUND','data':[],'source':'agent_db','as_of':now().isoformat(),
        'limitations':limitations}
