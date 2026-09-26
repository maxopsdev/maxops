"""add ASG inventory table

Revision ID: 20260715_000001
Revises: 20260428_000001
Create Date: 2026-07-15 00:00:01
"""

from alembic import op
import sqlalchemy as sa


revision = "20260715_000001"
down_revision = "20260428_000001"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "asg_inventory",
        sa.Column("id", sa.Integer(), primary_key=True, nullable=False),
        sa.Column("inventory_id", sa.Integer(), nullable=False),
        sa.Column("resource_id", sa.String(length=255), nullable=False),
        sa.Column("resource_name", sa.String(length=255), nullable=True),
        sa.Column("resource_type", sa.String(length=100), nullable=False),
        sa.Column("account_id", sa.String(length=100), nullable=False),
        sa.Column("region", sa.String(length=50), nullable=False),
        sa.Column("state", sa.String(length=50), nullable=True),
        sa.Column("min_size", sa.Integer(), nullable=False),
        sa.Column("desired_capacity", sa.Integer(), nullable=False),
        sa.Column("max_size", sa.Integer(), nullable=False),
        sa.Column("instance_type", sa.String(length=100), nullable=True),
        sa.Column("platform_normalized", sa.String(length=100), nullable=True),
        sa.Column("generated_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("tags_json", sa.JSON(), nullable=True),
        sa.Column("metadata_json", sa.JSON(), nullable=True),
        sa.Column("aws_payload_json", sa.JSON(), nullable=True),
        sa.Column("source_path", sa.String(length=500), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=True,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=True,
        ),
        sa.UniqueConstraint("inventory_id"),
        sa.UniqueConstraint(
            "account_id",
            "region",
            "resource_id",
            name="uq_asg_inventory_account_region_resource",
        ),
    )
    for column in (
        "id",
        "inventory_id",
        "resource_id",
        "account_id",
        "region",
        "state",
        "instance_type",
        "generated_at",
    ):
        op.create_index(f"ix_asg_inventory_{column}", "asg_inventory", [column])


def downgrade():
    for column in reversed(
        (
            "id",
            "inventory_id",
            "resource_id",
            "account_id",
            "region",
            "state",
            "instance_type",
            "generated_at",
        )
    ):
        op.drop_index(f"ix_asg_inventory_{column}", table_name="asg_inventory")
    op.drop_table("asg_inventory")
