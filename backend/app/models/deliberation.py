"""Deliberation models for structured agent debate, discussion, and synthesis."""

from __future__ import annotations

from datetime import datetime
from uuid import UUID, uuid4

from sqlalchemy import JSON, Column, Index, Text
from sqlmodel import Field

from app.core.time import utcnow
from app.models.base import QueryModel

RUNTIME_ANNOTATION_TYPES = (datetime,)

# Valid deliberation statuses
DELIBERATION_STATUSES = frozenset(
    {
        "created",
        "debating",
        "discussing",
        "verifying",
        "synthesizing",
        "concluded",
        "abandoned",
    }
)

# Terminal statuses that cannot transition further
TERMINAL_STATUSES = frozenset({"concluded", "abandoned"})

# Valid phase progression order
PHASE_ORDER: list[str] = [
    "created",
    "debating",
    "discussing",
    "verifying",
    "synthesizing",
    "concluded",
]

# Valid entry types per phase
ENTRY_TYPES = frozenset(
    {
        "thesis",
        "antithesis",
        "evidence",
        "question",
        "vote",
        "rebuttal",
        "synthesis",
    }
)

# Valid consensus levels
CONSENSUS_LEVELS = frozenset(
    {
        "unanimous",
        "majority",
        "contested",
        "split",
    }
)


class Deliberation(QueryModel, table=True):
    """A structured deliberation session attached to a board.

    Deliberations progress through phases: created → debating → discussing →
    verifying → synthesizing → concluded.  They can be abandoned from any
    non-terminal phase.
    """

    __tablename__ = "deliberations"  # pyright: ignore[reportAssignmentType]
    __table_args__ = (
        Index("ix_deliberations_board_status", "board_id", "status"),
        Index("ix_deliberations_board_created", "board_id", "created_at"),
    )

    id: UUID = Field(default_factory=uuid4, primary_key=True)
    board_id: UUID = Field(foreign_key="boards.id", index=True)
    topic: str = Field(sa_column=Column(Text, nullable=False))
    status: str = Field(default="created", index=True)

    # Agent references
    initiated_by_agent_id: UUID | None = Field(default=None, foreign_key="agents.id")
    synthesizer_agent_id: UUID | None = Field(default=None, foreign_key="agents.id")

    # Trigger context
    trigger_reason: str | None = None
    task_id: UUID | None = Field(default=None, foreign_key="tasks.id", index=True)
    parent_deliberation_id: UUID | None = Field(
        default=None, foreign_key="deliberations.id"
    )

    # Configuration
    max_turns: int = Field(default=6)

    # Outcome tracking
    outcome_changed: bool = Field(default=False)
    confidence_delta: float | None = None
    duration_ms: float | None = None

    # Approval integration
    approval_id: UUID | None = Field(default=None, foreign_key="approvals.id")

    # Timestamps
    created_at: datetime = Field(default_factory=utcnow)
    concluded_at: datetime | None = None
    updated_at: datetime = Field(default_factory=utcnow)


class DeliberationEntry(QueryModel, table=True):
    """A single contribution within a deliberation.

    Entries are the normalized replacement for the JSONB ``turns`` array in the
    source ``debate_chains`` model.  Each entry captures an agent's (or user's)
    position, argument, and metadata within a specific deliberation phase.
    """

    __tablename__ = "deliberation_entries"  # pyright: ignore[reportAssignmentType]
    __table_args__ = (
        Index(
            "ix_delib_entries_delib_seq",
            "deliberation_id",
            "sequence",
        ),
        Index(
            "ix_delib_entries_delib_phase",
            "deliberation_id",
            "phase",
        ),
        Index(
            "ix_delib_entries_agent_created",
            "agent_id",
            "created_at",
        ),
    )

    id: UUID = Field(default_factory=uuid4, primary_key=True)
    deliberation_id: UUID = Field(foreign_key="deliberations.id", index=True)
    sequence: int = Field(default=0)

    # Phase and type classification
    phase: str  # debate, discussion, verification
    entry_type: str  # thesis, antithesis, evidence, question, vote, rebuttal, synthesis

    # Author — exactly one of agent_id / user_id should be set
    agent_id: UUID | None = Field(default=None, foreign_key="agents.id")
    user_id: UUID | None = Field(default=None, foreign_key="users.id")

    # Content
    position: str | None = None  # free-form stance (optional for evidence/questions)
    confidence: float | None = Field(default=None, ge=0.0, le=1.0)
    content: str = Field(sa_column=Column(Text, nullable=False))

    # Threading
    parent_entry_id: UUID | None = Field(
        default=None, foreign_key="deliberation_entries.id"
    )

    # Structured references and metadata
    references: list[str] | None = Field(default=None, sa_column=Column(JSON))
    metadata_: dict[str, object] | None = Field(
        default=None, sa_column=Column("metadata", JSON)
    )

    created_at: datetime = Field(default_factory=utcnow)


class DeliberationSynthesis(QueryModel, table=True):
    """Synthesized conclusion of a deliberation.

    Each deliberation produces at most one synthesis.  The synthesis captures
    the consensus finding, dissenting views, and can be promoted into
    :class:`BoardMemory` for long-term recall.
    """

    __tablename__ = "deliberation_syntheses"  # pyright: ignore[reportAssignmentType]
    __table_args__ = (
        Index(
            "ix_delib_synth_promoted",
            "promoted_to_memory",
        ),
    )

    id: UUID = Field(default_factory=uuid4, primary_key=True)
    deliberation_id: UUID = Field(foreign_key="deliberations.id", unique=True)

    # Authorship
    synthesized_by_agent_id: UUID | None = Field(default=None, foreign_key="agents.id")

    # Content
    content: str = Field(sa_column=Column(Text, nullable=False))
    consensus_level: str  # unanimous, majority, contested, split
    key_points: list[str] | None = Field(default=None, sa_column=Column(JSON))
    dissenting_views: list[str] | None = Field(default=None, sa_column=Column(JSON))
    confidence: float = Field(ge=0.0, le=1.0)
    tags: list[str] | None = Field(default=None, sa_column=Column(JSON))

    # Memory promotion
    promoted_to_memory: bool = Field(default=False)
    board_memory_id: UUID | None = Field(default=None, foreign_key="board_memory.id")

    created_at: datetime = Field(default_factory=utcnow)
