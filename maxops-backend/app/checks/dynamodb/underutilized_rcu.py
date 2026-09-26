"""
DynamoDB Underutilized Tables (RCU) Check - identifies tables with low *read* capacity utilization.
"""

from typing import List, Dict, Any, Optional
from datetime import datetime, timedelta

from app.checks.registry import check_registry, CheckMetadata
from app.checks.base import create_check_reason, estimate_dynamodb_provisioned_monthly_cost
from app.checks.dynamodb.capacity_math import avg_consumed_capacity, avg_provisioned_capacity, utilization_pct


def check_dynamodb_underutilized_rcu(
    aws_adapter,
    lookback_days: int = 14,
    rcu_utilization_threshold: float = 10.0,  # percent
    min_provisioned_rcu: int = 1,
    region: Optional[str] = None
) -> List[Dict[str, Any]]:
    """
    Flags PROVISIONED DynamoDB tables whose average Consumed RCU is low vs Provisioned RCU.

    Utilization % = ((avg sum(Consumed RCU) / period) / avg_provisioned_rcu) * 100

    Args:
        aws_adapter: AWS adapter with credentials
        lookback_days: Number of days to check (default: 14)
        rcu_utilization_threshold: Utilization threshold % (default: 10.0)
        min_provisioned_rcu: Only evaluate tables with provisioned RCU >= this (default: 1)
        region: Optional specific region to check

    Returns:
        List of underutilized DynamoDB tables (RCU) with metadata
    """
    tables = aws_adapter.get_resources("dynamodb", {}, region)

    end_date = datetime.utcnow()
    start_date = end_date - timedelta(days=lookback_days)

    underutilized = []

    for table in tables:
        table_id = table.get("resource_id")
        if not table_id:
            continue

        try:
            utilization = aws_adapter.get_resource_utilization(
                table_id,
                "dynamodb",
                start_date,
                end_date
            )

            consumed_rcu = (
                utilization.get("consumedreadcapacityunits")
                or utilization.get("ConsumedReadCapacityUnits")
                or utilization.get("consumed_rcu")
                or 0
            )

            provisioned_rcu = (
                utilization.get("ProvisionedReadCapacityUnits")                
                or 0
            )

            avg_consumed = avg_consumed_capacity(utilization, "ConsumedReadCapacityUnits", consumed_rcu)
            avg_provisioned = avg_provisioned_capacity(provisioned_rcu)

            if avg_provisioned < float(min_provisioned_rcu):
                continue

            rcu_utilization_pct = utilization_pct(avg_consumed, avg_provisioned)
            is_underutilized = rcu_utilization_pct < rcu_utilization_threshold

            if is_underutilized:
                table.setdefault("metadata", {})
                md = table["metadata"]
                
                md["lookback_days"] = lookback_days
                md["avg_consumed_rcu"] = round(avg_consumed, 4)
                md["avg_provisioned_rcu"] = round(avg_provisioned, 4)
                md["rcu_utilization_pct"] = round(rcu_utilization_pct, 2)
                md["rcu_utilization_threshold"] = rcu_utilization_threshold
                md["recommended_action"] = "review"
                
                # Get WCU for full cost calculation
                avg_provisioned_wcu = float(md.get("write_capacity_units", 0))
                if not avg_provisioned_wcu:
                    # Try to get from utilization data
                    provisioned_wcu_data = (
                        utilization.get("ProvisionedWriteCapacityUnits") or 0
                    )
                    avg_provisioned_wcu = avg_provisioned_capacity(provisioned_wcu_data)
                
                # Calculate current monthly cost (full table provisioned capacity)
                current_monthly_cost = estimate_dynamodb_provisioned_monthly_cost(
                    avg_provisioned,
                    avg_provisioned_wcu
                )
                md["current_monthly_cost"] = round(current_monthly_cost, 2)
                
                # Calculate recommended RCU (consumed + 20% buffer, minimum 1)
                recommended_rcu = max(1.0, avg_consumed * 1.2)
                md["recommended_rcu"] = round(recommended_rcu, 2)
                
                # Calculate cost with recommended RCU
                recommended_monthly_cost = estimate_dynamodb_provisioned_monthly_cost(
                    recommended_rcu,
                    avg_provisioned_wcu
                )
                md["recommended_monthly_cost"] = round(recommended_monthly_cost, 2)
                
                # Calculate potential savings
                monthly_savings = current_monthly_cost - recommended_monthly_cost
                if monthly_savings > 0:
                    md["potential_savings_monthly"] = round(monthly_savings, 2)
                    md["potential_savings_yearly"] = round(monthly_savings * 12, 2)
                else:
                    md["potential_savings_monthly"] = 0.0
                    md["potential_savings_yearly"] = 0.0
                    md["note"] = "No cost savings available - RCU already at minimum or savings negligible"
                
                md["check_reason"] = create_check_reason("underutilized_rcu", {
                    "lookback_days": lookback_days,
                    "rcu_utilization_pct": round(rcu_utilization_pct, 2),
                    "threshold_pct": rcu_utilization_threshold,
                })

                underutilized.append(table)

        except Exception as e:
            print(f"Error checking DynamoDB table {table_id} (RCU): {e}")
            continue

    return underutilized


check_registry.register(CheckMetadata(
    check_id="dynamodb_underutilized_rcu",
    name="DynamoDB Underutilized Read Capacity (RCU)",
    description="Identifies DynamoDB tables with low read capacity utilization over a lookback window",
    resource_type="dynamodb",
    check_function=check_dynamodb_underutilized_rcu,
    default_action="review",
    parameters={
        "lookback_days": 14,
        "rcu_utilization_threshold": 10.0,
        "min_provisioned_rcu": 1,
        "region": None,
    }
))
