import apiClient from './api';

export type RightsizerResourceType = 'ec2' | 'asg' | 'ecs' | 'rds' | 's3';

export interface RightsizerResourceTypeSummary {
  resource_type: RightsizerResourceType;
  label: string;
  description: string;
  resource_count: number;
  detail_available: boolean;
}

export interface RightsizerPolicy {
  network_medium_ratio: number;
  network_high_ratio: number;
  ebs_medium_ratio: number;
  ebs_high_ratio: number;
  allow_unknown_instance_store_usage: boolean;
}

export interface RightsizerResourceSummary {
  inventory_id: number;
  resource_id: string;
  resource_name?: string | null;
  resource_type: RightsizerResourceType;
  account_id?: string | null;
  region?: string | null;
  state?: string | null;
  current_type?: string | null;
  target_type?: string | null;
  status: string;
  classification: string;
  monthly_cost?: number;
  monthly_savings: number;
  yearly_savings: number;
  risk_overall?: string | null;
  reason_codes: string[];
  detail_available: boolean;
}

export interface RightsizerResourcesResponse {
  resource_type: RightsizerResourceType;
  generated_at: string | null;
  policy: RightsizerPolicy;
  summary: {
    total_resources: number;
    actionable_resources: number;
    inventory_only_resources: number;
    monthly_savings: number;
    yearly_savings: number;
  };
  filters: Record<string, string[]>;
  resources: RightsizerResourceSummary[];
}

export interface RightsizerResourceFilters {
  q?: string;
  region?: string;
  state?: string;
  status?: string;
  current_type?: string;
  target_type?: string;
}

export interface RightsizerTrendChart {
  metric: string;
  unit: string;
  points: Array<Record<string, any>>;
  target_capacity_line: number;
}

export interface RightsizerDetailResponse {
  resource_type: RightsizerResourceType;
  inventory_id: number;
  resource: {
    inventory_id: number;
    resource_id: string;
    resource_name?: string | null;
    account_id?: string | null;
    region?: string | null;
    availability_zone?: string | null;
    state?: string | null;
    current_type?: string | null;
    tags: Record<string, string>;
  };
  status: string;
  classification: string;
  recommendation?: Record<string, any> | null;
  recommendations: Array<Record<string, any>>;
  current_instance?: Record<string, any> | null;
  current_capacity_evidence?: Record<string, any> | null;
  tiers: Record<string, any>;
  deferred_reason_codes: string[];
  blocking_reasons: string[];
  telemetry_summary: Record<string, any>;
  risk_assessment?: Record<string, any> | null;
  warnings: Array<{ code: string; message: string }>;
  lookback_summary: Array<{
    window: string;
    lookback_days?: number | null;
    maximum?: number | null;
    p99?: number | null;
    p95?: number | null;
    sample_count: number;
  }>;
  chart: RightsizerTrendChart;
  memory_chart?: RightsizerTrendChart | null;
  iops_chart?: RightsizerTrendChart | null;
  utilization_bars: Array<{
    id: string;
    label: string;
    value_ratio?: number | null;
    detail: Record<string, any>;
  }>;
  coverage_caveats: Record<string, any>;
  policy: RightsizerPolicy;
}

export const rightsizerApi = {
  getResourceTypes: async (): Promise<{
    resource_types: RightsizerResourceTypeSummary[];
    policy: RightsizerPolicy;
  }> => {
    const response = await apiClient.get('/rightsizer/resource-types');
    return response.data;
  },

  getResources: async (
    resourceType: RightsizerResourceType,
    filters: RightsizerResourceFilters = {}
  ): Promise<RightsizerResourcesResponse> => {
    const params = Object.fromEntries(
      Object.entries(filters).filter(([, value]) => value !== undefined && value !== '' && value !== 'all')
    );
    const response = await apiClient.get(`/rightsizer/resources/${resourceType}`, { params });
    return response.data;
  },

  getDetail: async (
    resourceType: RightsizerResourceType,
    inventoryId: number
  ): Promise<RightsizerDetailResponse> => {
    const response = await apiClient.get(`/rightsizer/resources/${resourceType}/${inventoryId}`);
    return response.data;
  },

  applyRecommendation: async (
    inventoryId: number,
    data: {
      target_instance_type: string;
      recommendation_option?: string | null;
      account_id: string;
      region: string;
      resource_id: string;
    }
  ): Promise<{
    check_id: string;
    action: string;
    status: string;
    message: string;
    details?: Record<string, any>;
  }> => {
    const response = await apiClient.post(`/rightsizer/resources/ec2/${inventoryId}/apply`, data);
    return response.data;
  },
};
