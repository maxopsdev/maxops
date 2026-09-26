"""EC2 Unused Instances Check - identifies stopped or completely unused EC2 instances."""
from typing import List, Dict, Any, Optional
from app.checks.registry import check_registry, CheckMetadata
from app.checks.base import (
    create_check_reason,
    estimate_ec2_monthly_cost,
    parse_state_transition_time,
    age_in_days,
    meets_min_age,
)


def check_ec2_unused_instances(
    aws_adapter,
    stopped_days: int = 30,
    region: Optional[str] = None
) -> List[Dict[str, Any]]:
    """
    Check for EC2 instances that are stopped or completely unused.

    Identifies instances that have been in 'stopped' state for longer than
    the specified threshold, indicating they may no longer be needed.

    Args:
        aws_adapter: AWS adapter with credentials
        stopped_days: Minimum days the instance must have been stopped before
            it is reported (default: 30). EC2 exposes no "stopped at" field, so
            the time is parsed out of StateTransitionReason. An instance whose
            stop time cannot be determined is not reported; pass 0 to disable
            the gate.
        region: Optional specific region to check

    Returns:
        List of unused EC2 instances with metadata
    """
    # Get all stopped EC2 instances
    instances = aws_adapter.get_resources('ec2', {'state': 'stopped'}, region)

    unused_instances = []

    for instance in instances:
        instance_metadata = instance.get('metadata', {})

        # How long has it actually been stopped? EC2 buries the timestamp in
        # StateTransitionReason, e.g. "User initiated (2024-01-15 10:30:00 GMT)".
        stopped_at = parse_state_transition_time(
            instance_metadata.get('state_transition_reason')
        )
        if not meets_min_age(stopped_at, stopped_days):
            continue

        instance_type = instance_metadata.get('instance_type') or instance.get('instance_type', 'unknown')
        instance_metadata.setdefault('instance_type', instance_type)

        # Estimate savings (full cost since instance is not being used)
        # Stopped instances still incur EBS storage costs, but we save on compute
        monthly_compute_cost = estimate_ec2_monthly_cost(instance_type)
        potential_savings_monthly = monthly_compute_cost * 0.9  # 90% savings (some EBS costs remain)
        potential_savings_yearly = potential_savings_monthly * 12

        # Add check-specific metadata
        instance['metadata']['stopped_days_threshold'] = stopped_days
        stopped_for = age_in_days(stopped_at)
        if stopped_for is not None:
            instance['metadata']['stopped_days_observed'] = round(stopped_for, 1)
        instance['metadata']['recommended_action'] = 'terminate'
        instance['metadata']['recommended_actions'] = [
            'terminate',
            'terminate_with_snapshot',
            'terminate_leave_volume',
        ]
        instance['metadata']['potential_savings_yearly'] = round(potential_savings_yearly, 2)
        instance['metadata']['check_reason'] = create_check_reason('unused', {
            'reason': f'Instance stopped for {stopped_days}+ days'
        })

        unused_instances.append(instance)

    return unused_instances


# Auto-register
check_registry.register(CheckMetadata(
    check_id='ec2_unused_instances',
    name='EC2 Unused Instances',
    description='Identifies EC2 instances that have been stopped for an extended period',
    resource_type='ec2',
    check_function=check_ec2_unused_instances,
    default_action='terminate',
    parameters={
        'stopped_days': 30,
        'region': None
    }
))
