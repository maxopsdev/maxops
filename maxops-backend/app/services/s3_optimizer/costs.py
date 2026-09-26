"""Pure S3 optimizer cost formulas and the single class-rule registry."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping, Optional

from .object_counts import ARCHIVE_OVERHEAD_BYTES, BYTES_PER_GIB, LIFECYCLE_FLOOR_BYTES


@dataclass(frozen=True)
class StorageClassRule:
    """Billing constraints used by all candidate cost models."""

    min_billable_bytes: Optional[int]
    min_duration_days: int
    per_object_overhead: int
    retrieval_speed: Optional[str]
    monitoring_applies: bool
    price_class: str


_UNKNOWN_CLASS_RULE = StorageClassRule(None, 0, 0, None, False, "UNKNOWN")


STORAGE_CLASS_RULES = {
    "STANDARD": StorageClassRule(None, 0, 0, None, False, "STANDARD"),
    "STANDARD_IA": StorageClassRule(LIFECYCLE_FLOOR_BYTES, 30, 0, "instant", False, "STANDARD_IA"),
    "ONEZONE_IA": StorageClassRule(LIFECYCLE_FLOOR_BYTES, 30, 0, "instant", False, "ONEZONE_IA"),
    "GLACIER_IR": StorageClassRule(LIFECYCLE_FLOOR_BYTES, 90, 0, "instant", False, "GLACIER_IR"),
    "INTELLIGENT_TIERING": StorageClassRule(LIFECYCLE_FLOOR_BYTES, 30, 0, "instant", True, "INTELLIGENT_TIERING"),
    "GLACIER": StorageClassRule(None, 90, 40 * 1024, "standard", False, "GLACIER"),
    "GLACIER_FLEXIBLE": StorageClassRule(None, 90, 40 * 1024, "standard", False, "GLACIER"),
    "DEEP_ARCHIVE": StorageClassRule(None, 180, 40 * 1024, "standard", False, "DEEP_ARCHIVE"),
}


def _resolved(prices: Mapping[str, Any]) -> Mapping[str, Any]:
    """Accept either a resolved map or the full Phase-1 resolve result."""

    return prices.get("resolved", prices)


def price_for(prices: Mapping[str, Any], key: str) -> Optional[float]:
    """Return a numeric canonical price, or ``None`` when it is unresolved."""

    item = _resolved(prices).get(key)
    if item is None:
        return None
    if isinstance(item, Mapping):
        value = item.get("price")
    else:
        value = item
    return None if value is None else float(value)


def _mul(first: Optional[float], second: Optional[float]) -> Optional[float]:
    """Multiply two optional values without converting unknown to zero."""

    return None if first is None or second is None else first * second


def _add(values: list[Optional[float]]) -> Optional[float]:
    """Add optional terms, returning unknown if any contributing term is unknown."""

    return None if any(value is None for value in values) else sum(value for value in values if value is not None)


def class_price_key(prefix: str, target_class: str, suffix: str) -> str:
    """Build a canonical key while mapping candidate aliases to CUR classes."""

    rule = STORAGE_CLASS_RULES.get(target_class)
    price_class = rule.price_class if rule else target_class
    return f"{prefix}.{price_class}.{suffix}"


def retrieval_price(
    prices: Mapping[str, Any],
    storage_class: str,
    speed: str,
) -> Optional[float]:
    """Resolve the retrieval price for the explicit class and speed key.

    IA and Glacier Instant Retrieval intentionally use their new ``instant``
    canonical keys.  Falling back to Glacier Flexible's ``standard`` key
    would hide an incomplete price map and could understate a recommendation.
    The old keys remain seed-resolvable for compatibility, but are not used by
    this engine path.
    """

    return price_for(prices, f"retrieval.{storage_class}.{speed}.gb")


def storage_cost(
    stored_gb_by_class: Mapping[str, Optional[float]],
    prices: Mapping[str, Any],
    *,
    target_class: Optional[str] = None,
) -> Optional[float]:
    """Compute GB-month storage cost in dollars.

    A current mix sums each class's stored GB at its class rate.  When
    ``target_class`` is supplied, all known bytes are priced at that target
    class.  Empty mixes return ``0.0``; an unresolved price for nonzero bytes
    returns ``None`` rather than silently understating savings.
    """

    if target_class is not None:
        total = sum(value for value in stored_gb_by_class.values() if value is not None)
        if not total:
            return 0.0
        return _mul(total, price_for(prices, class_price_key("storage", target_class, "gb_month")))
    terms: list[Optional[float]] = []
    for storage_class, amount in stored_gb_by_class.items():
        if amount is None:
            return None
        if amount == 0:
            continue
        terms.append(_mul(amount, price_for(prices, class_price_key("storage", storage_class, "gb_month"))))
    return _add(terms) if terms else 0.0


def request_cost(
    signals: Mapping[str, Any],
    prices: Mapping[str, Any],
    target_class: str,
) -> Optional[float]:
    """Price normalized monthly requests by family, dividing every count by 1,000.

    LIST and configuration requests always use Standard's Tier-1 rate because
    AWS bills those operations independently of the object's destination class;
    missing rates propagate as ``None`` when their family has nonzero volume.
    """

    families = (
        ("monthly_list_requests", "STANDARD", "list"),
        ("monthly_data_write_requests", target_class, "data_write"),
        ("monthly_data_read_requests", target_class, "data_read"),
        ("monthly_config_requests", "STANDARD", "config"),
        ("monthly_tier1_requests", target_class, "tier_unsplit"),
        ("monthly_tier2_requests", target_class, "tier_unsplit"),
    )
    terms: list[Optional[float]] = []
    for signal_name, request_class, family in families:
        signal_present = signal_name in signals
        amount = signals.get(signal_name)
        if amount is None:
            # Phase 1 exposes the unsplit families inside window_totals rather
            # than as top-level fields; absent means a known zero only when
            # the covered window is known.
            if family == "tier_unsplit" and not signal_present:
                tier = "tier1" if "tier1" in signal_name else "tier2"
                amount = (signals.get("window_totals") or {}).get(tier, {}).get("tier_unsplit")
            if amount is None:
                # A family omitted by a focused unit-test input is an empty
                # family.  An explicit ``None`` remains unknown and is not
                # silently changed to zero.
                amount = 0.0 if not signal_present else None
        if amount == 0:
            continue
        if amount is None:
            return None
        price_class = STORAGE_CLASS_RULES.get(request_class, _UNKNOWN_CLASS_RULE).price_class
        key = f"request.{price_class}.{family}.per_1000"
        terms.append(_mul(float(amount) / 1000.0, price_for(prices, key)))
    return _add(terms) if terms else 0.0


def _retrieval_items(signals: Mapping[str, Any], name: str) -> list[tuple[str, str, Optional[float]]]:
    """Normalize retrieval signals and preserve explicit unknown values.

    When a retrieval signal is present with value ``None``, return the
    ``("UNKNOWN", "standard", None)`` sentinel.  That sentinel distinguishes
    an explicitly unknown series from an absent, known-empty series so callers
    propagate ``None`` rather than silently treating the cost as zero.
    """

    value = signals.get(name)
    if value is None and name in signals:
        return [("UNKNOWN", "standard", None)]
    if not value:
        return []
    items = []
    for key, amount in value.items():
        if amount is None:
            items.append((str(key), "standard", None))
            continue
        if isinstance(amount, Mapping):
            for speed, speed_amount in amount.items():
                items.append((str(key), str(speed), speed_amount))
        else:
            if "." in str(key):
                storage_class, speed = str(key).split(".", 1)
            else:
                storage_class, speed = str(key), "standard"
            items.append((storage_class, speed, amount))
    return items


def retrieval_cost(
    signals: Mapping[str, Any],
    prices: Mapping[str, Any],
    *,
    target_class: Optional[str] = None,
    stress_full_retrievals_per_year: Optional[float] = None,
    stored_gb: Optional[float] = None,
    objects: Optional[float] = None,
) -> Optional[float]:
    """Price retrieval bytes and restore requests, observed or projected.

    Observed costs use each source class/speed.  A target class reprices the
    same observed volumes at the target's fixed speed mapping; stress adds one
    full-bucket retrieval per configured year and one restore request per
    object for Glacier Flexible/Deep Archive.  No observed volume returns zero
    (or the stress amount), while an unresolved price for nonzero usage returns
    ``None``.
    """

    retrievals = _retrieval_items(signals, "retrieval_gb_per_month_by_class")
    restores = _retrieval_items(signals, "restore_requests_by_class")
    if target_class is None:
        target_for_speed = None
    else:
        target_for_speed = STORAGE_CLASS_RULES[target_class].price_class
    terms: list[Optional[float]] = []
    for source_class, speed, amount in retrievals:
        if amount is None:
            return None
        price_class = target_for_speed or STORAGE_CLASS_RULES.get(source_class, _UNKNOWN_CLASS_RULE).price_class
        assumed_speed = STORAGE_CLASS_RULES.get(source_class, _UNKNOWN_CLASS_RULE).retrieval_speed if target_for_speed is None else STORAGE_CLASS_RULES[target_class].retrieval_speed
        if assumed_speed is None:
            assumed_speed = speed
        if target_for_speed in {"STANDARD", None} and target_for_speed == "STANDARD":
            continue
        terms.append(_mul(float(amount), retrieval_price(prices, price_class, assumed_speed)))
    for source_class, speed, amount in restores:
        if amount is None:
            return None
        price_class = target_for_speed or STORAGE_CLASS_RULES.get(source_class, _UNKNOWN_CLASS_RULE).price_class
        if target_for_speed in {"STANDARD", "STANDARD_IA", "ONEZONE_IA", "GLACIER_IR"}:
            continue
        terms.append(_mul(float(amount) / 1000.0, price_for(prices, f"restore_request.{price_class}.standard.per_1000")))
    observed = _add(terms) if terms else 0.0
    if stress_full_retrievals_per_year is None:
        return observed
    if stored_gb is None:
        return None
    if target_for_speed == "STANDARD":
        stress = 0.0
    else:
        speed = STORAGE_CLASS_RULES[target_class].retrieval_speed
        retrieval_rate = retrieval_price(prices, target_for_speed, speed or "standard")
        if retrieval_rate is None:
            return None
        stress = float(stress_full_retrievals_per_year) * stored_gb * retrieval_rate / 12.0
        if target_for_speed in {"GLACIER", "DEEP_ARCHIVE"} and objects is not None:
            restore_price = price_for(prices, f"restore_request.{target_for_speed}.standard.per_1000")
            if restore_price is None:
                return None
            stress += float(stress_full_retrievals_per_year) * objects / 1000.0 * restore_price / 12.0
    return None if observed is None else observed + stress


def it_monitoring_cost(objects_ge_128kb: Optional[float], prices: Mapping[str, Any]) -> Optional[float]:
    """Compute Intelligent-Tiering monitoring dollars as objects/1,000 × fee.

    Objects under 128 KB are not monitored and have no monitoring fee.  An
    unknown object count returns ``None``; zero objects returns ``0.0``.
    """

    if objects_ge_128kb is None:
        return None
    return _mul(objects_ge_128kb / 1000.0, price_for(prices, "it_monitoring.per_1000_objects"))


def transition_cost(objects: Optional[float], destination_class: str, prices: Mapping[str, Any]) -> Optional[float]:
    """Compute one-time transition dollars as objects/1,000 × destination fee."""

    if objects is None:
        return None
    key_class = STORAGE_CLASS_RULES[destination_class].price_class
    return _mul(objects / 1000.0, price_for(prices, f"transition.{key_class}.per_1000"))


def padding_cost(
    target_class: str,
    stored_bytes: Optional[float],
    objects: Optional[float],
    avg_object_bytes: Optional[float],
    prices: Mapping[str, Any],
    *,
    observed_overhead_bytes: Optional[float] = None,
) -> tuple[Optional[float], Optional[str]]:
    """Compute 128 KB padding and return ``(dollars, method)``.

    Existing SIA/ZIA/GIR bytes use AWS's observed `*SizeOverhead` metric;
    absent that metric, a candidate estimates object padding from average size.
    Unknown inputs return ``(None, None)``.  Classes without a 128 KB floor
    return ``(0.0, "not_applicable")``.
    """

    rule = STORAGE_CLASS_RULES[target_class]
    if rule.min_billable_bytes is None:
        return 0.0, "not_applicable"
    if observed_overhead_bytes is not None:
        return _mul(observed_overhead_bytes / BYTES_PER_GIB, price_for(prices, class_price_key("storage", target_class, "gb_month"))), "observed_overhead"
    if objects is None or avg_object_bytes is None:
        return None, None
    padding_bytes = max(0.0, rule.min_billable_bytes - avg_object_bytes) * objects
    return _mul(padding_bytes / BYTES_PER_GIB, price_for(prices, class_price_key("storage", target_class, "gb_month"))), "avg_size_estimate"


def archive_overhead_cost(
    destination_class: str,
    objects: Optional[float],
    prices: Mapping[str, Any],
) -> Optional[float]:
    """Price archive overhead as 32 KB at archive rate plus 8 KB at Standard.

    Glacier Flexible, Deep Archive, and Intelligent-Tiering archive tiers use
    the AWS 32 KB archive-index plus 8 KB Standard-rate accounting.  Unknown
    counts or prices return ``None``; classes without archive overhead return
    ``0.0``.
    """

    if destination_class not in {"GLACIER", "GLACIER_FLEXIBLE", "DEEP_ARCHIVE"}:
        return 0.0
    if objects is None:
        return None
    archive_class = STORAGE_CLASS_RULES[destination_class].price_class
    archive = _mul(objects * ARCHIVE_OVERHEAD_BYTES / BYTES_PER_GIB, price_for(prices, f"storage.{archive_class}.gb_month"))
    standard = _mul(objects * (8 * 1024) / BYTES_PER_GIB, price_for(prices, "storage.STANDARD.gb_month"))
    return _add([archive, standard])
