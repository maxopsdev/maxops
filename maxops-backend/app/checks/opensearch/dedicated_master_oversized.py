"""OpenSearch dedicated master oversized check."""
from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional

from app.checks.base import create_check_reason
from app.checks.registry import CheckMetadata, check_registry
from app.checks.opensearch._common import describe_domain, get_os_client, list_domains, metric_stat


def check_opensearch_dedicated_master_oversized(
    aws_adapter,
    lookback_days: int = 14,
    master_cpu_threshold: float = 25.0,
    min_dedicated_masters: int = 3,
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
            cfg = status.get("ClusterConfig") or {}
            if not cfg.get("DedicatedMasterEnabled"):
                continue
            dedicated_count = int(cfg.get("DedicatedMasterCount") or 0)
            if dedicated_count < min_dedicated_masters:
                continue
            master_cpu = metric_stat(
                cw, domain_name, "MasterCPUUtilization", start_date, end_date, "Average"
            )
            if master_cpu is None or master_cpu > master_cpu_threshold:
                continue
            flagged.append(
                {
                    "resource_id": domain_name,
                    "resource_type": "opensearch_domain",
                    "resource_name": domain_name,
                    "region": region,
                    "state": status.get("Processing") and "processing" or "active",
                    "metadata": {
                        "dedicated_master_type": cfg.get("DedicatedMasterType"),
                        "dedicated_master_count": dedicated_count,
                        "lookback_days": lookback_days,
                        "avg_master_cpu_utilization": round(master_cpu, 2),
                        "master_cpu_threshold": master_cpu_threshold,
                        "recommended_action": "downsize_dedicated_masters",
                        "check_reason": create_check_reason(
                            "underutilized",
                            {"avg_cpu": round(master_cpu, 2), "cpu_threshold": master_cpu_threshold},
                        ),
                    },
                }
            )
        except Exception as exc:
            print(f"Error checking dedicated master sizing for {domain_name}: {exc}")
    return flagged


check_registry.register(
    CheckMetadata(
        check_id="opensearch_dedicated_master_oversized",
        name="OpenSearch Dedicated Master Oversized",
        description="Identifies domains with low dedicated-master CPU utilization",
        resource_type="opensearch_domain",
        check_function=check_opensearch_dedicated_master_oversized,
        default_action="downsize_dedicated_masters",
        parameters={
            "lookback_days": 14,
            "master_cpu_threshold": 25.0,
            "min_dedicated_masters": 3,
            "region": None,
        },
    )
)

