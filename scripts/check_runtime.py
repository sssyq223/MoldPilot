"""本机启动只读预检：不执行 Alembic upgrade/stamp，不更改业务数据。"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import subprocess
import sys

from sqlalchemy import create_engine, inspect, text
from sqlalchemy.engine import make_url
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.pool import NullPool

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'backend'))
MESSAGES = {
    'LOCAL_POSTGRES_REQUIRED': 'Local launcher requires a loopback PostgreSQL target. Review .env; no remote connection attempted.',
    'DATABASE_UNAVAILABLE': 'Local PostgreSQL is unavailable, recovering, or authentication failed. Check the configured port and Windows service moldpilot-postgresql-55432.',
    'SCHEMA_VERSION_MISMATCH': 'Database version does not match this checkout. Stop and request a separately approved schema review; no migration was run.',
    'SCHEMA_COLUMNS_MISSING': 'Required database tables or columns are missing. Stop and request a separately approved schema review.',
    'READONLY_REQUIRED': 'Database probe did not enter read-only mode; startup stopped.',
    'CONFIG_INVALID': 'Could not read startup configuration. Review .env without sharing credentials.',
    'STARTUP_CHECK_TIMEOUT': 'Read-only database check exceeded 20 seconds. Check local PostgreSQL; no services were launched.',
    'STARTUP_CHECK_FAILED': 'Read-only database check failed. No child traceback or credentials were forwarded.',
}


class StartupCheckError(RuntimeError):
    pass


def _check_schema(connection):
    from alembic.script import ScriptDirectory
    from agent_core.migration_runtime import alembic_configs
    from app.models import Base

    inspector = inspect(connection)
    versions = {}
    for stage, config in alembic_configs():
        expected = set(ScriptDirectory.from_config(config).get_heads())
        if not inspector.has_table(stage.version_table, schema='public'):
            raise StartupCheckError('SCHEMA_VERSION_MISMATCH')
        name = connection.dialect.identifier_preparer.quote(stage.version_table)
        actual = set(connection.execute(text(f'SELECT version_num FROM public.{name}')).scalars())
        if not expected or actual != expected:
            raise StartupCheckError('SCHEMA_VERSION_MISMATCH')
        versions[stage.name] = sorted(actual)

    by_schema = {}
    for table in Base.metadata.tables.values():
        schema = table.schema or 'public'
        if schema not in by_schema:
            by_schema[schema] = inspector.get_multi_columns(schema=schema)
        columns = by_schema[schema].get((schema, table.name), [])
        if not set(table.columns.keys()) <= {column['name'] for column in columns}:
            raise StartupCheckError('SCHEMA_COLUMNS_MISSING')
    return versions


def check_database(url):
    try:
        parsed = make_url(url)
    except Exception:
        raise StartupCheckError('CONFIG_INVALID') from None
    if parsed.get_backend_name() != 'postgresql' or parsed.host not in {'127.0.0.1', 'localhost', '::1'} or not parsed.database:
        raise StartupCheckError('LOCAL_POSTGRES_REQUIRED')
    engine = None
    try:
        # 即使数据库账号有写权限，检查连接也从建立时就强制只读及 SQL 超时。
        engine = create_engine(parsed, poolclass=NullPool, connect_args={
            'connect_timeout': 5,
            'host': parsed.host, 'hostaddr': '::1' if parsed.host == '::1' else '127.0.0.1',
            'port': parsed.port or 5432, 'dbname': parsed.database,
            'application_name': 'moldpilot-startup-preflight',
            'options': '-c default_transaction_read_only=on -c statement_timeout=5000 -c lock_timeout=3000 -c idle_in_transaction_session_timeout=10000',
        })
        with engine.connect() as connection:
            if connection.execute(text('SHOW transaction_read_only')).scalar_one() != 'on':
                raise StartupCheckError('READONLY_REQUIRED')
            database = connection.execute(text('SELECT current_database()')).scalar_one()
            if parsed.database and database != parsed.database:
                raise StartupCheckError('CONFIG_INVALID')
            versions = _check_schema(connection)
        return {'ok': True, 'readonly': True, 'database': database, 'port': parsed.port or 5432, 'versions': versions}
    except SQLAlchemyError:
        raise StartupCheckError('DATABASE_UNAVAILABLE') from None
    finally:
        if engine is not None:
            engine.dispose()


def _probe():
    try:
        from app.config import settings
        result = check_database(settings().database_url)
    except StartupCheckError as error:
        result = {'ok': False, 'code': str(error)}
    except Exception:
        result = {'ok': False, 'code': 'CONFIG_INVALID'}
    print(json.dumps(result))
    return 0 if result['ok'] else 1


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--database-probe', action='store_true', help=argparse.SUPPRESS)
    args = parser.parse_args(argv)
    if args.database_probe:
        return _probe()
    try:
        env = os.environ.copy()
        executable = sys.executable
        if os.name == 'nt' and sys.executable != sys._base_executable:
            # 沿用 CPython multiprocessing 的 venv 启动方式：绕过重定向父进程，
            # 确保超时直接结束真实 Python，而不是留下占用管道的孙进程。
            executable = sys._base_executable
            env['__PYVENV_LAUNCHER__'] = sys.executable
        completed = subprocess.run(
            [executable, str(Path(__file__).resolve()), '--database-probe'],
            cwd=ROOT, env=env, capture_output=True, text=True, encoding='utf-8', errors='replace', timeout=20,
        )
        result = json.loads(completed.stdout)
        if not isinstance(result, dict):
            raise StartupCheckError('STARTUP_CHECK_FAILED')
        if completed.returncode != 0 or result.get('ok') is not True or result.get('readonly') is not True:
            code = result.get('code')
            raise StartupCheckError(code if isinstance(code, str) and code in MESSAGES else 'STARTUP_CHECK_FAILED')
        print('[OK] PostgreSQL connection, schema versions and required columns verified read-only.')
        print('[OK] No migrations executed.')
        return 0
    except subprocess.TimeoutExpired:
        code = 'STARTUP_CHECK_TIMEOUT'
    except StartupCheckError as error:
        code = str(error)
    except Exception:
        code = 'STARTUP_CHECK_FAILED'
    print(f'[ERROR] {code}: {MESSAGES.get(code, MESSAGES["STARTUP_CHECK_FAILED"])}')
    return 1


if __name__ == '__main__':
    raise SystemExit(main())
