from datetime import date
from sqlalchemy import select
import pytest
from app import models as m,domains,domain_schemas as s,project_closure as closure
from app.db import now
from app.errors import DomainError
from pg_db import database


@pytest.fixture()
def db():
    session = database()
    try:
        yield session
    finally:
        engine = session.info.get("moldpilot_test_engine")
        session.close()
        if engine:
            engine.dispose()


def setup_project(db,code='P-CLOSE',task_status='DONE'):
    user=m.User(username='owner-'+code,display_name='项目负责人',password_hash='test',super_admin=True)
    project=m.Project(code=code,name='项目关闭测试',status='ACTIVE')
    db.add_all([user,project]);db.flush()
    db.add(m.ProjectProfile(project_id=project.id,owner_user_id=user.id,execution_mode='INTERNAL',settlement_status='OPEN'))
    plan=m.BusinessSubject(kind='project_plan',number='PLAN-'+code,project_id=project.id,created_by=user.id,status='EFFECTIVE')
    db.add(plan);db.flush();db.add(m.PlanDetail(subject_id=plan.id,reason='基线'))
    task=m.PlanTask(plan_id=plan.id,key='main',name='主要任务',owner_user_id=user.id,
        planned_start=date(2026,9,1),planned_end=date(2026,9,10),status=task_status,
        actual_start=date(2026,9,1) if task_status=='DONE' else None,
        actual_end=date(2026,9,10) if task_status=='DONE' else None)
    db.add(task);db.commit()
    return user,project,task


def close_payload(project,decision,**extra):
    return s.SubjectInput(kind='project_close',project_id=project.id,remark='项目关闭测试',detail={
        'decision':decision,'effective_date':date(2026,9,15),'reason':'经项目负责人核对',
        'evidence':'已上传并核对的书面依据','project_version':project.row_version,**extra})


def complete_manual_items(db,user,case):
    for item in closure.items(db,case.id):
        if item.system_managed:continue
        closure.update_item(db,user,case.id,case.version,item.item_key,'DONE',
            '已逐项核对并完成','对应业务原件已核验','MANUAL',None,None)


def test_termination_stops_only_unfinished_local_tasks_and_opens_different_checklist(db):
    user,project,task=setup_project(db,task_status='RUNNING')
    subject=domains.create(db,user,close_payload(project,'TERMINATE',current_stage='制造加工',
        completed_work_summary='设计已完成，制造完成约六成',incurred_cost_summary='财务已汇总当前发生额',
        incurred_cost_amount='125000.00',currency='CNY'))
    domains.apply(db,user,subject)
    case=db.scalar(select(m.ProjectClosureCase).where(m.ProjectClosureCase.project_id==project.id))
    assert project.status=='TERMINATED' and task.status=='STOPPED'
    assert case.mode=='TERMINATION' and case.status=='OPEN'
    states={item.item_key:item.status for item in closure.items(db,case.id)}
    assert states['TERMINATION_NOTICE']=='DONE' and states['CURRENT_STAGE']=='DONE'
    assert states['DELIVERY_DISPOSITION']=='PENDING' and states['CUSTOMER_SETTLEMENT']=='PENDING'
    assert db.get(m.ProjectProfile,project.id).settlement_status=='TERMINATION_PENDING'


def test_erp_item_requires_native_reference_and_changes_keep_history(db):
    user,project,_=setup_project(db)
    case=closure.open_normal_case(db,user,project.id,project.row_version,'交付后结项','启动完整核对')
    item=next(row for row in closure.items(db,case.id) if row.item_key=='DELIVERY')
    with pytest.raises(DomainError,match='ERP 来源'):
        closure.update_item(db,user,case.id,case.version,item.item_key,'DONE','已交付','ERP 已核对','ERP',None,None)
    closure.update_item(db,user,case.id,case.version,item.item_key,'DONE','已交付','签收单已核对','ERP','delivery:1001',now())
    closure.update_item(db,user,case.id,case.version,item.item_key,'DONE','已交付并补充物流回执','新增回执','MANUAL',None,None)
    history=list(db.scalars(select(m.ProjectClosureItemRevision).where(
        m.ProjectClosureItemRevision.item_id==item.id).order_by(m.ProjectClosureItemRevision.revision)))
    assert [row.revision for row in history]==[1,2,3]
    assert history[1].source_ref=='delivery:1001' and history[2].result=='已交付并补充物流回执'
    assert item.revision==3 and case.version==3


def test_normal_close_rechecks_live_open_issues_and_cannot_use_a_completed_snapshot(db):
    user,project,_=setup_project(db)
    case=closure.open_normal_case(db,user,project.id,project.row_version,'客户验收后','核对正常关闭条件')
    complete_manual_items(db,user,case)
    db.add(m.ContactCase(project_id=project.id,title='关闭前新增异常',description='仍需处理',mode='ONLINE',
        created_by=user.id,request_key='open-contact',request_hash='x'*64));db.commit()
    with pytest.raises(DomainError) as error:
        domains.create(db,user,close_payload(project,'NORMAL_CLOSE',closure_case_id=case.id,
            closure_case_version=case.version))
    assert error.value.code=='CLOSE_BLOCKED' and '工程联络' in error.value.message
    assert project.status=='ACTIVE' and case.status=='OPEN'


def test_normal_close_uses_live_plan_completion_and_archives_system_check_revision(db):
    user,project,task=setup_project(db,code='P-NORMAL',task_status='RUNNING')
    case=closure.open_normal_case(db,user,project.id,project.row_version,'交付后','启动正常结项')
    complete_manual_items(db,user,case)
    task.status='DONE';task.actual_end=date(2026,9,15);db.flush()
    final=domains.create(db,user,close_payload(project,'NORMAL_CLOSE',closure_case_id=case.id,
        closure_case_version=case.version))
    domains.apply(db,user,final)
    plan_item=next(row for row in closure.items(db,case.id) if row.item_key=='PLAN_COMPLETION')
    history=list(db.scalars(select(m.ProjectClosureItemRevision).where(
        m.ProjectClosureItemRevision.item_id==plan_item.id).order_by(m.ProjectClosureItemRevision.revision)))
    assert project.status=='CLOSED' and case.status=='CLOSED'
    assert plan_item.status=='DONE' and [row.to_status for row in history]==['PENDING','DONE']
    assert db.get(m.ProjectProfile,project.id).settlement_status=='CLOSED_NORMAL'


def test_termination_settlement_close_keeps_history_and_uses_termination_conditions(db):
    user,project,_=setup_project(db,code='P-TERM',task_status='RUNNING')
    terminate=domains.create(db,user,close_payload(project,'TERMINATE',current_stage='试模前',
        completed_work_summary='设计和主要加工已完成',incurred_cost_summary='已发生费用由财务复核'))
    domains.apply(db,user,terminate)
    case=closure._open_case(db,project.id)
    complete_manual_items(db,user,case)
    final=domains.create(db,user,close_payload(project,'SETTLEMENT_CLOSE',closure_case_id=case.id,
        closure_case_version=case.version))
    domains.apply(db,user,final)
    assert project.status=='CLOSED' and case.status=='CLOSED' and case.closed_by==user.id
    assert db.get(m.ProjectProfile,project.id).settlement_status=='CLOSED_TERMINATION'
    with pytest.raises(DomainError):
        closure.update_item(db,user,case.id,case.version,'DELIVERY_DISPOSITION','NOT_APPLICABLE',
            '终止后不交付','客户书面确认','MANUAL',None,None)
