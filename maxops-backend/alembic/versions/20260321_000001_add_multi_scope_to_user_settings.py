"""add multi account and multi region to user_settings

Revision ID: 20260321_000001
Revises: 20260126_000001
Create Date: 2026-03-21 00:00:01.000000
"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = "20260321_000001"
down_revision = "20260126_000001"
branch_labels = None
depends_on = None


def upgrade():
    conn = op.get_bind()
    inspector = sa.inspect(conn)
    columns = [col["name"] for col in inspector.get_columns("user_settings")]
    user_settings_table = sa.table(
        "user_settings",
        sa.column("id", sa.Integer()),
        sa.column("accounts", sa.JSON()),
        sa.column("regions", sa.JSON()),
    )

    if "accounts" not in columns:
        op.add_column("user_settings", sa.Column("accounts", sa.JSON(), nullable=True))
    if "regions" not in columns:
        op.add_column("user_settings", sa.Column("regions", sa.JSON(), nullable=True))

    rows = conn.execute(sa.text("SELECT id, account, region FROM user_settings")).fetchall()
    for row in rows:
        accounts = [row.account] if row.account else []
        regions = [row.region] if row.region else []
        conn.execute(
            user_settings_table.update()
            .where(user_settings_table.c.id == row.id)
            .values(accounts=accounts, regions=regions)
        )


def downgrade():
    conn = op.get_bind()
    inspector = sa.inspect(conn)
    columns = [col["name"] for col in inspector.get_columns("user_settings")]

    if "regions" in columns:
        op.drop_column("user_settings", "regions")
    if "accounts" in columns:
        op.drop_column("user_settings", "accounts")
