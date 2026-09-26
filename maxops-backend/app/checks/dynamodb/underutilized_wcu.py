"""
DynamoDB Underutilized Tables (WCU) Check - identifies tables with low *write* capacity utilization.
"""

from typing import List, Dict, Any, Optional
from datetime import datetime, timedelta

from app.checks.registry import check_registry, CheckMetadata
from app.checks.base import create_check_reason, estimate_dynamodb_provisioned_monthly_cost
from app.checks.dynamodb.capacity_math import avg_consumed_capacity, avg_provisioned_capacity, utilization_pct


def check_dynamodb_underutilized_wcu(
    aws_adapter,
    lookback_days: int = 14,
    wcu_utilization_threshold: float = 10.0,  # percent
    min_provisioned_wcu: int = 1,
    region: Optional[str] = None
) -> List[Dict[str, Any]]:
    """
    Flags PROVISIONED DynamoDB tables whose average Consumed WCU is low vs Provisioned WCU.

    Utilization % = ((avg sum(Consumed WCU) / period) / avg_provisioned_wcu) * 100

    Args:
        aws_adapter: AWS adapter with credentials
        lookback_days: Number of days to check (default: 14)
        wcu_utilization_threshold: Utilization threshold % (default: 10.0)
        min_provisioned_wcu: Only evaluate tables with provisioned WCU >= this (default: 1)
        region: Optional specific region to check

    Returns:
        List of underutilized DynamoDB tables (WCU) with metadata
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

            consumed_wcu = (             
                utilization.get("ConsumedWriteCapacityUnits")               
                or 0
            )

            provisioned_wcu = (
                utilization.get("provisionedwritecapacityunits")
                or utilization.get("ProvisionedWriteCapacityUnits")
                or utilization.get("provisioned_wcu")
                or table.get("metadata", {}).get("write_capacity_units")
                or 0
            )

            avg_consumed = avg_consumed_capacity(utilization, "ConsumedWriteCapacityUnits", consumed_wcu)
            avg_provisioned = avg_provisioned_capacity(provisioned_wcu)

            if avg_provisioned < float(min_provisioned_wcu):
                continue

            wcu_utilization_pct = utilization_pct(avg_consumed, avg_provisioned)
            is_underutilized = wcu_utilization_pct < wcu_utilization_threshold

            if is_underutilized:
                table.setdefault("metadata", {})
                md = table["metadata"]
                
                md["lookback_days"] = lookback_days
                md["avg_consumed_wcu"] = round(avg_consumed, 4)
                md["avg_provisioned_wcu"] = round(avg_provisioned, 4)
                md["wcu_utilization_pct"] = round(wcu_utilization_pct, 2)
                md["wcu_utilization_threshold"] = wcu_utilization_threshold
                md["recommended_action"] = "review"
                
                # Get RCU for full cost calculation
                avg_provisioned_rcu = float(md.get("read_capacity_units", 0))
                if not avg_provisioned_rcu:
                    # Try to get from utilization data
                    provisioned_rcu_data = (
                        utilization.get("ProvisionedReadCapacityUnits") or 0
                    )
                    avg_provisioned_rcu = avg_provisioned_capacity(provisioned_rcu_data)
                
                # Calculate current monthly cost (full table provisioned capacity)
                current_monthly_cost = estimate_dynamodb_provisioned_monthly_cost(
                    avg_provisioned_rcu,
                    avg_provisioned
                )
                md["current_monthly_cost"] = round(current_monthly_cost, 2)
                
                # Calculate recommended WCU (consumed + 20% buffer, minimum 1)
                recommended_wcu = max(1.0, avg_consumed * 1.2)
                md["recommended_wcu"] = round(recommended_wcu, 2)
                
                # Calculate cost with recommended WCU
                recommended_monthly_cost = estimate_dynamodb_provisioned_monthly_cost(
                    avg_provisioned_rcu,
                    recommended_wcu
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
                    md["note"] = "No cost savings available - WCU already at minimum or savings negligible"
                
                md["check_reason"] = create_check_reason("underutilized_wcu", {
                    "lookback_days": lookback_days,
                    "wcu_utilization_pct": round(wcu_utilization_pct, 2),
                    "threshold_pct": wcu_utilization_threshold,
                })

                underutilized.append(table)

        except Exception as e:
            print(f"Error checking DynamoDB table {table_id} (WCU): {e}")
            continue

    return underutilized


check_registry.register(CheckMetadata(
    check_id="dynamodb_underutilized_wcu",
    name="DynamoDB Underutilized Write Capacity (WCU)",
    description="Identifies DynamoDB tables with low write capacity utilization over a lookback window",
    resource_type="dynamodb",
    check_function=check_dynamodb_underutilized_wcu,
    default_action="review",
    parameters={
        "lookback_days": 14,
        "wcu_utilization_threshold": 10.0,
        "min_provisioned_wcu": 1,
        "region": None,
    }
))
