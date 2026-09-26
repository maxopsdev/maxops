"""Register all pricing handlers."""
from __future__ import annotations

from app.pricing.registry import pricing_registry, PricingMetadata
from app.pricing.pricing_ec2 import handle_ec2_pricing
from app.pricing.pricing_ebs import handle_ebs_pricing
from app.pricing.pricing_snapshot import handle_snapshot_pricing
from app.pricing.pricing_s3 import handle_s3_pricing
from app.pricing.pricing_efs import handle_efs_pricing
from app.pricing.pricing_ecs import handle_ecs_pricing
from app.pricing.pricing_asg import handle_asg_pricing
from app.pricing.pricing_elasticache import (
    handle_elasticache_non_graviton_pricing,
    handle_elasticache_pricing,
)
from app.pricing.pricing_sagemaker import handle_sagemaker_pricing

for _sagemaker_check in (
    "sagemaker_endpoint_idle",
    "sagemaker_endpoint_overprovisioned",
    "sagemaker_endpoint_gpu_underutilized",
    "sagemaker_notebook_no_auto_stop",
    "sagemaker_notebook_idle",
    "sagemaker_training_no_managed_spot",
):
    pricing_registry.register(PricingMetadata(
        check_id=_sagemaker_check,
        name=f"SageMaker Pricing - {_sagemaker_check}",
        description="SageMaker ML instance pricing from the AWS public catalog",
        resource_type="sagemaker",
        handler=handle_sagemaker_pricing,
    ))

# ElastiCache pricing
pricing_registry.register(PricingMetadata(
    check_id="elasticache_non_graviton_instance_class",
    name="ElastiCache Non-Graviton Pricing",
    description="Calculate cost savings for migrating to Graviton",
    resource_type="elasticache",
    handler=handle_elasticache_non_graviton_pricing,
))

pricing_registry.register(PricingMetadata(
    check_id="elasticache_low_item_count",
    name="ElastiCache Low Item Count Pricing",
    description="Full cost savings for unused clusters",
    resource_type="elasticache",
    handler=handle_elasticache_pricing,
    parameters={"savings_ratio": 1.0},
))

# ASG pricing
asg_checks = [
    "asg_low_cpu_overprovisioned",
    "asg_scale_in_never_triggered",
    "asg_idle_capacity_high",
    "asg_non_graviton_candidates",
    "asg_on_demand_heavy_mix",
    "asg_launch_template_old_generation",
    "asg_scheduled_scaling_mismatch",
    "asg_warm_pool_oversized",
    "asg_low_traffic_with_running_instances",
]

for check_id in asg_checks:
    pricing_registry.register(
        PricingMetadata(
            check_id=check_id,
            name=f"ASG Pricing - {check_id}",
            description="Cost savings for ASG optimization checks",
            resource_type="asg",
            handler=handle_asg_pricing,
            parameters={"savings_ratio": 0.3},
        )
    )

pricing_registry.register(PricingMetadata(
    check_id="elasticache_redis_convertible_to_valkey",
    name="ElastiCache Valkey Migration Pricing",
    description="Cost savings for migrating from Redis to Valkey",
    resource_type="elasticache",
    handler=handle_elasticache_pricing,
    parameters={
        "savings_ratio": 0.2,
        "note": "Valkey offers ~20% lower costs than Redis OSS on ElastiCache"
    },
))

# EC2 pricing
pricing_registry.register(PricingMetadata(
    check_id="ec2_idle_instances",
    name="EC2 Idle Instances Pricing",
    description="Cost savings for stopping idle instances",
    resource_type="ec2",
    handler=handle_ec2_pricing,
    parameters={"savings_ratio": 0.7},
))

pricing_registry.register(PricingMetadata(
    check_id="ec2_unused_instances",
    name="EC2 Unused Instances Pricing",
    description="Cost savings for terminating unused instances",
    resource_type="ec2",
    handler=handle_ec2_pricing,
    parameters={"savings_ratio": 0.9},
))

pricing_registry.register(PricingMetadata(
    check_id="ec2_graviton_candidate",
    name="EC2 Graviton Candidate Pricing",
    description="Estimated savings for moving to a Graviton instance family",
    resource_type="ec2",
    handler=handle_ec2_pricing,
    parameters={"savings_ratio": 0.16},
))

# EBS pricing
pricing_registry.register(PricingMetadata(
    check_id="ebs_unattached_volumes",
    name="EBS Unattached Volumes Pricing",
    description="Full cost savings for deleting unattached volumes",
    resource_type="ebs",
    handler=handle_ebs_pricing,
    parameters={"full_savings": True},
))

pricing_registry.register(PricingMetadata(
    check_id="ebs_underutilized_volume",
    name="EBS Underutilized Volume Pricing",
    description="Cost savings for downsizing volumes",
    resource_type="ebs",
    handler=handle_ebs_pricing,
    parameters={"full_savings": False, "savings_ratio": 0.5},
))

pricing_registry.register(PricingMetadata(
    check_id="ebs_iops_overprovisioned_volume",
    name="EBS IOPS Overprovisioned Pricing",
    description="Cost savings for reducing IOPS",
    resource_type="ebs",
    handler=handle_ebs_pricing,
    parameters={"full_savings": False, "savings_ratio": 0.3},
))

pricing_registry.register(PricingMetadata(
    check_id="ebs_gp2_volumes_convertible_to_gp3",
    name="EBS gp2 to gp3 Conversion Pricing",
    description="Cost savings for migrating gp2 to gp3",
    resource_type="ebs",
    handler=handle_ebs_pricing,
    parameters={"full_savings": False, "savings_ratio": 0.2},
))

pricing_registry.register(PricingMetadata(
    check_id="ebs_underutilized_provisioned_iops",
    name="EBS Underutilized Provisioned IOPS Pricing",
    description="Cost savings for reducing provisioned IOPS",
    resource_type="ebs",
    handler=handle_ebs_pricing,
    parameters={"full_savings": False, "savings_ratio": 0.3},
))

pricing_registry.register(PricingMetadata(
    check_id="ebs_large_volumes_low_utilization",
    name="EBS Large Volumes Low Utilization Pricing",
    description="Cost savings for downsizing large volumes",
    resource_type="ebs",
    handler=handle_ebs_pricing,
    parameters={"full_savings": False, "savings_ratio": 0.5},
))

# Snapshot pricing
pricing_registry.register(PricingMetadata(
    check_id="snapshot_old_snapshots",
    name="Old Snapshots Pricing",
    description="Full cost savings for deleting old snapshots",
    resource_type="snapshot",
    handler=handle_snapshot_pricing,
    parameters={"full_savings": True},
))

# S3 pricing (all s3_* checks use same handler)
s3_checks = [
    ("s3_no_archival_policy", "S3 No Archival Policy Pricing"),
    ("s3_no_lifecycle_policy", "S3 No Lifecycle Policy Pricing"),
    ("s3_no_expiration_policy", "S3 No Expiration Policy Pricing"),
    ("s3_log_buckets_without_expiration_policy", "S3 Log Buckets Pricing"),
    ("s3_no_noncurrent_expiration", "S3 No Noncurrent Expiration Pricing"),
    ("s3_no_noncurrent_version_transition", "S3 No Noncurrent Transition Pricing"),
    ("s3_no_mpu_policy", "S3 No MPU Policy Pricing"),
    ("s3_no_delete_marker_cleanup", "S3 No Delete Marker Cleanup Pricing"),
    ("s3_no_delete_marker_expiration", "S3 No Delete Marker Expiration Pricing"),
    ("s3_logging_enabled_without_lifecycle_policy", "S3 Logging Lifecycle Pricing"),
    ("s3_logging_enabled", "S3 Logging Enabled Pricing"),
    ("s3_inventory_enabled_without_lifecycle_policy", "S3 Inventory Lifecycle Pricing"),
    ("s3_inventory_enabled", "S3 Inventory Enabled Pricing"),
    ("s3_replication_destination_bucket_unused", "S3 Replication Dest Pricing"),
    ("s3_replication_enabled", "S3 Replication Enabled Pricing"),
    ("athena_query_results_bucket_no_lifecycle_policy", "Athena Query Results Bucket Lifecycle Pricing"),
]

for check_id, name in s3_checks:
    pricing_registry.register(PricingMetadata(
        check_id=check_id,
        name=name,
        description=f"Cost savings for {check_id.replace('s3_', '').replace('_', ' ')}",
        resource_type="s3",
        handler=handle_s3_pricing,
    ))

# EFS pricing
pricing_registry.register(PricingMetadata(
    check_id="efs_unused_file_systems",
    name="EFS Unused File Systems Pricing",
    description="Full cost savings for deleting unused file systems",
    resource_type="efs",
    handler=handle_efs_pricing,
    parameters={"full_savings": True},
))

pricing_registry.register(PricingMetadata(
    check_id="efs_no_lifecycle_policy",
    name="EFS No Lifecycle Policy Pricing",
    description="Cost savings for adding IA lifecycle policy",
    resource_type="efs",
    handler=handle_efs_pricing,
    parameters={"full_savings": False, "savings_ratio": 0.85},
))

pricing_registry.register(PricingMetadata(
    check_id="efs_wrong_performance_mode",
    name="EFS Wrong Performance Mode Pricing",
    description="Cost savings for optimizing performance mode",
    resource_type="efs",
    handler=handle_efs_pricing,
    parameters={"full_savings": False, "savings_ratio": 0.3},
))

pricing_registry.register(PricingMetadata(
    check_id="efs_wrong_throughput_mode",
    name="EFS Wrong Throughput Mode Pricing",
    description="Cost savings for optimizing throughput mode",
    resource_type="efs",
    handler=handle_efs_pricing,
    parameters={"full_savings": False, "savings_ratio": 0.3},
))

# ECS pricing
pricing_registry.register(PricingMetadata(
    check_id="ecs_overprovisioned_task_cpu_memory_reservations",
    name="ECS Overprovisioned Task Pricing",
    description="Cost savings for rightsizing ECS tasks",
    resource_type="ecs",
    handler=handle_ecs_pricing,
    parameters={"savings_ratio": 0.3},
))

pricing_registry.register(PricingMetadata(
    check_id="ecs_services_desired_count_zero",
    name="ECS Services Desired Count Zero Pricing",
    description="Cost savings for cleanup of idle services",
    resource_type="ecs",
    handler=handle_ecs_pricing,
    parameters={"savings_ratio": 1.0},
))

pricing_registry.register(PricingMetadata(
    check_id="ecs_idle_clusters_no_active_services_tasks",
    name="ECS Idle Clusters Pricing",
    description="Cost savings for deleting idle clusters",
    resource_type="ecs",
    handler=handle_ecs_pricing,
    parameters={"savings_ratio": 1.0},
))


__all__ = ["pricing_registry", "PricingMetadata"]
