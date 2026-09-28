from uuid import uuid4
from datetime import timedelta

import pytest
from sqlalchemy import select

from app import models as m, business, domain_schemas as s
from app.db import now
from app.errors import DomainError
from app.tool_gateway import execute
from domain_packs.mold.erp.core import domains
from domain_packs.mold.erp.change import contacts, contact_lifecycle, contact_execution
from test_change_start import setup_change, prepare_confirm
from test_domains import approve
from test_contacts import response_body


def setup_execution(client,data):
    ids,Session=data
    args,run_id,case_id=setup_change(client,data)
    with Session.begin() as db:
        case=db.get(m.ContactCase,case_id)
        task=db.scalar(select(m.ContactTask).where(m.ContactTask.case_id==case_id))
        db.add(m.AssignmentMember(group_id=task.department_id,user_id=ids['buyer'],is_head=False))
        for permission in ('contact.read','contact.respond','contact_resolution.read'):
            db.add(m.Grant(user_id=ids['buyer'],permission=permission,effect='ALLOW',
                scope={'project_id':[ids['project']]},fields=['*'],reason='设变执行验证',granted_by=ids['admin']))
        contacts.assign(case_id,task.id,contacts.AssignInput(request_key=uuid4(),revision=case.revision,
            assignee_id=ids['buyer'],reason='指定责任处理人'),db.get(m.User,ids['admin']),db)
        task_id=task.id
    receipt=prepare_confirm(Session,ids,args,run_id)
    assert approve(client,receipt['instance_id'])['business_status']=='EFFECTIVE'
    return args,case_id,task_id,receipt


def feedback(Session,ids,case_id,task_id,args,receipt,linked=True,**overrides):
    with Session.begin() as db:
        case=db.get(m.ContactCase,case_id)
        payload=response_body(actual_completed_at=now(),source_system='MANUAL',**overrides)
        if linked:
            payload.update(execution_plan_id=args['source_subject_id'],execution_start_id=receipt['subject_id'])
        return contacts.respond(case_id,task_id,contacts.ResponseInput(
            request_key=uuid4(),revision=case.revision,**payload),db.get(m.User,ids['buyer']),db)


def review(Session,ids,case_id,task_id,decision='PASS'):
    with Session.begin() as db:
        case=db.get(m.ContactCase,case_id)
        return contact_lifecycle.execute(db,db.get(m.User,ids['admin']),case_id,task_id,'review',
            contact_lifecycle.ReviewInput(request_key=uuid4(),revision=case.revision,decision=decision,evidence='独立复验及报告核对'))


def test_change_execution_feedback_links_approval_then_independent_review_and_close(client,data):
    ids,Session=data
    args,case_id,task_id,receipt=setup_execution(client,data)
    with Session() as db:
        result=execute(db,db.get(m.User,ids['admin']),'query_change_intake_context',{'project_id':ids['project']})
        analysis=result['data'][0]['analysis']
        assert analysis['derived_status']['all_current_changes_started'] is True
        assert analysis['change_start_readiness'][0]['start_subject_id']==receipt['subject_id']
        visible=contact_lifecycle.context(db,db.get(m.User,ids['admin']),db.get(m.ContactCase,case_id))
        assert visible['execution_basis_options'][0]['execution_start_id']==receipt['subject_id']
        restricted=contact_lifecycle.context(db,db.get(m.User,ids['buyer']),db.get(m.ContactCase,case_id))
        assert restricted['execution_basis_options']==[]
    reported=feedback(Session,ids,case_id,task_id,args,receipt)
    assert reported['tasks'][0]['execution_basis']['plan_id']==args['source_subject_id']
    assert reported['tasks'][0]['execution_basis']['start_subject_id']==receipt['subject_id']
    checked=review(Session,ids,case_id,task_id)
    assert checked['progress_summary']['state']=='READY_TO_CLOSE'
    with Session.begin() as db:
        case=db.get(m.ContactCase,case_id)
        closed=contact_lifecycle.execute(db,db.get(m.User,ids['admin']),case_id,'','close',
            contact_lifecycle.CloseInput(request_key=uuid4(),revision=case.revision,evidence='本次执行与复验均已逐项核对'))
        assert closed['collaboration_status']=='CLOSED'
        records=list(db.scalars(select(m.ContactRecord).where(m.ContactRecord.case_id==case_id,m.ContactRecord.kind=='RESPONDED')))
        assert len(records)==1 and records[0].detail['execution_basis']['status']=='LINKED'


def test_unlinked_fact_is_preserved_but_cannot_pass_review_without_new_feedback(client,data):
    ids,Session=data
    args,case_id,task_id,receipt=setup_execution(client,data)
    reported=feedback(Session,ids,case_id,task_id,args,receipt,linked=False)
    assert reported['tasks'][0]['execution_basis']['status']=='UNLINKED_FACT'
    with pytest.raises(DomainError) as error:review(Session,ids,case_id,task_id)
    assert error.value.code=='EXECUTION_BASIS_STALE'
    review(Session,ids,case_id,task_id,'REWORK')
    feedback(Session,ids,case_id,task_id,args,receipt)
    assert review(Session,ids,case_id,task_id)['tasks'][0]['status']=='VERIFIED'
    with Session() as db:
        records=list(db.scalars(select(m.ContactRecord).where(m.ContactRecord.case_id==case_id,
            m.ContactRecord.kind=='RESPONDED').order_by(m.ContactRecord.created_at)))
        assert [r.detail['execution_basis']['status'] for r in records]==['UNLINKED_FACT','LINKED']


def test_feedback_cannot_backdate_execution_before_formal_start(client,data):
    ids,Session=data
    args,case_id,task_id,receipt=setup_execution(client,data)
    with Session() as db:
        case=db.get(m.ContactCase,case_id)
        body=contacts.ResponseInput(request_key=uuid4(),revision=case.revision,
            **response_body(actual_completed_at=now()-timedelta(days=1)),
            execution_plan_id=args['source_subject_id'],execution_start_id=receipt['subject_id'])
        with pytest.raises(DomainError) as error:
            contact_execution.preview_basis(db,db.get(m.User,ids['buyer']),case,body)
        assert error.value.code=='EXECUTION_PRECEDES_START'


def test_new_plan_does_not_reuse_prior_execution_basis(client,data):
    ids,Session=data
    args,case_id,task_id,receipt=setup_execution(client,data)
    feedback(Session,ids,case_id,task_id,args,receipt)
    with Session.begin() as db:
        admin=db.get(m.User,ids['admin']);case=db.get(m.ContactCase,case_id)
        new=domains.create(db,admin,s.SubjectInput(kind='contact_resolution',project_id=case.project_id,
            category=case.category,detail={'case_id':case.id,'case_revision':case.revision,
                'solution':'变更后须重新执行新增范围','customer_due_affected':False,'customer_evidence':'客户新版本书面确认'}))
        definition=db.scalar(select(m.WorkflowDefinition).where(m.WorkflowDefinition.process_key=='contact_resolution_test'))
        submitted=business.submit_subject(db,admin,new.id,new.revision,definition.id)
        new_id=new.id
    assert approve(client,submitted['instance_id'])['business_status']=='EFFECTIVE'
    with Session() as db:
        run_id=db.scalar(select(m.Run.id))
        args2={**args,'source_subject_id':new_id,'project_version':db.get(m.Project,ids['project']).row_version}
    second=prepare_confirm(Session,ids,args2,run_id,sequence=1)
    assert approve(client,second['instance_id'])['business_status']=='EFFECTIVE'
    with pytest.raises(DomainError) as review_error:
        review(Session,ids,case_id,task_id)
    assert review_error.value.code=='EXECUTION_BASIS_STALE'
    with Session() as db:
        old=contact_execution.latest_basis(db,db.get(m.ContactTask,task_id))
        assert old['plan_id']==args['source_subject_id'] and old['plan_id']!=new_id
        with pytest.raises(DomainError) as error:
            contact_execution.require_current_basis(db,db.get(m.ContactCase,case_id),
                db.get(m.ContactTask,task_id),db.get(m.ContactResolution,new_id))
        assert error.value.code=='EXECUTION_BASIS_STALE'


def test_response_hash_keeps_historical_unlinked_retries_stable(client,data):
    from app.bpm import content_hash
    ids,Session=data
    args,case_id,task_id,receipt=setup_execution(client,data)
    with Session.begin() as db:
        case=db.get(m.ContactCase,case_id);worker=db.get(m.User,ids['buyer'])
        body=contacts.ResponseInput(request_key=uuid4(),revision=case.revision,
            **response_body(actual_completed_at=now(),source_system='MANUAL'))
        legacy=body.model_dump(mode='json',exclude={'execution_plan_id','execution_start_id'})
        expected=content_hash({'action':'RESPOND:'+task_id,'input':legacy})
        db.add(m.ContactRecord(case_id=case_id,author_id=worker.id,request_key=str(body.request_key),
            request_hash=expected,kind='RESPONDED',occurred_at=now(),detail={'task_id':task_id}))
        db.flush()
        assert contacts.replay(db,worker,case,body,'RESPOND:'+task_id)==(expected,True)
