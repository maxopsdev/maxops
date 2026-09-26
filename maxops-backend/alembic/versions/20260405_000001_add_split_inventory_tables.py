"""add split inventory tables

Revision ID: 20260405_000001
Revises: 20260321_000001
Create Date: 2026-04-05 00:00:01.000000
"""
from alembic import op
import sqlalchemy as sa


revision = "20260405_000001"
down_revision = "20260321_000001"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "maxops_inventory",
        sa.Column("id", sa.Integer(), primary_key=True, nullable=False),
        sa.Column("inventory_id", sa.Integer(), nullable=False),
        sa.Column("resource_type", sa.String(length=100), nullable=False),
        sa.Column("resource_id", sa.String(length=255), nullable=False),
        sa.Column("resource_name", sa.String(length=255), nullable=True),
        sa.Column("account_id", sa.String(length=100), nullable=True),
        sa.Column("region", sa.String(length=50), nullable=True),
        sa.Column("generated_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("check_id", sa.String(length=255), nullable=True),
        sa.Column("finding_type", sa.String(length=100), nullable=True),
        sa.Column("title", sa.String(length=255), nullable=True),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("severity", sa.String(length=20), nullable=True),
        sa.Column("confidence_score", sa.Float(), nullable=True),
        sa.Column("risk_score", sa.Float(), nullable=True),
        sa.Column("recommended_action", sa.String(length=100), nullable=True),
        sa.Column("recommended_actions_json", sa.JSON(), nullable=True),
        sa.Column("available_actions_json", sa.JSON(), nullable=True),
        sa.Column("potential_savings_monthly", sa.Float(), nullable=True),
        sa.Column("potential_savings_yearly", sa.Float(), nullable=True),
        sa.Column("evidence_json", sa.JSON(), nullable=True),
        sa.Column("current_config_json", sa.JSON(), nullable=True),
        sa.Column("target_config_json", sa.JSON(), nullable=True),
        sa.Column("metadata_json", sa.JSON(), nullable=True),
        sa.Column("source_path", sa.String(length=500), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("CURRENT_TIMESTAMP"), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("CURRENT_TIMESTAMP"), nullable=True),
        sa.UniqueConstraint("resource_type", "inventory_id", name="uq_maxops_inventory_type_inventory"),
    )
    op.create_index("ix_maxops_inventory_id", "maxops_inventory", ["id"])
    op.create_index("ix_maxops_inventory_resource_type", "maxops_inventory", ["resource_type"])
    op.create_index("ix_maxops_inventory_resource_id", "maxops_inventory", ["resource_id"])
    op.create_index("ix_maxops_inventory_account_id", "maxops_inventory", ["account_id"])
    op.create_index("ix_maxops_inventory_region", "maxops_inventory", ["region"])
    op.create_index("ix_maxops_inventory_generated_at", "maxops_inventory", ["generated_at"])
    op.create_index("ix_maxops_inventory_check_id", "maxops_inventory", ["check_id"])
    op.create_index("ix_maxops_inventory_finding_type", "maxops_inventory", ["finding_type"])
    op.create_index("ix_maxops_inventory_severity", "maxops_inventory", ["severity"])
    op.create_index("idx_maxops_inventory_check_generated", "maxops_inventory", ["check_id", "generated_at"])

    op.create_table(
        "ec2_inventory",
        sa.Column("id", sa.Integer(), primary_key=True, nullable=False),
        sa.Column("inventory_id", sa.Integer(), nullable=False),
        sa.Column("resource_id", sa.String(length=255), nullable=False),
        sa.Column("resource_name", sa.String(length=255), nullable=True),
        sa.Column("resource_type", sa.String(length=100), nullable=False),
        sa.Column("account_id", sa.String(length=100), nullable=True),
        sa.Column("region", sa.String(length=50), nullable=True),
        sa.Column("availability_zone", sa.String(length=50), nullable=True),
        sa.Column("state", sa.String(length=50), nullable=True),
        sa.Column("instance_type", sa.String(length=100), nullable=True),
        sa.Column("launch_time", sa.DateTime(timezone=True), nullable=True),
        sa.Column("generated_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("tags_json", sa.JSON(), nullable=True),
        sa.Column("metadata_json", sa.JSON(), nullable=True),
        sa.Column("aws_payload_json", sa.JSON(), nullable=True),
        sa.Column("source_path", sa.String(length=500), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("CURRENT_TIMESTAMP"), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("CURRENT_TIMESTAMP"), nullable=True),
        sa.UniqueConstraint("inventory_id"),
        sa.UniqueConstraint("resource_id"),
    )
    op.create_index("ix_ec2_inventory_id", "ec2_inventory", ["id"])
    op.create_index("ix_ec2_inventory_inventory_id", "ec2_inventory", ["inventory_id"])
    op.create_index("ix_ec2_inventory_resource_id", "ec2_inventory", ["resource_id"])
    op.create_index("ix_ec2_inventory_account_id", "ec2_inventory", ["account_id"])
    op.create_index("ix_ec2_inventory_region", "ec2_inventory", ["region"])
    op.create_index("ix_ec2_inventory_state", "ec2_inventory", ["state"])
    op.create_index("ix_ec2_inventory_generated_at", "ec2_inventory", ["generated_at"])


def downgrade():
    op.drop_index("ix_ec2_inventory_generated_at", table_name="ec2_inventory")
    op.drop_index("ix_ec2_inventory_state", table_name="ec2_inventory")
    op.drop_index("ix_ec2_inventory_region", table_name="ec2_inventory")
    op.drop_index("ix_ec2_inventory_account_id", table_name="ec2_inventory")
    op.drop_index("ix_ec2_inventory_resource_id", table_name="ec2_inventory")
    op.drop_index("ix_ec2_inventory_inventory_id", table_name="ec2_inventory")
    op.drop_index("ix_ec2_inventory_id", table_name="ec2_inventory")
    op.drop_table("ec2_inventory")

    op.drop_index("idx_maxops_inventory_check_generated", table_name="maxops_inventory")
    op.drop_index("ix_maxops_inventory_severity", table_name="maxops_inventory")
    op.drop_index("ix_maxops_inventory_finding_type", table_name="maxops_inventory")
    op.drop_index("ix_maxops_inventory_check_id", table_name="maxops_inventory")
    op.drop_index("ix_maxops_inventory_generated_at", table_name="maxops_inventory")
    op.drop_index("ix_maxops_inventory_region", table_name="maxops_inventory")
    op.drop_index("ix_maxops_inventory_account_id", table_name="maxops_inventory")
    op.drop_index("ix_maxops_inventory_resource_id", table_name="maxops_inventory")
    op.drop_index("ix_maxops_inventory_resource_type", table_name="maxops_inventory")
    op.drop_index("ix_maxops_inventory_id", table_name="maxops_inventory")
    op.drop_table("maxops_inventory")
