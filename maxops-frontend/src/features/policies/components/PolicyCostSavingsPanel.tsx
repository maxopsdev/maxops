import React from 'react';
import { useQuery } from 'react-query';
import { LineChart, Line, XAxis, YAxis, CartesianGrid, Tooltip, Legend, ResponsiveContainer } from 'recharts';
import { DollarSign, TrendingDown, Loader2 } from 'lucide-react';
import { policiesApi } from '@/services/policies';
import { Card } from '@/components/common/Card';
import { formatDate } from '@/utils/formatters';
import type { Policy } from '@/types/api';
import { useTheme } from '@/contexts/ThemeContext';
import { CHART_COLORS, STATUS_COLORS, chartAxisLineColor, chartGridColor, chartTickColor } from '@/styles/chartColors';

interface PolicyCostSavingsPanelProps {
  policy: Policy;
}

export const PolicyCostSavingsPanel: React.FC<PolicyCostSavingsPanelProps> = ({ policy }) => {
  const { theme } = useTheme();
  const { data: costSavings = [], isLoading } = useQuery(
    ['cost-savings', policy.id],
    () => policiesApi.getCostSavings(policy.id)
  );

  if (isLoading) {
    return (
      <Card>
        <div className="text-center py-12">
          <Loader2 className="animate-spin h-8 w-8 text-primary-600 mx-auto" />
          <p className="mt-4 text-gray-600 dark:text-gray-400">Loading cost savings data...</p>
        </div>
      </Card>
    );
  }

  if (costSavings.length === 0) {
    return (
      <Card>
        <div className="text-center py-12">
          <DollarSign className="h-12 w-12 text-gray-400 mx-auto" />
          <p className="mt-4 text-gray-600 dark:text-gray-400">
            No cost savings data available yet. Run the policy and track your savings over time.
          </p>
        </div>
      </Card>
    );
  }

  // Prepare chart data
  const chartData = costSavings.map((saving) => ({
    date: new Date(saving.date).toLocaleDateString('en-US', { month: 'short', day: 'numeric' }),
    fullDate: saving.date,
    costSaved: saving.cost_saved,
    cumulativeSavings: costSavings
      .filter((s) => new Date(s.date) <= new Date(saving.date))
      .reduce((sum, s) => sum + s.cost_saved, 0),
    resourcesFixed: saving.resources_fixed,
  }));

  const totalSavings = costSavings.reduce((sum, saving) => sum + saving.cost_saved, 0);
  const totalResourcesFixed = costSavings.reduce((sum, saving) => sum + saving.resources_fixed, 0);
  const latestSavings = costSavings[costSavings.length - 1];

  // Calculate trend (comparing last period to previous)
  let trend = 0;
  if (costSavings.length >= 2) {
    const lastPeriod = costSavings[costSavings.length - 1].cost_saved;
    const previousPeriod = costSavings[costSavings.length - 2].cost_saved;
    trend = previousPeriod > 0 ? ((lastPeriod - previousPeriod) / previousPeriod) * 100 : 0;
  }

  return (
    <Card>
      <div className="space-y-6">
        {/* Header */}
        <div>
          <h3 className="text-lg font-semibold text-gray-900 dark:text-white">
            Cost Savings Progress
          </h3>
          <p className="text-sm text-gray-600 dark:text-gray-400 mt-1">
            Track your cost optimization progress over time
          </p>
        </div>

        {/* Summary Stats */}
        <div className="grid grid-cols-1 md:grid-cols-3 gap-4">
          <div className="bg-primary-50 dark:bg-primary-900/20 rounded-lg p-4 border border-primary-200 dark:border-primary-800">
            <div className="flex items-center space-x-2">
              <DollarSign className="h-5 w-5 text-primary-600 dark:text-primary-400" />
              <div className="text-sm text-primary-700 dark:text-primary-300 font-medium">
                Total Savings
              </div>
            </div>
            <div className="mt-2 text-2xl font-bold text-primary-900 dark:text-primary-100">
              ${totalSavings.toLocaleString('en-US', { minimumFractionDigits: 2, maximumFractionDigits: 2 })}
            </div>
          </div>

          <div className="bg-success-50 dark:bg-success-900/20 rounded-lg p-4 border border-success-200 dark:border-success-800">
            <div className="flex items-center space-x-2">
              <TrendingDown className="h-5 w-5 text-success-600 dark:text-success-400" />
              <div className="text-sm text-success-700 dark:text-success-300 font-medium">
                Resources Fixed
              </div>
            </div>
            <div className="mt-2 text-2xl font-bold text-success-900 dark:text-success-100">
              {totalResourcesFixed}
            </div>
          </div>

          <div className="bg-gray-50 dark:bg-gray-800 rounded-lg p-4 border border-gray-200 dark:border-gray-700">
            <div className="text-sm text-gray-700 dark:text-gray-300 font-medium">
              Latest Savings
            </div>
            <div className="mt-2 text-2xl font-bold text-gray-900 dark:text-white">
              ${latestSavings.cost_saved.toLocaleString('en-US', { minimumFractionDigits: 2, maximumFractionDigits: 2 })}
            </div>
            {trend !== 0 && (
              <div className={`mt-1 text-sm ${trend > 0 ? 'text-success-600' : 'text-danger-600'}`}>
                {trend > 0 ? '↑' : '↓'} {Math.abs(trend).toFixed(1)}% from previous
              </div>
            )}
          </div>
        </div>

        {/* Chart */}
        <div className="mt-6">
          <h4 className="text-sm font-semibold text-gray-700 dark:text-gray-300 mb-4">
            Cost Savings Over Time
          </h4>
          <ResponsiveContainer width="100%" height={300}>
            <LineChart data={chartData} margin={{ top: 5, right: 30, left: 20, bottom: 5 }}>
              <CartesianGrid strokeDasharray="3 3" stroke={chartGridColor(theme)} />
              <XAxis
                dataKey="date"
                stroke={chartAxisLineColor(theme)}
                tick={{ fill: chartTickColor(theme) }}
              />
              <YAxis
                stroke={chartAxisLineColor(theme)}
                tick={{ fill: chartTickColor(theme) }}
                tickFormatter={(value) => `$${value.toLocaleString()}`}
              />
              <Tooltip
                contentStyle={{
                  // Recharts renders SVG, so the Tailwind dark: classes that
                  // used to sit here never applied. Same inline pattern the
                  // CheckResultsDialog chart uses.
                  backgroundColor: theme === 'dark' ? 'rgba(14, 24, 48, 0.95)' : 'rgba(255, 255, 255, 0.95)',
                  border: `1px solid ${chartGridColor(theme)}`,
                  borderRadius: '8px',
                }}
                formatter={(value: number | undefined) => [`$${(value ?? 0).toLocaleString('en-US', { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`, 'Cost Saved']}
              />
              <Legend />
              <Line
                type="monotone"
                dataKey="costSaved"
                stroke={CHART_COLORS[0]}
                strokeWidth={2}
                name="Cost Saved (Period)"
                dot={{ fill: CHART_COLORS[0], r: 4 }}
              />
              <Line
                type="monotone"
                dataKey="cumulativeSavings"
                stroke={STATUS_COLORS.healthy}
                strokeWidth={2}
                name="Cumulative Savings"
                dot={{ fill: STATUS_COLORS.healthy, r: 4 }}
                strokeDasharray="5 5"
              />
            </LineChart>
          </ResponsiveContainer>
        </div>

        {/* Recent Savings Table */}
        <div className="mt-6">
          <h4 className="text-sm font-semibold text-gray-700 dark:text-gray-300 mb-4">
            Recent Savings History
          </h4>
          <div className="overflow-x-auto">
            <table className="w-full">
              <thead>
                <tr className="border-b border-gray-200 dark:border-gray-800">
                  <th className="text-left py-2 px-4 text-xs font-semibold text-gray-700 dark:text-gray-300">
                    Date
                  </th>
                  <th className="text-right py-2 px-4 text-xs font-semibold text-gray-700 dark:text-gray-300">
                    Cost Saved
                  </th>
                  <th className="text-right py-2 px-4 text-xs font-semibold text-gray-700 dark:text-gray-300">
                    Resources Fixed
                  </th>
                </tr>
              </thead>
              <tbody>
                {costSavings.slice(-5).reverse().map((saving) => (
                  <tr
                    key={saving.id}
                    className="border-b border-gray-100 dark:border-gray-800 hover:bg-gray-50 dark:hover:bg-gray-900"
                  >
                    <td className="py-2 px-4 text-sm text-gray-600 dark:text-gray-400">
                      {formatDate(saving.date)}
                    </td>
                    <td className="py-2 px-4 text-sm font-medium text-gray-900 dark:text-white text-right">
                      ${saving.cost_saved.toLocaleString('en-US', { minimumFractionDigits: 2, maximumFractionDigits: 2 })}
                    </td>
                    <td className="py-2 px-4 text-sm text-gray-600 dark:text-gray-400 text-right">
                      {saving.resources_fixed}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>
      </div>
    </Card>
  );
};

