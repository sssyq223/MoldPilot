"""邮件候选必须绑定原分类版本；选择项目不能静默串用模具或字段。"""
from hashlib import sha256
from uuid import uuid4

import pytest

from bid_start_db import bid_context, bid_db  # noqa: F401
from domain_packs.mold import models as m
from domain_packs.mold.ports.errors import DomainError
from domain_packs.mold.skills.erp.commercial.bid_to_start_notice.orchestrator import trigger_bid_to_start_notice
from domain_packs.mold.tools.erp.commercial.admin_start_notice_tools import execute_tool


def candidates(c, draft, **kwargs):
    return execute_tool(c.db, c.owner, 'query_admin_start_notices', {
        'draft_id': draft.id, **kwargs,
    })['data'][0]


def seed_fields(c):
    # 使用模型已校验的字段快照，旧 extracted 中的金额不能成为正式合同金额。
    c.db.add(m.DocumentRecognizedPage(
        intake_file_id=c.item.id, page_number=1, source_kind='TEXT_LAYER',
        source_sha256=c.blob.sha256, pipeline_version='synthetic-test',
        text='项目：合成项目 客户模具 CLIENT-01 中标金额 98000 CNY',
        blocks=[
            {'block_id': 'p1-t0001', 'text': '项目：合成项目', 'bbox': [0, 0, 100, 20], 'source': 'TEXT_LAYER'},
            {'block_id': 'p1-t0002', 'text': '客户模具 CLIENT-01', 'bbox': [0, 20, 100, 40], 'source': 'TEXT_LAYER'},
            {'block_id': 'p1-t0003', 'text': '中标金额 98000 CNY', 'bbox': [0, 40, 100, 60], 'source': 'TEXT_LAYER'},
        ], text_sha256=sha256('项目：合成项目 客户模具 CLIENT-01 中标金额 98000 CNY'.encode()).hexdigest(),
    ))
    c.classification.detail = {'classification': {
        **c.classification.detail['classification'],
        'extracted': {'amount': '98000', 'currency': 'CNY'},
        'bid_fields': [
            {'field_key': 'project_name', 'value': c.project.name, 'page_number': 1,
             'source_block_ids': ['p1-t0001'], 'source_text': '项目：合成项目', 'confidence': '0.95'},
            {'field_key': 'customer_mold_number', 'value': 'CLIENT-01', 'page_number': 1,
             'source_block_ids': ['p1-t0002'], 'source_text': '客户模具 CLIENT-01', 'confidence': '0.94'},
            {'field_key': 'bid_amount', 'value': '98000', 'page_number': 1,
             'source_block_ids': ['p1-t0003'], 'source_text': '中标金额 98000', 'confidence': '0.94'},
        ],
    }}
    c.db.flush()


def test_query_returns_source_fields_without_writing_or_assuming_contract_amount(bid_context):
    c = bid_context
    seed_fields(c)
    draft = trigger_bid_to_start_notice(c.db, c.owner, c.event.id)
    before = c.db.query(m.AdminStartNoticeRevision).count()
    result = candidates(c, draft)
    assert result['source_file']['id'] == c.blob.id
    assert result['classification_id'] == c.classification.id
    assert result['fields']['project_name'][0]['value'] == '合成项目'
    assert result['fields']['customer_mold_numbers'][0]['value'] == 'CLIENT-01'
    assert 'amount' not in result['fields']
    assert result['references']['bid_amount'][0]['value'] == '98000'
    assert result['fields']['project_name'][0]['source_block_ids'] == ['p1-t0001']
    assert draft.material_snapshot.get('project_name') is None
    assert c.db.query(m.AdminStartNoticeRevision).count() == before
    assert draft.row_version == 1 and draft.project_id is None


def test_candidates_use_confirmed_classification_not_latest_other_result(bid_context):
    c = bid_context
    seed_fields(c)
    draft = trigger_bid_to_start_notice(c.db, c.owner, c.event.id)
    c.db.add(m.AuditEvent(user_id=c.owner.id, action='document.ocr.completed', resource_id=c.job.id,
        detail={'classification': {'extracted': {'project_name': '另一次识别的项目'}}}))
    c.db.flush()
    assert candidates(c, draft)['fields']['project_name'][0]['value'] == '合成项目'
    c.event.detail = {**c.event.detail, 'classification_id': str(uuid4())}
    c.db.flush()
    with pytest.raises(DomainError) as error:
        candidates(c, draft)
    assert error.value.code == 'BID_CLASSIFICATION_SOURCE_INVALID'


def test_project_search_returns_actual_versions_and_only_project_molds(bid_context):
    c = bid_context
    seed_fields(c)
    mold = m.Mold(internal_number='TEST-' + uuid4().hex, name='本项目模具')
    foreign = m.Mold(internal_number='TEST-' + uuid4().hex, name='其他项目模具')
    c.db.add_all([mold, foreign]); c.db.flush()
    c.db.add(m.ProjectMold(project_id=c.project.id, mold_id=mold.id)); c.db.flush()
    draft = trigger_bid_to_start_notice(c.db, c.owner, c.event.id)
    result = candidates(c, draft)
    selected = next(row for row in result['projects'] if row['id'] == c.project.id)
    assert selected['row_version'] == c.project.row_version
    assert selected['matched_by'] == ['项目名称']
    assert [row['id'] for row in selected['molds']] == [mold.id]
    assert result['project_confirmation_required'] is True
    assert candidates(c, draft, project_query=c.project.code)['projects'][0]['id'] == c.project.id
    assert candidates(c, draft, project_query='definitely-no-match')['projects'] == []
    c.project.status = 'CLOSED'; c.db.flush()
    assert all(row['id'] != c.project.id for row in candidates(c, draft)['projects'])


def test_candidate_access_requires_active_admin_and_current_source_access(bid_context):
    c = bid_context
    draft = trigger_bid_to_start_notice(c.db, c.owner, c.event.id)
    c.owner.super_admin = False
    with pytest.raises(DomainError) as error:
        candidates(c, draft)
    assert error.value.code == 'SUPER_ADMIN_REQUIRED'
    c.owner.super_admin = True
    c.owner.active = False
    with pytest.raises(DomainError) as error:
        candidates(c, draft)
    assert error.value.code == 'SUPER_ADMIN_REQUIRED'


def test_project_selection_checks_identity_and_does_not_keep_previous_molds(bid_context):
    from domain_packs.mold.erp.commercial.admin_start_workflow import update_admin_start_notice_draft
    c = bid_context
    draft = trigger_bid_to_start_notice(c.db, c.owner, c.event.id)
    with pytest.raises(DomainError) as error:
        update_admin_start_notice_draft(c.db, c.owner, draft.id, expected_row_version=1,
            material_snapshot={'project_id': c.project.id, 'project_version': c.project.row_version,
                               'project_number': '其他项目编号'}, reason='选择项目', operation_id='select')
    assert error.value.code == 'PROJECT_IDENTITY_CONFLICT'
    result = update_admin_start_notice_draft(c.db, c.owner, draft.id, expected_row_version=1,
        material_snapshot={'project_id': c.project.id, 'project_version': c.project.row_version,
                           'internal_mold_ids': []}, reason='选择项目', operation_id='select-valid')
    assert result['row_version'] == 2
    assert draft.material_snapshot['project_number'] == c.project.code
    assert draft.material_snapshot['project_name'] == c.project.name


def test_legacy_fields_only_offered_when_found_in_same_file_page_cache(bid_context):
    c = bid_context
    c.classification.detail = {'classification': {**c.classification.detail['classification'],
        'extracted': {'project_name': '合成项目', 'customer_name': '无来源客户', 'amount': '98000'}}}
    c.db.add(m.DocumentRecognizedPage(intake_file_id=c.item.id, page_number=1,
        source_sha256=c.blob.sha256, pipeline_version='synthetic-test', text='项目：合成项目',
        blocks=[{'block_id': 'p1-t0001', 'text': '项目：合成项目', 'bbox': [0, 0, 100, 20],
                 'source': 'TEXT_LAYER', 'confidence': None}],
        source_kind='TEXT_LAYER', text_sha256=sha256('项目：合成项目'.encode()).hexdigest()))
    c.db.flush()
    draft = trigger_bid_to_start_notice(c.db, c.owner, c.event.id)
    result = candidates(c, draft)
    assert result['fields']['project_name'][0]['source_block_ids'] == ['p1-t0001']
    assert 'customer_name' not in result['fields']
    assert 'amount' not in result['fields']
    assert result['warnings'] == []
