import React, { useCallback, useEffect, useState } from 'react';
import { WelcomeScreen } from '@/components/onboarding/WelcomeScreen';
import { RoleCreationView } from '@/components/onboarding/RoleCreationView';
import { EnvironmentSetupView } from '@/components/onboarding/EnvironmentSetupView';
import { ChecksExecutionView } from '@/components/onboarding/ChecksExecutionView';
import { OnboardingResultsView } from '@/components/onboarding/OnboardingResultsView';
import { settingsApi, onboardingApi } from '@/services/settings';
import { useMutation, useQuery, useQueryClient } from 'react-query';
import { useLocation, useNavigate } from 'react-router-dom';
import type {
  OnboardingAwsProfile,
  OnboardingDraftRequest,
  OnboardingExecution,
  OnboardingIamRoleResponse,
  OnboardingRequest,
  PricingDatabaseStatus,
  UserSettings,
} from '@/types/api';

const SETUP_STEPS = [
  { id: 'information', label: 'Information' },
  { id: 'role', label: 'IAM Role' },
  { id: 'account', label: 'Account Settings' },
] as const;

type SetupStep = typeof SETUP_STEPS[number]['id'];
type OnboardingStep = SetupStep | 'checks' | 'results';

const SETUP_STEP_IDS = new Set<string>(SETUP_STEPS.map((step) => step.id));

const getSetupStepFromStatus = (status?: UserSettings | null): SetupStep => {
  const step = status?.onboarding_step;
  return step && SETUP_STEP_IDS.has(step) ? (step as SetupStep) : 'information';
};

const getSetupStepFromSearch = (search: string): SetupStep | null => {
  const requestedStep = new URLSearchParams(search).get('step');
  return requestedStep && SETUP_STEP_IDS.has(requestedStep)
    ? (requestedStep as SetupStep)
    : null;
};

const resolveSetupStep = (search: string, status?: UserSettings | null): SetupStep => {
  return getSetupStepFromSearch(search) ?? getSetupStepFromStatus(status);
};

const getSavedIamProfileName = (status?: UserSettings | null): string | null | undefined => {
  const roleCreationData = status?.onboarding_data?.role_creation;
  if (roleCreationData && typeof roleCreationData === 'object' && 'selected_profile_name' in roleCreationData) {
    const profileName = (roleCreationData as { selected_profile_name?: unknown }).selected_profile_name;
    return typeof profileName === 'string' ? profileName : null;
  }

  const iamRoleData = status?.onboarding_data?.iam_role;
  if (iamRoleData && typeof iamRoleData === 'object' && 'aws_profile_name' in iamRoleData) {
    const profileName = (iamRoleData as { aws_profile_name?: unknown }).aws_profile_name;
    return typeof profileName === 'string' ? profileName : null;
  }

  return undefined;
};

const hasSavedIamRole = (status?: UserSettings | null): boolean => {
  const iamRoleData = status?.onboarding_data?.iam_role;
  if (!iamRoleData || typeof iamRoleData !== 'object') {
    return false;
  }

  const profileName = (iamRoleData as { scan_profile_name?: unknown }).scan_profile_name;
  const roleArn = (iamRoleData as { role_arn?: unknown }).role_arn;
  return typeof profileName === 'string' && profileName.trim().length > 0
    && typeof roleArn === 'string' && roleArn.trim().length > 0;
};

export const OnboardingPage: React.FC = () => {
  const navigate = useNavigate();
  const location = useLocation();
  const queryClient = useQueryClient();
  const cachedStatus = queryClient.getQueryData<UserSettings | null>('onboarding-status');
  const [step, setStep] = useState<OnboardingStep>(() => resolveSetupStep(location.search, cachedStatus));
  const [accountAutofill, setAccountAutofill] = useState<{ accountId: string; nonce: number } | null>(null);
  const [executionId, setExecutionId] = useState<number | null>(null);

  const { data: onboardingStatus } = useQuery<UserSettings | null>(
    'onboarding-status',
    () => settingsApi.getOnboardingStatus(),
    {
      retry: false,
    }
  );

  const { data: pricingDatabaseStatus, refetch: refetchPricingDatabaseStatus } = useQuery<PricingDatabaseStatus>(
    ['onboarding-pricing-database-status'],
    () => onboardingApi.getPricingDatabaseStatus(),
    {
      retry: false,
    }
  );

  const unpackPricingDatabaseMutation = useMutation(
    () => onboardingApi.unpackPricingDatabase(false),
    {
      onSuccess: async () => {
        await refetchPricingDatabaseStatus();
      },
      onError: (error: any) => {
        console.error('Failed to extract pricing database:', error);
        const errorMessage = error?.response?.data?.detail || error?.message || 'Failed to extract pricing database.';
        alert(`Failed to extract pricing database: ${errorMessage}`);
      },
    }
  );

  useEffect(() => {
    const requestedStep = resolveSetupStep(location.search, onboardingStatus);
    if (requestedStep !== step && step !== 'checks' && step !== 'results') {
      setStep(requestedStep);
    }
  }, [location.search, onboardingStatus, step]);

  const onboardingMutation = useMutation(
    (data: OnboardingRequest) => settingsApi.completeOnboarding(data),
    {
      onSuccess: (data) => {
        // Persist onboarding status immediately for routing decisions
        queryClient.setQueryData('onboarding-status', data);
        setStep('checks');
      },
      onError: (error: any) => {
        console.error('Failed to save onboarding data:', error);
        const errorMessage = error?.response?.data?.detail || error?.message || 'Failed to save settings. Please try again.';
        alert(`Failed to save settings: ${errorMessage}`);
      },
    }
  );

  // Fetch execution details when we have an execution ID
  const { data: execution } = useQuery<OnboardingExecution>(
    ['onboarding-execution', executionId],
    () => onboardingApi.getExecution(executionId!),
    {
      enabled: !!executionId && step === 'results',
    }
  );

  const onboardingInitialValues: Partial<OnboardingRequest> | undefined = onboardingStatus
    ? {
        environment: onboardingStatus.environment,
        account: onboardingStatus.account,
        region: onboardingStatus.region,
        regions: onboardingStatus.regions,
      }
    : undefined;
  const initialIamProfileName = getSavedIamProfileName(onboardingStatus);
  const initialIamRoleReady = hasSavedIamRole(onboardingStatus);

  const goToSetupStep = async (nextStep: SetupStep) => {
    try {
      const savedStatus = await settingsApi.saveOnboardingProgress({
        onboarding_step: nextStep,
      });
      queryClient.setQueryData('onboarding-status', savedStatus);
    } catch (error) {
      console.error('Failed to save onboarding progress:', error);
    }

    setStep(nextStep);
    navigate(`/onboarding?step=${nextStep}`);
  };

  const handleWelcomeContinue = () => {
    goToSetupStep('role');
  };

  const handleRoleContinue = () => {
    goToSetupStep('account');
  };

  const handleRoleProfileSelected = useCallback((profile: OnboardingAwsProfile) => {
    settingsApi.saveOnboardingProgress({
      onboarding_data: {
        role_creation: {
          selected_profile_name: profile.profile_name,
          selected_profile_display_name: profile.display_name,
          selected_profile_account_id: profile.account_id ?? null,
        },
      },
    })
      .then((savedStatus) => {
        queryClient.setQueryData('onboarding-status', savedStatus);
      })
      .catch((error) => {
        console.error('Failed to save IAM role onboarding draft:', error);
      });
  }, [queryClient]);

  const handleRoleCreated = useCallback((result: OnboardingIamRoleResponse) => {
    queryClient.setQueryData<UserSettings | null | undefined>('onboarding-status', (current) => {
      if (!current) {
        return current;
      }
      return {
        ...current,
        account: result.aws_account_id || current.account,
        onboarding_step: 'account',
        onboarding_data: {
          ...(current.onboarding_data || {}),
          iam_role: result,
        },
      };
    });
  }, [queryClient]);

  const handleAccountResolved = (accountId: string) => {
    setAccountAutofill((current) => ({
      accountId,
      nonce: (current?.nonce ?? 0) + 1,
    }));
    queryClient.setQueryData<UserSettings | null | undefined>('onboarding-status', (current) => {
      if (!current) {
        return current;
      }
      return {
        ...current,
        account: accountId,
        onboarding_step: 'account',
      };
    });
  };

  const handleAccountDraftChange = useCallback((data: OnboardingDraftRequest) => {
    settingsApi.saveOnboardingDraft(data)
      .then((savedStatus) => {
        queryClient.setQueryData('onboarding-status', savedStatus);
      })
      .catch((error) => {
        console.error('Failed to save onboarding draft:', error);
      });
  }, [queryClient]);

  const handleEnvironmentSubmit = (data: OnboardingRequest) => {
    onboardingMutation.mutate(data);
  };

  const handleChecksComplete = (id?: number, error?: string) => {
    // If there's an error (cancellation or other), navigate to dashboard with error
    if (error) {
      navigate('/dashboard', { 
        state: { 
          error: error,
          errorType: 'run_canceled'
        } 
      });
      return;
    }
    
    // If no error and we have an execution ID, show results
    if (id) {
      setExecutionId(id);
      setStep('results');
    } else {
      // If no ID provided, try to get the latest execution
      onboardingApi.getExecutions(1).then((executions) => {
        if (executions.length > 0) {
          setExecutionId(executions[0].id);
          setStep('results');
        } else {
          // No execution found, navigate to dashboard
          navigate('/dashboard');
        }
      }).catch(() => {
        // Error fetching executions, navigate to dashboard
        navigate('/dashboard');
      });
    }
  };

  const handleViewDashboard = () => {
    navigate('/dashboard');
  };

  return (
    <>
      {step === 'information' && (
        <WelcomeScreen
          steps={SETUP_STEPS}
          currentStepId="information"
          onContinue={handleWelcomeContinue}
          pricingDatabaseStatus={pricingDatabaseStatus}
          isUnpackingPricingDatabase={unpackPricingDatabaseMutation.isLoading}
          onUnpackPricingDatabase={() => unpackPricingDatabaseMutation.mutate()}
        />
      )}

      {step === 'role' && (
        <RoleCreationView
          steps={SETUP_STEPS}
          currentStepId="role"
          initialProfileName={initialIamProfileName}
          initialRoleReady={initialIamRoleReady}
          onBack={() => goToSetupStep('information')}
          onContinue={handleRoleContinue}
          onProfileSelected={handleRoleProfileSelected}
          onRoleCreated={handleRoleCreated}
          onAccountResolved={handleAccountResolved}
        />
      )}

      {step === 'account' && (
        <EnvironmentSetupView
          steps={SETUP_STEPS}
          currentStepId="account"
          initialValues={onboardingInitialValues}
          accountAutofill={accountAutofill}
          onSubmit={handleEnvironmentSubmit}
          onDraftChange={handleAccountDraftChange}
          onBack={() => goToSetupStep('role')}
          isSubmitting={onboardingMutation.isLoading}
        />
      )}
      
      {step === 'checks' && <ChecksExecutionView onComplete={handleChecksComplete} />}

      {step === 'results' && execution && (
        <OnboardingResultsView execution={execution} onViewDashboard={handleViewDashboard} />
      )}

    </>
  );
};
