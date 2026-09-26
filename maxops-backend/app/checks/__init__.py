"""AWS resource checks package."""
from app.checks.registry import check_registry, CheckMetadata

# Import all check modules to trigger auto-registration
# EC2 checks
from app.checks.ec2 import (
    graviton_candidate,
    gpu_underutilized,
    idle_instances,
    unused_elastic_ips,
    unused_instances,
)

# SageMaker V1 checks
from app.checks import sagemaker  # noqa: F401

# RDS checks
from app.checks.rds import idle_databases, non_graviton

# EBS checks
from app.checks.ebs import (
    unattached_volumes,
    underutilized_volume,
    iops_overprovisioned_volume,
    gp2_convertible_to_gp3,
    underutilized_provisioned_iops,
    large_volumes_low_utilization,
)

# Snapshot checks
from app.checks.snapshots import old_snapshots

# CloudWatch checks
from app.checks.cloudwatch import (
    alarms_no_actions,
    alarms_with_duplicates,
    alarms_with_high_volume,
    log_groups_high_volume,
    log_groups_inactive,
    log_groups_no_expiration
)

# DynamoDB checks
from app.checks.dynamodb import (
    best_fit_ondemand,
    best_fit_provisoned,
    gsi_unused,
    underutilized_rcu,
    underutilized_tables,
    underutilized_wcu
)

# ElastiCache checks
from app.checks.elasticache import (
    low_items_count,
    non_graviton as elasticache_non_graviton,
    valkey_compatible
)

# EMR checks
from app.checks.emr import (
    idle_cluster,
    jobs_high_boostrap_time,
    jobs_older_version,
    no_instance_fleet,
    no_spot_task_nodes,
    overprovisoned_cluster,
    single_instance_type_core_task_nodes
)

# Glue checks
from app.checks.glue import jobs_older_version as glue_jobs_older_version
from app.checks.glue import tables_without_partitions
from app.checks.glue import underutilized_dcu

# Aurora checks
from app.checks.aurora import idle_clusters as aurora_idle_clusters
from app.checks.aurora import overprovisioned_instance_class as aurora_overprovisioned_instance_class
from app.checks.aurora import excess_reader_instances as aurora_excess_reader_instances
from app.checks.aurora import non_graviton_instances as aurora_non_graviton_instances
from app.checks.aurora import storage_type_not_io_optimized_candidate as aurora_storage_type_not_io_optimized_candidate
from app.checks.aurora import high_backup_retention_without_need as aurora_high_backup_retention_without_need
from app.checks.aurora import old_manual_snapshots as aurora_old_manual_snapshots
from app.checks.aurora import serverless_v2_min_acu_too_high as aurora_serverless_v2_min_acu_too_high
from app.checks.aurora import provisioned_vs_serverless_mismatch as aurora_provisioned_vs_serverless_mismatch
from app.checks.aurora import cross_region_replication_unused as aurora_cross_region_replication_unused

# OpenSearch checks
from app.checks.opensearch import underutilized_data_nodes as opensearch_underutilized_data_nodes
from app.checks.opensearch import over_replicated_indices as opensearch_over_replicated_indices
from app.checks.opensearch import oversharded_indices as opensearch_oversharded_indices
from app.checks.opensearch import low_storage_utilization as opensearch_low_storage_utilization
from app.checks.opensearch import gp2_to_gp3_candidate as opensearch_gp2_to_gp3_candidate
from app.checks.opensearch import no_ism_rollover_or_retention as opensearch_no_ism_rollover_or_retention
from app.checks.opensearch import old_cold_data_not_tiered as opensearch_old_cold_data_not_tiered
from app.checks.opensearch import dedicated_master_oversized as opensearch_dedicated_master_oversized
from app.checks.opensearch import idle_domains as opensearch_idle_domains
from app.checks.opensearch import outdated_instance_generation as opensearch_outdated_instance_generation

# ASG checks
from app.checks.asg import low_cpu_overprovisioned as asg_low_cpu_overprovisioned
from app.checks.asg import scale_in_never_triggered as asg_scale_in_never_triggered
from app.checks.asg import idle_capacity_high as asg_idle_capacity_high
from app.checks.asg import non_graviton_candidates as asg_non_graviton_candidates
from app.checks.asg import on_demand_heavy_mix as asg_on_demand_heavy_mix
from app.checks.asg import launch_template_old_generation as asg_launch_template_old_generation
from app.checks.asg import scheduled_scaling_mismatch as asg_scheduled_scaling_mismatch
from app.checks.asg import warm_pool_oversized as asg_warm_pool_oversized
from app.checks.asg import low_traffic_with_running_instances as asg_low_traffic_with_running_instances

# Kinesis checks
from app.checks.kinesis import over_sharded_streams as kinesis_over_sharded_streams
from app.checks.kinesis import inactive_streams as kinesis_inactive_streams
from app.checks.kinesis import firehose_no_compression_or_columnar as kinesis_firehose_no_compression_or_columnar
from app.checks.kinesis import firehose_small_files_to_s3 as kinesis_firehose_small_files_to_s3

# Athena checks
from app.checks.athena import older_version as athena_older_version
from app.checks.athena import query_results_bucket_no_policy as athena_query_results_bucket_no_policy
from app.checks.athena import datasources_non_parquet_file_type as athena_datasources_non_parquet_file_type
from app.checks.athena import datasources_low_avg_file_size as athena_datasources_low_avg_file_size

# Redshift checks
from app.checks.redshift import idle_cluster as redshift_idle_cluster
from app.checks.redshift import older_snapshots as redshift_older_snapshots
from app.checks.redshift import overprovioned_cluster as redshift_overprovisioned_cluster

# S3 checks
from app.checks.s3 import (
    access_logging_enabled,
    inventory_enabled,
    logs_bucket_no_expiration,
    no_archival_policies,
    no_delete_marker_expiration,
    no_expiration_set,
    no_life_cycle_policies,
    no_mpu_policy,
    no_noncurrent_expiration,
    no_noncurrent_objects_archival,
    replication_enabled,
    bucket_unused,
    bucket_low_access,
    bucket_retrieval_cost_dominant,
)

# VPC checks
from app.checks.vpc import (
    no_dynamodb_endpoint,
    no_s3_vpc_endpoint,
    vpc_flow_logs_enabled
)

# EFS checks
from app.checks.efs import (
    unused_file_systems,
    wrong_performance_mode,
    wrong_throughput_mode,
    no_lifecycle_policy
)

# Lambda checks
from app.checks.aws_lambda import (
    overprovisioned_memory,
    provisioned_concurrency_low_usage,
    high_log_ingestion
)

# ECS checks
from app.checks.ecs import (
    services_desired_count_zero,
    overprovisioned_task_reservations,
    idle_clusters,
)

__all__ = ['check_registry', 'CheckMetadata']
