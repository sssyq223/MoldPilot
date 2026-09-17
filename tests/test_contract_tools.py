from datetime import date, timedelta
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


def project(db,code,name='合同项目',status='ACTIVE'):
    row=m.Project(code=code,name=name,status=status)
    db.add(row);db.flush();return row


def grant(db,admin,target,permission,project_id,fields=None):
    db.add(m.Grant(user_id=target.id,permission=permission,effect='ALLOW',scope={'project_id':[project_id]},
        fields=fields or PERMISSIONS[permission],reason='unit test',granted_by=admin.id))


def capability(db,target,key,kind='TOOL'):
    db.add(m.Capability(user_id=target.id,kind=kind,key=key,enabled=True))


def customer(db,code='C-001',name='客户A'):
    row=m.Customer(code=code,name=name,rule_key='standard',active=True)
    db.add(row);db.flush();return row


def supplier(db,code='S-OUT',name='委外供应商'):
    row=m.Supplier(code=code,name=name,category='outsource',active=True)
    db.add(row);db.flush();return row


def workflow(db,user,kind='sales_contract'):
    config={'business_type':kind,
        'nodes':[{'key':'review','name':'合同审批','mode':'ALL','users':[user.id],'reject_rules':[]}]}
    row=m.WorkflowDefinition(process_key=kind+'_test',version=1,name='合同审批',
        status='PUBLISHED',config=config,bpmn_xml=bpm.compile_bpmn(config),package_hash='test')
    db.add(row);db.flush();return row


def contract(db,project,user,kind,number,status='EFFECTIVE',amount='1000.00',expected=None,replaces_id=None):
    subject=m.BusinessSubject(kind=kind,number='SUBJECT-'+number,project_id=project.id,
        created_by=user.id,status=status,category='outsource' if kind=='full_outsource_contract' else None)
    db.add(subject);db.flush()
    db.add(m.ContractDetail(subject_id=subject.id,customer_id=None,supplier_id=None,amount=Decimal(amount),
        currency='CNY',contract_number=number,expected_date=expected,replaces_id=replaces_id))
    db.add(m.PaymentStage(contract_id=subject.id,name='预付款',amount=Decimal(amount)/Decimal(2),
        currency='CNY',condition='合同生效'))
    return subject


def test_contract_context_resolves_by_contract_number_and_summarizes_nodes():
    engine,Session=factory()
    try:
        with Session.begin() as db:
            admin=user(db,'admin',True);p=project(db,'CONTRACT-M001')
            old=contract(db,p,admin,'sales_contract','SC-OLD',amount='800.00')
            contract(db,p,admin,'sales_contract','SC-NEW',amount='1200.00',replaces_id=old.id)
            contract(db,p,admin,'full_outsource_contract','FO-001',amount='5000.00')
        schema=tool_schema('query_contract_context')['function']['parameters']
        assert {'project_id','identifier'} <= set(schema['properties'])
        with Session() as db:
            admin=db.query(m.User).filter_by(username='admin').one()
            result=execute(db,admin,'query_contract_context',{'identifier':'SC-NEW'})
            assert result['resolution']=='RESOLVED'
            row=result['data'][0]
            assert row['project']['code']=='CONTRACT-M001'
            assert row['derived_status']['has_sales_contract'] is True
            assert row['derived_status']['has_full_outsource_contract'] is True
            assert row['derived_status']['has_replacement_relation'] is True
            assert row['sales_contracts'][0]['detail']['stages'][0]['name']=='预付款'
            assert row['contract_totals']['sales_contract'][0]['contract_amount']=='2000.00'
            assert any(link['contract_number']=='SC-NEW' and link['replaces_number']=='SC-OLD'
                for link in row['replacement_links'])
    finally:
        engine.dispose()


def test_contract_context_respects_contract_tool_boundaries():
    engine,Session=factory()
    try:
        with Session.begin() as db:
            admin=user(db,'admin',True);operator=user(db)
            p=project(db,'CONTRACT-LIMITED')
            contract(db,p,admin,'sales_contract','HIDDEN-SALES',amount='300.00')
            contract(db,p,admin,'full_outsource_contract','VISIBLE-OUTSOURCE',amount='900.00')
            grant(db,admin,operator,'project.read',p.id);grant(db,admin,operator,'project.dossier.read',p.id)
            grant(db,admin,operator,'full_outsource_contract.read',p.id)
            capability(db,operator,'query_contract_context');capability(db,operator,'query_full_outsource_contract')
        with Session() as db:
            operator=db.query(m.User).filter_by(username='operator').one()
            result=execute(db,operator,'query_contract_context',{'identifier':'CONTRACT-LIMITED'})
            row=result['data'][0]
            assert row['sales_contracts']==[]
            assert row['full_outsource_contracts'][0]['detail']['contract_number']=='VISIBLE-OUTSOURCE'
            assert 'HIDDEN-SALES' not in str(result)
            assert '未返回：销售合同' in ''.join(result['limitations'])
    finally:
        engine.dispose()


def test_contract_context_reports_multiple_projects_and_late_expected_contracts():
    engine,Session=factory()
    try:
        with Session.begin() as db:
            admin=user(db,'admin',True)
            p1=project(db,'CONTRACT-A','共同合同项目A')
            p2=project(db,'CONTRACT-B','共同合同项目B')
            contract(db,p1,admin,'sales_contract','SC-A',status='SUBMITTED',
                expected=date.today()-timedelta(days=1))
            contract(db,p2,admin,'sales_contract','SC-B')
        with Session() as db:
            admin=db.query(m.User).filter_by(username='admin').one()
            ambiguous=execute(db,admin,'query_contract_context',{'identifier':'共同合同项目'})
            assert ambiguous['resolution']=='MULTIPLE_CANDIDATES'
            assert {row['code'] for row in ambiguous['data']}=={'CONTRACT-A','CONTRACT-B'}
            resolved=execute(db,admin,'query_contract_context',{'identifier':'SC-A'})
            row=resolved['data'][0]
            assert row['derived_status']['has_late_expected_contract'] is True
            assert row['late_expected_contracts'][0]['contract_number']=='SC-A'
    finally:
        engine.dispose()


def test_prepare_sales_contract_requires_confirmation_then_submits_bpm():
    engine,Session=factory()
    try:
        with Session.begin() as db:
            admin=user(db,'admin',True);p=project(db,'CONTRACT-PREPARE','合同办理项目')
            c=customer(db,'C-PREPARE','准备合同客户')
            definition=workflow(db,admin,'sales_contract')
            conversation=m.Conversation(user_id=admin.id,title='合同登记')
            db.add(conversation);db.flush()
            run=m.Run(conversation_id=conversation.id,user_id=admin.id,security_version=admin.security_version,
                prompt='准备登记销售合同',status='SUCCEEDED',
                checkpoint={'authorization_hash':fingerprint(db,admin),'agent_permission_mode':'delegated_auto'})
            db.add(run);db.flush()
            args={'project_id':p.id,'project_version':p.row_version,'contract_kind':'sales_contract',
                'customer_id':c.id,'supplier_id':None,'amount':'1200.00','currency':'CNY',
                'contract_number':'SC-PREPARE-001','expected_date':date.today().isoformat(),
                'stages':[{'name':'预付款','amount':'600.00','condition':'合同生效'}],
                'remark':'客户线下签署合同待审批归档','workflow_definition_id':definition.id}
        schema=tool_schema('prepare_contract_record')['function']['parameters']
        assert {'project_id','project_version','contract_kind','workflow_definition_id'} <= set(schema['properties'])
        with Session.begin() as db:
            admin=db.query(m.User).filter_by(username='admin').one()
            run=db.scalar(select(m.Run).where(m.Run.user_id==admin.id))
            evidence=execute(db,admin,'prepare_contract_record',args,run=run)
            assert evidence['proposal']['kind']=='sales_contract'
            assert evidence['proposal']['requires_approval'] is True
            assert evidence['proposal']['display']['合同号']=='SC-PREPARE-001'
            assert db.scalar(select(m.BusinessSubject).where(m.BusinessSubject.kind=='sales_contract')) is None
            step=m.Step(run_id=run.id,sequence=0,tool='prepare_contract_record',request_hash='hash',result=evidence)
            db.add(step);db.flush()
            payload={'step_id':step.id,'proposal_hash':bpm.content_hash(evidence['proposal'])}
            intent=business.create_intent(db,admin,'contract.execute',step.id,payload)
            receipt=business.confirm_intent(db,admin,intent['id'],intent['challenge'])
            assert receipt['status']=='SUBMITTED'
            assert receipt['contract_kind']=='sales_contract'
            subject=db.scalar(select(m.BusinessSubject).where(m.BusinessSubject.kind=='sales_contract'))
            assert subject and subject.status=='SUBMITTED'
            detail=db.get(m.ContractDetail,subject.id)
            assert detail.contract_number=='SC-PREPARE-001'
            assert detail.customer_id==args['customer_id']
            stage=db.scalar(select(m.PaymentStage).where(m.PaymentStage.contract_id==subject.id))
            assert stage.name=='预付款'
            assert db.scalar(select(m.ApprovalInstance).where(
                m.ApprovalInstance.resource_type=='business_subject',
                m.ApprovalInstance.resource_id==subject.id,
            ))
    finally:
        engine.dispose()


def test_contract_context_returns_workflow_options_when_prepare_tool_is_available():
    engine,Session=factory()
    try:
        with Session.begin() as db:
            admin=user(db,'admin',True);p=project(db,'CONTRACT-WORKFLOW')
            definition=workflow(db,admin,'sales_contract')
        with Session() as db:
            admin=db.query(m.User).filter_by(username='admin').one()
            result=execute(db,admin,'query_contract_context',{'identifier':'CONTRACT-WORKFLOW'})
            workflows=result['data'][0]['workflow_options']['sales_contract']
            assert workflows[0]['id']==definition.id
    finally:
        engine.dispose()


def test_prepare_contract_rejects_duplicates_and_invalid_party():
    engine,Session=factory()
    try:
        with Session.begin() as db:
            admin=user(db,'admin',True);p=project(db,'CONTRACT-DUP')
            c=customer(db,'C-DUP','重复客户')
            definition=workflow(db,admin,'sales_contract')
            contract(db,p,admin,'sales_contract','SC-DUP')
            conversation=m.Conversation(user_id=admin.id,title='合同重复')
            db.add(conversation);db.flush()
            run=m.Run(conversation_id=conversation.id,user_id=admin.id,security_version=admin.security_version,
                prompt='准备登记销售合同',status='SUCCEEDED',
                checkpoint={'authorization_hash':fingerprint(db,admin),'agent_permission_mode':'ask'})
            db.add(run);db.flush()
            args={'project_id':p.id,'project_version':p.row_version,'contract_kind':'sales_contract',
                'customer_id':c.id,'supplier_id':None,'amount':'100.00','currency':'CNY',
                'contract_number':'SC-DUP','workflow_definition_id':definition.id}
            with pytest.raises(Exception) as duplicate:
                execute(db,admin,'prepare_contract_record',args,run=run)
            assert getattr(duplicate.value,'code',None)=='CONTRACT_DUPLICATE'
            bad={**args,'contract_number':'SC-NEW-DUP','customer_id':None}
            with pytest.raises(Exception) as invalid:
                execute(db,admin,'prepare_contract_record',bad,run=run)
            assert getattr(invalid.value,'code',None)=='PARTY_INVALID'
    finally:
        engine.dispose()


def test_prepare_contract_signing_record_requires_confirmation_then_records():
    engine,Session=factory()
    try:
        with Session.begin() as db:
            admin=user(db,'admin',True);p=project(db,'CONTRACT-SIGN-PREPARE','合同签署项目')
            sup=supplier(db,'S-SIGN','签署供应商')
            subject=contract(db,p,admin,'full_outsource_contract','FO-SIGN-001')
            detail=db.get(m.ContractDetail,subject.id)
            detail.supplier_id=sup.id
            conversation=m.Conversation(user_id=admin.id,title='合同签署登记')
            db.add(conversation);db.flush()
            run=m.Run(conversation_id=conversation.id,user_id=admin.id,security_version=admin.security_version,
                prompt='登记整套委外合同签署扫描件',status='SUCCEEDED',
                checkpoint={'authorization_hash':fingerprint(db,admin),'agent_permission_mode':'ask'})
            db.add(run);db.flush()
            args={'project_id':p.id,'project_version':p.row_version,'contract_subject_id':subject.id,
                'template_name':'整套委外标准合同模板','signing_method':'OFFLINE_FILE','status':'SIGNED',
                'signed_date':date.today().isoformat(),'signed_file_title':'FO-SIGN-001双方盖章扫描件.pdf',
                'supplier_signer':'供应商张三','evidence':'双方盖章扫描件已由采购核对','source_ref':'SIGN-PREPARE-001'}
        schema=tool_schema('prepare_contract_signing_record')['function']['parameters']
        assert {'project_id','project_version','contract_subject_id','status','signed_file_title'} <= set(schema['properties'])
        with Session.begin() as db:
            admin=db.query(m.User).filter_by(username='admin').one()
            run=db.scalar(select(m.Run).where(m.Run.user_id==admin.id))
            evidence=execute(db,admin,'prepare_contract_signing_record',args,run=run)
            assert evidence['proposal']['kind']=='contract_signing_record'
            assert evidence['proposal']['requires_approval'] is False
            assert evidence['proposal']['display']['整套委外合同']=='FO-SIGN-001'
            assert db.scalar(select(m.ContractSigningRecord).where(m.ContractSigningRecord.source_ref=='SIGN-PREPARE-001')) is None
            step=m.Step(run_id=run.id,sequence=0,tool='prepare_contract_signing_record',request_hash='hash',result=evidence)
            db.add(step);db.flush()
            payload={'step_id':step.id,'proposal_hash':bpm.content_hash(evidence['proposal'])}
            intent=business.create_intent(db,admin,'contract.execute',step.id,payload)
            receipt=business.confirm_intent(db,admin,intent['id'],intent['challenge'])
            assert receipt['status']=='CONFIRMED'
            row=db.get(m.ContractSigningRecord,receipt['contract_signing_record_id'])
            assert row.contract_subject_id==args['contract_subject_id']
            assert row.status=='SIGNED'
            assert row.signed_file_title=='FO-SIGN-001双方盖章扫描件.pdf'
            assert row.approved_by==admin.id
            assert row.source_system=='MANUAL'
    finally:
        engine.dispose()


def test_prepare_contract_signing_record_rejects_duplicate_and_missing_signed_date():
    engine,Session=factory()
    try:
        with Session.begin() as db:
            admin=user(db,'admin',True);p=project(db,'CONTRACT-SIGN-BLOCK','合同签署阻断项目')
            sup=supplier(db,'S-SIGN-BLOCK','签署阻断供应商')
            subject=contract(db,p,admin,'full_outsource_contract','FO-SIGN-BLOCK')
            detail=db.get(m.ContractDetail,subject.id)
            detail.supplier_id=sup.id
            db.add(m.ContractSigningRecord(contract_subject_id=subject.id,template_name='整套委外标准合同模板',
                signing_method='OFFLINE_FILE',status='SIGNED',signed_date=date.today(),
                signed_file_title='已登记盖章扫描件.pdf',supplier_signer='供应商李四',
                buyer_reviewer_id=admin.id,approved_by=admin.id,evidence='历史签署记录',
                source_system='MANUAL',source_ref='SIGN-DUP-001',recorded_by=admin.id))
            conversation=m.Conversation(user_id=admin.id,title='合同签署重复')
            db.add(conversation);db.flush()
            run=m.Run(conversation_id=conversation.id,user_id=admin.id,security_version=admin.security_version,
                prompt='登记整套委外合同签署扫描件',status='SUCCEEDED',
                checkpoint={'authorization_hash':fingerprint(db,admin),'agent_permission_mode':'ask'})
            db.add(run);db.flush()
            args={'project_id':p.id,'project_version':p.row_version,'contract_subject_id':subject.id,
                'template_name':'整套委外标准合同模板','signing_method':'OFFLINE_FILE','status':'SIGNED',
                'signed_date':date.today().isoformat(),'signed_file_title':'FO-SIGN-BLOCK双方盖章扫描件.pdf',
                'supplier_signer':'供应商李四','evidence':'重复签署来源','source_ref':'SIGN-DUP-001'}
            with pytest.raises(Exception) as duplicate:
                execute(db,admin,'prepare_contract_signing_record',args,run=run)
            assert getattr(duplicate.value,'code',None)=='CONTRACT_SIGNING_DUPLICATE'
            missing_date={**args,'source_ref':'SIGN-DATE-MISSING','signed_date':None}
            with pytest.raises(Exception) as invalid:
                execute(db,admin,'prepare_contract_signing_record',missing_date,run=run)
            assert getattr(invalid.value,'code',None)=='INVALID_TOOL_INPUT'
    finally:
        engine.dispose()

