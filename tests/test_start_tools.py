from datetime import date
from decimal import Decimal

import pytest
from sqlalchemy import select

from app import bpm, business, models as m
from app.authorization import PERMISSIONS, fingerprint
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


def test_start_readiness_schema_and_can_prepare_from_known_facts():
    engine,Session=factory()
    try:
        with Session.begin() as db:
            admin=user(db,'admin',True);p=project(db,'START-M001','正式开工核对项目')
            decision(db,p,admin,'quote_acceptance','QA-START','ACCEPT')
            sales_contract(db,p,admin)
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


def test_prepare_internal_start_requires_human_confirmation_then_submits_bpm():
    engine,Session=factory()
    try:
        with Session.begin() as db:
            admin=user(db,'admin',True);p=project(db,'START-PREPARE','正式开工办理项目')
            accept=decision(db,p,admin,'quote_acceptance','QA-START-PREPARE','ACCEPT',mode='FULL_OUTSOURCE')
            sales_contract(db,p,admin)
            definition=workflow(db,admin)
            conversation=m.Conversation(user_id=admin.id,title='正式开工')
            db.add(conversation);db.flush()
            run=m.Run(conversation_id=conversation.id,user_id=admin.id,security_version=admin.security_version,
                prompt='准备正式开工',status='SUCCEEDED',
                checkpoint={'authorization_hash':fingerprint(db,admin),'agent_permission_mode':'delegated_auto'})
            db.add(run);db.flush()
            args={'project_id':p.id,'project_version':p.row_version,'source_subject_id':accept.id,
                'effective_date':date.today().isoformat(),'evidence':'客户开工通知与工艺方案已确认',
                'workflow_definition_id':definition.id}
        schema=tool_schema('prepare_internal_start')['function']['parameters']
        assert {'project_id','project_version','source_subject_id','workflow_definition_id'} <= set(schema['properties'])
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
            definition=workflow(db,admin)
            conversation=m.Conversation(user_id=admin.id,title='正式开工')
            db.add(conversation);db.flush()
            run=m.Run(conversation_id=conversation.id,user_id=admin.id,security_version=admin.security_version,
                prompt='准备正式开工',status='SUCCEEDED',
                checkpoint={'authorization_hash':fingerprint(db,admin),'agent_permission_mode':'ask'})
            db.add(run);db.flush()
            args={'project_id':p.id,'project_version':p.row_version,'source_subject_id':accept.id,
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
            for permission in ('project.read','quote_acceptance.read','internal_start.read','internal_start.create','internal_start.submit'):
                grant(db,admin,operator,permission,p.id)
            capability(db,operator,'prepare_internal_start')
            run.checkpoint={'authorization_hash':fingerprint(db,operator),'agent_permission_mode':'ask'}
            args={'project_id':p.id,'project_version':p.row_version,'source_subject_id':accept.id,
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

