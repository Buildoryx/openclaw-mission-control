"""Schemas for episodic memory read API payloads."""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from sqlmodel import SQLModel

RUNTIME_ANNOTATION_TYPES = (datetime, UUID)


class EpisodicMemoryRead(SQLModel):
    """Episodic memory pattern returned from read endpoints."""

    id: UUID
    board_id: UUID
    pattern_type: str
    topic: str | None = None
    deliberation_id: UUID | None = None
    pattern_summary: str
    pattern_details: dict[str, object] | None = None
    outcome_positive: bool = True
    confidence_range: dict[str, object] | None = None
    occurrence_count: int = 1
    success_rate: float | None = None
    reliability_score: float | None = None
    created_at: datetime
    updated_at: datetime


class AgentTrackRecord(SQLModel):
    """Aggregated accuracy summary for an agent across deliberations."""

    agent_id: UUID
    board_id: UUID
    total_positions: int = 0
    accepted_positions: int = 0
    accuracy_rate: float | None = None
    strongest_areas: list[str] | None = None
    weakest_areas: list[str] | None = None
    pattern_count: int = 0
