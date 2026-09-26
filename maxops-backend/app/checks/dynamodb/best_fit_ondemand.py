"""
DynamoDB Billing Mode Best Fit: On-Demand

Flags tables where on-demand (PAY_PER_REQUEST) is likely a better fit based on:
- Spiky traffic (p95/avg over a threshold) on reads or writes
- Any throttling
- Low utilization vs provisioned capacity (when provisioned capacity signals are available)

Assumptions:
- aws_adapter.get_resource_utilization(table_id, "dynamodb", start, end) returns a dict of metrics.
- Each metric may be either a scalar or a time-series (list/tuple).
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional
from datetime import datetime, timedelta

from app.checks.registry import check_registry, CheckMetadata
from app.checks.base import (
    create_check_reason,
    estimate_dynamodb_provisioned_monthly_cost,
    estimate_dynamodb_ondemand_monthly_cost
)
from app.utils.math_utils import avg, safe_div, roundf
from app.checks.dynamodb.capacity_math import (
    avg_consumed_capacity,
    avg_provisioned_capacity,
    p95_consumed_capacity,
    utilization_pct,
)


def _metric(u: Dict[str, Any], *keys: str, default: Any = 0) -> Any:
    """
    Fetch a metric from a utilization dict with multiple possible key names.
    Returns `default` if none found.
    """
    for k in keys:
        if k in u and u[k] is not None:
            return u[k]
    return default


def check_dynamodb_best_fit_on_demand(
    aws_adapter,
    lookback_days: int = 14,
    spike_ratio_threshold: float = 3.0,        # (p95/avg) > threshold => spiky
    low_utilization_threshold_pct: float = 20.0,  # (sum(consumed)/period)/avg(provisioned) * 100
    throttle_events_threshold: float = 1.0,     # avg throttles >= threshold => throttling
    region: Optional[str] = None,
) -> List[Dict[str, Any]]:
    """
    Recommend on-demand (PAY_PER_REQUEST) when workload is bursty, throttling,
    or provisioned capacity appears significantly underutilized.

    Returns:
        A list of table resources with enriched metadata.
    """
    tables = aws_adapter.get_resources("dynamodb", {}, region)
    print(f"[DYNAMODB_ONDEMAND_CHECK] Found {len(tables)} DynamoDB tables")

    end_date = datetime.utcnow()
    start_date = end_date - timedelta(days=lookback_days)

    results: List[Dict[str, Any]] = []

    for table in tables:
        table_id = table.get("resource_id")
        if not table_id:
            print(f"[DYNAMODB_ONDEMAND_CHECK] Skipping table with no resource_id")
            continue

        # Only check tables that are using PROVISIONED billing mode
        # Tables already on PAY_PER_REQUEST don't need this recommendation
        billing_mode = table.get("metadata", {}).get("BillingMode")
        print(f"[DYNAMODB_ONDEMAND_CHECK] Table '{table_id}' has billing_mode: {billing_mode}")
        if billing_mode == "PAY_PER_REQUEST":
            print(f"[DYNAMODB_ONDEMAND_CHECK] Skipping table '{table_id}' - already on PAY_PER_REQUEST")
            continue  # Skip tables already on On-Demand

        try:
            print(f"[DYNAMODB_ONDEMAND_CHECK] Getting utilization for table '{table_id}'")
            u = aws_adapter.get_resource_utilization(table_id, "dynamodb", start_date, end_date)
            print(f"[DYNAMODB_ONDEMAND_CHECK] Utilization data for '{table_id}': {list(u.keys())}")

            consumed_rcu = _metric(
                u,
                "ConsumedReadCapacityUnits",
                "consumedreadcapacityunits",
                "consumed_rcu",
                default=0,
            )
            consumed_wcu = _metric(
                u,
                "ConsumedWriteCapacityUnits",
                "consumedwritecapacityunits",
                "consumed_wcu",
                default=0,
            )

            provisioned_rcu = _metric(
                u,
                "ProvisionedReadCapacityUnits",
                "provisionedreadcapacityunits",
                "provisioned_rcu",
                default=table.get("metadata", {}).get("read_capacity_units", 0),
            )
            provisioned_wcu = _metric(
                u,
                "ProvisionedWriteCapacityUnits",
                "provisionedwritecapacityunits",
                "provisioned_wcu",
                default=table.get("metadata", {}).get("write_capacity_units", 0),
            )

            read_throttles = _metric(
                u,
                "ReadThrottleEvents",
                "readthrottleevents",
                "read_throttles",
                default=0,
            )
            write_throttles = _metric(
                u,
                "WriteThrottleEvents",
                "writethrottleevents",
                "write_throttles",
                default=0,
            )

            # Traffic stats
            avg_cr = avg_consumed_capacity(u, "ConsumedReadCapacityUnits", consumed_rcu)
            avg_cw = avg_consumed_capacity(u, "ConsumedWriteCapacityUnits", consumed_wcu)
            p95_cr = p95_consumed_capacity(u, "ConsumedReadCapacityUnits", consumed_rcu)
            p95_cw = p95_consumed_capacity(u, "ConsumedWriteCapacityUnits", consumed_wcu)

            spike_r = safe_div(p95_cr, avg_cr, default=0.0)
            spike_w = safe_div(p95_cw, avg_cw, default=0.0)

            spiky = (spike_r > spike_ratio_threshold) or (spike_w > spike_ratio_threshold)

            # Throttling
            avg_read_th = avg(read_throttles)
            avg_write_th = avg(write_throttles)
            throttling = (avg_read_th >= throttle_events_threshold) or (avg_write_th >= throttle_events_threshold)

            # Low utilization (only if we can see provisioned capacity signals)
            avg_pr = avg_provisioned_capacity(provisioned_rcu)
            avg_pw = avg_provisioned_capacity(provisioned_wcu)

            util_r_pct: Optional[float] = None
            util_w_pct: Optional[float] = None

            if avg_pr > 0:
                util_r_pct = utilization_pct(avg_cr, avg_pr)
            if avg_pw > 0:
                util_w_pct = utilization_pct(avg_cw, avg_pw)

            low_util = False
            if (util_r_pct is not None) or (util_w_pct is not None):
                # If only one dimension exists, evaluate that. If both exist, require both low.
                r_low = (util_r_pct is not None) and (util_r_pct < low_utilization_threshold_pct)
                w_low = (util_w_pct is not None) and (util_w_pct < low_utilization_threshold_pct)
                low_util = (r_low and w_low) if (util_r_pct is not None and util_w_pct is not None) else (r_low or w_low)

            print(f"[DYNAMODB_ONDEMAND_CHECK] Table '{table_id}': spiky={spiky} (RCU spike={spike_r:.2f}, WCU spike={spike_w:.2f}), throttling={throttling} (read={avg_read_th:.2f}, write={avg_write_th:.2f}), low_util={low_util} (RCU util={util_r_pct}, WCU util={util_w_pct})")
            
            if spiky or throttling or low_util:
                print(f"[DYNAMODB_ONDEMAND_CHECK] ✓ Flagging table '{table_id}' for On-Demand recommendation")
                table.setdefault("metadata", {})
                md = table["metadata"]

                md["lookback_days"] = lookback_days
                md["recommended_billing_mode"] = "PAY_PER_REQUEST"
                md["recommended_action"] = "review"

                md["avg_consumed_rcu"] = roundf(avg_cr, 4)
                md["p95_consumed_rcu"] = roundf(p95_cr, 4)
                md["spike_ratio_rcu_p95_over_avg"] = roundf(spike_r, 2)

                md["avg_consumed_wcu"] = roundf(avg_cw, 4)
                md["p95_consumed_wcu"] = roundf(p95_cw, 4)
                md["spike_ratio_wcu_p95_over_avg"] = roundf(spike_w, 2)

                if avg_pr > 0:
                    md["avg_provisioned_rcu"] = roundf(avg_pr, 4)
                    md["avg_rcu_utilization_pct"] = roundf(util_r_pct, 2)
                if avg_pw > 0:
                    md["avg_provisioned_wcu"] = roundf(avg_pw, 4)
                    md["avg_wcu_utilization_pct"] = roundf(util_w_pct, 2)

                md["avg_read_throttle_events"] = roundf(avg_read_th, 4)
                md["avg_write_throttle_events"] = roundf(avg_write_th, 4)

                # Calculate pricing and potential savings
                # Current cost: PROVISIONED capacity cost
                current_monthly_cost = 0.0
                if avg_pr > 0 or avg_pw > 0:
                    # Use provisioned capacity from metadata if available, otherwise from utilization
                    provisioned_rcu = avg_pr if avg_pr > 0 else table.get("metadata", {}).get("read_capacity_units", 0)
                    provisioned_wcu = avg_pw if avg_pw > 0 else table.get("metadata", {}).get("write_capacity_units", 0)
                    current_monthly_cost = estimate_dynamodb_provisioned_monthly_cost(provisioned_rcu, provisioned_wcu)
                    md["current_monthly_cost"] = round(current_monthly_cost, 2)
                    md["provisioned_rcu"] = roundf(provisioned_rcu, 2)
                    md["provisioned_wcu"] = roundf(provisioned_wcu, 2)
                
                # Estimated On-Demand cost based on average consumption
                ondemand_monthly_cost = 0.0
                if avg_cr > 0 or avg_cw > 0:
                    ondemand_monthly_cost = estimate_dynamodb_ondemand_monthly_cost(avg_cr, avg_cw)
                    md["ondemand_monthly_cost"] = round(ondemand_monthly_cost, 2)
                
                # Potential savings: difference between PROVISIONED and On-Demand costs
                if current_monthly_cost > 0 and ondemand_monthly_cost > 0:
                    potential_savings = current_monthly_cost - ondemand_monthly_cost
                    md["potential_savings_yearly"] = round(potential_savings * 12, 2)
                elif current_monthly_cost > 0:
                    # If we can't estimate On-Demand cost, estimate savings based on utilization
                    # For low utilization, On-Demand would be cheaper
                    if low_util:
                        # Low utilization: estimate 20-50% savings
                        potential_savings = current_monthly_cost * 0.35  # Average of 20-50%
                        md["potential_savings_yearly"] = round(potential_savings * 12, 2)
                    elif throttling:
                        # Throttling: On-Demand would eliminate throttling costs and provide better performance
                        # Estimate 10-30% savings (avoiding throttling + better cost efficiency)
                        potential_savings = current_monthly_cost * 0.20
                        md["potential_savings_yearly"] = round(potential_savings * 12, 2)
                    elif spiky:
                        # Spiky traffic: On-Demand handles spikes better, estimate 15-25% savings
                        potential_savings = current_monthly_cost * 0.20
                        md["potential_savings_yearly"] = round(potential_savings * 12, 2)

                md["thresholds"] = {
                    "spike_ratio_threshold": spike_ratio_threshold,
                    "low_utilization_threshold_pct": low_utilization_threshold_pct,
                    "throttle_events_threshold": throttle_events_threshold,
                }

                md["check_reason"] = create_check_reason(
                    "billing_mode_best_fit",
                    {
                        "recommended_billing_mode": "PAY_PER_REQUEST",
                        "spiky": spiky,
                        "throttling": throttling,
                        "low_utilization": low_util,
                        "spike_ratio_rcu": roundf(spike_r, 2),
                        "spike_ratio_wcu": roundf(spike_w, 2),
                        "avg_read_throttles": roundf(avg_read_th, 4),
                        "avg_write_throttles": roundf(avg_write_th, 4),
                        "avg_rcu_utilization_pct": roundf(util_r_pct, 2),
                        "avg_wcu_utilization_pct": roundf(util_w_pct, 2),
                    },
                )

                results.append(table)

        except Exception as e:
            print(f"[DYNAMODB_ONDEMAND_CHECK] Error checking DynamoDB table {table_id} (on-demand best fit): {e}")
            import traceback
            print(f"[DYNAMODB_ONDEMAND_CHECK] Traceback: {traceback.format_exc()}")
            continue

    print(f"[DYNAMODB_ONDEMAND_CHECK] Found {len(results)} tables that should use On-Demand billing")
    return results


check_registry.register(
    CheckMetadata(
        check_id="dynamodb_best_fit_on_demand",
        name="DynamoDB Billing Mode Best Fit: On-Demand",
        description="Identifies DynamoDB tables where on-demand (PAY_PER_REQUEST) is likely a better fit",
        resource_type="dynamodb",
        check_function=check_dynamodb_best_fit_on_demand,
        default_action="review",
        parameters={
            "lookback_days": 14,
            "spike_ratio_threshold": 3.0,
            "low_utilization_threshold_pct": 20.0,
            "throttle_events_threshold": 1.0,
            "region": None,
        },
    )
)
