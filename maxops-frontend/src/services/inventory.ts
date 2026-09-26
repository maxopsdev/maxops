import apiClient from './api';

export interface ResourceSnoozeState {
  resource_id?: string;
  resource_type?: string;
  snoozed_until?: string | null;
  snooze_days?: number | null;
  reason?: string | null;
  active?: boolean;
  updated_at?: string | null;
}

export interface SnoozeResourceSummary {
  resource_id: string;
  resource_type: string;
  resource_name?: string | null;
  account_id?: string | null;
  region?: string | null;
}

export interface SnoozeResourcesPayload {
  resources: SnoozeResourceSummary[];
  snoozed_until: string;
  reason: string;
}

export interface RemoveSnoozeResourcesPayload {
  resources: SnoozeResourceSummary[];
  reason: string;
}

export interface Ec2OverviewInstance {
  inventory_id: number;
  resource_id: string;
  resource_name?: string | null;
  account_id?: string | null;
  region?: string | null;
  availability_zone?: string | null;
  state?: string | null;
  instance_type?: string | null;
  launch_time?: string | null;
  tags: Record<string, string>;
  metadata: Record<string, any>;
  usage: {
    cpu_utilization: number;
    memory_utilization: number;
    network_in_mb: number;
    network_out_mb: number;
    received_bytes_mb: number;
    sent_bytes_mb: number;
    disk_used_gb: number;
    disk_available_gb: number;
    trends: {
      cpu: {
        latest?: {
          timestamp: string;
          average: number;
          maximum: number;
          p90: number;
          p95: number;
          p99: number;
        } | null;
        points: Array<{
          timestamp: string;
          average: number;
          maximum: number;
          p90: number;
          p95: number;
          p99: number;
        }>;
      };
      memory: {
        latest?: {
          timestamp: string;
          average: number;
          maximum: number;
          p90: number;
          p95: number;
          p99: number;
        } | null;
        points: Array<{
          timestamp: string;
          average: number;
          maximum: number;
          p90: number;
          p95: number;
          p99: number;
        }>;
      };
    };
  };
  maxops: {
    check_id?: string | null;
    finding_type?: string | null;
    severity?: string | null;
    title?: string | null;
    recommended_action?: string | null;
    potential_savings_monthly: number;
    potential_savings_yearly: number;
    status: string;
  };
  snooze?: ResourceSnoozeState | null;
}

export interface Ec2OverviewResponse {
  generated_at: string | null;
  account_id: string | null;
  summary: {
    total_instances: number;
    running_instances: number;
    stopped_instances: number;
    actionable_instances: number;
    healthy_instances: number;
    average_cpu_utilization: number;
    average_memory_utilization: number;
    average_network_in: number;
    average_network_out: number;
    monthly_cost_estimate: number;
    potential_savings_yearly: number;
  };
  dimensions: {
    regions: Array<{ key: string; count: number }>;
    instance_types: Array<{ key: string; count: number }>;
    environments: Array<{ key: string; count: number }>;
  };
  findings_breakdown: Array<{ key: string; count: number }>;
  instances: Ec2OverviewInstance[];
}

export interface S3OverviewBucket {
  inventory_id: number;
  resource_id: string;
  resource_name?: string | null;
  account_id?: string | null;
  region?: string | null;
  state?: string | null;
  creation_date?: string | null;
  tags: Record<string, string>;
  metadata: Record<string, any>;
  storage: {
    bucket_size_gb: number;
    object_count: number;
    estimated_monthly_cost: number;
    storage_class_mix: Record<string, number>;
  };
  posture: {
    versioning_enabled: boolean;
    logging_enabled: boolean;
    inventory_enabled: boolean;
    replication_enabled: boolean;
    lifecycle_enabled: boolean;
  };
  maxops: {
    check_id?: string | null;
    finding_type?: string | null;
    severity?: string | null;
    title?: string | null;
    description?: string | null;
    recommended_action?: string | null;
    potential_savings_monthly: number;
    potential_savings_yearly: number;
    status: string;
    evidence?: Record<string, any>;
    current_config?: Record<string, any>;
    target_config?: Record<string, any>;
    metadata?: Record<string, any>;
  };
  aws_payload?: Record<string, any>;
  snooze?: ResourceSnoozeState | null;
}

export interface S3OverviewResponse {
  generated_at: string | null;
  account_id: string | null;
  source?: 'imported_inventory' | 'latest_check_results';
  summary: {
    total_buckets: number;
    actionable_buckets: number;
    healthy_buckets: number;
    versioned_buckets: number;
    logging_enabled_buckets: number;
    inventory_enabled_buckets: number;
    replicated_buckets: number;
    lifecycle_policy_buckets: number;
    total_size_gb: number;
    total_objects: number;
    monthly_cost_estimate: number;
    potential_savings_yearly: number;
  };
  dimensions: {
    regions: Array<{ key: string; count: number }>;
    environments: Array<{ key: string; count: number }>;
    teams: Array<{ key: string; count: number }>;
    bucket_kinds: Array<{ key: string; count: number }>;
    criticality: Array<{ key: string; count: number }>;
  };
  findings_breakdown: Array<{ key: string; count: number }>;
  posture_breakdown: {
    versioning: { enabled: number; disabled: number };
    logging: { enabled: number; disabled: number };
    inventory: { enabled: number; disabled: number };
    replication: { enabled: number; disabled: number };
    lifecycle: { enabled: number; disabled: number };
  };
  buckets: S3OverviewBucket[];
}

export interface RdsOverviewInstance {
  inventory_id: number;
  resource_id: string;
  resource_name?: string | null;
  account_id?: string | null;
  region?: string | null;
  availability_zone?: string | null;
  state?: string | null;
  engine?: string | null;
  db_instance_class?: string | null;
  created_at?: string | null;
  tags: Record<string, string>;
  metadata: Record<string, any>;
  workload: {
    cpu_utilization: number;
    connections: number;
    read_iops: number;
    write_iops: number;
    monthly_cost_estimate: number;
    is_graviton: boolean;
    trends: {
      cpu: {
        latest?: { timestamp: string; average: number } | null;
        points: Array<{ timestamp: string; average: number }>;
      };
      connections: {
        latest?: { timestamp: string; average: number } | null;
        points: Array<{ timestamp: string; average: number }>;
      };
      read_iops: {
        latest?: { timestamp: string; average: number } | null;
        points: Array<{ timestamp: string; average: number }>;
      };
      write_iops: {
        latest?: { timestamp: string; average: number } | null;
        points: Array<{ timestamp: string; average: number }>;
      };
    };
  };
  maxops: {
    check_id?: string | null;
    finding_type?: string | null;
    severity?: string | null;
    title?: string | null;
    description?: string | null;
    recommended_action?: string | null;
    potential_savings_monthly: number;
    potential_savings_yearly: number;
    status: string;
    evidence?: Record<string, any>;
    current_config?: Record<string, any>;
    target_config?: Record<string, any>;
    metadata?: Record<string, any>;
  };
  aws_payload?: Record<string, any>;
  snooze?: ResourceSnoozeState | null;
}

export interface RdsOverviewResponse {
  generated_at: string | null;
  account_id: string | null;
  summary: {
    total_instances: number;
    actionable_instances: number;
    healthy_instances: number;
    graviton_instances: number;
    non_graviton_instances: number;
    idle_candidates: number;
    total_connections: number;
    monthly_cost_estimate: number;
    potential_savings_yearly: number;
  };
  dimensions: {
    regions: Array<{ key: string; count: number }>;
    environments: Array<{ key: string; count: number }>;
    teams: Array<{ key: string; count: number }>;
    engines: Array<{ key: string; count: number }>;
    instance_classes: Array<{ key: string; count: number }>;
  };
  findings_breakdown: Array<{ key: string; count: number }>;
  instances: RdsOverviewInstance[];
}

export interface ElasticacheOverviewResource {
  inventory_id: number;
  resource_id: string;
  resource_name?: string | null;
  resource_type: string;
  account_id?: string | null;
  region?: string | null;
  availability_zone?: string | null;
  state?: string | null;
  tags: Record<string, string>;
  metadata: Record<string, any>;
  workload: {
    curritems: number;
    keycount: number;
    monthly_cost_estimate: number;
    is_graviton: boolean;
    trends: {
      curritems: {
        latest?: { timestamp: string; average: number } | null;
        points: Array<{ timestamp: string; average: number }>;
      };
      keycount: {
        latest?: { timestamp: string; average: number } | null;
        points: Array<{ timestamp: string; average: number }>;
      };
    };
  };
  maxops: {
    check_id?: string | null;
    finding_type?: string | null;
    severity?: string | null;
    title?: string | null;
    description?: string | null;
    recommended_action?: string | null;
    potential_savings_monthly: number;
    potential_savings_yearly: number;
    status: string;
    evidence?: Record<string, any>;
    current_config?: Record<string, any>;
    target_config?: Record<string, any>;
    metadata?: Record<string, any>;
  };
  aws_payload?: Record<string, any>;
  snooze?: ResourceSnoozeState | null;
}

export interface ElasticacheOverviewResponse {
  generated_at: string | null;
  account_id: string | null;
  summary: {
    total_resources: number;
    actionable_resources: number;
    healthy_resources: number;
    clusters: number;
    replication_groups: number;
    redis_resources: number;
    valkey_resources: number;
    graviton_resources: number;
    monthly_cost_estimate: number;
    potential_savings_yearly: number;
  };
  dimensions: {
    regions: Array<{ key: string; count: number }>;
    environments: Array<{ key: string; count: number }>;
    teams: Array<{ key: string; count: number }>;
    resource_types: Array<{ key: string; count: number }>;
    engines: Array<{ key: string; count: number }>;
    node_types: Array<{ key: string; count: number }>;
  };
  findings_breakdown: Array<{ key: string; count: number }>;
  resources: ElasticacheOverviewResource[];
}

export interface AsgOverviewResource {
  inventory_id: number;
  resource_id: string;
  resource_name?: string | null;
  resource_type: string;
  account_id?: string | null;
  region?: string | null;
  state?: string | null;
  instance_type?: string | null;
  platform_normalized?: string | null;
  tags: Record<string, string>;
  metadata: Record<string, any>;
  capacity: {
    min_size: number;
    desired_capacity: number;
    max_size: number;
    in_service_instances: number;
    availability_zone_count: number;
    monthly_cost_estimate: number;
  };
  maxops: {
    check_id?: string | null;
    finding_type?: string | null;
    severity?: string | null;
    title?: string | null;
    description?: string | null;
    recommended_action?: string | null;
    potential_savings_monthly: number;
    potential_savings_yearly: number;
    status: string;
    evidence?: Record<string, any>;
    current_config?: Record<string, any>;
    target_config?: Record<string, any>;
    metadata?: Record<string, any>;
  };
  aws_payload?: Record<string, any>;
  snooze?: ResourceSnoozeState | null;
}

export interface AsgOverviewResponse {
  generated_at: string | null;
  account_id: string | null;
  summary: {
    total_groups: number;
    active_groups: number;
    actionable_groups: number;
    healthy_groups: number;
    monthly_cost_estimate: number;
    potential_savings_yearly: number;
    total_min_size: number;
    total_desired_capacity: number;
    total_max_size: number;
  };
  dimensions: {
    regions: Array<{ key: string; count: number }>;
    environments: Array<{ key: string; count: number }>;
    teams: Array<{ key: string; count: number }>;
    instance_types: Array<{ key: string; count: number }>;
    platforms: Array<{ key: string; count: number }>;
  };
  findings_breakdown: Array<{ key: string; count: number }>;
  resources: AsgOverviewResource[];
}

export interface DynamoDbOverviewResource {
  inventory_id: number;
  resource_id: string;
  resource_name?: string | null;
  resource_type: string;
  account_id?: string | null;
  region?: string | null;
  state?: string | null;
  billing_mode?: string | null;
  table_name?: string | null;
  table_class?: string | null;
  tags: Record<string, string>;
  metadata: Record<string, any>;
  workload: {
    item_count: number;
    read_capacity_units: number;
    write_capacity_units: number;
    avg_consumed_rcu: number;
    avg_consumed_wcu: number;
    p95_consumed_rcu: number;
    p95_consumed_wcu: number;
    avg_read_throttle_events: number;
    avg_write_throttle_events: number;
    monthly_cost_estimate: number;
    trends: {
      consumed_rcu: {
        latest?: { timestamp: string; average: number } | null;
        points: Array<{ timestamp: string; average: number }>;
      };
      consumed_wcu: {
        latest?: { timestamp: string; average: number } | null;
        points: Array<{ timestamp: string; average: number }>;
      };
      item_count: {
        latest?: { timestamp: string; average: number } | null;
        points: Array<{ timestamp: string; average: number }>;
      };
      read_throttle_events: {
        latest?: { timestamp: string; average: number } | null;
        points: Array<{ timestamp: string; average: number }>;
      };
      write_throttle_events: {
        latest?: { timestamp: string; average: number } | null;
        points: Array<{ timestamp: string; average: number }>;
      };
    };
  };
  maxops: {
    check_id?: string | null;
    finding_type?: string | null;
    severity?: string | null;
    title?: string | null;
    description?: string | null;
    recommended_action?: string | null;
    potential_savings_monthly: number;
    potential_savings_yearly: number;
    status: string;
    evidence?: Record<string, any>;
    current_config?: Record<string, any>;
    target_config?: Record<string, any>;
    metadata?: Record<string, any>;
  };
  aws_payload?: Record<string, any>;
  snooze?: ResourceSnoozeState | null;
}

export interface DynamoDbOverviewResponse {
  generated_at: string | null;
  account_id: string | null;
  summary: {
    total_resources: number;
    actionable_resources: number;
    healthy_resources: number;
    tables: number;
    gsis: number;
    provisioned_resources: number;
    on_demand_resources: number;
    monthly_cost_estimate: number;
    potential_savings_yearly: number;
    total_item_count: number;
    avg_consumed_rcu: number;
    avg_consumed_wcu: number;
  };
  dimensions: {
    regions: Array<{ key: string; count: number }>;
    environments: Array<{ key: string; count: number }>;
    teams: Array<{ key: string; count: number }>;
    resource_types: Array<{ key: string; count: number }>;
    billing_modes: Array<{ key: string; count: number }>;
    table_classes: Array<{ key: string; count: number }>;
  };
  findings_breakdown: Array<{ key: string; count: number }>;
  resources: DynamoDbOverviewResource[];
}

export interface EbsOverviewVolume {
  inventory_id: number;
  resource_id: string;
  resource_name?: string | null;
  resource_type: string;
  account_id?: string | null;
  region?: string | null;
  availability_zone?: string | null;
  state?: string | null;
  volume_type?: string | null;
  attached: boolean;
  tags: Record<string, string>;
  metadata: Record<string, any>;
  workload: {
    size_gb: number;
    iops: number;
    throughput: number;
    avg_iops: number;
    avg_throughput_mb: number;
    monthly_cost_estimate: number;
    trends: {
      read_ops: {
        latest?: { timestamp: string; average: number } | null;
        points: Array<{ timestamp: string; average: number }>;
      };
      write_ops: {
        latest?: { timestamp: string; average: number } | null;
        points: Array<{ timestamp: string; average: number }>;
      };
      read_bytes: {
        latest?: { timestamp: string; average: number } | null;
        points: Array<{ timestamp: string; average: number }>;
      };
      write_bytes: {
        latest?: { timestamp: string; average: number } | null;
        points: Array<{ timestamp: string; average: number }>;
      };
    };
  };
  maxops: {
    check_id?: string | null;
    finding_type?: string | null;
    severity?: string | null;
    title?: string | null;
    description?: string | null;
    recommended_action?: string | null;
    potential_savings_monthly: number;
    potential_savings_yearly: number;
    status: string;
    evidence?: Record<string, any>;
    current_config?: Record<string, any>;
    target_config?: Record<string, any>;
    metadata?: Record<string, any>;
  };
  aws_payload?: Record<string, any>;
  snooze?: ResourceSnoozeState | null;
}

export interface EbsOverviewResponse {
  generated_at: string | null;
  account_id: string | null;
  summary: {
    total_volumes: number;
    actionable_volumes: number;
    healthy_volumes: number;
    attached_volumes: number;
    unattached_volumes: number;
    gp3_volumes: number;
    provisioned_iops_volumes: number;
    monthly_cost_estimate: number;
    potential_savings_yearly: number;
    total_size_gb: number;
    avg_iops: number;
    avg_throughput_mb: number;
  };
  dimensions: {
    regions: Array<{ key: string; count: number }>;
    environments: Array<{ key: string; count: number }>;
    teams: Array<{ key: string; count: number }>;
    volume_types: Array<{ key: string; count: number }>;
    states: Array<{ key: string; count: number }>;
    criticality: Array<{ key: string; count: number }>;
  };
  findings_breakdown: Array<{ key: string; count: number }>;
  volumes: EbsOverviewVolume[];
}

export const inventoryApi = {
  getEc2Overview: async (): Promise<Ec2OverviewResponse> => {
    const response = await apiClient.get('/inventory/ec2/overview');
    return response.data;
  },
  getS3Overview: async (): Promise<S3OverviewResponse> => {
    const response = await apiClient.get('/inventory/s3/overview');
    return response.data;
  },
  getRdsOverview: async (): Promise<RdsOverviewResponse> => {
    const response = await apiClient.get('/inventory/rds/overview');
    return response.data;
  },
  getElasticacheOverview: async (): Promise<ElasticacheOverviewResponse> => {
    const response = await apiClient.get('/inventory/elasticache/overview');
    return response.data;
  },
  getAsgOverview: async (): Promise<AsgOverviewResponse> => {
    const response = await apiClient.get('/inventory/asg/overview');
    return response.data;
  },
  getDynamoDbOverview: async (): Promise<DynamoDbOverviewResponse> => {
    const response = await apiClient.get('/inventory/dynamodb/overview');
    return response.data;
  },
  getEbsOverview: async (): Promise<EbsOverviewResponse> => {
    const response = await apiClient.get('/inventory/ebs/overview');
    return response.data;
  },
  snoozeResources: async (payload: SnoozeResourcesPayload) => {
    const response = await apiClient.post('/inventory/resources/snooze', payload);
    return response.data;
  },
  removeSnoozes: async (payload: RemoveSnoozeResourcesPayload) => {
    const response = await apiClient.post('/inventory/resources/snooze/remove', payload);
    return response.data;
  },
};
