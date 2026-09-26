import React from 'react';
import { Card } from '@/components/common/Card';
import { ArrowRight, BarChart3 } from 'lucide-react';
import { useNavigate } from 'react-router-dom';
import { ChecksList } from '@/components/common/ChecksList';
import type { OnboardingExecution } from '@/types/api';

interface OnboardingResultsViewProps {
  execution: OnboardingExecution;
  onViewDashboard: () => void;
}

export const OnboardingResultsView: React.FC<OnboardingResultsViewProps> = ({ 
  execution 
}) => {
  const navigate = useNavigate();
  const successRate = execution.total_checks > 0 
    ? Math.round((execution.completed_checks / execution.total_checks) * 100) 
    : 0;

  return (
    <div className="min-h-screen bg-gray-50 dark:bg-gray-950 p-6">
      <div className="max-w-4xl mx-auto space-y-6">
        <div className="mb-6">
          <h1 className="text-3xl font-bold text-gray-900 dark:text-white mb-2">
            Onboarding Complete
          </h1>
          <p className="text-gray-600 dark:text-gray-400">
            Review your initial scan results below
          </p>
        </div>

        {/* Summary Cards */}
        <div className="grid grid-cols-1 md:grid-cols-4 gap-4">
          <Card>
            <div className="p-4">
              <div className="text-sm text-gray-600 dark:text-gray-400 mb-1">Total Checks</div>
              <div className="text-2xl font-bold text-gray-900 dark:text-white">
                {execution.total_checks}
              </div>
            </div>
          </Card>
          <Card>
            <div className="p-4">
              <div className="text-sm text-gray-600 dark:text-gray-400 mb-1">Completed</div>
              <div className="text-2xl font-bold text-success-600 dark:text-success-400">
                {execution.completed_checks}
              </div>
            </div>
          </Card>
          <Card>
            <div className="p-4">
              <div className="text-sm text-gray-600 dark:text-gray-400 mb-1">Failed</div>
              <div className="text-2xl font-bold text-danger-600 dark:text-danger-400">
                {execution.failed_checks}
              </div>
            </div>
          </Card>
          <Card>
            <div className="p-4">
              <div className="text-sm text-gray-600 dark:text-gray-400 mb-1">Success Rate</div>
              <div className="text-2xl font-bold text-primary-600 dark:text-primary-400">
                {successRate}%
              </div>
            </div>
          </Card>
        </div>

        {/* Results List */}
        <ChecksList
          checks={execution.results || []}
          title="Check Results"
          showSummary={true}
          maxHeight="max-h-[600px]"
          emptyMessage="No check results available"
        />

        {/* Action Buttons */}
        <div className="flex items-center justify-between pt-4">
          <button
            onClick={() => navigate('/dashboard')}
            className="flex items-center space-x-2 px-6 py-3 bg-primary-600 hover:bg-primary-700 text-white rounded-lg font-medium transition-colors"
          >
            <BarChart3 size={20} />
            <span>View Dashboard</span>
            <ArrowRight size={20} />
          </button>
        </div>
      </div>
    </div>
  );
};

