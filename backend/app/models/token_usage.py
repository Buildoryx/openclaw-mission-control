"""Token usage event model for tracking LLM token consumption per turn."""

from __future__ import annotations

from datetime import datetime
from uuid import UUID, uuid4

from sqlalchemy import Column, Text
from sqlmodel import Field

from app.core.time import utcnow
from app.models.base import QueryModel

RUNTIME_ANNOTATION_TYPES = (datetime,)


class TokenUsageEvent(QueryModel, table=True):
    """Discrete token usage record tied to an agent session turn or system event.

    Each row captures the input/output token counts for a single LLM call,
    along with metadata about the model, session, and originating context.
    These records are ingested from the OpenClaw gateway and aggregated by
    the token-usage dashboard endpoints.
    """

    __tablename__ = "token_usage_events"  # pyright: ignore[reportAssignmentType]

    id: UUID = Field(default_factory=uuid4, primary_key=True)

    # ── Organization scoping ──────────────────────────────────────────────
    organization_id: UUID = Field(foreign_key="organizations.id", index=True)

    # ── Gateway / agent context ───────────────────────────────────────────
    gateway_id: UUID | None = Field(default=None, foreign_key="gateways.id", index=True)
    agent_id: UUID | None = Field(default=None, foreign_key="agents.id", index=True)
    board_id: UUID | None = Field(default=None, foreign_key="boards.id", index=True)

    # ── Session identification ────────────────────────────────────────────
    session_id: str | None = Field(default=None, index=True)

    # ── Model metadata ────────────────────────────────────────────────────
    model: str = Field(index=True)
    model_provider: str | None = Field(default=None, index=True)

    # ── Token counts ──────────────────────────────────────────────────────
    input_tokens: int = Field(default=0)
    output_tokens: int = Field(default=0)
    total_tokens: int = Field(default=0)

    # ── Optional cost tracking (USD micro-cents for precision) ────────────
    input_cost_microcents: int | None = Field(default=None)
    output_cost_microcents: int | None = Field(default=None)
    total_cost_microcents: int | None = Field(default=None)

    # ── Event classification ──────────────────────────────────────────────
    event_kind: str = Field(
        default="turn",
        index=True,
    )
    """Classification of the token usage event.

    Known values:
    - ``turn``        – standard conversation turn (user prompt → assistant reply)
    - ``boot``        – session bootstrap / context loading
    - ``compaction``  – context compaction / pruning cycle
    - ``memory_flush``– pre-compaction memory checkpoint
    - ``tool_call``   – standalone tool invocation
    - ``system``      – system-level overhead (e.g. function schema injection)
    """

    # ── Free-form annotation ──────────────────────────────────────────────
    note: str | None = Field(default=None, sa_column=Column(Text))

    # ── Timestamps ────────────────────────────────────────────────────────
    event_at: datetime = Field(default_factory=utcnow, index=True)
    created_at: datetime = Field(default_factory=utcnow)
