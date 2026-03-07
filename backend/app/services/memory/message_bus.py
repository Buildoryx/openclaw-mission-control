"""Redis Streams message bus for inter-agent deliberation events.

The message bus provides a lightweight pub/sub layer on top of Redis Streams,
enabling real-time event propagation between agents participating in
deliberations.  Each board gets its own stream key, and consumers can
subscribe to filtered event types.

Stream keys follow the pattern::

    mc:streams:{board_id}:deliberation

Event types published through the bus:

- ``deliberation.started`` — new deliberation opened
- ``deliberation.entry_added`` — new entry contributed
- ``deliberation.phase_advanced`` — phase transition occurred
- ``deliberation.synthesis_submitted`` — synthesis submitted
- ``deliberation.concluded`` — deliberation reached terminal state
- ``deliberation.abandoned`` — deliberation was abandoned
- ``agent.position_changed`` — an agent revised its stance
- ``memory.promoted`` — synthesis promoted to board memory

The bus honours ``settings.memory_stream_maxlen`` to cap stream length
(default 10 000 entries) and uses approximate trimming (``~``) for
performance.
"""

from __future__ import annotations

import json
import logging
import time
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any, AsyncIterator
from uuid import UUID, uuid4

import redis.asyncio as aioredis

from app.core.config import settings

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Stream key helpers
# ---------------------------------------------------------------------------

_STREAM_PREFIX = "mc:streams"


def _stream_key(board_id: UUID, channel: str = "deliberation") -> str:
    """Build the Redis stream key for a board channel."""
    return f"{_STREAM_PREFIX}:{board_id}:{channel}"


def _consumer_group(purpose: str = "mc-workers") -> str:
    """Return the default consumer group name."""
    return purpose


# ---------------------------------------------------------------------------
# Event envelope
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class BusEvent:
    """Immutable event envelope transmitted through the message bus."""

    event_type: str
    board_id: UUID
    payload: dict[str, Any]
    event_id: UUID = field(default_factory=uuid4)
    timestamp: datetime = field(default_factory=lambda: datetime.now(UTC))
    correlation_id: UUID | None = None
    agent_id: UUID | None = None

    def to_stream_fields(self) -> dict[str, str]:
        """Serialize the event into Redis Stream field-value pairs."""
        data: dict[str, Any] = {
            "event_id": str(self.event_id),
            "event_type": self.event_type,
            "board_id": str(self.board_id),
            "payload": json.dumps(self.payload, default=str, separators=(",", ":")),
            "timestamp": self.timestamp.isoformat(),
        }
        if self.correlation_id is not None:
            data["correlation_id"] = str(self.correlation_id)
        if self.agent_id is not None:
            data["agent_id"] = str(self.agent_id)
        return data

    @classmethod
    def from_stream_fields(cls, fields: dict[bytes | str, bytes | str]) -> BusEvent:
        """Deserialize a Redis Stream entry into a :class:`BusEvent`."""
        decoded: dict[str, str] = {}
        for k, v in fields.items():
            key = k.decode("utf-8") if isinstance(k, bytes) else k
            val = v.decode("utf-8") if isinstance(v, bytes) else v
            decoded[key] = val

        correlation_id: UUID | None = None
        if decoded.get("correlation_id"):
            correlation_id = UUID(decoded["correlation_id"])

        agent_id: UUID | None = None
        if decoded.get("agent_id"):
            agent_id = UUID(decoded["agent_id"])

        return cls(
            event_id=UUID(decoded["event_id"]),
            event_type=decoded["event_type"],
            board_id=UUID(decoded["board_id"]),
            payload=json.loads(decoded.get("payload", "{}")),
            timestamp=datetime.fromisoformat(decoded["timestamp"]),
            correlation_id=correlation_id,
            agent_id=agent_id,
        )


# ---------------------------------------------------------------------------
# Message bus service
# ---------------------------------------------------------------------------


class MessageBus:
    """Redis Streams-backed message bus for deliberation events.

    Usage::

        bus = MessageBus()
        await bus.publish(BusEvent(
            event_type="deliberation.started",
            board_id=board.id,
            payload={"deliberation_id": str(delib.id), "topic": delib.topic},
        ))

    Consumers can iterate over events with::

        async for event in bus.subscribe(board_id):
            handle(event)
    """

    def __init__(self, redis_url: str | None = None) -> None:
        self._redis_url = redis_url or settings.rq_redis_url
        self._client: aioredis.Redis | None = None
        self._maxlen = settings.memory_stream_maxlen

    # -- connection management ----------------------------------------------

    async def _get_client(self) -> aioredis.Redis:
        """Lazily initialize and return the async Redis client."""
        if self._client is None:
            self._client = aioredis.from_url(
                self._redis_url,
                decode_responses=False,
            )
            logger.info(
                "message_bus.redis.connected url=%s",
                self._redis_url.split("@")[-1],  # strip credentials
            )
        return self._client

    async def close(self) -> None:
        """Close the underlying Redis connection."""
        if self._client is not None:
            await self._client.aclose()  # type: ignore[union-attr]
            self._client = None
            logger.info("message_bus.redis.closed")

    # -- publish ------------------------------------------------------------

    async def publish(self, event: BusEvent) -> str | None:
        """Publish an event to the board's deliberation stream.

        Returns the Redis stream message ID on success, or ``None`` on
        failure (errors are logged but not raised so callers are not
        disrupted by bus outages).
        """
        client = await self._get_client()
        stream = _stream_key(event.board_id)
        try:
            msg_id: bytes | str = await client.xadd(
                stream,
                event.to_stream_fields(),
                maxlen=self._maxlen,
                approximate=True,
            )
            decoded_id = msg_id.decode("utf-8") if isinstance(msg_id, bytes) else msg_id
            logger.debug(
                "message_bus.published stream=%s type=%s id=%s",
                stream,
                event.event_type,
                decoded_id,
            )
            return decoded_id
        except Exception:
            logger.exception(
                "message_bus.publish_failed stream=%s type=%s",
                stream,
                event.event_type,
            )
            return None

    async def publish_many(self, events: list[BusEvent]) -> int:
        """Publish multiple events, returning the count of successes."""
        published = 0
        for event in events:
            result = await self.publish(event)
            if result is not None:
                published += 1
        return published

    # -- subscribe / consume ------------------------------------------------

    async def subscribe(
        self,
        board_id: UUID,
        *,
        event_types: set[str] | None = None,
        last_id: str = "$",
        block_ms: int = 2000,
        count: int = 50,
        channel: str = "deliberation",
    ) -> AsyncIterator[BusEvent]:
        """Yield events from a board stream, optionally filtered by type.

        This is an infinite async generator intended for SSE endpoints and
        background consumers.  It blocks for up to *block_ms* milliseconds
        waiting for new messages before yielding control (allowing callers
        to check cancellation or send heartbeats).

        Parameters
        ----------
        board_id:
            Board whose stream to consume.
        event_types:
            If set, only events matching these types are yielded.
        last_id:
            Redis stream ID to start reading after.  Use ``"$"`` for new
            messages only or ``"0"`` for the full history.
        block_ms:
            How long to block waiting for new messages (milliseconds).
        count:
            Maximum messages per read cycle.
        channel:
            Stream channel suffix (default ``"deliberation"``).
        """
        client = await self._get_client()
        stream = _stream_key(board_id, channel)
        cursor = last_id

        while True:
            try:
                results = await client.xread(
                    {stream: cursor},
                    block=block_ms,
                    count=count,
                )
            except Exception:
                logger.exception(
                    "message_bus.subscribe_error stream=%s",
                    stream,
                )
                # Back off briefly before retrying
                await _async_sleep(1.0)
                continue

            if not results:
                # No new messages within the block window — yield control
                continue

            for _stream_name, messages in results:
                for msg_id_raw, fields in messages:
                    msg_id = (
                        msg_id_raw.decode("utf-8")
                        if isinstance(msg_id_raw, bytes)
                        else msg_id_raw
                    )
                    cursor = msg_id
                    try:
                        event = BusEvent.from_stream_fields(fields)
                    except Exception:
                        logger.exception(
                            "message_bus.deserialize_failed stream=%s msg_id=%s",
                            stream,
                            msg_id,
                        )
                        continue

                    if event_types and event.event_type not in event_types:
                        continue

                    yield event

    # -- consumer group operations ------------------------------------------

    async def ensure_consumer_group(
        self,
        board_id: UUID,
        group: str = "mc-workers",
        *,
        start_id: str = "0",
        channel: str = "deliberation",
    ) -> bool:
        """Create a consumer group on a board stream (idempotent).

        Returns ``True`` if the group was created, ``False`` if it already
        exists.
        """
        client = await self._get_client()
        stream = _stream_key(board_id, channel)
        try:
            await client.xgroup_create(
                stream,
                group,
                id=start_id,
                mkstream=True,
            )
            logger.info(
                "message_bus.group_created stream=%s group=%s",
                stream,
                group,
            )
            return True
        except aioredis.ResponseError as exc:
            if "BUSYGROUP" in str(exc):
                # Group already exists — this is fine
                return False
            raise

    async def consume_group(
        self,
        board_id: UUID,
        group: str = "mc-workers",
        consumer: str | None = None,
        *,
        event_types: set[str] | None = None,
        block_ms: int = 2000,
        count: int = 50,
        channel: str = "deliberation",
    ) -> AsyncIterator[tuple[str, BusEvent]]:
        """Consume events via a consumer group for reliable processing.

        Yields ``(message_id, event)`` tuples.  Callers should acknowledge
        processed messages with :meth:`ack`.

        Parameters
        ----------
        board_id:
            Board whose stream to consume.
        group:
            Consumer group name.
        consumer:
            Unique consumer name within the group (auto-generated if omitted).
        event_types:
            Optional filter set.
        block_ms:
            Block timeout in milliseconds.
        count:
            Max messages per read cycle.
        channel:
            Stream channel suffix.
        """
        client = await self._get_client()
        stream = _stream_key(board_id, channel)
        consumer_name = consumer or f"consumer-{uuid4().hex[:8]}"

        await self.ensure_consumer_group(board_id, group, channel=channel)

        while True:
            try:
                results = await client.xreadgroup(
                    group,
                    consumer_name,
                    {stream: ">"},
                    block=block_ms,
                    count=count,
                )
            except Exception:
                logger.exception(
                    "message_bus.consume_group_error stream=%s group=%s",
                    stream,
                    group,
                )
                await _async_sleep(1.0)
                continue

            if not results:
                continue

            for _stream_name, messages in results:
                for msg_id_raw, fields in messages:
                    msg_id = (
                        msg_id_raw.decode("utf-8")
                        if isinstance(msg_id_raw, bytes)
                        else msg_id_raw
                    )
                    try:
                        event = BusEvent.from_stream_fields(fields)
                    except Exception:
                        logger.exception(
                            "message_bus.deserialize_failed stream=%s msg_id=%s",
                            stream,
                            msg_id,
                        )
                        continue

                    if event_types and event.event_type not in event_types:
                        # Still ack to avoid re-delivery of filtered events
                        await self.ack(board_id, group, msg_id, channel=channel)
                        continue

                    yield msg_id, event

    async def ack(
        self,
        board_id: UUID,
        group: str,
        message_id: str,
        *,
        channel: str = "deliberation",
    ) -> bool:
        """Acknowledge a consumed message within a consumer group."""
        client = await self._get_client()
        stream = _stream_key(board_id, channel)
        try:
            count = await client.xack(stream, group, message_id)
            return int(count) > 0
        except Exception:
            logger.exception(
                "message_bus.ack_failed stream=%s group=%s msg_id=%s",
                stream,
                group,
                message_id,
            )
            return False

    # -- utility ------------------------------------------------------------

    async def stream_length(
        self,
        board_id: UUID,
        channel: str = "deliberation",
    ) -> int:
        """Return the current length of a board's stream."""
        client = await self._get_client()
        stream = _stream_key(board_id, channel)
        try:
            length = await client.xlen(stream)
            return int(length)
        except Exception:
            logger.exception(
                "message_bus.xlen_failed stream=%s",
                stream,
            )
            return 0

    async def trim(
        self,
        board_id: UUID,
        maxlen: int | None = None,
        *,
        channel: str = "deliberation",
    ) -> int:
        """Trim a board stream to a maximum length.

        Returns the number of entries removed.
        """
        client = await self._get_client()
        stream = _stream_key(board_id, channel)
        effective_maxlen = maxlen if maxlen is not None else self._maxlen
        try:
            before = await client.xlen(stream)
            await client.xtrim(stream, maxlen=effective_maxlen, approximate=True)
            after = await client.xlen(stream)
            removed = max(0, int(before) - int(after))
            if removed > 0:
                logger.info(
                    "message_bus.trimmed stream=%s removed=%d remaining=%d",
                    stream,
                    removed,
                    int(after),
                )
            return removed
        except Exception:
            logger.exception(
                "message_bus.trim_failed stream=%s",
                stream,
            )
            return 0

    async def read_history(
        self,
        board_id: UUID,
        *,
        start: str = "-",
        end: str = "+",
        count: int = 100,
        channel: str = "deliberation",
    ) -> list[BusEvent]:
        """Read historical events from a board stream range.

        Parameters
        ----------
        board_id:
            Target board.
        start:
            Stream ID or ``"-"`` for the beginning.
        end:
            Stream ID or ``"+"`` for the end.
        count:
            Maximum events to return.
        channel:
            Stream channel suffix.
        """
        client = await self._get_client()
        stream = _stream_key(board_id, channel)
        events: list[BusEvent] = []
        try:
            results = await client.xrange(stream, min=start, max=end, count=count)
            for _msg_id, fields in results:
                try:
                    events.append(BusEvent.from_stream_fields(fields))
                except Exception:
                    logger.exception(
                        "message_bus.history_deserialize_failed stream=%s",
                        stream,
                    )
        except Exception:
            logger.exception(
                "message_bus.read_history_failed stream=%s",
                stream,
            )
        return events


# ---------------------------------------------------------------------------
# Async sleep helper (avoids importing asyncio at module level)
# ---------------------------------------------------------------------------


async def _async_sleep(seconds: float) -> None:
    """Async sleep without top-level asyncio import."""
    import asyncio

    await asyncio.sleep(seconds)


# ---------------------------------------------------------------------------
# Module-level singleton
# ---------------------------------------------------------------------------

message_bus = MessageBus()
