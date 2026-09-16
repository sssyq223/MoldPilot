from datetime import date
from decimal import Decimal


from app import models as m
from app.authorization import PERMISSIONS
from pg_db import factory as pg_factory
from app.tool_gateway import execute, tool_schema


def factory():
    return pg_factory()


def user(db,username='operator',super_admin=False):
    row=m.User(username=username,display_name=username,password_hash='test',super_admin=super_admin)
    db.add(row);db.flush();return row


def project(db,code,name='报价项目',status='DRAFT'):
    row=m.Project(code=code,name=name,status=status)
    db.add(row);db.flush();return row


def grant(db,admin,target,permission,project_id,fields=None):
    db.add(m.Grant(user_id=target.id,permission=permission,effect='ALLOW',scope={'project_id':[project_id]},
        fields=fields or PERMISSIONS[permission],reason='unit test',granted_by=admin.id))


def capability(db,target,key,kind='TOOL'):
    db.add(m.Capability(user_id=target.id,kind=kind,key=key,enabled=True))


def decision(db,project,user,kind,number,decision_value,status='EFFECTIVE',mode='INTERNAL'):
    subject=m.BusinessSubject(kind=kind,number=number,project_id=project.id,created_by=user.id,status=status)
    db.add(subject);db.flush()
    db.add(m.BusinessDecisionDetail(subject_id=subject.id,decision=decision_value,execution_mode=mode,
        effective_date=date.today(),evidence='人工核对依据',amount=Decimal('100.00'),currency='CNY'))
    return subject


def sales_contract(db,project,user,number='SC-001'):
    subject=m.BusinessSubject(kind='sales_contract',number='SUBJECT-'+number,project_id=project.id,
        created_by=user.id,status='EFFECTIVE')
    db.add(subject);db.flush()
    db.add(m.ContractDetail(subject_id=subject.id,customer_id=None,supplier_id=None,amount=Decimal('100.00'),
        currency='CNY',contract_number=number,expected_date=date.today()))
    return subject


def test_quote_context_schema_and_effective_acceptance_summary():
    engine,Session=factory()
    try:
        with Session.begin() as db:
            admin=user(db,'admin',True);p=project(db,'QUOTE-M001')
            decision(db,p,admin,'quote_acceptance','QA-ACCEPT','ACCEPT')
            decision(db,p,admin,'internal_start','START-001','START')
            sales_contract(db,p,admin,'CONTRACT-001')
        schema=tool_schema('query_quote_acceptance_context')['function']['parameters']
        assert {'project_id','identifier'} <= set(schema['properties'])
        with Session() as db:
            admin=db.query(m.User).filter_by(username='admin').one()
            result=execute(db,admin,'query_quote_acceptance_context',{'identifier':'QUOTE-M001'})
            assert result['resolution']=='RESOLVED'
            row=result['data'][0]
            assert row['derived_status']['has_effective_acceptance'] is True
            assert row['derived_status']['has_formal_start'] is True
            assert row['derived_status']['has_sales_contract'] is True
            assert row['latest_acceptance']['number']=='QA-ACCEPT'
            assert row['internal_starts'][0]['number']=='START-001'
            assert row['sales_contracts'][0]['detail']['contract_number']=='CONTRACT-001'
    finally:
        engine.dispose()


def test_quote_context_reports_multiple_candidates_without_deciding():
    engine,Session=factory()
    try:
        with Session.begin() as db:
            admin=user(db,'admin',True)
            p1=project(db,'QUOTE-A','共同报价项目A');p2=project(db,'QUOTE-B','共同报价项目B')
            decision(db,p1,admin,'quote_acceptance','QA-A','ACCEPT')
            decision(db,p2,admin,'quote_acceptance','QA-B','REJECT',mode=None)
        with Session() as db:
            admin=db.query(m.User).filter_by(username='admin').one()
            result=execute(db,admin,'query_quote_acceptance_context',{'identifier':'共同报价项目'})
            assert result['resolution']=='MULTIPLE_CANDIDATES'
            assert {row['code'] for row in result['data']}=={'QUOTE-A','QUOTE-B'}
            assert '请使用项目 ID' in ''.join(result['limitations'])
    finally:
        engine.dispose()


def test_quote_context_contracts_require_sales_contract_tool():
    engine,Session=factory()
    try:
        with Session.begin() as db:
            admin=user(db,'admin',True);operator=user(db)
            p=project(db,'QUOTE-LIMITED')
            decision(db,p,admin,'quote_acceptance','QA-LIMITED','ACCEPT')
            sales_contract(db,p,admin,'HIDDEN-CONTRACT')
            grant(db,admin,operator,'project.read',p.id);grant(db,admin,operator,'quote_acceptance.read',p.id)
            capability(db,operator,'query_quote_acceptance_context')
        with Session() as db:
            operator=db.query(m.User).filter_by(username='operator').one()
            result=execute(db,operator,'query_quote_acceptance_context',{'identifier':'QUOTE-LIMITED'})
            row=result['data'][0]
            assert row['derived_status']['has_effective_acceptance'] is True
            assert row['sales_contracts']==[]
            assert 'HIDDEN-CONTRACT' not in str(result)
        with Session.begin() as db:
            admin=db.query(m.User).filter_by(username='admin').one();operator=db.query(m.User).filter_by(username='operator').one()
            p=db.query(m.Project).filter_by(code='QUOTE-LIMITED').one()
            grant(db,admin,operator,'sales_contract.read',p.id);capability(db,operator,'query_sales_contract')
        with Session() as db:
            operator=db.query(m.User).filter_by(username='operator').one()
            result=execute(db,operator,'query_quote_acceptance_context',{'identifier':'QUOTE-LIMITED'})
            assert result['data'][0]['sales_contracts'][0]['detail']['contract_number']=='HIDDEN-CONTRACT'
    finally:
        engine.dispose()

