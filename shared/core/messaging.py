"""
shared/core/messaging.py — Redis Streams message bus.

Why Redis Streams (not pub/sub):
  • Messages PERSIST — if a service restarts, it replays missed messages
  • Consumer groups — only ONE instance processes each task (safe to scale)
  • ACK mechanism — task not lost if consumer crashes mid-processing
  • Full audit trail of every message

Stream names follow: agent:{role}   e.g. agent:developer, agent:qa
Result stream:        agent:results
"""
from __future__ import annotations

import asyncio
import json
import logging
from typing import AsyncGenerator, Callable

import redis.asyncio as aioredis

from shared.core.config import settings

log = logging.getLogger("messaging")

# Stream keys
def stream_key(role: str) -> str:
    return f"agent:{role}"

RESULTS_STREAM = "agent:results"
BROADCAST_STREAM = "agent:broadcast"   # manager → all agents


class MessageBus:
    """
    Thin wrapper around Redis Streams.

    Publisher (Manager): await bus.publish(role, payload)
    Consumer (Agent):    async for msg in bus.consume(role): ...
    """

    def __init__(self, redis_url: str = None):
        self._url = redis_url or settings.redis_url
        self._redis: aioredis.Redis | None = None

    async def connect(self):
        self._redis = await aioredis.from_url(
            self._url, encoding="utf-8", decode_responses=True
        )
        log.info(f"✅ Redis connected: {self._url}")

    async def close(self):
        if self._redis:
            await self._redis.aclose()

    # ── Publish ───────────────────────────────────────────────────────────────
    async def publish(self, role: str, payload: dict) -> str:
        """Publish a task to the agent's stream. Returns Redis message ID."""
        key = stream_key(role)
        msg_id = await self._redis.xadd(key, {"data": json.dumps(payload)})
        log.debug(f"📤 Published to {key}: {msg_id}")
        return msg_id

    async def publish_result(self, payload: dict) -> str:
        msg_id = await self._redis.xadd(RESULTS_STREAM, {"data": json.dumps(payload)})
        return msg_id

    # ── Consume (blocking, with consumer groups for exactly-once) ────────────
    async def consume(
        self,
        role: str,
        group: str | None = None,
        consumer: str | None = None,
        batch: int = 1,
    ) -> AsyncGenerator[dict, None]:
        """
        Async generator — yields payload dicts from the stream.
        Uses consumer groups so only one instance processes each message.
        Pass a unique consumer name per container/worker for horizontal scaling.
        """
        import socket
        key      = stream_key(role)
        group    = group    or f"group:{role}"
        consumer = consumer or f"worker:{role}-{socket.gethostname()}"

        # Create stream + group if they don't exist
        try:
            await self._redis.xgroup_create(key, group, id="0", mkstream=True)
        except Exception:
            pass  # group already exists — fine

        log.info(f"👂 Listening on stream '{key}' as consumer '{consumer}'")

        while True:
            try:
                entries = await self._redis.xreadgroup(
                    group, consumer, {key: ">"}, count=batch, block=5000
                )
                if not entries:
                    continue
                for stream_name, messages in entries:
                    for msg_id, fields in messages:
                        payload = json.loads(fields["data"])
                        yield msg_id, payload
                        # ACK after yield so caller can ACK after processing
                        await self._redis.xack(key, group, msg_id)
            except aioredis.ConnectionError:
                log.warning("Redis disconnected, reconnecting in 3s…")
                await asyncio.sleep(3)
                await self.connect()
            except Exception as e:
                log.error(f"Stream consumer error: {e}")
                await asyncio.sleep(1)

    async def consume_results(
        self, group: str = "result-readers", consumer: str = "manager"
    ) -> AsyncGenerator[dict, None]:
        """Consume from the results stream (used by Manager)."""
        try:
            await self._redis.xgroup_create(RESULTS_STREAM, group, id="0", mkstream=True)
        except Exception:
            pass
        while True:
            entries = await self._redis.xreadgroup(
                group, consumer, {RESULTS_STREAM: ">"}, count=10, block=5000
            )
            if not entries:
                continue
            for _, messages in entries:
                for msg_id, fields in messages:
                    yield json.loads(fields["data"])
                    await self._redis.xack(RESULTS_STREAM, group, msg_id)


# ── Module-level singleton ─────────────────────────────────────────────────────
bus = MessageBus()
