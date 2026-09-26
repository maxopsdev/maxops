"""add pricing cache table

Revision ID: 20260126_000001
Revises: 20260119_000002
Create Date: 2026-01-26 00:00:01.000000
"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = "20260126_000001"
down_revision = "20260119_000002"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "pricing_cache",
        sa.Column("id", sa.Integer(), primary_key=True, nullable=False),
        sa.Column("resource_type", sa.String(), nullable=False),
        sa.Column("region", sa.String(), nullable=False),
        sa.Column("parameters_hash", sa.String(), nullable=False),
        sa.Column("parameters_json", sa.JSON(), nullable=False),
        sa.Column("price_per_unit", sa.Float(), nullable=True),
        sa.Column("unit", sa.String(), nullable=True),
        sa.Column("currency", sa.String(), nullable=False, server_default="USD"),
        sa.Column("source", sa.String(), nullable=False, server_default="aws_pricing"),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
    )
    op.create_index("ix_pricing_cache_resource_type", "pricing_cache", ["resource_type"])
    op.create_index("ix_pricing_cache_region", "pricing_cache", ["region"])
    op.create_index("ix_pricing_cache_parameters_hash", "pricing_cache", ["parameters_hash"])
    op.create_index(
        "uq_pricing_cache_resource_region_params",
        "pricing_cache",
        ["resource_type", "region", "parameters_hash"],
        unique=True,
    )


def downgrade():
    op.drop_index("uq_pricing_cache_resource_region_params", table_name="pricing_cache")
    op.drop_index("ix_pricing_cache_parameters_hash", table_name="pricing_cache")
    op.drop_index("ix_pricing_cache_region", table_name="pricing_cache")
    op.drop_index("ix_pricing_cache_resource_type", table_name="pricing_cache")
    op.drop_table("pricing_cache")
