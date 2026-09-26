"""EC2 Idle Instances Check - identifies underutilized EC2 instances."""
from typing import List, Dict, Any, Optional
from datetime import datetime, timedelta
from app.checks.registry import check_registry, CheckMetadata
from app.checks.base import (
    create_check_reason,
    estimate_ec2_monthly_cost,
)
from app.checks.ec2.gpu_utils import gpu_is_idle, gpu_metadata, gpu_unavailable_reason
from app.utils.ec2_gpu_info import gpu_info_for_instance_type


def check_ec2_idle_instances(
    aws_adapter,
    idle_days: int = 7,
    cpu_threshold: float = 5.0,
    network_threshold: float = 1000,  # retained for backward compatibility
    cpu_max_threshold: float = 15.0,
    cpu_p90_threshold: float = 6.0,
    cpu_p95_threshold: float = 8.0,
    cpu_p99_threshold: float = 10.0,
    gpu_threshold: float = 5.0,
    gpu_max_threshold: float = 15.0,
    region: Optional[str] = None
) -> List[Dict[str, Any]]:
    """
    Check for EC2 instances with sustained low CPU utilization.

    Identifies instances where average CPU utilization and CPU peaks stay below
    threshold for the specified number of days. Network and memory metrics are
    collected as supporting evidence but do not drive the idle decision.

    Args:
        aws_adapter: AWS adapter with credentials
        idle_days: Number of days to check (default: 7)
        cpu_threshold: CPU utilization threshold % (default: 5.0)
        network_threshold: Deprecated compatibility parameter
        cpu_max_threshold: Maximum CPU threshold % (default: 15.0)
        cpu_p90_threshold: p90 CPU threshold % (default: 6.0)
        cpu_p95_threshold: p95 CPU threshold % (default: 8.0)
        cpu_p99_threshold: p99 CPU threshold % (default: 10.0)
        gpu_threshold: Busiest-device p95 threshold % (default: 5.0)
        gpu_max_threshold: Busiest-device maximum threshold % (default: 15.0)
        region: Optional specific region to check

    Returns:
        List of idle EC2 instances with metadata
    """
    # Get all running EC2 instances
    print(
        f"[EC2_IDLE_CHECK] Starting check - idle_days={idle_days}, cpu_threshold={cpu_threshold}, "
        f"cpu_max_threshold={cpu_max_threshold}, cpu_p90_threshold={cpu_p90_threshold}, "
        f"cpu_p95_threshold={cpu_p95_threshold}, cpu_p99_threshold={cpu_p99_threshold}, "
        f"gpu_threshold={gpu_threshold}, gpu_max_threshold={gpu_max_threshold}, region={region}"
    )
    instances = aws_adapter.get_resources('ec2', {'state': 'running'}, region)
    print(f"[EC2_IDLE_CHECK] Found {len(instances)} running instances")
    if len(instances) == 0:
        print(
            f"[EC2_IDLE_CHECK] WARNING: No instances found. "
            f"Instances may have been created in a different region."
        )

    idle_instances = []
    end_date = datetime.utcnow()
    start_date = end_date - timedelta(days=idle_days)

    for instance in instances:
        # Get CloudWatch metrics
        try:
            instance_region = instance.get('region') or region
            print(f"[EC2_IDLE_CHECK] Checking instance {instance['resource_id']} in region {instance_region}")
            utilization = aws_adapter.get_resource_utilization(
                instance['resource_id'],
                'ec2',
                start_date,
                end_date,
                region=instance_region
            )
            print(
                f"[EC2_IDLE_CHECK] Utilization for {instance['resource_id']}: "
                f"CPU={utilization.get('cpuutilization', 0)}, "
                f"NetworkIn={utilization.get('networkin', 0)}, "
                f"NetworkOut={utilization.get('networkout', 0)}, "
                f"Memory={utilization.get('memoryutilization', 0)}"
            )

            cpu = utilization.get('cpuutilization', 0)
            network_in = utilization.get('networkin', 0)
            network_out = utilization.get('networkout', 0)
            memory = utilization.get('memoryutilization', 0)
            metric_summary = utilization.get('metric_summary', {})
            cpu_summary = metric_summary.get('cpuutilization', {})
            cpu_statistics = {
                statistic: cpu_summary.get(statistic)
                for statistic in ('maximum', 'p90', 'p95', 'p99')
            }
            missing_statistics = [
                statistic
                for statistic, value in cpu_statistics.items()
                if value is None
            ]
            if missing_statistics:
                print(
                    f"[EC2_IDLE_CHECK] Skipping {instance['resource_id']}: "
                    f"CPU metric summary missing {', '.join(missing_statistics)}"
                )
                continue
            cpu_maximum = cpu_statistics['maximum']
            cpu_p90 = cpu_statistics['p90']
            cpu_p95 = cpu_statistics['p95']
            cpu_p99 = cpu_statistics['p99']

            # Check if idle based on CPU average and peak thresholds.
            is_idle = (
                cpu < cpu_threshold and
                cpu_maximum < cpu_max_threshold and
                cpu_p90 < cpu_p90_threshold and
                cpu_p95 < cpu_p95_threshold and
                cpu_p99 < cpu_p99_threshold
            )
            
            print(
                f"[EC2_IDLE_CHECK] Instance {instance['resource_id']}: CPU avg={cpu:.2f}% (threshold={cpu_threshold}%), "
                f"CPU max={cpu_maximum:.2f}% (threshold={cpu_max_threshold}%), "
                f"CPU p90={cpu_p90:.2f}% (threshold={cpu_p90_threshold}%), "
                f"CPU p95={cpu_p95:.2f}% (threshold={cpu_p95_threshold}%), "
                f"CPU p99={cpu_p99:.2f}% (threshold={cpu_p99_threshold}%), "
                f"is_idle={is_idle}"
            )

            if is_idle:
                # Add check-specific metadata
                instance_metadata = instance.setdefault('metadata', {})
                instance_type = instance_metadata.get('instance_type') or instance.get('instance_type', 'unknown')
                instance_metadata.setdefault('instance_type', instance_type)

                gpu_info = gpu_info_for_instance_type(instance_type)
                if gpu_info is not None:
                    # CPU-idle is not evidence of idleness on GPU hardware; a
                    # training job can consume 95% GPU while using 3% CPU.
                    if utilization.get("gpu_metric_status") != "usable":
                        print(
                            f"[EC2_IDLE_CHECK] Skipping {instance['resource_id']}: "
                            f"GPU instance ({instance_type}, {gpu_info.device_count} devices) "
                            f"without usable GPU telemetry ({gpu_unavailable_reason(utilization)})"
                        )
                        continue
                    if not gpu_is_idle(utilization, gpu_threshold, gpu_max_threshold):
                        print(
                            f"[EC2_IDLE_CHECK] Skipping {instance['resource_id']}: "
                            f"GPU instance ({instance_type}) does not pass GPU idle gates"
                        )
                        continue

                # Estimate monthly savings (70% of instance cost if stopped)
                monthly_cost = estimate_ec2_monthly_cost(instance_type)
                potential_savings = monthly_cost * 0.7

                instance_metadata['idle_days'] = idle_days
                instance_metadata['avg_cpu_utilization'] = cpu
                instance_metadata['cpu_maximum'] = cpu_maximum
                instance_metadata['cpu_p90'] = cpu_p90
                instance_metadata['cpu_p95'] = cpu_p95
                instance_metadata['cpu_p99'] = cpu_p99
                instance_metadata['avg_network_in'] = network_in
                instance_metadata['avg_network_out'] = network_out
                instance_metadata['avg_memory_utilization'] = memory
                instance_metadata['cpu_threshold'] = cpu_threshold
                instance_metadata['cpu_max_threshold'] = cpu_max_threshold
                instance_metadata['cpu_p90_threshold'] = cpu_p90_threshold
                instance_metadata['cpu_p95_threshold'] = cpu_p95_threshold
                instance_metadata['cpu_p99_threshold'] = cpu_p99_threshold
                instance_metadata['recommended_action'] = 'stop'
                instance_metadata['recommended_actions'] = ['stop', 'schedule_off_hours']
                if instance_type:
                    from app.utils.ec2_instance_types import recommended_graviton_instance_type

                    graviton_target = recommended_graviton_instance_type(instance_type)
                    if graviton_target:
                        instance_metadata['recommended_actions'].append('migrate_to_graviton')
                        instance_metadata['recommended_graviton_instance_type'] = graviton_target
                instance_metadata['potential_savings_yearly'] = round(potential_savings * 12, 2)
                if 'metric_history' in utilization:
                    instance_metadata['metric_history'] = utilization['metric_history']
                check_reason = create_check_reason('idle', {
                    'idle_days': idle_days,
                    'avg_cpu': cpu,
                    'cpu_threshold': cpu_threshold
                })
                if gpu_info is not None:
                    instance_metadata.update(
                        gpu_metadata(gpu_info, utilization, gpu_threshold)
                    )
                    gpu_p95 = instance_metadata['gpu_p95_utilization']
                    check_reason += (
                        f" and all {gpu_info.device_count} GPUs idle "
                        f"(p95 {gpu_p95:.1f}%)"
                    )
                instance_metadata['check_reason'] = check_reason

                idle_instances.append(instance)

        except Exception as e:
            # Log error but continue with other instances
            print(f"Error checking {instance['resource_id']}: {e}")
            continue

    return idle_instances


# Auto-register check
check_registry.register(CheckMetadata(
    check_id='ec2_idle_instances',
    name='EC2 Idle Instances',
    description='Identifies EC2 instances with CPU utilization below threshold for specified days',
    resource_type='ec2',
    check_function=check_ec2_idle_instances,
    default_action='stop',
    parameters={
        'idle_days': 7,
        'cpu_threshold': 5.0,
        'network_threshold': 1000,
        'cpu_max_threshold': 15.0,
        'cpu_p90_threshold': 6.0,
        'cpu_p95_threshold': 8.0,
        'cpu_p99_threshold': 10.0,
        'gpu_threshold': 5.0,
        'gpu_max_threshold': 15.0,
        'region': None
    }
))
