"""SageMaker notebook agent telemetry idle check."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

from app.checks.base import create_check_reason
from app.checks.ec2.gpu_utils import gpu_is_idle, gpu_metadata
from app.checks.registry import CheckMetadata, check_registry
from app.checks.sagemaker.common import (
    create_sagemaker_price_disclosure,
    metric_summary,
    resource_metadata,
    resolve_sagemaker_instance_type,
    signal_status,
)


def check_sagemaker_notebook_idle(
    aws_adapter: Any,
    lookback_days: int = 7,
    cpu_threshold: float = 5.0,
    cpu_max_threshold: float = 15.0,
    gpu_threshold: float = 5.0,
    gpu_max_threshold: float = 15.0,
    region: Optional[str] = None,
) -> List[Dict[str, Any]]:
    """Find notebooks idle by agent CPU and, for GPU types, GPU evidence."""
    resources = aws_adapter.get_resources("sagemaker_notebook", {"status": "InService"}, region)
    now = datetime.now(timezone.utc)
    flagged: List[Dict[str, Any]] = []
    for notebook in resources:
        metadata = resource_metadata(notebook)
        status = str(metadata.get("notebook_status") or notebook.get("status") or notebook.get("state") or "")
        if status.casefold() != "inservice":
            continue
        resolved = resolve_sagemaker_instance_type(metadata.get("instance_type") or notebook.get("instance_type"))
        try:
            utilization = aws_adapter.get_resource_utilization(
                notebook["resource_id"], "sagemaker_notebook", now - timedelta(days=lookback_days), now, region=notebook.get("region") or region
            )
        except Exception as exc:
            print(f"[SAGEMAKER_NOTEBOOK_IDLE] Skipping {notebook.get('resource_id')}: query_failed ({exc})")
            continue
        cpu_status, cpu_reason = signal_status(utilization, "cpu")
        if cpu_status != "usable":
            # Some adapters expose the canonical EC2-style key for agent CPU.
            cpu_status = utilization.get("cpu_metric_status") or utilization.get("cpuutilization_metric_status")
            cpu_reason = utilization.get("cpu_metric_unavailable_reason") or cpu_reason
        cpu_summary = metric_summary(utilization, "cpuutilization")
        cpu_p95 = cpu_summary.get("p95")
        cpu_max = cpu_summary.get("maximum")
        if cpu_status != "usable" or cpu_p95 is None or cpu_max is None:
            print(f"[SAGEMAKER_NOTEBOOK_IDLE] Skipping {notebook.get('resource_id')}: {cpu_reason or 'no_datapoints'}")
            continue
        if float(cpu_p95) >= cpu_threshold or float(cpu_max) >= cpu_max_threshold:
            continue
        if resolved["is_gpu"]:
            gpu_status = utilization.get("gpu_metric_status")
            if gpu_status != "usable":
                print(f"[SAGEMAKER_NOTEBOOK_IDLE] Skipping {notebook.get('resource_id')}: {utilization.get('gpu_metric_unavailable_reason') or 'no_datapoints'}")
                continue
            if not gpu_is_idle(utilization, gpu_threshold, gpu_max_threshold):
                continue
            metadata.update(gpu_metadata(resolved["gpu_info"], utilization, gpu_threshold))
        metadata.update(
            {
                "cpu_metric_status": "usable",
                "cpu_p95": float(cpu_p95),
                "cpu_max": float(cpu_max),
                "lookback_days": lookback_days,
                "recommended_action": "stop_notebook",
                "recommended_actions": ["stop_notebook", "attach_auto_stop_lifecycle_config"],
                "potential_savings_monthly": None,
                "pricing_component": "Notebook",
                "sagemaker_savings_basis": 730.0,
                "savings_disclosure": create_sagemaker_price_disclosure("Notebook") + " EBS volume cost continues while stopped.",
                "check_reason": create_check_reason("idle", {"idle_days": lookback_days, "avg_cpu": float(cpu_p95), "cpu_threshold": cpu_threshold}),
            }
        )
        notebook["metadata"] = metadata
        flagged.append(notebook)
    return flagged


check_registry.register(CheckMetadata(
    check_id="sagemaker_notebook_idle",
    name="SageMaker Notebook Idle",
    description="Finds InService notebooks that stay idle according to agent telemetry",
    resource_type="sagemaker",
    check_function=check_sagemaker_notebook_idle,
    default_action="stop_notebook",
    parameters={"lookback_days": 7, "cpu_threshold": 5.0, "cpu_max_threshold": 15.0, "gpu_threshold": 5.0, "gpu_max_threshold": 15.0, "region": None},
))
