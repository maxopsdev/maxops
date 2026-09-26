"""ECS services with desired count zero but still present."""
from typing import Any, Dict, List, Optional

from app.checks.base import create_check_reason
from app.checks.registry import CheckMetadata, check_registry


def check_ecs_services_desired_count_zero(
    aws_adapter,
    require_running_zero: bool = True,
    region: Optional[str] = None,
) -> List[Dict[str, Any]]:
    """Find ECS services set to desiredCount=0 that are still active."""
    services = aws_adapter.get_resources("ecs", region=region)
    flagged: List[Dict[str, Any]] = []

    for service in services:
        metadata = service.get("metadata", {})
        desired_count = int(metadata.get("desiredCount") or 0)
        running_count = int(metadata.get("runningCount") or 0)
        state = str(service.get("state") or "ACTIVE").upper()

        if state != "ACTIVE":
            continue
        if desired_count != 0:
            continue
        if require_running_zero and running_count > 0:
            continue

        metadata["recommended_action"] = "delete_service"
        metadata["potential_savings_monthly"] = 0.0
        metadata["potential_savings_yearly"] = 0.0
        metadata["check_reason"] = create_check_reason(
            "unused",
            {
                "reason": (
                    f"Service desiredCount is 0"
                    f"{' and runningCount is 0' if require_running_zero else ''}"
                ),
            },
        )
        service["metadata"] = metadata
        flagged.append(service)

    return flagged


check_registry.register(
    CheckMetadata(
        check_id="ecs_services_desired_count_zero",
        name="ECS Services desiredCount=0 but still present",
        description="Identifies active ECS services configured with desiredCount=0 that can usually be removed.",
        resource_type="ecs",
        check_function=check_ecs_services_desired_count_zero,
        default_action="ecs_delete_service",
        parameters={
            "require_running_zero": True,
            "region": None,
        },
    )
)
