"""Core deliberation engine — phase management, entry handling, and synthesis.

This service is the ported and generalized equivalent of the Index Mavens
``DebateEngine``.  It replaces trading-specific signal divergence detection
with a domain-agnostic deliberation lifecycle that progresses through:

    created → debating → discussing → verifying → synthesizing → concluded

Deliberations can be abandoned from any non-terminal phase.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING
from uuid import UUID

from sqlalchemy import func, select
from sqlmodel import col

from app.core.time import utcnow
from app.models.board_memory import BoardMemory
from app.models.deliberation import (
    CONSENSUS_LEVELS,
    ENTRY_TYPES,
    PHASE_ORDER,
    TERMINAL_STATUSES,
    Deliberation,
    DeliberationEntry,
    DeliberationSynthesis,
)
from app.services.activity_log import record_activity
from app.services.deliberation_policy import resolve_policy

if TYPE_CHECKING:
    from sqlmodel.ext.asyncio.session import AsyncSession

    from app.api.deps import ActorContext
    from app.models.boards import Board

logger = logging.getLogger(__name__)

# Phase aliases used by entries (shorter than status names)
_PHASE_ALIASES: dict[str, str] = {
    "debate": "debating",
    "discussion": "discussing",
    "verification": "verifying",
}

# Map from deliberation status to allowed entry phases
_STATUS_TO_ALLOWED_PHASES: dict[str, set[str]] = {
    "created": {"debate"},
    "debating": {"debate"},
    "discussing": {"discussion"},
    "verifying": {"verification"},
    "synthesizing": {"synthesis"},
}


def _phase_index(status: str) -> int | None:
    """Return the index of *status* in the phase order, or ``None``."""
    try:
        return PHASE_ORDER.index(status)
    except ValueError:
        return None


class DeliberationError(Exception):
    """Raised when a deliberation operation violates business rules."""

    status_code: int

    def __init__(self, message: str, *, status_code: int = 400) -> None:
        super().__init__(message)
        self.status_code = status_code


# ---------------------------------------------------------------------------
# Service
# ---------------------------------------------------------------------------


class DeliberationService:
    """Manages the full lifecycle of a deliberation."""

    # ----- Creation --------------------------------------------------------

    async def start_deliberation(
        self,
        session: AsyncSession,
        *,
        board: Board,
        topic: str,
        actor: ActorContext,
        trigger_reason: str | None = None,
        task_id: UUID | None = None,
        parent_deliberation_id: UUID | None = None,
        max_turns: int | None = None,
    ) -> Deliberation:
        """Start a new deliberation on a board."""
        policy = resolve_policy(board)

        effective_max_turns = max_turns or policy.max_total_turns

        agent_id: UUID | None = None
        if actor.actor_type == "agent" and actor.agent:
            agent_id = actor.agent.id

        deliberation = Deliberation(
            board_id=board.id,
            topic=topic.strip(),
            status="created",
            initiated_by_agent_id=agent_id,
            trigger_reason=trigger_reason,
            task_id=task_id,
            parent_deliberation_id=parent_deliberation_id,
            max_turns=effective_max_turns,
        )
        session.add(deliberation)
        await session.flush()

        record_activity(
            session,
            event_type="deliberation.started",
            message=f"Deliberation started: {deliberation.topic}",
            board_id=board.id,
            agent_id=agent_id,
        )
        await session.commit()
        await session.refresh(deliberation)

        logger.info(
            "deliberation.started id=%s board=%s topic=%s",
            deliberation.id,
            board.id,
            deliberation.topic,
        )
        return deliberation

    # ----- Entry submission ------------------------------------------------

    async def add_entry(
        self,
        session: AsyncSession,
        *,
        board: Board,
        deliberation: Deliberation,
        actor: ActorContext,
        content: str,
        phase: str,
        entry_type: str,
        position: str | None = None,
        confidence: float | None = None,
        parent_entry_id: UUID | None = None,
        references: list[str] | None = None,
        entry_metadata: dict[str, object] | None = None,
    ) -> DeliberationEntry:
        """Add an entry to a deliberation.

        Validates phase/type, auto-transitions the deliberation from
        ``created`` to ``debating`` on the first entry, and checks
        auto-advance rules.
        """
        # --- Validate status --------------------------------------------------
        if deliberation.status in TERMINAL_STATUSES:
            raise DeliberationError(
                f"Deliberation is {deliberation.status} and cannot accept entries.",
                status_code=409,
            )

        # --- Validate entry_type ---------------------------------------------
        if entry_type not in ENTRY_TYPES:
            raise DeliberationError(
                f"Invalid entry_type '{entry_type}'. "
                f"Allowed: {', '.join(sorted(ENTRY_TYPES))}",
            )

        # --- Validate phase --------------------------------------------------
        allowed_phases = _STATUS_TO_ALLOWED_PHASES.get(deliberation.status, set())
        if phase not in allowed_phases and phase != "synthesis":
            raise DeliberationError(
                f"Phase '{phase}' is not allowed in status '{deliberation.status}'. "
                f"Allowed phases: {', '.join(sorted(allowed_phases))}",
            )

        # --- Auto-transition created → debating on first entry ----------------
        if deliberation.status == "created":
            deliberation.status = "debating"
            deliberation.updated_at = utcnow()
            session.add(deliberation)

        # --- Compute sequence -------------------------------------------------
        current_max = await self._max_sequence(session, deliberation.id)
        next_sequence = current_max + 1

        # --- Check turn limit -------------------------------------------------
        if next_sequence > deliberation.max_turns:
            raise DeliberationError(
                f"Deliberation has reached the maximum of {deliberation.max_turns} turns.",
                status_code=409,
            )

        # --- Build entry ------------------------------------------------------
        agent_id: UUID | None = None
        user_id: UUID | None = None
        if actor.actor_type == "agent" and actor.agent:
            agent_id = actor.agent.id
        elif actor.user:
            user_id = actor.user.id

        entry = DeliberationEntry(
            deliberation_id=deliberation.id,
            sequence=next_sequence,
            phase=phase,
            entry_type=entry_type,
            agent_id=agent_id,
            user_id=user_id,
            position=position,
            confidence=confidence,
            content=content.strip(),
            parent_entry_id=parent_entry_id,
            references=references,
            metadata_=entry_metadata,
        )
        session.add(entry)
        await session.flush()

        record_activity(
            session,
            event_type="deliberation.entry_added",
            message=(
                f"Entry #{next_sequence} ({entry_type}) added to "
                f"deliberation: {deliberation.topic}"
            ),
            board_id=board.id,
            agent_id=agent_id,
        )

        # --- Auto-advance check ----------------------------------------------
        await self._maybe_auto_advance(session, deliberation, board)

        await session.commit()
        await session.refresh(entry)

        logger.info(
            "deliberation.entry_added id=%s delib=%s seq=%d type=%s",
            entry.id,
            deliberation.id,
            next_sequence,
            entry_type,
        )
        return entry

    # ----- Phase advancement -----------------------------------------------

    async def advance_phase(
        self,
        session: AsyncSession,
        *,
        board: Board,
        deliberation: Deliberation,
        actor: ActorContext,
        target_status: str | None = None,
    ) -> Deliberation:
        """Advance a deliberation to the next phase in the state machine.

        If *target_status* is given it must be the next valid phase; otherwise
        the service selects the natural successor.
        """
        if deliberation.status in TERMINAL_STATUSES:
            raise DeliberationError(
                f"Cannot advance: deliberation is already {deliberation.status}.",
                status_code=409,
            )

        current_index: int | None = _phase_index(deliberation.status)
        if current_index is None or current_index >= len(PHASE_ORDER) - 1:
            raise DeliberationError(
                f"Cannot advance from status '{deliberation.status}'.",
            )

        next_status: str = PHASE_ORDER[current_index + 1]

        if target_status is not None:
            if target_status != next_status:
                raise DeliberationError(
                    f"Cannot advance to '{target_status}' from '{deliberation.status}'. "
                    f"Next valid phase is '{next_status}'.",
                )

        deliberation.status = next_status
        deliberation.updated_at = utcnow()
        session.add(deliberation)

        agent_id: UUID | None = None
        if actor.actor_type == "agent" and actor.agent:
            agent_id = actor.agent.id

        record_activity(
            session,
            event_type="deliberation.phase_advanced",
            message=(
                f"Deliberation phase advanced to {next_status}: {deliberation.topic}"
            ),
            board_id=board.id,
            agent_id=agent_id,
        )

        await session.commit()
        await session.refresh(deliberation)

        logger.info(
            "deliberation.phase_advanced id=%s status=%s",
            deliberation.id,
            next_status,
        )
        return deliberation

    # ----- Abandon ---------------------------------------------------------

    async def abandon(
        self,
        session: AsyncSession,
        *,
        board: Board,
        deliberation: Deliberation,
        actor: ActorContext,
        reason: str | None = None,
    ) -> Deliberation:
        """Mark a deliberation as abandoned."""
        if deliberation.status in TERMINAL_STATUSES:
            raise DeliberationError(
                f"Cannot abandon: deliberation is already {deliberation.status}.",
                status_code=409,
            )

        deliberation.status = "abandoned"
        deliberation.concluded_at = utcnow()
        deliberation.updated_at = utcnow()
        session.add(deliberation)

        agent_id: UUID | None = None
        if actor.actor_type == "agent" and actor.agent:
            agent_id = actor.agent.id

        reason_suffix = f" — {reason}" if reason else ""
        record_activity(
            session,
            event_type="deliberation.abandoned",
            message=f"Deliberation abandoned: {deliberation.topic}{reason_suffix}",
            board_id=board.id,
            agent_id=agent_id,
        )

        await session.commit()
        await session.refresh(deliberation)

        logger.info("deliberation.abandoned id=%s", deliberation.id)
        return deliberation

    # ----- Synthesis -------------------------------------------------------

    async def submit_synthesis(
        self,
        session: AsyncSession,
        *,
        board: Board,
        deliberation: Deliberation,
        actor: ActorContext,
        content: str,
        consensus_level: str,
        confidence: float,
        key_points: list[str] | None = None,
        dissenting_views: list[str] | None = None,
        tags: list[str] | None = None,
    ) -> DeliberationSynthesis:
        """Submit the synthesis for a deliberation and conclude it."""
        if deliberation.status in TERMINAL_STATUSES:
            raise DeliberationError(
                f"Cannot synthesize: deliberation is already {deliberation.status}.",
                status_code=409,
            )
        if consensus_level not in CONSENSUS_LEVELS:
            raise DeliberationError(
                f"Invalid consensus_level '{consensus_level}'. "
                f"Allowed: {', '.join(sorted(CONSENSUS_LEVELS))}",
            )

        # Check for existing synthesis
        existing = await DeliberationSynthesis.objects.filter_by(
            deliberation_id=deliberation.id,
        ).first(session)
        if existing is not None:
            raise DeliberationError(
                "Deliberation already has a synthesis.",
                status_code=409,
            )

        agent_id: UUID | None = None
        if actor.actor_type == "agent" and actor.agent:
            agent_id = actor.agent.id

        synthesis = DeliberationSynthesis(
            deliberation_id=deliberation.id,
            synthesized_by_agent_id=agent_id,
            content=content.strip(),
            consensus_level=consensus_level,
            confidence=confidence,
            key_points=key_points,
            dissenting_views=dissenting_views,
            tags=tags,
        )
        session.add(synthesis)

        # Conclude the deliberation
        now = utcnow()
        deliberation.status = "concluded"
        deliberation.concluded_at = now
        deliberation.updated_at = now
        deliberation.synthesizer_agent_id = agent_id
        if deliberation.created_at:
            delta = now - deliberation.created_at
            deliberation.duration_ms = delta.total_seconds() * 1000
        session.add(deliberation)

        record_activity(
            session,
            event_type="deliberation.synthesis_submitted",
            message=f"Synthesis submitted for deliberation: {deliberation.topic}",
            board_id=board.id,
            agent_id=agent_id,
        )
        record_activity(
            session,
            event_type="deliberation.concluded",
            message=(
                f"Deliberation concluded ({consensus_level}): {deliberation.topic}"
            ),
            board_id=board.id,
            agent_id=agent_id,
        )

        # Auto-promote if policy allows
        policy = resolve_policy(board)
        if policy.auto_promote_to_memory:
            await self._promote_synthesis_to_memory(
                session, board=board, deliberation=deliberation, synthesis=synthesis
            )

        await session.commit()
        await session.refresh(synthesis)

        logger.info(
            "deliberation.synthesis_submitted id=%s delib=%s consensus=%s",
            synthesis.id,
            deliberation.id,
            consensus_level,
        )
        return synthesis

    # ----- Memory promotion ------------------------------------------------

    async def promote_synthesis(
        self,
        session: AsyncSession,
        *,
        board: Board,
        deliberation: Deliberation,
        actor: ActorContext,
    ) -> BoardMemory:
        """Manually promote a deliberation synthesis to board memory."""
        synthesis = await DeliberationSynthesis.objects.filter_by(
            deliberation_id=deliberation.id,
        ).first(session)
        if synthesis is None:
            raise DeliberationError(
                "Deliberation has no synthesis to promote.",
                status_code=404,
            )
        if synthesis.promoted_to_memory:
            raise DeliberationError(
                "Synthesis has already been promoted to memory.",
                status_code=409,
            )

        memory = await self._promote_synthesis_to_memory(
            session, board=board, deliberation=deliberation, synthesis=synthesis
        )

        agent_id: UUID | None = None
        if actor.actor_type == "agent" and actor.agent:
            agent_id = actor.agent.id

        record_activity(
            session,
            event_type="deliberation.synthesis_promoted",
            message=(f"Synthesis promoted to board memory: {deliberation.topic}"),
            board_id=board.id,
            agent_id=agent_id,
        )

        await session.commit()
        await session.refresh(memory)

        logger.info(
            "deliberation.synthesis_promoted delib=%s memory=%s",
            deliberation.id,
            memory.id,
        )
        return memory

    # ----- Reads -----------------------------------------------------------

    async def get_deliberation(
        self,
        session: AsyncSession,
        deliberation_id: UUID,
        board_id: UUID,
    ) -> Deliberation | None:
        """Fetch a single deliberation by ID, scoped to a board."""
        return await Deliberation.objects.filter_by(
            id=deliberation_id,
            board_id=board_id,
        ).first(session)

    async def get_entry_count(
        self,
        session: AsyncSession,
        deliberation_id: UUID,
    ) -> int:
        """Return the number of entries in a deliberation."""
        stmt = (
            select(func.count())
            .select_from(DeliberationEntry)
            .where(col(DeliberationEntry.deliberation_id) == deliberation_id)
        )
        result = await session.exec(stmt)  # type: ignore[call-overload]
        row = result.one()
        return int(row) if row is not None else 0

    async def has_synthesis(
        self,
        session: AsyncSession,
        deliberation_id: UUID,
    ) -> bool:
        """Check whether a deliberation has a synthesis."""
        synth = await DeliberationSynthesis.objects.filter_by(
            deliberation_id=deliberation_id,
        ).first(session)
        return synth is not None

    async def get_synthesis(
        self,
        session: AsyncSession,
        deliberation_id: UUID,
    ) -> DeliberationSynthesis | None:
        """Fetch the synthesis for a deliberation."""
        return await DeliberationSynthesis.objects.filter_by(
            deliberation_id=deliberation_id,
        ).first(session)

    async def get_deliberation_context(
        self,
        session: AsyncSession,
        deliberation_id: UUID,
    ) -> dict[str, object]:
        """Build a context payload for the synthesizer agent.

        Returns all entries and relevant metadata for producing a synthesis.
        """
        entries = await (
            DeliberationEntry.objects.filter_by(
                deliberation_id=deliberation_id,
            )
            .order_by(col(DeliberationEntry.sequence))
            .all(session)
        )
        return {
            "deliberation_id": str(deliberation_id),
            "entry_count": len(entries),
            "entries": [
                {
                    "sequence": e.sequence,
                    "phase": e.phase,
                    "entry_type": e.entry_type,
                    "agent_id": str(e.agent_id) if e.agent_id else None,
                    "user_id": str(e.user_id) if e.user_id else None,
                    "position": e.position,
                    "confidence": e.confidence,
                    "content": e.content,
                    "references": e.references,
                }
                for e in entries
            ],
        }

    # ----- Divergence detection --------------------------------------------

    async def detect_divergences(
        self,
        session: AsyncSession,
        *,
        board: Board,
        topic: str,
    ) -> bool:
        """Check whether agent positions on a topic are divergent enough
        to warrant a deliberation.

        Uses a simple confidence-gap heuristic: if any two agents'
        most-recent positions on the same topic differ by more than the
        configured threshold, divergence is detected.
        """
        policy = resolve_policy(board)
        if not policy.auto_trigger_on_divergence:
            return False

        # Look for recent entries across deliberations on the same board/topic
        delib_ids_subq = (
            select(col(Deliberation.id))
            .where(col(Deliberation.board_id) == board.id)
            .where(col(Deliberation.topic) == topic)
        )
        entries = await (
            DeliberationEntry.objects.filter(
                col(DeliberationEntry.deliberation_id).in_(delib_ids_subq),
                col(DeliberationEntry.confidence).isnot(None),
            )
            .order_by(col(DeliberationEntry.created_at).desc())
            .all(session)
        )

        # Group by agent and take most recent confidence per agent
        agent_confidences: dict[UUID, float] = {}
        for entry in entries:
            if entry.agent_id and entry.confidence is not None:
                if entry.agent_id not in agent_confidences:
                    agent_confidences[entry.agent_id] = entry.confidence

        # Check pairwise confidence gaps
        values = list(agent_confidences.values())
        for i in range(len(values)):
            for j in range(i + 1, len(values)):
                gap = abs(values[i] - values[j])
                if gap >= policy.divergence_confidence_gap:
                    return True

        return False

    # ----- Internal helpers ------------------------------------------------

    async def _max_sequence(
        self,
        session: AsyncSession,
        deliberation_id: UUID,
    ) -> int:
        """Return the current maximum sequence number for a deliberation."""
        stmt = select(
            func.coalesce(func.max(col(DeliberationEntry.sequence)), 0)
        ).where(col(DeliberationEntry.deliberation_id) == deliberation_id)
        result = await session.exec(stmt)  # type: ignore[call-overload]
        row = result.one()
        return int(row) if row is not None else 0

    async def _maybe_auto_advance(
        self,
        session: AsyncSession,
        deliberation: Deliberation,
        board: Board,
    ) -> None:
        """Check if auto-advance rules trigger a phase transition."""
        policy = resolve_policy(board)
        entry_count = await self._max_sequence(session, deliberation.id)

        if deliberation.status == "debating" and entry_count >= policy.max_debate_turns:
            deliberation.status = "discussing"
            deliberation.updated_at = utcnow()
            session.add(deliberation)
            logger.info(
                "deliberation.auto_advance id=%s → discussing (after %d debate turns)",
                deliberation.id,
                entry_count,
            )

        elif deliberation.status == "discussing" and entry_count >= (
            policy.max_debate_turns + policy.max_discussion_turns
        ):
            deliberation.status = "verifying"
            deliberation.updated_at = utcnow()
            session.add(deliberation)
            logger.info(
                "deliberation.auto_advance id=%s → verifying",
                deliberation.id,
            )

    async def _promote_synthesis_to_memory(
        self,
        session: AsyncSession,
        *,
        board: Board,
        deliberation: Deliberation,
        synthesis: DeliberationSynthesis,
    ) -> BoardMemory:
        """Copy synthesis content into BoardMemory for long-term recall."""
        memory_tags = [
            "synthesis",
            f"deliberation:{deliberation.id}",
            f"topic:{deliberation.topic}",
            f"consensus:{synthesis.consensus_level}",
            *(synthesis.tags or []),
        ]
        memory = BoardMemory(
            board_id=board.id,
            content=synthesis.content,
            tags=memory_tags,
            source=f"deliberation:{deliberation.id}",
            is_chat=False,
        )
        session.add(memory)
        await session.flush()

        synthesis.promoted_to_memory = True
        synthesis.board_memory_id = memory.id
        session.add(synthesis)

        return memory


# Module-level singleton following MC convention.
deliberation_service = DeliberationService()
