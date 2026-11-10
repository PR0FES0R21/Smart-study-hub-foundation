import asyncio
from unittest.mock import AsyncMock
from uuid import uuid4

import jwt
import pytest
from redis.exceptions import ConnectionError

from studyhub.core.config import Settings
from studyhub.core.security import access_token, decode_access
from studyhub.infrastructure.cache import Cache
from studyhub.infrastructure.events import EventHub
from studyhub.schemas import Login


def settings(**overrides):
    return Settings(
        _env_file=None,
        database_url="postgresql+asyncpg://localhost/test",
        cache_url="redis://localhost:6379/0",
        control_url="redis://localhost:6380/0",
        broker_url="redis://localhost:6381/0",
        jwt_secret="x" * 48,
        **overrides,
    )


def test_jwt_rejects_wrong_audience_and_algorithm():
    config = settings()
    user, session = uuid4(), uuid4()
    token = access_token(config, user, session)
    claims = decode_access(config, token)
    assert claims["sub"] == str(user)
    wrong = config.model_copy(update={"jwt_audience": "other-app"})
    with pytest.raises(jwt.InvalidAudienceError):
        decode_access(wrong, token)
    unsigned = jwt.encode(claims, key="", algorithm="none")
    with pytest.raises(jwt.InvalidAlgorithmError):
        decode_access(config, unsigned)


def test_password_whitespace_is_preserved():
    assert Login(email="user@example.org", password=" secret ").password == " secret "


async def test_cache_outage_falls_back_to_database():
    redis = AsyncMock()
    redis.get.side_effect = ConnectionError("offline")
    compute = AsyncMock(return_value={"subject_count": 7})
    assert await Cache(redis, "test").subject_statistics(compute, school_id=uuid4()) == {
        "subject_count": 7
    }
    compute.assert_awaited_once()


async def test_cache_has_finite_ttl_and_owner_safe_lock_release():
    redis = AsyncMock()
    redis.get.side_effect = ["4", None]
    redis.set.return_value = True
    school_id = uuid4()
    await Cache(redis, "test").subject_statistics(
        AsyncMock(return_value={"subject_count": 1}), school_id=school_id
    )
    assert redis.set.await_args_list[1].kwargs["ex"] == 30
    assert (
        f"test:cache:v1:school:{school_id}:subjects:stats:4" == redis.set.await_args_list[1].args[0]
    )
    assert redis.eval.await_args.args[1] == 1


async def test_private_event_routing_and_slow_client_isolation():
    hub = EventHub(AsyncMock(), "test", AsyncMock())
    alice, bob = hub.connect("alice", "school-a"), hub.connect("bob", "school-a")
    alice_other_school = hub.connect("alice", "school-b")
    for _ in range(64):
        alice.queue.put_nowait({})
    hub.deliver(
        {"version": 1, "user_id": "alice", "school_id": "school-a", "type": "job.completed"}
    )
    assert alice.id in hub.connections and alice.closing
    assert alice.queue.get_nowait() is None
    hub.disconnect(alice)
    assert bob.queue.empty()
    assert alice_other_school.queue.empty()
    hub.deliver({"version": 1, "type": "job.completed"})
    assert bob.queue.empty()


async def test_subscriber_reconnects_after_connection_failure():
    class PubSub:
        def __init__(self, fail):
            self.fail = fail

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            pass

        async def subscribe(self, channel):
            pass

        async def listen(self):
            yield {"type": "subscribe"}
            if self.fail:
                raise ConnectionError("disconnected")
            await asyncio.Event().wait()

    class Redis:
        attempts = 0
        reconnected = asyncio.Event()

        def pubsub(self):
            self.attempts += 1
            if self.attempts == 2:
                self.reconnected.set()
            return PubSub(self.attempts == 1)

    redis = Redis()
    hub = EventHub(redis, "test", AsyncMock())
    hub.start()
    try:
        async with asyncio.timeout(3):
            await redis.reconnected.wait()
            await hub.ready.wait()
        assert not hub.task.done()
    finally:
        await hub.stop()
    assert hub.task.done()
