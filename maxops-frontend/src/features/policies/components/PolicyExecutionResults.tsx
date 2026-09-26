import React from 'react';
import { useQuery } from 'react-query';
import { Loader2, AlertCircle, CheckCircle, DollarSign, TrendingUp } from 'lucide-react';
import { policiesApi } from '@/services/policies';
import { formatStatus, getStatusColor, formatCurrency } from '@/utils/formatters';
import { Card } from '@/components/common/Card';

interface PolicyExecutionResultsProps {
  executionId: number;
}

export const PolicyExecutionResults: React.FC<PolicyExecutionResultsProps> = ({
  executionId,
}) => {
  const { data: execution, isLoading } = useQuery(
    ['execution', executionId],
    () => policiesApi.getExecution(executionId),
    {
      refetchInterval: (data) => {
        // Poll if still running
        return data?.status === 'running' ? 2000 : false;
      },
    }
  );

  if (isLoading) {
    return (
      <div className="text-center py-12">
        <Loader2 className="animate-spin h-8 w-8 text-primary-600 mx-auto" />
            <p className="mt-4 text-gray-600 dark:text-gray-400">Loading execution results...</p>
      </div>
    );
  }

  if (!execution) {
    return (
      <div className="text-center py-12">
        <AlertCircle className="h-8 w-8 text-danger-600 mx-auto" />
            <p className="mt-4 text-gray-600 dark:text-gray-400">Execution not found</p>
      </div>
    );
  }

  if (execution.status === 'failed') {
    return (
      <div className="space-y-4">
        <div className="bg-danger-50 border border-danger-200 rounded-lg p-4">
          <div className="flex items-center space-x-2">
            <AlertCircle className="text-danger-600" />
            <h3 className="font-semibold text-danger-800">Execution Failed</h3>
          </div>
          {execution.error_message && (
            <p className="mt-2 text-sm text-danger-700">{execution.error_message}</p>
          )}
        </div>
      </div>
    );
  }

  const totalCost = execution.total_monthly_cost || 0;
  const avgCost = execution.results && execution.results.length > 0
    ? totalCost / execution.results.filter(r => r.monthly_cost).length
    : 0;

  return (
    <div className="space-y-6">
      {/* Summary Cards */}
      <div className="grid grid-cols-1 md:grid-cols-4 gap-4">
        <Card>
          <div className="flex items-center justify-between">
            <div>
              <div className="text-sm text-gray-600 dark:text-gray-400">Status</div>
              <div className="mt-1">
                <span
                  className={`inline-flex px-2 py-1 text-xs font-medium rounded-full ${getStatusColor(
                    execution.status
                  )}`}
                >
                  {formatStatus(execution.status)}
                </span>
              </div>
            </div>
          </div>
        </Card>
        <Card>
          <div className="flex items-center justify-between">
            <div>
              <div className="text-sm text-gray-600 dark:text-gray-400">Resources Found</div>
              <div className="mt-1 text-2xl font-bold text-gray-900 dark:text-white">
                {execution.resources_found}
              </div>
            </div>
          </div>
        </Card>
        <Card>
          <div className="flex items-center justify-between">
            <div>
              <div className="text-sm text-gray-600 dark:text-gray-400 flex items-center space-x-1">
                <DollarSign size={14} />
                <span>Total Monthly Cost</span>
              </div>
              <div className="mt-1 text-2xl font-bold text-gray-900 dark:text-white">
                {formatCurrency(totalCost)}
              </div>
            </div>
          </div>
        </Card>
        <Card>
          <div className="flex items-center justify-between">
            <div>
              <div className="text-sm text-gray-600 dark:text-gray-400 flex items-center space-x-1">
                <TrendingUp size={14} />
                <span>Avg Cost/Resource</span>
              </div>
              <div className="mt-1 text-2xl font-bold text-gray-900 dark:text-white">
                {formatCurrency(avgCost)}
              </div>
            </div>
          </div>
        </Card>
      </div>

      {/* Cost Breakdown by Type */}
      {execution.cost_by_resource_type && Object.keys(execution.cost_by_resource_type).length > 0 && (
        <Card title="Cost by Resource Type">
          <div className="space-y-2">
            {Object.entries(execution.cost_by_resource_type).map(([type, cost]) => (
              <div key={type} className="flex items-center justify-between py-2 border-b border-gray-100 dark:border-gray-800 last:border-0">
                <span className="text-gray-700 dark:text-gray-300 font-medium">{type}</span>
                <span className="text-gray-900 dark:text-white font-semibold">{formatCurrency(cost as number)}</span>
              </div>
            ))}
          </div>
        </Card>
      )}

      {/* Results */}
      {execution.status === 'completed' && (
        <Card title="Matching Resources">
          {execution.results && execution.results.length > 0 ? (
            <div className="overflow-x-auto">
              <table className="w-full">
                <thead>
                  <tr className="border-b border-gray-200 dark:border-gray-800">
                    <th className="text-left py-3 px-4 font-semibold text-gray-700 dark:text-gray-300">Resource ID</th>
                    <th className="text-left py-3 px-4 font-semibold text-gray-700 dark:text-gray-300">Name</th>
                    <th className="text-left py-3 px-4 font-semibold text-gray-700 dark:text-gray-300">Type</th>
                    <th className="text-left py-3 px-4 font-semibold text-gray-700 dark:text-gray-300">Region</th>
                    <th className="text-right py-3 px-4 font-semibold text-gray-700 dark:text-gray-300">Monthly Cost</th>
                    <th className="text-left py-3 px-4 font-semibold text-gray-700 dark:text-gray-300">Reason</th>
                  </tr>
                </thead>
                <tbody>
                  {execution.results.map((result) => (
                    <tr
                      key={result.id}
                      className="border-b border-gray-100 dark:border-gray-800 hover:bg-gray-50 dark:hover:bg-gray-900"
                    >
                      <td className="py-3 px-4 font-mono text-sm text-gray-900 dark:text-white">
                        {result.resource_id}
                      </td>
                      <td className="py-3 px-4 text-gray-700 dark:text-gray-300">
                        {result.resource_name || '-'}
                      </td>
                      <td className="py-3 px-4 text-gray-700 dark:text-gray-300">{result.resource_type}</td>
                      <td className="py-3 px-4 text-gray-700 dark:text-gray-300">{result.region || '-'}</td>
                      <td className="py-3 px-4 text-right text-gray-900 dark:text-white font-semibold">
                        {result.monthly_cost !== undefined ? formatCurrency(result.monthly_cost) : '-'}
                      </td>
                      <td className="py-3 px-4 text-sm text-gray-600 dark:text-gray-400">{result.reason || '-'}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          ) : (
            <div className="text-center py-8">
              <CheckCircle className="h-12 w-12 text-success-600 mx-auto" />
              <p className="mt-4 text-gray-600 dark:text-gray-400">No resources matched this policy</p>
            </div>
          )}
        </Card>
      )}

      {execution.status === 'running' && (
        <div className="text-center py-8">
          <Loader2 className="animate-spin h-8 w-8 text-primary-600 mx-auto" />
          <p className="mt-4 text-gray-600 dark:text-gray-400">Policy execution in progress...</p>
        </div>
      )}
    </div>
  );
};

