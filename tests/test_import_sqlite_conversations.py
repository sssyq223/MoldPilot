import json
import sqlite3
from pathlib import Path
from uuid import uuid4

from sqlalchemy import select

from app import models as m
from pg_db import factory as pg_factory
from scripts.import_sqlite_conversations import import_conversations


def factory():
    return pg_factory()


def user(db, username="admin"):
    row = m.User(username=username, display_name=username, password_hash="test", super_admin=True, security_version=7)
    db.add(row)
    db.flush()
    return row


def sqlite_source(path):
    con = sqlite3.connect(path)
    con.executescript(
        """
        create table ai_conversation (
            user_id varchar(36) not null,
            title varchar(150) not null,
            id varchar(36) primary key,
            created_at datetime not null,
            pinned boolean not null default 0,
            archived boolean not null default 0
        );
        create table ai_run (
            conversation_id varchar(36) not null,
            user_id varchar(36) not null,
            security_version integer not null,
            prompt text not null,
            status varchar(40) not null,
            checkpoint json not null,
            result json,
            lease_epoch integer not null,
            lease_until datetime,
            id varchar(36) primary key,
            created_at datetime not null
        );
        create table ai_step (
            run_id varchar(36) not null,
            sequence integer not null,
            tool varchar(100) not null,
            request_hash varchar(64) not null,
            result json not null,
            id varchar(36) primary key,
            created_at datetime not null
        );
        """
    )
    con.execute(
        "insert into ai_conversation(user_id,title,id,created_at,pinned,archived) values(?,?,?,?,?,?)",
        ("old-user", "旧对话\x00", "conv-1", "2026-09-15 10:00:00", 1, 0),
    )
    con.execute(
        """insert into ai_run(conversation_id,user_id,security_version,prompt,status,checkpoint,result,lease_epoch,lease_until,id,created_at)
           values(?,?,?,?,?,?,?,?,?,?,?)""",
        (
            "conv-1",
            "old-user",
            1,
            "查询旧记录\x00",
            "SUCCEEDED",
            json.dumps({"authorization_hash": "old-hash", "turn": 3, "messages": [{"content": "旧\x00上下文"}]}),
            json.dumps({"summary": "旧结果"}),
            0,
            None,
            "run-1",
            "2026-09-15 10:00:01",
        ),
    )
    con.execute(
        "insert into ai_step(run_id,sequence,tool,request_hash,result,id,created_at) values(?,?,?,?,?,?,?)",
        ("run-1", 0, "query_projects", "hash", json.dumps({"data": []}), "step-1", "2026-09-15 10:00:02"),
    )
    con.commit()
    con.close()


def test_import_sqlite_conversations_remaps_owner_and_visibility():
    tmp_dir = Path(".pytest_tmp")
    tmp_dir.mkdir(exist_ok=True)
    source = tmp_dir / f"legacy-{uuid4().hex}.sqlite"
    sqlite_source(source)
    engine, Session = factory()
    try:
        with Session.begin() as db:
            admin = user(db)
        with Session() as db:
            admin = db.scalar(select(m.User).where(m.User.username == "admin"))
            summary = import_conversations(source, db, admin, execute=True)
            assert summary["imported_conversations"] == 1
            assert summary["imported_runs"] == 1
            assert summary["imported_steps"] == 1
        with Session() as db:
            conversation = db.get(m.Conversation, "conv-1")
            run = db.get(m.Run, "run-1")
            step = db.get(m.Step, "step-1")
            assert conversation.user_id == admin.id
            assert conversation.title == "旧对话"
            assert conversation.pinned is True
            assert run.user_id == admin.id
            assert run.security_version == 7
            assert run.prompt == "查询旧记录"
            assert run.checkpoint["turn"] == 3
            assert run.checkpoint["messages"][0]["content"] == "旧上下文"
            assert "authorization_hash" not in run.checkpoint
            assert "imported_from_sqlite" in run.checkpoint
            assert run.result["summary"] == "旧结果"
            assert step.result == {"data": []}
    finally:
        try:
            source.unlink(missing_ok=True)
        except PermissionError:
            pass
        engine.dispose()
