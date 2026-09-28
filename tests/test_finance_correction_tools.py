from decimal import Decimal

import pytest
from sqlalchemy import select, func

from app import models as m
from app.authorization import fingerprint
from app.db import now
from app.errors import DomainError
from app.tool_gateway import execute, tool_schema
from conftest import sign_in
from test_domains import checked, subject, approve, command, workflow, confirm


@pytest.fixture
def correction_case(client, data):
    ids, factory = data
    sign_in(client)
    today = now().date().isoformat()
    with factory.begin() as db:
        db.add(m.ProjectProfile(project_id=ids['project'], owner_user_id=ids['admin'], execution_mode='FULL_OUTSOURCE'))
    supplier = checked(client.post('/api/master/suppliers', json={'code':'REVERSAL-SUP','name':'合成冲正供应商','category':'outsource'}))
    contract, iid = subject(client, ids, 'full_outsource_contract', {'supplier_id':supplier['id'],
        'amount':'100','currency':'CNY','contract_number':'REVERSAL-C1',
        'stages':[{'name':'阶段款','amount':'100','condition':'经核对支付'}]}, 'outsource')
    approve(client, iid)
    stage = checked(client.get('/api/business/subjects/'+contract['id']))['detail']['stages'][0]
    command(client, 'finance.condition', stage['id'], {'evidence':'合成条件依据'})
    payment, iid = subject(client, ids, 'supplier_payment', {'stage_id':stage['id'],'amount':'80','currency':'CNY'}, 'outsource')
    approve(client, iid)
    paid = command(client, 'finance.confirm', payment['id'], {'amount':'30','currency':'CNY',
        'paid_date':today,'reference':'PAY-REVERSAL','evidence':'合成实付凭证'})
    definition = workflow(client, ids, 'finance_correction')
    args = {'project_id':ids['project'], 'project_version':1,
        'original_payment_id':paid['payment_confirmation_id'], 'reason':'错误确认已实际冲回',
        'reversal_evidence':'经人工核对的实际冲回凭证', 'reversal_date':today, 'workflow_definition_id':definition}
    with factory() as db:
        args['project_version'] = db.get(m.Project, ids['project']).row_version
    return args, payment['id']


def prepared_step(data, args):
    ids, factory = data
    with factory.begin() as db:
        actor = db.get(m.User, ids['admin'])
        conversation = m.Conversation(user_id=actor.id,title='合成付款冲正会话')
        db.add(conversation)
        db.flush()
        run = m.Run(conversation_id=conversation.id,user_id=actor.id,security_version=actor.security_version,
            prompt='准备付款冲正审批',status='SUCCEEDED',
            checkpoint={'authorization_hash':fingerprint(db,actor),'agent_permission_mode':'ask'})
        db.add(run)
        db.flush()
        result = execute(db, actor, 'prepare_finance_correction', args, run=run)
        assert result['proposal']['requires_approval'] is True
        assert db.scalar(select(func.count()).select_from(m.FinanceCorrectionDetail)) == 0
        step = m.Step(run_id=run.id,sequence=0,tool='prepare_finance_correction',request_hash='synthetic',result=result)
        db.add(step)
        db.flush()
        return step.id


@pytest.mark.parametrize('project_status', ['ACTIVE','PAUSED','TERMINATED','CLOSED'])
def test_correction_card_submits_then_human_approval_reverses_once(client, data, correction_case, project_status):
    args, payment_id = correction_case
    ids, factory = data
    with factory.begin() as db:
        db.get(m.Project, ids['project']).status = project_status
    step_id = prepared_step(data, args)
    intent = checked(client.post('/api/finance-proposals/'+step_id+'/intent'))
    assert intent['display']['原付款金额'] == '30.00 CNY'
    assert intent['display']['拟冲正金额'] == '-30.00 CNY'
    receipt = confirm(client, intent)
    assert receipt['status'] == 'SUBMITTED'
    assert checked(client.get('/api/finance-proposals/'+step_id))['receipt'] == receipt
    assert confirm(client, intent) == receipt
    with factory() as db:
        instance = db.get(m.ApprovalInstance, receipt['instance_id'])
        assert Decimal(instance.snapshot['amount']) == 30
        assert instance.snapshot['currency'] == 'CNY'
        assert instance.snapshot['detail']['original_payment']['id'] == args['original_payment_id']
        assert db.scalar(select(func.count()).select_from(m.PaymentConfirmation)) == 1
        assert db.get(m.PaymentRequestDetail, payment_id).reservation == Decimal('50')
    assert approve(client, receipt['instance_id'])['business_status'] == 'EFFECTIVE'
    with factory() as db:
        payments = list(db.scalars(select(m.PaymentConfirmation).where(m.PaymentConfirmation.request_id == payment_id)))
        assert len(payments) == 2 and sum(row.amount for row in payments) == 0
        reversal = next(row for row in payments if row.amount < 0)
        assert reversal.reversal_of_id == args['original_payment_id']
        assert db.get(m.PaymentRequestDetail, payment_id).reservation == Decimal('80')
        assert db.get(m.Project, ids['project']).status == project_status
        with pytest.raises(DomainError) as repeated:
            execute(db, db.get(m.User, ids['admin']), 'prepare_finance_correction', args)
        assert repeated.value.code == 'ALREADY_REVERSED'


def test_finance_query_keeps_payment_ids_amounts_and_workflow_options_in_model_context(client, data, correction_case):
    args, payment_id = correction_case
    ids, factory = data
    assert 'workflow_definition_id' in tool_schema('prepare_finance_correction')['function']['parameters']['properties']
    with factory() as db:
        result = execute(db, db.get(m.User,ids['admin']), 'query_finance_context', {'project_id':ids['project']})
        context = result['model_context']
        assert context['project']['id'] == ids['project']
        assert context['project']['row_version'] == args['project_version']
        assert context['supplier_payment_summary'] == result['data'][0]['analysis']['supplier_payment_summary']
        options = next(row for row in context['finance_correction_options'] if row['payment_request_id'] == payment_id)
        assert args['workflow_definition_id'] in {row['id'] for row in options['workflows']}
        payments = context['supplier_payment_summary']['requests'][0]['payment_confirmations']
        assert payments[0]['id'] == args['original_payment_id']
        assert Decimal(payments[0]['amount']) == 30


def test_competing_correction_cards_stop_at_existing_pending_request(client, data, correction_case):
    args, _ = correction_case
    first = prepared_step(data, args)
    second = prepared_step(data, args)
    intent1 = checked(client.post('/api/finance-proposals/'+first+'/intent'))
    intent2 = checked(client.post('/api/finance-proposals/'+second+'/intent'))
    confirm(client, intent1)
    response = client.post('/api/human-actions/'+intent2['id']+'/confirm',json={'challenge':intent2['challenge']})
    assert response.status_code == 409 and response.json()['error']['code'] == 'CORRECTION_PENDING'
    with data[1]() as db:
        assert db.scalar(select(func.count()).select_from(m.FinanceCorrectionDetail)) == 1
        assert db.scalar(select(func.count()).select_from(m.PaymentConfirmation)) == 1


def test_correction_preview_rejects_wrong_workflow_or_stale_project(client, data, correction_case):
    args, _ = correction_case
    wrong = workflow(client, data[0], 'supplier_payment')
    with data[1]() as db:
        actor = db.get(m.User, data[0]['admin'])
        for changes, code in [({'workflow_definition_id':wrong},'WORKFLOW_MISMATCH'),
                              ({'project_version':args['project_version']+1},'VERSION_CONFLICT')]:
            with pytest.raises(DomainError) as rejected:
                execute(db, actor, 'prepare_finance_correction', {**args, **changes})
            assert rejected.value.code == code


def test_rejected_correction_keeps_original_payment_and_allows_new_proposal(client, data, correction_case):
    args, payment_id = correction_case
    step = prepared_step(data, args)
    submitted = confirm(client, checked(client.post('/api/finance-proposals/'+step+'/intent')))
    approve(client, submitted['instance_id'], 'REJECT')
    with data[1]() as db:
        assert db.scalar(select(func.count()).select_from(m.PaymentConfirmation)) == 1
        assert db.get(m.PaymentRequestDetail, payment_id).reservation == Decimal('50')
        assert db.get(m.BusinessSubject, submitted['subject_id']).status == 'REJECTED'
        result = execute(db, db.get(m.User,data[0]['admin']), 'prepare_finance_correction', args)
        assert result['proposal']['kind'] == 'finance_correction'


def test_finance_receipt_read_does_not_reopen_unconfirmed_stopped_run(client, data, correction_case):
    args, _ = correction_case
    step = prepared_step(data, args)
    with data[1].begin() as db:
        db.get(m.Run, db.get(m.Step, step).run_id).status = 'CANCELLED'
    response = client.get('/api/finance-proposals/'+step)
    assert response.status_code == 409 and response.json()['error']['code'] == 'PROPOSAL_STOPPED'
    sign_in(client, 'test_buyer')
    assert client.get('/api/finance-proposals/'+step).status_code == 404
