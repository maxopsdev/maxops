"""CUR-observed and seed fallback prices for S3 Optimizer Phase 1."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Iterable, Mapping, Optional

from app.services.s3_bucket_source import normalize_usage_type
from app.services.street_pricing import StreetPricingService


SEED_PATH = Path(__file__).resolve().parents[2] / "pricing" / "s3_price_seed.json"
S3_PRICING_URL = "https://aws.amazon.com/s3/pricing/"
STORAGE_CLASSES = (
    "STANDARD",
    "STANDARD_IA",
    "ONEZONE_IA",
    "GLACIER_IR",
    "GLACIER",
    "DEEP_ARCHIVE",
    "INTELLIGENT_TIERING",
)
REQUEST_FAMILIES = ("data_read", "data_write", "list", "config", "tier_unsplit")
TRANSITION_CLASSES = ("STANDARD_IA", "ONEZONE_IA", "GLACIER_IR", "INTELLIGENT_TIERING", "GLACIER", "DEEP_ARCHIVE")
RETRIEVAL_CLASSES = ("STANDARD_IA", "ONEZONE_IA", "GLACIER_IR", "GLACIER", "DEEP_ARCHIVE")
RETRIEVAL_SPEEDS = ("standard", "bulk", "expedited")


def _canonical_keys() -> list[str]:
    """Return the frozen set of canonical keys requested by the Phase-1 engine."""

    keys = [f"storage.{storage_class}.gb_month" for storage_class in STORAGE_CLASSES]
    keys.extend(
        f"request.{storage_class}.{family}.per_1000"
        for storage_class in STORAGE_CLASSES
        for family in REQUEST_FAMILIES
    )
    keys.extend(f"transition.{storage_class}.per_1000" for storage_class in TRANSITION_CLASSES)
    keys.extend(
        f"retrieval.{storage_class}.{speed}.gb"
        for storage_class in RETRIEVAL_CLASSES
        for speed in RETRIEVAL_SPEEDS
    )
    # Instant retrieval is a distinct billing concept from Glacier Flexible's
    # standard retrieval.  Keep the legacy ``standard`` keys above for one
    # release so stored/third-party maps remain readable, but resolve the
    # optimizer's IA/GIR observations against the explicit instant keys.
    keys.extend(
        f"retrieval.{storage_class}.instant.gb"
        for storage_class in ("STANDARD_IA", "ONEZONE_IA", "GLACIER_IR")
    )
    keys.extend(
        f"restore_request.{storage_class}.{speed}.per_1000"
        for storage_class in ("GLACIER", "DEEP_ARCHIVE")
        for speed in RETRIEVAL_SPEEDS
    )
    # CUR reports early-delete usage in GB-Hours.  The resolver converts the
    # observed rate to the engine's per-GB-month basis with 730 hours/month.
    keys.extend(f"early_delete.{storage_class}.gb" for storage_class in RETRIEVAL_CLASSES)
    keys.extend(("it_monitoring.per_1000_objects", "data_transfer_out.gb"))
    return keys


CANONICAL_KEYS = tuple(_canonical_keys())


def _row_value(row: Any, name: str, default: Any = None) -> Any:
    """Read a price input field from a mapping or attribute row."""

    if isinstance(row, Mapping):
        return row.get(name, default)
    return getattr(row, name, default)


def _canonical_key(row: Any) -> Optional[str]:
    """Map one raw CUR row to a canonical price key, or return None."""

    usage_type = _row_value(row, "usage_type", _row_value(row, "line_item_usage_type"))
    operation = _row_value(row, "operation", _row_value(row, "line_item_operation"))
    classification = normalize_usage_type(
        usage_type,
        operation,
        _row_value(row, "pricing_unit"),
    )
    if classification.category == "it_monitoring_objects" and classification.unit is None:
        return None
    storage_class = classification.storage_class
    if classification.category == "storage_gb_month" and storage_class in STORAGE_CLASSES:
        return f"storage.{storage_class}.gb_month"
    if classification.category == "request" and storage_class:
        family = classification.family or "tier_unsplit"
        return f"request.{storage_class}.{family}.per_1000"
    if classification.category == "transition" and storage_class:
        return f"transition.{storage_class}.per_1000"
    if classification.category == "retrieval_gb" and storage_class:
        speed = classification.retrieval_speed or (
            "instant"
            if storage_class in {"STANDARD_IA", "ONEZONE_IA", "GLACIER_IR"}
            else "standard"
        )
        return f"retrieval.{storage_class}.{speed}.gb"
    if classification.category == "restore_request" and storage_class:
        speed = classification.retrieval_speed or "standard"
        return f"restore_request.{storage_class}.{speed}.per_1000"
    if classification.category == "early_delete_gb_hours" and storage_class:
        return f"early_delete.{storage_class}.gb"
    if classification.category == "it_monitoring_objects":
        return "it_monitoring.per_1000_objects"
    return None


def derive_prices_from_cur(
    rows: Iterable[Any],
    region: str,
    minimum_usage_amount: float = 0.001,
) -> dict[str, dict[str, Any]]:
    """Derive canonical prices from positive unblended CUR usage.

    Rows with zero cost or usage below ``minimum_usage_amount`` are ignored;
    an empty/insufficient input returns an empty mapping.  Net amortized cost
    is intentionally never consulted.
    """

    materialized = list(rows)
    sums: dict[str, list[float]] = {}
    units: dict[str, str] = {}
    for row in materialized:
        row_region = _row_value(row, "region")
        # A regional map must never absorb account/global rows with no region.
        if region is not None and row_region != region:
            continue
        usage = _row_value(row, "usage_amount")
        cost = _row_value(row, "unblended_cost")
        if usage is None or cost is None or float(cost) <= 0 or float(usage) < minimum_usage_amount:
            continue
        key = _canonical_key(row)
        if key is None:
            continue
        sums.setdefault(key, [0.0, 0.0])
        sums[key][0] += float(cost)
        sums[key][1] += float(usage)
        classification = normalize_usage_type(
            _row_value(row, "usage_type", _row_value(row, "line_item_usage_type")),
            _row_value(row, "operation", _row_value(row, "line_item_operation")),
            _row_value(row, "pricing_unit"),
        )
        units[key] = _price_unit(key, classification.unit)

    prices = {
        key: {
            # CUR request/retrieval quantities are raw requests/GB; canonical
            # request keys are quoted per 1,000, so normalize only those keys.
            "price": (
                cost / (usage / 1000.0)
                if key.endswith("per_1000")
                else cost / usage * 730.0
                if key.startswith("early_delete.")
                else cost / usage
            ),
            "unit": units.get(key, _price_unit(key, None)),
        }
        for key, (cost, usage) in sums.items()
        if usage > 0
    }
    # Old consumers still ask for ``standard`` on always-warm IA/GIR rows.
    # Preserve that lookup for one release while making the new instant key
    # the authoritative observed price for the optimizer.
    for storage_class in ("STANDARD_IA", "ONEZONE_IA", "GLACIER_IR"):
        instant_key = f"retrieval.{storage_class}.instant.gb"
        legacy_key = f"retrieval.{storage_class}.standard.gb"
        if instant_key in prices and legacy_key not in prices:
            prices[legacy_key] = dict(prices[instant_key])
    return prices


def _price_unit(key: str, observed_unit: Optional[str]) -> str:
    """Return the stable display unit for a canonical key."""

    if key.endswith("per_1000"):
        if key.startswith("it_monitoring"):
            return "objects/1000"
        return "requests/1000"
    if ".gb_month" in key:
        return "GB-month"
    if key.startswith("early_delete."):
        return "GB-month (derived from GB-Hours × 730)"
    return "GB"


def _load_seed() -> dict[str, dict[str, dict[str, Any]]]:
    """Load the checked-in seed, returning empty data if it is absent."""

    try:
        return json.loads(SEED_PATH.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return {}


def _window_shape(window: Any) -> dict[str, Any]:
    """Normalize tuple or mapping window inputs to the persisted shape."""

    if isinstance(window, Mapping):
        return {"start": str(window.get("start")), "end": str(window.get("end"))}
    if isinstance(window, (tuple, list)) and len(window) == 2:
        return {"start": str(window[0]), "end": str(window[1])}
    return {"start": None, "end": None}


def resolve(
    rows: Iterable[Any],
    region: str,
    window: Any,
    database_path: str | Path | None = None,
) -> dict[str, Any]:
    """Resolve canonical keys from CUR, street pricing, then the bootstrap seed.

    The returned ``unresolved`` list is non-empty only when a requested key is
    absent from both sources.  An empty input still returns seed prices where
    the region has a seed table; unknown inputs never become a zero price.
    """

    observed = derive_prices_from_cur(rows, region)
    street_prices = StreetPricingService(database_path).get_s3_price_map(region, CANONICAL_KEYS)
    seeds = _load_seed().get("us-east-1", {}) if region == "us-east-1" else {}
    resolved: dict[str, dict[str, Any]] = {}
    unresolved: list[str] = []
    seed_dates: list[str] = []
    for key in CANONICAL_KEYS:
        if key in observed:
            resolved[key] = {
                "price": observed[key]["price"],
                "unit": observed[key]["unit"],
                "price_source": "cur_observed",
            }
            continue
        street = street_prices.get(key)
        if street is not None:
            resolved[key] = {
                "price": street["price"],
                "unit": street["unit"],
                "price_source": "street_pricing",
            }
            continue
        seed = seeds.get(key)
        if not isinstance(seed, Mapping) or seed.get("status") == "not_applicable" or seed.get("price") is None:
            unresolved.append(key)
            continue
        resolved[key] = {
            "price": seed["price"],
            "unit": seed["unit"],
            "price_source": "seed",
        }
        if seed.get("as_of"):
            seed_dates.append(str(seed["as_of"]))
    return {
        "resolved": resolved,
        "unresolved": unresolved,
        "price_map_window": _window_shape(window),
        "seed_as_of": max(seed_dates) if seed_dates else None,
    }
