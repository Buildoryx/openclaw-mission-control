"""Episodic memory extraction service for pattern learning from deliberations.

This module implements Phase 4 of the deliberation integration plan: extracting
recurring patterns, agent accuracy profiles, and topic-level insights from
concluded deliberations.  These learned patterns are stored as
:class:`~app.models.episodic_memory.EpisodicMemory` rows and can be queried
via the episodic-memory API endpoints or used by agents for contextual recall.

Pattern types extracted:

- ``deliberation_outcome`` — Records the overall outcome of a deliberation
  (consensus level, confidence delta, duration, participant count).
- ``consensus_pattern`` — Captures how consensus was reached on a topic
  (which phases contributed most, dissenting view count, etc.).
- ``agent_accuracy`` — Per-agent track record: how often their initial
  position aligned with the final synthesis.
- ``topic_pattern`` — Recurring topic themes and their typical outcomes.

The extraction pipeline is designed to run asynchronously after a deliberation
concludes — either inline (via ``extract_patterns``) or queued through the
RQ worker (via ``enqueue_extraction``).
"""

from __future__ import annotations

import logging
from collections import defaultdict
from typing import TYPE_CHECKING
from uuid import UUID

from sqlmodel import col

from app.core.config import settings
from app.core.time import utcnow
from app.models.deliberation import (
    Deliberation,
    DeliberationEntry,
    DeliberationSynthesis,
)
from app.models.episodic_memory import EpisodicMemory
from app.services.memory.embedding import embedding_service

if TYPE_CHECKING:
    from sqlmodel.ext.asyncio.session import AsyncSession

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Pattern type constants
# ---------------------------------------------------------------------------

PATTERN_DELIBERATION_OUTCOME = "deliberation_outcome"
PATTERN_CONSENSUS = "consensus_pattern"
PATTERN_AGENT_ACCURACY = "agent_accuracy"
PATTERN_TOPIC = "topic_pattern"

ALL_PATTERN_TYPES = frozenset(
    {
        PATTERN_DELIBERATION_OUTCOME,
        PATTERN_CONSENSUS,
        PATTERN_AGENT_ACCURACY,
        PATTERN_TOPIC,
    }
)


# ---------------------------------------------------------------------------
# Extraction helpers
# ---------------------------------------------------------------------------


def _safe_float(value: object, default: float = 0.0) -> float:
    """Coerce a value to float, returning *default* on failure."""
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return float(value)
    return default


def _confidence_range(
    confidences: list[float],
) -> dict[str, float]:
    """Build a confidence range dict from a list of confidence values."""
    if not confidences:
        return {"low": 0.0, "high": 0.0}
    return {"low": round(min(confidences), 4), "high": round(max(confidences), 4)}


def _build_outcome_summary(
    deliberation: Deliberation,
    synthesis: DeliberationSynthesis | None,
    entry_count: int,
) -> str:
    """Build a human-readable outcome summary for a deliberation."""
    topic = deliberation.topic or "unknown topic"
    status = deliberation.status
    if synthesis:
        consensus = synthesis.consensus_level
        return (
            f"Deliberation on '{topic}' {status} with {consensus} consensus "
            f"after {entry_count} entries."
        )
    return f"Deliberation on '{topic}' {status} after {entry_count} entries."


# ---------------------------------------------------------------------------
# Core extraction pipeline
# ---------------------------------------------------------------------------


class EpisodicExtractionService:
    """Extracts episodic memory patterns from concluded deliberations.

    Usage::

        service = EpisodicExtractionService()
        patterns = await service.extract_patterns(session, deliberation_id, board_id)
    """

    async def extract_patterns(
        self,
        session: AsyncSession,
        deliberation_id: UUID,
        board_id: UUID,
    ) -> list[EpisodicMemory]:
        """Run the full extraction pipeline for a concluded deliberation.

        Returns the list of newly created :class:`EpisodicMemory` rows.
        Skips extraction silently if the deliberation is not in a terminal state.
        """
        deliberation = await Deliberation.objects.filter_by(
            id=deliberation_id,
            board_id=board_id,
        ).first(session)

        if deliberation is None:
            logger.warning(
                "episodic.extract_patterns.not_found delib=%s board=%s",
                deliberation_id,
                board_id,
            )
            return []

        if deliberation.status not in {"concluded", "abandoned"}:
            logger.debug(
                "episodic.extract_patterns.skipped delib=%s status=%s",
                deliberation_id,
                deliberation.status,
            )
            return []

        # Fetch entries and synthesis
        entries = await (
            DeliberationEntry.objects.filter_by(deliberation_id=deliberation_id)
            .order_by(col(DeliberationEntry.sequence))
            .all(session)
        )

        synthesis = await DeliberationSynthesis.objects.filter_by(
            deliberation_id=deliberation_id,
        ).first(session)

        patterns: list[EpisodicMemory] = []

        # 1. Deliberation outcome pattern
        outcome = await self._extract_outcome(
            session,
            deliberation=deliberation,
            synthesis=synthesis,
            entries=entries,
        )
        if outcome:
            patterns.append(outcome)

        # 2. Consensus pattern (only for concluded deliberations with synthesis)
        if synthesis and deliberation.status == "concluded":
            consensus = await self._extract_consensus_pattern(
                session,
                deliberation=deliberation,
                synthesis=synthesis,
                entries=entries,
            )
            if consensus:
                patterns.append(consensus)

        # 3. Agent accuracy patterns
        if synthesis and deliberation.status == "concluded":
            agent_patterns = await self._extract_agent_accuracy(
                session,
                deliberation=deliberation,
                synthesis=synthesis,
                entries=entries,
            )
            patterns.extend(agent_patterns)

        # 4. Topic pattern
        topic_pattern = await self._extract_topic_pattern(
            session,
            deliberation=deliberation,
            synthesis=synthesis,
            entries=entries,
        )
        if topic_pattern:
            patterns.append(topic_pattern)

        if patterns:
            for p in patterns:
                session.add(p)
            await session.commit()
            for p in patterns:
                await session.refresh(p)

        logger.info(
            "episodic.extract_patterns.done delib=%s patterns=%d",
            deliberation_id,
            len(patterns),
        )
        return patterns

    # ------------------------------------------------------------------
    # Individual pattern extractors
    # ------------------------------------------------------------------

    async def _extract_outcome(
        self,
        session: AsyncSession,
        *,
        deliberation: Deliberation,
        synthesis: DeliberationSynthesis | None,
        entries: list[DeliberationEntry],
    ) -> EpisodicMemory | None:
        """Extract a deliberation_outcome pattern."""
        entry_count = len(entries)
        confidences = [e.confidence for e in entries if e.confidence is not None]
        unique_agents = {e.agent_id for e in entries if e.agent_id is not None}

        summary = _build_outcome_summary(deliberation, synthesis, entry_count)
        details: dict[str, object] = {
            "status": deliberation.status,
            "entry_count": entry_count,
            "participant_count": len(unique_agents),
            "duration_ms": deliberation.duration_ms,
            "max_turns": deliberation.max_turns,
            "trigger_reason": deliberation.trigger_reason,
        }

        if synthesis:
            details["consensus_level"] = synthesis.consensus_level
            details["synthesis_confidence"] = synthesis.confidence
            details["key_point_count"] = len(synthesis.key_points or [])
            details["dissenting_view_count"] = len(synthesis.dissenting_views or [])

        outcome_positive = deliberation.status == "concluded"

        embedding = await self._embed_text(summary)

        return EpisodicMemory(
            board_id=deliberation.board_id,
            pattern_type=PATTERN_DELIBERATION_OUTCOME,
            topic=deliberation.topic,
            deliberation_id=deliberation.id,
            pattern_summary=summary,
            pattern_details=details,
            outcome_positive=outcome_positive,
            confidence_range=_confidence_range(confidences),
            occurrence_count=1,
            success_rate=1.0 if outcome_positive else 0.0,
            reliability_score=_safe_float(
                synthesis.confidence if synthesis else None, 0.5
            ),
            embedding=embedding,
        )

    async def _extract_consensus_pattern(
        self,
        session: AsyncSession,
        *,
        deliberation: Deliberation,
        synthesis: DeliberationSynthesis,
        entries: list[DeliberationEntry],
    ) -> EpisodicMemory | None:
        """Extract a consensus_pattern from a concluded deliberation."""
        # Analyse phase distribution
        phase_counts: dict[str, int] = defaultdict(int)
        type_counts: dict[str, int] = defaultdict(int)
        for entry in entries:
            phase_counts[entry.phase] += 1
            type_counts[entry.entry_type] += 1

        # Find which phase had the most entries
        dominant_phase = (
            max(phase_counts, key=phase_counts.get) if phase_counts else "unknown"
        )  # type: ignore[arg-type]

        summary = (
            f"Consensus reached via {synthesis.consensus_level} on "
            f"'{deliberation.topic}' — dominant phase: {dominant_phase}, "
            f"{len(entries)} total entries."
        )

        details: dict[str, object] = {
            "consensus_level": synthesis.consensus_level,
            "phase_distribution": dict(phase_counts),
            "entry_type_distribution": dict(type_counts),
            "dominant_phase": dominant_phase,
            "dissenting_views": synthesis.dissenting_views or [],
            "dissenting_view_count": len(synthesis.dissenting_views or []),
            "key_points": synthesis.key_points or [],
            "synthesis_confidence": synthesis.confidence,
        }

        confidences = [e.confidence for e in entries if e.confidence is not None]

        embedding = await self._embed_text(summary)

        return EpisodicMemory(
            board_id=deliberation.board_id,
            pattern_type=PATTERN_CONSENSUS,
            topic=deliberation.topic,
            deliberation_id=deliberation.id,
            pattern_summary=summary,
            pattern_details=details,
            outcome_positive=True,
            confidence_range=_confidence_range(confidences),
            occurrence_count=1,
            success_rate=1.0,
            reliability_score=synthesis.confidence,
            embedding=embedding,
        )

    async def _extract_agent_accuracy(
        self,
        session: AsyncSession,
        *,
        deliberation: Deliberation,
        synthesis: DeliberationSynthesis,
        entries: list[DeliberationEntry],
    ) -> list[EpisodicMemory]:
        """Extract per-agent accuracy patterns.

        For each agent that participated, compares their initial position
        against the synthesis conclusion to determine alignment.
        """
        # Group entries by agent, keeping only agent-authored entries
        agent_entries: dict[UUID, list[DeliberationEntry]] = defaultdict(list)
        for entry in entries:
            if entry.agent_id is not None:
                agent_entries[entry.agent_id].append(entry)

        if not agent_entries:
            return []

        synthesis_content_lower = (synthesis.content or "").lower()
        patterns: list[EpisodicMemory] = []

        for agent_id, agent_entry_list in agent_entries.items():
            # Sort by sequence to find initial position
            sorted_entries = sorted(agent_entry_list, key=lambda e: e.sequence)
            initial_entry = sorted_entries[0]

            # Determine position types contributed
            types_contributed = list({e.entry_type for e in sorted_entries})

            # Heuristic alignment check: does the agent's initial position
            # appear (loosely) in the synthesis?
            initial_position = (initial_entry.position or "").lower().strip()
            position_aligned = False
            if initial_position and synthesis_content_lower:
                # Simple containment heuristic — a real implementation would
                # use embedding similarity, but this is a reasonable baseline.
                position_words = set(initial_position.split())
                synthesis_words = set(synthesis_content_lower.split())
                # If >40% of position words appear in synthesis, consider aligned
                if position_words:
                    overlap = len(position_words & synthesis_words) / len(
                        position_words
                    )
                    position_aligned = overlap > 0.4

            confidences = [
                e.confidence for e in sorted_entries if e.confidence is not None
            ]

            summary = (
                f"Agent {agent_id} contributed {len(sorted_entries)} entries "
                f"to deliberation on '{deliberation.topic}'. "
                f"Position {'aligned' if position_aligned else 'diverged'} "
                f"from synthesis."
            )

            details: dict[str, object] = {
                "agent_id": str(agent_id),
                "positions_taken": len(sorted_entries),
                "positions_accepted": 1 if position_aligned else 0,
                "initial_entry_type": initial_entry.entry_type,
                "initial_position": initial_entry.position,
                "initial_confidence": initial_entry.confidence,
                "types_contributed": types_contributed,
                "position_aligned": position_aligned,
                "consensus_level": synthesis.consensus_level,
            }

            # Determine strongest / weakest areas from entry types
            thesis_count = sum(
                1 for e in sorted_entries if e.entry_type in {"thesis", "antithesis"}
            )
            evidence_count = sum(
                1 for e in sorted_entries if e.entry_type == "evidence"
            )
            rebuttal_count = sum(
                1 for e in sorted_entries if e.entry_type == "rebuttal"
            )

            strongest: list[str] = []
            weakest: list[str] = []
            contributions = {
                "argumentation": thesis_count + rebuttal_count,
                "evidence_gathering": evidence_count,
            }
            if contributions:
                best_area = max(contributions, key=contributions.get)  # type: ignore[arg-type]
                worst_area = min(contributions, key=contributions.get)  # type: ignore[arg-type]
                if contributions[best_area] > 0:
                    strongest.append(best_area)
                if contributions[worst_area] == 0 and best_area != worst_area:
                    weakest.append(worst_area)

            details["strongest_areas"] = strongest
            details["weakest_areas"] = weakest

            embedding = await self._embed_text(summary)

            accuracy_rate = 1.0 if position_aligned else 0.0

            # Check for existing agent accuracy pattern to increment count
            existing = await EpisodicMemory.objects.filter_by(
                board_id=deliberation.board_id,
                pattern_type=PATTERN_AGENT_ACCURACY,
            ).all(session)

            prior_occurrence_count = 0
            prior_total_accepted = 0
            prior_total_positions = 0
            for ex in existing:
                ex_details = ex.pattern_details or {}
                if str(ex_details.get("agent_id")) == str(agent_id):
                    prior_occurrence_count += ex.occurrence_count
                    prior_total_accepted += int(ex_details.get("positions_accepted", 0))
                    prior_total_positions += int(ex_details.get("positions_taken", 0))

            cumulative_accepted = prior_total_accepted + (1 if position_aligned else 0)
            cumulative_total = prior_total_positions + len(sorted_entries)
            cumulative_accuracy = (
                round(cumulative_accepted / cumulative_total, 4)
                if cumulative_total > 0
                else None
            )

            pattern = EpisodicMemory(
                board_id=deliberation.board_id,
                pattern_type=PATTERN_AGENT_ACCURACY,
                topic=deliberation.topic,
                deliberation_id=deliberation.id,
                pattern_summary=summary,
                pattern_details=details,
                outcome_positive=position_aligned,
                confidence_range=_confidence_range(confidences),
                occurrence_count=prior_occurrence_count + 1,
                success_rate=cumulative_accuracy,
                reliability_score=_safe_float(synthesis.confidence, 0.5),
                embedding=embedding,
            )
            patterns.append(pattern)

        return patterns

    async def _extract_topic_pattern(
        self,
        session: AsyncSession,
        *,
        deliberation: Deliberation,
        synthesis: DeliberationSynthesis | None,
        entries: list[DeliberationEntry],
    ) -> EpisodicMemory | None:
        """Extract a topic_pattern capturing recurring topic themes."""
        topic = deliberation.topic
        if not topic:
            return None

        # Count existing topic patterns for this board+topic
        existing_topic_patterns = await EpisodicMemory.objects.filter_by(
            board_id=deliberation.board_id,
            pattern_type=PATTERN_TOPIC,
            topic=topic,
        ).all(session)

        occurrence_count = len(existing_topic_patterns) + 1

        # Aggregate success rate across all occurrences
        prior_successes = sum(1 for p in existing_topic_patterns if p.outcome_positive)
        current_success = 1 if deliberation.status == "concluded" else 0
        total_successes = prior_successes + current_success
        overall_success_rate = round(total_successes / occurrence_count, 4)

        # Typical consensus levels for this topic
        consensus_levels: list[str] = []
        for p in existing_topic_patterns:
            details = p.pattern_details or {}
            cl = details.get("consensus_level")
            if isinstance(cl, str):
                consensus_levels.append(cl)
        if synthesis:
            consensus_levels.append(synthesis.consensus_level)

        unique_agents = {e.agent_id for e in entries if e.agent_id is not None}

        summary = (
            f"Topic '{topic}' has been deliberated {occurrence_count} time(s) "
            f"on this board with a {overall_success_rate:.0%} conclusion rate."
        )

        details: dict[str, object] = {
            "topic": topic,
            "occurrence_count": occurrence_count,
            "latest_status": deliberation.status,
            "latest_consensus_level": (
                synthesis.consensus_level if synthesis else None
            ),
            "historical_consensus_levels": consensus_levels,
            "latest_participant_count": len(unique_agents),
            "latest_entry_count": len(entries),
            "latest_duration_ms": deliberation.duration_ms,
            "success_rate": overall_success_rate,
        }

        confidences = [e.confidence for e in entries if e.confidence is not None]

        embedding = await self._embed_text(summary)

        return EpisodicMemory(
            board_id=deliberation.board_id,
            pattern_type=PATTERN_TOPIC,
            topic=topic,
            deliberation_id=deliberation.id,
            pattern_summary=summary,
            pattern_details=details,
            outcome_positive=deliberation.status == "concluded",
            confidence_range=_confidence_range(confidences),
            occurrence_count=occurrence_count,
            success_rate=overall_success_rate,
            reliability_score=_safe_float(
                synthesis.confidence if synthesis else None, 0.5
            ),
            embedding=embedding,
        )

    # ------------------------------------------------------------------
    # Semantic search over episodic memories
    # ------------------------------------------------------------------

    async def search_similar(
        self,
        session: AsyncSession,
        *,
        board_id: UUID,
        query: str,
        pattern_type: str | None = None,
        limit: int = 10,
    ) -> list[EpisodicMemory]:
        """Search episodic memories by semantic similarity.

        Falls back to ILIKE text search when the embedding provider is
        ``none``.  When pgvector embeddings are available, performs a
        cosine-similarity nearest-neighbor search.
        """
        query_embedding = await self._embed_text(query)

        if query_embedding is not None:
            return await self._vector_search(
                session,
                board_id=board_id,
                query_embedding=query_embedding,
                pattern_type=pattern_type,
                limit=limit,
            )

        # Fallback: text-based ILIKE search
        return await self._text_search(
            session,
            board_id=board_id,
            query=query,
            pattern_type=pattern_type,
            limit=limit,
        )

    async def _vector_search(
        self,
        session: AsyncSession,
        *,
        board_id: UUID,
        query_embedding: list[float],
        pattern_type: str | None = None,
        limit: int = 10,
    ) -> list[EpisodicMemory]:
        """Perform a pgvector cosine similarity search.

        This implementation stores embeddings as JSON arrays (not native
        Vector columns) so we compute similarity in Python after fetching
        candidate rows.  A future migration can switch to native pgvector
        ``<=>`` operator for server-side ordering.
        """
        qs = EpisodicMemory.objects.filter_by(board_id=board_id)
        if pattern_type:
            qs = qs.filter(col(EpisodicMemory.pattern_type) == pattern_type)
        qs = qs.order_by(col(EpisodicMemory.created_at).desc())

        # Fetch candidates (cap at 500 for in-memory re-ranking)
        candidates = await qs.all(session)
        candidates = list(candidates[:500])

        if not candidates:
            return []

        # Compute cosine similarities in-memory
        scored: list[tuple[float, EpisodicMemory]] = []
        for mem in candidates:
            if mem.embedding and isinstance(mem.embedding, list):
                sim = _cosine_similarity(query_embedding, mem.embedding)
                scored.append((sim, mem))

        # Sort by descending similarity
        scored.sort(key=lambda t: t[0], reverse=True)
        return [mem for _, mem in scored[:limit]]

    async def _text_search(
        self,
        session: AsyncSession,
        *,
        board_id: UUID,
        query: str,
        pattern_type: str | None = None,
        limit: int = 10,
    ) -> list[EpisodicMemory]:
        """Fallback ILIKE text search over episodic memory summaries."""
        search_term = f"%{query.strip()}%"
        qs = EpisodicMemory.objects.filter_by(board_id=board_id).filter(
            col(EpisodicMemory.pattern_summary).ilike(search_term)
            | col(EpisodicMemory.topic).ilike(search_term)
        )
        if pattern_type:
            qs = qs.filter(col(EpisodicMemory.pattern_type) == pattern_type)
        qs = qs.order_by(col(EpisodicMemory.created_at).desc())

        results = await qs.all(session)
        return list(results[:limit])

    # ------------------------------------------------------------------
    # Aggregation helpers
    # ------------------------------------------------------------------

    async def get_agent_track_record(
        self,
        session: AsyncSession,
        *,
        board_id: UUID,
        agent_id: UUID,
    ) -> dict[str, object]:
        """Compute an aggregated accuracy record for an agent on a board.

        Returns a dict compatible with :class:`AgentTrackRecord`.
        """
        patterns = await EpisodicMemory.objects.filter_by(
            board_id=board_id,
            pattern_type=PATTERN_AGENT_ACCURACY,
        ).all(session)

        total_positions = 0
        accepted_positions = 0
        strongest_set: set[str] = set()
        weakest_set: set[str] = set()
        pattern_count = 0

        for pattern in patterns:
            details = pattern.pattern_details or {}
            if str(details.get("agent_id")) != str(agent_id):
                continue
            pattern_count += 1
            total_positions += int(details.get("positions_taken", 0))
            accepted_positions += int(details.get("positions_accepted", 0))
            for area in details.get("strongest_areas", []):
                if isinstance(area, str):
                    strongest_set.add(area)
            for area in details.get("weakest_areas", []):
                if isinstance(area, str):
                    weakest_set.add(area)

        accuracy_rate: float | None = None
        if total_positions > 0:
            accuracy_rate = round(accepted_positions / total_positions, 4)

        return {
            "agent_id": str(agent_id),
            "board_id": str(board_id),
            "total_positions": total_positions,
            "accepted_positions": accepted_positions,
            "accuracy_rate": accuracy_rate,
            "strongest_areas": sorted(strongest_set) if strongest_set else None,
            "weakest_areas": sorted(weakest_set) if weakest_set else None,
            "pattern_count": pattern_count,
        }

    async def get_board_topic_summary(
        self,
        session: AsyncSession,
        *,
        board_id: UUID,
    ) -> list[dict[str, object]]:
        """Return a summary of all topics deliberated on a board.

        Groups topic patterns by topic name and returns aggregated stats.
        """
        patterns = await EpisodicMemory.objects.filter_by(
            board_id=board_id,
            pattern_type=PATTERN_TOPIC,
        ).all(session)

        topic_map: dict[str, list[EpisodicMemory]] = defaultdict(list)
        for p in patterns:
            if p.topic:
                topic_map[p.topic].append(p)

        summaries: list[dict[str, object]] = []
        for topic, topic_patterns in topic_map.items():
            latest = max(topic_patterns, key=lambda p: p.created_at)
            details = latest.pattern_details or {}
            summaries.append(
                {
                    "topic": topic,
                    "deliberation_count": len(topic_patterns),
                    "latest_status": details.get("latest_status"),
                    "latest_consensus_level": details.get("latest_consensus_level"),
                    "success_rate": latest.success_rate,
                    "latest_deliberation_id": (
                        str(latest.deliberation_id) if latest.deliberation_id else None
                    ),
                }
            )

        return summaries

    # ------------------------------------------------------------------
    # Embedding helper
    # ------------------------------------------------------------------

    async def _embed_text(self, text: str) -> list[float] | None:
        """Generate an embedding vector for the given text.

        Returns ``None`` when the embedding provider is disabled.
        """
        try:
            return await embedding_service.embed(text)
        except Exception:
            logger.warning("episodic.embed_failed text_len=%d", len(text))
            return None


# ---------------------------------------------------------------------------
# Queue integration
# ---------------------------------------------------------------------------


def build_extraction_task_payload(
    deliberation_id: UUID,
    board_id: UUID,
) -> dict[str, str]:
    """Build a payload dict for enqueuing episodic extraction via RQ."""
    return {
        "deliberation_id": str(deliberation_id),
        "board_id": str(board_id),
    }


async def run_extraction_from_payload(
    session: AsyncSession,
    payload: dict[str, str],
) -> list[EpisodicMemory]:
    """Execute episodic extraction from a queued task payload.

    Called by the RQ worker when processing an ``episodic_extraction`` task.
    """
    deliberation_id = UUID(payload["deliberation_id"])
    board_id = UUID(payload["board_id"])
    service = EpisodicExtractionService()
    return await service.extract_patterns(session, deliberation_id, board_id)


# ---------------------------------------------------------------------------
# Math helpers
# ---------------------------------------------------------------------------


def _cosine_similarity(a: list[float], b: list[float]) -> float:
    """Compute cosine similarity between two vectors.

    Returns 0.0 if either vector has zero magnitude.
    """
    if len(a) != len(b):
        return 0.0
    dot = sum(x * y for x, y in zip(a, b))
    mag_a = sum(x * x for x in a) ** 0.5
    mag_b = sum(x * x for x in b) ** 0.5
    if mag_a == 0.0 or mag_b == 0.0:
        return 0.0
    return dot / (mag_a * mag_b)


# Module-level singleton
episodic_extraction_service = EpisodicExtractionService()
