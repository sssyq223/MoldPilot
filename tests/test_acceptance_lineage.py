from datetime import date
from types import SimpleNamespace

from domain_packs.mold.erp.procurement.customer_acceptance_status import summarize_customer_acceptance


def fact(identity, result='FAILED', previous=None, kind='INITIAL', signature='signature'):
    return SimpleNamespace(id=identity, project_id='project', signature_id=signature,
        previous_acceptance_id=previous, acceptance_type=kind, result=result, accepted_date=date(2026, 9, 1),
        deduction_amount=None, contract_change_required=False, schedule_impact_days=0)


def test_same_signature_new_initial_pass_cannot_clear_unrelated_failure():
    failure = fact('failure')
    other_pass = fact('unrelated', 'PASSED')
    summary = summarize_customer_acceptance([failure, other_pass])
    assert summary['has_unresolved_failure']
    assert not summary['has_customer_acceptance']
    assert summary['recheck_candidate_ids'] == ['failure']


def test_historical_unlinked_recheck_does_not_guess_failure_association():
    failure = fact('failure')
    legacy = fact('legacy', 'PASSED', kind='RECHECK')
    summary = summarize_customer_acceptance([failure, legacy])
    assert summary['has_unresolved_failure']
    assert summary['unlinked_recheck_ids'] == ['legacy']
    explicit = fact('explicit', 'PASSED', previous='failure', kind='RECHECK')
    summary = summarize_customer_acceptance([failure, legacy, explicit])
    assert not summary['has_unresolved_failure']
    assert summary['has_recheck_passed']
    assert summary['unlinked_recheck_ids'] == ['legacy']


def test_cross_signature_or_cyclic_links_cannot_hide_failure():
    failure = fact('failure')
    invalid = fact('invalid', 'PASSED', previous='failure', kind='RECHECK', signature='other')
    summary = summarize_customer_acceptance([failure, invalid])
    assert summary['has_acceptance_chain_conflict']
    assert summary['has_unresolved_failure']
    first = fact('a', previous='b', kind='RECHECK')
    second = fact('b', 'PASSED', previous='a', kind='RECHECK')
    summary = summarize_customer_acceptance([first, second])
    assert summary['has_acceptance_chain_conflict']
    assert not summary['has_customer_acceptance']
