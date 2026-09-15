from datetime import timedelta
from decimal import Decimal
from sqlalchemy import select,func,text
from sqlalchemy.exc import DBAPIError
import pytest
from app import models as m
from app.db import now
from conftest import sign_in
from test_domains import checked,subject,approve,command,workflow,confirm,order


def test_design_assembly_trial_requires_real_execution(client,data):
    ids,_=data;sign_in(client);today=str(now().date())
    design,iid=subject(client,ids,'design_route',{'design_type':'NEW_MOLD','drawing_revision':'TEST-A','drawing_evidence':'合成图纸审查',
        'reviewer_id':ids['admin'],'items':[{'material_id':ids['hardware'],'quantity':'1','route':'PURCHASE'}]})
    assert approve(client,iid)['business_status']=='EFFECTIVE'
    assembly,iid=subject(client,ids,'assembly_issue',{'design_id':design['id'],'supervisor_id':ids['admin'],
        'planned_date':today,'prerequisites_evidence':'合成装配齐套核实'})
    assert approve(client,iid)['business_status']=='EFFECTIVE'
    trial_payload={'kind':'trial_request','project_id':ids['project'],'detail':{'assembly_id':assembly['id'],
        'planned_date':today,'location':'测试试模区','acceptance_criteria':'合成验收标准','responsible_id':ids['admin']}}
    assert client.post('/api/business/subjects',json=trial_payload).json()['error']['code']=='ASSEMBLY_INCOMPLETE'
    command(client,'assembly.execute',assembly['id'],{'action':'START','actual_date':today,'evidence':'钳工主管确认开始'})
    command(client,'assembly.execute',assembly['id'],{'action':'DONE','actual_date':today,'evidence':'钳工主管确认实物装配完成'})
    trial,iid=subject(client,ids,'trial_request',trial_payload['detail'])
    assert approve(client,iid)['business_status']=='EFFECTIVE'
    assert checked(client.get('/api/business/subjects/'+trial['id']))['detail']['results']==[]
    result=command(client,'trial.confirm',trial['id'],{'passed':True,'actual_date':today,'evidence':'合成检验报告','findings':'合成尺寸与动作检测合格'})
    assert result['trial_result_id']
    read=checked(client.get('/api/business/subjects/'+trial['id']))['detail']['results']
    assert len(read)==1 and read[0]['passed'] is True


def test_full_outsource_blocks_internal_manufacturing_route(client,data):
    ids,factory=data;sign_in(client)
    with factory.begin() as db:db.add(m.ProjectProfile(project_id=ids['project'],owner_user_id=ids['admin'],execution_mode='FULL_OUTSOURCE'))
    response=client.post('/api/business/subjects',json={'kind':'design_route','project_id':ids['project'],'detail':{
        'design_type':'NEW_MOLD','drawing_revision':'A','drawing_evidence':'合成','reviewer_id':ids['admin'],
        'items':[{'material_id':ids['hardware'],'quantity':'1','route':'INTERNAL'}]}})
    assert response.json()['error']['code']=='MODE_CONFLICT'
    with factory() as db:assert db.scalar(select(func.count()).select_from(m.DesignDetail))==0


def test_order_price_must_match_approved_snapshot(client,data):
    ids,_=data;sign_in(client);po=order(client,ids)
    line=po['lines'][0]
    response=client.put('/api/orders/'+po['id'],json={'version':po['version'],'supplier_id':po['supplier_id'],'currency':'CNY',
        'lines':[{'id':line['id'],'price_subject_id':line['price_subject_id'],'unit_price':'0.01','agreed_ship_date':line['agreed_ship_date']}]})
    assert response.json()['error']['code']=='PRICE_MISMATCH'
    assert Decimal(checked(client.get('/api/orders'))[0]['lines'][0]['unit_price'])==Decimal('2.50')


def test_finance_correction_keeps_original_and_restores_reservation(client,data):
    ids,factory=data;sign_in(client);today=str(now().date())
    with factory.begin() as db:db.add(m.ProjectProfile(project_id=ids['project'],owner_user_id=ids['admin'],execution_mode='FULL_OUTSOURCE'))
    supplier=checked(client.post('/api/master/suppliers',json={'code':'O','name':'测试委外','category':'outsource'}))
    contract,iid=subject(client,ids,'full_outsource_contract',{'supplier_id':supplier['id'],'amount':'100','currency':'CNY',
        'contract_number':'TEST','stages':[{'name':'阶段一','amount':'100','condition':'人工核实'}]},'outsource');approve(client,iid)
    stage=checked(client.get('/api/business/subjects/'+contract['id']))['detail']['stages'][0]
    command(client,'finance.condition',stage['id'],{'evidence':'真实依据的合成替身'})
    payment,iid=subject(client,ids,'supplier_payment',{'stage_id':stage['id'],'amount':'80','currency':'CNY'},'outsource');approve(client,iid)
    paid=command(client,'finance.confirm',payment['id'],{'amount':'30','currency':'CNY','paid_date':today,'reference':'PAY1','evidence':'合成实付凭证'})
    correction,iid=subject(client,ids,'finance_correction',{'original_payment_id':paid['payment_confirmation_id'],
        'reason':'合成错误付款冲正','reversal_evidence':'人工核验实际退款凭证','reversal_date':today},'outsource')
    assert approve(client,iid)['business_status']=='EFFECTIVE'
    detail=checked(client.get('/api/business/subjects/'+payment['id']))['detail']
    assert Decimal(detail['reservation'])==80 and len(detail['payments'])==2
    assert sum(Decimal(p['amount']) for p in detail['payments'])==0
    duplicate,iid=subject(client,ids,'finance_correction',{'original_payment_id':paid['payment_confirmation_id'],
        'reason':'重复冲正测试','reversal_evidence':'重复凭据','reversal_date':today},'outsource')
    assert approve(client,iid)['business_status']=='APPLY_BLOCKED'
    with factory.begin() as db:
        with pytest.raises(DBAPIError),db.begin_nested():
            db.execute(text('UPDATE payment_confirmation SET amount=1 WHERE id=:id'),{'id':paid['payment_confirmation_id']})


def test_warehouse_receipt_list_query_is_unambiguous(client,data):
    ids,_=data;sign_in(client);po=order(client,ids)
    command(client,'order.issue',po['id'],{'version':po['version'],'evidence':'合成确认'})
    shipment=command(client,'shipment.confirm',po['lines'][0]['id'],{'quantity':'1','reference':'SHIP','shipped_date':str(now().date()),'evidence':'合成'})
    warehouse=checked(client.post('/api/master/warehouses',json={'code':'TEST','name':'测试仓库'}))
    command(client,'warehouse.configure',warehouse['id'],{'start_date':str(now().date()),'evidence':'测试零期初'})
    command(client,'receipt.confirm',shipment['shipment_id'],{'quantity':'1','reference':'RECEIPT','warehouse_id':warehouse['id'],'evidence':'合成'})
    assert len(checked(client.get('/api/warehouse/receipts')))==1
