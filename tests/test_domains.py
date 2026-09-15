from datetime import timedelta
from decimal import Decimal
from sqlalchemy import select,func,text
from sqlalchemy.exc import DBAPIError
import pytest
from app import models as m
from app.db import now
from app.authorization import PERMISSIONS
from conftest import sign_in


def checked(response):
    assert response.status_code==200,response.text
    return response.json()


def workflow(client,ids,kind):
    result=checked(client.post('/api/workflows',json={'process_key':kind+'_test','name':'合成业务审批',
        'config':{'business_type':kind,'nodes':[{'key':'review','name':'人工核对','mode':'ALL','users':[ids['admin']],'reject_rules':[]}]}}))
    checked(client.post('/api/workflows/'+result['id']+'/publish'))
    return result['id']


def confirm(client,intent):return checked(client.post('/api/human-actions/'+intent['id']+'/confirm',json={'challenge':intent['challenge']}))


def approve(client,instance_id,decision='APPROVE'):
    d=checked(client.get('/api/approvals/'+instance_id))
    payload={'instance_id':d['id'],'seat_id':d['seat_id'],'seat_version':d['seat_version'],'version':d['version'],
             'snapshot_hash':d['snapshot_hash'],'decision':decision,'comment':'模拟资料人工核对'}
    return confirm(client,checked(client.post('/api/approvals/decision-intent',json=payload)))


def subject(client,ids,kind,detail,category=None):
    record=checked(client.post('/api/business/subjects',json={'kind':kind,'project_id':ids['project'],'category':category,'remark':'测试依据','detail':detail}))
    definition=workflow(client,ids,kind)
    intent=checked(client.post('/api/business/subjects/'+record['id']+'/submit-intent',json={'revision':1,'definition_id':definition}))
    submitted=confirm(client,intent)
    return record,submitted['instance_id']


def command(client,action,resource_id,payload):
    intent=checked(client.post('/api/business/command-intents',json={'action':action,'resource_id':resource_id,'payload':payload}))
    return confirm(client,intent)


def order(client,ids,material=None,category='hardware',reference='S1'):
    purchase=checked(client.post('/api/purchases',json={'project_id':ids['project'],'remark':'测试采购','lines':[
        {'material_id':material or ids['hardware'],'quantity':'10','due_date':str(now().date()+timedelta(days=1))}]}))
    template=workflow(client,ids,'purchase_request')
    intent=checked(client.post('/api/purchases/'+purchase['id']+'/submit-intent',json={'revision':1,'definition_id':template}))
    iid=confirm(client,intent)['instance_id'];approve(client,iid)
    # request_id is deliberately not in the public order field catalog.
    results=checked(client.get('/api/orders'));result=next(r for r in results if r['status']=='DRAFT')
    supplier=checked(client.post('/api/master/suppliers',json={'code':reference,'name':'模拟'+category+'供应商','category':category}))
    price,iid=subject(client,ids,'purchase_price',{'supplier_id':supplier['id'],'material_id':material or ids['hardware'],
        'unit_price':'2.50','currency':'CNY','valid_from':str(now().date()),'valid_to':str(now().date()+timedelta(days=30)),
        'quote_evidence':'合成报价依据'},category)
    assert approve(client,iid)['business_status']=='EFFECTIVE'
    result=checked(client.put('/api/orders/'+result['id'],json={'version':result['version'],'supplier_id':supplier['id'],'currency':'CNY',
        'lines':[{'id':line['id'],'price_subject_id':price['id'],'unit_price':'2.50','agreed_ship_date':str(now().date()+timedelta(days=1))} for line in result['lines']]}))
    return result


def test_procurement_receipt_quality_stock_human_closed_loop(client,data):
    ids,_=data;sign_in(client);po=order(client,ids)
    assert po['status']=='DRAFT'
    command(client,'order.issue',po['id'],{'version':po['version'],'evidence':'正式成交方案人工确认'})
    shipment=command(client,'shipment.confirm',po['lines'][0]['id'],{'quantity':'10','reference':'SHIP1','shipped_date':str(now().date()),'evidence':'供应商发货凭证'})
    warehouse=checked(client.post('/api/master/warehouses',json={'code':'W1','name':'本地测试仓库'}))
    command(client,'warehouse.configure',warehouse['id'],{'start_date':str(now().date()),'evidence':'本地测试范围，期初为零，无 ERP 重复实物台账'})
    receipt=command(client,'receipt.confirm',shipment['shipment_id'],{'quantity':'10','reference':'RECEIPT1','warehouse_id':warehouse['id'],'evidence':'人工实收凭据'})
    assert checked(client.get('/api/warehouse/stock'))==[]  # received is not accepted stock
    command(client,'inspection.confirm',receipt['receipt_id'],{'accepted_quantity':'8','rejected_quantity':'2','evidence':'检验报告，2件不合格隔离'})
    stock=checked(client.get('/api/warehouse/stock'));assert Decimal(stock[0]['quantity'])==8
    command(client,'stock.issue',stock[0]['id'],{'quantity':'3','reference':'ISSUE1','evidence':'人工领料确认'})
    assert Decimal(checked(client.get('/api/warehouse/stock'))[0]['quantity'])==5


def test_over_shipment_and_unconfirmed_warehouse_do_not_write(client,data):
    ids,factory=data;sign_in(client);po=order(client,ids)
    command(client,'order.issue',po['id'],{'version':po['version'],'evidence':'人工确认'})
    intent=checked(client.post('/api/business/command-intents',json={'action':'shipment.confirm','resource_id':po['lines'][0]['id'],
        'payload':{'quantity':'11','reference':'OVER','shipped_date':str(now().date()),'evidence':'超量测试'}}))
    r=client.post('/api/human-actions/'+intent['id']+'/confirm',json={'challenge':intent['challenge']})
    assert r.status_code==409
    with factory() as db:assert db.scalar(select(func.count()).select_from(m.SupplierShipment))==0


def test_hardware_risks_exclude_other_supplier_domain(client,data):
    ids,_=data;sign_in(client)
    hardware=order(client,ids);command(client,'order.issue',hardware['id'],{'version':hardware['version'],'evidence':'五金订单'})
    raw=order(client,ids,ids['steel'],'raw_material','S2');command(client,'order.issue',raw['id'],{'version':raw['version'],'evidence':'原材订单'})
    checked(client.post('/api/risk/policy',json={'near_due_days':3,'reason':'合成场景阈值'}))
    target=next(u for u in client.get('/api/users').json() if u['id']==ids['buyer'])
    version=target['security_version']
    for permission in ['risk.read','order.read']:
        r=checked(client.post('/api/users/'+ids['buyer']+'/grants',json={'permission':permission,'scope':{'project_id':[ids['project']],'category':['hardware']},
            'fields':PERMISSIONS[permission],'reason':'测试五金范围','expected_security_version':version}));version=r['security_version']
    sign_in(client,'test_buyer');result=checked(client.post('/api/risk/analyze'))
    assert len(result['data'])==1 and result['data'][0]['category']=='hardware'
    assert 'raw_material' not in str(result) and '模拟raw_material供应商' not in str(result)
    assert 'NEAR_DUE_UNSHIPPED' in result['data'][0]['signals']


def test_contract_payment_reservation_authorization_and_actual_payment(client,data):
    ids,factory=data;sign_in(client)
    with factory.begin() as db:db.add(m.ProjectProfile(project_id=ids['project'],owner_user_id=ids['admin'],execution_mode='FULL_OUTSOURCE'))
    supplier=checked(client.post('/api/master/suppliers',json={'code':'OUT1','name':'整套委外测试','category':'outsource'}))
    contract,iid=subject(client,ids,'full_outsource_contract',{'supplier_id':supplier['id'],'amount':'100.00','currency':'CNY','contract_number':'C1',
        'stages':[{'name':'预付款','amount':'100.00','condition':'合同生效及人工核实'}]},'outsource')
    assert approve(client,iid)['business_status']=='EFFECTIVE'
    stage=checked(client.get('/api/business/subjects/'+contract['id']))['detail']['stages'][0]
    command(client,'finance.condition',stage['id'],{'evidence':'合同条件已人工核验'})
    payment,iid=subject(client,ids,'supplier_payment',{'stage_id':stage['id'],'amount':'80.00','currency':'CNY'},'outsource')
    assert approve(client,iid)['business_status']=='EFFECTIVE'
    with factory() as db:assert db.scalar(select(func.count()).select_from(m.PaymentConfirmation))==0
    command(client,'finance.confirm',payment['id'],{'amount':'30.00','currency':'CNY','paid_date':str(now().date()),'reference':'BANK-1','evidence':'财务实际支付凭据'})
    with factory() as db:
        assert db.get(m.PaymentRequestDetail,payment['id']).reservation==Decimal('50.00')
        assert db.scalar(select(func.sum(m.PaymentConfirmation.amount)))==Decimal('30.00')
    second=checked(client.post('/api/business/subjects',json={'kind':'supplier_payment','project_id':ids['project'],'category':'outsource',
        'detail':{'stage_id':stage['id'],'amount':'21.00','currency':'CNY'}}))
    template=workflow(client,ids,'supplier_payment')
    intent=checked(client.post('/api/business/subjects/'+second['id']+'/submit-intent',json={'revision':1,'definition_id':template}))
    assert client.post('/api/human-actions/'+intent['id']+'/confirm',json={'challenge':intent['challenge']}).status_code==409


def test_plan_dependency_blocks_early_execution_and_requires_actual_confirmation(client,data):
    ids,_=data;sign_in(client);today=str(now().date())
    plan,iid=subject(client,ids,'project_plan',{'reason':'合成计划','tasks':[
        {'key':'design','name':'设计','owner_user_id':ids['admin'],'planned_start':today,'planned_end':today},
        {'key':'work','name':'加工','owner_user_id':ids['admin'],'planned_start':today,'planned_end':today,'prerequisites':['design']}]})
    approve(client,iid)
    tasks={t['key']:t for t in checked(client.get('/api/business/subjects/'+plan['id']))['detail']['tasks']}
    intent=checked(client.post('/api/business/command-intents',json={'action':'plan.execute','resource_id':tasks['work']['id'],
        'payload':{'action':'START','actual_date':today,'evidence':'提前执行测试'}}))
    assert client.post('/api/human-actions/'+intent['id']+'/confirm',json={'challenge':intent['challenge']}).status_code==400
    command(client,'plan.execute',tasks['design']['id'],{'action':'START','actual_date':today,'evidence':'人工开工'})
    command(client,'plan.execute',tasks['design']['id'],{'action':'DONE','actual_date':today,'evidence':'人工完工'})
    command(client,'plan.execute',tasks['work']['id'],{'action':'START','actual_date':today,'evidence':'人工开工'})


def test_cyclic_plan_rejected_before_persisting_subject(client,data):
    ids,factory=data;sign_in(client);today=str(now().date())
    tasks=[{'key':key,'name':key,'owner_user_id':ids['admin'],'planned_start':today,'planned_end':today,'prerequisites':[other]}
           for key,other in [('a','b'),('b','a')]]
    r=client.post('/api/business/subjects',json={'kind':'project_plan','project_id':ids['project'],'detail':{'reason':'环检测','tasks':tasks}})
    assert r.status_code==400
    with factory() as db:assert db.scalar(select(func.count()).select_from(m.BusinessSubject))==0


def test_new_domain_human_actions_reject_worker_credentials(client,data):
    from app.config import settings
    assert client.post('/api/business/command-intents',headers={'Authorization':'Bearer '+settings().worker_secret},
        json={'action':'finance.confirm','resource_id':'anything','payload':{}}).status_code==401
