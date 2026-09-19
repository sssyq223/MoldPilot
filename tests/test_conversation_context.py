from datetime import timedelta
from types import SimpleNamespace

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app import files
from app.db import now
from app.errors import DomainError
from app.internal import conversation_history
from app.models import Base,Conversation,Run,RunFile,User


def test_conversation_history_keeps_all_prior_turns_and_attachment_positions(monkeypatch):
    engine=create_engine('sqlite+pysqlite:///:memory:')
    Base.metadata.create_all(engine)
    Session=sessionmaker(engine,expire_on_commit=False)
    try:
        with Session.begin() as db:
            user=User(username='history-user',display_name='历史用户',password_hash='test',
                      super_admin=True,security_version=3)
            db.add(user);db.flush()
            conversation=Conversation(user_id=user.id,title='完整历史')
            db.add(conversation);db.flush()
            base=now()-timedelta(minutes=30)
            for index in range(6):
                db.add(Run(
                    id=f'prior-run-{index}',conversation_id=conversation.id,user_id=user.id,
                    security_version=3,prompt=f'历史消息{index}',status='SUCCEEDED',
                    checkpoint={'authorization_hash':'current-hash'},
                    result={'response_kind':'CONVERSATION','summary':f'历史答复{index}','suggestions':[]},
                    created_at=base+timedelta(minutes=index),
                ))
            current=Run(id='current-run',conversation_id=conversation.id,user_id=user.id,
                        security_version=3,prompt='继续处理上面的文件',status='QUEUED',
                        created_at=base+timedelta(minutes=10))
            db.add(current)
        monkeypatch.setattr(files,'run_files',lambda _db,_user,run:[{
            'id':'historical-file','filename':'历史清单.xlsx','media_type':'application/test','size':10,
        }] if run.id=='prior-run-5' else [])
        with Session() as db:
            history=conversation_history(db,db.query(User).filter_by(username='history-user').one(),
                                         db.get(Run,'current-run'),'current-hash')
        assert [turn['user']['content'] for turn in history]==[f'历史消息{i}' for i in range(6)]
        assert history[-1]['user']['attachments'][0]['filename']=='历史清单.xlsx'
        assert history[-1]['assistant']['summary']=='历史答复5'
    finally:
        engine.dispose()


def test_conversation_history_drops_assistant_answer_after_authorization_change(monkeypatch):
    previous=SimpleNamespace(
        id='prior-run',security_version=4,prompt='此前查到什么',status='SUCCEEDED',
        checkpoint={'authorization_hash':'old-hash'},
        result={'response_kind':'CONVERSATION','summary':'已经失效的授权数据'},
        created_at=now()-timedelta(minutes=1),
    )
    current=SimpleNamespace(conversation_id='conversation-1',created_at=now())
    user=SimpleNamespace(id='user-1',security_version=4)

    class Scalars:
        def __iter__(self):return iter([previous])

    class Db:
        def scalars(self,_statement):return Scalars()

    monkeypatch.setattr(files,'run_files',lambda *_args:[])
    history=conversation_history(Db(),user,current,'new-hash')

    assert history[0]['user']['content']=='此前查到什么'
    assert history[0]['assistant'] is None


def test_historical_file_reference_is_rebound_only_inside_same_conversation(monkeypatch):
    blob=SimpleNamespace(id='file-1',filename='历史清单.xlsx',conversation_id='conversation-1')
    user=SimpleNamespace(id='user-1')
    run=SimpleNamespace(id='run-2',user_id='user-1',conversation_id='conversation-1')

    class Db:
        def __init__(self):self.added=[]
        def scalar(self,_statement):return None
        def add(self,value):self.added.append(value)

    db=Db();events=[]
    monkeypatch.setattr(files,'uploaded_file',lambda *_args:blob)
    monkeypatch.setattr(files,'record',lambda *args:events.append(args))

    selected=files.reference_run_file(db,user,run,blob.id)

    assert selected is blob
    assert len(db.added)==1 and isinstance(db.added[0],RunFile)
    assert db.added[0].run_id==run.id and db.added[0].file_id==blob.id
    assert events and events[0][2]=='agent.run.file_referenced'

    blob.conversation_id='another-conversation'
    with pytest.raises(DomainError,match='当前会话'):
        files.reference_run_file(Db(),user,run,blob.id)
