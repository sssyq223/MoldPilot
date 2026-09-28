from copy import deepcopy
from datetime import timedelta

import pytest
from sqlalchemy import select, func
from sqlalchemy.exc import DBAPIError

from app import models as m
from app.authorization import fingerprint
from app.db import now
from app.tool_gateway import execute
from conftest import sign_in
from test_domains import checked, approve, confirm, workflow
from test_contract_tools import uploaded_contract_file
from test_finance_correction_tools import correction_case, prepared_step
from test_replacement_finance_execution import replacement, original_contract_id


def material_case(client,data,kind='sales_contract'):
    ids,factory=data
    sign_in(client)
    with factory.begin() as db:
        actor=db.get(m.User,ids['admin'])
        if not db.scalar(select(m.ProjectMold).where(m.ProjectMold.project_id==ids['project'])):
            mold=m.Mold(internal_number='REVISION-MOLD',name='合同修订模具');db.add(mold);db.flush()
            db.add(m.ProjectMold(project_id=ids['project'],mold_id=mold.id))
        customer=m.Customer(code='REVISION-C',name='修订客户',active=True);db.add(customer);db.flush()
        conversation=m.Conversation(user_id=actor.id,title='合同材料修订');db.add(conversation);db.flush()
        blob=uploaded_contract_file(db,actor,conversation,filename='revision-original.pdf',digest='7'*64)
        args={'project_id':ids['project'],'project_version':db.get(m.Project,ids['project']).row_version,
            'contract_kind':kind,'customer_id':customer.id if kind=='sales_contract' else None,
            'amount':'100','currency':'CNY','contract_number':'REVISION-001','signed_date':now().date().isoformat(),
            'received_date':now().date().isoformat(),'delivery_due_date':(now().date()+timedelta(days=30)).isoformat(),
            'payment_method':'按节点支付','mapping_evidence':'已人工核对合同项目和模具',
            'stages':[{'name':'阶段款','amount':'100','condition':'依据核实后支付'}],
            'file_ids':[blob.id],'document_source':'ELECTRONIC'}
        conversation_id=conversation.id
    args['workflow_definition_id']=workflow(client,ids,kind)
    return args,conversation_id


def contract_step(data,args,conversation_id):
    with data[1].begin() as db:
        actor=db.get(m.User,data[0]['admin'])
        run=m.Run(conversation_id=conversation_id,user_id=actor.id,security_version=actor.security_version,
            prompt='明确核对合同材料并提交新审批轮',status='SUCCEEDED',
            checkpoint={'authorization_hash':fingerprint(db,actor),'agent_permission_mode':'ask'})
        db.add(run);db.flush()
        for file_id in args['file_ids']:db.add(m.RunFile(run_id=run.id,file_id=file_id))
        db.flush()
        result=execute(db,actor,'prepare_contract_record',args,run=run)
        step=m.Step(run_id=run.id,sequence=0,tool='prepare_contract_record',request_hash='contract-revision',result=result)
        db.add(step);db.flush();return step.id


def submit_card(client,step):
    intent=checked(client.post('/api/contract-proposals/'+step+'/intent'))
    result=confirm(client,intent)
    assert confirm(client,intent)==result
    assert checked(client.get('/api/contract-proposals/'+step))['receipt']==result
    return result


@pytest.mark.parametrize('decision',['RETURN','REJECT'])
def test_contract_revisions_append_materials_and_select_current_originals(client,data,decision):
    args,conversation_id=material_case(client,data)
    original=submit_card(client,contract_step(data,args,conversation_id))
    approve(client,original['instance_id'],decision)
    with data[1].begin() as db:
        snapshot=deepcopy(db.get(m.ApprovalInstance,original['instance_id']).snapshot)
        old_stage=snapshot['detail']['stages'][0]['id']
        blob=uploaded_contract_file(db,db.get(m.User,data[0]['admin']),db.get(m.Conversation,conversation_id),
            filename='revision-corrected.pdf',digest='8'*64)
        revised={**args,'existing_subject_id':original['subject_id'],'subject_revision':original['subject_revision'],
            'revision_reason':'合同金额与付款节点经客户核对修订','amount':'120',
            'stages':[{'name':'修订节点','amount':'120','condition':'新合同条件核对'}],'file_ids':[blob.id]}
    receipt=submit_card(client,contract_step(data,revised,conversation_id))
    assert receipt['subject_id']==original['subject_id'] and receipt['round_no']==2 and receipt['subject_revision']==2
    with data[1]() as db:
        assert db.get(m.ApprovalInstance,original['instance_id']).snapshot==snapshot
        assert db.get(m.ContractDetail,original['subject_id']).material_version==2
        assert db.get(m.ContractBusinessTerms,(1,original['subject_id'])).attachment_selection==args['file_ids']
        assert db.get(m.ContractBusinessTerms,(2,original['subject_id'])).attachment_selection==revised['file_ids']
        assert db.scalar(select(func.count()).select_from(m.ContractReceiptEvidence).where(
            m.ContractReceiptEvidence.contract_subject_id==original['subject_id']))==2
        current=db.get(m.ApprovalInstance,receipt['instance_id']).snapshot['detail']
        assert len(current['stages'])==1 and current['stages'][0]['id']!=old_stage
        assert {row['file_id'] for row in current['attachments'] if row['is_current']}==set(revised['file_ids'])
        assert {row['file_id'] for row in current['attachments'] if not row['is_current']}==set(args['file_ids'])
        assert db.get(m.PaymentStage,old_stage).amount==100
        with pytest.raises(DBAPIError),db.begin_nested():
            db.get(m.ContractBusinessTerms,(1,original['subject_id'])).payment_method='不能覆盖旧条款'
            db.flush()
    assert approve(client,receipt['instance_id'])['business_status']=='EFFECTIVE'
    intent=checked(client.post('/api/business/command-intents',json={'action':'customer_receipt.confirm',
        'resource_id':receipt['subject_id'],'payload':{'stage_id':old_stage,'amount':'1','currency':'CNY',
        'received_date':now().date().isoformat(),'reference':'OLD-STAGE','evidence':'旧节点不可办理'}}))
    response=client.post('/api/human-actions/'+intent['id']+'/confirm',json={'challenge':intent['challenge']})
    assert response.status_code==409 and response.json()['error']['code']=='CONTRACT_STAGE_SUPERSEDED'


def test_blocked_replacement_revises_allocations_without_mutating_prior_history(client,data,correction_case):
    correction_args,payment_id=correction_case
    old_id=original_contract_id(data,payment_id)
    target,_,old_iid=replacement(client,data,old_id)
    correction_step=prepared_step(data,correction_args)
    corrected=confirm(client,checked(client.post('/api/finance-proposals/'+correction_step+'/intent')))
    approve(client,corrected['instance_id'])
    assert approve(client,old_iid)['business_status']=='APPLY_BLOCKED'
    args,conversation_id=material_case(client,data,'full_outsource_contract')
    with data[1]() as db:
        from domain_packs.mold.erp.commercial.contract_relations import settlement_records
        old=db.get(m.ContractDetail,old_id);original=db.get(m.BusinessSubject,target)
        snapshot=deepcopy(db.get(m.ApprovalInstance,old_iid).snapshot)
        args.update({'supplier_id':old.supplier_id,'contract_number':db.get(m.ContractDetail,target).contract_number,
            'existing_subject_id':target,'subject_revision':original.revision,'revision_reason':'补齐最新冲正原件后重审',
            'replaces_id':old_id,'relation_type':'REPLACEMENT','settlement_allocation_evidence':'核对原付款及最新冲正',
            'settlement_allocations':[{'source_record_id':row['source_record_id'],'target_stage_name':'阶段款'}
                for row in settlement_records(db,db.get(m.BusinessSubject,old_id))]})
    revised=submit_card(client,contract_step(data,args,conversation_id))
    with data[1]() as db:
        rows=list(db.scalars(select(m.ContractSettlementAllocation).where(m.ContractSettlementAllocation.target_contract_id==target)))
        assert len(rows)==3
        assert len([row for row in rows if row.material_version==1])==1
        assert sum(row.amount for row in rows if row.material_version==2)==0
        assert db.get(m.ApprovalInstance,old_iid).snapshot==snapshot
        assert db.get(m.BusinessSubject,old_id).status=='EFFECTIVE'
    assert approve(client,revised['instance_id'])['business_status']=='EFFECTIVE'
    with data[1]() as db:
        finance=execute(db,db.get(m.User,data[0]['admin']),'query_finance_context',{'project_id':data[0]['project']})['model_context']
        balance=next(row for row in finance['current_effective_contract_balances']['supplier_payable'] if row['contract_id']==target)
        assert balance['confirmed_settlement_amount']=='0.00' and balance['outstanding_amount']=='100.00'


def test_revision_reuses_explicit_original_file_and_conflicting_cards_stop(client,data):
    args,conversation_id=material_case(client,data)
    original=submit_card(client,contract_step(data,args,conversation_id))
    approve(client,original['instance_id'],'RETURN')
    revised={**args,'existing_subject_id':original['subject_id'],'subject_revision':1,'revision_reason':'条款核对后原件不变重提'}
    first=contract_step(data,revised,conversation_id);second=contract_step(data,revised,conversation_id)
    intent1=checked(client.post('/api/contract-proposals/'+first+'/intent'))
    intent2=checked(client.post('/api/contract-proposals/'+second+'/intent'))
    confirm(client,intent1)
    response=client.post('/api/human-actions/'+intent2['id']+'/confirm',json={'challenge':intent2['challenge']})
    assert response.status_code==409 and response.json()['error']['code']=='VERSION_CONFLICT'
    with data[1]() as db:
        assert db.scalar(select(func.count()).select_from(m.ContractAttachment))==1
        assert db.scalar(select(func.count()).select_from(m.ContractBusinessTerms))==2
