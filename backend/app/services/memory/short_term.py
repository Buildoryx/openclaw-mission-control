"""Short-term memory service — Postgres-backed agent message store.

Short-term memory persists inter-agent messages and communication traces in the
``agent_messages`` table.  It provides:

- Message persistence with board/agent scoping
- Thread-based message retrieval via ``correlation_id``
- Sliding-window context retrieval for agent prompt assembly
- Retention-based cleanup (configurable via ``deliberation_entry_retention_days``)

This layer sits between ephemeral working memory (Redis) and long-term semantic
memory (pgvector), providing durable but time-bounded storage for agent
communication history.
"""

from __future__ import annotations

import logging
from datetime import timedelta
from typing import TYPE_CHECKING
from uuid import UUID, uuid4

from sqlalchemy import delete, func, select
from sqlmodel import col

from app.core.config import settings
from app.core.time import utcnow
from app.models.agent_message import AgentMessage

if TYPE_CHECKING:
    from sqlmodel.ext.asyncio.session import AsyncSession

logger = logging.getLogger(__name__)

# Default context window size when fetching recent messages for an agent.
DEFAULT_CONTEXT_WINDOW = 50

# Hard cap on messages returned in any single query to prevent OOM.
MAX_CONTEXT_WINDOW = 500


class ShortTermMemoryError(Exception):
    """Raised when a short-term memory operation fails."""


class ShortTermMemory:
    """Postgres-backed agent message store with retention policies.

    Each message is scoped to a board and agent, with optional threading
    via ``correlation_id`` and ``parent_message_id``.  Messages are stored
    durably and can be queried by time range, thread, or agent.
    """

    # ------------------------------------------------------------------
    # Write operations
    # ------------------------------------------------------------------

    async def store_message(
        self,
        session: AsyncSession,
        *,
        board_id: UUID,
        agent_id: UUID,
        message_type: str,
        payload: dict[str, object] | None = None,
        parent_message_id: UUID | None = None,
        correlation_id: UUID | None = None,
    ) -> AgentMessage:
        """Persist an agent message to the short-term store.

        Parameters
        ----------
        board_id:
            Board this message belongs to.
        agent_id:
            Agent that produced the message.
        message_type:
            Classification slug (e.g. ``"position"``, ``"evidence"``,
            ``"question"``, ``"ack"``, ``"system"``).
        payload:
            Arbitrary structured data carried by the message.
        parent_message_id:
            Optional parent for threaded replies.
        correlation_id:
            Optional correlation identifier for grouping related messages
            across agents (e.g. a shared deliberation round).
        """
        message = AgentMessage(
            board_id=board_id,
            agent_id=agent_id,
            message_type=message_type,
            payload=payload,
            parent_message_id=parent_message_id,
            correlation_id=correlation_id or uuid4(),
        )
        session.add(message)
        await session.flush()

        logger.debug(
            "short_term.store board=%s agent=%s type=%s correlation=%s",
            board_id,
            agent_id,
            message_type,
            message.correlation_id,
        )
        return message

    async def store_batch(
        self,
        session: AsyncSession,
        *,
        messages: list[dict[str, object]],
    ) -> list[AgentMessage]:
        """Persist multiple agent messages in a single flush.

        Each dict in *messages* must contain at minimum ``board_id``,
        ``agent_id``, ``message_type``.  Optional keys: ``payload``,
        ``parent_message_id``, ``correlation_id``.
        """
        if not messages:
            return []

        created: list[AgentMessage] = []
        shared_correlation = uuid4()

        for raw in messages:
            msg = AgentMessage(
                board_id=raw["board_id"],  # type: ignore[arg-type]
                agent_id=raw["agent_id"],  # type: ignore[arg-type]
                message_type=str(raw["message_type"]),
                payload=raw.get("payload"),  # type: ignore[arg-type]
                parent_message_id=raw.get("parent_message_id"),  # type: ignore[arg-type]
                correlation_id=raw.get("correlation_id") or shared_correlation,  # type: ignore[arg-type]
            )
            session.add(msg)
            created.append(msg)

        await session.flush()

        logger.debug(
            "short_term.store_batch count=%d correlation=%s",
            len(created),
            shared_correlation,
        )
        return created

    # ------------------------------------------------------------------
    # Read operations
    # ------------------------------------------------------------------

    async def get_recent_messages(
        self,
        session: AsyncSession,
        *,
        board_id: UUID,
        limit: int = DEFAULT_CONTEXT_WINDOW,
        agent_id: UUID | None = None,
        message_type: str | None = None,
        since_minutes: int | None = None,
    ) -> list[AgentMessage]:
        """Retrieve the most recent messages for a board.

        Results are ordered newest-first.  Pass *agent_id* to scope to a
        single agent's messages.  Pass *message_type* to filter by type.
        Pass *since_minutes* to restrict to a rolling time window.
        """
        effective_limit = min(max(1, limit), MAX_CONTEXT_WINDOW)

        qs = AgentMessage.objects.filter_by(board_id=board_id)

        if agent_id is not None:
            qs = qs.filter(col(AgentMessage.agent_id) == agent_id)
        if message_type is not None:
            qs = qs.filter(col(AgentMessage.message_type) == message_type)
        if since_minutes is not None and since_minutes > 0:
            cutoff = utcnow() - timedelta(minutes=since_minutes)
            qs = qs.filter(col(AgentMessage.created_at) >= cutoff)

        qs = qs.order_by(col(AgentMessage.created_at).desc())

        results = await qs.all(session)
        return list(results[:effective_limit])

    async def get_thread(
        self,
        session: AsyncSession,
        *,
        correlation_id: UUID,
        board_id: UUID | None = None,
    ) -> list[AgentMessage]:
        """Retrieve all messages in a correlation thread, ordered chronologically.

        Optionally scope to a specific board for multi-board safety.
        """
        qs = AgentMessage.objects.filter_by(correlation_id=correlation_id)
        if board_id is not None:
            qs = qs.filter(col(AgentMessage.board_id) == board_id)

        qs = qs.order_by(col(AgentMessage.created_at).asc())
        results = await qs.all(session)
        return list(results)

    async def get_agent_context_window(
        self,
        session: AsyncSession,
        *,
        board_id: UUID,
        agent_id: UUID,
        window_size: int = DEFAULT_CONTEXT_WINDOW,
        include_other_agents: bool = True,
    ) -> list[AgentMessage]:
        """Build a context window of recent messages for an agent's prompt.

        When *include_other_agents* is ``True`` (default), the window contains
        messages from all agents on the board — not just the requesting agent —
        giving the agent visibility into the broader conversation.

        Results are returned in chronological order (oldest first) so they can
        be directly appended to a prompt.
        """
        effective_limit = min(max(1, window_size), MAX_CONTEXT_WINDOW)

        if include_other_agents:
            qs = AgentMessage.objects.filter_by(board_id=board_id)
        else:
            qs = AgentMessage.objects.filter_by(
                board_id=board_id,
                agent_id=agent_id,
            )

        qs = qs.order_by(col(AgentMessage.created_at).desc())
        results = await qs.all(session)
        # Take the most recent N, then reverse to chronological order.
        window = list(results[:effective_limit])
        window.reverse()
        return window

    async def get_message_by_id(
        self,
        session: AsyncSession,
        *,
        message_id: UUID,
        board_id: UUID | None = None,
    ) -> AgentMessage | None:
        """Fetch a single message by ID, optionally scoped to a board."""
        qs = AgentMessage.objects.by_id(message_id)
        if board_id is not None:
            qs = qs.filter(col(AgentMessage.board_id) == board_id)
        return await qs.first(session)

    async def get_replies(
        self,
        session: AsyncSession,
        *,
        parent_message_id: UUID,
    ) -> list[AgentMessage]:
        """Retrieve all direct replies to a message."""
        qs = AgentMessage.objects.filter_by(
            parent_message_id=parent_message_id
        ).order_by(col(AgentMessage.created_at).asc())
        results = await qs.all(session)
        return list(results)

    # ------------------------------------------------------------------
    # Aggregation helpers
    # ------------------------------------------------------------------

    async def count_messages(
        self,
        session: AsyncSession,
        *,
        board_id: UUID,
        agent_id: UUID | None = None,
        since_minutes: int | None = None,
    ) -> int:
        """Return the count of messages matching the given filters."""
        stmt = (
            select(func.count())
            .select_from(AgentMessage)
            .where(col(AgentMessage.board_id) == board_id)
        )
        if agent_id is not None:
            stmt = stmt.where(col(AgentMessage.agent_id) == agent_id)
        if since_minutes is not None and since_minutes > 0:
            cutoff = utcnow() - timedelta(minutes=since_minutes)
            stmt = stmt.where(col(AgentMessage.created_at) >= cutoff)

        result = await session.exec(stmt)  # type: ignore[call-overload]
        row = result.one()
        return int(row) if row is not None else 0

    async def get_active_agents(
        self,
        session: AsyncSession,
        *,
        board_id: UUID,
        since_minutes: int = 30,
    ) -> list[UUID]:
        """Return agent IDs that have sent messages within the time window."""
        cutoff = utcnow() - timedelta(minutes=since_minutes)
        stmt = (
            select(AgentMessage.agent_id)
            .where(col(AgentMessage.board_id) == board_id)
            .where(col(AgentMessage.created_at) >= cutoff)
            .group_by(AgentMessage.agent_id)
            .order_by(func.max(col(AgentMessage.created_at)).desc())
        )
        result = await session.exec(stmt)  # type: ignore[call-overload]
        rows = result.all()
        return [row for row in rows if row is not None]  # type: ignore[misc]

    # ------------------------------------------------------------------
    # Retention / cleanup
    # ------------------------------------------------------------------

    async def purge_expired(
        self,
        session: AsyncSession,
        *,
        board_id: UUID | None = None,
        retention_days: int | None = None,
    ) -> int:
        """Delete messages older than the retention window.

        Parameters
        ----------
        board_id:
            If provided, only purge messages for this board.
            If ``None``, purge across all boards.
        retention_days:
            Override the default retention period from settings.

        Returns
        -------
        int
            Number of rows deleted.
        """
        days = (
            retention_days
            if retention_days is not None
            else settings.deliberation_entry_retention_days
        )
        cutoff = utcnow() - timedelta(days=days)

        stmt = delete(AgentMessage).where(
            col(AgentMessage.created_at) < cutoff,
        )
        if board_id is not None:
            stmt = stmt.where(col(AgentMessage.board_id) == board_id)

        result = await session.execute(stmt)
        deleted = result.rowcount or 0

        if deleted > 0:
            await session.commit()
            logger.info(
                "short_term.purge_expired board=%s days=%d deleted=%d",
                board_id or "all",
                days,
                deleted,
            )

        return deleted

    async def purge_board(
        self,
        session: AsyncSession,
        *,
        board_id: UUID,
    ) -> int:
        """Delete all messages for a board (used on board deletion)."""
        stmt = delete(AgentMessage).where(
            col(AgentMessage.board_id) == board_id,
        )
        result = await session.execute(stmt)
        deleted = result.rowcount or 0

        if deleted > 0:
            await session.commit()
            logger.info(
                "short_term.purge_board board=%s deleted=%d",
                board_id,
                deleted,
            )

        return deleted


# Module-level singleton following MC convention.
short_term_memory = ShortTermMemory()
