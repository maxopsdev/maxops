"""
CloudWatch Alarms Check - Duplicate Alarms

Flags alarms that appear to be duplicates (same underlying metric + dimensions + core threshold config).

How duplicates are detected:
- Build a stable fingerprint from common alarm fields:
  namespace, metric_name, statistic/extended_statistic, period, evaluation_periods,
  threshold, comparison_operator, treat_missing_data, dimensions (sorted)

Notes:
- Composite alarms and metric-math alarms can have different shapes. This check focuses on
  "single-metric" style alarms; others are skipped unless metadata still provides enough fields.
- If your adapter doesn't include these fields in `metadata`, consider enhancing it to do so.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional, Tuple, DefaultDict
from collections import defaultdict

from app.checks.registry import check_registry, CheckMetadata
from app.checks.base import create_check_reason


def _get_alarm_name(a: Dict[str, Any]) -> Optional[str]:
    return a.get("resource_id") or a.get("alarm_name") or a.get("name")


def _md(a: Dict[str, Any]) -> Dict[str, Any]:
    return a.get("metadata") or a


def _first_present(source: Dict[str, Any], *keys: str) -> Any:
    for key in keys:
        if key in source and source[key] is not None:
            return source[key]
    return None


def _norm_dimensions(dims: Any) -> Tuple[Tuple[str, str], ...]:
    """
    Normalize dimensions from possible shapes into a stable sorted tuple of (name,value).
    Accepts:
      - list[{"Name":..,"Value":..}] or list[{"name":..,"value":..}]
      - dict{name:value}
    """
    items: List[Tuple[str, str]] = []
    if isinstance(dims, dict):
        for k, v in dims.items():
            if k is None or v is None:
                continue
            items.append((str(k), str(v)))
    elif isinstance(dims, list):
        for it in dims:
            if not isinstance(it, dict):
                continue
            n = it.get("Name") or it.get("name")
            v = it.get("Value") or it.get("value")
            if n is None or v is None:
                continue
            items.append((str(n), str(v)))
    items.sort()
    return tuple(items)


def alarm_fingerprint(a: Dict[str, Any]) -> Optional[Tuple[Any, ...]]:
    md = _md(a)

    # Common fields (try a few spellings)
    namespace = _first_present(md, "Namespace", "namespace")
    metric_name = _first_present(md, "MetricName", "metric_name", "metricName")
    # Statistic can be "Statistic" or "ExtendedStatistic" depending on alarm type
    statistic = _first_present(md, "Statistic", "statistic")
    extended_stat = _first_present(md, "ExtendedStatistic", "extendedStatistic")
    period = _first_present(md, "Period", "period")
    eval_periods = _first_present(
        md, "EvaluationPeriods", "evaluation_periods", "evaluationPeriods"
    )
    threshold = _first_present(md, "Threshold", "threshold")
    comparison = _first_present(
        md, "ComparisonOperator", "comparison_operator", "comparisonOperator"
    )
    treat_missing = _first_present(
        md, "TreatMissingData", "treat_missing_data", "treatMissingData"
    )

    dims = _first_present(
        md, "Dimensions", "dimensions", "Dimension", "dimension"
    )
    dims_norm = _norm_dimensions(dims)

    # If we can't get at least namespace+metric, skip (not enough info for safe dedupe)
    if not namespace or not metric_name:
        return None

    return (
        str(namespace),
        str(metric_name),
        str(statistic) if statistic is not None else None,
        str(extended_stat) if extended_stat is not None else None,
        int(period) if period is not None else None,
        int(eval_periods) if eval_periods is not None else None,
        float(threshold) if threshold is not None else None,
        str(comparison) if comparison is not None else None,
        str(treat_missing) if treat_missing is not None else None,
        dims_norm,
    )


def check_cloudwatch_duplicate_alarms(
    aws_adapter,
    region: Optional[str] = None,
) -> List[Dict[str, Any]]:
    alarms = aws_adapter.get_resources("cloudwatch_alarm", {}, region)

    groups: DefaultDict[Tuple[Any, ...], List[Dict[str, Any]]] = defaultdict(list)
    skipped = 0

    for a in alarms:
        fp = alarm_fingerprint(a)
        if fp is None:
            skipped += 1
            continue
        groups[fp].append(a)

    flagged: List[Dict[str, Any]] = []
    for fp, items in groups.items():
        if len(items) < 2:
            continue

        # Flag each alarm in the duplicate set
        names = [n for n in (_get_alarm_name(x) for x in items) if n]
        for a in items:
            md = a.setdefault("metadata", {})
            md["recommended_action"] = "cloudwatch_consolidate_duplicate_alarms"
            md["recommended_actions"] = [
                "cloudwatch_consolidate_duplicate_alarms",
            ]
            md["duplicate_alarm_count"] = len(items)
            md["duplicate_alarm_names"] = names
            md["check_reason"] = create_check_reason("duplicate", {
                "resource": "cloudwatch_alarm",
                "duplicate_alarm_count": len(items),
                "duplicate_alarm_names": names,
            })
            flagged.append(a)

    return flagged


check_registry.register(CheckMetadata(
    check_id="cloudwatch_duplicate_alarms",
    name="CloudWatch Duplicate Alarms",
    description="Identifies CloudWatch alarms that appear duplicated (same metric/dimensions/threshold config)",
    resource_type="cloudwatch_alarm",
    check_function=check_cloudwatch_duplicate_alarms,
    default_action="cloudwatch_consolidate_duplicate_alarms",
    parameters={
        "region": None,
    },
))
