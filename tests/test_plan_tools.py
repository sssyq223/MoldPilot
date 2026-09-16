from datetime import date, timedelta
from types import SimpleNamespace

import pytest
from sqlalchemy import select

from app import bpm, business, domains, domain_schemas as s, erp_adapter, erp_progress, models as m, plan_confirmations
from app.authorization import PERMISSIONS, fingerprint
from app.db import now
from app.errors import DomainError
from pg_db import factory as pg_factory
from app.tool_gateway import execute, tool_schema
from conftest import sign_in
from test_agent_api import start, worker_headers
from test_domains import workflow as create_workflow


def factory():
    return pg_factory()


def user(db,username='operator',super_admin=False,department=''):
    row=m.User(username=username,display_name=username,password_hash='test',
        super_admin=super_admin,department=department)
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


class FakeERPProgressClient:
    calls=[]

    def __init__(self, token):
        self.token=token

    def close(self):
        pass

    def plan_execution_progress(self, mold_no=None, project_no=None):
        self.calls.append({'token':self.token,'mold_no':mold_no,'project_no':project_no})
        return erp_adapter.normalize_plan_progress(
            {'rows':[{'id':'NODE-1','nodeName':'加工节点','moldNo':mold_no,'statusLabel':'执行中',
                      'plannedStart':'2026-09-01','plannedEnd':'2026-09-10','secretField':'hidden'}]},
            {'rows':[{'workOrderNo':'WO-1','procedureName':'CNC','moldNo':mold_no,
                      'actualStartTime':'2026-09-02T08:00:00','progress':50,'internalCost':'hidden'}]},
            '2026-09-16T10:00:00+08:00')


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
            visualization=analysis['visualization']
            assert visualization['kind']=='project_plan_visualization_v1'
            assert [row['key'] for row in visualization['timeline']]==['design','purchase','assembly','delivery']
            purchase_row=next(row for row in visualization['timeline'] if row['key']=='purchase')
            assembly_row=next(row for row in visualization['timeline'] if row['key']=='assembly')
            delivery_row=next(row for row in visualization['timeline'] if row['key']=='delivery')
            assert purchase_row['lane']=='running'
            assert 'OVERDUE' in purchase_row['risk_flags']
            assert 'BLOCKED_BY_PREREQUISITE' in assembly_row['risk_flags']
            assert assembly_row['waiting_for']==['purchase']
            assert 'CUSTOMER_DUE_RISK' in delivery_row['risk_flags']
            assert [row['key'] for row in visualization['kanban']['columns']['done']]==['design']
            assert [row['key'] for row in visualization['kanban']['columns']['running']]==['purchase']
            assert {row['key'] for row in visualization['kanban']['risk_lanes']['blocked']}=={'assembly','delivery'}
            erp=result['data'][0]['erp_execution_progress']
            assert erp['status']=='NOT_CONFIGURED'
            assert erp['records'] is None
            assert visualization['external_progress']=={'source':'ERP','status':'NOT_CONFIGURED','available':False}
            assert 'ERP 服务地址未配置' in ''.join(result['limitations'])
    finally:
        engine.dispose()


def test_plan_context_reads_erp_progress_as_reference_without_mirroring(monkeypatch):
    engine,Session=factory()
    FakeERPProgressClient.calls=[]
    try:
        with Session.begin() as db:
            admin=user(db,'admin',True);p=project(db,'PLAN-ERP')
            mold=m.Mold(internal_number='MOLD-ERP-01',name='ERP进度模具')
            db.add(mold);db.flush()
            db.add(m.ProjectMold(project_id=p.id,mold_id=mold.id))
            base=plan(db,p,admin,'PLAN-ERP-BASE')
            task(db,base,admin,'machining','加工',date(2026,9,1),date(2026,9,10),'RUNNING')
            db.add(m.ERPIdentity(user_id=admin.id,erp_user_id='ERP-ADMIN',token_ciphertext='cipher'))
        monkeypatch.setattr(erp_progress,'settings',lambda:SimpleNamespace(erp_base_url='https://erp.example.test'))
        monkeypatch.setattr(erp_progress,'decrypt',lambda value:'token-123')
        monkeypatch.setattr(erp_progress,'ERPClient',FakeERPProgressClient)
        with Session() as db:
            admin=db.query(m.User).filter_by(username='admin').one()
            result=execute(db,admin,'query_project_plan_context',{'identifier':'PLAN-ERP'})
            erp=result['data'][0]['erp_execution_progress']
            assert erp['status']=='RESOLVED'
            assert FakeERPProgressClient.calls==[{'token':'token-123','mold_no':'MOLD-ERP-01','project_no':'PLAN-ERP'}]
            row=erp['records'][0]['project_nodes'][0]
            schedule=erp['records'][0]['production_schedules'][0]
            assert row['source_ref']=='system/projectNode/list:NODE-1'
            assert schedule['source_ref']=='system/productionSchedule/list:WO-1'
            visualization=result['data'][0]['analysis']['visualization']
            assert visualization['external_progress']=={'source':'ERP','status':'RESOLVED','available':True}
            assert 'secretField' not in str(erp)
            assert 'internalCost' not in str(erp)
            assert not list(db.scalars(select(m.PlanTask).where(m.PlanTask.plan_id==base.id,m.PlanTask.actual_end.is_not(None))))
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
            assert row['workflow_options'][0]['material_required'] is False
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
            admin=user(db,'admin',True,department='项目部')
            old_owner=user(db,'old_owner',department='加工部')
            new_owner=user(db,'new_owner',department='试模部')
            old_head=user(db,'old_head',department='加工部')
            new_head=user(db,'new_head',department='试模部')
            machining_group=m.AssignmentGroup(kind='DEPARTMENT',name='加工部')
            trial_group=m.AssignmentGroup(kind='DEPARTMENT',name='试模部')
            db.add_all([machining_group,trial_group]);db.flush()
            db.add_all([m.AssignmentMember(group_id=machining_group.id,user_id=old_head.id,is_head=True),
                m.AssignmentMember(group_id=trial_group.id,user_id=new_head.id,is_head=True)])
            p=project(db,'PLAN-NOTIFY')
            for permission in ('project.read','project_plan.read','plan_change.read','plan_change.execute'):
                grant(db,admin,new_head,permission,p.id)
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
            assert {row['department'] for row in audit.detail['affected_departments']}=={'加工部','试模部'}
            machining_departments=[row for row in audit.detail['affected_departments'] if 'machining' in row['task_keys']]
            assert {row['department'] for row in machining_departments}=={'加工部','试模部'}
            trial=[row for row in audit.detail['affected_departments'] if row['department']=='试模部'][0]
            assert set(trial['task_keys'])=={'machining','trial'}
            confirmations=list(db.scalars(select(m.PlanDepartmentConfirmation).where(
                m.PlanDepartmentConfirmation.plan_change_id==change.id).order_by(m.PlanDepartmentConfirmation.department)))
            assert [row.department for row in confirmations]==['加工部','试模部']
            assert all(row.status=='PENDING' for row in confirmations)
            assert confirmations[0].assigned_user_ids==[old_head.id]
            assert confirmations[1].assigned_user_ids==[new_head.id]
            pending=list(db.scalars(select(m.Outbox).where(
                m.Outbox.kind=='plan.department_confirmation.pending',m.Outbox.resource_id==change.id)))
            assert {tuple(row.payload['recipients']) for row in pending}=={(old_head.id,),(new_head.id,)}
            confirmed=plan_confirmations.confirm(db,new_head,confirmations[1].id,confirmations[1].version,'试模部已核对顺延影响')
            assert confirmed['status']=='CONFIRMED'
            assert confirmed['confirmed_by']['id']==new_head.id
            context=execute(db,admin,'query_project_plan_context',{'identifier':'PLAN-NOTIFY'})
            rows=context['data'][0]['department_confirmations']
            assert any(row['department']=='试模部' and row['status']=='CONFIRMED' for row in rows)
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
    assert '受影响部门' in evidence['proposal']['display']
    assert 'machining' in str(evidence['proposal']['display']['受影响部门'])
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


def test_plan_change_proposal_accepts_confirmed_material_review():
    engine,Session=factory()
    contract={'fields':[{'key':'signed_change','label':'客户确认设变','type':'boolean'}],'tables':[]}
    try:
        with Session.begin() as db:
            admin=user(db,'admin',True);p=project(db,'PLAN-MATERIAL')
            template=m.MaterialTemplate(template_key='plan_change_material',version=1,name='计划变更依据',
                status='PUBLISHED',contract=contract,package_hash=bpm.content_hash(
                    {'template_key':'plan_change_material','version':1,'contract':contract}))
            db.add(template);db.flush()
            conversation=m.Conversation(user_id=admin.id,title='计划变更附件')
            db.add(conversation);db.flush()
            file=m.FileObject(owner_id=admin.id,conversation_id=conversation.id,request_key='file-review',
                filename='客户确认单.pdf',media_type='application/pdf',size=32,sha256='2'*64,
                backend='local',storage_namespace='test',object_key='plan/material/'+'2'*64,storage_version=None)
            db.add(file);db.flush()
            material_data={'fields':{'signed_change':True},'tables':{}}
            review=m.MaterialReview(template_id=template.id,mapping_id=None,file_id=file.id,owner_id=admin.id,
                status='CONFIRMED',material_data=material_data,issues=[],template_hash=template.package_hash,
                mapping_hash='1'*64,file_sha256=file.sha256,review_hash=bpm.content_hash(material_data),
                confirmed_by=admin.id,confirmed_at=now())
            config={'business_type':'plan_change','material_contract':contract,
                'nodes':[{'key':'review','name':'计划变更依据核对','mode':'ALL','users':[admin.id],'reject_rules':[]}]}
            definition=m.WorkflowDefinition(process_key='plan_change_material',version=1,name='计划变更带附件审批',
                material_template_id=template.id,status='PUBLISHED',config=config,bpmn_xml=bpm.compile_bpmn(config),
                package_hash=bpm.content_hash({'config':config}))
            db.add_all([review,definition])
            baseline=plan(db,p,admin,'PLAN-MATERIAL-BASE')
            task(db,baseline,admin,'design','结构设计',date(2026,9,1),date(2026,9,5),'DONE')
            task(db,baseline,admin,'machining','加工',date(2026,9,6),date(2026,9,20),'PLANNED')
            run=m.Run(conversation_id=conversation.id,user_id=admin.id,security_version=admin.security_version,
                prompt='准备带附件的计划变更',status='SUCCEEDED',
                checkpoint={'authorization_hash':fingerprint(db,admin),'agent_permission_mode':'ask'})
            db.add(run);db.flush()
            args={'project_id':p.id,'project_version':p.row_version,'previous_id':baseline.id,
                'reason':'客户签字确认加工顺延','workflow_definition_id':definition.id,
                'tasks':[{'key':'design','name':'结构设计','owner_user_id':admin.id,
                    'planned_start':'2026-09-01','planned_end':'2026-09-05','prerequisites':[]},
                    {'key':'machining','name':'加工','owner_user_id':admin.id,
                    'planned_start':'2026-09-08','planned_end':'2026-09-23','prerequisites':['design']}]}
            with pytest.raises(DomainError) as missing:
                execute(db,admin,'prepare_project_plan_change',args,run=run)
            assert missing.value.code=='MATERIALS_NOT_BOUND'
            evidence=execute(db,admin,'prepare_project_plan_change',{**args,'material_review_id':review.id},run=run)
            assert evidence['proposal']['display']['资料核对包'].startswith('已确认')
            step=m.Step(run_id=run.id,sequence=0,tool='prepare_project_plan_change',request_hash='hash',result=evidence)
            db.add(step);db.flush()
            payload={'step_id':step.id,'proposal_hash':bpm.content_hash(evidence['proposal'])}
            intent=business.create_intent(db,admin,'project_plan.execute',step.id,payload)
            receipt=business.confirm_intent(db,admin,intent['id'],intent['challenge'])
            assert receipt['status']=='SUBMITTED'
            change=db.scalar(select(m.BusinessSubject).where(m.BusinessSubject.kind=='plan_change'))
            instance=db.scalar(select(m.ApprovalInstance).where(m.ApprovalInstance.subject_id==change.id))
            binding=db.scalar(select(m.MaterialBinding).where(m.MaterialBinding.review_id==review.id))
            assert binding.resource_type=='business_subject' and binding.resource_id==change.id
            assert instance.snapshot['material_data']['fields']['signed_change'] is True
            assert instance.snapshot['material_binding']['review_hash']==review.review_hash
    finally:
        engine.dispose()


def test_plan_change_proposal_postgres_confirm_chain(monkeypatch):
    engine,Session=factory()
    try:
        with Session.begin() as db:
            admin=user(db,'admin',True);p=project(db,'PLAN-CHANGE')
            config={'business_type':'plan_change','nodes':[{'key':'review','name':'计划核对','mode':'ALL','users':[admin.id],'reject_rules':[]}]}
            definition=m.WorkflowDefinition(process_key='plan_change_postgres',version=1,name='计划变更审批',
                status='PUBLISHED',config=config,bpmn_xml=bpm.compile_bpmn(config),package_hash='test')
            db.add(definition)
            baseline=plan(db,p,admin,'PLAN-POSTGRES')
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


def test_department_confirmation_proposal_requires_human_confirmation():
    engine,Session=factory()
    try:
        with Session.begin() as db:
            admin=user(db,'admin',True,department='项目部')
            head=user(db,'trial_head',department='试模部')
            group=m.AssignmentGroup(kind='DEPARTMENT',name='试模部')
            db.add(group);db.flush()
            db.add(m.AssignmentMember(group_id=group.id,user_id=head.id,is_head=True))
            p=project(db,'PLAN-DEPT-CONFIRM')
            change=plan(db,p,admin,'PLAN-CHANGE-CONFIRM',kind='plan_change',status='EFFECTIVE')
            confirmation=m.PlanDepartmentConfirmation(plan_change_id=change.id,project_id=p.id,
                department='试模部',assigned_user_ids=[head.id],task_keys=['trial'],
                change_types=['added'])
            db.add(confirmation)
            conversation=m.Conversation(user_id=head.id,title='计划影响确认')
            db.add(conversation);db.flush()
            run=m.Run(conversation_id=conversation.id,user_id=head.id,security_version=head.security_version,
                prompt='试模部已核对计划影响',status='SUCCEEDED',
                checkpoint={'authorization_hash':fingerprint(db,head),'agent_permission_mode':'ask'})
            db.add(run)
            for permission in ('plan_change.read','plan_change.execute'):
                grant(db,admin,head,permission,p.id)
            capability(db,head,'prepare_plan_department_confirmation')
            run.checkpoint={'authorization_hash':fingerprint(db,head),'agent_permission_mode':'ask'}
            confirmation_id=confirmation.id;version=confirmation.version
        schema=tool_schema('prepare_plan_department_confirmation')
        assert 'confirmation_id' in schema['function']['parameters']['properties']
        with Session.begin() as db:
            head=db.query(m.User).filter_by(username='trial_head').one()
            run=db.scalar(select(m.Run).where(m.Run.user_id==head.id))
            evidence=execute(db,head,'prepare_plan_department_confirmation',{
                'confirmation_id':confirmation_id,
                'expected_version':version,
                'note':'试模部已核对新增试模节点资源影响',
            },run=run)
            assert evidence['proposal']['kind']=='plan_department_confirmation'
            assert evidence['proposal']['requires_approval'] is False
            assert evidence['proposal']['display']['确认部门']=='试模部'
            assert db.get(m.PlanDepartmentConfirmation,confirmation_id).status=='PENDING'
            step=m.Step(run_id=run.id,sequence=0,tool='prepare_plan_department_confirmation',
                request_hash='hash',result=evidence)
            db.add(step);db.flush()
            payload={'step_id':step.id,'proposal_hash':bpm.content_hash(evidence['proposal'])}
            intent=business.create_intent(db,head,'project_plan.execute',step.id,payload)
            receipt=business.confirm_intent(db,head,intent['id'],intent['challenge'])
            row=db.get(m.PlanDepartmentConfirmation,confirmation_id)
            assert receipt['status']=='CONFIRMED'
            assert receipt['action']=='department_confirmation'
            assert row.status=='CONFIRMED'
            assert row.confirmed_by==head.id
            assert row.note=='试模部已核对新增试模节点资源影响'
    finally:
        engine.dispose()


def test_department_confirmation_proposal_detects_version_conflict_on_confirm():
    engine,Session=factory()
    try:
        with Session.begin() as db:
            admin=user(db,'admin',True,department='项目部')
            head=user(db,'trial_head',department='试模部')
            p=project(db,'PLAN-DEPT-CONFLICT')
            change=plan(db,p,admin,'PLAN-CHANGE-CONFLICT',kind='plan_change',status='EFFECTIVE')
            confirmation=m.PlanDepartmentConfirmation(plan_change_id=change.id,project_id=p.id,
                department='试模部',assigned_user_ids=[head.id],task_keys=['trial'],
                change_types=['added'])
            db.add(confirmation)
            conversation=m.Conversation(user_id=head.id,title='计划影响确认')
            db.add(conversation);db.flush()
            run=m.Run(conversation_id=conversation.id,user_id=head.id,security_version=head.security_version,
                prompt='试模部已核对计划影响',status='SUCCEEDED',
                checkpoint={'authorization_hash':fingerprint(db,head),'agent_permission_mode':'ask'})
            db.add(run)
            for permission in ('plan_change.read','plan_change.execute'):
                grant(db,admin,head,permission,p.id)
            capability(db,head,'prepare_plan_department_confirmation')
            run.checkpoint={'authorization_hash':fingerprint(db,head),'agent_permission_mode':'ask'}
            evidence=execute(db,head,'prepare_plan_department_confirmation',{
                'confirmation_id':confirmation.id,
                'expected_version':confirmation.version,
                'note':'试模部已核对新增试模节点资源影响',
            },run=run)
            step=m.Step(run_id=run.id,sequence=0,tool='prepare_plan_department_confirmation',
                request_hash='hash',result=evidence)
            db.add(step);db.flush()
            payload={'step_id':step.id,'proposal_hash':bpm.content_hash(evidence['proposal'])}
            intent=business.create_intent(db,head,'project_plan.execute',step.id,payload)
            confirmation.version += 1
            with pytest.raises(DomainError) as conflict:
                business.confirm_intent(db,head,intent['id'],intent['challenge'])
            assert conflict.value.code=='VERSION_CONFLICT'
            assert confirmation.status=='PENDING'
    finally:
        engine.dispose()

