import React from 'react';

export interface OnboardingStepItem {
  id: string;
  label: string;
}

interface OnboardingStepIndicatorProps {
  steps: readonly OnboardingStepItem[];
  currentStepId: string;
}

export const OnboardingStepIndicator: React.FC<OnboardingStepIndicatorProps> = ({
  steps,
  currentStepId,
}) => {
  const currentIndex = Math.max(
    0,
    steps.findIndex((step) => step.id === currentStepId)
  );

  return (
    <nav aria-label="Onboarding progress" className="w-full">
      <ol className="grid gap-2 sm:grid-flow-col sm:auto-cols-fr">
        {steps.map((step, index) => {
          const isCurrent = index === currentIndex;
          const isComplete = index < currentIndex;

          return (
            <li
              key={step.id}
              className={`flex items-center gap-3 rounded-lg border px-3 py-2 text-sm ${
                isCurrent
                  ? 'border-primary-200 bg-primary-50 text-primary-700 shadow-sm dark:border-primary-900/50 dark:bg-primary-900/20 dark:text-primary-300'
                  : isComplete
                    ? 'border-gray-200 bg-white text-gray-700 dark:border-gray-800 dark:bg-gray-900 dark:text-gray-300'
                    : 'border-gray-200 bg-gray-50 text-gray-500 dark:border-gray-800 dark:bg-gray-950 dark:text-gray-500'
              }`}
            >
              <span
                className={`flex h-7 w-7 shrink-0 items-center justify-center rounded-md text-xs font-semibold ${
                  isCurrent
                    ? 'bg-primary-600 text-white'
                    : isComplete
                      ? 'bg-primary-50 text-primary-700 ring-1 ring-primary-200 dark:bg-primary-900/20 dark:text-primary-300 dark:ring-primary-900/50'
                      : 'bg-gray-200 text-gray-600 dark:bg-gray-800 dark:text-gray-400'
                }`}
              >
                {index + 1}
              </span>
              <span className="truncate font-medium">{step.label}</span>
            </li>
          );
        })}
      </ol>
    </nav>
  );
};
