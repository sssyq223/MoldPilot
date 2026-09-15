from datetime import date, timedelta
from decimal import Decimal

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app import models as m
from app.authorization import PERMISSIONS
from app.models import Base
from app.tool_gateway import execute, tool_schema


def factory():
    engine=create_engine('sqlite+pysqlite:///:memory:')
    Base.metadata.create_all(engine)
    Session=sessionmaker(engine,expire_on_commit=False)
    return engine,Session


def user(db,username='operator',super_admin=False):
    row=m.User(username=username,display_name=username,password_hash='test',super_admin=super_admin)
    db.add(row);db.flush();return row


def project(db,code,name='采购项目',status='ACTIVE'):
    row=m.Project(code=code,name=name,status=status)
    db.add(row);db.flush();return row


def material(db,code,name,category='trial_material',unit='KG'):
    row=m.Material(code=code,name=name,category=category,unit=unit)
    db.add(row);db.flush();return row


def supplier(db,code,name,category='trial_material',active=True):
    row=m.Supplier(code=code,name=name,category=category,active=active)
    db.add(row);db.flush();return row


def grant(db,admin,target,permission,project_id,category=None,fields=None):
    scope={'project_id':[project_id]}
    if category:scope['category']=[category]
    db.add(m.Grant(user_id=target.id,permission=permission,effect='ALLOW',scope=scope,
        fields=fields or PERMISSIONS[permission],reason='unit test',granted_by=admin.id))


def capability(db,target,key,kind='TOOL'):
    db.add(m.Capability(user_id=target.id,kind=kind,key=key,enabled=True))


def price(db,project,user,mat,sup,number='PRICE-001',status='EFFECTIVE',valid_to=None,unit_price='12.34'):
    subject=m.BusinessSubject(kind='purchase_price',number=number,project_id=project.id,
        created_by=user.id,status=status,category=mat.category)
    db.add(subject);db.flush()
    db.add(m.PriceDetail(subject_id=subject.id,supplier_id=sup.id,material_id=mat.id,
        unit_price=Decimal(unit_price),currency='CNY',valid_from=date.today()-timedelta(days=1),
        valid_to=valid_to or date.today()+timedelta(days=30),quote_evidence='供应商报价单'))
    return subject


def design_need(db,project,user,mat,route='PURCHASE'):
    subject=m.BusinessSubject(kind='design_route',number='DESIGN-PROC',project_id=project.id,
        created_by=user.id,status='EFFECTIVE')
    db.add(subject);db.flush()
    db.add(m.DesignDetail(subject_id=subject.id,design_type='NEW_MOLD',drawing_revision='A1',
        drawing_evidence='采购需求图纸',reviewer_id=user.id))
    db.add(m.DesignItem(design_id=subject.id,material_id=mat.id,quantity=Decimal('3'),route=route))
    return subject


def purchase_request(db,project,user,mat,number='PR-001',status='APPROVED'):
    req=m.PurchaseRequest(number=number,project_id=project.id,created_by=user.id,remark='试模料采购',status=status)
    db.add(req);db.flush()
    db.add(m.PurchaseLine(request_id=req.id,material_id=mat.id,quantity=Decimal('5'),due_date=date.today()+timedelta(days=7)))
    return req


def purchase_order(db,project,user,req,mat,sup,price_subject,number='PO-001',status='ISSUED'):
    order=m.PurchaseOrder(request_id=req.id,project_id=project.id,supplier_id=sup.id,number=number,
        status=status,currency='CNY',issued_by=user.id,issued_at=date.today())
    db.add(order);db.flush()
    source_line=db.query(m.PurchaseLine).filter_by(request_id=req.id).one()
    line=m.OrderLine(order_id=order.id,source_line_id=source_line.id,material_id=mat.id,
        quantity=Decimal('5'),unit_price=Decimal('12.34'),agreed_ship_date=date.today()-timedelta(days=1))
    db.add(line);db.flush()
    db.add(m.OrderPriceSnapshot(line_id=line.id,price_subject_id=price_subject.id,
        unit_price=Decimal('12.34'),currency='CNY',selected_by=user.id))
    shipment=m.SupplierShipment(order_line_id=line.id,quantity=Decimal('2'),shipped_date=date.today()-timedelta(days=2),
        reference='SHIP-001',evidence='供应商发货单',confirmed_by=user.id)
    db.add(shipment);db.flush()
    receipt=m.GoodsReceipt(shipment_id=shipment.id,warehouse_id=warehouse(db).id,quantity=Decimal('2'),
        reference='GR-001',received_by=user.id,evidence='仓库签收')
    db.add(receipt);db.flush()
    db.add(m.ReceiptInspection(receipt_id=receipt.id,accepted_quantity=Decimal('1'),rejected_quantity=Decimal('1'),
        inspector_id=user.id,evidence='来料检验报告'))
    db.add(m.DeliveryException(order_line_id=line.id,reason='剩余未发货',expected_ship_date=date.today()+timedelta(days=2),
        status='OPEN',reported_by=user.id,evidence='采购反馈'))
    return order


def warehouse(db):
    row=m.Warehouse(code='WH-TEST',name='测试仓',active=True,scope_confirmed=True)
    db.add(row);db.flush();return row


def test_procurement_context_schema_prices_design_need_and_order_tracking():
    engine,Session=factory()
    try:
        with Session.begin() as db:
            admin=user(db,'admin',True);p=project(db,'PROC-M001')
            mat=material(db,'TM-001','试模料钢材')
            sup=supplier(db,'SUP-001','试模料供应商')
            prc=price(db,p,admin,mat,sup)
            design_need(db,p,admin,mat)
            req=purchase_request(db,p,admin,mat)
            purchase_order(db,p,admin,req,mat,sup,prc)
        schema=tool_schema('query_procurement_price_context')['function']['parameters']
        assert {'project_id','identifier'} <= set(schema['properties'])
        with Session() as db:
            admin=db.query(m.User).filter_by(username='admin').one()
            result=execute(db,admin,'query_procurement_price_context',{'identifier':'TM-001'})
            assert result['resolution']=='RESOLVED'
            row=result['data'][0];analysis=row['analysis']
            assert row['project']['code']=='PROC-M001'
            assert analysis['derived_status']['has_effective_price'] is True
            assert analysis['derived_status']['has_design_procurement_need'] is True
            assert analysis['derived_status']['has_unshipped_order_line'] is True
            assert analysis['derived_status']['has_order_exception'] is True
            assert analysis['effective_prices'][0]['material']['code']=='TM-001'
            assert row['design_procurement_needs'][0]['route']=='PURCHASE'
            assert row['purchase_requests'][0]['number']=='PR-001'
            line=analysis['order_tracking']['lines'][0]
            assert line['remaining_quantity']=='3.000000'
            assert line['received_quantity']=='2.000000'
            assert line['exceptions'][0]['reason']=='剩余未发货'
    finally:
        engine.dispose()


def test_procurement_context_hides_orders_without_order_tool():
    engine,Session=factory()
    try:
        with Session.begin() as db:
            admin=user(db,'admin',True);operator=user(db)
            p=project(db,'PROC-LIMITED')
            mat=material(db,'MAT-HIDDEN','隐藏订单物料')
            sup=supplier(db,'SUP-HIDDEN','隐藏供应商')
            prc=price(db,p,admin,mat,sup)
            req=purchase_request(db,p,admin,mat,number='PR-HIDDEN')
            purchase_order(db,p,admin,req,mat,sup,prc,number='PO-HIDDEN')
            grant(db,admin,operator,'project.read',p.id)
            grant(db,admin,operator,'purchase_price.read',p.id,mat.category)
            capability(db,operator,'query_procurement_price_context')
        with Session() as db:
            operator=db.query(m.User).filter_by(username='operator').one()
            result=execute(db,operator,'query_procurement_price_context',{'identifier':'PROC-LIMITED'})
            row=result['data'][0]
            assert row['purchase_orders']==[]
            assert row['purchase_requests']==[]
            assert 'PO-HIDDEN' not in str(result)
            assert '采购申请' in ''.join(result['limitations'])
            assert '正式采购订单' in ''.join(result['limitations'])
            assert row['analysis']['effective_prices'][0]['material']['code']=='MAT-HIDDEN'
    finally:
        engine.dispose()


def test_procurement_context_multiple_candidates_and_no_effective_price():
    engine,Session=factory()
    try:
        with Session.begin() as db:
            admin=user(db,'admin',True)
            p1=project(db,'PROC-A','共同采购项目A')
            p2=project(db,'PROC-B','共同采购项目B')
            mat=material(db,'MAT-A','采购零件')
            sup=supplier(db,'SUP-A','供应商A')
            price(db,p1,admin,mat,sup,number='PRICE-DRAFT',status='DRAFT')
            price(db,p2,admin,mat,sup,number='PRICE-EFF',status='EFFECTIVE')
        with Session() as db:
            admin=db.query(m.User).filter_by(username='admin').one()
            ambiguous=execute(db,admin,'query_procurement_price_context',{'identifier':'共同采购项目'})
            assert ambiguous['resolution']=='MULTIPLE_CANDIDATES'
            assert {row['code'] for row in ambiguous['data']}=={'PROC-A','PROC-B'}
            resolved=execute(db,admin,'query_procurement_price_context',{'identifier':'PROC-A'})
            analysis=resolved['data'][0]['analysis']
            assert analysis['derived_status']['has_effective_price'] is False
            assert '未见已生效采购价格' in ''.join(analysis['warnings'])
    finally:
        engine.dispose()
