from copy import deepcopy
import pytest
from app import bpm,material_rules as rules
from app.errors import DomainError

CONTRACT={
    'fields':[{'key':'urgent','label':'是否紧急','type':'boolean'},
              {'key':'needed_on','label':'要求日期','type':'date'}],
    'tables':[
        {'key':'design','label':'设计清单（合成）','fields':[
            {'key':'item','label':'料号','type':'text'},
            {'key':'quantity','label':'数量','type':'decimal','unit':'件'},
            {'key':'unit_price','label':'单价','type':'money','currency_field':'currency'},
            {'key':'currency','label':'币种','type':'text'}]},
        {'key':'costs','label':'核算清单（合成）','fields':[
            {'key':'process','label':'工序','type':'text'},
            {'key':'hours','label':'工时','type':'decimal','unit':'小时'}]},
        {'key':'attachments','label':'附件核对（合成）','fields':[
            {'key':'kind','label':'附件类型','type':'text'},
            {'key':'verified','label':'人工已核对','type':'boolean'}]},
    ]}

def row(identifier,quantity='20',price='100',currency='CNY'):
    return {'id':identifier,'values':{'item':identifier,'quantity':quantity,'unit_price':price,'currency':currency}}

def dataset(rows=None):return {'fields':{'urgent':False,'needed_on':'2026-09-20'},'tables':{'design':rows if rows is not None else [row('A'),row('B','5','1000')]}}

def compound():return {'table':'design','quantifier':'ANY','condition':{'all':[
    {'field':'quantity','op':'gt','value':'10'},
    {'field':'unit_price','op':'gt','value':'500','currency':'CNY'}]}}

def aggregate(operation='SUM',field='quantity',value='25',**extras):
    return {'table':'design','aggregate':operation,**({'field':field} if operation!='COUNT' else {}),'op':'eq','value':value,**extras}

def test_compound_conditions_must_match_in_same_row():
    result=rules.evaluate(compound(),dataset(),CONTRACT)
    assert result['result'] is False
    assert [r['evaluation']['result'] for r in result['rows']]==[False,False]
    assert result['rows'][0]['evaluation']['children'][1]['actual']=='100'
    matched=rules.evaluate(compound(),dataset([row('A','20','1000')]),CONTRACT)
    assert matched['result'] is True and matched['rows'][0]['row_id']=='A'

def test_stable_row_id_survives_reordering():
    condition={'table':'design','quantifier':'ROW','row_id':'B','condition':{'field':'quantity','op':'eq','value':'5'}}
    assert rules.evaluate(condition,dataset(),CONTRACT)['result'] is True
    assert rules.evaluate(condition,dataset(list(reversed(dataset()['tables']['design']))),CONTRACT)['result'] is True
    condition['row_id']='not-present'
    assert rules.evaluate(condition,dataset(),CONTRACT)['result'] is None

@pytest.mark.parametrize('rows',[[],[row('same'),row('same')],[{'values':{}}],[{'id':'A','values':[]}]] )
def test_empty_invalid_duplicate_rows_never_pass(rows):
    condition=compound();condition['quantifier']='ALL'
    assert rules.evaluate(condition,dataset(rows),CONTRACT)['result'] is None

def test_missing_value_is_not_bypassed_by_known_matching_row():
    bad=row('bad','20','900');del bad['values']['quantity']
    result=rules.evaluate(compound(),dataset([row('ok','20','900'),bad]),CONTRACT)
    assert result['result'] is None

@pytest.mark.parametrize('operation,expected',[('SUM','25'),('COUNT','2'),('MAX','20'),('MIN','5')])
def test_all_supported_aggregates(operation,expected):
    result=rules.evaluate(aggregate(operation,value=expected),dataset(),CONTRACT)
    assert result['result'] is True
    assert result['actual']==expected

def test_aggregate_filter_and_unknown_filter():
    condition=aggregate(value='20',filter={'field':'item','op':'eq','value':'A'})
    assert rules.evaluate(condition,dataset(),CONTRACT)['result'] is True
    broken=dataset();del broken['tables']['design'][1]['values']['item']
    assert rules.evaluate(condition,broken,CONTRACT)['reason']=='FILTER_DATA_MISSING'

def test_count_of_known_empty_filter_is_zero_but_missing_table_unknown():
    condition=aggregate('COUNT',value='0',filter={'field':'item','op':'eq','value':'absent'})
    assert rules.evaluate(condition,dataset(),CONTRACT)['result'] is True
    assert rules.evaluate(condition,{},CONTRACT)['result'] is None

def test_sum_decimal_and_mixed_currency():
    condition=aggregate(field='unit_price',value='0.3',currency='CNY')
    data=dataset([row('A','1','0.1'),row('B','1','0.2')])
    assert rules.evaluate(condition,data,CONTRACT)['result'] is True
    data['tables']['design'][1]['values']['currency']='USD'
    assert rules.evaluate(condition,data,CONTRACT)['reason']=='CURRENCY_MISMATCH'

def test_dates_booleans_and_attachment_fields_are_not_amount_specific():
    data=dataset();data['tables']['attachments']=[{'id':'file-version-1','values':{'kind':'合同','verified':True}}]
    condition={'all':[{'field':'needed_on','op':'gte','value':'2026-09-18'},
                      {'field':'urgent','op':'eq','value':False},
                      {'table':'attachments','quantifier':'ANY','condition':{'all':[
                          {'field':'kind','op':'eq','value':'合同'},{'field':'verified','op':'eq','value':True}]}}]}
    assert rules.evaluate(condition,data,CONTRACT)['result'] is True
    data['tables']['attachments'][0]['values']['verified']='true'
    assert rules.evaluate(condition,data,CONTRACT)['result'] is None

@pytest.mark.parametrize('value',[True,'NaN','Infinity','1e1000','1e-1000',{},'1'*101])
def test_invalid_numeric_values_fail_closed(value):
    assert rules.evaluate(aggregate(),dataset([row('bad',value)]),CONTRACT)['result'] is None

@pytest.mark.parametrize('change',[
    lambda c:c.update({'field':'__class__','op':'eq','value':'x'}),
    lambda c:c.update({'field':'quantity','op':'gt','value':True}),
    lambda c:c.update({'table':'missing','quantifier':'ANY','condition':{'field':'x','op':'eq','value':1}}),
    lambda c:c.update({'field':'urgent','op':'gt','value':True}),
    lambda c:c.update({'field':'needed_on','op':'eq','value':'2026-02-30'}),
])
def test_invalid_rules_rejected_before_execution(change):
    c={};change(c)
    with pytest.raises(DomainError):rules.validate_rule(c,CONTRACT)

def test_unknown_row_fields_money_without_currency_and_nested_tables_rejected():
    for c in [
        {'table':'design','quantifier':'ANY','condition':{'field':'unknown','op':'eq','value':1}},
        {'table':'design','aggregate':'SUM','field':'unit_price','op':'gt','value':'1'},
        {'table':'design','quantifier':'ANY','condition':compound()},
    ]:
        with pytest.raises(DomainError):rules.validate_rule(c,CONTRACT)

def test_engine_simulation_uses_material_conditions_and_provides_row_evidence():
    config={'business_type':'generic','material_contract':CONTRACT,'nodes':[
        {'key':'check','name':'核对','users':['test-user'],'mode':'ALL','routes':[{'condition':compound(),'target':'extra'}],'default_target':'end'},
        {'key':'extra','name':'增补审批','users':['test-user'],'mode':'ALL'}]}
    result=bpm.simulate(config,{'material_data':dataset()})
    assert result['outcome']=='ROUTE_VALID' and [n['key'] for n in result['path']]==['check']
    assert result['path'][0]['condition_evaluations'][0]['evaluation']['result'] is False
    result=bpm.simulate(config,{'material_data':dataset([row('match','20','1000')])})
    assert [n['key'] for n in result['path']]==['check','extra']
    assert bpm.simulate(config,{})['outcome']=='ROUTE_DATA_MISSING'
    config['nodes'][0]['reject_rules']=[{'condition':compound(),'reason':'必须人工驳回核对'}]
    assert bpm.simulate(config,{'material_data':dataset([row('match','20','1000')])})['outcome']=='MUST_REJECT'

def test_evaluation_preserves_source_and_rule_and_bounds_work():
    data=dataset();before=deepcopy(data);condition=compound();old=deepcopy(condition)
    rules.evaluate(condition,data,CONTRACT)
    assert data==before and condition==old
    large=dataset([row(str(i)) for i in range(5000)])
    condition['condition']={'all':[{'field':'quantity','op':'gt','value':'1'} for _ in range(12)]}
    with pytest.raises(DomainError,match='超出限制'):rules.evaluate(condition,large,CONTRACT)


def test_unbound_material_template_cannot_be_submitted_with_simulation_values(client,data):
    from conftest import sign_in,draft
    ids,_=data;sign_in(client)
    category=client.post('/api/workflow-categories',json={'name':'合成资料规则测试'}).json()
    config={'business_type':'generic','material_contract':CONTRACT,'nodes':[
        {'key':'review','name':'材料核对','users':[ids['admin']],'mode':'ALL'}]}
    r=client.post('/api/workflows',json={'process_key':'material_unbound','name':'资料规则待绑定','category_id':category['id'],'config':config})
    assert r.status_code==200,r.text
    did=r.json()['id'];assert client.post('/api/workflows/'+did+'/publish').status_code==200
    rid=draft(client,ids)
    response=client.post('/api/purchases/'+rid+'/submit-intent',json={'revision':1,'definition_id':did})
    assert response.status_code==409
    assert response.json()['error']['code']=='MATERIALS_NOT_BOUND'
    assert client.post('/api/purchases/'+rid+'/submit-intent',json={'revision':1,'definition_id':did,'material_data':dataset()}).status_code==422
