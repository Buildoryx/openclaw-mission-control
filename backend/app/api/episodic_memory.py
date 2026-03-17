"""Episodic memory REST API endpoints.

Provides read-only access to learned patterns extracted from past deliberations,
including semantic search and per-agent track record summaries.

Router prefix: /boards/{board_id}/episodic-memory
"""

from __future__ import annotations

from typing import TYPE_CHECKING
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlmodel import col

from app.api.deps import (
    ActorContext,
    get_board_for_actor_read,
    require_admin_or_agent,
)
from app.db.pagination import paginate
from app.db.session import get_session
from app.models.episodic_memory import EpisodicMemory
from app.schemas.episodic_memory import AgentTrackRecord, EpisodicMemoryRead
from app.schemas.pagination import DefaultLimitOffsetPage

if TYPE_CHECKING:
    from fastapi_pagination.limit_offset import LimitOffsetPage
    from sqlmodel.ext.asyncio.session import AsyncSession

    from app.models.boards import Board

router = APIRouter(
    prefix="/boards/{board_id}/episodic-memory",
    tags=["episodic-memory"],
)

BOARD_READ_DEP = Depends(get_board_for_actor_read)
SESSION_DEP = Depends(get_session)
ACTOR_DEP = Depends(require_admin_or_agent)
PATTERN_TYPE_QUERY = Query(default=None, alias="pattern_type")
SEARCH_QUERY = Query(default=None, alias="q")
LIMIT_QUERY = Query(default=10, ge=1, le=100, alias="limit")
_RUNTIME_TYPE_REFERENCES = (UUID,)


# ---------------------------------------------------------------------------
# List episodic patterns
# ---------------------------------------------------------------------------


@router.get("", response_model=DefaultLimitOffsetPage[EpisodicMemoryRead])
async def list_episodic_patterns(
    *,
    pattern_type: str | None = PATTERN_TYPE_QUERY,
    board: Board = BOARD_READ_DEP,
    session: AsyncSession = SESSION_DEP,
    _actor: ActorContext = ACTOR_DEP,
) -> LimitOffsetPage[EpisodicMemoryRead]:
    """List episodic memory patterns for a board, optionally filtered by type."""
    statement = EpisodicMemory.objects.filter_by(board_id=board.id)
    if pattern_type:
        statement = statement.filter(col(EpisodicMemory.pattern_type) == pattern_type)
    statement = statement.order_by(col(EpisodicMemory.created_at).desc())
    return await paginate(session, statement.statement)


# ---------------------------------------------------------------------------
# Search episodic patterns
# ---------------------------------------------------------------------------


@router.get("/search", response_model=list[EpisodicMemoryRead])
async def search_episodic_patterns(
    *,
    q: str | None = SEARCH_QUERY,
    limit: int = LIMIT_QUERY,
    board: Board = BOARD_READ_DEP,
    session: AsyncSession = SESSION_DEP,
    _actor: ActorContext = ACTOR_DEP,
) -> list[EpisodicMemory]:
    """Search episodic memory patterns by keyword match on summary and topic.

    Full semantic (pgvector) search will be available once embedding
    infrastructure is wired.  This endpoint currently performs a text-based
    ``ILIKE`` search as a functional stand-in.
    """
    if not q or not q.strip():
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Query parameter 'q' is required.",
        )

    search_term = f"%{q.strip()}%"
    statement = (
        EpisodicMemory.objects.filter_by(board_id=board.id)
        .filter(
            col(EpisodicMemory.pattern_summary).ilike(search_term)
            | col(EpisodicMemory.topic).ilike(search_term)
        )
        .order_by(col(EpisodicMemory.created_at).desc())
    )

    results = await statement.all(session)
    return list(results[:limit])


# ---------------------------------------------------------------------------
# Agent track record
# ---------------------------------------------------------------------------


@router.get(
    "/agent/{agent_id}/track-record",
    response_model=AgentTrackRecord,
)
async def get_agent_track_record(
    agent_id: UUID,
    board: Board = BOARD_READ_DEP,
    session: AsyncSession = SESSION_DEP,
    _actor: ActorContext = ACTOR_DEP,
) -> AgentTrackRecord:
    """Get an aggregated accuracy summary for an agent on a board.

    Computes the track record from ``agent_accuracy`` episodic memory
    patterns.  Returns zeroed counts if no patterns exist yet.
    """
    patterns = await (
        EpisodicMemory.objects.filter_by(
            board_id=board.id,
            pattern_type="agent_accuracy",
        )
        .order_by(col(EpisodicMemory.created_at).desc())
        .all(session)
    )

    # Filter patterns relevant to this agent (stored in pattern_details)
    total_positions = 0
    accepted_positions = 0
    strongest_set: set[str] = set()
    weakest_set: set[str] = set()
    pattern_count = 0

    for pattern in patterns:
        details = pattern.pattern_details or {}
        detail_agent_id = details.get("agent_id")
        if detail_agent_id and str(detail_agent_id) == str(agent_id):
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

    return AgentTrackRecord(
        agent_id=agent_id,
        board_id=board.id,
        total_positions=total_positions,
        accepted_positions=accepted_positions,
        accuracy_rate=accuracy_rate,
        strongest_areas=sorted(strongest_set) if strongest_set else None,
        weakest_areas=sorted(weakest_set) if weakest_set else None,
        pattern_count=pattern_count,
    )
