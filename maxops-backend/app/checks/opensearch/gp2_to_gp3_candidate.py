"""OpenSearch gp2 to gp3 candidate check."""
from __future__ import annotations

from typing import Any, Dict, List, Optional

from app.checks.base import create_check_reason
from app.checks.registry import CheckMetadata, check_registry
from app.checks.opensearch._common import describe_domain, get_os_client, list_domains


def check_opensearch_gp2_to_gp3_candidate(
    aws_adapter,
    min_volume_size_gib: int = 1,
    region: Optional[str] = None,
) -> List[Dict[str, Any]]:
    os_client = get_os_client(aws_adapter, region)
    flagged: List[Dict[str, Any]] = []

    for domain_name in list_domains(os_client):
        try:
            status = describe_domain(os_client, domain_name)
            ebs = status.get("EBSOptions") or {}
            if not ebs.get("EBSEnabled"):
                continue
            vtype = str(ebs.get("VolumeType") or "").lower()
            vsize = int(ebs.get("VolumeSize") or 0)
            if vtype != "gp2" or vsize < min_volume_size_gib:
                continue
            flagged.append(
                {
                    "resource_id": domain_name,
                    "resource_type": "opensearch_domain",
                    "resource_name": domain_name,
                    "region": region,
                    "state": status.get("Processing") and "processing" or "active",
                    "metadata": {
                        "volume_type": vtype,
                        "volume_size_gib": vsize,
                        "min_volume_size_gib": min_volume_size_gib,
                        "recommended_action": "migrate_ebs_gp2_to_gp3",
                        "check_reason": create_check_reason(
                            "gp2_convertible_to_gp3",
                            {"volume_type": "gp2", "recommended_type": "gp3", "size_gb": vsize},
                        ),
                    },
                }
            )
        except Exception as exc:
            print(f"Error checking gp2->gp3 candidate for {domain_name}: {exc}")
    return flagged


check_registry.register(
    CheckMetadata(
        check_id="opensearch_gp2_to_gp3_candidate",
        name="OpenSearch gp2 to gp3 Candidate",
        description="Identifies OpenSearch domains using gp2 EBS volumes",
        resource_type="opensearch_domain",
        check_function=check_opensearch_gp2_to_gp3_candidate,
        default_action="migrate_ebs_gp2_to_gp3",
        parameters={
            "min_volume_size_gib": 1,
            "region": None,
        },
    )
)

