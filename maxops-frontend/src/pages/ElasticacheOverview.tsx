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
import { AlertCircle, ArrowLeft, Database, Download, Play, Search, Sparkles, Wallet, X } from 'lucide-react';
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
import { inventoryApi, type ElasticacheOverviewResource, type ElasticacheOverviewResponse } from '@/services/inventory';
import { checksApi } from '@/services/checks';
import { useSharedOverviewFilters } from '@/stores/sharedOverviewFilters';
import { downloadCsv } from '@/utils/csv';
import { buildSharedTagOptions, formatSharedTagSelectionLabel, matchesSharedTagQuery, parseSharedTagQuery } from '@/utils/sharedOverviewFilters';
import { adjustOptimizationCount, adjustOptimizationSavings } from '@/utils/optimizationProfile';
import {
  elasticacheDisplayedPotential,
  elasticacheStatusTone,
  rightsizingApi,
  type ElastiCacheRightsizingResponse,
} from '@/services/recommendations';
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
  new Intl.NumberFormat('en-US', { style: 'currency', currency: 'USD', maximumFractionDigits: 0 }).format(value);
const formatLargeNumber = (value: number) => new Intl.NumberFormat('en-US').format(value);
const titleCase = (value: string) =>
  value
    .replace(/([a-z])([A-Z])/g, '$1 $2')
    .split(/[_\s-]+/)
    .filter(Boolean)
    .map((part) => part.charAt(0).toUpperCase() + part.slice(1))
    .join(' ');
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
const isActionErrorMessage = (value: string) => /failed|error|missing/i.test(value);
type ElasticacheInventorySortKey = 'name' | 'cost' | 'savings' | 'curritems' | 'keycount' | 'region';

const SummaryTile: React.FC<{ label: string; value: string; sublabel: string; icon: React.ReactNode }> = ({ label, value, sublabel, icon }) => (
  <Card className="border-gray-200 bg-white/95 dark:border-gray-700 dark:bg-gray-900/95">
    <div className="flex items-start justify-between gap-4">
      <div>
        <div className="text-xs font-semibold uppercase tracking-[0.18em] text-gray-500 dark:text-gray-400">{label}</div>
        <div className="mt-3 text-3xl font-semibold tracking-tight text-gray-900 dark:text-white">{value}</div>
        <div className="mt-2 text-sm text-gray-500 dark:text-gray-400">{sublabel}</div>
      </div>
      <div className="rounded-2xl bg-success-50 p-3 text-success-700 dark:bg-success-950/50 dark:text-success-300">{icon}</div>
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

const RegionsPanel: React.FC<{
  rows: Array<{ region: string; count: number; monthly_cost: number; yearly_savings: number }>;
  onOpenRegion?: (region: string) => void;
}> = ({ rows, onOpenRegion }) => {
  const maxCount = Math.max(...rows.map((row) => row.count), 1);
  const maxCost = Math.max(...rows.map((row) => row.monthly_cost), 1);

  return (
    <Card className="border-gray-200 bg-white/95 dark:border-gray-700 dark:bg-gray-900/95">
      <div className="mb-5 flex items-center justify-between">
        <div>
          <div className="text-sm font-semibold text-gray-700 dark:text-gray-200">Resources per region</div>
          <div className="mt-1 text-xs text-gray-500 dark:text-gray-400">Regional ElastiCache footprint across the visible filtered inventory</div>
        </div>
        <div className="flex items-center gap-3 text-[11px] font-semibold uppercase tracking-[0.14em] text-gray-500 dark:text-gray-400">
          <span className="inline-flex items-center gap-1.5">
            <span className="h-2.5 w-2.5 rounded-full bg-primary-600" />
            Resources
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
                  className="text-sm font-semibold text-gray-700 transition hover:text-success-700 dark:text-gray-200 dark:hover:text-success-300"
                >
                  {row.region}
                </button>
                <button
                  type="button"
                  onClick={() => onOpenRegion?.(row.region)}
                  className="text-sm font-semibold text-gray-900 transition hover:text-success-700 dark:text-white dark:hover:text-success-300"
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
                <span>{row.count} visible</span>
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
                  title={`${row.region}: ${row.count} resources - ${formatCurrency(row.monthly_cost)} / month - ${formatCurrency(row.yearly_savings)} yearly savings`}
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

const ResourceRow: React.FC<{
  resource: ElasticacheOverviewResource;
  rightsizing?: ElastiCacheRightsizingResponse;
  onOpen: (resource: ElasticacheOverviewResource) => void;
  actions: string[];
  selectedAction: string;
  onActionChange: (value: string) => void;
  onExecuteAction: () => void;
  canExecuteAction: boolean;
  isExecutingAction: boolean;
  actionMessage?: string;
}> = ({ resource, rightsizing, onOpen, actions, selectedAction, onActionChange, onExecuteAction, canExecuteAction, isExecutingAction, actionMessage }) => {
  const { profile } = useOptimizationProfile();
  const yearlyPotential = elasticacheDisplayedPotential(rightsizing, resource.maxops.potential_savings_yearly);
  const status = rightsizing?.classification || resource.maxops.status;
  const statusTone = elasticacheStatusTone(status);

  return (
  <div className="grid w-full grid-cols-[minmax(0,2fr)_130px_130px_140px_150px_minmax(260px,1fr)] gap-4 rounded-2xl border border-gray-200 bg-white/90 px-4 py-4 text-left transition hover:border-warning-300 hover:shadow-sm dark:border-gray-700 dark:bg-gray-900/90">
    <button type="button" onClick={() => onOpen(resource)} className="min-w-0 text-left">
      <div className="flex items-center gap-3">
        <span className="flex h-10 w-10 items-center justify-center rounded-2xl bg-warning-50 dark:bg-warning-950/40">
          <ResourceTypeIcon resourceType="elasticache" size={20} />
        </span>
        <div className="min-w-0">
          <div className="truncate text-sm font-semibold text-gray-900 dark:text-white">{resource.resource_name || resource.resource_id}</div>
          <div className="mt-1 text-xs text-gray-500 dark:text-gray-400">
            {titleCase(resource.resource_type || 'resource')} - {titleCase(String(resource.metadata.engine || resource.aws_payload?.engine || 'unknown'))} - {resource.region || 'unknown region'}
          </div>
        </div>
      </div>
      <div className="mt-3 flex flex-wrap gap-2">
        <span className={`rounded-full px-2.5 py-1 text-[11px] font-semibold uppercase tracking-[0.12em] ${resource.workload.is_graviton ? 'bg-success-50 text-success-700 dark:bg-success-950/40 dark:text-success-300' : 'bg-warning-50 text-warning-700 dark:bg-warning-950/40 dark:text-warning-300'}`}>
          {resource.workload.is_graviton ? 'Graviton' : 'Legacy family'}
        </span>
        <span className={`rounded-full px-2.5 py-1 text-[11px] font-semibold uppercase tracking-[0.12em] ${statusTone === 'positive' ? 'bg-success-50 text-success-700 dark:bg-success-950/40 dark:text-success-300' : statusTone === 'warning' ? 'bg-warning-50 text-warning-700 dark:bg-warning-950/40 dark:text-warning-300' : statusTone === 'critical' ? 'bg-danger-50 text-danger-700 dark:bg-danger-950/40 dark:text-danger-300' : 'bg-gray-100 text-gray-500 dark:bg-gray-800 dark:text-gray-300'}`}>
          {status}
        </span>
      </div>
    </button>
    <div>
      <div className="text-[11px] font-semibold uppercase tracking-[0.16em] text-gray-500 dark:text-gray-400">CurrItems</div>
      <div className="mt-2 text-lg font-semibold text-gray-900 dark:text-white">{formatLargeNumber(Math.round(resource.workload.curritems))}</div>
    </div>
    <div>
      <div className="text-[11px] font-semibold uppercase tracking-[0.16em] text-gray-500 dark:text-gray-400">KeyCount</div>
      <div className="mt-2 text-lg font-semibold text-gray-900 dark:text-white">{formatLargeNumber(Math.round(resource.workload.keycount))}</div>
    </div>
    <div>
      <div className="text-[11px] font-semibold uppercase tracking-[0.16em] text-gray-500 dark:text-gray-400">Monthly Cost</div>
      <div className="mt-2 text-lg font-semibold text-gray-900 dark:text-white">{formatCurrency(resource.workload.monthly_cost_estimate)}</div>
    </div>
    <div>
      <div className="text-[11px] font-semibold uppercase tracking-[0.16em] text-gray-500 dark:text-gray-400">Savings</div>
      <div className="mt-2 text-lg font-semibold text-success-700 dark:text-success-300">{formatCurrency(adjustOptimizationSavings(yearlyPotential, profile))}</div>
      <div className="mt-1 text-xs text-gray-500 dark:text-gray-400">{rightsizing ? 'Rightsizer potential' : resource.maxops.title || 'Healthy cache'}</div>
    </div>
    <div onClick={(event) => event.stopPropagation()}>
      {actions.length > 0 ? (
        <div className="space-y-2">
          <div className="flex items-center gap-2">
            <select
              value={selectedAction}
              onChange={(event) => onActionChange(event.target.value)}
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
              onClick={onExecuteAction}
              disabled={!canExecuteAction || isExecutingAction}
              className="inline-flex shrink-0 items-center gap-2 rounded-xl bg-warning-500 px-3 py-2 text-sm font-semibold text-white transition hover:bg-warning-600 disabled:cursor-not-allowed disabled:opacity-50"
            >
              <Play size={14} />
              {isExecutingAction ? 'Running...' : 'Execute'}
            </button>
            <div className="flex h-9 w-9 shrink-0 items-center justify-center">
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
            <div className="text-xs text-gray-500 dark:text-gray-400">{actionMessage}</div>
          ) : null}
        </div>
      ) : (
        <span className="text-sm text-gray-500 dark:text-gray-400">{titleCase(resource.maxops.recommended_action || 'observe')}</span>
      )}
    </div>
  </div>
  );
};

export const ElasticacheOverviewPage: React.FC = () => {
  const navigate = useNavigate();
  const { profile } = useOptimizationProfile();
  const { theme } = useTheme();
  const queryClient = useQueryClient();
  const [search, setSearch] = useState('');
  const [inventorySearch, setInventorySearch] = useState('');
  const [inventorySort, setInventorySort] = useState<ElasticacheInventorySortKey>('cost');
  const [selectedTeam, setSelectedTeam] = useState('all');
  const [selectedEngine, setSelectedEngine] = useState('all');
  const [selectedFinding, setSelectedFinding] = useState('all');
  const [actionableOnly, setActionableOnly] = useState(false);
  const [gravitonOnly, setGravitonOnly] = useState(false);
  const [selectedActions, setSelectedActions] = useState<Record<string, string>>({});
  const [executingResources, setExecutingResources] = useState<Set<string>>(new Set());
  const [actionMessages, setActionMessages] = useState<Record<string, string>>({});
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

  const { data, isLoading, isError } = useQuery<ElasticacheOverviewResponse>('inventory-elasticache-overview', () => inventoryApi.getElasticacheOverview());
  const { data: rightsizingRecommendations = [] } = useQuery(
    'elasticache-rightsizer-list',
    () => rightsizingApi.listElasticacheRecommendations(),
    { retry: false },
  );
  const rightsizingByInventory = useMemo(
    () => new Map(rightsizingRecommendations.map((item) => [item.inventory_id, item])),
    [rightsizingRecommendations],
  );
  const { data: elasticacheChecks = [] } = useQuery(['checks', 'elasticache'], () => checksApi.listChecks('elasticache'), {
    retry: false,
  });
  const availableTags = useMemo(() => buildSharedTagOptions(data?.resources || []), [data]);

  const snoozeResources = async (resources: ElasticacheOverviewResource[], snoozedUntil: string, reason: string) => {
    await inventoryApi.snoozeResources({
      resources: resources.map((resource) => ({
        resource_id: resource.resource_id,
        resource_type: resource.resource_type || 'elasticache',
        resource_name: resource.resource_name,
        account_id: resource.account_id,
        region: resource.region,
      })),
      snoozed_until: snoozedUntil,
      reason,
    });
    await queryClient.invalidateQueries('inventory-elasticache-overview');
    await queryClient.invalidateQueries('latest-check-results');
  };

  const removeResourceSnoozes = async (resources: ElasticacheOverviewResource[], reason = '') => {
    await inventoryApi.removeSnoozes({
      resources: resources.map((resource) => ({
        resource_id: resource.resource_id,
        resource_type: resource.resource_type || 'elasticache',
        resource_name: resource.resource_name,
        account_id: resource.account_id,
        region: resource.region,
      })),
      reason,
    });
    await queryClient.invalidateQueries('inventory-elasticache-overview');
    await queryClient.invalidateQueries('latest-check-results');
  };

  const filteredResources = useMemo(() => {
    if (!data) return [];
    const query = search.trim().toLowerCase();
    return data.resources.filter((resource) => {
      const environment = String(resource.tags.env || resource.metadata.environment || 'unknown').toLowerCase();
      const team = String(resource.metadata.team || resource.tags.team || 'unknown').toLowerCase();
      const engine = String(resource.metadata.engine || resource.aws_payload?.engine || 'unknown').toLowerCase();
      const finding = String(resource.maxops.finding_type || 'healthy').toLowerCase();

      if (selectedRegion !== 'all' && String(resource.region || 'unknown').toLowerCase() !== selectedRegion) return false;
      if (selectedEnvironment !== 'all' && environment !== selectedEnvironment) return false;
      if (!matchesSharedTagQuery(resource.tags, tagQuery, tagLogic)) return false;
      if (selectedTeam !== 'all' && team !== selectedTeam) return false;
      if (selectedEngine !== 'all' && engine !== selectedEngine) return false;
      if (selectedFinding !== 'all' && finding !== selectedFinding) return false;
      const rightsizing = rightsizingByInventory.get(resource.inventory_id);
      if (actionableOnly && rightsizing?.classification !== 'ACTIONABLE' && resource.maxops.status !== 'actionable') return false;
      if (gravitonOnly && !resource.workload.is_graviton) return false;
      if (!query) return true;

      return [
        resource.resource_id,
        resource.resource_name,
        resource.resource_type,
        resource.region,
        resource.metadata.engine,
        resource.metadata.node_type,
        resource.maxops.title,
        resource.maxops.recommended_action,
      ]
        .filter(Boolean)
        .some((value) => String(value).toLowerCase().includes(query));
    });
  }, [actionableOnly, data, gravitonOnly, rightsizingByInventory, search, selectedEngine, selectedEnvironment, selectedFinding, selectedRegion, selectedTeam, tagLogic, tagQuery]);

  const derived = useMemo(() => {
    let actionable = 0;
    let monthlyCost = 0;
    let yearlySavings = 0;
    let graviton = 0;
    const engineCounts: Record<string, number> = {};
    const typeCounts: Record<string, number> = {};
    const regionTotals: Record<string, { region: string; count: number; monthly_cost: number; yearly_savings: number }> = {};

    filteredResources.forEach((resource) => {
      const engine = titleCase(String(resource.metadata.engine || resource.aws_payload?.engine || 'unknown'));
      const type = titleCase(String(resource.resource_type || 'unknown'));
      const region = String(resource.region || 'unknown');

      const rightsizing = rightsizingByInventory.get(resource.inventory_id);
      if (rightsizing?.classification === 'ACTIONABLE' || resource.maxops.status === 'actionable') actionable += 1;
      if (resource.workload.is_graviton) graviton += 1;
      monthlyCost += resource.workload.monthly_cost_estimate;
      const potential = elasticacheDisplayedPotential(rightsizing, resource.maxops.potential_savings_yearly);
      yearlySavings += potential;
      engineCounts[engine] = (engineCounts[engine] || 0) + 1;
      typeCounts[type] = (typeCounts[type] || 0) + 1;
      if (!regionTotals[region]) {
        regionTotals[region] = { region, count: 0, monthly_cost: 0, yearly_savings: 0 };
      }
      regionTotals[region].count += 1;
      regionTotals[region].monthly_cost += resource.workload.monthly_cost_estimate;
      regionTotals[region].yearly_savings += potential;
    });

    return {
      total: filteredResources.length,
      actionable,
      healthy: Math.max(filteredResources.length - actionable, 0),
      graviton,
      engineRows: Object.entries(engineCounts).map(([name, value]) => ({ name, value })),
      typeRows: Object.entries(typeCounts).map(([name, value]) => ({ name, value })),
      regionRows: Object.values(regionTotals).sort((left, right) => right.monthly_cost - left.monthly_cost),
      scatterRows: filteredResources.map((resource) => ({
        x: Number(resource.workload.curritems.toFixed(2)),
        y: Number(resource.workload.keycount.toFixed(2)),
        z: Math.max(Number(resource.workload.monthly_cost_estimate.toFixed(0)), 8),
        resourceId: resource.resource_id,
      })),
      monthlyCost,
      yearlySavings,
      topCost: [...filteredResources].sort((left, right) => right.workload.monthly_cost_estimate - left.workload.monthly_cost_estimate).slice(0, 8),
    };
  }, [filteredResources, rightsizingByInventory]);

  const inventoryResources = useMemo(() => {
    const query = inventorySearch.trim().toLowerCase();
    const matches = !query
      ? filteredResources
      : filteredResources.filter((resource) =>
          [
            resource.resource_id,
            resource.resource_name,
            resource.resource_type,
            resource.region,
            resource.metadata.engine,
            resource.metadata.node_type,
            resource.maxops.finding_type,
            resource.maxops.title,
          ]
            .filter(Boolean)
            .some((value) => String(value).toLowerCase().includes(query))
        );

    return [...matches].sort((left, right) => {
      switch (inventorySort) {
        case 'name':
          return String(left.resource_name || left.resource_id).localeCompare(String(right.resource_name || right.resource_id));
        case 'savings':
          return elasticacheDisplayedPotential(rightsizingByInventory.get(right.inventory_id), right.maxops.potential_savings_yearly) - elasticacheDisplayedPotential(rightsizingByInventory.get(left.inventory_id), left.maxops.potential_savings_yearly);
        case 'curritems':
          return right.workload.curritems - left.workload.curritems;
        case 'keycount':
          return right.workload.keycount - left.workload.keycount;
        case 'region':
          return String(left.region || 'unknown').localeCompare(String(right.region || 'unknown'));
        case 'cost':
        default:
          return right.workload.monthly_cost_estimate - left.workload.monthly_cost_estimate;
      }
    });
  }, [filteredResources, inventorySearch, inventorySort, rightsizingByInventory]);

  const getResourceActions = (resource: ElasticacheOverviewResource) => {
    const check = elasticacheChecks.find((entry) => entry.check_id === resource.maxops.check_id);
    const mappedActions = check ? getActionsForCheck(check) : [];
    const recommendedAction = resource.maxops.recommended_action?.trim();
    if (!recommendedAction) return mappedActions;
    return mappedActions.includes(recommendedAction) ? mappedActions : [recommendedAction, ...mappedActions];
  };

  const executeResourceAction = async (resource: ElasticacheOverviewResource, actionOverride?: string) => {
    const action = actionOverride || selectedActions[resource.resource_id] || getResourceActions(resource)[0] || '';
    if (!resource.maxops.check_id || !resource.account_id || !resource.region || !action) {
      setActionMessages((prev) => ({
        ...prev,
        [resource.resource_id]: 'Missing check, account, region, or action for execution.',
      }));
      return;
    }

    setExecutingResources((prev) => new Set(prev).add(resource.resource_id));
    setActionMessages((prev) => ({
      ...prev,
      [resource.resource_id]: '',
    }));

    try {
      const response = await checksApi.executeAction(resource.maxops.check_id, {
        action,
        account_id: resource.account_id,
        region: resource.region,
        resource_id: resource.resource_id,
      });
      setActionMessages((prev) => ({
        ...prev,
        [resource.resource_id]: response.message || 'Action executed.',
      }));
    } catch (error: any) {
      setActionMessages((prev) => ({
        ...prev,
        [resource.resource_id]: error?.response?.data?.detail || error?.message || 'Failed to execute action.',
      }));
    } finally {
      setExecutingResources((prev) => {
        const next = new Set(prev);
        next.delete(resource.resource_id);
        return next;
      });
    }
  };

  const exportInventoryCsv = () => {
    downloadCsv(
      'elasticache-inventory.csv',
      inventoryResources.map((resource) => ({
        resource_id: resource.resource_id,
        resource_name: resource.resource_name || '',
        resource_type: resource.resource_type || '',
        region: resource.region || '',
        availability_zone: resource.availability_zone || '',
        state: resource.state || '',
        engine: String(resource.metadata.engine || ''),
        node_type: String(resource.metadata.node_type || ''),
        curritems: resource.workload.curritems,
        keycount: resource.workload.keycount,
        monthly_cost_estimate: resource.workload.monthly_cost_estimate,
        finding: resource.maxops.finding_type || '',
        status: resource.maxops.status || '',
        recommended_action: resource.maxops.recommended_action || '',
        potential_savings_yearly: elasticacheDisplayedPotential(rightsizingByInventory.get(resource.inventory_id), resource.maxops.potential_savings_yearly),
        tags: resource.tags || {},
        metadata: resource.metadata || {},
      }))
    );
  };

  if (isLoading) {
    return (
      <Layout>
        <div className="flex min-h-[60vh] items-center justify-center rounded-[32px] border border-gray-200 bg-white/90 dark:border-gray-700 dark:bg-gray-900/90">
          <div className="text-center">
            <div className="text-lg font-semibold text-gray-800 dark:text-gray-100">Loading ElastiCache overview</div>
            <div className="mt-2 text-sm text-gray-500 dark:text-gray-400">Preparing imported cache fleet and performance views.</div>
          </div>
        </div>
      </Layout>
    );
  }

  if (isError || !data) {
    return (
      <Layout>
        <Card className="border-danger-200 bg-danger-50 dark:border-danger-900/50 dark:bg-danger-950/30">
          <div className="flex items-start gap-3">
            <AlertCircle className="mt-0.5 text-danger-600 dark:text-danger-300" size={18} />
            <div>
              <div className="text-lg font-semibold text-danger-900 dark:text-danger-100">ElastiCache overview unavailable</div>
              <div className="mt-2 text-sm text-danger-700 dark:text-danger-200">The imported ElastiCache inventory could not be loaded.</div>
            </div>
          </div>
        </Card>
      </Layout>
    );
  }

  const chips = [
    ...(search.trim() ? [{ key: `search:${search}`, label: `Search: ${search}`, onRemove: () => setSearch('') }] : []),
    ...(selectedRegion !== 'all' ? [{ key: `region:${selectedRegion}`, label: `Region: ${selectedRegion}`, onRemove: () => setSelectedRegion('all') }] : []),
    ...(selectedEnvironment !== 'all' ? [{ key: `env:${selectedEnvironment}`, label: `Env: ${selectedEnvironment}`, onRemove: () => setSelectedEnvironment('all') }] : []),
    ...(tagQuery.trim() ? [{ key: 'tag-logic', label: tagLogic === 'or' ? 'Tags: Match any' : 'Tags: Match all', onRemove: () => setTagLogic('and') }] : []),
    ...parseSharedTagQuery(tagQuery).map((value) => ({ key: `tags:${value}`, label: `Tag: ${formatSharedTagSelectionLabel(value)}`, onRemove: () => setTagQuery(parseSharedTagQuery(tagQuery).filter((item) => item !== value).join(', ')) })),
    ...(selectedTeam !== 'all' ? [{ key: `team:${selectedTeam}`, label: `Team: ${selectedTeam}`, onRemove: () => setSelectedTeam('all') }] : []),
    ...(selectedEngine !== 'all' ? [{ key: `engine:${selectedEngine}`, label: `Engine: ${selectedEngine}`, onRemove: () => setSelectedEngine('all') }] : []),
    ...(selectedFinding !== 'all' ? [{ key: `finding:${selectedFinding}`, label: `Finding: ${selectedFinding}`, onRemove: () => setSelectedFinding('all') }] : []),
    ...(actionableOnly ? [{ key: 'actionable', label: 'Actionable', onRemove: () => setActionableOnly(false) }] : []),
    ...(gravitonOnly ? [{ key: 'graviton', label: 'Graviton only', onRemove: () => setGravitonOnly(false) }] : []),
  ];

  return (
    <Layout>
      <div className="space-y-8">
        <section className="overflow-hidden rounded-[32px] border border-gray-200 bg-[radial-gradient(circle_at_top_left,_rgba(16,185,129,0.24),_transparent_30%),radial-gradient(circle_at_bottom_right,_rgba(22,193,168,0.18),_transparent_26%),linear-gradient(135deg,_#f6fffb_0%,_#ecfdf5_44%,_#ecfdf9_100%)] p-8 shadow-sm dark:border-gray-700 dark:bg-[radial-gradient(circle_at_top_left,_rgba(16,185,129,0.18),_transparent_24%),radial-gradient(circle_at_bottom_right,_rgba(22,193,168,0.16),_transparent_20%),linear-gradient(135deg,_rgba(14,24,48,0.98)_0%,_rgba(24,33,64,0.96)_46%,_rgba(14,24,48,0.98)_100%)]">
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
                  <ResourceTypeIcon resourceType="elasticache" size={16} />
                  ElastiCache Fleet Overview
                </div>
                <h1 className="mt-4 text-4xl font-semibold tracking-tight text-gray-950 dark:text-gray-50">Cache cost posture and data shape</h1>
                <p className="mt-3 max-w-3xl text-sm leading-6 text-gray-600 dark:text-gray-300">
                  Review imported clusters and replication groups, memory-key growth patterns, and optimization opportunities across the cache estate.
                </p>
                {chips.length > 0 ? (
                  <div className="mt-4 flex flex-wrap gap-2">
                    {chips.map((chip) => (
                      <button
                        key={chip.key}
                        type="button"
                        onClick={chip.onRemove}
                        className="inline-flex items-center gap-1.5 rounded-full border border-gray-200 bg-white/80 px-3 py-1 text-xs font-semibold uppercase tracking-[0.12em] text-gray-600 transition hover:border-success-300 hover:bg-white hover:text-gray-900 dark:border-gray-700 dark:bg-gray-900/80 dark:text-gray-200 dark:hover:border-success-500 dark:hover:text-white"
                      >
                        <span>{chip.label}</span>
                        <X size={12} />
                      </button>
                    ))}
                  </div>
                ) : null}
              </div>
              <div className="rounded-[28px] border border-white/70 bg-white/70 px-5 py-4 shadow-sm backdrop-blur dark:border-gray-700 dark:bg-gray-900/70">
                <div className="text-xs font-semibold uppercase tracking-[0.18em] text-gray-500 dark:text-gray-400">Imported snapshot</div>
                <div className="mt-2 text-sm font-semibold text-gray-900 dark:text-white">{formatDateTime(data.generated_at)}</div>
                <div className="mt-1 text-sm text-gray-500 dark:text-gray-400">{data.account_id || 'Unknown account'}</div>
              </div>
            </div>
          </div>
        </section>

        <ResourcePageFilters
          searchValue={search}
          searchPlaceholder="Search cache ID, engine, node type, region, or finding"
          onSearchChange={setSearch}
          savedFilterSelect={{
            id: 'elasticache-saved-filter',
            label: 'Saved filter',
            value: activeSavedFilterId || '',
            options: [
              { label: 'Custom filters', value: '' },
              ...savedFilters.map((filter) => ({ label: filter.name, value: filter.id })),
            ],
            onChange: applySavedFilter,
          }}
          selects={[
            { id: 'elasticache-region', label: 'Region', value: selectedRegion, options: [{ label: 'All regions', value: 'all' }, ...data.dimensions.regions.map((row) => ({ label: `${row.key} (${row.count})`, value: row.key }))], onChange: setSelectedRegion },
            { id: 'elasticache-env', label: 'Environment', value: selectedEnvironment, options: [{ label: 'All environments', value: 'all' }, ...data.dimensions.environments.map((row) => ({ label: `${titleCase(row.key)} (${row.count})`, value: row.key }))], onChange: setSelectedEnvironment },
            { id: 'elasticache-team', label: 'Team', value: selectedTeam, options: [{ label: 'All teams', value: 'all' }, ...data.dimensions.teams.map((row) => ({ label: `${titleCase(row.key)} (${row.count})`, value: row.key }))], onChange: setSelectedTeam },
            { id: 'elasticache-engine', label: 'Engine', value: selectedEngine, options: [{ label: 'All engines', value: 'all' }, ...data.dimensions.engines.map((row) => ({ label: `${titleCase(row.key)} (${row.count})`, value: row.key }))], onChange: setSelectedEngine },
            { id: 'elasticache-finding', label: 'Finding', value: selectedFinding, options: [{ label: 'All findings', value: 'all' }, ...data.findings_breakdown.map((row) => ({ label: `${titleCase(row.key)} (${row.count})`, value: row.key }))], onChange: setSelectedFinding },
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
            { id: 'elasticache-actionable', label: 'Actionable only', checked: actionableOnly, onChange: setActionableOnly },
            { id: 'elasticache-graviton', label: 'Graviton only', checked: gravitonOnly, onChange: setGravitonOnly },
          ]}
          onReset={() => {
            setSearch('');
            setSelectedRegion('all');
            setSelectedEnvironment('all');
            setTagQuery('');
            setSelectedTeam('all');
            setSelectedEngine('all');
            setSelectedFinding('all');
            setActionableOnly(false);
            setGravitonOnly(false);
          }}
          resetDisabled={!activeSavedFilterId && !search.trim() && selectedRegion === 'all' && selectedEnvironment === 'all' && !tagQuery.trim() && tagLogic === 'and' && selectedTeam === 'all' && selectedEngine === 'all' && selectedFinding === 'all' && !actionableOnly && !gravitonOnly}
        />

        <div className="grid gap-4 md:grid-cols-2 xl:grid-cols-4">
          <SummaryTile label="Resources" value={formatLargeNumber(derived.total)} sublabel={`${adjustOptimizationCount(derived.actionable, profile)} actionable - ${Math.max(derived.total - adjustOptimizationCount(derived.actionable, profile), 0)} healthy`} icon={<Database size={22} />} />
          <SummaryTile label="Monthly Cost" value={formatCurrency(derived.monthlyCost)} sublabel="Estimated imported cache cost" icon={<Wallet size={22} />} />
          <SummaryTile label="Graviton" value={formatLargeNumber(derived.graviton)} sublabel="Resources already on modern families" icon={<Sparkles size={22} />} />
          <SummaryTile label="Yearly Savings" value={formatCurrency(adjustOptimizationSavings(derived.yearlySavings, profile))} sublabel="Potential annual optimization upside" icon={<Sparkles size={22} />} />
        </div>

        <ResourceActionPanel
          title="Cache Inventory"
          subtitle={`${inventoryResources.length} visible`}
          resourceLabel="ElastiCache resource"
          items={inventoryResources}
          getId={(resource) => resource.resource_id}
          getName={(resource) => resource.resource_name || resource.resource_id}
          getActions={getResourceActions}
          canExecute={(resource, action) => Boolean(resource.maxops.check_id && resource.account_id && resource.region && action)}
          executeAction={(resource, action) => executeResourceAction(resource, action)}
          formatActionLabel={titleCase}
          getSnooze={(resource) => resource.snooze}
          onSnooze={snoozeResources}
          onRemoveSnooze={removeResourceSnoozes}
          toolbar={
            <>
              <div className="relative min-w-[240px]">
                <Search size={14} className="pointer-events-none absolute left-3 top-1/2 -trangray-y-1/2 text-gray-400" />
                <input
                  type="text"
                  value={inventorySearch}
                  onChange={(event) => setInventorySearch(event.target.value)}
                  placeholder="Search visible cache resources"
                  className="w-full rounded-xl border border-gray-200 bg-white py-2 pl-9 pr-3 text-sm text-gray-700 outline-none transition focus:border-warning-300 focus:ring-2 focus:ring-warning-100 dark:border-gray-700 dark:bg-gray-900/80 dark:text-gray-100 dark:focus:border-warning-500 dark:focus:ring-warning-900/40"
                />
              </div>
              <select
                value={inventorySort}
                onChange={(event) => setInventorySort(event.target.value as ElasticacheInventorySortKey)}
                className="rounded-xl border border-gray-200 bg-white px-3 py-2 text-sm text-gray-700 outline-none transition focus:border-warning-300 focus:ring-2 focus:ring-warning-100 dark:border-gray-700 dark:bg-gray-900/80 dark:text-gray-100 dark:focus:border-warning-500 dark:focus:ring-warning-900/40"
              >
                <option value="cost">Sort by monthly cost</option>
                <option value="savings">Sort by yearly savings</option>
                <option value="curritems">Sort by CurrItems</option>
                <option value="keycount">Sort by KeyCount</option>
                <option value="name">Sort by name</option>
                <option value="region">Sort by region</option>
              </select>
              <button
                type="button"
                onClick={exportInventoryCsv}
                disabled={inventoryResources.length === 0}
                className="inline-flex items-center gap-2 rounded-xl border border-gray-200 bg-white px-3 py-2 text-sm font-medium text-gray-700 transition hover:border-warning-300 hover:text-warning-700 disabled:cursor-not-allowed disabled:opacity-50 dark:border-gray-700 dark:bg-gray-900/80 dark:text-gray-100 dark:hover:border-warning-500 dark:hover:text-warning-300"
              >
                <Download size={14} />
                Export CSV
              </button>
            </>
          }
          confirmColumns={[
            {
              header: 'Resource',
              render: (resource) => (
                <div>
                  <div className="font-semibold text-gray-900 dark:text-white">{resource.resource_name || resource.resource_id}</div>
                  <div className="mt-1 text-xs text-gray-500 dark:text-gray-400">{resource.resource_id}</div>
                </div>
              ),
            },
            { header: 'Type', render: (resource) => titleCase(resource.resource_type || 'resource') },
            { header: 'Engine', render: (resource) => titleCase(String(resource.metadata.engine || 'unknown')) },
            { header: 'Region', render: (resource) => resource.region || 'unknown' },
            { header: 'Finding', render: (resource) => titleCase(resource.maxops.finding_type || 'healthy') },
            {
              header: 'Yearly Savings',
              render: (resource) => (
                <span className="font-semibold text-success-700 dark:text-success-300">
                  {formatCurrency(adjustOptimizationSavings(elasticacheDisplayedPotential(rightsizingByInventory.get(resource.inventory_id), resource.maxops.potential_savings_yearly), profile))}
                </span>
              ),
            },
          ]}
          renderItem={(resource) => (
              <ResourceRow
                key={resource.resource_id}
                resource={resource}
                rightsizing={rightsizingByInventory.get(resource.inventory_id)}
                onOpen={(current) => navigate(`/dashboard/resources/elasticache/resources/${encodeURIComponent(current.resource_id)}`)}
                actions={getResourceActions(resource)}
                selectedAction={selectedActions[resource.resource_id] || getResourceActions(resource)[0] || ''}
                onActionChange={(value) =>
                  setSelectedActions((prev) => ({
                    ...prev,
                    [resource.resource_id]: value,
                  }))
                }
                onExecuteAction={() => executeResourceAction(resource)}
                canExecuteAction={Boolean(resource.maxops.check_id && resource.account_id && resource.region && (selectedActions[resource.resource_id] || getResourceActions(resource)[0] || ''))}
                isExecutingAction={executingResources.has(resource.resource_id)}
                actionMessage={actionMessages[resource.resource_id]}
              />
            )}
        />

        <RegionsPanel rows={derived.regionRows} onOpenRegion={(region) => setSelectedRegion(region.toLowerCase())} />

        <div className="grid gap-6">
          <ChartCard title="Engine Mix" subtitle="Current engine distribution across the visible cache fleet">
            <div className="grid gap-4 lg:grid-cols-[260px_minmax(0,1fr)]">
              <div className="h-72">
                <ResponsiveContainer width="100%" height="100%">
                  <PieChart>
                    <Pie data={derived.engineRows} innerRadius={62} outerRadius={102} dataKey="value" nameKey="name" stroke="none">
                      {derived.engineRows.map((entry, index) => <Cell key={entry.name} fill={CHART_COLORS[index % CHART_COLORS.length]} />)}
                    </Pie>
                    <Tooltip />
                  </PieChart>
                </ResponsiveContainer>
              </div>
              <div className="space-y-3">
                {derived.engineRows.map((entry, index) => (
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

          <ChartCard title="Resource Type Mix" subtitle="Clusters versus replication groups in the current scope">
            <div className="h-72">
              <ResponsiveContainer width="100%" height="100%">
                <BarChart data={derived.typeRows} margin={{ top: 8, right: 12, left: 0, bottom: 0 }}>
                  <CartesianGrid strokeDasharray="3 3" stroke={chartGridColor(theme)} opacity={0.35} />
                  <XAxis dataKey="name" stroke={chartTickColor(theme)} tickLine={false} axisLine={false} />
                  <YAxis stroke={chartTickColor(theme)} tickLine={false} axisLine={false} />
                  <Tooltip />
                  <Bar dataKey="value" radius={[8, 8, 0, 0]}>
                    {derived.typeRows.map((row, index) => <Cell key={row.name} fill={CHART_COLORS[index % CHART_COLORS.length]} />)}
                  </Bar>
                </BarChart>
              </ResponsiveContainer>
            </div>
          </ChartCard>
        </div>

        <div className="grid gap-6 xl:grid-cols-2">
          <ChartCard title="Regional Cost vs Savings" subtitle="Where cache spend and remediation upside cluster together">
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

          <ChartCard title="CurrItems vs KeyCount" subtitle="Upper-right resources hold larger active data shape and key volume">
            <div className="h-80">
              <ResponsiveContainer width="100%" height="100%">
                <ScatterChart margin={{ top: 8, right: 12, left: 0, bottom: 0 }}>
                  <CartesianGrid strokeDasharray="3 3" stroke={chartGridColor(theme)} opacity={0.35} />
                  <XAxis type="number" dataKey="x" name="CurrItems" stroke={chartTickColor(theme)} tickLine={false} axisLine={false} />
                  <YAxis type="number" dataKey="y" name="KeyCount" stroke={chartTickColor(theme)} tickLine={false} axisLine={false} />
                  <Tooltip cursor={{ strokeDasharray: '3 3' }} />
                  <Scatter
                    data={derived.scatterRows}
                    fill={CHART_COLORS[5]}
                    shape={(props: any) => (
                      <circle
                        cx={props.cx}
                        cy={props.cy}
                        r={Math.max(Math.min((props.payload?.z || 8) / 8, 14), 6)}
                        fill={CHART_COLORS[5]}
                        className="cursor-pointer"
                        onClick={() => navigate(`/dashboard/resources/elasticache/resources/${encodeURIComponent(props.payload?.resourceId || '')}`)}
                      />
                    )}
                  />
                </ScatterChart>
              </ResponsiveContainer>
            </div>
          </ChartCard>
        </div>

        <ChartCard title="Top Cost Drivers" subtitle="Highest monthly cost cache resources in the current filtered scope">
          <div className="grid gap-3 xl:grid-cols-2">
            {derived.topCost.map((resource) => (
              <button
                key={resource.resource_id}
                type="button"
                onClick={() => navigate(`/dashboard/resources/elasticache/resources/${encodeURIComponent(resource.resource_id)}`)}
                className="flex w-full items-center justify-between rounded-2xl border border-gray-200 bg-gray-50 px-4 py-3 text-left transition hover:border-success-300 hover:bg-white dark:border-gray-700 dark:bg-gray-800/70 dark:hover:bg-gray-800"
              >
                <div className="min-w-0">
                  <div className="truncate text-sm font-semibold text-gray-900 dark:text-white">{resource.resource_name || resource.resource_id}</div>
                  <div className="mt-1 text-xs text-gray-500 dark:text-gray-400">
                    {titleCase(String(resource.metadata.engine || resource.aws_payload?.engine || 'unknown'))} - {resource.metadata.node_type || resource.aws_payload?.node_type || 'Unknown node'}
                  </div>
                </div>
                <div className="text-sm font-semibold text-gray-900 dark:text-white">{formatCurrency(resource.workload.monthly_cost_estimate)}</div>
              </button>
            ))}
          </div>
        </ChartCard>
      </div>
    </Layout>
  );
};
