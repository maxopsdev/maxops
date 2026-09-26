import React from 'react';
import { Card } from '@/components/common/Card';
import { EnvironmentSettingsForm } from '@/components/settings/EnvironmentSettingsForm';
import {
  OnboardingStepIndicator,
  type OnboardingStepItem,
} from '@/components/onboarding/OnboardingStepIndicator';
import type { OnboardingDraftRequest, OnboardingRequest } from '@/types/api';

interface EnvironmentSetupViewProps {
  initialValues?: Partial<OnboardingRequest>;
  accountAutofill?: { accountId: string; nonce: number } | null;
  steps: readonly OnboardingStepItem[];
  currentStepId: string;
  onSubmit: (data: OnboardingRequest) => void;
  onDraftChange?: (data: OnboardingDraftRequest) => void;
  onBack?: () => void;
  isSubmitting?: boolean;
}

export const EnvironmentSetupView: React.FC<EnvironmentSetupViewProps> = ({
  initialValues,
  accountAutofill,
  steps,
  currentStepId,
  onSubmit,
  onDraftChange,
  onBack,
  isSubmitting = false,
}) => {
  return (
    <div className="flex h-screen items-start justify-center overflow-hidden bg-gradient-to-br from-primary-50 to-primary-100 p-4 dark:from-gray-900 dark:to-gray-950 sm:p-6">
      <Card className="flex h-full w-full max-w-6xl overflow-hidden">
        <div className="flex h-full min-h-0 w-full flex-col gap-4">
          <OnboardingStepIndicator steps={steps} currentStepId={currentStepId} />

          <div className="shrink-0">
            <h1 className="text-3xl font-bold text-gray-900 dark:text-white">Account Settings</h1>
            <p className="text-gray-600 dark:text-gray-400 mt-1">
              Configure the account context and regions MaxOps should scan.
            </p>
          </div>

          <EnvironmentSettingsForm
            initialValues={initialValues}
            accountAutofill={accountAutofill}
            onSubmit={onSubmit}
            onDraftChange={onDraftChange}
            onCancel={onBack}
            cancelLabel="Back"
            submitLabel="Continue"
            isSubmitting={isSubmitting}
            fitViewport
          />
        </div>
      </Card>
    </div>
  );
};
