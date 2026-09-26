import React, { useEffect, useState } from 'react';
import { ArrowLeft, ArrowRight } from 'lucide-react';

import { Button } from '@/components/common/Button';
import { Card } from '@/components/common/Card';
import { IamRolePermissionsPanel } from '@/components/onboarding/IamRolePermissionsPanel';
import {
  OnboardingStepIndicator,
  type OnboardingStepItem,
} from '@/components/onboarding/OnboardingStepIndicator';
import type { OnboardingAwsProfile, OnboardingIamRoleResponse } from '@/types/api';

interface RoleCreationViewProps {
  steps: readonly OnboardingStepItem[];
  currentStepId: string;
  initialProfileName?: string | null;
  initialRoleReady?: boolean;
  onBack: () => void;
  onContinue: () => void;
  onProfileSelected?: (profile: OnboardingAwsProfile) => void;
  onRoleCreated?: (result: OnboardingIamRoleResponse) => void;
  onAccountResolved?: (accountId: string) => void;
}

export const RoleCreationView: React.FC<RoleCreationViewProps> = ({
  steps,
  currentStepId,
  initialProfileName,
  initialRoleReady = false,
  onBack,
  onContinue,
  onProfileSelected,
  onRoleCreated,
  onAccountResolved,
}) => {
  const [isRoleReady, setIsRoleReady] = useState(initialRoleReady);

  useEffect(() => {
    setIsRoleReady(initialRoleReady);
  }, [initialRoleReady]);

  const handleProfileSelected = (profile: OnboardingAwsProfile) => {
    setIsRoleReady(false);
    onProfileSelected?.(profile);
  };

  const handleRoleCreated = (result: OnboardingIamRoleResponse) => {
    setIsRoleReady(true);
    onRoleCreated?.(result);
  };

  return (
    <div className="flex min-h-screen items-start justify-center bg-gradient-to-br from-primary-50 to-primary-100 p-6 dark:from-gray-900 dark:to-gray-950">
      <Card className="w-full max-w-6xl">
        <div className="space-y-6 p-8">
          <OnboardingStepIndicator steps={steps} currentStepId={currentStepId} />

          <div>
            <h1 className="text-3xl font-bold text-gray-900 dark:text-white">Create IAM Role</h1>
            <p className="mt-1 text-gray-600 dark:text-gray-400">
              Pick the AWS profile to use, create the read-only role, then continue to account settings.
            </p>
          </div>

          <IamRolePermissionsPanel
            initialProfileName={initialProfileName}
            onProfileSelected={handleProfileSelected}
            onRoleCreated={handleRoleCreated}
            onAccountResolved={onAccountResolved}
          />

          <div className="flex justify-between gap-3 border-t border-gray-200 pt-6 dark:border-gray-800">
            <Button type="button" variant="secondary" onClick={onBack}>
              <ArrowLeft className="mr-2" size={16} />
              Back
            </Button>
            <Button type="button" variant="primary" onClick={onContinue} disabled={!isRoleReady}>
              Continue
              <ArrowRight className="ml-2" size={16} />
            </Button>
          </div>
        </div>
      </Card>
    </div>
  );
};
