"""A changed correction basis requires a new, fully reviewed approval round."""
from copy import deepcopy

import pytest
from sqlalchemy import select, func

from app import models as m
from app.authorization import fingerprint
from app.errors import DomainError
from app.tool_gateway import execute
from test_domains import checked, approve, confirm
from test_finance_correction_tools import correction_case, prepared_step
from test_replacement_finance_execution import replacement, original_contract_id


def revision_args(args, receipt):
    return {**args, 'existing_subject_id':receipt['subject_id'],
        'subject_revision':receipt['subject_revision'], 'revision_reason':'重新核对当前依据并申请完整重审',
        'reason':'复核后的冲正原因', 'reversal_evidence':'重新核对的实际冲回原件'}


def revision_step(data, args):
    ids, factory = data
    with factory.begin() as db:
        actor = db.get(m.User,ids['admin'])
        conversation = m.Conversation(user_id=actor.id,title='修订原冲正申请')
        db.add(conversation);db.flush()
        run = m.Run(conversation_id=conversation.id,user_id=actor.id,security_version=actor.security_version,
            prompt='按当前依据修订原冲正申请并重新审批',status='SUCCEEDED',
            checkpoint={'authorization_hash':fingerprint(db,actor),'agent_permission_mode':'ask'})
        db.add(run);db.flush()
        before = db.get(m.BusinessSubject,args['existing_subject_id']).revision
        result = execute(db,actor,'prepare_finance_correction',args,run=run)
        assert db.get(m.BusinessSubject,args['existing_subject_id']).revision == before
        step = m.Step(run_id=run.id,sequence=0,tool='prepare_finance_correction',request_hash='revision',result=result)
        db.add(step);db.flush();return step.id


def submit_original(client, data, args):
    step = prepared_step(data,args)
    return confirm(client,checked(client.post('/api/finance-proposals/'+step+'/intent')))


def assert_history(factory, original, snapshot, status, incident=None):
    with factory() as db:
        previous = db.get(m.ApprovalInstance,original['instance_id'])
        assert previous.snapshot == snapshot
        assert previous.status == status and previous.incident == incident
        assert db.scalar(select(func.count()).select_from(m.BusinessSubject).where(
            m.BusinessSubject.kind == 'finance_correction')) == 1
        assert db.scalar(select(func.count()).select_from(m.ApprovalAction).where(
            m.ApprovalAction.instance_id == previous.id)) == 1


def test_blocked_correction_revises_same_subject_and_reapproves_current_allocation(client, data, correction_case):
    args, payment_id = correction_case
    original = submit_original(client,data,args)
    target, _, iid = replacement(client,data,original_contract_id(data,payment_id))
    approve(client,iid)
    assert approve(client,original['instance_id'])['business_status'] == 'APPLY_BLOCKED'
    with data[1]() as db:
        old_snapshot = deepcopy(db.get(m.ApprovalInstance,original['instance_id']).snapshot)
        context = execute(db,db.get(m.User,data[0]['admin']),'query_finance_context',{'project_id':args['project_id']})['model_context']
        row = next(row for row in context['finance_corrections'] if row['id'] == original['subject_id'])
        assert row['revision'] == original['subject_revision'] and row['status'] == 'APPLY_BLOCKED'
    step = revision_step(data,revision_args(args,original))
    intent = checked(client.post('/api/finance-proposals/'+step+'/intent'))
    assert intent['display']['原申请状态'] == 'APPLY_BLOCKED'
    assert intent['display']['原申请材料']['allocation_effects'] == []
    assert intent['display']['当前合同分配影响'][0]['target_contract_id'] == target
    revised = confirm(client,intent)
    assert revised['subject_id'] == original['subject_id']
    assert revised['instance_id'] != original['instance_id']
    assert revised['subject_revision'] == original['subject_revision']+1 and revised['round_no'] == 2
    assert confirm(client,intent) == revised
    assert checked(client.get('/api/finance-proposals/'+step))['receipt'] == revised
    with data[1]() as db:
        assert not db.scalar(select(m.PaymentConfirmation.id).where(m.PaymentConfirmation.reversal_of_id == args['original_payment_id']))
        current = db.get(m.ApprovalInstance,revised['instance_id'])
        assert current.status == 'RUNNING' and current.stage_index == 0
        assert current.snapshot['detail']['allocation_effects'][0]['target_contract_id'] == target
        audit = db.scalar(select(m.AuditEvent).where(m.AuditEvent.action == 'business.revised',
            m.AuditEvent.resource_id == original['subject_id']))
        assert audit.detail['previous_instance_id'] == original['instance_id']
        assert audit.detail['before']['detail']['reason'] == args['reason']
        assert audit.detail['after']['detail']['reason'] == '复核后的冲正原因'
    assert_history(data[1],original,old_snapshot,'COMPLETED','CORRECTION_ALLOCATION_CHANGED')
    assert approve(client,revised['instance_id'])['business_status'] == 'EFFECTIVE'
    assert_history(data[1],original,old_snapshot,'COMPLETED','CORRECTION_ALLOCATION_CHANGED')
    with data[1]() as db:
        allocations = list(db.scalars(select(m.ContractSettlementAllocation).where(
            m.ContractSettlementAllocation.target_contract_id == target)))
        assert len(allocations) == 2 and sum(row.amount for row in allocations) == 0


@pytest.mark.parametrize('decision,status',[('RETURN','RETURNED'),('REJECT','REJECTED')])
def test_returned_or_rejected_correction_preserves_old_snapshot_and_starts_full_round(client,data,correction_case,decision,status):
    args, _ = correction_case
    original = submit_original(client,data,args)
    assert approve(client,original['instance_id'],decision)['business_status'] == status
    with data[1]() as db:
        snapshot = deepcopy(db.get(m.ApprovalInstance,original['instance_id']).snapshot)
    step = revision_step(data,revision_args(args,original))
    revised = confirm(client,checked(client.post('/api/finance-proposals/'+step+'/intent')))
    assert_history(data[1],original,snapshot,status)
    assert revised['round_no'] == 2 and revised['subject_revision'] == 2
    assert approve(client,revised['instance_id'])['business_status'] == 'EFFECTIVE'
    assert_history(data[1],original,snapshot,status)


def test_revision_rejects_running_or_effective_forms_and_changed_payment(client,data,correction_case):
    args, _ = correction_case
    original = submit_original(client,data,args)
    revised_args = revision_args(args,original)
    with data[1]() as db:
        actor = db.get(m.User,data[0]['admin'])
        with pytest.raises(DomainError) as error:
            execute(db,actor,'prepare_finance_correction',revised_args)
        assert error.value.code == 'FORM_REVISION_NOT_ALLOWED'
    approve(client,original['instance_id'],'RETURN')
    with data[1]() as db:
        actor = db.get(m.User,data[0]['admin'])
        for change, code in [({'original_payment_id':'another-payment'},'CORRECTION_SOURCE_CHANGED'),
                             ({'subject_revision':999},'VERSION_CONFLICT')]:
            with pytest.raises(DomainError) as error:
                execute(db,actor,'prepare_finance_correction',{**revised_args,**change})
            assert error.value.code == code
    step = revision_step(data,revised_args)
    revised = confirm(client,checked(client.post('/api/finance-proposals/'+step+'/intent')))
    approve(client,revised['instance_id'])
    with data[1]() as db:
        with pytest.raises(DomainError) as error:
            execute(db,db.get(m.User,data[0]['admin']),'prepare_finance_correction',
                {**revised_args,'subject_revision':revised['subject_revision']})
        assert error.value.code == 'FORM_REVISION_NOT_ALLOWED'


def test_competing_revision_cards_cannot_submit_two_rounds(client,data,correction_case):
    args, _ = correction_case
    original = submit_original(client,data,args)
    approve(client,original['instance_id'],'RETURN')
    revised_args = revision_args(args,original)
    first = revision_step(data,revised_args);second = revision_step(data,revised_args)
    first_intent = checked(client.post('/api/finance-proposals/'+first+'/intent'))
    second_intent = checked(client.post('/api/finance-proposals/'+second+'/intent'))
    confirm(client,first_intent)
    response = client.post('/api/human-actions/'+second_intent['id']+'/confirm',json={'challenge':second_intent['challenge']})
    assert response.status_code == 409 and response.json()['error']['code'] == 'VERSION_CONFLICT'
    with data[1]() as db:
        assert db.scalar(select(func.count()).select_from(m.ApprovalInstance).where(
            m.ApprovalInstance.resource_id == original['subject_id'])) == 2


def test_revision_failure_rolls_back_form_and_audit(client,data,correction_case,monkeypatch):
    args, _ = correction_case
    original = submit_original(client,data,args)
    approve(client,original['instance_id'],'RETURN')
    step = revision_step(data,revision_args(args,original))
    intent = checked(client.post('/api/finance-proposals/'+step+'/intent'))
    from domain_packs.mold.erp.core import business
    def fail_enter(*_args,**_kwargs):
        raise DomainError('ASSIGNMENT_BLOCKED','模拟新轮审批人员无效',409)
    monkeypatch.setattr(business,'enter_stage',fail_enter)
    response = client.post('/api/human-actions/'+intent['id']+'/confirm',json={'challenge':intent['challenge']})
    assert response.status_code == 409 and response.json()['error']['code'] == 'ASSIGNMENT_BLOCKED'
    with data[1]() as db:
        subject = db.get(m.BusinessSubject,original['subject_id'])
        assert subject.status == 'RETURNED' and subject.revision == 1 and subject.round_no == 1
        assert db.get(m.FinanceCorrectionDetail,subject.id).reason == args['reason']
        assert not db.scalar(select(m.AuditEvent.id).where(m.AuditEvent.action == 'business.revised'))
        assert db.scalar(select(func.count()).select_from(m.ApprovalInstance).where(m.ApprovalInstance.resource_id == subject.id)) == 1


def test_revision_requires_original_applicant_and_full_material_access(client,data,correction_case):
    args, _ = correction_case
    original = submit_original(client,data,args)
    approve(client,original['instance_id'],'RETURN')
    from test_finance_context_tools import user, grant, capability
    with data[1].begin() as db:
        admin = db.get(m.User,data[0]['admin']);other = user(db,'revision-other')
        for permission in ['project.read','finance_correction.read','finance_correction.create','finance_correction.submit']:
            grant(db,admin,other,permission,project_id=args['project_id'])
        capability(db,other,'prepare_finance_correction')
        db.flush()
        with pytest.raises(DomainError) as error:
            execute(db,other,'prepare_finance_correction',revision_args(args,original))
        assert error.value.code == 'FORM_REVISION_FORBIDDEN'
        # Being the applicant still does not grant access to omitted form fields.
        db.get(m.BusinessSubject,original['subject_id']).created_by = other.id
        read_grant = db.scalar(select(m.Grant).where(m.Grant.user_id == other.id,
            m.Grant.permission == 'finance_correction.read'))
        read_grant.fields = ['id','kind','project_id','status','revision']
        db.flush()
        with pytest.raises(DomainError) as error:
            execute(db,other,'prepare_finance_correction',revision_args(args,original))
        assert error.value.code == 'FORM_REVISION_FORBIDDEN'


def test_draft_revision_submits_first_round_and_preserves_original_draft_audit(client,data,correction_case):
    args, _ = correction_case
    from domain_packs.mold.erp.core.domain_schemas import CorrectionInput
    detail = {key:args[key] for key in CorrectionInput.model_fields}
    draft = checked(client.post('/api/business/subjects',json={'kind':'finance_correction',
        'project_id':args['project_id'],'category':'outsource','remark':'原草稿','detail':detail}))
    proposed = revision_args(args,{'subject_id':draft['id'],'subject_revision':draft['revision']})
    step = revision_step(data,proposed)
    receipt = confirm(client,checked(client.post('/api/finance-proposals/'+step+'/intent')))
    assert receipt['subject_id'] == draft['id'] and receipt['round_no'] == 1 and receipt['subject_revision'] == 2
    with data[1]() as db:
        event = db.scalar(select(m.AuditEvent).where(m.AuditEvent.action == 'business.revised',
            m.AuditEvent.resource_id == draft['id']))
        assert event.detail['previous_instance_id'] is None
        assert event.detail['before']['detail']['reason'] == args['reason']
    assert approve(client,receipt['instance_id'])['business_status'] == 'EFFECTIVE'


def test_revision_arguments_require_explicit_version_and_reason(correction_case):
    from domain_packs.mold.tools.erp.finance.finance_correction_tools import parse
    args, _ = correction_case
    for extra in [{'existing_subject_id':'existing'}, {'subject_revision':1}, {'revision_reason':'理由'},
                  {'existing_subject_id':'existing','subject_revision':1,'revision_reason':' '}]:
        with pytest.raises(DomainError) as error:
            parse({**args,**extra})
        assert error.value.code == 'INVALID_TOOL_INPUT'


def test_revision_audit_cannot_bypass_finance_scope_or_field_permissions(client,data,correction_case):
    args, _ = correction_case
    original = submit_original(client,data,args)
    approve(client,original['instance_id'],'RETURN')
    step = revision_step(data,revision_args(args,original))
    confirm(client,checked(client.post('/api/finance-proposals/'+step+'/intent')))
    def audit_event():
        return next(row for row in checked(client.get('/api/audit?limit=50'))['items']
                    if row['action'] == 'business.revised' and row['resource_id'] == original['subject_id'])
    admin_event = audit_event()
    assert not admin_event['detail_redacted'] and admin_event['detail']['before']['detail']['reason'] == args['reason']
    assert '_detail_access' not in admin_event['detail']
    from test_finance_context_tools import grant
    from conftest import sign_in
    with data[1].begin() as db:
        admin = db.get(m.User,data[0]['admin']);buyer = db.get(m.User,data[0]['buyer'])
        grant(db,admin,buyer,'audit.read')
    sign_in(client,'test_buyer')
    assert audit_event()['detail_redacted'] and audit_event()['detail'] == {}
    with data[1].begin() as db:
        admin = db.get(m.User,data[0]['admin']);buyer = db.get(m.User,data[0]['buyer'])
        grant(db,admin,buyer,'finance_correction.read',project_id=data[0]['other_project'])
    assert audit_event()['detail_redacted']
    with data[1].begin() as db:
        admin = db.get(m.User,data[0]['admin']);buyer = db.get(m.User,data[0]['buyer'])
        grant(db,admin,buyer,'finance_correction.read',project_id=args['project_id'],fields=['id','status'])
    assert audit_event()['detail_redacted']
    with data[1].begin() as db:
        admin = db.get(m.User,data[0]['admin']);buyer = db.get(m.User,data[0]['buyer'])
        grant(db,admin,buyer,'finance_correction.read',project_id=args['project_id'])
    assert not audit_event()['detail_redacted']
    with data[1].begin() as db:
        for row in db.scalars(select(m.Grant).where(m.Grant.user_id == data[0]['buyer'],m.Grant.permission == 'finance_correction.read')):
            row.active = False
    assert audit_event()['detail_redacted'] and audit_event()['detail'] == {}
