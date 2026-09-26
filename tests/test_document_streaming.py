"""文档模型协议：流式、供应商参数、可信来源元数据与有界纠正。"""
import json
from dataclasses import replace
from decimal import Decimal
from types import SimpleNamespace

import httpx
import pytest

from domain_packs.mold.erp.commercial.ocr_provider import DocumentTextProvider, RecognizedDocument, RecognizedPage
from domain_packs.mold.erp.commercial.pdf_analysis import PageTextBlock
from domain_packs.mold.ports.errors import DomainError


def config(**overrides):
    return SimpleNamespace(document_model_base_url='https://model.example/v1', document_model_api_key='synthetic',
        document_model='GLM-5.3-Flash', document_model_connect_timeout=1,document_model_read_timeout=1,
        document_model_trusted_http_origin='',document_model_reasoning_effort='low',
        document_model_total_timeout=120, **overrides)


def document():
    block = PageTextBlock('p1-t0001','合同编号 SC-001',(1,2,20,30),'TEXT_LAYER',1.0)
    return RecognizedDocument((RecognizedPage(1,(block,)),))


def candidate(**changes):
    return {'scope':'HEADER','field_key':'contract_number','normalized_value':'SC-001',
            'confidence':0.98,'source_block_ids':['p1-t0001'],**changes}


def stream(payload):
    content=json.dumps(payload,ensure_ascii=False)
    events=[{'choices':[{'delta':{'content':content},'finish_reason':None}]},
            {'choices':[{'delta':{},'finish_reason':'stop'}]}]
    return httpx.Response(200,headers={'content-type':'text/event-stream'},
        content=''.join('data: '+json.dumps(e)+'\n\n' for e in events)+'data: [DONE]\n\n')


def test_document_stream_derives_header_identity_and_page_from_sources():
    def handler(request):
        payload=json.loads(request.content)
        assert payload['stream'] is True
        assert payload['reasoning_effort']=='low'
        return stream({'fields':[candidate(row_key=None)]})
    provider=DocumentTextProvider(config(),transport=httpx.MockTransport(handler))
    try:
        result=provider.extract_sales_contract(document())
        field=result.fields[0]
        assert (field.row_key,field.page_number)==('header',1)
        assert field.raw_value=={'value':'合同编号 SC-001'}
        assert field.bbox['x0']==1
    finally:provider.adapter.close()


def test_document_classification_uses_deterministic_temperature():
    def handler(request):
        payload=json.loads(request.content)
        assert payload['temperature'] == 0
        return stream({'document_type':'OTHER','event_type':'UNKNOWN','confidence':0.9,
                       'evidence':[],'conflicts':[],'extracted':{},'bid_fields':[],
                       'classifier_version':'test','needs_human_confirmation':True})
    provider=DocumentTextProvider(config(),transport=httpx.MockTransport(handler))
    try:
        assert provider.classify(document()).document_type == 'SALES_CONTRACT'
    finally:provider.adapter.close()


@pytest.mark.parametrize('elapsed',[61,121])
def test_streaming_total_limit_is_separate_from_idle_timeout(monkeypatch,elapsed):
    from domain_packs.mold.erp.commercial import ocr_provider as module
    clock=[0]
    monkeypatch.setattr(module,'time',SimpleNamespace(monotonic=lambda:clock[0]))
    def handler(request):
        assert request.extensions['timeout']['read']==1
        clock[0]=elapsed
        return stream({'fields':[candidate()]})
    provider=DocumentTextProvider(config(),transport=httpx.MockTransport(handler))
    try:
        if elapsed>120:
            with pytest.raises(DomainError) as error:provider.extract_sales_contract(document())
            assert error.value.code=='DOCUMENT_MODEL_TOTAL_TIMEOUT'
        else:assert len(provider.extract_sales_contract(document()).fields)==1
    finally:provider.adapter.close()


def test_idle_timeout_preserves_specific_failure_code():
    def handler(request):raise httpx.ReadTimeout('synthetic timeout')
    provider=DocumentTextProvider(config(),transport=httpx.MockTransport(handler))
    try:
        with pytest.raises(DomainError) as error:provider.extract_sales_contract(document())
        assert error.value.code=='DOCUMENT_MODEL_READ_TIMEOUT'
    finally:provider.adapter.close()


def test_invalid_source_is_reextracted_once_not_silently_dropped():
    sent=[]
    def handler(request):
        sent.append(json.loads(request.content))
        bad=len(sent)==1
        return stream({'fields':[candidate(source_block_ids=['p9-missing'] if bad else ['p1-t0001'])]})
    provider=DocumentTextProvider(config(),transport=httpx.MockTransport(handler))
    try:
        result=provider.extract_sales_contract(document())
        assert len(sent)==2 and len(result.fields)==1
        assert result.fields[0].source_block_ids==('p1-t0001',)
    finally:provider.adapter.close()


def test_exhausted_source_correction_rejects_the_entire_batch():
    attempts=[]
    def handler(request):
        attempts.append(1)
        return stream({'fields':[candidate(),candidate(field_key='amount',source_block_ids=['p9-missing'])]})
    provider=DocumentTextProvider(config(),transport=httpx.MockTransport(handler))
    try:
        with pytest.raises(DomainError) as error:provider.extract_sales_contract(document())
        assert error.value.code=='DOCUMENT_FIELD_SOURCE_INVALID'
        assert len(attempts)==2
    finally:provider.adapter.close()


@pytest.mark.parametrize('cache_case',['reuse','evicted','model_changed','empty'])
def test_completed_model_batches_are_reused_after_retry(monkeypatch,cache_case):
    from app import document_worker as worker, models as m
    from app.config import settings
    from agent_core.host_ports import host_ports
    from sqlalchemy import select, delete
    from test_contract_ocr import _full_contract_fixture
    import pymupdf
    engine, factory, ids = _full_contract_fixture(1)
    pdf=pymupdf.open()
    for index in range(5):pdf.new_page().insert_text((60,70),'CONTRACT SC-001 amount 100 '*4)
    body=pdf.tobytes();pdf.close()
    monkeypatch.setattr(host_ports().object_storage,'read',lambda blob:body)
    seen=[];fail=[True]
    def handler(request):
        content=json.loads(request.content)['messages'][0]['content']
        page=5 if '第 5 页文字块' in content else 1
        seen.append(page)
        if page==5 and fail[0]:raise httpx.ReadTimeout('synthetic timeout')
        if page==1 and cache_case=='empty':return stream({'fields':[]})
        return stream({'fields':[candidate(field_key='amount' if page==5 else 'contract_number',source_block_ids=[f'p{page}-t0001'])]})
    provider=DocumentTextProvider(config(),transport=httpx.MockTransport(handler))
    try:
        assert worker.run_once(factory,provider)
        with factory.begin() as db:
            job=db.get(m.DocumentOcrJob,ids['jobs'][0]);assert job.status=='RETRY_WAIT'
            job.status='QUEUED';job.retry_at=None
            if cache_case=='evicted':
                db.execute(delete(m.AuditEvent).where(m.AuditEvent.action=='document.ocr.batch.completed'))
        if cache_case=='model_changed':provider.adapter.model='another-document-model'
        fail[0]=False
        assert worker.run_once(factory,provider)
        with factory() as db:
            job=db.get(m.DocumentOcrJob,ids['jobs'][0]);assert job.status=='SUCCEEDED'
            fields=list(db.scalars(select(m.DocumentExtractedField).where(m.DocumentExtractedField.job_id==job.id)))
            assert {f.field_key for f in fields}==({'amount'} if cache_case=='empty' else {'contract_number','amount'})
            assert {f.row_key for f in fields}=={'header'}
        assert seen.count(1)==(2 if cache_case in {'evicted','model_changed'} else 1)
    finally:provider.adapter.close();engine.dispose()


def test_lost_lease_cannot_commit_or_fail_new_owners_job(monkeypatch):
    from uuid import uuid4
    from sqlalchemy import select,func
    from app import document_worker as worker, models as m
    from agent_core.host_ports import host_ports
    from test_contract_ocr import _full_contract_fixture, _text_pdf
    engine,factory,ids=_full_contract_fixture(1)
    monkeypatch.setattr(host_ports().object_storage,'read',lambda blob:_text_pdf())
    replacement=str(uuid4())
    class Provider:
        name='lease-test'
        def extract_sales_contract(self,document,*,on_progress,**kwargs):
            with factory.begin() as db:db.get(m.DocumentOcrJob,ids['jobs'][0]).lease_id=replacement
            on_progress()
            pytest.fail('失租后不应继续提取')
    try:
        assert worker.run_once(factory,Provider())
        with factory() as db:
            job=db.get(m.DocumentOcrJob,ids['jobs'][0])
            assert job.status=='PROCESSING' and job.lease_id==replacement and job.attempts==0
            assert db.scalar(select(func.count()).select_from(m.DocumentExtractedField))==0
            assert db.scalar(select(func.count()).select_from(m.Outbox).where(m.Outbox.kind=='document.ocr.completed'))==0
    finally:engine.dispose()


def test_each_new_job_resolves_fresh_document_profile(monkeypatch):
    from app import document_worker as worker, models as m
    from agent_core.host_ports import host_ports
    from test_contract_ocr import _full_contract_fixture, _text_pdf
    from domain_packs.mold.erp.commercial.ocr_provider import ContractExtraction
    engine,factory,ids=_full_contract_fixture(2)
    configurations=iter([SimpleNamespace(model='first-profile'),SimpleNamespace(model='updated-profile')])
    monkeypatch.setattr(worker,'document_model_settings',lambda:next(configurations))
    monkeypatch.setattr(host_ports().object_storage,'read',lambda blob:_text_pdf())
    class Provider:
        def __init__(self,config):
            self.name=config.model
            self.adapter=SimpleNamespace(close=lambda:None)
        def extract_sales_contract(self,document,**kwargs):return ContractExtraction(())
    monkeypatch.setattr(worker,'DocumentTextProvider',Provider)
    try:
        assert worker.run_once(factory)
        assert worker.run_once(factory)
        with factory() as db:
            assert {db.get(m.DocumentOcrJob,jid).provider for jid in ids['jobs']}=={'first-profile','updated-profile'}
    finally:engine.dispose()


def test_document_model_profile_is_pinned_not_following_chat_selection(monkeypatch):
    from app import config as module
    assert hasattr(module,'document_model_settings')
    monkeypatch.setattr(module.settings(),'document_model_profile_id','doc')
    monkeypatch.setattr(module,'_profile_document',lambda:{'active_profile_id':'chat','profiles':[
        {'id':'doc','llm_provider':'company','llm_enabled':True,'llm_model':'GLM-5.3-Flash',
         'llm_base_url':'https://document.example/v1','llm_api_key':'synthetic-doc'},
        {'id':'chat','llm_provider':'company','llm_enabled':True,'llm_model':'another-model',
         'llm_base_url':'https://chat.example/v1','llm_api_key':'synthetic-chat'}]})
    config=module.document_model_settings()
    assert config.document_model=='GLM-5.3-Flash'
    assert config.document_model_base_url=='https://document.example/v1'
    assert config.document_model_api_key=='synthetic-doc'
    monkeypatch.setattr(module.settings(),'document_model_profile_id','missing')
    with pytest.raises(ValueError):module.document_model_settings()
