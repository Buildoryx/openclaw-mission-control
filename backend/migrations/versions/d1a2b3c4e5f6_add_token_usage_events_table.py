"""add token_usage_events table

Revision ID: d1a2b3c4e5f6
Revises: a9b1c2d3e4f7
Create Date: 2025-07-14 12:00:00.000000

"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision = "d1a2b3c4e5f6"
down_revision = "a9b1c2d3e4f7"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)

    if not inspector.has_table("token_usage_events"):
        op.create_table(
            "token_usage_events",
            sa.Column("id", sa.Uuid(), nullable=False),
            # Organization scoping
            sa.Column("organization_id", sa.Uuid(), nullable=False),
            # Gateway / agent context
            sa.Column("gateway_id", sa.Uuid(), nullable=True),
            sa.Column("agent_id", sa.Uuid(), nullable=True),
            sa.Column("board_id", sa.Uuid(), nullable=True),
            # Session identification
            sa.Column("session_id", sa.String(), nullable=True),
            # Model metadata
            sa.Column("model", sa.String(), nullable=False),
            sa.Column("model_provider", sa.String(), nullable=True),
            # Token counts
            sa.Column("input_tokens", sa.Integer(), nullable=False, server_default="0"),
            sa.Column(
                "output_tokens", sa.Integer(), nullable=False, server_default="0"
            ),
            sa.Column("total_tokens", sa.Integer(), nullable=False, server_default="0"),
            # Cost tracking (micro-cents for precision)
            sa.Column("input_cost_microcents", sa.Integer(), nullable=True),
            sa.Column("output_cost_microcents", sa.Integer(), nullable=True),
            sa.Column("total_cost_microcents", sa.Integer(), nullable=True),
            # Event classification
            sa.Column(
                "event_kind",
                sa.String(),
                nullable=False,
                server_default="turn",
            ),
            # Free-form annotation
            sa.Column("note", sa.Text(), nullable=True),
            # Timestamps
            sa.Column("event_at", sa.DateTime(), nullable=False),
            sa.Column("created_at", sa.DateTime(), nullable=False),
            # Constraints
            sa.ForeignKeyConstraint(
                ["organization_id"],
                ["organizations.id"],
            ),
            sa.ForeignKeyConstraint(
                ["gateway_id"],
                ["gateways.id"],
            ),
            sa.ForeignKeyConstraint(
                ["agent_id"],
                ["agents.id"],
            ),
            sa.ForeignKeyConstraint(
                ["board_id"],
                ["boards.id"],
            ),
            sa.PrimaryKeyConstraint("id"),
        )

    existing_indexes = {
        item.get("name") for item in inspector.get_indexes("token_usage_events")
    }

    index_definitions = [
        ("ix_token_usage_events_organization_id", ["organization_id"], False),
        ("ix_token_usage_events_gateway_id", ["gateway_id"], False),
        ("ix_token_usage_events_agent_id", ["agent_id"], False),
        ("ix_token_usage_events_board_id", ["board_id"], False),
        ("ix_token_usage_events_session_id", ["session_id"], False),
        ("ix_token_usage_events_model", ["model"], False),
        ("ix_token_usage_events_model_provider", ["model_provider"], False),
        ("ix_token_usage_events_event_kind", ["event_kind"], False),
        ("ix_token_usage_events_event_at", ["event_at"], False),
    ]

    for index_name, columns, unique in index_definitions:
        if index_name not in existing_indexes:
            op.create_index(
                op.f(index_name),
                "token_usage_events",
                columns,
                unique=unique,
            )

    # Composite index for the most common dashboard query pattern:
    # filter by org + time range, group by model
    composite_name = "ix_token_usage_events_org_event_at_model"
    if composite_name not in existing_indexes:
        op.create_index(
            composite_name,
            "token_usage_events",
            ["organization_id", "event_at", "model"],
            unique=False,
        )


def downgrade() -> None:
    op.drop_index(
        "ix_token_usage_events_org_event_at_model",
        table_name="token_usage_events",
    )
    op.drop_index(
        op.f("ix_token_usage_events_event_at"),
        table_name="token_usage_events",
    )
    op.drop_index(
        op.f("ix_token_usage_events_event_kind"),
        table_name="token_usage_events",
    )
    op.drop_index(
        op.f("ix_token_usage_events_model_provider"),
        table_name="token_usage_events",
    )
    op.drop_index(
        op.f("ix_token_usage_events_model"),
        table_name="token_usage_events",
    )
    op.drop_index(
        op.f("ix_token_usage_events_session_id"),
        table_name="token_usage_events",
    )
    op.drop_index(
        op.f("ix_token_usage_events_board_id"),
        table_name="token_usage_events",
    )
    op.drop_index(
        op.f("ix_token_usage_events_agent_id"),
        table_name="token_usage_events",
    )
    op.drop_index(
        op.f("ix_token_usage_events_gateway_id"),
        table_name="token_usage_events",
    )
    op.drop_index(
        op.f("ix_token_usage_events_organization_id"),
        table_name="token_usage_events",
    )
    op.drop_table("token_usage_events")
