"""OpenSearch underutilized data nodes check."""
from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional

from app.checks.base import create_check_reason
from app.checks.registry import CheckMetadata, check_registry
from app.checks.opensearch._common import describe_domain, get_os_client, list_domains, metric_stat


def check_opensearch_underutilized_data_nodes(
    aws_adapter,
    lookback_days: int = 14,
    cpu_threshold: float = 25.0,
    jvm_pressure_threshold: float = 60.0,
    max_search_rate: float = 5.0,
    max_index_rate: float = 5.0,
    min_data_nodes: int = 2,
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
            cluster_cfg = status.get("ClusterConfig") or {}
            node_count = int(cluster_cfg.get("InstanceCount") or 0)
            if node_count < min_data_nodes:
                continue

            cpu = metric_stat(cw, domain_name, "CPUUtilization", start_date, end_date, "Average")
            jvm = metric_stat(cw, domain_name, "JVMMemoryPressure", start_date, end_date, "Average")
            search_rate = metric_stat(cw, domain_name, "SearchRate", start_date, end_date, "Average")
            index_rate = metric_stat(cw, domain_name, "IndexingRate", start_date, end_date, "Average")
            if None in {cpu, jvm, search_rate, index_rate}:
                continue
            if cpu > cpu_threshold or jvm > jvm_pressure_threshold:
                continue
            if search_rate > max_search_rate or index_rate > max_index_rate:
                continue

            flagged.append(
                {
                    "resource_id": domain_name,
                    "resource_type": "opensearch_domain",
                    "resource_name": domain_name,
                    "region": region,
                    "state": status.get("Processing") and "processing" or "active",
                    "metadata": {
                        "instance_type": cluster_cfg.get("InstanceType"),
                        "instance_count": node_count,
                        "lookback_days": lookback_days,
                        "avg_cpu_utilization": round(cpu, 2),
                        "avg_jvm_memory_pressure": round(jvm, 2),
                        "avg_search_rate": round(search_rate, 3),
                        "avg_indexing_rate": round(index_rate, 3),
                        "recommended_action": "rightsize_data_nodes",
                        "check_reason": create_check_reason(
                            "underutilized",
                            {"avg_cpu": round(cpu, 2), "cpu_threshold": cpu_threshold},
                        ),
                    },
                }
            )
        except Exception as exc:
            print(f"Error checking OpenSearch underutilized nodes for {domain_name}: {exc}")
    return flagged


check_registry.register(
    CheckMetadata(
        check_id="opensearch_underutilized_data_nodes",
        name="OpenSearch Underutilized Data Nodes",
        description="Identifies OpenSearch domains with low CPU/JVM and low query/index rates",
        resource_type="opensearch_domain",
        check_function=check_opensearch_underutilized_data_nodes,
        default_action="rightsize_data_nodes",
        parameters={
            "lookback_days": 14,
            "cpu_threshold": 25.0,
            "jvm_pressure_threshold": 60.0,
            "max_search_rate": 5.0,
            "max_index_rate": 5.0,
            "min_data_nodes": 2,
            "region": None,
        },
    )
)

