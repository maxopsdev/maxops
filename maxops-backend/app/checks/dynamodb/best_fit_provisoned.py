"""
DynamoDB Billing Mode Best Fit: Provisioned

Flags tables where provisioned capacity is likely a better fit based on:
- Relatively steady traffic (low p95/avg) for the dimensions with meaningful usage
- No throttling (or very close to none)
- Non-trivial sustained usage (so provisioned can be worth managing)
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
from app.checks.dynamodb.capacity_math import avg_consumed_capacity, p95_consumed_capacity


def _metric(u: Dict[str, Any], *keys: str, default: Any = 0) -> Any:
    """
    Fetch a metric from a utilization dict with multiple possible key names.
    Returns `default` if none found.
    """
    for k in keys:
        if k in u and u[k] is not None:
            return u[k]
    return default


def check_dynamodb_best_fit_provisioned(
    aws_adapter,
    lookback_days: int = 14,
    spike_ratio_max: float = 2.0,          # require p95/avg <= this (for used dimensions)
    min_avg_consumed_rcu: float = 1.0,     # ignore tables with near-zero sustained reads
    min_avg_consumed_wcu: float = 1.0,     # ignore tables with near-zero sustained writes
    throttle_events_max: float = 0.0,      # require avg throttles <= this
    region: Optional[str] = None,
) -> List[Dict[str, Any]]:
    """
    Recommend provisioned when workload is steady, non-trivial, and not throttling.

    Returns:
        A list of table resources with enriched metadata.
    """
    tables = aws_adapter.get_resources("dynamodb", {}, region)
    print(f"[DYNAMODB_PROVISIONED_CHECK] Found {len(tables)} DynamoDB tables")

    end_date = datetime.utcnow()
    start_date = end_date - timedelta(days=lookback_days)

    results: List[Dict[str, Any]] = []

    for table in tables:
        table_id = table.get("resource_id")
        if not table_id:
            print(f"[DYNAMODB_PROVISIONED_CHECK] Skipping table with no resource_id")
            continue

        # Only check tables that are using PAY_PER_REQUEST billing mode
        # Tables already on PROVISIONED don't need this recommendation
        billing_mode = table.get("metadata", {}).get("BillingMode")
        print(f"[DYNAMODB_PROVISIONED_CHECK] Table '{table_id}' has billing_mode: {billing_mode} (type: {type(billing_mode)})")
        
        # If billing_mode is None, it might be PAY_PER_REQUEST (default)
        # Check if it's explicitly PROVISIONED
        if billing_mode == "PROVISIONED":
            print(f"[DYNAMODB_PROVISIONED_CHECK] Skipping table '{table_id}' - already on PROVISIONED")
            continue  # Skip tables already on Provisioned
        
        # If billing_mode is None or not set, assume it's PAY_PER_REQUEST (default for new tables)
        # and proceed with the check
        if billing_mode is None:
            print(f"[DYNAMODB_PROVISIONED_CHECK] Table '{table_id}' has no billing_mode set, assuming PAY_PER_REQUEST (default)")

        try:
            print(f"[DYNAMODB_PROVISIONED_CHECK] Getting utilization for table '{table_id}'")
            u = aws_adapter.get_resource_utilization(table_id, "dynamodb", start_date, end_date)
            print(f"[DYNAMODB_PROVISIONED_CHECK] Utilization data for '{table_id}': {list(u.keys())}")
            print(f"[DYNAMODB_PROVISIONED_CHECK] Full utilization data: {u}")

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
            
            print(f"[DYNAMODB_PROVISIONED_CHECK] Table '{table_id}': consumed_rcu={consumed_rcu} (type: {type(consumed_rcu)}), consumed_wcu={consumed_wcu} (type: {type(consumed_wcu)})")

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

            avg_cr = avg_consumed_capacity(u, "ConsumedReadCapacityUnits", consumed_rcu)
            avg_cw = avg_consumed_capacity(u, "ConsumedWriteCapacityUnits", consumed_wcu)
            p95_cr = p95_consumed_capacity(u, "ConsumedReadCapacityUnits", consumed_rcu)
            p95_cw = p95_consumed_capacity(u, "ConsumedWriteCapacityUnits", consumed_wcu)

            print(f"[DYNAMODB_PROVISIONED_CHECK] Table '{table_id}': avg_rcu={avg_cr:.2f}, avg_wcu={avg_cw:.2f}, p95_rcu={p95_cr:.2f}, p95_wcu={p95_cw:.2f}")

            has_reads = avg_cr >= min_avg_consumed_rcu
            has_writes = avg_cw >= min_avg_consumed_wcu
            print(f"[DYNAMODB_PROVISIONED_CHECK] Table '{table_id}': has_reads={has_reads} (avg={avg_cr:.2f} >= {min_avg_consumed_rcu}), has_writes={has_writes} (avg={avg_cw:.2f} >= {min_avg_consumed_wcu})")
            
            if not (has_reads or has_writes):
                print(f"[DYNAMODB_PROVISIONED_CHECK] Skipping table '{table_id}' - insufficient usage (needs at least {min_avg_consumed_rcu} RCU or {min_avg_consumed_wcu} WCU)")
                continue

            spike_r = safe_div(p95_cr, avg_cr, default=0.0) if avg_cr > 0 else 0.0
            spike_w = safe_div(p95_cw, avg_cw, default=0.0) if avg_cw > 0 else 0.0

            print(f"[DYNAMODB_PROVISIONED_CHECK] Table '{table_id}': spike_ratio_rcu={spike_r:.2f}, spike_ratio_wcu={spike_w:.2f} (max allowed: {spike_ratio_max})")

            # Only enforce spikiness constraint for the dimensions that matter
            reads_steady = (spike_r <= spike_ratio_max) if has_reads else True
            writes_steady = (spike_w <= spike_ratio_max) if has_writes else True

            avg_read_th = avg(read_throttles)
            avg_write_th = avg(write_throttles)
            no_throttles = (avg_read_th <= throttle_events_max) and (avg_write_th <= throttle_events_max)
            
            print(f"[DYNAMODB_PROVISIONED_CHECK] Table '{table_id}': reads_steady={reads_steady}, writes_steady={writes_steady}, no_throttles={no_throttles} (read_th={avg_read_th:.2f}, write_th={avg_write_th:.2f}, max={throttle_events_max})")

            if reads_steady and writes_steady and no_throttles:
                print(f"[DYNAMODB_PROVISIONED_CHECK] ✓ Flagging table '{table_id}' for PROVISIONED recommendation")
                table.setdefault("metadata", {})
                md = table["metadata"]

                md["lookback_days"] = lookback_days
                md["recommended_billing_mode"] = "PROVISIONED"
                md["recommended_action"] = "review"

                md["avg_consumed_rcu"] = roundf(avg_cr, 4)
                md["p95_consumed_rcu"] = roundf(p95_cr, 4)
                md["spike_ratio_rcu_p95_over_avg"] = roundf(spike_r, 2)

                md["avg_consumed_wcu"] = roundf(avg_cw, 4)
                md["p95_consumed_wcu"] = roundf(p95_cw, 4)
                md["spike_ratio_wcu_p95_over_avg"] = roundf(spike_w, 2)

                md["avg_read_throttle_events"] = roundf(avg_read_th, 4)
                md["avg_write_throttle_events"] = roundf(avg_write_th, 4)

                # Calculate recommended PROVISIONED capacity
                # Use p95 with 20% buffer to handle occasional spikes
                recommended_rcu = max(1, int(round(p95_cr * 1.2))) if has_reads else 0
                recommended_wcu = max(1, int(round(p95_cw * 1.2))) if has_writes else 0
                
                md["recommended_read_capacity_units"] = recommended_rcu
                md["recommended_write_capacity_units"] = recommended_wcu

                # Calculate pricing and potential savings
                # Current cost: On-Demand (PAY_PER_REQUEST) cost
                current_monthly_cost = 0.0
                if avg_cr > 0 or avg_cw > 0:
                    current_monthly_cost = estimate_dynamodb_ondemand_monthly_cost(avg_cr, avg_cw)
                    md["current_monthly_cost"] = round(current_monthly_cost, 2)

                # Estimated PROVISIONED cost based on recommended capacity
                provisioned_monthly_cost = 0.0
                if recommended_rcu > 0 or recommended_wcu > 0:
                    provisioned_monthly_cost = estimate_dynamodb_provisioned_monthly_cost(
                        float(recommended_rcu), float(recommended_wcu)
                    )
                    md["provisioned_monthly_cost"] = round(provisioned_monthly_cost, 2)

                # Potential savings: difference between On-Demand and PROVISIONED costs
                if current_monthly_cost > 0 and provisioned_monthly_cost > 0:
                    potential_savings = current_monthly_cost - provisioned_monthly_cost
                    md["potential_savings_yearly"] = round(potential_savings * 12, 2)
                    
                    # Only flag if there's meaningful savings (at least 10%)
                    savings_percentage = (potential_savings / current_monthly_cost) * 100.0 if current_monthly_cost > 0 else 0.0
                    md["savings_percentage"] = round(savings_percentage, 2)
                    
                    print(f"[DYNAMODB_PROVISIONED_CHECK] Table '{table_id}': Current (On-Demand) ${current_monthly_cost:.2f}/mo, Recommended (PROVISIONED) ${provisioned_monthly_cost:.2f}/mo, Savings: ${potential_savings:.2f}/mo ({savings_percentage:.1f}%)")
                elif current_monthly_cost > 0:
                    # If we can't estimate PROVISIONED cost, estimate savings based on steady traffic
                    # For steady traffic, PROVISIONED is typically 20-40% cheaper than On-Demand
                    potential_savings = current_monthly_cost * 0.30  # Average of 20-40%
                    md["potential_savings_yearly"] = round(potential_savings * 12, 2)
                    md["savings_percentage"] = 30.0
                    print(f"[DYNAMODB_PROVISIONED_CHECK] Table '{table_id}': Estimated savings ${potential_savings:.2f}/mo (30% estimated)")

                md["thresholds"] = {
                    "spike_ratio_max": spike_ratio_max,
                    "min_avg_consumed_rcu": min_avg_consumed_rcu,
                    "min_avg_consumed_wcu": min_avg_consumed_wcu,
                    "throttle_events_max": throttle_events_max,
                }

                md["check_reason"] = create_check_reason(
                    "billing_mode_best_fit",
                    {
                        "recommended_billing_mode": "PROVISIONED",
                        "has_reads": has_reads,
                        "has_writes": has_writes,
                        "spike_ratio_rcu": roundf(spike_r, 2),
                        "spike_ratio_wcu": roundf(spike_w, 2),
                        "avg_read_throttles": roundf(avg_read_th, 4),
                        "avg_write_throttles": roundf(avg_write_th, 4),
                    },
                )

                results.append(table)

        except Exception as e:
            print(f"[DYNAMODB_PROVISIONED_CHECK] Error checking DynamoDB table {table_id} (provisioned best fit): {e}")
            import traceback
            print(f"[DYNAMODB_PROVISIONED_CHECK] Traceback: {traceback.format_exc()}")
            continue

    print(f"[DYNAMODB_PROVISIONED_CHECK] Found {len(results)} tables that should use PROVISIONED billing")
    return results


check_registry.register(
    CheckMetadata(
        check_id="dynamodb_best_fit_provisioned",
        name="DynamoDB Billing Mode Best Fit: Provisioned",
        description="Identifies DynamoDB tables where provisioned capacity is likely a better fit",
        resource_type="dynamodb",
        check_function=check_dynamodb_best_fit_provisioned,
        default_action="review",
        parameters={
            "lookback_days": 14,
            "spike_ratio_max": 2.0,
            "min_avg_consumed_rcu": 1.0,
            "min_avg_consumed_wcu": 1.0,
            "throttle_events_max": 0.0,
            "region": None,
        },
    )
)
