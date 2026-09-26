"""OpenSearch over-replicated indices check."""
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
    to_float,
)


def check_opensearch_over_replicated_indices(
    aws_adapter,
    max_replicas: int = 1,
    min_index_size_gib: float = 1.0,
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
                "/_cat/indices?format=json&h=index,rep,store.size,status",
            )
            if not isinstance(indices, list):
                continue
            for idx in indices:
                rep = int(to_float(idx.get("rep"), default=-1))
                store_size_text = str(idx.get("store.size") or "")
                size_gib = 0.0
                if store_size_text.endswith("gb"):
                    size_gib = to_float(store_size_text[:-2])
                elif store_size_text.endswith("mb"):
                    size_gib = to_float(store_size_text[:-2]) / 1024.0
                elif store_size_text.endswith("tb"):
                    size_gib = to_float(store_size_text[:-2]) * 1024.0
                if rep <= max_replicas or size_gib < min_index_size_gib:
                    continue
                index_name = str(idx.get("index") or "")
                if not index_name:
                    continue
                flagged.append(
                    {
                        "resource_id": f"{domain_name}:{index_name}",
                        "resource_type": "opensearch_index",
                        "resource_name": index_name,
                        "region": region_name,
                        "state": str(idx.get("status") or "unknown"),
                        "metadata": {
                            "domain_name": domain_name,
                            "replicas": rep,
                            "max_replicas": max_replicas,
                            "store_size_gib": round(size_gib, 3),
                            "min_index_size_gib": min_index_size_gib,
                            "recommended_action": "reduce_index_replicas",
                            "check_reason": create_check_reason(
                                "underutilized",
                                {"resource": "opensearch_index", "replicas": rep, "threshold": max_replicas},
                            ),
                        },
                    }
                )
        except Exception as exc:
            print(f"Error checking over-replicated indices for {domain_name}: {exc}")
    return flagged


check_registry.register(
    CheckMetadata(
        check_id="opensearch_over_replicated_indices",
        name="OpenSearch Over-Replicated Indices",
        description="Identifies indices with replica count above a configurable threshold",
        resource_type="opensearch_index",
        check_function=check_opensearch_over_replicated_indices,
        default_action="reduce_index_replicas",
        parameters={
            "max_replicas": 1,
            "min_index_size_gib": 1.0,
            "region": None,
        },
    )
)

