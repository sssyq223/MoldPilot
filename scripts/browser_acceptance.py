"""Create/start a synthetic, isolated local browser acceptance installation.

Never adopts or stamps the main database. The separate PostgreSQL database is
migrated normally, and the main application/Redis data/model file stay intact.
Local credentials and process receipts live under ignored .local storage.
"""
import argparse
import json
import os
from pathlib import Path
import secrets
import shutil
import socket
import subprocess
import sys
from urllib.parse import urlsplit, urlunsplit

ROOT=Path(__file__).resolve().parents[1]
LOCAL=ROOT/'.local'/'browser-acceptance'
CONFIG=LOCAL/'runtime.json'
DATABASE='moldpilot_browser_acceptance'
ORIGIN='http://localhost:5174'
sys.path.insert(0,str(ROOT/'backend'))


def prepare():
    from sqlalchemy import create_engine, text
    from sqlalchemy.engine import make_url
    from redis import Redis
    from app.config import settings
    base=settings()
    source=make_url(base.database_url)
    if source.host not in {'127.0.0.1','localhost'} or not source.drivername.startswith('postgresql'):
        raise RuntimeError('Acceptance bootstrap requires local PostgreSQL')
    if CONFIG.exists():
        raise RuntimeError('Acceptance installation exists; use start, do not overwrite its data/configuration')
    target=source.set(database=DATABASE)
    if source.database==DATABASE:
        raise RuntimeError('The main database cannot be the acceptance database')
    redis_parts=urlsplit(base.redis_url)
    if redis_parts.hostname not in {'127.0.0.1','localhost'}:
        raise RuntimeError('Acceptance bootstrap requires local Redis')
    redis_url=urlunsplit(redis_parts._replace(path='/15'))
    if redis_parts.path.strip('/')=='15':
        raise RuntimeError('Redis database 15 is already the main database')
    redis=Redis.from_url(redis_url,protocol=2,socket_connect_timeout=2,socket_timeout=3)
    if redis.dbsize():
        raise RuntimeError('Redis database 15 is not empty; refusing to reuse existing streams')
    redis.close()
    admin=create_engine(source.set(database='postgres'),isolation_level='AUTOCOMMIT')
    with admin.connect() as connection:
        if connection.scalar(text('SELECT 1 FROM pg_database WHERE datname=:name'),{'name':DATABASE}):
            raise RuntimeError('Acceptance database already exists without installation receipt; inspect before adopting')
        connection.execute(text('CREATE DATABASE "'+DATABASE+'"'))
    admin.dispose()
    LOCAL.mkdir(parents=True,exist_ok=True)
    model_source=Path(os.environ.get('AGENT_MODEL_CONFIG_FILE') or os.environ.get('MOLD_MODEL_CONFIG_FILE') or ROOT/'.local/model-config.json')
    if model_source.is_file():
        shutil.copy2(model_source,LOCAL/'model-config.json')
    overrides={
        'AGENT_DATABASE_URL':target.render_as_string(hide_password=False),
        'AGENT_MIGRATION_URL':target.render_as_string(hide_password=False),
        'AGENT_REDIS_URL':redis_url,'AGENT_WORKER_SCOPE':'browser-acceptance-'+secrets.token_hex(8),
        'AGENT_WORKER_SECRET':secrets.token_urlsafe(32),'AGENT_API_BASE_URL':'http://127.0.0.1:8001',
        'AGENT_ORIGIN':ORIGIN,'AGENT_TRUSTED_ORIGINS':'http://127.0.0.1:5173,'+ORIGIN,
        'AGENT_COOKIE_SECURE':'false','AGENT_ENVIRONMENT':'test',
        'AGENT_FILE_BACKEND':'local','AGENT_FILE_LOCAL_ROOT':str(LOCAL/'files'),
        'AGENT_MODEL_CONFIG_FILE':str(LOCAL/'model-config.json'),
        'MOLD_ERP_BASE_URL':'','MOLD_ERP_DESIGN_MCP_ROOT':'','MOLD_ERP_DESIGN_MCP_PACKAGE':'',
        'PYTHONPATH':str(ROOT/'backend'),'VITE_API_PROXY_TARGET':'http://127.0.0.1:8001',
    }
    # These credentials belong only to the loopback-bound synthetic installation.
    record={'overrides':overrides,'username':'acceptance_admin','password':secrets.token_urlsafe(24),
            'origin':ORIGIN,'database':DATABASE,'synthetic':True}
    CONFIG.write_text(json.dumps(record,indent=2),encoding='utf-8')
    apply_environment(record)
    from agent_core.migration_runtime import upgrade_all, check_all
    upgrade_all('head');check_all()
    from app.bootstrap import create_admin
    from app.db import SessionLocal
    with SessionLocal.begin() as db:
        create_admin(db,record['username'],'独立合成验收管理员',record['password'])
    from create_browser_smoke_fixture import build_customer_acceptance
    fixture=build_customer_acceptance(overrides['AGENT_DATABASE_URL'],record['password'],
        project_code='ACCEPTANCE-COMPOUND-001',username=record['username'])
    fixture.pop('password',None)
    record['fixture']=fixture
    CONFIG.write_text(json.dumps(record,indent=2,default=str),encoding='utf-8')
    print(json.dumps({'database':DATABASE,'origin':ORIGIN,'fixture':fixture},ensure_ascii=False,default=str))


def apply_environment(record):
    if record['database']!=DATABASE or record['origin']!=ORIGIN:
        raise RuntimeError('Unexpected acceptance installation receipt')
    os.environ.update(record['overrides'])
    from app.config import settings
    settings.cache_clear()


def start():
    record=json.loads(CONFIG.read_text(encoding='utf-8'))
    if not record.get('fixture'):
        raise RuntimeError('Acceptance preparation did not complete; inspect migration/bootstrap logs before starting')
    process_file=LOCAL/'processes.json'
    if process_file.exists():
        raise RuntimeError('Process receipt exists; inspect recorded processes before starting another installation')
    apply_environment(record)
    for host,port in [('127.0.0.1',8001),('localhost',5174)]:
        with socket.socket() as sock:
            if sock.connect_ex((host,port))==0:
                raise RuntimeError(f'{host}:{port} already in use; inspect the running installation instead of duplicating it')
    commands={
        'api':([sys.executable,'-m','uvicorn','app.api:app','--host','127.0.0.1','--port','8001'],ROOT),
        'agent_worker':([sys.executable,'-m','app.agent_worker'],ROOT),
        'message_worker':([sys.executable,'-m','app.message_worker'],ROOT),
        'web':([shutil.which('node') or 'node',str(ROOT/'web/node_modules/vite/bin/vite.js'),
                '--host','localhost','--port','5174','--strictPort'],ROOT/'web'),
    }
    receipt={}
    for name,(command,cwd) in commands.items():
        with (LOCAL/(name+'.log')).open('a',encoding='utf-8') as out:
            process=subprocess.Popen(command,cwd=cwd,env=os.environ.copy(),stdin=subprocess.DEVNULL,
                stdout=out,stderr=subprocess.STDOUT,
                creationflags=subprocess.CREATE_NO_WINDOW if os.name=='nt' else 0)
        receipt[name]={'pid':process.pid,'command':command}
        # Preserve handles even if a later component fails to launch.
        process_file.write_text(json.dumps(receipt,indent=2),encoding='utf-8')
    print(json.dumps({'origin':ORIGIN,'processes':{key:value['pid'] for key,value in receipt.items()}}))


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action',choices=['prepare','start'])
    args=parser.parse_args()
    os.chdir(ROOT)
    (prepare if args.action=='prepare' else start)()
