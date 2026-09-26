import React, { useMemo, useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { useQuery, useQueryClient } from 'react-query';
import {
  Bar,
  BarChart,
  CartesianGrid,
  Cell,
  Pie,
  PieChart,
  ResponsiveContainer,
  Scatter,
  ScatterChart,
  Tooltip,
  XAxis,
  YAxis,
} from 'recharts';
import { AlertCircle, ArrowLeft, Database, DollarSign, Download, HardDrive, Search, Sparkles, X } from 'lucide-react';
import { Layout } from '@/components/layout/Layout';
import { Card } from '@/components/common/Card';
import { ResourceActionPanel } from '@/components/common/ResourceActionPanel';
import { getActionsForCheck } from '@/components/checks/checkActions';
import { ResourcePageFilters } from '@/components/common/ResourcePageFilters';
import { SharedTagFilterField } from '@/components/common/SharedTagFilterField';
import { ResourceTypeIcon } from '@/components/icons/ResourceTypeIcon';
import { useOptimizationProfile } from '@/contexts/OptimizationProfileContext';
import { useTheme } from '@/contexts/ThemeContext';
import worldMapUrl from '@/assets/maps/world-map.svg';
import { inventoryApi, type S3OverviewBucket, type S3OverviewResponse } from '@/services/inventory';
import { checksApi } from '@/services/checks';
import { useSharedOverviewFilters } from '@/stores/sharedOverviewFilters';
import { downloadCsv } from '@/utils/csv';
import { buildSharedTagOptions, formatSharedTagSelectionLabel, matchesSharedTagQuery, parseSharedTagQuery } from '@/utils/sharedOverviewFilters';
import { adjustOptimizationCount, adjustOptimizationSavings } from '@/utils/optimizationProfile';
import { CHART_COLORS, chartGridColor, chartTickColor } from '@/styles/chartColors';

const REGION_MAP_WIDTH = 2000;
const REGION_MAP_HEIGHT = 857;
const REGION_MARKER_POSITIONS: Record<string, { x: number; y: number }> = {
  'us-east-1': { x: 545, y: 250 },
  'us-east-2': { x: 523, y: 238 },
  'us-west-1': { x: 413, y: 246 },
  'us-west-2': { x: 377, y: 222 },
  'ca-central-1': { x: 528, y: 178 },
  'eu-west-1': { x: 955, y: 214 },
  'eu-west-2': { x: 992, y: 194 },
  'eu-central-1': { x: 1072, y: 214 },
  'eu-north-1': { x: 1088, y: 152 },
  'ap-south-1': { x: 1470, y: 345 },
  'ap-southeast-1': { x: 1602, y: 498 },
  'ap-southeast-2': { x: 1778, y: 675 },
  'ap-northeast-1': { x: 1722, y: 266 },
  'sa-east-1': { x: 744, y: 616 },
};

const formatCurrency = (value: number) =>
  new Intl.NumberFormat('en-US', {
    style: 'currency',
    currency: 'USD',
    minimumFractionDigits: value > 0 && value < 1 ? 2 : 0,
    maximumFractionDigits: value > 0 && value < 100 ? 2 : 0,
  }).format(value);
const formatLargeNumber = (value: number) => new Intl.NumberFormat('en-US').format(value);
const formatCompact = (value: number) => new Intl.NumberFormat('en-US', { notation: 'compact', maximumFractionDigits: 1 }).format(value);
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
type S3InventorySortKey = 'name' | 'cost' | 'savings' | 'size' | 'objects' | 'region';

const SummaryTile: React.FC<{ label: string; value: string; sublabel: string; icon: React.ReactNode }> = ({ label, value, sublabel, icon }) => (
  <Card className="border-gray-200 bg-white/95 dark:border-gray-700 dark:bg-gray-900/95">
    <div className="flex items-start justify-between gap-4">
      <div>
        <div className="text-xs font-semibold uppercase tracking-[0.18em] text-gray-500 dark:text-gray-400">{label}</div>
        <div className="mt-3 text-3xl font-semibold tracking-tight text-gray-900 dark:text-white">{value}</div>
        <div className="mt-2 text-sm text-gray-500 dark:text-gray-400">{sublabel}</div>
      </div>
      <div className="rounded-2xl bg-warning-50 p-3 text-warning-700 dark:bg-warning-900/50 dark:text-warning-300">{icon}</div>
    </div>
  </Card>
);

const ChartCard: React.FC<{ title: string; subtitle: string; children: React.ReactNode }> = ({ title, subtitle, children }) => (
  <Card className="border-gray-200 bg-white/95 dark:border-gray-700 dark:bg-gray-900/95">
    <div className="mb-5">
      <div className="text-sm font-semibold text-gray-700 dark:text-gray-200">{title}</div>
      <div className="mt-1 text-sm text-gray-500 dark:text-gray-400">{subtitle}</div>
    </div>
    {children}
  </Card>
);

const PostureCard: React.FC<{ label: string; count: number; total: number; active?: boolean; onClick?: () => void }> = ({ label, count, total, active = false, onClick }) => {
  const percentage = total ? Math.round((count / total) * 100) : 0;
  return (
    <Card className={`border-gray-200 bg-white/95 dark:border-gray-700 dark:bg-gray-900/95 ${onClick ? 'relative cursor-pointer transition hover:border-warning-300 hover:shadow-sm' : ''} ${active ? 'ring-2 ring-warning-300 dark:ring-warning-500' : ''}`}>
      {onClick ? <button type="button" aria-label={`Filter by missing ${label}`} onClick={onClick} className="absolute inset-0" /> : null}
      <div className="text-xs font-semibold uppercase tracking-[0.18em] text-gray-500 dark:text-gray-400">{label}</div>
      <div className="mt-3 flex items-end justify-between gap-3">
        <div className="text-3xl font-semibold text-gray-900 dark:text-white">{percentage}%</div>
        <div className="text-sm text-gray-500 dark:text-gray-400">{count}/{total}</div>
      </div>
      <div className="mt-3 h-3 overflow-hidden rounded-full bg-gray-100 dark:bg-gray-800">
        <div className="h-full rounded-full bg-success-500" style={{ width: `${percentage}%` }} />
      </div>
      {onClick ? (
        <div className="mt-3 text-[11px] font-semibold uppercase tracking-[0.14em] text-gray-500 dark:text-gray-400">
          {active ? 'Showing missing feature' : 'Click to show missing feature'}
        </div>
      ) : null}
    </Card>
  );
};

const getBucketCoverageBand = (bucket: S3OverviewBucket) => {
  const coreCoverage = [
    bucket.posture.versioning_enabled,
    bucket.posture.logging_enabled,
    bucket.posture.inventory_enabled,
    bucket.posture.lifecycle_enabled,
  ].filter(Boolean).length;

  if (coreCoverage >= 4) {
    return {
      bodyClass: 'bg-success-500',
      rimClass: bucket.posture.replication_enabled ? 'bg-primary-600' : 'bg-success-700',
      label: 'Strong baseline',
    };
  }
  if (coreCoverage === 3) {
    return {
      bodyClass: 'bg-primary-500',
      rimClass: bucket.posture.replication_enabled ? 'bg-indigo-600' : 'bg-primary-700',
      label: 'Mostly covered',
    };
  }
  if (coreCoverage === 2) {
    return {
      bodyClass: 'bg-warning-500',
      rimClass: bucket.posture.replication_enabled ? 'bg-primary-600' : 'bg-warning-700',
      label: 'Mixed posture',
    };
  }
  return {
    bodyClass: 'bg-danger-500',
    rimClass: bucket.posture.replication_enabled ? 'bg-violet-600' : 'bg-danger-700',
    label: 'Coverage gaps',
  };
};

const getBucketCoverageOrder = (bucket: S3OverviewBucket) => {
  const coreCoverage = [
    bucket.posture.versioning_enabled,
    bucket.posture.logging_enabled,
    bucket.posture.inventory_enabled,
    bucket.posture.lifecycle_enabled,
  ].filter(Boolean).length;

  if (coreCoverage <= 1) return 0;
  if (coreCoverage === 2) return 1;
  if (coreCoverage === 3) return 2;
  return 3;
};

const BucketCloudPanel: React.FC<{
  buckets: S3OverviewBucket[];
  onOpenBucket?: (bucket: S3OverviewBucket) => void;
}> = ({ buckets, onOpenBucket }) => (
  <Card className="border-gray-200 bg-white/95 dark:border-gray-700 dark:bg-gray-900/95">
    <div className="mb-4 flex items-center justify-between">
      <div>
        <div className="text-sm font-semibold text-gray-700 dark:text-gray-200">Bucket posture cloud</div>
        <div className="text-xs text-gray-500 dark:text-gray-400">Body color tracks core controls. The top rim changes if replication is enabled, but replication is not treated as a deficiency.</div>
      </div>
      <div className="rounded-full bg-gray-100 px-3 py-1 text-[11px] font-semibold uppercase tracking-[0.16em] text-gray-500 dark:bg-gray-800 dark:text-gray-300">
        {buckets.length} buckets
      </div>
    </div>

    <div className="mb-4 flex flex-wrap gap-2">
      {[
        ['Strong baseline', 'bg-success-500'],
        ['Mostly covered', 'bg-primary-500'],
        ['Mixed posture', 'bg-warning-500'],
        ['Coverage gaps', 'bg-danger-500'],
      ].map(([label, tone]) => (
        <div key={label} className="inline-flex items-center gap-2 rounded-full bg-gray-50 px-3 py-1.5 text-[11px] font-semibold uppercase tracking-[0.12em] text-gray-600 dark:bg-gray-800 dark:text-gray-300">
          <span className={`h-3 w-3 rounded-full ${tone}`} />
          {label}
        </div>
      ))}
      <div className="inline-flex items-center gap-2 rounded-full bg-gray-50 px-3 py-1.5 text-[11px] font-semibold uppercase tracking-[0.12em] text-gray-600 dark:bg-gray-800 dark:text-gray-300">
        <span className="h-1.5 w-4 rounded-full bg-primary-600" />
        Replication accent
      </div>
    </div>

    <div className="flex h-[26rem] flex-wrap content-start gap-x-1 gap-y-4 overflow-hidden py-2">
      {buckets.slice(0, 72).map((bucket, index) => {
        const palette = getBucketCoverageBand(bucket);
        return (
          <button
            key={bucket.resource_id}
            type="button"
            title={`${bucket.resource_name || bucket.resource_id} · ${palette.label}`}
            aria-label={bucket.resource_name || bucket.resource_id}
            onClick={() => onOpenBucket?.(bucket)}
            className="group relative flex h-12 w-12 items-end justify-center"
            style={{
              marginTop: index % 2 === 0 ? 0 : 10,
              marginLeft: index === 0 ? 0 : -2,
            }}
          >
            <span className={`absolute top-1 h-2.5 w-8 rounded-t-[10px] ${palette.rimClass} shadow-sm transition group-hover:scale-105`} />
            <span
              className={`absolute bottom-0 h-8 w-10 ${palette.bodyClass} shadow-sm transition group-hover:scale-105`}
              style={{
                clipPath: 'polygon(8% 0%, 92% 0%, 82% 100%, 18% 100%)',
                borderBottomLeftRadius: 8,
                borderBottomRightRadius: 8,
              }}
            />
          </button>
        );
      })}
    </div>
  </Card>
);

const RegionsPanel: React.FC<{
  rows: Array<{ region: string; count: number; total_size_gb: number; monthly_cost: number; yearly_savings: number }>;
  onOpenRegion?: (region: string) => void;
}> = ({ rows, onOpenRegion }) => {
  const maxCount = Math.max(...rows.map((row) => row.count), 1);
  const maxCost = Math.max(...rows.map((row) => row.monthly_cost), 1);

  return (
    <Card className="border-gray-200 bg-white/95 dark:border-gray-700 dark:bg-gray-900/95">
      <div className="mb-5 flex items-center justify-between">
        <div>
          <div className="text-sm font-semibold text-gray-700 dark:text-gray-200">Buckets per region</div>
          <div className="mt-1 text-xs text-gray-500 dark:text-gray-400">Regional S3 footprint across the visible filtered inventory</div>
        </div>
        <div className="flex items-center gap-3 text-[11px] font-semibold uppercase tracking-[0.14em] text-gray-500 dark:text-gray-400">
          <span className="inline-flex items-center gap-1.5">
            <span className="h-2.5 w-2.5 rounded-full bg-primary-600" />
            Buckets
          </span>
          <span className="inline-flex items-center gap-1.5">
            <span className="h-2.5 w-2.5 rounded-full bg-success-500" />
            Cost
          </span>
        </div>
      </div>

      <div className="grid gap-6 xl:grid-cols-[280px_minmax(0,1fr)]">
        <div className="space-y-4">
          {rows.map((row) => (
            <div key={row.region} className="space-y-2">
              <div className="flex items-center justify-between gap-3">
                <button
                  type="button"
                  onClick={() => onOpenRegion?.(row.region)}
                  className="text-sm font-semibold text-gray-700 transition hover:text-warning-700 dark:text-gray-200 dark:hover:text-warning-300"
                >
                  {row.region}
                </button>
                <button
                  type="button"
                  onClick={() => onOpenRegion?.(row.region)}
                  className="text-sm font-semibold text-gray-900 transition hover:text-warning-700 dark:text-white dark:hover:text-warning-300"
                >
                  {row.count}
                </button>
              </div>
              <div className="space-y-1.5">
                <div className="h-2 overflow-hidden rounded-full bg-gray-100 dark:bg-gray-800">
                  <div className="h-full rounded-full bg-primary-600" style={{ width: `${(row.count / maxCount) * 100}%` }} />
                </div>
                <div className="h-2 overflow-hidden rounded-full bg-gray-100 dark:bg-gray-800">
                  <div className="h-full rounded-full bg-success-500" style={{ width: `${(row.monthly_cost / maxCost) * 100}%` }} />
                </div>
              </div>
              <div className="flex items-center justify-between text-xs text-gray-500 dark:text-gray-400">
                <span>{formatCompact(row.total_size_gb)} GB</span>
                <span>{formatCurrency(row.monthly_cost)}</span>
              </div>
            </div>
          ))}
        </div>

        <div className="overflow-hidden rounded-[28px] border border-gray-200 bg-[linear-gradient(180deg,_#f5fdfb_0%,_#e9f9f4_100%)] p-4 dark:border-gray-700 dark:bg-[linear-gradient(180deg,_rgba(14,24,48,0.95)_0%,_rgba(24,33,64,0.95)_100%)]">
          <div className="relative mx-auto w-full max-w-[1080px]" style={{ aspectRatio: `${REGION_MAP_WIDTH} / ${REGION_MAP_HEIGHT}` }}>
            <img
              src={worldMapUrl}
              alt="World map"
              className="h-full w-full select-none object-contain opacity-95 dark:opacity-75 dark:[filter:brightness(0.88)_contrast(1.05)]"
            />
            {rows.map((row) => {
              const position = REGION_MARKER_POSITIONS[row.region];
              if (!position) return null;

              const badgeSize = 44 + Math.min(row.count * 2, 24);
              const fontSize = badgeSize >= 60 ? 16 : 14;

              return (
                <div
                  key={row.region}
                  className="absolute"
                  style={{
                    left: `${(position.x / REGION_MAP_WIDTH) * 100}%`,
                    top: `${(position.y / REGION_MAP_HEIGHT) * 100}%`,
                    transform: 'translate(-50%, -50%)',
                  }}
                  title={`${row.region}: ${row.count} buckets · ${formatCurrency(row.monthly_cost)} / month · ${formatCompact(row.total_size_gb)} GB`}
                  onClick={() => onOpenRegion?.(row.region)}
                >
                  <div className="flex cursor-pointer flex-col items-center gap-2">
                    <div className="rounded-full bg-white/90 px-2 py-1 text-[10px] font-semibold uppercase tracking-[0.12em] text-gray-600 shadow-sm backdrop-blur dark:bg-gray-950/85 dark:text-gray-200">
                      {row.region}
                    </div>
                    <div
                      className="flex items-center justify-center rounded-full border-4 border-white bg-primary-600 font-bold text-white shadow-[0_12px_28px_rgba(15,23,42,0.24)] dark:border-gray-950 dark:bg-primary-500"
                      style={{ width: badgeSize, height: badgeSize, fontSize }}
                    >
                      {row.count}
                    </div>
                    <div className="rounded-full bg-success-500/90 px-2 py-1 text-[10px] font-semibold uppercase tracking-[0.12em] text-white shadow-sm">
                      {formatCurrency(row.monthly_cost)}
                    </div>
                  </div>
                </div>
              );
            })}
          </div>
        </div>
      </div>
    </Card>
  );
};

const BucketRow: React.FC<{ bucket: S3OverviewBucket; onOpen: (bucket: S3OverviewBucket) => void }> = ({ bucket, onOpen }) => {
  const { profile } = useOptimizationProfile();
  const flags = [
    ['Versioning', bucket.posture.versioning_enabled],
    ['Logging', bucket.posture.logging_enabled],
    ['Inventory', bucket.posture.inventory_enabled],
    ['Replication', bucket.posture.replication_enabled],
    ['Lifecycle', bucket.posture.lifecycle_enabled],
  ] as const;

  return (
    <button
      type="button"
      onClick={() => onOpen(bucket)}
      className="grid w-full grid-cols-[minmax(0,2fr)_120px_120px_130px_150px] gap-4 rounded-2xl border border-gray-200 bg-white/90 px-4 py-4 text-left transition hover:border-warning-300 hover:shadow-sm dark:border-gray-700 dark:bg-gray-900/90"
    >
      <div className="min-w-0">
        <div className="flex items-center gap-3">
          <span className="flex h-10 w-10 items-center justify-center rounded-2xl bg-warning-50 dark:bg-warning-900/40">
            <ResourceTypeIcon resourceType="s3" size={20} />
          </span>
          <div className="min-w-0">
            <div className="truncate text-sm font-semibold text-gray-900 dark:text-white">{bucket.resource_name || bucket.resource_id}</div>
            <div className="mt-1 text-xs text-gray-500 dark:text-gray-400">
              {bucket.region || 'unknown region'} · {titleCase(String(bucket.metadata.environment || 'unknown'))}
            </div>
          </div>
        </div>
        <div className="mt-3 flex flex-wrap gap-2">
          {flags.map(([label, ok]) => (
            <span
              key={label}
              className={`rounded-full px-2.5 py-1 text-[11px] font-semibold uppercase tracking-[0.12em] ${
                ok
                  ? 'bg-success-50 text-success-700 dark:bg-success-900/40 dark:text-success-300'
                  : 'bg-gray-100 text-gray-500 dark:bg-gray-800 dark:text-gray-300'
              }`}
            >
              {label}
            </span>
          ))}
        </div>
      </div>
      <div>
        <div className="text-[11px] font-semibold uppercase tracking-[0.16em] text-gray-500 dark:text-gray-400">Size</div>
        <div className="mt-2 text-lg font-semibold text-gray-900 dark:text-white">{formatCompact(bucket.storage.bucket_size_gb)} GB</div>
      </div>
      <div>
        <div className="text-[11px] font-semibold uppercase tracking-[0.16em] text-gray-500 dark:text-gray-400">Objects</div>
        <div className="mt-2 text-lg font-semibold text-gray-900 dark:text-white">{formatCompact(bucket.storage.object_count)}</div>
      </div>
      <div>
        <div className="text-[11px] font-semibold uppercase tracking-[0.16em] text-gray-500 dark:text-gray-400">Cost</div>
        <div className="mt-2 text-lg font-semibold text-gray-900 dark:text-white">{formatCurrency(bucket.storage.estimated_monthly_cost)}</div>
      </div>
      <div>
        <div className="text-[11px] font-semibold uppercase tracking-[0.16em] text-gray-500 dark:text-gray-400">Savings</div>
        <div className="mt-2 text-lg font-semibold text-success-700 dark:text-success-300">{formatCurrency(adjustOptimizationSavings(bucket.maxops.potential_savings_yearly, profile))}</div>
        <div className="mt-1 text-xs text-gray-500 dark:text-gray-400">{bucket.maxops.title || 'Healthy bucket'}</div>
      </div>
    </button>
  );
};

export const S3OverviewPage: React.FC = () => {
  const navigate = useNavigate();
  const { profile } = useOptimizationProfile();
  const { theme } = useTheme();
  const queryClient = useQueryClient();
  const [search, setSearch] = useState('');
  const [inventorySearch, setInventorySearch] = useState('');
  const [inventorySort, setInventorySort] = useState<S3InventorySortKey>('cost');
  const [selectedTeam, setSelectedTeam] = useState('all');
  const [selectedFinding, setSelectedFinding] = useState('all');
  const [selectedMissingFeatures, setSelectedMissingFeatures] = useState<Array<'versioning' | 'logging' | 'inventory' | 'replication' | 'lifecycle'>>([]);
  const [actionableOnly, setActionableOnly] = useState(false);
  const [coverageGapOnly, setCoverageGapOnly] = useState(false);
  const {
    region: selectedRegion,
    environment: selectedEnvironment,
    tagQuery,
    tagLogic,
    activeSavedFilterId,
    savedFilters,
    setRegion: setSelectedRegion,
    setEnvironment: setSelectedEnvironment,
    setTagQuery,
    setTagLogic,
    applySavedFilter,
  } = useSharedOverviewFilters();

  const { data, isLoading, isError } = useQuery<S3OverviewResponse>('inventory-s3-overview', () => inventoryApi.getS3Overview());
  const { data: s3Checks = [] } = useQuery(['checks', 's3'], () => checksApi.listChecks('s3'), { retry: false });
  const availableTags = useMemo(() => buildSharedTagOptions(data?.buckets || []), [data]);

  const snoozeBuckets = async (buckets: S3OverviewBucket[], snoozedUntil: string, reason: string) => {
    await inventoryApi.snoozeResources({
      resources: buckets.map((bucket) => ({
        resource_id: bucket.resource_id,
        resource_type: 's3',
        resource_name: bucket.resource_name,
        account_id: bucket.account_id,
        region: bucket.region,
      })),
      snoozed_until: snoozedUntil,
      reason,
    });
    await queryClient.invalidateQueries('inventory-s3-overview');
    await queryClient.invalidateQueries('latest-check-results');
  };

  const removeBucketSnoozes = async (buckets: S3OverviewBucket[], reason = '') => {
    await inventoryApi.removeSnoozes({
      resources: buckets.map((bucket) => ({
        resource_id: bucket.resource_id,
        resource_type: 's3',
        resource_name: bucket.resource_name,
        account_id: bucket.account_id,
        region: bucket.region,
      })),
      reason,
    });
    await queryClient.invalidateQueries('inventory-s3-overview');
    await queryClient.invalidateQueries('latest-check-results');
  };

  const filteredBuckets = useMemo(() => {
    if (!data) return [];
    const query = search.trim().toLowerCase();
    return data.buckets.filter((bucket) => {
      if (selectedRegion !== 'all' && (bucket.region || 'unknown').toLowerCase() !== selectedRegion) return false;
      if (selectedEnvironment !== 'all' && String(bucket.metadata.environment || 'unknown').toLowerCase() !== selectedEnvironment) return false;
      if (!matchesSharedTagQuery(bucket.tags, tagQuery, tagLogic)) return false;
      if (selectedTeam !== 'all' && String(bucket.metadata.team || 'unknown').toLowerCase() !== selectedTeam) return false;
      if (selectedFinding !== 'all' && String(bucket.maxops.finding_type || 'healthy').toLowerCase() !== selectedFinding) return false;
      if (selectedMissingFeatures.includes('versioning') && bucket.posture.versioning_enabled) return false;
      if (selectedMissingFeatures.includes('logging') && bucket.posture.logging_enabled) return false;
      if (selectedMissingFeatures.includes('inventory') && bucket.posture.inventory_enabled) return false;
      if (selectedMissingFeatures.includes('replication') && bucket.posture.replication_enabled) return false;
      if (selectedMissingFeatures.includes('lifecycle') && bucket.posture.lifecycle_enabled) return false;
      if (actionableOnly && bucket.maxops.status !== 'actionable') return false;
      if (coverageGapOnly && Object.values(bucket.posture).every(Boolean)) return false;
      if (!query) return true;
      const haystack = [
        bucket.resource_id,
        bucket.resource_name,
        bucket.region,
        bucket.maxops.title,
        bucket.maxops.recommended_action,
        bucket.metadata.environment,
        bucket.metadata.team,
        bucket.metadata.bucket_kind,
        ...Object.entries(bucket.tags || {}).flatMap(([key, value]) => [key, value]),
      ]
        .filter(Boolean)
        .join(' ')
        .toLowerCase();
      return haystack.includes(query);
    });
  }, [actionableOnly, coverageGapOnly, data, search, selectedEnvironment, selectedFinding, selectedMissingFeatures, selectedRegion, selectedTeam, tagLogic, tagQuery]);

  const derived = useMemo(() => {
    const findings: Record<string, number> = {};
    const storageMix: Record<string, number> = {};
    const regions: Record<string, { region: string; count: number; total_size_gb: number; monthly_cost: number; yearly_savings: number }> = {};
    const kinds: Record<string, { kind: string; count: number }> = {};
    let totalSize = 0;
    let totalObjects = 0;
    let totalCost = 0;
    let totalSavings = 0;
    let actionable = 0;
    let versioned = 0;
    let logging = 0;
    let inventory = 0;
    let replication = 0;
    let lifecycle = 0;

    filteredBuckets.forEach((bucket) => {
      totalSize += bucket.storage.bucket_size_gb;
      totalObjects += bucket.storage.object_count;
      totalCost += bucket.storage.estimated_monthly_cost;
      totalSavings += bucket.maxops.potential_savings_yearly;
      if (bucket.maxops.status === 'actionable') actionable += 1;
      if (bucket.posture.versioning_enabled) versioned += 1;
      if (bucket.posture.logging_enabled) logging += 1;
      if (bucket.posture.inventory_enabled) inventory += 1;
      if (bucket.posture.replication_enabled) replication += 1;
      if (bucket.posture.lifecycle_enabled) lifecycle += 1;

      const finding = titleCase(bucket.maxops.finding_type || 'healthy');
      findings[finding] = (findings[finding] || 0) + 1;

      Object.entries(bucket.storage.storage_class_mix || {}).forEach(([name, value]) => {
        storageMix[name] = (storageMix[name] || 0) + Number(value) * bucket.storage.bucket_size_gb;
      });

      const region = bucket.region || 'unknown';
      regions[region] = regions[region] || { region, count: 0, total_size_gb: 0, monthly_cost: 0, yearly_savings: 0 };
      regions[region].count += 1;
      regions[region].total_size_gb += bucket.storage.bucket_size_gb;
      regions[region].monthly_cost += bucket.storage.estimated_monthly_cost;
      regions[region].yearly_savings += bucket.maxops.potential_savings_yearly;

      const kind = String(bucket.metadata.bucket_kind || 'unknown');
      kinds[kind] = kinds[kind] || { kind, count: 0 };
      kinds[kind].count += 1;
    });

    return {
      totalBuckets: filteredBuckets.length,
      actionable,
      healthy: Math.max(filteredBuckets.length - actionable, 0),
      totalSize,
      totalObjects,
      totalCost,
      totalSavings,
      versioned,
      logging,
      inventory,
      replication,
      lifecycle,
      findings: Object.entries(findings).map(([name, value]) => ({ name, value })).sort((a, b) => b.value - a.value),
      storageMix: Object.entries(storageMix).map(([name, value]) => ({ name, value: Number(value.toFixed(2)) })).sort((a, b) => b.value - a.value),
      regionRows: Object.values(regions).sort((a, b) => b.monthly_cost - a.monthly_cost),
      kindRows: Object.values(kinds).sort((a, b) => b.count - a.count),
      costliest: [...filteredBuckets].sort((a, b) => b.storage.estimated_monthly_cost - a.storage.estimated_monthly_cost).slice(0, 8),
      scatterRows: filteredBuckets.map((bucket) => ({
        x: Number(bucket.storage.bucket_size_gb.toFixed(2)),
        y: Number(bucket.maxops.potential_savings_yearly.toFixed(2)),
        z: Math.max(Math.round(bucket.storage.object_count / 10000), 6),
      })),
      orderedBuckets: [...filteredBuckets].sort((left, right) => {
        const leftOrder = getBucketCoverageOrder(left);
        const rightOrder = getBucketCoverageOrder(right);
        if (leftOrder !== rightOrder) {
          return leftOrder - rightOrder;
        }

        const actionableDelta =
          Number(right.maxops.status === 'actionable') - Number(left.maxops.status === 'actionable');
        if (actionableDelta !== 0) {
          return actionableDelta;
        }

        const savingsDelta = right.maxops.potential_savings_yearly - left.maxops.potential_savings_yearly;
        if (savingsDelta !== 0) {
          return savingsDelta;
        }

        return (right.storage.bucket_size_gb || 0) - (left.storage.bucket_size_gb || 0);
      }),
    };
  }, [filteredBuckets]);

  const inventoryBuckets = useMemo(() => {
    const query = inventorySearch.trim().toLowerCase();
    const matches = !query
      ? filteredBuckets
      : filteredBuckets.filter((bucket) =>
          [
            bucket.resource_id,
            bucket.resource_name,
            bucket.region,
            bucket.metadata.environment,
            bucket.metadata.team,
            bucket.metadata.bucket_kind,
            bucket.maxops.finding_type,
            bucket.maxops.title,
            ...Object.entries(bucket.tags || {}).flatMap(([key, value]) => [key, `${key}:${value}`, String(value)]),
          ]
            .filter(Boolean)
            .some((value) => String(value).toLowerCase().includes(query))
        );

    return [...matches].sort((left, right) => {
      switch (inventorySort) {
        case 'name':
          return String(left.resource_name || left.resource_id).localeCompare(String(right.resource_name || right.resource_id));
        case 'savings':
          return right.maxops.potential_savings_yearly - left.maxops.potential_savings_yearly;
        case 'size':
          return right.storage.bucket_size_gb - left.storage.bucket_size_gb;
        case 'objects':
          return right.storage.object_count - left.storage.object_count;
        case 'region':
          return String(left.region || 'unknown').localeCompare(String(right.region || 'unknown'));
        case 'cost':
        default:
          return right.storage.estimated_monthly_cost - left.storage.estimated_monthly_cost;
      }
    });
  }, [filteredBuckets, inventorySearch, inventorySort]);

  const getBucketActions = (bucket: S3OverviewBucket) => {
    const check = s3Checks.find((entry) => entry.check_id === bucket.maxops.check_id);
    const mappedActions = check ? getActionsForCheck(check) : [];
    const recommendedAction = bucket.maxops.recommended_action?.trim();
    if (!recommendedAction) {
      return mappedActions;
    }
    return mappedActions.includes(recommendedAction) ? mappedActions : [recommendedAction, ...mappedActions];
  };

  const executeBucketAction = async (bucket: S3OverviewBucket, action: string) => {
    if (!bucket.maxops.check_id || !bucket.account_id || !bucket.region || !action) {
      return;
    }

    await checksApi.executeAction(bucket.maxops.check_id, {
      action,
      account_id: bucket.account_id,
      region: bucket.region,
      resource_id: bucket.resource_id,
    });
  };

  if (isLoading) {
    return (
      <Layout>
        <div className="flex min-h-[60vh] items-center justify-center rounded-[32px] border border-gray-200 bg-white/90 dark:border-gray-700 dark:bg-gray-900/90">
          <div className="text-center">
            <div className="text-lg font-semibold text-gray-800 dark:text-gray-100">Loading S3 overview</div>
            <div className="mt-2 text-sm text-gray-500 dark:text-gray-400">Preparing imported bucket posture and savings views.</div>
          </div>
        </div>
      </Layout>
    );
  }

  if (isError || !data) {
    return (
      <Layout>
        <Card className="border-danger-200 bg-danger-50 dark:border-danger-900/50 dark:bg-danger-900/30">
          <div className="flex items-start gap-3">
            <AlertCircle className="mt-0.5 text-danger-600 dark:text-danger-300" size={18} />
            <div>
              <div className="text-lg font-semibold text-danger-900 dark:text-danger-100">S3 overview unavailable</div>
              <div className="mt-2 text-sm text-danger-700 dark:text-danger-200">The imported S3 inventory could not be loaded.</div>
            </div>
          </div>
        </Card>
      </Layout>
    );
  }

  const isLatestCheckFallback = data.source === 'latest_check_results';

  const exportInventoryCsv = () => {
    downloadCsv(
      's3-bucket-inventory.csv',
      inventoryBuckets.map((bucket) => ({
        resource_id: bucket.resource_id,
        resource_name: bucket.resource_name || '',
        region: bucket.region || '',
        state: bucket.state || '',
        bucket_size_gb: bucket.storage.bucket_size_gb,
        object_count: bucket.storage.object_count,
        estimated_monthly_cost: bucket.storage.estimated_monthly_cost,
        finding: bucket.maxops.finding_type || '',
        status: bucket.maxops.status || '',
        recommended_action: bucket.maxops.recommended_action || '',
        potential_savings_yearly: bucket.maxops.potential_savings_yearly,
        tags: bucket.tags || {},
        metadata: bucket.metadata || {},
      }))
    );
  };

  const activeScopeChips = [
    ...(search.trim()
      ? [
          {
            key: `search:${search.trim()}`,
            label: `Search: ${search.trim()}`,
            onRemove: () => setSearch(''),
          },
        ]
      : []),
    ...(selectedRegion !== 'all'
      ? [
          {
            key: `region:${selectedRegion}`,
            label: `Region: ${selectedRegion}`,
            onRemove: () => setSelectedRegion('all'),
          },
        ]
      : []),
    ...(selectedEnvironment !== 'all'
      ? [
          {
            key: `environment:${selectedEnvironment}`,
            label: `Env: ${selectedEnvironment}`,
            onRemove: () => setSelectedEnvironment('all'),
          },
        ]
      : []),
    ...(tagQuery.trim()
      ? [
          {
            key: 'tag-logic',
            label: tagLogic === 'or' ? 'Tags: Match any' : 'Tags: Match all',
            onRemove: () => setTagLogic('and'),
          },
          ...parseSharedTagQuery(tagQuery).map((value) => ({
            key: `tags:${value}`,
            label: `Tag: ${formatSharedTagSelectionLabel(value)}`,
            onRemove: () => setTagQuery(parseSharedTagQuery(tagQuery).filter((item) => item !== value).join(', ')),
          })),
        ]
      : []),
    ...(selectedTeam !== 'all'
      ? [
          {
            key: `team:${selectedTeam}`,
            label: `Team: ${selectedTeam}`,
            onRemove: () => setSelectedTeam('all'),
          },
        ]
      : []),
    ...(selectedFinding !== 'all'
      ? [
          {
            key: `finding:${selectedFinding}`,
            label: `Finding: ${selectedFinding}`,
            onRemove: () => setSelectedFinding('all'),
          },
        ]
      : []),
    ...selectedMissingFeatures.map((value) => ({
      key: `missing-feature:${value}`,
      label: `Missing: ${value}`,
      onRemove: () => setSelectedMissingFeatures((current) => current.filter((item) => item !== value)),
    })),
    ...(actionableOnly
      ? [
          {
            key: 'actionable',
            label: 'Actionable',
            onRemove: () => setActionableOnly(false),
          },
        ]
      : []),
    ...(coverageGapOnly
      ? [
          {
            key: 'coverage-gap',
            label: 'Coverage gaps',
            onRemove: () => setCoverageGapOnly(false),
          },
        ]
      : []),
  ];
  const showResetButton =
    Boolean(activeSavedFilterId) ||
    Boolean(search.trim()) ||
    selectedRegion !== 'all' ||
    selectedEnvironment !== 'all' ||
    Boolean(tagQuery.trim()) ||
    tagLogic !== 'and' ||
    selectedTeam !== 'all' ||
    selectedFinding !== 'all' ||
    selectedMissingFeatures.length > 0 ||
    actionableOnly ||
    coverageGapOnly;

  return (
    <Layout>
      <div className="space-y-8">
        <section className="overflow-hidden rounded-[32px] border border-gray-200 bg-[radial-gradient(circle_at_top_left,_rgba(52,224,196,0.24),_transparent_30%),radial-gradient(circle_at_bottom_right,_rgba(245,166,35,0.18),_transparent_26%),linear-gradient(135deg,_#f5fdfb_0%,_#ecfdf9_44%,_#fffbeb_100%)] p-8 shadow-sm dark:border-gray-700 dark:bg-[radial-gradient(circle_at_top_left,_rgba(22,193,168,0.18),_transparent_24%),radial-gradient(circle_at_bottom_right,_rgba(245,166,35,0.16),_transparent_20%),linear-gradient(135deg,_rgba(14,24,48,0.98)_0%,_rgba(24,33,64,0.96)_46%,_rgba(14,24,48,0.98)_100%)]">
          <div className="flex flex-col gap-6">
            <div>
              <button
                type="button"
                onClick={() => navigate('/dashboard')}
                className="inline-flex items-center gap-2 rounded-full border border-gray-200 bg-white/80 px-4 py-2 text-sm font-medium text-gray-700 transition hover:bg-white dark:border-gray-700 dark:bg-gray-900/80 dark:text-gray-200 dark:hover:bg-gray-900"
              >
                <ArrowLeft size={16} />
                Dashboard
              </button>
            </div>
            <div className="flex flex-col gap-6 xl:flex-row xl:items-end xl:justify-between">
              <div>
                <div className="inline-flex items-center gap-2 rounded-full bg-white/80 px-3 py-1.5 text-xs font-semibold uppercase tracking-[0.16em] text-gray-600 dark:bg-gray-900/70 dark:text-gray-300">
                  <ResourceTypeIcon resourceType="s3" size={16} />
                  S3 Infrastructure Overview
                </div>
                <h1 className="mt-4 text-4xl font-semibold tracking-tight text-gray-950 dark:text-gray-50">Storage posture, cost, and optimization signals</h1>
                <p className="mt-3 max-w-3xl text-sm leading-6 text-gray-600 dark:text-gray-300">
                  {isLatestCheckFallback
                    ? 'Review bucket findings from the latest S3 scans, including potential savings and available resource context.'
                    : 'Review imported bucket configuration, baseline controls, storage footprint, and potential savings across the current S3 estate.'}
                </p>
                {activeScopeChips.length > 0 ? (
                  <div className="mt-4 flex flex-wrap gap-2">
                    {activeScopeChips.map((chip) => (
                      <button
                        key={chip.key}
                        type="button"
                        onClick={chip.onRemove}
                        className="inline-flex items-center gap-1.5 rounded-full border border-gray-200 bg-white/80 px-3 py-1 text-xs font-semibold uppercase tracking-[0.12em] text-gray-600 transition hover:border-warning-300 hover:bg-white hover:text-gray-900 dark:border-gray-700 dark:bg-gray-900/80 dark:text-gray-200 dark:hover:border-warning-500 dark:hover:text-white"
                      >
                        <span>{chip.label}</span>
                        <X size={12} />
                      </button>
                    ))}
                  </div>
                ) : null}
                {showResetButton ? (
                  <div className="mt-4">
                    <button
                      type="button"
                      onClick={() => {
                        setSearch('');
                        setSelectedRegion('all');
                        setSelectedEnvironment('all');
                        setTagQuery('');
                        setSelectedTeam('all');
                        setSelectedFinding('all');
                        setSelectedMissingFeatures([]);
                        setActionableOnly(false);
                        setCoverageGapOnly(false);
                      }}
                      className="inline-flex items-center rounded-full border border-gray-200 bg-white/85 px-4 py-2 text-sm font-medium text-gray-700 transition hover:bg-white dark:border-gray-700 dark:bg-gray-900/80 dark:text-gray-200 dark:hover:bg-gray-900"
                    >
                      Reset filters
                    </button>
                  </div>
                ) : null}
              </div>
              <div className="rounded-[28px] border border-white/70 bg-white/70 px-5 py-4 shadow-sm backdrop-blur dark:border-gray-700 dark:bg-gray-900/70">
                <div className="text-xs font-semibold uppercase tracking-[0.18em] text-gray-500 dark:text-gray-400">
                  {isLatestCheckFallback ? 'Latest scan results' : 'Imported snapshot'}
                </div>
                <div className="mt-2 text-sm font-semibold text-gray-900 dark:text-white">{formatDateTime(data.generated_at)}</div>
                <div className="mt-1 text-sm text-gray-500 dark:text-gray-400">{data.account_id || 'Unknown account'}</div>
              </div>
            </div>
          </div>
        </section>

        <ResourcePageFilters
          searchValue={search}
          searchPlaceholder="Search bucket name, region, finding, team, or tag"
          onSearchChange={setSearch}
          savedFilterSelect={{
            id: 's3-saved-filter',
            label: 'Saved filter',
            value: activeSavedFilterId || '',
            options: [
              { label: 'Custom filters', value: '' },
              ...savedFilters.map((filter) => ({ label: filter.name, value: filter.id })),
            ],
            onChange: applySavedFilter,
          }}
          selects={[
            { id: 'region', label: 'Region', value: selectedRegion, options: [{ label: 'All regions', value: 'all' }, ...data.dimensions.regions.map((row) => ({ label: `${row.key} (${row.count})`, value: row.key }))], onChange: setSelectedRegion },
            { id: 'environment', label: 'Environment', value: selectedEnvironment, options: [{ label: 'All environments', value: 'all' }, ...data.dimensions.environments.map((row) => ({ label: `${titleCase(row.key)} (${row.count})`, value: row.key }))], onChange: setSelectedEnvironment },
            { id: 'team', label: 'Team', value: selectedTeam, options: [{ label: 'All teams', value: 'all' }, ...data.dimensions.teams.map((row) => ({ label: `${titleCase(row.key)} (${row.count})`, value: row.key }))], onChange: setSelectedTeam },
            { id: 'finding', label: 'Finding', value: selectedFinding, options: [{ label: 'All findings', value: 'all' }, ...data.findings_breakdown.map((row) => ({ label: `${titleCase(row.key)} (${row.count})`, value: row.key }))], onChange: setSelectedFinding },
          ]}
          children={
            <SharedTagFilterField
              selectedTags={parseSharedTagQuery(tagQuery)}
              availableTags={availableTags}
              tagLogic={tagLogic}
              onChange={(tags) => setTagQuery(tags.join(', '))}
              onTagLogicChange={setTagLogic}
            />
          }
          toggles={[
            { id: 'actionable', label: 'Actionable only', checked: actionableOnly, onChange: setActionableOnly },
            { id: 'gaps', label: 'Coverage gaps only', checked: coverageGapOnly, onChange: setCoverageGapOnly },
          ]}
          onReset={() => {
            setSearch('');
            setSelectedRegion('all');
            setSelectedEnvironment('all');
            setTagQuery('');
            setSelectedTeam('all');
            setSelectedFinding('all');
            setSelectedMissingFeatures([]);
            setActionableOnly(false);
            setCoverageGapOnly(false);
          }}
          resetDisabled={!showResetButton}
        />

        <div className="grid gap-4 md:grid-cols-2 xl:grid-cols-4">
          <SummaryTile label="Buckets" value={formatLargeNumber(derived.totalBuckets)} sublabel={`${adjustOptimizationCount(derived.actionable, profile)} actionable · ${Math.max(derived.totalBuckets - adjustOptimizationCount(derived.actionable, profile), 0)} healthy`} icon={<Database size={22} />} />
          <SummaryTile label="Storage Footprint" value={`${formatCompact(derived.totalSize)} GB`} sublabel={`${formatLargeNumber(derived.totalObjects)} objects tracked`} icon={<HardDrive size={22} />} />
          <SummaryTile label="Monthly Cost" value={formatCurrency(derived.totalCost)} sublabel="Estimated storage cost from imported data" icon={<DollarSign size={22} />} />
          <SummaryTile label="Yearly Savings" value={formatCurrency(adjustOptimizationSavings(derived.totalSavings, profile))} sublabel="Potential annual optimization upside" icon={<Sparkles size={22} />} />
        </div>

        <div className="grid gap-4 md:grid-cols-2 xl:grid-cols-5">
          <PostureCard
            label="Versioning"
            count={derived.versioned}
            total={derived.totalBuckets}
            active={selectedMissingFeatures.includes('versioning')}
            onClick={() =>
              setSelectedMissingFeatures((current) =>
                current.includes('versioning')
                  ? current.filter((item) => item !== 'versioning')
                  : [...current, 'versioning']
              )
            }
          />
          <PostureCard
            label="Access Logging"
            count={derived.logging}
            total={derived.totalBuckets}
            active={selectedMissingFeatures.includes('logging')}
            onClick={() =>
              setSelectedMissingFeatures((current) =>
                current.includes('logging')
                  ? current.filter((item) => item !== 'logging')
                  : [...current, 'logging']
              )
            }
          />
          <PostureCard
            label="Inventory Reports"
            count={derived.inventory}
            total={derived.totalBuckets}
            active={selectedMissingFeatures.includes('inventory')}
            onClick={() =>
              setSelectedMissingFeatures((current) =>
                current.includes('inventory')
                  ? current.filter((item) => item !== 'inventory')
                  : [...current, 'inventory']
              )
            }
          />
          <PostureCard
            label="Replication"
            count={derived.replication}
            total={derived.totalBuckets}
            active={selectedMissingFeatures.includes('replication')}
            onClick={() =>
              setSelectedMissingFeatures((current) =>
                current.includes('replication')
                  ? current.filter((item) => item !== 'replication')
                  : [...current, 'replication']
              )
            }
          />
          <PostureCard
            label="Lifecycle Rules"
            count={derived.lifecycle}
            total={derived.totalBuckets}
            active={selectedMissingFeatures.includes('lifecycle')}
            onClick={() =>
              setSelectedMissingFeatures((current) =>
                current.includes('lifecycle')
                  ? current.filter((item) => item !== 'lifecycle')
                  : [...current, 'lifecycle']
              )
            }
          />
        </div>

        <section className="grid gap-6">
          <ResourceActionPanel
            title="Bucket Inventory"
            subtitle={`${inventoryBuckets.length} visible`}
            resourceLabel="S3 bucket"
            items={inventoryBuckets}
            getId={(bucket) => bucket.resource_id}
            getName={(bucket) => bucket.resource_name || bucket.resource_id}
            getActions={getBucketActions}
            canExecute={(bucket, action) => Boolean(bucket.maxops.check_id && bucket.account_id && bucket.region && action)}
            executeAction={executeBucketAction}
            formatActionLabel={titleCase}
            getSnooze={(bucket) => bucket.snooze}
            onSnooze={snoozeBuckets}
            onRemoveSnooze={removeBucketSnoozes}
            toolbar={
              <>
                <div className="relative min-w-[240px]">
                  <Search size={14} className="pointer-events-none absolute left-3 top-1/2 -translate-y-1/2 text-gray-400" />
                  <input
                    type="text"
                    value={inventorySearch}
                    onChange={(event) => setInventorySearch(event.target.value)}
                    placeholder="Search visible buckets"
                    className="w-full rounded-xl border border-gray-200 bg-white py-2 pl-9 pr-3 text-sm text-gray-700 outline-none transition focus:border-warning-300 focus:ring-2 focus:ring-warning-100 dark:border-gray-700 dark:bg-gray-900/80 dark:text-gray-100 dark:focus:border-warning-500 dark:focus:ring-warning-900/40"
                  />
                </div>
                <select
                  value={inventorySort}
                  onChange={(event) => setInventorySort(event.target.value as S3InventorySortKey)}
                  className="rounded-xl border border-gray-200 bg-white px-3 py-2 text-sm text-gray-700 outline-none transition focus:border-warning-300 focus:ring-2 focus:ring-warning-100 dark:border-gray-700 dark:bg-gray-900/80 dark:text-gray-100 dark:focus:border-warning-500 dark:focus:ring-warning-900/40"
                >
                  <option value="cost">Sort by monthly cost</option>
                  <option value="savings">Sort by yearly savings</option>
                  <option value="size">Sort by size</option>
                  <option value="objects">Sort by object count</option>
                  <option value="name">Sort by name</option>
                  <option value="region">Sort by region</option>
                </select>
                <button
                  type="button"
                  onClick={exportInventoryCsv}
                  disabled={inventoryBuckets.length === 0}
                  className="inline-flex items-center gap-2 rounded-xl border border-gray-200 bg-white px-3 py-2 text-sm font-medium text-gray-700 transition hover:border-warning-300 hover:text-warning-700 disabled:cursor-not-allowed disabled:opacity-50 dark:border-gray-700 dark:bg-gray-900/80 dark:text-gray-100 dark:hover:border-warning-500 dark:hover:text-warning-300"
                >
                  <Download size={14} />
                  Export CSV
                </button>
              </>
            }
            confirmColumns={[
              {
                header: 'Bucket',
                render: (bucket) => (
                  <div>
                    <div className="font-semibold text-gray-900 dark:text-white">{bucket.resource_name || bucket.resource_id}</div>
                    <div className="mt-1 text-xs text-gray-500 dark:text-gray-400">{bucket.resource_id}</div>
                  </div>
                ),
              },
              { header: 'Region', render: (bucket) => bucket.region || 'unknown' },
              { header: 'Size', render: (bucket) => `${bucket.storage.bucket_size_gb.toFixed(1)} GB` },
              { header: 'Objects', render: (bucket) => formatLargeNumber(bucket.storage.object_count) },
              { header: 'Finding', render: (bucket) => titleCase(bucket.maxops.finding_type || 'healthy') },
              {
                header: 'Yearly Savings',
                render: (bucket) => (
                  <span className="font-semibold text-success-700 dark:text-success-300">
                    {formatCurrency(adjustOptimizationSavings(bucket.maxops.potential_savings_yearly, profile))}
                  </span>
                ),
              },
            ]}
            renderItem={(bucket) => (
                <BucketRow
                  key={bucket.resource_id}
                  bucket={bucket}
                  onOpen={(current) => navigate(`/dashboard/resources/s3/buckets/${encodeURIComponent(current.resource_id)}`)}
                />
              )}
          />

          <BucketCloudPanel
            buckets={derived.orderedBuckets}
            onOpenBucket={(bucket) => navigate(`/dashboard/resources/s3/buckets/${encodeURIComponent(bucket.resource_id)}`)}
          />
        </section>

        <RegionsPanel
          rows={derived.regionRows}
          onOpenRegion={(region) => setSelectedRegion(region.toLowerCase())}
        />

        <div className="grid gap-6 xl:grid-cols-2">
          <ChartCard title="Savings by Region" subtitle="Monthly cost against yearly savings opportunity by AWS region">
            <div className="h-80">
              <ResponsiveContainer width="100%" height="100%">
                <BarChart data={derived.regionRows.slice(0, 8)} margin={{ top: 8, right: 12, left: 0, bottom: 0 }}>
                  <CartesianGrid strokeDasharray="3 3" stroke={chartGridColor(theme)} opacity={0.35} />
                  <XAxis dataKey="region" stroke={chartTickColor(theme)} tickLine={false} axisLine={false} />
                  <YAxis stroke={chartTickColor(theme)} tickLine={false} axisLine={false} />
                  <Tooltip formatter={(value: unknown) => formatCurrency(Number(value || 0))} />
                  <Bar dataKey="monthly_cost" name="Monthly cost" fill={CHART_COLORS[0]} radius={[8, 8, 0, 0]} />
                  <Bar dataKey="yearly_savings" name="Yearly savings" fill={CHART_COLORS[1]} radius={[8, 8, 0, 0]} />
                </BarChart>
              </ResponsiveContainer>
            </div>
          </ChartCard>

          <ChartCard title="Finding Distribution" subtitle="Which S3 findings dominate the currently visible estate">
            <div className="grid gap-4 lg:grid-cols-[280px_minmax(0,1fr)]">
              <div className="h-72">
                <ResponsiveContainer width="100%" height="100%">
                  <PieChart>
                    <Pie data={derived.findings} innerRadius={64} outerRadius={100} paddingAngle={2} dataKey="value" stroke="none">
                      {derived.findings.map((entry, index) => (
                        <Cell key={entry.name} fill={CHART_COLORS[index % CHART_COLORS.length]} />
                      ))}
                    </Pie>
                    <Tooltip />
                  </PieChart>
                </ResponsiveContainer>
              </div>
              <div className="space-y-3">
                {derived.findings.map((entry, index) => (
                  <div key={entry.name} className="flex items-center justify-between rounded-2xl border border-gray-200 bg-gray-50 px-4 py-3 dark:border-gray-700 dark:bg-gray-800/70">
                    <div className="flex items-center gap-3">
                      <span className="inline-block h-3 w-3 rounded-full" style={{ backgroundColor: CHART_COLORS[index % CHART_COLORS.length] }} />
                      <span className="text-sm font-medium text-gray-700 dark:text-gray-200">{entry.name}</span>
                    </div>
                    <span className="text-sm font-semibold text-gray-900 dark:text-white">{entry.value}</span>
                  </div>
                ))}
              </div>
            </div>
          </ChartCard>
        </div>

        <div className="grid gap-6 xl:grid-cols-3">
          <ChartCard title="Storage Class Footprint" subtitle="Weighted by bucket size across the filtered inventory">
            <div className="h-80">
              <ResponsiveContainer width="100%" height="100%">
                <PieChart>
                  <Pie data={derived.storageMix} innerRadius={62} outerRadius={106} dataKey="value" nameKey="name" stroke="none">
                    {derived.storageMix.map((entry, index) => (
                      <Cell key={entry.name} fill={CHART_COLORS[index % CHART_COLORS.length]} />
                    ))}
                  </Pie>
                  <Tooltip formatter={(value: unknown) => `${Number(value || 0).toFixed(1)} GB`} />
                </PieChart>
              </ResponsiveContainer>
            </div>
          </ChartCard>

          <ChartCard title="Bucket Kind Mix" subtitle="Operational shape of the imported S3 estate">
            <div className="h-80">
              <ResponsiveContainer width="100%" height="100%">
                <BarChart data={derived.kindRows.slice(0, 8)} layout="vertical" margin={{ top: 8, right: 12, left: 16, bottom: 0 }}>
                  <CartesianGrid strokeDasharray="3 3" stroke={chartGridColor(theme)} opacity={0.3} />
                  <XAxis type="number" stroke={chartTickColor(theme)} tickLine={false} axisLine={false} />
                  <YAxis type="category" dataKey="kind" stroke={chartTickColor(theme)} tickLine={false} axisLine={false} width={92} />
                  <Tooltip />
                  <Bar dataKey="count" fill={CHART_COLORS[0]} radius={[0, 8, 8, 0]} />
                </BarChart>
              </ResponsiveContainer>
            </div>
          </ChartCard>

          <ChartCard title="Size vs Savings" subtitle="Buckets in the upper-right combine heavy footprint with high upside">
            <div className="h-80">
              <ResponsiveContainer width="100%" height="100%">
                <ScatterChart margin={{ top: 8, right: 12, left: 0, bottom: 0 }}>
                  <CartesianGrid strokeDasharray="3 3" stroke={chartGridColor(theme)} opacity={0.35} />
                  <XAxis type="number" dataKey="x" name="Size" unit=" GB" stroke={chartTickColor(theme)} tickLine={false} axisLine={false} />
                  <YAxis type="number" dataKey="y" name="Savings" stroke={chartTickColor(theme)} tickLine={false} axisLine={false} />
                  <Tooltip formatter={(value: unknown, name?: string) => (name === 'Savings' ? formatCurrency(Number(value || 0)) : `${Number(value || 0).toFixed(1)} GB`)} />
                  <Scatter data={derived.scatterRows} fill={CHART_COLORS[1]} />
                </ScatterChart>
              </ResponsiveContainer>
            </div>
          </ChartCard>
        </div>

      </div>
    </Layout>
  );
};
