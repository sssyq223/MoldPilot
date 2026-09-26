"""启动预检只读、限时、脱敏；不触发测试库初始化或迁移。"""
import importlib
import json
import subprocess

import pytest


def checker():
    return importlib.import_module('scripts.check_runtime')


@pytest.mark.parametrize('url', [
    'postgresql+psycopg://user:secret@192.168.3.215:5432/moldpilot',
    'sqlite:///local.db',
])
def test_rejects_nonlocal_or_non_postgres_before_connecting(monkeypatch, url):
    module = checker()
    def unexpected(*args, **kwargs):
        pytest.fail('被拒绝的目标不应发起连接')
    monkeypatch.setattr(module, 'create_engine', unexpected)
    with pytest.raises(module.StartupCheckError, match='LOCAL_POSTGRES_REQUIRED'):
        module.check_database(url)


def test_probe_has_an_outer_deadline_and_does_not_print_child_secrets(monkeypatch, capsys):
    module = checker()
    def hung(command, **kwargs):
        assert kwargs['timeout'] == 20
        assert '--database-probe' in command
        raise subprocess.TimeoutExpired(command, 20, output='password=synthetic-secret')
    monkeypatch.setattr(module.subprocess, 'run', hung)
    assert module.main([]) == 1
    output = capsys.readouterr()
    assert 'STARTUP_CHECK_TIMEOUT' in output.out
    assert 'synthetic-secret' not in output.out + output.err


def test_failed_child_output_is_not_forwarded(monkeypatch, capsys):
    module = checker()
    monkeypatch.setattr(module.subprocess, 'run', lambda *a, **kw: subprocess.CompletedProcess(
        a[0], 1, stdout='password=synthetic-secret', stderr='token=synthetic-secret'))
    assert module.main([]) == 1
    output = capsys.readouterr()
    assert 'STARTUP_CHECK_FAILED' in output.out
    assert 'synthetic-secret' not in output.out + output.err


def test_success_requires_verified_readonly_result(monkeypatch, capsys):
    module = checker()
    monkeypatch.setattr(module.subprocess, 'run', lambda *a, **kw: subprocess.CompletedProcess(
        a[0], 0, stdout=json.dumps({'ok': True, 'readonly': False}), stderr=''))
    assert module.main([]) == 1
    assert 'STARTUP_CHECK_FAILED' in capsys.readouterr().out


def test_success_reports_no_migrations(monkeypatch, capsys):
    module = checker()
    result = {'ok': True, 'readonly': True, 'database': 'moldpilot_test', 'port': 55432}
    monkeypatch.setattr(module.subprocess, 'run', lambda *a, **kw: subprocess.CompletedProcess(
        a[0], 0, stdout=json.dumps(result), stderr=''))
    assert module.main([]) == 0
    assert 'No migrations executed' in capsys.readouterr().out


def test_database_errors_are_sanitized_and_engine_disposed(monkeypatch):
    module = checker()
    from sqlalchemy.exc import OperationalError
    disposed = []
    class Engine:
        def connect(self):
            raise OperationalError('SELECT 1', {}, RuntimeError('synthetic-secret'))
        def dispose(self):
            disposed.append(True)
    def engine(url, **kwargs):
        options = kwargs['connect_args']
        assert options['connect_timeout'] == 5
        assert (options['hostaddr'], options['port'], options['dbname']) == ('127.0.0.1', 55432, 'moldpilot_test')
        assert 'default_transaction_read_only=on' in options['options']
        assert 'statement_timeout=5000' in options['options']
        return Engine()
    monkeypatch.setattr(module, 'create_engine', engine)
    with pytest.raises(module.StartupCheckError) as error:
        module.check_database('postgresql+psycopg://user:synthetic-secret@127.0.0.1:55432/moldpilot_test')
    assert str(error.value) == 'DATABASE_UNAVAILABLE'
    assert disposed == [True]


@pytest.fixture
def existing_test_database_url():
    from pg_db import _test_database_url
    from sqlalchemy.engine import make_url
    url = _test_database_url()
    parsed = make_url(url)
    assert (parsed.host, parsed.port, parsed.database) == ('127.0.0.1', 55432, 'moldpilot_test')
    return url


def test_existing_postgres_schema_is_checked_without_migrations(existing_test_database_url, monkeypatch):
    from alembic import command
    def forbidden(*args, **kwargs):
        pytest.fail('只读启动检查不能调用迁移或盖章')
    for operation in ('upgrade', 'downgrade', 'stamp'):
        monkeypatch.setattr(command, operation, forbidden)
    result = checker().check_database(existing_test_database_url)
    assert result['readonly'] is True
    assert result['database'] == 'moldpilot_test'
    assert result['ok'] is True
    assert set(result['versions']) == {'core', 'mold'}
    assert all(result['versions'].values())


def test_schema_mismatch_stops_startup_without_repair(existing_test_database_url, monkeypatch):
    from alembic.script import ScriptDirectory
    module = checker()
    monkeypatch.setattr(ScriptDirectory, 'get_heads', lambda self: ['incompatible-checkout'])
    with pytest.raises(module.StartupCheckError, match='SCHEMA_VERSION_MISMATCH'):
        module.check_database(existing_test_database_url)


def test_missing_required_table_stops_startup(existing_test_database_url, monkeypatch):
    from types import SimpleNamespace
    from sqlalchemy import MetaData, Table, Column, Integer
    from app import models
    required = MetaData()
    Table('__startup_missing_table__', required, Column('id', Integer))
    monkeypatch.setattr(models, 'Base', SimpleNamespace(metadata=required))
    module = checker()
    with pytest.raises(module.StartupCheckError, match='SCHEMA_COLUMNS_MISSING'):
        module.check_database(existing_test_database_url)


def test_connection_query_cannot_override_the_local_target(monkeypatch):
    module = checker()
    from sqlalchemy.exc import OperationalError
    class Engine:
        def connect(self):
            raise OperationalError('', {}, RuntimeError('synthetic refusal'))
        def dispose(self):
            pass
    def engine(url, **kwargs):
        options = kwargs['connect_args']
        assert options['hostaddr'] == '127.0.0.1'
        assert options['dbname'] == 'moldpilot_test'
        assert options['connect_timeout'] == 5
        return Engine()
    monkeypatch.setattr(module, 'create_engine', engine)
    with pytest.raises(module.StartupCheckError, match='DATABASE_UNAVAILABLE'):
        module.check_database('postgresql+psycopg://test@127.0.0.1:55432/moldpilot_test?hostaddr=192.168.3.215&dbname=moldpilot&connect_timeout=0')
