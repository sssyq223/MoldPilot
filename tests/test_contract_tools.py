from datetime import date, timedelta
from decimal import Decimal


import pytest
from sqlalchemy import select

from app import bpm, business, models as m
from app.authorization import PERMISSIONS, fingerprint
from pg_db import factory as pg_factory
from app.tool_gateway import execute, tool_schema
from domain_packs.mold import file_policy


def factory():
    return pg_factory()


def user(db,username='operator',super_admin=False):
    row=m.User(username=username,display_name=username,password_hash='test',super_admin=super_admin)
    db.add(row);db.flush();return row


def project(db,code,name='合同项目',status='ACTIVE'):
    row=m.Project(code=code,name=name,status=status)
    db.add(row);db.flush()
    mold=m.Mold(internal_number='MOLD-'+code,name=name+'模具')
    db.add(mold);db.flush()
    db.add(m.ProjectMold(project_id=row.id,mold_id=mold.id))
    return row


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


def uploaded_contract_file(db, owner, conversation, filename='sales-contract.pdf', digest=None):
    digest = digest or ('a' * 64)
    row = m.FileObject(owner_id=owner.id, conversation_id=conversation.id,
        request_key='file-'+filename+'-'+digest[:8], filename=filename,
        media_type='application/pdf', size=128, sha256=digest,
        backend='local', storage_namespace='test', object_key='test/'+digest+'/'+filename,
        storage_version=None)
    db.add(row);db.flush();return row


def approve_instance(db, actor, instance_id):
    instance = db.get(m.ApprovalInstance, instance_id)
    seat = db.scalar(select(m.ApprovalSeat).where(
        m.ApprovalSeat.instance_id == instance_id,
        m.ApprovalSeat.user_id == actor.id,
        m.ApprovalSeat.status == 'PENDING',
    ))
    payload = {
        'instance_id': instance.id,
        'seat_id': seat.id,
        'seat_version': seat.version,
        'version': instance.version,
        'snapshot_hash': instance.snapshot_hash,
        'decision': 'APPROVE',
        'comment': '合同关系与历史收付款分配已人工核对',
    }
    intent = business.create_intent(db, actor, 'approval.decide', instance.id, payload)
    return business.confirm_intent(db, actor, intent['id'], intent['challenge'])


def contract(db,project,user,kind,number,status='EFFECTIVE',amount='1000.00',expected=None,replaces_id=None):
    subject=m.BusinessSubject(kind=kind,number='SUBJECT-'+number,project_id=project.id,
        created_by=user.id,status=status,category='outsource' if kind=='full_outsource_contract' else None)
    db.add(subject);db.flush()
    db.add(m.ContractDetail(subject_id=subject.id,customer_id=None,supplier_id=None,amount=Decimal(amount),
        currency='CNY',contract_number=number,expected_date=expected,replaces_id=replaces_id,
        relation_type='REPLACEMENT' if replaces_id else 'ORIGINAL',
        settlement_allocation_evidence='测试合同替代依据' if replaces_id else None))
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
            old.status='CLOSED'
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
            assert row['current_effective_contract_totals']['sales_contract'][0]['contract_amount']=='1200.00'
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
            file=uploaded_contract_file(db,admin,conversation)
            db.add(m.RunFile(run_id=run.id,file_id=file.id))
            args={'project_id':p.id,'project_version':p.row_version,'contract_kind':'sales_contract',
                'customer_id':c.id,'supplier_id':None,'amount':'1200.00','currency':'CNY',
                'contract_number':'SC-PREPARE-001','expected_date':date.today().isoformat(),
                'signed_date':date.today().isoformat(),'received_date':date.today().isoformat(),
                'delivery_due_date':(date.today()+timedelta(days=60)).isoformat(),
                'payment_method':'合同生效后按节点付款','mapping_evidence':'已核对项目、模具和客户合同原件',
                'stages':[{'name':'预付款','amount':'600.00','condition':'合同生效'}],
                'remark':'客户线下签署合同待审批归档','workflow_definition_id':definition.id,
                'file_ids':[file.id],'document_source':'PAPER_SCAN'}
        schema=tool_schema('prepare_contract_record')['function']['parameters']
        assert {'project_id','project_version','contract_kind','workflow_definition_id','file_ids','document_source'} <= set(schema['properties'])
        with Session.begin() as db:
            admin=db.query(m.User).filter_by(username='admin').one()
            run=db.scalar(select(m.Run).where(m.Run.user_id==admin.id))
            with pytest.raises(Exception) as future_receipt:
                execute(db,admin,'prepare_contract_record',{
                    **args,'received_date':(date.today()+timedelta(days=1)).isoformat()},run=run)
            assert getattr(future_receipt.value,'code',None)=='CONTRACT_RECEIPT_DATE_INVALID'
            evidence=execute(db,admin,'prepare_contract_record',args,run=run)
            assert evidence['proposal']['kind']=='sales_contract'
            assert evidence['proposal']['requires_approval'] is True
            assert evidence['proposal']['display']['合同号']=='SC-PREPARE-001'
            assert evidence['proposal']['display']['合同附件']==['sales-contract.pdf']
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
            assert db.get(m.ContractReceiptEvidence,subject.id).received_date==date.today()
            terms=db.get(m.ContractBusinessTerms,subject.id)
            assert terms.signed_date==date.today()
            assert terms.delivery_due_date==date.today()+timedelta(days=60)
            assert terms.association_snapshot['internal_molds'][0]['internal_number']=='MOLD-CONTRACT-PREPARE'
            stage=db.scalar(select(m.PaymentStage).where(m.PaymentStage.contract_id==subject.id))
            assert stage.name=='预付款'
            attachment=db.scalar(select(m.ContractAttachment).where(m.ContractAttachment.contract_subject_id==subject.id))
            assert attachment and attachment.file_id==args['file_ids'][0]
            assert attachment.version==1 and attachment.source_kind=='PAPER_SCAN'
            instance=db.scalar(select(m.ApprovalInstance).where(
                m.ApprovalInstance.resource_type=='business_subject',
                m.ApprovalInstance.resource_id==subject.id,
            ))
            assert instance.snapshot['detail']['attachments'][0]['filename']=='sales-contract.pdf'
            assert instance.snapshot['detail']['attachments'][0]['sha256']=='a'*64
            finance=execute(db,admin,'query_finance_context',{'project_id':p.id})['data'][0]['analysis']
            business_terms=finance['sales_contracts'][0]['business_terms']
            assert business_terms['payment_method']=='合同生效后按节点付款'
            assert business_terms['association_snapshot']['internal_molds'][0]['internal_number']=='MOLD-CONTRACT-PREPARE'
            context=execute(db,admin,'query_contract_context',{'project_id':p.id})
            model_card=context['model_context']['sales_contracts'][0]
            assert model_card['signed_date']==date.today().isoformat()
            assert model_card['delivery_due_date']==(date.today()+timedelta(days=60)).isoformat()
            assert model_card['expected_contract_signing_or_supplement_date']==date.today().isoformat()
            assert model_card['customer']=={
                'id':c.id,'code':'C-PREPARE','name':'准备合同客户'}
            assert model_card['internal_molds'][0]['internal_number']=='MOLD-CONTRACT-PREPARE'
            assert model_card['customer_reference']['type']=='MANUAL_CONFIRMED'
    finally:
        engine.dispose()


def test_contract_replacement_preserves_cash_and_closes_predecessor_only_after_approval():
    engine,Session=factory()
    try:
        with Session.begin() as db:
            admin=user(db,'admin',True);p=project(db,'CONTRACT-REPLACE','合同替代项目')
            c=customer(db,'C-REPLACE','替代合同客户')
            definition=workflow(db,admin,'sales_contract')
            old=m.BusinessSubject(kind='sales_contract',number='SUBJECT-SC-OLD-001',project_id=p.id,
                created_by=admin.id,status='EFFECTIVE')
            db.add(old);db.flush()
            db.add(m.ContractDetail(subject_id=old.id,customer_id=c.id,supplier_id=None,
                amount=Decimal('1000.00'),currency='CNY',contract_number='SC-OLD-001',
                expected_date=None,replaces_id=None,relation_type='ORIGINAL',
                settlement_allocation_evidence=None))
            old_stage=m.PaymentStage(contract_id=old.id,name='原首款',amount=Decimal('1000.00'),
                currency='CNY',condition='合同生效')
            db.add(old_stage);db.flush()
            receipt=m.CustomerReceiptConfirmation(project_id=p.id,contract_subject_id=old.id,
                stage_id=old_stage.id,amount=Decimal('300.00'),currency='CNY',
                received_date=date.today(),reference='RCPT-REPLACE-001',evidence='银行回单',
                confirmed_by=admin.id,source_system='MANUAL',source_ref='BANK-REPLACE-001',note='原合同首款')
            db.add(receipt);db.flush()
            conversation=m.Conversation(user_id=admin.id,title='合同替代')
            db.add(conversation);db.flush()
            run=m.Run(conversation_id=conversation.id,user_id=admin.id,security_version=admin.security_version,
                prompt='准备替代销售合同并保留历史回款',status='SUCCEEDED',
                checkpoint={'authorization_hash':fingerprint(db,admin),'agent_permission_mode':'ask'})
            db.add(run);db.flush()
            file=uploaded_contract_file(db,admin,conversation,filename='replace.pdf',digest='e'*64)
            db.add(m.RunFile(run_id=run.id,file_id=file.id))
            args={'project_id':p.id,'project_version':p.row_version,'contract_kind':'sales_contract',
                'customer_id':c.id,'supplier_id':None,'amount':'1200.00','currency':'CNY',
                'contract_number':'SC-NEW-001','signed_date':date.today().isoformat(),
                'received_date':date.today().isoformat(),
                'delivery_due_date':(date.today()+timedelta(days=60)).isoformat(),
                'payment_method':'按替代合同节点付款','mapping_evidence':'客户书面替代通知与项目模具已核对',
                'replaces_id':old.id,'relation_type':'REPLACEMENT',
                'settlement_allocation_evidence':'财务按银行回单逐条核对并转入新合同首款节点',
                'settlement_allocations':[{'source_record_id':receipt.id,'target_stage_name':'新合同首款'}],
                'stages':[{'name':'新合同首款','amount':'500.00','condition':'替代合同生效'}],
                'remark':'客户书面确认以新合同替代原合同','workflow_definition_id':definition.id,
                'file_ids':[file.id],'document_source':'ELECTRONIC'}

        with Session.begin() as db:
            admin=db.query(m.User).filter_by(username='admin').one()
            run=db.scalar(select(m.Run).where(m.Run.user_id==admin.id))
            old=db.scalar(select(m.BusinessSubject).where(m.BusinessSubject.number=='SUBJECT-SC-OLD-001'))
            with pytest.raises(Exception) as incomplete:
                execute(db,admin,'prepare_contract_record',{**args,'settlement_allocations':[]},run=run)
            assert getattr(incomplete.value,'code',None)=='CONTRACT_SETTLEMENT_ALLOCATION_INCOMPLETE'
            evidence=execute(db,admin,'prepare_contract_record',args,run=run)
            display=evidence['proposal']['display']
            assert display['合同关系']=='替代合同'
            assert display['前序合同']=='SC-OLD-001'
            assert display['历史实收实付分配'][0]['来源记录']==args['settlement_allocations'][0]['source_record_id']
            assert old.status=='EFFECTIVE'
            assert db.scalar(select(m.BusinessSubject).where(m.BusinessSubject.number!='SUBJECT-SC-OLD-001',
                m.BusinessSubject.kind=='sales_contract')) is None

            step=m.Step(run_id=run.id,sequence=0,tool='prepare_contract_record',request_hash='replace-hash',result=evidence)
            db.add(step);db.flush()
            payload={'step_id':step.id,'proposal_hash':bpm.content_hash(evidence['proposal'])}
            intent=business.create_intent(db,admin,'contract.execute',step.id,payload)
            submitted=business.confirm_intent(db,admin,intent['id'],intent['challenge'])
            replacement=db.get(m.BusinessSubject,submitted['subject_id'])
            assert replacement.status=='SUBMITTED'
            assert old.status=='EFFECTIVE'
            allocation=db.scalar(select(m.ContractSettlementAllocation).where(
                m.ContractSettlementAllocation.target_contract_id==replacement.id))
            assert allocation.source_contract_id==old.id
            assert allocation.source_record_id==args['settlement_allocations'][0]['source_record_id']
            assert allocation.amount==Decimal('300.00')
            assert db.get(m.CustomerReceiptConfirmation,allocation.source_record_id).contract_subject_id==old.id

            before=execute(db,admin,'query_finance_context',{'project_id':old.project_id})['data'][0]['analysis']
            assert before['current_effective_contract_balances']['customer_receivable'][0]['contract_number']=='SC-OLD-001'
            assert before['current_effective_contract_balances']['customer_receivable'][0]['direct_confirmed_amount']=='300.00'

            approved=approve_instance(db,admin,submitted['instance_id'])
            assert approved['business_status']=='EFFECTIVE'
            assert replacement.status=='EFFECTIVE'
            assert old.status=='CLOSED'
            assert db.get(m.CustomerReceiptConfirmation,allocation.source_record_id).contract_subject_id==old.id

            after=execute(db,admin,'query_finance_context',{'project_id':old.project_id})['data'][0]['analysis']
            balance=after['current_effective_contract_balances']['customer_receivable'][0]
            assert balance['contract_number']=='SC-NEW-001'
            assert balance['effective_contract_amount']=='1200.00'
            assert balance['direct_confirmed_amount']=='0'
            assert balance['allocated_historical_amount']=='300.00'
            assert balance['confirmed_settlement_amount']=='300.00'
            assert balance['outstanding_amount']=='900.00'
            node=after['customer_receivable_schedule']['nodes'][0]
            assert node['contract_number']=='SC-NEW-001'
            assert node['confirmed_received_amount']=='300.00'
            assert node['allocated_history_count']==1
            assert node['outstanding_amount']=='200.00'
            assert after['customer_receipt_summary']['confirmed_totals']==[{'currency':'CNY','amount':'300.00'}]
            audit=db.scalar(select(m.AuditEvent).where(
                m.AuditEvent.action=='contract.relation.effective',
                m.AuditEvent.resource_id==replacement.id))
            assert audit and audit.detail['predecessor_id']==old.id
    finally:
        engine.dispose()


def test_contract_addition_stays_independent_and_replacement_of_addition_does_not_absorb_base_cash():
    engine,Session=factory()
    try:
        with Session.begin() as db:
            admin=user(db,'admin',True);p=project(db,'CONTRACT-ADD','合同追加项目')
            c=customer(db,'C-ADD','追加合同客户')
            definition=workflow(db,admin,'sales_contract')
            base=m.BusinessSubject(kind='sales_contract',number='SUBJECT-BASE',project_id=p.id,
                created_by=admin.id,status='EFFECTIVE')
            db.add(base);db.flush()
            db.add(m.ContractDetail(subject_id=base.id,customer_id=c.id,supplier_id=None,
                amount=Decimal('1000.00'),currency='CNY',contract_number='SC-BASE',expected_date=None,
                replaces_id=None,relation_type='ORIGINAL',settlement_allocation_evidence=None))
            base_stage=m.PaymentStage(contract_id=base.id,name='主合同款',amount=Decimal('1000.00'),
                currency='CNY',condition='合同生效')
            db.add(base_stage);db.flush()
            base_receipt=m.CustomerReceiptConfirmation(project_id=p.id,contract_subject_id=base.id,
                stage_id=base_stage.id,amount=Decimal('400.00'),currency='CNY',received_date=date.today(),
                reference='RCPT-BASE',evidence='主合同银行回单',confirmed_by=admin.id,
                source_system='MANUAL',source_ref='BANK-BASE',note='主合同回款')
            db.add(base_receipt);db.flush()
            conversation=m.Conversation(user_id=admin.id,title='追加合同')
            db.add(conversation);db.flush()
            run=m.Run(conversation_id=conversation.id,user_id=admin.id,security_version=admin.security_version,
                prompt='准备追加销售合同',status='SUCCEEDED',
                checkpoint={'authorization_hash':fingerprint(db,admin),'agent_permission_mode':'ask'})
            db.add(run);db.flush()
            file=uploaded_contract_file(db,admin,conversation,filename='addition.pdf',digest='f'*64)
            db.add(m.RunFile(run_id=run.id,file_id=file.id))
            args={'project_id':p.id,'project_version':p.row_version,'contract_kind':'sales_contract',
                'customer_id':c.id,'supplier_id':None,'amount':'200.00','currency':'CNY',
                'contract_number':'SC-ADD-001','signed_date':date.today().isoformat(),
                'received_date':date.today().isoformat(),
                'delivery_due_date':(date.today()+timedelta(days=30)).isoformat(),
                'payment_method':'追加工作完成后付款','mapping_evidence':'追加范围与原项目模具已核对',
                'replaces_id':base.id,'relation_type':'ADDITION',
                'stages':[{'name':'追加款','amount':'200.00','condition':'追加合同生效'}],
                'remark':'主合同之外的追加工作','workflow_definition_id':definition.id,
                'file_ids':[file.id],'document_source':'ELECTRONIC'}

        with Session.begin() as db:
            admin=db.query(m.User).filter_by(username='admin').one()
            run=db.scalar(select(m.Run).where(m.Run.user_id==admin.id))
            base=db.scalar(select(m.BusinessSubject).where(m.BusinessSubject.number=='SUBJECT-BASE'))
            evidence=execute(db,admin,'prepare_contract_record',args,run=run)
            step=m.Step(run_id=run.id,sequence=0,tool='prepare_contract_record',request_hash='add-hash',result=evidence)
            db.add(step);db.flush()
            payload={'step_id':step.id,'proposal_hash':bpm.content_hash(evidence['proposal'])}
            intent=business.create_intent(db,admin,'contract.execute',step.id,payload)
            submitted=business.confirm_intent(db,admin,intent['id'],intent['challenge'])
            addition=db.get(m.BusinessSubject,submitted['subject_id'])
            assert base.status=='EFFECTIVE' and addition.status=='SUBMITTED'
            assert approve_instance(db,admin,submitted['instance_id'])['business_status']=='EFFECTIVE'
            assert base.status=='EFFECTIVE' and addition.status=='EFFECTIVE'
            assert db.scalar(select(m.ContractSettlementAllocation).where(
                m.ContractSettlementAllocation.target_contract_id==addition.id)) is None

            context=execute(db,admin,'query_contract_context',{'project_id':p.id})['data'][0]
            assert context['current_effective_contract_totals']['sales_contract']==[{
                'currency':'CNY','contract_amount':'1200.00','stage_amount':'1200.00','contract_count':2}]
            finance=execute(db,admin,'query_finance_context',{'project_id':p.id})['data'][0]['analysis']
            balances={row['contract_number']:row for row in finance['current_effective_contract_balances']['customer_receivable']}
            assert balances['SC-BASE']['confirmed_settlement_amount']=='400.00'
            assert balances['SC-ADD-001']['confirmed_settlement_amount']=='0'

            addition_stage=db.scalar(select(m.PaymentStage).where(m.PaymentStage.contract_id==addition.id))
            addition_receipt=m.CustomerReceiptConfirmation(project_id=p.id,contract_subject_id=addition.id,
                stage_id=addition_stage.id,amount=Decimal('50.00'),currency='CNY',received_date=date.today(),
                reference='RCPT-ADD',evidence='追加合同银行回单',confirmed_by=admin.id,
                source_system='MANUAL',source_ref='BANK-ADD',note='追加合同回款')
            db.add(addition_receipt);db.flush()
            from domain_packs.mold.erp.commercial import contract_relations
            records=contract_relations.settlement_records(db,addition)
            assert [row['source_record_id'] for row in records]==[addition_receipt.id]
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
            file=uploaded_contract_file(db,admin,conversation,filename='duplicate-contract.pdf',digest='b'*64)
            db.add(m.RunFile(run_id=run.id,file_id=file.id))
            args={'project_id':p.id,'project_version':p.row_version,'contract_kind':'sales_contract',
                'customer_id':c.id,'supplier_id':None,'amount':'100.00','currency':'CNY',
                'contract_number':'SC-DUP','signed_date':date.today().isoformat(),
                'received_date':date.today().isoformat(),
                'delivery_due_date':(date.today()+timedelta(days=20)).isoformat(),
                'payment_method':'按合同节点付款','mapping_evidence':'已核对项目与模具',
                'workflow_definition_id':definition.id,
                'file_ids':[file.id],'document_source':'ELECTRONIC'}
            with pytest.raises(Exception) as duplicate:
                execute(db,admin,'prepare_contract_record',args,run=run)
            assert getattr(duplicate.value,'code',None)=='CONTRACT_DUPLICATE'
            bad={**args,'contract_number':'SC-NEW-DUP','customer_id':None}
            with pytest.raises(Exception) as invalid:
                execute(db,admin,'prepare_contract_record',bad,run=run)
            assert getattr(invalid.value,'code',None)=='PARTY_INVALID'
    finally:
        engine.dispose()


def test_contract_attachment_requires_current_run_and_blocks_duplicate_content():
    engine,Session=factory()
    try:
        with Session.begin() as db:
            admin=user(db,'admin',True);p=project(db,'CONTRACT-FILE-GUARD')
            c=customer(db,'C-FILE-GUARD','附件防重客户')
            definition=workflow(db,admin,'sales_contract')
            conversation=m.Conversation(user_id=admin.id,title='合同附件防重')
            db.add(conversation);db.flush()
            run=m.Run(conversation_id=conversation.id,user_id=admin.id,security_version=admin.security_version,
                prompt='提交合同附件',status='SUCCEEDED',
                checkpoint={'authorization_hash':fingerprint(db,admin),'agent_permission_mode':'ask'})
            db.add(run);db.flush()
            current=uploaded_contract_file(db,admin,conversation,filename='current-contract.pdf',digest='c'*64)
            args={'project_id':p.id,'project_version':p.row_version,'contract_kind':'sales_contract',
                'customer_id':c.id,'supplier_id':None,'amount':'200.00','currency':'CNY',
                'contract_number':'SC-FILE-GUARD','signed_date':date.today().isoformat(),
                'received_date':date.today().isoformat(),
                'delivery_due_date':(date.today()+timedelta(days=20)).isoformat(),
                'payment_method':'按合同节点付款','mapping_evidence':'已核对项目与模具',
                'workflow_definition_id':definition.id,
                'file_ids':[current.id],'document_source':'ELECTRONIC'}
            with pytest.raises(Exception) as unbound:
                execute(db,admin,'prepare_contract_record',args,run=run)
            assert getattr(unbound.value,'code',None)=='FILE_CONTEXT_INVALID'

            db.add(m.RunFile(run_id=run.id,file_id=current.id))
            prior=contract(db,p,admin,'sales_contract','SC-FILE-PRIOR')
            prior_file=uploaded_contract_file(db,admin,conversation,filename='prior-contract.pdf',digest='c'*64)
            db.add(m.ContractAttachment(contract_subject_id=prior.id,file_id=prior_file.id,
                document_id='00000000-0000-0000-0000-000000000101',version=1,
                title=prior_file.filename,source_kind='ELECTRONIC',uploaded_by=admin.id))
            db.flush()
            with pytest.raises(Exception) as duplicate:
                execute(db,admin,'prepare_contract_record',args,run=run)
            assert getattr(duplicate.value,'code',None)=='CONTRACT_FILE_DUPLICATE'
    finally:
        engine.dispose()


def test_contract_file_readability_follows_contract_scope_after_linking():
    engine,Session=factory()
    try:
        with Session.begin() as db:
            admin=user(db,'admin',True);viewer=user(db,'viewer');outsider=user(db,'outsider')
            p=project(db,'CONTRACT-FILE-READ')
            subject=contract(db,p,admin,'sales_contract','SC-FILE-READ')
            conversation=m.Conversation(user_id=admin.id,title='合同附件读取')
            db.add(conversation);db.flush()
            blob=uploaded_contract_file(db,admin,conversation,filename='scoped-contract.pdf',digest='d'*64)
            db.add(m.ContractAttachment(contract_subject_id=subject.id,file_id=blob.id,
                document_id='00000000-0000-0000-0000-000000000102',version=1,
                title=blob.filename,source_kind='PAPER_SCAN',uploaded_by=admin.id))
            grant(db,admin,viewer,'sales_contract.read',p.id)
            assert file_policy.readable(db,viewer,blob) is True
            assert file_policy.readable(db,outsider,blob) is False
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


def test_contract_family_and_current_contracts_preserve_version_history():
    from domain_packs.mold.tools.erp.commercial.contract_tools import contract_family, current_contracts
    engine, Session = factory()
    try:
        with Session.begin() as db:
            admin = user(db, 'family-admin', True)
            p = project(db, 'CONTRACT-FAMILY')
            old = contract(db, p, admin, 'sales_contract', 'SC-FAMILY-OLD', status='CLOSED', amount='1000.00')
            replacement = contract(db, p, admin, 'sales_contract', 'SC-FAMILY-NEW', amount='1200.00')
            supplement = contract(db, p, admin, 'sales_contract', 'SC-FAMILY-SUP', amount='200.00')
            db.add_all([
                m.ContractRelation(source_contract_id=replacement.id, target_contract_id=old.id,
                    relation_type='REPLACEMENT', reason='替代旧合同', confirmed_by=admin.id),
                m.ContractRelation(source_contract_id=supplement.id, target_contract_id=replacement.id,
                    relation_type='SUPPLEMENT', reason='补充金额', confirmed_by=admin.id),
            ])
            family_ids = contract_family(db, replacement.id)
            current_ids = {row.id for row in current_contracts(db, p.id, 'sales_contract')}
            assert family_ids == {old.id, replacement.id, supplement.id}
            assert current_ids == {replacement.id, supplement.id}
    finally:
        engine.dispose()


def test_replacement_and_revision_close_target_but_supplement_does_not():
    from domain_packs.mold.erp.core.domains import apply
    engine, Session = factory()
    try:
        with Session.begin() as db:
            admin = user(db, 'relation-admin', True)
            p = project(db, 'CONTRACT-RELATION')
            old = contract(db, p, admin, 'sales_contract', 'SC-REL-OLD')
            replacement = contract(db, p, admin, 'sales_contract', 'SC-REL-NEW', status='APPROVED')
            db.add(m.ContractRelation(source_contract_id=replacement.id, target_contract_id=old.id,
                relation_type='REPLACEMENT', reason='正式替代', confirmed_by=admin.id))
            apply(db, admin, replacement)
            assert old.status == 'CLOSED'
            supplement = contract(db, p, admin, 'sales_contract', 'SC-REL-SUP', status='APPROVED')
            db.add(m.ContractRelation(source_contract_id=supplement.id, target_contract_id=replacement.id,
                relation_type='SUPPLEMENT', reason='补充条款', confirmed_by=admin.id))
            apply(db, admin, supplement)
            assert replacement.status == 'EFFECTIVE'
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


def test_haier_electronic_contract_requires_confirmed_order_mapping():
    engine,Session=factory()
    try:
        with Session.begin() as db:
            admin=user(db,'admin',True);p=project(db,'CONTRACT-HAIER','海尔合同项目')
            c=customer(db,'C-HAIER','海尔');c.rule_key='haier'
            db.add(m.ProjectProfile(project_id=p.id,customer_id=c.id,owner_user_id=admin.id,
                execution_mode='INTERNAL',settlement_status='OPEN'))
            case=m.BidIntakeCase(project_id=p.id,created_by=admin.id)
            db.add(case);db.flush()
            db.add(m.BidIntakeRevision(case_id=case.id,version=1,previous_revision_id=None,
                source_kind='EMAIL',source_ref='HAIER-ORDER-MAIL',source_fingerprint='9'*64,
                received_date=date.today(),customer_classification='HAIER',
                classification_evidence='业务已确认客户为海尔',classification_confirmed_by=admin.id,
                customer_company='海尔',customer_contact='客户项目经理',customer_mold_number='HM-001',
                customer_model_or_material='MODEL-001',project_name_snapshot=p.name,amount=Decimal('1000.00'),
                currency='CNY',our_recipient='业务员',external_order_number='HAIER-ORDER-001',
                external_start_date=date.today(),customer_due_date=date.today()+timedelta(days=60),
                customer_process_confirmed=False,customer_process_confirmation_evidence=None,
                matched_quotation_subject_id=None,historical_mold_number=None,historical_relation_kind=None,
                match_result='MATCHED',match_evidence='订单与项目人工核对',notes='',recorded_by=admin.id))
            definition=workflow(db,admin,'sales_contract')
            conversation=m.Conversation(user_id=admin.id,title='海尔电子合同映射')
            db.add(conversation);db.flush()
            run=m.Run(conversation_id=conversation.id,user_id=admin.id,security_version=admin.security_version,
                prompt='登记海尔电子合同',status='SUCCEEDED',
                checkpoint={'authorization_hash':fingerprint(db,admin),'agent_permission_mode':'ask'})
            db.add(run);db.flush()
            file=uploaded_contract_file(db,admin,conversation,filename='haier-contract.pdf',digest='8'*64)
            db.add(m.RunFile(run_id=run.id,file_id=file.id))
            args={'project_id':p.id,'project_version':p.row_version,'contract_kind':'sales_contract',
                'customer_id':c.id,'supplier_id':None,'amount':'1000.00','currency':'CNY',
                'contract_number':'HAIER-SC-001','signed_date':date.today().isoformat(),
                'received_date':date.today().isoformat(),
                'delivery_due_date':(date.today()+timedelta(days=60)).isoformat(),
                'payment_method':'3-3-3-1节点收款','customer_order_number':'WRONG-ORDER',
                'mapping_evidence':'已人工核对电子合同订单号','workflow_definition_id':definition.id,
                'file_ids':[file.id],'document_source':'ELECTRONIC'}
        with Session.begin() as db:
            admin=db.query(m.User).filter_by(username='admin').one()
            run=db.scalar(select(m.Run).where(m.Run.user_id==admin.id))
            with pytest.raises(Exception) as mismatch:
                execute(db,admin,'prepare_contract_record',args,run=run)
            assert getattr(mismatch.value,'code',None)=='CONTRACT_ORDER_MISMATCH'
            evidence=execute(db,admin,'prepare_contract_record',{
                **args,'customer_order_number':'HAIER-ORDER-001'},run=run)
            mapping=evidence['proposal']['display']['客户编号核对']
            assert mapping['客户规则']=='HAIER'
            assert mapping['关联类型']=='ORDER_NUMBER'
            assert mapping['客户订单号']=='HAIER-ORDER-001'
    finally:
        engine.dispose()


def test_contract_context_projects_cash_shortfall_without_changing_terms():
    engine,Session=factory()
    try:
        with Session.begin() as db:
            admin=user(db,'admin',True);p=project(db,'CONTRACT-CASH-TIMING','合同资金时序项目')
            sales=contract(db,p,admin,'sales_contract','SC-CASH',amount='1000.00')
            outsource=contract(db,p,admin,'full_outsource_contract','FO-CASH',amount='800.00')
            sales_stage=db.scalar(select(m.PaymentStage).where(m.PaymentStage.contract_id==sales.id))
            sales_stage.expected_due_date=date.today()+timedelta(days=30)
            outsource_stage=db.scalar(select(m.PaymentStage).where(m.PaymentStage.contract_id==outsource.id))
            outsource_stage.expected_due_date=date.today()+timedelta(days=10)
        with Session() as db:
            admin=db.query(m.User).filter_by(username='admin').one()
            provisional=execute(db,admin,'query_contract_context',{'project_id':p.id})
            provisional_analysis=provisional['data'][0]['cash_timing_analysis']
            assert provisional_analysis['state']=='DATES_INCOMPLETE'
            assert provisional_analysis['currencies'][0]['first_projected_shortfall'] is None
            assert provisional['data'][0]['derived_status']['has_projected_cash_shortfall'] is False
        with Session.begin() as db:
            sales_stage=db.scalar(select(m.PaymentStage).where(m.PaymentStage.contract_id==sales.id))
            sales_stage.schedule_confirmed=True
            sales_stage.trigger_event='客户验收'
            sales_stage.schedule_evidence='销售合同节点已由财务核对'
            outsource_stage=db.scalar(select(m.PaymentStage).where(m.PaymentStage.contract_id==outsource.id))
            outsource_stage.schedule_confirmed=True
            outsource_stage.trigger_event='供应商预付款'
            outsource_stage.schedule_evidence='委外合同节点已由采购和财务核对'
        with Session() as db:
            admin=db.query(m.User).filter_by(username='admin').one()
            result=execute(db,admin,'query_contract_context',{'project_id':p.id})
            analysis=result['data'][0]['cash_timing_analysis']
            assert analysis['state']=='DATED_RISK'
            cny=analysis['currencies'][0]
            assert cny['first_projected_shortfall']=={
                'date':(date.today()+timedelta(days=10)).isoformat(),
                'amount':'400.00','currency':'CNY'}
            assert result['data'][0]['derived_status']['has_projected_cash_shortfall'] is True
            assert '不改变条款' in analysis['semantics']
    finally:
        engine.dispose()

