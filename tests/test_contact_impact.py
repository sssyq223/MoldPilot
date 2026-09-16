from datetime import date
from uuid import uuid4
from pydantic import ValidationError
from sqlalchemy import select
import pytest
from app import contacts,contact_lifecycle as lifecycle,models as m
from app.errors import DomainError
from fastapi.encoders import jsonable_encoder
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


def setup_case(db):
    user=m.User(username='contact-owner',display_name='联络负责人',password_hash='test',super_admin=True)
    project=m.Project(code='CONTACT-P001',name='联络影响测试',status='ACTIVE')
    group=m.AssignmentGroup(kind='DEPARTMENT',name='设计部',active=True,version=1)
    db.add_all([user,project,group]);db.flush()
    db.add(m.AssignmentMember(group_id=group.id,user_id=user.id,is_head=True))
    case=m.ContactCase(project_id=project.id,category='hardware',title='装配尺寸异常',description='尺寸与图纸不符',
        mode='ONLINE',created_by=user.id,request_key=str(uuid4()),request_hash='x'*64,revision=1,
        customer_ref='ERP-C-001',customer_name='测试客户',mold_number='MOLD-001',product_ref='PART-001',
        application_date=date(2026,9,10),problem_source='ASSEMBLY_ISSUE',current_stage='装配阶段',
        change_type='EXCEPTION',urgency='URGENT')
    db.add(case);db.commit()
    return user,case,group


def task_input(case,group,**overrides):
    payload={'request_key':uuid4(),'revision':case.revision,'department_id':group.id,'title':'返工受影响图纸',
        'affected_type':'DRAWING','affected_ref':'DRAWING-001-R2','impact_description':'装配尺寸须按新版本返工',
        'planned_action':'REWORK','delivery_impact_days':2,'estimated_amount':'1200.00','currency':'CNY',
        'source_system':'AGENT','source_ref':None,'source_as_of':None,**overrides}
    return contacts.TaskInput(**payload)


def test_contact_requires_business_identity_and_rejects_future_application_date():
    with pytest.raises(ValidationError):
        contacts.CreateInput(request_key=uuid4(),project_id='p',category='hardware',title='异常',description='说明',mode='ONLINE')
    with pytest.raises(ValidationError,match='申请日期'):
        contacts.CreateInput(request_key=uuid4(),project_id='p',category='hardware',title='异常',description='说明',mode='ONLINE',
            customer_ref='ERP-C',customer_name='客户',mold_number='M1',product_ref='P1',application_date='2099-01-01',
            problem_source='QUALITY_ISSUE',current_stage='质检',change_type='EXCEPTION',urgency='URGENT')


def test_structured_impact_is_frozen_into_resolution_materials_and_erp_requires_provenance(db):
    user,case,group=setup_case(db)
    with pytest.raises(ValidationError,match='ERP影响事实'):
        task_input(case,group,source_system='ERP')
    result=contacts.add_task(case.id,task_input(case,group),user,db)
    task=db.scalar(select(m.ContactTask))
    snapshot=lifecycle.materials(db,case)
    assert result['tasks'][0]['planned_action']=='REWORK'
    assert snapshot['tasks'][0]['affected_ref']=='DRAWING-001-R2'
    assert snapshot['tasks'][0]['estimated_amount']=='1200.00'
    assert task.delivery_impact_days==2 and task.impact_description=='装配尺寸须按新版本返工'


def test_execution_feedback_records_time_hours_amount_evidence_and_source_without_overwrite(db):
    user,case,group=setup_case(db)
    contacts.add_task(case.id,task_input(case,group),user,db)
    task=db.scalar(select(m.ContactTask));task.assignee_id=user.id;task.status='ASSIGNED';db.flush()
    with pytest.raises(ValidationError,match='ERP执行结果'):
        contacts.ResponseInput(request_key=uuid4(),revision=case.revision,content='已完成',actual_completed_at='2026-09-10T12:00:00+08:00',
            actual_hours='3.5',actual_amount='1180',currency='CNY',execution_evidence='ERP报工记录',source_system='ERP')
    response=contacts.ResponseInput(request_key=uuid4(),revision=case.revision,content='返工与尺寸复测完成',
        actual_completed_at='2026-09-10T12:00:00+08:00',actual_hours='3.50',actual_amount='1180.00',currency='CNY',
        execution_evidence='返工记录与尺寸复测报告',source_system='MANUAL')
    result=contacts.respond(case.id,task.id,response,user,db)
    assert result['tasks'][0]['status']=='RESPONDED' and str(result['tasks'][0]['actual_hours'])=='3.50'
    assert result['tasks'][0]['execution_evidence']=='返工记录与尺寸复测报告'
    record=db.scalar(select(m.ContactRecord).where(m.ContactRecord.kind=='RESPONDED'))
    assert record.detail['actual_amount']=='1180.00' and record.detail['source_system']=='MANUAL'
    with pytest.raises(DomainError,match='已有反馈'):
        contacts.respond(case.id,task.id,contacts.ResponseInput(**{**response.model_dump(),'request_key':uuid4(),'revision':case.revision}),user,db)


def test_effective_resolution_records_handoff_once_and_increments_case_revision(db):
    user,case,group=setup_case(db)
    contacts.add_task(case.id,task_input(case,group),user,db)
    subject=m.BusinessSubject(kind='contact_resolution',number='CONTACT-PLAN-001',project_id=case.project_id,
        category=case.category,created_by=user.id,status='EFFECTIVE')
    db.add(subject);db.flush()
    resolution=m.ContactResolution(subject_id=subject.id,case_id=case.id,case_revision=case.revision,
        solution='按批准方案返工并复测',customer_due_affected=False,material_snapshot=jsonable_encoder(lifecycle.materials(db,case)))
    db.add(resolution);db.flush();before=case.revision
    lifecycle.activate_resolution(db,user,resolution)
    lifecycle.activate_resolution(db,user,resolution)
    records=list(db.scalars(select(m.ContactRecord).where(m.ContactRecord.kind=='RESOLUTION_EFFECTIVE')))
    assert case.revision==before+1 and len(records)==1
    assert records[0].detail['task_ids']==[db.scalar(select(m.ContactTask.id))]
