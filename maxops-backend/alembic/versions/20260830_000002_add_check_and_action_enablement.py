"""add enabled to check_settings and new action_settings table

Revision ID: 20260830_000002
Revises: 20260830_000001
Create Date: 2026-08-30 00:00:02
"""

from alembic import op
import sqlalchemy as sa


revision = "20260830_000002"
down_revision = "20260830_000001"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column(
        "check_settings",
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default=sa.true()),
    )

    op.create_table(
        "action_settings",
        sa.Column("id", sa.Integer(), primary_key=True, index=True),
        sa.Column(
            "account_settings_id",
            sa.Integer(),
            sa.ForeignKey("account_settings.id"),
            nullable=False,
        ),
        sa.Column("action_key", sa.String(length=255), nullable=False),
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            onupdate=sa.func.now(),
        ),
    )
    op.create_index(
        "ix_action_settings_account_settings_id",
        "action_settings",
        ["account_settings_id"],
    )
    op.create_index("ix_action_settings_action_key", "action_settings", ["action_key"])


def downgrade():
    op.drop_index("ix_action_settings_action_key", table_name="action_settings")
    op.drop_index("ix_action_settings_account_settings_id", table_name="action_settings")
    op.drop_table("action_settings")
    op.drop_column("check_settings", "enabled")
