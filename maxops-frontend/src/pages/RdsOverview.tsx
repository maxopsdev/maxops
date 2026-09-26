import React, { useCallback, useEffect, useMemo, useState } from 'react';
import { useNavigate, useSearchParams } from 'react-router-dom';
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
import { AlertCircle, ArrowLeft, Cpu, Database, Download, Play, Search, Sparkles, Wallet, X } from 'lucide-react';
import { Layout } from '@/components/layout/Layout';
import { Card } from '@/components/common/Card';
import { ResourceActionPanel } from '@/components/common/ResourceActionPanel';
import { getActionsForCheck } from '@/components/checks/checkActions';
import { ResourcePageFilters } from '@/components/common/ResourcePageFilters';
import { SharedTagFilterField } from '@/components/common/SharedTagFilterField';
import { ResourceTypeIcon } from '@/components/icons/ResourceTypeIcon';
import {
  RIGHTSIZING_CLASSIFICATION_ORDER,
  rightsizingClassificationClass,
} from '@/components/rightsizing/classification';
import { useOptimizationProfile } from '@/contexts/OptimizationProfileContext';
import { useTheme } from '@/contexts/ThemeContext';
import worldMapUrl from '@/assets/maps/world-map.svg';
import { inventoryApi, type RdsOverviewInstance, type RdsOverviewResponse } from '@/services/inventory';
import { checksApi } from '@/services/checks';
import { rightsizingApi, type RdsFleetRightsizingRow } from '@/services/recommendations';
import { useSharedOverviewFilters } from '@/stores/sharedOverviewFilters';
import { downloadCsv } from '@/utils/csv';
import { buildSharedTagOptions, formatSharedTagSelectionLabel, matchesSharedTagQuery, parseSharedTagQuery } from '@/utils/sharedOverviewFilters';
import { adjustOptimizationCount, adjustOptimizationSavings, type OptimizationProfile } from '@/utils/optimizationProfile';
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
type RdsInventorySortKey = 'classification' | 'name' | 'cost' | 'savings' | 'cpu' | 'connections' | 'region';
const RDS_SORT_KEYS = new Set<RdsInventorySortKey>(['classification', 'name', 'cost', 'savings', 'cpu', 'connections', 'region']);
const DB_LOAD_LABELS: Record<string, string> = {
  AVAILABLE: 'Available', NOT_NEEDED: 'Not needed', DISABLED: 'Enable for confidence',
  UNSUPPORTED: 'Unsupported', ACCESS_DENIED: 'Unavailable', ERROR: 'Unavailable',
};
const dbLoadLabel = (status?: RdsFleetRightsizingRow['database_load_status']) => DB_LOAD_LABELS[status || ''] || 'Unavailable';

const SummaryTile: React.FC<{ label: string; value: string; sublabel: string; icon: React.ReactNode }> = ({ label, value, sublabel, icon }) => (
  <Card className="border-gray-200 bg-white/95 dark:border-gray-700 dark:bg-gray-900/95">
    <div className="flex items-start justify-between gap-4">
      <div>
        <div className="text-xs font-semibold uppercase tracking-[0.18em] text-gray-500 dark:text-gray-400">{label}</div>
        <div className="mt-3 text-3xl font-semibold tracking-tight text-gray-900 dark:text-white">{value}</div>
        <div className="mt-2 text-sm text-gray-500 dark:text-gray-400">{sublabel}</div>
      </div>
      <div className="rounded-2xl bg-primary-50 p-3 text-primary-700 dark:bg-primary-950/50 dark:text-primary-300">{icon}</div>
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
          <div className="text-sm font-semibold text-gray-700 dark:text-gray-200">Instances per region</div>
          <div className="mt-1 text-xs text-gray-500 dark:text-gray-400">Regional RDS footprint across the visible filtered inventory</div>
        </div>
        <div className="flex items-center gap-3 text-[11px] font-semibold uppercase tracking-[0.14em] text-gray-500 dark:text-gray-400">
          <span className="inline-flex items-center gap-1.5">
            <span className="h-2.5 w-2.5 rounded-full bg-primary-600" />
            Instances
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
                  className="text-sm font-semibold text-gray-700 transition hover:text-primary-700 dark:text-gray-200 dark:hover:text-primary-300"
                >
                  {row.region}
                </button>
                <button
                  type="button"
                  onClick={() => onOpenRegion?.(row.region)}
                  className="text-sm font-semibold text-gray-900 transition hover:text-primary-700 dark:text-white dark:hover:text-primary-300"
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
                  title={`${row.region}: ${row.count} instances - ${formatCurrency(row.monthly_cost)} / month - ${formatCurrency(row.yearly_savings)} yearly savings`}
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

const InstanceRow: React.FC<{
  instance: RdsOverviewInstance;
  rightsizing?: RdsFleetRightsizingRow;
  profile: OptimizationProfile;
  onOpen: (instance: RdsOverviewInstance) => void;
  actions: string[];
  selectedAction: string;
  onActionChange: (value: string) => void;
  onExecuteAction: () => void;
  canExecuteAction: boolean;
  isExecutingAction: boolean;
  actionMessage?: string;
}> = ({ instance, rightsizing, profile, onOpen, actions, selectedAction, onActionChange, onExecuteAction, canExecuteAction, isExecutingAction, actionMessage }) => {

  return (
  <div className="overflow-x-auto rounded-2xl">
  <div className="grid min-w-[1450px] w-full grid-cols-[minmax(0,1.8fr)_110px_120px_130px_165px_130px_150px_160px_minmax(240px,1fr)] gap-4 rounded-2xl border border-gray-200 bg-white/90 px-4 py-4 text-left transition hover:border-warning-300 hover:shadow-sm dark:border-gray-700 dark:bg-gray-900/90">
    <button type="button" onClick={() => onOpen(instance)} className="min-w-0 text-left">
      <div className="flex items-center gap-3">
        <span className="flex h-10 w-10 items-center justify-center rounded-2xl bg-warning-50 dark:bg-warning-950/40">
          <ResourceTypeIcon resourceType="rds" size={20} />
        </span>
        <div className="min-w-0">
          <div className="truncate text-sm font-semibold text-gray-900 dark:text-white">{instance.resource_name || instance.resource_id}</div>
          <div className="mt-1 text-xs text-gray-500 dark:text-gray-400">
            {instance.resource_id} · {instance.region || 'unknown region'}
          </div>
        </div>
      </div>
      <div className="mt-3 flex flex-wrap gap-2">
        <span className={`rounded-full px-2.5 py-1 text-[11px] font-semibold uppercase tracking-[0.12em] ${instance.workload.is_graviton ? 'bg-success-50 text-success-700 dark:bg-success-950/40 dark:text-success-300' : 'bg-warning-50 text-warning-700 dark:bg-warning-950/40 dark:text-warning-300'}`}>
          {instance.workload.is_graviton ? 'Graviton' : 'Legacy family'}
        </span>
        <span className={`rounded-full px-2.5 py-1 text-[11px] font-semibold uppercase tracking-[0.12em] ${rightsizingClassificationClass(rightsizing?.classification)}`}>
          {rightsizing?.classification || (rightsizing?.classification === null ? 'No opportunity' : instance.maxops.status)}
        </span>
      </div>
    </button>
    <div>
      <div className="text-[11px] font-semibold uppercase tracking-[0.16em] text-gray-500 dark:text-gray-400">Engine</div>
      <div className="mt-2 text-sm font-semibold text-gray-900 dark:text-white">{instance.engine || 'Unknown'}</div>
      <div className="mt-1 text-xs text-gray-500">{rightsizing?.engine_version || 'Version unknown'}</div>
    </div>
    <div>
      <div className="text-[11px] font-semibold uppercase tracking-[0.16em] text-gray-500 dark:text-gray-400">Deployment</div>
      <div className="mt-2 text-sm font-semibold text-gray-900 dark:text-white">{rightsizing?.deployment || (instance.metadata.multi_az ? 'Multi-AZ' : 'Single-AZ')}</div>
    </div>
    <div>
      <div className="text-[11px] font-semibold uppercase tracking-[0.16em] text-gray-500 dark:text-gray-400">Current class</div>
      <div className="mt-2 text-sm font-semibold text-gray-900 dark:text-white">{instance.db_instance_class || 'Unknown'}</div>
    </div>
    <div>
      <div className="text-[11px] font-semibold uppercase tracking-[0.16em] text-gray-500 dark:text-gray-400">Recommended class</div>
      <div className="mt-2 text-sm font-semibold text-gray-900 dark:text-white">{rightsizing?.target_db_instance_class || 'None'}</div>
      <div className="mt-1 text-xs text-gray-500">{rightsizing?.binding_dimension ? `${titleCase(rightsizing.binding_dimension)} binds` : 'No binding dimension'}</div>
    </div>
    <div>
      <div className="text-[11px] font-semibold uppercase tracking-[0.16em] text-gray-500 dark:text-gray-400">Instance savings</div>
      <div className="mt-2 text-lg font-semibold text-success-700 dark:text-success-300">{rightsizing?.instance_monthly_savings == null ? '—' : `${formatCurrency(adjustOptimizationSavings(rightsizing.instance_monthly_savings, profile))}/mo`}</div>
    </div>
    <div>
      <div className="text-[11px] font-semibold uppercase tracking-[0.16em] text-gray-500 dark:text-gray-400">Storage opportunity</div>
      <div className="mt-2 text-sm font-semibold text-success-700 dark:text-success-300">{rightsizing?.storage_monthly_savings == null ? 'No storage option' : `${formatCurrency(adjustOptimizationSavings(rightsizing.storage_monthly_savings, profile))}/mo`}</div>
      <div className="mt-1 text-xs text-gray-500">{rightsizing?.storage_target?.storage_type || 'Current storage retained'}</div>
    </div>
    <div>
      <div className="text-[11px] font-semibold uppercase tracking-[0.16em] text-gray-500 dark:text-gray-400">Observed days</div>
      <div className="mt-2 text-sm font-semibold text-gray-900 dark:text-white">{rightsizing?.cpu_observed_days == null && rightsizing?.memory_observed_days == null ? 'CPU & memory missing' : rightsizing?.cpu_observed_days == null ? 'CPU missing' : rightsizing?.memory_observed_days == null ? 'Memory missing' : `${Math.min(rightsizing.cpu_observed_days, rightsizing.memory_observed_days).toFixed(1)}d`}</div>
      <div className="mt-1 text-xs text-gray-500 dark:text-gray-400">{rightsizing?.cpu_observed_days == null ? 'CPU missing' : `${rightsizing.cpu_observed_days.toFixed(1)}d CPU`} · {rightsizing?.memory_observed_days == null ? 'Memory missing' : `${rightsizing.memory_observed_days.toFixed(1)}d memory`}</div>
      <div className="mt-1 text-xs text-gray-500 dark:text-gray-400">DB load: {dbLoadLabel(rightsizing?.database_load_status)}</div>
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
        <span className="text-sm text-gray-500 dark:text-gray-400">{titleCase(instance.maxops.recommended_action || 'observe')}</span>
      )}
    </div>
  </div>
  </div>
  );
};

export const RdsOverviewPage: React.FC = () => {
  const navigate = useNavigate();
  const [searchParams, setSearchParams] = useSearchParams();
  const { profile } = useOptimizationProfile();
  const { theme } = useTheme();
  const queryClient = useQueryClient();
  const [search, setSearch] = useState('');
  const [inventorySearch, setInventorySearch] = useState('');
  const requestedSort = searchParams.get('rdsSort') as RdsInventorySortKey | null;
  const [inventorySort, setInventorySort] = useState<RdsInventorySortKey>(requestedSort && RDS_SORT_KEYS.has(requestedSort) ? requestedSort : 'classification');
  const [rightsizingClassification, setRightsizingClassification] = useState(searchParams.get('rdsClassification') || 'all');
  const [selectedTeam, setSelectedTeam] = useState('all');
  const [selectedEngine, setSelectedEngine] = useState('all');
  const [selectedFinding, setSelectedFinding] = useState('all');
  const [actionableOnly, setActionableOnly] = useState(false);
  const [idleOnly, setIdleOnly] = useState(false);
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

  const { data, isLoading, isError } = useQuery<RdsOverviewResponse>('inventory-rds-overview', () => inventoryApi.getRdsOverview());
  const { data: rightsizingRecommendations = [] } = useQuery(
    'rds-rightsizer-list',
    () => rightsizingApi.listRdsRecommendations(),
    { retry: false },
  );
  const rightsizingByInventory = useMemo(
    () => new Map(rightsizingRecommendations.map((item) => [item.inventory_id, item])),
    [rightsizingRecommendations],
  );
  const { data: rdsChecks = [] } = useQuery(['checks', 'rds'], () => checksApi.listChecks('rds'), {
    retry: false,
  });
  const availableTags = useMemo(() => buildSharedTagOptions(data?.instances || []), [data]);

  useEffect(() => {
    const next = new URLSearchParams(searchParams);
    next.set('rdsSort', inventorySort);
    if (rightsizingClassification === 'all') next.delete('rdsClassification');
    else next.set('rdsClassification', rightsizingClassification);
    if (next.toString() !== searchParams.toString()) setSearchParams(next, { replace: true });
  }, [inventorySort, rightsizingClassification, searchParams, setSearchParams]);

  const snoozeInstances = async (instances: RdsOverviewInstance[], snoozedUntil: string, reason: string) => {
    await inventoryApi.snoozeResources({
      resources: instances.map((instance) => ({
        resource_id: instance.resource_id,
        resource_type: 'rds',
        resource_name: instance.resource_name,
        account_id: instance.account_id,
        region: instance.region,
      })),
      snoozed_until: snoozedUntil,
      reason,
    });
    await queryClient.invalidateQueries('inventory-rds-overview');
    await queryClient.invalidateQueries('latest-check-results');
  };

  const removeInstanceSnoozes = async (instances: RdsOverviewInstance[], reason = '') => {
    await inventoryApi.removeSnoozes({
      resources: instances.map((instance) => ({
        resource_id: instance.resource_id,
        resource_type: 'rds',
        resource_name: instance.resource_name,
        account_id: instance.account_id,
        region: instance.region,
      })),
      reason,
    });
    await queryClient.invalidateQueries('inventory-rds-overview');
    await queryClient.invalidateQueries('latest-check-results');
  };

  const filteredInstances = useMemo(() => {
    if (!data) return [];
    const query = search.trim().toLowerCase();
    return data.instances.filter((instance) => {
      const environment = String(instance.tags.env || 'unknown').toLowerCase();
      const team = String(instance.metadata.team || instance.tags.team || 'unknown').toLowerCase();
      const engine = String(instance.engine || 'unknown').toLowerCase();
      const finding = String(instance.maxops.finding_type || 'healthy').toLowerCase();

      if (selectedRegion !== 'all' && String(instance.region || 'unknown').toLowerCase() !== selectedRegion) return false;
      if (selectedEnvironment !== 'all' && environment !== selectedEnvironment) return false;
      if (!matchesSharedTagQuery(instance.tags, tagQuery, tagLogic)) return false;
      if (selectedTeam !== 'all' && team !== selectedTeam) return false;
      if (selectedEngine !== 'all' && engine !== selectedEngine) return false;
      if (selectedFinding !== 'all' && finding !== selectedFinding) return false;
      const rightsizing = rightsizingByInventory.get(instance.inventory_id);
      if (rightsizingClassification !== 'all') {
        const actual = rightsizing?.classification ?? 'NONE';
        if (actual !== rightsizingClassification) return false;
      }
      if (actionableOnly && rightsizing?.classification !== 'ACTIONABLE' && instance.maxops.status !== 'actionable') return false;
      if (idleOnly && instance.maxops.check_id !== 'rds_idle_databases') return false;
      if (!query) return true;

      return [
        instance.resource_id,
        instance.resource_name,
        instance.region,
        instance.engine,
        instance.db_instance_class,
        instance.maxops.title,
        instance.maxops.recommended_action,
        instance.metadata.owner,
        instance.metadata.team,
      ]
        .filter(Boolean)
        .some((value) => String(value).toLowerCase().includes(query));
    });
  }, [actionableOnly, data, idleOnly, rightsizingByInventory, rightsizingClassification, search, selectedEngine, selectedEnvironment, selectedFinding, selectedRegion, selectedTeam, tagLogic, tagQuery]);

  const derived = useMemo(() => {
    let actionable = 0;
    let healthy = 0;
    let graviton = 0;
    let monthlyCost = 0;
    let yearlySavings = 0;
    let totalConnections = 0;
    const engineCounts: Record<string, number> = {};
    const regionCosts: Record<string, { region: string; monthly_cost: number; yearly_savings: number; count: number }> = {};

    filteredInstances.forEach((instance) => {
      const engine = titleCase(String(instance.engine || 'unknown'));
      const region = String(instance.region || 'unknown');
      engineCounts[engine] = (engineCounts[engine] || 0) + 1;
      if (!regionCosts[region]) regionCosts[region] = { region, monthly_cost: 0, yearly_savings: 0, count: 0 };
      regionCosts[region].monthly_cost += instance.workload.monthly_cost_estimate;
      const rightsizing = rightsizingByInventory.get(instance.inventory_id);
      const yearlyPotential = rightsizing
        ? adjustOptimizationSavings(Number(rightsizing.headline_monthly_savings || 0) * 12, profile)
        : adjustOptimizationSavings(instance.maxops.potential_savings_yearly, profile);
      regionCosts[region].yearly_savings += yearlyPotential;
      regionCosts[region].count += 1;
      totalConnections += instance.workload.connections;
      monthlyCost += instance.workload.monthly_cost_estimate;
      yearlySavings += yearlyPotential;
      if (rightsizing?.classification === 'ACTIONABLE' || instance.maxops.status === 'actionable') actionable += 1; else healthy += 1;
      if (instance.workload.is_graviton) graviton += 1;
    });

    return {
      total: filteredInstances.length,
      actionable,
      healthy,
      graviton,
      nonGraviton: Math.max(filteredInstances.length - graviton, 0),
      monthlyCost,
      yearlySavings,
      totalConnections,
      engineRows: Object.entries(engineCounts).map(([name, value]) => ({ name, value })),
      regionRows: Object.values(regionCosts).sort((left, right) => right.monthly_cost - left.monthly_cost),
      scatterRows: filteredInstances.map((instance) => ({
        x: Number(instance.workload.cpu_utilization.toFixed(2)),
        y: Number(instance.workload.connections.toFixed(2)),
        z: Math.max(Number(instance.workload.monthly_cost_estimate.toFixed(0)), 8),
        resourceId: instance.resource_id,
      })),
      costliest: [...filteredInstances].sort((left, right) => right.workload.monthly_cost_estimate - left.workload.monthly_cost_estimate).slice(0, 8),
    };
  }, [filteredInstances, profile, rightsizingByInventory]);

  const inventoryInstances = useMemo(() => {
    const query = inventorySearch.trim().toLowerCase();
    const matches = !query
      ? filteredInstances
      : filteredInstances.filter((instance) =>
          [
            instance.resource_id,
            instance.resource_name,
            instance.region,
            instance.engine,
            instance.db_instance_class,
            instance.maxops.finding_type,
            instance.maxops.title,
            instance.metadata.owner,
            instance.metadata.team,
          ]
            .filter(Boolean)
            .some((value) => String(value).toLowerCase().includes(query))
        );

    return [...matches].sort((left, right) => {
      switch (inventorySort) {
        case 'classification': {
          const leftRightsizing = rightsizingByInventory.get(left.inventory_id);
          const rightRightsizing = rightsizingByInventory.get(right.inventory_id);
          const leftClassification = leftRightsizing?.classification ?? 'NONE';
          const rightClassification = rightRightsizing?.classification ?? 'NONE';
          const classificationDelta = RIGHTSIZING_CLASSIFICATION_ORDER[leftClassification] - RIGHTSIZING_CLASSIFICATION_ORDER[rightClassification];
          if (classificationDelta) return classificationDelta;
          const savingsDelta = Number(rightRightsizing?.headline_monthly_savings || 0) - Number(leftRightsizing?.headline_monthly_savings || 0);
          if (savingsDelta) return savingsDelta;
          return String(left.resource_name || left.resource_id).localeCompare(String(right.resource_name || right.resource_id));
        }
        case 'name':
          return String(left.resource_name || left.resource_id).localeCompare(String(right.resource_name || right.resource_id));
        case 'savings':
          return Number(rightsizingByInventory.get(right.inventory_id)?.headline_monthly_savings || right.maxops.potential_savings_yearly / 12) - Number(rightsizingByInventory.get(left.inventory_id)?.headline_monthly_savings || left.maxops.potential_savings_yearly / 12);
        case 'cpu':
          return right.workload.cpu_utilization - left.workload.cpu_utilization;
        case 'connections':
          return right.workload.connections - left.workload.connections;
        case 'region':
          return String(left.region || 'unknown').localeCompare(String(right.region || 'unknown'));
        case 'cost':
        default:
          return right.workload.monthly_cost_estimate - left.workload.monthly_cost_estimate;
      }
    });
  }, [filteredInstances, inventorySearch, inventorySort, rightsizingByInventory]);

  const actionsByCheckId = useMemo(
    () => new Map(rdsChecks.map((check) => [check.check_id, getActionsForCheck(check)])),
    [rdsChecks]
  );

  const getInstanceActions = useCallback((instance: RdsOverviewInstance) => {
    const mappedActions = actionsByCheckId.get(instance.maxops.check_id || '') || [];
    const recommendedAction = instance.maxops.recommended_action?.trim();
    if (!recommendedAction) return mappedActions;
    return mappedActions.includes(recommendedAction) ? mappedActions : [recommendedAction, ...mappedActions];
  }, [actionsByCheckId]);

  const executeInstanceAction = async (instance: RdsOverviewInstance, actionOverride?: string) => {
    const action = actionOverride || selectedActions[instance.resource_id] || getInstanceActions(instance)[0] || '';
    if (!instance.maxops.check_id || !instance.account_id || !instance.region || !action) {
      setActionMessages((prev) => ({
        ...prev,
        [instance.resource_id]: 'Missing check, account, region, or action for execution.',
      }));
      return;
    }

    setExecutingResources((prev) => new Set(prev).add(instance.resource_id));
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

  const exportInventoryCsv = () => {
    downloadCsv(
      'rds-instance-inventory.csv',
      inventoryInstances.map((instance) => ({
        resource_id: instance.resource_id,
        resource_name: instance.resource_name || '',
        engine: instance.engine || '',
        db_instance_class: instance.db_instance_class || '',
        region: instance.region || '',
        state: instance.state || '',
        finding: instance.maxops.finding_type || '',
        status: instance.maxops.status || '',
        recommended_action: instance.maxops.recommended_action || '',
        monthly_cost_estimate: instance.workload.monthly_cost_estimate,
        cpu_utilization: instance.workload.cpu_utilization,
        connections: instance.workload.connections,
        potential_savings_yearly: instance.maxops.potential_savings_yearly,
        tags: instance.tags || {},
        metadata: instance.metadata || {},
      }))
    );
  };

  if (isLoading) {
    return (
      <Layout>
        <div className="flex min-h-[60vh] items-center justify-center rounded-[32px] border border-gray-200 bg-white/90 dark:border-gray-700 dark:bg-gray-900/90">
          <div className="text-center">
            <div className="text-lg font-semibold text-gray-800 dark:text-gray-100">Loading RDS overview</div>
            <div className="mt-2 text-sm text-gray-500 dark:text-gray-400">Preparing imported database performance and posture views.</div>
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
              <div className="text-lg font-semibold text-danger-900 dark:text-danger-100">RDS overview unavailable</div>
              <div className="mt-2 text-sm text-danger-700 dark:text-danger-200">The imported RDS inventory could not be loaded.</div>
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
    ...(idleOnly ? [{ key: 'idle', label: 'Idle candidates', onRemove: () => setIdleOnly(false) }] : []),
  ];

  return (
    <Layout>
      <div className="space-y-8">
        <section className="overflow-hidden rounded-[32px] border border-gray-200 bg-[radial-gradient(circle_at_top_left,_rgba(22,193,168,0.22),_transparent_32%),radial-gradient(circle_at_bottom_right,_rgba(22,193,168,0.16),_transparent_24%),linear-gradient(135deg,_#f5fdfb_0%,_#ecfdf9_48%,_#f5f7fb_100%)] p-8 shadow-sm dark:border-gray-700 dark:bg-[radial-gradient(circle_at_top_left,_rgba(22,193,168,0.2),_transparent_24%),radial-gradient(circle_at_bottom_right,_rgba(22,193,168,0.16),_transparent_20%),linear-gradient(135deg,_rgba(14,24,48,0.98)_0%,_rgba(24,33,64,0.97)_48%,_rgba(14,24,48,0.98)_100%)]">
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
                  <ResourceTypeIcon resourceType="rds" size={16} />
                  RDS Fleet Overview
                </div>
                <h1 className="mt-4 text-4xl font-semibold tracking-tight text-gray-950 dark:text-gray-50">Database cost posture and workload shape</h1>
                <p className="mt-3 max-w-3xl text-sm leading-6 text-gray-600 dark:text-gray-300">
                  Review imported instance classes, engine distribution, idle candidates, and migration opportunities across the current RDS estate.
                </p>
                {chips.length > 0 ? (
                  <div className="mt-4 flex flex-wrap gap-2">
                    {chips.map((chip) => (
                      <button
                        key={chip.key}
                        type="button"
                        onClick={chip.onRemove}
                        className="inline-flex items-center gap-1.5 rounded-full border border-gray-200 bg-white/80 px-3 py-1 text-xs font-semibold uppercase tracking-[0.12em] text-gray-600 transition hover:border-primary-300 hover:bg-white hover:text-gray-900 dark:border-gray-700 dark:bg-gray-900/80 dark:text-gray-200 dark:hover:border-primary-500 dark:hover:text-white"
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
          searchPlaceholder="Search DB identifier, engine, class, finding, owner, or team"
          onSearchChange={setSearch}
          savedFilterSelect={{
            id: 'rds-saved-filter',
            label: 'Saved filter',
            value: activeSavedFilterId || '',
            options: [
              { label: 'Custom filters', value: '' },
              ...savedFilters.map((filter) => ({ label: filter.name, value: filter.id })),
            ],
            onChange: applySavedFilter,
          }}
          selects={[
            { id: 'rds-region', label: 'Region', value: selectedRegion, options: [{ label: 'All regions', value: 'all' }, ...data.dimensions.regions.map((row) => ({ label: `${row.key} (${row.count})`, value: row.key }))], onChange: setSelectedRegion },
            { id: 'rds-env', label: 'Environment', value: selectedEnvironment, options: [{ label: 'All environments', value: 'all' }, ...data.dimensions.environments.map((row) => ({ label: `${titleCase(row.key)} (${row.count})`, value: row.key }))], onChange: setSelectedEnvironment },
            { id: 'rds-team', label: 'Team', value: selectedTeam, options: [{ label: 'All teams', value: 'all' }, ...data.dimensions.teams.map((row) => ({ label: `${titleCase(row.key)} (${row.count})`, value: row.key }))], onChange: setSelectedTeam },
            { id: 'rds-engine', label: 'Engine', value: selectedEngine, options: [{ label: 'All engines', value: 'all' }, ...data.dimensions.engines.map((row) => ({ label: `${titleCase(row.key)} (${row.count})`, value: row.key }))], onChange: setSelectedEngine },
            { id: 'rds-finding', label: 'Finding', value: selectedFinding, options: [{ label: 'All findings', value: 'all' }, ...data.findings_breakdown.map((row) => ({ label: `${titleCase(row.key)} (${row.count})`, value: row.key }))], onChange: setSelectedFinding },
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
            { id: 'rds-actionable', label: 'Actionable only', checked: actionableOnly, onChange: setActionableOnly },
            { id: 'rds-idle', label: 'Idle candidates', checked: idleOnly, onChange: setIdleOnly },
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
            setIdleOnly(false);
          }}
          resetDisabled={!activeSavedFilterId && !search.trim() && selectedRegion === 'all' && selectedEnvironment === 'all' && !tagQuery.trim() && tagLogic === 'and' && selectedTeam === 'all' && selectedEngine === 'all' && selectedFinding === 'all' && !actionableOnly && !idleOnly}
        />

        <div className="grid gap-4 md:grid-cols-2 xl:grid-cols-4">
          <SummaryTile label="Instances" value={formatLargeNumber(derived.total)} sublabel={`${adjustOptimizationCount(derived.actionable, profile)} actionable · ${Math.max(derived.total - adjustOptimizationCount(derived.actionable, profile), 0)} healthy`} icon={<Database size={22} />} />
          <SummaryTile label="Monthly Cost" value={formatCurrency(derived.monthlyCost)} sublabel="Estimated imported instance cost" icon={<Wallet size={22} />} />
          <SummaryTile label="Connections" value={formatLargeNumber(Math.round(derived.totalConnections))} sublabel="Current connection footprint across visible DBs" icon={<Cpu size={22} />} />
          <SummaryTile label="Potential Monthly Savings" value={formatCurrency(derived.yearlySavings / 12)} sublabel="Independent recommendations; class and storage are not additive" icon={<Sparkles size={22} />} />
        </div>

        <ResourceActionPanel
          title="Instance Inventory"
          subtitle={`${inventoryInstances.length} visible`}
          resourceLabel="RDS instance"
          items={inventoryInstances}
          getId={(instance) => instance.resource_id}
          getName={(instance) => instance.resource_name || instance.resource_id}
          getActions={getInstanceActions}
          canExecute={(instance, action) => Boolean(instance.maxops.check_id && instance.account_id && instance.region && action)}
          executeAction={(instance, action) => executeInstanceAction(instance, action)}
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
                  placeholder="Search visible databases"
                  className="w-full rounded-xl border border-gray-200 bg-white py-2 pl-9 pr-3 text-sm text-gray-700 outline-none transition focus:border-warning-300 focus:ring-2 focus:ring-warning-100 dark:border-gray-700 dark:bg-gray-900/80 dark:text-gray-100 dark:focus:border-warning-500 dark:focus:ring-warning-900/40"
                />
              </div>
              <select
                value={inventorySort}
                onChange={(event) => setInventorySort(event.target.value as RdsInventorySortKey)}
                className="rounded-xl border border-gray-200 bg-white px-3 py-2 text-sm text-gray-700 outline-none transition focus:border-warning-300 focus:ring-2 focus:ring-warning-100 dark:border-gray-700 dark:bg-gray-900/80 dark:text-gray-100 dark:focus:border-warning-500 dark:focus:ring-warning-900/40"
              >
                <option value="classification">Sort by classification</option>
                <option value="cost">Sort by monthly cost</option>
                <option value="savings">Sort by yearly savings</option>
                <option value="cpu">Sort by CPU</option>
                <option value="connections">Sort by connections</option>
                <option value="name">Sort by name</option>
                <option value="region">Sort by region</option>
              </select>
              <select
                value={rightsizingClassification}
                onChange={(event) => setRightsizingClassification(event.target.value)}
                aria-label="Rightsizing classification"
                className="rounded-xl border border-gray-200 bg-white px-3 py-2 text-sm text-gray-700 outline-none transition focus:border-warning-300 focus:ring-2 focus:ring-warning-100 dark:border-gray-700 dark:bg-gray-900/80 dark:text-gray-100"
              >
                <option value="all">All classifications</option>
                <option value="ACTIONABLE">Actionable</option>
                <option value="CONDITIONAL">Conditional</option>
                <option value="INSUFFICIENT_DATA">Insufficient data</option>
                <option value="DEFERRED">Deferred</option>
                <option value="NONE">No recommendation</option>
              </select>
              <button
                type="button"
                onClick={exportInventoryCsv}
                disabled={inventoryInstances.length === 0}
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
            { header: 'Engine', render: (instance) => instance.engine || 'unknown' },
            { header: 'Class', render: (instance) => instance.db_instance_class || 'unknown' },
            { header: 'Region', render: (instance) => instance.region || 'unknown' },
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
          renderItem={(instance) => (
              <InstanceRow
                key={instance.resource_id}
                instance={instance}
                rightsizing={rightsizingByInventory.get(instance.inventory_id)}
                profile={profile}
                onOpen={(current) => navigate(`/dashboard/resources/rds/instances/${encodeURIComponent(current.resource_id)}`)}
                actions={getInstanceActions(instance)}
                selectedAction={selectedActions[instance.resource_id] || getInstanceActions(instance)[0] || ''}
                onActionChange={(value) =>
                  setSelectedActions((prev) => ({
                    ...prev,
                    [instance.resource_id]: value,
                  }))
                }
                onExecuteAction={() => executeInstanceAction(instance)}
                canExecuteAction={Boolean(instance.maxops.check_id && instance.account_id && instance.region && (selectedActions[instance.resource_id] || getInstanceActions(instance)[0] || ''))}
                isExecutingAction={executingResources.has(instance.resource_id)}
                actionMessage={actionMessages[instance.resource_id]}
              />
            )}
        />

        <RegionsPanel rows={derived.regionRows} onOpenRegion={(region) => setSelectedRegion(region.toLowerCase())} />

        <div className="grid gap-6 xl:grid-cols-2">
          <ChartCard title="Engine Mix" subtitle="Current distribution across the filtered RDS fleet">
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

          <ChartCard title="Architecture Split" subtitle="Graviton adoption versus legacy families in the visible scope">
            <div className="grid gap-4 md:grid-cols-2">
              <div className="rounded-3xl border border-success-200 bg-success-50/80 p-5 dark:border-success-900/50 dark:bg-success-950/30">
                <div className="text-xs font-semibold uppercase tracking-[0.16em] text-success-700 dark:text-success-300">Graviton</div>
                <div className="mt-3 text-4xl font-semibold text-success-900 dark:text-success-100">{derived.graviton}</div>
              </div>
              <div className="rounded-3xl border border-warning-200 bg-warning-50/80 p-5 dark:border-warning-900/50 dark:bg-warning-950/30">
                <div className="text-xs font-semibold uppercase tracking-[0.16em] text-warning-700 dark:text-warning-300">Legacy</div>
                <div className="mt-3 text-4xl font-semibold text-warning-900 dark:text-warning-100">{derived.nonGraviton}</div>
              </div>
            </div>
          </ChartCard>
        </div>

        <div className="grid gap-6 xl:grid-cols-2">
          <ChartCard title="Regional Cost vs Savings" subtitle="Where spend and remediation upside cluster together">
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

          <ChartCard title="CPU vs Connections" subtitle="Upper-right databases combine active load with heavier connection pressure">
            <div className="h-80">
              <ResponsiveContainer width="100%" height="100%">
                <ScatterChart margin={{ top: 8, right: 12, left: 0, bottom: 0 }}>
                  <CartesianGrid strokeDasharray="3 3" stroke={chartGridColor(theme)} opacity={0.35} />
                  <XAxis type="number" dataKey="x" name="CPU" unit="%" stroke={chartTickColor(theme)} tickLine={false} axisLine={false} />
                  <YAxis type="number" dataKey="y" name="Connections" stroke={chartTickColor(theme)} tickLine={false} axisLine={false} />
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
                        onClick={() => navigate(`/dashboard/resources/rds/instances/${encodeURIComponent(props.payload?.resourceId || '')}`)}
                      />
                    )}
                  />
                </ScatterChart>
              </ResponsiveContainer>
            </div>
          </ChartCard>
        </div>

        <div className="grid gap-6 xl:grid-cols-3">
          <ChartCard title="Top Cost Drivers" subtitle="Highest monthly cost RDS instances in the current filtered scope">
            <div className="space-y-3">
              {derived.costliest.map((instance) => (
                <button
                  key={instance.resource_id}
                  type="button"
                  onClick={() => navigate(`/dashboard/resources/rds/instances/${encodeURIComponent(instance.resource_id)}`)}
                  className="flex w-full items-center justify-between rounded-2xl border border-gray-200 bg-gray-50 px-4 py-3 text-left transition hover:border-primary-300 hover:bg-white dark:border-gray-700 dark:bg-gray-800/70 dark:hover:bg-gray-800"
                >
                  <div className="min-w-0">
                    <div className="truncate text-sm font-semibold text-gray-900 dark:text-white">{instance.resource_name || instance.resource_id}</div>
                    <div className="mt-1 text-xs text-gray-500 dark:text-gray-400">{instance.engine} · {instance.db_instance_class}</div>
                  </div>
                  <div className="text-sm font-semibold text-gray-900 dark:text-white">{formatCurrency(instance.workload.monthly_cost_estimate)}</div>
                </button>
              ))}
            </div>
          </ChartCard>

          <ChartCard title="Finding Distribution" subtitle="Which RDS findings dominate the visible estate">
            <div className="h-80">
              <ResponsiveContainer width="100%" height="100%">
                <BarChart data={data.findings_breakdown.map((row) => ({ name: titleCase(row.key), value: row.count }))} margin={{ top: 8, right: 12, left: 0, bottom: 0 }}>
                  <CartesianGrid strokeDasharray="3 3" stroke={chartGridColor(theme)} opacity={0.35} />
                  <XAxis dataKey="name" stroke={chartTickColor(theme)} tickLine={false} axisLine={false} />
                  <YAxis stroke={chartTickColor(theme)} tickLine={false} axisLine={false} />
                  <Tooltip />
                  <Bar dataKey="value" radius={[8, 8, 0, 0]}>
                    {data.findings_breakdown.map((row, index) => <Cell key={row.key} fill={CHART_COLORS[index % CHART_COLORS.length]} />)}
                  </Bar>
                </BarChart>
              </ResponsiveContainer>
            </div>
          </ChartCard>

          <ChartCard title="Snapshot Health" subtitle="Quick read on hidden migration and idle patterns">
            <div className="grid gap-4">
              <div className="rounded-3xl border border-gray-200 bg-gray-50/90 p-5 dark:border-gray-700 dark:bg-gray-800/70">
                <div className="text-xs font-semibold uppercase tracking-[0.16em] text-gray-500 dark:text-gray-400">Idle candidates</div>
                <div className="mt-3 text-4xl font-semibold text-gray-900 dark:text-white">
                  {filteredInstances.filter((instance) => instance.maxops.check_id === 'rds_idle_databases').length}
                </div>
              </div>
              <div className="rounded-3xl border border-gray-200 bg-gray-50/90 p-5 dark:border-gray-700 dark:bg-gray-800/70">
                <div className="text-xs font-semibold uppercase tracking-[0.16em] text-gray-500 dark:text-gray-400">Migration candidates</div>
                <div className="mt-3 text-4xl font-semibold text-gray-900 dark:text-white">
                  {filteredInstances.filter((instance) => instance.maxops.check_id === 'rds_non_graviton_instance_class').length}
                </div>
              </div>
            </div>
          </ChartCard>
        </div>
      </div>
    </Layout>
  );
};
