"""drop legacy optimizer tables

Revision ID: 20260830_000001
Revises: 20260715_000001
Create Date: 2026-08-30 00:00:01
"""

from alembic import op
import sqlalchemy as sa


revision = "20260830_000001"
down_revision = "20260715_000001"
branch_labels = None
depends_on = None


def upgrade():
    # Drop children first to respect FK order.
    op.drop_table("change_plans")
    op.drop_table("recommendations")
    op.drop_table("enriched_candidates")
    op.drop_table("findings")
    op.drop_table("metric_observations")
    op.drop_table("resource_snapshots")


def downgrade():
    op.create_table(
        "resource_snapshots",
        sa.Column("id", sa.Integer(), primary_key=True, index=True),
        sa.Column("resource_id", sa.String(length=255), nullable=False, index=True),
        sa.Column("resource_type", sa.String(length=100), nullable=False, index=True),
        sa.Column("resource_name", sa.String(length=255), nullable=True),
        sa.Column("cloud_provider", sa.String(length=50), nullable=False, server_default="aws", index=True),
        sa.Column("account_id", sa.String(length=100), nullable=True, index=True),
        sa.Column("region", sa.String(length=50), nullable=True, index=True),
        sa.Column("state", sa.String(length=50), nullable=True),
        sa.Column("instance_type", sa.String(length=100), nullable=True),
        sa.Column("size_gb", sa.Integer(), nullable=True),
        sa.Column("tags_json", sa.JSON(), nullable=True),
        sa.Column("metadata_json", sa.JSON(), nullable=True),
        sa.Column("snapshot_timestamp", sa.DateTime(timezone=True), nullable=False, index=True),
        sa.Column("scan_id", sa.String(length=100), nullable=True, index=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )
    op.create_index(
        "idx_resource_snapshot_lookup",
        "resource_snapshots",
        ["resource_id", "resource_type", "snapshot_timestamp"],
    )

    op.create_table(
        "findings",
        sa.Column("id", sa.Integer(), primary_key=True, index=True),
        sa.Column("snapshot_id", sa.Integer(), sa.ForeignKey("resource_snapshots.id"), nullable=False),
        sa.Column("finding_type", sa.String(length=100), nullable=False, index=True),
        sa.Column("severity", sa.String(length=20), nullable=False, server_default="medium", index=True),
        sa.Column("title", sa.String(length=255), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("evidence_json", sa.JSON(), nullable=True),
        sa.Column("cpu_p50", sa.Float(), nullable=True),
        sa.Column("cpu_p95", sa.Float(), nullable=True),
        sa.Column("cpu_p99", sa.Float(), nullable=True),
        sa.Column("memory_p50", sa.Float(), nullable=True),
        sa.Column("memory_p95", sa.Float(), nullable=True),
        sa.Column("memory_p99", sa.Float(), nullable=True),
        sa.Column("network_in_p95", sa.Float(), nullable=True),
        sa.Column("network_out_p95", sa.Float(), nullable=True),
        sa.Column("observation_days", sa.Integer(), nullable=True),
        sa.Column("observation_start", sa.DateTime(timezone=True), nullable=True),
        sa.Column("observation_end", sa.DateTime(timezone=True), nullable=True),
        sa.Column("is_production", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("has_owner", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("has_redundancy", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("recent_incident", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("status", sa.String(length=20), nullable=False, server_default="open", index=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), index=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )
    op.create_index(
        "idx_finding_type_status", "findings", ["finding_type", "status", "created_at"]
    )

    op.create_table(
        "enriched_candidates",
        sa.Column("id", sa.Integer(), primary_key=True, index=True),
        sa.Column("finding_id", sa.Integer(), sa.ForeignKey("findings.id"), nullable=False),
        sa.Column("dependency_graph_json", sa.JSON(), nullable=True),
        sa.Column("business_rules_json", sa.JSON(), nullable=True),
        sa.Column("compliance_tags_json", sa.JSON(), nullable=True),
        sa.Column("risk_score", sa.Float(), nullable=True),
        sa.Column("risk_factors_json", sa.JSON(), nullable=True),
        sa.Column("can_modify", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("requires_approval", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("approval_reason", sa.Text(), nullable=True),
        sa.Column("status", sa.String(length=20), nullable=False, server_default="pending", index=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )

    op.create_table(
        "recommendations",
        sa.Column("id", sa.Integer(), primary_key=True, index=True),
        sa.Column("candidate_id", sa.Integer(), sa.ForeignKey("enriched_candidates.id"), nullable=False),
        sa.Column("recommendation_type", sa.String(length=100), nullable=False, index=True),
        sa.Column("recommendation_engine", sa.String(length=50), nullable=False),
        sa.Column("current_config_json", sa.JSON(), nullable=False),
        sa.Column("target_config_json", sa.JSON(), nullable=False),
        sa.Column("change_plan_json", sa.JSON(), nullable=True),
        sa.Column("estimated_monthly_savings", sa.Float(), nullable=False),
        sa.Column("estimated_annual_savings", sa.Float(), nullable=True),
        sa.Column("cost_breakdown_json", sa.JSON(), nullable=True),
        sa.Column("confidence_score", sa.Float(), nullable=False),
        sa.Column("risk_score", sa.Float(), nullable=False),
        sa.Column("risk_factors_json", sa.JSON(), nullable=True),
        sa.Column("assumptions_json", sa.JSON(), nullable=True),
        sa.Column("evidence_window_start", sa.DateTime(timezone=True), nullable=True),
        sa.Column("evidence_window_end", sa.DateTime(timezone=True), nullable=True),
        sa.Column("worst_case_spike_json", sa.JSON(), nullable=True),
        sa.Column("rollback_plan_json", sa.JSON(), nullable=True),
        sa.Column("validation_checklist_json", sa.JSON(), nullable=True),
        sa.Column("status", sa.String(length=20), nullable=False, server_default="draft", index=True),
        sa.Column("workflow_state_json", sa.JSON(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), index=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index(
        "idx_recommendation_status_type",
        "recommendations",
        ["status", "recommendation_type", "created_at"],
    )

    op.create_table(
        "change_plans",
        sa.Column("id", sa.Integer(), primary_key=True, index=True),
        sa.Column("recommendation_id", sa.Integer(), sa.ForeignKey("recommendations.id"), nullable=False),
        sa.Column("plan_type", sa.String(length=50), nullable=False),
        sa.Column("steps_json", sa.JSON(), nullable=False),
        sa.Column("scheduled_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("executed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("status", sa.String(length=20), nullable=False, server_default="pending", index=True),
        sa.Column("execution_result_json", sa.JSON(), nullable=True),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("approved_by", sa.String(length=255), nullable=True),
        sa.Column("approved_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("approval_ticket", sa.String(length=255), nullable=True),
        sa.Column("verification_status", sa.String(length=20), nullable=True),
        sa.Column("verification_result_json", sa.JSON(), nullable=True),
        sa.Column("verified_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )

    op.create_table(
        "metric_observations",
        sa.Column("id", sa.Integer(), primary_key=True, index=True),
        sa.Column("resource_id", sa.String(length=255), nullable=False, index=True),
        sa.Column("resource_type", sa.String(length=100), nullable=False, index=True),
        sa.Column("metric_name", sa.String(length=100), nullable=False, index=True),
        sa.Column("metric_value", sa.Float(), nullable=False),
        sa.Column("metric_unit", sa.String(length=50), nullable=True),
        sa.Column("observed_at", sa.DateTime(timezone=True), nullable=False, index=True),
        sa.Column("dimensions_json", sa.JSON(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )
    op.create_index(
        "idx_metric_resource_time",
        "metric_observations",
        ["resource_id", "metric_name", "observed_at"],
    )
