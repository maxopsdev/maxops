import React, { useState } from 'react';
import { AlertTriangle, KeyRound, RotateCcw } from 'lucide-react';
import { useMutation, useQuery, useQueryClient } from 'react-query';

import { Button } from '@/components/common/Button';
import { Card } from '@/components/common/Card';
import { settingsApi, type AwsProfileOption } from '@/services/settings';

const DEFAULT_CHAIN_VALUE = '__maxops_default_chain__';
const SCAN_CREDENTIALS_QUERY_KEY = ['settings', 'scanCredentials'];

type PendingAccountChange = {
  profile: string | null;
  currentAccountId: string | null;
  newAccountId: string | null;
  runningScans: number;
  message: string;
};

const profileLabel = (profile: AwsProfileOption) => {
  const account = profile.account_id || 'account unavailable';
  return `${profile.display_name} (${account})`;
};

export const ScanCredentialsCard: React.FC = () => {
  const queryClient = useQueryClient();
  const [pendingChange, setPendingChange] = useState<PendingAccountChange | null>(null);

  const { data: credentials, isLoading } = useQuery(
    SCAN_CREDENTIALS_QUERY_KEY,
    settingsApi.getScanCredentials,
  );

  const changeProfile = useMutation(
    ({ profile, confirmAccountChange }: { profile: string | null; confirmAccountChange?: boolean }) =>
      settingsApi.updateScanCredentials(profile, confirmAccountChange),
    {
      onSuccess: (updated) => {
        setPendingChange(null);
        queryClient.setQueryData(SCAN_CREDENTIALS_QUERY_KEY, updated);
        // Every AWS-derived view now comes from different credentials. Listing
        // the affected keys here would go stale as views are added, and the
        // app mixes string and array keys, so drop the lot.
        queryClient.invalidateQueries();
      },
      onError: (error: any, variables) => {
        const detail = error?.response?.data?.detail;
        // 409 is not a failure: the account differs and the reset needs a yes.
        if (error?.response?.status === 409 && detail?.code === 'account_change_requires_confirmation') {
          setPendingChange({
            profile: variables.profile,
            currentAccountId: detail.current_account_id ?? null,
            newAccountId: detail.new_account_id ?? null,
            runningScans: detail.running_scans ?? 0,
            message: detail.message ?? '',
          });
        }
      },
    },
  );

  const selectedValue = credentials?.profile ?? DEFAULT_CHAIN_VALUE;
  const isBusy = isLoading || changeProfile.isLoading;
  const needsConfirmation = Boolean(pendingChange);
  const failedOutright = changeProfile.isError && !needsConfirmation;

  const handleChange = (value: string) => {
    changeProfile.mutate({ profile: value === DEFAULT_CHAIN_VALUE ? null : value });
  };

  return (
    <>
      <Card className="rounded-[1.75rem] border border-gray-200 bg-white p-0 shadow-sm dark:border-gray-800 dark:bg-gray-900">
        <div className="space-y-4 p-5">
          <div className="flex items-center gap-2">
            <KeyRound size={16} className="text-primary-600 dark:text-gray-300" />
            <h2 className="text-sm font-semibold uppercase tracking-[0.18em] text-gray-700 dark:text-gray-300">
              Scan Credentials
            </h2>
          </div>

          <p className="text-sm text-gray-600 dark:text-gray-400">
            The AWS profile every scan, check and action runs as. Switching to another role in
            the same account takes effect on the next scan and keeps your findings. Switching
            to a different account starts over.
          </p>

          <div className="space-y-2">
            <label
              htmlFor="scan-profile"
              className="block text-xs font-medium uppercase tracking-wide text-gray-500 dark:text-gray-400"
            >
              AWS profile
            </label>
            <select
              id="scan-profile"
              value={selectedValue}
              disabled={isBusy}
              onChange={(event) => handleChange(event.target.value)}
              className="w-full rounded-md border border-gray-300 bg-white px-3 py-2 text-sm text-gray-900 disabled:opacity-60 dark:border-gray-600 dark:bg-gray-800 dark:text-gray-100"
            >
              <option value={DEFAULT_CHAIN_VALUE}>Reset to the default credential chain</option>
              {(credentials?.available_profiles ?? [])
                .filter((profile) => profile.profile_name)
                .map((profile) => (
                  <option key={profile.profile_name} value={profile.profile_name as string}>
                    {profileLabel(profile)}
                  </option>
                ))}
            </select>
          </div>

          {credentials?.using_default_chain && (
            <p className="flex items-start gap-2 text-xs text-gray-500 dark:text-gray-400">
              <RotateCcw size={14} className="mt-0.5 shrink-0" />
              No profile is set. MaxOps falls back to MAXOPS_AWS_PROFILE and then the default
              credential chain, the same as a machine that never ran onboarding.
            </p>
          )}

          {credentials?.account_mismatch && (
            <p className="text-xs text-amber-700 dark:text-amber-500">
              This profile resolves to account {credentials.resolved_account_id}, but Global
              Settings records {credentials.settings_account_id}. Scans will report resources
              from {credentials.resolved_account_id} until the account number is updated too.
            </p>
          )}

          {failedOutright && (
            <p className="text-xs text-red-600 dark:text-red-400">
              Could not change the scan profile. Complete onboarding first, then try again.
            </p>
          )}
        </div>
      </Card>

      {pendingChange && (
        <div className="fixed inset-0 z-[60] flex items-center justify-center bg-black/40 p-4 backdrop-blur-sm">
          <div className="w-full max-w-lg space-y-4 rounded-2xl border border-gray-200 bg-white p-6 shadow-xl dark:border-gray-700 dark:bg-gray-900">
            <div className="flex items-center gap-2">
              <AlertTriangle size={18} className="text-warning-600 dark:text-warning-500" />
              <h3 className="text-base font-semibold text-gray-900 dark:text-gray-100">
                Change AWS account?
              </h3>
            </div>

            <p className="text-sm text-gray-600 dark:text-gray-400">
              That profile belongs to account {pendingChange.newAccountId}, not{' '}
              {pendingChange.currentAccountId}.
            </p>

            <p className="text-sm font-medium text-warning-700 dark:text-warning-500">
              The MaxOps database will be emptied — every finding, inventory row, snooze,
              scan, action history entry and tuned check setting from{' '}
              {pendingChange.currentAccountId}
              {pendingChange.runningScans > 0
                ? `, and ${pendingChange.runningScans} running scan${
                    pendingChange.runningScans === 1 ? '' : 's'
                  } will be stopped`
                : ''}
. Default check policies are restored and MaxOps starts from scratch on the
              new account. This cannot be undone.
            </p>

            <div className="flex justify-end gap-2 pt-2">
              <Button variant="ghost" onClick={() => setPendingChange(null)}>
                Cancel
              </Button>
              <Button
                variant="primary"
                isLoading={changeProfile.isLoading}
                onClick={() =>
                  changeProfile.mutate({
                    profile: pendingChange.profile,
                    confirmAccountChange: true,
                  })
                }
              >
                Reset and switch account
              </Button>
            </div>
          </div>
        </div>
      )}
    </>
  );
};
