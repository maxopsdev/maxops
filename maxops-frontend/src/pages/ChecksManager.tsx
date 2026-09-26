import React, { useState } from 'react';
import { useQuery, useMutation, useQueryClient } from 'react-query';
import { Edit, Archive, Search } from 'lucide-react';
import { Layout } from '@/components/layout/Layout';
import { Card } from '@/components/common/Card';
import { Button } from '@/components/common/Button';
import { Modal } from '@/components/common/Modal';
import { policiesApi } from '@/services/policies';
import type { Policy } from '@/types/api';
import { PolicyEditor } from '@/features/policies/components/PolicyEditor';
import { PolicyExecutionResults } from '@/features/policies/components/PolicyExecutionResults';
import { PolicyCostSavingsPanel } from '@/features/policies/components/PolicyCostSavingsPanel';
import { ChevronDown, ChevronRight } from 'lucide-react';

export const ChecksManagerPage: React.FC = () => {
  const [searchTerm, setSearchTerm] = useState('');
  const [statusFilter, setStatusFilter] = useState<string>('all');
  const [editingPolicy, setEditingPolicy] = useState<Policy | null>(null);
  const [executingPolicy, setExecutingPolicy] = useState<number | null>(null);
  const [expandedPolicyId, setExpandedPolicyId] = useState<number | null>(null);
  const queryClient = useQueryClient();

  const { data: policies = [], isLoading } = useQuery(
    ['policies', statusFilter],
    () => policiesApi.list({ status: statusFilter === 'all' ? undefined : statusFilter })
  );

  const deleteMutation = useMutation(
    (id: number) => policiesApi.delete(id),
    {
      onSuccess: () => {
        queryClient.invalidateQueries('policies');
      },
    }
  );

  const filteredPolicies = policies.filter((policy) =>
    policy.name.toLowerCase().includes(searchTerm.toLowerCase()) ||
    policy.description?.toLowerCase().includes(searchTerm.toLowerCase())
  );

  const handleDelete = async (id: number) => {
    if (window.confirm('Are you sure you want to archive this check?')) {
      deleteMutation.mutate(id);
    }
  };

  return (
    <Layout>
      <div className="space-y-6">
        {/* Header */}
        <div className="flex items-center justify-between">
          <div>
            <h1 className="text-3xl font-bold text-gray-900 dark:text-white">Checks Manager</h1>
            <p className="text-gray-600 dark:text-gray-400 mt-1">
              Manage checks, filters, and executions for cost optimization
            </p>
          </div>
        </div>

        {/* Filters and Actions */}
        <Card>
          <div className="flex items-center space-x-4">
            <div className="flex-1 relative">
              <Search className="absolute left-3 top-1/2 transform -translate-y-1/2 text-gray-400 dark:text-gray-500" size={20} />
              <input
                type="text"
                placeholder="Search policies..."
                value={searchTerm}
                onChange={(e) => setSearchTerm(e.target.value)}
                className="input pl-10"
              />
            </div>
            <select
              value={statusFilter}
              onChange={(e) => setStatusFilter(e.target.value)}
              className="input w-40"
            >
              <option value="all">All Status</option>
              <option value="active">Active</option>
              <option value="inactive">Inactive</option>
            </select>
          </div>
        </Card>

        {/* Checks Table */}
        <Card>
          {isLoading ? (
            <div className="text-center py-12">
              <div className="animate-spin rounded-full h-8 w-8 border-b-2 border-primary-600 dark:border-primary-400 mx-auto"></div>
              <p className="mt-4 text-gray-600 dark:text-gray-400">Loading policies...</p>
            </div>
          ) : filteredPolicies.length === 0 ? (
            <div className="text-center py-12">
              <p className="text-gray-600 dark:text-gray-400">No checks found</p>
            </div>
          ) : (
            <div className="overflow-x-auto">
              <table className="w-full">
                <thead>
                  <tr className="border-b border-gray-200 dark:border-gray-800">
                    <th className="text-left py-3 px-4 font-semibold text-gray-700 dark:text-gray-300">Name</th>
                    <th className="text-center py-3 px-4 font-semibold text-gray-700 dark:text-gray-300 whitespace-nowrap">Resource Type</th>
                    <th className="text-center py-3 px-4 font-semibold text-gray-700 dark:text-gray-300 whitespace-nowrap">Filters</th>
                    <th className="text-center py-3 px-4 font-semibold text-gray-700 dark:text-gray-300">Actions</th>
                  </tr>
                </thead>
                <tbody>
                  {filteredPolicies.map((policy) => (
                    <React.Fragment key={policy.id}>
                      <tr className="border-b border-gray-100 dark:border-gray-800 hover:bg-gray-50 dark:hover:bg-gray-900 cursor-pointer" onClick={() => setExpandedPolicyId(expandedPolicyId === policy.id ? null : policy.id)}>
                        <td className="py-3 px-4">
                          <div className="flex items-center space-x-2">
                            {expandedPolicyId === policy.id ? (
                              <ChevronDown size={16} className="text-gray-400" />
                            ) : (
                              <ChevronRight size={16} className="text-gray-400" />
                            )}
                            <div>
                              <div className="flex items-center space-x-2">
                                <span className="font-medium text-gray-900 dark:text-white">{policy.name}</span>
                                {policy.policy_code && (
                                  <span className="px-2 py-0.5 bg-gray-100 dark:bg-gray-800 text-gray-600 dark:text-gray-400 text-xs font-mono rounded">
                                    {policy.policy_code}
                                  </span>
                                )}
                              </div>
                              {policy.description && (
                                <div className="text-sm text-gray-500 dark:text-gray-400 mt-1">{policy.description}</div>
                              )}
                            </div>
                          </div>
                        </td>
                        <td className="py-3 px-4 text-gray-700 dark:text-gray-300 text-left">{policy.resource_type}</td>
                        <td className="py-3 px-4 text-center">
                          {Array.isArray(policy.filters_json) && policy.filters_json.length > 0 ? (
                            <span className="inline-flex items-center rounded-full border border-success-200 bg-success-50 px-2.5 py-1 text-xs font-medium text-success-700 dark:border-success-800 dark:bg-success-900/20 dark:text-success-300">
                              {policy.filters_json.length} filter{policy.filters_json.length === 1 ? '' : 's'}
                            </span>
                          ) : (
                            <span className="inline-flex items-center rounded-full border border-gray-200 bg-gray-50 px-2.5 py-1 text-xs font-medium text-gray-600 dark:border-gray-700 dark:bg-gray-800 dark:text-gray-300">
                              No filters
                            </span>
                          )}
                        </td>
                        <td className="py-3 px-4" onClick={(e) => e.stopPropagation()}>
                          <div className="flex items-center justify-end space-x-2">
                            <Button
                              variant="secondary"
                              size="sm"
                              onClick={() => setEditingPolicy(policy)}
                              title="Edit check"
                            >
                              <Edit size={16} />
                            </Button>
                            <Button
                              variant="secondary"
                              size="sm"
                              onClick={() => handleDelete(policy.id)}
                              title="Archive check"
                            >
                              <Archive size={16} />
                            </Button>
                          </div>
                        </td>
                      </tr>
                      {expandedPolicyId === policy.id && (
                        <tr>
                          <td colSpan={4} className="p-0">
                            <div className="p-6 bg-gray-50 dark:bg-gray-900 border-t border-gray-200 dark:border-gray-800">
                              <PolicyCostSavingsPanel policy={policy} />
                            </div>
                          </td>
                        </tr>
                      )}
                    </React.Fragment>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </Card>
      </div>

      {/* Edit Modal */}
      <Modal
        isOpen={editingPolicy !== null}
        onClose={() => {
          setEditingPolicy(null);
        }}
        title="Edit Check"
        size="xl"
      >
        <PolicyEditor
          policy={editingPolicy}
          onClose={() => {
            setEditingPolicy(null);
          }}
        />
      </Modal>


      {/* Execution Results Modal */}
      {executingPolicy && (
        <Modal
          isOpen={true}
          onClose={() => setExecutingPolicy(null)}
          title="Check Execution Results"
          size="xl"
        >
          <PolicyExecutionResults executionId={executingPolicy} />
        </Modal>
      )}
    </Layout>
  );
};

