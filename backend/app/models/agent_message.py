"""Agent message model for short-term memory inter-agent communication traces."""

from __future__ import annotations

from datetime import datetime
from uuid import UUID, uuid4

from sqlalchemy import JSON, Column, Index
from sqlmodel import Field

from app.core.time import utcnow
from app.models.base import QueryModel

RUNTIME_ANNOTATION_TYPES = (datetime,)


class AgentMessage(QueryModel, table=True):
    """Persisted inter-agent message for audit, replay, and context windows."""

    __tablename__ = "agent_messages"  # pyright: ignore[reportAssignmentType]
    __table_args__ = (
        Index("ix_agent_messages_board_created", "board_id", "created_at"),
        Index("ix_agent_messages_correlation", "correlation_id"),
        Index("ix_agent_messages_agent_created", "agent_id", "created_at"),
    )

    id: UUID = Field(default_factory=uuid4, primary_key=True)
    board_id: UUID = Field(foreign_key="boards.id", index=True)
    agent_id: UUID = Field(foreign_key="agents.id", index=True)
    message_type: str = Field(index=True)
    payload: dict[str, object] | None = Field(default=None, sa_column=Column(JSON))
    parent_message_id: UUID | None = Field(
        default=None, foreign_key="agent_messages.id"
    )
    correlation_id: UUID | None = Field(default=None)
    created_at: datetime = Field(default_factory=utcnow)
