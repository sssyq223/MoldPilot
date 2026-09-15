from datetime import date, timedelta

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


def project(db,code,name='计划项目',status='ACTIVE'):
    row=m.Project(code=code,name=name,status=status)
    db.add(row);db.flush();return row


def grant(db,admin,target,permission,project_id,fields=None):
    db.add(m.Grant(user_id=target.id,permission=permission,effect='ALLOW',scope={'project_id':[project_id]},
        fields=fields or PERMISSIONS[permission],reason='unit test',granted_by=admin.id))


def capability(db,target,key,kind='TOOL'):
    db.add(m.Capability(user_id=target.id,kind=kind,key=key,enabled=True))


def plan(db,project,user,number='PLAN-001',kind='project_plan',status='EFFECTIVE',previous_id=None):
    subject=m.BusinessSubject(kind=kind,number=number,project_id=project.id,created_by=user.id,status=status)
    db.add(subject);db.flush()
    db.add(m.PlanDetail(subject_id=subject.id,reason='项目计划核对',previous_id=previous_id))
    return subject


def task(db,plan_subject,user,key,name,start,end,status='PLANNED'):
    row=m.PlanTask(plan_id=plan_subject.id,key=key,name=name,owner_user_id=user.id,
        planned_start=start,planned_end=end,status=status,
        actual_start=start if status in {'RUNNING','DONE'} else None,
        actual_end=end if status=='DONE' else None)
    db.add(row);db.flush();return row


def depends(db,item,previous):
    db.add(m.TaskDependency(task_id=item.id,prerequisite_id=previous.id))


def test_plan_context_schema_and_progress_analysis():
    engine,Session=factory()
    today=date.today()
    try:
        with Session.begin() as db:
            admin=user(db,'admin',True);p=project(db,'PLAN-M001')
            db.add(m.ProjectProfile(project_id=p.id,owner_user_id=admin.id,
                execution_mode='INTERNAL',customer_due_date=today+timedelta(days=3)))
            base=plan(db,p,admin,'PLAN-BASE')
            design=task(db,base,admin,'design','结构设计及出图',today-timedelta(days=8),today-timedelta(days=6),'DONE')
            purchase=task(db,base,admin,'purchase','五金采购',today-timedelta(days=5),today-timedelta(days=1),'RUNNING')
            assembly=task(db,base,admin,'assembly','装配',today+timedelta(days=1),today+timedelta(days=4),'PLANNED')
            delivery=task(db,base,admin,'delivery','最终交付',today+timedelta(days=5),today+timedelta(days=7),'PLANNED')
            depends(db,purchase,design);depends(db,assembly,purchase);depends(db,delivery,assembly)
        schema=tool_schema('query_project_plan_context')['function']['parameters']
        assert {'project_id','identifier'} <= set(schema['properties'])
        with Session() as db:
            admin=db.query(m.User).filter_by(username='admin').one()
            result=execute(db,admin,'query_project_plan_context',{'identifier':'PLAN-M001'})
            assert result['resolution']=='RESOLVED'
            row=result['data'][0];analysis=row['analysis']
            assert analysis['active_plan']['number']=='PLAN-BASE'
            assert analysis['derived_status']['has_effective_plan'] is True
            assert analysis['derived_status']['has_overdue_task'] is True
            assert analysis['derived_status']['has_customer_due_risk'] is True
            assert analysis['running_tasks'][0]['key']=='purchase'
            assert analysis['dependency_blocked_tasks'][0]['key']=='assembly'
            assert 'trial' in analysis['milestone_coverage']['missing']
            assert any(task['key']=='delivery' for task in analysis['customer_due_risk_tasks'])
    finally:
        engine.dispose()


def test_plan_context_does_not_leak_plan_change_without_tool():
    engine,Session=factory()
    today=date.today()
    try:
        with Session.begin() as db:
            admin=user(db,'admin',True);operator=user(db)
            p=project(db,'PLAN-LIMITED')
            base=plan(db,p,admin,'PLAN-VISIBLE')
            task(db,base,admin,'design','结构设计',today,today+timedelta(days=1))
            change=plan(db,p,admin,'PLAN-CHANGE-SECRET',kind='plan_change',status='SUBMITTED',previous_id=base.id)
            task(db,change,admin,'design','结构设计调整',today,today+timedelta(days=2))
            grant(db,admin,operator,'project.read',p.id);grant(db,admin,operator,'project_plan.read',p.id)
            capability(db,operator,'query_project_plan_context')
        with Session() as db:
            operator=db.query(m.User).filter_by(username='operator').one()
            result=execute(db,operator,'query_project_plan_context',{'identifier':'PLAN-LIMITED'})
            row=result['data'][0]
            assert row['project_plans'][0]['number']=='PLAN-VISIBLE'
            assert row['plan_changes']==[]
            assert 'PLAN-CHANGE-SECRET' not in str(result)
            assert '计划变更' in ''.join(result['limitations'])
    finally:
        engine.dispose()


def test_plan_context_reports_no_effective_plan_and_multiple_candidates():
    engine,Session=factory()
    today=date.today()
    try:
        with Session.begin() as db:
            admin=user(db,'admin',True)
            p1=project(db,'PLAN-A','共同计划项目A')
            p2=project(db,'PLAN-B','共同计划项目B')
            draft=plan(db,p1,admin,'PLAN-DRAFT',status='DRAFT')
            task(db,draft,admin,'design','结构设计',today,today+timedelta(days=1))
            effective=plan(db,p2,admin,'PLAN-B-EFFECTIVE')
            task(db,effective,admin,'design','结构设计',today,today+timedelta(days=1))
        with Session() as db:
            admin=db.query(m.User).filter_by(username='admin').one()
            ambiguous=execute(db,admin,'query_project_plan_context',{'identifier':'共同计划项目'})
            assert ambiguous['resolution']=='MULTIPLE_CANDIDATES'
            assert {row['code'] for row in ambiguous['data']}=={'PLAN-A','PLAN-B'}
            resolved=execute(db,admin,'query_project_plan_context',{'identifier':'PLAN-A'})
            analysis=resolved['data'][0]['analysis']
            assert analysis['derived_status']['has_effective_plan'] is False
            assert '未见有效项目计划' in ''.join(analysis['warnings'])
    finally:
        engine.dispose()
