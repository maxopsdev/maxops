"""add ec2 metric history columns

Revision ID: 20260413_000001
Revises: 20260405_000001
Create Date: 2026-04-13 00:00:01.000000
"""
from alembic import op
import sqlalchemy as sa


revision = "20260413_000001"
down_revision = "20260405_000001"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("ec2_inventory", sa.Column("usage_profile", sa.String(length=100), nullable=True))
    op.add_column("ec2_inventory", sa.Column("avg_cpu_utilization", sa.Float(), nullable=True))
    op.add_column("ec2_inventory", sa.Column("avg_memory_utilization", sa.Float(), nullable=True))
    op.add_column("ec2_inventory", sa.Column("avg_network_in", sa.Float(), nullable=True))
    op.add_column("ec2_inventory", sa.Column("avg_network_out", sa.Float(), nullable=True))
    op.add_column("ec2_inventory", sa.Column("monthly_cost_estimate", sa.Float(), nullable=True))
    op.add_column("ec2_inventory", sa.Column("metric_history_json", sa.JSON(), nullable=True))
    op.create_index("ix_ec2_inventory_usage_profile", "ec2_inventory", ["usage_profile"])


def downgrade():
    op.drop_index("ix_ec2_inventory_usage_profile", table_name="ec2_inventory")
    op.drop_column("ec2_inventory", "metric_history_json")
    op.drop_column("ec2_inventory", "monthly_cost_estimate")
    op.drop_column("ec2_inventory", "avg_network_out")
    op.drop_column("ec2_inventory", "avg_network_in")
    op.drop_column("ec2_inventory", "avg_memory_utilization")
    op.drop_column("ec2_inventory", "avg_cpu_utilization")
    op.drop_column("ec2_inventory", "usage_profile")
