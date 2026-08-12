from __future__ import annotations

import asyncio
import json
from collections import defaultdict
from typing import AsyncIterator

import redis.asyncio as redis

from .config import get_settings


class EventBroker:
    def __init__(self) -> None:
        self._redis: redis.Redis | None = None
        self._local: dict[str, set[asyncio.Queue]] = defaultdict(set)

    async def connect(self) -> None:
        try:
            client = redis.from_url(
                get_settings().redis_url,
                decode_responses=True,
                socket_connect_timeout=1,
                socket_timeout=2,
            )
            await client.ping()
            self._redis = client
        except Exception:
            self._redis = None

    async def close(self) -> None:
        if self._redis:
            await self._redis.aclose()

    async def publish(self, channel: str, event: dict) -> None:
        payload = json.dumps(event, ensure_ascii=False, default=str)
        if not self._redis:
            await self.connect()
        if self._redis:
            try:
                await self._redis.publish(channel, payload)
            except Exception:
                self._redis = None
        for queue in tuple(self._local[channel]):
            if not queue.full():
                queue.put_nowait(payload)

    async def subscribe(self, channel: str) -> AsyncIterator[str]:
        if not self._redis:
            await self.connect()
        if self._redis:
            pubsub = self._redis.pubsub()
            try:
                await pubsub.subscribe(channel)
                while True:
                    message = await pubsub.get_message(ignore_subscribe_messages=True, timeout=10)
                    if message:
                        yield str(message["data"])
                    else:
                        yield json.dumps({"type": "heartbeat"})
            except Exception:
                self._redis = None
            finally:
                try:
                    await pubsub.unsubscribe(channel)
                    await pubsub.aclose()
                except Exception:
                    pass
            if self._redis:
                return

        queue: asyncio.Queue = asyncio.Queue(maxsize=200)
        self._local[channel].add(queue)
        try:
            while True:
                try:
                    yield await asyncio.wait_for(queue.get(), timeout=10)
                except TimeoutError:
                    yield json.dumps({"type": "heartbeat"})
        finally:
            self._local[channel].discard(queue)


broker = EventBroker()
