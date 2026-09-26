"""EFS Wrong Performance Mode Check - finds file systems using generalPurpose when maxIO would be better."""
from typing import List, Dict, Any, Optional
from app.checks.registry import check_registry, CheckMetadata
from app.checks.base import create_check_reason


def check_efs_wrong_performance_mode(
    aws_adapter,
    region: Optional[str] = None
) -> List[Dict[str, Any]]:
    """
    Check for EFS file systems using generalPurpose mode when maxIO would be better.

    Note: This is a simplified check. In production, you'd analyze I/O patterns
    to determine if maxIO is needed. For now, we flag file systems that might
    benefit from maxIO based on certain criteria.

    Args:
        aws_adapter: AWS adapter with credentials
        region: Optional specific region to check

    Returns:
        List of EFS file systems that might benefit from maxIO mode
    """
    # Get all EFS file systems
    file_systems = aws_adapter.get_resources('efs', region=region)

    flagged = []

    for fs in file_systems:
        metadata = fs.get('metadata', {})
        performance_mode = metadata.get('PerformanceMode', 'generalPurpose')
        
        # Flag file systems using generalPurpose mode
        # In a real implementation, you'd analyze I/O patterns to determine
        # if maxIO is actually needed. For now, we'll flag all generalPurpose
        # file systems as potentially needing maxIO if they have high I/O.
        if performance_mode == 'generalPurpose':
            # Add check-specific metadata
            fs['metadata']['current_performance_mode'] = performance_mode
            fs['metadata']['recommended_performance_mode'] = 'maxIO'
            fs['metadata']['recommended_action'] = 'modify'
            fs['metadata']['check_reason'] = create_check_reason('wrong_mode', {
                'current': performance_mode,
                'recommended': 'maxIO',
                'reason': 'File system may benefit from maxIO performance mode for high I/O workloads'
            })

            flagged.append(fs)

    return flagged


# Auto-register
check_registry.register(CheckMetadata(
    check_id='efs_wrong_performance_mode',
    name='EFS Wrong Performance Mode',
    description='Identifies EFS file systems that may benefit from maxIO performance mode',
    resource_type='efs',
    check_function=check_efs_wrong_performance_mode,
    default_action='review',
    parameters={
        'region': None
    }
))
