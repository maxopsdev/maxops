"""OpenSearch oversharded indices check."""
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


def check_opensearch_oversharded_indices(
    aws_adapter,
    max_shards_per_node: int = 100,
    max_avg_shard_size_gib: float = 10.0,
    region: Optional[str] = None,
) -> List[Dict[str, Any]]:
    os_client = get_os_client(aws_adapter, region)
    region_name = region or os_client.meta.region_name
    flagged: List[Dict[str, Any]] = []

    for domain_name in list_domains(os_client):
        try:
            status = describe_domain(os_client, domain_name)
            cluster_cfg = status.get("ClusterConfig") or {}
            nodes = int(cluster_cfg.get("InstanceCount") or 0)
            if nodes <= 0:
                continue
            endpoint = get_domain_endpoint(status)
            if not endpoint:
                continue
            indices = signed_get_json(
                aws_adapter,
                region_name,
                endpoint,
                "/_cat/indices?format=json&h=index,pri,rep,store.size,status",
            )
            if not isinstance(indices, list):
                continue
            for idx in indices:
                pri = int(to_float(idx.get("pri"), default=0))
                rep = int(to_float(idx.get("rep"), default=0))
                total_shards = pri * (rep + 1)
                shards_per_node = total_shards / max(1, nodes)
                if shards_per_node <= max_shards_per_node:
                    continue
                size_text = str(idx.get("store.size") or "")
                size_gib = 0.0
                if size_text.endswith("gb"):
                    size_gib = to_float(size_text[:-2])
                elif size_text.endswith("mb"):
                    size_gib = to_float(size_text[:-2]) / 1024.0
                elif size_text.endswith("tb"):
                    size_gib = to_float(size_text[:-2]) * 1024.0
                avg_shard_gib = size_gib / max(1, total_shards)
                if avg_shard_gib > max_avg_shard_size_gib:
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
                            "data_nodes": nodes,
                            "primary_shards": pri,
                            "replicas": rep,
                            "total_shards": total_shards,
                            "shards_per_node": round(shards_per_node, 3),
                            "avg_shard_size_gib": round(avg_shard_gib, 3),
                            "max_shards_per_node": max_shards_per_node,
                            "max_avg_shard_size_gib": max_avg_shard_size_gib,
                            "recommended_action": "reduce_shard_count",
                            "check_reason": create_check_reason(
                                "underutilized",
                                {"resource": "opensearch_index", "shards_per_node": round(shards_per_node, 3)},
                            ),
                        },
                    }
                )
        except Exception as exc:
            print(f"Error checking oversharded indices for {domain_name}: {exc}")
    return flagged


check_registry.register(
    CheckMetadata(
        check_id="opensearch_oversharded_indices",
        name="OpenSearch Oversharded Indices",
        description="Identifies indices with high shard density and small average shard size",
        resource_type="opensearch_index",
        check_function=check_opensearch_oversharded_indices,
        default_action="reduce_shard_count",
        parameters={
            "max_shards_per_node": 100,
            "max_avg_shard_size_gib": 10.0,
            "region": None,
        },
    )
)

