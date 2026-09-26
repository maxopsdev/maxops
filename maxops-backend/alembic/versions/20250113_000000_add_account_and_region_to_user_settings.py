"""add account and region to user_settings

Revision ID: 20250113_000000
Revises: 20250101_000000
Create Date: 2025-01-13 00:00:00.000000

"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import sqlite

# revision identifiers, used by Alembic.
revision = '20250113_000000'
down_revision = '20250101_000000'
branch_labels = None
depends_on = None


def upgrade():
    """Add account and region columns to user_settings table."""
    # Check if columns already exist (for idempotency)
    try:
        conn = op.get_bind()
        inspector = sa.inspect(conn)
        columns = [col['name'] for col in inspector.get_columns('user_settings')]
    except Exception:
        # Table might not exist yet, columns will be created by model
        columns = []
    
    # Add account column if it doesn't exist
    if 'account' not in columns:
        op.add_column('user_settings', sa.Column('account', sa.String(100), nullable=True))
    
    # Add region column if it doesn't exist
    if 'region' not in columns:
        op.add_column('user_settings', sa.Column('region', sa.String(50), nullable=True))
    
    # Update any existing NULL values with default values
    # This ensures existing records have values before we enforce NOT NULL
    try:
        op.execute("""
            UPDATE user_settings 
            SET account = 'default-account' 
            WHERE account IS NULL
        """)
        op.execute("""
            UPDATE user_settings 
            SET region = 'us-east-1' 
            WHERE region IS NULL
        """)
    except Exception:
        # If table is empty or doesn't exist, that's fine
        pass
    
    # Note: SQLite doesn't support ALTER COLUMN to change nullable constraint
    # The application layer enforces NOT NULL through Pydantic validation
    # For PostgreSQL, you would add:
    # op.alter_column('user_settings', 'account', nullable=False)
    # op.alter_column('user_settings', 'region', nullable=False)


def downgrade():
    """Remove account and region columns from user_settings table."""
    # Check if columns exist before dropping
    conn = op.get_bind()
    inspector = sa.inspect(conn)
    columns = [col['name'] for col in inspector.get_columns('user_settings')]
    
    if 'region' in columns:
        op.drop_column('user_settings', 'region')
    if 'account' in columns:
        op.drop_column('user_settings', 'account')
