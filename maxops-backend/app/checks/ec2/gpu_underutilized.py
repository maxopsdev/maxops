"""EC2 check for instances whose paid GPUs are idle while CPU work continues."""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional

from app.checks.base import create_check_reason, estimate_ec2_monthly_cost
from app.checks.ec2.gpu_utils import gpu_is_idle, gpu_metadata, gpu_unavailable_reason
from app.checks.registry import CheckMetadata, check_registry
from app.utils.ec2_gpu_info import gpu_info_for_instance_type


def check_ec2_gpu_underutilized(
    aws_adapter,
    lookback_days: int = 7,
    cpu_threshold: float = 5.0,
    gpu_threshold: float = 5.0,
    gpu_max_threshold: float = 15.0,
    region: Optional[str] = None,
) -> List[Dict[str, Any]]:
    """Find GPU instances doing CPU work while every GPU is sustained-idle.

    GPU telemetry is mandatory for this finding. An unavailable signal is not
    evidence of idle hardware and therefore produces a logged skip.
    """
    instances = aws_adapter.get_resources("ec2", {"state": "running"}, region)
    end = datetime.utcnow()
    start = end - timedelta(days=lookback_days)
    flagged: List[Dict[str, Any]] = []

    for instance in instances:
        metadata = instance.setdefault("metadata", {})
        instance_type = metadata.get("instance_type") or instance.get("instance_type", "")
        metadata.setdefault("instance_type", instance_type)
        gpu_info = gpu_info_for_instance_type(instance_type)
        if gpu_info is None:
            continue

        try:
            utilization = aws_adapter.get_resource_utilization(
                instance["resource_id"],
                "ec2",
                start,
                end,
                region=instance.get("region") or region,
            )
        except Exception as exc:
            print(
                f"Error checking EC2 GPU underutilization "
                f"{instance['resource_id']}: {exc}"
            )
            continue

        if utilization.get("gpu_metric_status") != "usable":
            print(
                f"[EC2_GPU_UNDERUTILIZED] Skipping {instance['resource_id']}: "
                f"GPU instance ({instance_type}, {gpu_info.device_count} devices) "
                f"without usable GPU telemetry ({gpu_unavailable_reason(utilization)})"
            )
            continue
        if not gpu_is_idle(utilization, gpu_threshold, gpu_max_threshold):
            continue

        cpu = float(utilization.get("cpuutilization", 0.0) or 0.0)
        # Instances below this CPU average belong to ec2_idle_instances, which
        # owns the stop recommendation. Keep the two findings mutually exclusive.
        if cpu < cpu_threshold:
            continue

        gpu_summary = utilization.get("metric_summary", {}).get("gpuutilization", {})
        gpu_p95 = gpu_summary.get("p95")
        metadata.update(gpu_metadata(gpu_info, utilization, gpu_threshold))
        metadata["avg_cpu_utilization"] = cpu
        metadata["lookback_days"] = lookback_days
        metadata["recommended_action"] = "rightsize"
        metadata["recommended_actions"] = ["rightsize"]
        metadata["potential_savings_monthly"] = round(
            estimate_ec2_monthly_cost(instance_type), 2
        )
        metadata["potential_savings_disclosure"] = (
            "Full current instance cost is shown; true savings are the delta to "
            "a validated non-GPU replacement and are not estimated here."
        )
        if "metric_history" in utilization:
            metadata["metric_history"] = utilization["metric_history"]
        metadata["check_reason"] = create_check_reason(
            "underutilized",
            {
                "reason": (
                    f"GPUs idle (p95 {gpu_p95:.1f}%) while CPU averages {cpu:.1f}% "
                    "— workload does not appear to use the GPU."
                )
            },
        )
        flagged.append(instance)

    return flagged


check_registry.register(
    CheckMetadata(
        check_id="ec2_gpu_underutilized",
        name="EC2 GPU Underutilized",
        description="Identifies GPU instances doing CPU work while their GPUs remain idle",
        resource_type="ec2",
        check_function=check_ec2_gpu_underutilized,
        default_action="rightsize",
        parameters={
            "lookback_days": 7,
            "cpu_threshold": 5.0,
            "gpu_threshold": 5.0,
            "gpu_max_threshold": 15.0,
            "region": None,
        },
    )
)
