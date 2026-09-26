"""EFS Unused File Systems Check - finds file systems with no mount targets."""
from typing import List, Dict, Any, Optional
from app.checks.registry import check_registry, CheckMetadata
from app.checks.base import (
    age_in_days,
    create_check_reason,
    estimate_efs_monthly_cost,
    meets_min_age,
)


def check_efs_unused_file_systems(
    aws_adapter,
    min_age_days: int = 7,
    region: Optional[str] = None
) -> List[Dict[str, Any]]:
    """
    Check for EFS file systems with no mount targets (unused).

    Args:
        aws_adapter: AWS adapter with credentials
        min_age_days: Minimum file system age in days before it is reported
            (default: 7), measured from its creation time. A file system whose
            age cannot be determined is not reported; pass 0 to disable the
            gate.
        region: Optional specific region to check

    Returns:
        List of unused EFS file systems
    """
    # Get all EFS file systems
    file_systems = aws_adapter.get_resources('efs', region=region)

    unused = []

    for fs in file_systems:
        # Check if file system has mount targets
        metadata = fs.get('metadata', {})
        number_of_mount_targets = metadata.get('NumberOfMountTargets', 0)

        # Consider unused if no mount targets
        if number_of_mount_targets == 0:
            # A file system created minutes ago has no mount targets yet and
            # is not waste -- honour the age threshold before reporting.
            created_at = metadata.get('CreationTime')
            if not meets_min_age(created_at, min_age_days):
                continue

            # Get file system details
            size_in_bytes = metadata.get('SizeInBytes', {})
            size_value = size_in_bytes.get('Value', 0) if isinstance(size_in_bytes, dict) else 0
            size_gb = size_value / (1024 ** 3) if size_value > 0 else 0
            
            # Estimate savings (full file system cost since it's not being used)
            monthly_cost = estimate_efs_monthly_cost(size_gb, throughput_mode='bursting')
            yearly_cost = monthly_cost * 12

            # Add check-specific metadata
            fs['metadata']['unused'] = True
            fs['metadata']['recommended_action'] = 'delete'
            fs['metadata']['size_gb'] = round(size_gb, 2)
            fs['metadata']['mount_targets_count'] = 0
            fs['metadata']['potential_savings_yearly'] = round(yearly_cost, 2)
            fs_age = age_in_days(created_at)
            if fs_age is not None:
                fs['metadata']['file_system_age_days'] = round(fs_age, 1)
            fs['metadata']['check_reason'] = create_check_reason('unused', {
                'min_age_days': min_age_days,
                'reason': 'No mount targets found'
            })

            unused.append(fs)

    return unused


# Auto-register
check_registry.register(CheckMetadata(
    check_id='efs_unused_file_systems',
    name='EFS Unused File Systems',
    description='Finds EFS file systems with no mount targets (unused)',
    resource_type='efs',
    check_function=check_efs_unused_file_systems,
    default_action='delete',
    parameters={
        'min_age_days': 7,
        'region': None
    }
))
