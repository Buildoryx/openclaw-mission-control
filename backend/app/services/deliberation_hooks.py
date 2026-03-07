"""Deliberation lifecycle hooks — integration wiring layer.

This module connects the deliberation engine to the rest of Mission Control:

- **Message bus publishing** — emits events to Redis Streams on every
  deliberation lifecycle transition so SSE subscribers and background
  consumers receive real-time updates.
- **Working memory cache** — keeps the Redis ephemeral layer in sync with
  deliberation state changes (context blobs, phase cache, recent entries).
- **Episodic extraction** — triggers pattern learning when a deliberation
  reaches a terminal state (concluded / abandoned).
- **Approval flow** — creates an approval request when the board policy
  requires synthesis review before promotion.
- **Task-triggered deliberation** — auto-starts a deliberation when a task
  enters the ``review`` status and the board policy has
  ``auto_deliberate_reviews`` enabled.
- **Board cleanup** — flushes deliberation-related ephemeral state when a
  board or deliberation is removed.

Hooks are designed to be called from the API layer or the
:class:`~app.services.deliberation.DeliberationService` after the primary
database operation succeeds.  They are intentionally fire-and-forget for
non-critical side effects (bus publishing, cache updates) and only raise
for approval creation where the caller needs the result.

Usage from an API endpoint::

    from app.services.deliberation_hooks import deliberation_hooks

    # After starting a deliberation
    await deliberation_hooks.on_deliberation_started(
        session, board=board, deliberation=delib, actor=actor,
    )
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING
from uuid import UUID

from app.core.config import settings
from app.core.time import utcnow
from app.models.approvals import Approval
from app.models.deliberation import (
    Deliberation,
    DeliberationEntry,
    DeliberationSynthesis,
)
from app.services.activity_log import record_activity
from app.services.deliberation_policy import get_deliberation_policy
from app.services.memory.episodic import (
    EpisodicExtractionService,
    build_extraction_task_payload,
)
from app.services.memory.message_bus import BusEvent, MessageBus, message_bus
from app.services.memory.working_memory import WorkingMemoryService, working_memory
from app.services.queue import QueuedTask, enqueue_task

if TYPE_CHECKING:
    from sqlmodel.ext.asyncio.session import AsyncSession

    from app.api.deps import ActorContext
    from app.models.boards import Board

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Hook orchestrator
# ---------------------------------------------------------------------------


class DeliberationHooks:
    """Centralised integration hooks for deliberation lifecycle events.

    Each ``on_*`` method corresponds to a deliberation lifecycle transition
    and triggers the appropriate side effects.  Methods are async and
    designed to be called after the primary DB commit succeeds.
    """

    def __init__(
        self,
        bus: MessageBus | None = None,
        wm: WorkingMemoryService | None = None,
        extractor: EpisodicExtractionService | None = None,
    ) -> None:
        self._bus = bus or message_bus
        self._wm = wm or working_memory
        self._extractor = extractor or EpisodicExtractionService()

    # ------------------------------------------------------------------
    # Deliberation started
    # ------------------------------------------------------------------

    async def on_deliberation_started(
        self,
        session: AsyncSession,
        *,
        board: Board,
        deliberation: Deliberation,
        actor: ActorContext,
    ) -> None:
        """Called after a new deliberation is created and committed."""
        agent_id = _actor_agent_id(actor)

        # 1. Publish bus event
        await self._publish(
            event_type="deliberation.started",
            board_id=board.id,
            agent_id=agent_id,
            payload={
                "deliberation_id": str(deliberation.id),
                "topic": deliberation.topic,
                "status": deliberation.status,
                "trigger_reason": deliberation.trigger_reason,
                "task_id": _str_or_none(deliberation.task_id),
                "max_turns": deliberation.max_turns,
                "initiated_by_agent_id": _str_or_none(
                    deliberation.initiated_by_agent_id
                ),
            },
        )

        # 2. Seed working memory context
        policy = get_deliberation_policy(board)
        await self._wm.set_context(
            board.id,
            deliberation.id,
            {
                "topic": deliberation.topic,
                "status": deliberation.status,
                "participants": [],
                "entry_count": 0,
                "max_turns": deliberation.max_turns,
                "policy": {
                    "max_debate_turns": policy.max_debate_turns,
                    "max_discussion_turns": policy.max_discussion_turns,
                    "max_total_turns": policy.max_total_turns,
                    "auto_promote_to_memory": policy.auto_promote_to_memory,
                    "require_synthesis_approval": policy.require_synthesis_approval,
                },
            },
        )
        await self._wm.set_phase(board.id, deliberation.id, deliberation.status)

        logger.info(
            "hooks.deliberation_started delib=%s board=%s",
            deliberation.id,
            board.id,
        )

    # ------------------------------------------------------------------
    # Entry added
    # ------------------------------------------------------------------

    async def on_entry_added(
        self,
        session: AsyncSession,
        *,
        board: Board,
        deliberation: Deliberation,
        entry: DeliberationEntry,
        actor: ActorContext,
    ) -> None:
        """Called after a new entry is committed to a deliberation."""
        agent_id = _actor_agent_id(actor)

        # 1. Publish bus event
        await self._publish(
            event_type="deliberation.entry_added",
            board_id=board.id,
            agent_id=agent_id,
            payload={
                "deliberation_id": str(deliberation.id),
                "entry_id": str(entry.id),
                "sequence": entry.sequence,
                "phase": entry.phase,
                "entry_type": entry.entry_type,
                "agent_id": _str_or_none(entry.agent_id),
                "user_id": _str_or_none(entry.user_id),
                "position": entry.position,
                "confidence": entry.confidence,
                "content_preview": (entry.content or "")[:200],
            },
        )

        # 2. Push to working memory recent entries
        await self._wm.push_entry(
            board.id,
            deliberation.id,
            {
                "entry_id": str(entry.id),
                "sequence": entry.sequence,
                "phase": entry.phase,
                "entry_type": entry.entry_type,
                "agent_id": _str_or_none(entry.agent_id),
                "position": entry.position,
                "confidence": entry.confidence,
            },
        )

        # 3. Update cached phase if the deliberation status changed
        await self._wm.set_phase(board.id, deliberation.id, deliberation.status)

        # 4. Update context with new participant and entry count
        ctx = await self._wm.get_context(board.id, deliberation.id)
        if ctx is not None:
            participants: list[str] = ctx.get("participants", [])
            entry_agent = _str_or_none(entry.agent_id)
            if entry_agent and entry_agent not in participants:
                participants.append(entry_agent)
            ctx["participants"] = participants
            ctx["entry_count"] = entry.sequence
            ctx["status"] = deliberation.status
            await self._wm.set_context(board.id, deliberation.id, ctx)

        logger.debug(
            "hooks.entry_added delib=%s entry=%s seq=%d",
            deliberation.id,
            entry.id,
            entry.sequence,
        )

    # ------------------------------------------------------------------
    # Phase advanced
    # ------------------------------------------------------------------

    async def on_phase_advanced(
        self,
        session: AsyncSession,
        *,
        board: Board,
        deliberation: Deliberation,
        previous_status: str,
        actor: ActorContext,
    ) -> None:
        """Called after a deliberation phase is advanced."""
        agent_id = _actor_agent_id(actor)

        # 1. Publish bus event
        await self._publish(
            event_type="deliberation.phase_advanced",
            board_id=board.id,
            agent_id=agent_id,
            payload={
                "deliberation_id": str(deliberation.id),
                "previous_status": previous_status,
                "new_status": deliberation.status,
                "topic": deliberation.topic,
            },
        )

        # 2. Update working memory phase cache
        await self._wm.set_phase(board.id, deliberation.id, deliberation.status)

        # 3. Update context blob
        ctx = await self._wm.get_context(board.id, deliberation.id)
        if ctx is not None:
            ctx["status"] = deliberation.status
            await self._wm.set_context(board.id, deliberation.id, ctx)

        logger.info(
            "hooks.phase_advanced delib=%s %s → %s",
            deliberation.id,
            previous_status,
            deliberation.status,
        )

    # ------------------------------------------------------------------
    # Synthesis submitted
    # ------------------------------------------------------------------

    async def on_synthesis_submitted(
        self,
        session: AsyncSession,
        *,
        board: Board,
        deliberation: Deliberation,
        synthesis: DeliberationSynthesis,
        actor: ActorContext,
    ) -> Approval | None:
        """Called after a synthesis is submitted and the deliberation concluded.

        Returns an :class:`Approval` if the board policy requires synthesis
        review; otherwise returns ``None``.
        """
        agent_id = _actor_agent_id(actor)

        # 1. Publish bus event
        await self._publish(
            event_type="deliberation.synthesis_submitted",
            board_id=board.id,
            agent_id=agent_id,
            payload={
                "deliberation_id": str(deliberation.id),
                "synthesis_id": str(synthesis.id),
                "consensus_level": synthesis.consensus_level,
                "confidence": synthesis.confidence,
                "promoted_to_memory": synthesis.promoted_to_memory,
            },
        )

        # 2. Publish concluded event
        await self._publish(
            event_type="deliberation.concluded",
            board_id=board.id,
            agent_id=agent_id,
            payload={
                "deliberation_id": str(deliberation.id),
                "topic": deliberation.topic,
                "status": deliberation.status,
                "consensus_level": synthesis.consensus_level,
                "duration_ms": deliberation.duration_ms,
            },
        )

        # 3. Flush working memory (deliberation is terminal)
        await self._wm.flush_deliberation(board.id, deliberation.id)

        # 4. Trigger episodic extraction (async via queue or inline)
        await self._trigger_episodic_extraction(
            session,
            deliberation_id=deliberation.id,
            board_id=board.id,
        )

        # 5. Create approval if policy requires review before promotion
        approval: Approval | None = None
        policy = get_deliberation_policy(board)
        if policy.require_synthesis_approval and not synthesis.promoted_to_memory:
            approval = await self._create_synthesis_approval(
                session,
                board=board,
                deliberation=deliberation,
                synthesis=synthesis,
                agent_id=agent_id,
            )

        # 6. Publish memory promotion event if synthesis was auto-promoted
        if synthesis.promoted_to_memory:
            await self._publish(
                event_type="memory.promoted",
                board_id=board.id,
                agent_id=agent_id,
                payload={
                    "deliberation_id": str(deliberation.id),
                    "synthesis_id": str(synthesis.id),
                    "board_memory_id": _str_or_none(synthesis.board_memory_id),
                    "topic": deliberation.topic,
                },
            )

        logger.info(
            "hooks.synthesis_submitted delib=%s consensus=%s approval=%s",
            deliberation.id,
            synthesis.consensus_level,
            approval.id if approval else "none",
        )
        return approval

    # ------------------------------------------------------------------
    # Abandoned
    # ------------------------------------------------------------------

    async def on_deliberation_abandoned(
        self,
        session: AsyncSession,
        *,
        board: Board,
        deliberation: Deliberation,
        reason: str | None,
        actor: ActorContext,
    ) -> None:
        """Called after a deliberation is abandoned."""
        agent_id = _actor_agent_id(actor)

        # 1. Publish bus event
        await self._publish(
            event_type="deliberation.abandoned",
            board_id=board.id,
            agent_id=agent_id,
            payload={
                "deliberation_id": str(deliberation.id),
                "topic": deliberation.topic,
                "reason": reason,
            },
        )

        # 2. Flush working memory
        await self._wm.flush_deliberation(board.id, deliberation.id)

        # 3. Still trigger episodic extraction (captures abandoned patterns)
        await self._trigger_episodic_extraction(
            session,
            deliberation_id=deliberation.id,
            board_id=board.id,
        )

        logger.info(
            "hooks.deliberation_abandoned delib=%s reason=%s",
            deliberation.id,
            reason,
        )

    # ------------------------------------------------------------------
    # Memory promotion (manual)
    # ------------------------------------------------------------------

    async def on_synthesis_promoted(
        self,
        session: AsyncSession,
        *,
        board: Board,
        deliberation: Deliberation,
        synthesis: DeliberationSynthesis,
        actor: ActorContext,
    ) -> None:
        """Called after a synthesis is manually promoted to board memory."""
        agent_id = _actor_agent_id(actor)

        await self._publish(
            event_type="memory.promoted",
            board_id=board.id,
            agent_id=agent_id,
            payload={
                "deliberation_id": str(deliberation.id),
                "synthesis_id": str(synthesis.id),
                "board_memory_id": _str_or_none(synthesis.board_memory_id),
                "topic": deliberation.topic,
            },
        )

        logger.info(
            "hooks.synthesis_promoted delib=%s memory=%s",
            deliberation.id,
            synthesis.board_memory_id,
        )

    # ------------------------------------------------------------------
    # Task-triggered deliberation
    # ------------------------------------------------------------------

    async def maybe_trigger_review_deliberation(
        self,
        session: AsyncSession,
        *,
        board: Board,
        task_id: UUID,
        task_title: str,
        actor: ActorContext,
    ) -> Deliberation | None:
        """Auto-start a deliberation when a task enters review status.

        Only triggers if the board policy has ``auto_deliberate_reviews``
        enabled.  Returns the created :class:`Deliberation` or ``None``
        if the policy disables auto-deliberation.
        """
        policy = get_deliberation_policy(board)
        if not policy.auto_deliberate_reviews:
            return None

        # Import inline to avoid circular dependency
        from app.services.deliberation import DeliberationService

        svc = DeliberationService()

        topic = f"Review: {task_title}"
        try:
            deliberation = await svc.start_deliberation(
                session,
                board=board,
                topic=topic,
                actor=actor,
                trigger_reason="auto_review_deliberation",
                task_id=task_id,
            )
        except Exception:
            logger.exception(
                "hooks.review_deliberation_failed board=%s task=%s",
                board.id,
                task_id,
            )
            return None

        # Fire the started hook
        await self.on_deliberation_started(
            session,
            board=board,
            deliberation=deliberation,
            actor=actor,
        )

        record_activity(
            session,
            event_type="deliberation.auto_triggered",
            message=f"Auto-triggered review deliberation: {topic}",
            board_id=board.id,
            task_id=task_id,
            agent_id=_actor_agent_id(actor),
        )
        await session.commit()

        logger.info(
            "hooks.review_deliberation_triggered board=%s task=%s delib=%s",
            board.id,
            task_id,
            deliberation.id,
        )
        return deliberation

    # ------------------------------------------------------------------
    # Board / deliberation cleanup
    # ------------------------------------------------------------------

    async def on_board_deleting(
        self,
        board_id: UUID,
    ) -> None:
        """Flush all deliberation working memory for a board being deleted.

        Called from board deletion flow before DB records are removed.
        """
        deleted = await self._wm.flush_board(board_id)
        logger.info(
            "hooks.board_cleanup board=%s wm_keys_deleted=%d",
            board_id,
            deleted,
        )

    async def on_deliberation_cleanup(
        self,
        board_id: UUID,
        deliberation_id: UUID,
    ) -> None:
        """Flush working memory for a single deliberation.

        Called when a deliberation reaches a terminal state or is manually
        cleaned up.
        """
        await self._wm.flush_deliberation(board_id, deliberation_id)

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    async def _publish(
        self,
        *,
        event_type: str,
        board_id: UUID,
        agent_id: UUID | None = None,
        payload: dict[str, object],
        correlation_id: UUID | None = None,
    ) -> None:
        """Publish a bus event, logging but not raising on failure."""
        try:
            event = BusEvent(
                event_type=event_type,
                board_id=board_id,
                payload=payload,
                agent_id=agent_id,
                correlation_id=correlation_id,
            )
            await self._bus.publish(event)
        except Exception:
            logger.warning(
                "hooks.publish_failed type=%s board=%s",
                event_type,
                board_id,
                exc_info=True,
            )

    async def _trigger_episodic_extraction(
        self,
        session: AsyncSession,
        *,
        deliberation_id: UUID,
        board_id: UUID,
    ) -> None:
        """Trigger episodic pattern extraction, preferring async queue.

        Falls back to inline extraction if queuing fails.
        """
        task_payload = build_extraction_task_payload(deliberation_id, board_id)

        # Try to enqueue for background processing
        queued = False
        try:
            task = QueuedTask(
                task_type="episodic_extraction",
                payload=task_payload,
                created_at=utcnow(),
            )
            queued = enqueue_task(task, settings.rq_queue_name)
        except Exception:
            logger.warning(
                "hooks.episodic_queue_failed delib=%s — falling back to inline",
                deliberation_id,
                exc_info=True,
            )

        if queued:
            logger.debug(
                "hooks.episodic_extraction_queued delib=%s",
                deliberation_id,
            )
            return

        # Fallback: run extraction inline
        try:
            patterns = await self._extractor.extract_patterns(
                session, deliberation_id, board_id
            )
            logger.info(
                "hooks.episodic_extraction_inline delib=%s patterns=%d",
                deliberation_id,
                len(patterns),
            )
        except Exception:
            logger.exception(
                "hooks.episodic_extraction_failed delib=%s",
                deliberation_id,
            )

    async def _create_synthesis_approval(
        self,
        session: AsyncSession,
        *,
        board: Board,
        deliberation: Deliberation,
        synthesis: DeliberationSynthesis,
        agent_id: UUID | None,
    ) -> Approval:
        """Create an approval request for a synthesis that requires review.

        The approval links back to the deliberation via
        ``deliberation.approval_id`` so the approval resolution flow can
        trigger memory promotion.
        """
        approval = Approval(
            board_id=board.id,
            task_id=deliberation.task_id,
            agent_id=agent_id,
            action_type="synthesis_review",
            payload={
                "deliberation_id": str(deliberation.id),
                "synthesis_id": str(synthesis.id),
                "topic": deliberation.topic,
                "consensus_level": synthesis.consensus_level,
                "confidence": synthesis.confidence,
                "content_preview": (synthesis.content or "")[:500],
                "key_points": synthesis.key_points or [],
                "reason": (
                    f"Synthesis for deliberation '{deliberation.topic}' "
                    f"requires review ({synthesis.consensus_level} consensus, "
                    f"confidence {synthesis.confidence:.0%})."
                ),
            },
            confidence=synthesis.confidence * 100,  # Approval uses 0-100 scale
            status="pending",
        )
        session.add(approval)
        await session.flush()

        # Link approval back to deliberation
        deliberation.approval_id = approval.id
        deliberation.updated_at = utcnow()
        session.add(deliberation)

        record_activity(
            session,
            event_type="approval.created",
            message=(
                f"Synthesis approval requested for deliberation: {deliberation.topic}"
            ),
            board_id=board.id,
            agent_id=agent_id,
        )

        await session.commit()
        await session.refresh(approval)

        logger.info(
            "hooks.synthesis_approval_created delib=%s approval=%s",
            deliberation.id,
            approval.id,
        )
        return approval


# ---------------------------------------------------------------------------
# Utility helpers
# ---------------------------------------------------------------------------


def _actor_agent_id(actor: ActorContext) -> UUID | None:
    """Extract the agent UUID from an actor context, if present."""
    if actor.actor_type == "agent" and actor.agent is not None:
        return actor.agent.id
    return None


def _str_or_none(value: UUID | None) -> str | None:
    """Convert a UUID to string, or return None."""
    return str(value) if value is not None else None


# ---------------------------------------------------------------------------
# Module-level singleton
# ---------------------------------------------------------------------------

deliberation_hooks = DeliberationHooks()
