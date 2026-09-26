import React, { useEffect, useMemo, useRef, useState } from 'react';
import { CheckCircle2, Download, FileJson, ShieldCheck, Wand2 } from 'lucide-react';
import { useQuery } from 'react-query';

import { Button } from '@/components/common/Button';
import {
  READ_ONLY_IAM_POLICY_FILENAME,
  READ_ONLY_IAM_POLICY_NAME,
  READ_ONLY_IAM_ROLE_NAME,
  readOnlyIamActionCount,
  readOnlyIamPermissionGroups,
  readOnlyIamPolicyJson,
} from '@/constants/iamReadOnlyPolicy';
import { onboardingApi } from '@/services/settings';
import type { OnboardingAwsProfile, OnboardingIamRoleResponse } from '@/types/api';

const DEFAULT_PROFILE_VALUE = '__maxops_default_profile__';
const IAM_PROFILES_QUERY_KEY = ['onboarding', 'iamProfiles'];
const DEFAULT_AWS_PROFILE: OnboardingAwsProfile = {
  profile_name: null,
  display_name: 'default',
  account_id: null,
  arn: null,
  is_default: true,
  error: null,
};

const formatResource = (resource: string | string[]) => {
  return Array.isArray(resource) ? resource.join(', ') : resource;
};

const getProfileValue = (profile: OnboardingAwsProfile) => profile.profile_name || DEFAULT_PROFILE_VALUE;
const getProfileValueFromName = (profileName?: string | null) => profileName || DEFAULT_PROFILE_VALUE;

const formatProfileLabel = (profile: OnboardingAwsProfile) => {
  const accountLabel = profile.account_id || 'account unavailable';
  return `${profile.display_name} (${accountLabel})`;
};

const getErrorMessage = (error: any) => {
  return error?.response?.data?.detail ||
    error?.message ||
    'Failed to load AWS profiles.';
};

const isAwsAccountId = (value?: string | null) => /^\d{12}$/.test(String(value ?? '').trim());

// Matches a full IAM role ARN, e.g. arn:aws:iam::123456789012:role/MyReadOnlyRole
// (partitions like aws-us-gov and role paths like role/team/MyRole included).
const IAM_ROLE_ARN_PATTERN = /^arn:aws[a-zA-Z-]*:iam::\d{12}:role\/[\w+=,.@/-]+$/;

interface IamRolePermissionsPanelProps {
  initialProfileName?: string | null;
  onProfileSelected?: (profile: OnboardingAwsProfile) => void;
  onRoleCreated?: (result: OnboardingIamRoleResponse) => void;
  onAccountResolved?: (accountId: string) => void;
}

export const IamRolePermissionsPanel: React.FC<IamRolePermissionsPanelProps> = ({
  initialProfileName,
  onProfileSelected,
  onRoleCreated,
  onAccountResolved,
}) => {
  const [selectedProfileValue, setSelectedProfileValue] = useState(() => getProfileValueFromName(initialProfileName));
  const [isCreatingRole, setIsCreatingRole] = useState(false);
  const [createRoleError, setCreateRoleError] = useState<string | null>(null);
  const [createRoleResult, setCreateRoleResult] = useState<OnboardingIamRoleResponse | null>(null);
  const [existingRoleArn, setExistingRoleArn] = useState('');
  const [isUsingExistingRole, setIsUsingExistingRole] = useState(false);

  const {
    data: profilesResponse,
    error: profilesQueryError,
    isLoading: isProfilesQueryLoading,
    refetch: refetchProfiles,
  } = useQuery(IAM_PROFILES_QUERY_KEY, onboardingApi.getIamProfiles, {
    staleTime: Infinity,
    cacheTime: Infinity,
    refetchOnMount: false,
    refetchOnWindowFocus: false,
    retry: false,
  });

  const profiles = useMemo(() => {
    const loadedProfiles = profilesResponse?.profiles ?? [];
    return loadedProfiles.length > 0 ? loadedProfiles : [DEFAULT_AWS_PROFILE];
  }, [profilesResponse?.profiles]);

  const isLoadingProfiles = isProfilesQueryLoading && !profilesResponse;
  const profilesError = profilesQueryError ? getErrorMessage(profilesQueryError) : null;

  useEffect(() => {
    setSelectedProfileValue((currentValue) => {
      const currentStillExists = profiles.some((profile) => getProfileValue(profile) === currentValue);
      return currentStillExists ? currentValue : getProfileValue(profiles[0]);
    });
  }, [profiles]);

  // Restores whichever profile was previously saved (e.g. from an earlier
  // onboarding session) once the real profiles list has loaded. This should
  // only ever apply that one time on load -- without the ref guard, it
  // re-fires on every subsequent profiles refetch (including the one
  // activateScanProfile triggers after a successful role creation) and would
  // stomp back over a deliberate selection change with this stale prop.
  const hasAppliedInitialProfileRef = useRef(false);
  useEffect(() => {
    if (initialProfileName === undefined || hasAppliedInitialProfileRef.current) {
      return;
    }

    const initialProfileValue = getProfileValueFromName(initialProfileName);
    const initialProfileExists = profiles.some((profile) => getProfileValue(profile) === initialProfileValue);
    if (initialProfileExists) {
      setSelectedProfileValue(initialProfileValue);
      hasAppliedInitialProfileRef.current = true;
    }
  }, [initialProfileName, profiles]);

  const selectedProfile =
    profiles.find((profile) => getProfileValue(profile) === selectedProfileValue) || profiles[0] || DEFAULT_AWS_PROFILE;

  const handleProfileChange = (event: React.ChangeEvent<HTMLSelectElement>) => {
    const nextProfileValue = event.target.value;
    const profileActuallyChanged = nextProfileValue !== selectedProfileValue;
    setSelectedProfileValue(nextProfileValue);

    // A native <select> can fire onChange even when the user re-selects the
    // value it already had (e.g. re-opening the dropdown and clicking the
    // same option). Only treat this as "switched to a different profile" --
    // which the parent uses as a signal to require re-creating the role --
    // when the value genuinely changed. Otherwise a harmless re-click would
    // wipe out an already-successful role creation and re-disable Continue.
    if (!profileActuallyChanged) {
      return;
    }

    setCreateRoleError(null);
    setCreateRoleResult(null);

    const nextProfile = profiles.find((profile) => getProfileValue(profile) === nextProfileValue);
    if (nextProfile) {
      onProfileSelected?.(nextProfile);
    }
  };

  // After a successful create/register, the "AWS profile" dropdown was still
  // showing whatever source profile had been used to set the role up (e.g.
  // "maxops"), while the newly active MaxOpsReadOnlyRole scan profile sat
  // further down the list -- often still labeled "account unavailable"
  // because the profiles list is fetched once (staleTime: Infinity) and
  // never refreshed, so it can't reflect a role that didn't exist yet at
  // page load. Refetch so it resolves correctly, then switch the dropdown to
  // show the scan profile that's now actually active, confirming setup
  // succeeded. This calls the state setter directly (not the onChange
  // handler), so it does NOT trip the "profile changed, role needs
  // recreating" reset in the parent.
  const activateScanProfile = async (scanProfileName?: string | null) => {
    await refetchProfiles();
    if (scanProfileName) {
      setSelectedProfileValue(getProfileValueFromName(scanProfileName));
    }
  };

  // The scan profile is read-only by design -- it can't create or update IAM
  // resources, so using it as the "AWS profile" source for Create Role would
  // always fail. Once activateScanProfile selects it for display, guard
  // against that footgun rather than let the button fail confusingly.
  const isScanProfileSelected =
    Boolean(createRoleResult?.scan_profile_name) &&
    selectedProfileValue === getProfileValueFromName(createRoleResult?.scan_profile_name);

  const handleDownload = () => {
    const blob = new Blob([readOnlyIamPolicyJson], { type: 'application/json' });
    const url = URL.createObjectURL(blob);
    const link = document.createElement('a');
    link.href = url;
    link.download = READ_ONLY_IAM_POLICY_FILENAME;
    document.body.appendChild(link);
    link.click();
    document.body.removeChild(link);
    URL.revokeObjectURL(url);
  };

  const handleCreateRole = async () => {
    setIsCreatingRole(true);
    setCreateRoleError(null);
    setCreateRoleResult(null);

    try {
      const result = await onboardingApi.createReadOnlyIamRole(selectedProfile.profile_name);
      const resolvedAccountId = isAwsAccountId(result.aws_account_id)
        ? result.aws_account_id
        : selectedProfile.account_id;
      const accountIdForAutofill = String(resolvedAccountId ?? '').trim();
      setCreateRoleResult(result);
      onRoleCreated?.(result);
      if (isAwsAccountId(accountIdForAutofill)) {
        onAccountResolved?.(accountIdForAutofill);
      }
      // Show the created role's ARN in the existing-role box too, so the
      // active ARN is visible (and copyable) right where manual entry goes.
      if (result.role_arn) {
        setExistingRoleArn(result.role_arn);
      }
      await activateScanProfile(result.scan_profile_name);
    } catch (error: any) {
      setCreateRoleError(
        error?.response?.data?.detail ||
          error?.message ||
          `Failed to create ${READ_ONLY_IAM_ROLE_NAME}.`
      );
    } finally {
      setIsCreatingRole(false);
    }
  };

  const trimmedExistingRoleArn = existingRoleArn.trim();
  const existingRoleArnFormatError =
    trimmedExistingRoleArn.length > 0 && !IAM_ROLE_ARN_PATTERN.test(trimmedExistingRoleArn)
      ? 'Enter a full IAM role ARN, e.g. arn:aws:iam::123456789012:role/MyReadOnlyRole.'
      : null;

  const handleUseExistingRole = async () => {
    const trimmedArn = existingRoleArn.trim();
    if (!trimmedArn || !IAM_ROLE_ARN_PATTERN.test(trimmedArn)) {
      return;
    }

    setIsUsingExistingRole(true);
    setCreateRoleError(null);
    setCreateRoleResult(null);

    try {
      const result = await onboardingApi.useExistingIamRole(trimmedArn, selectedProfile.profile_name);
      const resolvedAccountId = isAwsAccountId(result.aws_account_id)
        ? result.aws_account_id
        : selectedProfile.account_id;
      const accountIdForAutofill = String(resolvedAccountId ?? '').trim();
      setCreateRoleResult(result);
      onRoleCreated?.(result);
      if (isAwsAccountId(accountIdForAutofill)) {
        onAccountResolved?.(accountIdForAutofill);
      }
      await activateScanProfile(result.scan_profile_name);
    } catch (error: any) {
      setCreateRoleError(
        error?.response?.data?.detail || error?.message || 'Failed to register the existing role.'
      );
    } finally {
      setIsUsingExistingRole(false);
    }
  };

  return (
    <section className="rounded-lg border border-gray-200 bg-gray-50 p-5 dark:border-gray-800 dark:bg-gray-950">
      <div className="flex min-w-0 flex-col gap-4">
        <div className="min-w-0 space-y-3">
          <div className="flex items-center gap-2 text-sm font-semibold uppercase tracking-[0.18em] text-primary-700 dark:text-primary-300">
            <ShieldCheck size={16} />
            IAM Role
          </div>
          <div>
            <h2 className="text-2xl font-semibold text-gray-900 dark:text-white">
              Read-only scan permissions
            </h2>
            <p className="mt-2 text-sm leading-6 text-gray-600 dark:text-gray-400">
              Create or attach this policy to the AWS role MaxOps will use for onboarding scans. It covers the current
              checks and pricing lookup with read-only List, Get, and Describe permissions only. CUR export setup, S3
              write access, and remediation actions are intentionally excluded.
            </p>
          </div>
        </div>
        <div className="flex w-full flex-col gap-3 sm:flex-row sm:flex-wrap sm:items-end sm:justify-end">
          <div className="w-full sm:w-80">
            <label
              htmlFor="iam-profile-select"
              className="mb-1 block text-xs font-semibold uppercase tracking-[0.16em] text-gray-500 dark:text-gray-400"
            >
              AWS profile
            </label>
            <select
              id="iam-profile-select"
              value={selectedProfileValue}
              onChange={handleProfileChange}
              disabled={isLoadingProfiles || isCreatingRole}
              className="h-10 w-full rounded-md border border-gray-300 bg-white px-3 text-sm font-medium text-gray-800 shadow-sm focus:border-primary-500 focus:outline-none focus:ring-2 focus:ring-primary-500/20 disabled:cursor-not-allowed disabled:opacity-50 dark:border-gray-700 dark:bg-gray-900 dark:text-gray-100"
            >
              {profiles.map((profile) => (
                <option key={getProfileValue(profile)} value={getProfileValue(profile)}>
                  {isLoadingProfiles ? 'Loading profiles...' : formatProfileLabel(profile)}
                </option>
              ))}
            </select>
            {isScanProfileSelected ? (
              <p className="mt-1 break-words text-xs text-gray-500 dark:text-gray-400">
                This is the read-only scan profile MaxOps just set up -- it can't create or update IAM resources.
                Pick a different profile here to re-create or update the role.
              </p>
            ) : (
              (profilesError || selectedProfile.error) && (
                <p className="mt-1 break-words text-xs text-gray-500 dark:text-gray-400">
                  {profilesError || selectedProfile.error}
                </p>
              )
            )}
          </div>
          <Button
            type="button"
            variant="primary"
            onClick={handleCreateRole}
            isLoading={isCreatingRole}
            disabled={isLoadingProfiles || isScanProfileSelected}
            className="w-full sm:w-auto"
          >
            <Wand2 className="mr-2" size={16} />
            Create Role
          </Button>
          <Button type="button" variant="secondary" onClick={handleDownload} className="w-full sm:w-auto">
            <Download className="mr-2" size={16} />
            Download JSON
          </Button>
        </div>
      </div>

      <div className="mt-4 rounded-lg border border-dashed border-gray-300 bg-white p-4 dark:border-gray-700 dark:bg-gray-900">
        <p className="text-sm font-semibold text-gray-900 dark:text-white">
          Credentials can't create IAM roles?
        </p>
        <p className="mt-1 text-sm text-gray-600 dark:text-gray-400">
          Download the policy JSON above, have someone with IAM access create a role with it (or use an existing
          read-only role), then paste its ARN here. MaxOps won't create or modify anything in AWS for this option.
        </p>
        <div className="mt-3 flex flex-col gap-3 sm:flex-row">
          <input
            type="text"
            value={existingRoleArn}
            onChange={(event) => setExistingRoleArn(event.target.value)}
            placeholder="arn:aws:iam::123456789012:role/MyReadOnlyRole"
            disabled={isUsingExistingRole}
            aria-invalid={Boolean(existingRoleArnFormatError)}
            className={`h-10 w-full flex-1 rounded-md border bg-white px-3 text-sm font-medium text-gray-800 shadow-sm focus:outline-none focus:ring-2 disabled:cursor-not-allowed disabled:opacity-50 dark:bg-gray-900 dark:text-gray-100 ${
              existingRoleArnFormatError
                ? 'border-danger-400 focus:border-danger-500 focus:ring-danger-500/20 dark:border-danger-700'
                : 'border-gray-300 focus:border-primary-500 focus:ring-primary-500/20 dark:border-gray-700'
            }`}
          />
          <Button
            type="button"
            variant="secondary"
            onClick={handleUseExistingRole}
            isLoading={isUsingExistingRole}
            disabled={!trimmedExistingRoleArn || Boolean(existingRoleArnFormatError)}
            className="w-full sm:w-auto"
          >
            Use this role
          </Button>
        </div>
        {existingRoleArnFormatError && (
          <p className="mt-2 text-xs text-danger-600 dark:text-danger-400">{existingRoleArnFormatError}</p>
        )}
      </div>

      {createRoleResult && (
        <div className="mt-5 rounded-lg border border-gray-200 bg-white p-4 text-sm shadow-sm dark:border-gray-800 dark:bg-gray-900">
          <div className="flex items-start gap-3">
            <div className="flex h-9 w-9 shrink-0 items-center justify-center rounded-lg bg-gray-100 text-gray-700 dark:bg-gray-950 dark:text-gray-300">
              <CheckCircle2 size={18} />
            </div>
            <div className="min-w-0 flex-1">
              <div className="flex flex-wrap items-center gap-2">
                <p className="font-semibold text-gray-900 dark:text-white">Role ready</p>
                <span className="rounded-md border border-gray-200 bg-gray-100 px-2 py-0.5 text-xs font-medium text-gray-700 dark:border-gray-800 dark:bg-gray-950 dark:text-gray-300">
                  {createRoleResult.status === 'created'
                    ? 'Created'
                    : createRoleResult.status === 'existing_role_registered'
                      ? 'Using existing role'
                      : 'Updated'}
                </span>
              </div>
              <p className="mt-1 text-gray-600 dark:text-gray-400">{createRoleResult.message}</p>
              <div className="mt-3 grid gap-3 sm:grid-cols-2">
                <div>
                  <p className="text-xs font-semibold uppercase tracking-[0.16em] text-gray-500 dark:text-gray-400">
                    Source Profile
                  </p>
                  <code className="mt-1 block break-all rounded-md bg-gray-100 px-3 py-2 text-xs text-gray-800 dark:bg-gray-950 dark:text-gray-200">
                    {createRoleResult.aws_profile_name || 'default'}
                  </code>
                </div>
                {createRoleResult.scan_profile_name && (
                  <div>
                    <p className="text-xs font-semibold uppercase tracking-[0.16em] text-gray-500 dark:text-gray-400">
                      MaxOps Profile
                    </p>
                    <code className="mt-1 block break-all rounded-md bg-gray-100 px-3 py-2 text-xs text-gray-800 dark:bg-gray-950 dark:text-gray-200">
                      {createRoleResult.scan_profile_name}
                    </code>
                  </div>
                )}
                {(createRoleResult.aws_account_id || selectedProfile.account_id) && (
                  <div>
                    <p className="text-xs font-semibold uppercase tracking-[0.16em] text-gray-500 dark:text-gray-400">
                      Account
                    </p>
                    <code className="mt-1 block break-all rounded-md bg-gray-100 px-3 py-2 text-xs text-gray-800 dark:bg-gray-950 dark:text-gray-200">
                      {createRoleResult.aws_account_id || selectedProfile.account_id}
                    </code>
                  </div>
                )}
              </div>
              {createRoleResult.role_arn && (
                <div className="mt-3">
                  <p className="text-xs font-semibold uppercase tracking-[0.16em] text-gray-500 dark:text-gray-400">
                    Role ARN
                  </p>
                  <code className="mt-1 block break-all rounded-md bg-gray-100 px-3 py-2 text-xs text-gray-800 dark:bg-gray-950 dark:text-gray-200">
                    {createRoleResult.role_arn}
                  </code>
                </div>
              )}
              {createRoleResult.scan_profile_config_path && (
                <div className="mt-3">
                  <p className="text-xs font-semibold uppercase tracking-[0.16em] text-gray-500 dark:text-gray-400">
                    AWS Config
                  </p>
                  <code className="mt-1 block break-all rounded-md bg-gray-100 px-3 py-2 text-xs text-gray-800 dark:bg-gray-950 dark:text-gray-200">
                    {createRoleResult.scan_profile_config_path}
                  </code>
                </div>
              )}
              {createRoleResult.trusted_principal_arn && (
                <div className="mt-3">
                  <p className="text-xs font-semibold uppercase tracking-[0.16em] text-gray-500 dark:text-gray-400">
                    Trusted Principal
                  </p>
                  <code className="mt-1 block break-all rounded-md bg-gray-100 px-3 py-2 text-xs text-gray-800 dark:bg-gray-950 dark:text-gray-200">
                    {createRoleResult.trusted_principal_arn}
                  </code>
                </div>
              )}
            </div>
          </div>
        </div>
      )}

      {createRoleError && (
        <div className="mt-5 rounded-lg border border-gray-200 bg-white p-4 text-sm shadow-sm dark:border-gray-800 dark:bg-gray-900">
          <p className="font-semibold text-danger-700 dark:text-danger-300">
            Failed to create {READ_ONLY_IAM_ROLE_NAME}
          </p>
          <p className="mt-1 break-words text-gray-600 dark:text-gray-400">{createRoleError}</p>
        </div>
      )}

      <div className="mt-5 grid gap-3 sm:grid-cols-3">
        <div className="rounded-lg border border-gray-200 bg-white p-4 dark:border-gray-800 dark:bg-gray-900">
          <p className="text-xs font-semibold uppercase tracking-[0.16em] text-gray-500 dark:text-gray-400">
            Role
          </p>
          <p className="mt-2 break-all text-sm font-semibold text-gray-900 dark:text-white">
            {READ_ONLY_IAM_ROLE_NAME}
          </p>
        </div>
        <div className="rounded-lg border border-gray-200 bg-white p-4 dark:border-gray-800 dark:bg-gray-900">
          <p className="text-xs font-semibold uppercase tracking-[0.16em] text-gray-500 dark:text-gray-400">
            Policy
          </p>
          <p className="mt-2 break-all text-sm font-semibold text-gray-900 dark:text-white">
            {READ_ONLY_IAM_POLICY_NAME}
          </p>
        </div>
        <div className="rounded-lg border border-gray-200 bg-white p-4 dark:border-gray-800 dark:bg-gray-900">
          <p className="text-xs font-semibold uppercase tracking-[0.16em] text-gray-500 dark:text-gray-400">
            Actions
          </p>
          <p className="mt-2 text-2xl font-semibold text-gray-900 dark:text-white">{readOnlyIamActionCount}</p>
        </div>
      </div>

      <div className="mt-5 grid gap-5 xl:grid-cols-[0.9fr_1.1fr]">
        <div className="space-y-3">
          <div className="flex items-center gap-2 text-sm font-semibold text-gray-900 dark:text-white">
            <FileJson size={16} className="text-primary-600 dark:text-primary-300" />
            Permission groups
          </div>
          <div className="max-h-[28rem] space-y-3 overflow-y-auto pr-1">
            {readOnlyIamPermissionGroups.map((group) => (
              <div
                key={group.sid}
                className="rounded-lg border border-gray-200 bg-white p-4 dark:border-gray-800 dark:bg-gray-900"
              >
                <div className="flex flex-wrap items-center justify-between gap-2">
                  <h3 className="text-sm font-semibold text-gray-900 dark:text-white">{group.sid}</h3>
                  <span className="text-xs text-gray-500 dark:text-gray-400">
                    {group.actions.length} action{group.actions.length === 1 ? '' : 's'}
                  </span>
                </div>
                <p className="mt-1 break-all text-xs text-gray-500 dark:text-gray-400">
                  Resource: {formatResource(group.resource)}
                </p>
                <div className="mt-3 flex flex-wrap gap-2">
                  {group.actions.map((action) => (
                    <span
                      key={action}
                      className="break-all rounded-md bg-gray-100 px-2 py-1 text-xs font-medium text-gray-700 dark:bg-gray-800 dark:text-gray-200"
                    >
                      {action}
                    </span>
                  ))}
                </div>
              </div>
            ))}
          </div>
        </div>

        <div className="min-w-0">
          <div className="mb-3 flex items-center gap-2 text-sm font-semibold text-gray-900 dark:text-white">
            <FileJson size={16} className="text-primary-600 dark:text-primary-300" />
            Policy JSON
          </div>
          <pre className="max-h-[28rem] overflow-auto rounded-lg border border-gray-900 bg-gray-950 p-4 text-xs leading-5 text-gray-100">
            <code>{readOnlyIamPolicyJson}</code>
          </pre>
        </div>
      </div>
    </section>
  );
};
