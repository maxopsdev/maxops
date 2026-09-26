"""Aurora non-Graviton instance check."""
from __future__ import annotations

from typing import Any, Dict, List, Optional

from app.checks.base import create_check_reason
from app.checks.registry import CheckMetadata, check_registry
from app.checks.aurora._common import is_graviton_class, list_aurora_clusters, list_instances_for_cluster


def check_aurora_non_graviton_instances(
    aws_adapter,
    region: Optional[str] = None,
) -> List[Dict[str, Any]]:
    rds = aws_adapter.session.client("rds", region_name=region)
    flagged: List[Dict[str, Any]] = []
    for cluster in list_aurora_clusters(rds):
        cluster_id = cluster.get("DBClusterIdentifier")
        if not cluster_id:
            continue
        try:
            for inst in list_instances_for_cluster(rds, cluster_id):
                instance_id = inst.get("DBInstanceIdentifier")
                instance_class = str(inst.get("DBInstanceClass") or "")
                if not instance_id or not instance_class:
                    continue
                if is_graviton_class(instance_class):
                    continue
                flagged.append(
                    {
                        "resource_id": instance_id,
                        "resource_type": "aurora_instance",
                        "resource_name": instance_id,
                        "region": region,
                        "state": inst.get("DBInstanceStatus", "unknown"),
                        "metadata": {
                            "cluster_id": cluster_id,
                            "db_instance_class": instance_class,
                            "recommended_action": "migrate_to_graviton",
                            "check_reason": create_check_reason(
                                "cost_efficiency",
                                {
                                    "resource": "aurora_instance",
                                    "instance_id": instance_id,
                                    "issue": "non_graviton_instance_class",
                                },
                            ),
                        },
                    }
                )
        except Exception as exc:
            print(f"Error checking Aurora non-Graviton instances for {cluster_id}: {exc}")
    return flagged


check_registry.register(
    CheckMetadata(
        check_id="aurora_non_graviton_instances",
        name="Aurora Non-Graviton Instances",
        description="Identifies Aurora instances that are not using Graviton classes",
        resource_type="aurora_instance",
        check_function=check_aurora_non_graviton_instances,
        default_action="migrate_to_graviton",
        parameters={
            "region": None,
        },
    )
)

