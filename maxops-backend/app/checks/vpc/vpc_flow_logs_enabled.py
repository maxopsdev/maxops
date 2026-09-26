"""
VPC Check - VPC Flow Logs Enabled
Flags VPCs that have VPC Flow Logs enabled (useful for cost visibility since flow logs generate log volume).
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from app.checks.registry import check_registry, CheckMetadata
from app.checks.base import create_check_reason


def _get_vpc_flow_logs(aws_adapter, vpc_id: str, region: Optional[str]) -> List[Dict[str, Any]]:
    """
    Best-effort flow log fetch across adapters.
    """
    for rt in ("vpc_flow_log", "vpc_flow_logs", "ec2_vpc_flow_log", "ec2_vpc_flow_logs", "flow_log", "flow_logs"):
        try:
            fl = aws_adapter.get_resources(rt, {"vpc_id": vpc_id}, region)
            if isinstance(fl, list):
                return fl
        except Exception:
            continue
    return []


def _extract_flowlog_dest(flowlog: Dict[str, Any]) -> Dict[str, Any]:
    md = flowlog.get("metadata") or {}
    return {
        "log_destination_type": (
            flowlog.get("log_destination_type")
            or flowlog.get("LogDestinationType")
            or md.get("log_destination_type")
            or md.get("LogDestinationType")
        ),
        "log_destination": (
            flowlog.get("log_destination")
            or flowlog.get("LogDestination")
            or md.get("log_destination")
            or md.get("LogDestination")
        ),
        "traffic_type": (
            flowlog.get("traffic_type")
            or flowlog.get("TrafficType")
            or md.get("traffic_type")
            or md.get("TrafficType")
        ),
    }


def check_vpc_flow_logs_enabled(
    aws_adapter,
    region: Optional[str] = None,
) -> List[Dict[str, Any]]:
    """
    Returns VPCs that have at least one flow log configured.
    """
    vpcs = aws_adapter.get_resources("vpc", {}, region)
    flagged: List[Dict[str, Any]] = []

    for vpc in vpcs:
        vpc_id = vpc.get("resource_id") or vpc.get("vpc_id") or vpc.get("id")
        if not vpc_id:
            continue

        try:
            flow_logs = _get_vpc_flow_logs(aws_adapter, vpc_id, region)
            if not flow_logs:
                continue

            # Enrich VPC metadata with a short summary (don’t dump everything)
            md = vpc.setdefault("metadata", {})
            md["recommended_action"] = "vpc_flow_logs_disabling"
            md["recommended_actions"] = [
                "vpc_flow_logs_disabling",
                "vpc_flow_logs_disabling_with_bucket_expiration",
                "vpc_flow_logs_disabling_with_bucket_deletion",
            ]
            md["flow_logs_count"] = len(flow_logs)

            # Include a compact sample of destinations (up to 3)
            samples: List[Dict[str, Any]] = []
            for fl in flow_logs[:3]:
                samples.append(_extract_flowlog_dest(fl))
            md["flow_logs_samples"] = samples

            md["check_reason"] = create_check_reason("enabled_feature", {
                "feature": "vpc_flow_logs",
                "vpc_id": vpc_id,
                "flow_logs_count": len(flow_logs),
            })

            flagged.append(vpc)

        except Exception as e:
            print(f"Error checking VPC flow logs for {vpc_id}: {e}")
            continue

    return flagged


check_registry.register(CheckMetadata(
    check_id="vpc_flow_logs_enabled",
    name="VPC Flow Logs Enabled",
    description="Identifies VPCs with Flow Logs enabled (generates log volume and associated costs)",
    resource_type="vpc",
    check_function=check_vpc_flow_logs_enabled,
    default_action="vpc_flow_logs_disabling",
    parameters={
        "region": None,
    },
))
