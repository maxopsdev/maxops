"""Actual billed cost lookups backed by the local CUR aggregate cache.

The CUR pipeline (``stacks/CUR`` to create the export, ``pricing/cur`` to
aggregate it into Parquet) produces a month-partitioned cache of real billed
cost per resource. This module is the bridge between that cache and the
pricing layer, so a resource that appears in the bill is priced from what it
actually cost rather than from a list-price estimate.

Nothing here reaches AWS. It reads Parquet that a prior refresh already wrote,
and when no cache is present every lookup simply returns ``None`` and callers
fall back to their existing estimates.
"""
from __future__ import annotations

import logging
import threading
import time
from pathlib import Path
from typing import Any, Dict, Optional, Tuple

from app.config import settings
from pricing.cur.months import BillingMonth
from pricing.cur.reader import resource_monthly_costs

logger = logging.getLogger(__name__)


# Whether CUR pricing is on is a stored preference, but it is consulted on the
# hot path for every resource priced during a scan. It is cached here so that
# path stays free of database queries; writes go through set_cur_pricing_enabled
# and startup primes it via load_cur_pricing_enabled.
_enabled_cache: Optional[bool] = None


def is_cur_pricing_enabled() -> bool:
    """Stored preference if one has been set, otherwise the environment default."""
    if _enabled_cache is not None:
        return _enabled_cache
    return bool(settings.cur_pricing_enabled)


def load_cur_pricing_enabled(db) -> bool:
    """Read the stored preference into the cache. Safe to call at startup."""
    global _enabled_cache
    try:
        from app.services.settings_service import get_account_settings

        account_settings = get_account_settings(db)
        stored = getattr(account_settings, "cur_pricing_enabled", None) if account_settings else None
        _enabled_cache = None if stored is None else bool(stored)
    except Exception as exc:  # noqa: BLE001 - never block startup on this
        logger.warning("Could not read CUR pricing preference: %s", exc)
        _enabled_cache = None
    return is_cur_pricing_enabled()


def set_cur_pricing_enabled(db, value: bool) -> bool:
    """Persist the preference and update the cache so it takes effect at once."""
    global _enabled_cache
    from app.services.settings_service import get_account_settings

    account_settings = get_account_settings(db)
    if account_settings is None:
        raise ValueError("No account settings exist yet; complete onboarding first.")

    account_settings.cur_pricing_enabled = bool(value)
    db.commit()
    _enabled_cache = bool(value)
    return _enabled_cache


def reset_cur_pricing_cache() -> None:
    """Drop the cached preference so the next read resolves it again."""
    global _enabled_cache
    _enabled_cache = None


def normalize_resource_id(value: Any) -> Optional[str]:
    """Reduce a CUR resource identifier to the bare id MaxOps inventories.

    CUR records some resources by bare id (``i-0abc``, ``vol-0def``) and others
    by full ARN (``arn:aws:rds:us-east-1:123456789012:db:mydb``). MaxOps stores
    the bare identifier, so ARNs are reduced to their trailing segment.
    """
    if value is None:
        return None
    text = str(value).strip()
    if not text:
        return None
    if text.startswith("arn:"):
        # Trailing segment after the last ':' or '/', whichever comes later:
        # 'db:mydb' -> 'mydb', 'table/mytable' -> 'mytable'.
        tail = text.rsplit(":", 1)[-1]
        tail = tail.rsplit("/", 1)[-1]
        text = tail or text
    return text or None


class CurCostLookup:
    """Cached ``resource_id -> monthly cost`` view over the CUR Parquet cache.

    The map is loaded once and refreshed on a timer rather than per resource,
    because a scan prices thousands of resources and each load is a DuckDB
    aggregation over a month of billing data.
    """

    def __init__(
        self,
        *,
        cache_root: Optional[Path] = None,
        refresh_seconds: Optional[int] = None,
    ) -> None:
        self._cache_root = Path(cache_root or settings.cur_cache_root)
        self._refresh_seconds = (
            refresh_seconds
            if refresh_seconds is not None
            else settings.cur_pricing_refresh_seconds
        )
        self._lock = threading.Lock()
        self._loaded_at: Optional[float] = None
        self._billing_month: Optional[BillingMonth] = None
        # Exact CUR identifiers, plus normalized ids that resolved unambiguously.
        self._costs: Dict[str, float] = {}
        self._normalized: Dict[str, float] = {}

    @property
    def billing_month(self) -> Optional[str]:
        return str(self._billing_month) if self._billing_month else None

    def _is_stale(self, now: float) -> bool:
        if self._loaded_at is None:
            return True
        return (now - self._loaded_at) >= self._refresh_seconds

    def _load(self) -> None:
        month, costs = resource_monthly_costs(cache_root=self._cache_root)

        # A normalized id is only usable when it maps to exactly one CUR
        # resource. Two ARNs from different services can share a trailing
        # segment, and reporting one resource's bill against another is worse
        # than reporting no CUR cost at all.
        normalized: Dict[str, float] = {}
        collisions: set[str] = set()
        for raw_id, cost in costs.items():
            key = normalize_resource_id(raw_id)
            if not key or key == raw_id:
                continue
            if key in normalized and normalized[key] != cost:
                collisions.add(key)
                continue
            normalized[key] = cost
        for key in collisions:
            normalized.pop(key, None)

        self._billing_month = month
        self._costs = costs
        self._normalized = normalized
        if month is not None:
            logger.info(
                "Loaded CUR cost map: %d resources for billing month %s",
                len(costs),
                month,
            )

    def refresh(self, *, force: bool = False) -> None:
        now = time.monotonic()
        with self._lock:
            if not force and not self._is_stale(now):
                return
            try:
                self._load()
            except Exception as exc:
                # A broken or half-written cache must not take a scan down;
                # pricing falls back to list-price estimates instead.
                logger.warning("CUR cost cache unavailable, using list pricing: %s", exc)
                self._billing_month = None
                self._costs = {}
                self._normalized = {}
            self._loaded_at = now

    def get(self, resource_id: Any) -> Optional[Tuple[float, str]]:
        """Monthly billed cost for a resource, with the billing month it came from."""
        if not is_cur_pricing_enabled():
            return None
        lookup = str(resource_id or "").strip()
        if not lookup:
            return None

        self.refresh()
        if not self._costs:
            return None

        cost = self._costs.get(lookup)
        if cost is None:
            key = normalize_resource_id(lookup)
            if key:
                cost = self._normalized.get(key)
        if cost is None:
            return None

        month = self.billing_month
        if month is None:
            return None
        return float(cost), month

    def status(self) -> Dict[str, Any]:
        self.refresh()
        return {
            "enabled": is_cur_pricing_enabled(),
            "cache_root": str(self._cache_root),
            "billing_month": self.billing_month,
            "resource_count": len(self._costs),
        }


# Process-wide lookup. Scans price thousands of resources per run, so the
# Parquet aggregation is shared rather than repeated per request.
cur_cost_lookup = CurCostLookup()
