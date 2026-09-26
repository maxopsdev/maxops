"""Read-only status probes for the CUR cost-data pipeline.

The setup page does not remember which step you are on. It asks AWS and the
local cache what is actually true, every time. That keeps the page correct
when someone creates the export by hand, deletes the bucket, or comes back a
day later to find AWS has delivered the first file.

Every probe here is read-only and independently fault-tolerant: a missing
permission or an unreachable account degrades one step to "unknown" rather
than failing the whole page.
"""
from __future__ import annotations

import logging
import re
from pathlib import Path
from typing import Any, Callable, Dict, Optional

from app.config import settings
from app.services.aws_credentials import create_setup_boto3_session, get_setup_aws_profile_name
from app.services.cur_cost_service import cur_cost_lookup
from pricing.cur.datasets import DEFAULT_DATABASE, DEFAULT_RAW_TABLE
from pricing.cur.reader import RESOURCE_COST_DATASET, cache_status
from stacks.CUR.create_cur_export import (
    DEFAULT_EXPORT_NAME,
    DEFAULT_PREFIX,
    default_bucket_name,
    export_data_location,
    normalize_prefix,
)

logger = logging.getLogger(__name__)


# Step identifiers, in the order the page renders them.
STEP_EXPORT = "export"
STEP_DELIVERY = "delivery"
STEP_CACHE = "cache"
STEP_PRICING = "pricing"

# Step states the UI knows how to render.
DONE = "done"
WAITING = "waiting"
ACTION_REQUIRED = "action_required"
ERROR = "error"
UNKNOWN = "unknown"


def _step(
    step_id: str,
    label: str,
    state: str,
    detail: str,
    **extra: Any,
) -> Dict[str, Any]:
    return {"id": step_id, "label": label, "state": state, "detail": detail, **extra}


def _error_code(error: Exception) -> str:
    response = getattr(error, "response", None)
    if isinstance(response, dict):
        return str(response.get("Error", {}).get("Code", "") or "")
    return ""


def _is_access_denied(error: Exception) -> bool:
    return _error_code(error) in {
        "AccessDenied",
        "AccessDeniedException",
        "UnauthorizedOperation",
        "403",
    }


def denied_action(error: Exception) -> Optional[str]:
    """Pull the specific IAM action out of an access-denied message.

    Botocore phrases these as "User: ... is not authorized to perform:
    athena:StartQueryExecution on resource ...". Naming the action turns an
    opaque refusal into something the reader can act on.
    """
    match = re.search(r"not authorized to perform:?\s+([A-Za-z0-9_-]+:[A-Za-z0-9_*-]+)", str(error))
    return match.group(1) if match else None


def _safe(probe: Callable[[], Dict[str, Any]], step_id: str, label: str) -> Dict[str, Any]:
    """Run one probe, converting any failure into a renderable step."""
    try:
        return probe()
    except Exception as exc:  # noqa: BLE001 - a probe must never break the page
        if _is_access_denied(exc):
            action = denied_action(exc)
            profile = get_setup_aws_profile_name()
            detail = (
                f"The credentials in use aren't allowed to call {action}."
                if action
                else "The credentials in use aren't allowed to check this."
            )
            if profile:
                remedy = (
                    f"Setup is using the '{profile}' profile. Choose a profile with "
                    "permission for this under Setup credentials above, or grant it to "
                    f"'{profile}'."
                )
            else:
                remedy = (
                    "Setup is using the read-only scan role, which deliberately cannot do "
                    "this. Choose an AWS profile under Setup credentials above."
                )
            return _step(
                step_id,
                label,
                UNKNOWN,
                detail,
                remedy=remedy,
                denied_action=action,
                setup_profile=profile,
                error=str(exc),
            )
        logger.warning("CUR setup probe '%s' failed: %s", step_id, exc)
        return _step(step_id, label, UNKNOWN, "Could not determine status.", error=str(exc))


# --------------------------------------------------------------------- probes


def probe_export(session, region: str) -> Dict[str, Any]:
    label = "Cost and Usage Report export"

    def run() -> Dict[str, Any]:
        client = session.client("bcm-data-exports", region_name=region)
        paginator_pages = []
        token: Optional[str] = None
        while True:
            kwargs = {"NextToken": token} if token else {}
            page = client.list_exports(**kwargs)
            paginator_pages.extend(page.get("Exports") or [])
            token = page.get("NextToken")
            if not token:
                break

        for export in paginator_pages:
            name = export.get("ExportName") or export.get("Name")
            if name == DEFAULT_EXPORT_NAME:
                return _step(
                    STEP_EXPORT,
                    label,
                    DONE,
                    f"Export '{DEFAULT_EXPORT_NAME}' exists.",
                    export_arn=export.get("ExportArn"),
                )

        return _step(
            STEP_EXPORT,
            label,
            ACTION_REQUIRED,
            f"No export named '{DEFAULT_EXPORT_NAME}' in this account. "
            "AWS has not been asked to produce a bill breakdown yet.",
        )

    return _safe(run, STEP_EXPORT, label)


def probe_delivery(session, region: str, bucket: Optional[str], prefix: str) -> Dict[str, Any]:
    label = "First bill delivered by AWS"

    def run() -> Dict[str, Any]:
        if not bucket:
            return _step(
                STEP_DELIVERY,
                label,
                UNKNOWN,
                "Cannot check until the destination bucket is known.",
            )

        search_prefix = f"{normalize_prefix(prefix)}/{DEFAULT_EXPORT_NAME}".strip("/")
        client = session.client("s3", region_name=region)
        response = client.list_objects_v2(Bucket=bucket, Prefix=search_prefix, MaxKeys=1)

        if response.get("KeyCount"):
            return _step(
                STEP_DELIVERY,
                label,
                DONE,
                "AWS has delivered billing data to the bucket.",
                bucket=bucket,
                prefix=search_prefix,
            )

        return _step(
            STEP_DELIVERY,
            label,
            WAITING,
            "The export exists but AWS has not written the first file yet. "
            "This normally takes up to 24 hours and needs nothing from you.",
            bucket=bucket,
            prefix=search_prefix,
        )

    return _safe(run, STEP_DELIVERY, label)


def probe_athena_table(session, region: str, bucket: Optional[str] = None) -> Dict[str, Any]:
    """Folded into the export step in the UI; surfaced separately for diagnostics."""
    label = "Athena table"

    def run() -> Dict[str, Any]:
        client = session.client("glue", region_name=region)
        try:
            table = client.get_table(DatabaseName=DEFAULT_DATABASE, Name=DEFAULT_RAW_TABLE)
        except Exception as exc:
            if _error_code(exc) in {"EntityNotFoundException", "AccessDeniedException"} and not _is_access_denied(exc):
                return _step(
                    "athena_table",
                    label,
                    ACTION_REQUIRED,
                    f"Table {DEFAULT_DATABASE}.{DEFAULT_RAW_TABLE} does not exist yet.",
                )
            raise

        location = (
            ((table.get("Table") or {}).get("StorageDescriptor") or {}).get("Location") or ""
        )

        # A table that exists but points somewhere else is worse than a missing
        # one: queries succeed against stale data, or fail with a permission
        # error naming a bucket the operator has never heard of.
        if bucket:
            expected = export_data_location(bucket, DEFAULT_PREFIX, DEFAULT_EXPORT_NAME)
            if location.rstrip("/") != expected.rstrip("/"):
                return _step(
                    "athena_table",
                    label,
                    ERROR,
                    f"Table {DEFAULT_DATABASE}.{DEFAULT_RAW_TABLE} points at {location or 'nothing'}, "
                    f"but the export MaxOps manages writes to {expected}.",
                    remedy=(
                        "The table is left over from a different export. Use "
                        "'Repair export' on the first step to point it at the current one."
                    ),
                    location=location,
                    expected_location=expected,
                )

        return _step(
            "athena_table",
            label,
            DONE,
            f"Table {DEFAULT_DATABASE}.{DEFAULT_RAW_TABLE} is queryable.",
            location=location,
        )

    return _safe(run, "athena_table", label)


def probe_cache(cache_root: Path) -> Dict[str, Any]:
    label = "Local cost cache"

    def run() -> Dict[str, Any]:
        status = cache_status(cache_root, datasets=[RESOURCE_COST_DATASET])
        months = (status.get(RESOURCE_COST_DATASET) or {}).get("months") or []
        if months:
            latest = months[-1]["month"]
            return _step(
                STEP_CACHE,
                label,
                DONE,
                f"{len(months)} month(s) cached, most recent {latest}.",
                months=[entry["month"] for entry in months],
                cache_root=str(cache_root),
            )
        return _step(
            STEP_CACHE,
            label,
            ACTION_REQUIRED,
            "No billing data has been summarised locally yet.",
            cache_root=str(cache_root),
        )

    return _safe(run, STEP_CACHE, label)


def probe_pricing() -> Dict[str, Any]:
    label = "Pricing from actual cost"

    def run() -> Dict[str, Any]:
        status = cur_cost_lookup.status()
        if not status.get("enabled"):
            return _step(
                STEP_PRICING,
                label,
                ACTION_REQUIRED,
                "Findings are priced from list prices. Turn on cost-data pricing to "
                "use what these resources actually cost.",
                **status,
            )
        if not status.get("resource_count"):
            return _step(
                STEP_PRICING,
                label,
                WAITING,
                "Enabled, but no resources were found in the cached bill yet.",
                **status,
            )
        return _step(
            STEP_PRICING,
            label,
            DONE,
            f"{status['resource_count']} resources priced from the "
            f"{status['billing_month']} bill.",
            **status,
        )

    return _safe(run, STEP_PRICING, label)


# ---------------------------------------------------------------- aggregation


def _resolve_identity(session, region: str) -> tuple[Optional[str], Optional[str]]:
    try:
        account_id = session.client("sts", region_name=region).get_caller_identity()["Account"]
        return account_id, None
    except Exception as exc:  # noqa: BLE001
        logger.warning("Could not resolve AWS account for CUR setup: %s", exc)
        return None, str(exc)


def get_setup_status(cache_root: Optional[Path] = None) -> Dict[str, Any]:
    """Everything the cost-data setup page needs, in one read-only call."""
    region = settings.aws_region or "us-east-1"
    resolved_cache_root = Path(cache_root or settings.cur_cache_root)

    # The local half of the pipeline works with no AWS access at all.
    local_steps = [probe_cache(resolved_cache_root), probe_pricing()]

    try:
        session = create_setup_boto3_session(region_name=region)
    except Exception as exc:  # noqa: BLE001
        logger.warning("No AWS session available for CUR setup probes: %s", exc)
        unavailable = "No AWS credentials are configured, so this cannot be checked."
        return {
            "region": region,
            "account_id": None,
            "bucket": None,
            "aws_available": False,
            "aws_error": str(exc),
            "steps": [
                _step(STEP_EXPORT, "Cost and Usage Report export", UNKNOWN, unavailable),
                _step(STEP_DELIVERY, "First bill delivered by AWS", UNKNOWN, unavailable),
                *local_steps,
            ],
            "diagnostics": [],
        }

    account_id, identity_error = _resolve_identity(session, region)
    bucket = default_bucket_name(account_id) if account_id else None

    export_step = probe_export(session, region)
    table_step = probe_athena_table(session, region, bucket)

    # The Athena table is part of what "the export is set up" means, even
    # though it renders as a diagnostic. If the export exists but its table
    # points elsewhere, the export step has to show as broken — otherwise the
    # page looks healthy and offers no way to repair it.
    if table_step.get("state") == ERROR and export_step.get("state") == DONE:
        export_step = _step(
            STEP_EXPORT,
            export_step["label"],
            ERROR,
            f"{export_step['detail']} {table_step['detail']}",
            remedy=table_step.get("remedy"),
            repairable=True,
            location=table_step.get("location"),
            expected_location=table_step.get("expected_location"),
        )

    steps = [
        export_step,
        probe_delivery(session, region, bucket, DEFAULT_PREFIX),
        *local_steps,
    ]

    return {
        "region": region,
        "account_id": account_id,
        "bucket": bucket,
        "aws_available": account_id is not None,
        "aws_error": identity_error,
        "steps": steps,
        "diagnostics": [table_step],
    }
