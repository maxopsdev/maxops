import React, { useEffect, useMemo, useState } from 'react';
import { useQuery } from 'react-query';
import { ArrowLeft, ChevronDown, Cpu, Download, Search, ShieldCheck, TrendingDown, Wallet } from 'lucide-react';
import { useNavigate } from 'react-router-dom';
import { Card } from '@/components/common/Card';
import { Layout } from '@/components/layout/Layout';
import { ResourceActionPanel } from '@/components/common/ResourceActionPanel';
import { ResourcePageFilters } from '@/components/common/ResourcePageFilters';
import { SharedTagFilterField } from '@/components/common/SharedTagFilterField';
import { inventoryApi, type AsgOverviewResource } from '@/services/inventory';
import { rightsizingApi, type AsgRightsizingResponse } from '@/services/recommendations';
import { useSharedOverviewFilters } from '@/stores/sharedOverviewFilters';
import { buildSharedTagOptions, matchesSharedTagSelections, parseSharedTagQuery } from '@/utils/sharedOverviewFilters';
import { downloadCsv } from '@/utils/csv';
import { resolveAsgSavings } from '@/utils/savings';

const formatCurrency = (value: number) =>
  new Intl.NumberFormat('en-US', { style: 'currency', currency: 'USD', maximumFractionDigits: 0 }).format(value);

const formatNumber = (value: number) => new Intl.NumberFormat('en-US').format(value);

const titleCase = (value: string) =>
  value
    .split(/[_\s-]+/)
    .filter(Boolean)
    .map((part) => part.charAt(0).toUpperCase() + part.slice(1))
    .join(' ');

const statusTone = (classification: AsgRightsizingResponse['classification']) => {
  if (classification === 'ACTIONABLE') return 'bg-success-100 text-success-800 dark:bg-success-950/50 dark:text-success-300';
  if (classification === 'CONDITIONAL' || classification === 'PREVIEW') return 'bg-warning-100 text-warning-800 dark:bg-warning-950/50 dark:text-warning-300';
  if (classification === 'INSUFFICIENT_DATA' || classification === 'DEFERRED') return 'bg-gray-200 text-gray-700 dark:bg-gray-800 dark:text-gray-300';
  return 'bg-gray-100 text-gray-700 dark:bg-gray-800 dark:text-gray-300';
};

const SummaryTile: React.FC<{ label: string; value: string; sublabel: string; icon: React.ReactNode }> = ({ label, value, sublabel, icon }) => (
  <Card className="border-gray-200 bg-white/95 dark:border-gray-700 dark:bg-gray-900/95">
    <div className="flex items-start justify-between gap-4">
      <div>
        <div className="text-xs font-semibold uppercase tracking-[0.18em] text-gray-500 dark:text-gray-400">{label}</div>
        <div className="mt-3 text-3xl font-semibold tracking-tight text-gray-900 dark:text-white">{value}</div>
        <div className="mt-2 text-sm text-gray-500 dark:text-gray-400">{sublabel}</div>
      </div>
      <div className="rounded-2xl bg-warning-50 p-3 text-warning-700 dark:bg-warning-950/50 dark:text-warning-300">{icon}</div>
    </div>
  </Card>
);

type JoinedRow = {
  resource: AsgOverviewResource;
  recommendation?: AsgRightsizingResponse;
};

type AsgInventorySortKey = 'savings' | 'name' | 'region' | 'desired' | 'instanceType';

export const AsgOverviewPage: React.FC = () => {
  const navigate = useNavigate();
  const [searchQuery, setSearchQuery] = useState('');
  const [selectedRegions, setSelectedRegions] = useState<string[]>([]);
  const [selectedEnvironments, setSelectedEnvironments] = useState<string[]>([]);
  const [selectedTags, setSelectedTags] = useState<string[]>([]);
  const [selectedClassification, setSelectedClassification] = useState<'all' | 'ACTIONABLE' | 'CONDITIONAL' | 'PREVIEW' | 'INSUFFICIENT_DATA' | 'DEFERRED'>('all');
  const [selectedState, setSelectedState] = useState('all');
  const [selectedInstanceType, setSelectedInstanceType] = useState('all');
  const [showActionableOnly, setShowActionableOnly] = useState(false);
  const [showPotentialSavingsOnly, setShowPotentialSavingsOnly] = useState(false);
  const [regionDropdownOpen, setRegionDropdownOpen] = useState(false);
  const [environmentDropdownOpen, setEnvironmentDropdownOpen] = useState(false);
  const [inventorySearch, setInventorySearch] = useState('');
  const [inventorySort, setInventorySort] = useState<AsgInventorySortKey>('savings');

  const {
    region: sharedRegion,
    environment: sharedEnvironment,
    tagQuery: sharedTagQuery,
    tagLogic: sharedTagLogic,
    activeSavedFilterId,
    savedFilters,
    setTagLogic: setSharedTagLogic,
    applySavedFilter,
    clearActiveSavedFilter,
    reset: resetSharedOverviewFilters,
  } = useSharedOverviewFilters();

  const overviewQuery = useQuery('asg-overview', () => inventoryApi.getAsgOverview());
  const recommendationsQuery = useQuery('asg-recommendations', () => rightsizingApi.listAsgRecommendations());

  useEffect(() => {
    setSelectedRegions(sharedRegion !== 'all' ? [sharedRegion] : []);
  }, [sharedRegion]);

  useEffect(() => {
    setSelectedEnvironments(sharedEnvironment !== 'all' ? [sharedEnvironment] : []);
  }, [sharedEnvironment]);

  useEffect(() => {
    setSelectedTags(parseSharedTagQuery(sharedTagQuery));
  }, [sharedTagQuery]);

  const rows = useMemo<JoinedRow[]>(() => {
    const resources = overviewQuery.data?.resources ?? [];
    const recommendations = recommendationsQuery.data ?? [];
    const byInventoryId = new Map<number, AsgRightsizingResponse>(recommendations.map((item) => [item.inventory_id, item]));
    return resources.map((resource) => ({
      resource,
      recommendation: byInventoryId.get(resource.inventory_id),
    }));
  }, [overviewQuery.data, recommendationsQuery.data]);

  const availableRegions = useMemo(
    () => Array.from(new Set(rows.map(({ resource }) => resource.region || 'unknown'))).sort((a, b) => a.localeCompare(b)),
    [rows]
  );
  const availableEnvironments = useMemo(
    () => Array.from(new Set(rows.map(({ resource }) => String(resource.tags.env || resource.metadata.environment || 'unknown').toLowerCase()))).sort((a, b) => a.localeCompare(b)),
    [rows]
  );
  const availableStates = useMemo(
    () => Array.from(new Set(rows.map(({ resource }) => String(resource.state || 'unknown').toLowerCase()))).sort((a, b) => a.localeCompare(b)),
    [rows]
  );
  const availableInstanceTypes = useMemo(
    () => Array.from(new Set(rows.map(({ resource }) => resource.instance_type || 'unknown'))).sort((a, b) => a.localeCompare(b)),
    [rows]
  );
  const availableTags = useMemo(
    () => buildSharedTagOptions(rows.map(({ resource }) => ({ tags: resource.tags }))),
    [rows]
  );

  const filteredRows = useMemo(() => {
    const normalizedQuery = searchQuery.trim().toLowerCase();
    return rows.filter(({ resource, recommendation }) => {
      const environment = String(resource.tags.env || resource.metadata.environment || 'unknown').toLowerCase();
      const classification = recommendation?.classification || null;
      const yearlySavings = recommendation?.tiers.balanced?.yearly_savings ?? resource.maxops.potential_savings_yearly ?? 0;

      if (selectedClassification !== 'all' && classification !== selectedClassification) return false;
      if (selectedState !== 'all' && String(resource.state || 'unknown').toLowerCase() !== selectedState) return false;
      if (selectedInstanceType !== 'all' && (resource.instance_type || 'unknown') !== selectedInstanceType) return false;
      if (selectedRegions.length > 0 && !selectedRegions.includes(resource.region || 'unknown')) return false;
      if (selectedEnvironments.length > 0 && !selectedEnvironments.includes(environment)) return false;
      if (showActionableOnly && classification !== 'ACTIONABLE' && resource.maxops.status !== 'actionable') return false;
      if (showPotentialSavingsOnly && yearlySavings <= 0) return false;
      if (selectedTags.length > 0 && !matchesSharedTagSelections(resource.tags, selectedTags, sharedTagLogic)) return false;

      if (!normalizedQuery) return true;
      return [
        resource.resource_id,
        resource.resource_name,
        resource.region,
        resource.instance_type,
        resource.platform_normalized,
        resource.state,
        resource.maxops.finding_type,
        classification,
      ].some((field) => String(field || '').toLowerCase().includes(normalizedQuery));
    });
  }, [
    rows,
    searchQuery,
    selectedClassification,
    selectedState,
    selectedInstanceType,
    selectedRegions,
    selectedEnvironments,
    selectedTags,
    sharedTagLogic,
    showActionableOnly,
    showPotentialSavingsOnly,
  ]);

  const inventoryRows = useMemo(() => {
    const query = inventorySearch.trim().toLowerCase();
    const matches = !query
      ? filteredRows
      : filteredRows.filter(({ resource, recommendation }) =>
          [
            resource.resource_id,
            resource.resource_name,
            resource.region,
            resource.instance_type,
            resource.platform_normalized,
            recommendation?.classification,
            resource.maxops.finding_type,
          ].some((field) => String(field || '').toLowerCase().includes(query))
        );

    return [...matches].sort((left, right) => {
      switch (inventorySort) {
        case 'name':
          return String(left.resource.resource_name || left.resource.resource_id).localeCompare(String(right.resource.resource_name || right.resource.resource_id));
        case 'region':
          return String(left.resource.region || '').localeCompare(String(right.resource.region || ''));
        case 'desired':
          return right.resource.capacity.desired_capacity - left.resource.capacity.desired_capacity;
        case 'instanceType':
          return String(left.resource.instance_type || '').localeCompare(String(right.resource.instance_type || ''));
        case 'savings':
        default: {
          const leftSavings = left.recommendation?.tiers.balanced?.yearly_savings ?? left.resource.maxops.potential_savings_yearly ?? 0;
          const rightSavings = right.recommendation?.tiers.balanced?.yearly_savings ?? right.resource.maxops.potential_savings_yearly ?? 0;
          return rightSavings - leftSavings;
        }
      }
    });
  }, [filteredRows, inventorySearch, inventorySort]);

  const visibleSavings = filteredRows.reduce(
    (sum, row) =>
      sum +
      resolveAsgSavings(
        row.recommendation?.tiers.balanced?.yearly_savings,
        row.resource.maxops.potential_savings_yearly,
      ).value,
    0,
  );
  // The dashboard prices every group with the checks' flat share of cost. Here
  // a rightsizer recommendation, where one exists, is priced from a specific
  // target configuration instead -- so the two totals legitimately differ.
  const refinedRowCount = filteredRows.filter(
    (row) => row.recommendation?.tiers.balanced?.yearly_savings != null,
  ).length;
  const visibleDesiredCapacity = filteredRows.reduce((sum, row) => sum + row.resource.capacity.desired_capacity, 0);

  const regionFilterLabel = selectedRegions.length === 0 ? 'All regions' : selectedRegions.length === 1 ? selectedRegions[0] : `${selectedRegions.length} regions`;
  const environmentFilterLabel = selectedEnvironments.length === 0 ? 'All environments' : selectedEnvironments.length === 1 ? titleCase(selectedEnvironments[0]) : `${selectedEnvironments.length} environments`;

  const showResetButton =
    Boolean(activeSavedFilterId) ||
    Boolean(searchQuery.trim()) ||
    selectedRegions.length > 0 ||
    selectedEnvironments.length > 0 ||
    selectedTags.length > 0 ||
    sharedTagLogic !== 'and' ||
    selectedClassification !== 'all' ||
    selectedState !== 'all' ||
    selectedInstanceType !== 'all' ||
    showActionableOnly ||
    showPotentialSavingsOnly;

  const clearOverviewFilters = () => {
    resetSharedOverviewFilters();
    setSearchQuery('');
    setSelectedRegions([]);
    setSelectedEnvironments([]);
    setSelectedTags([]);
    setSelectedClassification('all');
    setSelectedState('all');
    setSelectedInstanceType('all');
    setShowActionableOnly(false);
    setShowPotentialSavingsOnly(false);
    setRegionDropdownOpen(false);
    setEnvironmentDropdownOpen(false);
    setInventorySearch('');
    setInventorySort('savings');
  };

  const exportInventoryCsv = () => {
    downloadCsv(
      'asg-inventory.csv',
      inventoryRows.map(({ resource, recommendation }) => ({
        resource_id: resource.resource_id,
        resource_name: resource.resource_name || '',
        region: resource.region || '',
        state: resource.state || '',
        instance_type: resource.instance_type || '',
        platform: resource.platform_normalized || '',
        min_size: resource.capacity.min_size,
        desired_capacity: resource.capacity.desired_capacity,
        max_size: resource.capacity.max_size,
        classification: recommendation?.classification || '',
        finding: resource.maxops.finding_type || 'healthy',
        yearly_savings: recommendation?.tiers.balanced?.yearly_savings ?? resource.maxops.potential_savings_yearly ?? 0,
      }))
    );
  };

  const topFilterSelects = [
    {
      id: 'asg-classification',
      label: 'Classification',
      value: selectedClassification,
      options: [
        { label: 'All classifications', value: 'all' },
        { label: 'Actionable', value: 'ACTIONABLE' },
        { label: 'Conditional', value: 'CONDITIONAL' },
        { label: 'Preview', value: 'PREVIEW' },
        { label: 'Insufficient data', value: 'INSUFFICIENT_DATA' },
        { label: 'Deferred', value: 'DEFERRED' },
      ],
      onChange: (value: string) => setSelectedClassification(value as typeof selectedClassification),
    },
    {
      id: 'asg-state',
      label: 'State',
      value: selectedState,
      options: [{ label: 'All states', value: 'all' }, ...availableStates.map((value) => ({ label: titleCase(value), value }))],
      onChange: (value: string) => setSelectedState(value),
    },
    {
      id: 'asg-instance-type',
      label: 'Instance type',
      value: selectedInstanceType,
      options: [{ label: 'All instance types', value: 'all' }, ...availableInstanceTypes.map((value) => ({ label: value, value }))],
      onChange: (value: string) => setSelectedInstanceType(value),
    },
  ];

  const topFilterToggles = [
    { id: 'asg-actionable', label: 'Actionable only', checked: showActionableOnly, onChange: setShowActionableOnly },
    { id: 'asg-savings', label: 'Has savings', checked: showPotentialSavingsOnly, onChange: setShowPotentialSavingsOnly },
  ];

  if (overviewQuery.isLoading || recommendationsQuery.isLoading) {
    return (
      <Layout>
        <div className="flex min-h-[320px] items-center justify-center text-sm text-gray-500 dark:text-gray-400">Loading ASG overview...</div>
      </Layout>
    );
  }

  if (overviewQuery.isError) {
    return (
      <Layout>
        <Card className="border-danger-200 bg-danger-50 text-danger-900 dark:border-danger-900/60 dark:bg-danger-950/30 dark:text-danger-100">
          Failed to load ASG overview data.
        </Card>
      </Layout>
    );
  }

  const summary = overviewQuery.data?.summary;

  return (
    <Layout>
      <div className="space-y-6">
        <section className="overflow-hidden rounded-[32px] border border-gray-200 bg-[radial-gradient(circle_at_top_left,_rgba(245,166,35,0.24),_transparent_34%),linear-gradient(135deg,_#fffdf7_0%,_#fffbeb_42%,_#ecfdf9_100%)] p-8 shadow-sm dark:border-gray-700 dark:bg-[radial-gradient(circle_at_top_left,_rgba(245,166,35,0.14),_transparent_28%),linear-gradient(135deg,_rgba(14,24,48,0.98)_0%,_rgba(24,33,64,0.96)_46%,_rgba(14,24,48,0.98)_100%)]">
          <div className="flex flex-col gap-6 xl:flex-row xl:items-end xl:justify-between">
            <div>
              <button
                type="button"
                onClick={() => navigate('/dashboard')}
                className="mb-5 inline-flex items-center gap-2 rounded-full border border-gray-200 bg-white/80 px-4 py-2 text-sm font-medium text-gray-700 transition hover:bg-white dark:border-gray-600 dark:bg-gray-900/60 dark:text-gray-100 dark:hover:bg-gray-900"
              >
                <ArrowLeft className="h-4 w-4" />
                Dashboard
              </button>
              <div className="text-xs font-semibold uppercase tracking-[0.28em] text-gray-500 dark:text-gray-400">ASG Inventory Workspace</div>
              <h1 className="mt-3 text-4xl font-semibold tracking-tight text-gray-950 dark:text-gray-50">ASG Overview</h1>
              <p className="mt-3 max-w-3xl text-sm text-gray-600 dark:text-gray-300">
                Imported Auto Scaling Groups with current rightsizer classifications, capacity baselines, and balanced target recommendations.
              </p>
            </div>
          </div>
        </section>

        <ResourcePageFilters
          searchValue={searchQuery}
          searchPlaceholder="Search group, region, state, instance type, tag, or finding"
          onSearchChange={setSearchQuery}
          savedFilterSelect={{
            id: 'asg-saved-filter',
            label: 'Saved filter',
            value: activeSavedFilterId || '',
            options: [{ label: 'Custom filters', value: '' }, ...savedFilters.map((filter) => ({ label: filter.name, value: filter.id }))],
            onChange: applySavedFilter,
          }}
          selects={topFilterSelects}
          toggles={topFilterToggles}
          onReset={clearOverviewFilters}
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
              {regionDropdownOpen ? (
                <div className="absolute left-0 top-full z-20 mt-2 w-full rounded-2xl border border-gray-200 bg-white p-3 shadow-xl dark:border-gray-700 dark:bg-gray-900">
                  <div className="mb-3 flex items-center justify-between">
                    <div className="text-xs font-semibold uppercase tracking-[0.18em] text-gray-500 dark:text-gray-400">Select regions</div>
                    <button type="button" onClick={() => setRegionDropdownOpen(false)} className="text-xs font-semibold uppercase tracking-[0.14em] text-gray-500 hover:text-gray-700 dark:text-gray-400 dark:hover:text-gray-200">Close</button>
                  </div>
                  <div className="mb-3 flex gap-2">
                    <button type="button" onClick={() => { clearActiveSavedFilter(); setSelectedRegions([]); }} className="rounded-full bg-gray-100 px-3 py-1 text-xs font-medium text-gray-700 transition hover:bg-gray-200 dark:bg-gray-800 dark:text-gray-200">All</button>
                    <button type="button" onClick={() => { clearActiveSavedFilter(); setSelectedRegions(availableRegions); }} className="rounded-full bg-warning-100 px-3 py-1 text-xs font-medium text-warning-800 transition hover:bg-warning-200">Select all</button>
                  </div>
                  <div className="max-h-64 space-y-2 overflow-y-auto pr-1">
                    {availableRegions.map((region) => {
                      const checked = selectedRegions.includes(region);
                      return (
                        <label key={region} className="flex cursor-pointer items-center justify-between rounded-xl px-3 py-2 text-sm text-gray-700 transition hover:bg-gray-50 dark:text-gray-200 dark:hover:bg-gray-800">
                          <span>{region}</span>
                          <input
                            type="checkbox"
                            checked={checked}
                            onChange={() => {
                              clearActiveSavedFilter();
                              setSelectedRegions((current) => current.includes(region) ? current.filter((item) => item !== region) : [...current, region]);
                            }}
                            className="h-4 w-4 rounded border-gray-300 text-warning-500 focus:ring-warning-400"
                          />
                        </label>
                      );
                    })}
                  </div>
                </div>
              ) : null}
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
              {environmentDropdownOpen ? (
                <div className="absolute left-0 top-full z-20 mt-2 w-full rounded-2xl border border-gray-200 bg-white p-3 shadow-xl dark:border-gray-700 dark:bg-gray-900">
                  <div className="mb-3 flex items-center justify-between">
                    <div className="text-xs font-semibold uppercase tracking-[0.18em] text-gray-500 dark:text-gray-400">Select environments</div>
                    <button type="button" onClick={() => setEnvironmentDropdownOpen(false)} className="text-xs font-semibold uppercase tracking-[0.14em] text-gray-500 hover:text-gray-700 dark:text-gray-400 dark:hover:text-gray-200">Close</button>
                  </div>
                  <div className="mb-3 flex gap-2">
                    <button type="button" onClick={() => { clearActiveSavedFilter(); setSelectedEnvironments([]); }} className="rounded-full bg-gray-100 px-3 py-1 text-xs font-medium text-gray-700 transition hover:bg-gray-200 dark:bg-gray-800 dark:text-gray-200">All</button>
                    <button type="button" onClick={() => { clearActiveSavedFilter(); setSelectedEnvironments(availableEnvironments); }} className="rounded-full bg-warning-100 px-3 py-1 text-xs font-medium text-warning-800 transition hover:bg-warning-200">Select all</button>
                  </div>
                  <div className="max-h-64 space-y-2 overflow-y-auto pr-1">
                    {availableEnvironments.map((environment) => {
                      const checked = selectedEnvironments.includes(environment);
                      return (
                        <label key={environment} className="flex cursor-pointer items-center justify-between rounded-xl px-3 py-2 text-sm text-gray-700 transition hover:bg-gray-50 dark:text-gray-200 dark:hover:bg-gray-800">
                          <span>{titleCase(environment)}</span>
                          <input
                            type="checkbox"
                            checked={checked}
                            onChange={() => {
                              clearActiveSavedFilter();
                              setSelectedEnvironments((current) => current.includes(environment) ? current.filter((item) => item !== environment) : [...current, environment]);
                            }}
                            className="h-4 w-4 rounded border-gray-300 text-warning-500 focus:ring-warning-400"
                          />
                        </label>
                      );
                    })}
                  </div>
                </div>
              ) : null}
            </div>

            <SharedTagFilterField
              selectedTags={selectedTags}
              availableTags={availableTags}
              tagLogic={sharedTagLogic}
              onChange={(tags) => { clearActiveSavedFilter(); setSelectedTags(tags); }}
              onTagLogicChange={(logic) => { clearActiveSavedFilter(); setSharedTagLogic(logic); }}
            />
          </>
        </ResourcePageFilters>

        <div className="grid gap-4 md:grid-cols-2 xl:grid-cols-4">
          <SummaryTile label="Groups" value={formatNumber(summary?.total_groups ?? 0)} sublabel={`${formatNumber(summary?.active_groups ?? 0)} active groups`} icon={<ShieldCheck className="h-5 w-5" />} />
          <SummaryTile label="Desired Capacity" value={formatNumber(visibleDesiredCapacity)} sublabel={`${formatNumber(summary?.total_max_size ?? 0)} total max size`} icon={<Cpu className="h-5 w-5" />} />
          <SummaryTile
            label="Annual Savings"
            value={formatCurrency(visibleSavings)}
            sublabel={
              refinedRowCount > 0
                ? `${refinedRowCount} priced from a rightsizer target, so this differs from the dashboard`
                : `${formatCurrency(summary?.monthly_cost_estimate ?? 0)} current monthly cost`
            }
            icon={<TrendingDown className="h-5 w-5" />}
          />
          <SummaryTile label="Healthy" value={formatNumber(summary?.healthy_groups ?? 0)} sublabel={`${formatNumber(summary?.actionable_groups ?? 0)} flagged groups`} icon={<Wallet className="h-5 w-5" />} />
        </div>

        <ResourceActionPanel
          title="ASG inventory"
          subtitle={`${inventoryRows.length} visible`}
          resourceLabel="ASG"
          items={inventoryRows}
          getId={(item) => item.resource.resource_id}
          getName={(item) => item.resource.resource_name || item.resource.resource_id}
          getActions={() => []}
          canExecute={() => false}
          executeAction={async () => undefined}
          confirmColumns={[
            { header: 'Group', render: (item) => item.resource.resource_name || item.resource.resource_id },
            { header: 'Region', render: (item) => item.resource.region || 'unknown' },
            { header: 'Instance Type', render: (item) => item.resource.instance_type || 'unknown' },
            { header: 'Desired', render: (item) => item.resource.capacity.desired_capacity },
          ]}
          toolbar={
            <>
              <div className="relative min-w-[240px]">
                <Search size={14} className="pointer-events-none absolute left-3 top-1/2 -translate-y-1/2 text-gray-400" />
                <input
                  type="text"
                  value={inventorySearch}
                  onChange={(event) => setInventorySearch(event.target.value)}
                  placeholder="Search visible groups"
                  className="w-full rounded-xl border border-gray-200 bg-white py-2 pl-9 pr-3 text-sm text-gray-700 outline-none transition focus:border-warning-300 focus:ring-2 focus:ring-warning-100 dark:border-gray-700 dark:bg-gray-900/80 dark:text-gray-100 dark:focus:border-warning-500 dark:focus:ring-warning-900/40"
                />
              </div>
              <select
                value={inventorySort}
                onChange={(event) => setInventorySort(event.target.value as AsgInventorySortKey)}
                className="rounded-xl border border-gray-200 bg-white px-3 py-2 text-sm text-gray-700 outline-none transition focus:border-warning-300 focus:ring-2 focus:ring-warning-100 dark:border-gray-700 dark:bg-gray-900/80 dark:text-gray-100 dark:focus:border-warning-500 dark:focus:ring-warning-900/40"
              >
                <option value="savings">Sort by yearly savings</option>
                <option value="desired">Sort by desired capacity</option>
                <option value="name">Sort by name</option>
                <option value="region">Sort by region</option>
                <option value="instanceType">Sort by instance type</option>
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
          renderItem={(item) => {
            const { resource, recommendation } = item;
            const balanced = recommendation?.tiers.balanced;
            const effectiveSavings = resolveAsgSavings(balanced?.yearly_savings, resource.maxops.potential_savings_yearly).value;

            return (
              <div
                key={resource.inventory_id}
                role="button"
                tabIndex={0}
                onClick={() => navigate(`/dashboard/resources/asg/instances/${encodeURIComponent(resource.resource_id)}`)}
                onKeyDown={(event) => {
                  if (event.key === 'Enter' || event.key === ' ') {
                    event.preventDefault();
                    navigate(`/dashboard/resources/asg/instances/${encodeURIComponent(resource.resource_id)}`);
                  }
                }}
                className="grid w-full items-start gap-4 rounded-2xl border border-gray-200 bg-white/90 px-4 py-4 text-left transition hover:border-warning-300 hover:shadow-sm dark:border-gray-700 dark:bg-gray-900/90"
                style={{ gridTemplateColumns: 'minmax(0, 2.1fr) 140px 180px minmax(280px, 1fr) 160px' }}
              >
                <div className="min-w-0">
                  <div className="flex items-center gap-3">
                    <span className="flex h-10 w-10 items-center justify-center rounded-2xl bg-warning-50 dark:bg-warning-950/40">
                      <Cpu size={20} className="text-warning-600 dark:text-warning-300" />
                    </span>
                    <div className="min-w-0">
                      <div className="truncate text-sm font-semibold text-gray-900 dark:text-white">{resource.resource_name || resource.resource_id}</div>
                      <div className="mt-1 text-xs text-gray-500 dark:text-gray-400">{resource.instance_type || 'unknown type'} | {resource.region || 'unknown region'}</div>
                    </div>
                  </div>
                  <div className="mt-3 flex flex-wrap gap-2">
                    <span className="rounded-full bg-warning-50 px-2.5 py-1 text-[11px] font-semibold uppercase tracking-[0.12em] text-warning-700 dark:bg-warning-950/40 dark:text-warning-300">
                      {resource.state || 'unknown state'}
                    </span>
                    <span className={`rounded-full px-2.5 py-1 text-[11px] font-semibold uppercase tracking-[0.12em] ${statusTone(recommendation?.classification ?? null)}`}>
                      {recommendation?.classification || resource.maxops.status || 'UNKNOWN'}
                    </span>
                  </div>
                </div>

                <div className="min-w-0">
                  <div className="text-[11px] font-semibold uppercase tracking-[0.16em] text-gray-500 dark:text-gray-400">Finding</div>
                  <div className="mt-2 truncate text-base font-semibold text-gray-900 dark:text-white">{titleCase(resource.maxops.finding_type || 'healthy')}</div>
                </div>

                <div className="min-w-0">
                  <div className="text-[11px] font-semibold uppercase tracking-[0.16em] text-gray-500 dark:text-gray-400">Capacity</div>
                  <div className="mt-2 flex items-center justify-between gap-3 text-sm text-gray-700 dark:text-gray-300">
                    <span>Desired</span>
                    <span className="text-lg font-semibold text-gray-900 dark:text-white">{resource.capacity.desired_capacity}</span>
                  </div>
                  <div className="mt-1 flex items-center justify-between gap-3 text-xs text-gray-500 dark:text-gray-400">
                    <span>Min {resource.capacity.min_size}</span>
                    <span>Max {resource.capacity.max_size}</span>
                  </div>
                </div>

                <div className="min-w-0">
                  <div className="text-[11px] font-semibold uppercase tracking-[0.16em] text-gray-500 dark:text-gray-400">Balanced Target</div>
                  <div className="mt-2 flex items-center justify-between gap-3 text-sm text-gray-700 dark:text-gray-300">
                    <span>Desired</span>
                    <span className="text-lg font-semibold text-gray-900 dark:text-white">{balanced ? balanced.target_desired_capacity : 'N/A'}</span>
                  </div>
                  <div className="mt-1 flex items-center justify-between gap-3 text-xs text-gray-500 dark:text-gray-400">
                    {balanced ? (
                      <>
                        <span>Min {balanced.target_min_size}</span>
                        <span>Max {balanced.target_max_size}</span>
                      </>
                    ) : (
                      <span>No balanced target</span>
                    )}
                  </div>
                </div>

                <div className="min-w-0">
                  <div className="text-[11px] font-semibold uppercase tracking-[0.16em] text-gray-500 dark:text-gray-400">Opportunity</div>
                  <div className="mt-2 truncate text-lg font-semibold text-success-700 dark:text-success-300">{formatCurrency(effectiveSavings)}</div>
                  <div className="mt-1 line-clamp-2 text-xs text-gray-500 dark:text-gray-400">{resource.maxops.title || 'Healthy group'}</div>
                </div>
              </div>
            );
          }}
        />
      </div>
    </Layout>
  );
};
