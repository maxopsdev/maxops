import React from 'react';
import { Card } from '@/components/common/Card';
import { CheckCircle, XCircle, Loader2, ChevronRight } from 'lucide-react';

interface CheckItem {
  check_id: string;
  name: string;
  description: string;
  resource_type: string;
  status: 'running' | 'completed' | 'failed';
  resources_found: number;
  error: string | null;
}

interface ChecksListProps {
  checks: CheckItem[];
  title?: string;
  showSummary?: boolean;
  maxHeight?: string;
  emptyMessage?: string;
  onCheckClick?: (check: CheckItem) => void; // For future drill-down capability
}

export const ChecksList: React.FC<ChecksListProps> = ({
  checks,
  title = 'Check Results',
  showSummary = false,
  maxHeight = 'max-h-96',
  emptyMessage = 'No checks to display',
  onCheckClick,
}) => {
  if (checks.length === 0) {
    return (
      <Card>
        <div className="p-6">
          <h2 className="text-xl font-semibold text-gray-900 dark:text-white mb-4">
            {title}
          </h2>
          <div className="text-center py-8 text-gray-500 dark:text-gray-400">
            {emptyMessage}
          </div>
        </div>
      </Card>
    );
  }

  const completedCount = checks.filter((c) => c.status === 'completed').length;
  const failedCount = checks.filter((c) => c.status === 'failed').length;
  const runningCount = checks.filter((c) => c.status === 'running').length;

  return (
    <Card>
      <div className="p-6">
        <div className="flex items-center justify-between mb-4">
          <h2 className="text-xl font-semibold text-gray-900 dark:text-white">
            {title}
          </h2>
          {showSummary && (
            <div className="flex items-center space-x-4 text-sm text-gray-600 dark:text-gray-400">
              <span className="text-success-600 dark:text-success-400">
                {completedCount} completed
              </span>
              {failedCount > 0 && (
                <span className="text-danger-600 dark:text-danger-400">
                  {failedCount} failed
                </span>
              )}
              {runningCount > 0 && (
                <span className="text-primary-600 dark:text-primary-400">
                  {runningCount} running
                </span>
              )}
            </div>
          )}
        </div>
        <div className={`space-y-3 ${maxHeight} overflow-y-auto`}>
          {checks.map((check) => {
            const isClickable = onCheckClick && check.status === 'completed';
            
            return (
              <div
                key={check.check_id}
                onClick={() => isClickable && onCheckClick(check)}
                className={`p-4 border border-gray-200 dark:border-gray-700 rounded-lg transition-colors ${
                  isClickable
                    ? 'cursor-pointer hover:bg-gray-50 dark:hover:bg-gray-800 hover:border-primary-300 dark:hover:border-primary-700'
                    : 'hover:bg-gray-50 dark:hover:bg-gray-800'
                }`}
              >
                <div className="flex items-start justify-between">
                  <div className="flex-1 min-w-0">
                    {/* Check Name and Status */}
                    <div className="flex items-center space-x-3 mb-2">
                      {check.status === 'running' && (
                        <Loader2 className="animate-spin text-primary-600 dark:text-primary-400 flex-shrink-0" size={20} />
                      )}
                      {check.status === 'completed' && (
                        <CheckCircle className="text-success-600 dark:text-success-400 flex-shrink-0" size={20} />
                      )}
                      {check.status === 'failed' && (
                        <XCircle className="text-danger-600 dark:text-danger-400 flex-shrink-0" size={20} />
                      )}
                      <h3 className="font-semibold text-gray-900 dark:text-white text-lg">
                        {check.name}
                      </h3>
                      <span className="px-2 py-1 text-xs bg-gray-100 dark:bg-gray-800 text-gray-600 dark:text-gray-400 rounded flex-shrink-0">
                        {check.resource_type}
                      </span>
                    </div>

                    {/* Description */}
                    <p className="text-sm text-gray-600 dark:text-gray-400 mb-3 ml-8">
                      {check.description}
                    </p>

                    {/* Result and Resources Found - Only show for completed checks */}
                    {check.status === 'completed' && (
                      <div className="ml-8 p-3 bg-success-50 dark:bg-success-900/20 border border-success-200 dark:border-success-800 rounded-lg">
                        <div className="flex items-center space-x-2">
                          <CheckCircle className="text-success-600 dark:text-success-400" size={16} />
                          <span className="text-sm font-medium text-success-800 dark:text-success-300">
                            Check Completed Successfully
                          </span>
                        </div>
                        <p className="text-sm text-success-700 dark:text-success-400 mt-1">
                          Found <span className="font-semibold">{check.resources_found}</span> resource{check.resources_found !== 1 ? 's' : ''} matching this check
                        </p>
                      </div>
                    )}

                    {/* Error Message - Only show for failed checks */}
                    {check.status === 'failed' && check.error && (
                      <div className="ml-8 mt-2 p-3 bg-danger-50 dark:bg-danger-900/20 border border-danger-200 dark:border-danger-800 rounded-lg">
                        <div className="flex items-center space-x-2 mb-1">
                          <XCircle className="text-danger-600 dark:text-danger-400" size={16} />
                          <p className="text-sm font-medium text-danger-800 dark:text-danger-300">
                            Execution Failed
                          </p>
                        </div>
                        <p className="text-sm text-danger-700 dark:text-danger-400">
                          {check.error}
                        </p>
                      </div>
                    )}

                    {/* Running State */}
                    {check.status === 'running' && (
                      <div className="ml-8 p-3 bg-primary-50 dark:bg-primary-900/20 border border-primary-200 dark:border-primary-800 rounded-lg">
                        <div className="flex items-center space-x-2">
                          <Loader2 className="animate-spin text-primary-600 dark:text-primary-400" size={16} />
                          <span className="text-sm font-medium text-primary-800 dark:text-primary-300">
                            Check in progress...
                          </span>
                        </div>
                      </div>
                    )}
                  </div>

                  {/* Drill-down indicator for completed checks */}
                  {isClickable && (
                    <div className="ml-4 flex-shrink-0">
                      <ChevronRight className="text-gray-400 dark:text-gray-500" size={20} />
                    </div>
                  )}
                </div>
              </div>
            );
          })}
        </div>
      </div>
    </Card>
  );
};

