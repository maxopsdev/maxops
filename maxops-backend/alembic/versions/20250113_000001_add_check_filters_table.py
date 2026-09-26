"""Add check_filters table

Revision ID: 20250113_000001
Revises: 20250113_000000
Create Date: 2025-01-13

"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import sqlite

# revision identifiers, used by Alembic.
revision = '20250113_000001'
down_revision = '20250113_000000'
branch_labels = None
depends_on = None


def upgrade():
    # Create check_filters table
    op.create_table(
        'check_filters',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('check_id', sa.String(length=255), nullable=False),
        sa.Column('filters_json', sa.JSON(), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('(CURRENT_TIMESTAMP)'), nullable=True),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('(CURRENT_TIMESTAMP)'), nullable=True),
        sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_check_filters_check_id'), 'check_filters', ['check_id'], unique=True)
    op.create_index(op.f('ix_check_filters_id'), 'check_filters', ['id'], unique=False)


def downgrade():
    op.drop_index(op.f('ix_check_filters_id'), table_name='check_filters')
    op.drop_index(op.f('ix_check_filters_check_id'), table_name='check_filters')
    op.drop_table('check_filters')
