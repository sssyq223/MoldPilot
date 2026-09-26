"""抓住截断后原样重试、子批次缓存丢失以及错误状态混淆。只用合成文档。"""
import json
import re
from types import SimpleNamespace

import httpx
import pytest

from domain_packs.mold.erp.commercial.ocr_provider import DocumentTextProvider, RecognizedDocument, RecognizedPage
from domain_packs.mold.erp.commercial.pdf_analysis import PageTextBlock
from domain_packs.mold.ports.errors import DomainError
from test_document_streaming import config, stream


def document(count=4, text_size=20):
    return RecognizedDocument(tuple(RecognizedPage(page, (
        PageTextBlock(f'p{page}-t0001', 'x' * text_size, (1, 2, 30, 40), 'TEXT_LAYER', 1.0),
    )) for page in range(1, count + 1)))


def request_pages(request):
    body = json.loads(request.content)
    assert body['max_tokens'] == 8192
    assert body['reasoning_effort'] == 'low'
    return [int(n) for n in re.findall(r'--- 第 (\d+) 页文字块 ---', body['messages'][0]['content'])]


def fields(pages):
    return {'fields': [{'scope': 'MOLD', 'row_key': '1', 'field_key': 'customer_mold_number',
        'normalized_value': f'SYNTHETIC-{page}', 'confidence': 0.9,
        'source_block_ids': [f'p{page}-t0001']} for page in pages]}


def truncated():
    event = {'choices': [{'delta': {'content': '{"fields":['}, 'finish_reason': 'length'}],
        'usage': {'prompt_tokens': 123, 'completion_tokens': 8192,
            'completion_tokens_details': {'reasoning_tokens': 7000}, 'secret': 'never-record-this'}}
    return httpx.Response(200, headers={'content-type': 'text/event-stream'},
        content='data: '+json.dumps(event)+'\n\ndata: [DONE]\n\n')


def test_truncated_parent_is_split_without_increasing_tokens_or_losing_rows():
    seen=[]
    def handler(request):
        pages=request_pages(request);seen.append(pages)
        return truncated() if len(pages)>2 else stream(fields(pages))
    provider=DocumentTextProvider(config(),transport=httpx.MockTransport(handler))
    try:
        result=provider.extract_sales_contract(document())
        assert seen==[[1,2,3,4],[1,2],[3,4]]
        assert {f.normalized_value['value'] for f in result.fields}=={'SYNTHETIC-1','SYNTHETIC-2','SYNTHETIC-3','SYNTHETIC-4'}
        assert {f.row_key for f in result.fields}=={'p1:1','p2:1','p3:1','p4:1'}
    finally:provider.adapter.close()


def test_input_volume_is_split_before_sending_an_oversized_request():
    seen=[]
    def handler(request):
        pages=request_pages(request);seen.append(pages)
        return stream(fields(pages))
    provider=DocumentTextProvider(config(),transport=httpx.MockTransport(handler))
    try:
        assert len(provider.extract_sales_contract(document(text_size=20000)).fields)==4
        assert seen==[[1],[2],[3],[4]]
    finally:provider.adapter.close()


def test_single_page_truncation_stops_instead_of_repeating_the_same_request():
    seen=[]
    def handler(request):
        seen.append(request_pages(request));return truncated()
    provider=DocumentTextProvider(config(),transport=httpx.MockTransport(handler))
    try:
        with pytest.raises(DomainError) as error:provider.extract_sales_contract(document(1))
        assert error.value.code=='DOCUMENT_MODEL_BATCH_TOO_LARGE'
        assert seen==[[1]]
    finally:provider.adapter.close()


def test_call_metrics_include_failure_and_usage_but_not_source_or_credentials():
    records=[]
    provider=DocumentTextProvider(config(),transport=httpx.MockTransport(lambda request:truncated()))
    try:
        with pytest.raises(DomainError):provider.extract_sales_contract(document(1),on_call=records.append)
        assert len(records)==1
        assert records[0]['finish_reason']=='length'
        assert records[0]['error_code']=='DOCUMENT_MODEL_OUTPUT_TRUNCATED'
        assert records[0]['completion_tokens']==8192 and records[0]['reasoning_tokens']==7000
        assert records[0]['pages']==[1] and records[0]['reasoning_effort']=='low'
        dumped=json.dumps(records)
        assert 'never-record-this' not in dumped and 'synthetic' not in dumped
        assert 'source_block_ids' not in dumped and 'messages' not in dumped and 'content' not in dumped
    finally:provider.adapter.close()


def test_invalid_sources_still_fail_after_bounded_correction_and_are_diagnosed():
    records=[]
    bad=fields([1]);bad['fields'][0]['source_block_ids']=['unknown-block']
    provider=DocumentTextProvider(config(),transport=httpx.MockTransport(lambda request:stream(bad)))
    try:
        with pytest.raises(DomainError) as error:provider.extract_sales_contract(document(1),on_call=records.append)
        assert error.value.code=='DOCUMENT_FIELD_SOURCE_INVALID'
        assert len(records)==2
        assert all(r['error_code']=='DOCUMENT_FIELD_SOURCE_INVALID' for r in records)
    finally:provider.adapter.close()


def test_model_call_count_is_bounded_even_when_every_parent_needs_splitting():
    seen=[]
    def handler(request):
        pages=request_pages(request);seen.append(pages)
        return truncated() if len(pages)>1 else stream(fields(pages))
    provider=DocumentTextProvider(config(),transport=httpx.MockTransport(handler))
    try:
        with pytest.raises(DomainError) as error:provider.extract_sales_contract(document(20))
        assert error.value.code=='DOCUMENT_MODEL_CALL_LIMIT'
        assert len(seen)==22  # 初始5批各允许一次协议纠正，另给12次细分余量。
    finally:provider.adapter.close()


def test_retry_restores_split_plan_and_reuses_completed_child_batches(monkeypatch):
    from sqlalchemy import select
    from app import document_worker as worker, models as m
    from agent_core.host_ports import host_ports
    from test_contract_ocr import _full_contract_fixture
    import pymupdf
    engine,factory,ids=_full_contract_fixture(1)
    pdf=pymupdf.open()
    for _ in range(4):pdf.new_page().insert_text((60,70),'SYNTHETIC CONTRACT '*5)
    data=pdf.tobytes();pdf.close()
    monkeypatch.setattr(host_ports().object_storage,'read',lambda blob:data)
    seen=[];fail=[True]
    def handler(request):
        pages=request_pages(request);seen.append(pages)
        if len(pages)==4:return truncated()
        if pages==[3,4] and fail[0]:raise httpx.ReadTimeout('synthetic timeout')
        return stream(fields(pages))
    provider=DocumentTextProvider(config(),transport=httpx.MockTransport(handler))
    try:
        worker.run_once(factory,provider)
        with factory.begin() as db:
            job=db.get(m.DocumentOcrJob,ids['jobs'][0]);assert job.status=='RETRY_WAIT'
            job.status='QUEUED';job.retry_at=None
        fail[0]=False
        provider.adapter.close()
        provider=DocumentTextProvider(config(),transport=httpx.MockTransport(handler))
        worker.run_once(factory,provider)
        with factory() as db:
            job=db.get(m.DocumentOcrJob,ids['jobs'][0]);assert job.status=='SUCCEEDED'
            stored=list(db.scalars(select(m.DocumentExtractedField).where(m.DocumentExtractedField.job_id==job.id)))
            assert len(stored)==4
            assert {r.normalized_value['value'] for r in stored}=={'SYNTHETIC-1','SYNTHETIC-2','SYNTHETIC-3','SYNTHETIC-4'}
            audits=list(db.scalars(select(m.AuditEvent).where(m.AuditEvent.action=='document.ocr.model_call.completed',m.AuditEvent.resource_id==job.id)))
            assert audits and any(r.detail.get('finish_reason')=='length' for r in audits)
            assert {r.detail['attempt'] for r in audits}=={1,2}
        assert seen.count([1,2,3,4])==1 and seen.count([1,2])==1
    finally:provider.adapter.close();engine.dispose()


def test_terminal_batch_limit_does_not_auto_retry_and_retry_state_is_unambiguous(monkeypatch):
    from app import document_worker as worker, models as m
    from domain_packs.mold.erp.commercial.contract_intake import serialize
    from test_contract_ocr import _full_contract_fixture
    engine,factory,ids=_full_contract_fixture(1)
    try:
        with factory.begin() as db:
            job=db.get(m.DocumentOcrJob,ids['jobs'][0]);job.attempts=1;job.last_error='DOCUMENT_MODEL_OUTPUT_TRUNCATED'
        jid,lease=worker.claim(factory)
        with factory() as db:
            job=db.get(m.DocumentOcrJob,jid);file=db.get(m.DocumentIntakeFile,job.intake_file_id)
            snapshot=serialize(db,db.get(m.DocumentIntake,file.intake_id))['files'][0]['ocr_job']
            assert snapshot['current_attempt']==2
            assert snapshot['previous_error']=='DOCUMENT_MODEL_OUTPUT_TRUNCATED'
            assert snapshot['error_code'] is None
        worker._fail(factory,jid,lease,DomainError('DOCUMENT_MODEL_BATCH_TOO_LARGE','合成测试'))
        with factory() as db:
            job=db.get(m.DocumentOcrJob,jid)
            assert job.status=='FAILED' and job.retry_at is None
            snapshot=serialize(db,db.get(m.DocumentIntake,file.intake_id))['files'][0]['ocr_job']
            assert snapshot['current_attempt'] is None
            assert snapshot['previous_error'] is None
            assert snapshot['error_code']=='DOCUMENT_MODEL_BATCH_TOO_LARGE'
    finally:engine.dispose()
