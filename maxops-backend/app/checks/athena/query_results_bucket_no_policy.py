"""Athena query-results bucket lifecycle policy check."""
from __future__ import annotations

from typing import Any, Dict, List, Optional, Set

from app.checks.base import create_check_reason
from app.checks.registry import check_registry, CheckMetadata


def _bucket_from_s3_uri(uri: Optional[str]) -> Optional[str]:
    if not uri:
        return None
    s = str(uri).strip()
    if not s:
        return None
    if not s.lower().startswith("s3://"):
        return None
    tail = s[5:]
    bucket = tail.split("/", 1)[0].strip()
    return bucket or None


def _has_lifecycle_policy(s3_client, bucket: str) -> bool:
    try:
        response = s3_client.get_bucket_lifecycle_configuration(Bucket=bucket)
        rules = response.get("Rules", [])
        return isinstance(rules, list) and len(rules) > 0
    except Exception as exc:
        code = ""
        error = getattr(exc, "response", {}).get("Error", {})
        if isinstance(error, dict):
            code = str(error.get("Code") or "")
        # Treat "no lifecycle" as missing policy.
        if code in {"NoSuchLifecycleConfiguration", "NoSuchBucketPolicy", "404"}:
            return False
        raise


def _list_work_groups(athena_client) -> List[Dict[str, Any]]:
    workgroups: List[Dict[str, Any]] = []
    next_token: Optional[str] = None
    while True:
        kwargs: Dict[str, Any] = {}
        if next_token:
            kwargs["NextToken"] = next_token
        response = athena_client.list_work_groups(**kwargs)
        workgroups.extend(response.get("WorkGroups", []))
        next_token = response.get("NextToken")
        if not next_token:
            break
    return workgroups


def check_athena_query_results_bucket_no_lifecycle_policy(
    aws_adapter,
    include_states: Optional[List[str]] = None,
    region: Optional[str] = None,
) -> List[Dict[str, Any]]:
    """
    Identify Athena query-results S3 buckets without lifecycle policy.
    """
    athena = aws_adapter.session.client("athena", region_name=region)
    s3 = aws_adapter.session.client("s3", region_name=region)

    states = {s.upper() for s in (include_states or ["ENABLED"])}
    bucket_to_workgroups: Dict[str, Set[str]] = {}

    for summary in _list_work_groups(athena):
        wg_name = summary.get("Name")
        if not wg_name:
            continue
        try:
            details = athena.get_work_group(WorkGroup=wg_name).get("WorkGroup", {})
            state = str(details.get("State") or summary.get("State") or "").upper()
            if states and state not in states:
                continue

            output_location = (
                (details.get("Configuration") or {})
                .get("ResultConfiguration", {})
                .get("OutputLocation")
            )
            bucket = _bucket_from_s3_uri(output_location)
            if not bucket:
                continue
            bucket_to_workgroups.setdefault(bucket, set()).add(wg_name)
        except Exception as exc:
            print(f"Error reading Athena workgroup {wg_name}: {exc}")
            continue

    flagged: List[Dict[str, Any]] = []
    for bucket, workgroups in sorted(bucket_to_workgroups.items()):
        try:
            if _has_lifecycle_policy(s3, bucket):
                continue

            flagged.append(
                {
                    "resource_id": bucket,
                    "resource_type": "s3",
                    "resource_name": bucket,
                    "region": region,
                    "state": "active",
                    "metadata": {
                        "bucket_name": bucket,
                        "workgroups": sorted(workgroups),
                        "workgroups_count": len(workgroups),
                        "recommended_action": "add_lifecycle_policy",
                        "check_reason": create_check_reason(
                            "missing_policy",
                            {
                                "policy": "lifecycle",
                                "bucket": bucket,
                            },
                        ),
                    },
                }
            )
        except Exception as exc:
            print(f"Error checking lifecycle policy for bucket {bucket}: {exc}")
            continue

    return flagged


check_registry.register(
    CheckMetadata(
        check_id="athena_query_results_bucket_no_lifecycle_policy",
        name="Athena Query Results Bucket No Lifecycle Policy",
        description="Identifies Athena query-results buckets without lifecycle configuration",
        resource_type="s3",
        check_function=check_athena_query_results_bucket_no_lifecycle_policy,
        default_action="add_lifecycle_policy",
        parameters={
            "include_states": ["ENABLED"],
            "region": None,
        },
    )
)
