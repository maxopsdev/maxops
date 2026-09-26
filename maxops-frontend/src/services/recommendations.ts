import apiClient from './api';

export type RecommendationClassification = 'ACTIONABLE' | 'CONDITIONAL' | 'REJECTED' | 'DEFERRED' | 'NO_CANDIDATE';
export type TierName = 'conservative' | 'balanced' | 'aggressive';
export type ResourceRecommendationClassification = 'ACTIONABLE' | 'CONDITIONAL' | 'DEFERRED' | 'INSUFFICIENT_DATA';
export type CandidateRecommendationClassification = 'ACTIONABLE' | 'CONDITIONAL' | 'REJECTED';
export type RdsEvaluationStatus = 'RECOMMENDED' | 'NO_RECOMMENDATION' | 'INSUFFICIENT_DATA' | 'NOT_APPLICABLE' | 'DEFERRED';
export type RdsBindingDimension = 'cpu' | 'memory' | 'db_load_cpu' | 'storage_iops' | 'storage_throughput' | 'network';

export interface RiskAssessment {
  telemetry: 'LOW' | 'MEDIUM' | 'HIGH';
  compute: 'LOW' | 'MEDIUM' | 'HIGH';
  memory: 'LOW' | 'MEDIUM' | 'HIGH';
  network: 'LOW' | 'MEDIUM' | 'HIGH';
  cache_health: 'LOW' | 'MEDIUM' | 'HIGH';
  compatibility: 'LOW' | 'MEDIUM' | 'HIGH';
  migration: 'LOW' | 'MEDIUM' | 'HIGH';
  overall: 'LOW' | 'MEDIUM' | 'HIGH';
  reason_codes: string[];
}

export interface ElastiCacheNodeCandidate {
  kind: 'NODE_TYPE_CHANGE';
  rank: number;
  target_node_type: string;
  target_monthly_cost: number;
  monthly_savings: number;
  yearly_savings: number;
  projected_engine_cpu: number | null;
  projected_host_cpu: number | null;
  projected_memory_util: number | null;
  projected_util: number;
  binding_dimension: 'cpu' | 'memory' | 'network';
  classification: RecommendationClassification;
  risk_assessment: RiskAssessment;
  reason_codes: string[];
  warning_details: Array<{ code: string; message: string }>;
  compute_evidence: Record<string, unknown>;
  network_evaluation: Record<string, unknown>;
  memory_evaluation: Record<string, unknown>;
  cache_health: Record<string, unknown>;
  required_review: boolean;
}

export interface ElastiCacheTierOption {
  tier: TierName;
  target_node_type: string;
  monthly_savings: number;
  yearly_savings: number;
  projected_util: number;
  binding_dimension: string;
  classification: RecommendationClassification;
  risk_assessment: RiskAssessment;
  recommendation_rank: number;
}

export interface ElastiCacheReplicaRecommendation {
  kind: 'REPLICA_COUNT_REDUCTION';
  classification: 'ACTIONABLE' | 'CONDITIONAL';
  current_replicas_per_node_group: number;
  target_replicas_per_node_group: number;
  affected_node_group_count: number;
  removed_node_count: number;
  projected_survivor_engine_cpu: number;
  monthly_savings: number;
  yearly_savings: number;
  risk_assessment: RiskAssessment;
  reason_codes: string[];
  redundancy_disclosure: string;
  member_evidence: Array<Record<string, unknown>>;
}

export interface ElastiCacheRightsizingResponse {
  inventory_id: number;
  resource_id: string;
  resource_name: string | null;
  account_id: string | null;
  region: string | null;
  state: string | null;
  classification: RecommendationClassification;
  deferred_reason_codes: string[];
  current_monthly_cost: number;
  engine: string;
  engine_version: string | null;
  current_node_type: string;
  node_count: number;
  cluster_mode_enabled: boolean;
  num_node_groups: number;
  recommendations: ElastiCacheNodeCandidate[];
  tiers: {
    default: TierName | null;
    conservative: ElastiCacheTierOption | null;
    balanced: ElastiCacheTierOption | null;
    aggressive: ElastiCacheTierOption | null;
  };
  rejection_summary: Record<string, number>;
  replica_recommendation: ElastiCacheReplicaRecommendation | null;
  replica_deferred_reason_codes?: string[];
  operational_note: string;
  availability_note: string;
  telemetry_summary: Record<string, unknown>;
}

export interface ElastiCacheTrendSeries {
  cache_cluster_id: string;
  node_group_id: string;
  role: 'primary' | 'replica';
  selection_reason: 'hottest_engine_cpu' | 'hottest_memory' | 'primary';
  daily: Array<{ timestamp: string; maximum: number | null; p99: number | null; p95: number | null }>;
  buckets: Record<string, { maximum: number | null; p99: number | null; p95: number | null }>;
}

export interface ElastiCacheTrendResponse {
  inventory_id: number;
  resource_id: string;
  metrics: { engine_cpu: ElastiCacheTrendSeries[]; memory: ElastiCacheTrendSeries[] };
  selection_summary: Record<'engine_cpu' | 'memory', {
    primary_total: number;
    primary_returned: number;
    primary_omitted: number;
    primary_context_limit: number;
  }>;
  bucket_semantics: Record<string, string>;
  cached: boolean;
}

export interface AsgRecommendationOption {
  target_min_size: number;
  target_desired_capacity: number;
  target_max_size: number;
  max_size_changed: boolean;
  satisfied_tiers: string[];
  projected_cpu_util: number | null;
  projected_memory_util: number | null;
  projected_util: number | null;
  binding_dimension: string | null;
  monthly_savings: number;
  yearly_savings: number;
  classification: 'ACTIONABLE' | 'CONDITIONAL' | 'PREVIEW';
  risk_assessment: Record<string, unknown>;
  reason_codes: string[];
  evidence: Record<string, unknown>;
}

export interface AsgRightsizingResponse {
  inventory_id: number;
  resource_id: string;
  resource_name: string | null;
  account_id: string | null;
  region: string | null;
  state: string | null;
  classification: 'ACTIONABLE' | 'CONDITIONAL' | 'PREVIEW' | 'DEFERRED' | 'INSUFFICIENT_DATA' | null;
  current_monthly_cost: number | null;
  current_capacity_evidence: {
    min_size: number;
    desired_capacity: number;
    max_size: number;
    in_service_instances: number;
    availability_zone_count: number;
    inventory_generated_at: string | null;
  };
  current_configuration: {
    instance_type: string | null;
    min_size: number;
    desired_capacity: number;
    max_size: number;
    availability_zones: string[];
  };
  recommendations: AsgRecommendationOption[];
  tiers: {
    conservative: AsgRecommendationOption | null;
    balanced: AsgRecommendationOption | null;
    aggressive: AsgRecommendationOption | null;
    default: 'conservative' | 'balanced' | 'aggressive' | null;
  };
  savings_previews: Array<Record<string, unknown>>;
  blocking_reasons: string[];
  deferred_reason_codes: string[];
  messages: string[];
  telemetry_summary: Record<string, unknown>;
  capacity_policy: Record<string, unknown>;
  scope_policy: Record<string, unknown>;
  pricing_evidence: Record<string, unknown>;
}

export interface RdsRiskAssessment {
  telemetry: 'LOW' | 'MEDIUM' | 'HIGH';
  compute: 'LOW' | 'MEDIUM' | 'HIGH';
  memory: 'LOW' | 'MEDIUM' | 'HIGH';
  db_load: 'LOW' | 'MEDIUM' | 'HIGH' | 'NOT_AVAILABLE' | 'NOT_NEEDED';
  storage: 'LOW' | 'MEDIUM' | 'HIGH';
  network: 'LOW' | 'MEDIUM' | 'HIGH';
  connections: 'LOW' | 'MEDIUM' | 'HIGH';
  compatibility: 'LOW' | 'MEDIUM' | 'HIGH';
  operations: 'LOW' | 'MEDIUM' | 'HIGH';
  overall: 'LOW' | 'MEDIUM' | 'HIGH';
  reason_codes: string[];
}

export interface RdsTierOption {
  kind: 'DB_INSTANCE_CLASS_CHANGE';
  rank: number;
  target_db_instance_class: string;
  target_vcpus: number;
  target_memory_gib: number;
  projected_cpu_percent: number | null;
  projected_memory_util: number | null;
  projected_freeable_gib: number | null;
  projected_util: number | null;
  binding_dimension: RdsBindingDimension | null;
  target_monthly_cost: number;
  monthly_savings: number;
  yearly_savings: number;
  classification: 'ACTIONABLE' | 'CONDITIONAL';
  satisfied_tiers: TierName[];
  risk_assessment: RdsRiskAssessment;
  reason_codes: string[];
  warning_details: Array<{ code: string; message: string }>;
  evidence: Record<string, number | string | null>;
}

export interface RdsInstanceClassRecommendation {
  kind: 'DB_INSTANCE_CLASS_CHANGE';
  classification: 'ACTIONABLE' | 'CONDITIONAL';
  tiers: {
    default: TierName | null;
    conservative: RdsTierOption | null;
    balanced: RdsTierOption | null;
    aggressive: RdsTierOption | null;
  };
  candidates: RdsTierOption[];
}

export interface RdsStorageRecommendation {
  kind: 'STORAGE_CONFIGURATION_CHANGE';
  classification: 'ACTIONABLE' | 'CONDITIONAL';
  current_storage: { storage_type: string; allocated_storage_gib: number; iops: number | null; throughput_mibps: number | null };
  target_storage: { storage_type: string; allocated_storage_gib: number; iops: number | null; throughput_mibps: number | null };
  monthly_savings: number;
  yearly_savings: number;
  evidence: Record<string, number | string | null>;
  risk_assessment: RdsRiskAssessment;
  reason_codes: string[];
  warning_details: Array<{ code: string; message: string }>;
}

export interface RdsDbLoadAttribution {
  status: 'AVAILABLE' | 'DISABLED' | 'UNSUPPORTED' | 'ACCESS_DENIED' | 'ERROR' | 'NOT_NEEDED';
  required: boolean;
  observed_days: number | null;
  total_load: Record<string, number | null> | null;
  cpu_load: Record<string, number | null> | null;
  non_cpu_load: Record<string, number | null> | null;
  unattributed_load: Record<string, number | null> | null;
  wait_type_shares: Array<{ name: string; share: number; aas_p99: number | null }>;
  enablement_prompt: {
    title: string;
    message: string;
    documentation_url: string;
    causes_downtime: false;
    recommended_mode: 'standard';
    sufficient_retention_days: 7;
  } | null;
}

export interface RdsRightsizingResponse {
  inventory_id: number;
  resource_id: string;
  resource_name: string | null;
  account_id: string | null;
  region: string;
  state: string;
  engine: string;
  engine_version: string;
  license_model: string;
  multi_az: boolean;
  read_replica: boolean;
  current: {
    db_instance_class: string;
    vcpus?: number | null;
    memory_gib?: number | null;
    storage_type: string;
    allocated_storage_gib: number;
    iops: number | null;
    storage_throughput_mibps: number | null;
    monthly_cost: number | null;
  };
  classification: ResourceRecommendationClassification | null;
  evaluation_status: { instance_class: RdsEvaluationStatus; storage_configuration: RdsEvaluationStatus };
  evaluation_reason_codes: { instance_class: string[]; storage_configuration: string[] };
  deferred_reason_codes?: string[];
  recommendations: Array<RdsInstanceClassRecommendation | RdsStorageRecommendation>;
  telemetry_summary: Record<string, any>;
  database_load_attribution: RdsDbLoadAttribution;
  policy: Record<string, any>;
  pricing_scope: Record<string, any>;
  operational_note: string;
  availability_note: string;
}

export interface RdsFleetRightsizingRow {
  inventory_id: number;
  resource_id: string;
  resource_name: string | null;
  account_id: string | null;
  region: string;
  state: string;
  engine: string;
  engine_version: string;
  deployment: string;
  current_db_instance_class: string;
  target_db_instance_class: string | null;
  classification: ResourceRecommendationClassification | null;
  evaluation_status: { instance_class: RdsEvaluationStatus; storage_configuration: RdsEvaluationStatus };
  instance_monthly_savings: number | null;
  storage_monthly_savings: number | null;
  storage_target: RdsStorageRecommendation['target_storage'] | null;
  headline_monthly_savings: number | null;
  binding_dimension: RdsBindingDimension | null;
  overall_risk: 'LOW' | 'MEDIUM' | 'HIGH' | null;
  database_load_status: RdsDbLoadAttribution['status'];
  cpu_observed_days: number | null;
  memory_observed_days: number | null;
  reason_codes: string[];
  generated_at: string | null;
}

export interface RdsTrendMetric {
  daily: Array<{ timestamp: string; value: number }>;
  buckets: Record<string, Record<string, number | null>>;
}

export interface RdsTrendResponse {
  inventory_id: number;
  resource_id: string;
  region: string;
  cpu: RdsTrendMetric;
  freeable_memory: RdsTrendMetric;
  connections: RdsTrendMetric;
  free_storage: RdsTrendMetric;
  bucket_semantics: Record<string, string>;
  generated_at: string;
  cached: boolean;
}

export const elasticacheRightsizingPotential = (
  recommendation?: ElastiCacheRightsizingResponse,
) => {
  if (!recommendation || recommendation.classification === 'DEFERRED') return 0;
  const balanced = recommendation.tiers.balanced?.yearly_savings || 0;
  const replica = recommendation.replica_recommendation?.yearly_savings || 0;
  return Math.max(balanced, replica);
};

export const elasticacheDisplayedPotential = (
  recommendation: ElastiCacheRightsizingResponse | undefined,
  legacyPotential: number,
) => recommendation === undefined
  ? legacyPotential
  : elasticacheRightsizingPotential(recommendation);

export const elasticacheStatusTone = (status: string | null | undefined) => {
  if (status === 'ACTIONABLE') return 'positive';
  if (status === 'CONDITIONAL') return 'warning';
  if (status === 'actionable') return 'critical';
  return 'neutral';
};

export const rightsizingApi = {
  listAsgRecommendations: async (): Promise<AsgRightsizingResponse[]> => {
    const response = await apiClient.get('/recommendations/asg/rightsize');
    return response.data;
  },
  getAsgRecommendation: async (inventoryId: number): Promise<AsgRightsizingResponse> => {
    const response = await apiClient.get(`/recommendations/asg/rightsize/${inventoryId}`);
    return response.data;
  },
  listElasticacheRecommendations: async (): Promise<ElastiCacheRightsizingResponse[]> => {
    const response = await apiClient.get('/recommendations/elasticache/rightsize');
    return response.data;
  },
  getElasticacheRecommendation: async (inventoryId: number): Promise<ElastiCacheRightsizingResponse> => {
    const response = await apiClient.get(`/recommendations/elasticache/rightsize/${inventoryId}`);
    return response.data;
  },
  getElasticacheTrend: async (inventoryId: number): Promise<ElastiCacheTrendResponse> => {
    const response = await apiClient.get(`/recommendations/elasticache/rightsize/${inventoryId}/trend`);
    return response.data;
  },
  listRdsRecommendations: async (params?: Record<string, string | number | undefined>): Promise<RdsFleetRightsizingRow[]> => {
    const response = await apiClient.get('/recommendations/rds/rightsize', { params });
    return response.data;
  },
  getRdsRecommendation: async (inventoryId: number): Promise<RdsRightsizingResponse> => {
    const response = await apiClient.get(`/recommendations/rds/rightsize/${inventoryId}`);
    return response.data;
  },
  getRdsTrend: async (inventoryId: number): Promise<RdsTrendResponse> => {
    const response = await apiClient.get(`/recommendations/rds/rightsize/${inventoryId}/trend`);
    return response.data;
  },
};
