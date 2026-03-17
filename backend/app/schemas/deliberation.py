"""Schemas for deliberation create/read/update API payloads."""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from pydantic import Field
from sqlmodel import SQLModel
from sqlmodel._compat import SQLModelConfig

from app.schemas.common import NonEmptyStr

RUNTIME_ANNOTATION_TYPES = (datetime, UUID, NonEmptyStr)


class DeliberationCreate(SQLModel):
    """Payload for starting a new deliberation."""

    model_config = SQLModelConfig(
        json_schema_extra={
            "x-llm-intent": "deliberation_create",
            "x-when-to-use": [
                "Start a structured deliberation among board agents",
                "Initiate debate on a topic that requires multi-agent consensus",
            ],
            "x-when-not-to-use": [
                "Simple questions that don't need debate (use board chat)",
                "Task status changes (use task endpoints)",
            ],
            "x-required-actor": "lead_or_worker_agent",
            "x-prerequisites": ["board_id from URL path"],
            "x-response-shape": "DeliberationRead",
            "x-side-effects": [
                "Creates a new deliberation session",
                "Notifies participating agents via gateway dispatch",
                "Records activity event",
            ],
        },
    )

    topic: NonEmptyStr = Field(
        description="Subject of the deliberation.",
        examples=["Should we refactor the authentication module?"],
    )
    trigger_reason: str | None = Field(
        default=None,
        description="Why this deliberation was triggered.",
        examples=["divergent_positions", "auto_review_deliberation", "manual"],
    )
    task_id: UUID | None = Field(
        default=None,
        description="Optional task that triggered this deliberation.",
    )
    max_turns: int | None = Field(
        default=None,
        ge=2,
        le=50,
        description="Override the default maximum number of turns.",
    )


class DeliberationUpdate(SQLModel):
    """Payload for updating deliberation state (phase advance, abandon)."""

    model_config = SQLModelConfig(
        json_schema_extra={
            "x-llm-intent": "deliberation_update",
            "x-when-to-use": [
                "Advance a deliberation to the next phase",
                "Abandon a deliberation that is no longer relevant",
            ],
        },
    )

    status: str | None = Field(
        default=None,
        description="Target status for the deliberation.",
        examples=["discussing", "verifying", "synthesizing", "abandoned"],
    )
    reason: str | None = Field(
        default=None,
        description="Reason for the status change (required for abandon).",
    )


class DeliberationRead(SQLModel):
    """Full deliberation representation returned from read endpoints."""

    model_config = SQLModelConfig(
        json_schema_extra={
            "x-llm-intent": "deliberation_lookup",
            "x-when-to-use": [
                "Inspect current deliberation state and progress",
                "Check deliberation outcome and synthesis",
            ],
        },
    )

    id: UUID
    board_id: UUID
    topic: str
    status: str
    initiated_by_agent_id: UUID | None = None
    synthesizer_agent_id: UUID | None = None
    trigger_reason: str | None = None
    task_id: UUID | None = None
    parent_deliberation_id: UUID | None = None
    max_turns: int = 6
    outcome_changed: bool = False
    confidence_delta: float | None = None
    duration_ms: float | None = None
    approval_id: UUID | None = None
    entry_count: int = Field(default=0, description="Total number of entries.")
    has_synthesis: bool = Field(
        default=False,
        description="Whether a synthesis has been submitted.",
    )
    created_at: datetime
    concluded_at: datetime | None = None
    updated_at: datetime


class DeliberationEntryCreate(SQLModel):
    """Payload for contributing an entry to a deliberation."""

    model_config = SQLModelConfig(
        json_schema_extra={
            "x-llm-intent": "deliberation_entry_create",
            "x-when-to-use": [
                "Submit a thesis, antithesis, evidence, or vote",
                "Contribute an argument to an ongoing deliberation",
            ],
            "x-required-actor": "lead_or_worker_agent",
            "x-response-shape": "DeliberationEntryRead",
            "x-side-effects": [
                "Creates a new deliberation entry",
                "May auto-advance deliberation phase",
                "Streams entry to SSE subscribers",
                "Records activity event",
            ],
        },
    )

    content: NonEmptyStr = Field(
        description="The argument, evidence, question, or vote content.",
        examples=["I believe we should adopt JWT-based auth because..."],
    )
    phase: str = Field(
        description="Deliberation phase this entry belongs to.",
        examples=["debate", "discussion", "verification"],
    )
    entry_type: str = Field(
        description="Classification of this entry.",
        examples=["thesis", "antithesis", "evidence", "question", "vote", "rebuttal"],
    )
    position: str | None = Field(
        default=None,
        description="Agent's stance on the topic (optional for evidence/questions).",
        examples=["Support refactoring", "Oppose refactoring"],
    )
    confidence: float | None = Field(
        default=None,
        ge=0.0,
        le=1.0,
        description="Agent's confidence in their position (0.0–1.0).",
    )
    parent_entry_id: UUID | None = Field(
        default=None,
        description="Parent entry ID for threaded replies.",
    )
    references: list[str] | None = Field(
        default=None,
        description="Links to board memory IDs, task IDs, or external URLs.",
    )
    entry_metadata: dict[str, object] | None = Field(
        default=None,
        description="Agent-specific structured data (rubric scores, quality ratings, etc.).",
    )


class DeliberationEntryRead(SQLModel):
    """Full entry representation returned from read endpoints."""

    id: UUID
    deliberation_id: UUID
    sequence: int
    phase: str
    entry_type: str
    agent_id: UUID | None = None
    user_id: UUID | None = None
    position: str | None = None
    confidence: float | None = None
    content: str
    parent_entry_id: UUID | None = None
    references: list[str] | None = None
    entry_metadata: dict[str, object] | None = None
    created_at: datetime


class DeliberationSynthesisCreate(SQLModel):
    """Payload for submitting a deliberation synthesis."""

    model_config = SQLModelConfig(
        json_schema_extra={
            "x-llm-intent": "deliberation_synthesis_create",
            "x-when-to-use": [
                "Submit the final synthesized conclusion of a deliberation",
            ],
            "x-required-actor": "lead_or_synthesizer_agent",
            "x-response-shape": "DeliberationSynthesisRead",
            "x-side-effects": [
                "Concludes the deliberation",
                "May trigger approval flow",
                "May auto-promote to board memory",
            ],
        },
    )

    content: NonEmptyStr = Field(
        description="The synthesized finding.",
        examples=["After reviewing all positions, the consensus is to proceed with..."],
    )
    consensus_level: str = Field(
        description="Level of agreement reached.",
        examples=["unanimous", "majority", "contested", "split"],
    )
    key_points: list[str] | None = Field(
        default=None,
        description="Extracted bullet points from the deliberation.",
    )
    dissenting_views: list[str] | None = Field(
        default=None,
        description="Captured minority opinions.",
    )
    confidence: float = Field(
        ge=0.0,
        le=1.0,
        description="Aggregate confidence in the synthesis (0.0–1.0).",
    )
    tags: list[str] | None = Field(
        default=None,
        description="Tags for categorization and recall.",
    )


class DeliberationSynthesisRead(SQLModel):
    """Full synthesis representation returned from read endpoints."""

    id: UUID
    deliberation_id: UUID
    synthesized_by_agent_id: UUID | None = None
    content: str
    consensus_level: str
    key_points: list[str] | None = None
    dissenting_views: list[str] | None = None
    confidence: float
    tags: list[str] | None = None
    promoted_to_memory: bool = False
    board_memory_id: UUID | None = None
    created_at: datetime
