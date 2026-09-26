import apiClient from './api';

export interface ScanResult {
  check_id: string;
  name: string;
  description: string;
  resource_type: string;
  status: 'completed' | 'failed';
  resources_found: number;
  potential_savings_yearly: number;
  error: string | null;
  execution_time: string | null;
}

export interface ScanRunResponse {
  execution_id: number;
  status: string;
  resource_type?: string | null;
  selected_regions: string[];
  inventory_counts: Record<string, number>;
  inventory_errors: Array<{
    resource_type: string;
    region: string;
    error: string;
  }>;
  error_details?: Array<{
    check_id: string;
    name: string;
    resource_type: string;
    error: string;
  }>;
  checks_completed: number;
  checks_failed: number;
  total_checks: number;
  progress?: {
    phase?: string;
    message?: string;
    current_region?: string | null;
    current_resource_type?: string | null;
    current_check_id?: string | null;
    current_check_name?: string | null;
    check_index?: number | null;
    total_checks?: number | null;
    inventory_counts?: Record<string, number>;
    completed_checks?: number;
    failed_checks?: number;
    updated_at?: string;
  };
  results: Record<string, ScanResult>;
  execution_time: string | null;
}

export interface ScanStartResponse {
  execution_id: number;
  status: string;
  resource_type?: string | null;
  selected_regions: string[];
  total_checks: number;
  progress?: ScanRunResponse['progress'];
}

export const scansApi = {
  runScan: async (resourceType?: string): Promise<ScanRunResponse> => {
    const response = await apiClient.post('/scans', {
      resource_type: resourceType || null,
    });
    return response.data;
  },
  startScan: async (resourceType?: string): Promise<ScanStartResponse> => {
    const response = await apiClient.post('/scans/start', {
      resource_type: resourceType || null,
    });
    return response.data;
  },
  getScanStatus: async (executionId: number): Promise<ScanRunResponse> => {
    const response = await apiClient.get(`/scans/${executionId}`);
    return response.data;
  },
};
