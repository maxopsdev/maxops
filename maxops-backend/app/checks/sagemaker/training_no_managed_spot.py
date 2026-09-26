"""SageMaker recurring training jobs without managed spot."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from statistics import median
from typing import Any, Dict, List, Optional

from app.checks.registry import CheckMetadata, check_registry
from app.checks.sagemaker.common import (
    create_sagemaker_price_disclosure,
    parse_time,
    resource_metadata,
    training_job_family,
)


def check_sagemaker_training_no_managed_spot(
    aws_adapter: Any,
    job_history_days: int = 30,
    minimum_runs: int = 3,
    spot_discount: float = 0.6,
    family_patterns: Optional[List[str]] = None,
    runs_per_month: float = 30.0,
    region: Optional[str] = None,
) -> List[Dict[str, Any]]:
    """Find recurring completed job families whose runs are all on demand."""
    resources = aws_adapter.get_resources(
        "sagemaker_training_job", {"job_history_days": job_history_days}, region
    )
    now = datetime.now(timezone.utc)
    grouped: Dict[str, List[Dict[str, Any]]] = {}
    for job in resources:
        metadata = resource_metadata(job)
        status = metadata.get("training_job_status") or metadata.get("TrainingJobStatus") or job.get("status") or job.get("state")
        if str(status or "").casefold() != "completed":
            continue
        created = parse_time(metadata.get("creation_time") or job.get("created_at"))
        if created and now - created > timedelta(days=job_history_days):
            continue
        family = training_job_family(job.get("resource_name") or job.get("resource_id"), family_patterns)
        grouped.setdefault(family, []).append(job)
    flagged: List[Dict[str, Any]] = []
    for family, runs in grouped.items():
        if len(runs) < minimum_runs:
            print(f"[SAGEMAKER_TRAINING_SPOT] Skipping {family}: fewer_than_minimum_runs")
            continue
        if any(
            bool(
                resource_metadata(run).get("enable_managed_spot_training")
                if "enable_managed_spot_training" in resource_metadata(run)
                else resource_metadata(run).get("EnableManagedSpotTraining")
            )
            for run in runs
        ):
            continue
        first_metadata = resource_metadata(runs[0])
        durations = [
            float(
                resource_metadata(run).get("training_time_seconds")
                or resource_metadata(run).get("TrainingTimeInSeconds")
            )
            for run in runs
            if resource_metadata(run).get("training_time_seconds") is not None
            or resource_metadata(run).get("TrainingTimeInSeconds") is not None
        ]
        median_duration = float(median(durations)) if durations else None
        count = int(first_metadata.get("instance_count") or runs[0].get("instance_count") or 1)
        output = dict(runs[0])
        metadata = dict(first_metadata)
        metadata.update(
            {
                "job_family": family,
                "runs_observed": len(runs),
                "median_training_time_seconds": median_duration,
                "instance_type": first_metadata.get("instance_type") or first_metadata.get("InstanceType") or runs[0].get("instance_type"),
                "instance_count": count,
                "checkpoint_config_present": all(bool(resource_metadata(run).get("checkpoint_config_present") or resource_metadata(run).get("CheckpointConfig")) for run in runs),
                "recommended_action": "enable_managed_spot_training",
                "recommended_actions": ["enable_managed_spot_training"],
                "spot_discount": spot_discount,
                "runs_per_month": runs_per_month,
                "potential_savings_monthly": None,
                "pricing_component": "Training",
                "check_reason": "Recurring training runs are all on-demand; managed spot savings vary and checkpointing is required to protect progress." if not all(bool(resource_metadata(run).get("checkpoint_config_present") or resource_metadata(run).get("CheckpointConfig")) for run in runs) else "Recurring training runs are all on-demand.",
                "savings_disclosure": create_sagemaker_price_disclosure("Training") + " Spot savings vary by capacity and interruption; this applies to future runs.",
            }
        )
        metadata["sagemaker_savings_basis"] = (
            median_duration / 3600.0 * count * runs_per_month * spot_discount
            if median_duration is not None
            else None
        )
        output["metadata"] = metadata
        flagged.append(output)
    return flagged


check_registry.register(CheckMetadata(
    check_id="sagemaker_training_no_managed_spot",
    name="SageMaker Training Without Managed Spot",
    description="Finds recurring training families that always run on demand",
    resource_type="sagemaker",
    check_function=check_sagemaker_training_no_managed_spot,
    default_action="enable_managed_spot_training",
    parameters={"job_history_days": 30, "minimum_runs": 3, "spot_discount": 0.6, "runs_per_month": 30.0, "region": None},
))
