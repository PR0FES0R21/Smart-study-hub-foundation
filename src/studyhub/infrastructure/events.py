import asyncio
import json
import logging
from collections import defaultdict
from dataclasses import dataclass, field
from uuid import uuid4

from redis.asyncio import Redis
from redis.exceptions import RedisError

from studyhub.infrastructure.cache import Cache

logger = logging.getLogger(__name__)


@dataclass(eq=False)
class Connection:
    user_id: str
    school_id: str
    queue: asyncio.Queue = field(default_factory=lambda: asyncio.Queue(maxsize=64))
    id: str = field(default_factory=lambda: str(uuid4()))
    closing: bool = False


class EventHub:
    """One subscriber per API process; bounded queues isolate slow WebSocket clients."""

    def __init__(self, redis: Redis, namespace: str, cache: Cache):
        self.redis, self.cache = redis, cache
        self.channel = f"{namespace}:events:v1"
        self.connections: dict[str, Connection] = {}
        self.by_target: dict[tuple[str, str], dict[str, Connection]] = defaultdict(dict)
        self.user_counts: dict[str, int] = defaultdict(int)
        self.ready = asyncio.Event()
        self.task: asyncio.Task | None = None

    def start(self):
        self.task = asyncio.create_task(self.listen(), name="redis-event-subscriber")

    async def stop(self):
        if self.task:
            self.task.cancel()
            await asyncio.gather(self.task, return_exceptions=True)
        self.ready.clear()

    def connect(self, user_id: str, school_id: str) -> Connection:
        if self.user_counts[user_id] >= 3:
            raise ValueError("Connection limit reached")
        connection = Connection(user_id, school_id)
        self.connections[connection.id] = connection
        self.by_target[(user_id, school_id)][connection.id] = connection
        self.user_counts[user_id] += 1
        return connection

    def disconnect(self, connection: Connection):
        if self.connections.pop(connection.id, None) is None:
            return
        key = (connection.user_id, connection.school_id)
        bucket = self.by_target[key]
        bucket.pop(connection.id, None)
        if not bucket:
            del self.by_target[key]
        self.user_counts[connection.user_id] -= 1
        if self.user_counts[connection.user_id] == 0:
            del self.user_counts[connection.user_id]

    async def listen(self):
        backoff = 0.25
        while True:
            try:
                async with self.redis.pubsub() as pubsub:
                    await pubsub.subscribe(self.channel)
                    # Consume the subscribe acknowledgement before declaring readiness.
                    async for message in pubsub.listen():
                        if message["type"] == "subscribe":
                            self.ready.set()
                            backoff = 0.25
                        elif message["type"] == "message":
                            try:
                                event = json.loads(message["data"])
                                if not isinstance(event, dict) or event.get("version") != 1:
                                    continue
                                self.deliver(event)
                            except (ValueError, TypeError, KeyError):
                                logger.warning("event_invalid_payload")
                    raise ConnectionError("PubSub iterator ended")
            except asyncio.CancelledError:
                raise
            except (RedisError, OSError, ConnectionError):
                self.ready.clear()
                logger.warning("event_subscriber_reconnecting")
                await asyncio.sleep(backoff)
                backoff = min(backoff * 2, 10)
            finally:
                self.ready.clear()

    def deliver(self, event: dict):
        # All private events require an explicit user; never infer a broad audience.
        user_id = event.get("user_id")
        school_id = event.get("school_id")
        if not isinstance(user_id, str) or not isinstance(school_id, str):
            return
        for connection in tuple(self.by_target.get((user_id, school_id), {}).values()):
            if connection.closing:
                continue
            if connection.queue.full():
                connection.closing = True
                while not connection.queue.empty():
                    connection.queue.get_nowait()
                connection.queue.put_nowait(None)  # The writer closes the slow client.
            else:
                connection.queue.put_nowait(event)
