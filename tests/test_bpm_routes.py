from copy import deepcopy
import json
import pytest
from sqlalchemy import select, func
from app import bpm
from app.errors import DomainError
from domain_packs.mold.erp.core.rules import evaluate, validate_rule
from app.models import ApprovalInstance, ApprovalSeat, ApprovalAction, User, WorkflowDefinition
from conftest import sign_in, draft, submit
from test_core import confirm_decision, decision_payload


def config(users=None):
    users = users or ['test-user']
    return {'business_type': 'purchase_request', 'nodes': [
        {'key': 'review', 'name': '资料核对', 'users': users, 'mode': 'ALL', 'reject_rules': [],
         'routes': [{'condition': {'field': 'quantity', 'op': 'gt', 'value': '10'}, 'target': 'extra'}],
         'default_target': 'finish'},
        {'key': 'extra', 'name': '增补审批', 'users': users, 'mode': 'ALL', 'reject_rules': []},
        {'key': 'finish', 'name': '最终审批', 'users': users, 'mode': 'ALL', 'reject_rules': []},
    ]}


@pytest.mark.parametrize('quantity,expected', [('5', ['review', 'finish']), ('11', ['review', 'extra', 'finish'])])
def test_simulation_uses_real_engine_exclusive_gateway(quantity, expected):
    result = bpm.simulate(config(), {'lines': [{'quantity': quantity}]})
    assert result['outcome'] == 'ROUTE_VALID'
    assert [n['key'] for n in result['path']] == expected


def test_engine_inspection_and_advance_leave_serialized_checkpoint_intact():
    cfg = config()
    state = bpm.start_engine(bpm.compile_bpmn(cfg))
    before = deepcopy(state)
    assert bpm.current_stage(state, cfg) == 0
    advanced = bpm.advance_engine(state, 'review', 'finish')
    assert state == before
    assert bpm.current_stage(advanced, cfg) == 2
    json.dumps(advanced)


def test_decimal_equality_and_membership_are_numeric_not_formatted_text():
    assert evaluate({'field':'quantity','op':'eq','value':'0.10'}, {'quantity':'0.100000'}) is True
    assert evaluate({'field':'amount','op':'in','value':['10','20.00']}, {'amount':'20','currency':'CNY'}) is True
    assert evaluate({'field':'quantity','op':'eq','value':'1'}, {'quantity':True}) is None


@pytest.mark.parametrize('condition', [
    {'field':'quantity','op':'gt','value':'NaN'},
    {'field':'amount','op':'eq','value':True},
    {'field':'quantity','op':'in','value':['10',{}]},
    {'field':'remark','op':'gt','value':'10'},
    {'field':'remark','op':'eq','value':[]},
    {'field':[],'op':'eq','value':'a'},
])
def test_invalid_typed_conditions_rejected_at_save(condition):
    with pytest.raises(DomainError): validate_rule(condition)


def test_unknown_data_does_not_take_default_and_overlapping_conditions_block():
    assert bpm.simulate(config(), {})['outcome'] == 'ROUTE_DATA_MISSING'
    cfg = config()
    cfg['nodes'][0]['routes'].append({'condition': {'field': 'quantity', 'op': 'gte', 'value': '10'}, 'target': 'finish'})
    assert bpm.simulate(cfg, {'quantity': '11'})['outcome'] == 'ROUTE_AMBIGUOUS'


@pytest.mark.parametrize('target', ['review', 'unknown', '../end', "end' or True", [], None])
def test_routes_reject_cycles_unknown_targets_and_expression_injection(target):
    cfg = config()
    cfg['nodes'][0]['default_target'] = target
    with pytest.raises(DomainError): bpm.compile_bpmn(cfg)


def test_unreachable_nodes_and_missing_default_rejected():
    cfg = config()
    del cfg['nodes'][0]['default_target']
    with pytest.raises(DomainError): bpm.validate(cfg)
    cfg = config()
    cfg['nodes'][0]['routes'][0]['target'] = 'finish'
    with pytest.raises(DomainError): bpm.validate(cfg)


@pytest.mark.parametrize('quantity,expected_stages', [('5', [0, 2]), ('11', [0, 1, 2])])
def test_persisted_route_seats_and_final_approval(client, data, quantity, expected_stages):
    ids, factory = data
    sign_in(client)
    cfg = config([ids['admin']])
    result = client.post('/api/workflows', json={'process_key': 'conditional_test', 'name': '条件审批合成测试', 'config': cfg})
    assert result.status_code == 200, result.text
    did = result.json()['id']
    assert client.post(f'/api/workflows/{did}/publish').status_code == 200
    iid = submit(client, {**ids, 'definition': did}, draft(client, ids, quantity=quantity))
    for stage in expected_stages:
        detail = client.get('/api/approvals/'+iid).json()
        assert detail['stage_index'] == stage
        _, response = confirm_decision(client, iid)
    assert response.json()['status'] == 'COMPLETED'
    with factory() as db:
        assert sorted(db.scalars(select(ApprovalSeat.stage_index).where(ApprovalSeat.instance_id == iid))) == expected_stages
        assert db.scalar(select(func.count()).select_from(ApprovalAction)) == len(expected_stages)
        state = db.get(ApprovalInstance, iid).engine_state
        assert bpm.current_stage(state, cfg) == 3
        json.dumps(state)


def test_failed_route_rolls_back_human_decision(client, data):
    ids, factory = data
    sign_in(client)
    cfg = config([ids['admin']])
    cfg['nodes'][0]['routes'][0]['condition'] = {'field': 'amount', 'op': 'gt', 'value': '10'}
    did = client.post('/api/workflows', json={'process_key': 'missing_route', 'name': '资料缺失', 'config': cfg}).json()['id']
    assert client.post(f'/api/workflows/{did}/publish').status_code == 200
    iid = submit(client, {**ids, 'definition': did}, draft(client, ids))
    detail = client.get('/api/approvals/'+iid).json()
    intent = client.post('/api/approvals/decision-intent', json=decision_payload(detail)).json()
    result = client.post(f"/api/human-actions/{intent['id']}/confirm", json={'challenge': intent['challenge']})
    assert result.status_code == 409
    assert result.json()['error']['code'] == 'ROUTE_DATA_MISSING'
    with factory() as db:
        assert db.scalar(select(func.count()).select_from(ApprovalAction)) == 0
        assert db.get(ApprovalInstance, iid).version == 1


def test_all_signers_cannot_silently_shrink_after_user_disabled(client, data):
    ids, factory = data
    sign_in(client)
    cfg = config([ids['admin'], ids['reviewer']])
    did = client.post('/api/workflows', json={'process_key': 'all_required', 'name': '全部人员', 'config': cfg}).json()['id']
    assert client.post(f'/api/workflows/{did}/publish').status_code == 200
    with factory.begin() as db: db.get(User, ids['reviewer']).active = False
    iid = submit(client, {**ids, 'definition': did}, draft(client, ids))
    detail = client.get('/api/approvals/'+iid).json()
    assert detail['incident'] == 'ASSIGNMENT_BLOCKED'
    assert detail['allowed_actions'] == []


def test_simulation_is_authorized_and_creates_no_approvals(client, data):
    ids, factory = data
    sign_in(client, 'test_buyer')
    body = {'config': config([ids['admin']]), 'snapshot': {'quantity': '1'}}
    assert client.post('/api/workflows/simulate', json=body).status_code == 403
    sign_in(client)
    result = client.post('/api/workflows/simulate', json=body)
    assert result.status_code == 200, result.text
    assert result.json()['simulation'] is True
    with factory() as db:
        assert db.scalar(select(func.count()).select_from(ApprovalInstance)) == 0
