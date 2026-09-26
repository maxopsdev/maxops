"""OpenSearch low storage utilization check."""
from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional

from app.checks.base import create_check_reason
from app.checks.registry import CheckMetadata, check_registry
from app.checks.opensearch._common import describe_domain, get_os_client, list_domains, metric_stat


def check_opensearch_low_storage_utilization(
    aws_adapter,
    lookback_days: int = 14,
    min_free_storage_ratio: float = 0.7,
    region: Optional[str] = None,
) -> List[Dict[str, Any]]:
    os_client = get_os_client(aws_adapter, region)
    cw = aws_adapter.session.client("cloudwatch", region_name=region)
    start_date = datetime.utcnow() - timedelta(days=lookback_days)
    end_date = datetime.utcnow()
    flagged: List[Dict[str, Any]] = []

    for domain_name in list_domains(os_client):
        try:
            status = describe_domain(os_client, domain_name)
            ebs = status.get("EBSOptions") or {}
            if not ebs.get("EBSEnabled"):
                continue
            volume_size_gib = float(ebs.get("VolumeSize") or 0)
            node_count = float((status.get("ClusterConfig") or {}).get("InstanceCount") or 0)
            if volume_size_gib <= 0 or node_count <= 0:
                continue
            total_allocated_gib = volume_size_gib * node_count
            free_storage_mib = metric_stat(
                cw, domain_name, "FreeStorageSpace", start_date, end_date, "Average"
            )
            if free_storage_mib is None:
                continue
            free_storage_gib = free_storage_mib / 1024.0
            free_ratio = free_storage_gib / max(1.0, total_allocated_gib)
            if free_ratio < min_free_storage_ratio:
                continue
            flagged.append(
                {
                    "resource_id": domain_name,
                    "resource_type": "opensearch_domain",
                    "resource_name": domain_name,
                    "region": region,
                    "state": status.get("Processing") and "processing" or "active",
                    "metadata": {
                        "allocated_storage_gib": round(total_allocated_gib, 2),
                        "avg_free_storage_gib": round(free_storage_gib, 2),
                        "avg_free_storage_ratio": round(free_ratio, 3),
                        "min_free_storage_ratio": min_free_storage_ratio,
                        "recommended_action": "reduce_storage_or_node_count",
                        "check_reason": create_check_reason(
                            "underutilized",
                            {"free_storage_ratio": round(free_ratio, 3), "threshold": min_free_storage_ratio},
                        ),
                    },
                }
            )
        except Exception as exc:
            print(f"Error checking low storage utilization for {domain_name}: {exc}")
    return flagged


check_registry.register(
    CheckMetadata(
        check_id="opensearch_low_storage_utilization",
        name="OpenSearch Low Storage Utilization",
        description="Identifies domains with high free storage ratio over a lookback window",
        resource_type="opensearch_domain",
        check_function=check_opensearch_low_storage_utilization,
        default_action="reduce_storage_or_node_count",
        parameters={
            "lookback_days": 14,
            "min_free_storage_ratio": 0.7,
            "region": None,
        },
    )
)

