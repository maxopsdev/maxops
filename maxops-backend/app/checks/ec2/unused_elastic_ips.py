"""EC2 check for allocated but unassociated Elastic IP addresses."""
from __future__ import annotations

from typing import Any, Dict, List, Optional

from app.checks.base import create_check_reason
from app.checks.registry import CheckMetadata, check_registry


def check_unused_elastic_ips(
    aws_adapter,
    region: Optional[str] = None,
) -> List[Dict[str, Any]]:
    """Return VPC Elastic IP allocations with no active association."""
    addresses = aws_adapter.get_resources("elastic_ip", {}, region)
    flagged: List[Dict[str, Any]] = []

    for address in addresses:
        metadata = address.setdefault("metadata", {})
        if (
            metadata.get("AssociationId")
            or metadata.get("NetworkInterfaceId")
            or metadata.get("InstanceId")
        ):
            continue

        allocation_id = (
            address.get("resource_id")
            or metadata.get("AllocationId")
            or address.get("allocation_id")
        )
        metadata["recommended_action"] = "release_unused_elastic_ip"
        metadata["recommended_actions"] = ["release_unused_elastic_ip"]
        metadata["public_ip"] = metadata.get("PublicIp") or address.get("public_ip")
        metadata["allocation_id"] = allocation_id
        metadata["check_reason"] = create_check_reason(
            "unused",
            {
                "resource": "elastic_ip",
                "allocation_id": allocation_id,
                "public_ip": metadata["public_ip"],
            },
        )
        flagged.append(address)

    return flagged


check_registry.register(CheckMetadata(
    check_id="unused_elastic_ips",
    name="Unused Elastic IP Addresses",
    description="Identifies allocated VPC Elastic IP addresses without an association",
    resource_type="elastic_ip",
    check_function=check_unused_elastic_ips,
    default_action="release_unused_elastic_ip",
    parameters={"region": None},
))
