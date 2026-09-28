from copy import deepcopy
from datetime import date
from decimal import Decimal

import pytest
from sqlalchemy import select, func

from app import models as m
from app.authorization import fingerprint
from app.errors import DomainError
from app.tool_gateway import execute
from conftest import sign_in
from test_domains import checked, subject, approve, workflow, confirm


@pytest.fixture
def payment_case(client, data):
    ids, factory = data
    sign_in(client)
    with factory.begin() as db:
        db.add(m.ProjectProfile(project_id=ids['project'], owner_user_id=ids['admin'], execution_mode='FULL_OUTSOURCE'))
    supplier = checked(client.post('/api/master/suppliers', json={'code':'REQUEST-SUP','name':'合成付款供应商','category':'outsource'}))
    contract, iid = subject(client, ids, 'full_outsource_contract', {'supplier_id':supplier['id'],
        'amount':'100','currency':'CNY','contract_number':'REQUEST-C1',
        'stages':[{'name':'阶段款','amount':'100','condition':'完成合同阶段验收','payment_type':'ACCEPTANCE',
            'condition_rules':[{'key':'ACCEPTANCE','requirement':'阶段验收原件已核对'},
                               {'key':'INVOICE','requirement':'发票已收到','applicable':False}]}]}, 'outsource')
    approve(client, iid)
    stage = checked(client.get('/api/business/subjects/'+contract['id']))['detail']['stages'][0]
    definition = workflow(client, ids, 'supplier_payment')
    with factory() as db:
        version = db.get(m.Project, ids['project']).row_version
    return {'project_id':ids['project'], 'project_version':version, 'stage_id':stage['id'],
            'amount':'80', 'currency':'CNY', 'reason':'申请本阶段合同款', 'workflow_definition_id':definition}


def prepared(data, tool, args):
    ids, factory = data
    with factory.begin() as db:
        actor = db.get(m.User, ids['admin'])
        conversation = m.Conversation(user_id=actor.id, title='合成付款申请会话')
        db.add(conversation);db.flush()
        run = m.Run(conversation_id=conversation.id, user_id=actor.id, security_version=actor.security_version,
            prompt='按本次资料办理付款条件或申请', status='SUCCEEDED',
            checkpoint={'authorization_hash':fingerprint(db,actor),'agent_permission_mode':'ask'})
        db.add(run);db.flush()
        result = execute(db, actor, tool, args, run=run)
        step = m.Step(run_id=run.id, sequence=0, tool=tool, request_hash='synthetic-payment', result=result)
        db.add(step);db.flush();return step.id


def submit(client, data, args):
    step = prepared(data, 'prepare_supplier_payment_request', args)
    intent = checked(client.post('/api/finance-proposals/'+step+'/intent'))
    receipt = confirm(client, intent)
    assert confirm(client, intent) == receipt
    assert checked(client.get('/api/finance-proposals/'+step))['receipt'] == receipt
    return receipt


def check_condition(client, data, args):
    values = {k:args[k] for k in ('project_id','project_version','stage_id')}
    step = prepared(data, 'prepare_supplier_payment_condition', {**values,
        'evidence':'阶段验收原件已由财务核对',
        'evidence_by_rule':{'ACCEPTANCE':'阶段验收原件已由财务核对'}})
    with data[1]() as db:
        assert not db.get(m.PaymentStage,args['stage_id']).condition_confirmed
    intent = checked(client.post('/api/finance-proposals/'+step+'/intent'))
    receipt = confirm(client, intent)
    assert receipt['status'] == 'CONFIRMED'
    assert confirm(client, intent) == receipt
    with data[1]() as db:
        stage = db.get(m.PaymentStage,args['stage_id'])
        assert stage.condition_evidence_map == {'ACCEPTANCE':'阶段验收原件已由财务核对'}


def test_condition_matrix_requires_each_applicable_rule_and_special_approval(client,data,payment_case):
    args = payment_case
    values = {k:args[k] for k in ('project_id','project_version','stage_id')}
    with data[1].begin() as db:
        stage = db.get(m.PaymentStage,args['stage_id'])
        stage.condition_profile = {'payment_type':'ACCEPTANCE','rules':[
            {'key':'ACCEPTANCE','requirement':'验收原件','applicable':True,'special_approval_required':False},
            {'key':'INVOICE','requirement':'发票偏离','applicable':True,'special_approval_required':True},
            {'key':'DELIVERY','requirement':'不适用交付','applicable':False,'special_approval_required':False},
        ]}
    with data[1]() as db:
        actor = db.get(m.User,data[0]['admin'])
        with pytest.raises(DomainError) as error:
                execute(db,actor,'prepare_supplier_payment_condition',{**values,'evidence':'总依据',
                'evidence_by_rule':{'ACCEPTANCE':'验收依据','INVOICE':'偏离依据'}})
        assert error.value.code == 'PAYMENT_SPECIAL_APPROVAL_REQUIRED'
    # The card preparation is valid once the special reference is present;
    # confirmation will persist the structured evidence below.
    step = prepared(data,'prepare_supplier_payment_condition',{**values,'evidence':'总依据',
        'evidence_by_rule':{'ACCEPTANCE':'验收依据','INVOICE':'偏离依据'},
        'special_approval_reference':'SPECIAL-001'})
    intent = checked(client.post('/api/finance-proposals/'+step+'/intent'))
    receipt = confirm(client,intent)
    assert receipt['status'] == 'CONFIRMED'


def test_wrong_payment_workflow_returns_authorized_choices_for_recovery(client, data, payment_case):
    args = payment_case
    check_condition(client, data, args)
    wrong = {**args, 'workflow_definition_id': '00000000-0000-0000-0000-000000000001'}
    with data[1]() as db:
        actor = db.get(m.User, data[0]['admin'])
        with pytest.raises(DomainError) as caught:
            execute(db, actor, 'prepare_supplier_payment_request', wrong)
        error = caught.value
        assert error.code == 'WORKFLOW_MISMATCH'
        assert error.details['expected_business_type'] == 'supplier_payment'
        assert error.details['available_workflows']
        assert all(row['business_type'] == 'supplier_payment'
                   for row in error.details['available_workflows'])


def test_special_condition_requires_workflow_that_declares_special_approval(client, data, payment_case):
    args = payment_case
    values = {k: args[k] for k in ('project_id', 'project_version', 'stage_id')}
    with data[1].begin() as db:
        stage = db.get(m.PaymentStage, args['stage_id'])
        stage.condition_profile = {'payment_type': 'ACCEPTANCE', 'rules': [
            {'key': 'ACCEPTANCE', 'requirement': '验收原件', 'applicable': True},
            {'key': 'INVOICE', 'requirement': '发票偏离', 'applicable': True,
             'special_approval_required': True},
        ]}
    condition_step = prepared(data, 'prepare_supplier_payment_condition', {
        **values, 'evidence': '总依据',
        'evidence_by_rule': {'ACCEPTANCE': '验收依据', 'INVOICE': '偏离依据'},
        'special_approval_reference': 'SPECIAL-002',
    })
    condition_intent = checked(client.post('/api/finance-proposals/'+condition_step+'/intent'))
    confirm(client, condition_intent)
    with data[1].begin() as db:
        actor = db.get(m.User, data[0]['admin'])
        with pytest.raises(DomainError) as error:
            execute(db, actor, 'prepare_supplier_payment_request', args)
        assert error.value.code == 'PAYMENT_SPECIAL_APPROVAL_WORKFLOW_REQUIRED'
        assert error.value.details['special_approval_reference'] == 'SPECIAL-002'
        definition = db.get(m.WorkflowDefinition, args['workflow_definition_id'])
        definition.config = {**definition.config, 'supports_special_approval': True}
    with data[1]() as db:
        actor = db.get(m.User, data[0]['admin'])
        proposal = execute(db, actor, 'prepare_supplier_payment_request', args)
        assert proposal['proposal']['display']['特殊审批依据'] == 'SPECIAL-002'


def test_condition_matrix_missing_rule_is_rejected_before_card(client,data,payment_case):
    args = payment_case
    values = {k:args[k] for k in ('project_id','project_version','stage_id')}
    with data[1]() as db:
        actor = db.get(m.User,data[0]['admin'])
        with pytest.raises(DomainError) as error:
            execute(db,actor,'prepare_supplier_payment_condition',{**values,'evidence':'只有总说明'})
        assert error.value.code == 'PAYMENT_CONDITION_MISSING'


def test_condition_request_approval_and_actual_payment_are_separate(client, data, payment_case):
    args = payment_case
    with data[1]() as db:
        actor = db.get(m.User,data[0]['admin'])
        with pytest.raises(DomainError) as error:
            execute(db, actor, 'prepare_supplier_payment_request', args)
        assert error.value.code == 'PAYMENT_CONDITION'
        context = execute(db, actor, 'query_finance_context', {'project_id':args['project_id']})['model_context']
        options = context['supplier_payment_options'][0]
        assert options['stages'][0]['stage_id'] == args['stage_id']
        assert options['stages'][0]['condition_profile']['payment_type'] == 'ACCEPTANCE'
        assert options['stages'][0]['condition_profile']['rules'][1]['applicable'] is False
        assert args['workflow_definition_id'] in {x['id'] for x in options['workflows']}
    check_condition(client, data, args)
    with data[1]() as db:
        assert db.scalar(select(func.count()).select_from(m.PaymentRequestDetail)) == 0
    receipt = submit(client, data, args)
    with data[1]() as db:
        assert db.get(m.BusinessSubject,receipt['subject_id']).status == 'SUBMITTED'
        assert db.get(m.PaymentRequestDetail,receipt['subject_id']).reservation == Decimal('80')
        assert db.scalar(select(func.count()).select_from(m.PaymentConfirmation)) == 0
        basis = db.get(m.ApprovalInstance,receipt['instance_id']).snapshot['detail']['payment_basis']
        assert basis['stage_name'] == '阶段款' and basis['stage_amount'] == '100.00'
        assert basis['contract_number'] == 'REQUEST-C1' and basis['supplier_name'] == '合成付款供应商'
        assert basis['condition_evidence'] == '阶段验收原件已由财务核对'
    assert approve(client, receipt['instance_id'])['business_status'] == 'EFFECTIVE'
    with data[1]() as db:
        assert db.scalar(select(func.count()).select_from(m.PaymentConfirmation)) == 0
        context = execute(db,db.get(m.User,data[0]['admin']), 'query_finance_context', {'project_id':args['project_id']})['model_context']
        assert context['supplier_payment_summary']['requests'][0]['revision'] == 1
    payment_args = {k:args[k] for k in ('project_id','project_version','amount','currency')}
    payment_step = prepared(data, 'prepare_supplier_payment_confirmation', {**payment_args,
        'payment_subject_id':receipt['subject_id'], 'paid_date':date.today().isoformat(),
        'reference':'SYNTHETIC-REQUEST-PAID-001', 'evidence':'合成付款回单'})
    payment_intent = checked(client.post('/api/finance-proposals/'+payment_step+'/intent'))
    paid = confirm(client, payment_intent)
    assert paid['status'] == 'CONFIRMED' and confirm(client, payment_intent) == paid
    with data[1]() as db:
        assert db.get(m.PaymentRequestDetail,receipt['subject_id']).reservation == 0
        actual = db.scalars(select(m.PaymentConfirmation)).all()
        assert len(actual) == 1 and actual[0].amount == Decimal('80')


@pytest.mark.parametrize('decision,status', [('RETURN','RETURNED'),('REJECT','REJECTED')])
def test_returned_payment_revises_same_subject_and_full_approval_round(client, data, payment_case, decision, status):
    args = payment_case
    check_condition(client,data,args)
    original = submit(client,data,args)
    assert approve(client,original['instance_id'],decision)['business_status'] == status
    with data[1]() as db:
        old_snapshot = deepcopy(db.get(m.ApprovalInstance, original['instance_id']).snapshot)
        assert db.get(m.PaymentRequestDetail,original['subject_id']).reservation == 0
    revised_args = {**args,'amount':'60','existing_subject_id':original['subject_id'],
        'subject_revision':1,'revision_reason':'按财务意见重新核对金额','reason':'修订申请依据'}
    revised = submit(client,data,revised_args)
    assert revised['subject_id'] == original['subject_id']
    assert revised['subject_revision'] == 2 and revised['round_no'] == 2
    with data[1]() as db:
        assert db.get(m.ApprovalInstance,original['instance_id']).snapshot == old_snapshot
        assert db.get(m.ApprovalInstance,original['instance_id']).status == status
        assert db.get(m.PaymentRequestDetail,revised['subject_id']).reservation == Decimal('60')
        assert db.scalar(select(func.count()).select_from(m.PaymentRequestDetail)) == 1
    assert approve(client,revised['instance_id'])['business_status'] == 'EFFECTIVE'
    with data[1]() as db:
        assert db.get(m.ApprovalInstance,original['instance_id']).snapshot == old_snapshot
        assert db.scalar(select(m.AuditEvent.id).where(m.AuditEvent.action=='business.revised',m.AuditEvent.resource_id==revised['subject_id']))


def test_competing_payment_cards_recheck_balance_before_reserving(client,data,payment_case):
    args=payment_case
    check_condition(client,data,args)
    competing=prepared(data,'prepare_supplier_payment_request',args)
    intent=checked(client.post('/api/finance-proposals/'+competing+'/intent'))
    first=submit(client,data,args)
    response=client.post('/api/human-actions/'+intent['id']+'/confirm',json={'challenge':intent['challenge']})
    assert response.status_code == 409 and response.json()['error']['code'] == 'PAYMENT_OVERFLOW'
    # A stale card cannot reuse the pre-reservation balance.
    with data[1]() as db:
        from domain_packs.mold.tools.erp.finance import supplier_payment_tools as payment
        with pytest.raises(DomainError) as error:
            payment.preview(db,db.get(m.User,data[0]['admin']),'supplier_payment_request',payment.parse('supplier_payment_request',args))
        assert error.value.code=='PAYMENT_OVERFLOW'
        assert db.get(m.PaymentRequestDetail,first['subject_id']).reservation==80
        assert db.scalar(select(func.count()).select_from(m.PaymentRequestDetail)) == 1
    assert client.post('/api/finance-proposals/'+competing+'/intent').status_code==409


def test_effective_or_running_payment_cannot_be_revised(client,data,payment_case):
    args=payment_case
    check_condition(client,data,args)
    original=submit(client,data,args)
    revised={**args,'existing_subject_id':original['subject_id'],'subject_revision':1,'revision_reason':'修改'}
    for effective in (False,True):
        if effective:approve(client,original['instance_id'])
        with data[1]() as db:
            with pytest.raises(DomainError) as error:
                execute(db,db.get(m.User,data[0]['admin']),'prepare_supplier_payment_request',revised)
            assert error.value.code=='FORM_REVISION_NOT_ALLOWED'


def test_revision_failure_rolls_back_amount_reservation_and_history(client,data,payment_case,monkeypatch):
    args = payment_case
    check_condition(client,data,args)
    original = submit(client,data,args)
    approve(client,original['instance_id'],'RETURN')
    step = prepared(data,'prepare_supplier_payment_request',{**args,'amount':'60',
        'existing_subject_id':original['subject_id'],'subject_revision':1,'revision_reason':'复核金额'})
    intent = checked(client.post('/api/finance-proposals/'+step+'/intent'))
    from domain_packs.mold.erp.core import business
    def fail_enter(*_args,**_kwargs):
        raise DomainError('ASSIGNMENT_BLOCKED','模拟审批人员失效',409)
    monkeypatch.setattr(business,'enter_stage',fail_enter)
    response = client.post('/api/human-actions/'+intent['id']+'/confirm',json={'challenge':intent['challenge']})
    assert response.status_code == 409 and response.json()['error']['code'] == 'ASSIGNMENT_BLOCKED'
    with data[1]() as db:
        current = db.get(m.BusinessSubject,original['subject_id'])
        detail = db.get(m.PaymentRequestDetail,current.id)
        assert current.status == 'RETURNED' and current.revision == 1 and current.round_no == 1
        assert detail.amount == 80 and detail.reservation == 0
        assert db.scalar(select(func.count()).select_from(m.ApprovalInstance).where(m.ApprovalInstance.resource_id == current.id)) == 1
        assert not db.scalar(select(m.AuditEvent.id).where(m.AuditEvent.action == 'business.revised'))


def test_request_requires_full_material_access_and_original_applicant(client,data,payment_case):
    args = payment_case
    check_condition(client,data,args)
    original = submit(client,data,args)
    approve(client,original['instance_id'],'RETURN')
    from test_finance_context_tools import user, grant, capability
    with data[1].begin() as db:
        admin = db.get(m.User,data[0]['admin']);other = user(db,'payment-other')
        for permission in ['project.read','full_outsource_contract.read','supplier_payment.read',
                           'supplier_payment.create','supplier_payment.submit']:
            grant(db,admin,other,permission,project_id=args['project_id'])
        capability(db,other,'prepare_supplier_payment_request')
        db.flush()
        revised = {**args,'existing_subject_id':original['subject_id'],'subject_revision':1,'revision_reason':'更正'}
        with pytest.raises(DomainError) as error:
            execute(db,other,'prepare_supplier_payment_request',revised)
        assert error.value.code == 'FORM_REVISION_FORBIDDEN'
        db.get(m.BusinessSubject,original['subject_id']).created_by = other.id
        read_grant = db.scalar(select(m.Grant).where(m.Grant.user_id == other.id,m.Grant.permission == 'supplier_payment.read'))
        read_grant.fields = ['id','kind','project_id','status','revision']
        db.flush()
        with pytest.raises(DomainError) as error:
            execute(db,other,'prepare_supplier_payment_request',revised)
        assert error.value.code == 'FORM_REVISION_FORBIDDEN'
        with pytest.raises(DomainError) as error:
            execute(db,other,'prepare_supplier_payment_request',args)
        assert error.value.code == 'PAYMENT_MATERIAL_FORBIDDEN'


def test_confirm_refreshes_contract_loaded_before_concurrent_replacement(client,data,payment_case):
    args = payment_case
    check_condition(client,data,args)
    from domain_packs.mold.tools.erp.finance import supplier_payment_tools as payment
    with data[1]() as db:
        actor = db.get(m.User,data[0]['admin'])
        contract = db.get(m.BusinessSubject,db.get(m.PaymentStage,args['stage_id']).contract_id)
        detail = db.get(m.ContractDetail,contract.id)
        parsed = payment.parse('supplier_payment_request',args)
        proposal = {'display':payment.preview(db,actor,'supplier_payment_request',parsed)}
        from test_replacement_finance_execution import replacement
        _, _, instance_id = replacement(client,data,contract.id)
        approve(client,instance_id)
        assert contract.status == 'EFFECTIVE' and detail.material_version == 1
        with pytest.raises(DomainError) as error:
            payment.confirm(db,actor,'supplier_payment_request',parsed,proposal)
        assert error.value.code == 'SOURCE_INVALID'
        assert db.scalar(select(func.count()).select_from(m.PaymentRequestDetail)) == 0


def test_blocked_old_contract_request_moves_to_current_stage_with_full_reapproval(client,data,payment_case):
    args = payment_case
    check_condition(client,data,args)
    original = submit(client,data,args)
    from test_replacement_finance_execution import replacement
    with data[1]() as db:
        old_contract = db.get(m.PaymentStage,args['stage_id']).contract_id
    _, new_stage, contract_instance = replacement(client,data,old_contract)
    approve(client,contract_instance)
    assert approve(client,original['instance_id'])['business_status'] == 'APPLY_BLOCKED'
    with data[1]() as db:
        old_snapshot = deepcopy(db.get(m.ApprovalInstance,original['instance_id']).snapshot)
        assert db.get(m.PaymentRequestDetail,original['subject_id']).reservation == 80
        current_version = db.get(m.Project,args['project_id']).row_version
    revised_args = {**args,'project_version':current_version,'stage_id':new_stage,'amount':'60',
        'existing_subject_id':original['subject_id'],'subject_revision':1,'revision_reason':'合同替代后按新节点重新申请'}
    check_condition(client,data,revised_args)
    revised = submit(client,data,revised_args)
    assert revised['subject_id'] == original['subject_id'] and revised['round_no'] == 2
    with data[1]() as db:
        current = db.get(m.PaymentRequestDetail,original['subject_id'])
        assert current.stage_id == new_stage and current.reservation == 60
        assert db.scalar(select(func.sum(m.PaymentRequestDetail.reservation)).where(m.PaymentRequestDetail.stage_id == args['stage_id'])) is None
        previous = db.get(m.ApprovalInstance,original['instance_id'])
        assert previous.snapshot == old_snapshot and previous.incident == 'SOURCE_INVALID'
        assert previous.snapshot['detail']['payment_basis']['stage_id'] == args['stage_id']
        assert db.get(m.ApprovalInstance,revised['instance_id']).snapshot['detail']['payment_basis']['stage_id'] == new_stage
    assert approve(client,revised['instance_id'])['business_status'] == 'EFFECTIVE'
