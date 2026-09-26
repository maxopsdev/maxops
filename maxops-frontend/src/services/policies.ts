/** Policy API service */
import apiClient from './api';
import type {
  Policy,
  PolicyCreate,
  PolicyUpdate,
  PolicyExecution,
  PolicyTemplate,
  PolicyValidationResponse,
  PolicyCostSavings,
  FilterCondition,
  FilterDefinition,
} from '@/types/api';

export const policiesApi = {
  // Policy CRUD
  list: async (params?: { skip?: number; limit?: number; status?: string }): Promise<Policy[]> => {
    const response = await apiClient.get('/policies', { params });
    return response.data;
  },

  get: async (id: number): Promise<Policy> => {
    const response = await apiClient.get(`/policies/${id}`);
    return response.data;
  },

  create: async (data: PolicyCreate): Promise<Policy> => {
    const response = await apiClient.post('/policies', data);
    return response.data;
  },

  update: async (id: number, data: PolicyUpdate): Promise<Policy> => {
    const response = await apiClient.put(`/policies/${id}`, data);
    return response.data;
  },

  delete: async (id: number): Promise<void> => {
    await apiClient.delete(`/policies/${id}`);
  },

  // Policy validation
  validate: async (policyYaml?: string, filters?: FilterCondition[], resourceType?: string): Promise<PolicyValidationResponse> => {
    const payload: any = {};
    if (filters && resourceType) {
      payload.filters_json = filters;
      payload.resource_type = resourceType;
    } else if (policyYaml) {
      payload.policy_yaml = policyYaml;
    }
    const response = await apiClient.post('/policies/validate', payload);
    return response.data;
  },
  
  // Filter management
  getFilters: async (resourceType: string): Promise<Record<string, FilterDefinition>> => {
    const response = await apiClient.get(`/policy-filters/${resourceType}`);
    return response.data.filters || {};
  },
  
  getCommonFilters: async (): Promise<Record<string, FilterDefinition>> => {
    const response = await apiClient.get('/policy-filters/common');
    return response.data.filters || {};
  },
  
  getFilterDefinition: async (resourceType: string, filterType: string): Promise<FilterDefinition> => {
    const response = await apiClient.get(`/policy-filters/${resourceType}/${filterType}`);
    return response.data.definition;
  },
  
  validateFilter: async (resourceType: string, filter: FilterCondition): Promise<boolean> => {
    try {
      const response = await apiClient.post('/policy-filters/validate', {
        resource_type: resourceType,
        filter_data: filter,
      });
      return response.data.valid;
    } catch {
      return false;
    }
  },

  validateById: async (id: number): Promise<PolicyValidationResponse> => {
    const response = await apiClient.post(`/policies/${id}/validate`);
    return response.data;
  },

  // Policy execution (scan only, no actions)
  execute: async (
    id: number,
    filters?: Record<string, any>
  ): Promise<PolicyExecution> => {
    const response = await apiClient.post(`/policies/${id}/execute`, {
      filters,
    });
    return response.data;
  },

  // Policy execution by code (scan only, no actions)
  executeByCode: async (
    policyCode: string,
    filters?: Record<string, any>
  ): Promise<PolicyExecution> => {
    const response = await apiClient.post(`/policies/code/${policyCode}/execute`, {
      filters,
    });
    return response.data;
  },

  // Batch execution (scan only, no actions)
  executeBatch: async (
    policyIds: number[]
  ): Promise<PolicyExecution[]> => {
    const response = await apiClient.post('/policies/execute-batch', {
      policy_ids: policyIds,
    });
    return response.data;
  },

  getExecutions: async (policyId: number, limit: number = 50): Promise<PolicyExecution[]> => {
    const response = await apiClient.get(`/policies/${policyId}/executions`, {
      params: { limit },
    });
    return response.data;
  },

  getExecution: async (executionId: number): Promise<PolicyExecution> => {
    const response = await apiClient.get(`/policies/executions/${executionId}`);
    return response.data;
  },

  getAllExecutions: async (params?: {
    skip?: number;
    limit?: number;
    policy_id?: number;
    status?: string;
  }): Promise<PolicyExecution[]> => {
    const response = await apiClient.get('/policies/executions', { params });
    return response.data;
  },

  // Templates
  listTemplates: async (category?: string): Promise<PolicyTemplate[]> => {
    const response = await apiClient.get('/policy-templates', {
      params: category ? { category } : {},
    });
    return response.data.templates || response.data;
  },

  getTemplate: async (templateId: string): Promise<PolicyTemplate> => {
    const response = await apiClient.get(`/policy-templates/${templateId}`);
    return response.data;
  },

  getCostSavings: async (policyId: number): Promise<PolicyCostSavings[]> => {
    const response = await apiClient.get(`/policies/${policyId}/cost-savings`);
    return response.data;
  },
};
