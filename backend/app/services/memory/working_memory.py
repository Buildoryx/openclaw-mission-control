"""Working memory service — Redis ephemeral layer for active deliberations.

Working memory provides fast, TTL-scoped state for deliberations that are
currently in progress.  Data stored here is intentionally ephemeral: it
expires automatically after a configurable TTL (default 600s) and is never
treated as durable.

Typical payloads cached in working memory:

- Active deliberation context (participants, current phase, recent entries)
- Agent scratch-pad state during a deliberation round
- Transient divergence signals before they mature into formal entries
- SSE subscriber tracking for live deliberation streams

All keys are namespaced under ``mc:wm:{board_id}:{deliberation_id}:*`` to
avoid collisions and enable efficient per-deliberation cleanup.

The service degrades gracefully: if Redis is unavailable, methods return
``None`` / empty results and log warnings rather than raising.  This lets
the deliberation engine continue operating (with higher DB latency) when
the cache layer is down.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime
from typing import Any
from uuid import UUID

import redis
import redis.asyncio as aioredis

from app.core.config import settings

logger = logging.getLogger(__name__)

# Redis key namespace
_PREFIX = "mc:wm"

# Sub-key segments
_CTX = "ctx"  # deliberation context blob
_SCRATCH = "scratch"  # per-agent scratch-pad
_SUBS = "subs"  # SSE subscriber set
_PHASE = "phase"  # current phase cache
_LOCK = "lock"  # distributed entry lock
_ENTRIES = "entries"  # recent entries list (capped)

# Maximum recent entries kept in working memory
_MAX_RECENT_ENTRIES = 50


def _key(*parts: str) -> str:
    """Build a colon-separated Redis key from namespace parts."""
    return ":".join((_PREFIX, *parts))


def _board_delib_key(board_id: UUID, deliberation_id: UUID, suffix: str) -> str:
    """Build a working-memory key scoped to a board + deliberation."""
    return _key(str(board_id), str(deliberation_id), suffix)


def _serialize(value: Any) -> str:
    """JSON-serialize a value for Redis storage."""
    return json.dumps(value, separators=(",", ":"), default=str)


def _deserialize(raw: str | bytes | None) -> Any:
    """Deserialize a JSON value retrieved from Redis."""
    if raw is None:
        return None
    if isinstance(raw, bytes):
        raw = raw.decode("utf-8")
    try:
        return json.loads(raw)
    except (json.JSONDecodeError, TypeError):
        return None


class WorkingMemoryService:
    """Redis-backed ephemeral cache for active deliberation state.

    All operations are async and use a shared ``redis.asyncio`` connection
    pool created lazily on first access.
    """

    def __init__(
        self,
        redis_url: str | None = None,
        default_ttl: int | None = None,
    ) -> None:
        self._redis_url = redis_url or settings.rq_redis_url
        self._default_ttl = default_ttl or settings.memory_working_ttl_default
        self._client: aioredis.Redis | None = None

    # ------------------------------------------------------------------
    # Connection lifecycle
    # ------------------------------------------------------------------

    async def _get_client(self) -> aioredis.Redis:
        """Lazily create or return the async Redis client."""
        if self._client is None:
            self._client = aioredis.from_url(
                self._redis_url,
                decode_responses=False,
                socket_connect_timeout=3,
                socket_timeout=3,
            )
        return self._client

    async def close(self) -> None:
        """Close the Redis connection pool."""
        if self._client is not None:
            await self._client.aclose()  # type: ignore[union-attr]
            self._client = None

    # ------------------------------------------------------------------
    # Deliberation context — full snapshot of active deliberation state
    # ------------------------------------------------------------------

    async def set_context(
        self,
        board_id: UUID,
        deliberation_id: UUID,
        context: dict[str, Any],
        *,
        ttl: int | None = None,
    ) -> bool:
        """Store the deliberation context blob with TTL expiration.

        The context typically contains:
        - ``topic``: deliberation topic string
        - ``status``: current phase/status
        - ``participants``: list of agent UUIDs
        - ``entry_count``: number of entries so far
        - ``max_turns``: configured turn limit
        - ``policy``: serialized deliberation policy overrides
        """
        key = _board_delib_key(board_id, deliberation_id, _CTX)
        effective_ttl = ttl if ttl is not None else self._default_ttl
        try:
            client = await self._get_client()
            await client.set(key, _serialize(context), ex=effective_ttl)
            logger.debug(
                "wm.context.set board=%s delib=%s ttl=%d",
                board_id,
                deliberation_id,
                effective_ttl,
            )
            return True
        except (redis.RedisError, OSError) as exc:
            logger.warning(
                "wm.context.set_failed board=%s delib=%s error=%s",
                board_id,
                deliberation_id,
                exc,
            )
            return False

    async def get_context(
        self,
        board_id: UUID,
        deliberation_id: UUID,
    ) -> dict[str, Any] | None:
        """Retrieve the cached deliberation context, or ``None`` if expired/missing."""
        key = _board_delib_key(board_id, deliberation_id, _CTX)
        try:
            client = await self._get_client()
            raw = await client.get(key)
            result = _deserialize(raw)
            if result is not None:
                logger.debug(
                    "wm.context.hit board=%s delib=%s",
                    board_id,
                    deliberation_id,
                )
            return result
        except (redis.RedisError, OSError) as exc:
            logger.warning(
                "wm.context.get_failed board=%s delib=%s error=%s",
                board_id,
                deliberation_id,
                exc,
            )
            return None

    async def delete_context(
        self,
        board_id: UUID,
        deliberation_id: UUID,
    ) -> bool:
        """Remove the deliberation context (e.g. on conclusion/abandon)."""
        key = _board_delib_key(board_id, deliberation_id, _CTX)
        try:
            client = await self._get_client()
            await client.delete(key)
            logger.debug(
                "wm.context.deleted board=%s delib=%s",
                board_id,
                deliberation_id,
            )
            return True
        except (redis.RedisError, OSError) as exc:
            logger.warning(
                "wm.context.delete_failed board=%s delib=%s error=%s",
                board_id,
                deliberation_id,
                exc,
            )
            return False

    # ------------------------------------------------------------------
    # Phase cache — lightweight current-phase lookup
    # ------------------------------------------------------------------

    async def set_phase(
        self,
        board_id: UUID,
        deliberation_id: UUID,
        phase: str,
        *,
        ttl: int | None = None,
    ) -> bool:
        """Cache the current deliberation phase for fast reads."""
        key = _board_delib_key(board_id, deliberation_id, _PHASE)
        effective_ttl = ttl if ttl is not None else self._default_ttl
        try:
            client = await self._get_client()
            await client.set(key, phase.encode(), ex=effective_ttl)
            return True
        except (redis.RedisError, OSError) as exc:
            logger.warning(
                "wm.phase.set_failed board=%s delib=%s error=%s",
                board_id,
                deliberation_id,
                exc,
            )
            return False

    async def get_phase(
        self,
        board_id: UUID,
        deliberation_id: UUID,
    ) -> str | None:
        """Read cached phase, returning ``None`` on miss."""
        key = _board_delib_key(board_id, deliberation_id, _PHASE)
        try:
            client = await self._get_client()
            raw = await client.get(key)
            if raw is None:
                return None
            return raw.decode("utf-8") if isinstance(raw, bytes) else str(raw)
        except (redis.RedisError, OSError) as exc:
            logger.warning(
                "wm.phase.get_failed board=%s delib=%s error=%s",
                board_id,
                deliberation_id,
                exc,
            )
            return None

    # ------------------------------------------------------------------
    # Agent scratch-pad — per-agent transient working notes
    # ------------------------------------------------------------------

    async def set_scratch(
        self,
        board_id: UUID,
        deliberation_id: UUID,
        agent_id: UUID,
        data: dict[str, Any],
        *,
        ttl: int | None = None,
    ) -> bool:
        """Store an agent's scratch-pad data for this deliberation."""
        key = _board_delib_key(board_id, deliberation_id, f"{_SCRATCH}:{agent_id}")
        effective_ttl = ttl if ttl is not None else self._default_ttl
        try:
            client = await self._get_client()
            await client.set(key, _serialize(data), ex=effective_ttl)
            return True
        except (redis.RedisError, OSError) as exc:
            logger.warning(
                "wm.scratch.set_failed agent=%s error=%s",
                agent_id,
                exc,
            )
            return False

    async def get_scratch(
        self,
        board_id: UUID,
        deliberation_id: UUID,
        agent_id: UUID,
    ) -> dict[str, Any] | None:
        """Retrieve an agent's scratch-pad data."""
        key = _board_delib_key(board_id, deliberation_id, f"{_SCRATCH}:{agent_id}")
        try:
            client = await self._get_client()
            raw = await client.get(key)
            return _deserialize(raw)
        except (redis.RedisError, OSError) as exc:
            logger.warning(
                "wm.scratch.get_failed agent=%s error=%s",
                agent_id,
                exc,
            )
            return None

    async def delete_scratch(
        self,
        board_id: UUID,
        deliberation_id: UUID,
        agent_id: UUID,
    ) -> bool:
        """Remove an agent's scratch-pad."""
        key = _board_delib_key(board_id, deliberation_id, f"{_SCRATCH}:{agent_id}")
        try:
            client = await self._get_client()
            await client.delete(key)
            return True
        except (redis.RedisError, OSError) as exc:
            logger.warning(
                "wm.scratch.delete_failed agent=%s error=%s",
                agent_id,
                exc,
            )
            return False

    # ------------------------------------------------------------------
    # Recent entries — capped list of latest entry summaries
    # ------------------------------------------------------------------

    async def push_entry(
        self,
        board_id: UUID,
        deliberation_id: UUID,
        entry_summary: dict[str, Any],
        *,
        ttl: int | None = None,
    ) -> bool:
        """Append an entry summary to the capped recent-entries list.

        The list is trimmed to ``_MAX_RECENT_ENTRIES`` after each push to
        prevent unbounded growth.  The entire list shares a single TTL that
        is refreshed on every push.
        """
        key = _board_delib_key(board_id, deliberation_id, _ENTRIES)
        effective_ttl = ttl if ttl is not None else self._default_ttl
        try:
            client = await self._get_client()
            pipe = client.pipeline()
            pipe.rpush(key, _serialize(entry_summary))
            pipe.ltrim(key, -_MAX_RECENT_ENTRIES, -1)
            pipe.expire(key, effective_ttl)
            await pipe.execute()
            return True
        except (redis.RedisError, OSError) as exc:
            logger.warning(
                "wm.entries.push_failed board=%s delib=%s error=%s",
                board_id,
                deliberation_id,
                exc,
            )
            return False

    async def get_recent_entries(
        self,
        board_id: UUID,
        deliberation_id: UUID,
        *,
        count: int = _MAX_RECENT_ENTRIES,
    ) -> list[dict[str, Any]]:
        """Retrieve the most recent entry summaries from working memory."""
        key = _board_delib_key(board_id, deliberation_id, _ENTRIES)
        try:
            client = await self._get_client()
            raw_list = await client.lrange(key, -count, -1)
            return [item for raw in raw_list if (item := _deserialize(raw)) is not None]
        except (redis.RedisError, OSError) as exc:
            logger.warning(
                "wm.entries.get_failed board=%s delib=%s error=%s",
                board_id,
                deliberation_id,
                exc,
            )
            return []

    # ------------------------------------------------------------------
    # SSE subscriber tracking
    # ------------------------------------------------------------------

    async def add_subscriber(
        self,
        board_id: UUID,
        deliberation_id: UUID,
        subscriber_id: str,
        *,
        ttl: int | None = None,
    ) -> bool:
        """Register an SSE subscriber for a deliberation."""
        key = _board_delib_key(board_id, deliberation_id, _SUBS)
        effective_ttl = ttl if ttl is not None else self._default_ttl * 2
        try:
            client = await self._get_client()
            pipe = client.pipeline()
            pipe.sadd(key, subscriber_id)
            pipe.expire(key, effective_ttl)
            await pipe.execute()
            return True
        except (redis.RedisError, OSError) as exc:
            logger.warning(
                "wm.subs.add_failed sub=%s error=%s",
                subscriber_id,
                exc,
            )
            return False

    async def remove_subscriber(
        self,
        board_id: UUID,
        deliberation_id: UUID,
        subscriber_id: str,
    ) -> bool:
        """Unregister an SSE subscriber."""
        key = _board_delib_key(board_id, deliberation_id, _SUBS)
        try:
            client = await self._get_client()
            await client.srem(key, subscriber_id)
            return True
        except (redis.RedisError, OSError) as exc:
            logger.warning(
                "wm.subs.remove_failed sub=%s error=%s",
                subscriber_id,
                exc,
            )
            return False

    async def get_subscriber_count(
        self,
        board_id: UUID,
        deliberation_id: UUID,
    ) -> int:
        """Return the number of active SSE subscribers."""
        key = _board_delib_key(board_id, deliberation_id, _SUBS)
        try:
            client = await self._get_client()
            return await client.scard(key)
        except (redis.RedisError, OSError) as exc:
            logger.warning(
                "wm.subs.count_failed board=%s delib=%s error=%s",
                board_id,
                deliberation_id,
                exc,
            )
            return 0

    # ------------------------------------------------------------------
    # Distributed entry lock — prevent duplicate concurrent entries
    # ------------------------------------------------------------------

    async def acquire_entry_lock(
        self,
        board_id: UUID,
        deliberation_id: UUID,
        agent_id: UUID,
        *,
        lock_ttl: int = 10,
    ) -> bool:
        """Acquire a short-lived lock to prevent duplicate concurrent entries.

        Returns ``True`` if the lock was acquired, ``False`` if another
        entry submission from the same agent is already in progress.
        """
        key = _board_delib_key(
            board_id,
            deliberation_id,
            f"{_LOCK}:{agent_id}",
        )
        try:
            client = await self._get_client()
            acquired = await client.set(key, b"1", ex=lock_ttl, nx=True)
            return bool(acquired)
        except (redis.RedisError, OSError) as exc:
            logger.warning(
                "wm.lock.acquire_failed agent=%s error=%s",
                agent_id,
                exc,
            )
            # Fail-open: allow the entry if Redis is down
            return True

    async def release_entry_lock(
        self,
        board_id: UUID,
        deliberation_id: UUID,
        agent_id: UUID,
    ) -> bool:
        """Release the entry submission lock."""
        key = _board_delib_key(
            board_id,
            deliberation_id,
            f"{_LOCK}:{agent_id}",
        )
        try:
            client = await self._get_client()
            await client.delete(key)
            return True
        except (redis.RedisError, OSError) as exc:
            logger.warning(
                "wm.lock.release_failed agent=%s error=%s",
                agent_id,
                exc,
            )
            return False

    # ------------------------------------------------------------------
    # Bulk cleanup — flush all working memory for a deliberation
    # ------------------------------------------------------------------

    async def flush_deliberation(
        self,
        board_id: UUID,
        deliberation_id: UUID,
    ) -> int:
        """Delete all working memory keys for a deliberation.

        Called when a deliberation concludes or is abandoned to free
        Redis memory promptly rather than waiting for TTL expiry.

        Returns the number of keys deleted.
        """
        pattern = _board_delib_key(board_id, deliberation_id, "*")
        try:
            client = await self._get_client()
            keys: list[bytes] = []
            async for key in client.scan_iter(match=pattern, count=100):
                keys.append(key)
            if keys:
                deleted = await client.delete(*keys)
                logger.info(
                    "wm.flush board=%s delib=%s keys_deleted=%d",
                    board_id,
                    deliberation_id,
                    deleted,
                )
                return int(deleted)
            return 0
        except (redis.RedisError, OSError) as exc:
            logger.warning(
                "wm.flush_failed board=%s delib=%s error=%s",
                board_id,
                deliberation_id,
                exc,
            )
            return 0

    async def flush_board(
        self,
        board_id: UUID,
    ) -> int:
        """Delete all working memory keys for an entire board.

        Useful during board deletion or bulk cleanup.
        """
        pattern = _key(str(board_id), "*")
        try:
            client = await self._get_client()
            keys: list[bytes] = []
            async for key in client.scan_iter(match=pattern, count=200):
                keys.append(key)
            if keys:
                deleted = await client.delete(*keys)
                logger.info(
                    "wm.flush_board board=%s keys_deleted=%d",
                    board_id,
                    deleted,
                )
                return int(deleted)
            return 0
        except (redis.RedisError, OSError) as exc:
            logger.warning(
                "wm.flush_board_failed board=%s error=%s",
                board_id,
                exc,
            )
            return 0

    # ------------------------------------------------------------------
    # Health / diagnostics
    # ------------------------------------------------------------------

    async def ping(self) -> bool:
        """Check whether the Redis backend is reachable."""
        try:
            client = await self._get_client()
            return await client.ping()
        except (redis.RedisError, OSError):
            return False

    async def active_deliberation_count(self, board_id: UUID) -> int:
        """Estimate the number of active deliberations for a board.

        Counts distinct ``ctx`` keys under the board namespace.
        """
        pattern = _key(str(board_id), "*", _CTX)
        try:
            client = await self._get_client()
            count = 0
            async for _key_name in client.scan_iter(match=pattern, count=200):
                count += 1
            return count
        except (redis.RedisError, OSError) as exc:
            logger.warning(
                "wm.active_count_failed board=%s error=%s",
                board_id,
                exc,
            )
            return 0


# ---------------------------------------------------------------------------
# Module-level singleton following MC service conventions
# ---------------------------------------------------------------------------

working_memory: WorkingMemoryService = WorkingMemoryService()
