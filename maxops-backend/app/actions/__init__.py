"""Register all action handlers."""
from __future__ import annotations

from app.actions.registry import action_registry, ActionMetadata
from app.actions.handlers_asg import handle_asg_rightsize
from app.actions.handlers_dynamodb import (
    handle_backup_and_delete_table,
    handle_delete_gsi,
    handle_delete_table,
    handle_enable_autoscaling,
    handle_reduce_rcu,
    handle_reduce_wcu,
    handle_switch_to_on_demand_billing,
    handle_use_provisioned_capacity,
)
from app.actions.handlers_ebs import (
    handle_ebs_downsize_volume,
    handle_ebs_lifecycle_policy,
    handle_ebs_reduce_iops,
    handle_snapshot_and_terminate,
)
from app.actions.handlers_ec2 import (
    handle_ec2_migrate_to_graviton,
    handle_ec2_rightsize,
    handle_ec2_schedule_off_hours,
    handle_ec2_stop,
    handle_ec2_terminate,
    handle_ec2_terminate_leave_volume,
    handle_ec2_terminate_with_snapshot,
    handle_install_cloudwatch_agent,
    handle_release_unused_elastic_ip,
)
from app.actions.handlers_ecs import (
    handle_ecs_delete_cluster,
    handle_ecs_delete_service,
    handle_ecs_rightsize_task_definition,
)
from app.actions.handlers_efs import (
    handle_efs_add_lifecycle_policy,
    handle_efs_delete_file_system,
    handle_efs_modify_performance_mode,
    handle_efs_modify_throughput_mode,
)
from app.actions.handlers_elasticache import (
    handle_elasticache_delete,
    handle_elasticache_downsize,
    handle_elasticache_migrate_graviton,
    handle_elasticache_upgrade_valkey,
)
from app.actions.handlers_lambda import (
    handle_reduce_provisioned_concurrency,
    handle_set_log_retention,
    handle_update_memory_configuration,
)
from app.actions.handlers_s3 import (
    handle_add_abort_mpu_policy,
    handle_add_archival_transition,
    handle_add_basic_lifecycle_policy,
    handle_add_expiration_policy,
    handle_add_log_retention_lifecycle,
    handle_add_noncurrent_archival_transition,
    handle_add_noncurrent_expiration,
    handle_disable_inventory_and_delete_log_bucket,
    handle_disable_logging_and_delete_log_bucket,
    handle_disable_replication_and_delete_bucket,
    handle_enable_delete_marker_cleanup,
)
from app.actions.handlers_snapshot import handle_delete_snapshot
from app.actions.handlers_cloudwatch import (
    handle_cloudwatch_consolidate_duplicate_alarms,
    handle_cloudwatch_delete_alarm,
    handle_cloudwatch_delete_log_group,
    handle_cloudwatch_disable_alarm_actions,
    handle_cloudwatch_set_log_group_retention,
    handle_cloudwatch_tune_alarm,
)
from app.actions.handlers_glue import (
    handle_glue_set_workers_count,
    handle_glue_upgrade_version,
)
from app.actions.handlers_redshift import (
    handle_redshift_delete_snapshot,
    handle_redshift_reduce_snapshot_retention,
    handle_redshift_rightsize_cluster,
)
from app.actions.handlers_vpc import (
    handle_dynamodb_vpc_endpoint,
    handle_s3_vpc_endpoint,
    handle_vpc_flow_logs_disabling,
    handle_vpc_flow_logs_disabling_with_bucket_deletion,
    handle_vpc_flow_logs_disabling_with_bucket_expiration,
)
from app.actions.handlers_rds import handle_rds_migrate_graviton
from app.actions.handlers_sagemaker import (
    handle_sagemaker_delete_endpoint,
    handle_sagemaker_stop_notebook,
)

action_registry.register(ActionMetadata(
    action_key="stop_notebook",
    name="Stop SageMaker Notebook",
    description="Stop an InService SageMaker notebook instance",
    resource_types=["sagemaker"],
    handler=handle_sagemaker_stop_notebook,
))

action_registry.register(ActionMetadata(
    action_key="delete_endpoint",
    name="Delete SageMaker Endpoint",
    description="Delete a SageMaker endpoint while retaining shared configuration and model resources",
    resource_types=["sagemaker"],
    handler=handle_sagemaker_delete_endpoint,
))


# EC2 actions
# ASG actions
action_registry.register(ActionMetadata(
    action_key="asg_rightsize",
    name="Rightsize Auto Scaling Group",
    description="Change an Auto Scaling group's min/desired/max capacity",
    resource_types=["asg"],
    handler=handle_asg_rightsize,
))

action_registry.register(ActionMetadata(
    action_key="install_cloudwatch_agent",
    name="Install CloudWatch Agent",
    description="Install and configure CloudWatch agent on EC2 instance",
    resource_types=["ec2"],
    handler=handle_install_cloudwatch_agent,
))

action_registry.register(ActionMetadata(
    action_key="stop",
    name="Stop Instance",
    description="Stop EC2 instance or RDS database",
    resource_types=["ec2", "rds"],
    handler=handle_ec2_stop,
))

action_registry.register(ActionMetadata(
    action_key="terminate",
    name="Terminate Instance",
    description="Terminate EC2 instance",
    resource_types=["ec2"],
    handler=handle_ec2_terminate,
))

action_registry.register(ActionMetadata(
    action_key="terminate_with_snapshot",
    name="Terminate with Snapshot",
    description="Create snapshot before terminating EC2 instance",
    resource_types=["ec2"],
    handler=handle_ec2_terminate_with_snapshot,
))

action_registry.register(ActionMetadata(
    action_key="terminate_leave_volume",
    name="Terminate but Leave Volume",
    description="Terminate EC2 instance but keep EBS volume",
    resource_types=["ec2"],
    handler=handle_ec2_terminate_leave_volume,
))

action_registry.register(ActionMetadata(
    action_key="migrate_to_graviton",
    name="Migrate to Graviton",
    description="Migrate EC2 instance to Graviton-based instance type",
    resource_types=["ec2"],
    handler=handle_ec2_migrate_to_graviton,
))

action_registry.register(ActionMetadata(
    action_key="rightsize",
    name="Rightsize Instance",
    description="Change an EC2 instance to a smaller instance type",
    resource_types=["ec2"],
    handler=handle_ec2_rightsize,
))

action_registry.register(ActionMetadata(
    action_key="schedule_off_hours",
    name="Schedule Off-Hours Shutdown",
    description="Create paired EC2 stop/start schedules for off-hours savings",
    resource_types=["ec2"],
    handler=handle_ec2_schedule_off_hours,
))

action_registry.register(ActionMetadata(
    action_key="release_unused_elastic_ip",
    name="Release Unused Elastic IP",
    description="Release an explicitly confirmed, unassociated Elastic IP allocation",
    resource_types=["elastic_ip"],
    handler=handle_release_unused_elastic_ip,
))

# EBS actions
action_registry.register(ActionMetadata(
    action_key="snapshot_and_terminate",
    name="Snapshot and Terminate",
    description="Create snapshot and delete unattached EBS volume",
    resource_types=["ebs"],
    handler=handle_snapshot_and_terminate,
))

action_registry.register(ActionMetadata(
    action_key="ebs_downsize_volume",
    name="Downsize Volume",
    description="Reduce EBS volume size or change type",
    resource_types=["ebs"],
    handler=handle_ebs_downsize_volume,
))

action_registry.register(ActionMetadata(
    action_key="ebs_reduce_iops",
    name="Reduce Provisioned IOPS",
    description="Reduce provisioned IOPS for EBS volume",
    resource_types=["ebs"],
    handler=handle_ebs_reduce_iops,
))

action_registry.register(ActionMetadata(
    action_key="ebs_lifecycle_policy",
    name="Create Lifecycle Policy",
    description="Create DLM lifecycle policy for EBS snapshots",
    resource_types=["ebs"],
    handler=handle_ebs_lifecycle_policy,
))

# Lambda actions
action_registry.register(ActionMetadata(
    action_key="update_memory_configuration",
    name="Update Memory Configuration",
    description="Adjust Lambda function memory allocation",
    resource_types=["lambda"],
    handler=handle_update_memory_configuration,
))

action_registry.register(ActionMetadata(
    action_key="update_memory",
    name="Update Memory",
    description="Adjust Lambda function memory allocation (alias)",
    resource_types=["lambda"],
    handler=handle_update_memory_configuration,
))

action_registry.register(ActionMetadata(
    action_key="reduce_provisioned_concurrency",
    name="Reduce Provisioned Concurrency",
    description="Reduce or disable Lambda provisioned concurrency",
    resource_types=["lambda"],
    handler=handle_reduce_provisioned_concurrency,
))

action_registry.register(ActionMetadata(
    action_key="update_provisioned_concurrency",
    name="Update Provisioned Concurrency",
    description="Update Lambda provisioned concurrency (alias)",
    resource_types=["lambda"],
    handler=handle_reduce_provisioned_concurrency,
))

action_registry.register(ActionMetadata(
    action_key="set_log_retention",
    name="Set Log Retention",
    description="Configure CloudWatch log retention for Lambda",
    resource_types=["lambda"],
    handler=handle_set_log_retention,
))

action_registry.register(ActionMetadata(
    action_key="reduce_log_verbosity_and_retention",
    name="Reduce Log Verbosity and Retention",
    description="Optimize Lambda logging configuration (alias)",
    resource_types=["lambda"],
    handler=handle_set_log_retention,
))

# Snapshot actions
action_registry.register(ActionMetadata(
    action_key="delete_snapshot",
    name="Delete Snapshot",
    description="Delete old EBS snapshot",
    resource_types=["snapshot"],
    handler=handle_delete_snapshot,
))

# ECS actions
action_registry.register(ActionMetadata(
    action_key="ecs_delete_service",
    name="Delete ECS Service",
    description="Delete ECS service and cleanup resources",
    resource_types=["ecs"],
    handler=handle_ecs_delete_service,
))

action_registry.register(ActionMetadata(
    action_key="ecs_rightsize_task_definition",
    name="Rightsize Task Definition",
    description="Optimize ECS task CPU and memory allocation",
    resource_types=["ecs"],
    handler=handle_ecs_rightsize_task_definition,
))

action_registry.register(ActionMetadata(
    action_key="ecs_delete_cluster",
    name="Delete ECS Cluster",
    description="Delete idle ECS cluster",
    resource_types=["ecs"],
    handler=handle_ecs_delete_cluster,
))

# DynamoDB actions
action_registry.register(ActionMetadata(
    action_key="use_provisioned_capacity",
    name="Use Provisioned Capacity",
    description="Switch DynamoDB table to provisioned capacity mode",
    resource_types=["dynamodb"],
    handler=handle_use_provisioned_capacity,
))

action_registry.register(ActionMetadata(
    action_key="enable_autoscaling",
    name="Enable Autoscaling",
    description="Enable DynamoDB table autoscaling",
    resource_types=["dynamodb"],
    handler=handle_enable_autoscaling,
))

action_registry.register(ActionMetadata(
    action_key="delete_gsi",
    name="Delete GSI",
    description="Delete unused Global Secondary Index",
    resource_types=["dynamodb"],
    handler=handle_delete_gsi,
))

action_registry.register(ActionMetadata(
    action_key="reduce_rcu",
    name="Reduce RCU",
    description="Reduce DynamoDB Read Capacity Units",
    resource_types=["dynamodb"],
    handler=handle_reduce_rcu,
))

action_registry.register(ActionMetadata(
    action_key="reduce_wcu",
    name="Reduce WCU",
    description="Reduce DynamoDB Write Capacity Units",
    resource_types=["dynamodb"],
    handler=handle_reduce_wcu,
))

action_registry.register(ActionMetadata(
    action_key="delete_table",
    name="Delete Table",
    description="Delete unused DynamoDB table",
    resource_types=["dynamodb"],
    handler=handle_delete_table,
))

action_registry.register(ActionMetadata(
    action_key="backup_and_delete_table",
    name="Backup and Delete Table",
    description="Create backup before deleting DynamoDB table",
    resource_types=["dynamodb"],
    handler=handle_backup_and_delete_table,
))

action_registry.register(ActionMetadata(
    action_key="switch_to_on_demand_billing",
    name="Switch to On-Demand Billing",
    description="Switch DynamoDB table to on-demand billing mode",
    resource_types=["dynamodb"],
    handler=handle_switch_to_on_demand_billing,
))

# ElastiCache actions
action_registry.register(ActionMetadata(
    action_key="elasticache_delete",
    name="Delete ElastiCache Instance",
    description="Delete ElastiCache cluster or replication group",
    resource_types=["elasticache"],
    handler=handle_elasticache_delete,
))

action_registry.register(ActionMetadata(
    action_key="elasticache_downsize",
    name="Downsize ElastiCache",
    description="Reduce ElastiCache node type or count",
    resource_types=["elasticache"],
    handler=handle_elasticache_downsize,
))

action_registry.register(ActionMetadata(
    action_key="elasticache_migrate_graviton",
    name="Migrate to Graviton",
    description="Migrate ElastiCache to Graviton node type",
    resource_types=["elasticache"],
    handler=handle_elasticache_migrate_graviton,
))

action_registry.register(ActionMetadata(
    action_key="elasticache_upgrade_valkey",
    name="Upgrade to Valkey",
    description="Upgrade ElastiCache from Redis to Valkey",
    resource_types=["elasticache"],
    handler=handle_elasticache_upgrade_valkey,
))

# RDS actions
action_registry.register(ActionMetadata(
    action_key="rds_migrate_graviton",
    name="Migrate RDS to Graviton",
    description="Change an RDS DB instance to an explicitly selected Graviton class",
    resource_types=["rds", "rds_instance"],
    handler=handle_rds_migrate_graviton,
))

# S3 actions
action_registry.register(ActionMetadata(
    action_key="disable_logging_and_delete_log_bucket",
    name="Disable Logging and Delete Log Bucket",
    description="Disable S3 access logging and cleanup log bucket",
    resource_types=["s3"],
    handler=handle_disable_logging_and_delete_log_bucket,
))

action_registry.register(ActionMetadata(
    action_key="disable_inventory_and_delete_log_bucket",
    name="Disable Inventory and Delete Log Bucket",
    description="Disable S3 inventory and cleanup destination bucket",
    resource_types=["s3"],
    handler=handle_disable_inventory_and_delete_log_bucket,
))

action_registry.register(ActionMetadata(
    action_key="add_log_retention_lifecycle",
    name="Add Log Retention Lifecycle",
    description="Add lifecycle policy for log bucket retention",
    resource_types=["s3"],
    handler=handle_add_log_retention_lifecycle,
))

action_registry.register(ActionMetadata(
    action_key="add_archival_transition",
    name="Add Archival Transition",
    description="Add lifecycle rule to transition objects to Glacier",
    resource_types=["s3"],
    handler=handle_add_archival_transition,
))

action_registry.register(ActionMetadata(
    action_key="enable_delete_marker_cleanup",
    name="Enable Delete Marker Cleanup",
    description="Add lifecycle rule to cleanup expired delete markers",
    resource_types=["s3"],
    handler=handle_enable_delete_marker_cleanup,
))

action_registry.register(ActionMetadata(
    action_key="add_expiration_policy",
    name="Add Expiration Policy",
    description="Add lifecycle rule to expire old objects",
    resource_types=["s3"],
    handler=handle_add_expiration_policy,
))

action_registry.register(ActionMetadata(
    action_key="add_basic_lifecycle_policy",
    name="Add Basic Lifecycle Policy",
    description="Add comprehensive S3 lifecycle policy",
    resource_types=["s3"],
    handler=handle_add_basic_lifecycle_policy,
))

action_registry.register(ActionMetadata(
    action_key="add_abort_mpu_policy",
    name="Add Abort MPU Policy",
    description="Add rule to abort incomplete multipart uploads",
    resource_types=["s3"],
    handler=handle_add_abort_mpu_policy,
))

action_registry.register(ActionMetadata(
    action_key="add_noncurrent_expiration",
    name="Add Noncurrent Expiration",
    description="Add rule to expire old noncurrent versions",
    resource_types=["s3"],
    handler=handle_add_noncurrent_expiration,
))

action_registry.register(ActionMetadata(
    action_key="add_noncurrent_archival_transition",
    name="Add Noncurrent Archival Transition",
    description="Add rule to archive old noncurrent versions",
    resource_types=["s3"],
    handler=handle_add_noncurrent_archival_transition,
))

action_registry.register(ActionMetadata(
    action_key="disable_replication_and_delete_bucket",
    name="Disable Replication and Delete Bucket",
    description="Disable S3 replication and cleanup destination bucket",
    resource_types=["s3"],
    handler=handle_disable_replication_and_delete_bucket,
))

# EFS actions
action_registry.register(ActionMetadata(
    action_key="delete_file_system",
    name="Delete File System",
    description="Delete unused EFS file system",
    resource_types=["efs"],
    handler=handle_efs_delete_file_system,
))

action_registry.register(ActionMetadata(
    action_key="efs_delete",
    name="Delete EFS",
    description="Delete unused EFS file system (alias)",
    resource_types=["efs"],
    handler=handle_efs_delete_file_system,
))

action_registry.register(ActionMetadata(
    action_key="modify_performance_mode",
    name="Modify Performance Mode",
    description="Change EFS performance mode using DataSync",
    resource_types=["efs"],
    handler=handle_efs_modify_performance_mode,
))

action_registry.register(ActionMetadata(
    action_key="efs_modify_performance_mode",
    name="Modify EFS Performance Mode",
    description="Change EFS performance mode (alias)",
    resource_types=["efs"],
    handler=handle_efs_modify_performance_mode,
))

action_registry.register(ActionMetadata(
    action_key="modify_throughput_mode",
    name="Modify Throughput Mode",
    description="Change EFS throughput mode",
    resource_types=["efs"],
    handler=handle_efs_modify_throughput_mode,
))

action_registry.register(ActionMetadata(
    action_key="efs_modify_throughput_mode",
    name="Modify EFS Throughput Mode",
    description="Change EFS throughput mode (alias)",
    resource_types=["efs"],
    handler=handle_efs_modify_throughput_mode,
))

action_registry.register(ActionMetadata(
    action_key="add_lifecycle_policy",
    name="Add Lifecycle Policy",
    description="Add EFS lifecycle policy for IA transition",
    resource_types=["efs"],
    handler=handle_efs_add_lifecycle_policy,
))

action_registry.register(ActionMetadata(
    action_key="efs_add_lifecycle_policy",
    name="Add EFS Lifecycle Policy",
    description="Add EFS lifecycle policy (alias)",
    resource_types=["efs"],
    handler=handle_efs_add_lifecycle_policy,
))

# CloudWatch actions
action_registry.register(ActionMetadata(
    action_key="cloudwatch_delete_alarm",
    name="Delete CloudWatch Alarm",
    description="Delete one or more CloudWatch alarms",
    resource_types=["cloudwatch"],
    handler=handle_cloudwatch_delete_alarm,
))

action_registry.register(ActionMetadata(
    action_key="cloudwatch_delete_log_group",
    name="Delete CloudWatch Log Group",
    description="Delete a CloudWatch Logs log group",
    resource_types=["cloudwatch"],
    handler=handle_cloudwatch_delete_log_group,
))

action_registry.register(ActionMetadata(
    action_key="cloudwatch_set_log_group_retention",
    name="Set CloudWatch Log Group Retention",
    description="Set retention policy for a CloudWatch Logs log group",
    resource_types=["cloudwatch"],
    handler=handle_cloudwatch_set_log_group_retention,
))

action_registry.register(ActionMetadata(
    action_key="cloudwatch_disable_alarm_actions",
    name="Disable CloudWatch Alarm Actions",
    description="Disable actions while preserving the alarm and its history",
    resource_types=["cloudwatch"],
    handler=handle_cloudwatch_disable_alarm_actions,
))

action_registry.register(ActionMetadata(
    action_key="cloudwatch_tune_alarm",
    name="Tune CloudWatch Alarm",
    description="Update explicitly selected fields on a simple metric alarm",
    resource_types=["cloudwatch"],
    handler=handle_cloudwatch_tune_alarm,
))

action_registry.register(ActionMetadata(
    action_key="cloudwatch_consolidate_duplicate_alarms",
    name="Consolidate Duplicate CloudWatch Alarms",
    description="Keep one alarm and delete explicitly selected matching duplicates",
    resource_types=["cloudwatch"],
    handler=handle_cloudwatch_consolidate_duplicate_alarms,
))

# Glue actions
action_registry.register(ActionMetadata(
    action_key="glue_set_workers_count",
    name="Set Glue Workers Count",
    description="Update AWS Glue job worker count",
    resource_types=["glue"],
    handler=handle_glue_set_workers_count,
))

action_registry.register(ActionMetadata(
    action_key="glue_upgrade_version",
    name="Upgrade Glue Version",
    description="Update AWS Glue job to a specified Glue version",
    resource_types=["glue"],
    handler=handle_glue_upgrade_version,
))

# Redshift actions
action_registry.register(ActionMetadata(
    action_key="redshift_delete_snapshot",
    name="Delete Redshift Snapshot",
    description="Delete Redshift snapshots by identifier(s)",
    resource_types=["redshift"],
    handler=handle_redshift_delete_snapshot,
))

action_registry.register(ActionMetadata(
    action_key="redshift_reduce_snapshot_retention",
    name="Reduce Redshift Snapshot Retention",
    description="Reduce automated snapshot retention period for a Redshift cluster",
    resource_types=["redshift"],
    handler=handle_redshift_reduce_snapshot_retention,
))

action_registry.register(ActionMetadata(
    action_key="redshift_rightsize_cluster",
    name="Rightsize Redshift Cluster",
    description="Right-size a Redshift cluster by changing node type/count/cluster type",
    resource_types=["redshift"],
    handler=handle_redshift_rightsize_cluster,
))

# VPC actions
action_registry.register(ActionMetadata(
    action_key="dynamodb_vpc_endpoint",
    name="Create DynamoDB VPC Endpoint",
    description="Create DynamoDB VPC endpoint for a VPC",
    resource_types=["vpc"],
    handler=handle_dynamodb_vpc_endpoint,
))

action_registry.register(ActionMetadata(
    action_key="s3_vpc_endpoint",
    name="Create S3 VPC Endpoint",
    description="Create S3 VPC endpoint for a VPC",
    resource_types=["vpc"],
    handler=handle_s3_vpc_endpoint,
))

action_registry.register(ActionMetadata(
    action_key="vpc_flow_logs_disabling",
    name="Disable VPC Flow Logs",
    description="Disable VPC flow logs by deleting flow log resources",
    resource_types=["vpc"],
    handler=handle_vpc_flow_logs_disabling,
))

action_registry.register(ActionMetadata(
    action_key="vpc_flow_logs_disabling_with_bucket_expiration",
    name="Disable VPC Flow Logs with Bucket Expiration",
    description="Disable VPC flow logs and set S3 log expiration policy",
    resource_types=["vpc"],
    handler=handle_vpc_flow_logs_disabling_with_bucket_expiration,
))

action_registry.register(ActionMetadata(
    action_key="vpc_flow_logs_disabling_with_bucket_deletion",
    name="Disable VPC Flow Logs and Delete Buckets",
    description="Disable selected flow logs and delete explicitly approved S3 destinations",
    resource_types=["vpc"],
    handler=handle_vpc_flow_logs_disabling_with_bucket_deletion,
))

from . import s3_optimizer_advisory  # noqa: F401
