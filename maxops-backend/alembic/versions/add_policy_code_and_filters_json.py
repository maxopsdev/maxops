"""Add policy_code and filters_json to policies table

Revision ID: add_policy_code_filters
Revises: 763b22d20a17
Create Date: 2024-01-01 00:00:00.000000

"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import sqlite

# revision identifiers, used by Alembic.
revision = 'add_policy_code_filters'
down_revision = '763b22d20a17'
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Add policy_code column (nullable first, we'll populate it later)
    op.add_column('policies', 
        sa.Column('policy_code', sa.String(length=6), nullable=True)
    )
    
    # Create index on policy_code (unique constraint will be added after migration)
    op.create_index('ix_policies_policy_code', 'policies', ['policy_code'], unique=False)
    
    # Add filters_json column
    # SQLite stores JSON as TEXT, but SQLAlchemy handles it
    # Use JSON type which SQLAlchemy will map to TEXT for SQLite
    op.add_column('policies',
        sa.Column('filters_json', sa.JSON(), nullable=True)
    )


def downgrade() -> None:
    # Remove index
    op.drop_index('ix_policies_policy_code', table_name='policies')
    
    # Remove columns
    op.drop_column('policies', 'filters_json')
    op.drop_column('policies', 'policy_code')

