"""RDS Idle Databases Check - identifies underutilized RDS instances."""
from typing import List, Dict, Any, Optional
from datetime import datetime, timedelta
from app.checks.registry import check_registry, CheckMetadata
from app.checks.base import estimate_rds_monthly_cost, create_check_reason


def check_rds_idle_databases(
    aws_adapter,
    idle_days: int = 7,
    cpu_threshold: float = 5.0,
    connections_threshold: int = 5,
    region: Optional[str] = None
) -> List[Dict[str, Any]]:
    """
    Check for RDS databases with low utilization.

    Identifies RDS instances where average CPU utilization and connection count
    are below thresholds for the specified number of days.

    Args:
        aws_adapter: AWS adapter with credentials
        idle_days: Number of days to check (default: 7)
        cpu_threshold: CPU utilization threshold % (default: 5.0)
        connections_threshold: Database connections threshold (default: 5)
        region: Optional specific region to check

    Returns:
        List of idle RDS databases with metadata
    """
    # Get all available RDS instances
    databases = aws_adapter.get_resources('rds', {'state': 'available'}, region)

    idle_databases = []
    end_date = datetime.utcnow()
    start_date = end_date - timedelta(days=idle_days)

    for database in databases:
        # Get CloudWatch metrics
        try:
            utilization = aws_adapter.get_resource_utilization(
                database['resource_id'],
                'rds',
                start_date,
                end_date
            )

            cpu = utilization.get('cpuutilization', 0)
            connections = utilization.get('databaseconnections', 0)

            # Check if idle based on thresholds
            is_idle = (
                cpu < cpu_threshold and
                connections < connections_threshold
            )

            if is_idle:
                # Get instance details
                db_metadata = database.get('metadata', {})
                instance_class = db_metadata.get('instance_class', 'db.t3.micro')

                # Estimate monthly savings (70% of cost if stopped)
                monthly_cost = estimate_rds_monthly_cost(instance_class)
                potential_savings_monthly = monthly_cost * 0.7
                potential_savings_yearly = potential_savings_monthly * 12

                # Add check-specific metadata
                database['metadata']['idle_days'] = idle_days
                database['metadata']['avg_cpu_utilization'] = cpu
                database['metadata']['avg_connections'] = connections
                database['metadata']['cpu_threshold'] = cpu_threshold
                database['metadata']['connections_threshold'] = connections_threshold
                database['metadata']['recommended_action'] = 'stop'
                database['metadata']['potential_savings_yearly'] = round(potential_savings_yearly, 2)
                database['metadata']['check_reason'] = create_check_reason('idle', {
                    'idle_days': idle_days,
                    'avg_cpu': cpu,
                    'cpu_threshold': cpu_threshold
                })

                idle_databases.append(database)

        except Exception as e:
            # Log error but continue with other databases
            print(f"Error checking {database['resource_id']}: {e}")
            continue

    return idle_databases


# Auto-register
check_registry.register(CheckMetadata(
    check_id='rds_idle_databases',
    name='RDS Idle Databases',
    description='Identifies RDS instances with CPU and connection count below threshold for specified days',
    resource_type='rds',
    check_function=check_rds_idle_databases,
    default_action='stop',
    parameters={
        'idle_days': 7,
        'cpu_threshold': 5.0,
        'connections_threshold': 5,
        'region': None
    }
))
