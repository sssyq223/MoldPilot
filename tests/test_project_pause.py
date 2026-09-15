from datetime import date
from sqlalchemy import create_engine,select
from sqlalchemy.orm import sessionmaker
from app import models as m,domains,domain_schemas as s
from app.db import Base
from app.errors import DomainError


def database():
    engine=create_engine('sqlite+pysqlite:///:memory:')
    Base.metadata.create_all(engine)
    return sessionmaker(engine,expire_on_commit=False)()


def setup_project(db):
    user=m.User(username='owner',display_name='项目负责人',password_hash='test',super_admin=True)
    project=m.Project(code='P-PAUSE',name='暂停恢复测试',status='ACTIVE')
    db.add_all([user,project]);db.flush()
    db.add(m.ProjectProfile(project_id=project.id,owner_user_id=user.id,execution_mode='INTERNAL',
        customer_due_date=date(2026,10,31)))
    plan=m.BusinessSubject(kind='project_plan',number='PLAN-1',project_id=project.id,
        created_by=user.id,status='EFFECTIVE')
    db.add(plan);db.flush();db.add(m.PlanDetail(subject_id=plan.id,reason='基线'))
    completed=m.PlanTask(plan_id=plan.id,key='done',name='已完成节点',owner_user_id=user.id,
        planned_start=date(2026,9,1),planned_end=date(2026,9,5),actual_start=date(2026,9,1),
        actual_end=date(2026,9,5),status='DONE')
    pending=m.PlanTask(plan_id=plan.id,key='pending',name='未完成节点',owner_user_id=user.id,
        planned_start=date(2026,9,15),planned_end=date(2026,9,20),status='PLANNED')
    db.add_all([completed,pending]);db.commit()
    return user,project,completed,pending


def payload(project,decision,effective_date,**extra):
    return s.SubjectInput(kind='pause_resume',project_id=project.id,remark='客户通知',detail={
        'decision':decision,'effective_date':effective_date,'reason':'客户书面通知',
        'evidence':'已上传并由负责人核对的通知',**extra})


def test_resume_shifts_only_frozen_incomplete_tasks_and_preserves_customer_due_date():
    db=database();user,project,completed,pending=setup_project(db)
    pause=domains.create(db,user,payload(project,'PAUSE',date(2026,9,10),expected_resume_date=date(2026,9,20)))
    detail=db.get(m.ProjectPauseDetail,pause.id)
    assert [item['id'] for item in detail.task_snapshot]==[pending.id]
    domains.apply(db,user,pause)
    assert project.status=='PAUSED'

    resume=domains.create(db,user,payload(project,'RESUME',date(2026,9,13),source_pause_subject_id=pause.id))
    domains.apply(db,user,resume)

    assert project.status=='ACTIVE'
    assert (completed.planned_start,completed.planned_end)==(date(2026,9,1),date(2026,9,5))
    assert (pending.planned_start,pending.planned_end)==(date(2026,9,18),date(2026,9,23))
    record=db.scalar(select(m.PauseRecord).where(m.PauseRecord.subject_id==pause.id))
    shift=db.scalar(select(m.PauseTaskShift).where(m.PauseTaskShift.pause_id==record.id))
    assert record.shift_applied and record.shifted_days==3
    assert (shift.previous_start,shift.shifted_start)==(date(2026,9,15),date(2026,9,18))
    assert db.get(m.ProjectProfile,project.id).customer_due_date==date(2026,10,31)


def test_pause_approval_rejects_scope_changed_after_draft():
    db=database();user,project,_,pending=setup_project(db)
    pause=domains.create(db,user,payload(project,'PAUSE',date(2026,9,10)))
    pending.status='RUNNING';db.flush()
    try:domains.apply(db,user,pause)
    except DomainError as error:assert error.code=='PAUSE_SCOPE_CHANGED'
    else:raise AssertionError('changed scope must not be applied')
    assert project.status=='ACTIVE'


def test_resume_cannot_apply_twice():
    db=database();user,project,_,_=setup_project(db)
    pause=domains.create(db,user,payload(project,'PAUSE',date(2026,9,10)))
    domains.apply(db,user,pause)
    resume=domains.create(db,user,payload(project,'RESUME',date(2026,9,12),source_pause_subject_id=pause.id))
    domains.apply(db,user,resume)
    try:domains.apply(db,user,resume)
    except DomainError as error:assert error.code=='RESUME_STATE'
    else:raise AssertionError('resume must be idempotently blocked after first application')
