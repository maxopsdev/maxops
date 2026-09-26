"""OpenSearch outdated instance generation check."""
from __future__ import annotations

from typing import Any, Dict, List, Optional

from app.checks.base import create_check_reason
from app.checks.registry import CheckMetadata, check_registry
from app.checks.opensearch._common import describe_domain, get_os_client, list_domains, parse_generation


def _is_outdated(instance_type: str, min_generation: int) -> bool:
    _fam, gen, _grav = parse_generation(instance_type)
    return gen is not None and gen < min_generation


def check_opensearch_outdated_instance_generation(
    aws_adapter,
    min_generation: int = 6,
    region: Optional[str] = None,
) -> List[Dict[str, Any]]:
    os_client = get_os_client(aws_adapter, region)
    flagged: List[Dict[str, Any]] = []
    for domain_name in list_domains(os_client):
        try:
            status = describe_domain(os_client, domain_name)
            cfg = status.get("ClusterConfig") or {}
            data_type = str(cfg.get("InstanceType") or "")
            master_type = str(cfg.get("DedicatedMasterType") or "")
            warm_type = str(cfg.get("WarmType") or "")
            outdated_types = []
            for t in [data_type, master_type, warm_type]:
                if t and _is_outdated(t, min_generation):
                    outdated_types.append(t)
            if not outdated_types:
                continue
            flagged.append(
                {
                    "resource_id": domain_name,
                    "resource_type": "opensearch_domain",
                    "resource_name": domain_name,
                    "region": region,
                    "state": status.get("Processing") and "processing" or "active",
                    "metadata": {
                        "outdated_instance_types": sorted(set(outdated_types)),
                        "min_generation": min_generation,
                        "recommended_action": "upgrade_instance_generation",
                        "check_reason": create_check_reason(
                            "version_outdated",
                            {
                                "resource": "opensearch_domain",
                                "domain_name": domain_name,
                                "outdated_instance_types": sorted(set(outdated_types)),
                                "min_generation": min_generation,
                            },
                        ),
                    },
                }
            )
        except Exception as exc:
            print(f"Error checking OpenSearch instance generation for {domain_name}: {exc}")
    return flagged


check_registry.register(
    CheckMetadata(
        check_id="opensearch_outdated_instance_generation",
        name="OpenSearch Outdated Instance Generation",
        description="Identifies OpenSearch domains using older instance generations",
        resource_type="opensearch_domain",
        check_function=check_opensearch_outdated_instance_generation,
        default_action="upgrade_instance_generation",
        parameters={
            "min_generation": 6,
            "region": None,
        },
    )
)

