from types import SimpleNamespace

import pytest

from agent_core.audit_projection import visible_detail


def test_protected_audit_requires_every_declared_read_scope():
    detail = {'reason':'sensitive', '_detail_access':[
        {'permission':'source.read','scope':{'project_id':'p'},'fields':['amount']},
        {'permission':'other.read','scope':{'project_id':'p'},'fields':['detail']},
    ]}
    calls = []
    def access(permission, scope):
        calls.append((permission, scope))
        return SimpleNamespace(allowed=permission == 'source.read', fields={'*'})
    assert visible_detail(detail,access) == ({},True)
    assert [permission for permission,_ in calls] == ['source.read','other.read']
    assert visible_detail(detail,lambda *_:SimpleNamespace(allowed=True,fields={'*'})) == ({'reason':'sensitive'},False)
    assert '_detail_access' in detail


@pytest.mark.parametrize('rules',[None,{},[],[{}],[{'permission':'x','scope':{},'fields':'amount'}]])
def test_malformed_audit_requirements_do_not_expose_details(rules):
    assert visible_detail({'secret':'value','_detail_access':rules},lambda *_:pytest.fail('invalid requirement')) == ({},True)


def test_ordinary_audit_events_keep_their_existing_projection():
    detail = {'kind':'example'}
    assert visible_detail(detail,lambda *_:pytest.fail('no protected data')) == (detail,False)
