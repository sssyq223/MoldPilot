from datetime import date, timedelta
from decimal import Decimal

import pytest

from app import models as m
from app.tool_gateway import execute
from test_finance_context_tools import factory, user, project, grant, capability


def acceptance(db, p, actor, **changes):
    values = dict(project_id=p.id, acceptance_type='INITIAL', result='FAILED',
        accepted_date=date.today(), issue_description='客户质量问题', responsibility='INTERNAL',
        deduction_amount=Decimal('731.25'), currency='CNY', contract_change_required=True,
        evidence='验收扣款原件-PRIVATE', confirmed_by=actor.id)
    values.update(changes)
    row = m.CustomerAcceptanceRecord(**values)
    db.add(row)
    db.flush()
    return row


def seed(db, deduction_statuses=('RESPONSIBILITY_CONFIRMED', 'SETTLED')):
    admin = user(db, 'admin', True)
    p = project(db, 'FIN-QUALITY')
    sup = m.Supplier(code='FIN-QUALITY-SUP', name='扣款供应商', category='outsource')
    db.add(sup)
    db.flush()
    failure = acceptance(db, p, admin)
    recheck = acceptance(db, p, admin, acceptance_type='RECHECK', previous_acceptance_id=failure.id,
        result='PASSED', deduction_amount=None, currency=None, contract_change_required=False,
        evidence='整改复验签字依据')
    deductions = []
    for status in deduction_statuses:
        deduction = m.SupplierDeductionSettlement(project_id=p.id, supplier_id=sup.id,
            reason='供应商质量扣款', responsibility='SUPPLIER', deduction_amount=Decimal('8000.00'),
            currency='CNY', status=status, source_ref='SOURCE-'+status,
            responsibility_evidence='供应商责任确认单', settlement_evidence='结算单' if status=='SETTLED' else '',
            settlement_reference='SETTLEMENT-1' if status=='SETTLED' else None,
            confirmed_by=admin.id, source_system='MANUAL')
        db.add(deduction)
        deductions.append(deduction)
    db.flush()
    pending, settled = deductions
    return admin, p, failure, recheck, pending, settled


def test_finance_includes_separate_quality_and_settlement_facts_without_changing_money():
    engine, Session = factory()
    try:
        with Session.begin() as db:
            admin, p, failure, recheck, pending, settled = seed(db)
            unrelated = project(db, 'UNRELATED-QUALITY')
            acceptance(db, unrelated, admin, evidence='OTHER-PROJECT-SECRET')
            ids = [failure.id, recheck.id, pending.id, settled.id]
            actor_id, project_id = admin.id, p.id
        with Session() as db:
            result = execute(db, db.get(m.User, actor_id), 'query_finance_context', {'project_id':project_id})
            analysis = result['data'][0]['analysis']
            quality = analysis['quality_finance_context']
            assert quality == result['model_context']['quality_finance_context']
            assert {row['id'] for row in quality['customer_acceptance']['records']} == set(ids[:2])
            assert quality['customer_acceptance']['derived_status']['has_recheck_passed'] is True
            assert {row['id'] for row in quality['supplier_deductions']['records']} == set(ids[2:])
            assert quality['accounting_effect'] == 'NONE'
            assert analysis['derived_status']['has_customer_acceptance_financial_impact'] is True
            assert analysis['derived_status']['has_unsettled_supplier_deduction'] is True
            assert analysis['derived_status']['has_cost_or_deduction_signal'] is True
            assert analysis['cost_and_change_impacts'] == []
            assert analysis['customer_receipt_summary']['confirmed_totals'] == []
            assert analysis['supplier_payment_summary']['confirmed_totals'] == []
            assert analysis['current_effective_contract_balances']['supplier_payable'] == []
            assert 'OTHER-PROJECT-SECRET' not in str(result)
            assert '复验通过不会抹去' in ''.join(analysis['warnings'])
    finally:
        engine.dispose()


@pytest.mark.parametrize('source', ['acceptance', 'supplier'])
@pytest.mark.parametrize('has_capability,has_permission', [(False,True),(True,False),(True,True)])
def test_finance_quality_sources_require_both_capability_and_project_permission(source,has_capability,has_permission):
    engine, Session = factory()
    try:
        with Session.begin() as db:
            admin, p, *_ = seed(db)
            operator = user(db)
            grant(db, admin, operator, 'project.read', project_id=p.id)
            grant(db, admin, operator, 'project.dossier.read', project_id=p.id)
            capability(db, operator, 'query_finance_context')
            permission = 'project_close.read' if source == 'acceptance' else 'full_outsource_contract.read'
            tool = 'query_project_closure_context' if source == 'acceptance' else 'query_full_outsource_context'
            if has_permission:
                grant(db, admin, operator, permission, project_id=p.id,
                    category=None if source == 'acceptance' else 'outsource')
            if has_capability:
                capability(db, operator, tool)
            operator_id, project_id = operator.id, p.id
        with Session() as db:
            result = execute(db, db.get(m.User, operator_id), 'query_finance_context', {'project_id':project_id})
            quality = result['model_context']['quality_finance_context']
            key = 'customer_acceptance' if source == 'acceptance' else 'supplier_deductions'
            visible = has_capability and has_permission
            assert quality[key]['visible'] is visible
            assert bool(quality[key]['records']) is visible
            if not visible:
                assert '验收扣款原件-PRIVATE' not in str(result)
                assert '731.25' not in str(result)
                assert '8000.00' not in str(result)
                assert quality['customer_acceptance']['derived_status'] is None
                assert result['data'][0]['analysis']['derived_status']['has_unsettled_supplier_deduction'] is None
    finally:
        engine.dispose()


def test_finance_keeps_old_acceptance_impact_when_detail_page_is_truncated():
    engine, Session = factory()
    try:
        with Session.begin() as db:
            admin = user(db, 'admin', True)
            p = project(db, 'FIN-OLD-IMPACT')
            failure = acceptance(db, p, admin, accepted_date=date.today()-timedelta(days=5))
            for index in range(50):
                acceptance(db, p, admin, result='PASSED', deduction_amount=None, currency=None,
                    contract_change_required=False, evidence=f'其他独立验收-{index}')
            actor_id, project_id, failure_id = admin.id, p.id, failure.id
        with Session() as db:
            result = execute(db, db.get(m.User, actor_id), 'query_finance_context', {'project_id':project_id})
            analysis = result['data'][0]['analysis']
            source = analysis['quality_finance_context']['customer_acceptance']
            assert source['record_count'] == 51 and source['records_truncated'] is True
            assert failure_id not in {row['id'] for row in source['records']}
            assert source['derived_status']['has_unresolved_failure'] is True
            assert analysis['derived_status']['has_customer_acceptance_financial_impact'] is True
            assert '返回上限' in ''.join(analysis['gaps'])
    finally:
        engine.dispose()


def test_cancelled_and_settled_deductions_are_not_reported_as_unsettled():
    engine, Session = factory()
    try:
        with Session.begin() as db:
            admin, p, *_ = seed(db, ('CANCELLED', 'SETTLED'))
            actor_id, project_id = admin.id, p.id
        with Session() as db:
            result = execute(db, db.get(m.User, actor_id), 'query_finance_context', {'project_id':project_id})
            analysis = result['data'][0]['analysis']
            assert analysis['derived_status']['has_supplier_deduction_record'] is True
            assert analysis['derived_status']['has_unsettled_supplier_deduction'] is False
            assert '尚未登记结算' not in ''.join(analysis['warnings'])
    finally:
        engine.dispose()
