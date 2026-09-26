"""OpenSearch old data not tiered check."""
from __future__ import annotations

import re
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional

from app.checks.base import create_check_reason
from app.checks.registry import CheckMetadata, check_registry
from app.checks.opensearch._common import describe_domain, get_domain_endpoint, get_os_client, list_domains, signed_get_json

_DATE_PATTERNS = [
    re.compile(r".*(\d{4})[.\-](\d{2})[.\-](\d{2}).*"),
]


def _extract_date(index_name: str) -> Optional[datetime]:
    s = str(index_name or "")
    for pat in _DATE_PATTERNS:
        m = pat.match(s)
        if not m:
            continue
        try:
            return datetime(int(m.group(1)), int(m.group(2)), int(m.group(3)))
        except ValueError:
            return None
    return None


def check_opensearch_old_cold_data_not_tiered(
    aws_adapter,
    old_data_days: int = 30,
    min_old_indices: int = 20,
    region: Optional[str] = None,
) -> List[Dict[str, Any]]:
    os_client = get_os_client(aws_adapter, region)
    region_name = region or os_client.meta.region_name
    cutoff = datetime.utcnow() - timedelta(days=old_data_days)
    flagged: List[Dict[str, Any]] = []

    for domain_name in list_domains(os_client):
        try:
            status = describe_domain(os_client, domain_name)
            cfg = status.get("ClusterConfig") or {}
            if cfg.get("WarmEnabled"):
                continue
            endpoint = get_domain_endpoint(status)
            if not endpoint:
                continue
            indices = signed_get_json(
                aws_adapter,
                region_name,
                endpoint,
                "/_cat/indices?format=json&h=index,status",
            )
            if not isinstance(indices, list):
                continue
            old_count = 0
            for idx in indices:
                d = _extract_date(str(idx.get("index") or ""))
                if d and d < cutoff:
                    old_count += 1
            if old_count < min_old_indices:
                continue
            flagged.append(
                {
                    "resource_id": domain_name,
                    "resource_type": "opensearch_domain",
                    "resource_name": domain_name,
                    "region": region_name,
                    "state": status.get("Processing") and "processing" or "active",
                    "metadata": {
                        "old_data_days": old_data_days,
                        "old_indices_count": old_count,
                        "min_old_indices": min_old_indices,
                        "warm_enabled": False,
                        "recommended_action": "enable_warm_or_cold_tiering",
                        "check_reason": create_check_reason(
                            "underutilized",
                            {"resource": "opensearch_domain", "old_indices_count": old_count},
                        ),
                    },
                }
            )
        except Exception as exc:
            print(f"Error checking old/cold data tiering for {domain_name}: {exc}")
    return flagged


check_registry.register(
    CheckMetadata(
        check_id="opensearch_old_cold_data_not_tiered",
        name="OpenSearch Old Cold Data Not Tiered",
        description="Identifies domains with many old time-series indices but no warm tier",
        resource_type="opensearch_domain",
        check_function=check_opensearch_old_cold_data_not_tiered,
        default_action="enable_warm_or_cold_tiering",
        parameters={
            "old_data_days": 30,
            "min_old_indices": 20,
            "region": None,
        },
    )
)

