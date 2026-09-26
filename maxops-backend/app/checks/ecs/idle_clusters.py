"""ECS idle clusters check."""
from typing import Any, Dict, List, Optional

from app.checks.base import create_check_reason
from app.checks.registry import CheckMetadata, check_registry


def check_ecs_idle_clusters(
    aws_adapter,
    require_zero_pending_tasks: bool = True,
    region: Optional[str] = None,
) -> List[Dict[str, Any]]:
    """Find ECS clusters with no active services and no running tasks."""
    clusters = aws_adapter.get_resources("ecs_cluster", region=region)
    flagged: List[Dict[str, Any]] = []

    for cluster in clusters:
        metadata = cluster.get("metadata", {})
        active_services = int(metadata.get("activeServicesCount") or 0)
        running_tasks = int(metadata.get("runningTasksCount") or 0)
        pending_tasks = int(metadata.get("pendingTasksCount") or 0)
        state = str(cluster.get("state") or "ACTIVE").upper()

        if state != "ACTIVE":
            continue
        if active_services != 0:
            continue
        if running_tasks != 0:
            continue
        if require_zero_pending_tasks and pending_tasks != 0:
            continue

        metadata["recommended_action"] = "delete_cluster"
        metadata["potential_savings_monthly"] = 0.0
        metadata["potential_savings_yearly"] = 0.0
        metadata["check_reason"] = create_check_reason(
            "unused",
            {
                "reason": (
                    f"No active services and no running tasks"
                    f"{' with no pending tasks' if require_zero_pending_tasks else ''}"
                ),
            },
        )
        cluster["metadata"] = metadata
        flagged.append(cluster)

    return flagged


check_registry.register(
    CheckMetadata(
        check_id="ecs_idle_clusters_no_active_services_tasks",
        name="ECS Idle clusters (no active services/tasks)",
        description="Identifies ECS clusters that have no active services or running tasks.",
        resource_type="ecs",
        check_function=check_ecs_idle_clusters,
        default_action="ecs_delete_cluster",
        parameters={
            "require_zero_pending_tasks": True,
            "region": None,
        },
    )
)
