"""Deliberation REST + SSE API endpoints.

Provides CRUD, phase management, entry submission, synthesis, and real-time
streaming for board-scoped deliberations.

Router prefix: /boards/{board_id}/deliberations
"""

from __future__ import annotations

import asyncio
import json
from datetime import UTC, datetime
from typing import TYPE_CHECKING
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from sqlmodel import col
from sse_starlette.sse import EventSourceResponse

from app.api.deps import (
    ActorContext,
    get_board_for_actor_read,
    get_board_for_actor_write,
    require_admin_or_agent,
)
from app.core.time import utcnow
from app.db.pagination import paginate
from app.db.session import async_session_maker, get_session
from app.models.deliberation import (
    DELIBERATION_STATUSES,
    Deliberation,
    DeliberationEntry,
    DeliberationSynthesis,
)
from app.schemas.deliberation import (
    DeliberationCreate,
    DeliberationEntryCreate,
    DeliberationEntryRead,
    DeliberationRead,
    DeliberationSynthesisCreate,
    DeliberationSynthesisRead,
    DeliberationUpdate,
)
from app.schemas.pagination import DefaultLimitOffsetPage
from app.services.deliberation import DeliberationError, deliberation_service
from app.services.deliberation_hooks import deliberation_hooks

if TYPE_CHECKING:
    from collections.abc import AsyncIterator, Sequence

    from fastapi_pagination.limit_offset import LimitOffsetPage
    from sqlmodel.ext.asyncio.session import AsyncSession

    from app.models.boards import Board

router = APIRouter(
    prefix="/boards/{board_id}/deliberations",
    tags=["deliberations"],
)

BOARD_READ_DEP = Depends(get_board_for_actor_read)
BOARD_WRITE_DEP = Depends(get_board_for_actor_write)
SESSION_DEP = Depends(get_session)
ACTOR_DEP = Depends(require_admin_or_agent)
SINCE_QUERY = Query(default=None)
STATUS_FILTER_QUERY = Query(default=None, alias="status")
PHASE_FILTER_QUERY = Query(default=None, alias="phase")
STREAM_POLL_SECONDS = 2
_RUNTIME_TYPE_REFERENCES = (UUID,)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _parse_since(value: str | None) -> datetime | None:
    if not value:
        return None
    normalized = value.strip()
    if not normalized:
        return None
    normalized = normalized.replace("Z", "+00:00")
    try:
        parsed = datetime.fromisoformat(normalized)
    except ValueError:
        return None
    if parsed.tzinfo is not None:
        return parsed.astimezone(UTC).replace(tzinfo=None)
    return parsed


async def _enrich_deliberation_read(
    session: AsyncSession,
    deliberation: Deliberation,
) -> DeliberationRead:
    """Convert a Deliberation model to a DeliberationRead with computed fields."""
    entry_count = await deliberation_service.get_entry_count(session, deliberation.id)
    has_synthesis = await deliberation_service.has_synthesis(session, deliberation.id)
    return DeliberationRead(
        id=deliberation.id,
        board_id=deliberation.board_id,
        topic=deliberation.topic,
        status=deliberation.status,
        initiated_by_agent_id=deliberation.initiated_by_agent_id,
        synthesizer_agent_id=deliberation.synthesizer_agent_id,
        trigger_reason=deliberation.trigger_reason,
        task_id=deliberation.task_id,
        parent_deliberation_id=deliberation.parent_deliberation_id,
        max_turns=deliberation.max_turns,
        outcome_changed=deliberation.outcome_changed,
        confidence_delta=deliberation.confidence_delta,
        duration_ms=deliberation.duration_ms,
        approval_id=deliberation.approval_id,
        entry_count=entry_count,
        has_synthesis=has_synthesis,
        created_at=deliberation.created_at,
        concluded_at=deliberation.concluded_at,
        updated_at=deliberation.updated_at,
    )


def _serialize_entry(entry: DeliberationEntry) -> dict[str, object]:
    return DeliberationEntryRead.model_validate(
        entry,
        from_attributes=True,
    ).model_dump(mode="json")


def _get_deliberation_or_404(
    result: Deliberation | None,
) -> Deliberation:
    if result is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND)
    return result


# ---------------------------------------------------------------------------
# List deliberations
# ---------------------------------------------------------------------------


@router.get("", response_model=DefaultLimitOffsetPage[DeliberationRead])
async def list_deliberations(
    *,
    status_filter: str | None = STATUS_FILTER_QUERY,
    board: Board = BOARD_READ_DEP,
    session: AsyncSession = SESSION_DEP,
    _actor: ActorContext = ACTOR_DEP,
) -> LimitOffsetPage[DeliberationRead]:
    """List deliberations for a board, optionally filtering by status."""
    statement = Deliberation.objects.filter_by(board_id=board.id)
    if status_filter and status_filter in DELIBERATION_STATUSES:
        statement = statement.filter(col(Deliberation.status) == status_filter)
    statement = statement.order_by(col(Deliberation.created_at).desc())

    async def _transform(items: Sequence[Deliberation]) -> list[DeliberationRead]:
        results: list[DeliberationRead] = []
        for item in items:
            results.append(await _enrich_deliberation_read(session, item))
        return results

    return await paginate(session, statement.statement, transformer=_transform)


# ---------------------------------------------------------------------------
# Create deliberation
# ---------------------------------------------------------------------------


@router.post(
    "",
    response_model=DeliberationRead,
    status_code=status.HTTP_201_CREATED,
)
async def create_deliberation(
    payload: DeliberationCreate,
    board: Board = BOARD_WRITE_DEP,
    session: AsyncSession = SESSION_DEP,
    actor: ActorContext = ACTOR_DEP,
) -> DeliberationRead:
    """Start a new deliberation on a board."""
    try:
        deliberation = await deliberation_service.start_deliberation(
            session,
            board=board,
            topic=payload.topic,
            actor=actor,
            trigger_reason=payload.trigger_reason,
            task_id=payload.task_id,
            max_turns=payload.max_turns,
        )
    except DeliberationError as exc:
        raise HTTPException(
            status_code=exc.status_code,
            detail=str(exc),
        ) from exc
    await deliberation_hooks.on_deliberation_started(
        session,
        board=board,
        deliberation=deliberation,
        actor=actor,
    )
    return await _enrich_deliberation_read(session, deliberation)


# ---------------------------------------------------------------------------
# Get deliberation detail
# ---------------------------------------------------------------------------


@router.get("/{deliberation_id}", response_model=DeliberationRead)
async def get_deliberation(
    deliberation_id: UUID,
    board: Board = BOARD_READ_DEP,
    session: AsyncSession = SESSION_DEP,
    _actor: ActorContext = ACTOR_DEP,
) -> DeliberationRead:
    """Get a single deliberation with entry count and synthesis status."""
    deliberation = _get_deliberation_or_404(
        await deliberation_service.get_deliberation(session, deliberation_id, board.id)
    )
    return await _enrich_deliberation_read(session, deliberation)


# ---------------------------------------------------------------------------
# List entries
# ---------------------------------------------------------------------------


@router.get(
    "/{deliberation_id}/entries",
    response_model=DefaultLimitOffsetPage[DeliberationEntryRead],
)
async def list_entries(
    deliberation_id: UUID,
    *,
    phase_filter: str | None = PHASE_FILTER_QUERY,
    board: Board = BOARD_READ_DEP,
    session: AsyncSession = SESSION_DEP,
    _actor: ActorContext = ACTOR_DEP,
) -> LimitOffsetPage[DeliberationEntryRead]:
    """List entries for a deliberation, optionally filtered by phase."""
    _ = _get_deliberation_or_404(
        await deliberation_service.get_deliberation(session, deliberation_id, board.id)
    )
    statement = DeliberationEntry.objects.filter_by(
        deliberation_id=deliberation_id,
    )
    if phase_filter:
        statement = statement.filter(col(DeliberationEntry.phase) == phase_filter)
    statement = statement.order_by(col(DeliberationEntry.sequence))
    return await paginate(session, statement.statement)


# ---------------------------------------------------------------------------
# Create entry
# ---------------------------------------------------------------------------


@router.post(
    "/{deliberation_id}/entries",
    response_model=DeliberationEntryRead,
    status_code=status.HTTP_201_CREATED,
)
async def create_entry(
    deliberation_id: UUID,
    payload: DeliberationEntryCreate,
    board: Board = BOARD_WRITE_DEP,
    session: AsyncSession = SESSION_DEP,
    actor: ActorContext = ACTOR_DEP,
) -> DeliberationEntry:
    """Contribute an entry (thesis, antithesis, evidence, vote, etc.)."""
    deliberation = _get_deliberation_or_404(
        await deliberation_service.get_deliberation(session, deliberation_id, board.id)
    )
    try:
        entry = await deliberation_service.add_entry(
            session,
            board=board,
            deliberation=deliberation,
            actor=actor,
            content=payload.content,
            phase=payload.phase,
            entry_type=payload.entry_type,
            position=payload.position,
            confidence=payload.confidence,
            parent_entry_id=payload.parent_entry_id,
            references=payload.references,
            entry_metadata=dict(payload.entry_metadata)
            if payload.entry_metadata
            else None,
        )
    except DeliberationError as exc:
        raise HTTPException(
            status_code=exc.status_code,
            detail=str(exc),
        ) from exc
    # Refresh deliberation to capture any auto-advance status change
    await session.refresh(deliberation)
    await deliberation_hooks.on_entry_added(
        session,
        board=board,
        deliberation=deliberation,
        entry=entry,
        actor=actor,
    )
    return entry


# ---------------------------------------------------------------------------
# SSE stream
# ---------------------------------------------------------------------------


@router.get("/{deliberation_id}/stream")
async def stream_deliberation(
    request: Request,
    deliberation_id: UUID,
    board: Board = BOARD_READ_DEP,
    _actor: ActorContext = ACTOR_DEP,
    since: str | None = SINCE_QUERY,
    session: AsyncSession = SESSION_DEP,
) -> EventSourceResponse:
    """Stream deliberation entries and phase changes over server-sent events.

    Emits event types: ``entry``, ``phase``, ``synthesis``, ``concluded``.
    """
    # Validate deliberation exists before opening the stream.
    _ = _get_deliberation_or_404(
        await deliberation_service.get_deliberation(session, deliberation_id, board.id)
    )

    since_dt = _parse_since(since) or utcnow()
    last_seen = since_dt
    last_status: str | None = None

    async def event_generator() -> AsyncIterator[dict[str, str]]:
        nonlocal last_seen, last_status
        while True:
            if await request.is_disconnected():
                break
            async with async_session_maker() as session:
                # Validate deliberation exists
                deliberation = await Deliberation.objects.filter_by(
                    id=deliberation_id, board_id=board.id
                ).first(session)
                if deliberation is None:
                    break

                # Emit phase change event
                if last_status is not None and deliberation.status != last_status:
                    yield {
                        "event": "phase",
                        "data": json.dumps(
                            {
                                "deliberation_id": str(deliberation_id),
                                "status": deliberation.status,
                                "previous_status": last_status,
                            }
                        ),
                    }
                    if deliberation.status == "concluded":
                        synthesis = await deliberation_service.get_synthesis(
                            session, deliberation_id
                        )
                        if synthesis is not None:
                            yield {
                                "event": "synthesis",
                                "data": json.dumps(
                                    DeliberationSynthesisRead.model_validate(
                                        synthesis, from_attributes=True
                                    ).model_dump(mode="json")
                                ),
                            }
                        yield {
                            "event": "concluded",
                            "data": json.dumps(
                                {
                                    "deliberation_id": str(deliberation_id),
                                    "concluded_at": (
                                        deliberation.concluded_at.isoformat()
                                        if deliberation.concluded_at
                                        else None
                                    ),
                                }
                            ),
                        }
                last_status = deliberation.status

                # Fetch new entries since last poll
                entries = await (
                    DeliberationEntry.objects.filter_by(
                        deliberation_id=deliberation_id,
                    )
                    .filter(col(DeliberationEntry.created_at) >= last_seen)
                    .order_by(col(DeliberationEntry.created_at))
                    .all(session)
                )
                for entry in entries:
                    last_seen = max(entry.created_at, last_seen)
                    yield {
                        "event": "entry",
                        "data": json.dumps({"entry": _serialize_entry(entry)}),
                    }

            await asyncio.sleep(STREAM_POLL_SECONDS)

    return EventSourceResponse(event_generator(), ping=15)


# ---------------------------------------------------------------------------
# Advance phase
# ---------------------------------------------------------------------------


@router.post("/{deliberation_id}/advance", response_model=DeliberationRead)
async def advance_deliberation(
    deliberation_id: UUID,
    board: Board = BOARD_WRITE_DEP,
    session: AsyncSession = SESSION_DEP,
    actor: ActorContext = ACTOR_DEP,
) -> DeliberationRead:
    """Advance a deliberation to the next phase."""
    deliberation = _get_deliberation_or_404(
        await deliberation_service.get_deliberation(session, deliberation_id, board.id)
    )
    previous_status = deliberation.status
    try:
        deliberation = await deliberation_service.advance_phase(
            session,
            board=board,
            deliberation=deliberation,
            actor=actor,
        )
    except DeliberationError as exc:
        raise HTTPException(
            status_code=exc.status_code,
            detail=str(exc),
        ) from exc
    await deliberation_hooks.on_phase_advanced(
        session,
        board=board,
        deliberation=deliberation,
        previous_status=previous_status,
        actor=actor,
    )
    return await _enrich_deliberation_read(session, deliberation)


# ---------------------------------------------------------------------------
# Abandon
# ---------------------------------------------------------------------------


@router.post("/{deliberation_id}/abandon", response_model=DeliberationRead)
async def abandon_deliberation(
    deliberation_id: UUID,
    payload: DeliberationUpdate | None = None,
    board: Board = BOARD_WRITE_DEP,
    session: AsyncSession = SESSION_DEP,
    actor: ActorContext = ACTOR_DEP,
) -> DeliberationRead:
    """Abandon a deliberation."""
    deliberation = _get_deliberation_or_404(
        await deliberation_service.get_deliberation(session, deliberation_id, board.id)
    )
    reason = payload.reason if payload else None
    try:
        deliberation = await deliberation_service.abandon(
            session,
            board=board,
            deliberation=deliberation,
            actor=actor,
            reason=reason,
        )
    except DeliberationError as exc:
        raise HTTPException(
            status_code=exc.status_code,
            detail=str(exc),
        ) from exc
    await deliberation_hooks.on_deliberation_abandoned(
        session,
        board=board,
        deliberation=deliberation,
        reason=reason,
        actor=actor,
    )
    return await _enrich_deliberation_read(session, deliberation)


# ---------------------------------------------------------------------------
# Synthesis
# ---------------------------------------------------------------------------


@router.get(
    "/{deliberation_id}/synthesis",
    response_model=DeliberationSynthesisRead,
)
async def get_synthesis(
    deliberation_id: UUID,
    board: Board = BOARD_READ_DEP,
    session: AsyncSession = SESSION_DEP,
    _actor: ActorContext = ACTOR_DEP,
) -> DeliberationSynthesis:
    """Get the synthesis for a concluded deliberation."""
    _ = _get_deliberation_or_404(
        await deliberation_service.get_deliberation(session, deliberation_id, board.id)
    )
    synthesis = await deliberation_service.get_synthesis(session, deliberation_id)
    if synthesis is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND)
    return synthesis


@router.post(
    "/{deliberation_id}/synthesis",
    response_model=DeliberationSynthesisRead,
    status_code=status.HTTP_201_CREATED,
)
async def create_synthesis(
    deliberation_id: UUID,
    payload: DeliberationSynthesisCreate,
    board: Board = BOARD_WRITE_DEP,
    session: AsyncSession = SESSION_DEP,
    actor: ActorContext = ACTOR_DEP,
) -> DeliberationSynthesis:
    """Submit the synthesis for a deliberation, concluding it."""
    deliberation = _get_deliberation_or_404(
        await deliberation_service.get_deliberation(session, deliberation_id, board.id)
    )
    try:
        synthesis = await deliberation_service.submit_synthesis(
            session,
            board=board,
            deliberation=deliberation,
            actor=actor,
            content=payload.content,
            consensus_level=payload.consensus_level,
            confidence=payload.confidence,
            key_points=payload.key_points,
            dissenting_views=payload.dissenting_views,
            tags=payload.tags,
        )
    except DeliberationError as exc:
        raise HTTPException(
            status_code=exc.status_code,
            detail=str(exc),
        ) from exc
    await deliberation_hooks.on_synthesis_submitted(
        session,
        board=board,
        deliberation=deliberation,
        synthesis=synthesis,
        actor=actor,
    )
    return synthesis


@router.post(
    "/{deliberation_id}/synthesis/promote",
    response_model=DeliberationRead,
)
async def promote_synthesis(
    deliberation_id: UUID,
    board: Board = BOARD_WRITE_DEP,
    session: AsyncSession = SESSION_DEP,
    actor: ActorContext = ACTOR_DEP,
) -> DeliberationRead:
    """Promote a deliberation synthesis into board memory."""
    deliberation = _get_deliberation_or_404(
        await deliberation_service.get_deliberation(session, deliberation_id, board.id)
    )
    try:
        memory = await deliberation_service.promote_synthesis(
            session,
            board=board,
            deliberation=deliberation,
            actor=actor,
        )
    except DeliberationError as exc:
        raise HTTPException(
            status_code=exc.status_code,
            detail=str(exc),
        ) from exc
    synthesis = await deliberation_service.get_synthesis(session, deliberation_id)
    if synthesis is not None:
        await deliberation_hooks.on_synthesis_promoted(
            session,
            board=board,
            deliberation=deliberation,
            synthesis=synthesis,
            actor=actor,
        )
    return await _enrich_deliberation_read(session, deliberation)
