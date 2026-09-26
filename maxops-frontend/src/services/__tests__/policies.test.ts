import { describe, it, expect, vi, beforeEach } from 'vitest';
import { policiesApi } from '../policies';
import apiClient from '../api';

// Mock the API client
vi.mock('../api', () => ({
  default: {
    get: vi.fn(),
    post: vi.fn(),
    put: vi.fn(),
    delete: vi.fn(),
  },
}));

describe('policiesApi', () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  describe('list', () => {
    it('fetches list of policies', async () => {
      const mockPolicies = [
        { id: 1, name: 'Policy 1', resource_type: 'ec2' },
        { id: 2, name: 'Policy 2', resource_type: 'rds' },
      ];

      (apiClient.get as any).mockResolvedValue({ data: mockPolicies });

      const result = await policiesApi.list();

      expect(apiClient.get).toHaveBeenCalledWith('/policies', { params: undefined });
      expect(result).toEqual(mockPolicies);
    });

    it('fetches policies with filters', async () => {
      (apiClient.get as any).mockResolvedValue({ data: [] });

      await policiesApi.list({ status: 'active' });

      expect(apiClient.get).toHaveBeenCalledWith('/policies', {
        params: { status: 'active' },
      });
    });
  });

  describe('get', () => {
    it('fetches a single policy', async () => {
      const mockPolicy = { id: 1, name: 'Policy 1' };
      (apiClient.get as any).mockResolvedValue({ data: mockPolicy });

      const result = await policiesApi.get(1);

      expect(apiClient.get).toHaveBeenCalledWith('/policies/1');
      expect(result).toEqual(mockPolicy);
    });
  });

  describe('create', () => {
    it('creates a new policy', async () => {
      const policyData = {
        name: 'New Policy',
        resource_type: 'ec2',
        filters_json: [],
      };
      const mockResponse = { id: 1, ...policyData };
      (apiClient.post as any).mockResolvedValue({ data: mockResponse });

      const result = await policiesApi.create(policyData);

      expect(apiClient.post).toHaveBeenCalledWith('/policies', policyData);
      expect(result).toEqual(mockResponse);
    });
  });

  describe('execute', () => {
    it('executes a policy', async () => {
      const mockExecution = {
        id: 1,
        policy_id: 1,
        execution_type: 'dry-run',
        status: 'completed',
      };
      (apiClient.post as any).mockResolvedValue({ data: mockExecution });

      const result = await policiesApi.execute(1, { region: 'us-east-1' });

      expect(apiClient.post).toHaveBeenCalledWith('/policies/1/execute', {
        filters: { region: 'us-east-1' },
      });
      expect(result).toEqual(mockExecution);
    });
  });

  describe('executeByCode', () => {
    it('executes a policy by code', async () => {
      const mockExecution = {
        id: 1,
        policy_id: 1,
        execution_type: 'apply',
        status: 'completed',
      };
      (apiClient.post as any).mockResolvedValue({ data: mockExecution });

      const result = await policiesApi.executeByCode('POLA1B', { region: 'us-east-1' });

      expect(apiClient.post).toHaveBeenCalledWith('/policies/code/POLA1B/execute', {
        filters: { region: 'us-east-1' },
      });
      expect(result).toEqual(mockExecution);
    });
  });

  describe('getFilters', () => {
    it('fetches filters for a resource type', async () => {
      const mockFilters = {
        idle: { type: 'idle', label: 'Idle Duration' },
      };
      (apiClient.get as any).mockResolvedValue({ data: { filters: mockFilters } });

      const result = await policiesApi.getFilters('ec2');

      expect(apiClient.get).toHaveBeenCalledWith('/policy-filters/ec2');
      expect(result).toEqual(mockFilters);
    });
  });
});

