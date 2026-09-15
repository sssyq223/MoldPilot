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


def project(db,code,name='项目'):
    row=m.Project(code=code,name=name,status='ACTIVE')
    db.add(row);db.flush();return row


def grant(db,admin,target,permission,project_id,fields=None):
    db.add(m.Grant(user_id=target.id,permission=permission,effect='ALLOW',scope={'project_id':[project_id]},
        fields=fields or PERMISSIONS[permission],reason='unit test',granted_by=admin.id))


def capability(db,target,key,kind='TOOL'):
    db.add(m.Capability(user_id=target.id,kind=kind,key=key,enabled=True))


def test_business_match_schema_and_multiple_mold_candidates():
    engine,Session=factory()
    try:
        with Session.begin() as db:
            admin=user(db,'admin',True);p1=project(db,'MATCH-A','匹配项目A');p2=project(db,'MATCH-B','匹配项目B')
            mold=m.Mold(internal_number='SHARED-MOLD-X',name='共用模号')
            db.add(mold);db.flush()
            db.add_all([m.ProjectMold(project_id=p1.id,mold_id=mold.id),m.ProjectMold(project_id=p2.id,mold_id=mold.id)])
        schema=tool_schema('query_business_object_candidates')['function']['parameters']
        assert {'identifier','project_code','mold_number','contract_number','order_number'} <= set(schema['properties'])
        with Session() as db:
            admin=db.query(m.User).filter_by(username='admin').one()
            result=execute(db,admin,'query_business_object_candidates',{'mold_number':'SHARED-MOLD-X'})
            assert result['resolution']=='MULTIPLE_CANDIDATES'
            assert {row['project']['code'] for row in result['data']}=={'MATCH-A','MATCH-B'}
            assert all(row['match_quality']=='EXACT' for row in result['data'])
            assert '不会自动创建、合并' in ''.join(result['limitations'])
    finally:
        engine.dispose()


def test_business_match_hides_forbidden_project_even_with_same_mold():
    engine,Session=factory()
    try:
        with Session.begin() as db:
            admin=user(db,'admin',True);operator=user(db)
            visible=project(db,'VISIBLE-MATCH','可见项目');hidden=project(db,'HIDDEN-MATCH','隐藏项目')
            mold=m.Mold(internal_number='LIMITED-MOLD-01',name='受限模号')
            db.add(mold);db.flush()
            db.add_all([m.ProjectMold(project_id=visible.id,mold_id=mold.id),m.ProjectMold(project_id=hidden.id,mold_id=mold.id)])
            grant(db,admin,operator,'project.read',visible.id);grant(db,admin,operator,'project.dossier.read',visible.id)
            capability(db,operator,'query_business_object_candidates')
        with Session() as db:
            operator=db.query(m.User).filter_by(username='operator').one()
            result=execute(db,operator,'query_business_object_candidates',{'mold_number':'LIMITED-MOLD-01'})
            assert result['resolution']=='UNIQUE_CANDIDATE'
            assert [row['project']['code'] for row in result['data']]==['VISIBLE-MATCH']
            assert 'HIDDEN-MATCH' not in str(result)
    finally:
        engine.dispose()


def test_contract_number_only_participates_with_contract_query_capability():
    engine,Session=factory()
    try:
        with Session.begin() as db:
            admin=user(db,'admin',True);operator=user(db)
            p=project(db,'CONTRACT-MATCH','合同项目')
            subject=m.BusinessSubject(kind='sales_contract',number='SC-MATCH-OPEN',project_id=p.id,
                created_by=admin.id,status='EFFECTIVE')
            db.add(subject);db.flush()
            db.add(m.ContractDetail(subject_id=subject.id,customer_id=None,supplier_id=None,amount=Decimal('100.00'),
                currency='CNY',contract_number='SECRET-CONTRACT-001',expected_date=date.today()))
            grant(db,admin,operator,'project.read',p.id);grant(db,admin,operator,'project.dossier.read',p.id)
            capability(db,operator,'query_business_object_candidates')
        with Session() as db:
            operator=db.query(m.User).filter_by(username='operator').one()
            assert execute(db,operator,'query_business_object_candidates',{'contract_number':'SECRET-CONTRACT-001'})['resolution']=='NOT_FOUND'
        with Session.begin() as db:
            admin=db.query(m.User).filter_by(username='admin').one();operator=db.query(m.User).filter_by(username='operator').one()
            p=db.query(m.Project).filter_by(code='CONTRACT-MATCH').one()
            grant(db,admin,operator,'sales_contract.read',p.id);capability(db,operator,'query_sales_contract')
        with Session() as db:
            operator=db.query(m.User).filter_by(username='operator').one()
            result=execute(db,operator,'query_business_object_candidates',{'contract_number':'SECRET-CONTRACT-001'})
            assert result['resolution']=='UNIQUE_CANDIDATE'
            assert result['data'][0]['project']['code']=='CONTRACT-MATCH'
            assert result['data'][0]['evidence'][0]['label']=='合同号'
    finally:
        engine.dispose()
