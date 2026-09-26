import React, { useMemo, useState } from 'react';
import { useNavigate, useParams } from 'react-router-dom';
import { useQuery } from 'react-query';
import { Bar, BarChart, CartesianGrid, Cell, Pie, PieChart, ResponsiveContainer, Tooltip, XAxis, YAxis } from 'recharts';
import { ArrowLeft, CopyCheck, Globe, Play, ShieldCheck, Sparkles } from 'lucide-react';
import { Layout } from '@/components/layout/Layout';
import { Card } from '@/components/common/Card';
import { getActionsForCheck } from '@/components/checks/checkActions';
import { useOptimizationProfile } from '@/contexts/OptimizationProfileContext';
import { useTheme } from '@/contexts/ThemeContext';
import { checksApi } from '@/services/checks';
import { inventoryApi } from '@/services/inventory';
import { adjustOptimizationSavings } from '@/utils/optimizationProfile';
import { CHART_COLORS, chartGridColor, chartTickColor } from '@/styles/chartColors';

const formatCurrency = (value: number) =>
  new Intl.NumberFormat('en-US', { style: 'currency', currency: 'USD', maximumFractionDigits: 0 }).format(value);
const formatLargeNumber = (value: number) => new Intl.NumberFormat('en-US').format(value);
const formatDateTime = (value: string | null | undefined) =>
  value
    ? new Date(value).toLocaleString('en-US', {
        year: 'numeric',
        month: 'short',
        day: 'numeric',
        hour: 'numeric',
        minute: '2-digit',
      })
    : 'Unavailable';
const titleCase = (value: string) =>
  value
    .replace(/([a-z])([A-Z])/g, '$1 $2')
    .split(/[_\s-]+/)
    .filter(Boolean)
    .map((part) => part.charAt(0).toUpperCase() + part.slice(1))
    .join(' ');
const isActionErrorMessage = (value: string) => /failed|error|missing/i.test(value);

const DetailMetric: React.FC<{ label: string; value: string; subtitle: string }> = ({ label, value, subtitle }) => (
  <Card className="border-gray-200 bg-white/95 dark:border-gray-700 dark:bg-gray-900/95">
    <div className="text-xs font-semibold uppercase tracking-[0.18em] text-gray-500 dark:text-gray-400">{label}</div>
    <div className="mt-3 text-3xl font-semibold text-gray-900 dark:text-white">{value}</div>
    <div className="mt-2 text-sm text-gray-500 dark:text-gray-400">{subtitle}</div>
  </Card>
);

const PostureBadge: React.FC<{ label: string; ok: boolean }> = ({ label, ok }) => (
  <div className={`rounded-2xl border px-4 py-3 ${ok ? 'border-success-200 bg-success-50 dark:border-success-900/50 dark:bg-success-900/30' : 'border-gray-200 bg-gray-50 dark:border-gray-700 dark:bg-gray-800/60'}`}>
    <div className="text-sm font-medium text-gray-800 dark:text-gray-100">{label}</div>
    <div className="mt-2 text-xs uppercase tracking-[0.16em] text-gray-500 dark:text-gray-400">{ok ? 'Enabled' : 'Gap detected'}</div>
  </div>
);

export const S3BucketDetailsPage: React.FC = () => {
  const navigate = useNavigate();
  const { bucketId } = useParams<{ bucketId: string }>();
  const { profile } = useOptimizationProfile();
  const { theme } = useTheme();
  const [selectedAction, setSelectedAction] = useState('');
  const [isExecutingAction, setIsExecutingAction] = useState(false);
  const [actionMessage, setActionMessage] = useState('');

  const { data, isLoading, isError } = useQuery('inventory-s3-overview', () => inventoryApi.getS3Overview());
  const bucket = useMemo(() => data?.buckets.find((entry) => entry.resource_id === bucketId), [bucketId, data?.buckets]);
  const { data: check } = useQuery(['s3-check-metadata', bucket?.maxops.check_id], () => checksApi.getCheck(bucket!.maxops.check_id!), {
    enabled: Boolean(bucket?.maxops.check_id),
    retry: false,
  });

  if (isLoading) {
    return (
      <Layout>
        <div className="flex min-h-[60vh] items-center justify-center rounded-[32px] border border-gray-200 bg-white/90 dark:border-gray-700 dark:bg-gray-900/90">
          <div className="text-center">
            <div className="text-lg font-semibold text-gray-800 dark:text-gray-100">Loading S3 bucket details</div>
            <div className="mt-2 text-sm text-gray-500 dark:text-gray-400">Preparing imported posture and savings data.</div>
          </div>
        </div>
      </Layout>
    );
  }

  if (isError || !data || !bucket) {
    return (
      <Layout>
        <Card className="border-danger-200 bg-danger-50 dark:border-danger-900/50 dark:bg-danger-900/30">
          <div className="text-lg font-semibold text-danger-900 dark:text-danger-100">S3 bucket unavailable</div>
          <div className="mt-2 text-sm text-danger-700 dark:text-danger-200">The selected bucket could not be found in the imported S3 snapshot.</div>
        </Card>
      </Layout>
    );
  }

  const storageMix = Object.entries(bucket.storage.storage_class_mix || {}).map(([name, value]) => ({
    name,
    value: Number((Number(value) * bucket.storage.bucket_size_gb).toFixed(2)),
  }));
  const lifecycleRules = bucket.metadata.lifecycle_rules || [];
  const inventoryTargets = bucket.metadata.inventory_target_buckets || [];
  const replicationTargets = bucket.metadata.replication_target_buckets || [];
  const yearlySavings = adjustOptimizationSavings(Number(bucket.maxops.potential_savings_yearly || 0), profile);
  const actionOptions = (() => {
    const actions = check ? getActionsForCheck(check) : [];
    const recommended = bucket.maxops.recommended_action?.trim() || '';
    if (!recommended) return actions;
    return actions.includes(recommended) ? actions : [recommended, ...actions];
  })();
  const activeAction = selectedAction || actionOptions[0] || '';
  const canExecuteAction = Boolean(bucket.maxops.check_id && bucket.account_id && bucket.region && activeAction);

  const handleExecuteAction = async () => {
    if (!bucket.maxops.check_id || !bucket.account_id || !bucket.region || !activeAction) {
      setActionMessage('Missing check, account, region, or action for execution.');
      return;
    }

    setIsExecutingAction(true);
    setActionMessage('');
    try {
      const response = await checksApi.executeAction(bucket.maxops.check_id, {
        action: activeAction,
        account_id: bucket.account_id,
        region: bucket.region,
        resource_id: bucket.resource_id,
      });
      setActionMessage(response.message || 'Action executed.');
    } catch (error: any) {
      setActionMessage(error?.response?.data?.detail || error?.message || 'Failed to execute action.');
    } finally {
      setIsExecutingAction(false);
    }
  };

  return (
    <Layout>
      <div className="space-y-8">
        <section className="overflow-hidden rounded-[32px] border border-gray-200 bg-[radial-gradient(circle_at_top_left,_rgba(52,224,196,0.2),_transparent_30%),radial-gradient(circle_at_bottom_right,_rgba(16,185,129,0.18),_transparent_28%),linear-gradient(135deg,_#f5fdfb_0%,_#ecfdf9_46%,_#f0fdf4_100%)] p-8 shadow-sm dark:border-gray-700 dark:bg-[radial-gradient(circle_at_top_left,_rgba(22,193,168,0.18),_transparent_24%),radial-gradient(circle_at_bottom_right,_rgba(16,185,129,0.16),_transparent_20%),linear-gradient(135deg,_rgba(14,24,48,0.98)_0%,_rgba(24,33,64,0.96)_46%,_rgba(14,24,48,0.98)_100%)]">
          <button
            type="button"
            onClick={() => navigate(-1)}
            className="mb-5 inline-flex items-center gap-2 rounded-full border border-gray-200 bg-white/80 px-4 py-2 text-sm font-medium text-gray-700 transition hover:bg-white dark:border-gray-700 dark:bg-gray-900/80 dark:text-gray-200"
          >
            <ArrowLeft size={16} />
            Back
          </button>

          <div className="flex flex-col gap-6 xl:flex-row xl:items-end xl:justify-between">
            <div>
              <div className="text-xs font-semibold uppercase tracking-[0.28em] text-gray-500 dark:text-gray-400">S3 Bucket Details</div>
              <h1 className="mt-3 text-4xl font-semibold tracking-tight text-gray-950 dark:text-gray-50">{bucket.resource_name || bucket.resource_id}</h1>
              <div className="mt-3 text-sm text-gray-600 dark:text-gray-300">
                {bucket.region || 'Unknown region'} · {titleCase(String(bucket.metadata.environment || 'unknown'))} · Created {formatDateTime(bucket.creation_date)}
              </div>
            </div>
            <div className="flex flex-wrap gap-2">
              <span className="rounded-full border border-gray-200 bg-white/80 px-3 py-1.5 text-xs font-semibold uppercase tracking-[0.12em] text-gray-700 dark:border-gray-700 dark:bg-gray-900/80 dark:text-gray-200">
                {titleCase(String(bucket.metadata.bucket_kind || 'unknown kind'))}
              </span>
              <span className="rounded-full border border-gray-200 bg-white/80 px-3 py-1.5 text-xs font-semibold uppercase tracking-[0.12em] text-gray-700 dark:border-gray-700 dark:bg-gray-900/80 dark:text-gray-200">
                {bucket.maxops.severity ? titleCase(bucket.maxops.severity) : 'Healthy'}
              </span>
            </div>
          </div>
        </section>

        <div className="grid gap-4 md:grid-cols-2 xl:grid-cols-4">
          <DetailMetric label="Monthly Cost" value={formatCurrency(bucket.storage.estimated_monthly_cost)} subtitle="Imported storage estimate" />
          <DetailMetric label="Annual Savings" value={formatCurrency(yearlySavings)} subtitle="Optimization upside if remediated" />
          <DetailMetric label="Bucket Size" value={`${bucket.storage.bucket_size_gb.toFixed(1)} GB`} subtitle={`${formatLargeNumber(bucket.storage.object_count)} objects`} />
          <DetailMetric label="Finding" value={bucket.maxops.title || 'Healthy bucket'} subtitle={bucket.maxops.recommended_action ? `Recommended: ${titleCase(bucket.maxops.recommended_action)}` : 'No active recommendation'} />
        </div>

        <div className="grid gap-4 md:grid-cols-2 xl:grid-cols-5">
          <PostureBadge label="Versioning" ok={bucket.posture.versioning_enabled} />
          <PostureBadge label="Access logging" ok={bucket.posture.logging_enabled} />
          <PostureBadge label="Inventory reports" ok={bucket.posture.inventory_enabled} />
          <PostureBadge label="Replication" ok={bucket.posture.replication_enabled} />
          <PostureBadge label="Lifecycle rules" ok={bucket.posture.lifecycle_enabled} />
        </div>

        <div className="grid gap-6 xl:grid-cols-2">
          <Card className="border-gray-200 bg-white/95 dark:border-gray-700 dark:bg-gray-900/95">
            <div className="mb-4 text-sm font-semibold text-gray-700 dark:text-gray-200">Storage class composition</div>
            <div className="grid gap-4 lg:grid-cols-[260px_minmax(0,1fr)]">
              <div className="h-72">
                <ResponsiveContainer width="100%" height="100%">
                  <PieChart>
                    <Pie data={storageMix} innerRadius={64} outerRadius={100} dataKey="value" nameKey="name" stroke="none">
                      {storageMix.map((entry, index) => (
                        <Cell key={entry.name} fill={CHART_COLORS[index % CHART_COLORS.length]} />
                      ))}
                    </Pie>
                    <Tooltip formatter={(value: unknown) => `${Number(value || 0).toFixed(1)} GB`} />
                  </PieChart>
                </ResponsiveContainer>
              </div>
              <div className="space-y-3">
                {storageMix.map((entry) => (
                  <div key={entry.name} className="rounded-2xl border border-gray-200 bg-gray-50 px-4 py-3 dark:border-gray-700 dark:bg-gray-800/70">
                    <div className="text-sm font-medium text-gray-800 dark:text-gray-100">{entry.name}</div>
                    <div className="mt-1 text-xs uppercase tracking-[0.16em] text-gray-500 dark:text-gray-400">{entry.value.toFixed(1)} GB</div>
                  </div>
                ))}
              </div>
            </div>
          </Card>

          <Card className="border-gray-200 bg-white/95 dark:border-gray-700 dark:bg-gray-900/95">
            <div className="mb-4 text-sm font-semibold text-gray-700 dark:text-gray-200">Cost and savings profile</div>
            <div className="h-72">
              <ResponsiveContainer width="100%" height="100%">
                <BarChart data={[{ name: 'Monthly cost', value: bucket.storage.estimated_monthly_cost }, { name: 'Annual savings', value: yearlySavings }]} margin={{ top: 8, right: 12, left: 0, bottom: 0 }}>
                  <CartesianGrid strokeDasharray="3 3" stroke={chartGridColor(theme)} opacity={0.35} />
                  <XAxis dataKey="name" stroke={chartTickColor(theme)} tickLine={false} axisLine={false} />
                  <YAxis stroke={chartTickColor(theme)} tickLine={false} axisLine={false} />
                  <Tooltip formatter={(value: unknown) => formatCurrency(Number(value || 0))} />
                  <Bar dataKey="value" radius={[8, 8, 0, 0]}>
                    <Cell fill={CHART_COLORS[0]} />
                    <Cell fill={CHART_COLORS[1]} />
                  </Bar>
                </BarChart>
              </ResponsiveContainer>
            </div>
          </Card>
        </div>

        <div className="grid gap-6 xl:grid-cols-[1.2fr_1fr]">
          <Card className="border-gray-200 bg-white/95 dark:border-gray-700 dark:bg-gray-900/95">
            <div className="mb-4 text-sm font-semibold text-gray-700 dark:text-gray-200">Optimization narrative</div>
            <div className="space-y-4">
              <div className="rounded-2xl border border-gray-200 bg-gray-50 px-4 py-4 dark:border-gray-700 dark:bg-gray-800/70">
                <div className="text-xs font-semibold uppercase tracking-[0.16em] text-gray-500 dark:text-gray-400">Finding</div>
                <div className="mt-2 text-lg font-semibold text-gray-900 dark:text-white">{bucket.maxops.title || 'Healthy bucket'}</div>
                <div className="mt-2 text-sm leading-6 text-gray-600 dark:text-gray-300">{bucket.maxops.description || 'No active issue detected in the imported dataset.'}</div>
              </div>
              <div className="grid gap-4 md:grid-cols-2">
                <div className="rounded-2xl border border-gray-200 bg-gray-50 px-4 py-4 dark:border-gray-700 dark:bg-gray-800/70">
                  <div className="text-xs font-semibold uppercase tracking-[0.16em] text-gray-500 dark:text-gray-400">Recommended action</div>
                  <div className="mt-2 text-sm font-semibold text-gray-900 dark:text-white">{bucket.maxops.recommended_action ? titleCase(bucket.maxops.recommended_action) : 'Observe only'}</div>
                </div>
                <div className="rounded-2xl border border-gray-200 bg-gray-50 px-4 py-4 dark:border-gray-700 dark:bg-gray-800/70">
                  <div className="text-xs font-semibold uppercase tracking-[0.16em] text-gray-500 dark:text-gray-400">Check ID</div>
                  <div className="mt-2 text-sm font-semibold text-gray-900 dark:text-white">{bucket.maxops.check_id || 'None'}</div>
                </div>
              </div>
            </div>
          </Card>

          <Card className="border-gray-200 bg-white/95 dark:border-gray-700 dark:bg-gray-900/95">
            <div className="mb-4 text-sm font-semibold text-gray-700 dark:text-gray-200">Runbook action</div>
            <div className="space-y-4">
              <label className="block">
                <div className="mb-2 text-xs font-semibold uppercase tracking-[0.16em] text-gray-500 dark:text-gray-400">Action</div>
                <select
                  value={activeAction}
                  onChange={(event) => setSelectedAction(event.target.value)}
                  className="w-full rounded-xl border border-gray-200 bg-gray-50 px-3 py-2.5 text-sm text-gray-700 outline-none transition focus:border-warning-300 focus:ring-2 focus:ring-warning-100 dark:border-gray-700 dark:bg-gray-800/80 dark:text-gray-100 dark:focus:border-warning-500 dark:focus:ring-warning-900/40"
                >
                  {actionOptions.length === 0 ? <option value="">No actions available</option> : null}
                  {actionOptions.map((action) => <option key={action} value={action}>{titleCase(action)}</option>)}
                </select>
              </label>

              <button
                type="button"
                onClick={handleExecuteAction}
                disabled={!canExecuteAction || isExecutingAction}
                className="inline-flex w-full items-center justify-center gap-2 rounded-xl bg-warning-500 px-4 py-3 text-sm font-semibold text-white transition hover:bg-warning-600 disabled:cursor-not-allowed disabled:bg-gray-300 dark:disabled:bg-gray-700"
              >
                <Play size={16} />
                {isExecutingAction ? 'Executing action...' : 'Execute action'}
              </button>

              {actionMessage ? (
                <div className={`rounded-2xl px-4 py-3 text-sm ${isActionErrorMessage(actionMessage) ? 'bg-danger-50 text-danger-700 dark:bg-danger-900/30 dark:text-danger-200' : 'bg-success-50 text-success-700 dark:bg-success-900/30 dark:text-success-200'}`}>
                  {actionMessage}
                </div>
              ) : null}
            </div>
          </Card>
        </div>

        <div className="grid gap-6 xl:grid-cols-3">
          <Card className="border-gray-200 bg-white/95 dark:border-gray-700 dark:bg-gray-900/95">
            <div className="mb-4 flex items-center gap-2 text-sm font-semibold text-gray-700 dark:text-gray-200">
              <ShieldCheck size={16} />
              Lifecycle configuration
            </div>
            <div className="space-y-3">
              {lifecycleRules.length === 0 ? (
                <div className="text-sm text-gray-500 dark:text-gray-400">No lifecycle rules were imported for this bucket.</div>
              ) : (
                lifecycleRules.map((rule: any, index: number) => (
                  <div key={`${rule.ID || rule.Id || 'rule'}-${index}`} className="rounded-2xl border border-gray-200 bg-gray-50 px-4 py-3 dark:border-gray-700 dark:bg-gray-800/70">
                    <div className="text-sm font-semibold text-gray-900 dark:text-white">{rule.ID || rule.Id || `Rule ${index + 1}`}</div>
                    <div className="mt-1 text-xs uppercase tracking-[0.16em] text-gray-500 dark:text-gray-400">{rule.Status || 'Unknown status'}</div>
                  </div>
                ))
              )}
            </div>
          </Card>

          <Card className="border-gray-200 bg-white/95 dark:border-gray-700 dark:bg-gray-900/95">
            <div className="mb-4 flex items-center gap-2 text-sm font-semibold text-gray-700 dark:text-gray-200">
              <CopyCheck size={16} />
              Inventory and logging targets
            </div>
            <div className="space-y-4">
              <div>
                <div className="text-xs font-semibold uppercase tracking-[0.16em] text-gray-500 dark:text-gray-400">Access log target</div>
                <div className="mt-2 text-sm text-gray-800 dark:text-gray-100">{bucket.metadata.logging_target_bucket || 'No access logging target configured'}</div>
              </div>
              <div>
                <div className="text-xs font-semibold uppercase tracking-[0.16em] text-gray-500 dark:text-gray-400">Inventory targets</div>
                <div className="mt-2 space-y-2">
                  {inventoryTargets.length === 0 ? <div className="text-sm text-gray-500 dark:text-gray-400">No inventory destination buckets configured.</div> : inventoryTargets.map((target: string) => <div key={target} className="rounded-xl bg-gray-50 px-3 py-2 text-sm text-gray-800 dark:bg-gray-800/70 dark:text-gray-100">{target}</div>)}
                </div>
              </div>
            </div>
          </Card>

          <Card className="border-gray-200 bg-white/95 dark:border-gray-700 dark:bg-gray-900/95">
            <div className="mb-4 flex items-center gap-2 text-sm font-semibold text-gray-700 dark:text-gray-200">
              <Globe size={16} />
              Replication targets
            </div>
            <div className="space-y-2">
              {replicationTargets.length === 0 ? <div className="text-sm text-gray-500 dark:text-gray-400">Replication is not configured for this bucket.</div> : replicationTargets.map((target: string) => <div key={target} className="rounded-xl bg-gray-50 px-3 py-2 text-sm text-gray-800 dark:bg-gray-800/70 dark:text-gray-100">{target}</div>)}
            </div>
          </Card>
        </div>

        <div className="grid gap-6 xl:grid-cols-2">
          <Card className="border-gray-200 bg-white/95 dark:border-gray-700 dark:bg-gray-900/95">
            <div className="mb-4 text-sm font-semibold text-gray-700 dark:text-gray-200">Imported metadata</div>
            <div className="grid gap-3 md:grid-cols-2">
              {[
                ['Environment', bucket.metadata.environment],
                ['Team', bucket.metadata.team],
                ['Owner', bucket.metadata.owner],
                ['Cost center', bucket.metadata.cost_center],
                ['Business unit', bucket.metadata.business_unit],
                ['Compliance', bucket.metadata.compliance],
                ['Data classification', bucket.metadata.data_classification],
                ['Criticality', bucket.metadata.criticality],
              ].map(([label, value]) => (
                <div key={String(label)} className="rounded-2xl border border-gray-200 bg-gray-50 px-4 py-3 dark:border-gray-700 dark:bg-gray-800/70">
                  <div className="text-xs font-semibold uppercase tracking-[0.16em] text-gray-500 dark:text-gray-400">{label}</div>
                  <div className="mt-2 text-sm font-medium text-gray-800 dark:text-gray-100">{value ? titleCase(String(value)) : 'Unavailable'}</div>
                </div>
              ))}
            </div>
          </Card>

          <Card className="border-gray-200 bg-white/95 dark:border-gray-700 dark:bg-gray-900/95">
            <div className="mb-4 flex items-center gap-2 text-sm font-semibold text-gray-700 dark:text-gray-200">
              <Sparkles size={16} />
              Imported AWS payload
            </div>
            <div className="space-y-3">
              <div className="rounded-2xl border border-gray-200 bg-gray-50 px-4 py-3 dark:border-gray-700 dark:bg-gray-800/70">
                <div className="text-xs font-semibold uppercase tracking-[0.16em] text-gray-500 dark:text-gray-400">Bucket name</div>
                <div className="mt-2 text-sm font-medium text-gray-800 dark:text-gray-100">{bucket.aws_payload?.bucket?.Name || bucket.resource_id}</div>
              </div>
              <div className="rounded-2xl border border-gray-200 bg-gray-50 px-4 py-3 dark:border-gray-700 dark:bg-gray-800/70">
                <div className="text-xs font-semibold uppercase tracking-[0.16em] text-gray-500 dark:text-gray-400">Creation date</div>
                <div className="mt-2 text-sm font-medium text-gray-800 dark:text-gray-100">{formatDateTime(bucket.aws_payload?.bucket?.CreationDate || bucket.creation_date)}</div>
              </div>
              <div className="rounded-2xl border border-gray-200 bg-gray-50 px-4 py-3 dark:border-gray-700 dark:bg-gray-800/70">
                <div className="text-xs font-semibold uppercase tracking-[0.16em] text-gray-500 dark:text-gray-400">Detail operations imported</div>
                <div className="mt-2 text-sm font-medium text-gray-800 dark:text-gray-100">{formatLargeNumber(Object.keys(bucket.aws_payload?.details || {}).length)}</div>
              </div>
            </div>
          </Card>
        </div>
      </div>
    </Layout>
  );
};
