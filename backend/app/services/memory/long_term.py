"""Long-term memory service — pgvector semantic search over board memory.

Long-term memory is the persistent semantic layer backed by Postgres + pgvector.
It enables agents to recall board memories and episodic patterns by meaning
rather than exact keyword match.

Two search modes are supported:

1. **Semantic search** — embed the query text and find nearest neighbours via
   cosine distance (requires ``EMBEDDING_PROVIDER != none``).
2. **Keyword fallback** — ``ILIKE`` text search when embeddings are disabled
   or the embedding call fails at runtime.

Usage::

    from app.services.memory.long_term import long_term_memory

    results = await long_term_memory.search(
        session, board_id=board.id, query="authentication module"
    )
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Literal
from uuid import UUID

from sqlalchemy import Select, literal_column, text
from sqlmodel import col, select

from app.core.config import settings
from app.models.board_memory import BoardMemory
from app.models.episodic_memory import EpisodicMemory
from app.services.memory.embedding import EmbeddingService, embedding_service

if TYPE_CHECKING:
    from sqlmodel.ext.asyncio.session import AsyncSession

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Result types
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class MemorySearchResult:
    """A single search hit from long-term memory."""

    id: UUID
    board_id: UUID
    content: str
    tags: list[str] | None = None
    source: str | None = None
    score: float = 0.0
    match_type: Literal["semantic", "keyword"] = "keyword"


@dataclass(frozen=True)
class EpisodicSearchResult:
    """A single search hit from episodic memory."""

    id: UUID
    board_id: UUID
    pattern_type: str
    pattern_summary: str
    topic: str | None = None
    confidence_range: dict[str, object] | None = None
    score: float = 0.0
    match_type: Literal["semantic", "keyword"] = "keyword"


# ---------------------------------------------------------------------------
# Service
# ---------------------------------------------------------------------------

# Minimum score threshold for semantic results (cosine distance).
# Lower distance = better match.  We filter out anything above this.
_DEFAULT_SEMANTIC_DISTANCE_THRESHOLD: float = 0.85

# Maximum results when no explicit limit is given.
_DEFAULT_LIMIT: int = 10


@dataclass
class LongTermMemoryService:
    """Persistent semantic search across board memory and episodic patterns.

    The service wraps the :class:`EmbeddingService` and applies pgvector
    ``<=>`` (cosine distance) ordering when vector search is available,
    falling back to ``ILIKE`` keyword matching otherwise.
    """

    _embedder: EmbeddingService = field(default_factory=lambda: embedding_service)
    _distance_threshold: float = _DEFAULT_SEMANTIC_DISTANCE_THRESHOLD

    # ------------------------------------------------------------------
    # Board memory search
    # ------------------------------------------------------------------

    async def search(
        self,
        session: AsyncSession,
        *,
        board_id: UUID,
        query: str,
        limit: int = _DEFAULT_LIMIT,
        tags: list[str] | None = None,
        include_chat: bool = False,
    ) -> list[MemorySearchResult]:
        """Search board memories by semantic similarity or keyword match.

        Parameters
        ----------
        session:
            Active database session.
        board_id:
            Scope the search to this board.
        query:
            Free-text search query.
        limit:
            Maximum results to return.
        tags:
            Optional tag filter — only return memories with *any* of the
            given tags.
        include_chat:
            Whether to include chat-sourced memories.  Defaults to ``False``
            to keep recall focused on structured knowledge.
        """
        query = (query or "").strip()
        if not query:
            return []

        # Attempt semantic search first
        vector = await self._embedder.embed(query)
        if vector is not None:
            results = await self._semantic_board_search(
                session,
                board_id=board_id,
                vector=vector,
                limit=limit,
                tags=tags,
                include_chat=include_chat,
            )
            if results:
                return results
            logger.debug(
                "longterm.board_search.semantic_empty board=%s query=%s — "
                "falling back to keyword",
                board_id,
                query[:80],
            )

        # Fallback: keyword search
        return await self._keyword_board_search(
            session,
            board_id=board_id,
            query=query,
            limit=limit,
            tags=tags,
            include_chat=include_chat,
        )

    async def store_embedding(
        self,
        session: AsyncSession,
        *,
        memory: BoardMemory,
    ) -> bool:
        """Compute and persist an embedding vector for a board memory row.

        Returns ``True`` if the embedding was successfully stored, ``False``
        if the embedding provider is disabled or the call failed.
        """
        if not memory.content:
            return False

        vector = await self._embedder.embed(memory.content)
        if vector is None:
            return False

        memory.embedding = vector
        session.add(memory)
        await session.flush()

        logger.debug(
            "longterm.board_memory.embedded id=%s dim=%d",
            memory.id,
            len(vector),
        )
        return True

    # ------------------------------------------------------------------
    # Episodic memory search
    # ------------------------------------------------------------------

    async def search_episodic(
        self,
        session: AsyncSession,
        *,
        board_id: UUID,
        query: str,
        limit: int = _DEFAULT_LIMIT,
        pattern_type: str | None = None,
    ) -> list[EpisodicSearchResult]:
        """Search episodic memories by semantic similarity or keyword.

        Parameters
        ----------
        session:
            Active database session.
        board_id:
            Scope the search to this board.
        query:
            Free-text search query.
        limit:
            Maximum results to return.
        pattern_type:
            Optional filter to a specific pattern category
            (e.g. ``"agent_accuracy"``, ``"consensus_pattern"``).
        """
        query = (query or "").strip()
        if not query:
            return []

        vector = await self._embedder.embed(query)
        if vector is not None:
            results = await self._semantic_episodic_search(
                session,
                board_id=board_id,
                vector=vector,
                limit=limit,
                pattern_type=pattern_type,
            )
            if results:
                return results

        return await self._keyword_episodic_search(
            session,
            board_id=board_id,
            query=query,
            limit=limit,
            pattern_type=pattern_type,
        )

    async def store_episodic_embedding(
        self,
        session: AsyncSession,
        *,
        memory: EpisodicMemory,
    ) -> bool:
        """Compute and persist an embedding for an episodic memory row."""
        text_payload = memory.pattern_summary or ""
        if memory.topic:
            text_payload = f"{memory.topic}: {text_payload}"
        if not text_payload.strip():
            return False

        vector = await self._embedder.embed(text_payload)
        if vector is None:
            return False

        memory.embedding = vector
        session.add(memory)
        await session.flush()

        logger.debug(
            "longterm.episodic_memory.embedded id=%s dim=%d",
            memory.id,
            len(vector),
        )
        return True

    # ------------------------------------------------------------------
    # Related / contextual helpers
    # ------------------------------------------------------------------

    async def get_related_memories(
        self,
        session: AsyncSession,
        *,
        board_id: UUID,
        content: str,
        exclude_ids: list[UUID] | None = None,
        limit: int = 5,
    ) -> list[MemorySearchResult]:
        """Find board memories semantically related to *content*.

        Useful for enriching deliberation context windows with relevant
        prior knowledge before agents contribute.
        """
        results = await self.search(
            session,
            board_id=board_id,
            query=content,
            limit=limit + len(exclude_ids or []),
        )
        if exclude_ids:
            exclude_set = set(exclude_ids)
            results = [r for r in results if r.id not in exclude_set]
        return results[:limit]

    # ------------------------------------------------------------------
    # Internal — semantic (pgvector)
    # ------------------------------------------------------------------

    async def _semantic_board_search(
        self,
        session: AsyncSession,
        *,
        board_id: UUID,
        vector: list[float],
        limit: int,
        tags: list[str] | None,
        include_chat: bool,
    ) -> list[MemorySearchResult]:
        """Execute a pgvector cosine distance search over board_memory."""
        try:
            vector_literal = _vector_literal(vector)

            # Build distance expression.
            # ``embedding <=> $vector`` returns cosine distance (0 = identical).
            distance_expr = literal_column(
                f"embedding <=> '{vector_literal}'::vector"
            ).label("distance")

            stmt = (
                select(BoardMemory, distance_expr)
                .where(col(BoardMemory.board_id) == board_id)
                .where(col(BoardMemory.embedding).isnot(None))
                .order_by(text(f"embedding <=> '{vector_literal}'::vector"))
                .limit(limit)
            )

            if not include_chat:
                stmt = stmt.where(col(BoardMemory.is_chat).is_(False))

            if tags:
                stmt = _apply_json_tag_filter(stmt, BoardMemory, tags)

            rows = (await session.exec(stmt)).all()  # type: ignore[call-overload]

            results: list[MemorySearchResult] = []
            for row in rows:
                # row is a tuple of (BoardMemory, distance_float)
                memory, distance = row[0], float(row[1])
                if distance > self._distance_threshold:
                    continue
                # Convert distance to a similarity score (1 - distance).
                score = max(0.0, 1.0 - distance)
                results.append(
                    MemorySearchResult(
                        id=memory.id,
                        board_id=memory.board_id,
                        content=memory.content,
                        tags=memory.tags,
                        source=memory.source,
                        score=round(score, 4),
                        match_type="semantic",
                    )
                )
            return results

        except Exception:
            logger.warning(
                "longterm.semantic_board_search.failed board=%s — "
                "pgvector may not be available",
                board_id,
                exc_info=True,
            )
            return []

    async def _semantic_episodic_search(
        self,
        session: AsyncSession,
        *,
        board_id: UUID,
        vector: list[float],
        limit: int,
        pattern_type: str | None,
    ) -> list[EpisodicSearchResult]:
        """Execute a pgvector cosine distance search over episodic_memory."""
        try:
            vector_literal = _vector_literal(vector)

            distance_expr = literal_column(
                f"embedding <=> '{vector_literal}'::vector"
            ).label("distance")

            stmt = (
                select(EpisodicMemory, distance_expr)
                .where(col(EpisodicMemory.board_id) == board_id)
                .where(col(EpisodicMemory.embedding).isnot(None))
                .order_by(text(f"embedding <=> '{vector_literal}'::vector"))
                .limit(limit)
            )

            if pattern_type:
                stmt = stmt.where(col(EpisodicMemory.pattern_type) == pattern_type)

            rows = (await session.exec(stmt)).all()  # type: ignore[call-overload]

            results: list[EpisodicSearchResult] = []
            for row in rows:
                mem, distance = row[0], float(row[1])
                if distance > self._distance_threshold:
                    continue
                score = max(0.0, 1.0 - distance)
                results.append(
                    EpisodicSearchResult(
                        id=mem.id,
                        board_id=mem.board_id,
                        pattern_type=mem.pattern_type,
                        topic=mem.topic,
                        pattern_summary=mem.pattern_summary,
                        confidence_range=mem.confidence_range,
                        score=round(score, 4),
                        match_type="semantic",
                    )
                )
            return results

        except Exception:
            logger.warning(
                "longterm.semantic_episodic_search.failed board=%s",
                board_id,
                exc_info=True,
            )
            return []

    # ------------------------------------------------------------------
    # Internal — keyword fallback
    # ------------------------------------------------------------------

    async def _keyword_board_search(
        self,
        session: AsyncSession,
        *,
        board_id: UUID,
        query: str,
        limit: int,
        tags: list[str] | None,
        include_chat: bool,
    ) -> list[MemorySearchResult]:
        """ILIKE keyword search fallback for board memories."""
        pattern = f"%{query}%"
        stmt = (
            BoardMemory.objects.filter_by(board_id=board_id)
            .filter(col(BoardMemory.content).ilike(pattern))
            .order_by(col(BoardMemory.created_at).desc())
        )

        if not include_chat:
            stmt = stmt.filter(col(BoardMemory.is_chat).is_(False))

        # Tag filtering on the queryset level (JSON contains check)
        if tags:
            for tag in tags:
                stmt = stmt.filter(
                    col(BoardMemory.content).isnot(None)  # keep queryset chainable
                )
            # We'll filter in-memory for tags since JSON array overlap
            # queries vary by driver.

        all_rows = await stmt.all(session)

        results: list[MemorySearchResult] = []
        for memory in all_rows:
            # Tag filter (in-memory for portability)
            if tags and memory.tags:
                if not any(t in memory.tags for t in tags):
                    continue
            elif tags and not memory.tags:
                continue

            results.append(
                MemorySearchResult(
                    id=memory.id,
                    board_id=memory.board_id,
                    content=memory.content,
                    tags=memory.tags,
                    source=memory.source,
                    score=_keyword_score(query, memory.content),
                    match_type="keyword",
                )
            )
            if len(results) >= limit:
                break

        # Sort by score descending
        results.sort(key=lambda r: r.score, reverse=True)
        return results

    async def _keyword_episodic_search(
        self,
        session: AsyncSession,
        *,
        board_id: UUID,
        query: str,
        limit: int,
        pattern_type: str | None,
    ) -> list[EpisodicSearchResult]:
        """ILIKE keyword search fallback for episodic memories."""
        pattern = f"%{query}%"
        qs = EpisodicMemory.objects.filter_by(board_id=board_id).filter(
            col(EpisodicMemory.pattern_summary).ilike(pattern)
            | col(EpisodicMemory.topic).ilike(pattern)
        )

        if pattern_type:
            qs = qs.filter(col(EpisodicMemory.pattern_type) == pattern_type)

        qs = qs.order_by(col(EpisodicMemory.created_at).desc())
        all_rows = await qs.all(session)

        results: list[EpisodicSearchResult] = []
        for mem in all_rows:
            combined = f"{mem.topic or ''} {mem.pattern_summary}"
            results.append(
                EpisodicSearchResult(
                    id=mem.id,
                    board_id=mem.board_id,
                    pattern_type=mem.pattern_type,
                    topic=mem.topic,
                    pattern_summary=mem.pattern_summary,
                    confidence_range=mem.confidence_range,
                    score=_keyword_score(query, combined),
                    match_type="keyword",
                )
            )
            if len(results) >= limit:
                break

        results.sort(key=lambda r: r.score, reverse=True)
        return results


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _vector_literal(vector: list[float]) -> str:
    """Format a Python float list as a pgvector literal ``[0.1,0.2,...]``."""
    components = ",".join(f"{v:.8f}" for v in vector)
    return f"[{components}]"


def _keyword_score(query: str, content: str) -> float:
    """Compute a naive keyword relevance score (0.0–1.0).

    Counts the fraction of query terms that appear in the content,
    case-insensitively.  This is intentionally simple — it exists only
    as a ranking heuristic when semantic search is unavailable.
    """
    if not query or not content:
        return 0.0
    query_lower = query.lower()
    content_lower = content.lower()

    terms = query_lower.split()
    if not terms:
        return 0.0

    matches = sum(1 for t in terms if t in content_lower)
    return round(matches / len(terms), 4)


def _apply_json_tag_filter(
    stmt: Select,  # type: ignore[type-arg]
    model: type,
    tags: list[str],
) -> Select:  # type: ignore[type-arg]
    """Apply a JSON array tag overlap filter via Postgres ``?|`` operator.

    Falls back gracefully if the column is not a native JSON/JSONB type
    by wrapping in a ``CAST``.
    """
    # Use the raw ``?|`` (overlap) operator for JSONB arrays.
    # ``tags_column ?| array['tag1', 'tag2']``
    tags_array = "{" + ",".join(tags) + "}"
    tag_col = getattr(model, "tags", None)
    if tag_col is not None:
        stmt = stmt.where(
            text(f"{model.__tablename__}.tags::jsonb ?| :tag_arr").bindparams(
                tag_arr=tags_array
            )
        )
    return stmt


# ---------------------------------------------------------------------------
# Module-level singleton
# ---------------------------------------------------------------------------

long_term_memory = LongTermMemoryService()
