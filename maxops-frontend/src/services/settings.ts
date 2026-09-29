/** Settings and onboarding API service */
import apiClient from './api';
import type {
  OnboardingAwsProfilesResponse,
  OnboardingDraftRequest,
  OnboardingIamRoleResponse,
  OnboardingProgressRequest,
  OnboardingRequest,
  PricingDatabaseStatus,
  SettingsCatalogResponse,
  SettingsUpdateRequest,
  UserSettings,
} from '@/types/api';

export interface AwsProfileOption {
  profile_name: string;
  display_name: string;
  account_id: string | null;
  arn: string | null;
  is_default: boolean;
  error: string | null;
}

/**
 * Credentials for privileged setup work. Scans always run as the read-only
 * scan role; this profile is used only by operations that create or modify
 * AWS resources.
 */
export interface SetupCredentials {
  profile: string | null;
  scan_profile: string | null;
  using_scan_profile: boolean;
  available_profiles: AwsProfileOption[];
}

/**
 * Credentials every scan, check and action runs as. Distinct from
 * SetupCredentials, which covers privileged setup work only.
 */
export interface ScanCredentials {
  profile: string | null;
  using_default_chain: boolean;
  resolved_account_id: string | null;
  settings_account_id: string | null;
  account_mismatch: boolean;
  available_profiles: AwsProfileOption[];
  /** Present only on a confirmed account switch, which resets scanned data. */
  account_changed?: boolean;
  stopped_scans?: number;
  cleared_rows?: number;
}

export interface SetupRolePolicy {
  role_name: string;
  policy_name: string;
  policy: Record<string, unknown>;
}

export interface SetupRoleResult {
  role_name: string;
  role_arn: string;
  policy_name: string;
  aws_account_id: string;
  setup_profile_name: string;
  status: 'created' | 'updated_existing';
  message: string;
  selected_as_setup_profile: boolean;
  /** False when the written profile can't resolve credentials at all. */
  credentials_usable: boolean;
  credentials_error: string | null;
  credentials_hint: string | null;
}

export const settingsApi = {
  getSetupCredentials: async (): Promise<SetupCredentials> => {
    const response = await apiClient.get('/settings/setup-credentials');
    return response.data;
  },

  updateSetupCredentials: async (profile: string | null): Promise<SetupCredentials> => {
    const response = await apiClient.put('/settings/setup-credentials', { profile });
    return response.data;
  },

  getScanCredentials: async (): Promise<ScanCredentials> => {
    const response = await apiClient.get('/settings/scan-credentials');
    return response.data;
  },

  updateScanCredentials: async (
    profile: string | null,
    confirmAccountChange = false,
  ): Promise<ScanCredentials> => {
    const response = await apiClient.put('/settings/scan-credentials', {
      profile,
      confirm_account_change: confirmAccountChange,
    });
    return response.data;
  },

  getSetupRolePolicy: async (): Promise<SetupRolePolicy> => {
    const response = await apiClient.get('/settings/setup-credentials/policy');
    return response.data;
  },

  createSetupRole: async (profileName: string | null): Promise<SetupRoleResult> => {
    const response = await apiClient.post('/settings/setup-credentials/role', {
      profile_name: profileName,
    });
    return response.data;
  },

  getOnboardingStatus: async (): Promise<UserSettings | null> => {
    try {
      const response = await apiClient.get('/settings/onboarding');
      return response.data;
    } catch (error: any) {
      if (error.response?.status === 404) {
        return null; // Onboarding not completed
      }
      throw error;
    }
  },

  completeOnboarding: async (data: OnboardingRequest): Promise<UserSettings> => {
    const response = await apiClient.post('/settings/onboarding', data);
    return response.data;
  },

  saveOnboardingProgress: async (data: OnboardingProgressRequest): Promise<UserSettings> => {
    const response = await apiClient.patch('/settings/onboarding/progress', data);
    return response.data;
  },

  saveOnboardingDraft: async (data: OnboardingDraftRequest): Promise<UserSettings> => {
    const response = await apiClient.patch('/settings/onboarding/draft', data);
    return response.data;
  },

  getSettings: async (): Promise<UserSettings> => {
    const response = await apiClient.get('/settings');
    return response.data;
  },

  getSettingsCatalog: async (): Promise<SettingsCatalogResponse> => {
    const response = await apiClient.get('/settings/catalog');
    return response.data;
  },

  updateSettings: async (data: SettingsUpdateRequest): Promise<SettingsCatalogResponse> => {
    const response = await apiClient.put('/settings', data);
    return response.data;
  },
};

export const featureFlagsApi = {
  getFlags: async (): Promise<{ test_action: boolean }> => {
    const response = await apiClient.get('/settings/feature-flags');
    return response.data;
  },
};

export const onboardingApi = {
  getPricingDatabaseStatus: async (): Promise<PricingDatabaseStatus> => {
    const response = await apiClient.get('/onboarding/pricing-database');
    return response.data;
  },

  unpackPricingDatabase: async (force: boolean = false): Promise<{
    status: PricingDatabaseStatus;
    result: {
      artifact_path: string;
      sha256: string;
      db_path: string;
      size_bytes: number;
    };
  }> => {
    const response = await apiClient.post('/onboarding/pricing-database/unpack', { force });
    return response.data;
  },

  getIamProfiles: async (): Promise<OnboardingAwsProfilesResponse> => {
    const response = await apiClient.get('/onboarding/iam/profiles');
    return response.data;
  },

  createReadOnlyIamRole: async (profileName?: string | null): Promise<OnboardingIamRoleResponse> => {
    const response = await apiClient.post('/onboarding/iam/read-only-role', {
      profile_name: profileName || null,
    });
    return response.data;
  },

  useExistingIamRole: async (
    roleArn: string,
    profileName?: string | null
  ): Promise<OnboardingIamRoleResponse> => {
    const response = await apiClient.post('/onboarding/iam/use-existing-role', {
      role_arn: roleArn,
      profile_name: profileName || null,
    });
    return response.data;
  },

  runAllChecks: async (): Promise<{
    execution_id: number;
    total_checks: number;
    completed: number;
    failed: number;
    results: Array<{
      check_id: string;
      name: string;
      description: string;
      resource_type: string;
      status: 'completed' | 'failed';
      resources_found: number;
      error: string | null;
    }>;
  }> => {
    const response = await apiClient.post('/onboarding/run-all-checks');
    return response.data;
  },

  getExecutions: async (limit: number = 10): Promise<import('@/types/api').OnboardingExecution[]> => {
    const response = await apiClient.get(`/onboarding/executions?limit=${limit}`);
    return response.data;
  },

  getExecution: async (executionId: number): Promise<import('@/types/api').OnboardingExecution> => {
    const response = await apiClient.get(`/onboarding/executions/${executionId}`);
    return response.data;
  },

  saveResults: async (results: Array<{
    check_id: string;
    name: string;
    description: string;
    resource_type: string;
    status: 'completed' | 'failed';
    resources_found: number;
    error: string | null;
  }>): Promise<{ execution_id: number }> => {
    const response = await apiClient.post('/onboarding/save-results', results);
    return response.data;
  },
};
