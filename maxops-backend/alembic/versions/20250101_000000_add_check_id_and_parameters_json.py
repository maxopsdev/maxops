"""add check_id and parameters_json fields

Revision ID: 20250101_000000
Revises: add_policy_code_and_filters_json
Create Date: 2025-01-01 00:00:00.000000

"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import sqlite

# revision identifiers, used by Alembic.
revision = '20250101_000000'
down_revision = 'add_policy_code_filters'
branch_labels = None
depends_on = None


def upgrade():
    """Add check_id and parameters_json fields to policies table."""
    # Add new fields (nullable initially for migration)
    op.add_column('policies', sa.Column('check_id', sa.String(100), nullable=True))
    op.add_column('policies', sa.Column('parameters_json', sa.JSON(), nullable=True))

    # Create index on check_id for faster lookups
    op.create_index('ix_policies_check_id', 'policies', ['check_id'])

    # Note: Data migration should be done separately before making check_id required
    # For now, we'll keep check_id nullable to allow gradual migration

    # After data migration is complete, run:
    # op.alter_column('policies', 'check_id', nullable=False)
    # op.drop_column('policies', 'policy_yaml')
    # op.drop_column('policies', 'filters_json')


def downgrade():
    """Remove check_id and parameters_json fields."""
    op.drop_index('ix_policies_check_id', 'policies')
    op.drop_column('policies', 'parameters_json')
    op.drop_column('policies', 'check_id')
