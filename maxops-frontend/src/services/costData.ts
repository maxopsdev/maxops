import apiClient from './api';

export type CostDataStepState =
  | 'done'
  | 'waiting'
  | 'action_required'
  | 'error'
  | 'unknown';

export interface CostDataStep {
  id: string;
  label: string;
  state: CostDataStepState;
  detail: string;
  /** What to do about it, when the step is blocked by permissions. */
  remedy?: string;
  denied_action?: string | null;
  setup_profile?: string | null;
  error?: string | null;
  /** Present on the cache step. */
  months?: string[];
  cache_root?: string;
  /** Present on the pricing step. */
  enabled?: boolean;
  billing_month?: string | null;
  resource_count?: number;
  /** Present on the export/delivery steps. */
  export_arn?: string;
  bucket?: string;
  prefix?: string;
}

export interface CostDataJob {
  id: number;
  job_type: 'export' | 'refresh';
  status: 'running' | 'completed' | 'failed';
  progress: {
    phase?: string;
    message?: string;
    current?: number;
    total?: number;
    updated_at?: string;
  };
  result?: Record<string, unknown> | null;
  error?: string | null;
  started_at?: string | null;
  completed_at?: string | null;
}

export interface CostDataSetupStatus {
  region: string;
  account_id: string | null;
  bucket: string | null;
  aws_available: boolean;
  aws_error?: string | null;
  steps: CostDataStep[];
  diagnostics: CostDataStep[];
  jobs: CostDataJob[];
  running: {
    export: CostDataJob | null;
    refresh: CostDataJob | null;
  };
}

export interface CostDataPreflight {
  job_type: string;
  simulated: boolean;
  can_proceed: boolean;
  required_actions: string[];
  denied_actions: string[];
  message: string;
  error?: string | null;
}

export interface CostDataRefreshEstimate {
  query_count: number;
  datasets: string[];
  months: string[];
  estimated_usd: number | null;
  export_bytes?: number;
  note: string;
  error?: string;
}

export const costDataApi = {
  async getSetupStatus(): Promise<CostDataSetupStatus> {
    const response = await apiClient.get('/cur/setup/status');
    return response.data;
  },

  async getPreflight(jobType: 'export' | 'refresh'): Promise<CostDataPreflight> {
    const response = await apiClient.get(`/cur/setup/preflight/${jobType}`);
    return response.data;
  },

  async getRefreshEstimate(): Promise<CostDataRefreshEstimate> {
    const response = await apiClient.get('/cur/setup/estimate/refresh');
    return response.data;
  },

  async startExport(): Promise<{ job_id: number; status: string }> {
    const response = await apiClient.post('/cur/setup/export');
    return response.data;
  },

  async startRefresh(): Promise<{ job_id: number; status: string }> {
    const response = await apiClient.post('/cur/setup/refresh', {});
    return response.data;
  },

  async setPricingEnabled(enabled: boolean): Promise<{ enabled: boolean }> {
    const response = await apiClient.post('/cur/setup/pricing', { enabled });
    return response.data;
  },
};
