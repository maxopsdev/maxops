import React, { useEffect, useMemo, useState } from 'react';
import { useNavigate, useSearchParams } from 'react-router-dom';
import { useQuery, useQueryClient } from 'react-query';
import {
  Bar,
  BarChart,
  CartesianGrid,
  Cell,
  Legend,
  Pie,
  PieChart,
  ResponsiveContainer,
  Scatter,
  ScatterChart,
  Tooltip,
  XAxis,
  YAxis,
} from 'recharts';
import { AlertCircle, ArrowLeft, ChevronDown, Cpu, Download, HardDrive, Network, Play, Search, Server, ShieldAlert, Wallet, X } from 'lucide-react';
import { Layout } from '@/components/layout/Layout';
import { Card } from '@/components/common/Card';
import { ResourceActionPanel } from '@/components/common/ResourceActionPanel';
import { getActionsForCheck } from '@/components/checks/checkActions';
import { ResourcePageFilters } from '@/components/common/ResourcePageFilters';
import { SharedTagFilterField } from '@/components/common/SharedTagFilterField';
import { useOptimizationProfile } from '@/contexts/OptimizationProfileContext';
import worldMapUrl from '@/assets/maps/world-map.svg';
import { inventoryApi, type Ec2OverviewInstance, type Ec2OverviewResponse } from '@/services/inventory';
import { checksApi } from '@/services/checks';
import {
  applyEc2CollectionFilters,
  buildEc2CollectionSearch,
  EMPTY_EC2_COLLECTION_FILTERS,
  getEc2CollectionTitle,
  mergeEc2CollectionFilters,
  parseEc2CollectionFilters,
  type Ec2CollectionFilters,
} from '@/features/ec2/drilldown';
import { useSharedOverviewFilters } from '@/stores/sharedOverviewFilters';
import { downloadCsv } from '@/utils/csv';
import { parseSharedTagQuery } from '@/utils/sharedOverviewFilters';
import { adjustOptimizationCount, adjustOptimizationSavings } from '@/utils/optimizationProfile';
import { useTheme } from '@/contexts/ThemeContext';
import { CHART_COLORS, STATUS_COLORS, chartGridColor, chartTickColor } from '@/styles/chartColors';

const STATE_DISTRIBUTION_COLORS = {
  Running: STATUS_COLORS.running,
  Stopped: STATUS_COLORS.stopped,
} as const;

const titleCase = (value: string) =>
  value
    .split(/[_\s-]+/)
    .filter(Boolean)
    .map((part) => part.charAt(0).toUpperCase() + part.slice(1))
    .join(' ');

const formatPercent = (value: number) => `${value.toFixed(1)}%`;
const formatCurrency = (value: number) =>
  new Intl.NumberFormat('en-US', { style: 'currency', currency: 'USD', maximumFractionDigits: 0 }).format(value);
const formatDateTime = (value: string | null) =>
  value
    ? new Date(value).toLocaleString('en-US', {
        year: 'numeric',
        month: 'short',
        day: 'numeric',
        hour: 'numeric',
        minute: '2-digit',
      })
    : 'Unavailable';
const isActionErrorMessage = (value: string) => /failed|error|missing/i.test(value);
type Ec2InventorySortKey = 'name' | 'region' | 'savings' | 'cpu' | 'memory' | 'monthlyCost';

const GaugeCard: React.FC<{
  label: string;
  value: number;
  max?: number;
  unit?: string;
  tone?: string;
  onClick?: () => void;
}> = ({ label, value, max = 100, unit = '%', tone = STATUS_COLORS.healthy, onClick }) => {
  const { theme } = useTheme();
  const normalized = Math.min(Math.max(value / max, 0), 1);
  const circumference = 2 * Math.PI * 54;
  const dashOffset = circumference * (1 - normalized);

  return (
    <Card className={`relative border-gray-200 bg-white/95 dark:border-gray-700 dark:bg-gray-900/95 ${onClick ? 'cursor-pointer transition hover:border-warning-300 hover:shadow-sm' : ''}`}>
      {onClick ? <button type="button" aria-label={`Open ${label}`} onClick={onClick} className="absolute inset-0" /> : null}
      <div className="text-sm font-medium text-gray-500 dark:text-gray-400">{label}</div>
      <div className="mt-5 flex items-center justify-center">
        <svg viewBox="0 0 160 110" className="h-36 w-full max-w-[220px] overflow-visible">
          <path d="M 20 90 A 60 60 0 0 1 140 90" fill="none" stroke={chartGridColor(theme)} strokeWidth="14" strokeLinecap="round" />
          <path
            d="M 20 90 A 60 60 0 0 1 140 90"
            fill="none"
            stroke={tone}
            strokeWidth="14"
            strokeLinecap="round"
            strokeDasharray={circumference}
            strokeDashoffset={dashOffset}
          />
          <text x="80" y="72" textAnchor="middle" className="fill-gray-800 text-[28px] font-semibold dark:fill-gray-100">
            {value.toFixed(1)}
          </text>
          <text x="80" y="92" textAnchor="middle" className="fill-gray-500 text-[11px] font-medium uppercase tracking-[0.18em] dark:fill-gray-400">
            {unit}
          </text>
        </svg>
      </div>
    </Card>
  );
};

const HexCloud: React.FC<{
  title: string;
  instances: Ec2OverviewInstance[];
  tone: 'warm' | 'underutilized' | 'memory-levels';
  metric: 'cpu' | 'memory';
  onOpenCollection?: () => void;
  onOpenInstance?: (instance: Ec2OverviewInstance) => void;
}> = ({ title, instances, tone, metric, onOpenCollection, onOpenInstance }) => {
  const palette =
    tone === 'memory-levels' || tone === 'underutilized'
        ? ['bg-warning-300', 'bg-warning-400', 'bg-danger-500']
        : ['bg-warning-200', 'bg-warning-400', 'bg-warning-600', 'bg-warning-800'];

  const rankedBuckets = useMemo(() => {
    if (tone !== 'underutilized' && tone !== 'memory-levels') {
      return new Map<string, number>();
    }

    const sorted = [...instances].sort((left, right) => {
      const leftValue = metric === 'cpu' ? left.usage.cpu_utilization : left.usage.memory_utilization;
      const rightValue = metric === 'cpu' ? right.usage.cpu_utilization : right.usage.memory_utilization;
      return leftValue - rightValue;
    });

    const total = sorted.length;
    const buckets = new Map<string, number>();
    sorted.forEach((instance, index) => {
      const percentile = total <= 1 ? 0 : index / total;
      const bucket = percentile < 1 / 3 ? 0 : percentile < 2 / 3 ? 1 : 2;
      buckets.set(instance.resource_id, bucket);
    });
    return buckets;
  }, [instances, metric, tone]);

  return (
    <Card className="border-gray-200 bg-white/95">
      <div className="mb-4 flex items-center justify-between">
        <div>
          <div className="text-sm font-semibold text-gray-700">{title}</div>
          <div className="text-xs text-gray-500">{instances.length} instances</div>
        </div>
        <div className="flex items-center gap-2">
          <div className="rounded-full bg-gray-100 px-3 py-1 text-[11px] font-semibold uppercase tracking-[0.16em] text-gray-500">
            {metric}
          </div>
          {onOpenCollection ? (
            <button
              type="button"
              onClick={onOpenCollection}
              className="rounded-full bg-warning-100 px-3 py-1 text-[11px] font-semibold uppercase tracking-[0.16em] text-warning-800 transition hover:bg-warning-200"
            >
              Open list
            </button>
          ) : null}
        </div>
      </div>
      <div className="flex flex-wrap gap-0 py-2">
        {instances.slice(0, 60).map((instance, index) => {
          const value = metric === 'cpu' ? instance.usage.cpu_utilization : instance.usage.memory_utilization;
          const tooltipLabel = instance.resource_name || instance.resource_id;
          const bucket =
            tone === 'memory-levels' || tone === 'underutilized'
              ? rankedBuckets.get(instance.resource_id) ?? 0
              : Math.min(Math.floor(value / 25), palette.length - 1);
          return (
            <div
              key={instance.resource_id}
              className={`h-7 w-8 ${palette[bucket]} shadow-sm`}
              style={{
                clipPath: 'polygon(25% 6%, 75% 6%, 100% 50%, 75% 94%, 25% 94%, 0% 50%)',
                marginTop: index % 2 === 0 ? 0 : 10,
                marginLeft: index === 0 ? 0 : -3,
                cursor: onOpenInstance ? 'pointer' : 'default',
              }}
              title={tooltipLabel}
              aria-label={tooltipLabel}
              onClick={() => onOpenInstance?.(instance)}
            />
          );
        })}
      </div>
    </Card>
  );
};

export const Ec2OverviewPage: React.FC = () => <Ec2InventoryExplorer mode="overview" />;

const SummaryPill: React.FC<{ label: string; value: string; icon: React.ReactNode; onClick?: () => void }> = ({ label, value, icon, onClick }) => (
  <button
    type="button"
    onClick={onClick}
    className={`rounded-2xl border border-gray-200 bg-white/90 p-5 text-left shadow-sm dark:border-gray-700 dark:bg-gray-900/90 ${onClick ? 'transition hover:border-warning-300 hover:shadow-md' : ''}`}
  >
    <div className="flex items-center justify-between">
      <div>
        <div className="text-xs font-semibold uppercase tracking-[0.2em] text-gray-500 dark:text-gray-400">{label}</div>
        <div className="mt-3 text-3xl font-semibold text-gray-900 dark:text-white">{value}</div>
      </div>
      <div className="rounded-2xl bg-warning-50 p-3 text-warning-700 dark:bg-warning-950/50 dark:text-warning-300">{icon}</div>
    </div>
  </button>
);

const MetricCard: React.FC<{ title: string; value: string; subtitle: string; onClick?: () => void }> = ({ title, value, subtitle, onClick }) => (
  <Card className={`relative border-gray-200 bg-white/95 dark:border-gray-700 dark:bg-gray-900/95 ${onClick ? 'cursor-pointer transition hover:border-warning-300 hover:shadow-sm' : ''}`}>
    {onClick ? <button type="button" aria-label={`Open ${title}`} onClick={onClick} className="absolute inset-0" /> : null}
    <div className="text-sm font-medium text-gray-500 dark:text-gray-400">{title}</div>
    <div className="mt-4 text-4xl font-semibold tracking-tight text-gray-900 dark:text-white">{value}</div>
    <div className="mt-2 text-xs uppercase tracking-[0.18em] text-gray-500 dark:text-gray-400">{subtitle}</div>
  </Card>
);

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

const InstancesPerRegionPanel: React.FC<{
  rows: Array<{ region: string; count: number; running: number; stopped: number }>;
  onOpenRegion?: (region: string) => void;
}> = ({ rows, onOpenRegion }) => {
  const maxCount = Math.max(...rows.map((row) => row.count), 1);

  return (
    <Card className="border-gray-200 bg-white/95 dark:border-gray-700 dark:bg-gray-900/95">
      <div className="mb-5 flex items-center justify-between">
        <div>
          <div className="text-sm font-semibold text-gray-700 dark:text-gray-200">Instances per region</div>
          <div className="mt-1 text-xs text-gray-500 dark:text-gray-400">Regional EC2 footprint across the visible filtered inventory</div>
        </div>
        <div className="flex items-center gap-3 text-[11px] font-semibold uppercase tracking-[0.14em] text-gray-500 dark:text-gray-400">
          <span className="inline-flex items-center gap-1.5">
            <span className="h-2.5 w-2.5 rounded-full bg-success-500" />
            Running
          </span>
          <span className="inline-flex items-center gap-1.5">
            <span className="h-2.5 w-2.5 rounded-full bg-danger-500" />
            Stopped
          </span>
        </div>
      </div>

      <div className="grid gap-6 xl:grid-cols-[260px_minmax(0,1fr)]">
        <div className="space-y-4">
          {rows.map((row) => {
            const runningWidth = `${(row.running / maxCount) * 100}%`;
            const stoppedWidth = `${(row.stopped / maxCount) * 100}%`;

            return (
              <div key={row.region} className="space-y-2">
                <div className="flex items-center justify-between">
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
                    <div className="h-full rounded-full bg-success-500" style={{ width: runningWidth }} />
                  </div>
                  <div className="h-2 overflow-hidden rounded-full bg-gray-100 dark:bg-gray-800">
                    <div className="h-full rounded-full bg-danger-500" style={{ width: stoppedWidth }} />
                  </div>
                </div>
              </div>
            );
          })}
        </div>

        <div className="overflow-hidden rounded-[28px] border border-gray-200 bg-[linear-gradient(180deg,_#f5f7fb_0%,_#e9edf6_100%)] p-4 dark:border-gray-700 dark:bg-[linear-gradient(180deg,_rgba(14,24,48,0.95)_0%,_rgba(24,33,64,0.95)_100%)]">
          <div className="relative mx-auto w-full max-w-[1080px]" style={{ aspectRatio: `${REGION_MAP_WIDTH} / ${REGION_MAP_HEIGHT}` }}>
            <img
              src={worldMapUrl}
              alt="World map"
              className="h-full w-full select-none object-contain opacity-95 dark:opacity-75 dark:[filter:brightness(0.88)_contrast(1.05)]"
            />
            {rows.map((row) => {
              const position = REGION_MARKER_POSITIONS[row.region];
              if (!position) {
                return null;
              }

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
                  title={`${row.region}: ${row.count} instances (${row.running} running / ${row.stopped} stopped)`}
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

const ChartTooltip = ({ active, payload, label }: any) => {
  if (!active || !payload?.length) return null;
  return (
    <div className="rounded-xl border border-gray-200 bg-white px-3 py-2 shadow-lg">
      {label ? <div className="mb-1 text-xs font-semibold uppercase tracking-[0.18em] text-gray-500">{label}</div> : null}
      {payload.map((entry: any) => (
        <div key={`${entry.name}-${entry.dataKey}`} className="flex items-center gap-2 text-sm text-gray-700">
          <span className="h-2.5 w-2.5 rounded-full" style={{ backgroundColor: entry.color }} />
          <span>{entry.name}</span>
          <span className="font-semibold">{typeof entry.value === 'number' ? entry.value.toFixed(1) : entry.value}</span>
        </div>
      ))}
    </div>
  );
};

const buildInstanceTypeTable = (instances: Ec2OverviewInstance[]) => {
  const grouped = new Map<string, Ec2OverviewInstance[]>();
  instances.forEach((instance) => {
    const key = instance.instance_type || 'unknown';
    const existing = grouped.get(key) || [];
    existing.push(instance);
    grouped.set(key, existing);
  });

  return Array.from(grouped.entries())
    .map(([instanceType, rows]) => ({
      instanceType,
      count: rows.length,
      averageCpu: rows.reduce((sum, row) => sum + row.usage.cpu_utilization, 0) / Math.max(rows.length, 1),
      averageMemory: rows.reduce((sum, row) => sum + row.usage.memory_utilization, 0) / Math.max(rows.length, 1),
      averageSent: rows.reduce((sum, row) => sum + row.usage.sent_bytes_mb, 0) / Math.max(rows.length, 1),
      averageReceived: rows.reduce((sum, row) => sum + row.usage.received_bytes_mb, 0) / Math.max(rows.length, 1),
      averageDiskUsed: rows.reduce((sum, row) => sum + row.usage.disk_used_gb, 0) / Math.max(rows.length, 1),
      averageDiskAvailable: rows.reduce((sum, row) => sum + row.usage.disk_available_gb, 0) / Math.max(rows.length, 1),
    }))
    .sort((left, right) => right.count - left.count);
};

export const Ec2InventoryExplorer: React.FC<{ mode: 'overview' | 'collection' }> = ({ mode }) => {
  const navigate = useNavigate();
  const { theme } = useTheme();
  const { profile } = useOptimizationProfile();
  const queryClient = useQueryClient();
  const [searchParams] = useSearchParams();
  const {
    region: sharedRegion,
    environment: sharedEnvironment,
    tagQuery: sharedTagQuery,
    tagLogic: sharedTagLogic,
    activeSavedFilterId,
    savedFilters,
    applySavedFilter,
    setTagLogic: setSharedTagLogic,
    clearActiveSavedFilter,
    reset: resetSharedOverviewFilters,
  } = useSharedOverviewFilters();
  const [searchQuery, setSearchQuery] = useState('');
  const [selectedRegions, setSelectedRegions] = useState<string[]>(
    sharedRegion !== 'all' ? [sharedRegion] : []
  );
  const [selectedState, setSelectedState] = useState('all');
  const [showActionableOnly, setShowActionableOnly] = useState(false);
  const [regionDropdownOpen, setRegionDropdownOpen] = useState(false);
  const [selectedEnvironments, setSelectedEnvironments] = useState<string[]>(
    sharedEnvironment !== 'all' ? [sharedEnvironment] : []
  );
  const [environmentDropdownOpen, setEnvironmentDropdownOpen] = useState(false);
  const [selectedTags, setSelectedTags] = useState<string[]>(
    parseSharedTagQuery(sharedTagQuery)
  );
  const [showPotentialSavingsOnly, setShowPotentialSavingsOnly] = useState(false);
  const [selectedActions, setSelectedActions] = useState<Record<string, string>>({});
  const [executingResources, setExecutingResources] = useState<Set<string>>(new Set());
  const [actionMessages, setActionMessages] = useState<Record<string, string>>({});
  const [inventorySearch, setInventorySearch] = useState('');
  const [inventorySort, setInventorySort] = useState<Ec2InventorySortKey>('savings');

  const { data, isLoading, isError } = useQuery<Ec2OverviewResponse>('inventory-ec2-overview', () =>
    inventoryApi.getEc2Overview()
  );
  const { data: ec2Checks = [] } = useQuery(['checks', 'ec2'], () => checksApi.listChecks('ec2'), {
    retry: false,
  });

  const snoozeInstances = async (instances: Ec2OverviewInstance[], snoozedUntil: string, reason: string) => {
    await inventoryApi.snoozeResources({
      resources: instances.map((instance) => ({
        resource_id: instance.resource_id,
        resource_type: 'ec2',
        resource_name: instance.resource_name,
        account_id: instance.account_id,
        region: instance.region,
      })),
      snoozed_until: snoozedUntil,
      reason,
    });
    await queryClient.invalidateQueries('inventory-ec2-overview');
    await queryClient.invalidateQueries('latest-check-results');
  };

  const removeInstanceSnoozes = async (instances: Ec2OverviewInstance[], reason = '') => {
    await inventoryApi.removeSnoozes({
      resources: instances.map((instance) => ({
        resource_id: instance.resource_id,
        resource_type: 'ec2',
        resource_name: instance.resource_name,
        account_id: instance.account_id,
        region: instance.region,
      })),
      reason,
    });
    await queryClient.invalidateQueries('inventory-ec2-overview');
    await queryClient.invalidateQueries('latest-check-results');
  };

  const collectionFilters = useMemo(
    () => (mode === 'collection' ? parseEc2CollectionFilters(searchParams) : EMPTY_EC2_COLLECTION_FILTERS),
    [mode, searchParams]
  );

  const activeFilters = useMemo<Ec2CollectionFilters>(() => {
      if (mode === 'collection') {
        return collectionFilters;
      }
      return {
        ...EMPTY_EC2_COLLECTION_FILTERS,
        search: searchQuery,
        environments: selectedEnvironments,
        tags: selectedTags,
        tagLogic: sharedTagLogic,
        states: selectedState !== 'all' ? [selectedState] : [],
        regions: selectedRegions,
        actionable: showActionableOnly,
        hasSavings: showPotentialSavingsOnly,
      };
    }, [collectionFilters, mode, searchQuery, selectedEnvironments, selectedRegions, selectedTags, selectedState, sharedTagLogic, showActionableOnly, showPotentialSavingsOnly]);

  const allInstances = data?.instances || [];

  const filteredInstances = useMemo(
    () => applyEc2CollectionFilters(allInstances, activeFilters),
    [activeFilters, allInstances]
  );

    const availableEnvironments = useMemo(() => {
      if (!data) return [];
      return data.dimensions.environments.map((entry) => entry.key).sort();
    }, [data]);

    const availableRegions = useMemo(() => {
      if (!data) return [];
      return data.dimensions.regions.map((entry) => entry.key).sort();
    }, [data]);

    const availableStates = useMemo(() => {
      const states = new Set<string>();
      allInstances.forEach((instance) => {
        states.add((instance.state || 'unknown').toLowerCase());
      });
      return Array.from(states).sort();
    }, [allInstances]);

  const availableTags = useMemo(() => {
    if (!data) return [];

    const tags = new Set<string>();
    data.instances.forEach((instance) => {
      Object.entries(instance.tags || {}).forEach(([key, value]) => {
        tags.add(`${key}:${value}`);
      });
    });

    return Array.from(tags).sort((left, right) => left.localeCompare(right));
  }, [data]);

  useEffect(() => {
    if (mode !== 'overview') {
      return;
    }

    setSelectedRegions(sharedRegion !== 'all' ? [sharedRegion] : []);
  }, [mode, sharedRegion]);

  useEffect(() => {
    if (mode !== 'overview') {
      return;
    }

    setSelectedEnvironments(sharedEnvironment !== 'all' ? [sharedEnvironment] : []);
  }, [mode, sharedEnvironment]);

  useEffect(() => {
    if (mode !== 'overview') {
      return;
    }

    setSelectedTags(parseSharedTagQuery(sharedTagQuery));
  }, [mode, sharedTagQuery]);

  const toggleEnvironment = (environment: string) => {
    if (mode === 'overview') {
      clearActiveSavedFilter();
    }

    setSelectedEnvironments((current) =>
      current.includes(environment)
        ? current.filter((item) => item !== environment)
        : [...current, environment]
    );
  };

  const toggleRegion = (region: string) => {
    if (mode === 'overview') {
      clearActiveSavedFilter();
    }

    setSelectedRegions((current) =>
      current.includes(region)
        ? current.filter((item) => item !== region)
        : [...current, region]
    );
  };

  const openInstanceDetails = (instance: Ec2OverviewInstance) => {
    navigate(`/dashboard/resources/ec2/instances/${encodeURIComponent(instance.resource_id)}`);
  };

  const openCollection = (next: Partial<Ec2CollectionFilters>) => {
    const merged = mergeEc2CollectionFilters(activeFilters, next);
    const matches = applyEc2CollectionFilters(allInstances, merged);

    if (matches.length === 1) {
      openInstanceDetails(matches[0]);
      return;
    }

    navigate(`/dashboard/resources/ec2/collection${buildEc2CollectionSearch(merged)}`);
  };

  const getInstanceActions = (instance: Ec2OverviewInstance) => {
    const check = ec2Checks.find((entry) => entry.check_id === instance.maxops.check_id);
    const mappedActions = check ? getActionsForCheck(check) : [];
    const recommendedAction = instance.maxops.recommended_action?.trim();
    if (!recommendedAction) {
      return mappedActions;
    }
    return mappedActions.includes(recommendedAction) ? mappedActions : [recommendedAction, ...mappedActions];
  };

  const executeInstanceActionRequest = async (instance: Ec2OverviewInstance, action: string) => {
    if (!instance.maxops.check_id || !instance.account_id || !instance.region || !action) {
      setActionMessages((prev) => ({
        ...prev,
        [instance.resource_id]: 'Missing check, account, region, or action for execution.',
      }));
      return;
    }

    setExecutingResources((prev) => {
      const next = new Set(prev);
      next.add(instance.resource_id);
      return next;
    });
    setActionMessages((prev) => ({
      ...prev,
      [instance.resource_id]: '',
    }));

    try {
      const response = await checksApi.executeAction(instance.maxops.check_id, {
        action,
        account_id: instance.account_id,
        region: instance.region,
        resource_id: instance.resource_id,
      });
      setActionMessages((prev) => ({
        ...prev,
        [instance.resource_id]: response.message || 'Action executed.',
      }));
    } catch (error: any) {
      setActionMessages((prev) => ({
        ...prev,
        [instance.resource_id]: error?.response?.data?.detail || error?.message || 'Failed to execute action.',
      }));
    } finally {
      setExecutingResources((prev) => {
        const next = new Set(prev);
        next.delete(instance.resource_id);
        return next;
      });
    }
  };

  const executeInstanceAction = async (instance: Ec2OverviewInstance) => {
    const action = selectedActions[instance.resource_id] || getInstanceActions(instance)[0] || '';
    await executeInstanceActionRequest(instance, action);
  };

  const clearOverviewFilters = () => {
    resetSharedOverviewFilters();
    setSearchQuery('');
    setSelectedRegions([]);
    setSelectedState('all');
    setShowActionableOnly(false);
    setRegionDropdownOpen(false);
    setSelectedEnvironments([]);
    setSelectedTags([]);
    setShowPotentialSavingsOnly(false);
    setEnvironmentDropdownOpen(false);
  };

  const derived = useMemo(() => {
    const lowCpu = filteredInstances
      .filter((instance) => instance.state === 'running' && instance.usage.cpu_utilization <= 10)
      .sort((left, right) => left.usage.cpu_utilization - right.usage.cpu_utilization);
    const lowMemory = filteredInstances
      .filter((instance) => instance.state === 'running' && instance.usage.memory_utilization <= 18)
      .sort((left, right) => left.usage.memory_utilization - right.usage.memory_utilization);

    const topInstanceTypes = buildInstanceTypeTable(filteredInstances);
    const averageCpu = filteredInstances.reduce((sum, instance) => sum + instance.usage.cpu_utilization, 0) / Math.max(filteredInstances.length, 1);
    const averageMemory = filteredInstances.reduce((sum, instance) => sum + instance.usage.memory_utilization, 0) / Math.max(filteredInstances.length, 1);
    const averageSent = filteredInstances.reduce((sum, instance) => sum + instance.usage.sent_bytes_mb, 0) / Math.max(filteredInstances.length, 1);
    const averageReceived = filteredInstances.reduce((sum, instance) => sum + instance.usage.received_bytes_mb, 0) / Math.max(filteredInstances.length, 1);
    const actionable = filteredInstances.filter((instance) => instance.maxops.status === 'actionable').length;
    const running = filteredInstances.filter((instance) => instance.state === 'running').length;
    const stopped = filteredInstances.filter((instance) => instance.state === 'stopped').length;
    const yearlySavings = filteredInstances.reduce((sum, instance) => sum + instance.maxops.potential_savings_yearly, 0);

    const findings = Array.from(
      filteredInstances.reduce((acc, instance) => {
        const key = instance.maxops.finding_type || 'healthy';
        acc.set(key, (acc.get(key) || 0) + 1);
        return acc;
      }, new Map<string, number>())
    )
      .map(([key, count]) => ({ key, name: titleCase(key), count }))
      .sort((left, right) => right.count - left.count);

    const environments = Array.from(
      filteredInstances.reduce((acc, instance) => {
        const key = (instance.tags.env || 'unknown').toLowerCase();
        acc.set(key, (acc.get(key) || 0) + 1);
        return acc;
      }, new Map<string, number>())
    )
      .map(([key, count]) => ({ key, name: titleCase(key), count }))
      .sort((left, right) => right.count - left.count);

    const stateData = [
      { name: 'Running', count: running },
      { name: 'Stopped', count: stopped },
    ];

    const scatterData = filteredInstances.slice(0, 120).map((instance) => ({
      x: instance.usage.cpu_utilization,
      y: instance.usage.memory_utilization,
      z: Math.max(instance.usage.received_bytes_mb, 10),
      name: instance.resource_name || instance.resource_id,
      resourceId: instance.resource_id,
      type: instance.instance_type || 'unknown',
    }));

    const regionRows = Array.from(
      filteredInstances.reduce((acc, instance) => {
        const region = instance.region || 'unknown';
        const existing = acc.get(region) || { region, count: 0, running: 0, stopped: 0 };
        existing.count += 1;
        if (instance.state === 'running') {
          existing.running += 1;
        }
        if (instance.state === 'stopped') {
          existing.stopped += 1;
        }
        acc.set(region, existing);
        return acc;
      }, new Map<string, { region: string; count: number; running: number; stopped: number }>())
    )
      .map(([, row]) => row)
      .sort((left, right) => right.count - left.count);

    const actionableRows = filteredInstances
      .filter((instance) => instance.maxops.status === 'actionable')
      .sort((left, right) => right.maxops.potential_savings_yearly - left.maxops.potential_savings_yearly)
      .slice(0, 15);

    const collectionRows = [...filteredInstances].sort((left, right) => {
      const leftActionable = left.maxops.status === 'actionable' ? 1 : 0;
      const rightActionable = right.maxops.status === 'actionable' ? 1 : 0;

      if (rightActionable !== leftActionable) {
        return rightActionable - leftActionable;
      }

      return right.maxops.potential_savings_yearly - left.maxops.potential_savings_yearly;
    });

    return {
      lowCpu,
      lowMemory,
      topInstanceTypes,
      averageCpu,
      averageMemory,
      averageSent,
      averageReceived,
      actionable,
      running,
      stopped,
      yearlySavings,
      findings,
      environments,
      stateData,
      scatterData,
      regionRows,
      actionableRows,
      collectionRows,
    };
  }, [filteredInstances]);

  const tableRows = derived.collectionRows;
  const inventoryRows = useMemo(() => {
    const query = inventorySearch.trim().toLowerCase();
    const matches = !query
      ? tableRows
      : tableRows.filter((instance) =>
          [
            instance.resource_id,
            instance.resource_name,
            instance.instance_type,
            instance.region,
            instance.state,
            instance.maxops.finding_type,
            instance.maxops.title,
            ...Object.entries(instance.tags || {}).flatMap(([key, value]) => [key, `${key}:${value}`, String(value)]),
          ]
            .filter(Boolean)
            .some((value) => String(value).toLowerCase().includes(query))
        );

    return [...matches].sort((left, right) => {
      switch (inventorySort) {
        case 'name':
          return String(left.resource_name || left.resource_id).localeCompare(String(right.resource_name || right.resource_id));
        case 'region':
          return String(left.region || 'unknown').localeCompare(String(right.region || 'unknown'));
        case 'cpu':
          return right.usage.cpu_utilization - left.usage.cpu_utilization;
        case 'memory':
          return right.usage.memory_utilization - left.usage.memory_utilization;
        case 'monthlyCost':
          return Number(right.metadata.monthly_cost_estimate || 0) - Number(left.metadata.monthly_cost_estimate || 0);
        case 'savings':
        default:
          return right.maxops.potential_savings_yearly - left.maxops.potential_savings_yearly;
      }
    });
  }, [inventorySearch, inventorySort, tableRows]);

  if (isLoading) {
    return (
      <Layout>
        <div className="flex min-h-[60vh] items-center justify-center rounded-[32px] border border-gray-200 bg-white/90">
          <div className="text-center">
            <div className="text-lg font-semibold text-gray-800">Loading EC2 overview</div>
            <div className="mt-2 text-sm text-gray-500">Preparing imported EC2 inventory from the database.</div>
          </div>
        </div>
      </Layout>
    );
  }

  if (isError || !data) {
    return (
      <Layout>
        <Card className="border-danger-200 bg-danger-50">
          <div className="text-lg font-semibold text-danger-900">EC2 overview unavailable</div>
          <div className="mt-2 text-sm text-danger-700">The UI could not load imported EC2 inventory from the backend.</div>
        </Card>
      </Layout>
    );
  }

  const donutData = derived.topInstanceTypes.slice(0, 6).map((row) => ({ name: row.instanceType, value: row.count }));
  const instanceTypeBars = derived.topInstanceTypes.slice(0, 6).map((row) => ({
    name: row.instanceType,
    CPU: Number(row.averageCpu.toFixed(1)),
    Memory: Number(row.averageMemory.toFixed(1)),
  }));
  const networkBars = derived.topInstanceTypes.slice(0, 6).map((row) => ({
    name: row.instanceType,
    Received: Number(row.averageReceived.toFixed(1)),
    Sent: Number(row.averageSent.toFixed(1)),
  }));
  const exportInventoryCsv = () => {
    downloadCsv(
      mode === 'collection' ? 'ec2-collection-inventory.csv' : 'ec2-instance-inventory.csv',
      inventoryRows.map((instance) => ({
        resource_id: instance.resource_id,
        resource_name: instance.resource_name || '',
        instance_type: instance.instance_type || '',
        region: instance.region || '',
        state: instance.state || '',
        finding: instance.maxops.finding_type || '',
        status: instance.maxops.status || '',
        recommended_action: instance.maxops.recommended_action || '',
        monthly_cost_estimate: Number(instance.metadata.monthly_cost_estimate || 0),
        cpu_utilization: instance.usage.cpu_utilization,
        memory_utilization: instance.usage.memory_utilization,
        sent_bytes_mb: instance.usage.sent_bytes_mb,
        received_bytes_mb: instance.usage.received_bytes_mb,
        potential_savings_yearly: instance.maxops.potential_savings_yearly,
        tags: instance.tags || {},
        metadata: instance.metadata || {},
      }))
    );
  };
  const diskBars = derived.topInstanceTypes.slice(0, 6).map((row) => ({
    name: row.instanceType,
    Used: Number(row.averageDiskUsed.toFixed(1)),
    Available: Number(row.averageDiskAvailable.toFixed(1)),
  }));
  const activeRegionSelections = mode === 'collection' ? collectionFilters.regions : selectedRegions;
  const activeEnvironmentSelections = mode === 'collection' ? collectionFilters.environments : selectedEnvironments;
  const activeTagSelections = mode === 'collection' ? collectionFilters.tags : selectedTags;
  const regionFilterLabel =
    activeRegionSelections.length === 0
      ? 'All regions'
      : activeRegionSelections.length === 1
        ? activeRegionSelections[0]
        : `${activeRegionSelections.length} regions`;
  const environmentFilterLabel =
    activeEnvironmentSelections.length === 0
      ? 'All environments'
      : activeEnvironmentSelections.length === 1
        ? titleCase(activeEnvironmentSelections[0])
        : `${activeEnvironmentSelections.length} environments`;
  const collectionTitle = mode === 'collection' ? getEc2CollectionTitle(collectionFilters) : 'Imported EC2 inventory, utilization, and optimization insights';
  const collectionScopeChips =
    mode === 'collection'
      ? [
          ...(collectionFilters.search.trim()
            ? [
                {
                  key: `search:${collectionFilters.search.trim()}`,
                  label: `Search: ${collectionFilters.search.trim()}`,
                  onRemove: () => openCollection({ search: '' }),
                },
              ]
            : []),
          ...collectionFilters.environments.map((value) => ({
            key: `env:${value}`,
            label: `Env: ${value}`,
            onRemove: () => openCollection({ environments: collectionFilters.environments.filter((item) => item !== value) }),
          })),
          ...collectionFilters.tags.map((value) => ({
            key: `tag:${value}`,
            label: `Tag: ${value}`,
            onRemove: () => openCollection({ tags: collectionFilters.tags.filter((item) => item !== value) }),
          })),
          ...collectionFilters.states.map((value) => ({
            key: `state:${value}`,
            label: `State: ${value}`,
            onRemove: () => openCollection({ states: collectionFilters.states.filter((item) => item !== value) }),
          })),
          ...collectionFilters.regions.map((value) => ({
            key: `region:${value}`,
            label: `Region: ${value}`,
            onRemove: () => openCollection({ regions: collectionFilters.regions.filter((item) => item !== value) }),
          })),
          ...collectionFilters.instanceTypes.map((value) => ({
            key: `type:${value}`,
            label: `Type: ${value}`,
            onRemove: () => openCollection({ instanceTypes: collectionFilters.instanceTypes.filter((item) => item !== value) }),
          })),
          ...collectionFilters.findings.map((value) => ({
            key: `finding:${value}`,
            label: `Finding: ${value}`,
            onRemove: () => openCollection({ findings: collectionFilters.findings.filter((item) => item !== value) }),
          })),
          ...(collectionFilters.actionable
            ? [
                {
                  key: 'actionable',
                  label: 'Actionable',
                  onRemove: () => openCollection({ actionable: false }),
                },
              ]
            : []),
          ...(collectionFilters.hasSavings
            ? [
                {
                  key: 'hasSavings',
                  label: 'Potential savings',
                  onRemove: () => openCollection({ hasSavings: false }),
                },
              ]
            : []),
          ...(collectionFilters.lowCpu
            ? [
                {
                  key: 'lowCpu',
                  label: 'Low CPU',
                  onRemove: () => openCollection({ lowCpu: false }),
                },
              ]
            : []),
          ...(collectionFilters.lowMemory
            ? [
                {
                  key: 'lowMemory',
                  label: 'Low memory',
                  onRemove: () => openCollection({ lowMemory: false }),
                },
              ]
            : []),
          ...(collectionFilters.sortBy
            ? [
                {
                  key: `sort:${collectionFilters.sortBy}`,
                  label: `Sorted by ${collectionFilters.sortBy}`,
                  onRemove: () => openCollection({ sortBy: undefined }),
                },
              ]
            : []),
        ]
      : [];
  const tableTitle = mode === 'collection' ? 'EC2 instances in this collection' : 'EC2 instance inventory';
  const hasOverviewFilters =
    Boolean(activeSavedFilterId) ||
    Boolean(searchQuery.trim()) ||
    selectedRegions.length > 0 ||
    selectedState !== 'all' ||
    showActionableOnly ||
    selectedEnvironments.length > 0 ||
    selectedTags.length > 0 ||
    sharedTagLogic !== 'and' ||
    showPotentialSavingsOnly;
  const hasCollectionFilters =
    Boolean(collectionFilters.search.trim()) ||
    collectionFilters.environments.length > 0 ||
    collectionFilters.tags.length > 0 ||
    collectionFilters.tagLogic !== 'and' ||
    collectionFilters.states.length > 0 ||
    collectionFilters.regions.length > 0 ||
    collectionFilters.instanceTypes.length > 0 ||
    collectionFilters.findings.length > 0 ||
    collectionFilters.actionable ||
    collectionFilters.hasSavings ||
    collectionFilters.lowCpu ||
    collectionFilters.lowMemory ||
    Boolean(collectionFilters.sortBy);
  const showResetButton = mode === 'collection' ? hasCollectionFilters : hasOverviewFilters;

  const topFilterSelects = [
    {
      id: 'ec2-state',
      label: 'State',
      value: mode === 'collection' ? collectionFilters.states[0] || 'all' : selectedState,
      options: [{ label: 'All states', value: 'all' }, ...availableStates.map((value) => ({ label: titleCase(value), value }))],
      onChange: (value: string) => {
        if (mode === 'collection') {
          openCollection({ states: value === 'all' ? [] : [value] });
          return;
        }
        setSelectedState(value);
      },
    },
  ];

  const topFilterToggles = mode === 'collection'
    ? [
        {
          id: 'ec2-actionable',
          label: 'Actionable only',
          checked: collectionFilters.actionable,
          onChange: (checked: boolean) => openCollection({ actionable: checked }),
        },
        {
          id: 'ec2-savings',
          label: 'Has savings',
          checked: collectionFilters.hasSavings,
          onChange: (checked: boolean) => openCollection({ hasSavings: checked }),
        },
      ]
    : [
        {
          id: 'ec2-actionable',
          label: 'Actionable only',
          checked: showActionableOnly,
          onChange: setShowActionableOnly,
        },
        {
          id: 'ec2-savings',
          label: 'Has savings',
          checked: showPotentialSavingsOnly,
          onChange: setShowPotentialSavingsOnly,
        },
      ];

  return (
    <Layout>
      <div className="space-y-8">
        <div className="flex flex-col gap-8">
        <div className="order-2">
        <ResourcePageFilters
          searchValue={mode === 'collection' ? collectionFilters.search : searchQuery}
          searchPlaceholder="Search instance ID, name, region, type, tag, or finding"
          onSearchChange={(value) => {
            if (mode === 'collection') {
              openCollection({ search: value });
              return;
            }
            setSearchQuery(value);
          }}
          savedFilterSelect={
            mode === 'overview'
              ? {
                  id: 'ec2-saved-filter',
                  label: 'Saved filter',
                  value: activeSavedFilterId || '',
                  options: [
                    { label: 'Custom filters', value: '' },
                    ...savedFilters.map((filter) => ({ label: filter.name, value: filter.id })),
                  ],
                  onChange: applySavedFilter,
                }
              : undefined
          }
          selects={topFilterSelects}
          toggles={topFilterToggles}
          onReset={() => {
            if (mode === 'collection') {
              navigate('/dashboard/resources/ec2');
              return;
            }
            setSearchQuery('');
            clearOverviewFilters();
          }}
          resetDisabled={!showResetButton}
        >
          <>
            <div className="relative">
              <div className="mb-2 text-xs font-semibold uppercase tracking-[0.18em] text-gray-500 dark:text-gray-400">Region</div>
              <button
                type="button"
                onClick={() => setRegionDropdownOpen((open) => !open)}
                className="inline-flex w-full items-center justify-between gap-3 rounded-2xl bg-gray-50 px-4 py-3 text-sm font-medium text-gray-700 ring-1 ring-gray-200 transition hover:bg-gray-100 dark:bg-gray-800/80 dark:text-gray-100 dark:ring-gray-700 dark:hover:bg-gray-800"
              >
                <span>{regionFilterLabel}</span>
                <ChevronDown size={16} className={regionDropdownOpen ? 'rotate-180 transition' : 'transition'} />
              </button>

              {regionDropdownOpen && (
                <div className="absolute left-0 top-full z-20 mt-2 w-full rounded-2xl border border-gray-200 bg-white p-3 shadow-xl dark:border-gray-700 dark:bg-gray-900">
                  <div className="mb-3 flex items-center justify-between">
                    <div className="text-xs font-semibold uppercase tracking-[0.18em] text-gray-500 dark:text-gray-400">Select regions</div>
                    <button
                      type="button"
                      onClick={() => setRegionDropdownOpen(false)}
                      className="text-xs font-semibold uppercase tracking-[0.14em] text-gray-500 hover:text-gray-700 dark:text-gray-400 dark:hover:text-gray-200"
                    >
                      Close
                    </button>
                  </div>

                  <div className="mb-3 flex gap-2">
                    <button
                      type="button"
                      onClick={() => {
                        if (mode === 'collection') {
                          openCollection({ regions: [] });
                          return;
                        }
                        clearActiveSavedFilter();
                        setSelectedRegions([]);
                      }}
                      className="rounded-full bg-gray-100 px-3 py-1 text-xs font-medium text-gray-700 transition hover:bg-gray-200 dark:bg-gray-800 dark:text-gray-200"
                    >
                      All
                    </button>
                    <button
                      type="button"
                      onClick={() => {
                        if (mode === 'collection') {
                          openCollection({ regions: availableRegions });
                          return;
                        }
                        clearActiveSavedFilter();
                        setSelectedRegions(availableRegions);
                      }}
                      className="rounded-full bg-warning-100 px-3 py-1 text-xs font-medium text-warning-800 transition hover:bg-warning-200"
                    >
                      Select all
                    </button>
                  </div>

                  <div className="max-h-64 space-y-2 overflow-y-auto pr-1">
                    {availableRegions.map((region) => {
                      const checked = activeRegionSelections.includes(region);
                      return (
                        <label
                          key={region}
                          className="flex cursor-pointer items-center justify-between rounded-xl px-3 py-2 text-sm text-gray-700 transition hover:bg-gray-50 dark:text-gray-200 dark:hover:bg-gray-800"
                        >
                          <span>{region}</span>
                          <input
                            type="checkbox"
                            checked={checked}
                            onChange={() => {
                              if (mode === 'collection') {
                                openCollection({
                                  regions: checked
                                    ? activeRegionSelections.filter((item) => item !== region)
                                    : [...activeRegionSelections, region],
                                });
                                return;
                              }
                              toggleRegion(region);
                            }}
                            className="h-4 w-4 rounded border-gray-300 text-warning-500 focus:ring-warning-400"
                          />
                        </label>
                      );
                    })}
                  </div>
                </div>
              )}
            </div>

            <div className="relative">
              <div className="mb-2 text-xs font-semibold uppercase tracking-[0.18em] text-gray-500 dark:text-gray-400">Environment</div>
              <button
                type="button"
                onClick={() => setEnvironmentDropdownOpen((open) => !open)}
                className="inline-flex w-full items-center justify-between gap-3 rounded-2xl bg-gray-50 px-4 py-3 text-sm font-medium text-gray-700 ring-1 ring-gray-200 transition hover:bg-gray-100 dark:bg-gray-800/80 dark:text-gray-100 dark:ring-gray-700 dark:hover:bg-gray-800"
              >
                <span>{environmentFilterLabel}</span>
                <ChevronDown size={16} className={environmentDropdownOpen ? 'rotate-180 transition' : 'transition'} />
              </button>

              {environmentDropdownOpen && (
                <div className="absolute left-0 top-full z-20 mt-2 w-full rounded-2xl border border-gray-200 bg-white p-3 shadow-xl dark:border-gray-700 dark:bg-gray-900">
                  <div className="mb-3 flex items-center justify-between">
                    <div className="text-xs font-semibold uppercase tracking-[0.18em] text-gray-500 dark:text-gray-400">Select environments</div>
                    <button
                      type="button"
                      onClick={() => setEnvironmentDropdownOpen(false)}
                      className="text-xs font-semibold uppercase tracking-[0.14em] text-gray-500 hover:text-gray-700 dark:text-gray-400 dark:hover:text-gray-200"
                    >
                      Close
                    </button>
                  </div>

                  <div className="mb-3 flex gap-2">
                    <button
                      type="button"
                      onClick={() => {
                        if (mode === 'collection') {
                          openCollection({ environments: [] });
                          return;
                        }
                        clearActiveSavedFilter();
                        setSelectedEnvironments([]);
                      }}
                      className="rounded-full bg-gray-100 px-3 py-1 text-xs font-medium text-gray-700 transition hover:bg-gray-200 dark:bg-gray-800 dark:text-gray-200"
                    >
                      All
                    </button>
                    <button
                      type="button"
                      onClick={() => {
                        if (mode === 'collection') {
                          openCollection({ environments: availableEnvironments });
                          return;
                        }
                        clearActiveSavedFilter();
                        setSelectedEnvironments(availableEnvironments);
                      }}
                      className="rounded-full bg-warning-100 px-3 py-1 text-xs font-medium text-warning-800 transition hover:bg-warning-200"
                    >
                      Select all
                    </button>
                  </div>

                  <div className="max-h-64 space-y-2 overflow-y-auto pr-1">
                    {availableEnvironments.map((environment) => {
                      const checked = activeEnvironmentSelections.includes(environment);
                      return (
                        <label
                          key={environment}
                          className="flex cursor-pointer items-center justify-between rounded-xl px-3 py-2 text-sm text-gray-700 transition hover:bg-gray-50 dark:text-gray-200 dark:hover:bg-gray-800"
                        >
                          <span>{titleCase(environment)}</span>
                          <input
                            type="checkbox"
                            checked={checked}
                            onChange={() => {
                              if (mode === 'collection') {
                                openCollection({
                                  environments: checked
                                    ? activeEnvironmentSelections.filter((item) => item !== environment)
                                    : [...activeEnvironmentSelections, environment],
                                });
                                return;
                              }
                              toggleEnvironment(environment);
                            }}
                            className="h-4 w-4 rounded border-gray-300 text-warning-500 focus:ring-warning-400"
                          />
                        </label>
                      );
                    })}
                  </div>
                </div>
              )}
            </div>

            <SharedTagFilterField
              selectedTags={activeTagSelections}
              availableTags={availableTags}
              tagLogic={mode === 'collection' ? collectionFilters.tagLogic : sharedTagLogic}
              onChange={(tags) => {
                if (mode === 'collection') {
                  openCollection({ tags });
                  return;
                }

                clearActiveSavedFilter();
                setSelectedTags(tags);
              }}
              onTagLogicChange={(logic) => {
                if (mode === 'collection') {
                  openCollection({ tagLogic: logic });
                  return;
                }

                clearActiveSavedFilter();
                setSharedTagLogic(logic);
              }}
            />
          </>
        </ResourcePageFilters>
        </div>

        <section className="order-1 overflow-hidden rounded-[32px] border border-gray-200 bg-[radial-gradient(circle_at_top_left,_rgba(248,191,61,0.24),_transparent_34%),linear-gradient(135deg,_#f5f7fb_0%,_#fffbeb_42%,_#ecfdf9_100%)] p-8 shadow-sm dark:border-gray-700 dark:bg-[radial-gradient(circle_at_top_left,_rgba(245,166,35,0.14),_transparent_28%),linear-gradient(135deg,_rgba(14,24,48,0.98)_0%,_rgba(24,33,64,0.96)_46%,_rgba(7,12,22,0.98)_100%)]">
          <div className="flex flex-col gap-6 xl:flex-row xl:items-end xl:justify-between">
            <div>
              <button
                type="button"
                onClick={() => navigate(mode === 'collection' ? '/dashboard/resources/ec2' : '/dashboard')}
                className="mb-5 inline-flex items-center gap-2 rounded-full border border-gray-200 bg-white/80 px-4 py-2 text-sm font-medium text-gray-700 transition hover:bg-white"
              >
                <ArrowLeft size={16} />
                {mode === 'collection' ? 'EC2 Overview' : 'Dashboard'}
              </button>
              <div className="text-xs font-semibold uppercase tracking-[0.28em] text-gray-500">
                {mode === 'collection' ? 'EC2 Collection Workspace' : 'EC2 Inventory Workspace'}
              </div>
              <h1 className="mt-3 text-4xl font-semibold tracking-tight text-gray-950 dark:text-gray-50">{collectionTitle}</h1>
              {collectionScopeChips.length > 0 ? (
                <div className="mt-4 flex flex-wrap gap-2">
                  {collectionScopeChips.map((chip) => (
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
                      if (mode === 'collection') {
                        navigate('/dashboard/resources/ec2');
                        return;
                      }
                      clearOverviewFilters();
                    }}
                    className="inline-flex items-center rounded-full border border-gray-200 bg-white/85 px-4 py-2 text-sm font-medium text-gray-700 transition hover:bg-white dark:border-gray-700 dark:bg-gray-900/80 dark:text-gray-200 dark:hover:bg-gray-900"
                  >
                    Reset filters
                  </button>
                </div>
              ) : null}
            </div>
            <div className="grid gap-3 sm:grid-cols-2">
              <div className="rounded-2xl border border-gray-200 bg-white/80 px-5 py-4 dark:border-gray-700 dark:bg-gray-900/80">
                <div className="text-xs font-semibold uppercase tracking-[0.18em] text-gray-500 dark:text-gray-400">Account</div>
                <div className="mt-2 text-lg font-semibold text-gray-900 dark:text-white">{data.account_id || 'Unknown'}</div>
              </div>
              <div className="rounded-2xl border border-gray-200 bg-white/80 px-5 py-4 dark:border-gray-700 dark:bg-gray-900/80">
                <div className="text-xs font-semibold uppercase tracking-[0.18em] text-gray-500 dark:text-gray-400">Generated</div>
                <div className="mt-2 text-lg font-semibold text-gray-900 dark:text-white">{formatDateTime(data.generated_at)}</div>
              </div>
            </div>
          </div>

          <div className="mt-8 grid gap-4 xl:grid-cols-6">
            <SummaryPill label="Instances" value={String(filteredInstances.length)} icon={<Server size={22} />} onClick={() => openCollection({})} />
            <SummaryPill label="Running" value={String(derived.running)} icon={<Cpu size={22} />} onClick={() => openCollection({ states: ['running'] })} />
            <SummaryPill label="Stopped" value={String(derived.stopped)} icon={<HardDrive size={22} />} onClick={() => openCollection({ states: ['stopped'] })} />
            <SummaryPill label="Actionable" value={String(adjustOptimizationCount(derived.actionable, profile))} icon={<ShieldAlert size={22} />} onClick={() => openCollection({ actionable: true })} />
            <SummaryPill label="Avg Sent" value={`${derived.averageSent.toFixed(0)} MB`} icon={<Network size={22} />} onClick={() => openCollection({ sortBy: 'sent' })} />
            <SummaryPill label="Potential Yearly Savings" value={formatCurrency(adjustOptimizationSavings(derived.yearlySavings, profile))} icon={<Wallet size={22} />} onClick={() => openCollection({ hasSavings: true, sortBy: 'monthlyCost' })} />
          </div>
        </section>
        </div>

        <section className="grid gap-6">
          <ResourceActionPanel
            title={tableTitle}
            subtitle={`${inventoryRows.length} visible`}
            resourceLabel="EC2 instance"
            items={inventoryRows}
            getId={(instance) => instance.resource_id}
            getName={(instance) => instance.resource_name || instance.resource_id}
            getActions={getInstanceActions}
            canExecute={(instance, action) => Boolean(instance.maxops.check_id && instance.account_id && instance.region && action)}
            executeAction={executeInstanceActionRequest}
            formatActionLabel={titleCase}
            getSnooze={(instance) => instance.snooze}
            onSnooze={snoozeInstances}
            onRemoveSnooze={removeInstanceSnoozes}
            toolbar={
              <>
                <div className="relative min-w-[240px]">
                  <Search size={14} className="pointer-events-none absolute left-3 top-1/2 -trangray-y-1/2 text-gray-400" />
                  <input
                    type="text"
                    value={inventorySearch}
                    onChange={(event) => setInventorySearch(event.target.value)}
                    placeholder="Search visible instances"
                    className="w-full rounded-xl border border-gray-200 bg-white py-2 pl-9 pr-3 text-sm text-gray-700 outline-none transition focus:border-warning-300 focus:ring-2 focus:ring-warning-100 dark:border-gray-700 dark:bg-gray-900/80 dark:text-gray-100 dark:focus:border-warning-500 dark:focus:ring-warning-900/40"
                  />
                </div>
                <select
                  value={inventorySort}
                  onChange={(event) => setInventorySort(event.target.value as Ec2InventorySortKey)}
                  className="rounded-xl border border-gray-200 bg-white px-3 py-2 text-sm text-gray-700 outline-none transition focus:border-warning-300 focus:ring-2 focus:ring-warning-100 dark:border-gray-700 dark:bg-gray-900/80 dark:text-gray-100 dark:focus:border-warning-500 dark:focus:ring-warning-900/40"
                >
                  <option value="savings">Sort by yearly savings</option>
                  <option value="monthlyCost">Sort by monthly cost</option>
                  <option value="cpu">Sort by CPU</option>
                  <option value="memory">Sort by memory</option>
                  <option value="name">Sort by name</option>
                  <option value="region">Sort by region</option>
                </select>
                <button
                  type="button"
                  onClick={exportInventoryCsv}
                  disabled={inventoryRows.length === 0}
                  className="inline-flex items-center gap-2 rounded-xl border border-gray-200 bg-white px-3 py-2 text-sm font-medium text-gray-700 transition hover:border-warning-300 hover:text-warning-700 disabled:cursor-not-allowed disabled:opacity-50 dark:border-gray-700 dark:bg-gray-900/80 dark:text-gray-100 dark:hover:border-warning-500 dark:hover:text-warning-300"
                >
                  <Download size={14} />
                  Export CSV
                </button>
              </>
            }
            confirmColumns={[
              {
                header: 'Instance',
                render: (instance) => (
                  <div>
                    <div className="font-semibold text-gray-900 dark:text-white">{instance.resource_name || instance.resource_id}</div>
                    <div className="mt-1 text-xs text-gray-500 dark:text-gray-400">{instance.resource_id}</div>
                  </div>
                ),
              },
              { header: 'Type', render: (instance) => instance.instance_type || 'unknown' },
              { header: 'Region', render: (instance) => instance.region || 'unknown' },
              { header: 'State', render: (instance) => instance.state || 'unknown' },
              { header: 'Finding', render: (instance) => titleCase(instance.maxops.finding_type || 'healthy') },
              {
                header: 'Yearly Savings',
                render: (instance) => (
                  <span className="font-semibold text-success-700 dark:text-success-300">
                    {formatCurrency(adjustOptimizationSavings(instance.maxops.potential_savings_yearly, profile))}
                  </span>
                ),
              },
            ]}
            renderItem={(instance) => {
                const actions = getInstanceActions(instance);
                const selectedAction = selectedActions[instance.resource_id] || actions[0] || '';
                const canExecute = Boolean(instance.maxops.check_id && instance.account_id && instance.region && selectedAction);

                return (
                  <div
                    key={instance.resource_id}
                    className="grid w-full grid-cols-[minmax(0,2.1fr)_140px_180px_minmax(280px,1fr)_160px] gap-4 rounded-2xl border border-gray-200 bg-white/90 px-4 py-4 text-left transition hover:border-warning-300 hover:shadow-sm dark:border-gray-700 dark:bg-gray-900/90"
                  >
                    <button type="button" onClick={() => openInstanceDetails(instance)} className="min-w-0 text-left">
                      <div className="flex items-center gap-3">
                        <span className="flex h-10 w-10 items-center justify-center rounded-2xl bg-warning-50 dark:bg-warning-950/40">
                          <Server size={20} className="text-warning-600 dark:text-warning-300" />
                        </span>
                        <div className="min-w-0">
                          <div className="truncate text-sm font-semibold text-gray-900 dark:text-white">{instance.resource_name || instance.resource_id}</div>
                          <div className="mt-1 text-xs text-gray-500 dark:text-gray-400">
                            {instance.instance_type || 'unknown type'} • {instance.region || 'unknown region'}
                          </div>
                        </div>
                      </div>
                      <div className="mt-3 flex flex-wrap gap-2">
                        <span className="rounded-full bg-warning-50 px-2.5 py-1 text-[11px] font-semibold uppercase tracking-[0.12em] text-warning-700 dark:bg-warning-950/40 dark:text-warning-300">
                          {instance.state || 'unknown state'}
                        </span>
                        <span className={`rounded-full px-2.5 py-1 text-[11px] font-semibold uppercase tracking-[0.12em] ${instance.maxops.status === 'actionable' ? 'bg-danger-50 text-danger-700 dark:bg-danger-950/40 dark:text-danger-300' : 'bg-gray-100 text-gray-500 dark:bg-gray-800 dark:text-gray-300'}`}>
                          {instance.maxops.status}
                        </span>
                      </div>
                    </button>
                    <div>
                      <div className="text-[11px] font-semibold uppercase tracking-[0.16em] text-gray-500 dark:text-gray-400">Finding</div>
                      <div className="mt-2 text-lg font-semibold text-gray-900 dark:text-white">{titleCase(instance.maxops.finding_type || 'healthy')}</div>
                    </div>
                    <div>
                      <div className="text-[11px] font-semibold uppercase tracking-[0.16em] text-gray-500 dark:text-gray-400">Monthly Cost</div>
                      <div className="mt-2 text-lg font-semibold text-gray-900 dark:text-white">{formatCurrency(Number(instance.metadata.monthly_cost_estimate || 0))}</div>
                      <div className="mt-1 text-xs text-gray-500 dark:text-gray-400">
                        {formatPercent(instance.usage.cpu_utilization || 0)} CPU • {formatPercent(instance.usage.memory_utilization || 0)} Memory
                      </div>
                    </div>
                    <div onClick={(event) => event.stopPropagation()}>
                      {actions.length > 0 ? (
                        <div className="space-y-2">
                          <div className="flex items-center gap-2">
                            <select
                              value={selectedAction}
                              onChange={(event) =>
                                setSelectedActions((prev) => ({
                                  ...prev,
                                  [instance.resource_id]: event.target.value,
                                }))
                              }
                              className="min-w-0 flex-1 rounded-xl border border-gray-200 bg-white px-3 py-2 text-sm text-gray-700 outline-none transition focus:border-warning-300 focus:ring-2 focus:ring-warning-100 dark:border-gray-700 dark:bg-gray-900/80 dark:text-gray-100 dark:focus:border-warning-500 dark:focus:ring-warning-900/40"
                            >
                              {actions.map((action) => (
                                <option key={action} value={action}>
                                  {titleCase(action)}
                                </option>
                              ))}
                            </select>
                            <button
                              type="button"
                              onClick={() => executeInstanceAction(instance)}
                              disabled={!canExecute || executingResources.has(instance.resource_id)}
                              className="inline-flex shrink-0 items-center gap-2 rounded-xl bg-warning-500 px-3 py-2 text-sm font-semibold text-white transition hover:bg-warning-600 disabled:cursor-not-allowed disabled:opacity-50"
                            >
                              <Play size={14} />
                              {executingResources.has(instance.resource_id) ? 'Running...' : 'Execute'}
                            </button>
                            <div className="flex h-9 w-9 shrink-0 items-center justify-center">
                              {actionMessages[instance.resource_id] && isActionErrorMessage(actionMessages[instance.resource_id]) ? (
                                <div className="group relative inline-flex items-center justify-center">
                                  <AlertCircle size={16} className="text-danger-500" />
                                  <div className="pointer-events-none absolute left-1/2 top-full z-20 mt-2 hidden w-80 -trangray-x-1/2 rounded-xl border border-danger-200 bg-white px-3 py-2 text-xs normal-case tracking-normal text-danger-700 shadow-lg group-hover:block dark:border-danger-900 dark:bg-gray-900 dark:text-danger-300">
                                    {actionMessages[instance.resource_id]}
                                  </div>
                                </div>
                              ) : null}
                            </div>
                          </div>
                          {actionMessages[instance.resource_id] && !isActionErrorMessage(actionMessages[instance.resource_id]) ? (
                            <div className="max-w-[360px] text-xs text-gray-500 dark:text-gray-400">{actionMessages[instance.resource_id]}</div>
                          ) : null}
                        </div>
                      ) : (
                        <span className="text-sm text-gray-500 dark:text-gray-400">{titleCase(instance.maxops.recommended_action || 'observe')}</span>
                      )}
                    </div>
                    <div>
                      <div className="text-[11px] font-semibold uppercase tracking-[0.16em] text-gray-500 dark:text-gray-400">Opportunity</div>
                      <div className="mt-2 text-lg font-semibold text-success-700 dark:text-success-300">
                        {formatCurrency(adjustOptimizationSavings(instance.maxops.potential_savings_yearly, profile))}
                      </div>
                      <div className="mt-1 text-xs text-gray-500 dark:text-gray-400">{instance.maxops.title || 'Healthy instance'}</div>
                    </div>
                  </div>
                );
              }}
          />
        </section>

        <section>
          <InstancesPerRegionPanel rows={derived.regionRows} onOpenRegion={(region) => openCollection({ regions: [region.toLowerCase()] })} />
        </section>

        <section>
          <Card className="border-gray-200 bg-white/95">
            <div className="mb-4 text-sm font-semibold text-gray-700">State distribution</div>
            <div className="h-80">
              <ResponsiveContainer width="100%" height="100%">
                <PieChart>
                  <Pie
                    data={derived.stateData}
                    dataKey="count"
                    nameKey="name"
                    innerRadius={64}
                    outerRadius={102}
                    onClick={(entry: { name?: string }) => {
                      if (entry?.name) {
                        openCollection({ states: [entry.name.toLowerCase()] });
                      }
                    }}
                  >
                    {derived.stateData.map((entry, index) => (
                      <Cell
                        key={entry.name}
                        fill={STATE_DISTRIBUTION_COLORS[entry.name as keyof typeof STATE_DISTRIBUTION_COLORS] || CHART_COLORS[index % CHART_COLORS.length]}
                      />
                    ))}
                  </Pie>
                  <Tooltip />
                  <Legend />
                </PieChart>
              </ResponsiveContainer>
            </div>
          </Card>
        </section>

        <section className="grid gap-6 xl:grid-cols-2">
          <HexCloud
            title="Instances with low CPU utilization"
            instances={derived.lowCpu}
            tone="underutilized"
            metric="cpu"
            onOpenCollection={() => openCollection({ lowCpu: true })}
            onOpenInstance={openInstanceDetails}
          />
          <HexCloud
            title="Instances with low memory utilization"
            instances={derived.lowMemory}
            tone="memory-levels"
            metric="memory"
            onOpenCollection={() => openCollection({ lowMemory: true })}
            onOpenInstance={openInstanceDetails}
          />
        </section>

        <section className="grid gap-6 xl:grid-cols-[1.25fr_0.8fr_0.8fr_0.8fr]">
          <Card className="border-gray-200 bg-white/95">
            <div className="mb-4 text-sm font-semibold text-gray-700">Instance types</div>
            <div className="grid gap-4 lg:grid-cols-[220px_1fr]">
              <div className="h-56">
                <ResponsiveContainer width="100%" height="100%">
                  <PieChart>
                    <Pie
                      data={donutData}
                      dataKey="value"
                      nameKey="name"
                      innerRadius={54}
                      outerRadius={78}
                      paddingAngle={2}
                      onClick={(entry: { name?: string }) => {
                        if (entry?.name) {
                          openCollection({ instanceTypes: [entry.name.toLowerCase()] });
                        }
                      }}
                    >
                      {donutData.map((entry, index) => (
                        <Cell key={entry.name} fill={CHART_COLORS[index % CHART_COLORS.length]} />
                      ))}
                    </Pie>
                    <Tooltip />
                  </PieChart>
                </ResponsiveContainer>
              </div>
              <div className="space-y-3">
                {derived.topInstanceTypes.slice(0, 6).map((row, index) => (
                  <button
                    key={row.instanceType}
                    type="button"
                    onClick={() => openCollection({ instanceTypes: [row.instanceType.toLowerCase()] })}
                    className="flex w-full items-center justify-between rounded-2xl bg-gray-50 px-4 py-3 text-left transition hover:bg-warning-50"
                  >
                    <div className="flex items-center gap-3">
                      <span className="h-3 w-3 rounded-full" style={{ backgroundColor: CHART_COLORS[index % CHART_COLORS.length] }} />
                      <span className="font-medium text-gray-700">{row.instanceType}</span>
                    </div>
                    <div className="text-sm font-semibold text-gray-900">{row.count}</div>
                  </button>
                ))}
              </div>
            </div>
          </Card>

          <GaugeCard label="Average CPU utilization" value={derived.averageCpu} onClick={() => openCollection({ sortBy: 'cpu' })} />
          <GaugeCard label="Average memory utilization" value={derived.averageMemory} tone={STATUS_COLORS.warning} onClick={() => openCollection({ sortBy: 'memory' })} />
          <MetricCard title="Average received bytes" value={`${derived.averageReceived.toFixed(0)} MB`} subtitle="Across visible EC2 instances" onClick={() => openCollection({ sortBy: 'received' })} />
        </section>

        <section className="grid gap-6 xl:grid-cols-[1.15fr_0.85fr_0.85fr_0.85fr]">
          <Card className="border-gray-200 bg-white/95">
            <div className="mb-4 flex items-center justify-between">
              <div>
                <div className="text-sm font-semibold text-gray-700">Average utilization by instance type</div>
                <div className="text-xs text-gray-500">Current snapshot values only.</div>
              </div>
            </div>
            <div className="overflow-x-auto">
              <table className="min-w-full text-sm">
                <thead>
                  <tr className="border-b border-gray-200 text-left text-xs uppercase tracking-[0.16em] text-gray-500">
                    <th className="pb-3 pr-4">Instance Type</th>
                    <th className="pb-3 pr-4">Count</th>
                    <th className="pb-3 pr-4">CPU</th>
                    <th className="pb-3 pr-4">Memory</th>
                    <th className="pb-3">Sent MB</th>
                  </tr>
                </thead>
                <tbody>
                  {derived.topInstanceTypes.slice(0, 8).map((row) => (
                    <tr
                      key={row.instanceType}
                      className="cursor-pointer border-b border-gray-100 text-gray-700 transition hover:bg-warning-50"
                      onClick={() => openCollection({ instanceTypes: [row.instanceType.toLowerCase()] })}
                    >
                      <td className="py-3 pr-4 font-medium">{row.instanceType}</td>
                      <td className="py-3 pr-4">{row.count}</td>
                      <td className="py-3 pr-4">{formatPercent(row.averageCpu)}</td>
                      <td className="py-3 pr-4">{formatPercent(row.averageMemory)}</td>
                      <td className="py-3">{row.averageSent.toFixed(0)}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </Card>
          <MetricCard title="Actionable instances" value={String(adjustOptimizationCount(derived.actionable, profile))} subtitle="Instances with active findings" onClick={() => openCollection({ actionable: true })} />
          <MetricCard title="Average sent bytes" value={`${derived.averageSent.toFixed(0)} MB`} subtitle="Current fleet mean" onClick={() => openCollection({ sortBy: 'sent' })} />
          <MetricCard title="Monthly cost estimate" value={formatCurrency(filteredInstances.reduce((sum, instance) => sum + Number(instance.metadata.monthly_cost_estimate || 0), 0))} subtitle="Visible EC2 scope" onClick={() => openCollection({ sortBy: 'monthlyCost' })} />
        </section>

        <section className="grid gap-6 xl:grid-cols-2">
          <Card className="border-gray-200 bg-white/95">
            <div className="mb-4 text-sm font-semibold text-gray-700">CPU vs memory by instance type</div>
            <div className="h-80">
              <ResponsiveContainer width="100%" height="100%">
                <BarChart
                  data={instanceTypeBars}
                  onClick={(event) => {
                    const activeLabel = (event as { activeLabel?: string } | undefined)?.activeLabel;
                    if (activeLabel) {
                      openCollection({ instanceTypes: [activeLabel.toLowerCase()] });
                    }
                  }}
                >
                  <CartesianGrid stroke={chartGridColor(theme)} strokeDasharray="3 3" />
                  <XAxis dataKey="name" tick={{ fill: chartTickColor(theme), fontSize: 12 }} />
                  <YAxis tick={{ fill: chartTickColor(theme), fontSize: 12 }} domain={[0, 100]} />
                  <Tooltip content={<ChartTooltip />} />
                  <Legend />
                  <Bar dataKey="CPU" fill={CHART_COLORS[0]} radius={[6, 6, 0, 0]} />
                  <Bar dataKey="Memory" fill={CHART_COLORS[1]} radius={[6, 6, 0, 0]} />
                </BarChart>
              </ResponsiveContainer>
            </div>
          </Card>

          <Card className="border-gray-200 bg-white/95">
            <div className="mb-4 text-sm font-semibold text-gray-700">Network throughput by instance type</div>
            <div className="h-80">
              <ResponsiveContainer width="100%" height="100%">
                <BarChart
                  data={networkBars}
                  onClick={(event) => {
                    const activeLabel = (event as { activeLabel?: string } | undefined)?.activeLabel;
                    if (activeLabel) {
                      openCollection({ instanceTypes: [activeLabel.toLowerCase()] });
                    }
                  }}
                >
                  <CartesianGrid stroke={chartGridColor(theme)} strokeDasharray="3 3" />
                  <XAxis dataKey="name" tick={{ fill: chartTickColor(theme), fontSize: 12 }} />
                  <YAxis tick={{ fill: chartTickColor(theme), fontSize: 12 }} />
                  <Tooltip content={<ChartTooltip />} />
                  <Legend />
                  <Bar dataKey="Received" fill={CHART_COLORS[4]} radius={[6, 6, 0, 0]} />
                  <Bar dataKey="Sent" fill={CHART_COLORS[5]} radius={[6, 6, 0, 0]} />
                </BarChart>
              </ResponsiveContainer>
            </div>
          </Card>

          <Card className="border-gray-200 bg-white/95">
            <div className="mb-4 text-sm font-semibold text-gray-700">Disk footprint by instance type</div>
            <div className="h-80">
              <ResponsiveContainer width="100%" height="100%">
                <BarChart
                  data={diskBars}
                  onClick={(event) => {
                    const activeLabel = (event as { activeLabel?: string } | undefined)?.activeLabel;
                    if (activeLabel) {
                      openCollection({ instanceTypes: [activeLabel.toLowerCase()] });
                    }
                  }}
                >
                  <CartesianGrid stroke={chartGridColor(theme)} strokeDasharray="3 3" />
                  <XAxis dataKey="name" tick={{ fill: chartTickColor(theme), fontSize: 12 }} />
                  <YAxis tick={{ fill: chartTickColor(theme), fontSize: 12 }} />
                  <Tooltip content={<ChartTooltip />} />
                  <Legend />
                  <Bar dataKey="Used" fill={CHART_COLORS[1]} radius={[6, 6, 0, 0]} />
                  <Bar dataKey="Available" fill={CHART_COLORS[0]} radius={[6, 6, 0, 0]} />
                </BarChart>
              </ResponsiveContainer>
            </div>
          </Card>

          <Card className="border-gray-200 bg-white/95">
            <div className="mb-4 text-sm font-semibold text-gray-700">Current fleet distribution</div>
            <div className="h-80">
              <ResponsiveContainer width="100%" height="100%">
                <BarChart
                  data={derived.environments}
                  onClick={(event) => {
                    const key = (event as { activePayload?: Array<{ payload?: { key?: string } }> } | undefined)?.activePayload?.[0]?.payload?.key;
                    if (key) {
                      openCollection({ environments: [key] });
                    }
                  }}
                >
                  <CartesianGrid stroke={chartGridColor(theme)} strokeDasharray="3 3" />
                  <XAxis dataKey="name" tick={{ fill: chartTickColor(theme), fontSize: 12 }} />
                  <YAxis tick={{ fill: chartTickColor(theme), fontSize: 12 }} />
                  <Tooltip content={<ChartTooltip />} />
                  <Bar dataKey="count" fill={CHART_COLORS[1]} radius={[6, 6, 0, 0]} />
                </BarChart>
              </ResponsiveContainer>
            </div>
          </Card>

          <Card className="border-gray-200 bg-white/95">
            <div className="mb-4 text-sm font-semibold text-gray-700">CPU vs memory per instance</div>
            <div className="h-80">
              <ResponsiveContainer width="100%" height="100%">
                <ScatterChart>
                  <CartesianGrid stroke={chartGridColor(theme)} strokeDasharray="3 3" />
                  <XAxis type="number" dataKey="x" name="CPU" unit="%" tick={{ fill: chartTickColor(theme), fontSize: 12 }} />
                  <YAxis type="number" dataKey="y" name="Memory" unit="%" tick={{ fill: chartTickColor(theme), fontSize: 12 }} />
                  <Tooltip cursor={{ strokeDasharray: '3 3' }} content={<ChartTooltip />} />
                  <Scatter
                    data={derived.scatterData}
                    fill={CHART_COLORS[0]}
                    shape={(props: any) => (
                      <circle
                        cx={props.cx}
                        cy={props.cy}
                        r={6}
                        fill={CHART_COLORS[0]}
                        className="cursor-pointer"
                        onClick={() => {
                          const instance = allInstances.find((row) => row.resource_id === props.payload?.resourceId);
                          if (instance) {
                            openInstanceDetails(instance);
                          }
                        }}
                      />
                    )}
                  />
                </ScatterChart>
              </ResponsiveContainer>
            </div>
          </Card>

          <Card className="border-gray-200 bg-white/95">
            <div className="mb-4 text-sm font-semibold text-gray-700">Findings breakdown</div>
            <div className="h-80">
              <ResponsiveContainer width="100%" height="100%">
                <BarChart
                  data={derived.findings}
                  onClick={(event) => {
                    const key = (event as { activePayload?: Array<{ payload?: { key?: string } }> } | undefined)?.activePayload?.[0]?.payload?.key;
                    if (key) {
                      openCollection({ findings: [String(key).toLowerCase()] });
                    }
                  }}
                >
                  <CartesianGrid stroke={chartGridColor(theme)} strokeDasharray="3 3" />
                  <XAxis dataKey="name" tick={{ fill: chartTickColor(theme), fontSize: 12 }} />
                  <YAxis tick={{ fill: chartTickColor(theme), fontSize: 12 }} />
                  <Tooltip content={<ChartTooltip />} />
                  <Bar dataKey="count" radius={[6, 6, 0, 0]}>
                    {derived.findings.map((entry, index) => (
                      <Cell key={entry.name} fill={CHART_COLORS[index % CHART_COLORS.length]} />
                    ))}
                  </Bar>
                </BarChart>
              </ResponsiveContainer>
            </div>
          </Card>
        </section>

      </div>
    </Layout>
  );
};
