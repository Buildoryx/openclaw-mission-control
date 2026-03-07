"""Episodic memory model for pattern learning from past deliberations."""

from __future__ import annotations

from datetime import datetime
from uuid import UUID, uuid4

from sqlalchemy import JSON, Column, Index, Text
from sqlmodel import Field

from app.core.time import utcnow
from app.models.base import QueryModel

RUNTIME_ANNOTATION_TYPES = (datetime,)


class EpisodicMemory(QueryModel, table=True):
    """Learned pattern extracted from concluded deliberations.

    Episodic memories capture recurring patterns, agent accuracy profiles,
    and topic-level insights that emerge over multiple deliberation cycles.
    They enable agents to recall what worked (and what didn't) when
    approaching similar topics in the future.
    """

    __tablename__ = "episodic_memory"  # pyright: ignore[reportAssignmentType]
    __table_args__ = (
        Index("ix_episodic_memory_board_pattern", "board_id", "pattern_type"),
        Index("ix_episodic_memory_board_topic", "board_id", "topic"),
        Index("ix_episodic_memory_deliberation", "deliberation_id"),
    )

    id: UUID = Field(default_factory=uuid4, primary_key=True)
    board_id: UUID = Field(foreign_key="boards.id", index=True)

    # Pattern classification
    pattern_type: str = Field(
        index=True,
        description=(
            "Category of learned pattern: deliberation_outcome, "
            "consensus_pattern, agent_accuracy, topic_pattern"
        ),
    )

    # Human-readable summary of the pattern (required, placed before optional fields)
    pattern_summary: str = Field(sa_column=Column(Text, nullable=False))

    # Optional fields below ------------------------------------------------

    topic: str | None = Field(default=None)

    # Link back to the source deliberation (if applicable)
    deliberation_id: UUID | None = Field(default=None, foreign_key="deliberations.id")

    # Structured pattern data (schema varies by pattern_type)
    pattern_details: dict[str, object] | None = Field(
        default=None, sa_column=Column(JSON)
    )

    # Outcome tracking
    outcome_positive: bool = Field(default=True)
    confidence_range: dict[str, object] | None = Field(
        default=None,
        sa_column=Column(JSON),
        description="JSON object with 'low' and 'high' float fields",
    )

    # Statistical metadata
    occurrence_count: int = Field(default=1)
    success_rate: float | None = Field(default=None)
    reliability_score: float | None = Field(default=None)

    # pgvector embedding for semantic search over episodic memories
    # Column type is added via migration; kept as None-typed here for
    # portability when pgvector is not installed.
    embedding: list[float] | None = Field(default=None, sa_column=Column(JSON))

    created_at: datetime = Field(default_factory=utcnow)
    updated_at: datetime = Field(default_factory=utcnow)
