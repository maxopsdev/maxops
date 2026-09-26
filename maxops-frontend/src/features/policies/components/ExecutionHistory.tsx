import React, { useState } from 'react';
import { useQuery } from 'react-query';
import { Clock, CheckCircle, XCircle, Loader2, TestTube, Play, Eye, Calendar } from 'lucide-react';
import { policiesApi } from '@/services/policies';
import { formatDate, formatStatus, getStatusColor } from '@/utils/formatters';
import { Card } from '@/components/common/Card';
import { Button } from '@/components/common/Button';
import { Modal } from '@/components/common/Modal';
import { PolicyExecutionResults } from './PolicyExecutionResults';
import type { Policy } from '@/types/api';

interface ExecutionHistoryProps {
  policies: Policy[];
  onExecutionClick?: (executionId: number) => void;
}

export const ExecutionHistory: React.FC<ExecutionHistoryProps> = ({
  policies,
  onExecutionClick,
}) => {
  const [selectedExecution, setSelectedExecution] = useState<number | null>(null);
  const [filters, setFilters] = useState<{
    policy_id?: number;
    status?: string;
    execution_type?: string;
  }>({});
  const [showFilters, setShowFilters] = useState(false);

  const { data: executions = [], isLoading } = useQuery(
    ['executions', filters],
    () => policiesApi.getAllExecutions({ limit: 50, ...filters }),
    {
      refetchInterval: 5000, // Poll every 5 seconds to catch running executions
    }
  );

  const getPolicyName = (policyId: number): string => {
    const policy = policies.find((p) => p.id === policyId);
    return policy?.name || `Policy #${policyId}`;
  };

  const handleViewDetails = (executionId: number) => {
    setSelectedExecution(executionId);
    if (onExecutionClick) {
      onExecutionClick(executionId);
    }
  };

  const getStatusIcon = (status: string) => {
    switch (status) {
      case 'completed':
        return <CheckCircle size={16} className="text-success-600" />;
      case 'failed':
        return <XCircle size={16} className="text-danger-600" />;
      case 'running':
        return <Loader2 size={16} className="animate-spin text-primary-600" />;
      default:
        return <Clock size={16} className="text-gray-400" />;
    }
  };

  const getExecutionTypeIcon = (type: string) => {
    return type === 'dry-run' ? (
      <TestTube size={14} className="text-primary-600" />
    ) : (
      <Play size={14} className="text-danger-600" />
    );
  };

  const recentExecutions = executions.slice(0, 10);
  const allExecutions = executions;

  return (
    <>
      <div className="space-y-6">
        {/* Recent Executions Section */}
        <Card>
          <div className="flex items-center justify-between mb-4">
            <div>
              <h2 className="text-xl font-semibold text-gray-900 dark:text-white">
                Recent Executions
              </h2>
              <p className="text-sm text-gray-600 dark:text-gray-400 mt-1">
                Latest policy execution results
              </p>
            </div>
            <Button
              variant="secondary"
              size="sm"
              onClick={() => setShowFilters(!showFilters)}
            >
              <Calendar size={16} className="mr-2" />
              {showFilters ? 'Hide Filters' : 'Show Filters'}
            </Button>
          </div>

          {/* Filters */}
          {showFilters && (
            <div className="mb-4 p-4 bg-gray-50 dark:bg-gray-800 rounded-lg border border-gray-200 dark:border-gray-700">
              <div className="grid grid-cols-1 md:grid-cols-3 gap-4">
                <div>
                  <label className="block text-sm font-medium text-gray-700 dark:text-gray-300 mb-1">
                    Policy
                  </label>
                  <select
                    value={filters.policy_id || ''}
                    onChange={(e) =>
                      setFilters({
                        ...filters,
                        policy_id: e.target.value ? Number(e.target.value) : undefined,
                      })
                    }
                    className="input w-full"
                  >
                    <option value="">All Policies</option>
                    {policies.map((policy) => (
                      <option key={policy.id} value={policy.id}>
                        {policy.name}
                      </option>
                    ))}
                  </select>
                </div>
                <div>
                  <label className="block text-sm font-medium text-gray-700 dark:text-gray-300 mb-1">
                    Status
                  </label>
                  <select
                    value={filters.status || ''}
                    onChange={(e) =>
                      setFilters({
                        ...filters,
                        status: e.target.value || undefined,
                      })
                    }
                    className="input w-full"
                  >
                    <option value="">All Statuses</option>
                    <option value="completed">Completed</option>
                    <option value="failed">Failed</option>
                    <option value="running">Running</option>
                  </select>
                </div>
                <div>
                  <label className="block text-sm font-medium text-gray-700 dark:text-gray-300 mb-1">
                    Execution Type
                  </label>
                  <select
                    value={filters.execution_type || ''}
                    onChange={(e) =>
                      setFilters({
                        ...filters,
                        execution_type: e.target.value || undefined,
                      })
                    }
                    className="input w-full"
                  >
                    <option value="">All Types</option>
                    <option value="dry-run">Dry Run</option>
                    <option value="apply">Apply</option>
                  </select>
                </div>
              </div>
              {(filters.policy_id || filters.status || filters.execution_type) && (
                <div className="mt-4">
                  <Button
                    variant="ghost"
                    size="sm"
                    onClick={() => setFilters({})}
                  >
                    Clear Filters
                  </Button>
                </div>
              )}
            </div>
          )}

          {isLoading ? (
            <div className="text-center py-12">
              <Loader2 className="animate-spin h-8 w-8 text-primary-600 mx-auto" />
              <p className="mt-4 text-gray-600 dark:text-gray-400">Loading executions...</p>
            </div>
          ) : recentExecutions.length === 0 ? (
            <div className="text-center py-12">
              <Clock className="h-12 w-12 text-gray-400 mx-auto" />
              <p className="mt-4 text-gray-600 dark:text-gray-400">
                No executions found. Run a policy to see results here.
              </p>
            </div>
          ) : (
            <div className="overflow-x-auto">
              <table className="w-full">
                <thead>
                  <tr className="border-b border-gray-200 dark:border-gray-800">
                    <th className="text-left py-3 px-4 font-semibold text-gray-700 dark:text-gray-300">
                      Policy
                    </th>
                    <th className="text-center py-3 px-4 font-semibold text-gray-700 dark:text-gray-300">
                      Type
                    </th>
                    <th className="text-left py-3 px-4 font-semibold text-gray-700 dark:text-gray-300">
                      Status
                    </th>
                    <th className="text-center py-3 px-4 font-semibold text-gray-700 dark:text-gray-300">
                      Resources
                    </th>
                    <th className="text-left py-3 px-4 font-semibold text-gray-700 dark:text-gray-300">
                      Started
                    </th>
                    <th className="text-center py-3 px-4 font-semibold text-gray-700 dark:text-gray-300">
                      Actions
                    </th>
                  </tr>
                </thead>
                <tbody>
                  {recentExecutions.map((execution) => (
                    <tr
                      key={execution.id}
                      className="border-b border-gray-100 dark:border-gray-800 hover:bg-gray-50 dark:hover:bg-gray-900"
                    >
                      <td className="py-3 px-4">
                        <div className="font-medium text-gray-900 dark:text-white">
                          {getPolicyName(execution.policy_id)}
                        </div>
                      </td>
                      <td className="py-3 px-4 text-center">
                        <div className="flex items-center justify-center">
                          {getExecutionTypeIcon(execution.execution_type)}
                          <span className="ml-1 text-sm text-gray-600 dark:text-gray-400">
                            {execution.execution_type === 'dry-run' ? 'Dry Run' : 'Apply'}
                          </span>
                        </div>
                      </td>
                      <td className="py-3 px-4">
                        <div className="flex items-center space-x-2">
                          {getStatusIcon(execution.status)}
                          <span
                            className={`inline-flex items-center px-2.5 py-0.5 rounded-full text-xs font-semibold ${getStatusColor(execution.status)}`}
                          >
                            {formatStatus(execution.status)}
                          </span>
                        </div>
                      </td>
                      <td className="py-3 px-4 text-center">
                        <span className="font-medium text-gray-900 dark:text-white">
                          {execution.resources_found}
                        </span>
                      </td>
                      <td className="py-3 px-4 text-sm text-gray-600 dark:text-gray-400">
                        {formatDate(execution.started_at)}
                      </td>
                      <td className="py-3 px-4 text-center">
                        <Button
                          variant="ghost"
                          size="sm"
                          onClick={() => handleViewDetails(execution.id)}
                          title="View details"
                        >
                          <Eye size={16} />
                        </Button>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}

          {/* Show more link if there are more executions */}
          {allExecutions.length > 10 && (
            <div className="mt-4 text-center">
              <p className="text-sm text-gray-600 dark:text-gray-400">
                Showing 10 of {allExecutions.length} executions
              </p>
            </div>
          )}
        </Card>

        {/* Historical Executions Section */}
        {allExecutions.length > 10 && (
          <Card>
            <div className="mb-4">
              <h2 className="text-xl font-semibold text-gray-900 dark:text-white">
                All Executions
              </h2>
              <p className="text-sm text-gray-600 dark:text-gray-400 mt-1">
                Complete execution history
              </p>
            </div>

            <div className="overflow-x-auto">
              <table className="w-full">
                <thead>
                  <tr className="border-b border-gray-200 dark:border-gray-800">
                    <th className="text-left py-3 px-4 font-semibold text-gray-700 dark:text-gray-300">
                      Policy
                    </th>
                    <th className="text-center py-3 px-4 font-semibold text-gray-700 dark:text-gray-300">
                      Type
                    </th>
                    <th className="text-left py-3 px-4 font-semibold text-gray-700 dark:text-gray-300">
                      Status
                    </th>
                    <th className="text-center py-3 px-4 font-semibold text-gray-700 dark:text-gray-300">
                      Resources
                    </th>
                    <th className="text-left py-3 px-4 font-semibold text-gray-700 dark:text-gray-300">
                      Started
                    </th>
                    <th className="text-left py-3 px-4 font-semibold text-gray-700 dark:text-gray-300">
                      Completed
                    </th>
                    <th className="text-center py-3 px-4 font-semibold text-gray-700 dark:text-gray-300">
                      Actions
                    </th>
                  </tr>
                </thead>
                <tbody>
                  {allExecutions.map((execution) => (
                    <tr
                      key={execution.id}
                      className="border-b border-gray-100 dark:border-gray-800 hover:bg-gray-50 dark:hover:bg-gray-900"
                    >
                      <td className="py-3 px-4">
                        <div className="font-medium text-gray-900 dark:text-white">
                          {getPolicyName(execution.policy_id)}
                        </div>
                      </td>
                      <td className="py-3 px-4 text-center">
                        <div className="flex items-center justify-center">
                          {getExecutionTypeIcon(execution.execution_type)}
                          <span className="ml-1 text-sm text-gray-600 dark:text-gray-400">
                            {execution.execution_type === 'dry-run' ? 'Dry Run' : 'Apply'}
                          </span>
                        </div>
                      </td>
                      <td className="py-3 px-4">
                        <div className="flex items-center space-x-2">
                          {getStatusIcon(execution.status)}
                          <span
                            className={`inline-flex items-center px-2.5 py-0.5 rounded-full text-xs font-semibold ${getStatusColor(execution.status)}`}
                          >
                            {formatStatus(execution.status)}
                          </span>
                        </div>
                      </td>
                      <td className="py-3 px-4 text-center">
                        <span className="font-medium text-gray-900 dark:text-white">
                          {execution.resources_found}
                        </span>
                      </td>
                      <td className="py-3 px-4 text-sm text-gray-600 dark:text-gray-400">
                        {formatDate(execution.started_at)}
                      </td>
                      <td className="py-3 px-4 text-sm text-gray-600 dark:text-gray-400">
                        {execution.completed_at ? formatDate(execution.completed_at) : '-'}
                      </td>
                      <td className="py-3 px-4 text-center">
                        <Button
                          variant="ghost"
                          size="sm"
                          onClick={() => handleViewDetails(execution.id)}
                          title="View details"
                        >
                          <Eye size={16} />
                        </Button>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </Card>
        )}
      </div>

      {/* Execution Details Modal */}
      {selectedExecution && (
        <Modal
          isOpen={true}
          onClose={() => setSelectedExecution(null)}
          title="Execution Details"
          size="xl"
        >
          <PolicyExecutionResults executionId={selectedExecution} />
        </Modal>
      )}
    </>
  );
};

