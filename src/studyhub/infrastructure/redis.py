import hashlib

from fastapi import HTTPException
from redis.asyncio import Redis
from redis.exceptions import RedisError

RATE_LIMIT = """
local count = redis.call('INCR', KEYS[1])
if count == 1 then redis.call('EXPIRE', KEYS[1], ARGV[1]) end
return count
"""


def create_redis(url: str, *, connections: int = 20) -> Redis:
    return Redis.from_url(
        url,
        decode_responses=True,
        max_connections=connections,
        socket_connect_timeout=2,
        socket_timeout=3,
        health_check_interval=30,
    )


async def rate_limit(redis: Redis, namespace: str, identity: str, limit: int, seconds: int):
    digest = hashlib.sha256(identity.encode()).hexdigest()
    try:
        count = await redis.eval(RATE_LIMIT, 1, f"{namespace}:rate:{digest}", seconds)
    except RedisError as exc:
        raise HTTPException(503, "Authentication temporarily unavailable") from exc
    if count > limit:
        raise HTTPException(429, "Too many requests", headers={"Retry-After": str(seconds)})
