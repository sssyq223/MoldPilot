from datetime import date
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


def project(db,code,name='开工项目',status='DRAFT'):
    row=m.Project(code=code,name=name,status=status)
    db.add(row);db.flush();return row


def grant(db,admin,target,permission,project_id,fields=None):
    db.add(m.Grant(user_id=target.id,permission=permission,effect='ALLOW',scope={'project_id':[project_id]},
        fields=fields or PERMISSIONS[permission],reason='unit test',granted_by=admin.id))


def capability(db,target,key,kind='TOOL'):
    db.add(m.Capability(user_id=target.id,kind=kind,key=key,enabled=True))


def decision(db,project,user,kind,number,decision_value,status='EFFECTIVE',source_subject_id=None,mode='INTERNAL'):
    subject=m.BusinessSubject(kind=kind,number=number,project_id=project.id,created_by=user.id,status=status)
    db.add(subject);db.flush()
    db.add(m.BusinessDecisionDetail(subject_id=subject.id,source_subject_id=source_subject_id,
        decision=decision_value,execution_mode=mode,effective_date=date.today(),evidence='人工核对依据',
        amount=Decimal('100.00'),currency='CNY'))
    return subject


def sales_contract(db,project,user,number='SC-START'):
    subject=m.BusinessSubject(kind='sales_contract',number='SUBJECT-'+number,project_id=project.id,
        created_by=user.id,status='EFFECTIVE')
    db.add(subject);db.flush()
    db.add(m.ContractDetail(subject_id=subject.id,customer_id=None,supplier_id=None,amount=Decimal('100.00'),
        currency='CNY',contract_number=number,expected_date=date.today()))
    return subject


def test_start_readiness_schema_and_can_prepare_from_known_facts():
    engine,Session=factory()
    try:
        with Session.begin() as db:
            admin=user(db,'admin',True);p=project(db,'START-M001','正式开工核对项目')
            decision(db,p,admin,'quote_acceptance','QA-START','ACCEPT')
            sales_contract(db,p,admin)
        schema=tool_schema('query_internal_start_readiness')['function']['parameters']
        assert {'project_id','identifier'} <= set(schema['properties'])
        with Session() as db:
            admin=db.query(m.User).filter_by(username='admin').one()
            result=execute(db,admin,'query_internal_start_readiness',{'identifier':'START-M001'})
            assert result['resolution']=='RESOLVED'
            row=result['data'][0]
            assert row['readiness']['has_effective_acceptance'] is True
            assert row['readiness']['has_effective_internal_start'] is False
            assert row['readiness']['can_prepare_start_from_known_facts'] is True
            assert row['latest_acceptance']['number']=='QA-START'
            assert row['sales_contracts'][0]['detail']['contract_number']=='SC-START'
    finally:
        engine.dispose()


def test_start_readiness_reports_already_started_and_keeps_start_separate():
    engine,Session=factory()
    try:
        with Session.begin() as db:
            admin=user(db,'admin',True);p=project(db,'START-ACTIVE',status='ACTIVE')
            accept=decision(db,p,admin,'quote_acceptance','QA-ACTIVE','ACCEPT')
            decision(db,p,admin,'internal_start','START-001','START',source_subject_id=accept.id)
        with Session() as db:
            admin=db.query(m.User).filter_by(username='admin').one()
            result=execute(db,admin,'query_internal_start_readiness',{'identifier':'START-ACTIVE'})
            row=result['data'][0]
            assert row['readiness']['has_effective_acceptance'] is True
            assert row['readiness']['has_effective_internal_start'] is True
            assert row['readiness']['can_prepare_start_from_known_facts'] is False
            assert row['latest_internal_start']['number']=='START-001'
            assert '已有有效正式开工通知' in ''.join(row['readiness']['hints'])
    finally:
        engine.dispose()


def test_start_readiness_does_not_leak_acceptance_without_quote_tool():
    engine,Session=factory()
    try:
        with Session.begin() as db:
            admin=user(db,'admin',True);operator=user(db)
            p=project(db,'START-LIMITED')
            decision(db,p,admin,'quote_acceptance','SECRET-QA','ACCEPT')
            grant(db,admin,operator,'project.read',p.id);grant(db,admin,operator,'internal_start.read',p.id)
            capability(db,operator,'query_internal_start_readiness')
        with Session() as db:
            operator=db.query(m.User).filter_by(username='operator').one()
            result=execute(db,operator,'query_internal_start_readiness',{'identifier':'START-LIMITED'})
            row=result['data'][0]
            assert row['quote_acceptance']==[]
            assert row['readiness']['has_effective_acceptance'] is False
            assert row['readiness']['can_prepare_start_from_known_facts'] is False
            assert 'SECRET-QA' not in str(result)
            assert '承接依据' in ''.join(result['limitations'])
    finally:
        engine.dispose()


def test_start_readiness_reports_multiple_candidates_without_deciding():
    engine,Session=factory()
    try:
        with Session.begin() as db:
            admin=user(db,'admin',True)
            p1=project(db,'START-A','共同开工项目A')
            p2=project(db,'START-B','共同开工项目B')
            decision(db,p1,admin,'quote_acceptance','QA-A','ACCEPT')
            decision(db,p2,admin,'quote_acceptance','QA-B','ACCEPT')
        with Session() as db:
            admin=db.query(m.User).filter_by(username='admin').one()
            result=execute(db,admin,'query_internal_start_readiness',{'identifier':'共同开工项目'})
            assert result['resolution']=='MULTIPLE_CANDIDATES'
            assert {row['code'] for row in result['data']}=={'START-A','START-B'}
            assert '请使用项目 ID' in ''.join(result['limitations'])
    finally:
        engine.dispose()
