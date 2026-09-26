import React, { useMemo } from 'react';
import { useNavigate, useParams } from 'react-router-dom';
import { useQuery } from 'react-query';
import { ArrowLeft, Cpu, Layers, ShieldAlert, Target, Wallet } from 'lucide-react';
import { Layout } from '@/components/layout/Layout';
import { Card } from '@/components/common/Card';
import { inventoryApi } from '@/services/inventory';
import { rightsizingApi } from '@/services/recommendations';

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

const titleCase = (value: string) =>
  value
    .replace(/([a-z])([A-Z])/g, '$1 $2')
    .split(/[_\s-]+/)
    .filter(Boolean)
    .map((part) => part.charAt(0).toUpperCase() + part.slice(1))
    .join(' ');

const DetailMetric: React.FC<{ label: string; value: string; icon: React.ReactNode }> = ({ label, value, icon }) => (
  <div className="rounded-2xl border border-gray-200 bg-white/95 p-5 dark:border-gray-700 dark:bg-gray-900/95">
    <div className="flex items-center justify-between gap-4">
      <div className="min-w-0">
        <div className="text-xs font-semibold uppercase tracking-[0.18em] text-gray-500 dark:text-gray-400">{label}</div>
        <div className="mt-3 truncate text-2xl font-semibold text-gray-900 dark:text-white">{value}</div>
      </div>
      <div className="rounded-2xl bg-warning-50 p-3 text-warning-700 dark:bg-warning-950/50 dark:text-warning-300">{icon}</div>
    </div>
  </div>
);

const SnapshotField: React.FC<{ label: string; value: React.ReactNode }> = ({ label, value }) => (
  <div className="rounded-2xl bg-gray-50 p-4 dark:bg-gray-800/70">
    <div className="text-xs font-semibold uppercase tracking-[0.16em] text-gray-500 dark:text-gray-400">{label}</div>
    <div className="mt-2 text-sm font-medium text-gray-900 dark:text-white">{value}</div>
  </div>
);

export const AsgInstanceDetailsPage: React.FC = () => {
  const navigate = useNavigate();
  const { instanceId } = useParams<{ instanceId: string }>();
  const resourceId = instanceId ? decodeURIComponent(instanceId) : '';

  const overviewQuery = useQuery('asg-overview', () => inventoryApi.getAsgOverview());
  const recommendationsQuery = useQuery('asg-recommendations', () => rightsizingApi.listAsgRecommendations());

  const resource = useMemo(
    () => overviewQuery.data?.resources.find((entry) => entry.resource_id === resourceId),
    [overviewQuery.data?.resources, resourceId]
  );
  const recommendation = useMemo(
    () => recommendationsQuery.data?.find((entry) => entry.resource_id === resourceId || entry.inventory_id === resource?.inventory_id),
    [recommendationsQuery.data, resource?.inventory_id, resourceId]
  );

  if (overviewQuery.isLoading || recommendationsQuery.isLoading) {
    return (
      <Layout>
        <div className="flex min-h-[60vh] items-center justify-center rounded-[32px] border border-gray-200 bg-white/90">
          <div className="text-center">
            <div className="text-lg font-semibold text-gray-800">Loading ASG details</div>
            <div className="mt-2 text-sm text-gray-500">Preparing the current Auto Scaling Group snapshot from imported inventory.</div>
          </div>
        </div>
      </Layout>
    );
  }

  if (overviewQuery.isError || !overviewQuery.data || !resource) {
    return (
      <Layout>
        <Card className="border-danger-200 bg-danger-50">
          <div className="text-lg font-semibold text-danger-900">ASG unavailable</div>
          <div className="mt-2 text-sm text-danger-700">The selected Auto Scaling Group could not be found in the imported inventory snapshot.</div>
        </Card>
      </Layout>
    );
  }

  const balanced = recommendation?.tiers.balanced;
  const effectiveSavings = balanced?.yearly_savings ?? resource.maxops.potential_savings_yearly ?? 0;
  const generatedAt = recommendation?.current_capacity_evidence.inventory_generated_at || overviewQuery.data.generated_at;
  const availabilityZones = recommendation?.current_configuration.availability_zones || resource.metadata.availability_zones || [];
  const classification = recommendation?.classification || resource.maxops.status || 'UNKNOWN';

  return (
    <Layout>
      <div className="space-y-8">
        <section className="overflow-hidden rounded-[32px] border border-gray-200 bg-[radial-gradient(circle_at_top_left,_rgba(245,166,35,0.24),_transparent_34%),linear-gradient(135deg,_#fffdf7_0%,_#fffbeb_42%,_#ecfdf9_100%)] p-8 shadow-sm dark:border-gray-700 dark:bg-[radial-gradient(circle_at_top_left,_rgba(245,166,35,0.14),_transparent_28%),linear-gradient(135deg,_rgba(14,24,48,0.98)_0%,_rgba(24,33,64,0.96)_46%,_rgba(14,24,48,0.98)_100%)]">
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
              <div className="text-xs font-semibold uppercase tracking-[0.28em] text-gray-500">ASG Details</div>
              <h1 className="mt-3 text-4xl font-semibold tracking-tight text-gray-950 dark:text-gray-50">
                {resource.resource_name || resource.resource_id}
              </h1>
              <div className="mt-3 text-sm text-gray-600 dark:text-gray-300">
                {resource.instance_type || 'Unknown type'} | {resource.region || 'Unknown region'} | {resource.state || 'Unknown state'}
              </div>
            </div>

            <div className="flex flex-wrap gap-2">
              {[resource.region, resource.instance_type, resource.state].filter(Boolean).map((value) => (
                <span
                  key={String(value)}
                  className="rounded-full border border-gray-200 bg-white/80 px-3 py-1.5 text-xs font-semibold uppercase tracking-[0.12em] text-gray-700"
                >
                  {value}
                </span>
              ))}
            </div>
          </div>
        </section>

        <section className="grid gap-4 xl:grid-cols-6">
          <DetailMetric label="Desired" value={String(resource.capacity.desired_capacity)} icon={<Cpu size={20} />} />
          <DetailMetric label="Min Size" value={String(resource.capacity.min_size)} icon={<Layers size={20} />} />
          <DetailMetric label="Max Size" value={String(resource.capacity.max_size)} icon={<Layers size={20} />} />
          <DetailMetric label="Balanced Target" value={balanced ? String(balanced.target_desired_capacity) : 'N/A'} icon={<Target size={20} />} />
          <DetailMetric label="Potential Savings" value={formatCurrency(effectiveSavings)} icon={<Wallet size={20} />} />
          <DetailMetric label="Status" value={titleCase(classification)} icon={<ShieldAlert size={20} />} />
        </section>

        <section className="grid gap-6 xl:grid-cols-[1.15fr_0.85fr]">
          <Card className="border-gray-200 bg-white/95 dark:border-gray-700 dark:bg-gray-900/95">
            <div className="mb-4 text-sm font-semibold text-gray-700 dark:text-gray-200">Group snapshot</div>
            <div className="grid gap-4 md:grid-cols-2">
              <SnapshotField label="Resource ID" value={resource.resource_id} />
              <SnapshotField label="Generated" value={formatDateTime(generatedAt)} />
              <SnapshotField label="Account" value={resource.account_id || 'Unknown'} />
              <SnapshotField label="Region" value={resource.region || 'Unknown'} />
              <SnapshotField label="Instance Type" value={resource.instance_type || 'Unknown'} />
              <SnapshotField label="Platform" value={resource.platform_normalized || 'Unknown'} />
              <SnapshotField label="In Service" value={String(resource.capacity.in_service_instances)} />
              <SnapshotField label="Availability Zones" value={Array.isArray(availabilityZones) && availabilityZones.length > 0 ? availabilityZones.join(', ') : 'Unavailable'} />
            </div>
          </Card>

          <Card className="border-gray-200 bg-white/95 dark:border-gray-700 dark:bg-gray-900/95">
            <div className="mb-4 text-sm font-semibold text-gray-700 dark:text-gray-200">Rightsizer recommendation</div>
            <div className="space-y-4">
              <SnapshotField label="Classification" value={titleCase(classification)} />
              <SnapshotField label="Finding" value={titleCase(resource.maxops.finding_type || 'healthy')} />
              <SnapshotField label="Current Monthly Cost" value={formatCurrency(recommendation?.current_monthly_cost ?? resource.capacity.monthly_cost_estimate ?? 0)} />
              <SnapshotField label="Annual Opportunity" value={formatCurrency(effectiveSavings)} />
            </div>
          </Card>
        </section>

        <section className="grid gap-6 xl:grid-cols-2">
          <Card className="border-gray-200 bg-white/95 dark:border-gray-700 dark:bg-gray-900/95">
            <div className="mb-4 text-sm font-semibold text-gray-700 dark:text-gray-200">Capacity plan</div>
            <div className="grid gap-4 md:grid-cols-2">
              <SnapshotField label="Current Min" value={String(resource.capacity.min_size)} />
              <SnapshotField label="Target Min" value={balanced ? String(balanced.target_min_size) : 'N/A'} />
              <SnapshotField label="Current Desired" value={String(resource.capacity.desired_capacity)} />
              <SnapshotField label="Target Desired" value={balanced ? String(balanced.target_desired_capacity) : 'N/A'} />
              <SnapshotField label="Current Max" value={String(resource.capacity.max_size)} />
              <SnapshotField label="Target Max" value={balanced ? String(balanced.target_max_size) : 'N/A'} />
            </div>
          </Card>

          <Card className="border-gray-200 bg-white/95 dark:border-gray-700 dark:bg-gray-900/95">
            <div className="mb-4 text-sm font-semibold text-gray-700 dark:text-gray-200">Tags</div>
            <div className="flex flex-wrap gap-2">
              {Object.entries(resource.tags || {}).length > 0 ? (
                Object.entries(resource.tags || {}).map(([key, value]) => (
                  <span key={key} className="rounded-full bg-gray-100 px-3 py-1.5 text-xs font-medium text-gray-700 dark:bg-gray-800 dark:text-gray-200">
                    {key}:{String(value)}
                  </span>
                ))
              ) : (
                <div className="text-sm text-gray-500 dark:text-gray-400">No tags were imported for this group.</div>
              )}
            </div>
          </Card>
        </section>
      </div>
    </Layout>
  );
};
