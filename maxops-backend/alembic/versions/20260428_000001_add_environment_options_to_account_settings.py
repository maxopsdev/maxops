"""add environment options to account settings

Revision ID: 20260428_000001
Revises: 20260420_000001
Create Date: 2026-04-28 00:00:01.000000
"""
from alembic import op
import sqlalchemy as sa
import json


revision = "20260428_000001"
down_revision = "20260420_000001"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("account_settings", sa.Column("environment_options", sa.JSON(), nullable=True))
    bind = op.get_bind()
    rows = bind.execute(sa.text("SELECT id, environment FROM account_settings WHERE environment_options IS NULL")).mappings()
    for row in rows:
        values = [
            "Development",
            "Staging",
            "Production",
            "UAT",
            "QA",
            "Testing",
            "Sandbox",
            row.get("environment"),
        ]
        normalized = []
        seen = set()
        for value in values:
            item = str(value or "").strip()
            if not item:
                continue
            key = item.lower()
            if key in seen:
                continue
            seen.add(key)
            normalized.append(item)
        bind.execute(
            sa.text("UPDATE account_settings SET environment_options = :environment_options WHERE id = :id"),
            {"environment_options": json.dumps(normalized), "id": row["id"]},
        )


def downgrade():
    op.drop_column("account_settings", "environment_options")
