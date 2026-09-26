"""EBS Unattached Volumes Check - finds volumes not attached to any instance."""
from typing import List, Dict, Any, Optional
from app.checks.registry import check_registry, CheckMetadata
from app.checks.base import (
    age_in_days,
    create_check_reason,
    estimate_ebs_monthly_cost,
    meets_min_age,
)


def check_ebs_unattached_volumes(
    aws_adapter,
    min_age_days: int = 7,
    region: Optional[str] = None
) -> List[Dict[str, Any]]:
    """
    Check for EBS volumes not attached to any instance.

    Args:
        aws_adapter: AWS adapter with credentials
        min_age_days: Minimum volume age in days before it is reported
            (default: 7). Measured from the volume's creation time -- AWS
            does not publish a detach time, so creation time is the only age
            signal available without CloudTrail. A volume whose age cannot be
            determined is not reported; pass 0 to disable the gate.
        region: Optional specific region to check

    Returns:
        List of unattached EBS volumes
    """
    # Get all EBS volumes
    volumes = aws_adapter.get_resources('ebs', region=region)

    unattached = []

    for volume in volumes:
        # Only consider available (unattached) volumes
        if volume.get('state') != 'available':
            continue
        if not volume.get('attached', False):
            # Get volume details
            volume_metadata = volume.get('metadata', {})

            # Honour the age threshold before reporting anything -- these
            # findings recommend deletion, so a volume detached moments ago
            # during maintenance must not surface as waste.
            created_at = volume_metadata.get('create_time')
            if not meets_min_age(created_at, min_age_days):
                continue

            size_gb = volume_metadata.get('size', 0)
            volume_type = volume_metadata.get('volume_type', 'gp2')

            # Estimate savings (full volume cost since it's not being used)
            monthly_cost = estimate_ebs_monthly_cost(size_gb, volume_type)
            yearly_cost = monthly_cost * 12

            # Add check-specific metadata
            volume['metadata']['unattached'] = True
            volume['metadata']['recommended_action'] = 'snapshot_and_terminate'
            volume['metadata']['recommended_actions'] = [
                'snapshot_and_terminate',
                'ebs_lifecycle_policy',
            ]
            volume['metadata']['size_gb'] = size_gb
            volume['metadata']['volume_type'] = volume_type
            volume['metadata']['potential_savings_yearly'] = round(yearly_cost, 2)
            volume_age = age_in_days(created_at)
            if volume_age is not None:
                volume['metadata']['volume_age_days'] = round(volume_age, 1)
            volume['metadata']['check_reason'] = create_check_reason('unattached', {
                'min_age_days': min_age_days
            })

            unattached.append(volume)

    return unattached


# Auto-register
check_registry.register(CheckMetadata(
    check_id='ebs_unattached_volumes',
    name='EBS Unattached Volumes',
    description='Finds EBS volumes not attached to any EC2 instance',
    resource_type='ebs',
    check_function=check_ebs_unattached_volumes,
    default_action='snapshot_and_terminate',
    parameters={
        'min_age_days': 7,
        'region': None
    }
))
