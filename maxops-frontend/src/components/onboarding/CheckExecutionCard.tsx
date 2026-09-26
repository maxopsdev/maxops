import React from 'react';
import { Card } from '@/components/common/Card';
import { Loader2, CheckCircle, XCircle } from 'lucide-react';

interface CheckExecutionCardProps {
  check: {
    check_id: string;
    name: string;
    description: string;
    resource_type: string;
    status: 'running' | 'completed' | 'failed';
    resources_found: number;
    error: string | null;
  };
}

export const CheckExecutionCard: React.FC<CheckExecutionCardProps> = ({ check }) => {
  return (
    <Card className="w-full">
      <div className="flex items-start justify-between p-4">
        <div className="flex-1">
          <div className="flex items-center space-x-3 mb-2">
            {check.status === 'running' && (
              <Loader2 className="animate-spin text-primary-600 dark:text-primary-400" size={20} />
            )}
            {check.status === 'completed' && (
              <CheckCircle className="text-success-600 dark:text-success-400" size={20} />
            )}
            {check.status === 'failed' && (
              <XCircle className="text-danger-600 dark:text-danger-400" size={20} />
            )}
            <h3 className="font-semibold text-gray-900 dark:text-white">
              {check.name}
            </h3>
            <span className="px-2 py-1 text-xs bg-gray-100 dark:bg-gray-800 text-gray-600 dark:text-gray-400 rounded">
              {check.resource_type}
            </span>
          </div>
          <p className="text-sm text-gray-600 dark:text-gray-400 mb-2">
            {check.description}
          </p>
          {check.status === 'completed' && (
            <p className="text-sm text-gray-700 dark:text-gray-300">
              Found <span className="font-semibold">{check.resources_found}</span> resources
            </p>
          )}
          {check.status === 'failed' && check.error && (
            <div className="mt-2 p-3 bg-danger-50 dark:bg-danger-900/20 border border-danger-200 dark:border-danger-800 rounded-lg">
              <p className="text-sm font-medium text-danger-800 dark:text-danger-300 mb-1">
                Execution Failed
              </p>
              <p className="text-sm text-danger-700 dark:text-danger-400">
                {check.error}
              </p>
            </div>
          )}
          {check.status === 'running' && (
            <div className="mt-2">
              <div className="w-full bg-gray-200 dark:bg-gray-700 rounded-full h-2">
                <div className="bg-primary-600 h-2 rounded-full animate-pulse" style={{ width: '60%' }}></div>
              </div>
            </div>
          )}
        </div>
      </div>
    </Card>
  );
};

