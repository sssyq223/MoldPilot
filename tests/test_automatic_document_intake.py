"""上传自身创建识别任务，模型 Run 不是启动或人工确认的必要条件。"""
from uuid import uuid4
from decimal import Decimal
import pymupdf

import pytest
from sqlalchemy import select, func

from app import models as m, object_storage
from app.errors import DomainError
from conftest import sign_in
from test_files import PDF, isolated_storage, upload


def count(db, model):
    return db.scalar(select(func.count()).select_from(model))


def batch_upload(client, items=None, key=None, cid=None):
    params = {"request_key": key or str(uuid4())}
    if cid:
        params["conversation_id"] = cid
    return client.post("/api/files/batch", params=params, files=[
        ("files", (name, body, "application/octet-stream"))
        for name, body in (items or [("主合同.pdf", PDF), ("合同附件.pdf", PDF + b"\nattachment")])
    ])


def test_single_upload_queues_classification_without_run_or_confirmation(client, data):
    sign_in(client)
    key = str(uuid4())
    response = upload(client, key=key)
    assert response.status_code == 200, response.text
    again = upload(client, key=key)
    assert again.json()["id"] == response.json()["id"]
    with data[1]() as db:
        assert count(db, m.DocumentIntake) == 1
        assert count(db, m.DocumentIntakeFile) == 1
        job = db.scalar(select(m.DocumentOcrJob))
        assert (job.phase, job.status) == ("PRECLASSIFY", "QUEUED")
        assert count(db, m.Run) == count(db, m.Step) == count(db, m.HumanIntent) == 0


def test_batch_upload_is_atomic_idempotent_and_groups_only_pdfs(client, data):
    sign_in(client)
    key = str(uuid4())
    items = [("主合同.pdf", PDF), ("附件.pdf", PDF + b"\nattachment"), ("数据.csv", b"name,value\nx,1")]
    response = batch_upload(client, items, key)
    assert response.status_code == 200, response.text
    body = response.json()
    assert len(body["files"]) == 3
    repeated = batch_upload(client, items, key)
    assert repeated.status_code == 200, repeated.text
    assert [f["id"] for f in repeated.json()["files"]] == [f["id"] for f in body["files"]]
    assert batch_upload(client, items[:1], key).status_code == 409
    with data[1]() as db:
        assert count(db, m.FileObject) == 3
        assert count(db, m.Conversation) == 1
        assert count(db, m.DocumentIntake) == 1
        assert count(db, m.DocumentOcrJob) == 2
        assert count(db, m.Run) == count(db, m.HumanIntent) == 0


def test_invalid_batch_never_leaves_visible_files_or_jobs(client, data):
    sign_in(client)
    response = batch_upload(client, [("正常.pdf", PDF), ("伪造.pdf", b"not-pdf")])
    assert response.status_code == 400, response.text
    with data[1]() as db:
        assert count(db, m.FileObject) == count(db, m.DocumentOcrJob) == 0


def test_non_pdf_upload_does_not_enter_contract_pipeline(client, data):
    sign_in(client)
    response = upload(client, b"name,value\nx,1", "数据.csv")
    assert response.status_code == 200, response.text
    with data[1]() as db:
        assert count(db, m.DocumentIntake) == count(db, m.DocumentOcrJob) == 0


def test_upload_job_failure_rolls_back_file_transaction(client, data, monkeypatch):
    from domain_packs.mold.erp.commercial import contract_intake
    sign_in(client)
    def unavailable(*args, **kwargs):
        raise DomainError("INTAKE_UNAVAILABLE", "识别任务无法登记", 503)
    monkeypatch.setattr(contract_intake, "create", unavailable)
    response = upload(client)
    assert response.status_code == 503, response.text
    with data[1]() as db:
        assert count(db, m.FileObject) == count(db, m.DocumentIntake) == count(db, m.DocumentOcrJob) == 0


def classified_upload(client, data):
    from app.document_worker import run_once
    from domain_packs.mold.erp.commercial.ocr_provider import Classification
    pdf = pymupdf.open()
    pdf.new_page().insert_text((60, 70), 'SALES CONTRACT SC-TEST-001 ' * 3)
    body = pdf.tobytes()
    pdf.close()
    blob = upload(client, body, '待确认合同.pdf').json()
    class Provider:
        name = 'synthetic-classifier'
        def classify(self, document, **kwargs):
            assert document.page_count == 1
            return Classification('SALES_CONTRACT', Decimal('0.98'))
    assert run_once(data[1], Provider())
    with data[1]() as db:
        intake = db.scalar(select(m.DocumentIntake))
        row = db.scalar(select(m.DocumentIntakeFile))
        return blob, intake.id, {'document_intake_id': intake.id, 'expected_version': intake.row_version,
                                'files': [{'intake_file_id': row.id, 'document_type': 'SALES_CONTRACT',
                                           'contract_group_key': '合同1'}]}


def test_background_types_use_human_intent_without_agent_run(client, data):
    sign_in(client)
    blob, iid, body = classified_upload(client, data)
    status = client.get(f"/api/conversations/{blob['conversation_id']}/documents")
    assert status.status_code == 200, status.text
    item = status.json()[0]
    assert item['status'] == 'AWAITING_TYPE_CONFIRMATION'
    assert item['files'][0]['suggested_type'] == 'SALES_CONTRACT'
    assert item['files'][0]['ocr_job']['status'] == 'SUCCEEDED'
    response = client.post(f'/api/document-intakes/{iid}/type-intent', json=body)
    assert response.status_code == 200, response.text
    intent = response.json()
    with data[1]() as db:
        assert count(db, m.Run) == count(db, m.Step) == 0
        assert db.scalar(select(m.DocumentIntakeFile)).confirmed_type is None
        assert count(db, m.DocumentOcrJob) == 1
    confirm = client.post(f"/api/human-actions/{intent['id']}/confirm", json={'challenge':intent['challenge']})
    assert confirm.status_code == 200, confirm.text
    assert confirm.json()['status'] == 'FULL_OCR_QUEUED'
    assert client.post(f"/api/human-actions/{intent['id']}/confirm", json={'challenge':intent['challenge']}).json() == confirm.json()
    with data[1]() as db:
        assert count(db,m.DocumentOcrJob) == 2
        assert db.scalar(select(m.DocumentIntakeFile)).confirmed_type == 'SALES_CONTRACT'
        assert count(db,m.Run) == count(db,m.Step) == 0


def test_bid_classification_confirmation_is_idempotent_and_only_emits_gate_event(client, data):
    from app.document_worker import run_once
    from domain_packs.mold.erp.commercial.ocr_provider import Classification

    sign_in(client)
    pdf = pymupdf.open()
    pdf.new_page().insert_text((60, 70), 'Congratulations your company won bid project P-BID-001 Customer Buyer Delivery 30 days')
    body = pdf.tobytes()
    pdf.close()
    blob = upload(client, body, '中标通知.pdf').json()

    class Provider:
        name = 'synthetic-bid-classifier'

        def classify(self, document, **kwargs):
            return Classification(
                'BID_NOTICE', Decimal('0.96'), event_type='BID_WON', decision='NEEDS_REVIEW',
                evidence=({'page': 1, 'text': '恭喜贵司中标项目', 'rule': 'AWARD_RESULT'},),
                classifier_version='bid-classifier-v1', needs_human_confirmation=True,
            )

    assert run_once(data[1], Provider())
    classification = client.get(f"/api/files/{blob['id']}/document-classification").json()
    assert classification['status'] == 'CLASSIFIED'
    assert classification['classification']['event_type'] == 'BID_WON'

    with data[1]() as db:
        intake = db.scalar(select(m.DocumentIntake))
        row_version = intake.row_version
    payload = {
        'classification_id': classification['classification_id'],
        'row_version': row_version,
        'document_type': 'BID_NOTICE',
        'reason': '人工核对中标通知证据',
    }
    response = client.post(
        f"/api/files/{blob['id']}/document-classification/confirm",
        headers={'Idempotency-Key': 'bid-confirm-operation-001'}, json=payload,
    )
    assert response.status_code == 200, response.text
    receipt = response.json()
    assert receipt['status'] == 'CONFIRMED'
    assert receipt['bid_notice_event_id']
    assert receipt['admin_start_draft_id']
    documents = client.get(f"/api/conversations/{blob['conversation_id']}/documents")
    assert documents.status_code == 200, documents.text
    assert documents.json()[0]['admin_start_drafts'][0]['id'] == receipt['admin_start_draft_id']
    assert documents.json()[0]['admin_start_drafts'][0]['status'] == 'ADMIN_PENDING_INPUT'
    replay = client.post(
        f"/api/files/{blob['id']}/document-classification/confirm",
        headers={'Idempotency-Key': 'bid-confirm-operation-001'}, json=payload,
    )
    assert replay.status_code == 200, replay.text
    assert replay.json()['event_id'] == receipt['event_id']
    with data[1]() as db:
        assert db.scalar(select(func.count()).select_from(m.AuditEvent).where(
            m.AuditEvent.action == 'bid_notice.confirmed',
        )) == 1
        assert db.scalar(select(func.count()).select_from(m.BidIntakeCase)) == 0
        assert db.scalar(select(func.count()).select_from(m.AdminStartNoticeDraft)) == 1
        assert db.scalar(select(func.count()).select_from(m.AdminStartNoticeRevision)) == 1
        assert db.scalar(select(func.count()).select_from(m.StartNoticeDepartmentAck)) == 0


@pytest.mark.parametrize('change', ['version', 'authorization', 'archive', 'payload', 'expired', 'challenge'])
def test_background_intent_rechecks_snapshot_and_permission(client, data, change):
    sign_in(client)
    blob, iid, body = classified_upload(client, data)
    response = client.post(f'/api/document-intakes/{iid}/type-intent', json=body)
    assert response.status_code == 200, response.text
    intent = response.json()
    with data[1].begin() as db:
        if change == 'version': db.get(m.DocumentIntake,iid).row_version += 1
        elif change == 'authorization': db.get(m.User,data[0]['admin']).security_version += 1
        elif change == 'archive': db.get(m.Conversation,blob['conversation_id']).archived = True
        elif change == 'expired':
            from datetime import timedelta
            from app.db import now
            db.get(m.HumanIntent,intent['id']).expires_at = now()-timedelta(seconds=1)
        elif change == 'payload': db.get(m.HumanIntent,intent['id']).payload = {'changed':True}
    response = client.post(f"/api/human-actions/{intent['id']}/confirm", json={'challenge':(('0' if intent['challenge'][0]!='0' else '1')+intent['challenge'][1:]) if change=='challenge' else intent['challenge']})
    assert response.status_code in {403,409}, response.text
    with data[1]() as db:
        assert count(db,m.DocumentOcrJob) == 1
        assert db.scalar(select(m.DocumentIntakeFile)).confirmed_type is None


def test_other_user_cannot_read_or_confirm_document_task(client, data):
    sign_in(client)
    blob, iid, body = classified_upload(client, data)
    sign_in(client, 'test_buyer')
    assert client.get(f"/api/conversations/{blob['conversation_id']}/documents").status_code == 404
    assert client.post(f'/api/document-intakes/{iid}/type-intent', json=body).status_code == 404


def test_document_notification_reaches_owner_and_links_conversation(client, data):
    from app.message_worker import deliver
    with data[1].begin() as db:
        db.add(m.Grant(user_id=data[0]['buyer'],permission='file.upload',effect='ALLOW',scope={'all':True},
                       fields=['*'],reason='文档上传',granted_by=data[0]['admin']))
    sign_in(client,'test_buyer')
    blob, iid, body = classified_upload(client,data)
    with data[1]() as db:
        event = db.scalar(select(m.Outbox).where(m.Outbox.kind=='document.ocr.completed'))
        eid = event.id
    assert deliver(data[1],eid)=='DELIVERED'
    assert deliver(data[1],eid)=='DUPLICATE'
    notices = client.get('/api/notifications').json()
    matching = [n for n in notices if n['kind']=='document.ocr.completed']
    assert len(matching)==1
    assert matching[0]['conversation_id']==blob['conversation_id']
    with data[1].begin() as db:
        db.add(m.Grant(user_id=data[0]['buyer'],permission='file.upload',effect='DENY',scope={'all':True},
                       fields=['*'],reason='撤回文件权限',granted_by=data[0]['admin']))
    assert client.get('/api/notifications').json()==[]
    assert client.get(f"/api/conversations/{blob['conversation_id']}/documents").json()==[]


def test_full_job_claim_updates_status_and_failure_notification(client, data):
    from app.document_worker import claim, process_claim
    from app.message_worker import deliver
    sign_in(client)
    blob, iid, body = classified_upload(client,data)
    intent=client.post(f'/api/document-intakes/{iid}/type-intent',json=body).json()
    response=client.post(f"/api/human-actions/{intent['id']}/confirm",json={'challenge':intent['challenge']})
    assert response.status_code==200,response.text
    with data[1].begin() as db:
        db.scalar(select(m.DocumentOcrJob).where(m.DocumentOcrJob.phase=='FULL_CONTRACT')).attempts=4
    jid, lease=claim(data[1])
    with data[1]() as db:
        assert db.get(m.DocumentIntake,iid).status=='FULL_OCR_PROCESSING'
    class Provider:
        name='synthetic-failure'
        def extract_sales_contract(self, document, **kwargs):
            raise DomainError('DOCUMENT_MODEL_READ_TIMEOUT','模型空闲超时',503)
    process_claim(data[1],jid,lease,Provider())
    with data[1]() as db:
        assert db.get(m.DocumentIntake,iid).status=='OCR_FAILED'
        assert db.get(m.DocumentOcrJob,jid).last_error=='DOCUMENT_MODEL_READ_TIMEOUT'
        eid=db.scalar(select(m.Outbox.id).where(m.Outbox.kind=='document.ocr.failed'))
    deliver(data[1],eid)
    assert any(n['kind']=='document.ocr.failed' for n in client.get('/api/notifications').json())


def test_model_progress_renews_lease_during_generation(client, data, monkeypatch):
    from datetime import timedelta
    from app import document_worker as worker
    from domain_packs.mold.erp.commercial.ocr_provider import Classification
    sign_in(client)
    pdf=pymupdf.open();pdf.new_page().insert_text((60,70),'CONTRACT TEXT '*8)
    body=pdf.tobytes();pdf.close();upload(client,body)
    jid,lease=worker.claim(data[1]);real_now=worker.now
    class Provider:
        name='lease-test'
        def classify(self, document, *, on_progress=None, on_call=None):
            assert on_progress is not None
            future=real_now()+timedelta(minutes=2)
            monkeypatch.setattr(worker,'now',lambda:future)
            on_progress()
            with data[1]() as db:
                assert db.get(m.DocumentOcrJob,jid).lease_until >= future+timedelta(minutes=5)
            return Classification('OTHER',Decimal('0.9'))
    worker.process_claim(data[1],jid,lease,Provider())
    with data[1]() as db:
        assert db.get(m.DocumentOcrJob,jid).status=='SUCCEEDED'
