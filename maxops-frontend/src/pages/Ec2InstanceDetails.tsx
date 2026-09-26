import React, { useMemo, useState } from 'react';
import { useNavigate, useParams } from 'react-router-dom';
import { useQuery } from 'react-query';
import { CartesianGrid, Legend, Line, LineChart, ResponsiveContainer, Tooltip, XAxis, YAxis } from 'recharts';
import { AlertCircle, ArrowLeft, Cpu, HardDrive, Network, Play, ShieldAlert, Wallet } from 'lucide-react';
import { Layout } from '@/components/layout/Layout';
import { Card } from '@/components/common/Card';
import { getActionsForCheck } from '@/components/checks/checkActions';
import { useOptimizationProfile } from '@/contexts/OptimizationProfileContext';
import { inventoryApi } from '@/services/inventory';
import { checksApi } from '@/services/checks';
import { buildEc2CollectionSearch, EMPTY_EC2_COLLECTION_FILTERS } from '@/features/ec2/drilldown';
import { adjustOptimizationSavings } from '@/utils/optimizationProfile';
import { useTheme } from '@/contexts/ThemeContext';
import { CHART_COLORS, STATUS_COLORS, chartGridColor, chartTickColor } from '@/styles/chartColors';

const formatPercent = (value: number) => `${value.toFixed(1)}%`;
const formatCurrency = (value: number) =>
  new Intl.NumberFormat('en-US', { style: 'currency', currency: 'USD', maximumFractionDigits: 0 }).format(value);
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
const formatTrendDate = (value: string) =>
  new Date(value).toLocaleDateString('en-US', {
    month: 'short',
    year: '2-digit',
  });
const isActionErrorMessage = (value: string) => /failed|error|missing/i.test(value);
const titleCase = (value: string) =>
  value
    .replace(/([a-z])([A-Z])/g, '$1 $2')
    .split(/[_\s-]+/)
    .filter(Boolean)
    .map((part) => part.charAt(0).toUpperCase() + part.slice(1))
    .join(' ');

const DetailMetric: React.FC<{ label: string; value: string; icon: React.ReactNode }> = ({ label, value, icon }) => (
  <div className="rounded-2xl border border-gray-200 bg-white/95 p-5 dark:border-gray-700 dark:bg-gray-900/95">
    <div className="flex items-center justify-between">
      <div>
        <div className="text-xs font-semibold uppercase tracking-[0.18em] text-gray-500 dark:text-gray-400">{label}</div>
        <div className="mt-3 text-2xl font-semibold text-gray-900 dark:text-white">{value}</div>
      </div>
      <div className="rounded-2xl bg-warning-50 p-3 text-warning-700 dark:bg-warning-950/50 dark:text-warning-300">{icon}</div>
    </div>
  </div>
);

const TrendTooltip: React.FC<{
  active?: boolean;
  label?: string;
  payload?: Array<{
    color?: string;
    dataKey?: string | number;
    name?: string;
    value?: number;
    payload?: { timestamp?: string };
  }>;
}> = ({ active, payload }) => {
  const { theme } = useTheme();
  if (!active || !payload || payload.length === 0) {
    return null;
  }

  const timestamp = payload[0]?.payload?.timestamp;

  return (
    <div className="rounded-2xl border border-gray-200 bg-white/95 px-4 py-3 shadow-xl dark:border-gray-700 dark:bg-gray-900/95">
      <div className="text-xs font-semibold uppercase tracking-[0.16em] text-gray-500 dark:text-gray-400">
        {timestamp ? formatDateTime(timestamp) : 'Metric sample'}
      </div>
      <div className="mt-3 space-y-2">
        {payload.map((entry) => {
          const label = entry.name || titleCase(String(entry.dataKey ?? 'Metric'));
          return (
            <div key={String(entry.dataKey ?? label)} className="flex items-center justify-between gap-6">
              <div className="flex items-center gap-2 text-sm text-gray-700 dark:text-gray-200">
                <span className="inline-block h-2.5 w-2.5 rounded-full" style={{ backgroundColor: entry.color || chartTickColor(theme) }} />
                <span>{label}</span>
              </div>
              <div className="text-sm font-semibold text-gray-900 dark:text-white">
                {formatPercent(Number(entry.value ?? 0))}
              </div>
            </div>
          );
        })}
      </div>
    </div>
  );
};

const TrendCard: React.FC<{
  title: string;
  subtitle: string;
  points: Array<{ timestamp: string; average: number; maximum: number; p90: number; p95: number; p99: number }>;
  tone: {
    average: string;
    p90: string;
    p95: string;
    p99: string;
    maximum: string;
  };
}> = ({ title, subtitle, points, tone }) => {
  const { theme } = useTheme();
  if (points.length === 0) {
    return (
      <Card className="border-gray-200 bg-white/95 dark:border-gray-700 dark:bg-gray-900/95">
        <div className="mb-2 text-sm font-semibold text-gray-700 dark:text-gray-200">{title}</div>
        <div className="text-sm text-gray-500 dark:text-gray-400">{subtitle}</div>
        <div className="mt-6 rounded-2xl bg-gray-50 px-4 py-8 text-sm text-gray-500 dark:bg-gray-800/70 dark:text-gray-400">
          No historical metric data was imported for this instance.
        </div>
      </Card>
    );
  }

  const chartData = [...points]
    .reverse()
    .map((point) => ({ ...point, label: formatTrendDate(point.timestamp) }));
  const latestPoint = points[0];

  return (
    <Card className="border-gray-200 bg-white/95 dark:border-gray-700 dark:bg-gray-900/95">
      <div className="mb-2 flex items-start justify-between gap-4">
        <div>
          <div className="text-sm font-semibold text-gray-700 dark:text-gray-200">{title}</div>
          <div className="text-sm text-gray-500 dark:text-gray-400">{subtitle}</div>
        </div>
        <div className="rounded-full bg-gray-100 px-3 py-1 text-[11px] font-semibold uppercase tracking-[0.16em] text-gray-600 dark:bg-gray-800 dark:text-gray-300">
          Percentile View
        </div>
      </div>
      <div className="mt-5 grid gap-3 md:grid-cols-2 xl:grid-cols-5">
        {[
          ['Average', latestPoint.average, tone.average],
          ['P90', latestPoint.p90, tone.p90],
          ['P95', latestPoint.p95, tone.p95],
          ['P99', latestPoint.p99, tone.p99],
          ['Maximum', latestPoint.maximum, tone.maximum],
        ].map(([label, value, color]) => (
          <div key={String(label)} className="rounded-2xl border border-gray-200 bg-gray-50 px-4 py-3 dark:border-gray-700 dark:bg-gray-800/70">
            <div className="text-[11px] font-semibold uppercase tracking-[0.16em] text-gray-500 dark:text-gray-400">{label}</div>
            <div className="mt-2 flex items-center gap-2">
              <span className="inline-block h-2.5 w-2.5 rounded-full" style={{ backgroundColor: String(color) }} />
              <span className="text-lg font-semibold text-gray-900 dark:text-white">{formatPercent(Number(value))}</span>
            </div>
            <div className="mt-1 text-xs text-gray-500 dark:text-gray-400">
              {latestPoint.timestamp ? `Latest sample: ${formatDateTime(latestPoint.timestamp)}` : 'Latest sample unavailable'}
            </div>
          </div>
        ))}
      </div>
      <div className="mt-6 h-72">
        <ResponsiveContainer width="100%" height="100%">
          <LineChart data={chartData} margin={{ top: 8, right: 12, left: -12, bottom: 0 }}>
            <CartesianGrid strokeDasharray="3 3" stroke={chartGridColor(theme)} opacity={0.4} />
            <XAxis dataKey="label" stroke={chartTickColor(theme)} tickLine={false} axisLine={false} minTickGap={18} />
            <YAxis stroke={chartTickColor(theme)} tickLine={false} axisLine={false} width={40} />
            <Tooltip
              content={<TrendTooltip />}
            />
            <Legend />
            <Line type="monotone" dataKey="average" name="Average" stroke={tone.average} strokeWidth={2.5} dot={false} />
            <Line type="monotone" dataKey="p90" name="P90" stroke={tone.p90} strokeWidth={2} dot={false} />
            <Line type="monotone" dataKey="p95" name="P95" stroke={tone.p95} strokeWidth={2.5} dot={false} />
            <Line type="monotone" dataKey="p99" name="P99" stroke={tone.p99} strokeWidth={2} dot={false} />
            <Line type="monotone" dataKey="maximum" name="Maximum" stroke={tone.maximum} strokeWidth={2} dot={false} strokeDasharray="5 4" />
          </LineChart>
        </ResponsiveContainer>
      </div>
    </Card>
  );
};

export const Ec2InstanceDetailsPage: React.FC = () => {
  const navigate = useNavigate();
  const { instanceId } = useParams<{ instanceId: string }>();
  const { profile } = useOptimizationProfile();
  const [selectedAction, setSelectedAction] = useState('');
  const [isExecutingAction, setIsExecutingAction] = useState(false);
  const [actionMessage, setActionMessage] = useState('');

  const { data, isLoading, isError } = useQuery('inventory-ec2-overview', () => inventoryApi.getEc2Overview());

  const instance = useMemo(
    () => data?.instances.find((entry) => entry.resource_id === instanceId),
    [data?.instances, instanceId]
  );
  const { data: check } = useQuery(
    ['check-metadata', instance?.maxops.check_id],
    () => checksApi.getCheck(instance!.maxops.check_id!),
    {
      enabled: Boolean(instance?.maxops.check_id),
      retry: false,
    }
  );

  const openCollection = (search: string) => navigate(`/dashboard/resources/ec2/collection${search}`);

  if (isLoading) {
    return (
      <Layout>
        <div className="flex min-h-[60vh] items-center justify-center rounded-[32px] border border-gray-200 bg-white/90">
          <div className="text-center">
            <div className="text-lg font-semibold text-gray-800">Loading EC2 instance details</div>
            <div className="mt-2 text-sm text-gray-500">Preparing the current instance snapshot from the imported inventory.</div>
          </div>
        </div>
      </Layout>
    );
  }

  if (isError || !data || !instance) {
    return (
      <Layout>
        <Card className="border-danger-200 bg-danger-50">
          <div className="text-lg font-semibold text-danger-900">EC2 instance unavailable</div>
          <div className="mt-2 text-sm text-danger-700">The selected EC2 instance could not be found in the imported inventory snapshot.</div>
        </Card>
      </Layout>
    );
  }

  const environment = (instance.tags.env || 'unknown').toLowerCase();
  const finding = (instance.maxops.finding_type || 'healthy').toLowerCase();
  const monthlyCost = Number(instance.metadata.monthly_cost_estimate || 0);
  const yearlySavings = adjustOptimizationSavings(Number(instance.maxops.potential_savings_yearly || 0), profile);
  const cpuTrendPoints = instance.usage.trends?.cpu?.points || [];
  const memoryTrendPoints = instance.usage.trends?.memory?.points || [];
  const recommendedAction = instance.maxops.recommended_action?.trim() || '';
  const actionOptions = (() => {
    const actions = check ? getActionsForCheck(check) : [];
    if (!recommendedAction) {
      return actions;
    }
    return actions.includes(recommendedAction) ? actions : [recommendedAction, ...actions];
  })();
  const activeAction = selectedAction || actionOptions[0] || '';
  const canExecuteAction = Boolean(instance.maxops.check_id && instance.account_id && instance.region && activeAction);

  const handleExecuteAction = async () => {
    if (!instance.maxops.check_id || !instance.account_id || !instance.region || !activeAction) {
      setActionMessage('Missing check, account, region, or action for execution.');
      return;
    }

    setIsExecutingAction(true);
    setActionMessage('');

    try {
      const response = await checksApi.executeAction(instance.maxops.check_id, {
        action: activeAction,
        account_id: instance.account_id,
        region: instance.region,
        resource_id: instance.resource_id,
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
        <section className="overflow-hidden rounded-[32px] border border-gray-200 bg-[radial-gradient(circle_at_top_left,_rgba(248,191,61,0.24),_transparent_34%),linear-gradient(135deg,_#f5f7fb_0%,_#fffbeb_42%,_#ecfdf9_100%)] p-8 shadow-sm dark:border-gray-700 dark:bg-[radial-gradient(circle_at_top_left,_rgba(245,166,35,0.14),_transparent_28%),linear-gradient(135deg,_rgba(14,24,48,0.98)_0%,_rgba(24,33,64,0.96)_46%,_rgba(7,12,22,0.98)_100%)]">
          <button
            type="button"
            onClick={() => navigate(-1)}
            className="mb-5 inline-flex items-center gap-2 rounded-full border border-gray-200 bg-white/80 px-4 py-2 text-sm font-medium text-gray-700 transition hover:bg-white"
          >
            <ArrowLeft size={16} />
            Back
          </button>

          <div className="flex flex-col gap-6 xl:flex-row xl:items-end xl:justify-between">
            <div>
              <div className="text-xs font-semibold uppercase tracking-[0.28em] text-gray-500">EC2 Instance Details</div>
              <h1 className="mt-3 text-4xl font-semibold tracking-tight text-gray-950 dark:text-gray-50">
                {instance.resource_name || instance.resource_id}
              </h1>
              <div className="mt-3 text-sm text-gray-600 dark:text-gray-300">
                {instance.instance_type || 'Unknown type'} · {instance.region || 'Unknown region'} · {instance.state || 'Unknown state'}
              </div>
            </div>

            <div className="flex flex-wrap gap-2">
              <button
                type="button"
                onClick={() =>
                  openCollection(
                    buildEc2CollectionSearch({
                      ...EMPTY_EC2_COLLECTION_FILTERS,
                      regions: instance.region ? [instance.region.toLowerCase()] : [],
                    })
                  )
                }
                className="rounded-full border border-gray-200 bg-white/80 px-3 py-1.5 text-xs font-semibold uppercase tracking-[0.12em] text-gray-700"
              >
                {instance.region || 'Unknown region'}
              </button>
              <button
                type="button"
                onClick={() =>
                  openCollection(
                    buildEc2CollectionSearch({
                      ...EMPTY_EC2_COLLECTION_FILTERS,
                      instanceTypes: instance.instance_type ? [instance.instance_type.toLowerCase()] : [],
                    })
                  )
                }
                className="rounded-full border border-gray-200 bg-white/80 px-3 py-1.5 text-xs font-semibold uppercase tracking-[0.12em] text-gray-700"
              >
                {instance.instance_type || 'Unknown type'}
              </button>
              <button
                type="button"
                onClick={() =>
                  openCollection(
                    buildEc2CollectionSearch({
                      ...EMPTY_EC2_COLLECTION_FILTERS,
                      states: instance.state ? [instance.state.toLowerCase()] : [],
                    })
                  )
                }
                className="rounded-full border border-gray-200 bg-white/80 px-3 py-1.5 text-xs font-semibold uppercase tracking-[0.12em] text-gray-700"
              >
                {instance.state || 'Unknown state'}
              </button>
            </div>
          </div>
        </section>

        <section className="grid gap-4 xl:grid-cols-6">
          <DetailMetric label="CPU" value={formatPercent(instance.usage.cpu_utilization)} icon={<Cpu size={20} />} />
          <DetailMetric label="Memory" value={formatPercent(instance.usage.memory_utilization)} icon={<HardDrive size={20} />} />
          <DetailMetric label="Received" value={`${instance.usage.received_bytes_mb.toFixed(0)} MB`} icon={<Network size={20} />} />
          <DetailMetric label="Sent" value={`${instance.usage.sent_bytes_mb.toFixed(0)} MB`} icon={<Network size={20} />} />
          <DetailMetric label="Potential Savings" value={formatCurrency(yearlySavings)} icon={<Wallet size={20} />} />
          <DetailMetric label="Status" value={instance.maxops.status || 'unknown'} icon={<ShieldAlert size={20} />} />
        </section>

        <section className="grid gap-6 xl:grid-cols-[1.15fr_0.85fr]">
          <Card className="border-gray-200 bg-white/95 dark:border-gray-700 dark:bg-gray-900/95">
            <div className="mb-4 text-sm font-semibold text-gray-700 dark:text-gray-200">Instance snapshot</div>
            <div className="grid gap-4 md:grid-cols-2">
              <div className="rounded-2xl bg-gray-50 p-4 dark:bg-gray-800/70">
                <div className="text-xs font-semibold uppercase tracking-[0.16em] text-gray-500 dark:text-gray-400">Resource ID</div>
                <div className="mt-2 text-sm font-medium text-gray-900 dark:text-white">{instance.resource_id}</div>
              </div>
              <div className="rounded-2xl bg-gray-50 p-4 dark:bg-gray-800/70">
                <div className="text-xs font-semibold uppercase tracking-[0.16em] text-gray-500 dark:text-gray-400">Launch time</div>
                <div className="mt-2 text-sm font-medium text-gray-900 dark:text-white">{formatDateTime(instance.launch_time)}</div>
              </div>
              <div className="rounded-2xl bg-gray-50 p-4 dark:bg-gray-800/70">
                <div className="text-xs font-semibold uppercase tracking-[0.16em] text-gray-500 dark:text-gray-400">Availability zone</div>
                <div className="mt-2 text-sm font-medium text-gray-900 dark:text-white">{instance.availability_zone || 'Unavailable'}</div>
              </div>
              <div className="rounded-2xl bg-gray-50 p-4 dark:bg-gray-800/70">
                <div className="text-xs font-semibold uppercase tracking-[0.16em] text-gray-500 dark:text-gray-400">Account</div>
                <div className="mt-2 text-sm font-medium text-gray-900 dark:text-white">{instance.account_id || 'Unavailable'}</div>
              </div>
              <div className="rounded-2xl bg-gray-50 p-4 dark:bg-gray-800/70">
                <div className="text-xs font-semibold uppercase tracking-[0.16em] text-gray-500 dark:text-gray-400">Monthly cost estimate</div>
                <div className="mt-2 text-sm font-medium text-gray-900 dark:text-white">{formatCurrency(monthlyCost)}</div>
              </div>
              <div className="rounded-2xl bg-gray-50 p-4 dark:bg-gray-800/70">
                <div className="text-xs font-semibold uppercase tracking-[0.16em] text-gray-500 dark:text-gray-400">Environment</div>
                <button
                  type="button"
                  onClick={() =>
                    openCollection(
                      buildEc2CollectionSearch({
                        ...EMPTY_EC2_COLLECTION_FILTERS,
                        environments: [environment],
                      })
                    )
                  }
                  className="mt-2 text-sm font-medium text-warning-700 transition hover:text-warning-800 dark:text-warning-300"
                >
                  {environment}
                </button>
              </div>
            </div>
          </Card>

          <Card className="border-gray-200 bg-white/95 dark:border-gray-700 dark:bg-gray-900/95">
            <div className="mb-4 text-sm font-semibold text-gray-700 dark:text-gray-200">Optimization insight</div>
            <div className="space-y-4">
              <div className="rounded-2xl bg-gray-50 p-4 dark:bg-gray-800/70">
                <div className="text-xs font-semibold uppercase tracking-[0.16em] text-gray-500 dark:text-gray-400">Finding</div>
                <button
                  type="button"
                  onClick={() =>
                    openCollection(
                      buildEc2CollectionSearch({
                        ...EMPTY_EC2_COLLECTION_FILTERS,
                        findings: [finding],
                      })
                    )
                  }
                  className="mt-2 text-sm font-medium text-warning-700 transition hover:text-warning-800 dark:text-warning-300"
                >
                  {instance.maxops.finding_type || 'healthy'}
                </button>
              </div>
              <div className="rounded-2xl bg-gray-50 p-4 dark:bg-gray-800/70">
                <div className="text-xs font-semibold uppercase tracking-[0.16em] text-gray-500 dark:text-gray-400">Title</div>
                <div className="mt-2 text-sm font-medium text-gray-900 dark:text-white">{instance.maxops.title || 'No title'}</div>
              </div>
              <div className="rounded-2xl bg-gray-50 p-4 dark:bg-gray-800/70">
                <div className="text-xs font-semibold uppercase tracking-[0.16em] text-gray-500 dark:text-gray-400">Recommended actions</div>
                {actionOptions.length > 0 ? (
                  <div className="mt-3 space-y-3">
                    <div className="flex flex-col gap-2 sm:flex-row sm:items-center">
                      <select
                        value={activeAction}
                        onChange={(event) => setSelectedAction(event.target.value)}
                        className="min-w-0 flex-1 rounded-xl border border-gray-200 bg-white px-3 py-2.5 text-sm text-gray-700 outline-none transition focus:border-warning-300 focus:ring-2 focus:ring-warning-100 dark:border-gray-700 dark:bg-gray-900/80 dark:text-gray-100 dark:focus:border-warning-500 dark:focus:ring-warning-900/40"
                      >
                        {actionOptions.map((action) => (
                          <option key={action} value={action}>
                            {titleCase(action)}
                          </option>
                        ))}
                      </select>
                      <button
                        type="button"
                        onClick={handleExecuteAction}
                        disabled={!canExecuteAction || isExecutingAction}
                        className="inline-flex shrink-0 items-center justify-center gap-2 rounded-xl bg-warning-500 px-4 py-2.5 text-sm font-semibold text-white transition hover:bg-warning-600 disabled:cursor-not-allowed disabled:opacity-50"
                      >
                        <Play size={14} />
                        {isExecutingAction ? 'Executing...' : 'Execute Action'}
                      </button>
                      <div className="flex h-10 w-10 shrink-0 items-center justify-center">
                        {actionMessage && isActionErrorMessage(actionMessage) ? (
                          <div className="relative inline-flex items-center justify-center group">
                            <AlertCircle size={16} className="text-danger-500" />
                            <div className="pointer-events-none absolute left-1/2 top-full z-20 mt-2 hidden w-80 -trangray-x-1/2 rounded-xl border border-danger-200 bg-white px-3 py-2 text-xs text-danger-700 shadow-lg group-hover:block dark:border-danger-900 dark:bg-gray-900 dark:text-danger-300">
                              {actionMessage}
                            </div>
                          </div>
                        ) : null}
                      </div>
                    </div>
                    {actionMessage && !isActionErrorMessage(actionMessage) ? (
                      <div className="text-sm text-gray-600 dark:text-gray-300">{actionMessage}</div>
                    ) : null}
                    {!canExecuteAction ? (
                      <div className="text-xs text-gray-500 dark:text-gray-400">
                        Action execution requires a check ID, account, and region on this instance.
                      </div>
                    ) : null}
                  </div>
                ) : (
                  <div className="mt-2 text-sm font-medium text-gray-900 dark:text-white">Observe</div>
                )}
              </div>
            </div>
          </Card>
        </section>

        <section className="grid gap-6 xl:grid-cols-[1fr_1fr]">
          <TrendCard
            title="CPU trend"
            subtitle="Imported historic CPU utilization for this instance."
            points={cpuTrendPoints}
            tone={{
              average: CHART_COLORS[0],
              p90: CHART_COLORS[4],
              p95: CHART_COLORS[5],
              p99: CHART_COLORS[2],
              maximum: CHART_COLORS[1],
            }}
          />
          <TrendCard
            title="Memory trend"
            subtitle="Imported historic memory utilization from the synthetic CloudWatch series."
            points={memoryTrendPoints}
            tone={{
              average: CHART_COLORS[0],
              p90: STATUS_COLORS.healthy,
              p95: CHART_COLORS[1],
              p99: CHART_COLORS[2],
              maximum: CHART_COLORS[5],
            }}
          />
        </section>

        <section className="grid gap-6 xl:grid-cols-[1fr_1fr]">
          <Card className="border-gray-200 bg-white/95 dark:border-gray-700 dark:bg-gray-900/95">
            <div className="mb-4 text-sm font-semibold text-gray-700 dark:text-gray-200">Utilization breakdown</div>
            <div className="space-y-4">
              {[
                ['CPU utilization', formatPercent(instance.usage.cpu_utilization)],
                ['Memory utilization', formatPercent(instance.usage.memory_utilization)],
                ['Disk used', `${instance.usage.disk_used_gb.toFixed(1)} GB`],
                ['Disk available', `${instance.usage.disk_available_gb.toFixed(1)} GB`],
                ['Network in', `${instance.usage.network_in_mb.toFixed(1)} MB`],
                ['Network out', `${instance.usage.network_out_mb.toFixed(1)} MB`],
              ].map(([label, value]) => (
                <div key={label} className="flex items-center justify-between rounded-2xl bg-gray-50 px-4 py-3 dark:bg-gray-800/70">
                  <div className="text-sm font-medium text-gray-700 dark:text-gray-200">{label}</div>
                  <div className="text-sm font-semibold text-gray-900 dark:text-white">{value}</div>
                </div>
              ))}
            </div>
          </Card>

          <Card className="border-gray-200 bg-white/95 dark:border-gray-700 dark:bg-gray-900/95">
            <div className="mb-4 text-sm font-semibold text-gray-700 dark:text-gray-200">Tags</div>
            <div className="h-96 space-y-3 overflow-auto pr-1">
              {Object.keys(instance.tags).length === 0 ? (
                <div className="rounded-2xl bg-gray-50 px-4 py-3 text-sm text-gray-500 dark:bg-gray-800/70 dark:text-gray-400">
                  No tags were imported for this instance.
                </div>
              ) : (
                Object.entries(instance.tags).map(([key, value]) => (
                  <button
                    type="button"
                    key={key}
                    onClick={() =>
                      openCollection(
                        buildEc2CollectionSearch({
                          ...EMPTY_EC2_COLLECTION_FILTERS,
                          tags: [`${key}:${value}`],
                        })
                      )
                    }
                    className="flex w-full items-center justify-between rounded-2xl bg-gray-50 px-4 py-3 text-left transition hover:bg-warning-50 dark:bg-gray-800/70 dark:hover:bg-gray-800"
                  >
                    <div className="text-sm font-medium text-gray-700 dark:text-gray-200">{key}</div>
                    <div className="text-sm font-semibold text-gray-900 dark:text-white">{value}</div>
                  </button>
                ))
              )}
            </div>
          </Card>
        </section>

        <section>
          <Card className="border-gray-200 bg-white/95 dark:border-gray-700 dark:bg-gray-900/95">
            <div className="mb-4 text-sm font-semibold text-gray-700 dark:text-gray-200">Metadata</div>
            <pre className="h-96 overflow-auto rounded-2xl bg-gray-50 p-4 text-xs text-gray-700 dark:bg-gray-800/70 dark:text-gray-200">
              {JSON.stringify(instance.metadata, null, 2)}
            </pre>
          </Card>
        </section>
      </div>
    </Layout>
  );
};
