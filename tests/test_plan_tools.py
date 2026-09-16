from datetime import date, timedelta

from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker

from app import bpm, business, domains, domain_schemas as s, models as m
from app.authorization import PERMISSIONS, fingerprint
from app.models import Base
from app.tool_gateway import execute, tool_schema
from conftest import sign_in
from test_agent_api import start, worker_headers
from test_domains import workflow as create_workflow


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


def test_plan_change_skill_context_returns_active_change_and_workflows_without_generic_query_tool():
    engine,Session=factory()
    today=date.today()
    try:
        with Session.begin() as db:
            admin=user(db,'admin',True);operator=user(db)
            p=project(db,'PLAN-CHANGE-SKILL')
            base=plan(db,p,admin,'PLAN-OLD',status='CLOSED')
            task(db,base,admin,'design','结构设计',today,today+timedelta(days=1),'DONE')
            change=plan(db,p,admin,'PLAN-CHANGE-EFFECTIVE',kind='plan_change',status='EFFECTIVE',previous_id=base.id)
            task(db,change,admin,'design','结构设计',today,today+timedelta(days=1),'DONE')
            task(db,change,admin,'trial','试模',today+timedelta(days=2),today+timedelta(days=3))
            definition=m.WorkflowDefinition(process_key='plan_change_visible',version=1,name='计划变更审批',
                status='PUBLISHED',config={'business_type':'plan_change','nodes':[{'key':'review','name':'计划负责人确认','mode':'ALL','users':[admin.id],'reject_rules':[]}]})
            db.add(definition)
            for permission in ('project.read','project_plan.read','plan_change.read','plan_change.create','plan_change.submit'):
                grant(db,admin,operator,permission,p.id)
            capability(db,operator,'query_project_plan_context')
            capability(db,operator,'prepare_project_plan_change')
        with Session() as db:
            operator=db.query(m.User).filter_by(username='operator').one()
            result=execute(db,operator,'query_project_plan_context',{'identifier':'PLAN-CHANGE-SKILL'})
            row=result['data'][0]
            assert row['analysis']['active_plan']['number']=='PLAN-CHANGE-EFFECTIVE'
            assert row['plan_changes'][0]['number']=='PLAN-CHANGE-EFFECTIVE'
            assert row['workflow_options'][0]['id']==definition.id
            assert '未返回：计划变更' not in ''.join(result['limitations'])
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


def test_plan_change_effective_notifies_changed_task_owners():
    engine,Session=factory()
    try:
        with Session.begin() as db:
            admin=user(db,'admin',True);old_owner=user(db,'old_owner');new_owner=user(db,'new_owner')
            p=project(db,'PLAN-NOTIFY')
            baseline=plan(db,p,admin,'PLAN-NOTIFY-BASE')
            task(db,baseline,admin,'design','结构设计',date(2026,9,1),date(2026,9,5),'DONE')
            task(db,baseline,old_owner,'machining','加工',date(2026,9,6),date(2026,9,20),'PLANNED')
            change=domains.create(db,admin,s.SubjectInput(kind='plan_change',project_id=p.id,remark='加工顺延并新增试模',detail={
                'previous_id':baseline.id,'reason':'客户确认加工顺延并新增试模节点',
                'tasks':[{'key':'design','name':'结构设计','owner_user_id':admin.id,
                    'planned_start':'2026-09-01','planned_end':'2026-09-05','prerequisites':[]},
                    {'key':'machining','name':'加工','owner_user_id':new_owner.id,
                    'planned_start':'2026-09-08','planned_end':'2026-09-23','prerequisites':['design']},
                    {'key':'trial','name':'试模','owner_user_id':new_owner.id,
                    'planned_start':'2026-09-24','planned_end':'2026-09-26','prerequisites':['machining']}]}))
            domains.apply(db,admin,change)
            event=db.scalar(select(m.Outbox).where(m.Outbox.kind=='plan.change.effective',m.Outbox.resource_id==change.id))
            assert event
            assert set(event.payload['recipients'])=={old_owner.id,new_owner.id}
            audit=db.scalar(select(m.AuditEvent).where(m.AuditEvent.action=='plan.change.effective',m.AuditEvent.resource_id==change.id))
            assert audit.detail['previous_id']==baseline.id
            assert audit.detail['changed_task_keys']==['machining','trial']
            assert audit.detail['changed_tasks'][0]['changes']['owner_user_id']=={'from':old_owner.id,'to':new_owner.id}
            assert db.get(m.BusinessSubject,baseline.id).status=='CLOSED'
    finally:
        engine.dispose()


def test_plan_change_proposal_requires_human_confirmation_then_submits_bpm(client,data,monkeypatch):
    ids,factory=data;sign_in(client)
    definition=create_workflow(client,ids,'plan_change')
    with factory.begin() as db:
        admin=db.get(m.User,ids['admin']);project_row=db.get(m.Project,ids['project'])
        baseline=m.BusinessSubject(kind='project_plan',number='PLAN-BASE-AGENT',project_id=project_row.id,
            created_by=admin.id,status='EFFECTIVE')
        db.add(baseline);db.flush()
        db.add(m.PlanDetail(subject_id=baseline.id,reason='原始基线计划'))
        done=m.PlanTask(plan_id=baseline.id,key='design',name='结构设计',owner_user_id=admin.id,
            planned_start=date(2026,9,1),planned_end=date(2026,9,5),actual_start=date(2026,9,1),
            actual_end=date(2026,9,5),status='DONE')
        machining=m.PlanTask(plan_id=baseline.id,key='machining',name='加工',owner_user_id=admin.id,
            planned_start=date(2026,9,6),planned_end=date(2026,9,20),status='PLANNED')
        db.add_all([done,machining]);db.flush()
        baseline_id=baseline.id;project_version=project_row.row_version
    _,ctx=start(client,monkeypatch,'admin')
    with factory.begin() as db:
        run=db.get(m.Run,ctx['id'])
        run.checkpoint={**run.checkpoint,'agent_permission_mode':'delegated_auto'}
    args={'project_id':ids['project'],'project_version':project_version,'previous_id':baseline_id,
        'reason':'客户确认加工节点顺延，并补充试模节点','workflow_definition_id':definition,
        'tasks':[{'key':'design','name':'结构设计','owner_user_id':ids['admin'],
            'planned_start':'2026-09-01','planned_end':'2026-09-05','prerequisites':[]},
            {'key':'machining','name':'加工','owner_user_id':ids['admin'],
            'planned_start':'2026-09-08','planned_end':'2026-09-23','prerequisites':['design']},
            {'key':'trial','name':'试模','owner_user_id':ids['admin'],
            'planned_start':'2026-09-24','planned_end':'2026-09-26','prerequisites':['machining']}]}
    response=client.post(f"/internal/runs/{ctx['id']}/tools",headers=worker_headers(),json={
        'epoch':ctx['epoch'],'sequence':0,'key':'prepare_project_plan_change','arguments':args})
    assert response.status_code==200,response.text
    evidence=response.json()
    assert evidence['proposal']['kind']=='project_plan_change'
    assert evidence['proposal']['confirmation_policy']['status']=='CONFIRM_THEN_DELEGATED_APPROVAL_ALLOWED'
    assert '2026-09-06~2026-09-20 → 2026-09-08~2026-09-23' in str(evidence['proposal']['display'])
    with factory() as db:
        assert not list(db.scalars(select(m.BusinessSubject).where(m.BusinessSubject.kind=='plan_change')))
    intent_response=client.post('/api/project-plan-proposals/'+evidence['evidence_id']+'/intent')
    assert intent_response.status_code==200,intent_response.text
    intent=intent_response.json()
    assert intent['confirmation_policy']['status']=='CONFIRM_THEN_DELEGATED_APPROVAL_ALLOWED'
    captured={}
    original=business.submit_subject
    def capture_mode(db,user,subject_id,revision,definition_id,material_review_id=None,agent_permission_mode="ask"):
        captured['mode']=agent_permission_mode
        return original(db,user,subject_id,revision,definition_id,material_review_id,agent_permission_mode)
    monkeypatch.setattr(business,'submit_subject',capture_mode)
    confirm=client.post('/api/human-actions/'+intent['id']+'/confirm',json={'challenge':intent['challenge']})
    assert confirm.status_code==200,confirm.text
    receipt=confirm.json()
    assert receipt['status']=='SUBMITTED'
    assert captured['mode']=='delegated_auto'
    assert client.get('/api/project-plan-proposals/'+evidence['evidence_id']).json()['receipt']==receipt
    with factory() as db:
        change=db.scalar(select(m.BusinessSubject).where(m.BusinessSubject.kind=='plan_change'))
        assert change and change.status=='SUBMITTED'
        detail=db.get(m.PlanDetail,change.id)
        assert detail.previous_id==baseline_id
        assert db.scalar(select(m.ApprovalInstance).where(m.ApprovalInstance.subject_id==change.id))


def test_plan_change_proposal_sqlite_confirm_chain(monkeypatch):
    engine,Session=factory()
    try:
        with Session.begin() as db:
            admin=user(db,'admin',True);p=project(db,'PLAN-CHANGE')
            config={'business_type':'plan_change','nodes':[{'key':'review','name':'计划核对','mode':'ALL','users':[admin.id],'reject_rules':[]}]}
            definition=m.WorkflowDefinition(process_key='plan_change_sqlite',version=1,name='计划变更审批',
                status='PUBLISHED',config=config,bpmn_xml=bpm.compile_bpmn(config),package_hash='test')
            db.add(definition)
            baseline=plan(db,p,admin,'PLAN-SQLITE')
            task(db,baseline,admin,'design','结构设计',date(2026,9,1),date(2026,9,5),'DONE')
            task(db,baseline,admin,'machining','加工',date(2026,9,6),date(2026,9,20),'PLANNED')
            conversation=m.Conversation(user_id=admin.id,title='计划变更')
            db.add(conversation);db.flush()
            run=m.Run(conversation_id=conversation.id,user_id=admin.id,security_version=admin.security_version,
                prompt='准备计划变更',status='SUCCEEDED',
                checkpoint={'authorization_hash':fingerprint(db,admin),'agent_permission_mode':'delegated_auto'})
            db.add(run);db.flush()
            args={'project_id':p.id,'project_version':p.row_version,'previous_id':baseline.id,
                'reason':'客户确认加工顺延','workflow_definition_id':definition.id,
                'tasks':[{'key':'design','name':'结构设计','owner_user_id':admin.id,
                    'planned_start':'2026-09-01','planned_end':'2026-09-05','prerequisites':[]},
                    {'key':'machining','name':'加工','owner_user_id':admin.id,
                    'planned_start':'2026-09-08','planned_end':'2026-09-23','prerequisites':['design']}]}
            evidence=execute(db,admin,'prepare_project_plan_change',args,run=run)
            assert evidence['proposal']['confirmation_policy']['status']=='CONFIRM_THEN_DELEGATED_APPROVAL_ALLOWED'
            step=m.Step(run_id=run.id,sequence=0,tool='prepare_project_plan_change',request_hash='hash',result=evidence)
            db.add(step);db.flush()
            payload={'step_id':step.id,'proposal_hash':bpm.content_hash(evidence['proposal'])}
            intent=business.create_intent(db,admin,'project_plan.execute',step.id,payload)
            assert not list(db.scalars(select(m.BusinessSubject).where(m.BusinessSubject.kind=='plan_change')))
            captured={}
            original=business.submit_subject
            def capture_mode(db,user,subject_id,revision,definition_id,material_review_id=None,agent_permission_mode="ask"):
                captured['mode']=agent_permission_mode
                return original(db,user,subject_id,revision,definition_id,material_review_id,agent_permission_mode)
            monkeypatch.setattr(business,'submit_subject',capture_mode)
            receipt=business.confirm_intent(db,admin,intent['id'],intent['challenge'])
            assert receipt['status']=='SUBMITTED'
            assert captured['mode']=='delegated_auto'
            change=db.scalar(select(m.BusinessSubject).where(m.BusinessSubject.kind=='plan_change'))
            assert change and change.status=='SUBMITTED'
            assert db.get(m.PlanDetail,change.id).previous_id==baseline.id
    finally:
        engine.dispose()
