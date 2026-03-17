"""Add deliberation module tables and columns.

Creates the core tables for the agent deliberation subsystem:
- agent_messages: inter-agent communication traces (short-term memory)
- deliberations: structured debate sessions attached to boards
- deliberation_entries: individual contributions within deliberations
- deliberation_syntheses: synthesized conclusions of deliberations
- episodic_memory: learned patterns from past deliberations

Also adds columns to existing tables:
- boards.deliberation_config (JSONB)
- board_memory.embedding (JSONB, placeholder for pgvector Vector)

Revision ID: a4f7b8c9d0e1
Revises: d1a2b3c4e5f6
Create Date: 2026-03-05 18:28:52.000000
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision = "a4f7b8c9d0e1"
down_revision = "d1a2b3c4e5f6"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # ------------------------------------------------------------------
    # Enable pgvector extension (idempotent)
    # ------------------------------------------------------------------
    op.execute("CREATE EXTENSION IF NOT EXISTS vector")

    # ------------------------------------------------------------------
    # Add columns to existing tables
    # ------------------------------------------------------------------
    op.add_column(
        "boards",
        sa.Column("deliberation_config", sa.JSON(), nullable=True),
    )
    op.add_column(
        "board_memory",
        sa.Column("embedding", sa.JSON(), nullable=True),
    )

    # ------------------------------------------------------------------
    # agent_messages
    # ------------------------------------------------------------------
    op.create_table(
        "agent_messages",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("board_id", sa.Uuid(), nullable=False),
        sa.Column("agent_id", sa.Uuid(), nullable=False),
        sa.Column("message_type", sa.String(), nullable=False),
        sa.Column("payload", sa.JSON(), nullable=True),
        sa.Column("parent_message_id", sa.Uuid(), nullable=True),
        sa.Column("correlation_id", sa.Uuid(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["board_id"], ["boards.id"]),
        sa.ForeignKeyConstraint(["agent_id"], ["agents.id"]),
        sa.ForeignKeyConstraint(["parent_message_id"], ["agent_messages.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_agent_messages_board_id", "agent_messages", ["board_id"])
    op.create_index("ix_agent_messages_agent_id", "agent_messages", ["agent_id"])
    op.create_index(
        "ix_agent_messages_message_type", "agent_messages", ["message_type"]
    )
    op.create_index(
        "ix_agent_messages_board_created",
        "agent_messages",
        ["board_id", "created_at"],
    )
    op.create_index(
        "ix_agent_messages_correlation",
        "agent_messages",
        ["correlation_id"],
    )
    op.create_index(
        "ix_agent_messages_agent_created",
        "agent_messages",
        ["agent_id", "created_at"],
    )

    # ------------------------------------------------------------------
    # deliberations
    # ------------------------------------------------------------------
    op.create_table(
        "deliberations",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("board_id", sa.Uuid(), nullable=False),
        sa.Column("topic", sa.Text(), nullable=False),
        sa.Column("status", sa.String(), nullable=False, server_default="created"),
        sa.Column("initiated_by_agent_id", sa.Uuid(), nullable=True),
        sa.Column("synthesizer_agent_id", sa.Uuid(), nullable=True),
        sa.Column("trigger_reason", sa.String(), nullable=True),
        sa.Column("task_id", sa.Uuid(), nullable=True),
        sa.Column("parent_deliberation_id", sa.Uuid(), nullable=True),
        sa.Column("max_turns", sa.Integer(), nullable=False, server_default="6"),
        sa.Column(
            "outcome_changed",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("false"),
        ),
        sa.Column("confidence_delta", sa.Float(), nullable=True),
        sa.Column("duration_ms", sa.Float(), nullable=True),
        sa.Column("approval_id", sa.Uuid(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("concluded_at", sa.DateTime(), nullable=True),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["board_id"], ["boards.id"]),
        sa.ForeignKeyConstraint(["initiated_by_agent_id"], ["agents.id"]),
        sa.ForeignKeyConstraint(["synthesizer_agent_id"], ["agents.id"]),
        sa.ForeignKeyConstraint(["task_id"], ["tasks.id"]),
        sa.ForeignKeyConstraint(["parent_deliberation_id"], ["deliberations.id"]),
        sa.ForeignKeyConstraint(["approval_id"], ["approvals.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_deliberations_board_id", "deliberations", ["board_id"])
    op.create_index("ix_deliberations_status", "deliberations", ["status"])
    op.create_index("ix_deliberations_task_id", "deliberations", ["task_id"])
    op.create_index(
        "ix_deliberations_board_status",
        "deliberations",
        ["board_id", "status"],
    )
    op.create_index(
        "ix_deliberations_board_created",
        "deliberations",
        ["board_id", "created_at"],
    )

    # ------------------------------------------------------------------
    # deliberation_entries
    # ------------------------------------------------------------------
    op.create_table(
        "deliberation_entries",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("deliberation_id", sa.Uuid(), nullable=False),
        sa.Column("sequence", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("phase", sa.String(), nullable=False),
        sa.Column("entry_type", sa.String(), nullable=False),
        sa.Column("agent_id", sa.Uuid(), nullable=True),
        sa.Column("user_id", sa.Uuid(), nullable=True),
        sa.Column("position", sa.String(), nullable=True),
        sa.Column("confidence", sa.Float(), nullable=True),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("parent_entry_id", sa.Uuid(), nullable=True),
        sa.Column("references", sa.JSON(), nullable=True),
        sa.Column("metadata", sa.JSON(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["deliberation_id"], ["deliberations.id"]),
        sa.ForeignKeyConstraint(["agent_id"], ["agents.id"]),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"]),
        sa.ForeignKeyConstraint(["parent_entry_id"], ["deliberation_entries.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_deliberation_entries_deliberation_id",
        "deliberation_entries",
        ["deliberation_id"],
    )
    op.create_index(
        "ix_delib_entries_delib_seq",
        "deliberation_entries",
        ["deliberation_id", "sequence"],
    )
    op.create_index(
        "ix_delib_entries_delib_phase",
        "deliberation_entries",
        ["deliberation_id", "phase"],
    )
    op.create_index(
        "ix_delib_entries_agent_created",
        "deliberation_entries",
        ["agent_id", "created_at"],
    )

    # ------------------------------------------------------------------
    # deliberation_syntheses
    # ------------------------------------------------------------------
    op.create_table(
        "deliberation_syntheses",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("deliberation_id", sa.Uuid(), nullable=False),
        sa.Column("synthesized_by_agent_id", sa.Uuid(), nullable=True),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("consensus_level", sa.String(), nullable=False),
        sa.Column("key_points", sa.JSON(), nullable=True),
        sa.Column("dissenting_views", sa.JSON(), nullable=True),
        sa.Column("confidence", sa.Float(), nullable=False),
        sa.Column("tags", sa.JSON(), nullable=True),
        sa.Column(
            "promoted_to_memory",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("false"),
        ),
        sa.Column("board_memory_id", sa.Uuid(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["deliberation_id"], ["deliberations.id"]),
        sa.ForeignKeyConstraint(["synthesized_by_agent_id"], ["agents.id"]),
        sa.ForeignKeyConstraint(["board_memory_id"], ["board_memory.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("deliberation_id"),
    )
    op.create_index(
        "ix_delib_synth_promoted",
        "deliberation_syntheses",
        ["promoted_to_memory"],
    )

    # ------------------------------------------------------------------
    # episodic_memory
    # ------------------------------------------------------------------
    op.create_table(
        "episodic_memory",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("board_id", sa.Uuid(), nullable=False),
        sa.Column("pattern_type", sa.String(), nullable=False),
        sa.Column("topic", sa.String(), nullable=True),
        sa.Column("deliberation_id", sa.Uuid(), nullable=True),
        sa.Column("pattern_summary", sa.Text(), nullable=False),
        sa.Column("pattern_details", sa.JSON(), nullable=True),
        sa.Column(
            "outcome_positive",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("true"),
        ),
        sa.Column("confidence_range", sa.JSON(), nullable=True),
        sa.Column(
            "occurrence_count",
            sa.Integer(),
            nullable=False,
            server_default="1",
        ),
        sa.Column("success_rate", sa.Float(), nullable=True),
        sa.Column("reliability_score", sa.Float(), nullable=True),
        sa.Column("embedding", sa.JSON(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["board_id"], ["boards.id"]),
        sa.ForeignKeyConstraint(["deliberation_id"], ["deliberations.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_episodic_memory_board_id", "episodic_memory", ["board_id"])
    op.create_index(
        "ix_episodic_memory_pattern_type",
        "episodic_memory",
        ["pattern_type"],
    )
    op.create_index(
        "ix_episodic_memory_board_pattern",
        "episodic_memory",
        ["board_id", "pattern_type"],
    )
    op.create_index(
        "ix_episodic_memory_board_topic",
        "episodic_memory",
        ["board_id", "topic"],
    )
    op.create_index(
        "ix_episodic_memory_deliberation",
        "episodic_memory",
        ["deliberation_id"],
    )


def downgrade() -> None:
    # Drop tables in reverse dependency order
    op.drop_table("episodic_memory")
    op.drop_table("deliberation_syntheses")
    op.drop_table("deliberation_entries")
    op.drop_table("deliberations")
    op.drop_table("agent_messages")

    # Remove added columns
    op.drop_column("board_memory", "embedding")
    op.drop_column("boards", "deliberation_config")

    # Note: we intentionally do NOT drop the pgvector extension on downgrade
    # as other parts of the system may depend on it.
