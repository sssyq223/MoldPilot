from uuid import uuid4
import pytest
from sqlalchemy import select, func
from contact_document_db import contact_document_db, contact_document_context
from domain_packs.mold import models as m
from domain_packs.mold.erp.commercial import contract_intake, document_workflow
from domain_packs.mold.ports.errors import DomainError


def confirm(ctx, operation_id=None):
    return contract_intake.confirm_engineering_contact(ctx.db, ctx.owner, ctx.intake.id,
        expected_version=ctx.intake.row_version,
        confirmations=[{'intake_file_id': ctx.item.id, 'document_type': 'ENGINEERING_CONTACT'}],
        operation_id=operation_id or str(uuid4()))


def test_confirmation_is_projected_and_preserves_classification_identity(contact_document_context):
    ctx = contact_document_context
    before = ctx.db.scalar(select(func.count()).select_from(m.ContactCase))
    confirm(ctx)
    row = contract_intake.serialize(ctx.db, ctx.intake)['files'][0]
    assert row['confirmed_type'] == 'ENGINEERING_CONTACT'
    assert row['classification_id'] == ctx.classification.id
    event = ctx.db.scalar(select(m.AuditEvent).where(
        m.AuditEvent.action == 'engineering_contact.document.confirmed', m.AuditEvent.resource_id == ctx.blob.id))
    assert event.detail['classification_id'] == ctx.classification.id
    assert event.detail['file_sha256'] == ctx.blob.sha256
    assert ctx.db.scalar(select(func.count()).select_from(m.ContactCase)) == before


def test_archived_conversation_cannot_confirm(contact_document_context):
    ctx = contact_document_context
    ctx.conversation.archived = True
    with pytest.raises(DomainError) as error:
        confirm(ctx)
    assert error.value.code == 'CONVERSATION_ARCHIVED'


def test_missing_classification_event_cannot_confirm(contact_document_context):
    ctx = contact_document_context
    ctx.db.delete(ctx.classification); ctx.db.flush()
    with pytest.raises(DomainError) as error:
        confirm(ctx)
    assert error.value.code == 'CLASSIFICATION_NOT_FOUND'


def test_classification_confirmation_run_is_skill_only(contact_document_context, monkeypatch):
    ctx = contact_document_context
    monkeypatch.setattr(document_workflow, '_trigger_engineering_contact_skill',
                        lambda db, user, intake, item, operation_id: m.Run(
                            user_id=user.id, conversation_id=intake.conversation_id,
                            security_version=user.security_version, prompt='synthetic', status='QUEUED',
                            checkpoint={'run_trigger': 'DOCUMENT_CLASSIFICATION_CONFIRMED'}))
    intake, item = confirm(ctx)
    run = document_workflow._trigger_engineering_contact_skill(
        ctx.db, ctx.owner, intake, item, 'operation-2')
    assert run.checkpoint['run_trigger'] == 'DOCUMENT_CLASSIFICATION_CONFIRMED'
    assert ctx.db.scalar(select(func.count()).select_from(m.ContactCase)) == 0


def test_same_operation_replay_is_idempotent(contact_document_context):
    ctx = contact_document_context
    key = str(uuid4())
    confirm(ctx, key)
    confirm(ctx, key)
    count = ctx.db.scalar(select(func.count()).select_from(m.AuditEvent).where(
        m.AuditEvent.action == 'engineering_contact.document.confirmed', m.AuditEvent.resource_id == ctx.blob.id))
    assert count == 1
