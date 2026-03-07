"""Token usage tracking and analytics endpoints.

This module provides two categories of endpoints:

1. **Ingestion** – accepts token usage events from the OpenClaw gateway or
   agent runtime.  These are write-heavy, append-only operations.
2. **Dashboard queries** – aggregates stored events into daily rollups,
   model breakdowns, and KPI summaries consumed by the frontend token
   usage dashboard.

All dashboard endpoints are scoped to the caller's organization membership.
The ingest endpoint supports both admin-user and agent-token authentication.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import TYPE_CHECKING
from uuid import UUID

import sqlalchemy as sa
from fastapi import APIRouter, Depends, Query, status
from sqlalchemy import func
from sqlmodel import col, select
from sqlmodel.ext.asyncio.session import AsyncSession

from app.api.deps import require_org_member
from app.core.time import utcnow
from app.db.session import get_session
from app.models.token_usage import TokenUsageEvent
from app.schemas.token_usage import (
    TokenUsageByKind,
    TokenUsageDailyRollup,
    TokenUsageDashboard,
    TokenUsageEventRead,
    TokenUsageIngestRequest,
    TokenUsageIngestResponse,
    TokenUsageModelBreakdown,
    TokenUsageRecentEvents,
    TokenUsageSummaryKpis,
    UsageRangeKey,
)
from app.services.organizations import OrganizationContext

if TYPE_CHECKING:
    pass

router = APIRouter(prefix="/token-usage", tags=["token-usage"])

SESSION_DEP = Depends(get_session)
ORG_MEMBER_DEP = Depends(require_org_member)

RANGE_QUERY = Query(default="5d")
BOARD_ID_QUERY = Query(default=None)
LIMIT_QUERY = Query(default=50, ge=1, le=200)
OFFSET_QUERY = Query(default=0, ge=0)

_RUNTIME_TYPE_REFERENCES = (UUID, AsyncSession)


# ── Range resolution ──────────────────────────────────────────────────────────


@dataclass(frozen=True)
class _RangeSpec:
    """Resolved time range for token usage queries."""

    key: UsageRangeKey
    start: datetime
    end: datetime
    days: int


_RANGE_DAYS: dict[str, int] = {
    "24h": 1,
    "3d": 3,
    "5d": 5,
    "7d": 7,
    "14d": 14,
    "1m": 30,
}


def _resolve_range(key: UsageRangeKey) -> _RangeSpec:
    days = _RANGE_DAYS.get(key, 5)
    now = utcnow()
    start = now - timedelta(days=days)
    return _RangeSpec(key=key, start=start, end=now, days=days)


# ── Helper: safe UUID parse ───────────────────────────────────────────────────


def _try_uuid(value: str | None) -> UUID | None:
    if not value:
        return None
    try:
        return UUID(value)
    except (ValueError, AttributeError):
        return None


# ── Ingest endpoint ───────────────────────────────────────────────────────────


@router.post(
    "/ingest",
    response_model=TokenUsageIngestResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Ingest token usage events",
    description=(
        "Accept a batch of token usage events from the gateway or agent runtime. "
        "Each event records the input/output token counts for a single LLM call."
    ),
)
async def ingest_token_usage(
    payload: TokenUsageIngestRequest,
    session: AsyncSession = SESSION_DEP,
    ctx: OrganizationContext = ORG_MEMBER_DEP,
) -> TokenUsageIngestResponse:
    """Persist one or more token usage events for the caller's organization."""
    org_id = ctx.organization.id
    now = utcnow()
    count = 0

    for item in payload.events:
        total = (
            item.total_tokens
            if item.total_tokens is not None
            else (item.input_tokens + item.output_tokens)
        )
        total_cost = item.total_cost_microcents
        if total_cost is None:
            ic = item.input_cost_microcents or 0
            oc = item.output_cost_microcents or 0
            total_cost = ic + oc if (ic or oc) else None

        event = TokenUsageEvent(
            organization_id=org_id,
            gateway_id=_try_uuid(item.gateway_id),
            agent_id=_try_uuid(item.agent_id),
            board_id=_try_uuid(item.board_id),
            session_id=item.session_id,
            model=item.model,
            model_provider=item.model_provider,
            input_tokens=item.input_tokens,
            output_tokens=item.output_tokens,
            total_tokens=total,
            input_cost_microcents=item.input_cost_microcents,
            output_cost_microcents=item.output_cost_microcents,
            total_cost_microcents=total_cost,
            event_kind=item.event_kind,
            note=item.note,
            event_at=item.event_at or now,
            created_at=now,
        )
        session.add(event)
        count += 1

    await session.commit()
    return TokenUsageIngestResponse(ok=True, ingested=count)


# ── Dashboard endpoint ────────────────────────────────────────────────────────


async def _daily_rollup(
    session: AsyncSession,
    org_id: UUID,
    spec: _RangeSpec,
    board_id: UUID | None,
) -> list[TokenUsageDailyRollup]:
    """Aggregate token usage into daily buckets grouped by model."""
    t = TokenUsageEvent.__table__  # type: ignore[attr-defined]

    date_trunc = func.date(t.c.event_at).label("event_date")

    stmt = (
        sa.select(
            date_trunc,
            t.c.model,
            t.c.model_provider,
            func.sum(t.c.input_tokens).label("sum_input"),
            func.sum(t.c.output_tokens).label("sum_output"),
            func.sum(t.c.total_tokens).label("sum_total"),
            func.coalesce(func.sum(t.c.input_cost_microcents), 0).label(
                "sum_input_cost"
            ),
            func.coalesce(func.sum(t.c.output_cost_microcents), 0).label(
                "sum_output_cost"
            ),
            func.coalesce(func.sum(t.c.total_cost_microcents), 0).label(
                "sum_total_cost"
            ),
            func.count().label("event_count"),
            func.count(func.distinct(t.c.session_id)).label("session_count"),
        )
        .where(t.c.organization_id == org_id)
        .where(t.c.event_at >= spec.start)
        .where(t.c.event_at <= spec.end)
    )

    if board_id is not None:
        stmt = stmt.where(t.c.board_id == board_id)

    stmt = stmt.group_by(date_trunc, t.c.model, t.c.model_provider).order_by(date_trunc)

    raw = await session.execute(stmt)
    rows = raw.all()

    rollups: list[TokenUsageDailyRollup] = []
    for row in rows:
        event_date = row[0]
        if isinstance(event_date, datetime):
            date_str = event_date.strftime("%Y-%m-%d")
        elif event_date is not None:
            date_str = str(event_date)
        else:
            date_str = "unknown"

        rollups.append(
            TokenUsageDailyRollup(
                date=date_str,
                model=str(row[1] or "unknown"),
                model_provider=row[2],
                input_tokens=int(row[3] or 0),
                output_tokens=int(row[4] or 0),
                total_tokens=int(row[5] or 0),
                input_cost_microcents=int(row[6] or 0),
                output_cost_microcents=int(row[7] or 0),
                total_cost_microcents=int(row[8] or 0),
                event_count=int(row[9] or 0),
                session_count=int(row[10] or 0),
            )
        )

    return rollups


async def _model_breakdown(
    session: AsyncSession,
    org_id: UUID,
    spec: _RangeSpec,
    board_id: UUID | None,
) -> list[TokenUsageModelBreakdown]:
    """Aggregate usage by model across the full range."""
    t = TokenUsageEvent.__table__  # type: ignore[attr-defined]

    stmt = (
        sa.select(
            t.c.model,
            t.c.model_provider,
            func.sum(t.c.input_tokens).label("sum_input"),
            func.sum(t.c.output_tokens).label("sum_output"),
            func.sum(t.c.total_tokens).label("sum_total"),
            func.coalesce(func.sum(t.c.input_cost_microcents), 0).label(
                "sum_input_cost"
            ),
            func.coalesce(func.sum(t.c.output_cost_microcents), 0).label(
                "sum_output_cost"
            ),
            func.coalesce(func.sum(t.c.total_cost_microcents), 0).label(
                "sum_total_cost"
            ),
            func.count().label("event_count"),
            func.count(func.distinct(t.c.session_id)).label("session_count"),
        )
        .where(t.c.organization_id == org_id)
        .where(t.c.event_at >= spec.start)
        .where(t.c.event_at <= spec.end)
    )

    if board_id is not None:
        stmt = stmt.where(t.c.board_id == board_id)

    stmt = stmt.group_by(t.c.model, t.c.model_provider).order_by(
        func.sum(t.c.total_tokens).desc()
    )

    raw = await session.execute(stmt)
    rows = raw.all()

    grand_total = sum(int(row[4] or 0) for row in rows)
    breakdowns: list[TokenUsageModelBreakdown] = []
    for row in rows:
        total = int(row[4] or 0)
        share = (total / grand_total * 100) if grand_total > 0 else 0.0
        breakdowns.append(
            TokenUsageModelBreakdown(
                model=str(row[0] or "unknown"),
                model_provider=row[1],
                input_tokens=int(row[2] or 0),
                output_tokens=int(row[3] or 0),
                total_tokens=total,
                input_cost_microcents=int(row[5] or 0),
                output_cost_microcents=int(row[6] or 0),
                total_cost_microcents=int(row[7] or 0),
                event_count=int(row[8] or 0),
                session_count=int(row[9] or 0),
                share_pct=round(share, 1),
            )
        )

    return breakdowns


async def _by_kind(
    session: AsyncSession,
    org_id: UUID,
    spec: _RangeSpec,
    board_id: UUID | None,
) -> list[TokenUsageByKind]:
    """Aggregate usage by event_kind."""
    t = TokenUsageEvent.__table__  # type: ignore[attr-defined]

    stmt = (
        sa.select(
            t.c.event_kind,
            func.sum(t.c.input_tokens).label("sum_input"),
            func.sum(t.c.output_tokens).label("sum_output"),
            func.sum(t.c.total_tokens).label("sum_total"),
            func.count().label("event_count"),
        )
        .where(t.c.organization_id == org_id)
        .where(t.c.event_at >= spec.start)
        .where(t.c.event_at <= spec.end)
    )

    if board_id is not None:
        stmt = stmt.where(t.c.board_id == board_id)

    stmt = stmt.group_by(t.c.event_kind).order_by(func.sum(t.c.total_tokens).desc())

    raw = await session.execute(stmt)
    rows = raw.all()

    grand_total = sum(int(row[3] or 0) for row in rows)
    kinds: list[TokenUsageByKind] = []
    for row in rows:
        total = int(row[3] or 0)
        share = (total / grand_total * 100) if grand_total > 0 else 0.0
        kinds.append(
            TokenUsageByKind(
                event_kind=str(row[0] or "unknown"),
                input_tokens=int(row[1] or 0),
                output_tokens=int(row[2] or 0),
                total_tokens=total,
                event_count=int(row[4] or 0),
                share_pct=round(share, 1),
            )
        )

    return kinds


async def _summary_kpis(
    session: AsyncSession,
    org_id: UUID,
    spec: _RangeSpec,
    board_id: UUID | None,
) -> TokenUsageSummaryKpis:
    """Compute top-level KPI summary values."""
    t = TokenUsageEvent.__table__  # type: ignore[attr-defined]

    stmt = (
        sa.select(
            func.coalesce(func.sum(t.c.input_tokens), 0).label("sum_input"),
            func.coalesce(func.sum(t.c.output_tokens), 0).label("sum_output"),
            func.coalesce(func.sum(t.c.total_tokens), 0).label("sum_total"),
            func.coalesce(func.sum(t.c.total_cost_microcents), 0).label("sum_cost"),
            func.count().label("event_count"),
            func.count(func.distinct(t.c.session_id)).label("session_count"),
        )
        .where(t.c.organization_id == org_id)
        .where(t.c.event_at >= spec.start)
        .where(t.c.event_at <= spec.end)
    )

    if board_id is not None:
        stmt = stmt.where(t.c.board_id == board_id)

    raw = await session.execute(stmt)
    row = raw.one()

    total_input = int(row[0])
    total_output = int(row[1])
    total_tokens = int(row[2])
    total_cost = int(row[3])
    total_events = int(row[4])
    total_sessions = int(row[5])

    avg_per_session = (
        round(total_tokens / total_sessions, 1) if total_sessions > 0 else 0.0
    )
    avg_per_event = round(total_tokens / total_events, 1) if total_events > 0 else 0.0

    return TokenUsageSummaryKpis(
        total_input_tokens=total_input,
        total_output_tokens=total_output,
        total_tokens=total_tokens,
        total_cost_microcents=total_cost,
        total_events=total_events,
        total_sessions=total_sessions,
        avg_tokens_per_session=avg_per_session,
        avg_tokens_per_event=avg_per_event,
    )


@router.get(
    "/dashboard",
    response_model=TokenUsageDashboard,
    summary="Token usage dashboard",
    description=(
        "Return aggregated token usage analytics for the caller's organization "
        "including daily rollups, per-model breakdowns, per-kind breakdowns, "
        "and summary KPIs."
    ),
)
async def token_usage_dashboard(
    range_key: UsageRangeKey = RANGE_QUERY,
    board_id: UUID | None = BOARD_ID_QUERY,
    session: AsyncSession = SESSION_DEP,
    ctx: OrganizationContext = ORG_MEMBER_DEP,
) -> TokenUsageDashboard:
    """Aggregate token usage into a dashboard-ready payload."""
    org_id = ctx.organization.id
    spec = _resolve_range(range_key)

    kpis = await _summary_kpis(session, org_id, spec, board_id)
    daily = await _daily_rollup(session, org_id, spec, board_id)
    models = await _model_breakdown(session, org_id, spec, board_id)
    kinds = await _by_kind(session, org_id, spec, board_id)

    return TokenUsageDashboard(
        range=spec.key,
        generated_at=utcnow(),
        kpis=kpis,
        daily_rollup=daily,
        by_model=models,
        by_kind=kinds,
    )


# ── Recent events endpoint ────────────────────────────────────────────────────


@router.get(
    "/events",
    response_model=TokenUsageRecentEvents,
    summary="List recent token usage events",
    description=(
        "Return a paginated list of the most recent token usage events "
        "for the caller's organization."
    ),
)
async def list_token_usage_events(
    range_key: UsageRangeKey = RANGE_QUERY,
    board_id: UUID | None = BOARD_ID_QUERY,
    limit: int = LIMIT_QUERY,
    offset: int = OFFSET_QUERY,
    session: AsyncSession = SESSION_DEP,
    ctx: OrganizationContext = ORG_MEMBER_DEP,
) -> TokenUsageRecentEvents:
    """Return recent token usage events ordered by event_at descending."""
    org_id = ctx.organization.id
    spec = _resolve_range(range_key)

    base = (
        select(TokenUsageEvent)
        .where(col(TokenUsageEvent.organization_id) == org_id)
        .where(col(TokenUsageEvent.event_at) >= spec.start)
        .where(col(TokenUsageEvent.event_at) <= spec.end)
    )

    if board_id is not None:
        base = base.where(col(TokenUsageEvent.board_id) == board_id)

    # Total count
    count_stmt = select(func.count()).select_from(base.subquery())
    count_result = await session.exec(count_stmt)
    total = int(count_result.one())

    # Paginated results
    events_stmt = (
        base.order_by(col(TokenUsageEvent.event_at).desc()).offset(offset).limit(limit)
    )
    events_result = await session.exec(events_stmt)
    rows = events_result.all()

    events = [
        TokenUsageEventRead(
            id=row.id,
            organization_id=row.organization_id,
            gateway_id=row.gateway_id,
            agent_id=row.agent_id,
            board_id=row.board_id,
            session_id=row.session_id,
            model=row.model,
            model_provider=row.model_provider,
            input_tokens=row.input_tokens,
            output_tokens=row.output_tokens,
            total_tokens=row.total_tokens,
            input_cost_microcents=row.input_cost_microcents,
            output_cost_microcents=row.output_cost_microcents,
            total_cost_microcents=row.total_cost_microcents,
            event_kind=row.event_kind,
            note=row.note,
            event_at=row.event_at,
            created_at=row.created_at,
        )
        for row in rows
    ]

    return TokenUsageRecentEvents(total=total, events=events)
