import json
import logging
from collections.abc import Awaitable, Callable
from secrets import token_hex
from uuid import UUID

from redis.asyncio import Redis
from redis.exceptions import RedisError

logger = logging.getLogger(__name__)
RELEASE_LOCK = "if redis.call('get', KEYS[1]) == ARGV[1] then return redis.call('del', KEYS[1]) else return 0 end"


class Cache:
    """Best-effort bounded cache; authoritative application data stays in PostgreSQL."""

    def __init__(self, redis: Redis, namespace: str):
        self.redis = redis
        self.prefix = f"{namespace}:cache:v1"

    async def invalidate_subjects(self, school_id: UUID):
        try:
            await self.redis.set(
                f"{self.prefix}:school:{school_id}:subjects:generation", token_hex(16)
            )
        except RedisError:
            logger.warning("cache_invalidation_unavailable")

    async def subject_statistics(
        self, compute: Callable[[], Awaitable[dict]], *, school_id: UUID
    ) -> dict:
        lock = None
        owner = token_hex(16)
        try:
            generation_key = f"{self.prefix}:school:{school_id}:subjects:generation"
            generation = await self.redis.get(generation_key)
            if generation is None:
                await self.redis.set(generation_key, token_hex(16), nx=True)
                generation = await self.redis.get(generation_key)
                if generation is None:
                    return await compute()
            key = f"{self.prefix}:school:{school_id}:subjects:stats:{generation}"
            cached = await self.redis.get(key)
            if cached is not None:
                return json.loads(cached)
            lock = f"{key}:lock"
            acquired = await self.redis.set(lock, owner, nx=True, ex=10)
        except (RedisError, ValueError):
            logger.warning("cache_read_unavailable")
            return await compute()
        # No waiting on a cache lock: skip caching when another writer owns it.
        if not acquired:
            return await compute()
        try:
            data = await compute()
            try:
                await self.redis.set(key, json.dumps(data), ex=30)
            except RedisError:
                logger.warning("cache_write_unavailable")
            return data
        finally:
            try:
                await self.redis.eval(RELEASE_LOCK, 1, lock, owner)
            except RedisError:
                logger.warning("cache_lock_release_unavailable")
