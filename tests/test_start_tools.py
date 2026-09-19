from datetime import date
from decimal import Decimal

import pytest
from sqlalchemy import select

from app import bpm, business, models as m
from app.message_worker import deliver
from app.authorization import PERMISSIONS, fingerprint
from domain_packs.mold.erp.core import domains
from pg_db import factory as pg_factory
from app.tool_gateway import execute, tool_schema


def factory():
    return pg_factory()


def user(db,username='operator',super_admin=False):
    row=m.User(username=username,display_name=username,password_hash='test',super_admin=super_admin)
    db.add(row);db.flush();return row


def project(db,code,name='开工项目',status='DRAFT'):
    row=m.Project(code=code,name=name,status=status)
    db.add(row);db.flush();return row


def grant(db,admin,target,permission,project_id,fields=None):
    db.add(m.Grant(user_id=target.id,permission=permission,effect='ALLOW',scope={'project_id':[project_id]},
        fields=fields or PERMISSIONS[permission],reason='unit test',granted_by=admin.id))


def capability(db,target,key,kind='TOOL'):
    db.add(m.Capability(user_id=target.id,kind=kind,key=key,enabled=True))


def decision(db,project,user,kind,number,decision_value,status='EFFECTIVE',source_subject_id=None,mode='INTERNAL'):
    subject=m.BusinessSubject(kind=kind,number=number,project_id=project.id,created_by=user.id,status=status)
    db.add(subject);db.flush()
    db.add(m.BusinessDecisionDetail(subject_id=subject.id,source_subject_id=source_subject_id,
        decision=decision_value,execution_mode=mode,effective_date=date.today(),evidence='人工核对依据',
        amount=Decimal('100.00'),currency='CNY'))
    return subject


def sales_contract(db,project,user,number='SC-START'):
    subject=m.BusinessSubject(kind='sales_contract',number='SUBJECT-'+number,project_id=project.id,
        created_by=user.id,status='EFFECTIVE')
    db.add(subject);db.flush()
    db.add(m.ContractDetail(subject_id=subject.id,customer_id=None,supplier_id=None,amount=Decimal('100.00'),
        currency='CNY',contract_number=number,expected_date=date.today()))
    return subject


def workflow(db,user):
    config={'business_type':'internal_start',
        'nodes':[{'key':'review','name':'项目负责人确认开工','mode':'ALL','users':[user.id],'reject_rules':[]}]}
    row=m.WorkflowDefinition(process_key='internal_start_test',version=1,name='正式开工审批',
        status='PUBLISHED',config=config,bpmn_xml=bpm.compile_bpmn(config),package_hash='test')
    db.add(row);db.flush();return row


def customer_start_conditions(db,project,user,suffix='001'):
    conversation=m.Conversation(user_id=user.id,title='客户开工条件 '+suffix)
    db.add(conversation);db.flush()
    case=m.BidIntakeCase(project_id=project.id,created_by=user.id)
    db.add(case);db.flush()
    revision=m.BidIntakeRevision(
        case_id=case.id,version=1,previous_revision_id=None,source_kind='EMAIL',
        source_ref='START-MAIL-'+suffix,source_fingerprint=('e'*60+suffix)[-64:],
        received_date=date.today(),customer_classification='OTHER',
        classification_evidence='业务人员已人工确认客户分类',classification_confirmed_by=user.id,
        customer_company='测试客户',customer_contact='客户项目经理',customer_mold_number=None,
        customer_model_or_material=None,project_name_snapshot=project.name,amount=None,currency=None,
        our_recipient='项目负责人',external_order_number='EXT-ORDER-'+suffix,
        external_start_date=date.today(),customer_due_date=date.today(),
        customer_process_confirmed=True,
        customer_process_confirmation_evidence='客户工艺方案已经双方人工确认',
        matched_quotation_subject_id=None,historical_mold_number=None,historical_relation_kind=None,
        match_result='UNMATCHED',match_evidence='本次不引用历史报价或模具',notes='',recorded_by=user.id,
    )
    db.add(revision);db.flush()
    blob=m.FileObject(
        owner_id=user.id,conversation_id=conversation.id,request_key='start-condition-'+suffix,
        filename='customer-start-'+suffix+'.pdf',media_type='application/pdf',size=256,
        sha256=('d'*60+suffix)[-64:],backend='local',storage_namespace='test',
        object_key='test/customer-start-'+suffix+'.pdf',storage_version=None,
    )
    db.add(blob);db.flush()
    db.add(m.BidIntakeAttachment(
        revision_id=revision.id,file_id=blob.id,role='EXTERNAL_START_NOTICE',
        content_sha256=blob.sha256,title=blob.filename,
    ))
    return revision


def test_start_readiness_schema_and_can_prepare_from_known_facts():
    engine,Session=factory()
    try:
        with Session.begin() as db:
            admin=user(db,'admin',True);p=project(db,'START-M001','正式开工核对项目')
            decision(db,p,admin,'quote_acceptance','QA-START','ACCEPT')
            sales_contract(db,p,admin)
            customer_start_conditions(db,p,admin,'101')
        schema=tool_schema('query_internal_start_readiness')['function']['parameters']
        assert {'project_id','identifier'} <= set(schema['properties'])
        with Session() as db:
            admin=db.query(m.User).filter_by(username='admin').one()
            result=execute(db,admin,'query_internal_start_readiness',{'identifier':'START-M001'})
            assert result['resolution']=='RESOLVED'
            row=result['data'][0]
            assert row['readiness']['has_effective_acceptance'] is True
            assert row['readiness']['has_effective_internal_start'] is False
            assert row['readiness']['can_prepare_start_from_known_facts'] is True
            assert row['latest_acceptance']['number']=='QA-START'
            assert row['sales_contracts'][0]['detail']['contract_number']=='SC-START'
    finally:
        engine.dispose()


def test_start_readiness_blocks_acceptance_without_customer_start_conditions():
    engine,Session=factory()
    try:
        with Session.begin() as db:
            admin=user(db,'admin',True);p=project(db,'START-MISSING','开工条件缺失项目')
            decision(db,p,admin,'quote_acceptance','QA-MISSING','ACCEPT')
        with Session() as db:
            admin=db.query(m.User).filter_by(username='admin').one()
            result=execute(db,admin,'query_internal_start_readiness',{'identifier':'START-MISSING'})
            row=result['data'][0]
            assert row['readiness']['has_effective_acceptance'] is True
            assert row['readiness']['can_prepare_start_from_known_facts'] is False
            assert row['customer_start_conditions']['complete'] is False
            assert '中标接收记录' in ''.join(row['readiness']['known_blockers'])
    finally:
        engine.dispose()


def test_prepare_internal_start_requires_human_confirmation_then_submits_bpm():
    engine,Session=factory()
    try:
        with Session.begin() as db:
            admin=user(db,'admin',True);p=project(db,'START-PREPARE','正式开工办理项目')
            accept=decision(db,p,admin,'quote_acceptance','QA-START-PREPARE','ACCEPT',mode='FULL_OUTSOURCE')
            sales_contract(db,p,admin)
            intake=customer_start_conditions(db,p,admin,'102')
            definition=workflow(db,admin)
            conversation=m.Conversation(user_id=admin.id,title='正式开工')
            db.add(conversation);db.flush()
            run=m.Run(conversation_id=conversation.id,user_id=admin.id,security_version=admin.security_version,
                prompt='准备正式开工',status='SUCCEEDED',
                checkpoint={'authorization_hash':fingerprint(db,admin),'agent_permission_mode':'delegated_auto'})
            db.add(run);db.flush()
            args={'project_id':p.id,'project_version':p.row_version,'source_subject_id':accept.id,
                'bid_intake_revision_id':intake.id,
                'effective_date':date.today().isoformat(),'evidence':'客户开工通知与工艺方案已确认',
                'workflow_definition_id':definition.id}
        schema=tool_schema('prepare_internal_start')['function']['parameters']
        assert {'project_id','project_version','source_subject_id','bid_intake_revision_id','workflow_definition_id'} <= set(schema['properties'])
        with Session.begin() as db:
            admin=db.query(m.User).filter_by(username='admin').one()
            run=db.scalar(select(m.Run).where(m.Run.user_id==admin.id))
            evidence=execute(db,admin,'prepare_internal_start',args,run=run)
            assert evidence['proposal']['kind']=='internal_start'
            assert evidence['proposal']['requires_approval'] is True
            assert evidence['proposal']['display']['最终加工方式']=='FULL_OUTSOURCE'
            assert db.scalar(select(m.BusinessSubject).where(m.BusinessSubject.kind=='internal_start')) is None
            step=m.Step(run_id=run.id,sequence=0,tool='prepare_internal_start',request_hash='hash',result=evidence)
            db.add(step);db.flush()
            payload={'step_id':step.id,'proposal_hash':bpm.content_hash(evidence['proposal'])}
            intent=business.create_intent(db,admin,'internal_start.execute',step.id,payload)
            receipt=business.confirm_intent(db,admin,intent['id'],intent['challenge'])
            assert receipt['status']=='SUBMITTED'
            assert receipt['action']=='start'
            start=db.scalar(select(m.BusinessSubject).where(m.BusinessSubject.kind=='internal_start'))
            assert start and start.status=='SUBMITTED'
            detail=db.get(m.BusinessDecisionDetail,start.id)
            assert detail.source_subject_id==args['source_subject_id']
            assert detail.decision=='START'
            assert detail.execution_mode=='FULL_OUTSOURCE'
            intake_link=db.scalar(select(m.BidIntakeLifecycleLink).where(
                m.BidIntakeLifecycleLink.subject_id==start.id
            ))
            assert intake_link.source_revision_id==args['bid_intake_revision_id']
            assert db.get(m.Project,args['project_id']).status=='DRAFT'
            assert db.scalar(select(m.ApprovalInstance).where(
                m.ApprovalInstance.resource_type=='business_subject',
                m.ApprovalInstance.resource_id==start.id,
            ))
    finally:
        engine.dispose()


def test_prepare_internal_start_rejects_stale_project_version():
    engine,Session=factory()
    try:
        with Session.begin() as db:
            admin=user(db,'admin',True);p=project(db,'START-STALE')
            accept=decision(db,p,admin,'quote_acceptance','QA-STALE','ACCEPT')
            intake=customer_start_conditions(db,p,admin,'103')
            definition=workflow(db,admin)
            conversation=m.Conversation(user_id=admin.id,title='正式开工')
            db.add(conversation);db.flush()
            run=m.Run(conversation_id=conversation.id,user_id=admin.id,security_version=admin.security_version,
                prompt='准备正式开工',status='SUCCEEDED',
                checkpoint={'authorization_hash':fingerprint(db,admin),'agent_permission_mode':'ask'})
            db.add(run);db.flush()
            args={'project_id':p.id,'project_version':p.row_version,'source_subject_id':accept.id,
                'bid_intake_revision_id':intake.id,
                'effective_date':date.today().isoformat(),'evidence':'客户开工通知已确认',
                'workflow_definition_id':definition.id}
            p.row_version += 1
            with pytest.raises(Exception) as conflict:
                execute(db,admin,'prepare_internal_start',args,run=run)
            assert getattr(conflict.value,'code',None)=='VERSION_CONFLICT'
    finally:
        engine.dispose()


def test_prepare_internal_start_does_not_leak_contract_or_plan_without_permission():
    engine,Session=factory()
    try:
        with Session.begin() as db:
            admin=user(db,'admin',True);operator=user(db,'operator')
            p=project(db,'START-NO-LEAK')
            accept=decision(db,p,admin,'quote_acceptance','QA-NO-LEAK','ACCEPT')
            intake=customer_start_conditions(db,p,admin,'104')
            sales_contract(db,p,admin)
            plan_subject=m.BusinessSubject(kind='project_plan',number='PLAN-NO-LEAK',project_id=p.id,
                created_by=admin.id,status='EFFECTIVE')
            db.add(plan_subject);db.flush()
            db.add(m.PlanDetail(subject_id=plan_subject.id,reason='计划已准备'))
            definition=workflow(db,admin)
            conversation=m.Conversation(user_id=operator.id,title='正式开工')
            db.add(conversation);db.flush()
            run=m.Run(conversation_id=conversation.id,user_id=operator.id,security_version=operator.security_version,
                prompt='准备正式开工',status='SUCCEEDED',
                checkpoint={'authorization_hash':'pending','agent_permission_mode':'ask'})
            db.add(run)
            for permission in ('project.read','project.dossier.read','quote_acceptance.read','internal_start.read','internal_start.create','internal_start.submit'):
                grant(db,admin,operator,permission,p.id)
            capability(db,operator,'prepare_internal_start')
            run.checkpoint={'authorization_hash':fingerprint(db,operator),'agent_permission_mode':'ask'}
            args={'project_id':p.id,'project_version':p.row_version,'source_subject_id':accept.id,
                'bid_intake_revision_id':intake.id,
                'effective_date':date.today().isoformat(),'evidence':'客户开工通知已确认',
                'workflow_definition_id':definition.id}
        with Session.begin() as db:
            operator=db.query(m.User).filter_by(username='operator').one()
            run=db.scalar(select(m.Run).where(m.Run.user_id==operator.id))
            evidence=execute(db,operator,'prepare_internal_start',args,run=run)
            display=evidence['proposal']['display']
            assert display['销售合同']=='未授权查看'
            assert display['项目计划']=='未授权查看'
            assert '已见 1 条' not in str(display)
    finally:
        engine.dispose()


def test_start_readiness_reports_already_started_and_keeps_start_separate():
    engine,Session=factory()
    try:
        with Session.begin() as db:
            admin=user(db,'admin',True);p=project(db,'START-ACTIVE',status='ACTIVE')
            accept=decision(db,p,admin,'quote_acceptance','QA-ACTIVE','ACCEPT')
            decision(db,p,admin,'internal_start','START-001','START',source_subject_id=accept.id)
        with Session() as db:
            admin=db.query(m.User).filter_by(username='admin').one()
            result=execute(db,admin,'query_internal_start_readiness',{'identifier':'START-ACTIVE'})
            row=result['data'][0]
            assert row['readiness']['has_effective_acceptance'] is True
            assert row['readiness']['has_effective_internal_start'] is True
            assert row['readiness']['can_prepare_start_from_known_facts'] is False
            assert row['latest_internal_start']['number']=='START-001'
            assert '已有有效正式开工通知' in ''.join(row['readiness']['hints'])
    finally:
        engine.dispose()


def test_start_readiness_does_not_leak_acceptance_without_quote_tool():
    engine,Session=factory()
    try:
        with Session.begin() as db:
            admin=user(db,'admin',True);operator=user(db)
            p=project(db,'START-LIMITED')
            decision(db,p,admin,'quote_acceptance','SECRET-QA','ACCEPT')
            grant(db,admin,operator,'project.read',p.id);grant(db,admin,operator,'internal_start.read',p.id)
            capability(db,operator,'query_internal_start_readiness')
        with Session() as db:
            operator=db.query(m.User).filter_by(username='operator').one()
            result=execute(db,operator,'query_internal_start_readiness',{'identifier':'START-LIMITED'})
            row=result['data'][0]
            assert row['quote_acceptance']==[]
            assert row['readiness']['has_effective_acceptance'] is False
            assert row['readiness']['can_prepare_start_from_known_facts'] is False
            assert 'SECRET-QA' not in str(result)
            assert '承接依据' in ''.join(result['limitations'])
    finally:
        engine.dispose()


def test_start_readiness_hides_customer_start_evidence_without_dossier_permission():
    engine,Session=factory()
    try:
        with Session.begin() as db:
            admin=user(db,'admin',True);operator=user(db,'limited')
            p=project(db,'START-HIDDEN')
            decision(db,p,admin,'quote_acceptance','QA-HIDDEN','ACCEPT')
            customer_start_conditions(db,p,admin,'105')
            for permission in ('project.read','quote_acceptance.read','internal_start.read'):
                grant(db,admin,operator,permission,p.id)
            capability(db,operator,'query_internal_start_readiness')
            capability(db,operator,'query_quote_acceptance_context')
        with Session() as db:
            operator=db.query(m.User).filter_by(username='limited').one()
            result=execute(db,operator,'query_internal_start_readiness',{'identifier':'START-HIDDEN'})
            conditions=result['data'][0]['customer_start_conditions']
            assert conditions['visible'] is False
            assert conditions['current_revision_id'] is None
            assert conditions['external_order_number'] is None
            assert 'EXT-ORDER-105' not in str(result)
            assert '客户工艺方案已经双方人工确认' not in str(result)
    finally:
        engine.dispose()


def test_start_readiness_reports_multiple_candidates_without_deciding():
    engine,Session=factory()
    try:
        with Session.begin() as db:
            admin=user(db,'admin',True)
            p1=project(db,'START-A','共同开工项目A')
            p2=project(db,'START-B','共同开工项目B')
            decision(db,p1,admin,'quote_acceptance','QA-A','ACCEPT')
            decision(db,p2,admin,'quote_acceptance','QA-B','ACCEPT')
        with Session() as db:
            admin=db.query(m.User).filter_by(username='admin').one()
            result=execute(db,admin,'query_internal_start_readiness',{'identifier':'共同开工项目'})
            assert result['resolution']=='MULTIPLE_CANDIDATES'
            assert {row['code'] for row in result['data']}=={'START-A','START-B'}
            assert '请使用项目 ID' in ''.join(result['limitations'])
    finally:
        engine.dispose()


def test_effective_internal_start_creates_role_handoffs_and_delivers_notifications():
    engine,Session=factory()
    try:
        with Session.begin() as db:
            admin=user(db,'admin',True);p=project(db,'START-HANDOFF','正式开工部门交接')
            accept=decision(db,p,admin,'quote_acceptance','QA-HANDOFF','ACCEPT')
            customer_start_conditions(db,p,admin,'201')
            role_rows=[]
            for index,role_key in enumerate((
                'DESIGN_OWNER','PURCHASE_OWNER','MANUFACTURING_OWNER','ASSEMBLY_OWNER','FINANCE_OWNER'
            ),start=1):
                person=user(db,f'handoff-{index}',True)
                role_rows.append(m.ProjectRoleMember(
                    project_id=p.id,role_key=role_key,user_id=person.id
                ))
            db.add_all(role_rows)
            start=decision(
                db,p,admin,'internal_start','START-HANDOFF-001','START',
                status='APPROVED',source_subject_id=accept.id,
            )
            domains.apply(db,admin,start)
            db.flush()
            dispatches=list(db.scalars(select(m.InternalStartDispatch).where(
                m.InternalStartDispatch.start_subject_id==start.id
            )))
            event_ids=[row.event_id for row in dispatches if row.event_id]
            assert len(dispatches)==5
            assert len(event_ids)==5
            assert {row.dispatch_status for row in dispatches}=={'QUEUED'}
            assert db.get(m.Project,p.id).status=='ACTIVE'
            assert db.get(m.BusinessSubject,start.id).status=='EFFECTIVE'
            assert db.scalar(select(m.PlanTask).limit(1)) is None
            assert db.scalar(select(m.PurchaseRequest).limit(1)) is None
            assert set(db.scalars(select(m.BusinessSubject.kind)))=={
                'quote_acceptance','internal_start'
            }
        for event_id in event_ids:
            assert deliver(Session,event_id)=='DELIVERED'
        with Session() as db:
            admin=db.query(m.User).filter_by(username='admin').one()
            result=execute(db,admin,'query_internal_start_readiness',{'identifier':'START-HANDOFF'})
            row=result['data'][0]
            assert row['business_state']['key']=='FORMALLY_ISSUED'
            assert row['department_handoffs']['status']=='DELIVERED'
            assert row['department_handoffs']['required_count']==5
            assert row['department_handoffs']['assigned_count']==5
            assert row['department_handoffs']['delivered_count']==5
            assert all(item['delivery_state']=='DELIVERED' for item in row['department_handoffs']['items'])
            assert db.scalar(select(m.Notification).where(
                m.Notification.title=='项目已正式开工，请核对计划交接'
            ).limit(1))
    finally:
        engine.dispose()


def test_effective_internal_start_records_unassigned_handoff_gaps_without_fabricating_people():
    engine,Session=factory()
    try:
        with Session.begin() as db:
            admin=user(db,'admin',True);p=project(db,'START-GAPS','开工交接缺口')
            accept=decision(db,p,admin,'quote_acceptance','QA-GAPS','ACCEPT')
            designer=user(db,'designer',True)
            db.add(m.ProjectRoleMember(
                project_id=p.id,role_key='DESIGN_OWNER',user_id=designer.id
            ))
            start=decision(
                db,p,admin,'internal_start','START-GAPS-001','START',
                status='APPROVED',source_subject_id=accept.id,
            )
            domains.apply(db,admin,start)
            db.flush()
            dispatches=list(db.scalars(select(m.InternalStartDispatch).where(
                m.InternalStartDispatch.start_subject_id==start.id
            ).order_by(m.InternalStartDispatch.role_key)))
            assert len(dispatches)==5
            assert sum(row.dispatch_status=='QUEUED' for row in dispatches)==1
            assert sum(row.dispatch_status=='UNASSIGNED' for row in dispatches)==4
            assert all(row.recipient_snapshot==[] for row in dispatches if row.dispatch_status=='UNASSIGNED')
            assert all(row.event_id is None for row in dispatches if row.dispatch_status=='UNASSIGNED')
        with Session() as db:
            admin=db.query(m.User).filter_by(username='admin').one()
            result=execute(db,admin,'query_internal_start_readiness',{'identifier':'START-GAPS'})
            handoffs=result['data'][0]['department_handoffs']
            assert handoffs['status']=='RECIPIENT_CONFIGURATION_REQUIRED'
            assert handoffs['assigned_count']==1
            assert len(handoffs['gaps'])==4
            assert all(gap['reason']=='项目角色尚未配置有效人员' for gap in handoffs['gaps'])
    finally:
        engine.dispose()


def test_internal_start_business_state_follows_six_stage_sequence():
    engine,Session=factory()
    try:
        with Session.begin() as db:
            admin=user(db,'admin',True);p=project(db,'START-STATES','开工状态链')
            first=execute(db,admin,'query_internal_start_readiness',{'identifier':'START-STATES'})
            assert first['data'][0]['business_state']['key']=='AWAITING_ACCEPTANCE'

            accept=decision(db,p,admin,'quote_acceptance','QA-STATES','ACCEPT')
            second=execute(db,admin,'query_internal_start_readiness',{'identifier':'START-STATES'})
            assert second['data'][0]['business_state']['key']=='ACCEPTED_AWAITING_START_CONDITIONS'

            customer_start_conditions(db,p,admin,'202')
            third=execute(db,admin,'query_internal_start_readiness',{'identifier':'START-STATES'})
            assert third['data'][0]['business_state']['key']=='AWAITING_FORMAL_ISSUE'

            start=decision(db,p,admin,'internal_start','START-STATES-001','START',source_subject_id=accept.id)
            p.status='ACTIVE';p.row_version+=1
            fourth=execute(db,admin,'query_internal_start_readiness',{'identifier':'START-STATES'})
            assert fourth['data'][0]['business_state']['key']=='FORMALLY_ISSUED'

            plan=m.BusinessSubject(kind='project_plan',number='PLAN-STATES-001',project_id=p.id,
                created_by=admin.id,status='DRAFT')
            db.add(plan);db.flush();db.add(m.PlanDetail(subject_id=plan.id,reason='基线计划待审批'))
            fifth=execute(db,admin,'query_internal_start_readiness',{'identifier':'START-STATES'})
            assert fifth['data'][0]['business_state']['key']=='AWAITING_PLAN_APPROVAL'

            plan.status='EFFECTIVE';db.flush()
            sixth=execute(db,admin,'query_internal_start_readiness',{'identifier':'START-STATES'})
            state=sixth['data'][0]['business_state']
            assert state['key']=='EXECUTING'
            assert [item['name'] for item in state['sequence']]==[
                '待承接确认','已承接待开工条件','待正式下达','已正式下达','待计划审批','执行中'
            ]
            assert all(item['status']=='DONE' for item in state['sequence'][:-1])
            assert state['sequence'][-1]['status']=='CURRENT'
            assert start.id==sixth['data'][0]['latest_internal_start']['id']
    finally:
        engine.dispose()

