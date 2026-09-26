"""Old EBS Snapshots Check - identifies snapshots exceeding age threshold."""
from typing import List, Dict, Any, Optional
from datetime import datetime
from app.checks.registry import check_registry, CheckMetadata
from app.checks.base import estimate_snapshot_monthly_cost, create_check_reason


def check_old_snapshots(
    aws_adapter,
    min_age_days: int = 90,
    region: Optional[str] = None
) -> List[Dict[str, Any]]:
    """
    Check for EBS snapshots older than the specified threshold.

    Args:
        aws_adapter: AWS adapter with credentials
        min_age_days: Minimum age in days to consider snapshot old (default: 90)
        region: Optional specific region to check

    Returns:
        List of old snapshots that exceed the age threshold
    """
    # Get all snapshots
    snapshots = aws_adapter.get_resources('snapshot', region=region)
    print(f"[OLD_SNAPSHOTS_CHECK] Found {len(snapshots)} total snapshots")

    old_snapshots = []

    for snapshot in snapshots:
        snapshot_id = snapshot.get('resource_id', 'unknown')
        # Get creation time from resource
        creation_time = snapshot.get('start_time') or snapshot.get('creation_time')
        if not creation_time:
            print(f"[OLD_SNAPSHOTS_CHECK] Snapshot {snapshot_id} has no start_time or creation_time, skipping")
            continue

        # Parse creation time if it's a string
        if isinstance(creation_time, str):
            try:
                creation_time = datetime.fromisoformat(creation_time.replace('Z', '+00:00'))
            except ValueError as e:
                print(f"[OLD_SNAPSHOTS_CHECK] Failed to parse start_time '{creation_time}' for snapshot {snapshot_id}: {e}")
                continue

        # Calculate age in days
        age_days = (datetime.utcnow() - creation_time.replace(tzinfo=None)).days
        print(f"[OLD_SNAPSHOTS_CHECK] Snapshot {snapshot_id}: age={age_days} days, threshold={min_age_days} days")

        # Check if snapshot is old enough
        if age_days > min_age_days:
            # Get snapshot details
            snapshot_metadata = snapshot.get('metadata', {})
            size_gb = snapshot_metadata.get('size', 0)

            # Estimate savings
            monthly_cost = estimate_snapshot_monthly_cost(size_gb)
            yearly_cost = monthly_cost * 12

            # Add check-specific metadata
            snapshot['metadata']['age_days'] = age_days
            snapshot['metadata']['size_gb'] = size_gb
            snapshot['metadata']['min_age_days'] = min_age_days
            snapshot['metadata']['recommended_action'] = 'delete'
            snapshot['metadata']['potential_savings_yearly'] = round(yearly_cost, 2)
            snapshot['metadata']['check_reason'] = create_check_reason('old', {
                'age_days': age_days,
                'min_age_days': min_age_days
            })

            old_snapshots.append(snapshot)
            print(f"[OLD_SNAPSHOTS_CHECK] ✓ Flagged snapshot {snapshot_id} as old (age: {age_days} days)")

    print(f"[OLD_SNAPSHOTS_CHECK] Found {len(old_snapshots)} old snapshots (threshold: {min_age_days} days)")
    return old_snapshots


# Auto-register
check_registry.register(CheckMetadata(
    check_id='snapshot_old_snapshots',
    name='Old EBS Snapshots',
    description='Identifies EBS snapshots older than the specified age threshold',
    resource_type='snapshot',
    check_function=check_old_snapshots,
    default_action='delete',
    parameters={
        'min_age_days': 90,
        'region': None
    }
))
