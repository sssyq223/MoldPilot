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


def project(db,code,name='合同项目',status='ACTIVE'):
    row=m.Project(code=code,name=name,status=status)
    db.add(row);db.flush();return row


def grant(db,admin,target,permission,project_id,fields=None):
    db.add(m.Grant(user_id=target.id,permission=permission,effect='ALLOW',scope={'project_id':[project_id]},
        fields=fields or PERMISSIONS[permission],reason='unit test',granted_by=admin.id))


def capability(db,target,key,kind='TOOL'):
    db.add(m.Capability(user_id=target.id,kind=kind,key=key,enabled=True))


def contract(db,project,user,kind,number,status='EFFECTIVE',amount='1000.00',expected=None,replaces_id=None):
    subject=m.BusinessSubject(kind=kind,number='SUBJECT-'+number,project_id=project.id,
        created_by=user.id,status=status,category='outsource' if kind=='full_outsource_contract' else None)
    db.add(subject);db.flush()
    db.add(m.ContractDetail(subject_id=subject.id,customer_id=None,supplier_id=None,amount=Decimal(amount),
        currency='CNY',contract_number=number,expected_date=expected,replaces_id=replaces_id))
    db.add(m.PaymentStage(contract_id=subject.id,name='预付款',amount=Decimal(amount)/Decimal(2),
        currency='CNY',condition='合同生效'))
    return subject


def test_contract_context_resolves_by_contract_number_and_summarizes_nodes():
    engine,Session=factory()
    try:
        with Session.begin() as db:
            admin=user(db,'admin',True);p=project(db,'CONTRACT-M001')
            old=contract(db,p,admin,'sales_contract','SC-OLD',amount='800.00')
            contract(db,p,admin,'sales_contract','SC-NEW',amount='1200.00',replaces_id=old.id)
            contract(db,p,admin,'full_outsource_contract','FO-001',amount='5000.00')
        schema=tool_schema('query_contract_context')['function']['parameters']
        assert {'project_id','identifier'} <= set(schema['properties'])
        with Session() as db:
            admin=db.query(m.User).filter_by(username='admin').one()
            result=execute(db,admin,'query_contract_context',{'identifier':'SC-NEW'})
            assert result['resolution']=='RESOLVED'
            row=result['data'][0]
            assert row['project']['code']=='CONTRACT-M001'
            assert row['derived_status']['has_sales_contract'] is True
            assert row['derived_status']['has_full_outsource_contract'] is True
            assert row['derived_status']['has_replacement_relation'] is True
            assert row['sales_contracts'][0]['detail']['stages'][0]['name']=='预付款'
            assert row['contract_totals']['sales_contract'][0]['contract_amount']=='2000.00'
            assert any(link['contract_number']=='SC-NEW' and link['replaces_number']=='SC-OLD'
                for link in row['replacement_links'])
    finally:
        engine.dispose()


def test_contract_context_respects_contract_tool_boundaries():
    engine,Session=factory()
    try:
        with Session.begin() as db:
            admin=user(db,'admin',True);operator=user(db)
            p=project(db,'CONTRACT-LIMITED')
            contract(db,p,admin,'sales_contract','HIDDEN-SALES',amount='300.00')
            contract(db,p,admin,'full_outsource_contract','VISIBLE-OUTSOURCE',amount='900.00')
            grant(db,admin,operator,'project.read',p.id);grant(db,admin,operator,'project.dossier.read',p.id)
            grant(db,admin,operator,'full_outsource_contract.read',p.id)
            capability(db,operator,'query_contract_context');capability(db,operator,'query_full_outsource_contract')
        with Session() as db:
            operator=db.query(m.User).filter_by(username='operator').one()
            result=execute(db,operator,'query_contract_context',{'identifier':'CONTRACT-LIMITED'})
            row=result['data'][0]
            assert row['sales_contracts']==[]
            assert row['full_outsource_contracts'][0]['detail']['contract_number']=='VISIBLE-OUTSOURCE'
            assert 'HIDDEN-SALES' not in str(result)
            assert '未返回：销售合同' in ''.join(result['limitations'])
    finally:
        engine.dispose()


def test_contract_context_reports_multiple_projects_and_late_expected_contracts():
    engine,Session=factory()
    try:
        with Session.begin() as db:
            admin=user(db,'admin',True)
            p1=project(db,'CONTRACT-A','共同合同项目A')
            p2=project(db,'CONTRACT-B','共同合同项目B')
            contract(db,p1,admin,'sales_contract','SC-A',status='SUBMITTED',
                expected=date.today()-timedelta(days=1))
            contract(db,p2,admin,'sales_contract','SC-B')
        with Session() as db:
            admin=db.query(m.User).filter_by(username='admin').one()
            ambiguous=execute(db,admin,'query_contract_context',{'identifier':'共同合同项目'})
            assert ambiguous['resolution']=='MULTIPLE_CANDIDATES'
            assert {row['code'] for row in ambiguous['data']}=={'CONTRACT-A','CONTRACT-B'}
            resolved=execute(db,admin,'query_contract_context',{'identifier':'SC-A'})
            row=resolved['data'][0]
            assert row['derived_status']['has_late_expected_contract'] is True
            assert row['late_expected_contracts'][0]['contract_number']=='SC-A'
    finally:
        engine.dispose()
