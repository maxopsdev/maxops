"""add rds and elasticache inventory tables

Revision ID: 20260420_000001
Revises: 20260413_000001
Create Date: 2026-04-20 00:00:01.000000
"""
from alembic import op
import sqlalchemy as sa


revision = "20260420_000001"
down_revision = "20260413_000001"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "rds_inventory",
        sa.Column("id", sa.Integer(), primary_key=True, nullable=False),
        sa.Column("inventory_id", sa.Integer(), nullable=False),
        sa.Column("resource_id", sa.String(length=255), nullable=False),
        sa.Column("resource_name", sa.String(length=255), nullable=True),
        sa.Column("resource_type", sa.String(length=100), nullable=False),
        sa.Column("account_id", sa.String(length=100), nullable=True),
        sa.Column("region", sa.String(length=50), nullable=True),
        sa.Column("availability_zone", sa.String(length=50), nullable=True),
        sa.Column("state", sa.String(length=50), nullable=True),
        sa.Column("engine", sa.String(length=100), nullable=True),
        sa.Column("db_instance_class", sa.String(length=100), nullable=True),
        sa.Column("created_at_source", sa.DateTime(timezone=True), nullable=True),
        sa.Column("generated_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("avg_cpu_utilization", sa.Float(), nullable=True),
        sa.Column("avg_connections", sa.Float(), nullable=True),
        sa.Column("avg_read_iops", sa.Float(), nullable=True),
        sa.Column("avg_write_iops", sa.Float(), nullable=True),
        sa.Column("monthly_cost_estimate", sa.Float(), nullable=True),
        sa.Column("metric_history_json", sa.JSON(), nullable=True),
        sa.Column("tags_json", sa.JSON(), nullable=True),
        sa.Column("metadata_json", sa.JSON(), nullable=True),
        sa.Column("aws_payload_json", sa.JSON(), nullable=True),
        sa.Column("source_path", sa.String(length=500), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("CURRENT_TIMESTAMP"), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("CURRENT_TIMESTAMP"), nullable=True),
        sa.UniqueConstraint("inventory_id"),
        sa.UniqueConstraint("resource_id"),
    )
    op.create_index("ix_rds_inventory_id", "rds_inventory", ["id"])
    op.create_index("ix_rds_inventory_inventory_id", "rds_inventory", ["inventory_id"])
    op.create_index("ix_rds_inventory_resource_id", "rds_inventory", ["resource_id"])
    op.create_index("ix_rds_inventory_account_id", "rds_inventory", ["account_id"])
    op.create_index("ix_rds_inventory_region", "rds_inventory", ["region"])
    op.create_index("ix_rds_inventory_state", "rds_inventory", ["state"])
    op.create_index("ix_rds_inventory_engine", "rds_inventory", ["engine"])
    op.create_index("ix_rds_inventory_db_instance_class", "rds_inventory", ["db_instance_class"])
    op.create_index("ix_rds_inventory_generated_at", "rds_inventory", ["generated_at"])

    op.create_table(
        "elasticache_inventory",
        sa.Column("id", sa.Integer(), primary_key=True, nullable=False),
        sa.Column("inventory_id", sa.Integer(), nullable=False),
        sa.Column("resource_id", sa.String(length=255), nullable=False),
        sa.Column("resource_name", sa.String(length=255), nullable=True),
        sa.Column("resource_type", sa.String(length=100), nullable=False),
        sa.Column("account_id", sa.String(length=100), nullable=True),
        sa.Column("region", sa.String(length=50), nullable=True),
        sa.Column("availability_zone", sa.String(length=50), nullable=True),
        sa.Column("state", sa.String(length=50), nullable=True),
        sa.Column("engine", sa.String(length=100), nullable=True),
        sa.Column("engine_version", sa.String(length=100), nullable=True),
        sa.Column("cache_node_type", sa.String(length=100), nullable=True),
        sa.Column("generated_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("monthly_cost_estimate", sa.Float(), nullable=True),
        sa.Column("avg_curritems", sa.Float(), nullable=True),
        sa.Column("avg_keycount", sa.Float(), nullable=True),
        sa.Column("metric_history_json", sa.JSON(), nullable=True),
        sa.Column("tags_json", sa.JSON(), nullable=True),
        sa.Column("metadata_json", sa.JSON(), nullable=True),
        sa.Column("aws_payload_json", sa.JSON(), nullable=True),
        sa.Column("source_path", sa.String(length=500), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("CURRENT_TIMESTAMP"), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("CURRENT_TIMESTAMP"), nullable=True),
        sa.UniqueConstraint("inventory_id"),
        sa.UniqueConstraint("resource_id"),
    )
    op.create_index("ix_elasticache_inventory_id", "elasticache_inventory", ["id"])
    op.create_index("ix_elasticache_inventory_inventory_id", "elasticache_inventory", ["inventory_id"])
    op.create_index("ix_elasticache_inventory_resource_id", "elasticache_inventory", ["resource_id"])
    op.create_index("ix_elasticache_inventory_account_id", "elasticache_inventory", ["account_id"])
    op.create_index("ix_elasticache_inventory_region", "elasticache_inventory", ["region"])
    op.create_index("ix_elasticache_inventory_state", "elasticache_inventory", ["state"])
    op.create_index("ix_elasticache_inventory_engine", "elasticache_inventory", ["engine"])
    op.create_index("ix_elasticache_inventory_cache_node_type", "elasticache_inventory", ["cache_node_type"])
    op.create_index("ix_elasticache_inventory_generated_at", "elasticache_inventory", ["generated_at"])


def downgrade():
    op.drop_index("ix_elasticache_inventory_generated_at", table_name="elasticache_inventory")
    op.drop_index("ix_elasticache_inventory_cache_node_type", table_name="elasticache_inventory")
    op.drop_index("ix_elasticache_inventory_engine", table_name="elasticache_inventory")
    op.drop_index("ix_elasticache_inventory_state", table_name="elasticache_inventory")
    op.drop_index("ix_elasticache_inventory_region", table_name="elasticache_inventory")
    op.drop_index("ix_elasticache_inventory_account_id", table_name="elasticache_inventory")
    op.drop_index("ix_elasticache_inventory_resource_id", table_name="elasticache_inventory")
    op.drop_index("ix_elasticache_inventory_inventory_id", table_name="elasticache_inventory")
    op.drop_index("ix_elasticache_inventory_id", table_name="elasticache_inventory")
    op.drop_table("elasticache_inventory")

    op.drop_index("ix_rds_inventory_generated_at", table_name="rds_inventory")
    op.drop_index("ix_rds_inventory_db_instance_class", table_name="rds_inventory")
    op.drop_index("ix_rds_inventory_engine", table_name="rds_inventory")
    op.drop_index("ix_rds_inventory_state", table_name="rds_inventory")
    op.drop_index("ix_rds_inventory_region", table_name="rds_inventory")
    op.drop_index("ix_rds_inventory_account_id", table_name="rds_inventory")
    op.drop_index("ix_rds_inventory_resource_id", table_name="rds_inventory")
    op.drop_index("ix_rds_inventory_inventory_id", table_name="rds_inventory")
    op.drop_index("ix_rds_inventory_id", table_name="rds_inventory")
    op.drop_table("rds_inventory")
