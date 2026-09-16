from datetime import date, datetime, timedelta

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


def project(db,code,name='设计项目',status='ACTIVE'):
    row=m.Project(code=code,name=name,status=status)
    db.add(row);db.flush();return row


def material(db,code,name,category='raw_material',unit='PCS'):
    row=m.Material(code=code,name=name,category=category,unit=unit)
    db.add(row);db.flush();return row


def grant(db,admin,target,permission,project_id,fields=None):
    db.add(m.Grant(user_id=target.id,permission=permission,effect='ALLOW',scope={'project_id':[project_id]},
        fields=fields or PERMISSIONS[permission],reason='unit test',granted_by=admin.id))


def capability(db,target,key,kind='TOOL'):
    db.add(m.Capability(user_id=target.id,kind=kind,key=key,enabled=True))


def plan(db,project,user,number='PLAN-001',status='EFFECTIVE'):
    subject=m.BusinessSubject(kind='project_plan',number=number,project_id=project.id,created_by=user.id,status=status)
    db.add(subject);db.flush()
    db.add(m.PlanDetail(subject_id=subject.id,reason='设计路线关联计划'))
    return subject


def task(db,plan_subject,user,key,name,start,end,status='PLANNED'):
    row=m.PlanTask(plan_id=plan_subject.id,key=key,name=name,owner_user_id=user.id,
        planned_start=start,planned_end=end,status=status)
    db.add(row);db.flush();return row


def design(db,project,user,number='DESIGN-001',status='EFFECTIVE',revision='A0',created_at=None):
    subject=m.BusinessSubject(kind='design_route',number=number,project_id=project.id,created_by=user.id,status=status)
    if created_at:subject.created_at=created_at
    db.add(subject);db.flush()
    db.add(m.DesignDetail(subject_id=subject.id,design_type='NEW_MOLD',drawing_revision=revision,
        drawing_evidence='正式图纸版本 '+revision,reviewer_id=user.id))
    return subject


def item(db,design_subject,mat,route='INTERNAL',quantity='1',task_id=None):
    row=m.DesignItem(design_id=design_subject.id,material_id=mat.id,quantity=quantity,route=route,task_id=task_id)
    db.add(row);db.flush();return row


def contact(db,project,user,department,task_ref):
    case=m.ContactCase(project_id=project.id,category='raw_material',title='图纸改版影响采购与加工',
        description='客户改版后需核对BOM和路线',mode='ONLINE',created_by=user.id,request_key='REQ-1',
        request_hash='hash',customer_name='客户A',mold_number='MOLD-01',problem_source='DESIGN_ISSUE',
        current_stage='结构设计',change_type='CHANGE',urgency='URGENT')
    db.add(case);db.flush()
    db.add(m.ContactTask(case_id=case.id,department_id=department.id,title='核对加工路线',
        created_by=user.id,status='ASSIGNED',affected_type='PLAN_NODE',affected_ref=task_ref,
        impact_description='加工节点受图纸影响',planned_action='REWORK',delivery_impact_days=2,
        source_system='AGENT'))
    return case


def department(db):
    row=m.AssignmentGroup(kind='DEPARTMENT',name='设计部')
    db.add(row);db.flush();return row


def test_design_route_context_schema_routes_plan_and_contacts():
    engine,Session=factory()
    today=date.today()
    try:
        with Session.begin() as db:
            admin=user(db,'admin',True);p=project(db,'DES-M001')
            db.add(m.ProjectProfile(project_id=p.id,owner_user_id=admin.id,execution_mode='INTERNAL',
                customer_due_date=today+timedelta(days=30)))
            mat_core=material(db,'CORE-001','型芯零件')
            mat_steel=material(db,'STEEL-001','试模料标准钢材')
            mat_out=material(db,'OUT-001','委外电极','outsource')
            base=plan(db,p,admin)
            machining=task(db,base,admin,'machining','模具加工',today,today+timedelta(days=10),'RUNNING')
            purchase=task(db,base,admin,'trial-material','试模料采购',today,today+timedelta(days=5))
            previous_design=design(db,p,admin,'DESIGN-BASE-A0','CLOSED','A0',datetime(2026,1,1,9,0,0))
            item(db,previous_design,mat_core,'INTERNAL','1',machining.id)
            design_subject=design(db,p,admin,'DESIGN-BASE','EFFECTIVE','A1',datetime(2026,1,2,9,0,0))
            item(db,design_subject,mat_core,'INTERNAL','2',machining.id)
            item(db,design_subject,mat_steel,'PURCHASE','5',purchase.id)
            item(db,design_subject,mat_out,'OUTSOURCE','1')
            contact(db,p,admin,department(db),machining.key)
        schema=tool_schema('query_design_route_context')['function']['parameters']
        assert {'project_id','identifier'} <= set(schema['properties'])
        with Session() as db:
            admin=db.query(m.User).filter_by(username='admin').one()
            result=execute(db,admin,'query_design_route_context',{'identifier':'DES-M001'})
            assert result['resolution']=='RESOLVED'
            row=result['data'][0];analysis=row['analysis']
            assert analysis['latest_effective_design']['number']=='DESIGN-BASE'
            assert analysis['route_summary']['counts']=={'INTERNAL':1,'PURCHASE':1,'OUTSOURCE':1}
            assert {task['key'] for task in analysis['linked_plan_tasks']}=={'machining','trial-material'}
            assert analysis['engineering_contact_impacts'][0]['title']=='图纸改版影响采购与加工'
            assert analysis['derived_status']['has_engineering_contact_impacts'] is True
            impact=analysis['revision_impact']
            assert impact['status']=='COMPARED'
            assert impact['previous_design']['drawing_revision']=='A0'
            assert impact['latest_design']['drawing_revision']=='A1'
            assert impact['summary']['added']==2
            assert impact['summary']['quantity_changed']==1
            assert impact['derived_status']['has_bom_or_route_changes'] is True
            assert {task['key'] for task in impact['affected_plan_tasks']}=={'machining','trial-material'}
            candidate=analysis['plan_change_candidates'][0]
            assert candidate['candidate_status']=='READY_FOR_PLAN_CONTEXT_QUERY'
            assert candidate['recommended_next_tools']==['query_project_plan_context','prepare_project_plan_change']
            seed=candidate['plan_change_prepare_seed']
            assert seed['status']=='READY_TO_QUERY_PLAN_CONTEXT'
            assert seed['project_id']==p.id
            assert seed['project_version']==p.row_version
            assert seed['previous_id']==base.id
            assert set(seed['candidate_task_keys'])=={'machining','trial-material'}
            assert 'query_project_plan_context' in seed['required_before_prepare'][0]
            assert analysis['derived_status']['has_plan_change_candidates'] is True
            assert any(item['code']=='STEEL-001' for item in analysis['route_summary']['materials'])
    finally:
        engine.dispose()


def test_design_context_hides_plan_and_contact_without_tools():
    engine,Session=factory()
    today=date.today()
    try:
        with Session.begin() as db:
            admin=user(db,'admin',True);operator=user(db)
            p=project(db,'DES-LIMITED')
            mat=material(db,'SECRET-MAT','受控零件')
            base=plan(db,p,admin);machining=task(db,base,admin,'machining','秘密加工',today,today+timedelta(days=3))
            d=design(db,p,admin,'DESIGN-LIMITED');item(db,d,mat,'INTERNAL','1',machining.id)
            contact(db,p,admin,department(db),machining.key)
            grant(db,admin,operator,'project.read',p.id);grant(db,admin,operator,'design_route.read',p.id)
            capability(db,operator,'query_design_route_context')
        with Session() as db:
            operator=db.query(m.User).filter_by(username='operator').one()
            result=execute(db,operator,'query_design_route_context',{'identifier':'DES-LIMITED'})
            analysis=result['data'][0]['analysis']
            assert analysis['linked_plan_tasks']==[]
            assert analysis['engineering_contact_impacts']==[]
            assert analysis['revision_impact']['status']=='NO_PREVIOUS_COMPARABLE_DESIGN'
            assert analysis['plan_change_candidates']==[]
            assert '秘密加工' not in str(result)
            assert '工程联络影响' in ''.join(result['limitations'])
            assert analysis['route_summary']['materials'][0]['code']=='SECRET-MAT'
    finally:
        engine.dispose()


def test_design_context_reports_multiple_candidates_and_no_effective_design():
    engine,Session=factory()
    try:
        with Session.begin() as db:
            admin=user(db,'admin',True)
            p1=project(db,'DES-A','共同设计项目A')
            p2=project(db,'DES-B','共同设计项目B')
            mat=material(db,'PART-A','测试零件')
            d1=design(db,p1,admin,'DESIGN-DRAFT',status='DRAFT');item(db,d1,mat,'INTERNAL')
            d2=design(db,p2,admin,'DESIGN-EFF',status='EFFECTIVE');item(db,d2,mat,'PURCHASE')
        with Session() as db:
            admin=db.query(m.User).filter_by(username='admin').one()
            ambiguous=execute(db,admin,'query_design_route_context',{'identifier':'共同设计项目'})
            assert ambiguous['resolution']=='MULTIPLE_CANDIDATES'
            assert {row['code'] for row in ambiguous['data']}=={'DES-A','DES-B'}
            resolved=execute(db,admin,'query_design_route_context',{'identifier':'DES-A'})
            analysis=resolved['data'][0]['analysis']
            assert analysis['derived_status']['has_effective_design_route'] is False
            assert '未见生效设计版本' in ''.join(analysis['warnings'])
    finally:
        engine.dispose()
