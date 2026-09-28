from datetime import date
from uuid import uuid4

import pytest
from sqlalchemy import select, func

from app import models as m, business, bpm
from app.authorization import fingerprint
from app.errors import DomainError
from app.tool_gateway import execute
from conftest import sign_in
from test_contacts import create, add_task
from test_bpm_assignments import group
from test_domains import subject, approve, workflow


def setup_change(client,data):
    ids,Session=data
    sign_in(client)
    department=group(client,[ids['admin']],kind='DEPARTMENT',name='设变评估',heads=[ids['admin']])
    case,_=create(client,ids,change_type='CHANGE',problem_source='CUSTOMER_CHANGE')
    case=add_task(client,case,department)
    with Session.begin() as db:
        project=db.get(m.Project,ids['project'])
        project.status='ACTIVE'
        mold=m.Mold(internal_number='ORIGINAL-MOLD',name='原模具')
        db.add(mold);db.flush()
        db.add(m.ProjectMold(project_id=project.id,mold_id=mold.id))
        conversation=m.Conversation(user_id=ids['admin'],title='设变开工测试')
        db.add(conversation);db.flush()
        blob=m.FileObject(owner_id=ids['admin'],conversation_id=conversation.id,request_key='change-notice',
            filename='客户书面开工及费用确认.pdf',media_type='application/pdf',size=64,
            sha256='a'*64,backend='local',storage_namespace='test',object_key='test/change-start.pdf')
        db.add(blob);db.flush()
        db.add(m.ContactAttachment(case_id=case['id'],file_id=blob.id,document_id=str(uuid4()),
            version=1,title='客户书面开工及费用确认',created_by=ids['admin']))
        mold_id,file_id,conversation_id=mold.id,blob.id,conversation.id
    record,instance=subject(client,ids,'contact_resolution',{
        'case_id':case['id'],'case_revision':case['revision'],'solution':'仅返工本次确认范围，其他任务照常',
        'customer_due_affected':False,'customer_evidence':'客户书面确认本次修模及交期'},category='hardware')
    assert approve(client,instance)['business_status']=='EFFECTIVE'
    definition=workflow(client,ids,'internal_start')
    with Session.begin() as db:
        admin=db.get(m.User,ids['admin']);project=db.get(m.Project,ids['project'])
        run=m.Run(conversation_id=conversation_id,user_id=admin.id,security_version=admin.security_version,
            prompt='按已批准方案准备原模免费设变开工',status='SUCCEEDED',
            checkpoint={'authorization_hash':fingerprint(db,admin),'agent_permission_mode':'ask'})
        db.add(run);db.flush()
        args={'project_id':project.id,'project_version':project.row_version,
            'source_subject_id':record['id'],'processing_kind':'EXISTING_MOLD_CHANGE',
            'execution_mode':'INTERNAL','effective_date':date.today().isoformat(),
            'evidence':'书面开工通知已经本人核对','workflow_definition_id':definition,
            'change':{'mold_ids':[mold_id],'charge_kind':'FREE','amount':'0','currency':'CNY',
                'commercial_evidence':'客户与项目负责人书面确认免费小设变',
                'commercial_file_id':file_id,'notice_file_id':file_id,'contract_mode':'NO_NEW_CONTRACT',
                'customer_mold_number':'CUSTOMER-NEW-REF','requested_due_date':date.today().isoformat()}}
        return args,run.id,case['id']


def prepare_confirm(Session,ids,args,run_id,sequence=0):
    with Session.begin() as db:
        admin=db.get(m.User,ids['admin']);run=db.get(m.Run,run_id)
        evidence=execute(db,admin,'prepare_internal_start',args,run=run)
        if sequence==0:
            assert db.scalar(select(m.BusinessSubject.id).where(m.BusinessSubject.kind=='internal_start')) is None
        step=m.Step(run_id=run.id,sequence=sequence,tool='prepare_internal_start',request_hash='change-start',result=evidence)
        db.add(step);db.flush()
        payload={'step_id':step.id,'proposal_hash':bpm.content_hash(evidence['proposal'])}
        intent=business.create_intent(db,admin,'internal_start.execute',step.id,payload)
        receipt=business.confirm_intent(db,admin,intent['id'],intent['challenge'])
        assert receipt['status']=='SUBMITTED'
        approval=db.get(m.ApprovalInstance,receipt['instance_id'])
        frozen=approval.snapshot['detail']['formal_start_material']['linked_business']
        assert frozen['change_terms']['charge_kind']==args['change']['charge_kind']
        assert frozen['change_source']['subject_id']==args['source_subject_id']
        assert db.scalar(select(m.InternalStartDispatch).where(m.InternalStartDispatch.start_subject_id==receipt['subject_id'])) is None
        return receipt


@pytest.mark.parametrize('mode,charged',[('INTERNAL',False),('FULL_OUTSOURCE',True)])
def test_existing_mold_change_approval_reuses_identity_and_does_not_execute_erp(client,data,mode,charged):
    ids,Session=data
    args,run_id,case_id=setup_change(client,data)
    args['execution_mode']=mode
    if charged:
        args['change'].update(charge_kind='CHARGED',amount='800.00',commercial_evidence='采购与供应商书面确认新增费用 800 CNY')
    if mode=='FULL_OUTSOURCE':
        with Session.begin() as db:
            supplier=m.Supplier(code='CHANGE-SUPPLIER',name='原委外供应商',category='outsource',active=True)
            db.add(supplier);db.flush()
            args['change']['supplier_terms']={'supplier_id':supplier.id,'amount':'500.00','currency':'CNY',
                'evidence':'采购与供应商书面确认新增费用 500 CNY','file_id':args['change']['commercial_file_id']}
        args['change']['commercial_evidence']='客户与我方书面确认收费 800 CNY'
    with Session() as db:
        counts=(db.scalar(select(func.count()).select_from(m.Project)),db.scalar(select(func.count()).select_from(m.Mold)))
        from domain_packs.mold.erp.project import change_start
        case=db.get(m.ContactCase,case_id);resolution=db.get(m.ContactResolution,args['source_subject_id'])
        with pytest.raises(DomainError) as missing:
            change_start.require_effective_notice(db,case,resolution)
        assert missing.value.code=='CHANGE_START_REQUIRED'
    receipt=prepare_confirm(Session,ids,args,run_id)
    assert approve(client,receipt['instance_id'])['business_status']=='EFFECTIVE'
    with Session() as db:
        snapshot=db.get(m.InternalStartSnapshot,receipt['subject_id'])
        assert snapshot.bid_intake_revision_id is None
        assert snapshot.linked_business['internal_molds'][0]['internal_number']=='ORIGINAL-MOLD'
        assert snapshot.linked_business['customer_mold_number']=='CUSTOMER-NEW-REF'
        assert snapshot.linked_business['change_terms']['contract_mode']=='NO_NEW_CONTRACT'
        if mode=='FULL_OUTSOURCE':
            assert snapshot.linked_business['change_terms']['amount']=='800.00'
            assert snapshot.linked_business['change_terms']['supplier_terms']['amount']=='500.00'
        assert db.get(m.Project,ids['project']).status=='ACTIVE'
        assert (db.scalar(select(func.count()).select_from(m.Project)),db.scalar(select(func.count()).select_from(m.Mold)))==counts
        assert len(list(db.scalars(select(m.InternalStartDispatch))))==5
        assert db.scalar(select(m.BidIntakeCase)) is None
        assert db.scalar(select(m.PlanTask)) is None
        from domain_packs.mold.erp.project import change_start
        change_start.require_effective_notice(db,db.get(m.ContactCase,case_id),db.get(m.ContactResolution,args['source_subject_id']))
        args['project_version']=db.get(m.Project,ids['project']).row_version
        with pytest.raises(DomainError) as repeated:
            execute(db,db.get(m.User,ids['admin']),'prepare_internal_start',args,run=db.get(m.Run,run_id))
        assert repeated.value.code=='CHANGE_START_EXISTS'


@pytest.mark.parametrize('change,code',[
    ('wrong_mold','CHANGE_MOLD_MISMATCH'),('missing_attachment','CHANGE_START_EVIDENCE_REQUIRED'),
    ('paused','CHANGE_START_STATE'),('new_contract_without_date','EXPECTED_CONTRACT_DATE_REQUIRED'),
    ('source_changed','RESOLUTION_STALE'),
    ('supplier_terms_missing','SUPPLIER_CHANGE_TERMS_REQUIRED'),
])
def test_change_start_rejects_missing_or_changed_materials(client,data,change,code):
    ids,Session=data
    args,run_id,case_id=setup_change(client,data)
    with Session.begin() as db:
        if change=='wrong_mold':args['change']['mold_ids']=[str(uuid4())]
        if change=='missing_attachment':args['change']['notice_file_id']=str(uuid4())
        if change=='paused':db.get(m.Project,ids['project']).status='PAUSED'
        if change=='new_contract_without_date':args['change']['contract_mode']='NEW_CONTRACT_PENDING'
        if change=='source_changed':db.get(m.ContactCase,case_id).description='方案审批后改变范围'
        if change=='supplier_terms_missing':args['execution_mode']='FULL_OUTSOURCE'
        with pytest.raises(DomainError) as rejected:
            execute(db,db.get(m.User,ids['admin']),'prepare_internal_start',args,run=db.get(m.Run,run_id))
        assert rejected.value.code==code
        assert db.scalar(select(m.InternalStartSnapshot)) is None


def test_change_start_rechecks_original_mold_at_final_approval(client,data):
    ids,Session=data
    args,run_id,_=setup_change(client,data)
    receipt=prepare_confirm(Session,ids,args,run_id)
    with Session.begin() as db:
        db.get(m.Mold,args['change']['mold_ids'][0]).internal_number='CHANGED-AFTER-CONFIRMATION'
    result=approve(client,receipt['instance_id'])
    assert result['business_status']=='APPLY_BLOCKED'
    with Session() as db:
        assert db.scalar(select(m.InternalStartDispatch)) is None
        assert db.get(m.InternalStartSnapshot,receipt['subject_id']).linked_business['internal_molds'][0]['internal_number']=='ORIGINAL-MOLD'


def test_readiness_exposes_real_original_ids_and_hides_unauthorized_plan_material(client,data):
    from domain_packs.mold.erp.project import change_start
    ids,Session=data
    args,_,_=setup_change(client,data)
    with Session.begin() as db:
        project=db.get(m.Project,ids['project']);admin=db.get(m.User,ids['admin'])
        visible=execute(db,admin,'query_internal_start_readiness',{'project_id':project.id})['data'][0]['existing_mold_change_start']
        assert args['change']['mold_ids'][0] in {row['id'] for row in visible['original_molds']}
        assert visible['plan_candidates'][0]['source_subject_id']==args['source_subject_id']
        reader=m.User(username='start-reader',display_name='受限开工读者',password_hash='test')
        db.add(reader);db.flush()
        for permission,fields in [('contact.read',['*']),('contact_resolution.read',['id','number'])]:
            db.add(m.Grant(user_id=reader.id,permission=permission,effect='ALLOW',scope={'project_id':[project.id]},
                fields=fields,reason='受限读取验证',granted_by=admin.id))
        db.flush()
        hidden=change_start.context(db,reader,project)
        assert hidden['plan_candidates']==[]
        assert hidden['original_molds']==[]


def test_late_change_contract_must_explicitly_link_the_notice(client,data):
    from test_contract_tools import uploaded_contract_file, contract
    from domain_packs.mold.erp.project import start_materials
    from domain_packs.mold.erp.core import domains
    ids,Session=data
    args,run_id,_=setup_change(client,data)
    args['change'].update(charge_kind='CHARGED',amount='800.00',contract_mode='NEW_CONTRACT_PENDING')
    args['expected_contract_date']=date.today().isoformat()
    receipt=prepare_confirm(Session,ids,args,run_id)
    assert approve(client,receipt['instance_id'])['business_status']=='EFFECTIVE'
    definition=workflow(client,ids,'sales_contract')
    with Session.begin() as db:
        admin=db.get(m.User,ids['admin']);project=db.get(m.Project,ids['project'])
        run=db.get(m.Run,run_id)
        customer=m.Customer(code='CHANGE-CUSTOMER',name='设变客户',rule_key='standard',active=True)
        db.add(customer);db.flush()
        old=contract(db,project,admin,'sales_contract','UNRELATED-OLD')
        blob=uploaded_contract_file(db,admin,db.get(m.Conversation,run.conversation_id),filename='change-contract.pdf',digest='b'*64)
        db.add(m.RunFile(run_id=run.id,file_id=blob.id));db.flush()
        pending=start_materials.contract_follow_up(db,project.id,receipt['subject_id'],[domains.data(db,admin,old)])
        assert pending['state']=='DUE_TODAY' and pending['contracts']==[]
        contract_args={'project_id':project.id,'project_version':project.row_version,'contract_kind':'sales_contract',
            'customer_id':customer.id,'amount':'800.00','currency':'CNY','contract_number':'CHANGE-CONTRACT',
            'signed_date':date.today().isoformat(),'received_date':date.today().isoformat(),
            'delivery_due_date':date.today().isoformat(),'payment_method':'合同约定节点',
            'mapping_evidence':'本人确认此合同补齐本次设变开工','start_notice_subject_id':receipt['subject_id'],
            'stages':[{'name':'合同款','amount':'800.00','condition':'合同生效'}],
            'workflow_definition_id':definition,'file_ids':[blob.id],'document_source':'PAPER_SCAN'}
        with pytest.raises(DomainError) as wrong:
            execute(db,admin,'prepare_contract_record',{**contract_args,'start_notice_subject_id':str(uuid4())},run=run)
        assert wrong.value.code=='CONTRACT_START_MISMATCH'
        evidence=execute(db,admin,'prepare_contract_record',contract_args,run=run)
        step=m.Step(run_id=run.id,sequence=1,tool='prepare_contract_record',request_hash='change-contract',result=evidence)
        db.add(step);db.flush()
        intent=business.create_intent(db,admin,'contract.execute',step.id,
            {'step_id':step.id,'proposal_hash':bpm.content_hash(evidence['proposal'])})
        saved=business.confirm_intent(db,admin,intent['id'],intent['challenge'])
    assert approve(client,saved['instance_id'])['business_status']=='EFFECTIVE'
    with Session() as db:
        admin=db.get(m.User,ids['admin'])
        contracts=[domains.data(db,admin,row) for row in db.scalars(select(m.BusinessSubject).where(
            m.BusinessSubject.kind=='sales_contract',m.BusinessSubject.project_id==ids['project']))]
        received=start_materials.contract_follow_up(db,ids['project'],receipt['subject_id'],contracts)
        assert received['state']=='RECEIVED'
        assert [row['contract_number'] for row in received['contracts']]==['CHANGE-CONTRACT']
