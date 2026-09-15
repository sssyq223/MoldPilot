from datetime import date
from decimal import Decimal

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app import models as m
from app.authorization import PERMISSIONS
from app.models import Base
from app.tool_gateway import execute


def factory():
    engine=create_engine('sqlite+pysqlite:///:memory:')
    Base.metadata.create_all(engine)
    Session=sessionmaker(engine,expire_on_commit=False)
    return engine,Session


def user(db,username='operator',super_admin=False):
    row=m.User(username=username,display_name=username,password_hash='test',super_admin=super_admin)
    db.add(row);db.flush();return row


def project(db,code,name='项目',status='ACTIVE'):
    row=m.Project(code=code,name=name,status=status)
    db.add(row);db.flush();return row


def grant(db,admin,target,permission,project_id,fields=None):
    db.add(m.Grant(user_id=target.id,permission=permission,effect='ALLOW',scope={'project_id':[project_id]},
        fields=fields or PERMISSIONS[permission],reason='unit test',granted_by=admin.id))


def capability(db,target,key,kind='TOOL'):
    db.add(m.Capability(user_id=target.id,kind=kind,key=key,enabled=True))


def test_dossier_reverse_locator_reports_ambiguous_mold_and_resolves_project_id():
    engine,Session=factory()
    with Session.begin() as db:
        admin=user(db,'admin',True);p1=project(db,'MP-A','A项目');p2=project(db,'MP-B','B项目')
        mold=m.Mold(internal_number='SHARED-MOLD-01',name='共用模号')
        db.add(mold);db.flush()
        db.add_all([m.ProjectMold(project_id=p1.id,mold_id=mold.id),m.ProjectMold(project_id=p2.id,mold_id=mold.id)])
    with Session() as db:
        admin=db.query(m.User).filter_by(username='admin').one()
        result=execute(db,admin,'query_project_dossier',{'identifier':'SHARED-MOLD-01'})
        assert result['resolution']=='AMBIGUOUS'
        assert {row['code'] for row in result['data']}=={'MP-A','MP-B'}
        resolved=execute(db,admin,'query_project_dossier',{'project_id':db.query(m.Project).filter_by(code='MP-A').one().id})
        assert resolved['resolution']=='RESOLVED'
        assert resolved['data'][0]['project']['code']=='MP-A'
    engine.dispose()


def test_dossier_requires_project_dossier_permission_and_hides_forbidden_project():
    engine,Session=factory()
    with Session.begin() as db:
        admin=user(db,'admin',True);operator=user(db)
        visible=project(db,'VISIBLE','可见项目');hidden=project(db,'HIDDEN','隐藏项目')
        grant(db,admin,operator,'project.read',visible.id);grant(db,admin,operator,'project.dossier.read',visible.id)
        capability(db,operator,'query_project_dossier')
    with Session() as db:
        operator=db.query(m.User).filter_by(username='operator').one()
        assert execute(db,operator,'query_project_dossier',{'identifier':'VISIBLE'})['resolution']=='RESOLVED'
        assert execute(db,operator,'query_project_dossier',{'identifier':'隐藏项目'})['resolution']=='NOT_FOUND'
        hidden_id=db.query(m.Project).filter_by(code='HIDDEN').one().id
        assert execute(db,operator,'query_project_dossier',{'project_id':hidden_id})['resolution']=='NOT_FOUND_OR_FORBIDDEN'
    engine.dispose()


def test_dossier_reverse_locator_does_not_use_redacted_contract_number():
    engine,Session=factory()
    with Session.begin() as db:
        admin=user(db,'admin',True);operator=user(db)
        p=project(db,'MP-C','合同项目')
        subject=m.BusinessSubject(kind='sales_contract',number='SC-OPEN',project_id=p.id,created_by=admin.id,status='EFFECTIVE')
        db.add(subject);db.flush()
        db.add(m.ContractDetail(subject_id=subject.id,customer_id=None,supplier_id=None,amount=Decimal('100.00'),
            currency='CNY',contract_number='SECRET-CN-777',expected_date=date.today()))
        grant(db,admin,operator,'project.read',p.id);grant(db,admin,operator,'project.dossier.read',p.id)
        grant(db,admin,operator,'sales_contract.read',p.id,fields=['id','project_id'])
        capability(db,operator,'query_project_dossier');capability(db,operator,'query_sales_contract')
    with Session() as db:
        operator=db.query(m.User).filter_by(username='operator').one()
        assert execute(db,operator,'query_project_dossier',{'identifier':'SECRET-CN-777'})['resolution']=='NOT_FOUND'
        assert execute(db,operator,'query_project_dossier',{'identifier':'MP-C'})['resolution']=='RESOLVED'
    engine.dispose()


def test_dossier_financial_summary_uses_only_actual_confirmed_payment_records():
    engine,Session=factory()
    with Session.begin() as db:
        admin=user(db,'admin',True);p=project(db,'MP-FIN','财务项目')
        customer=m.Customer(code='C1',name='客户');db.add(customer);db.flush()
        contract=m.BusinessSubject(kind='sales_contract',number='SC-001',project_id=p.id,created_by=admin.id,status='EFFECTIVE')
        db.add(contract);db.flush()
        db.add(m.ContractDetail(subject_id=contract.id,customer_id=customer.id,supplier_id=None,amount=Decimal('100.00'),
            currency='CNY',contract_number='CN-001',expected_date=date.today()))
        db.add(m.PaymentStage(contract_id=contract.id,name='预付款',amount=Decimal('40.00'),currency='CNY',condition='合同生效'))
        supplier=m.BusinessSubject(kind='supplier_payment',number='PAY-001',project_id=p.id,category='outsource',created_by=admin.id,status='EFFECTIVE')
        db.add(supplier);db.flush()
        stage=m.PaymentStage(contract_id=contract.id,name='委外款',amount=Decimal('80.00'),currency='CNY',condition='验收')
        db.add(stage);db.flush()
        db.add(m.PaymentRequestDetail(subject_id=supplier.id,stage_id=stage.id,amount=Decimal('50.00'),currency='CNY'))
        db.add_all([m.PaymentConfirmation(request_id=supplier.id,amount=Decimal('30.00'),currency='CNY',paid_date=date.today(),
            reference='BANK-1',evidence='实付',confirmed_by=admin.id),
            m.PaymentConfirmation(request_id=supplier.id,amount=Decimal('-5.00'),currency='CNY',paid_date=date.today(),
            reference='BANK-REV',evidence='冲正',confirmed_by=admin.id)])
    with Session() as db:
        admin=db.query(m.User).filter_by(username='admin').one()
        result=execute(db,admin,'query_project_dossier',{'identifier':'MP-FIN'})
        assert result['resolution']=='RESOLVED'
        summary=result['data'][0]['financial_summary']
        assert summary['confirmed_supplier_payments']==[{'currency':'CNY','amount':'25.00'}]
        assert summary['customer_payment_nodes'][0]['contract_number']=='CN-001'
    engine.dispose()
