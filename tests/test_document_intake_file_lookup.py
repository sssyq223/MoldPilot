"""附件与接收批次是不同身份；只在当前任务授权范围内解析关联。"""
from types import SimpleNamespace
from uuid import uuid4

import pytest
from contact_document_db import contact_document_db, contact_document_context
from domain_packs.mold import models as m
from domain_packs.mold.erp.commercial import contract_intake
from domain_packs.mold.tools.erp.commercial import document_intake_tools as tools
from domain_packs.mold.ports.errors import DomainError


def run_for(ctx, *, bind=True):
    run = m.Run(user_id=ctx.owner.id, conversation_id=ctx.conversation.id,
                security_version=ctx.owner.security_version, prompt='test', status='QUEUED')
    ctx.db.add(run); ctx.db.flush()
    if bind:
        ctx.db.add(m.RunFile(run_id=run.id, file_id=ctx.blob.id)); ctx.db.flush()
    return run


def query(ctx, run, args):
    return tools.execute_tool(ctx.db, ctx.owner, 'query_document_intake', args, run=run)


def test_file_id_resolves_authoritative_intake(contact_document_context):
    ctx = contact_document_context
    result = query(ctx, run_for(ctx), {'file_id': ctx.blob.id})
    assert [row['id'] for row in result['data']] == [ctx.intake.id]
    assert result['data'][0]['files'][0]['file_id'] == ctx.blob.id


@pytest.mark.parametrize('invalid', ['unbound', 'other_owner', 'other_conversation'])
def test_file_lookup_rejects_outside_current_run(contact_document_context, invalid):
    ctx = contact_document_context
    run = run_for(ctx, bind=invalid != 'unbound')
    if invalid == 'other_owner':
        run.user_id = str(uuid4())
    if invalid == 'other_conversation':
        run.conversation_id = str(uuid4())
    # 禁止将未授权的内存身份变更刷入数据库；查询须先校验任务和附件范围。
    with ctx.db.no_autoflush, pytest.raises(DomainError):
        query(ctx, run, {'file_id': ctx.blob.id})


def test_empty_query_prefers_bound_files_over_other_batches(contact_document_context):
    ctx = contact_document_context
    other = m.DocumentIntake(conversation_id=ctx.conversation.id, created_by=ctx.owner.id,
                             request_key=str(uuid4()), status='PRECLASSIFYING')
    ctx.db.add(other); ctx.db.flush()
    result = query(ctx, run_for(ctx), {})
    assert [row['id'] for row in result['data']] == [ctx.intake.id]


def test_batch_id_is_not_silently_reinterpreted_as_file_id(contact_document_context):
    ctx = contact_document_context
    with pytest.raises(DomainError) as error:
        query(ctx, run_for(ctx), {'document_intake_id': ctx.blob.id})
    assert error.value.code == 'NOT_FOUND'


def test_conflicting_selectors_rejected(contact_document_context):
    ctx = contact_document_context
    with pytest.raises(DomainError) as error:
        query(ctx, run_for(ctx), {'document_intake_id': ctx.intake.id, 'file_id': ctx.blob.id})
    assert error.value.code == 'INVALID_TOOL_INPUT'
