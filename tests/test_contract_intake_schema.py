from decimal import Decimal

import pytest
from sqlalchemy.exc import IntegrityError

from app import models as m
from pg_db import factory as pg_factory


def factory():
    return pg_factory()


def test_contract_intake_schema_separates_machine_and_human_facts():
    names = {table.name for table in m.Base.metadata.sorted_tables}
    assert {
        "document_intake",
        "document_intake_file",
        "document_ocr_job",
        "document_recognized_page",
        "document_extracted_field",
        "contract_intake_group",
        "contract_intake_mold_match",
        "contract_attachment",
        "contract_mold_line",
        "contract_relation",
    } <= names

    page_columns = m.DocumentRecognizedPage.__table__.c
    assert {
        "intake_file_id", "page_number", "source_kind", "source_sha256",
        "pipeline_version", "text", "blocks", "average_confidence", "text_sha256",
    } <= set(page_columns.keys())

    field_columns = m.DocumentExtractedField.__table__.c
    assert {
        "raw_value", "normalized_value", "source_block_ids",
        "confirmed_value", "confirmed_by", "confirmed_at",
    } <= set(field_columns.keys())
    assert field_columns.raw_value.type.python_type is dict
    assert field_columns.source_block_ids.nullable is False
    assert field_columns.confirmed_value.type.python_type is dict

    attachment_columns = m.ContractAttachment.__table__.c
    assert {"intake_file_id", "role"} <= set(attachment_columns.keys())
    assert not hasattr(m, "ContractDocument")

    contract_columns = m.ContractDetail.__table__.c
    assert {"signed_date", "external_order_number"} <= set(contract_columns.keys())
    stage_columns = m.PaymentStage.__table__.c
    assert {"sequence", "ratio", "term_days"} <= set(stage_columns.keys())


def _user(db, username="schema-user"):
    row = m.User(username=username, display_name=username, password_hash="test")
    db.add(row)
    db.flush()
    return row


def _file(db, user, conversation, key):
    row = m.FileObject(
        owner_id=user.id,
        conversation_id=conversation.id,
        request_key=key,
        filename=f"{key}.pdf",
        media_type="application/pdf",
        size=10,
        sha256=(key * 64)[:64],
        backend="local",
        storage_namespace="local",
        object_key=f"objects/{key}",
        storage_version=None,
    )
    db.add(row)
    db.flush()
    return row


def test_one_uploaded_file_cannot_enter_two_intakes():
    engine, Session = factory()
    try:
        with Session.begin() as db:
            user = _user(db)
            conversation = m.Conversation(user_id=user.id, title="合同识别")
            db.add(conversation)
            db.flush()
            blob = _file(db, user, conversation, "a")
            first = m.DocumentIntake(
                conversation_id=conversation.id,
                created_by=user.id,
                request_key="11111111-1111-4111-8111-111111111111",
            )
            second = m.DocumentIntake(
                conversation_id=conversation.id,
                created_by=user.id,
                request_key="22222222-2222-4222-8222-222222222222",
            )
            db.add_all([first, second])
            db.flush()
            db.add(m.DocumentIntakeFile(intake_id=first.id, file_id=blob.id))
            db.flush()
            db.add(m.DocumentIntakeFile(intake_id=second.id, file_id=blob.id))
            with pytest.raises(IntegrityError):
                db.flush()
    finally:
        engine.dispose()


def test_recognized_page_versions_are_immutable_and_validated():
    engine, Session = factory()
    try:
        with Session.begin() as db:
            user = _user(db, "recognized-page-user")
            conversation = m.Conversation(user_id=user.id, title="页面识别")
            db.add(conversation)
            db.flush()
            blob = _file(db, user, conversation, "b")
            intake = m.DocumentIntake(
                conversation_id=conversation.id,
                created_by=user.id,
                request_key="33333333-3333-4333-8333-333333333333",
            )
            db.add(intake)
            db.flush()
            intake_file = m.DocumentIntakeFile(intake_id=intake.id, file_id=blob.id)
            db.add(intake_file)
            db.flush()
            values = {
                "intake_file_id": intake_file.id,
                "page_number": 1,
                "source_kind": "PADDLE_OCR",
                "source_sha256": "1" * 64,
                "pipeline_version": "pymupdf-1+paddleocr-3.7.0",
                "text": "销售合同",
                "blocks": [{"block_id": "p1-b0001", "text": "销售合同"}],
                "average_confidence": Decimal("0.9800"),
                "text_sha256": "2" * 64,
            }
            db.add(m.DocumentRecognizedPage(**values))
            db.flush()
            db.add(m.DocumentRecognizedPage(**values))
            with pytest.raises(IntegrityError):
                db.flush()
    finally:
        engine.dispose()


@pytest.mark.parametrize("updates", [
    {"page_number": 0},
    {"source_kind": "VISION_MODEL"},
    {"average_confidence": Decimal("1.0001")},
])
def test_recognized_page_rejects_invalid_page_facts(updates):
    engine, Session = factory()
    try:
        with Session.begin() as db:
            user = _user(db, "invalid-recognized-page-" + str(abs(hash(str(updates)))))
            conversation = m.Conversation(user_id=user.id, title="无效页面识别")
            db.add(conversation)
            db.flush()
            blob = _file(db, user, conversation, ("c" + str(abs(hash(str(updates)))))[:32])
            intake = m.DocumentIntake(
                conversation_id=conversation.id,
                created_by=user.id,
                request_key="44444444-4444-4444-8444-" + str(abs(hash(str(updates)))).zfill(12)[-12:],
            )
            db.add(intake)
            db.flush()
            intake_file = m.DocumentIntakeFile(intake_id=intake.id, file_id=blob.id)
            db.add(intake_file)
            db.flush()
            values = {
                "intake_file_id": intake_file.id,
                "page_number": 1,
                "source_kind": "TEXT_LAYER",
                "source_sha256": "3" * 64,
                "pipeline_version": "pymupdf-1",
                "text": "合同文本",
                "blocks": [],
                "average_confidence": None,
                "text_sha256": "4" * 64,
            }
            values.update(updates)
            db.add(m.DocumentRecognizedPage(**values))
            with pytest.raises(IntegrityError):
                db.flush()
    finally:
        engine.dispose()


def test_contract_relation_rejects_self_reference():
    engine, Session = factory()
    try:
        with Session.begin() as db:
            user = _user(db, "relation-user")
            project = m.Project(code="SCHEMA-PROJECT", name="合同结构项目", status="ACTIVE")
            db.add(project)
            db.flush()
            subject = m.BusinessSubject(
                kind="sales_contract",
                number="SCHEMA-CONTRACT",
                project_id=project.id,
                created_by=user.id,
                status="EFFECTIVE",
                remark="结构约束",
            )
            db.add(subject)
            db.flush()
            db.add(m.ContractDetail(
                subject_id=subject.id,
                amount=Decimal("100.00"),
                currency="CNY",
                contract_number="SCHEMA-001",
            ))
            db.add(m.ContractRelation(
                source_contract_id=subject.id,
                target_contract_id=subject.id,
                relation_type="REVISION",
                reason="不能关联自身",
                confirmed_by=user.id,
            ))
            with pytest.raises(IntegrityError):
                db.flush()
    finally:
        engine.dispose()
