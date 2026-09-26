/** Checks API service */
import apiClient from './api';

export interface CheckMetadata {
  check_id: string;
  name: string;
  description: string;
  resource_type: string;
  default_action: string;
  parameters: Record<string, any>;
}

export interface CheckTestResponse {
  check_id: string;
  resources_found: number;
  resources: Array<{
    resource_id: string;
    resource_type: string;
    metadata?: {
      potential_savings_monthly?: number;
      potential_savings_yearly?: number;
      [key: string]: any;
    };
    [key: string]: any;
  }>;
  potential_savings_yearly?: number;
}

export interface CheckState {
  check_id: string;
  name: string;
  description: string;
  resource_type: string;
  resources_found: number;
  potential_savings_monthly?: number;
  potential_savings_yearly?: number;
  last_run?: string;
  status?: 'idle' | 'running' | 'completed' | 'failed';
}

export const checksApi = {
  listChecks: async (resource_type?: string): Promise<CheckMetadata[]> => {
    const url = resource_type 
      ? `/checks?resource_type=${resource_type}`
      : '/checks';
    const response = await apiClient.get(url);
    return response.data;
  },

  getCheck: async (check_id: string): Promise<CheckMetadata> => {
    const response = await apiClient.get(`/checks/${check_id}`);
    return response.data;
  },

  testCheck: async (
    check_id: string,
    parameters?: Record<string, any>
  ): Promise<CheckTestResponse> => {
    const response = await apiClient.post(`/checks/${check_id}/test`, {
      parameters: parameters || {}
    });
    return response.data;
  },

  getLastRuns: async (): Promise<Record<string, string>> => {
    const response = await apiClient.get('/checks/last-runs');
    return response.data;
  },

  getLatestResults: async (): Promise<Record<string, {
    check_id: string;
    name: string;
    description: string;
    resource_type: string;
    status: string;
    resources_found: number;
    potential_savings_yearly: number;
    error: string | null;
    execution_time: string | null;
  }>> => {
    const response = await apiClient.get('/checks/latest-results');
    return response.data;
  },

  getCheckResources: async (check_id: string): Promise<{
    check_id: string;
    resources: Array<{
      resource_id: string;
      resource_type: string;
      resource_name?: string;
      region?: string;
      account_id?: string;
      metadata?: Record<string, any>;
      tags?: Record<string, any> | null;
    }>;
    resources_found: number;
    execution_time: string | null;
  }> => {
    const response = await apiClient.get(`/checks/${check_id}/resources`);
    return response.data;
  },

  getCheckSavingsHistory: async (check_id: string): Promise<{
    check_id: string;
    data_points: Array<{
      timestamp: string;
      savings: number;
      resources_found: number;
    }>;
  }> => {
    const response = await apiClient.get(`/checks/${check_id}/savings-history`);
    return response.data;
  },

  getCheckFilters: async (check_id: string): Promise<{
    check_id: string;
    filters: Array<{
      type: string;
      operator: string;
      key?: string;
      value: any;
    }>;
    updated_at?: string | null;
  }> => {
    const response = await apiClient.get(`/checks/${check_id}/filters`);
    return response.data;
  },

  saveCheckFilters: async (check_id: string, filters: Array<{
    type: string;
    operator: string;
    key?: string;
    value: any;
  }>): Promise<{
    check_id: string;
    filters: Array<any>;
    updated_at?: string | null;
  }> => {
    const response = await apiClient.put(`/checks/${check_id}/filters`, filters);
    return response.data;
  },

  deleteCheckFilters: async (check_id: string): Promise<{
    check_id: string;
    message: string;
  }> => {
    const response = await apiClient.delete(`/checks/${check_id}/filters`);
    return response.data;
  },

  snoozeResource: async (check_id: string, resource_id: string, days: number): Promise<{
    check_id: string;
    resource_id: string;
    snoozed_until: string | null;
    snooze_days: number | null;
    message: string;
  }> => {
    const response = await apiClient.post(`/checks/${check_id}/resources/${resource_id}/snooze`, null, {
      params: { days }
    });
    return response.data;
  },

  cancelSnoozeResource: async (check_id: string, resource_id: string): Promise<{
    check_id: string;
    resource_id: string;
    snoozed_until: string | null;
    snooze_days: number | null;
    message: string;
  }> => {
    const response = await apiClient.post(`/checks/${check_id}/resources/${resource_id}/snooze/cancel`);
    return response.data;
  },

  getCloudWatchAgentStatus: async (
    check_id: string,
    resource_id: string,
    region?: string
  ): Promise<{
    check_id: string;
    resource_id: string;
    installed: boolean | null;
    status: 'installed' | 'not_installed' | 'unknown';
  }> => {
    const response = await apiClient.get(
      `/checks/${check_id}/resources/${resource_id}/cloudwatch-agent`,
      { params: region ? { region } : undefined }
    );
    return response.data;
  },

  installCloudWatchAgent: async (
    check_id: string,
    resource_id: string,
    region?: string
  ): Promise<{
    check_id: string;
    resource_id: string;
    installed: boolean;
    status: 'installed' | 'installing';
    command_id?: string;
    message: string;
  }> => {
    const response = await apiClient.post(
      `/checks/${check_id}/resources/${resource_id}/cloudwatch-agent/install`,
      null,
      { params: region ? { region } : undefined }
    );
    return response.data;
  },

  exemptResource: async (check_id: string, resource_id: string, exempted: boolean): Promise<{
    check_id: string;
    resource_id: string;
    exempted: boolean;
    message: string;
  }> => {
    const response = await apiClient.post(`/checks/${check_id}/resources/${resource_id}/exempt?exempted=${exempted}`);
    return response.data;
  },

  getResourceExemption: async (check_id: string, resource_id: string): Promise<{
    check_id: string;
    resource_id: string;
    exempted: boolean;
    snoozed_until: string | null;
    snooze_days: number | null;
  }> => {
    const response = await apiClient.get(`/checks/${check_id}/resources/${resource_id}/exemption`);
    return response.data;
  },

  executeAction: async (
    check_id: string,
    data: {
      action: string;
      account_id: string;
      region: string;
      resource_id: string;
      parameters?: Record<string, any>;
    }
  ): Promise<{
    check_id: string;
    action: string;
    status: string;
    message: string;
    details?: Record<string, any>;
  }> => {
    const response = await apiClient.post(`/checks/${check_id}/actions`, data);
    return response.data;
  },
  getRdsInstanceClasses: async (
    check_id: string,
    resource_id: string,
    region: string
  ): Promise<{
    current_instance_class?: string;
    graviton_instance_classes: string[];
  }> => {
    const response = await apiClient.get(
      `/checks/${check_id}/resources/${resource_id}/rds-instance-classes`,
      { params: { region } }
    );
    return response.data;
  },
  getDatasyncStatus: async (
    check_id: string,
    task_execution_arn: string,
    region: string,
    account_id: string
  ): Promise<{
    status: string;
    task_execution_arn: string;
    bytes_transferred?: number;
    files_transferred?: number;
    files_skipped?: number;
    bytes_written?: number;
    bytes_read?: number;
    error_code?: string;
    error_detail?: string;
  }> => {
    const response = await apiClient.get(
      `/checks/${check_id}/actions/datasync-status`,
      { params: { task_execution_arn, region, account_id } }
    );
    return response.data;
  },
  getActionStatus: async (
    check_id: string,
    resource_id: string
  ): Promise<{
    status: string;
    action: string | null;
    message?: string;
    details?: Record<string, any>;
    started_at?: string;
    requires_polling?: string;
    polling_identifier?: string;
  }> => {
    const response = await apiClient.get(
      `/checks/${check_id}/actions/${resource_id}/status`
    );
    return response.data;
  },
};
