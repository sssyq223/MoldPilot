from datetime import timedelta
from sqlalchemy import select,func
from app import models as m
from app.db import now
from app.message_worker import publish_once,deliver,claim,consume_batch,reclaim_pending
from redis.exceptions import ResponseError
from conftest import sign_in
from test_domains import checked,workflow,confirm


class Transport:
    def __init__(self,fail=False):self.messages=[];self.acks=[];self.fail=fail
    def xadd(self,stream,body):
        if self.fail:raise ConnectionError('synthetic URL with secret must never be persisted')
        self.messages.append(body);return '1-0'
    def xack(self,*args):self.acks.append(args)


class RedisFiveTransport:
    def __init__(self):self.claimed=None
    def xautoclaim(self,*args,**kwargs):raise ResponseError("unknown command 'XAUTOCLAIM'")
    def xpending_range(self,*args):
        return [
            {'message_id':'1-0','time_since_delivered':10_000},
            {'message_id':'2-0','time_since_delivered':30_000},
            {'message_id':'3-0','time_since_delivered':90_000},
        ]
    def xclaim(self,*args):self.claimed=args;return [('2-0',{'event_id':'a'*36}),('3-0',{'event_id':'b'*36})]


def event(factory,resource_id,user_id):
    with factory.begin() as db:
        e=m.Outbox(kind='purchase.draft.created',resource_id=resource_id,payload={'recipients':[user_id]})
        db.add(e);db.flush();return e.id


def test_no_event_published_before_business_commit(data):
    ids,factory=data;transport=Transport()
    with factory() as db:
        pending=m.Outbox(kind='test',resource_id='test',payload={});db.add(pending);db.flush()
        # Other committed seed events may exist; uncommitted event must never be claimed.
        for _ in range(25):
            if not publish_once(factory,transport):break
        assert pending.id not in [msg['event_id'] for msg in transport.messages]
        db.rollback()


def test_failed_publish_leaves_retry_and_redacts_error(data):
    ids,factory=data
    with factory.begin() as db:
        for item in db.scalars(select(m.Outbox)):item.published_at=now()
        target=m.Outbox(kind='test',resource_id='test',payload={});db.add(target);db.flush();eid=target.id
    assert publish_once(factory,Transport(fail=True))
    with factory() as db:
        e=db.get(m.Outbox,eid)
        assert e.published_at is None and e.attempts==1 and e.retry_at>now()
        assert e.last_error=='ConnectionError' and e.lease_id is None


def test_duplicate_delivery_creates_one_notification_and_revocation_hides_it(client,data):
    ids,factory=data;sign_in(client)
    draft=checked(client.post('/api/purchases',json={'project_id':ids['project'],'remark':'通知测试',
        'lines':[{'material_id':ids['hardware'],'quantity':'1','due_date':str(now().date())}]}))
    eid=event(factory,draft['id'],ids['buyer'])
    assert deliver(factory,eid)=='DELIVERED'
    assert deliver(factory,eid)=='DUPLICATE'
    with factory() as db:
        assert db.scalar(select(func.count()).select_from(m.Notification).where(m.Notification.event_id==eid))==1
    sign_in(client,'test_buyer');assert len(checked(client.get('/api/notifications')))==1
    with factory.begin() as db:
        for grant in db.scalars(select(m.Grant).where(m.Grant.user_id==ids['buyer'],m.Grant.permission=='purchase.read')):grant.active=False
    assert checked(client.get('/api/notifications'))==[]


def test_duplicate_stream_message_ack_after_inbox_commit(data):
    ids,factory=data;eid=event(factory,'missing',ids['buyer']);transport=Transport()
    consume_batch(factory,transport,[('1-0',{'event_id':eid}),('2-0',{'event_id':eid})])
    assert len(transport.acks)==2
    with factory() as db:assert db.scalar(select(func.count()).select_from(m.Inbox).where(m.Inbox.event_id==eid))==1


def test_redis_five_reclaims_pending_without_xautoclaim():
    transport=RedisFiveTransport()
    messages=reclaim_pending(transport,'worker-test')
    assert [item[0] for item in messages]==['2-0','3-0']
    assert transport.claimed[-1]==['2-0','3-0']


def test_redis_loss_reconciles_unconsumed_published_event(data):
    ids,factory=data
    with factory.begin() as db:
        for e in db.scalars(select(m.Outbox)):e.published_at=now()
        e=m.Outbox(kind='test',resource_id='test',payload={},published_at=now()-timedelta(minutes=2));db.add(e);db.flush();eid=e.id
    transport=Transport();assert publish_once(factory,transport)
    assert transport.messages==[{'event_id':eid}]
