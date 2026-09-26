
"""
VPC Check - No DynamoDB VPC Endpoint
Flags VPCs that do not have a DynamoDB VPC endpoint (typically a Gateway endpoint).
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from app.checks.registry import check_registry, CheckMetadata
from app.checks.base import create_check_reason


def _service_name(ep: Dict[str, Any]) -> str:
    md = ep.get("metadata") or {}
    return (
        ep.get("service_name")
        or ep.get("ServiceName")
        or md.get("service_name")
        or md.get("ServiceName")
        or ""
    )


def _get_vpc_endpoints(aws_adapter, vpc_id: str, region: Optional[str]) -> List[Dict[str, Any]]:
    for rt in ("vpc_endpoint", "vpc_endpoints", "ec2_vpc_endpoint", "ec2_vpc_endpoints"):
        try:
            eps = aws_adapter.get_resources(rt, {"vpc_id": vpc_id}, region)
            if isinstance(eps, list):
                return eps
        except Exception:
            continue
    return []


def check_vpc_no_dynamodb_vpc_endpoint(
    aws_adapter,
    region: Optional[str] = None,
) -> List[Dict[str, Any]]:
    """
    Flags VPCs missing a DynamoDB VPC endpoint.
    """
    vpcs = aws_adapter.get_resources("vpc", {}, region)
    flagged: List[Dict[str, Any]] = []

    for vpc in vpcs:
        vpc_id = vpc.get("resource_id") or vpc.get("vpc_id") or vpc.get("id")
        if not vpc_id:
            continue

        try:
            endpoints = _get_vpc_endpoints(aws_adapter, vpc_id, region)

            has_ddb = False
            for ep in endpoints:
                svc = _service_name(ep).lower()
                if ".dynamodb" in svc or svc.endswith(":dynamodb") or svc.endswith("/dynamodb") or svc.endswith("dynamodb"):
                    has_ddb = True
                    break

            if not has_ddb:
                md = vpc.setdefault("metadata", {})
                md["recommended_action"] = "create"
                md["missing_endpoint"] = "dynamodb"
                md["check_reason"] = create_check_reason("missing_policy", {
                    "policy": "vpc_endpoint",
                    "service": "dynamodb",
                    "vpc_id": vpc_id,
                })
                flagged.append(vpc)

        except Exception as e:
            print(f"Error checking DynamoDB VPC endpoint for {vpc_id}: {e}")
            continue

    return flagged


check_registry.register(CheckMetadata(
    check_id="vpc_no_dynamodb_vpc_endpoint",
    name="VPC No DynamoDB VPC Endpoint",
    description="Identifies VPCs that do not have a DynamoDB VPC endpoint (Gateway endpoint)",
    resource_type="vpc",
    check_function=check_vpc_no_dynamodb_vpc_endpoint,
    default_action="create",
    parameters={
        "region": None,
    },
))
