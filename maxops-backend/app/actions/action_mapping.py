"""Centralized action label -> action key mapping for checks routes."""
from __future__ import annotations

from typing import Optional


NORMALIZED_ACTION_MAP = {
    "install cloudwatch agent": "install_cloudwatch_agent",
    "stop": "stop",
    "terminate": "terminate",
    "terminate with snapshot": "terminate_with_snapshot",
    "terminate but leave the volume": "terminate_leave_volume",
    "snapshot and terminate": "snapshot_and_terminate",
    "migrate to graviton": "migrate_to_graviton",
    "change instance family": "migrate_to_graviton",
    "rightsize instance": "rightsize",
    "schedule stop/start": "schedule_off_hours",
    "schedule off-hours shutdown": "schedule_off_hours",
    "use provisioned capacity": "use_provisioned_capacity",
    "enable autoscaling": "enable_autoscaling",
    "delete gsi": "delete_gsi",
    "reduce rcu": "reduce_rcu",
    "reduce wcu": "reduce_wcu",
    "delete table": "delete_table",
    "backup and delete table": "backup_and_delete_table",
    "switch to on-demand billing": "switch_to_on_demand_billing",
    "downsize": "elasticache_downsize",
    "delete instance": "elasticache_delete",
    "upgrade to valkey": "elasticache_upgrade_valkey",
    "migrate to graviton node type": "elasticache_migrate_graviton",
    "migrate rds to graviton": "rds_migrate_graviton",
    "release unused eips": "release_unused_elastic_ip",
    "release unused elastic ips": "release_unused_elastic_ip",
    "downsize volume": "ebs_downsize_volume",
    "reduce provisioned iops": "ebs_reduce_iops",
    "modify volume type to gp3; tune iops/throughput": "ebs_downsize_volume",
    "reduce provisioned iops or migrate to gp3": "ebs_reduce_iops",
    "downsize volume; consider filesystem resize + snapshot/restore": "ebs_downsize_volume",
    "lifecycle policy": "ebs_lifecycle_policy",
    "disable logging report and delete the log bucket": "disable_logging_and_delete_log_bucket",
    "disable inventory report and delete the log bucket": "disable_inventory_and_delete_log_bucket",
    "add policy - log retention lifecycle": "add_log_retention_lifecycle",
    "add policy - add archival transition": "add_archival_transition",
    "add policy - enable delete-marker cleanup": "enable_delete_marker_cleanup",
    "add policy - add expiration for old objects": "add_expiration_policy",
    "add lifecycle policy": "add_lifecycle_policy",
    "add policy - abort incomplete mpus": "add_abort_mpu_policy",
    "add policy - expire noncurrent versions": "add_noncurrent_expiration",
    "add policy - add noncurrent archival transition": "add_noncurrent_archival_transition",
    "disable replication and delete the replicate bucket": "disable_replication_and_delete_bucket",
    "delete snapshot": "delete_snapshot",
    "delete file system": "delete_file_system",
    "modify performance mode": "modify_performance_mode",
    "modify throughput mode": "modify_throughput_mode",
    "update memory configuration": "update_memory_configuration",
    "update memory": "update_memory",
    "rightsize memory using duration vs memory analysis": "update_memory_configuration",
    "reduce provisioned concurrency": "reduce_provisioned_concurrency",
    "update provisioned concurrency": "update_provisioned_concurrency",
    "reduce/disable provisioned concurrency": "reduce_provisioned_concurrency",
    "reduce log verbosity and set retention": "reduce_log_verbosity_and_retention",
    "set log retention": "set_log_retention",
    "reduce log verbosity; set retention": "reduce_log_verbosity_and_retention",
    "delete service; clean up alb/tg/log groups/roles": "ecs_delete_service",
    "rightsize task definition based on utilization": "ecs_rightsize_task_definition",
    "delete cluster after verifying no dependencies": "ecs_delete_cluster",
    "disable alarm actions": "cloudwatch_disable_alarm_actions",
    "tune alarm": "cloudwatch_tune_alarm",
    "consolidate duplicate alarms": "cloudwatch_consolidate_duplicate_alarms",
    "disable flow logs": "vpc_flow_logs_disabling",
    "disable flow logs and set bucket expiration": "vpc_flow_logs_disabling_with_bucket_expiration",
    "disable flow logs and delete buckets": "vpc_flow_logs_disabling_with_bucket_deletion",
}


def resolve_action_key(raw_action: str, check_id: Optional[str] = None) -> str:
    """Resolve UI action label to backend action key."""
    if raw_action == "migrate to graviton" and check_id == "rds_non_graviton_instance_class":
        return "rds_migrate_graviton"
    action_key = NORMALIZED_ACTION_MAP.get(raw_action, raw_action)
    # "Add lifecycle policy" label is shared by S3 and EFS checks.
    if raw_action == "add lifecycle policy":
        if check_id and check_id.startswith("efs_"):
            return "add_lifecycle_policy"
        return "add_basic_lifecycle_policy"
    return action_key

