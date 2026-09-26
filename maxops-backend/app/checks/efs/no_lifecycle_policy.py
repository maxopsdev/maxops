"""EFS No Lifecycle Policy Check - finds file systems without lifecycle policies for IA transition."""
from typing import List, Dict, Any, Optional
from app.checks.registry import check_registry, CheckMetadata
from app.checks.base import create_check_reason, estimate_efs_monthly_cost


def check_efs_no_lifecycle_policy(
    aws_adapter,
    region: Optional[str] = None
) -> List[Dict[str, Any]]:
    """
    Check for EFS file systems without lifecycle policies for Infrequent Access (IA) transition.

    EFS Lifecycle Management can automatically move files to the IA storage class
    after a specified number of days, reducing costs by up to 85%.

    Args:
        aws_adapter: AWS adapter with credentials
        region: Optional specific region to check

    Returns:
        List of EFS file systems without lifecycle policies
    """
    # Get all EFS file systems
    file_systems = aws_adapter.get_resources('efs', region=region)

    flagged = []

    for fs in file_systems:
        metadata = fs.get('metadata', {})
        lifecycle_policies = metadata.get('LifecyclePolicies', [])
        
        # Check if file system has any lifecycle policies
        if len(lifecycle_policies) == 0:
            # Get file system size for savings calculation
            size_in_bytes = metadata.get('SizeInBytes', {})
            size_value = size_in_bytes.get('Value', 0) if isinstance(size_in_bytes, dict) else 0
            size_gb = size_value / (1024 ** 3) if size_value > 0 else 0
            
            # Estimate potential savings (IA is ~85% cheaper than Standard)
            # Assuming 50% of data could be moved to IA
            monthly_cost_standard = estimate_efs_monthly_cost(size_gb, throughput_mode='bursting')
            monthly_cost_ia = estimate_efs_monthly_cost(size_gb * 0.5, throughput_mode='bursting', storage_class='ia')
            potential_savings_monthly = monthly_cost_standard - (monthly_cost_standard * 0.5 * 0.85 + monthly_cost_ia)
            potential_savings_yearly = potential_savings_monthly * 12

            # Add check-specific metadata
            fs['metadata']['lifecycle_policies_count'] = 0
            fs['metadata']['recommended_action'] = 'add_lifecycle_policy'
            fs['metadata']['size_gb'] = round(size_gb, 2)
            fs['metadata']['potential_savings_yearly'] = round(potential_savings_yearly, 2)
            fs['metadata']['check_reason'] = create_check_reason('missing_policy', {
                'policy': 'lifecycle',
                'reason': 'No lifecycle policy configured for IA transition'
            })

            flagged.append(fs)

    return flagged


# Auto-register
check_registry.register(CheckMetadata(
    check_id='efs_no_lifecycle_policy',
    name='EFS No Lifecycle Policy',
    description='Identifies EFS file systems without lifecycle policies for Infrequent Access (IA) transition',
    resource_type='efs',
    check_function=check_efs_no_lifecycle_policy,
    default_action='add_lifecycle_policy',
    parameters={
        'region': None
    }
))
