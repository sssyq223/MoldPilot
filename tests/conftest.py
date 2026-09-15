import os
from pathlib import Path
import pytest
from dotenv import dotenv_values
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker
from fastapi.testclient import TestClient
from alembic.config import Config
from alembic import command
from app.models import Base
from app.db import get_db, make_engine
from app.api import app
from app.bootstrap import create_admin
from app.demo_seed import seed

PASSWORD = "OnlyForSyntheticTests-2026!"


@pytest.fixture(scope="session")
def test_engine():
    url = os.environ.get("MOLD_TEST_DATABASE_URL") or dotenv_values('.env').get("MOLD_TEST_DATABASE_URL")
    if not url: pytest.skip("Isolated PostgreSQL test URL is required")
    engine = make_engine(url)
    # Destructive cleanup is strictly limited to a dedicated, explicitly named test DB.
    if engine.url.database != "agent_test" or engine.url.host not in {"127.0.0.1", "localhost", "postgres"}:
        raise RuntimeError("Refusing to initialize a database outside the isolated agent_test target")
    previous = os.environ.get("MOLD_MIGRATION_URL")
    os.environ["MOLD_MIGRATION_URL"] = url
    try: command.upgrade(Config('alembic.ini'), 'head')
    finally:
        if previous is None: os.environ.pop("MOLD_MIGRATION_URL", None)
        else: os.environ["MOLD_MIGRATION_URL"] = previous
    yield engine
    engine.dispose()


@pytest.fixture
def data(test_engine):
    tables = ','.join('"'+t.name+'"' for t in Base.metadata.sorted_tables)
    with test_engine.begin() as conn: conn.execute(text('TRUNCATE TABLE '+tables+' CASCADE'))
    factory = sessionmaker(test_engine, expire_on_commit=False)
    with factory.begin() as db:
        create_admin(db, 'admin', '测试管理员', PASSWORD)
        ids = seed(db, PASSWORD)
    def override():
        with factory() as db: yield db
    app.dependency_overrides[get_db] = override
    yield ids, factory
    app.dependency_overrides.clear()


@pytest.fixture
def client(data):
    with TestClient(app) as c: yield c


def sign_in(client, username='admin'):
    r = client.post('/api/auth/login', json={'username': username, 'password': PASSWORD})
    assert r.status_code == 200, r.text
    client.headers['X-CSRF-Token'] = r.json()['csrf']
    return r.json()['user']


def draft(client, ids, material=None, project=None, quantity='100'):
    r = client.post('/api/purchases', json={'project_id':project or ids['project'], 'remark':'合成测试材料',
                                          'lines':[{'material_id': material or ids['hardware'], 'quantity':quantity, 'due_date':'2026-09-16'}]})
    assert r.status_code == 200, r.text
    return r.json()['id']


def submit(client, ids, request_id):
    r=client.post(f'/api/purchases/{request_id}/submit-intent',json={'revision':1,'definition_id':ids['definition']})
    assert r.status_code==200,r.text
    intent=r.json()
    r=client.post(f"/api/human-actions/{intent['id']}/confirm",json={'challenge':intent['challenge']})
    assert r.status_code==200,r.text
    return r.json()['instance_id']
