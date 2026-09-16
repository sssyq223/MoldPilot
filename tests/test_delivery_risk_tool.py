from datetime import timedelta
from decimal import Decimal


from app import models as m
from app.db import now
from pg_db import factory as pg_factory
from app.tool_gateway import execute, tool_schema


def factory():
    return pg_factory()


def add_order(db,project,user,material,supplier,number):
    request=m.PurchaseRequest(number='REQ-'+number,project_id=project.id,created_by=user.id,status='APPROVED')
    db.add(request);db.flush()
    line=m.PurchaseLine(request_id=request.id,material_id=material.id,quantity=Decimal('10'),
                        due_date=now().date()+timedelta(days=1))
    db.add(line);db.flush()
    order=m.PurchaseOrder(request_id=request.id,project_id=project.id,supplier_id=supplier.id,
                          number='PO-'+number,status='ISSUED',issued_by=user.id,issued_at=now())
    db.add(order);db.flush()
    db.add(m.OrderLine(order_id=order.id,source_line_id=line.id,material_id=material.id,quantity=Decimal('10'),
                       unit_price=Decimal('2.50'),agreed_ship_date=now().date()+timedelta(days=1)))


def test_delivery_risk_tool_schema_and_project_identifier_scope_postgres():
    engine,Session=factory()
    try:
        with Session.begin() as db:
            admin=m.User(username='admin',display_name='管理员',password_hash='test',super_admin=True)
            first=m.Project(code='RISK-M001',name='风险项目一',status='ACTIVE')
            second=m.Project(code='RISK-M002',name='风险项目二',status='ACTIVE')
            material=m.Material(code='H-01',name='螺钉',category='hardware',unit='件')
            supplier=m.Supplier(code='S-01',name='五金供应商',category='hardware')
            db.add_all([admin,first,second,material,supplier]);db.flush()
            add_order(db,first,admin,material,supplier,'1')
            add_order(db,second,admin,material,supplier,'2')
            db.add(m.RiskPolicy(version=1,near_due_days=3,configured_by=admin.id))
        schema=tool_schema('analyze_delivery_risk')['function']['parameters']
        assert {'project_id','identifier'} <= set(schema['properties'])
        with Session() as db:
            admin=db.query(m.User).filter_by(username='admin').one()
            focused=execute(db,admin,'analyze_delivery_risk',{'identifier':'RISK-M001'})
            assert focused['resolution']=='FILTERED_BY_PROJECT'
            assert focused['project']['code']=='RISK-M001'
            assert {row['order_number'] for row in focused['data']}=={'PO-1'}
            assert {row['project_id'] for row in focused['data']}=={db.query(m.Project).filter_by(code='RISK-M001').one().id}
            all_visible=execute(db,admin,'analyze_delivery_risk',{})
            assert {row['order_number'] for row in all_visible['data']}=={'PO-1','PO-2'}
    finally:
        engine.dispose()


def test_delivery_risk_ambiguous_identifier_does_not_fallback_to_global_postgres():
    engine,Session=factory()
    try:
        with Session.begin() as db:
            admin=m.User(username='admin',display_name='管理员',password_hash='test',super_admin=True)
            first=m.Project(code='AMB-001',name='共同风险项目A',status='ACTIVE')
            second=m.Project(code='AMB-002',name='共同风险项目B',status='ACTIVE')
            material=m.Material(code='H-01',name='螺钉',category='hardware',unit='件')
            supplier=m.Supplier(code='S-01',name='五金供应商',category='hardware')
            db.add_all([admin,first,second,material,supplier]);db.flush()
            add_order(db,first,admin,material,supplier,'1')
            add_order(db,second,admin,material,supplier,'2')
            db.add(m.RiskPolicy(version=1,near_due_days=3,configured_by=admin.id))
        with Session() as db:
            admin=db.query(m.User).filter_by(username='admin').one()
            result=execute(db,admin,'analyze_delivery_risk',{'identifier':'共同风险'})
            assert result['resolution']=='AMBIGUOUS'
            assert result['data']==[]
            assert {row['code'] for row in result['candidates']}=={'AMB-001','AMB-002'}
    finally:
        engine.dispose()

