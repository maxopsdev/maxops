import React, { useMemo, useState } from 'react';
import { useNavigate, useParams } from 'react-router-dom';
import { useQuery } from 'react-query';
import {
  AlertCircle,
  ArrowLeft,
  DollarSign,
  Download,
  FileText,
  Loader2,
  Play,
} from 'lucide-react';
import { Layout } from '@/components/layout/Layout';
import { Card } from '@/components/common/Card';
import { Button } from '@/components/common/Button';
import { TestActionDialog } from '@/components/checks/TestActionDialog';
import { getActionsForCheck } from '@/components/checks/checkActions';
import { ResourceTypeIcon } from '@/components/icons/ResourceTypeIcon';
import { checksApi } from '@/services/checks';
import { settingsApi } from '@/services/settings';

type ResourceRecord = {
  resource_id: string;
  resource_type: string;
  resource_name?: string;
  region?: string;
  account_id?: string;
  pricing?: Record<string, any>;
  metadata?: Record<string, any>;
};

const formatCurrency = (value: number): string => {
  if (!Number.isFinite(value)) return '$0.00';
  if (value === 0) return '$0.00';
  if (Math.abs(value) < 0.01) return `$${value.toFixed(6)}`;
  return `$${value.toFixed(2)}`;
};

const formatLabel = (key: string): string =>
  key
    .replace(/[_-]+/g, ' ')
    .replace(/([a-z])([A-Z])/g, '$1 $2')
    .replace(/\s+/g, ' ')
    .trim()
    .replace(/\b\w/g, (char) => char.toUpperCase());

const formatValue = (value: unknown): string => {
  if (value === null || value === undefined || value === '') return 'N/A';
  if (typeof value === 'boolean') return value ? 'Yes' : 'No';
  if (typeof value === 'number') return Number.isInteger(value) ? String(value) : value.toFixed(2);
  if (Array.isArray(value)) return value.length ? value.map(formatValue).join(', ') : 'N/A';
  return String(value);
};

const getResourceSavings = (resource: ResourceRecord): number => {
  const yearly = resource.metadata?.potential_savings_yearly;
  if (typeof yearly === 'number') return yearly;

  const monthly = resource.metadata?.potential_savings_monthly;
  if (typeof monthly === 'number') return monthly * 12;

  return 0;
};

const getResourceAccountId = (resource: ResourceRecord): string => {
  const accountId =
    resource.account_id ??
    resource.metadata?.account_id ??
    resource.metadata?.account ??
    resource.metadata?.AccountId;

  if (typeof accountId === 'string' && accountId.trim()) {
    return accountId;
  }

  if (typeof accountId === 'number') {
    return String(accountId);
  }

  return 'N/A';
};

const getPricingEntries = (resource: ResourceRecord): Array<[string, unknown]> => {
  const pricing =
    (resource.pricing && typeof resource.pricing === 'object' ? resource.pricing : null) ||
    (resource.metadata?.pricing && typeof resource.metadata.pricing === 'object'
      ? resource.metadata.pricing
      : null);

  if (!pricing) {
    return [];
  }

  return Object.entries(pricing).filter(
    ([, value]) =>
      typeof value !== 'object' &&
      value !== null &&
      value !== undefined &&
      !(Array.isArray(value) && value.length === 0) &&
      value !== ''
  );
};

const buildTrendPath = (values: number[], width: number, height: number): string => {
  if (values.length === 0) return '';

  const min = Math.min(...values);
  const max = Math.max(...values);
  const range = max - min || 1;
  const step = values.length > 1 ? width / (values.length - 1) : width;

  return values
    .map((value, index) => {
      const x = values.length === 1 ? width / 2 : index * step;
      const y = height - ((value - min) / range) * height;
      return `${index === 0 ? 'M' : 'L'} ${x} ${y}`;
    })
    .join(' ');
};

const buildCsv = (resources: ResourceRecord[]): string => {
  const metadataKeys = Array.from(
    new Set(resources.flatMap((resource) => Object.keys(resource.metadata || {})))
  ).sort();

  const headers = [
    'resource_id',
    'resource_name',
    'resource_type',
    'account_id',
    'region',
    'potential_savings_yearly',
    ...metadataKeys,
  ];

  const escapeCsv = (value: unknown) => {
    const text = String(value ?? '');
    if (text.includes('"') || text.includes(',') || text.includes('\n')) {
      return `"${text.replace(/"/g, '""')}"`;
    }
    return text;
  };

  const rows = resources.map((resource) =>
    [
      resource.resource_id,
      resource.resource_name || '',
      resource.resource_type,
      resource.account_id || '',
      resource.region || '',
      getResourceSavings(resource),
      ...metadataKeys.map((key) => formatValue(resource.metadata?.[key])),
    ]
      .map(escapeCsv)
      .join(',')
  );

  return [headers.join(','), ...rows].join('\n');
};

export const CheckDetailsPage: React.FC = () => {
  const navigate = useNavigate();
  const { checkId } = useParams<{ checkId: string }>();
  const [expandedResources, setExpandedResources] = useState<Set<string>>(new Set());
  const [actionResource, setActionResource] = useState<ResourceRecord | null>(null);
  const [dialogAction, setDialogAction] = useState<string>('');
  const [selectedActions, setSelectedActions] = useState<Record<string, string>>({});
  const [executingResources, setExecutingResources] = useState<Set<string>>(new Set());
  const [actionMessages, setActionMessages] = useState<Record<string, string>>({});
  const [selectedResources, setSelectedResources] = useState<Set<string>>(new Set());
  const [bulkAction, setBulkAction] = useState<string>('');
  const [bulkActionMessage, setBulkActionMessage] = useState<string>('');
  const [bulkActionDialog, setBulkActionDialog] = useState<{
    action: string;
    resourceIds: string[];
  } | null>(null);

  const {
    data: check,
    isLoading: checkLoading,
    isError: checkError,
  } = useQuery(['check', checkId], () => checksApi.getCheck(checkId!), {
    enabled: Boolean(checkId),
    retry: false,
  });

  const {
    data: resourcesData,
    isLoading: resourcesLoading,
    isError: resourcesError,
  } = useQuery(['check-resources', checkId, 'page'], () => checksApi.getCheckResources(checkId!), {
    enabled: Boolean(checkId),
    retry: false,
  });

  const { data: latestResults } = useQuery(
    'latest-check-results',
    () => checksApi.getLatestResults(),
    {
      enabled: Boolean(checkId),
      retry: false,
    }
  );

  const { data: userSettings } = useQuery('user-settings', () => settingsApi.getSettings(), {
    retry: false,
  });

  const { data: savingsHistory } = useQuery(
    ['check-savings-history', checkId, 'details-page'],
    () => checksApi.getCheckSavingsHistory(checkId!),
    {
      enabled: Boolean(checkId),
      retry: false,
    }
  );

  const latestResult = checkId ? latestResults?.[checkId] : undefined;
  const resources = resourcesData?.resources || [];
  const actions = check ? getActionsForCheck(check) : [];
  const selectableResourceIds = useMemo(
    () => resources.map((resource) => resource.resource_id).filter(Boolean),
    [resources]
  );
  const selectedResourceList = useMemo(
    () => resources.filter((resource) => selectedResources.has(resource.resource_id)),
    [resources, selectedResources]
  );
  const allResourcesSelected =
    selectableResourceIds.length > 0 &&
    selectableResourceIds.every((resourceId) => selectedResources.has(resourceId));

  const actionsRequiringParameters = useMemo(
    () =>
      new Set([
        'Migrate to Graviton',
        'Add policy - Add archival transition',
        'Add policy - Add expiration for old objects',
        'Add policy - Expire noncurrent versions',
        'Add policy - Add noncurrent archival transition',
        'Use Provisioned Capacity',
        'Reduce provisioned IOPS',
        'Reduce provisioned IOPS or migrate to gp3',
        'Downsize volume',
        'Modify volume type to gp3; tune IOPS/throughput',
        'Downsize volume; consider filesystem resize + snapshot/restore',
        'lifecycle policy',
        'Downsize',
        'Migrate to Graviton node type',
        'Upgrade to Valkey',
        'Modify performance mode',
        'Modify throughput mode',
        'Rightsize memory using duration vs memory analysis',
      ]),
    []
  );

  const totalSavings = useMemo(() => {
    if (typeof latestResult?.potential_savings_yearly === 'number') {
      return latestResult.potential_savings_yearly;
    }
    return resources.reduce((sum, resource) => sum + getResourceSavings(resource), 0);
  }, [latestResult?.potential_savings_yearly, resources]);

  const trendValues = useMemo(
    () => savingsHistory?.data_points.map((point) => point.savings).slice(-8) || [],
    [savingsHistory]
  );
  const trendLabels = useMemo(
    () =>
      savingsHistory?.data_points
        .slice(-8)
        .map((point) =>
          new Date(point.timestamp).toLocaleDateString('en-US', {
            month: 'short',
            day: 'numeric',
          })
        ) || [],
    [savingsHistory]
  );
  const trendMin = trendValues.length > 0 ? Math.min(...trendValues) : 0;
  const trendMax = trendValues.length > 0 ? Math.max(...trendValues) : 0;
  const trendPath = useMemo(() => buildTrendPath(trendValues, 160, 56), [trendValues]);

  const isLoading = checkLoading || resourcesLoading;
  const hasError = checkError || resourcesError || !checkId;

  const exportCsv = () => {
    if (!resources.length || !check) return;
    const blob = new Blob([buildCsv(resources)], { type: 'text/csv;charset=utf-8;' });
    const url = URL.createObjectURL(blob);
    const link = document.createElement('a');
    link.href = url;
    link.download = `${check.check_id}-details.csv`;
    document.body.appendChild(link);
    link.click();
    document.body.removeChild(link);
    URL.revokeObjectURL(url);
  };

  const toggleResourceDetails = (resourceId: string) => {
    setExpandedResources((prev) => {
      const next = new Set(prev);
      if (next.has(resourceId)) {
        next.delete(resourceId);
      } else {
        next.add(resourceId);
      }
      return next;
    });
  };

  const toggleResourceSelection = (resourceId: string) => {
    setSelectedResources((prev) => {
      const next = new Set(prev);
      if (next.has(resourceId)) {
        next.delete(resourceId);
      } else {
        next.add(resourceId);
      }
      return next;
    });
  };

  const toggleSelectAllResources = () => {
    setSelectedResources((prev) => {
      if (selectableResourceIds.length === 0) {
        return prev;
      }
      if (allResourcesSelected) {
        return new Set<string>();
      }
      return new Set(selectableResourceIds);
    });
  };

  const executeRowAction = async (resource: ResourceRecord) => {
    const selectedAction = selectedActions[resource.resource_id] || actions[0];
    if (!check || !selectedAction) {
      return;
    }

    if (actionsRequiringParameters.has(selectedAction)) {
      setDialogAction(selectedAction);
      setActionResource(resource);
      return;
    }

    const accountId = resource.account_id || userSettings?.account;
    const region = resource.region || userSettings?.region;
    if (!accountId || !region) {
      setActionMessages((prev) => ({
        ...prev,
        [resource.resource_id]: 'Missing account or region.',
      }));
      return;
    }

    setExecutingResources((prev) => new Set(prev).add(resource.resource_id));
    setActionMessages((prev) => ({
      ...prev,
      [resource.resource_id]: '',
    }));

    try {
      const response = await checksApi.executeAction(check.check_id, {
        action: selectedAction,
        account_id: accountId,
        region,
        resource_id: resource.resource_id,
      });

      setActionMessages((prev) => ({
        ...prev,
        [resource.resource_id]: response.message || 'Action executed.',
      }));
    } catch (error: any) {
      const message = error?.response?.data?.detail || error?.message || 'Failed to execute action.';
      setActionMessages((prev) => ({
        ...prev,
        [resource.resource_id]: message,
      }));
    } finally {
      setExecutingResources((prev) => {
        const next = new Set(prev);
        next.delete(resource.resource_id);
        return next;
      });
    }
  };

  const openBulkActionDialog = () => {
    if (!bulkAction) {
      return;
    }
    if (selectedResourceList.length === 0) {
      setBulkActionMessage('Select at least one resource.');
      return;
    }
    setBulkActionMessage('');
    setBulkActionDialog({
      action: bulkAction,
      resourceIds: selectedResourceList.map((resource) => resource.resource_id),
    });
  };

  return (
    <Layout>
      <div className="space-y-6 pt-4">
        <div className="flex items-start justify-between gap-4">
          <div className="space-y-3">
            <Button variant="ghost" size="sm" onClick={() => navigate('/dashboard')}>
              <ArrowLeft size={16} className="mr-2" />
              Back to Dashboard
            </Button>
            <div>
              <div className="flex items-center gap-3">
                {check?.resource_type && (
                  <ResourceTypeIcon resourceType={check.resource_type} size={48} className="shrink-0" />
                )}
                <h1 className="text-3xl font-bold text-gray-900 dark:text-white">
                  {check?.name || 'Check Details'}
                </h1>
              </div>
              {check?.description && (
                <p className="mt-2 text-gray-600 dark:text-gray-400">{check.description}</p>
              )}
            </div>
          </div>
          {resources.length > 0 && (
            <Button variant="secondary" size="sm" onClick={exportCsv}>
              <Download size={16} className="mr-2" />
              Export CSV
            </Button>
          )}
        </div>

        {isLoading && (
          <Card>
            <div className="flex items-center justify-center py-12 text-gray-600 dark:text-gray-400">
              <Loader2 className="mr-3 animate-spin text-primary-600" size={24} />
              Loading check details...
            </div>
          </Card>
        )}

        {hasError && !isLoading && (
          <Card>
            <div className="flex items-center gap-3 py-6 text-danger-600 dark:text-danger-400">
              <AlertCircle size={20} />
              <span>Unable to load this check detail page.</span>
            </div>
          </Card>
        )}

        {!isLoading && !hasError && check && (
          <>
            <div className="grid grid-cols-1 gap-4 md:grid-cols-4">
              <Card>
                <div>
                  <div>
                    <p className="text-sm text-gray-600 dark:text-gray-400">Trend</p>
                    {trendValues.length > 0 ? (
                      <div className="mt-2 rounded-lg bg-gray-50 px-3 py-3 dark:bg-gray-800/70">
                        <div className="mb-1 flex items-center justify-between text-[10px] text-gray-500 dark:text-gray-400">
                          <span>${trendMax.toFixed(0)}</span>
                          <span>${trendMin.toFixed(0)}</span>
                        </div>
                        <svg width="160" height="56" viewBox="0 0 160 56" className="overflow-visible">
                          <path
                            d={trendPath}
                            fill="none"
                            stroke="currentColor"
                            strokeWidth="2"
                            className="text-primary-600 dark:text-primary-400"
                          />
                        </svg>
                        <div className="mt-1 flex items-center justify-between text-[10px] text-gray-500 dark:text-gray-400">
                          <span>{trendLabels[0] || 'Start'}</span>
                          <span>{trendLabels[trendLabels.length - 1] || 'Now'}</span>
                        </div>
                      </div>
                    ) : (
                      <div className="mt-2 rounded-lg bg-gray-50 px-3 py-6 text-center text-sm text-gray-500 dark:bg-gray-800/70 dark:text-gray-400">
                        No trend
                      </div>
                    )}
                  </div>
                </div>
              </Card>
              <Card>
                <div className="flex items-center justify-between">
                  <div>
                    <p className="text-sm text-gray-600 dark:text-gray-400">Resources</p>
                    <p className="mt-2 text-2xl font-bold text-gray-900 dark:text-white">
                      {resourcesData?.resources_found ?? resources.length}
                    </p>
                  </div>
                  <FileText size={20} className="text-primary-600 dark:text-primary-400" />
                </div>
              </Card>
              <Card>
                <div className="flex items-center justify-between">
                  <div>
                    <p className="text-sm text-gray-600 dark:text-gray-400">Potential Savings</p>
                    <p className="mt-2 text-2xl font-bold text-success-600 dark:text-success-400">
                      {formatCurrency(totalSavings)}/yr
                    </p>
                  </div>
                  <DollarSign size={20} className="text-success-600 dark:text-success-400" />
                </div>
              </Card>
              <Card>
                <div className="flex items-center justify-between">
                  <div>
                    <p className="text-sm text-gray-600 dark:text-gray-400">Last Run</p>
                    <p className="mt-2 text-sm font-medium text-gray-900 dark:text-white">
                      {latestResult?.execution_time
                        ? new Date(latestResult.execution_time).toLocaleString()
                        : 'Not available'}
                    </p>
                  </div>
                </div>
              </Card>
            </div>

            <Card>
              {resources.length === 0 ? (
                <div className="py-10 text-center text-gray-500 dark:text-gray-400">
                  No stored resources are available for this check yet.
                </div>
              ) : (
                <div className="space-y-4">
                  {actions.length > 0 && (
                    <div className="rounded-lg bg-gray-100 px-4 py-3 dark:bg-gray-800/70">
                      <div className="flex flex-wrap items-center justify-end gap-2">
                        <select
                          value={bulkAction}
                          onChange={(e) => setBulkAction(e.target.value)}
                          className="input w-auto max-w-[320px]"
                        >
                          <option value="">Select bulk action</option>
                          {actions.map((action) => (
                            <option key={action} value={action}>
                              {action}
                            </option>
                          ))}
                        </select>
                        <Button
                          variant="primary"
                          size="sm"
                          onClick={openBulkActionDialog}
                          disabled={!bulkAction || selectedResources.size === 0}
                        >
                          Execute Selected
                        </Button>
                      </div>
                      {bulkActionMessage && (
                        <div className="mt-2 text-sm text-gray-600 dark:text-gray-400">
                          {bulkActionMessage}
                        </div>
                      )}
                    </div>
                  )}

                  <div className="overflow-x-auto">
                  <table className="min-w-full divide-y divide-gray-200 dark:divide-gray-800">
                    <thead>
                      <tr className="text-left text-xs uppercase tracking-wide text-gray-500 dark:text-gray-400">
                        <th className="px-4 py-3 font-semibold">
                          <input
                            type="checkbox"
                            checked={allResourcesSelected}
                            onChange={toggleSelectAllResources}
                            className="h-4 w-4 rounded border-gray-300 text-primary-600 focus:ring-primary-500"
                            aria-label="Select all resources"
                          />
                        </th>
                        <th className="px-4 py-3 font-semibold">Resource</th>
                        <th className="px-4 py-3 font-semibold">Account</th>
                        <th className="px-4 py-3 font-semibold">Region</th>
                        <th className="px-4 py-3 font-semibold">Potential Savings</th>
                        <th className="px-4 py-3 font-semibold">Pricing</th>
                        <th className="px-4 py-3 font-semibold">Actions</th>
                        <th className="px-4 py-3 font-semibold">Details</th>
                      </tr>
                    </thead>
                    <tbody className="divide-y divide-gray-100 dark:divide-gray-900">
                      {resources.map((resource) => {
                        const pricingEntries = getPricingEntries(resource);
                                              const isExpanded = expandedResources.has(resource.resource_id);
                        const metadataEntries = Object.entries(resource.metadata || {}).filter(
                          ([key, value]) =>
                            key !== 'pricing' &&
                            key !== 'estimated_monthly_cost' &&
                            key !== 'potential_savings_monthly' &&
                            key !== 'potential_savings_yearly' &&
                            typeof value !== 'object' &&
                            value !== null &&
                            value !== undefined &&
                            !(Array.isArray(value) && value.length === 0) &&
                            value !== ''
                        );

                        return (
                          <React.Fragment key={resource.resource_id}>
                            <tr className="align-top text-sm text-gray-700 dark:text-gray-300">
                              <td className="px-4 py-4">
                                <input
                                  type="checkbox"
                                  checked={selectedResources.has(resource.resource_id)}
                                  onChange={() => toggleResourceSelection(resource.resource_id)}
                                  className="h-4 w-4 rounded border-gray-300 text-primary-600 focus:ring-primary-500"
                                  aria-label={`Select ${resource.resource_id}`}
                                />
                              </td>
                              <td className="px-4 py-4">
                                <div className="font-medium text-gray-900 dark:text-white">
                                  {resource.resource_id}
                                </div>
                                {resource.resource_name && resource.resource_name !== resource.resource_id && (
                                  <div className="mt-1 text-xs text-gray-500 dark:text-gray-400">
                                    {resource.resource_name}
                                  </div>
                                )}
                              </td>
                              <td className="px-4 py-4">{getResourceAccountId(resource)}</td>
                              <td className="px-4 py-4">{resource.region || 'N/A'}</td>
                              <td className="px-4 py-4">
                                <span className="break-words font-medium text-success-600 dark:text-success-400">
                                  {formatCurrency(getResourceSavings(resource))}
                                </span>
                              </td>
                              <td className="px-4 py-4">
                                {pricingEntries.length > 0 ? (
                                  <div className="space-y-2">
                                    {pricingEntries.map(([key, value]) => (
                                      <div key={key} className="grid grid-cols-[120px_1fr] gap-3">
                                        <span className="text-xs font-semibold uppercase tracking-wide text-gray-500 dark:text-gray-400">
                                          {formatLabel(key)}
                                        </span>
                                        <span className="break-words text-gray-900 dark:text-white">
                                          {formatValue(value)}
                                        </span>
                                      </div>
                                    ))}
                                  </div>
                                ) : (
                                  <span className="text-gray-500 dark:text-gray-400">N/A</span>
                                )}
                              </td>
                              <td className="px-4 py-4">
                                {actions.length > 0 ? (
                                  <div className="space-y-2">
                                    <div className="flex items-center gap-2">
                                      <select
                                        value={selectedActions[resource.resource_id] || actions[0] || ''}
                                        onChange={(e) =>
                                          setSelectedActions((prev) => ({
                                            ...prev,
                                            [resource.resource_id]: e.target.value,
                                          }))
                                        }
                                        className="input min-w-[220px]"
                                      >
                                        {actions.map((action) => (
                                          <option key={action} value={action}>
                                            {action}
                                          </option>
                                        ))}
                                      </select>
                                      <Button
                                        variant="secondary"
                                        size="sm"
                                        onClick={() => executeRowAction(resource)}
                                        disabled={executingResources.has(resource.resource_id)}
                                        title="Execute action"
                                      >
                                        <Play size={14} />
                                      </Button>
                                    </div>
                                    {actionMessages[resource.resource_id] && (
                                      <div className="max-w-[320px] text-xs text-gray-600 dark:text-gray-400">
                                        {actionMessages[resource.resource_id]}
                                      </div>
                                    )}
                                  </div>
                                ) : (
                                  <span className="text-gray-500 dark:text-gray-400">N/A</span>
                                )}
                              </td>
                              <td className="px-4 py-4">
                                <Button
                                  variant="secondary"
                                  size="sm"
                                  onClick={() => toggleResourceDetails(resource.resource_id)}
                                >
                                  {isExpanded ? 'Hide Details' : 'Details'}
                                </Button>
                              </td>
                            </tr>
                            {isExpanded && (
                              <tr className="text-sm">
                                <td colSpan={8} className="bg-gray-50 px-4 py-4 dark:bg-gray-900/40">
                                  {metadataEntries.length > 0 ? (
                                    <div className="grid grid-cols-1 gap-x-8 gap-y-5 md:grid-cols-2 xl:grid-cols-3">
                                      {metadataEntries.map(([key, value]) => (
                                        <div key={key} className="min-w-0">
                                          <div className="text-[11px] font-semibold uppercase tracking-[0.14em] text-gray-500 dark:text-gray-400">
                                            {formatLabel(key)}
                                          </div>
                                          <div className="mt-1 break-words text-sm leading-6 text-gray-900 dark:text-white">
                                            {formatValue(value)}
                                          </div>
                                        </div>
                                      ))}
                                    </div>
                                  ) : (
                                    <span className="text-gray-500 dark:text-gray-400">No extra details</span>
                                  )}
                                </td>
                              </tr>
                            )}
                          </React.Fragment>
                        );
                      })}
                    </tbody>
                  </table>
                </div>
                </div>
              )}
            </Card>
          </>
        )}

        {check && actionResource && (
          <TestActionDialog
            isOpen={true}
            onClose={() => {
              setActionResource(null);
              setDialogAction('');
            }}
            check={check}
            actions={actions}
            defaultAccountId={userSettings?.account}
            defaultRegion={userSettings?.region}
            initialResourceId={actionResource.resource_id}
            initialAccountId={actionResource.account_id}
            initialRegion={actionResource.region}
            initialAction={dialogAction}
            resourceIdReadOnly={true}
            title="Run Action"
            runLabel="Execute Action"
            onRun={async (payload) => {
              const response = await checksApi.executeAction(check.check_id, payload);
              setActionMessages((prev) => ({
                ...prev,
                [actionResource.resource_id]: response.message || 'Action executed.',
              }));
              return response;
            }}
          />
        )}

        {check && bulkActionDialog && (
          <TestActionDialog
            isOpen={true}
            onClose={() => setBulkActionDialog(null)}
            check={check}
            actions={actions}
            initialAction={bulkActionDialog.action}
            initialResourceId={bulkActionDialog.resourceIds[0]}
            initialAccountId={resources.find((resource) => resource.resource_id === bulkActionDialog.resourceIds[0])?.account_id}
            initialRegion={resources.find((resource) => resource.resource_id === bulkActionDialog.resourceIds[0])?.region}
            hideTargetInputs={true}
            bulkResourceIds={bulkActionDialog.resourceIds}
            title="Execute Selected Resources"
            runLabel="Confirm Execute"
            resourceSummary={
              <div className="space-y-2">
                <div className="text-sm text-gray-700 dark:text-gray-300">
                  Action: <span className="font-semibold">{bulkActionDialog.action}</span>
                </div>
                <div className="text-sm text-gray-700 dark:text-gray-300">
                  Target resources: {bulkActionDialog.resourceIds.length}
                </div>
                <div className="max-h-40 overflow-y-auto text-xs text-gray-600 dark:text-gray-400">
                  {bulkActionDialog.resourceIds.join(', ')}
                </div>
              </div>
            }
            onRun={() =>
              Promise.resolve({
                status: 'error',
                message: 'Single-resource execution is not used in bulk mode.',
              })
            }
            onRunBulk={async (payload) => {
              const resourceIds = payload.resource_ids;
              const resourcesToExecute = resources.filter((resource) => resourceIds.includes(resource.resource_id));
              setExecutingResources((prev) => {
                const next = new Set(prev);
                resourceIds.forEach((resourceId) => next.add(resourceId));
                return next;
              });

              try {
                const results = await Promise.all(
                  resourcesToExecute.map(async (resource) => {
                    const accountId = resource.account_id || userSettings?.account;
                    const region = resource.region || userSettings?.region;
                    if (!accountId || !region) {
                      return {
                        resourceId: resource.resource_id,
                        message: 'Missing account or region.',
                      };
                    }

                    try {
                      const response = await checksApi.executeAction(check.check_id, {
                        action: payload.action,
                        account_id: accountId,
                        region,
                        resource_id: resource.resource_id,
                        parameters: payload.parameters,
                      });
                      return {
                        resourceId: resource.resource_id,
                        message: response.message || 'Action executed.',
                      };
                    } catch (error: any) {
                      return {
                        resourceId: resource.resource_id,
                        message: error?.response?.data?.detail || error?.message || 'Failed to execute action.',
                      };
                    }
                  })
                );

                setActionMessages((prev) => {
                  const next = { ...prev };
                  results.forEach((result) => {
                    next[result.resourceId] = result.message;
                  });
                  return next;
                });

                const failed = results.filter((result) => /failed|missing/i.test(result.message)).length;
                const succeeded = results.length - failed;
                const message =
                  failed > 0
                    ? `Executed "${payload.action}" for ${succeeded} resource(s); ${failed} failed.`
                    : `Executed "${payload.action}" for ${results.length} resource(s).`;
                setBulkActionMessage(message);
                return { status: failed > 0 ? 'partial' : 'success', message };
              } finally {
                setBulkActionDialog(null);
                setExecutingResources((prev) => {
                  const next = new Set(prev);
                  resourceIds.forEach((resourceId) => next.delete(resourceId));
                  return next;
                });
              }
            }}
          />
        )}
      </div>
    </Layout>
  );
};
