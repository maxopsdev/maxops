"""Preflight checks, cost estimates, and background jobs for cost-data setup.

Setting up cost data is the one place in MaxOps that writes to AWS and spends
money doing it: it creates an S3 bucket and a CUR export, then runs Athena
queries billed per terabyte scanned. So the flow here is deliberately
front-loaded — tell the operator what permissions are missing and roughly what
a refresh will cost, then do the work in the background with visible progress.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from sqlalchemy.orm import Session

from app.config import settings
from app.models.cur import CurSetupJob
from app.services.aws_credentials import create_setup_boto3_session
from app.services.cur_cost_service import cur_cost_lookup
from pricing.cur.cache import plan_refresh_tasks
from pricing.cur.datasets import DEFAULT_DATABASE, DEFAULT_RAW_TABLE
from pricing.cur.months import BillingMonth
from pricing.cur.reader import RESOURCE_COST_DATASET
from stacks.CUR.create_cur_export import (
    DEFAULT_EXPORT_NAME,
    DEFAULT_PREFIX,
    default_bucket_name,
    normalize_prefix,
)

logger = logging.getLogger(__name__)


JOB_EXPORT = "export"
JOB_REFRESH = "refresh"

# What each setup action needs. Used both for the preflight simulation and to
# show the operator an accurate list when simulation is unavailable.
REQUIRED_ACTIONS: Dict[str, List[str]] = {
    JOB_EXPORT: [
        "s3:CreateBucket",
        "s3:PutBucketPolicy",
        # HeadBucket is authorised by s3:ListBucket; there is no s3:HeadBucket
        # IAM action, and simulating one reports a denial that never resolves.
        "s3:ListBucket",
        "bcm-data-exports:CreateExport",
        "bcm-data-exports:ListExports",
        # CreateExport is a front end over the legacy CUR API and authorises
        # against this too. Omitting it makes preflight pass and the job fail.
        "cur:PutReportDefinition",
        "athena:StartQueryExecution",
        "athena:GetQueryExecution",
        "glue:CreateDatabase",
        "glue:CreateTable",
        "glue:GetTable",
    ],
    JOB_REFRESH: [
        "s3:GetObject",
        "s3:PutObject",
        "s3:ListBucket",
        "athena:StartQueryExecution",
        "athena:GetQueryExecution",
        "glue:GetTable",
    ],
}

# Athena's standard on-demand rate. Only used to turn bytes into a rough
# dollar figure for the confirmation dialog.
ATHENA_USD_PER_TB = 5.0
BYTES_PER_TB = 1024**4


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


# ------------------------------------------------------------------ preflight


def iam_principal_arn(caller_arn: str) -> str:
    """Convert an STS caller ARN into the IAM principal ARN.

    get_caller_identity returns the *session* ARN when running under an
    assumed role — arn:aws:sts::123:assumed-role/RoleName/session-name — but
    SimulatePrincipalPolicy only accepts an IAM user or role ARN. Without this
    conversion the simulation fails with InvalidInput and the caller is left
    with no permission check at all.
    """
    parts = caller_arn.split(":")
    if len(parts) < 6 or parts[2] != "sts":
        return caller_arn

    account = parts[4]
    resource = parts[5]
    if resource.startswith("assumed-role/"):
        role_name = resource.split("/")[1]
        return f"arn:aws:iam::{account}:role/{role_name}"
    return caller_arn


def _simulate(session, actions: List[str], region: str) -> Dict[str, str]:
    """Ask IAM whether the caller may perform each action.

    Resource-scoped actions have to be simulated against the resource they will
    actually touch. The cost-data policy grants S3 only on the report bucket,
    so simulating those without a resource evaluates them against "*" and
    reports them denied even though the real call would succeed.

    Raises when the simulation itself is not permitted, which is common:
    iam:SimulatePrincipalPolicy is not something most profiles carry.
    """
    sts = session.client("sts", region_name=region)
    identity = sts.get_caller_identity()
    iam = session.client("iam", region_name=region)

    bucket_arns = ["*"]
    account_id = str(identity.get("Account") or "")
    if account_id:
        bucket = f"arn:aws:s3:::{default_bucket_name(account_id)}"
        bucket_arns = [bucket, f"{bucket}/*"]

    s3_actions = [action for action in actions if action.startswith("s3:")]
    other_actions = [action for action in actions if not action.startswith("s3:")]

    results: Dict[str, str] = {}
    for group, resource_arns in ((other_actions, ["*"]), (s3_actions, bucket_arns)):
        # Simulate in batches; the API caps the action list per call.
        for start in range(0, len(group), 20):
            batch = group[start : start + 20]
            if not batch:
                continue
            response = iam.simulate_principal_policy(
                PolicySourceArn=iam_principal_arn(identity["Arn"]),
                ActionNames=batch,
                ResourceArns=resource_arns,
            )
            for evaluation in response.get("EvaluationResults", []):
                name = evaluation["EvalActionName"]
                decision = evaluation["EvalDecision"]
                # One action is evaluated once per resource; allowed against
                # any of them is enough, since the code only touches that
                # bucket.
                if results.get(name) != "allowed":
                    results[name] = decision
    return results


def preflight(job_type: str, region: Optional[str] = None) -> Dict[str, Any]:
    """Check whether the current AWS profile can perform a setup action."""
    resolved_region = region or settings.aws_region or "us-east-1"
    actions = REQUIRED_ACTIONS.get(job_type)
    if actions is None:
        raise ValueError(f"Unknown setup job type: {job_type}")

    try:
        session = create_setup_boto3_session(region_name=resolved_region)
    except Exception as exc:  # noqa: BLE001
        return {
            "job_type": job_type,
            "simulated": False,
            "can_proceed": False,
            "required_actions": actions,
            "denied_actions": [],
            "message": "No AWS credentials are configured.",
            "error": str(exc),
        }

    try:
        principal = iam_principal_arn(
            session.client("sts", region_name=resolved_region).get_caller_identity()["Arn"]
        )
    except Exception as exc:  # noqa: BLE001
        principal = None
        logger.info("Could not resolve the calling principal: %s", exc)

    try:
        decisions = _simulate(session, actions, resolved_region)
    except Exception as exc:  # noqa: BLE001
        # Simulation unavailable is not a failure — it just means we cannot
        # promise anything in advance. Let the operator decide, but say so
        # plainly rather than implying the check passed.
        logger.info("IAM policy simulation unavailable: %s", exc)
        return {
            "job_type": job_type,
            "principal": principal,
            "simulated": False,
            "can_proceed": True,
            "required_actions": actions,
            "denied_actions": [],
            "message": (
                "Permissions could not be checked in advance — this profile cannot run "
                "IAM policy simulation. Confirm it holds the permissions listed below "
                "before continuing; if it does not, the job will fail partway through."
            ),
            "error": str(exc),
        }

    denied = sorted(
        action for action, decision in (decisions or {}).items() if decision != "allowed"
    )
    return {
        "job_type": job_type,
        "principal": principal,
        "simulated": True,
        "can_proceed": not denied,
        "required_actions": actions,
        "denied_actions": denied,
        "message": (
            "This AWS profile has everything it needs."
            if not denied
            else f"{len(denied)} required permission(s) are missing from this profile."
        ),
        "error": None,
    }


# ------------------------------------------------------------- cost estimate


def estimate_refresh(
    *,
    cache_root: Optional[Path] = None,
    datasets: Optional[List[str]] = None,
    start_month: Optional[str] = None,
    end_month: Optional[str] = None,
    force: bool = False,
) -> Dict[str, Any]:
    """How many Athena queries a refresh would run, and roughly what it costs."""
    resolved_root = Path(cache_root or settings.cur_cache_root)
    tasks = plan_refresh_tasks(
        cache_root=resolved_root,
        datasets=datasets,
        start_month=BillingMonth.parse(start_month) if start_month else None,
        end_month=BillingMonth.parse(end_month) if end_month else None,
        force=force,
    )

    estimate: Dict[str, Any] = {
        "query_count": len(tasks),
        "datasets": sorted({task.dataset for task in tasks}),
        "months": sorted({str(task.month) for task in tasks}),
        "bytes_per_query": None,
        "estimated_usd": None,
        "note": (
            "Each planned month runs one Athena query. Athena bills per terabyte "
            "scanned, so cost depends on how large your bill is."
        ),
    }

    # A rough per-query scan size, taken from what the export has written.
    try:
        region = settings.aws_region or "us-east-1"
        session = create_setup_boto3_session(region_name=region)
        account_id = session.client("sts", region_name=region).get_caller_identity()["Account"]
        bucket = default_bucket_name(account_id)
        prefix = f"{normalize_prefix(DEFAULT_PREFIX)}/{DEFAULT_EXPORT_NAME}".strip("/")

        s3 = session.client("s3", region_name=region)
        total_bytes = 0
        token: Optional[str] = None
        while True:
            kwargs: Dict[str, Any] = {"Bucket": bucket, "Prefix": prefix}
            if token:
                kwargs["ContinuationToken"] = token
            page = s3.list_objects_v2(**kwargs)
            total_bytes += sum(item.get("Size", 0) for item in page.get("Contents") or [])
            if not page.get("IsTruncated"):
                break
            token = page.get("NextContinuationToken")

        estimate["export_bytes"] = total_bytes
        if len(tasks):
            estimate["estimated_usd"] = round(
                (total_bytes / BYTES_PER_TB) * ATHENA_USD_PER_TB * len(tasks), 2
            )
            estimate["note"] = (
                "Upper bound: assumes every query scans the whole export. Real cost "
                "is usually lower because queries are filtered to one month."
            )
    except Exception as exc:  # noqa: BLE001 - an estimate is a nicety, not a gate
        logger.info("Could not size the CUR export for a cost estimate: %s", exc)
        estimate["error"] = str(exc)

    return estimate


# ------------------------------------------------------------------ job state


def create_job(db: Session, job_type: str) -> CurSetupJob:
    job = CurSetupJob(
        job_type=job_type,
        status="running",
        progress_json={
            "phase": "starting",
            "message": "Starting…",
            "current": 0,
            "total": 0,
            "updated_at": _utc_now().isoformat(),
        },
    )
    db.add(job)
    db.commit()
    db.refresh(job)
    return job


def update_progress(
    db: Session,
    job_id: int,
    *,
    phase: str,
    message: str,
    current: int = 0,
    total: int = 0,
) -> None:
    job = db.query(CurSetupJob).filter(CurSetupJob.id == job_id).first()
    if not job:
        return
    job.progress_json = {
        "phase": phase,
        "message": message,
        "current": current,
        "total": total,
        "updated_at": _utc_now().isoformat(),
    }
    db.commit()


def complete_job(db: Session, job_id: int, result: Dict[str, Any]) -> None:
    job = db.query(CurSetupJob).filter(CurSetupJob.id == job_id).first()
    if not job:
        return
    job.status = "completed"
    job.result_json = result
    job.completed_at = _utc_now()
    job.progress_json = {
        **(job.progress_json or {}),
        "phase": "completed",
        "message": "Finished.",
        "updated_at": _utc_now().isoformat(),
    }
    db.commit()


def fail_job(db: Session, job_id: int, error: str) -> None:
    job = db.query(CurSetupJob).filter(CurSetupJob.id == job_id).first()
    if not job:
        return
    job.status = "failed"
    job.error_message = error
    job.completed_at = _utc_now()
    job.progress_json = {
        **(job.progress_json or {}),
        "phase": "failed",
        "message": error,
        "updated_at": _utc_now().isoformat(),
    }
    db.commit()


def job_state(job: CurSetupJob) -> Dict[str, Any]:
    return {
        "id": job.id,
        "job_type": job.job_type,
        "status": job.status,
        "progress": job.progress_json or {},
        "result": job.result_json,
        "error": job.error_message,
        "started_at": job.started_at.isoformat() if job.started_at else None,
        "completed_at": job.completed_at.isoformat() if job.completed_at else None,
    }


def get_job(db: Session, job_id: int) -> Dict[str, Any]:
    job = db.query(CurSetupJob).filter(CurSetupJob.id == job_id).first()
    if not job:
        raise ValueError(f"Setup job '{job_id}' not found")
    return job_state(job)


def latest_jobs(db: Session, limit: int = 5) -> List[Dict[str, Any]]:
    rows = (
        db.query(CurSetupJob)
        .order_by(CurSetupJob.started_at.desc(), CurSetupJob.id.desc())
        .limit(limit)
        .all()
    )
    return [job_state(row) for row in rows]


def running_job_of_type(db: Session, job_type: str) -> Optional[Dict[str, Any]]:
    row = (
        db.query(CurSetupJob)
        .filter(CurSetupJob.job_type == job_type, CurSetupJob.status == "running")
        .order_by(CurSetupJob.id.desc())
        .first()
    )
    return job_state(row) if row else None


# --------------------------------------------------------------- job runners


def run_export_job(db: Session, job_id: int) -> None:
    """Create the CUR export, its bucket, and the Athena table."""
    from stacks.CUR.create_cur_export import (
        build_bucket_policy,
        build_export_payload,
        create_cur_export,
        create_external_table,
        ensure_bucket,
        export_data_location,
        put_bucket_policy,
        resolve_account_id,
    )

    region = settings.aws_region or "us-east-1"
    try:
        update_progress(db, job_id, phase="identity", message="Resolving AWS account…", current=1, total=5)
        session = create_setup_boto3_session(region_name=region)
        account_id = resolve_account_id(session, region)
        bucket = default_bucket_name(account_id)

        update_progress(db, job_id, phase="bucket", message=f"Preparing bucket {bucket}…", current=2, total=5)
        ensure_bucket(session, bucket, region)
        put_bucket_policy(session, bucket, build_bucket_policy(bucket, account_id, region), region)

        update_progress(db, job_id, phase="export", message="Creating the Cost and Usage Report export…", current=3, total=5)
        export_payload = build_export_payload(
            account_id=account_id, bucket_name=bucket, prefix=DEFAULT_PREFIX, region=region
        )
        export_result = create_cur_export(session, export_payload, region)

        update_progress(db, job_id, phase="table", message="Creating the Athena table…", current=4, total=5)
        table_location = export_data_location(bucket, DEFAULT_PREFIX, DEFAULT_EXPORT_NAME)
        # Recreate rather than CREATE IF NOT EXISTS. A table left over from an
        # earlier or hand-made export keeps its old LOCATION, so "if not
        # exists" silently leaves it pointing at the wrong bucket and every
        # query afterwards fails somewhere confusing. Dropping an external
        # table removes no data in S3.
        table_result = create_external_table(
            session,
            database=DEFAULT_DATABASE,
            table=DEFAULT_RAW_TABLE,
            location=table_location,
            output_location=f"s3://{bucket}/athena-results/",
            region=region,
            workgroup="primary",
            recreate=True,
        )

        complete_job(
            db,
            job_id,
            {
                "account_id": account_id,
                "bucket": bucket,
                "export": {"name": DEFAULT_EXPORT_NAME, "arn": (export_result or {}).get("ExportArn")},
                "table": table_result,
                "next": "AWS will deliver the first billing file within about 24 hours.",
            },
        )
    except Exception as exc:  # noqa: BLE001 - surfaced to the page verbatim
        logger.exception("CUR export job failed")
        fail_job(db, job_id, str(exc))


def run_refresh_job(
    db: Session,
    job_id: int,
    *,
    datasets: Optional[List[str]] = None,
    start_month: Optional[str] = None,
    end_month: Optional[str] = None,
    force: bool = False,
) -> None:
    """Rebuild the local Parquet cost cache from Athena, one month at a time."""
    from app.services.aws_credentials import get_setup_aws_profile_name
    from pricing.cur.refresh_cur_cache import refresh_tasks

    cache_root = Path(settings.cur_cache_root)
    region = settings.aws_region or "us-east-1"
    try:
        # The refresh tooling builds its own boto3 session from a profile name,
        # so hand it the same profile the rest of MaxOps runs as. Athena results
        # and the S3 cache root are derived from this account's report bucket
        # rather than the CLI defaults, which point elsewhere.
        profile = get_setup_aws_profile_name()
        session = create_setup_boto3_session(region_name=region)
        account_id = session.client("sts", region_name=region).get_caller_identity()["Account"]
        bucket = default_bucket_name(account_id)
        athena_results_s3 = f"s3://{bucket}/athena-results/"
        s3_cache_root = f"s3://{bucket}/cur-aggregates"

        tasks = plan_refresh_tasks(
            cache_root=cache_root,
            datasets=datasets or [RESOURCE_COST_DATASET],
            start_month=BillingMonth.parse(start_month) if start_month else None,
            end_month=BillingMonth.parse(end_month) if end_month else None,
            force=force,
        )
        total = len(tasks)
        if not total:
            complete_job(db, job_id, {"planned": 0, "note": "The cache is already up to date."})
            return

        results = []
        for index, task in enumerate(tasks, start=1):
            update_progress(
                db,
                job_id,
                phase="querying",
                message=f"Summarising {task.dataset} for {task.month}…",
                current=index,
                total=total,
            )
            # One task at a time so progress is truthful rather than estimated.
            results.extend(
                refresh_tasks(
                    [task],
                    profile=profile,
                    region=region,
                    database=DEFAULT_DATABASE,
                    raw_table=DEFAULT_RAW_TABLE,
                    athena_results_s3=athena_results_s3,
                    workgroup="primary",
                    mode="local",
                    s3_cache_root=s3_cache_root,
                    dry_run=False,
                )
            )

        # The cost map is cached in-process; force it to pick up new months.
        cur_cost_lookup.refresh(force=True)
        complete_job(db, job_id, {"planned": total, "results": results})
    except Exception as exc:  # noqa: BLE001
        logger.exception("CUR refresh job failed")
        fail_job(db, job_id, str(exc))
