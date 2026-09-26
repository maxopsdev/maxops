"""
ElastiCache Check - Redis OSS Engine Convertible to Valkey

Flags ElastiCache resources that are running Redis OSS (node-based) and are candidates
to migrate/upgrade to Valkey.

This check is intentionally conservative and configurable:
- It flags engine == redis/redis oss
- Optionally only flags if EngineVersion >= eligible_min_redis_version (default "6.0")

Assumptions:
- aws_adapter.get_resources("elasticache_replication_group", {}, region) OR
  aws_adapter.get_resources("elasticache_cluster", {}, region)
- Engine and EngineVersion are present in resource metadata (best-effort key lookups)
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional, Tuple

from app.checks.registry import check_registry, CheckMetadata
from app.checks.base import create_check_reason


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


def _engine(r: Dict[str, Any]) -> str:
    md = _md(r)
    return str(md.get("Engine") or md.get("engine") or r.get("Engine") or r.get("engine") or "").strip().lower()


def _engine_version(r: Dict[str, Any]) -> str:
    md = _md(r)
    return str(md.get("EngineVersion") or md.get("engine_version") or md.get("engineVersion") or r.get("EngineVersion") or "").strip()


def _parse_version(v: Any) -> Optional[tuple[int, ...]]:
    if v is None:
        return None
    s = str(v).strip()
    if not s:
        return None
    parts = s.split(".")
    out: List[int] = []
    for p in parts:
        p = p.strip()
        if p == "":
            out.append(0)
            continue
        try:
            out.append(int(p))
        except ValueError:
            return None
    return tuple(out)


def _version_gte(a: Any, b: Any) -> Optional[bool]:
    pa = _parse_version(a)
    pb = _parse_version(b)
    if pa is None or pb is None:
        return None
    n = max(len(pa), len(pb))
    pa = pa + (0,) * (n - len(pa))
    pb = pb + (0,) * (n - len(pb))
    return pa >= pb


def check_elasticache_redis_convertible_to_valkey(
    aws_adapter,
    eligible_min_redis_version: str = "6.0",
    region: Optional[str] = None,
) -> List[Dict[str, Any]]:
    flagged: List[Dict[str, Any]] = []

    for rt, r in _get_cache_resources(aws_adapter, region):
        rid = _get_resource_name(r)
        if not rid:
            continue

        try:
            if rt == "elasticache_cluster" and _md(r).get("ReplicationGroupId"):
                # Replication-group members are represented by their parent group.
                continue
            eng = _engine(r)
            if eng not in {"redis", "redis oss", "redisos", "redis_oss"}:
                continue

            ver = _engine_version(r)
            ok = _version_gte(ver, eligible_min_redis_version)

            # If version is unparsable, skip (avoid false positives)
            if ok is not True:
                continue

            md = r.setdefault("metadata", {})
            md["recommended_action"] = "elasticache_upgrade_valkey"
            md["recommended_actions"] = ["elasticache_upgrade_valkey"]
            md["current_engine"] = eng
            md["current_engine_version"] = ver
            md["target_engine"] = "valkey"
            md["eligible_min_redis_version"] = eligible_min_redis_version
            md["check_reason"] = create_check_reason("upgrade_opportunity", {
                "resource": rt,
                "id": rid,
                "current_engine": eng,
                "current_engine_version": ver,
                "target_engine": "valkey",
                "eligible_min_redis_version": eligible_min_redis_version,
            })
            flagged.append(r)

        except Exception as e:
            print(f"Error checking redis->valkey eligibility for {rt}:{rid}: {e}")
            continue

    return flagged


check_registry.register(CheckMetadata(
    check_id="elasticache_redis_convertible_to_valkey",
    name="ElastiCache Redis Convertible to Valkey",
    description="Identifies ElastiCache Redis OSS resources that are candidates to migrate/upgrade to Valkey",
    resource_type="elasticache",
    check_function=check_elasticache_redis_convertible_to_valkey,
    default_action="elasticache_upgrade_valkey",
    parameters={
        "eligible_min_redis_version": "6.0",
        "region": None,
    },
))
