from datetime import date
from sqlalchemy import select
import pytest
from app import models as m,domains,domain_schemas as s
from app.errors import DomainError
from app.tool_gateway import execute,tool_schema
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


def test_resume_shifts_only_frozen_incomplete_tasks_and_preserves_customer_due_date(db):
    user,project,completed,pending=setup_project(db)
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


def test_pause_approval_rejects_scope_changed_after_draft(db):
    user,project,_,pending=setup_project(db)
    pause=domains.create(db,user,payload(project,'PAUSE',date(2026,9,10)))
    pending.status='RUNNING';db.flush()
    try:domains.apply(db,user,pause)
    except DomainError as error:assert error.code=='PAUSE_SCOPE_CHANGED'
    else:raise AssertionError('changed scope must not be applied')
    assert project.status=='ACTIVE'


def test_resume_cannot_apply_twice(db):
    user,project,_,_=setup_project(db)
    pause=domains.create(db,user,payload(project,'PAUSE',date(2026,9,10)))
    domains.apply(db,user,pause)
    resume=domains.create(db,user,payload(project,'RESUME',date(2026,9,12),source_pause_subject_id=pause.id))
    domains.apply(db,user,resume)
    try:domains.apply(db,user,resume)
    except DomainError as error:assert error.code=='RESUME_STATE'
    else:raise AssertionError('resume must be idempotently blocked after first application')


def test_project_control_context_resolves_identifier_and_returns_shift_evidence(db):
    user,project,completed,pending=setup_project(db)
    pause=domains.create(db,user,payload(project,'PAUSE',date(2026,9,10),expected_resume_date=date(2026,9,20)))
    domains.apply(db,user,pause)
    resume=domains.create(db,user,payload(project,'RESUME',date(2026,9,13),source_pause_subject_id=pause.id))
    domains.apply(db,user,resume)

    schema=tool_schema('query_project_control_context')['function']['parameters']
    assert {'project_id','identifier'}<=set(schema['properties'])
    result=execute(db,user,'query_project_control_context',{'identifier':'P-PAUSE'})
    assert result['resolution']=='RESOLVED'
    row=result['data'][0]
    assert row['code']=='P-PAUSE'
    assert row['derived_status']['has_resume_shift_evidence'] is True
    assert row['derived_status']['customer_due_date_is_independent'] is True
    assert row['customer_due_date']=='2026-10-31'
    assert row['pause_history'][0]['shifted_days']==3
    assert row['pause_history'][0]['task_shift_count']==1
    assert row['pause_history'][0]['task_shifts'][0]['previous_start']=='2026-09-15'
    assert row['pause_history'][0]['task_shifts'][0]['shifted_start']=='2026-09-18'
    assert '普通下单' in ''.join(row['blocked_during_pause'])
    assert '工程联络' in ''.join(row['allowed_during_pause'])
    assert '不同事实' in ''.join(result['limitations'])


def test_project_control_prepare_rejects_identifier_only(db):
    user,project,_,_=setup_project(db)
    try:
        execute(db,user,'prepare_project_pause',{'identifier':'P-PAUSE','project_version':1,
            'effective_date':'2026-09-10','reason':'客户通知','evidence':'邮件',
            'workflow_definition_id':'wf'})
    except DomainError as error:assert error.code=='INVALID_TOOL_INPUT'
    else:raise AssertionError('write proposals must use the real project_id returned by query context')


def test_project_control_context_reports_multiple_candidates_without_deciding(db):
    user,project,_,_=setup_project(db)
    other=m.Project(code='P-PAUSE-2',name='暂停恢复测试二',status='ACTIVE')
    db.add(other);db.flush()
    db.add(m.ProjectProfile(project_id=other.id,owner_user_id=user.id,execution_mode='INTERNAL'))
    db.commit()
    result=execute(db,user,'query_project_control_context',{'identifier':'暂停恢复'})
    assert result['resolution']=='MULTIPLE_CANDIDATES'
    assert {row['code'] for row in result['data']}=={'P-PAUSE','P-PAUSE-2'}
    assert '请使用项目 ID' in ''.join(result['limitations'])
