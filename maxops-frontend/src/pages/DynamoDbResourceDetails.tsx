import React, { useMemo, useState } from 'react';
import { useNavigate, useParams } from 'react-router-dom';
import { useQuery } from 'react-query';
import { CartesianGrid, Legend, Line, LineChart, ResponsiveContainer, Tooltip, XAxis, YAxis } from 'recharts';
import { AlertCircle, ArrowLeft, Database, Gauge, Play, ShieldCheck, Sparkles } from 'lucide-react';
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

const MetricCard: React.FC<{ label: string; value: string; subtitle: string; icon: React.ReactNode }> = ({ label, value, subtitle, icon }) => (
  <Card className="border-gray-200 bg-white/95 dark:border-gray-700 dark:bg-gray-900/95">
    <div className="flex items-start justify-between gap-4">
      <div>
        <div className="text-xs font-semibold uppercase tracking-[0.18em] text-gray-500 dark:text-gray-400">{label}</div>
        <div className="mt-3 text-3xl font-semibold text-gray-900 dark:text-white">{value}</div>
        <div className="mt-2 text-sm text-gray-500 dark:text-gray-400">{subtitle}</div>
      </div>
      <div className="rounded-2xl bg-primary-50 p-3 text-primary-700 dark:bg-primary-900/50 dark:text-primary-300">{icon}</div>
    </div>
  </Card>
);

const TrendCard: React.FC<{
  title: string;
  subtitle: string;
  points: Array<{ timestamp: string; average: number }>;
  color: string;
  formatter?: (value: number) => string;
}> = ({ title, subtitle, points, color, formatter = (value) => formatLargeNumber(Math.round(value)) }) => {
  const { theme } = useTheme();
  const chartData = [...points].reverse().map((point) => ({
    ...point,
    label: new Date(point.timestamp).toLocaleDateString('en-US', { month: 'short', day: 'numeric' }),
  }));

  return (
    <Card className="border-gray-200 bg-white/95 dark:border-gray-700 dark:bg-gray-900/95">
      <div className="mb-4 flex items-start justify-between gap-4">
        <div>
          <div className="text-sm font-semibold text-gray-700 dark:text-gray-200">{title}</div>
          <div className="mt-1 text-sm text-gray-500 dark:text-gray-400">{subtitle}</div>
        </div>
        <div className="rounded-full bg-gray-100 px-3 py-1 text-[11px] font-semibold uppercase tracking-[0.14em] text-gray-600 dark:bg-gray-800 dark:text-gray-300">
          {points[0] ? formatter(points[0].average) : 'No data'}
        </div>
      </div>
      {points.length === 0 ? (
        <div className="rounded-2xl bg-gray-50 px-4 py-8 text-sm text-gray-500 dark:bg-gray-800/70 dark:text-gray-400">
          No imported samples are available for this metric.
        </div>
      ) : (
        <div className="h-72">
          <ResponsiveContainer width="100%" height="100%">
            <LineChart data={chartData} margin={{ top: 8, right: 12, left: -12, bottom: 0 }}>
              <CartesianGrid strokeDasharray="3 3" stroke={chartGridColor(theme)} opacity={0.35} />
              <XAxis dataKey="label" stroke={chartTickColor(theme)} tickLine={false} axisLine={false} minTickGap={18} />
              <YAxis stroke={chartTickColor(theme)} tickLine={false} axisLine={false} />
              <Tooltip formatter={(value: unknown) => formatter(Number(value || 0))} labelFormatter={(_, payload) => formatDateTime(payload?.[0]?.payload?.timestamp)} />
              <Legend />
              <Line type="monotone" dataKey="average" name={title} stroke={color} strokeWidth={2.5} dot={false} />
            </LineChart>
          </ResponsiveContainer>
        </div>
      )}
    </Card>
  );
};

export const DynamoDbResourceDetailsPage: React.FC = () => {
  const navigate = useNavigate();
  const { resourceId } = useParams<{ resourceId: string }>();
  const { profile } = useOptimizationProfile();
  const [selectedAction, setSelectedAction] = useState('');
  const [isExecutingAction, setIsExecutingAction] = useState(false);
  const [actionMessage, setActionMessage] = useState('');

  const { data, isLoading, isError } = useQuery('inventory-dynamodb-overview', () => inventoryApi.getDynamoDbOverview());
  const resource = useMemo(() => data?.resources.find((entry) => entry.resource_id === resourceId), [data?.resources, resourceId]);
  const { data: check } = useQuery(['dynamodb-check-metadata', resource?.maxops.check_id], () => checksApi.getCheck(resource!.maxops.check_id!), {
    enabled: Boolean(resource?.maxops.check_id),
    retry: false,
  });

  if (isLoading) {
    return (
      <Layout>
        <div className="flex min-h-[60vh] items-center justify-center rounded-[32px] border border-gray-200 bg-white/90 dark:border-gray-700 dark:bg-gray-900/90">
          <div className="text-center">
            <div className="text-lg font-semibold text-gray-800 dark:text-gray-100">Loading DynamoDB resource details</div>
            <div className="mt-2 text-sm text-gray-500 dark:text-gray-400">Preparing imported throughput and configuration history.</div>
          </div>
        </div>
      </Layout>
    );
  }

  if (isError || !data || !resource) {
    return (
      <Layout>
        <Card className="border-danger-200 bg-danger-50 dark:border-danger-900/50 dark:bg-danger-900/30">
          <div className="text-lg font-semibold text-danger-900 dark:text-danger-100">DynamoDB resource unavailable</div>
          <div className="mt-2 text-sm text-danger-700 dark:text-danger-200">The selected resource could not be found in the imported snapshot.</div>
        </Card>
      </Layout>
    );
  }

  const actionOptions = (() => {
    const actions = check ? getActionsForCheck(check) : [];
    const recommended = resource.maxops.recommended_action?.trim() || '';
    if (!recommended) return actions;
    return actions.includes(recommended) ? actions : [recommended, ...actions];
  })();
  const activeAction = selectedAction || actionOptions[0] || '';
  const canExecuteAction = Boolean(resource.maxops.check_id && resource.account_id && resource.region && activeAction);

  const handleExecuteAction = async () => {
    if (!resource.maxops.check_id || !resource.account_id || !resource.region || !activeAction) {
      setActionMessage('Missing check, account, region, or action for execution.');
      return;
    }
    setIsExecutingAction(true);
    setActionMessage('');
    try {
      const response = await checksApi.executeAction(resource.maxops.check_id, {
        action: activeAction,
        account_id: resource.account_id,
        region: resource.region,
        resource_id: resource.resource_id,
      });
      setActionMessage(response.message || 'Action executed.');
    } catch (error: any) {
      setActionMessage(error?.response?.data?.detail || error?.message || 'Failed to execute action.');
    } finally {
      setIsExecutingAction(false);
    }
  };

  const currentConfig = resource.maxops.current_config || {};
  const targetConfig = resource.maxops.target_config || {};
  const tags = Object.entries(resource.tags || {});
  const yearlySavings = adjustOptimizationSavings(Number(resource.maxops.potential_savings_yearly || 0), profile);

  return (
    <Layout>
      <div className="space-y-8">
        <section className="overflow-hidden rounded-[32px] border border-gray-200 bg-[radial-gradient(circle_at_top_left,_rgba(52,224,196,0.24),_transparent_30%),radial-gradient(circle_at_bottom_right,_rgba(22,193,168,0.18),_transparent_24%),linear-gradient(135deg,_#f5fdfb_0%,_#e9f9f4_44%,_#ecfdf9_100%)] p-8 shadow-sm dark:border-gray-700 dark:bg-[radial-gradient(circle_at_top_left,_rgba(52,224,196,0.18),_transparent_24%),radial-gradient(circle_at_bottom_right,_rgba(22,193,168,0.16),_transparent_20%),linear-gradient(135deg,_rgba(14,24,48,0.98)_0%,_rgba(24,33,64,0.96)_46%,_rgba(14,24,48,0.98)_100%)]">
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
              <div className="text-xs font-semibold uppercase tracking-[0.28em] text-gray-500 dark:text-gray-400">DynamoDB Resource Details</div>
              <h1 className="mt-3 text-4xl font-semibold tracking-tight text-gray-950 dark:text-gray-50">{resource.resource_name || resource.resource_id}</h1>
              <div className="mt-3 text-sm text-gray-600 dark:text-gray-300">
                {titleCase(resource.resource_type)} - {titleCase(String(resource.billing_mode || 'unknown'))} - {resource.region || 'Unknown region'}
              </div>
            </div>
            <div className="flex flex-wrap gap-2">
              <span className="rounded-full bg-primary-50 px-3 py-1.5 text-xs font-semibold uppercase tracking-[0.12em] text-primary-700 dark:bg-primary-900/40 dark:text-primary-300">
                {titleCase(String(resource.table_class || 'unknown class'))}
              </span>
              <span className="rounded-full border border-gray-200 bg-white/80 px-3 py-1.5 text-xs font-semibold uppercase tracking-[0.12em] text-gray-700 dark:border-gray-700 dark:bg-gray-900/80 dark:text-gray-200">
                {resource.maxops.severity ? titleCase(resource.maxops.severity) : 'Healthy'}
              </span>
            </div>
          </div>
        </section>

        <section className="grid gap-4 md:grid-cols-2 xl:grid-cols-4">
          <MetricCard label="Item Count" value={formatLargeNumber(resource.workload.item_count)} subtitle="Imported inventory size" icon={<Database size={20} />} />
          <MetricCard label="Avg RCU" value={resource.workload.avg_consumed_rcu.toFixed(2)} subtitle={`P95 ${resource.workload.p95_consumed_rcu.toFixed(2)}`} icon={<Gauge size={20} />} />
          <MetricCard label="Avg WCU" value={resource.workload.avg_consumed_wcu.toFixed(2)} subtitle={`P95 ${resource.workload.p95_consumed_wcu.toFixed(2)}`} icon={<Gauge size={20} />} />
          <MetricCard label="Yearly Savings" value={formatCurrency(yearlySavings)} subtitle="Potential optimization upside" icon={<ShieldCheck size={20} />} />
        </section>

        <section className="grid gap-6 xl:grid-cols-[1.15fr_0.85fr]">
          <Card className="border-gray-200 bg-white/95 dark:border-gray-700 dark:bg-gray-900/95">
            <div className="mb-4 text-sm font-semibold text-gray-700 dark:text-gray-200">Resource snapshot</div>
            <div className="grid gap-4 md:grid-cols-2">
              {[
                ['Resource ID', resource.resource_id],
                ['State', resource.state || 'Unavailable'],
                ['Resource type', titleCase(resource.resource_type)],
                ['Billing mode', titleCase(String(resource.billing_mode || 'unknown'))],
                ['Table name', resource.table_name || 'Unavailable'],
                ['Table class', titleCase(String(resource.table_class || 'unknown'))],
                ['Read capacity units', resource.workload.read_capacity_units.toFixed(2)],
                ['Write capacity units', resource.workload.write_capacity_units.toFixed(2)],
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
                <div className="mt-2 text-lg font-semibold text-gray-900 dark:text-white">{resource.maxops.title || 'Healthy resource'}</div>
                <div className="mt-2 text-sm leading-6 text-gray-600 dark:text-gray-300">{resource.maxops.description || 'No active issue was attached to this imported resource.'}</div>
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
                      {actionOptions.map((action) => <option key={action} value={action}>{titleCase(action)}</option>)}
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
          <TrendCard title="Consumed RCU trend" subtitle="Historic imported read-capacity consumption." points={resource.workload.trends.consumed_rcu.points} color={CHART_COLORS[0]} formatter={(value) => value.toFixed(2)} />
          <TrendCard title="Consumed WCU trend" subtitle="Historic imported write-capacity consumption." points={resource.workload.trends.consumed_wcu.points} color={CHART_COLORS[3]} formatter={(value) => value.toFixed(2)} />
        </section>

        <section className="grid gap-6 xl:grid-cols-3">
          <TrendCard title="Item Count trend" subtitle="Imported item-count evolution for this resource." points={resource.workload.trends.item_count.points} color={CHART_COLORS[0]} />
          <TrendCard title="Read throttles" subtitle="Historic imported read-throttle events." points={resource.workload.trends.read_throttle_events.points} color={CHART_COLORS[1]} formatter={(value) => value.toFixed(4)} />
          <TrendCard title="Write throttles" subtitle="Historic imported write-throttle events." points={resource.workload.trends.write_throttle_events.points} color={CHART_COLORS[2]} formatter={(value) => value.toFixed(4)} />
        </section>

        <section className="grid gap-6 xl:grid-cols-3">
          <Card className="border-gray-200 bg-white/95 dark:border-gray-700 dark:bg-gray-900/95">
            <div className="mb-4 text-sm font-semibold text-gray-700 dark:text-gray-200">Config delta</div>
            <div className="space-y-3">
              {Object.keys({ ...currentConfig, ...targetConfig }).length === 0 ? (
                <div className="rounded-2xl bg-gray-50 px-4 py-3 text-sm text-gray-500 dark:bg-gray-800/70 dark:text-gray-400">No current or target configuration delta was attached to this finding.</div>
              ) : (
                Object.keys({ ...currentConfig, ...targetConfig }).map((key) => (
                  <div key={key} className="rounded-2xl border border-gray-200 bg-gray-50 px-4 py-3 dark:border-gray-700 dark:bg-gray-800/70">
                    <div className="text-xs font-semibold uppercase tracking-[0.16em] text-gray-500 dark:text-gray-400">{titleCase(key)}</div>
                    <div className="mt-2 text-sm text-gray-700 dark:text-gray-200">Current: {String(currentConfig[key] ?? 'n/a')}</div>
                    <div className="mt-1 text-sm font-semibold text-gray-900 dark:text-white">Target: {String(targetConfig[key] ?? 'n/a')}</div>
                  </div>
                ))
              )}
            </div>
          </Card>

          <Card className="border-gray-200 bg-white/95 dark:border-gray-700 dark:bg-gray-900/95">
            <div className="mb-4 text-sm font-semibold text-gray-700 dark:text-gray-200">Metadata highlights</div>
            <div className="space-y-3">
              {[
                ['Environment', resource.tags.env || resource.metadata.environment || 'Unavailable'],
                ['Team', resource.metadata.team || resource.tags.team || 'Unavailable'],
                ['Owner', resource.metadata.owner || resource.tags.owner || 'Unavailable'],
                ['Criticality', resource.metadata.criticality || 'Unavailable'],
                ['Compliance', resource.metadata.compliance || 'Unavailable'],
                ['Data classification', resource.metadata.data_classification || 'Unavailable'],
              ].map(([label, value]) => (
                <div key={String(label)} className="rounded-2xl bg-gray-50 px-4 py-3 dark:bg-gray-800/70">
                  <div className="text-xs font-semibold uppercase tracking-[0.16em] text-gray-500 dark:text-gray-400">{label}</div>
                  <div className="mt-2 text-sm font-medium text-gray-900 dark:text-white">{String(value)}</div>
                </div>
              ))}
            </div>
          </Card>

          <Card className="border-gray-200 bg-white/95 dark:border-gray-700 dark:bg-gray-900/95">
            <div className="mb-4 flex items-center gap-2 text-sm font-semibold text-gray-700 dark:text-gray-200">
              <Sparkles size={16} />
              Tags and payload
            </div>
            <div className="space-y-3">
              <div className="rounded-2xl bg-gray-50 px-4 py-3 dark:bg-gray-800/70">
                <div className="text-xs font-semibold uppercase tracking-[0.16em] text-gray-500 dark:text-gray-400">Tag count</div>
                <div className="mt-2 text-sm font-medium text-gray-900 dark:text-white">{formatLargeNumber(tags.length)}</div>
              </div>
              <div className="rounded-2xl bg-gray-50 px-4 py-3 dark:bg-gray-800/70">
                <div className="text-xs font-semibold uppercase tracking-[0.16em] text-gray-500 dark:text-gray-400">Payload sections</div>
                <div className="mt-2 text-sm font-medium text-gray-900 dark:text-white">{formatLargeNumber(Object.keys(resource.aws_payload || {}).length)}</div>
              </div>
              <div className="rounded-2xl bg-gray-50 px-4 py-3 dark:bg-gray-800/70">
                <div className="text-xs font-semibold uppercase tracking-[0.16em] text-gray-500 dark:text-gray-400">Last imported sample</div>
                <div className="mt-2 text-sm font-medium text-gray-900 dark:text-white">{formatDateTime(resource.workload.trends.consumed_rcu.latest?.timestamp)}</div>
              </div>
            </div>
          </Card>
        </section>
      </div>
    </Layout>
  );
};
