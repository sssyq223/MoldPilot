from types import SimpleNamespace

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app import models as m
from app.errors import DomainError
from app.tool_gateway import available_tools, execute, skill_context
from domain_packs.mold.tools.erp.design import erp_design_mcp


@pytest.mark.parametrize(('arguments', 'query'), [
    ({}, {'pageNum': 1, 'pageSize': 10}),
    ({'keyword_text': '导柱', 'page_num': 2, 'page_size': 10},
     {'keywordText': '导柱', 'pageNum': 2, 'pageSize': 10}),
    ({'query': ' 导柱 '}, {'keywordText': '导柱', 'pageNum': 1, 'pageSize': 10}),
    ({'query': {'keywordText': '导柱', 'pageNum': 3, 'pageSize': 20}},
     {'keywordText': '导柱', 'pageNum': 3, 'pageSize': 20}),
])
def test_keyword_filters_reach_the_erp_contract_and_rows_remain_available(monkeypatch, arguments, query):
    calls = []
    receipt = {'code': 200, 'rows': [{'id': 19, 'keywordText': '导柱', 'remark': None}],
               'total': 21, 'pageNum': query['pageNum'], 'pageSize': query['pageSize'], 'hasNext': True}

    def call(name, params):
        calls.append((name, params))
        return receipt

    monkeypatch.setattr(erp_design_mcp, 'call_mcp', call)
    result = execute(None, SimpleNamespace(super_admin=True), 'erp_design_query_group_keywords', arguments)
    assert calls == [('query_erp_design_group_keywords', {'query': query})]
    assert result['data'] == receipt
    assert result['model_context']['erp_table'] == 'design_group_keyword'
    assert result['model_context']['query'] == query
    assert result['model_context']['total'] == 21
    assert result['model_context']['returned_count'] == 1
    assert result['as_of']


@pytest.mark.parametrize('arguments', [
    {'query': {'moldNo': '导柱'}}, {'query': {'status': 'active'}},
    {'page_num': 0}, {'page_size': 501},
    {'keyword_text': '导柱', 'query': {'keywordText': '顶杆'}},
])
def test_invalid_keyword_filters_fail_before_contacting_erp(monkeypatch, arguments):
    def unexpected_call(*_args):
        pytest.fail('Invalid filters must not become an unfiltered ERP query')

    monkeypatch.setattr(erp_design_mcp, 'call_mcp', unexpected_call)
    with pytest.raises(DomainError, match='ERP 设计工具参数无效'):
        execute(None, SimpleNamespace(super_admin=True), 'erp_design_query_group_keywords', arguments)


def test_empty_keywords_and_failed_queries_remain_distinct(monkeypatch):
    monkeypatch.setattr(erp_design_mcp, 'call_mcp', lambda *_args: {
        'rows': [], 'total': 0, 'pageNum': 1, 'pageSize': 10, 'hasNext': False,
    })
    result = execute(None, SimpleNamespace(super_admin=True), 'erp_design_query_group_keywords', {})
    assert result['data']['rows'] == []
    assert result['model_context']['total'] == 0
    for failure in [{'error': 'ERP unavailable'}, {'code': 500, 'rows': [], 'total': 0},
                    {'success': False, 'rows': [], 'total': 0}]:
        monkeypatch.setattr(erp_design_mcp, 'call_mcp', lambda *_args: failure)
        with pytest.raises(DomainError, match='未返回有效的分页记录'):
            execute(None, SimpleNamespace(super_admin=True), 'erp_design_query_group_keywords', {})


def test_existing_master_data_grants_allow_keyword_read_but_not_writes():
    engine = create_engine('sqlite+pysqlite:///:memory:')
    m.Base.metadata.create_all(engine)
    try:
        with Session(engine) as db:
            user = m.User(username='keyword-reader', display_name='查询员', password_hash='test')
            db.add(user)
            db.flush()
            db.add_all([
                m.Grant(user_id=user.id, permission='design_route.read', effect='ALLOW',
                        scope={'all': True}, fields=['*'], reason='existing grant', granted_by=user.id),
                m.Capability(user_id=user.id, kind='SKILL', key='erp_design_master_data_maintenance', enabled=True),
                m.Capability(user_id=user.id, kind='TOOL', key='erp_design_query_master_data', enabled=True),
            ])
            db.flush()
            tools = available_tools(db, user)
            assert 'erp_design_query_group_keywords' in tools
            assert 'erp_design_manage_group_keyword' not in tools
            assert 'erp_design_group_keyword_review' in {skill['key'] for skill in skill_context(db, user)}
            with pytest.raises(DomainError, match='工具不在当前有效能力范围内'):
                execute(db, user, 'erp_design_manage_group_keyword', {'operation': 'delete', 'id': 1, 'confirm': True})
            db.query(m.Grant).delete()
            assert 'erp_design_query_group_keywords' not in available_tools(db, user)
    finally:
        engine.dispose()
