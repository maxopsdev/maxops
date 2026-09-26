"""
DynamoDB Underutilized Tables Check - identifies DynamoDB tables with low item count.
"""

from typing import List, Dict, Any, Optional
from datetime import datetime, timedelta

from app.checks.registry import check_registry, CheckMetadata
from app.checks.base import (
    create_check_reason,
    estimate_dynamodb_provisioned_monthly_cost,
    estimate_dynamodb_ondemand_monthly_cost
)
from app.checks.dynamodb.capacity_math import avg_consumed_capacity


def check_dynamodb_underutilized_tables(
    aws_adapter,
    lookback_days: int = 14,
    item_count_threshold: int = 100,
    region: Optional[str] = None
) -> List[Dict[str, Any]]:
    """
    Check for DynamoDB tables with consistently low item count.

    Identifies tables where the average ItemCount is below the threshold
    over the specified lookback window.

    Args:
        aws_adapter: AWS adapter with credentials
        lookback_days: Number of days to check (default: 14)
        item_count_threshold: Item count threshold (default: 100)
        region: Optional specific region to check

    Returns:
        List of underutilized DynamoDB tables with metadata
    """
    # Get all DynamoDB tables
    tables = aws_adapter.get_resources("dynamodb", {}, region)

    underutilized_tables = []
    end_date = datetime.utcnow()
    start_date = end_date - timedelta(days=lookback_days)

    for table in tables:
        table_id = table.get("resource_id")

        if not table_id:
            continue

        try:
            # Get CloudWatch metrics
            utilization = aws_adapter.get_resource_utilization(
                table_id,
                "dynamodb",
                start_date,
                end_date
            )

            item_count = (
                utilization.get("itemcount")
                or utilization.get("ItemCount")
                or utilization.get("item_count")
                or 0
            )

            # Normalize to average if time-series
            if isinstance(item_count, (list, tuple)):
                values = [float(v) for v in item_count if v is not None]
                avg_item_count = sum(values) / len(values) if values else 0.0
            else:
                avg_item_count = float(item_count)

            # Check underutilization
            is_underutilized = avg_item_count < item_count_threshold

            if is_underutilized:
                table.setdefault("metadata", {})
                md = table["metadata"]
                
                md["lookback_days"] = lookback_days
                md["avg_item_count"] = round(avg_item_count, 2)
                md["item_count_threshold"] = item_count_threshold
                md["recommended_action"] = "review"
                
                # Calculate pricing and potential savings
                billing_mode = md.get("BillingMode") or md.get("billing_mode")
                read_capacity_units = md.get("read_capacity_units", 0)
                write_capacity_units = md.get("write_capacity_units", 0)
                
                if billing_mode == "PAY_PER_REQUEST" or (read_capacity_units == 0 and write_capacity_units == 0):
                    # On-Demand billing mode
                    # Get actual consumed capacity to estimate cost
                    consumed_rcu = (
                        utilization.get("ConsumedReadCapacityUnits")
                        or utilization.get("consumedreadcapacityunits")
                        or 0
                    )
                    consumed_wcu = (
                        utilization.get("ConsumedWriteCapacityUnits")
                        or utilization.get("consumedwritecapacityunits")
                        or 0
                    )
                    
                    avg_consumed_rcu = avg_consumed_capacity(utilization, "ConsumedReadCapacityUnits", consumed_rcu)
                    avg_consumed_wcu = avg_consumed_capacity(utilization, "ConsumedWriteCapacityUnits", consumed_wcu)
                    
                    if avg_consumed_rcu > 0 or avg_consumed_wcu > 0:
                        monthly_cost = estimate_dynamodb_ondemand_monthly_cost(
                            avg_consumed_rcu, avg_consumed_wcu
                        )
                        md["billing_mode"] = "PAY_PER_REQUEST"
                        md["current_monthly_cost"] = round(monthly_cost, 2)
                        md["potential_savings_monthly"] = round(monthly_cost, 2)
                        md["potential_savings_yearly"] = round(monthly_cost * 12, 2)
                    else:
                        md["billing_mode"] = "PAY_PER_REQUEST"
                        md["current_monthly_cost"] = 0.0
                        md["potential_savings_monthly"] = 0.0
                        md["potential_savings_yearly"] = 0.0
                        md["note"] = "On-Demand billing with no consumption: No direct costs, but table should be deleted if truly unused"
                else:
                    # Provisioned billing mode
                    monthly_cost = estimate_dynamodb_provisioned_monthly_cost(
                        float(read_capacity_units),
                        float(write_capacity_units)
                    )
                    md["billing_mode"] = "PROVISIONED"
                    md["current_monthly_cost"] = round(monthly_cost, 2)
                    # Full savings if table is deleted
                    md["potential_savings_monthly"] = round(monthly_cost, 2)
                    md["potential_savings_yearly"] = round(monthly_cost * 12, 2)
                
                md["check_reason"] = create_check_reason(
                    "underutilized",
                    {
                        "lookback_days": lookback_days,
                        "avg_item_count": round(avg_item_count, 2),
                        "item_count_threshold": item_count_threshold,
                    },
                )

                underutilized_tables.append(table)

        except Exception as e:
            # Log error but continue with other tables
            print(f"Error checking DynamoDB table {table_id}: {e}")
            continue

    return underutilized_tables


# Auto-register check
check_registry.register(CheckMetadata(
    check_id="dynamodb_underutilized_tables",
    name="DynamoDB Underutilized Tables",
    description="Identifies DynamoDB tables with low item count over a specified period",
    resource_type="dynamodb",
    check_function=check_dynamodb_underutilized_tables,
    default_action="review",
    parameters={
        "lookback_days": 14,
        "item_count_threshold": 100,
        "region": None,
    }
))
