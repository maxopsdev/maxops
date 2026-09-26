"""Base utilities for check functions."""
import re
from datetime import datetime, timezone
from typing import Any, Dict, Optional


def get_metric_history_statistic(
    metric_history: Any,
    metric_name: str,
    statistic: str,
) -> Optional[float]:
    """Return the highest observed value for a metric-history statistic.

    Metric history stores one value per CloudWatch period for each statistic.
    An absent or empty series is an unknown signal, not a measured zero.
    """
    if not isinstance(metric_history, dict):
        return None
    metric_entry = metric_history.get(metric_name)
    if not isinstance(metric_entry, dict):
        return None
    values = metric_entry.get(statistic)
    if not isinstance(values, (list, tuple)) or not values:
        return None

    numeric_values = []
    for value in values:
        try:
            numeric_values.append(float(value))
        except (TypeError, ValueError):
            continue
    return max(numeric_values) if numeric_values else None


def estimate_ec2_monthly_cost(instance_type: str) -> float:
    """
    Estimate monthly cost for EC2 instance types.

    Args:
        instance_type: EC2 instance type (e.g., 't2.micro', 'm5.large')

    Returns:
        Estimated monthly cost in USD
    """
    # Simplified pricing - in production would use AWS Pricing API
    pricing = {
        't2.micro': 8.50,
        't2.small': 17.00,
        't2.medium': 34.00,
        't2.large': 68.00,
        't3.micro': 7.50,
        't3.small': 15.00,
        't3.medium': 30.00,
        't3.large': 60.00,
        't3.xlarge': 120.00,
        'm5.large': 70.00,
        'm5.xlarge': 140.00,
        'm5.2xlarge': 280.00,
        'c5.large': 62.00,
        'c5.xlarge': 125.00,
        'c5.2xlarge': 250.00,
        'r5.large': 90.00,
        'r5.xlarge': 180.00,
    }
    return pricing.get(instance_type, 50.00)  # Default estimate


def estimate_ebs_monthly_cost(size_gb: int, volume_type: str = 'gp2') -> float:
    """
    Estimate monthly cost for EBS volumes.

    Args:
        size_gb: Volume size in GB
        volume_type: Volume type (gp2, gp3, io1, io2, st1, sc1)

    Returns:
        Estimated monthly cost in USD
    """
    # Simplified pricing per GB-month
    pricing_per_gb = {
        'gp2': 0.10,
        'gp3': 0.08,
        'io1': 0.125,
        'io2': 0.125,
        'st1': 0.045,
        'sc1': 0.025,
    }
    cost_per_gb = pricing_per_gb.get(volume_type, 0.10)
    return size_gb * cost_per_gb


def estimate_snapshot_monthly_cost(size_gb: int) -> float:
    """
    Estimate monthly cost for EBS snapshots.

    Args:
        size_gb: Snapshot size in GB

    Returns:
        Estimated monthly cost in USD
    """
    # EBS snapshot pricing: $0.05 per GB-month
    return size_gb * 0.05


def estimate_rds_monthly_cost(instance_class: str) -> float:
    """
    Estimate monthly cost for RDS instances.

    Args:
        instance_class: RDS instance class (e.g., 'db.t3.micro', 'db.m5.large')

    Returns:
        Estimated monthly cost in USD
    """
    # Simplified pricing - in production would use AWS Pricing API
    # Graviton instances are typically 10-20% cheaper than equivalent non-Graviton
    pricing = {
        # Non-Graviton instances
        'db.t3.micro': 12.00,
        'db.t3.small': 24.00,
        'db.t3.medium': 48.00,
        'db.t3.large': 96.00,
        'db.m5.large': 120.00,
        'db.m5.xlarge': 240.00,
        'db.m5.2xlarge': 480.00,
        'db.r5.large': 160.00,
        'db.r5.xlarge': 320.00,
        # Graviton instances (typically 15% cheaper)
        'db.t4g.micro': 10.20,  # ~15% cheaper than t3.micro
        'db.t4g.small': 20.40,  # ~15% cheaper than t3.small
        'db.t4g.medium': 40.80,  # ~15% cheaper than t3.medium
        'db.t4g.large': 81.60,  # ~15% cheaper than t3.large
        'db.m6g.large': 102.00,  # ~15% cheaper than m5.large
        'db.m6g.xlarge': 204.00,  # ~15% cheaper than m5.xlarge
        'db.m6g.2xlarge': 408.00,  # ~15% cheaper than m5.2xlarge
        'db.r6g.large': 136.00,  # ~15% cheaper than r5.large
        'db.r6g.xlarge': 272.00,  # ~15% cheaper than r5.xlarge
    }
    return pricing.get(instance_class, 75.00)  # Default estimate


def estimate_dynamodb_provisioned_monthly_cost(read_capacity_units: float, write_capacity_units: float) -> float:
    """
    Estimate monthly cost for DynamoDB PROVISIONED capacity.

    Args:
        read_capacity_units: Provisioned read capacity units
        write_capacity_units: Provisioned write capacity units

    Returns:
        Estimated monthly cost in USD
    """
    # DynamoDB PROVISIONED pricing (as of 2024):
    # - Read capacity: $0.00013 per RCU-hour
    # - Write capacity: $0.00065 per WCU-hour
    # - Monthly (730 hours): RCU * 0.00013 * 730 + WCU * 0.00065 * 730
    
    hours_per_month = 730
    rcu_cost_per_hour = 0.00013
    wcu_cost_per_hour = 0.00065
    
    monthly_rcu_cost = read_capacity_units * rcu_cost_per_hour * hours_per_month
    monthly_wcu_cost = write_capacity_units * wcu_cost_per_hour * hours_per_month
    
    return monthly_rcu_cost + monthly_wcu_cost


def estimate_dynamodb_ondemand_monthly_cost(avg_consumed_rcu: float, avg_consumed_wcu: float) -> float:
    """
    Estimate monthly cost for DynamoDB On-Demand (PAY_PER_REQUEST) based on average consumption.

    Args:
        avg_consumed_rcu: Average consumed read capacity units per hour
        avg_consumed_wcu: Average consumed write capacity units per hour

    Returns:
        Estimated monthly cost in USD
    """
    # DynamoDB On-Demand pricing (as of 2024):
    # - Read: $0.25 per million read units
    # - Write: $1.25 per million write units
    # - 1 RCU = 1 read unit per second = 3,600 read units per hour
    # - 1 WCU = 1 write unit per second = 3,600 write units per hour
    
    hours_per_month = 730
    read_units_per_rcu_hour = 3600  # 1 RCU = 1 read unit/second = 3600 read units/hour
    write_units_per_wcu_hour = 3600  # 1 WCU = 1 write unit/second = 3600 write units/hour
    
    # Calculate total read/write units per month
    total_read_units = avg_consumed_rcu * read_units_per_rcu_hour * hours_per_month
    total_write_units = avg_consumed_wcu * write_units_per_wcu_hour * hours_per_month
    
    # Convert to millions and apply pricing
    read_cost = (total_read_units / 1_000_000) * 0.25
    write_cost = (total_write_units / 1_000_000) * 1.25
    
    return read_cost + write_cost


def estimate_efs_monthly_cost(size_gb: float, throughput_mode: str = 'bursting', storage_class: str = 'standard') -> float:
    """
    Estimate monthly cost for EFS file systems.

    Args:
        size_gb: File system size in GB
        throughput_mode: Throughput mode ('bursting' or 'provisioned')
        storage_class: Storage class ('standard' or 'ia' for Infrequent Access)

    Returns:
        Estimated monthly cost in USD
    """
    # EFS pricing (as of 2024):
    # Standard storage: $0.30 per GB-month
    # IA storage: $0.045 per GB-month (85% cheaper)
    # Bursting throughput: Included with storage
    # Provisioned throughput: $6.00 per MB/s-month
    
    if storage_class == 'ia':
        cost_per_gb = 0.045
    else:
        cost_per_gb = 0.30
    
    storage_cost = size_gb * cost_per_gb
    
    # Provisioned throughput cost (if applicable)
    # Note: This is a simplified estimate. In production, you'd need actual provisioned throughput value
    throughput_cost = 0.0
    if throughput_mode == 'provisioned':
        # Estimate based on typical provisioned throughput (e.g., 100 MB/s)
        # This should be calculated based on actual provisioned throughput
        estimated_throughput_mbps = 100  # Placeholder
        throughput_cost = estimated_throughput_mbps * 6.00
    
    return storage_cost + throughput_cost


def create_check_reason(check_type: str, details: Dict[str, Any]) -> str:
    """
    Create a human-readable reason string for why a resource matched a check.

    Args:
        check_type: Type of check (e.g., 'idle', 'unattached', 'old')
        details: Dictionary with check-specific details

    Returns:
        Human-readable reason string
    """
    if check_type == 'idle':
        return (
            f"Idle for {details.get('idle_days', 'N/A')} days: "
            f"CPU {details.get('avg_cpu', 0):.1f}% < {details.get('cpu_threshold', 0)}%"
        )
    elif check_type == 'unattached':
        return f"Unattached for {details.get('min_age_days', 'N/A')}+ days"
    elif check_type == 'old':
        return f"Age {details.get('age_days', 'N/A')} days > {details.get('min_age_days', 'N/A')} days threshold"
    elif check_type == 'unused':
        return f"Unused: {details.get('reason', 'No activity detected')}"
    elif check_type == 'wrong_mode':
        return (
            f"Current mode: {details.get('current', 'N/A')}, "
            f"Recommended: {details.get('recommended', 'N/A')}. "
            f"{details.get('reason', '')}"
        )
    elif check_type == 'missing_policy':
        return f"Missing {details.get('policy', 'policy')}: {details.get('reason', 'Not configured')}"
    elif check_type == 'cost_efficiency':
        return (
            f"Cost efficiency opportunity for {details.get('resource', 'resource')}: "
            f"{details.get('issue', 'review recommended')}"
        )
    elif check_type == 'underutilized':
        return f"Underutilized: {details.get('reason', 'Observed demand is below provisioned capacity')}"
    else:
        return f"Matched {check_type} check criteria"


# --- resource age helpers ---------------------------------------------------
#
# Several checks ("unattached for N days", "stopped for N days") need to know
# how long a resource has been in its current state. AWS exposes this
# unevenly: EBS and EFS report a creation time, EC2 only embeds a stop
# timestamp inside the free-text StateTransitionReason, and detach time is not
# published at all. These helpers normalise what is available so the checks
# can apply their age thresholds consistently.

# e.g. "User initiated (2024-01-15 10:30:00 GMT)"
_STATE_TRANSITION_TIME = re.compile(
    r"\((\d{4}-\d{2}-\d{2}[ T]\d{2}:\d{2}:\d{2})(?:\.\d+)?\s*(?:GMT|UTC)?\)"
)


def parse_timestamp(value: Any) -> Optional[datetime]:
    """Coerce an ISO-8601 string or datetime into an aware UTC datetime.

    Returns None for anything unparseable -- callers treat that as "age
    unknown" rather than guessing.
    """
    if value is None:
        return None
    if isinstance(value, datetime):
        parsed = value
    elif isinstance(value, str):
        text = value.strip()
        if not text:
            return None
        try:
            parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
        except ValueError:
            return None
    else:
        return None
    # boto3 returns aware datetimes; a naive one is assumed to be UTC.
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def parse_state_transition_time(reason: Any) -> Optional[datetime]:
    """Pull the timestamp out of an EC2 StateTransitionReason string.

    EC2 has no first-class "stopped at" field; the time is embedded in a
    human-readable reason such as "User initiated (2024-01-15 10:30:00 GMT)".
    Returns None when the reason is absent or has no timestamp (which happens
    for instances that were never stopped).
    """
    if not isinstance(reason, str):
        return None
    match = _STATE_TRANSITION_TIME.search(reason)
    if not match:
        return None
    return parse_timestamp(match.group(1).replace(" ", "T"))


def age_in_days(value: Any, *, now: Optional[datetime] = None) -> Optional[float]:
    """Days elapsed since `value`, or None when the timestamp is unknown."""
    parsed = parse_timestamp(value)
    if parsed is None:
        return None
    reference = now or datetime.now(timezone.utc)
    if reference.tzinfo is None:
        reference = reference.replace(tzinfo=timezone.utc)
    return (reference - parsed).total_seconds() / 86400.0


def meets_min_age(
    value: Any, min_age_days: Optional[float], *, now: Optional[datetime] = None
) -> bool:
    """Whether a resource is at least `min_age_days` old.

    Deliberately conservative: an unknown timestamp does NOT satisfy a
    positive threshold. A check asking for "unattached for at least 7 days"
    cannot honestly report a resource whose age it could not establish, and
    these findings recommend deletion.

    A threshold of 0 (or None) disables the gate entirely, which is the
    escape hatch for seeing everything regardless of age.
    """
    if not min_age_days or min_age_days <= 0:
        return True
    age = age_in_days(value, now=now)
    if age is None:
        return False
    return age >= min_age_days
