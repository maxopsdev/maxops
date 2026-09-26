"""OpenSearch no ISM rollover/retention policy check."""
from __future__ import annotations

from typing import Any, Dict, List, Optional

from app.checks.base import create_check_reason
from app.checks.registry import CheckMetadata, check_registry
from app.checks.opensearch._common import (
    describe_domain,
    get_domain_endpoint,
    get_os_client,
    list_domains,
    signed_get_json,
)


def check_opensearch_no_ism_rollover_or_retention(
    aws_adapter,
    min_indices_to_consider: int = 10,
    region: Optional[str] = None,
) -> List[Dict[str, Any]]:
    os_client = get_os_client(aws_adapter, region)
    region_name = region or os_client.meta.region_name
    flagged: List[Dict[str, Any]] = []

    for domain_name in list_domains(os_client):
        try:
            status = describe_domain(os_client, domain_name)
            endpoint = get_domain_endpoint(status)
            if not endpoint:
                continue
            indices = signed_get_json(
                aws_adapter,
                region_name,
                endpoint,
                "/_cat/indices?format=json&h=index,status",
            )
            if not isinstance(indices, list) or len(indices) < min_indices_to_consider:
                continue
            policies = signed_get_json(
                aws_adapter,
                region_name,
                endpoint,
                "/_plugins/_ism/policies?from=0&size=200",
            )
            hits = ((policies or {}).get("hits") or {}).get("hits") if isinstance(policies, dict) else None
            if isinstance(hits, list) and len(hits) > 0:
                continue
            flagged.append(
                {
                    "resource_id": domain_name,
                    "resource_type": "opensearch_domain",
                    "resource_name": domain_name,
                    "region": region_name,
                    "state": status.get("Processing") and "processing" or "active",
                    "metadata": {
                        "indices_count": len(indices),
                        "min_indices_to_consider": min_indices_to_consider,
                        "recommended_action": "configure_ism_rollover_and_retention",
                        "check_reason": create_check_reason(
                            "missing_policy",
                            {"policy": "ism_rollover_retention", "domain": domain_name},
                        ),
                    },
                }
            )
        except Exception as exc:
            print(f"Error checking ISM policies for {domain_name}: {exc}")
    return flagged


check_registry.register(
    CheckMetadata(
        check_id="opensearch_no_ism_rollover_or_retention",
        name="OpenSearch No ISM Rollover/Retention",
        description="Identifies domains with many indices but no ISM policies configured",
        resource_type="opensearch_domain",
        check_function=check_opensearch_no_ism_rollover_or_retention,
        default_action="configure_ism_rollover_and_retention",
        parameters={
            "min_indices_to_consider": 10,
            "region": None,
        },
    )
)

