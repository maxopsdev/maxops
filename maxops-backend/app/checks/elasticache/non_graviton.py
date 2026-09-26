"""
ElastiCache Check - Non-Graviton Instance Class

Flags ElastiCache resources that are NOT using Graviton-based instance classes
(e.g., cache.m6g.large, cache.r6g.xlarge, cache.t4g.micro, cache.m7g.2xlarge).

Heuristic:
- Parse CacheNodeType like "cache.m6g.large" -> family = "m6g"
- If the family segment contains a "g" family marker (e.g., m6g, r6gd, t4g, x2gd),
  treat it as Graviton.
- Skip instances where CacheNodeType is missing (avoid false positives).

Assumptions:
- aws_adapter.get_resources("elasticache_replication_group", {}, region) OR
  aws_adapter.get_resources("elasticache_cluster", {}, region)
- CacheNodeType is present in resource metadata (best-effort key lookups)
"""

from __future__ import annotations

import re
from typing import Any, Dict, List, Optional, Tuple

from app.checks.registry import check_registry, CheckMetadata
from app.checks.base import create_check_reason


_GRAVITON_FAMILY_RE = re.compile(r"^[a-z0-9]+g[a-z0-9]*$")  # m6g, r6gd, t4g, x2gd, m7g, etc.


def _get_cache_resources(aws_adapter, region: Optional[str]) -> List[Tuple[str, Dict[str, Any]]]:
    out: List[Tuple[str, Dict[str, Any]]] = []
    for rt in ("elasticache_replication_group", "elasticache_cluster"):
        try:
            items = aws_adapter.get_resources(rt, {}, region)
            if isinstance(items, list):
                for it in items:
                    if isinstance(it, dict):
                        out.append((rt, it))
        except Exception:
            continue
    return out


def _get_resource_name(r: Dict[str, Any]) -> Optional[str]:
    return r.get("resource_id") or r.get("id") or r.get("name")


def _md(r: Dict[str, Any]) -> Dict[str, Any]:
    return r.get("metadata") or {}


def _cache_node_type(r: Dict[str, Any]) -> Optional[str]:
    md = _md(r)
    v = (
        md.get("CacheNodeType")
        or md.get("cache_node_type")
        or md.get("cacheNodeType")
        or r.get("CacheNodeType")
        or r.get("cache_node_type")
    )
    if v is None:
        return None
    s = str(v).strip()
    return s if s else None


def _is_graviton_instance_class(node_type: str) -> bool:
    """
    Returns True if node_type looks like a Graviton-based ElastiCache class.

    Examples:
      - cache.m6g.large -> True
      - cache.r6gd.xlarge -> True
      - cache.t4g.micro -> True
      - cache.m5.large -> False
    """
    parts = node_type.strip().lower().split(".")
    # Expected: ["cache", "<family>", "<size>"] (sometimes size has multiple segments, but family is parts[1])
    if len(parts) < 3:
        return False
    family = parts[1].strip()
    return bool(_GRAVITON_FAMILY_RE.match(family))


def check_elasticache_non_graviton_instance_class(
    aws_adapter,
    region: Optional[str] = None,
) -> List[Dict[str, Any]]:
    """
    Identify ElastiCache resources not using Graviton instance classes.
    """
    flagged: List[Dict[str, Any]] = []

    for rt, r in _get_cache_resources(aws_adapter, region):
        rid = _get_resource_name(r)
        if not rid:
            continue

        try:
            node_type = _cache_node_type(r)
            if not node_type:
                # Unknown / missing node type -> skip to avoid false positives
                continue

            if not _is_graviton_instance_class(node_type):
                md = r.setdefault("metadata", {})
                md["recommended_action"] = "elasticache_migrate_graviton"
                md["recommended_actions"] = [
                    "elasticache_migrate_graviton",
                ]
                md["cache_node_type"] = node_type
                md["check_reason"] = create_check_reason("cost_efficiency", {
                    "resource": rt,
                    "id": rid,
                    "issue": "non_graviton_instance_class",
                    "cache_node_type": node_type,
                })
                flagged.append(r)

        except Exception as e:
            print(f"Error checking ElastiCache node type for {rt}:{rid}: {e}")
            continue

    return flagged


check_registry.register(CheckMetadata(
    check_id="elasticache_non_graviton_instance_class",
    name="ElastiCache Non-Graviton Instance Class",
    description="Identifies ElastiCache resources that are not using Graviton-based instance classes",
    resource_type="elasticache",
    check_function=check_elasticache_non_graviton_instance_class,
    default_action="elasticache_migrate_graviton",
    parameters={
        "region": None,
    },
))
