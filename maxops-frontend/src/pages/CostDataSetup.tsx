import React from 'react';
import { Link } from 'react-router-dom';
import { useMutation, useQuery, useQueryClient } from 'react-query';
import {
  AlertCircle,
  AlertTriangle,
  ArrowLeft,
  CheckCircle2,
  Circle,
  Clock,
  Download,
  HelpCircle,
  KeyRound,
  Loader2,
  RefreshCw,
} from 'lucide-react';

import { Layout } from '@/components/layout/Layout';
import { Card } from '@/components/common/Card';
import { Button } from '@/components/common/Button';
import {
  costDataApi,
  type CostDataJob,
  type CostDataSetupStatus,
  type CostDataStep,
  type CostDataStepState,
} from '@/services/costData';
import { settingsApi, type SetupCredentials } from '@/services/settings';

const STEP_ICONS: Record<CostDataStepState, React.ElementType> = {
  done: CheckCircle2,
  waiting: Clock,
  action_required: Circle,
  error: AlertCircle,
  unknown: HelpCircle,
};

const STEP_ICON_COLORS: Record<CostDataStepState, string> = {
  done: 'text-emerald-500',
  waiting: 'text-amber-500',
  action_required: 'text-gray-400 dark:text-gray-500',
  error: 'text-red-500',
  unknown: 'text-gray-400 dark:text-gray-600',
};

const STEP_BADGES: Record<CostDataStepState, { label: string; className: string }> = {
  done: {
    label: 'Done',
    className: 'bg-emerald-50 text-emerald-700 dark:bg-emerald-500/10 dark:text-emerald-400',
  },
  waiting: {
    label: 'Waiting on AWS',
    className: 'bg-amber-50 text-amber-700 dark:bg-amber-500/10 dark:text-amber-400',
  },
  action_required: {
    label: 'Not set up',
    className: 'bg-gray-100 text-gray-600 dark:bg-gray-700/50 dark:text-gray-300',
  },
  error: {
    label: 'Error',
    className: 'bg-red-50 text-red-700 dark:bg-red-500/10 dark:text-red-400',
  },
  unknown: {
    label: 'Unknown',
    className: 'bg-gray-100 text-gray-500 dark:bg-gray-700/50 dark:text-gray-400',
  },
};

/** "3 minutes ago" — so a failure card says when, not just what. */
const formatWhen = (iso?: string | null): string => {
  if (!iso) return 'time unknown';
  // Backend timestamps are UTC; some arrive without an offset.
  const normalized = /[Z+]|-\d{2}:\d{2}$/.test(iso) ? iso : `${iso}Z`;
  const then = new Date(normalized).getTime();
  if (Number.isNaN(then)) return 'time unknown';

  const seconds = Math.round((Date.now() - then) / 1000);
  if (seconds < 0) return 'just now';
  if (seconds < 60) return `${seconds}s ago`;
  const minutes = Math.round(seconds / 60);
  if (minutes < 60) return `${minutes} minute${minutes === 1 ? '' : 's'} ago`;
  const hours = Math.round(minutes / 60);
  if (hours < 24) return `${hours} hour${hours === 1 ? '' : 's'} ago`;
  const days = Math.round(hours / 24);
  return `${days} day${days === 1 ? '' : 's'} ago`;
};

const formatBytes = (bytes?: number): string => {
  if (bytes == null) return '—';
  const units = ['B', 'KB', 'MB', 'GB', 'TB'];
  let value = bytes;
  let unit = 0;
  while (value >= 1024 && unit < units.length - 1) {
    value /= 1024;
    unit += 1;
  }
  return `${value.toFixed(value < 10 && unit > 0 ? 1 : 0)} ${units[unit]}`;
};

const JobProgress: React.FC<{ job: CostDataJob }> = ({ job }) => {
  const { current = 0, total = 0, message } = job.progress ?? {};
  const pct = total > 0 ? Math.round((current / total) * 100) : null;

  return (
    <div className="mt-3 rounded-md border border-primary-200 bg-primary-50/60 p-3 dark:border-primary-900/50 dark:bg-primary-500/5">
      <div className="flex items-center gap-2 text-sm text-primary-900 dark:text-primary-200">
        <Loader2 className="h-4 w-4 animate-spin" />
        <span>{message || 'Working…'}</span>
        {pct !== null && <span className="ml-auto font-mono text-xs">{current}/{total}</span>}
      </div>
      {pct !== null && (
        <div className="mt-2 h-1.5 w-full overflow-hidden rounded-full bg-primary-100 dark:bg-primary-900/40">
          <div
            className="h-full rounded-full bg-primary-500 transition-all duration-500"
            style={{ width: `${pct}%` }}
          />
        </div>
      )}
    </div>
  );
};

/** Inline confirmation shown before anything is created or any query is billed. */
const ConfirmPanel: React.FC<{
  title: string;
  children: React.ReactNode;
  confirmLabel: string;
  onConfirm: () => void;
  onCancel: () => void;
  isLoading?: boolean;
  blocked?: boolean;
}> = ({ title, children, confirmLabel, onConfirm, onCancel, isLoading, blocked }) => (
  <div className="mt-3 rounded-md border border-gray-200 bg-gray-50 p-4 dark:border-gray-700 dark:bg-gray-800/60">
    <h4 className="text-sm font-semibold text-gray-900 dark:text-white">{title}</h4>
    <div className="mt-2 space-y-2 text-sm text-gray-600 dark:text-gray-400">{children}</div>
    <div className="mt-4 flex flex-wrap gap-2">
      <Button onClick={onConfirm} isLoading={isLoading} disabled={blocked}>
        {confirmLabel}
      </Button>
      <Button variant="secondary" onClick={onCancel}>
        Cancel
      </Button>
    </div>
  </div>
);

export const CostDataSetupPage: React.FC = () => {
  const queryClient = useQueryClient();
  const [confirming, setConfirming] = React.useState<'export' | 'refresh' | null>(null);
  const [actionError, setActionError] = React.useState<string | null>(null);

  const { data, isLoading, isFetching, error, refetch } = useQuery<CostDataSetupStatus>(
    ['cost-data-setup-status'],
    () => costDataApi.getSetupStatus(),
    {
      // Poll quickly while a job is in flight, slowly otherwise: the probes
      // make real AWS calls, and the delivery step can sit for a day.
      refetchInterval: (status) =>
        status?.running?.export || status?.running?.refresh ? 2_000 : 60_000,
    }
  );

  const runningExport = data?.running?.export ?? null;
  const runningRefresh = data?.running?.refresh ?? null;

  const preflightQuery = useQuery(
    ['cost-data-preflight', confirming],
    () => costDataApi.getPreflight(confirming as 'export' | 'refresh'),
    { enabled: confirming !== null, retry: false }
  );

  const estimateQuery = useQuery(
    ['cost-data-refresh-estimate'],
    () => costDataApi.getRefreshEstimate(),
    { enabled: confirming === 'refresh', retry: false }
  );

  const invalidate = () => queryClient.invalidateQueries(['cost-data-setup-status']);

  const exportMutation = useMutation(() => costDataApi.startExport(), {
    onSuccess: () => {
      setConfirming(null);
      setActionError(null);
      invalidate();
    },
    onError: (err: any) =>
      setActionError(err?.response?.data?.detail ?? err?.message ?? 'Could not start the export.'),
  });

  const refreshMutation = useMutation(() => costDataApi.startRefresh(), {
    onSuccess: () => {
      setConfirming(null);
      setActionError(null);
      invalidate();
    },
    onError: (err: any) =>
      setActionError(err?.response?.data?.detail ?? err?.message ?? 'Could not start the refresh.'),
  });

  const { data: credentials } = useQuery<SetupCredentials>(
    ['setup-credentials'],
    () => settingsApi.getSetupCredentials(),
    { retry: false }
  );

  const [createWithProfile, setCreateWithProfile] = React.useState('');

  const createRoleMutation = useMutation(
    (profileName: string | null) => settingsApi.createSetupRole(profileName),
    {
      onSuccess: () => {
        setActionError(null);
        queryClient.invalidateQueries(['setup-credentials']);
        queryClient.invalidateQueries(['cost-data-preflight']);
        invalidate();
      },
      onError: (err: any) =>
        setActionError(
          err?.response?.data?.detail ?? err?.message ?? 'Could not create the setup role.'
        ),
    }
  );

  const downloadPolicy = async () => {
    try {
      const { role_name, policy } = await settingsApi.getSetupRolePolicy();
      const blob = new Blob([JSON.stringify(policy, null, 2)], { type: 'application/json' });
      const url = URL.createObjectURL(blob);
      const link = document.createElement('a');
      link.href = url;
      link.download = `${role_name}-policy.json`;
      link.click();
      URL.revokeObjectURL(url);
    } catch (err: any) {
      setActionError(err?.message ?? 'Could not download the policy.');
    }
  };

  const credentialsMutation = useMutation(
    (profile: string | null) => settingsApi.updateSetupCredentials(profile),
    {
      onSuccess: () => {
        setActionError(null);
        queryClient.invalidateQueries(['setup-credentials']);
        queryClient.invalidateQueries(['cost-data-preflight']);
        invalidate();
      },
      onError: (err: any) =>
        setActionError(
          err?.response?.data?.detail ?? err?.message ?? 'Could not change the setup profile.'
        ),
    }
  );

  const pricingMutation = useMutation(
    (enabled: boolean) => costDataApi.setPricingEnabled(enabled),
    {
      onSuccess: () => {
        setActionError(null);
        invalidate();
        queryClient.invalidateQueries(['settings-cost-data-status']);
      },
      onError: (err: any) =>
        setActionError(
          err?.response?.data?.detail ?? err?.message ?? 'Could not change the pricing setting.'
        ),
    }
  );

  const renderStepAction = (step: CostDataStep) => {
    if (step.id === 'export') {
      if (runningExport) return <JobProgress job={runningExport} />;
      // Offered when nothing exists yet, and when what exists is broken — a
      // stale Athena table is only fixable by re-running this.
      const needsRepair = step.state === 'error';
      if (step.state !== 'action_required' && !needsRepair) return null;
      if (confirming === 'export') {
        const preflight = preflightQuery.data;
        return (
          <ConfirmPanel
            title={
              needsRepair
                ? 'Repair the export and its Athena table?'
                : 'Create the Cost and Usage Report export?'
            }
            confirmLabel={needsRepair ? 'Repair export' : 'Create export'}
            onConfirm={() => exportMutation.mutate()}
            onCancel={() => setConfirming(null)}
            isLoading={exportMutation.isLoading}
            blocked={preflight ? !preflight.can_proceed : false}
          >
            {needsRepair ? (
              <p>
                This recreates the Athena table so it reads the export MaxOps manages. Dropping
                and recreating the table removes no data from S3 — it only changes where the
                table looks. The export itself is reused if it already exists.
              </p>
            ) : (
              <p>
                This creates an S3 bucket in your account, a daily CUR v2 export, and an Athena
                table. It does not change any existing resources.
              </p>
            )}
            {preflightQuery.isLoading && <p>Checking permissions…</p>}
            {preflight && !preflight.can_proceed && (
              <div className="rounded-md bg-red-50 p-3 text-red-800 dark:bg-red-500/10 dark:text-red-300">
                <p className="font-medium">{preflight.message}</p>
                <ul className="mt-1 list-inside list-disc font-mono text-xs">
                  {preflight.denied_actions.map((action) => (
                    <li key={action}>{action}</li>
                  ))}
                </ul>
              </div>
            )}
            {preflight && preflight.can_proceed && !preflight.simulated && (
              <p className="text-amber-700 dark:text-amber-400">{preflight.message}</p>
            )}
            {preflight && preflight.can_proceed && preflight.simulated && (
              <p className="text-emerald-700 dark:text-emerald-400">{preflight.message}</p>
            )}
          </ConfirmPanel>
        );
      }
      return (
        <div className="mt-3">
          <Button variant="secondary" onClick={() => setConfirming('export')}>
            {needsRepair ? 'Repair export' : 'Create export'}
          </Button>
        </div>
      );
    }

    if (step.id === 'cache') {
      if (runningRefresh) return <JobProgress job={runningRefresh} />;
      if (step.state === 'unknown') return null;
      if (confirming === 'refresh') {
        const preflight = preflightQuery.data;
        const estimate = estimateQuery.data;
        return (
          <ConfirmPanel
            title="Summarise your billing data?"
            confirmLabel="Run summary"
            onConfirm={() => refreshMutation.mutate()}
            onCancel={() => setConfirming(null)}
            isLoading={refreshMutation.isLoading}
            blocked={preflight ? !preflight.can_proceed : false}
          >
            <p>
              This runs Athena queries over your Cost and Usage Report and stores the results
              locally. <strong>Athena bills per terabyte scanned.</strong>
            </p>
            {estimateQuery.isLoading && <p>Estimating…</p>}
            {estimate && (
              <div className="rounded-md bg-white p-3 dark:bg-gray-900/60">
                <dl className="space-y-1">
                  <div className="flex justify-between gap-4">
                    <dt>Queries to run</dt>
                    <dd className="font-mono">{estimate.query_count}</dd>
                  </div>
                  <div className="flex justify-between gap-4">
                    <dt>Months</dt>
                    <dd className="font-mono">
                      {estimate.months.length > 0
                        ? `${estimate.months[0]} → ${estimate.months[estimate.months.length - 1]}`
                        : '—'}
                    </dd>
                  </div>
                  <div className="flex justify-between gap-4">
                    <dt>Report size</dt>
                    <dd className="font-mono">{formatBytes(estimate.export_bytes)}</dd>
                  </div>
                  <div className="flex justify-between gap-4">
                    <dt>Estimated cost</dt>
                    <dd className="font-mono">
                      {estimate.estimated_usd == null
                        ? 'unknown'
                        : `$${estimate.estimated_usd.toFixed(2)}`}
                    </dd>
                  </div>
                </dl>
                <p className="mt-2 text-xs text-gray-500 dark:text-gray-500">{estimate.note}</p>
              </div>
            )}
            {preflight && !preflight.can_proceed && (
              <div className="rounded-md bg-red-50 p-3 text-red-800 dark:bg-red-500/10 dark:text-red-300">
                <p className="font-medium">{preflight.message}</p>
                <ul className="mt-1 list-inside list-disc font-mono text-xs">
                  {preflight.denied_actions.map((action) => (
                    <li key={action}>{action}</li>
                  ))}
                </ul>
              </div>
            )}
          </ConfirmPanel>
        );
      }
      return (
        <div className="mt-3">
          <Button variant="secondary" onClick={() => setConfirming('refresh')}>
            {step.state === 'done' ? 'Refresh now' : 'Summarise billing data'}
          </Button>
        </div>
      );
    }

    if (step.id === 'pricing') {
      const enabled = step.enabled === true;
      return (
        <div className="mt-3 flex flex-wrap items-center gap-3">
          <Button
            variant={enabled ? 'secondary' : 'primary'}
            onClick={() => pricingMutation.mutate(!enabled)}
            isLoading={pricingMutation.isLoading}
          >
            {enabled ? 'Turn off cost-data pricing' : 'Use actual cost for pricing'}
          </Button>
          {enabled && (
            <span className="text-xs text-gray-500 dark:text-gray-400">
              Takes effect on the next scan.
            </span>
          )}
        </div>
      );
    }

    return null;
  };

  // The newest job of each type, kept only if it failed.
  //
  // Showing "the newest failed job" alone hid the export failure behind a
  // later refresh failure, and kept displaying failures that a subsequent
  // successful run had already superseded.
  const failedJobs = React.useMemo(() => {
    const newestByType = new Map<string, CostDataJob>();
    for (const job of data?.jobs ?? []) {
      // Jobs arrive newest-first, so the first of each type is the current one.
      if (!newestByType.has(job.job_type)) newestByType.set(job.job_type, job);
    }
    return Array.from(newestByType.values()).filter((job) => job.status === 'failed');
  }, [data?.jobs]);

  return (
    <Layout>
      <div className="space-y-6">
        <div>
          <Link
            to="/settings"
            className="inline-flex items-center gap-1 text-sm text-gray-500 hover:text-gray-700 dark:text-gray-400 dark:hover:text-gray-200"
          >
            <ArrowLeft className="h-4 w-4" />
            Settings
          </Link>
          <div className="mt-2 flex flex-wrap items-start justify-between gap-4">
            <div>
              <h1 className="text-3xl font-bold text-gray-900 dark:text-white">Cost data</h1>
              <p className="mt-1 max-w-3xl text-gray-600 dark:text-gray-400">
                By default MaxOps prices findings from public list prices. Connect your AWS Cost
                and Usage Report and it will use what your resources actually cost instead —
                including your Reserved Instance and Savings Plan discounts.
              </p>
            </div>
            <button
              type="button"
              onClick={() => refetch()}
              disabled={isFetching}
              className="inline-flex items-center gap-2 rounded-md border border-gray-300 px-3 py-2 text-sm font-medium text-gray-700 hover:bg-gray-50 disabled:opacity-50 dark:border-gray-600 dark:text-gray-200 dark:hover:bg-gray-800"
            >
              <RefreshCw className={`h-4 w-4 ${isFetching ? 'animate-spin' : ''}`} />
              Re-check
            </button>
          </div>
        </div>

        {actionError && (
          <Card>
            <div className="flex gap-3">
              <AlertCircle className="mt-0.5 h-5 w-5 shrink-0 text-red-500" />
              <p className="text-sm text-gray-700 dark:text-gray-300">{actionError}</p>
            </div>
          </Card>
        )}

        {credentials && (
          <Card>
            <div className="flex flex-wrap items-start justify-between gap-4">
              <div className="min-w-0">
                <div className="flex items-center gap-2">
                  <KeyRound className="h-4 w-4 text-gray-500 dark:text-gray-400" />
                  <h2 className="text-sm font-semibold text-gray-900 dark:text-white">
                    Setup credentials
                  </h2>
                </div>
                <p className="mt-2 max-w-2xl text-sm text-gray-600 dark:text-gray-400">
                  Scans always run as your read-only role
                  {credentials.scan_profile ? (
                    <>
                      {' '}
                      (<code className="font-mono text-xs">{credentials.scan_profile}</code>)
                    </>
                  ) : null}
                  , which deliberately can't create AWS resources. Setting up cost data needs to
                  create an export and run Athena queries, so choose a profile with those rights.
                  It is used <strong>only</strong> for the actions on this page.
                </p>
                {credentials.using_scan_profile && (
                  <p className="mt-2 text-sm text-amber-700 dark:text-amber-400">
                    No setup profile chosen — setup actions will use the read-only role and fail.
                  </p>
                )}
              </div>
              <div className="w-full sm:w-72">
                <label
                  htmlFor="setup-profile"
                  className="block text-xs font-medium text-gray-500 dark:text-gray-400"
                >
                  AWS profile for setup
                </label>
                <select
                  id="setup-profile"
                  className="mt-1 w-full rounded-md border border-gray-300 bg-white px-3 py-2 text-sm text-gray-900 dark:border-gray-600 dark:bg-gray-800 dark:text-gray-100"
                  value={credentials.profile ?? ''}
                  disabled={credentialsMutation.isLoading}
                  onChange={(event) =>
                    credentialsMutation.mutate(event.target.value || null)
                  }
                >
                  <option value="">Use the read-only scan role</option>
                  {credentials.available_profiles.map((profile) => (
                    <option key={profile.profile_name} value={profile.profile_name}>
                      {profile.display_name}
                      {profile.account_id ? ` · ${profile.account_id}` : ''}
                    </option>
                  ))}
                </select>
              </div>
            </div>

            <div className="mt-4 border-t border-gray-200 pt-4 dark:border-gray-700">
              <h3 className="text-sm font-medium text-gray-900 dark:text-white">
                Don't have a profile with these permissions?
              </h3>
              <p className="mt-1 max-w-2xl text-sm text-gray-600 dark:text-gray-400">
                MaxOps can create a dedicated <code className="font-mono text-xs">MaxOpsCostDataRole</code>{' '}
                in your account, scoped to just this job — creating the report bucket, registering
                the export, and running the summary queries. It is a separate role from the one
                used for scanning, so this doesn't widen what a scan can do.
              </p>

              <div className="mt-3 flex flex-wrap items-end gap-3">
                <div className="w-full sm:w-64">
                  <label
                    htmlFor="create-role-with"
                    className="block text-xs font-medium text-gray-500 dark:text-gray-400"
                  >
                    Create it using
                  </label>
                  <select
                    id="create-role-with"
                    className="mt-1 w-full rounded-md border border-gray-300 bg-white px-3 py-2 text-sm text-gray-900 dark:border-gray-600 dark:bg-gray-800 dark:text-gray-100"
                    value={createWithProfile}
                    onChange={(event) => setCreateWithProfile(event.target.value)}
                  >
                    <option value="">Default credentials</option>
                    {credentials.available_profiles.map((profile) => (
                      <option key={profile.profile_name} value={profile.profile_name}>
                        {profile.display_name}
                        {profile.account_id ? ` · ${profile.account_id}` : ''}
                      </option>
                    ))}
                  </select>
                </div>
                <Button
                  variant="secondary"
                  onClick={() => createRoleMutation.mutate(createWithProfile || null)}
                  isLoading={createRoleMutation.isLoading}
                >
                  Create role
                </Button>
                <Button variant="ghost" onClick={downloadPolicy}>
                  <Download className="mr-2 h-4 w-4" />
                  Download policy JSON
                </Button>
              </div>

              <p className="mt-2 text-xs text-gray-500 dark:text-gray-500">
                Creating the role needs credentials that can make IAM roles. If yours can't,
                download the policy and ask an administrator to create the role, then pick its
                profile above.
              </p>

              {createRoleMutation.isSuccess && createRoleMutation.data && (
                <div
                  className={
                    createRoleMutation.data.credentials_usable
                      ? 'mt-3 rounded-md border border-emerald-200 bg-emerald-50 p-3 text-sm text-emerald-900 dark:border-emerald-900/50 dark:bg-emerald-500/10 dark:text-emerald-200'
                      : 'mt-3 rounded-md border border-amber-200 bg-amber-50 p-3 text-sm text-amber-900 dark:border-amber-900/50 dark:bg-amber-500/10 dark:text-amber-200'
                  }
                >
                  <p>{createRoleMutation.data.message}</p>
                  <p className="mt-1 font-mono text-xs">{createRoleMutation.data.role_arn}</p>

                  {/* The role can be created successfully while the profile it
                      wrote is unusable — saying only "created" then sends the
                      user off to debug a page that can't reach AWS. */}
                  {!createRoleMutation.data.credentials_usable && (
                    <div className="mt-3 border-t border-amber-300/60 pt-3 dark:border-amber-800/60">
                      <p className="font-medium">
                        The role was created, but the AWS profile it wrote can't sign in.
                      </p>
                      {createRoleMutation.data.credentials_hint && (
                        <p className="mt-1">{createRoleMutation.data.credentials_hint}</p>
                      )}
                      {createRoleMutation.data.credentials_error && (
                        <pre className="mt-2 overflow-x-auto rounded bg-white/60 p-2 text-xs dark:bg-black/20">
                          {createRoleMutation.data.credentials_error}
                        </pre>
                      )}
                    </div>
                  )}
                </div>
              )}
            </div>
          </Card>
        )}

        {data && !data.aws_available && (
          <Card>
            <div className="flex gap-3">
              <AlertTriangle className="mt-0.5 h-5 w-5 shrink-0 text-amber-500" />
              <div>
                <h2 className="text-sm font-semibold text-gray-900 dark:text-white">
                  Can't reach AWS
                </h2>
                <p className="mt-1 text-sm text-gray-600 dark:text-gray-400">
                  The AWS steps below can't be checked. The local steps are still accurate.
                </p>
                {data.aws_error && (
                  <pre className="mt-2 overflow-x-auto rounded-md bg-gray-50 p-3 text-xs text-gray-600 dark:bg-gray-800/60 dark:text-gray-400">
                    {data.aws_error}
                  </pre>
                )}
              </div>
            </div>
          </Card>
        )}

        <Card>
          {isLoading && (
            <p className="py-8 text-center text-sm text-gray-500 dark:text-gray-400">
              Checking your cost data pipeline…
            </p>
          )}

          {error != null && !isLoading && (
            <div className="flex gap-3 py-2">
              <AlertCircle className="mt-0.5 h-5 w-5 shrink-0 text-red-500" />
              <p className="text-sm text-gray-600 dark:text-gray-400">
                Could not load setup status. Check that the MaxOps backend is running.
              </p>
            </div>
          )}

          {data && (
            <ol className="mt-1">
              {data.steps.map((step, index) => {
                const Icon = STEP_ICONS[step.state] ?? HelpCircle;
                const badge = STEP_BADGES[step.state] ?? STEP_BADGES.unknown;
                const isLast = index === data.steps.length - 1;

                return (
                  <li key={step.id} className="relative flex gap-4 pb-8 last:pb-0">
                    {!isLast && (
                      <span
                        aria-hidden="true"
                        className="absolute left-[11px] top-7 h-full w-px bg-gray-200 dark:bg-gray-700"
                      />
                    )}
                    <Icon
                      className={`relative mt-0.5 h-6 w-6 shrink-0 bg-white dark:bg-gray-900 ${
                        STEP_ICON_COLORS[step.state]
                      }`}
                    />
                    <div className="min-w-0 flex-1">
                      <div className="flex flex-wrap items-center gap-x-3 gap-y-1">
                        <h3 className="text-sm font-semibold text-gray-900 dark:text-white">
                          {index + 1}. {step.label}
                        </h3>
                        <span
                          className={`rounded-full px-2 py-0.5 text-xs font-medium ${badge.className}`}
                        >
                          {badge.label}
                        </span>
                      </div>
                      <p className="mt-1 text-sm leading-6 text-gray-600 dark:text-gray-400">
                        {step.detail}
                      </p>

                      {step.remedy && (
                        <div className="mt-2 flex gap-2 rounded-md border border-amber-200 bg-amber-50 p-3 text-sm text-amber-900 dark:border-amber-900/50 dark:bg-amber-500/10 dark:text-amber-200">
                          <KeyRound className="mt-0.5 h-4 w-4 shrink-0" />
                          <div>
                            <p>{step.remedy}</p>
                            {step.denied_action && (
                              <p className="mt-1 font-mono text-xs opacity-80">
                                Needs: {step.denied_action}
                              </p>
                            )}
                          </div>
                        </div>
                      )}

                      {step.months && step.months.length > 0 && (
                        <p className="mt-1 font-mono text-xs text-gray-500 dark:text-gray-500">
                          {step.months.join(', ')}
                        </p>
                      )}

                      {step.error && (
                        <pre className="mt-2 overflow-x-auto rounded-md bg-gray-50 p-3 text-xs text-gray-600 dark:bg-gray-800/60 dark:text-gray-400">
                          {step.error}
                        </pre>
                      )}

                      {renderStepAction(step)}
                    </div>
                  </li>
                );
              })}
            </ol>
          )}
        </Card>

        {failedJobs.map((job) => (
          <Card key={job.id}>
            <div className="flex gap-3">
              <AlertCircle className="mt-0.5 h-5 w-5 shrink-0 text-red-500" />
              <div className="min-w-0">
                <h2 className="text-sm font-semibold text-gray-900 dark:text-white">
                  {job.job_type === 'export' ? 'Export' : 'Summary'} attempt failed{' '}
                  <span className="font-normal text-gray-500 dark:text-gray-400">
                    · {formatWhen(job.completed_at ?? job.started_at)}
                  </span>
                </h2>
                <p className="mt-0.5 text-xs text-gray-500 dark:text-gray-500">
                  Attempt #{job.id}
                  {job.completed_at
                    ? ` · ${new Date(
                        /[Z+]|-\d{2}:\d{2}$/.test(job.completed_at)
                          ? job.completed_at
                          : `${job.completed_at}Z`
                      ).toLocaleString()}`
                    : ''}
                </p>
                <pre className="mt-2 overflow-x-auto rounded-md bg-gray-50 p-3 text-xs text-gray-600 dark:bg-gray-800/60 dark:text-gray-400">
                  {job.error}
                </pre>
              </div>
            </div>
          </Card>
        ))}

        {data && (
          <Card>
            <h2 className="text-sm font-semibold text-gray-900 dark:text-white">Details</h2>
            <dl className="mt-3 grid grid-cols-1 gap-x-8 gap-y-2 text-sm sm:grid-cols-2">
              <div className="flex justify-between gap-4 sm:block">
                <dt className="text-gray-500 dark:text-gray-400">AWS account</dt>
                <dd className="font-mono text-gray-900 dark:text-gray-200">
                  {data.account_id ?? '—'}
                </dd>
              </div>
              <div className="flex justify-between gap-4 sm:block">
                <dt className="text-gray-500 dark:text-gray-400">Region</dt>
                <dd className="font-mono text-gray-900 dark:text-gray-200">{data.region}</dd>
              </div>
              <div className="flex justify-between gap-4 sm:block">
                <dt className="text-gray-500 dark:text-gray-400">Report bucket</dt>
                <dd className="break-all font-mono text-gray-900 dark:text-gray-200">
                  {data.bucket ?? '—'}
                </dd>
              </div>
              {data.diagnostics.map((diagnostic) => (
                <div key={diagnostic.id} className="flex justify-between gap-4 sm:block">
                  <dt className="text-gray-500 dark:text-gray-400">{diagnostic.label}</dt>
                  <dd className="text-gray-900 dark:text-gray-200">{diagnostic.detail}</dd>
                </div>
              ))}
            </dl>

            {/* Only show a diagnostic's remedy if no step is already showing
                it. A duplicate down here reads as a second, separate problem
                with no button next to it. */}
            {data.diagnostics
              .filter(
                (diagnostic) =>
                  diagnostic.remedy &&
                  !data.steps.some((step) => step.remedy === diagnostic.remedy)
              )
              .map((diagnostic) => (
                <div
                  key={`${diagnostic.id}-remedy`}
                  className="mt-3 flex gap-2 rounded-md border border-amber-200 bg-amber-50 p-3 text-sm text-amber-900 dark:border-amber-900/50 dark:bg-amber-500/10 dark:text-amber-200"
                >
                  <KeyRound className="mt-0.5 h-4 w-4 shrink-0" />
                  <div>
                    <p>{diagnostic.remedy}</p>
                    {diagnostic.denied_action && (
                      <p className="mt-1 font-mono text-xs opacity-80">
                        Needs: {diagnostic.denied_action}
                      </p>
                    )}
                  </div>
                </div>
              ))}
          </Card>
        )}
      </div>
    </Layout>
  );
};
