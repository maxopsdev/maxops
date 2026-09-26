import React, { useEffect, useState } from 'react';
import { useNavigate, useParams, useSearchParams } from 'react-router-dom';
import { useMutation, useQuery } from 'react-query';
import {
  Area,
  CartesianGrid,
  ComposedChart,
  Legend,
  Line,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from 'recharts';
import {
  AlertTriangle,
  ArrowLeft,
  CheckCircle2,
  ChevronDown,
  Cpu,
  Database,
  Gauge,
  HardDrive,
  Play,
} from 'lucide-react';

import { Button } from '@/components/common/Button';
import { Card } from '@/components/common/Card';
import { ResourcePageFilters } from '@/components/common/ResourcePageFilters';
import { Layout } from '@/components/layout/Layout';
import { ResourceTypeIcon } from '@/components/icons/ResourceTypeIcon';
import {
  rightsizerApi,
  type RightsizerDetailResponse,
  type RightsizerResourceSummary,
  type RightsizerResourceType,
} from '@/services/rightsizer';
import { CHART_COLORS, chartGridColor, chartTickColor } from '@/styles/chartColors';
import { useTheme } from '@/contexts/ThemeContext';

const RESOURCE_TYPES: RightsizerResourceType[] = ['ec2', 'asg', 'ecs', 'rds', 's3'];

const formatCurrency = (value: number) =>
  new Intl.NumberFormat('en-US', {
    style: 'currency',
    currency: 'USD',
    maximumFractionDigits: 0,
  }).format(value);

const formatPercent = (value: number | null | undefined) =>
  value === null || value === undefined ? 'n/a' : `${(value * 100).toFixed(0)}%`;

const formatMetricPercent = (value: number | null | undefined) =>
  value === null || value === undefined ? 'n/a' : `${Number(value).toFixed(1)}%`;

const formatTrendValue = (value: number | null | undefined, unit = 'Percent') => {
  if (value === null || value === undefined || !Number.isFinite(Number(value))) {
    return 'n/a';
  }
  if (unit.toLowerCase() === 'percent') {
    return `${Number(value).toFixed(1)}%`;
  }
  return `${new Intl.NumberFormat('en-US', { maximumFractionDigits: 0 }).format(Number(value))} ${unit}`;
};

const parseTimestamp = (value: unknown) => {
  const time = new Date(String(value)).getTime();
  return Number.isFinite(time) ? time : 0;
};

const formatTrendDate = (value: unknown) => {
  const time = parseTimestamp(value);
  if (!time) {
    return '';
  }
  return new Date(time).toLocaleDateString('en-US', {
    month: 'short',
    day: 'numeric',
    timeZone: 'UTC',
  });
};

const TREND_VALUE_KEYS = ['maximum_on_target', 'p99_on_target', 'p95_on_target', 'maximum'];

const buildTrendYAxis = (points: Array<Record<string, any>>) => {
  const maxValue = points.reduce((currentMax, point) => {
    const pointMax = TREND_VALUE_KEYS.reduce((maxForPoint, key) => {
      const value = Number(point[key]);
      return Number.isFinite(value) ? Math.max(maxForPoint, value) : maxForPoint;
    }, 0);
    return Math.max(currentMax, pointMax);
  }, 100);
  const step = maxValue <= 100 ? 25 : maxValue <= 200 ? 50 : maxValue <= 400 ? 100 : 200;
  const upper = Math.max(100, Math.ceil(maxValue / step) * step);
  return {
    upper,
    ticks: Array.from({ length: Math.floor(upper / step) + 1 }, (_item, index) => index * step),
  };
};

const hasTrendPoints = (points: Array<Record<string, any>> | null | undefined): points is Array<Record<string, any>> =>
  Array.isArray(points) && points.length > 0;

const pickTrendPoints = (...sources: Array<Array<Record<string, any>> | null | undefined>) => {
  const selectedPoints = sources.find(hasTrendPoints) || [];
  return selectedPoints.slice().sort((left, right) => parseTimestamp(left.timestamp) - parseTimestamp(right.timestamp));
};

const EVIDENCE_TREND_SERIES: Record<string, { label: string; color: string; detail: string }> = {
  maximum_on_target: {
    label: 'Maximum on target',
    color: CHART_COLORS[0], // teal-400 (primary)
    detail: 'Peak utilization if this workload ran on the target size',
  },
  p99_on_target: {
    label: 'p99 on target',
    color: CHART_COLORS[5], // soft violet — harmonized categorical accent
    detail: 'Near-peak utilization after the highest spikes are ignored',
  },
  p95_on_target: {
    label: 'p95 on target',
    color: CHART_COLORS[4], // muted slate-blue — harmonized neutral accent
    detail: 'Typical high utilization during busier periods',
  },
  maximum: {
    label: 'Observed maximum',
    color: CHART_COLORS[2], // rose-500 (danger)
    detail: 'Peak utilization observed on the current size',
  },
};

const statusClass = (classification: string) => {
  const value = classification.toUpperCase();
  if (value === 'ACTIONABLE') {
    return 'border-success-300 bg-success-50 text-success-700 dark:border-success-900 dark:bg-success-950/40 dark:text-success-300';
  }
  if (value === 'CONDITIONAL') {
    return 'border-warning-300 bg-warning-50 text-warning-700 dark:border-warning-900 dark:bg-warning-950/40 dark:text-warning-300';
  }
  if (value === 'PREVIEW') {
    return 'border-primary-300 bg-primary-50 text-primary-700 dark:border-primary-900 dark:bg-primary-950/40 dark:text-primary-300';
  }
  if (value === 'OPPORTUNITY') {
    return 'border-violet-300 bg-violet-50 text-violet-700 dark:border-violet-900 dark:bg-violet-950/40 dark:text-violet-300';
  }
  return 'border-gray-300 bg-gray-50 text-gray-600 dark:border-gray-700 dark:bg-gray-900 dark:text-gray-300';
};

const riskClass = (risk?: string | null) => {
  const value = String(risk || '').toUpperCase();
  if (value === 'HIGH') {
    return 'border-danger-300 bg-danger-50 text-danger-700 dark:border-danger-900 dark:bg-danger-950/30 dark:text-danger-300';
  }
  if (value === 'MEDIUM') {
    return 'border-warning-300 bg-warning-50 text-warning-700 dark:border-warning-900 dark:bg-warning-950/30 dark:text-warning-300';
  }
  return 'border-success-300 bg-success-50 text-success-700 dark:border-success-900 dark:bg-success-950/30 dark:text-success-300';
};

const buildSelectOptions = (values: string[] = []) => [
  { label: 'All', value: 'all' },
  ...values.map((value) => ({ label: value, value })),
];

const PanelNote: React.FC<{ children: React.ReactNode; className?: string }> = ({ children, className = '' }) => (
  <div className={`rounded-2xl border border-gray-200 bg-gray-50 px-4 py-3 text-sm text-gray-600 dark:border-gray-800 dark:bg-gray-950 dark:text-gray-300 ${className}`}>
    {children}
  </div>
);

type RecommendationOption = {
  key: string;
  label: string;
  recommendation: Record<string, any> | null;
  isDefault: boolean;
};

const TIER_KEYS = ['conservative', 'balanced', 'aggressive'];
const TIER_LABELS: Record<string, string> = {
  conservative: 'Conservative',
  balanced: 'Balanced',
  aggressive: 'Aggressive',
};

const isRecommendationPayload = (value: unknown): value is Record<string, any> =>
  Boolean(value && typeof value === 'object' && !Array.isArray(value));

const buildRecommendationOptions = (data: RightsizerDetailResponse): RecommendationOption[] => {
  const tierOptions = TIER_KEYS.map((key) => ({
    key,
    label: TIER_LABELS[key],
    recommendation: isRecommendationPayload(data.tiers?.[key]) ? data.tiers[key] : null,
    isDefault: data.tiers?.default === key,
  }));

  if (tierOptions.some((option) => option.recommendation)) {
    return tierOptions;
  }

  if (data.recommendations?.length) {
    return data.recommendations.map((recommendation, index) => {
      const tier = recommendation.tier || recommendation.option || recommendation.name;
      return {
        key: String(tier || `option-${index + 1}`),
        label: tier ? TIER_LABELS[String(tier)] || String(tier) : `Option ${index + 1}`,
        recommendation,
        isDefault: index === 0,
      };
    });
  }

  if (data.recommendation) {
    return [
      {
        key: 'recommended',
        label: 'Recommended',
        recommendation: data.recommendation,
        isDefault: true,
      },
    ];
  }

  return [];
};

const getOptionTargetLabel = (option: RecommendationOption, resourceType: RightsizerResourceType) => {
  const recommendation = option.recommendation;
  if (!recommendation) {
    return 'No eligible option';
  }
  if (resourceType === 'asg') {
    return (
      recommendation.target_capacity ||
      [
        recommendation.target_min_size,
        recommendation.target_desired_capacity,
        recommendation.target_max_size,
      ]
        .filter((value) => value !== undefined && value !== null)
        .join('/') ||
      'n/a'
    );
  }
  if (resourceType === 'ecs') {
    return (
      recommendation.target_capacity ||
      recommendation.target_instance_type ||
      `${recommendation.target_cpu_reservation ?? 'n/a'} CPU / ${recommendation.target_memory_reservation ?? 'n/a'} MiB`
    );
  }
  if (resourceType === 'rds') {
    return (
      recommendation.target_db_instance_class ||
      recommendation.target_db_instance_status ||
      recommendation.target_capacity ||
      recommendation.label ||
      'n/a'
    );
  }
  return recommendation.target_instance_type || recommendation.target_capacity || 'n/a';
};

const getActiveRecommendationOption = (options: RecommendationOption[], selectedKey: string | null) =>
  options.find((option) => option.key === selectedKey && option.recommendation) ||
  options.find((option) => option.isDefault && option.recommendation) ||
  options.find((option) => option.recommendation) ||
  null;

export const RightsizerPage: React.FC = () => {
  const navigate = useNavigate();
  const [searchParams, setSearchParams] = useSearchParams();
  const selectedType = (searchParams.get('type') as RightsizerResourceType | null) || 'ec2';
  const safeType = RESOURCE_TYPES.includes(selectedType) ? selectedType : 'ec2';
  const [search, setSearch] = useState('');
  const [region, setRegion] = useState('all');
  const [state, setState] = useState('all');
  const [status, setStatus] = useState('all');

  const { data: typeData } = useQuery('rightsizer-resource-types', () => rightsizerApi.getResourceTypes());
  const { data, isLoading, isFetching, isError } = useQuery(
    ['rightsizer-resources', safeType, search, region, state, status],
    () =>
      rightsizerApi.getResources(safeType, {
        q: search,
        region,
        state,
        status,
    }),
    { keepPreviousData: true }
  );
  const currentData = data?.resource_type === safeType ? data : undefined;
  const isLoadingCurrentType = isLoading || (!currentData && isFetching);

  const changeType = (resourceType: RightsizerResourceType) => {
    setSearchParams({ type: resourceType });
    setRegion('all');
    setState('all');
    setStatus('all');
    setSearch('');
  };

  return (
    <Layout>
      <div className="space-y-6">
        <section className="rounded-[2rem] border border-gray-200 bg-white p-6 shadow-sm dark:border-gray-800 dark:bg-gray-900">
          <div className="flex flex-wrap items-start justify-between gap-6">
            <div>
              <div className="text-xs font-semibold uppercase tracking-[0.24em] text-primary-700 dark:text-primary-300">
                Rightsizer
              </div>
              <h1 className="mt-2 text-3xl font-semibold tracking-tight text-gray-950 dark:text-white">
                Resource Sizing
              </h1>
            </div>
            {currentData ? (
              <div className="grid grid-cols-3 gap-3">
                <div className="rounded-2xl border border-gray-200 px-4 py-3 dark:border-gray-700">
                  <div className="text-xs font-semibold uppercase tracking-[0.14em] text-gray-500">Resources</div>
                  <div className="mt-1 text-2xl font-semibold text-gray-900 dark:text-white">{currentData.summary.total_resources}</div>
                </div>
                <div className="rounded-2xl border border-gray-200 px-4 py-3 dark:border-gray-700">
                  <div className="text-xs font-semibold uppercase tracking-[0.14em] text-gray-500">Monthly</div>
                  <div className="mt-1 text-2xl font-semibold text-success-600">{formatCurrency(currentData.summary.monthly_savings)}</div>
                </div>
                <div className="rounded-2xl border border-gray-200 px-4 py-3 dark:border-gray-700">
                  <div className="text-xs font-semibold uppercase tracking-[0.14em] text-gray-500">Annual</div>
                  <div className="mt-1 text-2xl font-semibold text-success-600">{formatCurrency(currentData.summary.yearly_savings)}</div>
                </div>
              </div>
            ) : null}
          </div>
        </section>

        <div className="grid gap-4 xl:grid-cols-4">
          {(typeData?.resource_types || []).map((item) => (
            <button
              key={item.resource_type}
              type="button"
              onClick={() => changeType(item.resource_type)}
              className={`rounded-2xl border p-4 text-left transition ${
                item.resource_type === safeType
                  ? 'border-primary-300 bg-primary-50 shadow-sm dark:border-primary-800 dark:bg-primary-950/30'
                  : 'border-gray-200 bg-white hover:border-primary-200 dark:border-gray-800 dark:bg-gray-900'
              }`}
            >
              <div className="flex items-center justify-between gap-3">
                <div className="flex items-center gap-3">
                  <ResourceTypeIcon resourceType={item.resource_type} size={34} />
                  <div>
                    <div className="font-semibold text-gray-900 dark:text-white">{item.label}</div>
                    <div className="text-xs text-gray-500 dark:text-gray-400">{item.resource_count} resources</div>
                  </div>
                </div>
                {!item.detail_available ? (
                  <span className="rounded-full border border-gray-200 px-2 py-1 text-[11px] font-semibold text-gray-500 dark:border-gray-700">
                    Inventory
                  </span>
                ) : null}
              </div>
            </button>
          ))}
        </div>

        <ResourcePageFilters
          searchValue={search}
          searchPlaceholder="Search resources, types, status, or identifiers"
          onSearchChange={setSearch}
          selects={[
            {
              id: 'region',
              label: 'Region',
              value: region,
              options: buildSelectOptions(currentData?.filters.regions ?? currentData?.filters.region),
              onChange: setRegion,
            },
            {
              id: 'state',
              label: 'State',
              value: state,
              options: buildSelectOptions(currentData?.filters.states ?? currentData?.filters.state),
              onChange: setState,
            },
            {
              id: 'status',
              label: 'Status',
              value: status,
              options: buildSelectOptions(currentData?.filters.status),
              onChange: setStatus,
            },
          ]}
          onReset={() => {
            setSearch('');
            setRegion('all');
            setState('all');
            setStatus('all');
          }}
          resetDisabled={!search && region === 'all' && state === 'all' && status === 'all'}
        />

        <Card className="overflow-hidden border-gray-200 bg-white p-0 dark:border-gray-800 dark:bg-gray-900">
          {isLoadingCurrentType ? (
            <div className="p-8 text-sm text-gray-500">Loading rightsizer resources...</div>
          ) : isError ? (
            <div className="p-8 text-sm text-danger-600">Unable to load rightsizer resources.</div>
          ) : (
            <div className="overflow-x-auto">
              <table className="min-w-full divide-y divide-gray-200 text-sm dark:divide-gray-800">
                <thead className="bg-gray-50 text-xs uppercase tracking-[0.14em] text-gray-500 dark:bg-gray-950 dark:text-gray-400">
                  <tr>
                    <th className="px-5 py-3 text-left">Resource</th>
                    <th className="px-5 py-3 text-left">Current</th>
                    <th className="px-5 py-3 text-left">Target</th>
                    <th className="px-5 py-3 text-left">Status</th>
                    <th className="px-5 py-3 text-right">Savings</th>
                    <th className="px-5 py-3 text-left">Risk</th>
                  </tr>
                </thead>
                <tbody className="divide-y divide-gray-100 dark:divide-gray-800">
                  {(currentData?.resources || []).map((resource) => (
                    <RightsizerResourceRow
                      key={`${resource.resource_type}-${resource.inventory_id}`}
                      resource={resource}
                      onOpen={() =>
                        resource.detail_available
                          ? navigate(`/rightsizer/${resource.resource_type}/${resource.inventory_id}`)
                          : undefined
                      }
                    />
                  ))}
                  {(currentData?.resources || []).length === 0 ? (
                    <tr>
                      <td colSpan={6} className="px-5 py-8 text-center text-sm text-gray-500">
                        No resources match the current filters.
                      </td>
                    </tr>
                  ) : null}
                </tbody>
              </table>
            </div>
          )}
        </Card>
      </div>
    </Layout>
  );
};

const RightsizerResourceRow: React.FC<{
  resource: RightsizerResourceSummary;
  onOpen: () => void | undefined;
}> = ({ resource, onOpen }) => {
  const clickable = resource.detail_available;
  return (
    <tr
      className={clickable ? 'cursor-pointer transition hover:bg-primary-50/60 dark:hover:bg-gray-800/70' : ''}
      onClick={clickable ? onOpen : undefined}
    >
      <td className="px-5 py-4">
        <div className="font-semibold text-gray-900 dark:text-white">{resource.resource_name || resource.resource_id}</div>
        <div className="mt-1 text-xs text-gray-500">
          {resource.resource_id} · {resource.region || 'unknown'} · {resource.state || 'unknown'}
        </div>
      </td>
      <td className="px-5 py-4 font-medium text-gray-700 dark:text-gray-200">{resource.current_type || 'n/a'}</td>
      <td className="px-5 py-4 font-medium text-gray-700 dark:text-gray-200">{resource.target_type || 'n/a'}</td>
      <td className="px-5 py-4">
        <span className={`inline-flex rounded-full border px-3 py-1 text-xs font-semibold ${statusClass(resource.classification)}`}>
          {resource.status}
        </span>
      </td>
      <td className="px-5 py-4 text-right">
        <div className="font-semibold text-success-600">{formatCurrency(resource.monthly_savings)}/mo</div>
        <div className="text-xs text-gray-500">{formatCurrency(resource.yearly_savings)}/yr</div>
      </td>
      <td className="px-5 py-4">
        <span className={`inline-flex rounded-full border px-3 py-1 text-xs font-semibold ${riskClass(resource.risk_overall)}`}>
          {resource.risk_overall || 'n/a'}
        </span>
      </td>
    </tr>
  );
};

export const RightsizerDetailPage: React.FC = () => {
  const navigate = useNavigate();
  const { resourceType, inventoryId } = useParams<{
    resourceType: RightsizerResourceType;
    inventoryId: string;
  }>();
  const [caveatsOpen, setCaveatsOpen] = useState(false);
  const [applyMessage, setApplyMessage] = useState<string | null>(null);
  const [selectedOptionKey, setSelectedOptionKey] = useState<string | null>(null);
  const safeType = RESOURCE_TYPES.includes(resourceType as RightsizerResourceType)
    ? (resourceType as RightsizerResourceType)
    : 'ec2';
  const numericInventoryId = Number(inventoryId);

  const { data, isLoading, isError } = useQuery(
    ['rightsizer-detail', safeType, numericInventoryId],
    () => rightsizerApi.getDetail(safeType, numericInventoryId),
    { enabled: Number.isFinite(numericInventoryId), refetchOnMount: 'always' }
  );

  useEffect(() => {
    setSelectedOptionKey(null);
  }, [safeType, numericInventoryId]);

  const applyMutation = useMutation(
    async ({
      detail,
      recommendation,
      optionKey,
    }: {
      detail: RightsizerDetailResponse;
      recommendation: Record<string, any> | null;
      optionKey: string | null;
    }) => {
      const target = recommendation?.target_instance_type;
      if (!target || !detail.resource.account_id || !detail.resource.region) {
        throw new Error('Missing target instance, account, or region.');
      }
      return rightsizerApi.applyRecommendation(detail.inventory_id, {
        target_instance_type: target,
        recommendation_option: optionKey,
        account_id: detail.resource.account_id,
        region: detail.resource.region,
        resource_id: detail.resource.resource_id,
      });
    }
  );

  const handleApply = async () => {
    if (!data) {
      return;
    }
    const options = buildRecommendationOptions(data);
    const activeOption = getActiveRecommendationOption(options, selectedOptionKey);
    setApplyMessage(null);
    try {
      const result = await applyMutation.mutateAsync({
        detail: data,
        recommendation: activeOption?.recommendation || data.recommendation || null,
        optionKey: activeOption?.key || null,
      });
      setApplyMessage(result.message || 'Resize request submitted.');
    } catch (error: any) {
      setApplyMessage(error?.response?.data?.detail || error?.message || 'Unable to apply recommendation.');
    }
  };

  if (isLoading) {
    return (
      <Layout>
        <div className="p-8 text-sm text-gray-500">Loading rightsizer evidence...</div>
      </Layout>
    );
  }

  if (isError || !data) {
    return (
      <Layout>
        <Card className="border-danger-200 bg-danger-50 text-danger-700">Rightsizer evidence is unavailable for this resource.</Card>
      </Layout>
    );
  }

  const recommendationOptions = buildRecommendationOptions(data);
  const activeOption = getActiveRecommendationOption(recommendationOptions, selectedOptionKey);
  const recommendation = activeOption?.recommendation || data.recommendation;
  const target = recommendation?.target_instance || null;
  const current = data.current_instance || {};
  const isAsg = data.resource_type === 'asg';
  const isEcs = data.resource_type === 'ecs';
  const isRds = data.resource_type === 'rds';
  const chartPoints = pickTrendPoints(recommendation?.chart?.points, data.chart?.points);
  const memoryChartPoints = pickTrendPoints(recommendation?.memory_chart?.points, data.memory_chart?.points);
  const iopsChartPoints = pickTrendPoints(recommendation?.iops_chart?.points, data.iops_chart?.points);
  const trendChartKey = [
    activeOption?.key,
    recommendation?.target_instance_type,
    recommendation?.target_db_instance_class,
    recommendation?.target_db_instance_status,
    recommendation?.target_capacity,
    recommendation?.target_desired_capacity,
    recommendation?.target_cpu_reservation,
    recommendation?.target_memory_reservation,
  ]
    .filter((value) => value !== undefined && value !== null)
    .join('|');
  const trendGridClass = isRds && iopsChartPoints.length ? 'grid items-start gap-6 xl:grid-cols-3' : 'grid items-start gap-6 xl:grid-cols-2';
  const canApply = !isAsg && !isEcs && !isRds && Boolean(recommendation?.target_instance_type && data.resource.account_id && data.resource.region);
  const currentDisplay = isAsg
    ? `${current.min_size ?? 'n/a'}/${current.desired_capacity ?? 'n/a'}/${current.max_size ?? 'n/a'}`
    : isEcs
    ? `${current.cpu_reservation ?? 'n/a'} CPU / ${current.memory_reservation ?? 'n/a'} MiB`
    : isRds
    ? current.db_instance_class || data.resource.current_type
    : data.resource.current_type;
  const currentDetail = isAsg
    ? 'min / desired / max'
    : isEcs
    ? `${current.running_count ?? 'n/a'} running / ${current.desired_count ?? 'n/a'} desired`
    : isRds
    ? `${current.engine ?? 'n/a'} / ${current.storage_type ?? 'n/a'}`
    : `${current.vcpus ?? 'n/a'} vCPU / ${current.memory_gib ?? 'n/a'} GiB`;
  const targetDisplay = activeOption
    ? getOptionTargetLabel(activeOption, data.resource_type)
    : isEcs
    ? recommendation?.target_capacity || recommendation?.target_instance_type || 'No change'
    : isRds
    ? recommendation?.target_db_instance_class || recommendation?.target_db_instance_status || recommendation?.target_capacity || 'No change'
    : recommendation?.target_instance_type || 'n/a';
  const targetDetail = isAsg
    ? 'target min / desired / max'
    : isEcs
    ? 'target CPU / memory reservation'
    : isRds
    ? recommendation?.label || recommendation?.action_key || 'target DB action'
    : `${target?.vcpus ?? 'n/a'} vCPU / ${target?.memory_gib ?? 'n/a'} GiB`;

  return (
    <Layout>
      <div className="space-y-6">
        <button
          type="button"
          onClick={() => navigate(`/rightsizer?type=${data.resource_type}`)}
          className="inline-flex items-center gap-2 rounded-full border border-gray-200 bg-white px-4 py-2 text-sm font-medium text-gray-700 transition hover:bg-gray-50 dark:border-gray-700 dark:bg-gray-900 dark:text-gray-200"
        >
          <ArrowLeft size={16} />
          Rightsizer
        </button>

        <section className="grid gap-6 xl:grid-cols-[minmax(0,1fr)_320px]">
          <Card className="flex flex-col border-gray-200 bg-white dark:border-gray-800 dark:bg-gray-900">
            <div className="flex flex-1 flex-wrap items-start justify-between gap-6">
              <div>
                <div className="flex flex-wrap items-center gap-3">
                  <h1 className="text-3xl font-semibold tracking-tight text-gray-950 dark:text-white">
                    {data.resource.resource_name || data.resource.resource_id}
                  </h1>
                  <span className={`rounded-full border px-3 py-1 text-sm font-semibold ${statusClass(data.classification)}`}>
                    {data.status}
                  </span>
                </div>
                <div className="mt-2 text-sm text-gray-500">
                  {data.resource.resource_id} · {data.resource.region || 'unknown'} · {data.resource.state || 'unknown'}
                </div>
                <div className="mt-6 flex flex-wrap items-center gap-4 text-gray-900 dark:text-white">
                  <div>
                    <div className="text-xs font-semibold uppercase tracking-[0.16em] text-gray-500">Current</div>
                    <div className="mt-1 font-mono text-2xl font-semibold">{currentDisplay}</div>
                    <div className="mt-1 text-sm text-gray-500">{currentDetail}</div>
                  </div>
                  <div className="text-2xl text-gray-400">→</div>
                  <div>
                    <div className="text-xs font-semibold uppercase tracking-[0.16em] text-gray-500">Target</div>
                    <div className="mt-1 font-mono text-2xl font-semibold">{targetDisplay}</div>
                    <div className="mt-1 text-sm text-gray-500">{targetDetail}</div>
                  </div>
                </div>
              </div>

              <div className="text-right">
                <div className="text-4xl font-semibold text-success-600">
                  {formatCurrency(Number(recommendation?.monthly_savings || 0))}/mo
                </div>
                <div className="mt-1 text-sm text-gray-500">
                  {formatCurrency(Number(recommendation?.yearly_savings || 0))}/yr
                </div>
                <Button
                  className="mt-5 inline-flex gap-2"
                  onClick={() => void handleApply()}
                  disabled={!canApply}
                  isLoading={applyMutation.isLoading}
                >
                  <Play size={16} />
                  Apply
                </Button>
                {applyMessage ? <div className="mt-3 max-w-xs text-sm text-gray-600 dark:text-gray-300">{applyMessage}</div> : null}
              </div>
            </div>
            <PanelNote className="mt-5">
              Compares the current size with the recommended target, including expected savings and whether the recommendation is ready to apply or needs review.
            </PanelNote>
          </Card>

          <Card className="flex flex-col border-gray-200 bg-white dark:border-gray-800 dark:bg-gray-900">
            <div className="text-sm font-semibold text-gray-700 dark:text-gray-200">Policy Bands</div>
            <div className="mt-4 flex-1 space-y-3 text-sm text-gray-600 dark:text-gray-300">
              <PolicyBand label="Network" medium={data.policy.network_medium_ratio} high={data.policy.network_high_ratio} />
              <PolicyBand label="EBS" medium={data.policy.ebs_medium_ratio} high={data.policy.ebs_high_ratio} />
            </div>
            <PanelNote className="mt-4">
              Shows the medium and high review thresholds used for network and EBS capacity risk.
            </PanelNote>
          </Card>
        </section>

        <RecommendationOptionsPanel
          options={recommendationOptions}
          selectedKey={activeOption?.key || null}
          onSelect={setSelectedOptionKey}
          resourceType={data.resource_type}
        />

        <section className={trendGridClass}>
          <EvidenceTrendCard
            title="CPU Evidence Trend"
            description="CPU utilization projected onto the recommended target size."
            note="Shows how the current CPU trend would look on the recommended target size, using maximum, p99, and p95 utilization."
            chartKey={`cpu|${trendChartKey}`}
            chartPoints={chartPoints}
            unit={data.chart?.unit}
          />
          <EvidenceTrendCard
            title="Memory Evidence Trend"
            description="Memory utilization projected onto the recommended target size."
            note="Shows how the current memory trend would look on the recommended target size, using maximum, p99, and p95 utilization."
            chartKey={`memory|${trendChartKey}`}
            chartPoints={memoryChartPoints}
            unit={data.memory_chart?.unit}
          />
          {isRds && iopsChartPoints.length ? (
            <EvidenceTrendCard
              title="IOPS Evidence Trend"
              description="Read and write IOPS projected onto the selected RDS option."
              note="Shows how combined read/write IOPS changes under the selected RDS recommendation option."
              chartKey={`iops|${trendChartKey}`}
              chartPoints={iopsChartPoints}
              unit={data.iops_chart?.unit}
            />
          ) : null}
        </section>

        <section>
          <WarningsPanel warnings={data.warnings} deferred={data.deferred_reason_codes} blocking={data.blocking_reasons} />
        </section>

        <section className="grid gap-6 xl:grid-cols-2">
          <RiskGrid risk={recommendation?.risk_assessment || data.risk_assessment} />
          <LookbackSummary rows={data.lookback_summary} />
        </section>

        <section className="grid gap-6 xl:grid-cols-2">
          <UtilizationBars rows={data.utilization_bars} />
          <TelemetryDisclosure telemetry={data.telemetry_summary} />
        </section>

        <section>
          <Card className="border-gray-200 bg-white p-0 dark:border-gray-800 dark:bg-gray-900">
            <button
              type="button"
              onClick={() => setCaveatsOpen((currentValue) => !currentValue)}
              className="flex w-full items-center justify-between px-5 py-4 text-left"
            >
              <span className="block text-sm font-semibold uppercase tracking-[0.18em] text-gray-700 dark:text-gray-200">
                Coverage & Caveats
              </span>
              <ChevronDown size={16} className={caveatsOpen ? 'transition' : '-rotate-90 transition'} />
            </button>
            {caveatsOpen ? (
              <pre className="max-h-80 overflow-auto border-t border-gray-200 bg-gray-50 p-5 text-xs text-gray-700 dark:border-gray-800 dark:bg-gray-950 dark:text-gray-200">
                {JSON.stringify(data.coverage_caveats, null, 2)}
              </pre>
            ) : null}
            <div className="px-5 pb-5">
              <PanelNote>
                Shows what assumptions, policy limits, and data coverage may affect confidence in this recommendation.
              </PanelNote>
            </div>
          </Card>
        </section>
      </div>
    </Layout>
  );
};

const PolicyBand: React.FC<{ label: string; medium: number; high: number }> = ({ label, medium, high }) => (
  <div className="rounded-2xl border border-gray-200 p-3 dark:border-gray-700">
    <div className="flex items-center justify-between">
      <span className="font-medium">{label}</span>
      <span className="text-xs text-gray-500">
        {formatPercent(medium)} / {formatPercent(high)}
      </span>
    </div>
    <div className="mt-3 h-2 overflow-hidden rounded-full bg-gray-100 dark:bg-gray-800">
      <div className="h-full rounded-full bg-gradient-to-r from-success-400 via-warning-400 to-danger-500" />
    </div>
  </div>
);

const RecommendationOptionsPanel: React.FC<{
  options: RecommendationOption[];
  selectedKey: string | null;
  onSelect: (key: string) => void;
  resourceType: RightsizerResourceType;
}> = ({ options, selectedKey, onSelect, resourceType }) => {
  if (!options.length) {
    return null;
  }

  return (
    <Card className="border-gray-200 bg-white dark:border-gray-800 dark:bg-gray-900">
      <div className="mb-4 flex flex-wrap items-center justify-between gap-3">
        <div className="text-sm font-semibold text-gray-700 dark:text-gray-200">Recommendation Options</div>
        <div className="text-xs font-semibold uppercase tracking-[0.14em] text-gray-500">
          {options.length} option{options.length === 1 ? '' : 's'}
        </div>
      </div>
      <div className="grid gap-3 lg:grid-cols-3">
        {options.map((option) => {
          const recommendation = option.recommendation;
          const selected = option.key === selectedKey;
          const risk = recommendation?.risk_assessment?.overall;
          const projectedCpu = recommendation?.projected_cpu_util ?? recommendation?.projected_util;
          const projectedMemory = recommendation?.projected_memory_util;
          const reasonCodes = recommendation?.reason_codes || [];
          return (
            <button
              key={option.key}
              type="button"
              onClick={() => recommendation && onSelect(option.key)}
              disabled={!recommendation}
              aria-pressed={selected}
              className={`min-h-[190px] rounded-2xl border p-4 text-left transition ${
                selected
                  ? 'border-primary-400 bg-white shadow-sm ring-1 ring-primary-200 dark:border-primary-700 dark:bg-gray-900 dark:ring-primary-900/60'
                  : recommendation
                  ? 'border-gray-200 bg-white hover:border-primary-200 dark:border-gray-700 dark:bg-gray-900'
                  : 'border-gray-200 bg-gray-50 text-gray-400 dark:border-gray-800 dark:bg-gray-950 dark:text-gray-500'
              }`}
            >
              <div className="flex items-start justify-between gap-3">
                <div>
                  <div className="font-semibold text-gray-900 dark:text-white">{option.label}</div>
                  <div className="mt-1 font-mono text-lg font-semibold text-gray-900 dark:text-white">
                    {getOptionTargetLabel(option, resourceType)}
                  </div>
                </div>
                {option.isDefault ? (
                  <span className="rounded-full border border-gray-200 bg-white px-2 py-1 text-[11px] font-semibold uppercase tracking-[0.12em] text-gray-500 dark:border-gray-700 dark:bg-gray-900">
                    Default
                  </span>
                ) : null}
              </div>

              {recommendation ? (
                <div className="mt-4 grid gap-3 text-sm sm:grid-cols-2">
                  <div>
                    <div className="text-xs font-semibold uppercase tracking-[0.14em] text-gray-500">Monthly</div>
                    <div className="mt-1 font-semibold text-success-600">
                      {formatCurrency(Number(recommendation.monthly_savings || 0))}
                    </div>
                  </div>
                  <div>
                    <div className="text-xs font-semibold uppercase tracking-[0.14em] text-gray-500">Annual</div>
                    <div className="mt-1 font-semibold text-success-600">
                      {formatCurrency(Number(recommendation.yearly_savings || 0))}
                    </div>
                  </div>
                  <div>
                    <div className="text-xs font-semibold uppercase tracking-[0.14em] text-gray-500">CPU</div>
                    <div className="mt-1 font-semibold text-gray-800 dark:text-gray-100">
                      {formatPercent(projectedCpu)}
                    </div>
                  </div>
                  <div>
                    <div className="text-xs font-semibold uppercase tracking-[0.14em] text-gray-500">Memory</div>
                    <div className="mt-1 font-semibold text-gray-800 dark:text-gray-100">
                      {formatPercent(projectedMemory)}
                    </div>
                  </div>
                </div>
              ) : (
                <div className="mt-4 text-sm text-gray-500">Unavailable for this resource.</div>
              )}

              <div className="mt-4 flex flex-wrap items-center gap-2">
                <span className={`inline-flex rounded-full border px-3 py-1 text-xs font-semibold ${riskClass(risk)}`}>
                  {risk || 'n/a'}
                </span>
                {reasonCodes.slice(0, 2).map((code: string) => (
                  <span
                    key={code}
                    className="max-w-full truncate rounded-full border border-gray-200 px-2 py-1 text-[11px] font-semibold text-gray-500 dark:border-gray-700"
                  >
                    {code}
                  </span>
                ))}
              </div>
            </button>
          );
        })}
      </div>
    </Card>
  );
};

const EvidenceTrendCard: React.FC<{
  title: string;
  description: string;
  note: string;
  chartKey: string;
  chartPoints: Array<Record<string, any>>;
  unit?: string | null;
}> = ({ title, description, note, chartKey, chartPoints, unit = 'Percent' }) => {
  const { theme } = useTheme();
  const trendYAxis = buildTrendYAxis(chartPoints);
  const chartUnit = unit || 'Percent';
  return (
    <Card className="flex h-full flex-col border-gray-200 bg-white dark:border-gray-800 dark:bg-gray-900">
      <div className="mb-4 flex items-start justify-between gap-4">
        <div>
          <div className="text-sm font-semibold text-gray-700 dark:text-gray-200">{title}</div>
          <div className="mt-1 text-sm text-gray-500">{description}</div>
        </div>
        <Gauge size={20} className="text-primary-600" />
      </div>
      <div className="h-[360px] w-full flex-none">
        {chartPoints.length ? (
          <ResponsiveContainer width="100%" height="100%">
            <ComposedChart key={chartKey} data={chartPoints} margin={{ top: 18, right: 18, left: 8, bottom: 0 }}>
              <CartesianGrid stroke={chartGridColor(theme)} strokeDasharray="3 3" opacity={0.45} />
              <XAxis
                dataKey="timestamp"
                tickFormatter={formatTrendDate}
                tick={{ fill: chartTickColor(theme) }}
                tickLine={false}
                axisLine={false}
                minTickGap={28}
              />
              <YAxis
                width={chartUnit.toLowerCase() === 'percent' ? 56 : 78}
                domain={[0, trendYAxis.upper]}
                ticks={trendYAxis.ticks}
                tickFormatter={(value) => formatTrendValue(Number(value), chartUnit)}
                tick={{ fill: chartTickColor(theme) }}
                tickLine={false}
                axisLine={false}
              />
              <Tooltip content={<EvidenceTooltip unit={chartUnit} />} />
              <Legend
                align="right"
                verticalAlign="top"
                iconType="plainline"
                formatter={(value) => (
                  <span className="text-xs font-medium text-gray-600 dark:text-gray-300">
                    {EVIDENCE_TREND_SERIES[String(value)]?.label || value}
                  </span>
                )}
              />
              <Area
                type="monotone"
                dataKey="maximum_on_target"
                name="maximum_on_target"
                fill={EVIDENCE_TREND_SERIES.maximum_on_target.color}
                fillOpacity={0.15}
                stroke={EVIDENCE_TREND_SERIES.maximum_on_target.color}
                strokeWidth={2}
                dot={false}
              />
              <Line
                type="monotone"
                dataKey="p99_on_target"
                name="p99_on_target"
                stroke={EVIDENCE_TREND_SERIES.p99_on_target.color}
                strokeWidth={2}
                dot={false}
              />
              <Line
                type="monotone"
                dataKey="p95_on_target"
                name="p95_on_target"
                stroke={EVIDENCE_TREND_SERIES.p95_on_target.color}
                strokeWidth={2}
                dot={false}
                strokeDasharray="4 4"
              />
              <Line
                type="monotone"
                dataKey="maximum"
                name="maximum"
                stroke={EVIDENCE_TREND_SERIES.maximum.color}
                strokeWidth={2}
                dot={false}
                strokeDasharray="2 5"
              />
            </ComposedChart>
          </ResponsiveContainer>
        ) : (
          <div className="flex h-full items-center justify-center rounded-2xl border border-dashed border-gray-200 bg-gray-50 text-sm text-gray-500 dark:border-gray-800 dark:bg-gray-950 dark:text-gray-400">
            Trend data is unavailable for the selected option.
          </div>
        )}
      </div>
      <PanelNote className="mt-4">{note}</PanelNote>
    </Card>
  );
};

const EvidenceTooltip: React.FC<any> = ({ active, payload, label, unit = 'Percent' }) => {
  if (!active || !payload?.length) {
    return null;
  }
  const point = payload[0].payload;
  const rows = ['maximum_on_target', 'p99_on_target', 'p95_on_target', 'maximum'];
  return (
    <div className="max-w-sm rounded-2xl border border-gray-200 bg-white p-3 text-sm shadow-xl dark:border-gray-700 dark:bg-gray-900">
      <div className="text-xs font-semibold uppercase tracking-[0.14em] text-gray-500">
        {label ? formatTrendDate(label) : 'Sample'}
      </div>
      <div className="mt-3 space-y-3 text-gray-700 dark:text-gray-200">
        {rows.map((key) => {
          const series = EVIDENCE_TREND_SERIES[key];
          return (
            <div key={key} className="flex items-start gap-2">
              <span
                className="mt-1.5 h-2.5 w-2.5 flex-none rounded-full"
                style={{ backgroundColor: series.color }}
                aria-hidden="true"
              />
              <div>
                <div className="flex items-center gap-2">
                  <span className="font-semibold">{series.label}</span>
                  <span className="font-mono text-xs text-gray-500">{formatTrendValue(point[key], unit)}</span>
                </div>
                <div className="mt-0.5 text-xs text-gray-500 dark:text-gray-400">{series.detail}</div>
              </div>
            </div>
          );
        })}
      </div>
    </div>
  );
};

const RiskGrid: React.FC<{ risk?: Record<string, any> | null }> = ({ risk }) => {
  const entries = ['telemetry', 'compute', 'memory', 'network', 'storage', 'compatibility', 'migration', 'overall'];
  return (
    <Card className="flex h-full flex-col border-gray-200 bg-white dark:border-gray-800 dark:bg-gray-900">
      <div className="mb-4 text-sm font-semibold text-gray-700 dark:text-gray-200">Risk by Dimension</div>
      <div className="grid flex-1 grid-cols-2 gap-3">
        {entries.map((key) => (
          <div key={key} className="rounded-2xl border border-gray-200 p-3 dark:border-gray-700">
            <div className="text-xs font-semibold uppercase tracking-[0.14em] text-gray-500">{key}</div>
            <span className={`mt-3 inline-flex rounded-full border px-3 py-1 text-sm font-semibold ${riskClass(risk?.[key])}`}>
              {risk?.[key] || 'n/a'}
            </span>
          </div>
        ))}
      </div>
      <PanelNote className="mt-4">
        Breaks down where review risk comes from, such as telemetry quality, compute, memory, network, storage, compatibility, or migration concerns.
      </PanelNote>
    </Card>
  );
};

const WarningsPanel: React.FC<{
  warnings: Array<{ code: string; message: string }>;
  deferred: string[];
  blocking: string[];
}> = ({ warnings, deferred, blocking }) => {
  const items = warnings.length
    ? warnings.map((warning) => ({ code: warning.code, message: warning.message, tone: 'warn' }))
    : [...deferred, ...blocking].map((code) => ({ code, message: code.replace(/_/g, ' '), tone: 'info' }));
  return (
    <Card className="flex flex-col border-gray-200 bg-white dark:border-gray-800 dark:bg-gray-900">
      <div className="mb-4 text-sm font-semibold text-gray-700 dark:text-gray-200">Warnings & Required Review</div>
      {items.length === 0 ? (
        <div className="flex flex-1 items-center gap-2 rounded-2xl border border-success-200 bg-success-50 px-4 py-3 text-sm text-success-700 dark:border-success-900 dark:bg-success-950/30 dark:text-success-300">
          <CheckCircle2 size={16} />
          No warnings.
        </div>
      ) : (
        <div className="flex-1 space-y-3">
          {items.map((item) => (
            <div key={item.code} className="rounded-2xl border border-warning-200 bg-warning-50 px-4 py-3 dark:border-warning-900 dark:bg-warning-950/30">
              <div className="flex items-start gap-3">
                <AlertTriangle size={16} className="mt-0.5 text-warning-600" />
                <div>
                  <div className="font-mono text-xs font-semibold text-gray-800 dark:text-gray-100">{item.code}</div>
                  <div className="mt-1 text-sm text-gray-600 dark:text-gray-300">{item.message}</div>
                </div>
              </div>
            </div>
          ))}
        </div>
      )}
      <PanelNote className="mt-4">
        Lists the specific reasons to review before applying, or confirms that no additional review warning was found.
      </PanelNote>
    </Card>
  );
};

const LookbackSummary: React.FC<{ rows: RightsizerDetailResponse['lookback_summary'] }> = ({ rows }) => (
  <Card className="flex flex-col border-gray-200 bg-white dark:border-gray-800 dark:bg-gray-900">
    <div className="mb-4 flex items-center gap-2 text-sm font-semibold text-gray-700 dark:text-gray-200">
      <Cpu size={17} />
      Lookback Summary
    </div>
    <div className="grid flex-1 gap-3 md:grid-cols-3">
      {rows.map((row) => (
        <div key={row.window} className="rounded-2xl border border-gray-200 p-4 dark:border-gray-700">
          <div className="text-xs font-semibold uppercase tracking-[0.14em] text-gray-500">{row.window}</div>
          <div className="mt-3 text-2xl font-semibold text-gray-900 dark:text-white">{formatMetricPercent(row.maximum)}</div>
          <div className="mt-2 text-sm text-gray-500">
            p99 {formatMetricPercent(row.p99)} · p95 {formatMetricPercent(row.p95)}
          </div>
          <div className="mt-1 text-xs text-gray-400">{row.sample_count} samples</div>
        </div>
      ))}
    </div>
    <PanelNote className="mt-4">
      Compares recent observation windows so you can see whether peaks and percentiles are stable over time.
    </PanelNote>
  </Card>
);

const UtilizationBars: React.FC<{ rows: RightsizerDetailResponse['utilization_bars'] }> = ({ rows }) => (
  <Card className="flex flex-col border-gray-200 bg-white dark:border-gray-800 dark:bg-gray-900">
    <div className="mb-4 flex items-center gap-2 text-sm font-semibold text-gray-700 dark:text-gray-200">
      <HardDrive size={17} />
      Decision-Window Utilization
    </div>
    <div className="flex-1 space-y-4">
      {rows.map((row) => {
        const ratio = Number(row.value_ratio ?? 0);
        return (
          <div key={row.id}>
            <div className="mb-1 flex items-center justify-between text-sm">
              <span className="font-medium text-gray-700 dark:text-gray-200">{row.label}</span>
              <span className="font-semibold text-gray-900 dark:text-white">{formatPercent(row.value_ratio)}</span>
            </div>
            <div className="relative h-3 rounded-full bg-gray-100 dark:bg-gray-800">
              <div className="absolute left-[40%] top-[-4px] h-5 border-l border-gray-300" />
              <div className="absolute left-[70%] top-[-4px] h-5 border-l border-gray-300" />
              <div
                className="h-3 rounded-full bg-primary-600"
                style={{ width: `${Math.min(Math.max(ratio * 100, 0), 100)}%` }}
              />
            </div>
          </div>
        );
      })}
    </div>
    <PanelNote className="mt-4">
      Shows how much of the target capacity would be used during the decision window for CPU, network, and EBS dimensions.
    </PanelNote>
  </Card>
);

const TelemetryDisclosure: React.FC<{ telemetry: Record<string, any> }> = ({ telemetry }) => {
  const rows: Array<[string, any]> = [
    ['CPU utilization', telemetry.cpu_percent],
    ['Memory utilization', telemetry.memory_percent],
    ['Network in', telemetry.network_in_mbps],
    ['Network out', telemetry.network_out_mbps],
  ];
  return (
    <Card className="flex flex-col border-gray-200 bg-white dark:border-gray-800 dark:bg-gray-900">
      <div className="mb-4 flex items-center gap-2 text-sm font-semibold text-gray-700 dark:text-gray-200">
        <Database size={17} />
        Telemetry Disclosure
      </div>
      <div className="grid flex-1 gap-3 md:grid-cols-2">
        {rows.map(([label, value]) => (
          <div key={String(label)} className={`rounded-2xl border p-4 ${value?.thin_data ? 'border-warning-300 bg-warning-50 dark:border-warning-900 dark:bg-warning-950/30' : 'border-gray-200 dark:border-gray-700'}`}>
            <div className="font-semibold text-gray-900 dark:text-white">{label}</div>
            <div className="mt-2 text-2xl font-semibold text-gray-900 dark:text-white">
              ~{Number(value?.observed_days || 0).toFixed(1)} days
            </div>
            <div className="mt-1 text-sm text-gray-500">{value?.thin_data ? 'Limited confidence' : 'Present, considered in full'}</div>
          </div>
        ))}
      </div>
      <PanelNote className="mt-4">
        Shows which signals were available and whether the observation period is long enough to trust the recommendation.
      </PanelNote>
    </Card>
  );
};
