"""ECS overprovisioned task CPU/memory reservations."""
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional, Tuple

from app.checks.base import create_check_reason
from app.checks.registry import CheckMetadata, check_registry


FARGATE_VCPU_PRICE_PER_HOUR = 0.04048
FARGATE_MEMORY_GB_PRICE_PER_HOUR = 0.004445
HOURS_PER_MONTH = 730


def _to_int(value: Any) -> Optional[int]:
    try:
        if value is None:
            return None
        return int(value)
    except (TypeError, ValueError):
        return None


def _round_up(value: int, step: int, minimum: int) -> int:
    if value <= minimum:
        return minimum
    return ((value + step - 1) // step) * step


def _closest_fargate_combo(cpu: int, memory: int) -> Tuple[int, int]:
    """Pick the smallest valid Fargate CPU/memory pair >= requested memory."""
    valid_memory = {
        256: [512, 1024, 2048],
        512: [1024, 2048, 3072, 4096],
        1024: list(range(2048, 8193, 1024)),
        2048: list(range(4096, 16385, 1024)),
        4096: list(range(8192, 30721, 1024)),
    }
    cpu_options = sorted(valid_memory.keys())
    selected_cpu = cpu_options[-1]
    for candidate in cpu_options:
        if candidate >= cpu:
            selected_cpu = candidate
            break

    selected_memory = valid_memory[selected_cpu][-1]
    for candidate_mem in valid_memory[selected_cpu]:
        if candidate_mem >= memory:
            selected_memory = candidate_mem
            break
    return selected_cpu, selected_memory


def _estimate_fargate_monthly_cost(task_cpu: int, task_memory: int, desired_count: int) -> float:
    per_task_hourly = (task_cpu / 1024.0) * FARGATE_VCPU_PRICE_PER_HOUR + (
        task_memory / 1024.0
    ) * FARGATE_MEMORY_GB_PRICE_PER_HOUR
    return per_task_hourly * desired_count * HOURS_PER_MONTH


def check_ecs_overprovisioned_task_cpu_memory_reservations(
    aws_adapter,
    lookback_days: int = 14,
    utilization_threshold_pct: float = 40.0,
    target_utilization_pct: float = 60.0,
    min_desired_count: int = 1,
    region: Optional[str] = None,
) -> List[Dict[str, Any]]:
    """Find ECS services with low CPU/memory utilization relative to task reservations."""
    services = aws_adapter.get_resources("ecs", region=region)
    end_date = datetime.utcnow()
    start_date = end_date - timedelta(days=lookback_days)
    flagged: List[Dict[str, Any]] = []

    for service in services:
        metadata = service.get("metadata", {})
        desired_count = _to_int(metadata.get("desiredCount")) or 0
        if desired_count < min_desired_count:
            continue

        task_cpu = _to_int(metadata.get("taskCpu"))
        task_memory = _to_int(metadata.get("taskMemory"))
        if not task_cpu or not task_memory:
            continue

        resource_id = service.get("resource_id")
        if not resource_id:
            continue

        utilization = aws_adapter.get_resource_utilization(
            resource_id=resource_id,
            resource_type="ecs",
            start_date=start_date,
            end_date=end_date,
            region=service.get("region") or region,
        )
        cpu_util = float(utilization.get("cpu_utilization") or 0.0)
        memory_util = float(utilization.get("memory_utilization") or 0.0)

        if cpu_util > utilization_threshold_pct or memory_util > utilization_threshold_pct:
            continue

        # Scale current reservation toward target utilization with a small buffer.
        cpu_ratio = max(0.5, (cpu_util / max(target_utilization_pct, 1.0)) * 1.2)
        memory_ratio = max(0.5, (memory_util / max(target_utilization_pct, 1.0)) * 1.2)
        proposed_cpu = _round_up(int(task_cpu * cpu_ratio), step=256, minimum=256)
        proposed_memory = _round_up(int(task_memory * memory_ratio), step=512, minimum=512)
        launch_type = str(metadata.get("launchType") or "").upper()
        if not launch_type:
            compat = [str(v).upper() for v in (metadata.get("taskRequiresCompatibilities") or [])]
            if "FARGATE" in compat:
                launch_type = "FARGATE"
        if launch_type == "FARGATE":
            proposed_cpu, proposed_memory = _closest_fargate_combo(proposed_cpu, proposed_memory)

        if proposed_cpu >= task_cpu and proposed_memory >= task_memory:
            continue

        current_monthly_cost = 0.0
        optimized_monthly_cost = 0.0
        if launch_type == "FARGATE":
            current_monthly_cost = _estimate_fargate_monthly_cost(task_cpu, task_memory, desired_count)
            optimized_monthly_cost = _estimate_fargate_monthly_cost(
                proposed_cpu, proposed_memory, desired_count
            )
        savings_monthly = max(0.0, current_monthly_cost - optimized_monthly_cost)

        metadata["current_task_cpu"] = task_cpu
        metadata["current_task_memory"] = task_memory
        metadata["recommended_task_cpu"] = proposed_cpu
        metadata["recommended_task_memory"] = proposed_memory
        metadata["cpu_utilization_pct"] = round(cpu_util, 2)
        metadata["memory_utilization_pct"] = round(memory_util, 2)
        metadata["lookback_days"] = lookback_days
        metadata["estimated_monthly_cost"] = round(current_monthly_cost, 4)
        metadata["optimized_monthly_cost"] = round(optimized_monthly_cost, 4)
        metadata["potential_savings_monthly"] = round(savings_monthly, 4)
        metadata["potential_savings_yearly"] = round(savings_monthly * 12, 4)
        metadata["recommended_action"] = "rightsize_task_definition"
        metadata["check_reason"] = create_check_reason(
            "unused",
            {
                "reason": (
                    f"CPU {cpu_util:.1f}% and memory {memory_util:.1f}% are below "
                    f"{utilization_threshold_pct:.1f}% reservation threshold"
                ),
            },
        )
        service["metadata"] = metadata
        flagged.append(service)

    return flagged


check_registry.register(
    CheckMetadata(
        check_id="ecs_overprovisioned_task_cpu_memory_reservations",
        name="ECS Overprovisioned task CPU/memory reservations",
        description="Identifies ECS services with low utilization compared to reserved task CPU/memory.",
        resource_type="ecs",
        check_function=check_ecs_overprovisioned_task_cpu_memory_reservations,
        default_action="ecs_rightsize_task_definition",
        parameters={
            "lookback_days": 14,
            "utilization_threshold_pct": 40.0,
            "target_utilization_pct": 60.0,
            "min_desired_count": 1,
            "region": None,
        },
    )
)
