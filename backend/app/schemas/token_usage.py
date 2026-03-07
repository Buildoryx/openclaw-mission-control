"""Schemas for token usage tracking, ingestion, and dashboard analytics."""

from __future__ import annotations

from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import Field
from sqlmodel import SQLModel

RUNTIME_ANNOTATION_TYPES = (datetime, UUID)

EventKind = Literal[
    "turn",
    "boot",
    "compaction",
    "memory_flush",
    "tool_call",
    "system",
]

RollupBucket = Literal["hour", "day", "week", "month"]
UsageRangeKey = Literal["24h", "3d", "5d", "7d", "14d", "1m"]


# ── Ingestion schemas ────────────────────────────────────────────────────────


class TokenUsageIngestItem(SQLModel):
    """Single token usage record submitted by the gateway or agent runtime."""

    model: str = Field(
        description="LLM model identifier, e.g. 'claude-sonnet-4' or 'gpt-4.1'.",
        examples=["claude-sonnet-4"],
    )
    model_provider: str | None = Field(
        default=None,
        description="Provider name, e.g. 'anthropic', 'openai'.",
        examples=["anthropic"],
    )
    input_tokens: int = Field(
        ge=0,
        description="Number of input (prompt) tokens consumed.",
        examples=[4200],
    )
    output_tokens: int = Field(
        ge=0,
        description="Number of output (completion) tokens produced.",
        examples=[1800],
    )
    total_tokens: int | None = Field(
        default=None,
        description="Total tokens. Computed as input + output if omitted.",
        examples=[6000],
    )

    input_cost_microcents: int | None = Field(
        default=None,
        description="Input cost in USD micro-cents (1 USD = 100_000_000 micro-cents).",
    )
    output_cost_microcents: int | None = Field(
        default=None,
        description="Output cost in USD micro-cents.",
    )
    total_cost_microcents: int | None = Field(
        default=None,
        description="Total cost in USD micro-cents. Computed if omitted.",
    )

    event_kind: EventKind = Field(
        default="turn",
        description="Classification of the usage event.",
        examples=["turn"],
    )
    session_id: str | None = Field(
        default=None,
        description="OpenClaw session identifier for the originating session.",
        examples=["session-abc-123"],
    )
    gateway_id: str | None = Field(
        default=None,
        description="Gateway UUID as string. Resolved server-side if omitted.",
    )
    agent_id: str | None = Field(
        default=None,
        description="Agent UUID as string.",
    )
    board_id: str | None = Field(
        default=None,
        description="Board UUID as string.",
    )
    note: str | None = Field(
        default=None,
        description="Optional human-readable annotation for this event.",
        examples=["Boot context load for session start"],
    )
    event_at: datetime | None = Field(
        default=None,
        description="Timestamp of the event. Defaults to server receipt time if omitted.",
    )


class TokenUsageIngestRequest(SQLModel):
    """Batch ingest request for one or more token usage events."""

    events: list[TokenUsageIngestItem] = Field(
        description="List of token usage events to record.",
        min_length=1,
    )


class TokenUsageIngestResponse(SQLModel):
    """Response from a token usage ingest operation."""

    ok: bool = True
    ingested: int = Field(
        description="Number of events successfully persisted.",
        examples=[5],
    )


# ── Read / analytics schemas ─────────────────────────────────────────────────


class TokenUsageEventRead(SQLModel):
    """Single token usage event returned by the API."""

    id: UUID
    organization_id: UUID
    gateway_id: UUID | None = None
    agent_id: UUID | None = None
    board_id: UUID | None = None
    session_id: str | None = None
    model: str
    model_provider: str | None = None
    input_tokens: int
    output_tokens: int
    total_tokens: int
    input_cost_microcents: int | None = None
    output_cost_microcents: int | None = None
    total_cost_microcents: int | None = None
    event_kind: str
    note: str | None = None
    event_at: datetime
    created_at: datetime


class TokenUsageDailyRollup(SQLModel):
    """Aggregated token usage for a single day and model combination."""

    date: str = Field(
        description="Date string in YYYY-MM-DD format.",
        examples=["2025-07-14"],
    )
    model: str = Field(
        description="LLM model identifier.",
        examples=["claude-sonnet-4"],
    )
    model_provider: str | None = Field(
        default=None,
        description="Provider name.",
    )
    input_tokens: int = Field(
        description="Total input tokens for this date/model.",
    )
    output_tokens: int = Field(
        description="Total output tokens for this date/model.",
    )
    total_tokens: int = Field(
        description="Total tokens for this date/model.",
    )
    input_cost_microcents: int = Field(
        default=0,
        description="Total input cost in micro-cents.",
    )
    output_cost_microcents: int = Field(
        default=0,
        description="Total output cost in micro-cents.",
    )
    total_cost_microcents: int = Field(
        default=0,
        description="Total cost in micro-cents.",
    )
    event_count: int = Field(
        description="Number of usage events aggregated.",
    )
    session_count: int = Field(
        description="Distinct session count for this date/model.",
    )


class TokenUsageModelBreakdown(SQLModel):
    """Aggregated usage across the full query range for a single model."""

    model: str
    model_provider: str | None = None
    input_tokens: int
    output_tokens: int
    total_tokens: int
    input_cost_microcents: int = 0
    output_cost_microcents: int = 0
    total_cost_microcents: int = 0
    event_count: int
    session_count: int
    share_pct: float = Field(
        description="Percentage share of total tokens across all models.",
        examples=[45.2],
    )


class TokenUsageByKind(SQLModel):
    """Aggregated usage for a single event_kind across the query range."""

    event_kind: str
    input_tokens: int
    output_tokens: int
    total_tokens: int
    event_count: int
    share_pct: float = Field(
        description="Percentage share of total tokens across all kinds.",
        examples=[62.5],
    )


class TokenUsageSummaryKpis(SQLModel):
    """Top-level KPI values for the token usage dashboard."""

    total_input_tokens: int
    total_output_tokens: int
    total_tokens: int
    total_cost_microcents: int
    total_events: int
    total_sessions: int
    avg_tokens_per_session: float
    avg_tokens_per_event: float


class TokenUsageDashboard(SQLModel):
    """Complete token usage dashboard response payload."""

    range: UsageRangeKey = Field(
        description="Time range used for the query.",
    )
    generated_at: datetime
    kpis: TokenUsageSummaryKpis
    daily_rollup: list[TokenUsageDailyRollup]
    by_model: list[TokenUsageModelBreakdown]
    by_kind: list[TokenUsageByKind]


class TokenUsageRecentEvents(SQLModel):
    """Paginated list of recent token usage events."""

    total: int
    events: list[TokenUsageEventRead]
