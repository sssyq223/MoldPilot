"""验收真实 Proposal → HumanIntent → 确认，隔离事务，不运行迁移。"""
from uuid import uuid4
import json
from types import SimpleNamespace

import pytest
from sqlalchemy import select, func

from bid_start_db import bid_db, bid_context
from test_model_catalog import catalog_file
from domain_packs.mold import models as m
from domain_packs.mold.authorization import fingerprint
from domain_packs.mold.ports.bpm import content_hash
from domain_packs.mold.erp.core.business import create_intent, confirm_intent
from domain_packs.mold.tools.local import model_configuration_tools as model_tools
from domain_packs.mold.tools.local import change_intake_tools as change_tools
from agent_core.errors import DomainError


def step_for(ctx, tool, args, module):
    run = m.Run(conversation_id=ctx.conversation.id, user_id=ctx.owner.id,
                security_version=ctx.owner.security_version, status='SUCCEEDED', prompt='合成验收',
                checkpoint={'authorization_hash': fingerprint(ctx.db, ctx.owner)})
    ctx.db.add(run); ctx.db.flush()
    result = module.execute_tool(ctx.db, ctx.owner, tool, args, run=run)
    step = m.Step(run_id=run.id, sequence=1, tool=tool, request_hash=content_hash(args), result=result)
    ctx.db.add(step); ctx.db.flush()
    return step


def payload_for(step):
    return {'step_id': step.id, 'proposal_hash': content_hash(step.result['proposal'])}


def test_model_configuration_human_intent_writes_only_after_confirmation(bid_context, catalog_file):
    ctx = bid_context
    state = model_tools.catalog.public_catalog()
    step = step_for(ctx, 'prepare_model_provider_save', {
        'provider_id': state['providers'][0]['id'], 'revision': state['revision'], 'name': '确认后的名称',
    }, model_tools)
    before = catalog_file.read_bytes()
    intent = create_intent(ctx.db, ctx.owner, 'model_configuration.execute', step.id, payload_for(step))
    assert catalog_file.read_bytes() == before
    receipt = confirm_intent(ctx.db, ctx.owner, intent['id'], intent['challenge'])
    assert model_tools.catalog.public_catalog()['providers'][0]['name'] == '确认后的名称'
    assert 'synthetic-secret' not in json.dumps(receipt)
    after = catalog_file.read_bytes()
    assert confirm_intent(ctx.db, ctx.owner, intent['id'], intent['challenge']) == receipt
    assert catalog_file.read_bytes() == after


def test_model_proposal_rejects_stale_revision_before_card(bid_context, catalog_file):
    state = model_tools.catalog.public_catalog()
    with pytest.raises(DomainError) as error:
        step_for(bid_context, 'prepare_model_default', {'revision': '0' * 64, 'model_id': state['default_model_id']}, model_tools)
    assert error.value.code == 'MODEL_CONFIG_CONFLICT'


def test_model_tool_does_not_persist_proxy_credentials_in_proposal(bid_context, catalog_file):
    state = model_tools.catalog.public_catalog()
    with pytest.raises(DomainError) as error:
        step_for(bid_context, 'prepare_model_provider_save', {
            'revision': state['revision'], 'provider_id': state['providers'][0]['id'],
            'proxy_url': 'http://someone:synthetic-password@proxy.example:8080',
        }, model_tools)
    assert 'synthetic-password' not in str(error.value)


def test_change_intake_rejects_nonexistent_mold_before_proposal(bid_context):
    with pytest.raises(DomainError) as error:
        step_for(bid_context, 'prepare_local_change_intake', {
            'project_id': bid_context.project.id, 'mold_mode': 'EXISTING', 'original_mold_id': str(uuid4()),
            'classification': 'CUSTOMER', 'execution_mode': 'INTERNAL', 'charge_status': 'FREE',
            'contract_status': 'NONE', 'execution_scope': '局部修模', 'customer_basis': '合成邮件',
        }, change_tools)
    assert error.value.code == 'NOT_FOUND'


def test_change_acceptance_does_not_fabricate_execution_status():
    row = SimpleNamespace(id='c', number='LC', project_id='p', original_mold_id='m',
        mold_mode='EXISTING', customer_mold_number='C-MOLD', classification='CUSTOMER',
        execution_mode='INTERNAL', charge_status='FREE', contract_status='NONE',
        execution_scope='局部修模', customer_basis='客户依据', acceptance_status='ACCEPTED',
        status='ACCEPTED', revision=1, latest_snapshot={})
    card = change_tools._serialize(row)
    assert card.get('derived_status', {}).get('has_open_execution_or_recheck_items') is not True
