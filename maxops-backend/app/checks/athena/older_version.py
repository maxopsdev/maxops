"""Athena workgroups older engine version check."""
from __future__ import annotations

import re
from typing import Any, Dict, List, Optional

from app.checks.base import create_check_reason
from app.checks.registry import CheckMetadata, check_registry


def _parse_engine_major(version_value: Any) -> Optional[int]:
    if version_value is None:
        return None
    s = str(version_value).strip()
    if not s:
        return None

    # Handles values like:
    # "Athena engine version 2", "Athena engine version 3", "3", "3.0"
    match = re.search(r"(\d+)(?:\.\d+)?", s)
    if not match:
        return None
    try:
        return int(match.group(1))
    except ValueError:
        return None


def _list_work_groups(athena_client) -> List[Dict[str, Any]]:
    # ListWorkGroups has no botocore paginator config -- page manually via NextToken.
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


def _extract_engine_version_info(workgroup: Dict[str, Any]) -> Dict[str, Optional[str]]:
    config = workgroup.get("Configuration") or {}
    engine = config.get("EngineVersion") or {}
    selected = engine.get("SelectedEngineVersion")
    effective = engine.get("EffectiveEngineVersion")
    return {
        "selected_engine_version": str(selected).strip() if selected is not None else None,
        "effective_engine_version": str(effective).strip() if effective is not None else None,
    }


def check_athena_workgroups_older_version(
    aws_adapter,
    min_engine_major_version: int = 3,
    include_states: Optional[List[str]] = None,
    region: Optional[str] = None,
) -> List[Dict[str, Any]]:
    """
    Identify Athena workgroups running engine version older than minimum.
    """
    athena = aws_adapter.session.client("athena", region_name=region)
    states = {s.upper() for s in (include_states or ["ENABLED"])}
    flagged: List[Dict[str, Any]] = []

    for summary in _list_work_groups(athena):
        name = summary.get("Name")
        if not name:
            continue

        try:
            workgroup = athena.get_work_group(WorkGroup=name).get("WorkGroup", {})
            state = str(workgroup.get("State") or summary.get("State") or "").upper()
            if states and state not in states:
                continue

            version_info = _extract_engine_version_info(workgroup)
            selected = version_info["selected_engine_version"]
            effective = version_info["effective_engine_version"]

            major = _parse_engine_major(selected) or _parse_engine_major(effective)
            if major is None:
                # Unknown version format; skip to avoid false positives.
                continue

            if major < min_engine_major_version:
                flagged.append(
                    {
                        "resource_id": name,
                        "resource_type": "athena_workgroup",
                        "resource_name": name,
                        "region": region,
                        "state": state or "UNKNOWN",
                        "metadata": {
                            "workgroup_name": name,
                            "selected_engine_version": selected,
                            "effective_engine_version": effective,
                            "engine_major_version": major,
                            "min_engine_major_version": min_engine_major_version,
                            "recommended_action": "upgrade_engine_version",
                            "check_reason": create_check_reason(
                                "version_outdated",
                                {
                                    "resource": "athena_workgroup",
                                    "workgroup_name": name,
                                    "current_engine_major_version": major,
                                    "min_engine_major_version": min_engine_major_version,
                                },
                            ),
                        },
                    }
                )
        except Exception as exc:
            print(f"Error checking Athena workgroup {name}: {exc}")
            continue

    return flagged


check_registry.register(
    CheckMetadata(
        check_id="athena_workgroups_older_engine_version",
        name="Athena Workgroups Older Engine Version",
        description="Identifies Athena workgroups using engine major version lower than minimum",
        resource_type="athena_workgroup",
        check_function=check_athena_workgroups_older_version,
        default_action="upgrade_engine_version",
        parameters={
            "min_engine_major_version": 3,
            "include_states": ["ENABLED"],
            "region": None,
        },
    )
)
