import React from 'react';
import { cn } from '@/utils/helpers';
import { awsResourceIconMap } from '@/components/icons/awsResourceIcons.generated';

type ResourceTypeIconProps = {
  resourceType?: string;
  className?: string;
  size?: number;
};

const aliasMap: Record<string, string> = {
  asg: 'asg',
  auto_scaling_group: 'asg',
  autoscaling_group: 'asg',
  'auto-scaling-group': 'asg',
  athena: 'athena',
  athena_workgroup: 'athena',
  athena_query: 'athena',
  aurora: 'aurora',
  aurora_cluster: 'aurora',
  aurora_instance: 'aurora',
  aurora_mysql: 'aurora',
  aurora_postgresql: 'aurora',
  aurora_cluster_snapshot: 'aurora-cluster-snapshot',
  aurora_snapshot: 'aurora-cluster-snapshot',
  aurora_global_cluster: 'aurora-global-cluster',
  aurora_global: 'aurora-global-cluster',
  cloudwatch_alarm: 'cloudwatch',
  cloudwatch_log_group: 'cloudwatch',
  dynamodb_table: 'dynamodb',
  dynamodb_gsi: 'dynamodb',
  efs_file_system: 'efs',
  ecs_service: 'ecs',
  ecs_cluster: 'ecs',
  elasticache_replication_group: 'elasticache',
  elasticache_cluster: 'elasticache',
  emr_cluster: 'emr',
  emr_instance_group: 'emr',
  emr_instance_groups: 'emr',
  emr_instance_fleet: 'emr',
  emr_instance_fleets: 'emr',
  firehose_delivery_stream: 'firehose',
  glue_job: 'glue',
  glue_table: 'glue',
  kinesis_stream: 'kinesis',
  lambda_function: 'lambda',
  opensearch_domain: 'opensearch',
  opensearch_index: 'opensearch-index',
  rds_instance: 'rds',
  redshift_cluster: 'redshift',
  redshift_snapshot: 'redshift-snapshot',
  sagemaker: 'sagemaker',
  sagemaker_notebook: 'sagemaker',
  sagemaker_endpoint: 'sagemaker',
  sagemaker_training_job: 'sagemaker',
  vpc_endpoint: 'vpc',
  vpc_endpoints: 'vpc',
  vpc_flow_log: 'vpc',
  vpc_flow_logs: 'vpc',
};

const resolveResourceTypeKey = (resourceType?: string): string => {
  const normalized = (resourceType || '').trim().toLowerCase();
  if (!normalized) return '';
  return aliasMap[normalized] || normalized;
};

export const ResourceTypeIcon: React.FC<ResourceTypeIconProps> = ({
  resourceType,
  className,
  size = 18,
}) => {
  const key = resolveResourceTypeKey(resourceType);
  const IconComponent = key
    ? awsResourceIconMap[key as keyof typeof awsResourceIconMap]
    : undefined;

  if (!IconComponent) {
    return null;
  }

  return (
    <span
      aria-hidden="true"
      className={cn(
        'inline-flex shrink-0 items-center justify-center overflow-hidden rounded-lg bg-primary-600 text-white dark:bg-primary-500',
        className
      )}
      style={{ width: size, height: size }}
    >
      <span
        className="inline-flex items-center justify-center"
        style={{
          width: Math.round(size * 0.72),
          height: Math.round(size * 0.72),
        }}
      >
        <IconComponent
          width="100%"
          height="100%"
          focusable="false"
          className="block text-white"
        />
      </span>
    </span>
  );
};
