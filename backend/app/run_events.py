"""Best-effort cross-process notifications for live Agent run projections.

PostgreSQL remains the authoritative checkpoint store.  Redis Pub/Sub only
wakes connected browsers so they can read a fresh, authorization-filtered
projection instead of polling the database for every model delta.
"""
from functools import lru_cache
import json
import secrets

from redis import Redis
from redis.asyncio import Redis as AsyncRedis
from redis.exceptions import RedisError

from .config import settings


CHANNEL_PREFIX = "agent:conversation-runs:"


def conversation_channel(conversation_id: str) -> str:
    return CHANNEL_PREFIX + conversation_id


@lru_cache
def _publisher(redis_url: str):
    return Redis.from_url(
        redis_url,
        decode_responses=True,
        socket_connect_timeout=0.2,
        socket_timeout=0.2,
        protocol=2,
    )


def publish_run_update(conversation_id: str, run_id: str, status: str) -> bool:
    """Wake live clients after the database transaction has committed.

    Redis loss must never change a run result or roll back an authoritative
    checkpoint.  Returning ``False`` lets callers/tests observe degradation;
    the browser reconnect/fallback path will refresh from PostgreSQL.
    """
    if not conversation_id or not run_id:
        return False
    payload = json.dumps(
        {
            "event_id": secrets.token_urlsafe(12),
            "run_id": run_id,
            "status": status,
        },
        separators=(",", ":"),
    )
    try:
        _publisher(settings().redis_url).publish(conversation_channel(conversation_id), payload)
        return True
    except RedisError:
        return False


async def subscribe_run_updates(conversation_id: str):
    """Yield readiness, update and heartbeat signals for one conversation."""
    client = AsyncRedis.from_url(
        settings().redis_url,
        decode_responses=True,
        socket_connect_timeout=1,
        socket_timeout=20,
        protocol=2,
    )
    pubsub = client.pubsub()
    try:
        await pubsub.subscribe(conversation_channel(conversation_id))
        # The endpoint reads its initial PostgreSQL snapshot only after this
        # subscription is active, closing the subscribe/snapshot race.
        yield {"type": "ready"}
        while True:
            message = await pubsub.get_message(ignore_subscribe_messages=True, timeout=15)
            if message and message.get("type") == "message":
                yield {"type": "update", "data": message.get("data")}
            else:
                yield {"type": "heartbeat"}
    except RedisError:
        yield {"type": "unavailable"}
    finally:
        try:
            await pubsub.aclose()
        finally:
            await client.aclose()
