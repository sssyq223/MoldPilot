"""仅在既有隔离测试库中写合成数据，外层事务全部回滚；禁止 DDL/迁移。"""
from uuid import uuid4
from types import SimpleNamespace

import pytest
from sqlalchemy import create_engine, event, select
from sqlalchemy.orm import Session

from pg_db import _test_database_url
from domain_packs.mold import models as m


@pytest.fixture
def contact_document_db():
    url = _test_database_url()
    if not url:
        pytest.skip('需要本机隔离测试库')
    engine = create_engine(url, hide_parameters=True, connect_args={
        'connect_timeout': 5, 'options': '-c statement_timeout=10000 -c lock_timeout=3000'})
    if (engine.url.host, engine.url.port, engine.url.database) != ('127.0.0.1', 55432, 'moldpilot_test'):
        engine.dispose()
        raise RuntimeError('拒绝访问非隔离测试库')

    @event.listens_for(engine, 'before_cursor_execute')
    def forbid_ddl(conn, cursor, statement, parameters, context, executemany):
        if statement.lstrip().split(None, 1)[0].upper() in {'CREATE', 'ALTER', 'DROP', 'TRUNCATE'}:
            raise AssertionError('本测试不允许结构变更或清空表')

    try:
        with engine.connect() as conn:
            transaction = conn.begin()
            db = Session(bind=conn, expire_on_commit=False, join_transaction_mode='create_savepoint')
            try:
                yield db
            finally:
                db.close()
                transaction.rollback()
    finally:
        engine.dispose()


@pytest.fixture
def contact_document_context(contact_document_db):
    db = contact_document_db
    owner = m.User(username='contact_doc_' + uuid4().hex, display_name='合成测试人员',
                   password_hash='not-a-login', super_admin=True)
    db.add(owner); db.flush()
    conversation = m.Conversation(user_id=owner.id, title='工程联络合成测试')
    project = m.Project(code='CD-' + uuid4().hex[:12], name='合成项目', status='DRAFT')
    db.add_all([conversation, project]); db.flush()
    blob = m.FileObject(owner_id=owner.id, conversation_id=conversation.id, request_key=str(uuid4()),
        filename='synthetic.pdf', media_type='application/pdf', size=1, sha256=uuid4().hex * 2,
        backend='local', storage_namespace='test', object_key=uuid4().hex)
    intake = m.DocumentIntake(conversation_id=conversation.id, created_by=owner.id,
        request_key=str(uuid4()), status='AWAITING_TYPE_CONFIRMATION')
    db.add_all([blob, intake]); db.flush()
    item = m.DocumentIntakeFile(intake_id=intake.id, file_id=blob.id)
    db.add(item); db.flush()
    job = m.DocumentOcrJob(intake_file_id=item.id, phase='PRECLASSIFY', status='SUCCEEDED')
    db.add(job); db.flush()
    classification = m.AuditEvent(action='document.ocr.completed', resource_id=job.id,
        detail={'classification': {'document_type': 'ENGINEERING_CONTACT', 'event_type': 'ENGINEERING_CONTACT',
            'confidence': '0.95', 'decision': 'NEEDS_REVIEW', 'needs_human_confirmation': True,
            'classifier_version': 'contact-v1', 'contact_fields': [], 'evidence': [], 'conflicts': []}})
    db.add(classification); db.flush()
    return SimpleNamespace(db=db, owner=owner, conversation=conversation, project=project,
        blob=blob, intake=intake, item=item, job=job, classification=classification)
