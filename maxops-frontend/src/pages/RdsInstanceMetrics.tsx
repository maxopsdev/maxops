import React, { useMemo } from 'react';
import { useNavigate, useParams } from 'react-router-dom';
import { useQuery } from 'react-query';
import {
  CartesianGrid,
  Line,
  LineChart,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from 'recharts';
import { ArrowLeft, BarChart3, Database, Gauge, HardDrive, Network } from 'lucide-react';
import { Layout } from '@/components/layout/Layout';
import { Card } from '@/components/common/Card';
import { useTheme } from '@/contexts/ThemeContext';
import { inventoryApi } from '@/services/inventory';
import { CHART_COLORS, chartGridColor, chartTickColor } from '@/styles/chartColors';

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

const chartSeries = [
  { key: 'cpu', label: 'CPU Utilization', color: CHART_COLORS[0], formatter: (value: number) => `${value.toFixed(1)}%` },
  { key: 'connections', label: 'Connections', color: CHART_COLORS[3], formatter: (value: number) => formatLargeNumber(Math.round(value)) },
  { key: 'read_iops', label: 'Read IOPS', color: CHART_COLORS[1], formatter: (value: number) => formatLargeNumber(Math.round(value)) },
  { key: 'write_iops', label: 'Write IOPS', color: CHART_COLORS[5], formatter: (value: number) => formatLargeNumber(Math.round(value)) },
] as const;

const StatCard: React.FC<{ label: string; value: string; icon: React.ReactNode; subtitle: string }> = ({ label, value, icon, subtitle }) => (
  <Card className="border-gray-200 bg-white/95 dark:border-gray-700 dark:bg-gray-900/95">
    <div className="flex items-start justify-between gap-4">
      <div>
        <div className="text-xs font-semibold uppercase tracking-[0.18em] text-gray-500 dark:text-gray-400">{label}</div>
        <div className="mt-3 text-3xl font-semibold text-gray-900 dark:text-white">{value}</div>
        <div className="mt-2 text-sm text-gray-500 dark:text-gray-400">{subtitle}</div>
      </div>
      <div className="rounded-2xl bg-primary-50 p-3 text-primary-700 dark:bg-primary-950/50 dark:text-primary-300">{icon}</div>
    </div>
  </Card>
);

const MetricChart: React.FC<{
  title: string;
  subtitle: string;
  color: string;
  points: Array<{ timestamp: string; average: number }>;
  valueFormatter?: (value: number) => string;
}> = ({ title, subtitle, color, points, valueFormatter = (value) => String(value) }) => {
  const { theme } = useTheme();
  const chartData = [...points].reverse().map((point) => ({
    ...point,
    label: new Date(point.timestamp).toLocaleDateString('en-US', { month: 'short', day: 'numeric' }),
  }));

  return (
    <Card className="border-gray-200 bg-white/95 dark:border-gray-700 dark:bg-gray-900/95">
      <div className="mb-4">
        <div className="text-sm font-semibold text-gray-700 dark:text-gray-200">{title}</div>
        <div className="mt-1 text-sm text-gray-500 dark:text-gray-400">{subtitle}</div>
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
              <Tooltip
                formatter={(value: unknown) => valueFormatter(Number(value || 0))}
                labelFormatter={(_, payload) => formatDateTime(payload?.[0]?.payload?.timestamp)}
              />
              <Line type="monotone" dataKey="average" stroke={color} strokeWidth={2.5} dot={false} />
            </LineChart>
          </ResponsiveContainer>
        </div>
      )}
    </Card>
  );
};

export const RdsInstanceMetricsPage: React.FC = () => {
  const navigate = useNavigate();
  const { instanceId } = useParams<{ instanceId: string }>();
  const { data, isLoading, isError } = useQuery('inventory-rds-overview', () => inventoryApi.getRdsOverview());

  const instance = useMemo(() => data?.instances.find((entry) => entry.resource_id === instanceId), [data?.instances, instanceId]);

  if (isLoading) {
    return (
      <Layout>
        <div className="flex min-h-[60vh] items-center justify-center rounded-[32px] border border-gray-200 bg-white/90 dark:border-gray-700 dark:bg-gray-900/90">
          <div className="text-center">
            <div className="text-lg font-semibold text-gray-800 dark:text-gray-100">Loading workload view</div>
            <div className="mt-2 text-sm text-gray-500 dark:text-gray-400">Collecting imported metric and metadata detail.</div>
          </div>
        </div>
      </Layout>
    );
  }

  if (isError || !data || !instance) {
    return (
      <Layout>
        <Card className="border-danger-200 bg-danger-50 dark:border-danger-900/50 dark:bg-danger-950/30">
          <div className="text-lg font-semibold text-danger-900 dark:text-danger-100">Workload view unavailable</div>
          <div className="mt-2 text-sm text-danger-700 dark:text-danger-200">The selected RDS instance was not found in the imported snapshot.</div>
        </Card>
      </Layout>
    );
  }

  const endpoint = instance.metadata.endpoint || instance.aws_payload?.endpoint || {};
  const networkMetadata = instance.metadata.network || instance.aws_payload?.network || {};
  const storageMetadata = instance.metadata.storage || instance.aws_payload?.storage || {};

  return (
    <Layout>
      <div className="space-y-8">
        <section className="overflow-hidden rounded-[32px] border border-gray-200 bg-[radial-gradient(circle_at_top_left,_rgba(22,193,168,0.2),_transparent_28%),radial-gradient(circle_at_bottom_right,_rgba(124,58,237,0.18),_transparent_24%),linear-gradient(135deg,_#f5fdfb_0%,_#ecfdf9_48%,_#f5f7fb_100%)] p-8 shadow-sm dark:border-gray-700 dark:bg-[radial-gradient(circle_at_top_left,_rgba(22,193,168,0.18),_transparent_22%),radial-gradient(circle_at_bottom_right,_rgba(124,58,237,0.16),_transparent_18%),linear-gradient(135deg,_rgba(14,24,48,0.98)_0%,_rgba(24,33,64,0.97)_48%,_rgba(14,24,48,0.98)_100%)]">
          <button
            type="button"
            onClick={() => navigate(`/dashboard/resources/rds/instances/${encodeURIComponent(instance.resource_id)}`)}
            className="mb-5 inline-flex items-center gap-2 rounded-full border border-gray-200 bg-white/80 px-4 py-2 text-sm font-medium text-gray-700 transition hover:bg-white dark:border-gray-700 dark:bg-gray-900/80 dark:text-gray-200"
          >
            <ArrowLeft size={16} />
            Instance details
          </button>

          <div className="flex flex-col gap-6 xl:flex-row xl:items-end xl:justify-between">
            <div>
              <div className="text-xs font-semibold uppercase tracking-[0.28em] text-gray-500 dark:text-gray-400">RDS Workload View</div>
              <h1 className="mt-3 text-4xl font-semibold tracking-tight text-gray-950 dark:text-gray-50">
                {instance.resource_name || instance.resource_id}
              </h1>
              <div className="mt-3 text-sm text-gray-600 dark:text-gray-300">
                {titleCase(String(instance.engine || 'unknown'))} - {instance.db_instance_class || 'Unknown class'} - imported metrics and metadata
              </div>
            </div>
            <div className="rounded-[28px] border border-white/70 bg-white/70 px-5 py-4 shadow-sm backdrop-blur dark:border-gray-700 dark:bg-gray-900/70">
              <div className="text-xs font-semibold uppercase tracking-[0.18em] text-gray-500 dark:text-gray-400">Latest imported sample</div>
              <div className="mt-2 text-sm font-semibold text-gray-900 dark:text-white">
                {formatDateTime(instance.workload.trends.cpu.latest?.timestamp || instance.created_at)}
              </div>
            </div>
          </div>
        </section>

        <section className="grid gap-4 md:grid-cols-2 xl:grid-cols-4">
          <StatCard label="CPU" value={`${instance.workload.cpu_utilization.toFixed(1)}%`} subtitle="Current imported utilization" icon={<Gauge size={20} />} />
          <StatCard label="Connections" value={formatLargeNumber(Math.round(instance.workload.connections))} subtitle="Current database sessions" icon={<Network size={20} />} />
          <StatCard label="Read IOPS" value={formatLargeNumber(Math.round(instance.workload.read_iops))} subtitle="Imported read pressure" icon={<Database size={20} />} />
          <StatCard label="Write IOPS" value={formatLargeNumber(Math.round(instance.workload.write_iops))} subtitle="Imported write pressure" icon={<HardDrive size={20} />} />
        </section>

        <section className="grid gap-6 xl:grid-cols-2">
          {chartSeries.map((series) => (
            <MetricChart
              key={series.key}
              title={series.label}
              subtitle={`Historic ${series.label.toLowerCase()} samples from the imported synthetic dataset.`}
              color={series.color}
              points={instance.workload.trends[series.key].points}
              valueFormatter={series.formatter}
            />
          ))}
        </section>

        <section className="grid gap-6 xl:grid-cols-3">
          <Card className="border-gray-200 bg-white/95 dark:border-gray-700 dark:bg-gray-900/95">
            <div className="mb-4 flex items-center gap-2 text-sm font-semibold text-gray-700 dark:text-gray-200">
              <Network size={16} />
              Endpoint and network
            </div>
            <div className="space-y-3">
              {[
                ['Address', endpoint.Address || endpoint.address || 'Unavailable'],
                ['Port', endpoint.Port || endpoint.port || 'Unavailable'],
                ['Subnet group', instance.metadata.db_subnet_group || networkMetadata.db_subnet_group || 'Unavailable'],
                ['Security groups', Array.isArray(networkMetadata.security_groups) ? networkMetadata.security_groups.join(', ') : 'Unavailable'],
                ['Availability zone', instance.availability_zone || 'Unavailable'],
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
              <HardDrive size={16} />
              Storage and engine
            </div>
            <div className="space-y-3">
              {[
                ['Allocated storage', String(instance.metadata.allocated_storage ?? storageMetadata.allocated_storage ?? 'Unavailable')],
                ['Storage type', instance.metadata.storage_type || storageMetadata.storage_type || 'Unavailable'],
                ['Engine version', instance.metadata.engine_version || instance.aws_payload?.engine_version || 'Unavailable'],
                ['License model', instance.metadata.license_model || instance.aws_payload?.license_model || 'Unavailable'],
                ['Backup retention', String(instance.metadata.backup_retention_period ?? instance.aws_payload?.backup_retention_period ?? 'Unavailable')],
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
              <BarChart3 size={16} />
              Optimization context
            </div>
            <div className="space-y-3">
              {[
                ['Finding', instance.maxops.title || 'Healthy instance'],
                ['Severity', instance.maxops.severity ? titleCase(instance.maxops.severity) : 'Healthy'],
                ['Status', titleCase(instance.maxops.status || 'healthy')],
                ['Recommended action', instance.maxops.recommended_action ? titleCase(instance.maxops.recommended_action) : 'Observe'],
                ['Evidence keys', formatLargeNumber(Object.keys(instance.maxops.evidence || {}).length)],
              ].map(([label, value]) => (
                <div key={String(label)} className="rounded-2xl bg-gray-50 px-4 py-3 dark:bg-gray-800/70">
                  <div className="text-xs font-semibold uppercase tracking-[0.16em] text-gray-500 dark:text-gray-400">{label}</div>
                  <div className="mt-2 text-sm font-medium text-gray-900 dark:text-white">{String(value)}</div>
                </div>
              ))}
            </div>
          </Card>
        </section>
      </div>
    </Layout>
  );
};
