"""OpenSearch idle domains check."""
from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional

from app.checks.base import create_check_reason
from app.checks.registry import CheckMetadata, check_registry
from app.checks.opensearch._common import describe_domain, get_os_client, list_domains, metric_stat


def check_opensearch_idle_domains(
    aws_adapter,
    lookback_days: int = 14,
    cpu_threshold: float = 10.0,
    max_search_rate: float = 1.0,
    max_index_rate: float = 1.0,
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
            cpu = metric_stat(cw, domain_name, "CPUUtilization", start_date, end_date, "Average")
            search_rate = metric_stat(cw, domain_name, "SearchRate", start_date, end_date, "Average")
            index_rate = metric_stat(cw, domain_name, "IndexingRate", start_date, end_date, "Average")
            if None in {cpu, search_rate, index_rate}:
                continue
            if cpu > cpu_threshold or search_rate > max_search_rate or index_rate > max_index_rate:
                continue
            flagged.append(
                {
                    "resource_id": domain_name,
                    "resource_type": "opensearch_domain",
                    "resource_name": domain_name,
                    "region": region,
                    "state": status.get("Processing") and "processing" or "active",
                    "metadata": {
                        "lookback_days": lookback_days,
                        "avg_cpu_utilization": round(cpu, 2),
                        "avg_search_rate": round(search_rate, 3),
                        "avg_indexing_rate": round(index_rate, 3),
                        "cpu_threshold": cpu_threshold,
                        "max_search_rate": max_search_rate,
                        "max_index_rate": max_index_rate,
                        "recommended_action": "decommission_or_downsize_domain",
                        "check_reason": create_check_reason(
                            "unused",
                            {
                                "reason": "Very low CPU and near-zero search/index activity across lookback window"
                            },
                        ),
                    },
                }
            )
        except Exception as exc:
            print(f"Error checking idle OpenSearch domain {domain_name}: {exc}")
    return flagged


check_registry.register(
    CheckMetadata(
        check_id="opensearch_idle_domains",
        name="OpenSearch Idle Domains",
        description="Identifies OpenSearch domains with minimal activity and low CPU",
        resource_type="opensearch_domain",
        check_function=check_opensearch_idle_domains,
        default_action="decommission_or_downsize_domain",
        parameters={
            "lookback_days": 14,
            "cpu_threshold": 10.0,
            "max_search_rate": 1.0,
            "max_index_rate": 1.0,
            "region": None,
        },
    )
)

