"""Glue underutilized DCU/DPU check."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

from app.checks.base import create_check_reason
from app.checks.registry import CheckMetadata, check_registry


def _to_float(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _worker_type_dpu(worker_type: str) -> float:
    wt = str(worker_type or "").upper()
    # AWS Glue worker type to DPU mapping
    mapping = {
        "STANDARD": 1.0,
        "G.025X": 0.25,
        "G.1X": 1.0,
        "G.2X": 2.0,
        "G.4X": 4.0,
        "G.8X": 8.0,
        "Z.2X": 2.0,
    }
    return mapping.get(wt, 1.0)


def _provisioned_dpu_for_run(run: Dict[str, Any], job: Dict[str, Any]) -> float:
    # Prefer run-level details first.
    if run.get("MaxCapacity") is not None:
        return max(0.0, _to_float(run.get("MaxCapacity")))
    if run.get("NumberOfWorkers") is not None:
        workers = _to_float(run.get("NumberOfWorkers"))
        return max(0.0, workers * _worker_type_dpu(str(run.get("WorkerType") or "")))

    # Fallback to job defaults.
    if job.get("MaxCapacity") is not None:
        return max(0.0, _to_float(job.get("MaxCapacity")))
    if job.get("NumberOfWorkers") is not None:
        workers = _to_float(job.get("NumberOfWorkers"))
        return max(0.0, workers * _worker_type_dpu(str(job.get("WorkerType") or "")))

    # Conservative fallback for Spark jobs if unspecified.
    return 1.0


def _is_within_lookback(started_on: Any, start_cutoff: datetime) -> bool:
    if not isinstance(started_on, datetime):
        return False
    dt = started_on.astimezone(timezone.utc) if started_on.tzinfo else started_on.replace(tzinfo=timezone.utc)
    return dt >= start_cutoff


def check_glue_underutilized_dcu(
    aws_adapter,
    lookback_days: int = 14,
    min_runs: int = 3,
    max_avg_dpu_utilization: float = 0.4,
    include_job_commands: Optional[List[str]] = None,
    region: Optional[str] = None,
) -> List[Dict[str, Any]]:
    """
    Identify Glue jobs whose recent runs use a low share of provisioned DPU.
    Utilization per run: DPUSeconds / (provisioned_dpu * execution_time_seconds).
    """
    if lookback_days <= 0:
        return []

    glue = aws_adapter.session.client("glue", region_name=region)
    start_cutoff = datetime.now(timezone.utc) - timedelta(days=lookback_days)
    allowed_commands = {c.lower() for c in (include_job_commands or ["glueetl", "pythonshell"])}

    flagged: List[Dict[str, Any]] = []
    paginator = glue.get_paginator("get_jobs")
    for page in paginator.paginate():
        for job in page.get("Jobs", []):
            job_name = job.get("Name")
            if not job_name:
                continue

            command_name = str((job.get("Command") or {}).get("Name") or "").lower()
            if allowed_commands and command_name and command_name not in allowed_commands:
                continue

            try:
                runs = glue.get_job_runs(JobName=job_name, MaxResults=50).get("JobRuns", [])
                run_utils: List[float] = []

                for run in runs:
                    if not _is_within_lookback(run.get("StartedOn"), start_cutoff):
                        continue
                    if str(run.get("JobRunState") or "").upper() not in {"SUCCEEDED", "STOPPED", "FAILED", "TIMEOUT"}:
                        continue

                    dpu_seconds = _to_float(run.get("DPUSeconds"))
                    execution_seconds = _to_float(run.get("ExecutionTime"))
                    provisioned_dpu = _provisioned_dpu_for_run(run, job)
                    if dpu_seconds <= 0 or execution_seconds <= 0 or provisioned_dpu <= 0:
                        continue

                    possible_dpu_seconds = provisioned_dpu * execution_seconds
                    util = dpu_seconds / possible_dpu_seconds if possible_dpu_seconds > 0 else 0.0
                    run_utils.append(max(0.0, min(util, 1.0)))

                if len(run_utils) < min_runs:
                    continue

                avg_util = sum(run_utils) / len(run_utils)
                if avg_util > max_avg_dpu_utilization:
                    continue

                flagged.append(
                    {
                        "resource_id": job_name,
                        "resource_type": "glue_job",
                        "resource_name": job_name,
                        "region": region,
                        "state": "active",
                        "metadata": {
                            "lookback_days": lookback_days,
                            "evaluated_runs": len(run_utils),
                            "min_runs": min_runs,
                            "avg_dpu_utilization": round(avg_util, 3),
                            "max_avg_dpu_utilization": max_avg_dpu_utilization,
                            "job_command": command_name or None,
                            "recommended_action": "reduce_workers_or_dpu",
                            "check_reason": create_check_reason(
                                "underutilized",
                                {
                                    "resource": "glue_job",
                                    "job_name": job_name,
                                    "avg_dpu_utilization": round(avg_util, 3),
                                    "threshold": max_avg_dpu_utilization,
                                },
                            ),
                        },
                    }
                )
            except Exception as exc:
                print(f"Error checking Glue DCU utilization for {job_name}: {exc}")
                continue

    return flagged


check_registry.register(
    CheckMetadata(
        check_id="glue_underutilized_dcu",
        name="Glue Underutilized DCU",
        description="Identifies Glue jobs with low DPU utilization over recent runs",
        resource_type="glue_job",
        check_function=check_glue_underutilized_dcu,
        default_action="reduce_workers_or_dpu",
        parameters={
            "lookback_days": 14,
            "min_runs": 3,
            "max_avg_dpu_utilization": 0.4,
            "include_job_commands": ["glueetl", "pythonshell"],
            "region": None,
        },
    )
)

