"""Redis carries event IDs; committed PostgreSQL facts remain authoritative.

Publisher network I/O runs outside business/claim transactions. Lost replies may
duplicate messages, so Inbox + Notification are committed together before XACK.
"""
from datetime import timedelta
import argparse
import logging
import time
import uuid
from redis import Redis
from redis.exceptions import ResponseError
from sqlalchemy import select,or_,exists,update
from sqlalchemy.dialects.postgresql import insert
from . import models as m
from .db import SessionLocal,now
from .config import settings

from agent_core.message_contract import GROUP, STREAM
log=logging.getLogger(__name__)
from agent_core.domain_pack import component
from agent_core.workflow_timers import tick_due_timers


def claim(factory):
    current=now()
    with factory.begin() as db:
        event=db.scalar(select(m.Outbox).where(m.Outbox.dead_at.is_(None),
            or_(m.Outbox.lease_until.is_(None),m.Outbox.lease_until<=current),
            or_(m.Outbox.retry_at.is_(None),m.Outbox.retry_at<=current),
            ~exists(select(m.Inbox.id).where(m.Inbox.event_id==m.Outbox.id)),
            or_(m.Outbox.published_at.is_(None),m.Outbox.published_at<=current-timedelta(seconds=60))
            ).order_by(m.Outbox.created_at,m.Outbox.id).with_for_update(skip_locked=True).limit(1))
        if not event:return None
        token=str(uuid.uuid4());event.lease_id=token;event.lease_until=current+timedelta(seconds=15)
        return event.id,token


def publish_once(factory,redis):
    work=claim(factory)
    if not work:return False
    event_id,token=work;error=None
    try:redis.xadd(STREAM,{'event_id':event_id})
    except Exception as exc:error=type(exc).__name__[:80]
    with factory.begin() as db:
        event=db.scalar(select(m.Outbox).where(m.Outbox.id==event_id,m.Outbox.lease_id==token).with_for_update())
        if not event:return True
        event.lease_id=None;event.lease_until=None;event.last_error=error
        if error:
            event.attempts+=1;event.retry_at=now()+timedelta(seconds=min(300,2**min(event.attempts,8)))
            if event.attempts>=20:event.dead_at=now()
        else:event.published_at=now();event.retry_at=None;event.attempts=0
    return True


def permitted(db,user,event):
    return component("notification_policy").permitted(db, user, event)


def deliver(factory,event_id):
    with factory.begin() as db:
        event=db.get(m.Outbox,event_id)
        if not event:return 'UNKNOWN_EVENT'
        inserted=db.execute(insert(m.Inbox).values(id=str(uuid.uuid4()),created_at=now(),event_id=event.id)
            .on_conflict_do_nothing(index_elements=['event_id']).returning(m.Inbox.id)).scalar_one_or_none()
        if not inserted:return 'DUPLICATE'
        for user_id in set(event.payload.get('recipients',[])):
            user=db.get(m.User,user_id)
            if permitted(db,user,event):
                title = component("notification_policy").title(event.kind)
                db.add(m.Notification(event_id=event.id,user_id=user.id,title=title,resource_id=event.resource_id))
        return 'DELIVERED'


def consume_batch(factory,redis,messages):
    for stream_id,fields in messages:
        event_id=fields.get('event_id')
        if not event_id or len(event_id)!=36:
            redis.xack(STREAM,GROUP,stream_id);continue
        deliver(factory,event_id)  # DB error leaves pending, retried through XAUTOCLAIM.
        redis.xack(STREAM,GROUP,stream_id)


def main():
    argparse.ArgumentParser(description=__doc__).parse_args()
    config=settings()
    redis=Redis.from_url(config.redis_url,decode_responses=True,socket_connect_timeout=2,socket_timeout=3,protocol=2)
    consumer='worker-'+str(uuid.uuid4())
    while True:
        try:
            tick_due_timers(SessionLocal, limit=25)
            try:redis.xgroup_create(STREAM,GROUP,id='0',mkstream=True)
            except ResponseError as exc:
                if 'BUSYGROUP' not in str(exc):raise
            for _ in range(25):
                if not publish_once(SessionLocal,redis):break
            pending=redis.xautoclaim(STREAM,GROUP,consumer,30_000,'0-0',count=25)
            consume_batch(SessionLocal,redis,pending[1])
            for _,messages in redis.xreadgroup(GROUP,consumer,{STREAM:'>'},count=25,block=1000):
                consume_batch(SessionLocal,redis,messages)
        except Exception as exc:
            # Exception bodies may contain connection URLs. Only emit the type.
            log.warning('Message worker retry: %s',type(exc).__name__)
            time.sleep(2)


if __name__=='__main__':main()
