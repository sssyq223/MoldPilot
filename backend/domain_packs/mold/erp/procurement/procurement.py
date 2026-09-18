from decimal import Decimal
import secrets
from pydantic import Field, model_validator
from sqlalchemy import select, func, exists
from domain_packs.mold import models as m
from domain_packs.mold.authorization import require, predicate, select_fields, fingerprint, access
from domain_packs.mold.ports.errors import DomainError
from domain_packs.mold.ports.db import now
from domain_packs.mold.ports.events import record
from domain_packs.mold.erp.core.domains import values, rows
from domain_packs.mold.ports.schemas import StrictModel


class DeliveryRiskInput(StrictModel):
    project_id: str | None = Field(default=None, min_length=1, max_length=36,
        description='可选。限定分析的项目 ID；不填写时分析当前用户可见范围内的正式订单。')
    identifier: str | None = Field(default=None, min_length=1, max_length=200,
        description='可选。项目编号或项目名称，用于把风险分析限定到单个可见项目。')

    @model_validator(mode='after')
    def one_project_locator(self):
        if self.project_id and self.identifier:
            raise ValueError('project_id 和 identifier 只能填写一项')
        if self.identifier:
            self.identifier=self.identifier.strip()
            if not self.identifier:raise ValueError('项目标识不能为空')
        return self


def line_context(db,line):
    order=db.get(m.PurchaseOrder,line.order_id);material=db.get(m.Material,line.material_id)
    return order,material,{'project_id':order.project_id,'category':material.category}


def order_access(db,user,order,permission):
    allowed=None
    for line in rows(db,m.OrderLine,order_id=order.id):
        _,material,scope=line_context(db,line)
        fields=require(db,user,permission,scope)
        allowed=fields if allowed is None else allowed & fields
    if allowed is None:raise DomainError('ORDER_EMPTY','订单无明细')
    return allowed


def create_execution_order(db,request):
    existing=db.scalar(select(m.PurchaseOrder).where(m.PurchaseOrder.request_id==request.id))
    if existing:return existing
    order=m.PurchaseOrder(request_id=request.id,project_id=request.project_id,number='PO-'+secrets.token_hex(6).upper())
    db.add(order);db.flush()
    for line in rows(db,m.PurchaseLine,request_id=request.id):
        db.add(m.OrderLine(order_id=order.id,source_line_id=line.id,material_id=line.material_id,
                           quantity=line.quantity,agreed_ship_date=line.due_date))
    record(db,None,'order.execution.draft.created',order.id,recipients=[request.created_by])
    return order


def order_data(db,user,order):
    fields=order_access(db,user,order,'order.read')
    lines=[]
    for line in rows(db,m.OrderLine,order_id=order.id):
        material=db.get(m.Material,line.material_id)
        shipments=rows(db,m.SupplierShipment,order_line_id=line.id)
        lines.append({**values(line),'material_name':material.name,'category':material.category,'unit':material.unit,
                      'price_subject_id':db.get(m.OrderPriceSnapshot,line.id).price_subject_id if db.get(m.OrderPriceSnapshot,line.id) else None,
                      'shipped_quantity':str(sum((s.quantity for s in shipments),Decimal(0))),
                      'shipments':[values(s) for s in shipments],
                      'exceptions':[values(e) for e in rows(db,m.DeliveryException,order_line_id=line.id)]})
    return select_fields({**values(order),'lines':lines},fields)


def visible_orders(db,user):
    allowed=predicate(db,user,'order.read',{'project_id':m.PurchaseOrder.project_id,'category':m.Material.category})
    forbidden=exists(select(m.OrderLine.id).join(m.Material).where(m.OrderLine.order_id==m.PurchaseOrder.id,~allowed))
    q=select(m.PurchaseOrder).where(~forbidden).order_by(m.PurchaseOrder.created_at.desc()).limit(100)
    return [order_data(db,user,row) for row in db.scalars(q)]


def edit_order(db,user,order_id,data):
    order=db.scalar(select(m.PurchaseOrder).where(m.PurchaseOrder.id==order_id).with_for_update())
    if not order:raise DomainError('NOT_FOUND','订单不存在',404)
    order_access(db,user,order,'order.edit')
    if order.status!='DRAFT':raise DomainError('ORDER_FROZEN','正式订单不可覆盖修改，应走受控变更',409)
    if order.version!=data.version:raise DomainError('VERSION_CONFLICT','订单已变化',409)
    supplier=db.get(m.Supplier,data.supplier_id)
    if not supplier or not supplier.active:raise DomainError('SUPPLIER_UNKNOWN','供应商无效')
    lines={l.id:l for l in rows(db,m.OrderLine,order_id=order.id)}
    if set(lines)!={l.id for l in data.lines} or len(lines)!=len(data.lines):raise DomainError('LINE_MISMATCH','必须完整核对订单明细')
    for incoming in data.lines:
        line=lines[incoming.id];material=db.get(m.Material,line.material_id)
        if supplier.category!=material.category:raise DomainError('SUPPLIER_SCOPE','供应商责任域与订单明细不一致')
        subject=db.get(m.BusinessSubject,incoming.price_subject_id)
        price=db.get(m.PriceDetail,incoming.price_subject_id)
        if not subject or not price or subject.kind!='purchase_price' or subject.status!='EFFECTIVE' or subject.project_id!=order.project_id:
            raise DomainError('PRICE_NOT_APPROVED','须选择本项目已生效的价格版本')
        if price.supplier_id!=supplier.id or price.material_id!=material.id or price.currency!=data.currency or price.unit_price!=incoming.unit_price:
            raise DomainError('PRICE_MISMATCH','成交方案与已审批价格不一致')
        if not price.valid_from<=now().date()<=price.valid_to:raise DomainError('PRICE_EXPIRED','选定价格不在有效期内')
        snapshot=db.get(m.OrderPriceSnapshot,line.id)
        if not snapshot:
            snapshot=m.OrderPriceSnapshot(line_id=line.id);db.add(snapshot)
        snapshot.price_subject_id=subject.id;snapshot.unit_price=price.unit_price;snapshot.currency=price.currency;snapshot.selected_by=user.id
        line.unit_price=incoming.unit_price;line.agreed_ship_date=incoming.agreed_ship_date
    order.supplier_id=supplier.id;order.currency=data.currency;order.version+=1
    record(db,user,'order.draft.updated',order.id)
    return order_data(db,user,order)


def move_stock(db,user,warehouse_id,material_id,project_id,quantity,kind,source_key,evidence):
    warehouse=db.scalar(select(m.Warehouse).where(m.Warehouse.id==warehouse_id).with_for_update())
    if not warehouse or not warehouse.active or not warehouse.scope_confirmed:
        raise DomainError('STOCK_SCOPE_UNCONFIRMED','仓库实物管理范围和期初依据尚未确认',409)
    balance=db.scalar(select(m.StockBalance).where(m.StockBalance.warehouse_id==warehouse_id,
                      m.StockBalance.material_id==material_id,m.StockBalance.project_id==project_id).with_for_update())
    if not balance:
        balance=m.StockBalance(warehouse_id=warehouse_id,material_id=material_id,project_id=project_id,quantity=Decimal(0))
        db.add(balance);db.flush()
    if balance.quantity+quantity<0:raise DomainError('INSUFFICIENT_STOCK','合格可用库存不足',409)
    balance.quantity+=quantity
    movement=m.StockMovement(balance_id=balance.id,quantity=quantity,kind=kind,source_key=source_key,confirmed_by=user.id,evidence=evidence)
    db.add(movement);db.flush();return movement


def delivery_risks(db,user,project_id=None):
    """Only visible lines participate in arithmetic; never compute a global hidden risk."""
    policy=db.scalar(select(m.RiskPolicy).order_by(m.RiskPolicy.version.desc()).limit(1))
    if not policy:raise DomainError('RISK_POLICY_REQUIRED','请管理员先配置临期天数；当前未使用默认阈值')
    allowed=predicate(db,user,'risk.read',{'project_id':m.PurchaseOrder.project_id,'category':m.Material.category})
    readable=predicate(db,user,'order.read',{'project_id':m.PurchaseOrder.project_id,'category':m.Material.category})
    q=select(m.OrderLine,m.PurchaseOrder,m.Material).join(m.PurchaseOrder).join(m.Material).where(
        allowed,readable,m.PurchaseOrder.status=='ISSUED')
    if project_id:q=q.where(m.PurchaseOrder.project_id==project_id)
    findings=[];today=now().date()
    # Bound evidence volume; report the bound rather than claiming complete analysis.
    candidates=list(db.execute(q.order_by(m.OrderLine.agreed_ship_date,m.OrderLine.id).limit(501)))
    for line,order,material in candidates[:500]:
        fields=access(db,user,'order.read',{'project_id':order.project_id,'category':material.category}).fields
        if '*' not in fields and not {'number','project_id','supplier_id','lines'}<=fields:
            continue
        shipped=db.scalar(select(func.coalesce(func.sum(m.SupplierShipment.quantity),0)).where(m.SupplierShipment.order_line_id==line.id))
        remaining=line.quantity-shipped
        if remaining<=0:continue
        exceptions=rows(db,m.DeliveryException,order_line_id=line.id,status='OPEN')
        days=(line.agreed_ship_date-today).days
        signals=[]
        if exceptions:signals.append('REPORTED_EXCEPTION')
        if days<0:signals.append('OVERDUE_UNSHIPPED')
        elif days<=policy.near_due_days:signals.append('NEAR_DUE_UNSHIPPED')
        if not signals:continue
        supplier=db.get(m.Supplier,order.supplier_id)
        findings.append({'project_id':order.project_id,'order_number':order.number,'line_id':line.id,
                         'material':material.name,'category':material.category,'supplier':supplier.name,
                         'agreed_ship_date':str(line.agreed_ship_date),'remaining_quantity':str(remaining),
                         'unit':material.unit,'signals':signals,'exception_reasons':[e.reason for e in exceptions],
                         'suggestions':['联系责任供应商核实剩余发货安排；需要改变正式交期时提交人工审批。']})
    limitations=['只分析当前授权责任域内已正式下单的供应商发货信息，不代表整套模具的总体延期结论。',
                 '未读取其他采购域、制造、装配或客户验收信息；没有记录不能推断这些环节正常。']
    if len(candidates)>500:limitations.append('最多分析前500条可见明细，结果未覆盖全部可见订单。')
    analysis=m.RiskAnalysis(user_id=user.id,authorization_hash=fingerprint(db,user),policy_version=policy.version,
                            findings=findings,limitations=limitations)
    db.add(analysis);db.flush()
    return {'analysis_id':analysis.id,'data':findings,'source':'agent_db','as_of':now().isoformat(),
            'rule_version':policy.version,'near_due_days':policy.near_due_days,'limitations':limitations}


def _project_card(db,user,project,matched_by=()):
    try:fields=access(db,user,'project.read',{'project_id':project.id}).fields
    except DomainError:fields=frozenset()
    data={'id':project.id,'code':project.code,'name':project.name,'status':project.status,
          'matched_by':sorted(set(matched_by))}
    return select_fields(data,fields) if fields else {'id':project.id,'matched_by':sorted(set(matched_by))}


def _resolve_project(db,user,data:DeliveryRiskInput):
    if data.project_id:
        project=db.get(m.Project,data.project_id)
        return (data.project_id,{'resolution':'FILTERED_BY_PROJECT_ID',
            'project':_project_card(db,user,project,('项目ID',)) if project else {'id':data.project_id}})
    if not data.identifier:
        return None,{'resolution':'ALL_VISIBLE'}
    visible=list(db.scalars(select(m.Project).where(predicate(db,user,'project.read',{'project_id':m.Project.id}))
                            .order_by(m.Project.code).limit(501)))
    needle=data.identifier.casefold()
    matches=[]
    for project in visible[:500]:
        reasons=[]
        if project.code.casefold()==needle:reasons.append('项目编号')
        elif needle in project.code.casefold():reasons.append('项目编号')
        if project.name.casefold()==needle:reasons.append('项目名称')
        elif needle in project.name.casefold():reasons.append('项目名称')
        if reasons:matches.append((project,reasons))
    exact=[item for item in matches if item[0].code.casefold()==needle or item[0].name.casefold()==needle]
    matches=exact or matches
    if not matches:
        return None,{'resolution':'NOT_FOUND_OR_FORBIDDEN','identifier':data.identifier,
            'candidates':[],'limitations':['未找到唯一可见项目；不会扩大到全部项目进行替代分析。']}
    if len(matches)>1:
        return None,{'resolution':'AMBIGUOUS','identifier':data.identifier,
            'candidates':[_project_card(db,user,project,reasons) for project,reasons in matches[:20]],
            'limitations':['项目标识命中多个可见项目；请使用项目 ID 或完整项目编号后再分析。']}
    project,reasons=matches[0]
    return project.id,{'resolution':'FILTERED_BY_PROJECT','identifier':data.identifier,
        'project':_project_card(db,user,project,reasons)}


def analyze_delivery_risk(db,user,data:DeliveryRiskInput):
    project_id,resolution=_resolve_project(db,user,data)
    if resolution['resolution'] in {'NOT_FOUND_OR_FORBIDDEN','AMBIGUOUS'}:
        return {'data':[],'source':'agent_db','as_of':now().isoformat(),**resolution}
    result=delivery_risks(db,user,project_id)
    result.update(resolution)
    if project_id:
        result['limitations']=['本次已限定到解析出的单个项目；仍只分析当前授权责任域内已正式下单的供应商发货信息。']+result['limitations']
    return result
