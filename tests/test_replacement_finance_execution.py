"""Confirmed historical cash must constrain execution on replacement contracts."""
from decimal import Decimal

import pytest
from sqlalchemy import select

from app import models as m
from app.db import now
from app.errors import DomainError
from app.tool_gateway import execute
from conftest import sign_in
from test_domains import checked, subject, approve, command, workflow, confirm
from test_finance_correction_tools import correction_case, prepared_step


def replacement(client, data, predecessor_id, *, stage_amount='100', submit=True):
    ids, factory = data
    with factory() as db:
        previous = db.get(m.BusinessSubject, predecessor_id)
        old = db.get(m.ContractDetail, predecessor_id)
        detail = {'customer_id':old.customer_id, 'supplier_id':old.supplier_id,
                  'amount':'100', 'currency':'CNY', 'contract_number':'REPLACE-'+predecessor_id[:8],
                  'replaces_id':predecessor_id, 'relation_type':'REPLACEMENT',
                  'settlement_allocation_evidence':'财务逐条确认转入新合同节点',
                  'stages':[{'name':'替代节点', 'amount':stage_amount, 'condition':'核对付款条件'}]}
        kind, category = previous.kind, previous.category
    created = checked(client.post('/api/business/subjects', json={'kind':kind, 'project_id':ids['project'],
        'category':category, 'remark':'合成替代合同', 'detail':detail}))
    with factory.begin() as db:
        from domain_packs.mold.erp.commercial.contract_relations import settlement_records
        stage = db.scalar(select(m.PaymentStage).where(m.PaymentStage.contract_id == created['id']))
        for row in settlement_records(db, db.get(m.BusinessSubject, predecessor_id)):
            db.add(m.ContractSettlementAllocation(target_contract_id=created['id'], target_stage_id=stage.id,
                source_contract_id=row['source_contract_id'], record_type=row['record_type'],
                source_record_id=row['source_record_id'], amount=row['amount'], currency=row['currency'],
                evidence=detail['settlement_allocation_evidence'], recorded_by=ids['admin']))
        stage_id = stage.id
    definition = workflow(client, ids, kind)
    iid = None
    if submit:
        intent = checked(client.post('/api/business/subjects/'+created['id']+'/submit-intent',
            json={'revision':1,'definition_id':definition}))
        iid = confirm(client, intent)['instance_id']
    return created['id'], stage_id, iid


def original_contract_id(data, payment_id):
    with data[1]() as db:
        request = db.get(m.PaymentRequestDetail, payment_id)
        return db.get(m.PaymentStage, request.stage_id).contract_id


def test_replacement_payment_request_cannot_spend_allocated_history_again(client, data, correction_case):
    _, payment_id = correction_case
    target, stage_id, iid = replacement(client, data, original_contract_id(data, payment_id))
    approve(client, iid)
    command(client, 'finance.condition', stage_id, {'evidence':'替代节点条件核对'})
    too_much = checked(client.post('/api/business/subjects', json={'kind':'supplier_payment',
        'project_id':data[0]['project'], 'category':'outsource', 'remark':'不能重复花已付历史',
        'detail':{'stage_id':stage_id,'amount':'71','currency':'CNY'}}))
    definition = workflow(client, data[0], 'supplier_payment')
    result = client.post('/api/business/subjects/'+too_much['id']+'/submit-intent',
        json={'revision':1,'definition_id':definition})
    # Some validation occurs at the confirmation boundary rather than intent preparation.
    if result.status_code == 200:
        intent = result.json()
        result = client.post('/api/human-actions/'+intent['id']+'/confirm', json={'challenge':intent['challenge']})
    assert result.status_code == 409 and result.json()['error']['code'] == 'PAYMENT_OVERFLOW'
    payment, iid = subject(client, data[0], 'supplier_payment',
        {'stage_id':stage_id,'amount':'70','currency':'CNY'}, 'outsource')
    approve(client, iid)
    command(client, 'finance.confirm', payment['id'], {'amount':'70','currency':'CNY',
        'paid_date':now().date().isoformat(),'reference':'REPLACEMENT-FINAL','evidence':'实际尾款凭证'})
    with data[1]() as db:
        analysis = execute(db, db.get(m.User,data[0]['admin']), 'query_finance_context',
            {'project_id':data[0]['project']})['model_context']
        balance = next(row for row in analysis['current_effective_contract_balances']['supplier_payable']
                       if row['contract_id'] == target)
        assert Decimal(balance['confirmed_settlement_amount']) == 100
        assert Decimal(balance['outstanding_amount']) == 0


def test_replacement_cannot_use_old_payment_authorization(client, data, correction_case):
    _, payment_id = correction_case
    _, _, iid = replacement(client, data, original_contract_id(data, payment_id))
    approve(client, iid)
    intent = checked(client.post('/api/business/command-intents',json={'action':'finance.confirm',
        'resource_id':payment_id,'payload':{'amount':'1','currency':'CNY','paid_date':now().date().isoformat(),
        'reference':'OLD-AUTH','evidence':'不能按失效合同继续支付'}}))
    response = client.post('/api/human-actions/'+intent['id']+'/confirm',json={'challenge':intent['challenge']})
    assert response.status_code == 409 and response.json()['error']['code'] == 'SOURCE_INVALID'


def test_confirmation_refreshes_authorization_after_other_session_pays(client, data, correction_case):
    _, payment_id = correction_case
    from domain_packs.mold.erp.core.domain_commands import Payment, validate_supplier_payment
    with data[1]() as stale:
        request = stale.get(m.PaymentRequestDetail,payment_id)
        payment = stale.get(m.BusinessSubject,payment_id)
        assert request.reservation == 50
        command(client,'finance.confirm',payment_id,{'amount':'30','currency':'CNY',
            'paid_date':now().date().isoformat(),'reference':'CONCURRENT-PAY','evidence':'另一会话真实确认'})
        payload = Payment(amount='30',currency='CNY',paid_date=now().date(),reference='STALE-PAY',evidence='过期占用')
        with pytest.raises(DomainError) as error:
            validate_supplier_payment(stale,payment,payload)
        assert error.value.code == 'PAYMENT_OVERFLOW'
        assert request.reservation == 20


def test_post_replacement_reversal_updates_current_balance_without_overwriting_allocation(client, data, correction_case):
    args, payment_id = correction_case
    old_id = original_contract_id(data, payment_id)
    target, stage_id, iid = replacement(client, data, old_id)
    approve(client, iid)
    step = prepared_step(data, args)
    intent = checked(client.post('/api/finance-proposals/'+step+'/intent'))
    assert intent['display']['当前合同分配影响'][0]['target_contract_id'] == target
    submitted = confirm(client, intent)
    with data[1]() as db:
        original_allocation = db.scalar(select(m.ContractSettlementAllocation).where(
            m.ContractSettlementAllocation.target_contract_id == target))
        allocation_id = original_allocation.id
        assert db.get(m.ApprovalInstance,submitted['instance_id']).snapshot['detail']['allocation_effects'][0]['allocation_id'] == allocation_id
    assert approve(client, submitted['instance_id'])['business_status'] == 'EFFECTIVE'
    with data[1]() as db:
        rows = list(db.scalars(select(m.ContractSettlementAllocation).where(
            m.ContractSettlementAllocation.target_contract_id == target)))
        assert len(rows) == 2 and sum(row.amount for row in rows) == 0
        assert db.get(m.ContractSettlementAllocation, allocation_id).amount == 30
        negative = next(row for row in rows if row.amount < 0)
        reversal = db.get(m.PaymentConfirmation, negative.source_record_id)
        assert reversal.reversal_of_id == args['original_payment_id']
        assert negative.target_stage_id == stage_id and negative.source_contract_id == old_id
        result = execute(db, db.get(m.User,data[0]['admin']), 'query_finance_context',
                         {'project_id':data[0]['project']})['model_context']
        balance = next(row for row in result['current_effective_contract_balances']['supplier_payable'] if row['contract_id'] == target)
        assert Decimal(balance['confirmed_settlement_amount']) == 0
        assert Decimal(balance['outstanding_amount']) == 100
        assert Decimal(result['supplier_payment_summary']['confirmed_totals'][0]['amount']) == 0
        request = next(row for row in result['supplier_payment_summary']['requests'] if row['id'] == payment_id)
        assert request['contract_status'] == 'CLOSED' and request['payment_execution_eligible'] is False
    # A later replacement consumes original cash and reversal once each.
    next_target, _, iid = replacement(client, data, target)
    assert approve(client, iid)['business_status'] == 'EFFECTIVE'
    with data[1]() as db:
        rows = list(db.scalars(select(m.ContractSettlementAllocation).where(
            m.ContractSettlementAllocation.target_contract_id == next_target)))
        assert len(rows) == 2 and sum(row.amount for row in rows) == 0
        from domain_packs.mold import domains
        history = domains.typed_detail(db, db.get(m.BusinessSubject, submitted['subject_id']))
        assert history['allocation_effects'][0]['target_contract_id'] == target


def test_pending_replacement_cannot_ignore_a_later_reversal(client, data, correction_case):
    args, payment_id = correction_case
    _, _, replacement_iid = replacement(client, data, original_contract_id(data, payment_id))
    step = prepared_step(data, args)
    correction = confirm(client, checked(client.post('/api/finance-proposals/'+step+'/intent')))
    approve(client, correction['instance_id'])
    assert approve(client, replacement_iid)['business_status'] == 'APPLY_BLOCKED'
    with data[1]() as db:
        assert db.get(m.ApprovalInstance, replacement_iid).incident == 'CONTRACT_SETTLEMENT_ALLOCATION_INCOMPLETE'


def test_pending_old_payment_approval_cannot_activate_after_replacement(client, data, correction_case):
    _, payment_id = correction_case
    with data[1]() as db:
        stage_id = db.get(m.PaymentRequestDetail, payment_id).stage_id
    pending, payment_iid = subject(client, data[0], 'supplier_payment',
        {'stage_id':stage_id,'amount':'20','currency':'CNY'}, 'outsource')
    _, _, replacement_iid = replacement(client, data, original_contract_id(data, payment_id))
    approve(client, replacement_iid)
    assert approve(client, payment_iid)['business_status'] == 'APPLY_BLOCKED'
    with data[1]() as db:
        assert db.get(m.ApprovalInstance,payment_iid).incident == 'SOURCE_INVALID'
        assert not db.scalar(select(m.PaymentConfirmation.id).where(m.PaymentConfirmation.request_id == pending['id']))


def test_reversal_approval_rechecks_replacement_basis(client, data, correction_case):
    args, payment_id = correction_case
    step = prepared_step(data, args)
    submitted = confirm(client, checked(client.post('/api/finance-proposals/'+step+'/intent')))
    target, _, iid = replacement(client, data, original_contract_id(data, payment_id))
    approve(client, iid)
    assert approve(client, submitted['instance_id'])['business_status'] == 'APPLY_BLOCKED'
    with data[1]() as db:
        assert db.get(m.ApprovalInstance,submitted['instance_id']).incident == 'CORRECTION_ALLOCATION_CHANGED'
        assert not db.scalar(select(m.PaymentConfirmation.id).where(m.PaymentConfirmation.reversal_of_id == args['original_payment_id']))
        assert db.get(m.PaymentRequestDetail,payment_id).reservation == 50


def test_replacement_receipt_limits_include_allocated_history_at_stage_and_contract(client, data):
    ids, factory = data
    sign_in(client)
    with factory.begin() as db:
        customer = m.Customer(code='REPLACE-C',name='合成客户')
        db.add(customer);db.flush();customer_id = customer.id
    old, iid = subject(client, ids, 'sales_contract', {'customer_id':customer_id,'amount':'100','currency':'CNY',
        'contract_number':'RCPT-ORIGINAL','stages':[{'name':'原首款','amount':'100','condition':'合同生效'}]})
    approve(client, iid)
    payload = {'amount':'30','currency':'CNY','received_date':now().date().isoformat(),
               'reference':'RCPT-HISTORY','evidence':'原合同实际回单'}
    command(client,'customer_receipt.confirm',old['id'],payload)
    target, stage_id, iid = replacement(client, data, old['id'],stage_amount='60')
    approve(client, iid)
    for amount, target_stage, code in [('31',stage_id,'RECEIPT_STAGE_OVERFLOW'),('71',None,'RECEIPT_CONTRACT_OVERFLOW')]:
        intent = checked(client.post('/api/business/command-intents',json={'action':'customer_receipt.confirm',
            'resource_id':target,'payload':{**payload,'amount':amount,'stage_id':target_stage,'reference':'OVER-'+amount}}))
        response = client.post('/api/human-actions/'+intent['id']+'/confirm',json={'challenge':intent['challenge']})
        assert response.status_code == 409 and response.json()['error']['code'] == code
    command(client,'customer_receipt.confirm',target,{**payload,'amount':'30','stage_id':stage_id,'reference':'RCPT-NODE'})
    command(client,'customer_receipt.confirm',target,{**payload,'amount':'40','reference':'RCPT-REST'})
    with factory() as db:
        analysis = execute(db,db.get(m.User,ids['admin']),'query_finance_context',{'project_id':ids['project']})['model_context']
        assert Decimal(analysis['current_effective_contract_balances']['customer_receivable'][0]['outstanding_amount']) == 0
