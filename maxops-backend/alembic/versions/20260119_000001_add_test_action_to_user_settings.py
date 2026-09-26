"""Add test_action to user_settings.

Revision ID: 20260119_000001
Revises: 20250113_000002_add_resource_exemptions_table
Create Date: 2026-01-19
"""
from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision = "20260119_000001"
down_revision = "20250113_000002"
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table("user_settings") as batch_op:
        batch_op.add_column(sa.Column("test_action", sa.Boolean(), server_default=sa.false(), nullable=False))


def downgrade():
    with op.batch_alter_table("user_settings") as batch_op:
        batch_op.drop_column("test_action")
