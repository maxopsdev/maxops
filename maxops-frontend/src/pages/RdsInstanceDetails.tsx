import React, { useMemo, useState } from 'react';
import { useNavigate, useParams } from 'react-router-dom';
import { useQuery } from 'react-query';
import {
  CartesianGrid,
  Legend,
  Line,
  LineChart,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from 'recharts';
import {
  AlertCircle,
  ArrowLeft,
  Cpu,
  Database,
  HardDrive,
  Network,
  Play,
  ShieldAlert,
  Sparkles,
} from 'lucide-react';
import { Layout } from '@/components/layout/Layout';
import { Card } from '@/components/common/Card';
import { RdsRightsizerPanel } from '@/components/rds/RdsRightsizerPanel';
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

const DetailMetric: React.FC<{ label: string; value: string; icon: React.ReactNode }> = ({ label, value, icon }) => (
  <div className="rounded-2xl border border-gray-200 bg-white/95 p-5 dark:border-gray-700 dark:bg-gray-900/95">
    <div className="flex items-center justify-between gap-4">
      <div>
        <div className="text-xs font-semibold uppercase tracking-[0.18em] text-gray-500 dark:text-gray-400">{label}</div>
        <div className="mt-3 text-2xl font-semibold text-gray-900 dark:text-white">{value}</div>
      </div>
      <div className="rounded-2xl bg-primary-50 p-3 text-primary-700 dark:bg-primary-950/50 dark:text-primary-300">{icon}</div>
    </div>
  </div>
);

const TrendTooltip: React.FC<{
  active?: boolean;
  payload?: Array<{ color?: string; name?: string; value?: number; payload?: { timestamp?: string } }>;
}> = ({ active, payload }) => {
  const { theme } = useTheme();
  if (!active || !payload || payload.length === 0) return null;

  return (
    <div className="rounded-2xl border border-gray-200 bg-white/95 px-4 py-3 shadow-xl dark:border-gray-700 dark:bg-gray-900/95">
      <div className="text-xs font-semibold uppercase tracking-[0.16em] text-gray-500 dark:text-gray-400">
        {formatDateTime(payload[0]?.payload?.timestamp)}
      </div>
      <div className="mt-3 space-y-2">
        {payload.map((entry) => (
          <div key={`${entry.name}-${entry.color}`} className="flex items-center justify-between gap-6">
            <div className="flex items-center gap-2 text-sm text-gray-700 dark:text-gray-200">
              <span className="inline-block h-2.5 w-2.5 rounded-full" style={{ backgroundColor: entry.color || chartTickColor(theme) }} />
              <span>{entry.name || 'Metric'}</span>
            </div>
            <div className="text-sm font-semibold text-gray-900 dark:text-white">{formatLargeNumber(Number(entry.value || 0))}</div>
          </div>
        ))}
      </div>
    </div>
  );
};

const TrendCard: React.FC<{
  title: string;
  subtitle: string;
  points: Array<{ timestamp: string; average: number }>;
  color: string;
  formatter?: (value: number) => string;
}> = ({ title, subtitle, points, color, formatter = (value) => formatLargeNumber(Number(value.toFixed(0))) }) => {
  const { theme } = useTheme();
  if (points.length === 0) {
    return (
      <Card className="border-gray-200 bg-white/95 dark:border-gray-700 dark:bg-gray-900/95">
        <div className="text-sm font-semibold text-gray-700 dark:text-gray-200">{title}</div>
        <div className="mt-1 text-sm text-gray-500 dark:text-gray-400">{subtitle}</div>
        <div className="mt-6 rounded-2xl bg-gray-50 px-4 py-8 text-sm text-gray-500 dark:bg-gray-800/70 dark:text-gray-400">
          No historic metric samples were imported for this series.
        </div>
      </Card>
    );
  }

  const chartData = [...points].reverse().map((point) => ({
    ...point,
    label: new Date(point.timestamp).toLocaleDateString('en-US', { month: 'short', day: 'numeric' }),
  }));
  const latest = points[0];

  return (
    <Card className="border-gray-200 bg-white/95 dark:border-gray-700 dark:bg-gray-900/95">
      <div className="mb-4 flex items-start justify-between gap-4">
        <div>
          <div className="text-sm font-semibold text-gray-700 dark:text-gray-200">{title}</div>
          <div className="mt-1 text-sm text-gray-500 dark:text-gray-400">{subtitle}</div>
        </div>
        <div className="rounded-full bg-gray-100 px-3 py-1 text-[11px] font-semibold uppercase tracking-[0.14em] text-gray-600 dark:bg-gray-800 dark:text-gray-300">
          Latest {formatter(latest.average)}
        </div>
      </div>
      <div className="h-72">
        <ResponsiveContainer width="100%" height="100%">
          <LineChart data={chartData} margin={{ top: 8, right: 12, left: -12, bottom: 0 }}>
            <CartesianGrid strokeDasharray="3 3" stroke={chartGridColor(theme)} opacity={0.35} />
            <XAxis dataKey="label" stroke={chartTickColor(theme)} tickLine={false} axisLine={false} minTickGap={18} />
            <YAxis stroke={chartTickColor(theme)} tickLine={false} axisLine={false} />
            <Tooltip content={<TrendTooltip />} />
            <Legend />
            <Line type="monotone" dataKey="average" name={title} stroke={color} strokeWidth={2.5} dot={false} />
          </LineChart>
        </ResponsiveContainer>
      </div>
    </Card>
  );
};

export const RdsInstanceDetailsPage: React.FC = () => {
  const navigate = useNavigate();
  const { instanceId } = useParams<{ instanceId: string }>();
  const { profile } = useOptimizationProfile();
  const [selectedAction, setSelectedAction] = useState('');
  const [isExecutingAction, setIsExecutingAction] = useState(false);
  const [actionMessage, setActionMessage] = useState('');

  const { data, isLoading, isError } = useQuery('inventory-rds-overview', () => inventoryApi.getRdsOverview());
  const instance = useMemo(() => data?.instances.find((entry) => entry.resource_id === instanceId), [data?.instances, instanceId]);
  const { data: check } = useQuery(['rds-check-metadata', instance?.maxops.check_id], () => checksApi.getCheck(instance!.maxops.check_id!), {
    enabled: Boolean(instance?.maxops.check_id),
    retry: false,
  });

  if (isLoading) {
    return (
      <Layout>
        <div className="flex min-h-[60vh] items-center justify-center rounded-[32px] border border-gray-200 bg-white/90 dark:border-gray-700 dark:bg-gray-900/90">
          <div className="text-center">
            <div className="text-lg font-semibold text-gray-800 dark:text-gray-100">Loading RDS instance details</div>
            <div className="mt-2 text-sm text-gray-500 dark:text-gray-400">Preparing imported database metadata and metric history.</div>
          </div>
        </div>
      </Layout>
    );
  }

  if (isError || !data || !instance) {
    return (
      <Layout>
        <Card className="border-danger-200 bg-danger-50 dark:border-danger-900/50 dark:bg-danger-950/30">
          <div className="text-lg font-semibold text-danger-900 dark:text-danger-100">RDS instance unavailable</div>
          <div className="mt-2 text-sm text-danger-700 dark:text-danger-200">The selected instance could not be found in the imported RDS snapshot.</div>
        </Card>
      </Layout>
    );
  }

  const actionOptions = (() => {
    const actions = check ? getActionsForCheck(check) : [];
    const recommended = instance.maxops.recommended_action?.trim() || '';
    if (!recommended) return actions;
    return actions.includes(recommended) ? actions : [recommended, ...actions];
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

  const engine = titleCase(String(instance.engine || 'unknown'));
  const cpuPoints = instance.workload.trends?.cpu?.points || [];
  const connectionPoints = instance.workload.trends?.connections?.points || [];
  const yearlySavings = adjustOptimizationSavings(Number(instance.maxops.potential_savings_yearly || 0), profile);
  const currentConfig = instance.maxops.current_config || {};
  const targetConfig = instance.maxops.target_config || {};
  const endpoint = instance.metadata.endpoint || instance.aws_payload?.endpoint || {};
  const tags = Object.entries(instance.tags || {});

  return (
    <Layout>
      <div className="space-y-8">
        <section className="overflow-hidden rounded-[32px] border border-gray-200 bg-[radial-gradient(circle_at_top_left,_rgba(22,193,168,0.22),_transparent_32%),radial-gradient(circle_at_bottom_right,_rgba(20,184,166,0.18),_transparent_24%),linear-gradient(135deg,_#f5fdfb_0%,_#ecfdf9_48%,_#f5f7fb_100%)] p-8 shadow-sm dark:border-gray-700 dark:bg-[radial-gradient(circle_at_top_left,_rgba(22,193,168,0.2),_transparent_24%),radial-gradient(circle_at_bottom_right,_rgba(20,184,166,0.16),_transparent_20%),linear-gradient(135deg,_rgba(14,24,48,0.98)_0%,_rgba(24,33,64,0.97)_48%,_rgba(14,24,48,0.98)_100%)]">
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
              <div className="text-xs font-semibold uppercase tracking-[0.28em] text-gray-500 dark:text-gray-400">RDS Instance Details</div>
              <h1 className="mt-3 text-4xl font-semibold tracking-tight text-gray-950 dark:text-gray-50">
                {instance.resource_name || instance.resource_id}
              </h1>
              <div className="mt-3 text-sm text-gray-600 dark:text-gray-300">
                {engine} - {instance.db_instance_class || 'Unknown class'} - {instance.region || 'Unknown region'} - {instance.state || 'Unknown state'}
              </div>
            </div>
            <div className="flex flex-wrap gap-2">
              <button
                type="button"
                onClick={() => navigate(`/dashboard/resources/rds/instances/${encodeURIComponent(instance.resource_id)}/workload`)}
                className="rounded-full bg-primary-600 px-4 py-2 text-sm font-semibold text-white transition hover:bg-primary-700"
              >
                Open workload view
              </button>
              <span className={`rounded-full px-3 py-1.5 text-xs font-semibold uppercase tracking-[0.12em] ${instance.workload.is_graviton ? 'bg-success-50 text-success-700 dark:bg-success-950/40 dark:text-success-300' : 'bg-warning-50 text-warning-700 dark:bg-warning-950/40 dark:text-warning-300'}`}>
                {instance.workload.is_graviton ? 'Graviton' : 'Legacy family'}
              </span>
              <span className="rounded-full border border-gray-200 bg-white/80 px-3 py-1.5 text-xs font-semibold uppercase tracking-[0.12em] text-gray-700 dark:border-gray-700 dark:bg-gray-900/80 dark:text-gray-200">
                {instance.maxops.severity ? titleCase(instance.maxops.severity) : 'Healthy'}
              </span>
            </div>
          </div>
        </section>

        <section className="grid gap-4 xl:grid-cols-6">
          <DetailMetric label="CPU" value={`${instance.workload.cpu_utilization.toFixed(1)}%`} icon={<Cpu size={20} />} />
          <DetailMetric label="Connections" value={formatLargeNumber(Math.round(instance.workload.connections))} icon={<Network size={20} />} />
          <DetailMetric label="Read IOPS" value={formatLargeNumber(Math.round(instance.workload.read_iops))} icon={<Database size={20} />} />
          <DetailMetric label="Write IOPS" value={formatLargeNumber(Math.round(instance.workload.write_iops))} icon={<HardDrive size={20} />} />
          <DetailMetric label="Monthly Cost" value={formatCurrency(instance.workload.monthly_cost_estimate)} icon={<Sparkles size={20} />} />
          <DetailMetric label="Yearly Savings" value={formatCurrency(yearlySavings)} icon={<ShieldAlert size={20} />} />
        </section>

        <RdsRightsizerPanel inventoryId={instance.inventory_id} />

        <section className="grid gap-6 xl:grid-cols-[1.15fr_0.85fr]">
          <Card className="border-gray-200 bg-white/95 dark:border-gray-700 dark:bg-gray-900/95">
            <div className="mb-4 text-sm font-semibold text-gray-700 dark:text-gray-200">Instance snapshot</div>
            <div className="grid gap-4 md:grid-cols-2">
              {[
                ['Resource ID', instance.resource_id],
                ['Created', formatDateTime(instance.created_at)],
                ['Account', instance.account_id || 'Unavailable'],
                ['Availability zone', instance.availability_zone || 'Unavailable'],
                ['Endpoint', endpoint.Address || endpoint.address || 'Unavailable'],
                ['Port', endpoint.Port || endpoint.port || 'Unavailable'],
                ['Engine version', instance.metadata.engine_version || instance.aws_payload?.engine_version || 'Unavailable'],
                ['Storage type', instance.metadata.storage_type || instance.aws_payload?.storage_type || 'Unavailable'],
              ].map(([label, value]) => (
                <div key={String(label)} className="rounded-2xl bg-gray-50 p-4 dark:bg-gray-800/70">
                  <div className="text-xs font-semibold uppercase tracking-[0.16em] text-gray-500 dark:text-gray-400">{label}</div>
                  <div className="mt-2 text-sm font-medium text-gray-900 dark:text-white">{String(value)}</div>
                </div>
              ))}
            </div>
          </Card>

          <Card className="border-gray-200 bg-white/95 dark:border-gray-700 dark:bg-gray-900/95">
            <div className="mb-4 text-sm font-semibold text-gray-700 dark:text-gray-200">Optimization insight</div>
            <div className="space-y-4">
              <div className="rounded-2xl bg-gray-50 p-4 dark:bg-gray-800/70">
                <div className="text-xs font-semibold uppercase tracking-[0.16em] text-gray-500 dark:text-gray-400">Finding</div>
                <div className="mt-2 text-lg font-semibold text-gray-900 dark:text-white">{instance.maxops.title || 'Healthy instance'}</div>
                <div className="mt-2 text-sm leading-6 text-gray-600 dark:text-gray-300">
                  {instance.maxops.description || 'No active issue was attached to this imported instance.'}
                </div>
              </div>
              <div className="rounded-2xl bg-gray-50 p-4 dark:bg-gray-800/70">
                <div className="text-xs font-semibold uppercase tracking-[0.16em] text-gray-500 dark:text-gray-400">Recommended action</div>
                <div className="mt-2 text-sm font-semibold text-gray-900 dark:text-white">
                  {instance.maxops.recommended_action ? titleCase(instance.maxops.recommended_action) : 'Observe'}
                </div>
              </div>
              <div className="rounded-2xl bg-gray-50 p-4 dark:bg-gray-800/70">
                <div className="text-xs font-semibold uppercase tracking-[0.16em] text-gray-500 dark:text-gray-400">Runbook action</div>
                <div className="mt-3 space-y-3">
                  <div className="flex flex-col gap-2 sm:flex-row sm:items-center">
                    <select
                      value={activeAction}
                      onChange={(event) => setSelectedAction(event.target.value)}
                      className="min-w-0 flex-1 rounded-xl border border-gray-200 bg-white px-3 py-2.5 text-sm text-gray-700 outline-none transition focus:border-primary-300 focus:ring-2 focus:ring-primary-100 dark:border-gray-700 dark:bg-gray-900/80 dark:text-gray-100 dark:focus:border-primary-500 dark:focus:ring-primary-900/40"
                    >
                      {actionOptions.length === 0 ? <option value="">No actions available</option> : null}
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
                      className="inline-flex shrink-0 items-center justify-center gap-2 rounded-xl bg-primary-600 px-4 py-2.5 text-sm font-semibold text-white transition hover:bg-primary-700 disabled:cursor-not-allowed disabled:opacity-50"
                    >
                      <Play size={14} />
                      {isExecutingAction ? 'Executing...' : 'Execute'}
                    </button>
                    <div className="flex h-10 w-10 shrink-0 items-center justify-center">
                      {actionMessage && isActionErrorMessage(actionMessage) ? <AlertCircle size={16} className="text-danger-500" /> : null}
                    </div>
                  </div>
                  {actionMessage ? (
                    <div className={`text-sm ${isActionErrorMessage(actionMessage) ? 'text-danger-600 dark:text-danger-300' : 'text-gray-600 dark:text-gray-300'}`}>
                      {actionMessage}
                    </div>
                  ) : null}
                </div>
              </div>
            </div>
          </Card>
        </section>

        <section className="grid gap-6 xl:grid-cols-2">
          <TrendCard
            title="CPU trend"
            subtitle="Imported CPU utilization for this RDS instance."
            points={cpuPoints}
            color={CHART_COLORS[0]}
            formatter={(value) => `${value.toFixed(1)}%`}
          />
          <TrendCard
            title="Connection trend"
            subtitle="Imported database connection history for this instance."
            points={connectionPoints}
            color={CHART_COLORS[3]}
          />
        </section>

        <section className="grid gap-6 xl:grid-cols-3">
          <Card className="border-gray-200 bg-white/95 dark:border-gray-700 dark:bg-gray-900/95">
            <div className="mb-4 text-sm font-semibold text-gray-700 dark:text-gray-200">Config delta</div>
            <div className="space-y-3">
              {Object.keys({ ...currentConfig, ...targetConfig }).length === 0 ? (
                <div className="rounded-2xl bg-gray-50 px-4 py-3 text-sm text-gray-500 dark:bg-gray-800/70 dark:text-gray-400">
                  No current or target configuration delta was attached to this finding.
                </div>
              ) : (
                Object.keys({ ...currentConfig, ...targetConfig }).map((key) => (
                  <div key={key} className="rounded-2xl border border-gray-200 bg-gray-50 px-4 py-3 dark:border-gray-700 dark:bg-gray-800/70">
                    <div className="text-xs font-semibold uppercase tracking-[0.16em] text-gray-500 dark:text-gray-400">{titleCase(key)}</div>
                    <div className="mt-2 grid gap-2">
                      <div className="text-sm text-gray-700 dark:text-gray-200">Current: {String(currentConfig[key] ?? 'n/a')}</div>
                      <div className="text-sm font-semibold text-gray-900 dark:text-white">Target: {String(targetConfig[key] ?? 'n/a')}</div>
                    </div>
                  </div>
                ))
              )}
            </div>
          </Card>

          <Card className="border-gray-200 bg-white/95 dark:border-gray-700 dark:bg-gray-900/95">
            <div className="mb-4 text-sm font-semibold text-gray-700 dark:text-gray-200">Metadata highlights</div>
            <div className="space-y-3">
              {[
                ['Environment', instance.tags.env || instance.metadata.environment || 'Unavailable'],
                ['Team', instance.metadata.team || instance.tags.team || 'Unavailable'],
                ['Owner', instance.metadata.owner || instance.tags.owner || 'Unavailable'],
                ['DB name', instance.metadata.db_name || instance.aws_payload?.db_name || 'Unavailable'],
                ['Multi AZ', String(instance.metadata.multi_az ?? instance.aws_payload?.multi_az ?? 'Unavailable')],
                ['Storage GB', String(instance.metadata.allocated_storage ?? instance.aws_payload?.allocated_storage ?? 'Unavailable')],
              ].map(([label, value]) => (
                <div key={String(label)} className="rounded-2xl bg-gray-50 px-4 py-3 dark:bg-gray-800/70">
                  <div className="text-xs font-semibold uppercase tracking-[0.16em] text-gray-500 dark:text-gray-400">{label}</div>
                  <div className="mt-2 text-sm font-medium text-gray-900 dark:text-white">{String(value)}</div>
                </div>
              ))}
            </div>
          </Card>

          <Card className="border-gray-200 bg-white/95 dark:border-gray-700 dark:bg-gray-900/95">
            <div className="mb-4 text-sm font-semibold text-gray-700 dark:text-gray-200">Tags</div>
            <div className="space-y-3">
              {tags.length === 0 ? (
                <div className="rounded-2xl bg-gray-50 px-4 py-3 text-sm text-gray-500 dark:bg-gray-800/70 dark:text-gray-400">
                  No tags were imported for this instance.
                </div>
              ) : (
                tags.map(([key, value]) => (
                  <div key={key} className="flex items-center justify-between rounded-2xl bg-gray-50 px-4 py-3 dark:bg-gray-800/70">
                    <div className="text-sm font-medium text-gray-700 dark:text-gray-200">{key}</div>
                    <div className="text-sm font-semibold text-gray-900 dark:text-white">{value}</div>
                  </div>
                ))
              )}
            </div>
          </Card>
        </section>
      </div>
    </Layout>
  );
};
