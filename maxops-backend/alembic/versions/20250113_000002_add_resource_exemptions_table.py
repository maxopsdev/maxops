"""add_resource_exemptions_table

Revision ID: 20250113_000002
Revises: 20250113_000001
Create Date: 2025-01-13 00:00:02.000000

"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import sqlite

# revision identifiers, used by Alembic.
revision = '20250113_000002'
down_revision = '20250113_000001'
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        'resource_exemptions',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('check_id', sa.String(length=255), nullable=False),
        sa.Column('resource_id', sa.String(length=255), nullable=False),
        sa.Column('resource_type', sa.String(length=100), nullable=False),
        sa.Column('exempted', sa.Boolean(), nullable=False, server_default='0'),
        sa.Column('snoozed_until', sa.DateTime(timezone=True), nullable=True),
        sa.Column('snooze_days', sa.Integer(), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.func.now(), onupdate=sa.func.now()),
        sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_resource_exemptions_check_id'), 'resource_exemptions', ['check_id'], unique=False)
    op.create_index(op.f('ix_resource_exemptions_resource_id'), 'resource_exemptions', ['resource_id'], unique=False)


def downgrade():
    op.drop_index(op.f('ix_resource_exemptions_resource_id'), table_name='resource_exemptions')
    op.drop_index(op.f('ix_resource_exemptions_check_id'), table_name='resource_exemptions')
    op.drop_table('resource_exemptions')
