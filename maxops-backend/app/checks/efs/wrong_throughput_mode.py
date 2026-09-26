"""EFS Wrong Throughput Mode Check - finds file systems using bursting when provisioned would be better."""
from typing import List, Dict, Any, Optional
from app.checks.registry import check_registry, CheckMetadata
from app.checks.base import create_check_reason


def check_efs_wrong_throughput_mode(
    aws_adapter,
    region: Optional[str] = None
) -> List[Dict[str, Any]]:
    """
    Check for EFS file systems using bursting mode when provisioned would be better.

    Note: This is a simplified check. In production, you'd analyze throughput patterns
    to determine if provisioned mode is needed. For now, we flag file systems that
    might benefit from provisioned throughput.

    Args:
        aws_adapter: AWS adapter with credentials
        region: Optional specific region to check

    Returns:
        List of EFS file systems that might benefit from provisioned throughput mode
    """
    # Get all EFS file systems
    file_systems = aws_adapter.get_resources('efs', region=region)

    flagged = []

    for fs in file_systems:
        metadata = fs.get('metadata', {})
        throughput_mode = metadata.get('ThroughputMode', 'bursting')
        
        # Flag file systems using bursting mode
        # In a real implementation, you'd analyze throughput patterns to determine
        # if provisioned mode is actually needed. For now, we'll flag all bursting
        # file systems as potentially needing provisioned throughput.
        if throughput_mode == 'bursting':
            # Add check-specific metadata
            fs['metadata']['current_throughput_mode'] = throughput_mode
            fs['metadata']['recommended_throughput_mode'] = 'provisioned'
            fs['metadata']['recommended_action'] = 'modify'
            fs['metadata']['check_reason'] = create_check_reason('wrong_mode', {
                'current': throughput_mode,
                'recommended': 'provisioned',
                'reason': 'File system may benefit from provisioned throughput mode for consistent performance'
            })

            flagged.append(fs)

    return flagged


# Auto-register
check_registry.register(CheckMetadata(
    check_id='efs_wrong_throughput_mode',
    name='EFS Wrong Throughput Mode',
    description='Identifies EFS file systems that may benefit from provisioned throughput mode',
    resource_type='efs',
    check_function=check_efs_wrong_throughput_mode,
    default_action='review',
    parameters={
        'region': None
    }
))
