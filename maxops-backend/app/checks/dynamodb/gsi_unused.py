"""
DynamoDB Unused GSI Check - identifies GSIs with *no read traffic*.

AWS guidance:
- Evaluate ONLY ConsumedReadCapacityUnits (GSI writes can be > 0 even if unused).
- Use a longer window (e.g., 30 days) and daily periods (86400s).
- Using Sum, ANY datapoint Sum > 0 indicates read traffic occurred in that period.
  If all datapoints are 0, the GSI is unused.

References: Amazon DynamoDB Developer Guide (Cost Optimization - Unused resources).
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional
from datetime import datetime, timedelta

from app.checks.registry import check_registry, CheckMetadata
from app.checks.base import create_check_reason, estimate_dynamodb_provisioned_monthly_cost
from app.utils.math_utils import roundf


def _metric(u: Dict[str, Any], *keys: str, default: Any = 0) -> Any:
    """Fetch a metric from a utilization dict using multiple possible key names."""
    for k in keys:
        if k in u and u[k] is not None:
            return u[k]
    return default


def _extract_period_sums(metric_value: Any) -> List[float]:
    """
    Normalize various possible adapter return shapes into a list of per-period "Sum-like" numbers.

    Supported shapes:
    - scalar number -> [number]
    - list[number] -> list of floats
    - list[dict] where dict has 'Sum' or 'sum' -> extracted
    - list[dict] where dict has 'Value' -> extracted (fallback)
    - dict with 'Datapoints' -> recurse into that list

    If it can't parse, returns [].
    """
    if metric_value is None:
        return []

    # CloudWatch-style wrapper
    if isinstance(metric_value, dict) and "Datapoints" in metric_value:
        return _extract_period_sums(metric_value.get("Datapoints"))

    # Scalar
    if isinstance(metric_value, (int, float)):
        return [float(metric_value)]

    # List/Tuple
    if isinstance(metric_value, (list, tuple)):
        out: List[float] = []
        for item in metric_value:
            if item is None:
                continue
            if isinstance(item, (int, float)):
                out.append(float(item))
                continue
            if isinstance(item, dict):
                if "Sum" in item and item["Sum"] is not None:
                    out.append(float(item["Sum"]))
                    continue
                if "sum" in item and item["sum"] is not None:
                    out.append(float(item["sum"]))
                    continue
                # fallback if adapter uses generic naming
                if "Value" in item and item["Value"] is not None:
                    out.append(float(item["Value"]))
                    continue
                if "value" in item and item["value"] is not None:
                    out.append(float(item["value"]))
                    continue
        return out

    return []


def check_dynamodb_gsi_unused(
    aws_adapter,
    lookback_days: int = 30,
    period_seconds: int = 86400,
    region: Optional[str] = None,
) -> List[Dict[str, Any]]:
    """
    Identify GSIs with no read traffic by evaluating per-period sums of
    ConsumedReadCapacityUnits across the lookback window.

    Args:
        aws_adapter: AWS adapter with credentials
        lookback_days: Lookback window in days (default: 30)
        period_seconds: Period in seconds (default: 86400 = 1 day)
        region: Optional region filter

    Returns:
        List of GSI resources flagged as unused (no read traffic), enriched with metadata.
    """
    # Expected: GSIs are first-class resources in your adapter.
    gsis = aws_adapter.get_resources("dynamodb_gsi", {}, region)

    end_date = datetime.utcnow()
    start_date = end_date - timedelta(days=lookback_days)

    unused: List[Dict[str, Any]] = []

    for gsi in gsis:
        gsi_id = gsi.get("resource_id")
        if not gsi_id:
            continue

        try:
            # If your adapter supports passing period/statistic, wire it here.
            # We keep compatibility with the EC2-style signature by not requiring it.
            u = aws_adapter.get_resource_utilization(gsi_id, "dynamodb_gsi", start_date, end_date)

            # Read traffic is the ONLY thing we use to classify unused.
            consumed_read = _metric(
                u,
                "ConsumedReadCapacityUnits",
                "consumedreadcapacityunits",
                "consumed_read_capacity_units",
                "consumed_rcu",
                default=0,
            )

            period_sums = _extract_period_sums(consumed_read)

            # AWS doc: any Sum > 0 in the period means there was read traffic. :contentReference[oaicite:2]{index=2}
            has_read_traffic = any(v > 0 for v in period_sums)
            is_unused = not has_read_traffic

            if is_unused:
                md = gsi.setdefault("metadata", {})
                table_name = md.get("table_name") or md.get("TableName")
                index_name = md.get("index_name") or md.get("IndexName") or md.get("name")

                total_read_sum = sum(period_sums) if period_sums else 0.0
                nonzero_periods = sum(1 for v in period_sums if v > 0)

                md["lookback_days"] = lookback_days
                md["period_seconds"] = period_seconds
                md["statistic"] = "Sum"

                md["total_gsi_consumed_read_rcu_sum"] = roundf(total_read_sum, 4)
                md["nonzero_periods"] = int(nonzero_periods)
                md["periods_evaluated"] = int(len(period_sums))

                # --- Pricing Calculation ---
                # Get provisioned capacity from GSI metadata
                provisioned_throughput = md.get("ProvisionedThroughput", {})
                read_capacity_units = provisioned_throughput.get("ReadCapacityUnits", 0) if provisioned_throughput else 0
                write_capacity_units = provisioned_throughput.get("WriteCapacityUnits", 0) if provisioned_throughput else 0
                
                # Determine billing mode
                is_on_demand = (read_capacity_units == 0 and write_capacity_units == 0)
                
                # Calculate monthly cost for this GSI
                if not is_on_demand and (read_capacity_units > 0 or write_capacity_units > 0):
                    # PROVISIONED billing mode
                    monthly_cost = estimate_dynamodb_provisioned_monthly_cost(
                        read_capacity_units,
                        write_capacity_units
                    )
                    md["billing_mode"] = "PROVISIONED"
                    md["current_monthly_cost"] = roundf(monthly_cost, 2)
                    md["potential_savings_yearly"] = roundf(monthly_cost * 12, 2)  # Full cost saved if deleted
                    md["read_capacity_units"] = read_capacity_units
                    md["write_capacity_units"] = write_capacity_units
                else:
                    # ON-DEMAND (PAY_PER_REQUEST) billing mode
                    # For unused GSIs on on-demand tables:
                    # - No read/write costs (since unused)
                    # - Still consumes storage and adds complexity
                    # - Should still be removed for optimization
                    md["billing_mode"] = "PAY_PER_REQUEST"
                    md["current_monthly_cost"] = 0.0
                    md["potential_savings_yearly"] = 0.0
                    md["read_capacity_units"] = 0
                    md["write_capacity_units"] = 0
                    md["note"] = "On-Demand billing: No direct cost savings, but removal improves table efficiency and reduces storage overhead"

                md["recommended_action"] = "review"
                md["recommended_actions"] = [
                    "confirm_no_query_traffic",
                    "remove_index_from_app",
                    "delete_gsi_if_unused",
                ]

                md["check_reason"] = create_check_reason(
                    "unused",
                    {
                        "resource": "gsi",
                        "table_name": table_name,
                        "index_name": index_name,
                        "lookback_days": lookback_days,
                        "period_seconds": period_seconds,
                        "statistic": "Sum",
                        "has_read_traffic": has_read_traffic,
                        "total_read_sum": roundf(total_read_sum, 4),
                        "nonzero_periods": int(nonzero_periods),
                        "current_monthly_cost": md.get("current_monthly_cost", 0.0),
                        "potential_savings_monthly": md.get("potential_savings_monthly", 0.0),
                    },
                )

                unused.append(gsi)

        except Exception as e:
            print(f"Error checking DynamoDB GSI {gsi_id}: {e}")
            continue

    return unused


check_registry.register(
    CheckMetadata(
        check_id="dynamodb_gsi_unused",
        name="DynamoDB Unused GSI",
        description="Identifies DynamoDB GSIs with no read traffic (ConsumedReadCapacityUnits Sum == 0) over a lookback window",
        resource_type="dynamodb",
        check_function=check_dynamodb_gsi_unused,
        default_action="review",
        parameters={
            "lookback_days": 30,
            "period_seconds": 86400,
            "region": None,
        },
    )
)
