import React from 'react';
import { Button } from '@/components/common/Button';
import { Play, Loader2, CheckCircle, XCircle, AlertCircle } from 'lucide-react';
import type { CheckMetadata } from '@/services/checks';
import type { CheckState } from '@/services/checks';

interface CheckCardProps {
  check: CheckMetadata;
  state?: CheckState;
  savingsTrend?: number[];
  savingsTrendLabels?: string[];
  isRunning: boolean;
  disableRun?: boolean;
  onTestClick?: () => void;
  onRunCheck: (check_id: string) => void;
  onClick?: (check: CheckMetadata, state?: CheckState) => void;
}

export const CheckCard: React.FC<CheckCardProps> = ({
  check,
  state,
  savingsTrend = [],
  savingsTrendLabels = [],
  isRunning,
  disableRun = false,
  onTestClick,
  onRunCheck,
  onClick,
}) => {
  const resourcesFound = state?.resources_found || 0;
  const savings = state?.potential_savings_yearly || 0;
  const status = state?.status || 'idle';
  const hasBeenRun = state?.last_run || status !== 'idle';
  const isClickable = onClick && hasBeenRun;

  const handleCardClick = (e: React.MouseEvent) => {
    // Don't trigger if clicking on the Run Check button
    if ((e.target as HTMLElement).closest('button')) {
      return;
    }
    if (isClickable) {
      onClick(check, state);
    }
  };

  const formatUsd = (value: number): string => {
    if (!Number.isFinite(value)) return '$0.00';
    if (value === 0) return '$0.00';
    if (Math.abs(value) < 0.01) return `$${value.toFixed(6)}`;
    return `$${value.toFixed(2)}`;
  };

  const renderSparkline = () => {
    if (savingsTrend.length === 0) {
      return (
        <div className="flex h-12 w-28 items-center justify-center rounded-md bg-gray-50 text-[11px] text-gray-400 dark:bg-gray-800/70 dark:text-gray-500">
          No trend
        </div>
      );
    }

    const width = 112;
    const height = 40;
    const min = Math.min(...savingsTrend);
    const max = Math.max(...savingsTrend);
    const range = max - min || 1;
    const step = savingsTrend.length > 1 ? width / (savingsTrend.length - 1) : width;
    const points = savingsTrend
      .map((value, index) => {
        const x = savingsTrend.length === 1 ? width / 2 : index * step;
        const y = height - ((value - min) / range) * height;
        return `${x},${y}`;
      })
      .join(' ');

    return (
      <div className="rounded-md bg-gray-50 px-2 py-2 dark:bg-gray-800/70">
        <div className="mb-1 flex items-center justify-between text-[10px] text-gray-500 dark:text-gray-400">
          <span>${max.toFixed(0)}</span>
          <span>${min.toFixed(0)}</span>
        </div>
        <svg width={width} height={height} viewBox={`0 0 ${width} ${height}`} className="overflow-visible">
          <polyline
            fill="none"
            stroke="currentColor"
            strokeWidth="2"
            points={points}
            className="text-primary-600 dark:text-primary-400"
          />
        </svg>
        <div className="mt-1 flex items-center justify-between text-[10px] text-gray-500 dark:text-gray-400">
          <span>{savingsTrendLabels[0] || 'Start'}</span>
          <span>{savingsTrendLabels[savingsTrendLabels.length - 1] || 'Now'}</span>
        </div>
      </div>
    );
  };

  return (
    <div
      onClick={handleCardClick}
      className={`p-4 border rounded-lg border-gray-200 dark:border-gray-700 transition-colors ${
        isClickable
          ? 'cursor-pointer hover:bg-gray-50 dark:hover:bg-gray-800 hover:border-primary-300 dark:hover:border-primary-700'
          : 'hover:bg-gray-50 dark:hover:bg-gray-800'
      }`}
    >
      <div className="flex items-stretch w-full">
        {/* First Column: Name, Description, Type */}
        <div className="pr-4 w-[55%] flex-shrink-0">
          <div className="flex items-start space-x-3">
            {status === 'completed' && (
              <CheckCircle className="text-success-600 dark:text-success-400 mt-1" size={24} />
            )}
            {status === 'failed' && (
              <XCircle className="text-danger-600 dark:text-danger-400 mt-1" size={24} />
            )}
            {isRunning && (
              <Loader2 className="text-primary-600 dark:text-primary-400 animate-spin mt-1" size={24} />
            )}
            {status === 'idle' && !isRunning && (
              <AlertCircle className="text-gray-400 dark:text-gray-500 mt-1" size={24} />
            )}
            <div>
              <div className="font-semibold text-gray-900 dark:text-white">
                {check.name}
              </div>
              <div className="text-sm text-gray-600 dark:text-gray-400 mt-1">
                {check.description}
              </div>
              <div className="mt-2">
                <span className="inline-flex items-center rounded bg-gray-100 px-2 py-1 text-xs dark:bg-gray-700">
                  {check.resource_type}
                </span>
              </div>
              {state?.last_run && (
                <div className="text-xs text-gray-500 dark:text-gray-500 mt-2">
                  Last run: {new Date(state.last_run).toLocaleString()}
                </div>
              )}
              {!state?.last_run && state?.status === 'idle' && (
                <div className="text-xs text-gray-400 dark:text-gray-600 mt-2">
                  Never run
                </div>
              )}
            </div>
          </div>
        </div>

        {/* Second Column: Resources, Savings, Trend */}
        <div className="px-4 flex-1 flex items-center justify-between gap-4">
          <div className="flex flex-col justify-center">
            <div className="h-[50%] flex items-center">
              <div className="text-xs text-gray-600 dark:text-gray-400">
                Resources: <span className="font-semibold text-gray-900 dark:text-white">{resourcesFound}</span>
              </div>
            </div>
            <div className="h-[50%] flex items-center">
              <div className="text-xs text-gray-600 dark:text-gray-400">
                Potential Savings: <span className="font-semibold text-success-600 dark:text-success-400">
                  {formatUsd(savings)}/yr
                </span>
              </div>
            </div>
          </div>
          <div className="h-[50%] flex items-center">
            {renderSparkline()}
          </div>
        </div>

        {/* Third Column: Run Check Button */}
        <div className="pl-4 flex items-center justify-end space-x-2 flex-shrink-0">
          {onTestClick && (
            <Button
              onClick={(e) => {
                e.stopPropagation();
                onTestClick();
              }}
              variant="secondary"
              size="sm"
              title="Test action"
            >
              Test
            </Button>
          )}
          <Button
            onClick={(e) => {
              e.stopPropagation();
              onRunCheck(check.check_id);
            }}
            disabled={isRunning || disableRun}
            title={disableRun ? 'Check is inactive' : 'Run check'}
            variant="primary"
            size="sm"
          >
            <span className="flex items-center space-x-2">
              {isRunning ? (
                <>
                  <Loader2 className="animate-spin" size={16} />
                  <span>Running...</span>
                </>
              ) : (
                <>
                  <Play size={16} />
                  <span>Run Check</span>
                </>
              )}
            </span>
          </Button>
        </div>
      </div>
    </div>
  );
};
