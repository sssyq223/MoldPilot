from datetime import date
from decimal import Decimal

import pytest
from sqlalchemy import select, func, text
from sqlalchemy.exc import DBAPIError

from app import models as m, bpm, business
from app.authorization import fingerprint
from app.errors import DomainError
from app.tool_gateway import execute
from domain_packs.mold.tools.erp.finance.finance_context_tools import (
    parse_supplier_deduction_settlement, create_supplier_deduction_settlement,
)
from test_finance_context_tools import factory, seed_finance_project, project, user, grant, capability


@pytest.fixture
def scenario():
    engine, Session = factory()
    try:
        with Session.begin() as db:
            admin, p = seed_finance_project(db, 'FIN-DEDUCTION-FLOW')
            supplier = db.scalar(select(m.Supplier).where(m.Supplier.code == 'FIN-SUP'))
            contract = db.scalar(select(m.BusinessSubject).where(
                m.BusinessSubject.project_id == p.id, m.BusinessSubject.kind == 'full_outsource_contract'))
            acceptance = m.CustomerAcceptanceRecord(project_id=p.id, acceptance_type='INITIAL',
                result='FAILED', accepted_date=date.today(), issue_description='客户质量问题',
                responsibility='SUPPLIER', supplier_id=supplier.id,
                deduction_amount=Decimal('500'), currency='CNY', evidence='客户验收原件-PRIVATE', confirmed_by=admin.id)
            db.add(acceptance)
            db.flush()
            args = dict(project_id=p.id, project_version=p.row_version, supplier_id=supplier.id,
                contract_subject_id=contract.id, customer_acceptance_id=acceptance.id,
                reason='已确认供应商质量责任', responsibility='SUPPLIER', deduction_amount='3000.00',
                currency='CNY', status='RESPONSIBILITY_CONFIRMED', responsibility_evidence='双方责任确认单',
                source_ref='RESPONSIBILITY-SOURCE')
            yield db, admin, p, args
    finally:
        engine.dispose()


def prepare_intent(db, actor, args):
    conversation = m.Conversation(user_id=actor.id, title='扣款责任至结算合成验证')
    db.add(conversation)
    db.flush()
    run = m.Run(conversation_id=conversation.id, user_id=actor.id,
        security_version=actor.security_version, prompt='登记供应商扣款依据', status='SUCCEEDED',
        checkpoint={'authorization_hash':fingerprint(db, actor), 'agent_permission_mode':'ask'})
    db.add(run)
    db.flush()
    before = db.scalar(select(func.count()).select_from(m.SupplierDeductionSettlement))
    result = execute(db, actor, 'prepare_supplier_deduction_settlement', args, run=run)
    assert db.scalar(select(func.count()).select_from(m.SupplierDeductionSettlement)) == before
    step = m.Step(run_id=run.id, sequence=0, tool='prepare_supplier_deduction_settlement',
        request_hash='synthetic', result=result)
    db.add(step)
    db.flush()
    intent = business.create_intent(db, actor, 'finance.execute', step.id,
        {'step_id':step.id, 'proposal_hash':bpm.content_hash(result['proposal'])})
    return intent, result['proposal']


def follow_up(args, previous):
    return {**args, 'previous_deduction_id':previous.id, 'source_ref':None, 'status':'SETTLED',
        'settlement_reference':'ERP-RECONCILIATION-001', 'settlement_evidence':'实际结算单核对依据'}


def test_confirmed_responsibility_then_linked_settlement_keeps_history_and_money(scenario):
    db, actor, p, args = scenario
    initial_intent, initial_card = prepare_intent(db, actor, args)
    first = business.confirm_intent(db, actor, initial_intent['id'], initial_intent['challenge'])
    previous = db.get(m.SupplierDeductionSettlement, first['supplier_deduction_settlement_id'])
    assert previous.status == 'RESPONSIBILITY_CONFIRMED'
    assert initial_card['display']['关联客户验收记录'] == args['customer_acceptance_id']
    before = execute(db, actor, 'query_finance_context', {'project_id':p.id})['data'][0]['analysis']
    assert before['derived_status']['has_unsettled_supplier_deduction'] is True
    intent, card = prepare_intent(db, actor, follow_up(args, previous))
    assert card['display']['原责任确认记录'] == previous.id
    assert card['display']['原责任来源引用'] == args['source_ref']
    receipt = business.confirm_intent(db, actor, intent['id'], intent['challenge'])
    assert business.confirm_intent(db, actor, intent['id'], intent['challenge']) == receipt
    settled = db.get(m.SupplierDeductionSettlement, receipt['supplier_deduction_settlement_id'])
    assert settled.previous_deduction_id == previous.id and settled.deduction_amount == Decimal('3000')
    assert settled.customer_acceptance_id == previous.customer_acceptance_id
    db.refresh(previous)
    assert previous.status == 'RESPONSIBILITY_CONFIRMED' and previous.source_ref == 'RESPONSIBILITY-SOURCE'
    after = execute(db, actor, 'query_finance_context', {'project_id':p.id})['data'][0]['analysis']
    assert after['derived_status']['has_unsettled_supplier_deduction'] is False
    records = {row['id']:row for row in after['quality_finance_context']['supplier_deductions']['records']}
    assert records[previous.id]['is_current'] is False
    assert records[previous.id]['superseded_by_id'] == settled.id
    assert records[settled.id]['is_current'] is True
    for key in ('customer_receipt_summary','supplier_payment_summary','current_effective_contract_balances'):
        assert after[key] == before[key]
    outsource = execute(db, actor, 'query_full_outsource_context', {'project_id':p.id})['data'][0]['analysis']
    assert outsource['derived_status']['has_settled_supplier_deduction'] is True
    assert outsource['derived_status']['has_pending_supplier_deduction'] is False


@pytest.mark.parametrize('changed', [{'deduction_amount':'3001'}, {'currency':'USD'}, {'responsibility':'SHARED'}, {'reason':'改写原原因'}, {'customer_acceptance_id':None}])
def test_linked_settlement_cannot_change_confirmed_basis(scenario, changed):
    db, actor, _, args = scenario
    previous = create_supplier_deduction_settlement(db, actor, parse_supplier_deduction_settlement(args))
    with pytest.raises(DomainError) as rejected:
        execute(db, actor, 'prepare_supplier_deduction_settlement', {**follow_up(args, previous), **changed})
    assert rejected.value.code == 'DEDUCTION_BASIS_CHANGED'


def test_competing_prepared_cards_revalidate_and_database_protects_history(scenario):
    db, actor, _, args = scenario
    previous = create_supplier_deduction_settlement(db, actor, parse_supplier_deduction_settlement(args))
    next_args = follow_up(args, previous)
    first, _ = prepare_intent(db, actor, next_args)
    second, _ = prepare_intent(db, actor, {**next_args, 'settlement_reference':'ERP-RECONCILIATION-002'})
    business.confirm_intent(db, actor, first['id'], first['challenge'])
    with pytest.raises(DomainError) as rejected:
        with db.begin_nested():
            business.confirm_intent(db, actor, second['id'], second['challenge'])
    assert rejected.value.code == 'DEDUCTION_ALREADY_SETTLED'
    values = parse_supplier_deduction_settlement(next_args).model_dump(exclude={'project_version'})
    with pytest.raises(DBAPIError, match='uq_supplier_deduction_previous'):
        with db.begin_nested():
            db.add(m.SupplierDeductionSettlement(**values, confirmed_by=actor.id))
            db.flush()
    for statement in ('UPDATE supplier_deduction_settlement SET status=\'SETTLED\' WHERE id=:id',
                      'DELETE FROM supplier_deduction_settlement WHERE id=:id'):
        with pytest.raises(DBAPIError, match='supplier deduction facts are immutable'):
            with db.begin_nested():
                db.execute(text(statement), {'id':previous.id})


def test_acceptance_link_requires_same_project_and_read_permission(scenario):
    db, actor, p, args = scenario
    other = project(db, 'FOREIGN-ACCEPTANCE')
    row = m.CustomerAcceptanceRecord(project_id=other.id, acceptance_type='INITIAL',result='FAILED',
        accepted_date=date.today(), issue_description='其他项目问题', responsibility='UNKNOWN',
        evidence='OTHER-PROJECT-SECRET', confirmed_by=actor.id)
    db.add(row)
    db.flush()
    with pytest.raises(DomainError) as mismatch:
        execute(db, actor, 'prepare_supplier_deduction_settlement', {**args, 'customer_acceptance_id':row.id})
    assert mismatch.value.code == 'ACCEPTANCE_NOT_FOUND'
    operator = user(db)
    for permission in ('project.read','full_outsource_contract.read','finance.confirm'):
        grant(db, actor, operator, permission, project_id=p.id,
            category='outsource' if permission != 'project.read' else None)
    capability(db, operator, 'prepare_supplier_deduction_settlement')
    db.flush()
    with pytest.raises(DomainError) as forbidden:
        execute(db, operator, 'prepare_supplier_deduction_settlement', args)
    assert forbidden.value.status == 403 and 'PRIVATE' not in forbidden.value.message


def test_unlinked_settlement_never_resolves_an_existing_responsibility_record(scenario):
    db, actor, p, args = scenario
    previous = create_supplier_deduction_settlement(db, actor, parse_supplier_deduction_settlement(args))
    unrelated = {**follow_up(args, previous), 'previous_deduction_id':None, 'source_ref':'UNLINKED-LEGACY'}
    create_supplier_deduction_settlement(db, actor, parse_supplier_deduction_settlement(unrelated))
    result = execute(db, actor, 'query_finance_context', {'project_id':p.id})['data'][0]['analysis']
    assert result['derived_status']['has_unsettled_supplier_deduction'] is True


def test_follow_up_keeps_source_on_original_and_requires_settlement_evidence(scenario):
    db, actor, _, args = scenario
    previous = create_supplier_deduction_settlement(db, actor, parse_supplier_deduction_settlement(args))
    for change in ({'source_ref':args['source_ref']}, {'status':'RESPONSIBILITY_CONFIRMED'}, {'settlement_evidence':None}):
        with pytest.raises(DomainError) as rejected:
            execute(db, actor, 'prepare_supplier_deduction_settlement', {**follow_up(args, previous), **change})
        assert rejected.value.code == 'INVALID_TOOL_INPUT'


def test_settlement_can_reference_original_closed_contract_without_new_deduction(scenario):
    db, actor, _, args = scenario
    previous = create_supplier_deduction_settlement(db, actor, parse_supplier_deduction_settlement(args))
    contract = db.get(m.BusinessSubject, args['contract_subject_id'])
    contract.status = 'CLOSED'
    db.flush()
    with pytest.raises(DomainError) as first_registration:
        execute(db, actor, 'prepare_supplier_deduction_settlement', {**args, 'source_ref':'NEW-CLOSED-CONTRACT'})
    assert first_registration.value.code == 'CONTRACT_NOT_EFFECTIVE'
    intent, _ = prepare_intent(db, actor, follow_up(args, previous))
    receipt = business.confirm_intent(db, actor, intent['id'], intent['challenge'])
    assert db.get(m.SupplierDeductionSettlement, receipt['supplier_deduction_settlement_id']).contract_subject_id == contract.id


def test_inconsistent_imported_link_cannot_hide_responsibility(scenario):
    db, actor, p, args = scenario
    previous = create_supplier_deduction_settlement(db, actor, parse_supplier_deduction_settlement(args))
    values = parse_supplier_deduction_settlement(follow_up(args, previous)).model_dump(exclude={'project_version'})
    values['deduction_amount'] = Decimal('1')
    db.add(m.SupplierDeductionSettlement(**values, confirmed_by=actor.id))
    db.flush()
    result = execute(db, actor, 'query_finance_context', {'project_id':p.id})['data'][0]['analysis']
    assert result['derived_status']['has_unsettled_supplier_deduction'] is True
    records = result['quality_finance_context']['supplier_deductions']['records']
    assert any(not row['lineage_valid'] for row in records)
    assert result['derived_status']['has_deduction_link_conflict'] is True
    assert next(row for row in records if row['id'] == previous.id)['is_current'] is True
    outsource = execute(db, actor, 'query_full_outsource_context', {'project_id':p.id})['data'][0]['analysis']
    assert outsource['derived_status']['has_settled_supplier_deduction'] is False
