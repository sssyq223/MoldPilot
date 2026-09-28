"""Seed an isolated supplier payment journey; no model output or payment is seeded."""
import json

import httpx
from sqlalchemy import select

import browser_acceptance as installation


def build():
    record = json.loads(installation.CONFIG.read_text(encoding='utf-8'))
    installation.apply_environment(record)
    from app.db import SessionLocal
    from app import models as m
    code = 'ACCEPTANCE-PAYMENT-001'
    with SessionLocal.begin() as db:
        if db.bind.url.database != installation.DATABASE:
            raise RuntimeError('This fixture is restricted to the isolated acceptance database')
        if db.scalar(select(m.Project.id).where(m.Project.code == code)):
            raise RuntimeError('Fixture already exists; inspect its receipt instead of overwriting history')
        actor = db.scalar(select(m.User).where(m.User.username == record['username']))
        project = m.Project(code=code,name='合成供应商付款流程验收',status='ACTIVE')
        db.add(project);db.flush()
        db.add(m.ProjectProfile(project_id=project.id,owner_user_id=actor.id,execution_mode='FULL_OUTSOURCE'))
        project_id, actor_id = project.id, actor.id
    receipt = {'synthetic':True,'project_code':code,'project_id':project_id,'setup_status':'IN_PROGRESS'}
    receipt_path = installation.LOCAL/'supplier-payment-fixture.json'
    receipt_path.write_text(json.dumps(receipt,ensure_ascii=False,indent=2),encoding='utf-8')
    with httpx.Client(base_url='http://127.0.0.1:8001',headers={'Origin':installation.ORIGIN},timeout=30) as client:
        def checked(response):
            response.raise_for_status()
            return response.json()
        login = checked(client.post('/api/auth/login',json={'username':record['username'],'password':record['password']}))
        client.headers['X-CSRF-Token'] = login['csrf']
        def post(path, data=None):
            return checked(client.post(path,json=data))
        def confirm(intent):
            return post('/api/human-actions/'+intent['id']+'/confirm',{'challenge':intent['challenge']})
        def workflow(kind, label):
            result = post('/api/workflows',{'process_key':'synthetic_payment_'+kind,'name':label,
                'config':{'business_type':kind,'nodes':[{'key':'review','name':'合成验收人工核对',
                    'mode':'ALL','users':[actor_id],'reject_rules':[]}]}})
            post('/api/workflows/'+result['id']+'/publish')
            return result['id']
        supplier = post('/api/master/suppliers',{'code':code+'-SUP','name':'合成付款验收供应商','category':'outsource'})
        contract = post('/api/business/subjects',{'kind':'full_outsource_contract','project_id':project_id,
            'category':'outsource','remark':'仅隔离合成数据，非真实合同',
            'detail':{'supplier_id':supplier['id'],'amount':'100','currency':'CNY','contract_number':code+'-CONTRACT',
                'stages':[{'name':'合成验收阶段款','amount':'100','condition':'合成阶段验收原件已核对'}]}})
        definition = workflow('full_outsource_contract','合成委外合同审批')
        submitted = confirm(post('/api/business/subjects/'+contract['id']+'/submit-intent',{'revision':1,'definition_id':definition}))
        approval = checked(client.get('/api/approvals/'+submitted['instance_id']))
        approved = confirm(post('/api/approvals/decision-intent',{'instance_id':approval['id'],
            'seat_id':approval['seat_id'],'seat_version':approval['seat_version'],'version':approval['version'],
            'snapshot_hash':approval['snapshot_hash'],'decision':'APPROVE','comment':'隔离合成合同准备完成'}))
        if approved['business_status'] != 'EFFECTIVE':
            raise RuntimeError('Synthetic contract failed to become effective')
        payment_workflow = workflow('supplier_payment','合成供应商付款审批')
        current = checked(client.get('/api/business/subjects/'+contract['id']))
        receipt.update({'setup_status':'READY','contract_id':contract['id'],
            'stage_id':current['detail']['stages'][0]['id'],'workflow_definition_id':payment_workflow})
        receipt_path.write_text(json.dumps(receipt,ensure_ascii=False,indent=2),encoding='utf-8')
    return receipt


if __name__ == '__main__':
    print(json.dumps(build(),ensure_ascii=False))
