"""中标流程的事务隔离 PostgreSQL 测试支架：不迁移、不清空已有表。"""
from pathlib import Path
from tempfile import NamedTemporaryFile
from uuid import uuid4
from types import SimpleNamespace

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session
from sqlalchemy.schema import CreateTable, CreateIndex

from pg_db import _test_database_url
from domain_packs.mold import models as m
from domain_packs.mold.ports.db import now
from domain_packs.mold.erp.commercial.document_workflow import record_confirmed_bid_event


@pytest.fixture
def bid_db():
    engine = create_engine(_test_database_url(), hide_parameters=True,
                           connect_args={"connect_timeout": 5, "options": "-c statement_timeout=10000 -c lock_timeout=3000"})
    if (engine.url.host, engine.url.port, engine.url.database) != ("127.0.0.1", 55432, "moldpilot_test"):
        engine.dispose()
        raise RuntimeError("测试仅允许 127.0.0.1:55432/moldpilot_test")
    schema = "test_bid_" + uuid4().hex
    with engine.connect() as conn:
        transaction = conn.begin()
        db = None
        try:
            # 仅新领域表放入私有 schema；旧表中的合成资料也都随外层事务回滚。
            conn.execute(text(f'CREATE SCHEMA "{schema}"'))
            conn.execute(text(f'SET LOCAL search_path TO "{schema}", public'))
            names = {
                "bid_notice_match", "start_notice", "start_notice_department_ack",
                "project_start_decision", "post_start_binding",
                "admin_start_notice_draft", "admin_start_notice_revision",
                "admin_start_department_ack", "start_contract_match_candidate",
                "local_change_intake", "local_change_customer_mold_history", "local_change_association",
            }
            for table in (t for t in m.Base.metadata.sorted_tables if t.name in names):
                statements = [str(CreateTable(table).compile(dialect=engine.dialect))]
                statements += [str(CreateIndex(index).compile(dialect=engine.dialect)) for index in table.indexes]
                with NamedTemporaryFile(mode="w", suffix=".sql", encoding="utf-8", delete=False) as source:
                    source.write(";\n".join(statements))
                    path = Path(source.name)
                try:
                    conn.exec_driver_sql(path.read_text(encoding="utf-8"))
                finally:
                    path.unlink(missing_ok=True)
            db = Session(bind=conn, expire_on_commit=False, join_transaction_mode="create_savepoint")
            yield db
        finally:
            if db:
                db.close()
            transaction.rollback()
    engine.dispose()


@pytest.fixture
def bid_context(bid_db):
    db = bid_db
    owner = m.User(username="bid_test_" + uuid4().hex, display_name="合成测试人员", password_hash="not-a-login", super_admin=True)
    other = m.User(username="bid_test_" + uuid4().hex, display_name="另一合成人员", password_hash="not-a-login", super_admin=True)
    db.add_all([owner, other]); db.flush()
    conversation = m.Conversation(user_id=owner.id, title="合成中标资料")
    project = m.Project(code="bid_test_" + uuid4().hex, name="合成项目", status="DRAFT")
    db.add_all([conversation, project]); db.flush()
    blob = m.FileObject(owner_id=owner.id, conversation_id=conversation.id, request_key=str(uuid4()),
                        filename="synthetic.pdf", media_type="application/pdf", size=1, sha256="a" * 64,
                        backend="local", storage_namespace="test", object_key=uuid4().hex)
    intake = m.DocumentIntake(conversation_id=conversation.id, created_by=owner.id, request_key=str(uuid4()), status="CLASSIFIED_ARCHIVED")
    db.add_all([blob, intake]); db.flush()
    item = m.DocumentIntakeFile(intake_id=intake.id, file_id=blob.id, confirmed_type="BID_NOTICE", confirmed_by=owner.id, confirmed_at=now())
    db.add(item); db.flush()
    job = m.DocumentOcrJob(intake_file_id=item.id, phase="PRECLASSIFY", status="SUCCEEDED")
    db.add(job); db.flush()
    classification = m.AuditEvent(user_id=owner.id, action="document.ocr.completed", resource_id=job.id,
                                  detail={"classification": {"document_type": "BID_NOTICE", "event_type": "BID_WON", "confidence": 0.9, "classifier_version": "bid-classifier-v1"}})
    db.add(classification); db.flush()
    event = record_confirmed_bid_event(db, owner, intake, item, classification.id,
                                       classification.detail["classification"], "BID_NOTICE", str(uuid4()))
    db.flush()
    return SimpleNamespace(db=db, owner=owner, other=other, conversation=conversation, project=project,
                           blob=blob, intake=intake, item=item, job=job, classification=classification, event=event)
