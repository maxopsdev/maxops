import React, { useState, useEffect, useMemo } from 'react';
import { useLocation, useNavigate } from 'react-router-dom';
import { Layout } from '@/components/layout/Layout';
import { Card } from '@/components/common/Card';
import { Button } from '@/components/common/Button';
import { ResourcePageFilters } from '@/components/common/ResourcePageFilters';
import { SharedTagFilterField } from '@/components/common/SharedTagFilterField';
import { CheckCard } from '@/components/checks/CheckCard';
import { TestActionDialog } from '@/components/checks/TestActionDialog';
import { getActionsForCheck } from '@/components/checks/checkActions';
import { ResourceTypeIcon } from '@/components/icons/ResourceTypeIcon';
import { TrendingUp, DollarSign, FileText, AlertCircle, Loader2, XCircle, X, Filter, ChevronDown, ChevronRight, Play } from 'lucide-react';
import { useQuery, useMutation, useQueryClient } from 'react-query';
import { checksApi, type CheckState } from '@/services/checks';
import { summariseYearlySavings } from '@/utils/savings';
import { inventoryApi } from '@/services/inventory';
import { policiesApi } from '@/services/policies';
import type { CheckMetadata, CheckTestResponse } from '@/services/checks';
import { scansApi, type ScanRunResponse } from '@/services/scans';
import { settingsApi, featureFlagsApi } from '@/services/settings';
import { useOptimizationProfile } from '@/contexts/OptimizationProfileContext';
import { useSharedOverviewFilters } from '@/stores/sharedOverviewFilters';
import { adjustOptimizationCount, adjustOptimizationSavings } from '@/utils/optimizationProfile';
import { buildSharedTagOptions, parseSharedTagQuery } from '@/utils/sharedOverviewFilters';

// Format resource type for display (e.g., "ec2" -> "EC2", "cloudwatch_alarm" -> "CloudWatch Alarm")
const formatResourceType = (resourceType: string): string => {
  // Known acronyms that should be uppercase
  const acronyms: Record<string, string> = {
    'ec2': 'EC2',
    'rds': 'RDS',
    's3': 'S3',
    'ebs': 'EBS',
    'gsi': 'GSI',
    'rcu': 'RCU',
    'wcu': 'WCU',
    'mpu': 'MPU',
    'aws': 'AWS',
    'api': 'API',
    'dns': 'DNS',
    'vpc': 'VPC',
    'iam': 'IAM',
    'sns': 'SNS',
    'sqs': 'SQS',
    'lambda': 'Lambda',
    'ecs': 'ECS',
    'eks': 'EKS',
    'elb': 'ELB',
    'alb': 'ALB',
    'nlb': 'NLB',
    'efs': 'EFS',
    'fsx': 'FSx',
    'glacier': 'Glacier',
    'emr': 'EMR',
    'sagemaker': 'SageMaker',
  };

  // Split by underscore and process each part
  const parts = resourceType.toLowerCase().split('_');
  
  return parts.map((part) => {
    // Check if it's a known acronym
    if (acronyms[part]) {
      return acronyms[part];
    }
    
    // Capitalize first letter of each word
    return part.charAt(0).toUpperCase() + part.slice(1);
  }).join(' ');
};

const buildSparklinePath = (values: number[], width: number, height: number): string => {
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

const aggregateTrendSeries = (seriesList: number[][], maxPoints: number = 8): number[] => {
  const validSeries = seriesList.filter((series) => series.length > 0);
  if (validSeries.length === 0) return [];

  const targetLength = Math.min(
    maxPoints,
    Math.max(...validSeries.map((series) => series.length))
  );

  const normalizedSeries = validSeries.map((series) =>
    series.length > targetLength ? series.slice(series.length - targetLength) : series
  );

  return Array.from({ length: targetLength }, (_, index) =>
    normalizedSeries.reduce((sum, series) => {
      const offset = targetLength - series.length;
      const valueIndex = index - offset;
      return sum + (valueIndex >= 0 ? series[valueIndex] ?? 0 : 0);
    }, 0)
  );
};

const aggregateTrendLabels = (labelSeriesList: string[][], maxPoints: number = 8): string[] => {
  const validSeries = labelSeriesList.filter((series) => series.length > 0);
  if (validSeries.length === 0) return [];

  const longestSeries = validSeries.reduce((longest, current) =>
    current.length > longest.length ? current : longest
  );

  return longestSeries.length > maxPoints
    ? longestSeries.slice(longestSeries.length - maxPoints)
    : longestSeries;
};

const normalizeSavingsHistoryEntry = (
  entry: { values: number[]; labels: string[] } | number[] | undefined
): { values: number[]; labels: string[] } => {
  if (!entry) {
    return { values: [], labels: [] };
  }

  if (Array.isArray(entry)) {
    return { values: entry, labels: [] };
  }

  return {
    values: Array.isArray(entry.values) ? entry.values : [],
    labels: Array.isArray(entry.labels) ? entry.labels : [],
  };
};

const SavingsSparkline: React.FC<{
  values: number[];
  labels?: string[];
  emptyLabel?: string;
  className?: string;
}> = ({ values, labels = [], emptyLabel = 'No trend', className = '' }) => {
  if (values.length === 0) {
    return (
      <div className={`flex h-12 w-28 items-center justify-center rounded-md bg-gray-50 text-[11px] text-gray-400 dark:bg-gray-800/70 dark:text-gray-500 ${className}`}>
        {emptyLabel}
      </div>
    );
  }

  const width = 112;
  const height = 40;
  const path = buildSparklinePath(values, width, height);
  const min = Math.min(...values);
  const max = Math.max(...values);

  return (
    <div className={`rounded-md bg-gray-50 px-2 py-2 dark:bg-gray-800/70 ${className}`}>
      <div className="mb-1 flex items-center justify-between text-[10px] text-gray-500 dark:text-gray-400">
        <span>${max.toFixed(0)}</span>
        <span>${min.toFixed(0)}</span>
      </div>
      <svg width={width} height={height} viewBox={`0 0 ${width} ${height}`} className="overflow-visible">
        <path
          d={path}
          fill="none"
          stroke="currentColor"
          strokeWidth="2"
          className="text-primary-600 dark:text-primary-400"
        />
      </svg>
      <div className="mt-1 flex items-center justify-between text-[10px] text-gray-500 dark:text-gray-400">
        <span>{labels[0] || 'Start'}</span>
        <span>{labels[labels.length - 1] || 'Now'}</span>
      </div>
    </div>
  );
};

export const DashboardPage: React.FC = () => {
  const { profile } = useOptimizationProfile();
  const location = useLocation();
  const navigate = useNavigate();
  const queryClient = useQueryClient();
  const [runningChecks, setRunningChecks] = useState<Set<string>>(new Set());
  const [bulkRunScope, setBulkRunScope] = useState<string | null>(null);
  const [activeScanId, setActiveScanId] = useState<number | null>(null);
  const [checkStates, setCheckStates] = useState<Map<string, CheckState>>(new Map());
  const [errorBanner, setErrorBanner] = useState<{ message: string; type?: string; details?: string[] } | null>(null);
  const [testCheck, setTestCheck] = useState<CheckMetadata | null>(null);
  const [selectedResourceType, setSelectedResourceType] = useState<string>('all');
  const [searchQuery, setSearchQuery] = useState('');
  const [groupBy, setGroupBy] = useState<'resource_type' | 'account' | 'region'>('resource_type');
  const [sortBy, setSortBy] = useState<'name' | 'resources' | 'savings'>('savings');
  const [expandedGroups, setExpandedGroups] = useState<Set<string>>(new Set());
  const {
    region: sharedRegion,
    environment: sharedEnvironment,
    tagQuery: sharedTagQuery,
    tagLogic: sharedTagLogic,
    activeSavedFilterId,
    savedFilters,
    setRegion: setSharedRegion,
    setEnvironment: setSharedEnvironment,
    setTagQuery: setSharedTagQuery,
    setTagLogic: setSharedTagLogic,
    applySavedFilter,
    saveCurrentFilter,
    reset: resetSharedOverviewFilters,
  } = useSharedOverviewFilters();

  const handleSaveCurrentFilter = () => {
    const name = window.prompt('Name this saved filter');
    if (!name) {
      return;
    }

    saveCurrentFilter(name);
  };

  // Check for error in location state (from navigation)
  useEffect(() => {
    const state = location.state as { error?: string; errorType?: string } | null;
    if (state?.error) {
      setErrorBanner({
        message: state.error,
        type: state.errorType || 'error'
      });
      // Clear location state to prevent showing error on refresh
      window.history.replaceState({}, document.title);
    }
  }, [location]);

  // Fetch all checks
  const { data: checks, isLoading: checksLoading } = useQuery<CheckMetadata[]>(
    'checks',
    () => checksApi.listChecks()
  );

  // Fetch policies to determine inactive checks
  const { data: policies } = useQuery(
    'policies-all',
    () => policiesApi.list(),
    { retry: false }
  );

  // Fetch last run times for all checks
  const { data: lastRuns } = useQuery<Record<string, string>>(
    'check-last-runs',
    () => checksApi.getLastRuns(),
    {
      enabled: !checksLoading && !!checks,
    }
  );

  // Fetch latest check results for each check to populate initial states
  const { data: latestCheckResults } = useQuery<Record<string, any>>(
    'latest-check-results',
    () => checksApi.getLatestResults(),
    {
      enabled: !checksLoading && !!checks && checks.length > 0,
    }
  );

  const { data: savingsHistories } = useQuery<Record<string, { values: number[]; labels: string[] }>>(
    ['dashboard-savings-histories-v2', checks?.map((check) => check.check_id).join(',')],
    async () => {
      if (!checks || checks.length === 0) {
        return {};
      }

      const histories = await Promise.all(
        checks.map(async (check) => {
          const response = await checksApi.getCheckSavingsHistory(check.check_id);
          const points = response.data_points.slice(-8);
          return [
            check.check_id,
            {
              values: points.map((point) => point.savings),
              labels: points.map((point) =>
                new Date(point.timestamp).toLocaleDateString('en-US', {
                  month: 'short',
                  day: 'numeric',
                })
              ),
            },
          ] as const;
        })
      );

      return Object.fromEntries(histories);
    },
    {
      enabled: !!checks && checks.length > 0,
      staleTime: 30000,
      refetchOnWindowFocus: false,
    }
  );

  const { data: featureFlags } = useQuery(
    'feature-flags',
    () => featureFlagsApi.getFlags(),
    { retry: false }
  );

  const { data: userSettings } = useQuery(
    'user-settings',
    () => settingsApi.getSettings(),
    { retry: false }
  );

  const selectedScanRegions = useMemo(
    () => (userSettings?.regions?.length ? userSettings.regions : userSettings?.region ? [userSettings.region] : []),
    [userSettings]
  );

  const buildScanProgressMessage = (scan: ScanRunResponse): string => {
    const progress = scan.progress || {};
    const selectedRegions = scan.selected_regions || [];
    const inventoryCounts = progress.inventory_counts || scan.inventory_counts || {};
    const inventorySummary = Object.entries(inventoryCounts)
      .filter(([, count]) => count > 0)
      .map(([type, count]) => `${formatResourceType(type)} ${count}`)
      .join(', ');
    const checkProgress = progress.total_checks
      ? `Policy ${progress.check_index || 0}/${progress.total_checks}`
      : `${scan.checks_completed || 0}/${scan.total_checks || 0} policies`;
    const region = progress.current_region ? `Region: ${progress.current_region}. ` : '';
    const policy = progress.current_check_name ? `Current policy: ${progress.current_check_name}. ` : '';
    const inventory = inventorySummary ? `Inventory found: ${inventorySummary}. ` : '';
    const regions = selectedRegions.length ? `Selected regions: ${selectedRegions.join(', ')}. ` : '';

    return `${progress.message || 'Scan is running.'} ${region}${policy}${checkProgress}. ${inventory}${regions}`.trim();
  };

  const buildScanErrorDetails = (scan: ScanRunResponse): { message: string; details: string[] } => {
    const details = scan.error_details || [];
    if (details.length === 0) {
      return {
        message: scan.progress?.message || `${scan.checks_failed} scan${scan.checks_failed === 1 ? '' : 's'} failed. Successful results were still saved.`,
        details: [],
      };
    }

    return {
      message: `${details.length} policy scan${details.length === 1 ? '' : 's'} failed. Successful results were still saved.`,
      details: details.map((detail) => `${detail.name}: ${detail.error}`),
    };
  };

  const { data: activeScanStatus } = useQuery<ScanRunResponse>(
    ['scan-status', activeScanId],
    () => scansApi.getScanStatus(activeScanId!),
    {
      enabled: activeScanId !== null,
      refetchInterval: activeScanId !== null ? 1500 : false,
      refetchOnWindowFocus: false,
    }
  );

  const inactiveCheckIds = useMemo(
    () =>
      new Set(
        (policies || [])
          .filter((policy) => policy.status === 'inactive' && policy.check_id)
          .map((policy) => policy.check_id as string)
      ),
    [policies]
  );

  const hasRunnableChecks = useMemo(
    () => (checks || []).some((check) => !inactiveCheckIds.has(check.check_id) && !runningChecks.has(check.check_id)),
    [checks, inactiveCheckIds, runningChecks]
  );

  const { data: sharedFilterOptions } = useQuery(
    'dashboard-shared-overview-filter-options',
    async () => {
      const responses = await Promise.allSettled([
        inventoryApi.getEc2Overview(),
        inventoryApi.getRdsOverview(),
        inventoryApi.getS3Overview(),
        inventoryApi.getDynamoDbOverview(),
        inventoryApi.getEbsOverview(),
        inventoryApi.getElasticacheOverview(),
      ]);

      const regions = new Set<string>();
      const environments = new Set<string>();
      const tags = new Set<string>();

      responses.forEach((result) => {
        if (result.status !== 'fulfilled') {
          return;
        }

        const response = result.value;
        const dimensions = response?.dimensions;
        dimensions?.regions?.forEach((row: { key: string }) => {
          if (row.key) {
            regions.add(row.key);
          }
        });
        dimensions?.environments?.forEach((row: { key: string }) => {
          if (row.key) {
            environments.add(row.key);
          }
        });

        const taggedItems = [
          ...('instances' in response ? response.instances : []),
          ...('resources' in response ? response.resources : []),
          ...('buckets' in response ? response.buckets : []),
          ...('volumes' in response ? response.volumes : []),
        ];

        buildSharedTagOptions(taggedItems).forEach((tag) => tags.add(tag));
      });

      return {
        regions: Array.from(regions).sort((left, right) => left.localeCompare(right)),
        environments: Array.from(environments).sort((left, right) => left.localeCompare(right)),
        tags: Array.from(tags).sort((left, right) => left.localeCompare(right)),
      };
    },
    {
      retry: false,
      staleTime: 60000,
    }
  );

  // Update check states with last run times and results when data is available
  useEffect(() => {
    if (checks && (lastRuns !== undefined || latestCheckResults !== undefined)) {
      setCheckStates(prev => {
        const newStates = new Map(prev);
        checks.forEach(check => {
          const lastRun = lastRuns?.[check.check_id];
          const latestResult = latestCheckResults?.[check.check_id];
          
          if (latestResult) {
            // Use latest result data - this is the primary source
            newStates.set(check.check_id, {
              check_id: check.check_id,
              name: latestResult.name || check.name,
              description: latestResult.description || check.description,
              resource_type: latestResult.resource_type || check.resource_type,
              resources_found: latestResult.resources_found || 0,
              potential_savings_yearly: latestResult.potential_savings_yearly || 
                (latestResult.potential_savings_monthly ? latestResult.potential_savings_monthly * 12 : 0),
              savings_by_resource: latestResult.savings_by_resource,
              last_run: latestResult.execution_time || lastRun || undefined,
              status: latestResult.status === 'completed' ? 'completed' : (latestResult.status === 'failed' ? 'failed' : 'idle')
            });
          } else if (lastRun) {
            // Only have last run time, no result data - create minimal state
            const existing = newStates.get(check.check_id);
            if (existing) {
              // Update existing state with last run time
              newStates.set(check.check_id, {
                ...existing,
                last_run: lastRun
              });
            } else {
              // Create new state with just last run time
              newStates.set(check.check_id, {
                check_id: check.check_id,
                name: check.name,
                description: check.description,
                resource_type: check.resource_type,
                resources_found: 0,
                potential_savings_yearly: 0,
                last_run: lastRun,
                status: 'idle'
              });
            }
          } else {
            // No data at all - ensure check exists in state with idle status
            if (!newStates.has(check.check_id)) {
              newStates.set(check.check_id, {
                check_id: check.check_id,
                name: check.name,
                description: check.description,
                resource_type: check.resource_type,
                resources_found: 0,
                potential_savings_yearly: 0,
                last_run: undefined,
                status: 'idle'
              });
            }
          }
        });
        return newStates;
      });
    }
  }, [lastRuns, latestCheckResults, checks]);

  // Mutation to run a single check
  const runCheckMutation = useMutation(
    (check_id: string) => checksApi.testCheck(check_id),
    {
      onMutate: (check_id) => {
        setRunningChecks(prev => new Set(prev).add(check_id));
      },
      onSuccess: (data: CheckTestResponse, check_id: string) => {
        // Use potential_savings_yearly from API response if available, otherwise calculate from resources
        const totalSavings = data.potential_savings_yearly ?? data.resources.reduce((sum, resource) => {
          const savings = resource.metadata?.potential_savings_yearly || 
            (resource.metadata?.potential_savings_monthly ? resource.metadata.potential_savings_monthly * 12 : 0);
          return sum + savings;
        }, 0);

        // Update check state with current timestamp as last_run
        const check = checks?.find(c => c.check_id === check_id);
        const now = new Date().toISOString();
        if (check) {
          setCheckStates(prev => {
            const newMap = new Map(prev);
            newMap.set(check_id, {
              check_id: check.check_id,
              name: check.name,
              description: check.description,
              resource_type: check.resource_type,
              resources_found: data.resources_found,
              potential_savings_yearly: totalSavings,
              last_run: now,
              status: 'completed'
            });
            return newMap;
          });
        }
        setRunningChecks(prev => {
          const newSet = new Set(prev);
          newSet.delete(check_id);
          return newSet;
        });
        
        // Invalidate queries to refresh data
        queryClient.invalidateQueries('check-last-runs');
        queryClient.invalidateQueries('latest-check-results');
        // Refetch immediately to update UI
        queryClient.refetchQueries('latest-check-results');
      },
      onError: (_error: any, check_id: string) => {
        const check = checks?.find(c => c.check_id === check_id);
        if (check) {
          setCheckStates(prev => {
            const newMap = new Map(prev);
            const existing = newMap.get(check_id);
            newMap.set(check_id, {
              check_id: check.check_id,
              name: check.name,
              description: check.description,
              resource_type: check.resource_type,
              resources_found: existing?.resources_found || 0,
              potential_savings_yearly: existing?.potential_savings_yearly || 0,
              last_run: existing?.last_run,
              status: 'failed'
            });
            return newMap;
          });
        }
        setRunningChecks(prev => {
          const newSet = new Set(prev);
          newSet.delete(check_id);
          return newSet;
        });
      }
    }
  );

  const handleRunCheck = (check_id: string) => {
    runCheckMutation.mutate(check_id);
  };

  const refreshScanQueries = () => {
    queryClient.invalidateQueries('check-last-runs');
    queryClient.invalidateQueries('latest-check-results');
    queryClient.invalidateQueries('dashboard-savings-histories-v2');
    queryClient.invalidateQueries('dashboard-shared-overview-filter-options');
    queryClient.invalidateQueries('inventory-ec2-overview');
    queryClient.invalidateQueries('inventory-rds-overview');
    queryClient.invalidateQueries('inventory-s3-overview');
    queryClient.invalidateQueries('inventory-dynamodb-overview');
    queryClient.invalidateQueries('inventory-ebs-overview');
    queryClient.invalidateQueries('inventory-elasticache-overview');
  };

  const applyScanResults = (data: ScanRunResponse) => {
    setCheckStates(prev => {
      const newMap = new Map(prev);
      Object.values(data.results).forEach((result) => {
        newMap.set(result.check_id, {
          check_id: result.check_id,
          name: result.name,
          description: result.description,
          resource_type: result.resource_type,
          resources_found: result.resources_found,
          potential_savings_yearly: result.potential_savings_yearly,
          last_run: result.execution_time || data.execution_time || undefined,
          status: result.status,
        });
      });
      return newMap;
    });
  };

  useEffect(() => {
    if (!activeScanStatus || activeScanId === null) {
      return;
    }

    if (activeScanStatus.status === 'running') {
      setErrorBanner({
        message: buildScanProgressMessage(activeScanStatus),
        type: 'info',
      });
      return;
    }

    applyScanResults(activeScanStatus);
    if (activeScanStatus.status === 'completed') {
      const inventoryTotal = Object.values(activeScanStatus.inventory_counts || {}).reduce((sum, count) => sum + count, 0);
      setErrorBanner({
        message: `Scan completed. Checked ${activeScanStatus.selected_regions.length} selected region${activeScanStatus.selected_regions.length === 1 ? '' : 's'} and refreshed ${inventoryTotal} inventory resource${inventoryTotal === 1 ? '' : 's'}.`,
        type: 'success',
      });
    } else {
      const errorDetails = buildScanErrorDetails(activeScanStatus);
      setErrorBanner({
        message: errorDetails.message,
        details: errorDetails.details,
        type: 'warning',
      });
    }

    setActiveScanId(null);
    setBulkRunScope(null);
    refreshScanQueries();
  }, [activeScanStatus, activeScanId]);

  const runChecksBatch = async (checksToRun: CheckMetadata[], scope: string, resourceType?: string) => {
    const runnableChecks = checksToRun.filter(
      (check) => !inactiveCheckIds.has(check.check_id) && !runningChecks.has(check.check_id)
    );

    if (runnableChecks.length === 0) {
      return;
    }

    setBulkRunScope(scope);
    setErrorBanner({
      message: `Running ${resourceType ? formatResourceType(resourceType) : 'all'} scan for ${selectedScanRegions.length || 0} selected region${selectedScanRegions.length === 1 ? '' : 's'}${selectedScanRegions.length > 0 ? `: ${selectedScanRegions.join(', ')}` : ''}.`,
      type: 'info',
    });
    try {
      const data = await scansApi.startScan(resourceType);
      setActiveScanId(data.execution_id);
      setErrorBanner({
        message: data.progress?.message || `Scan started for ${data.selected_regions.join(', ')}.`,
        type: 'info',
      });
    } catch (error: any) {
      setErrorBanner({
        message: error?.response?.data?.detail || error?.message || 'Failed to run scan.',
        type: 'error',
      });
      setBulkRunScope(null);
    }
  };

  const handleRunAllChecks = () => {
    if (!checks || checks.length === 0) {
      return;
    }
    void runChecksBatch(checks, 'all');
  };

  const handleRunResourceTypeChecks = (resourceType: string, checksToRun: CheckMetadata[]) => {
    void runChecksBatch(checksToRun, `resource_type:${resourceType}`, resourceType);
  };

  const handleResourceTypeChange = (value: string) => {
    const route = getResourceOverviewRoute(value);
    if (route) {
      navigate(route);
      return;
    }
    setSelectedResourceType(value);
  };

  const handleGroupHeaderClick = (groupKey: string, iconType?: string) => {
    if (groupBy === 'resource_type' && iconType) {
      const route = getResourceOverviewRoute(iconType);
      if (route) {
        navigate(route);
        return;
      }
    }
    toggleGroup(groupKey);
  };

  // Get unique resource types from checks
  const resourceTypes = useMemo(() => {
    if (!checks) return [];
    const types = new Set<string>();
    checks.forEach(check => {
      if (check.resource_type) {
        types.add(check.resource_type);
      }
    });
    return Array.from(types).sort();
  }, [checks]);

  // Filter checks by resource type
  const filteredChecks = useMemo(() => {
    if (!checks) return [];
    return checks.filter((check) => {
      if (selectedResourceType !== 'all' && check.resource_type !== selectedResourceType) {
        return false;
      }

      const search = searchQuery.trim().toLowerCase();
      if (!search) {
        return true;
      }

      return [
        check.name,
        check.description,
        check.resource_type,
        check.check_id,
      ]
        .filter(Boolean)
        .some((value) => String(value).toLowerCase().includes(search));
    });
  }, [checks, searchQuery, selectedResourceType]);

  // Calculate totals based on filtered checks
  const totalResources = useMemo(() => {
    const rawResources = filteredChecks.reduce((sum, check) => {
      const state = checkStates.get(check.check_id);
      return sum + (state?.resources_found || 0);
    }, 0);
    return adjustOptimizationCount(rawResources, profile);
  }, [filteredChecks, checkStates, profile]);

  // One resource can be flagged by several checks; see summariseYearlySavings.
  const savingsSummary = useMemo(() => {
    const states = filteredChecks.map((check) => checkStates.get(check.check_id));
    return summariseYearlySavings(states);
  }, [filteredChecks, checkStates]);

  const totalSavings = useMemo(
    () => adjustOptimizationSavings(savingsSummary.total, profile),
    [savingsSummary, profile],
  );

  const groupedChecks = useMemo(() => {
    const groups = new Map<string, CheckMetadata[]>();

    filteredChecks.forEach((check) => {
      let groupLabel = '';

      if (groupBy === 'resource_type') {
        groupLabel = formatResourceType(check.resource_type || 'unknown');
      } else if (groupBy === 'account') {
        groupLabel = userSettings?.account?.trim() || 'Current Account';
      } else {
        groupLabel = userSettings?.region?.trim() || 'Current Region';
      }

      const existing = groups.get(groupLabel) || [];
      existing.push(check);
      groups.set(groupLabel, existing);
    });

    return Array.from(groups.entries())
      .map(([label, items]) => ({
        key: `${groupBy}:${label}`,
        label,
        iconType: groupBy === 'resource_type' ? items[0]?.resource_type : undefined,
        items: items
          .slice()
          .sort((left, right) => {
            if (sortBy === 'resources') {
              const leftResources = checkStates.get(left.check_id)?.resources_found || 0;
              const rightResources = checkStates.get(right.check_id)?.resources_found || 0;
              if (rightResources !== leftResources) {
                return rightResources - leftResources;
              }
            }

            if (sortBy === 'savings') {
              const leftSavings = checkStates.get(left.check_id)?.potential_savings_yearly || 0;
              const rightSavings = checkStates.get(right.check_id)?.potential_savings_yearly || 0;
              if (rightSavings !== leftSavings) {
                return rightSavings - leftSavings;
              }
            }

            return left.name.localeCompare(right.name);
          }),
        resourcesFound: adjustOptimizationCount(items.reduce((sum, check) => {
          const state = checkStates.get(check.check_id);
          return sum + (state?.resources_found || 0);
        }, 0), profile),
        savings: adjustOptimizationSavings(items.reduce((sum, check) => {
          const state = checkStates.get(check.check_id);
          return sum + (state?.potential_savings_yearly || 0);
        }, 0), profile),
        trend: aggregateTrendSeries(
          items.map((check) =>
            normalizeSavingsHistoryEntry(savingsHistories?.[check.check_id]).values.map((value) =>
              adjustOptimizationSavings(value, profile)
            )
          )
        ),
        trendLabels: aggregateTrendLabels(
          items.map((check) => normalizeSavingsHistoryEntry(savingsHistories?.[check.check_id]).labels)
        ),
      }))
      .sort((left, right) => {
        if (sortBy === 'resources' && right.resourcesFound !== left.resourcesFound) {
          return right.resourcesFound - left.resourcesFound;
        }

        if (sortBy === 'savings' && right.savings !== left.savings) {
          return right.savings - left.savings;
        }

        return left.label.localeCompare(right.label);
      });
  }, [filteredChecks, groupBy, sortBy, userSettings?.account, userSettings?.region, checkStates, savingsHistories, profile]);

  const toggleGroup = (groupKey: string) => {
    setExpandedGroups((prev) => {
      const next = new Set(prev);
      if (next.has(groupKey)) {
        next.delete(groupKey);
      } else {
        next.add(groupKey);
      }
      return next;
    });
  };

  return (
    <Layout>
      <div className="space-y-6">
        <div className="flex flex-wrap items-start justify-between gap-4">
          <div>
            <h1 className="text-3xl font-bold text-gray-900 dark:text-white">Dashboard</h1>
            <p className="text-gray-600 dark:text-gray-400 mt-1">
              Overview of your cost optimization checks and potential savings
            </p>
          </div>
          <Button
            variant="primary"
            onClick={handleRunAllChecks}
            disabled={!hasRunnableChecks || bulkRunScope !== null || runningChecks.size > 0}
          >
            <span className="flex items-center gap-2">
              {bulkRunScope === 'all' ? <Loader2 className="animate-spin" size={16} /> : <Play size={16} />}
              <span>{bulkRunScope === 'all' ? 'Running all scans...' : 'Run all scans'}</span>
            </span>
          </Button>
        </div>

        <ResourcePageFilters
          searchValue={searchQuery}
          searchPlaceholder="Search checks by name, description, resource type, or check ID"
          onSearchChange={setSearchQuery}
          savedFilterSelect={{
            id: 'dashboard-saved-filter',
            label: 'Saved filter',
            value: activeSavedFilterId || '',
            options: [
              { label: 'Custom filters', value: '' },
              ...savedFilters.map((filter) => ({ label: filter.name, value: filter.id })),
            ],
            onChange: applySavedFilter,
          }}
          onSaveCurrentFilter={handleSaveCurrentFilter}
          selects={[
            {
              id: 'dashboard-resource-type',
              label: 'Resource type',
              value: selectedResourceType,
              options: [
                { label: 'All resource types', value: 'all' },
                ...resourceTypes.map((type) => ({ label: formatResourceType(type), value: type })),
              ],
              onChange: handleResourceTypeChange,
            },
            {
              id: 'dashboard-region-scope',
              label: 'Region',
              value: sharedRegion,
              options: [
                { label: 'All regions', value: 'all' },
                ...(sharedFilterOptions?.regions || []).map((region) => ({
                  label: region,
                  value: region,
                })),
              ],
              onChange: setSharedRegion,
            },
            {
              id: 'dashboard-environment-scope',
              label: 'Environment',
              value: sharedEnvironment,
              options: [
                { label: 'All environments', value: 'all' },
                ...(sharedFilterOptions?.environments || []).map((environment) => ({
                  label: formatResourceType(environment),
                  value: environment,
                })),
              ],
              onChange: setSharedEnvironment,
            },
          ]}
          children={
            <SharedTagFilterField
              selectedTags={parseSharedTagQuery(sharedTagQuery)}
              availableTags={sharedFilterOptions?.tags || []}
              tagLogic={sharedTagLogic}
              onChange={(tags) => setSharedTagQuery(tags.join(', '))}
              onTagLogicChange={setSharedTagLogic}
            />
          }
          onReset={() => {
            setSearchQuery('');
            setSelectedResourceType('all');
            resetSharedOverviewFilters();
          }}
          resetDisabled={
            !activeSavedFilterId &&
            !searchQuery.trim() &&
            selectedResourceType === 'all' &&
            sharedRegion === 'all' &&
            sharedEnvironment === 'all' &&
            !sharedTagQuery.trim() &&
            sharedTagLogic === 'and'
          }
        />
        <div className="text-xs text-gray-500 dark:text-gray-400">
          Saved filters capture region, environment, and tag scope and can be applied from the overview pages.
        </div>

        {/* Scan and error feedback */}
        {errorBanner && (
          <div className={`rounded-lg border-2 p-4 ${
            errorBanner.type === 'info'
              ? 'border-primary-500 bg-primary-50 dark:bg-primary-900/20'
              : errorBanner.type === 'success'
                ? 'border-success-500 bg-success-50 dark:bg-success-900/20'
                : errorBanner.type === 'warning'
                  ? 'border-warning-500 bg-warning-50 dark:bg-warning-900/20'
                  : 'border-danger-500 bg-danger-50 dark:bg-danger-900/20'
          }`}>
            <div className="flex items-start justify-between">
              <div className="flex items-start space-x-3 flex-1">
                {errorBanner.type === 'info' ? (
                  <Loader2 className="mt-0.5 flex-shrink-0 animate-spin text-primary-600 dark:text-primary-400" size={24} />
                ) : errorBanner.type === 'success' || errorBanner.type === 'warning' ? (
                  <AlertCircle className={`mt-0.5 flex-shrink-0 ${
                    errorBanner.type === 'success'
                      ? 'text-success-600 dark:text-success-400'
                      : 'text-warning-600 dark:text-warning-400'
                  }`} size={24} />
                ) : (
                  <XCircle className="text-danger-600 dark:text-danger-400 flex-shrink-0 mt-0.5" size={24} />
                )}
                <div className="flex-1">
                  <h3 className={`font-semibold ${
                    errorBanner.type === 'info'
                      ? 'text-primary-800 dark:text-primary-300'
                      : errorBanner.type === 'success'
                        ? 'text-success-800 dark:text-success-300'
                        : errorBanner.type === 'warning'
                          ? 'text-warning-800 dark:text-warning-300'
                          : 'text-danger-800 dark:text-danger-300'
                  }`}>
                    {errorBanner.type === 'info'
                      ? 'Scan Running'
                      : errorBanner.type === 'success'
                        ? 'Scan Completed'
                        : errorBanner.type === 'warning'
                          ? 'Scan Completed With Errors'
                          : errorBanner.type === 'run_canceled'
                            ? 'Run Canceled'
                            : 'Error'}
                  </h3>
                  <p className={`mt-1 text-sm ${
                    errorBanner.type === 'info'
                      ? 'text-primary-700 dark:text-primary-400'
                      : errorBanner.type === 'success'
                        ? 'text-success-700 dark:text-success-400'
                        : errorBanner.type === 'warning'
                          ? 'text-warning-700 dark:text-warning-400'
                          : 'text-danger-700 dark:text-danger-400'
                  }`}>
                    {errorBanner.message}
                  </p>
                  {errorBanner.details && errorBanner.details.length > 0 && (
                    <ul className={`mt-3 list-disc space-y-1 pl-5 text-sm ${
                      errorBanner.type === 'warning'
                        ? 'text-warning-800 dark:text-warning-300'
                        : 'text-danger-800 dark:text-danger-300'
                    }`}>
                      {errorBanner.details.map((detail, index) => (
                        <li key={`${detail}-${index}`}>{detail}</li>
                      ))}
                    </ul>
                  )}
                </div>
              </div>
              <button
                onClick={() => setErrorBanner(null)}
                className={`ml-4 flex-shrink-0 ${
                  errorBanner.type === 'info'
                    ? 'text-primary-600 hover:text-primary-800 dark:text-primary-400 dark:hover:text-primary-300'
                    : errorBanner.type === 'success'
                      ? 'text-success-600 hover:text-success-800 dark:text-success-400 dark:hover:text-success-300'
                      : errorBanner.type === 'warning'
                        ? 'text-warning-600 hover:text-warning-800 dark:text-warning-400 dark:hover:text-warning-300'
                        : 'text-danger-600 hover:text-danger-800 dark:text-danger-400 dark:hover:text-danger-300'
                }`}
                aria-label="Dismiss message"
              >
                <X size={20} />
              </button>
            </div>
          </div>
        )}

        {/* Stats Cards */}
        <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-4 gap-6">
          <Card>
            <div className="flex items-center justify-between">
              <div>
                <div className="text-sm text-gray-600 dark:text-gray-400">Total Checks</div>
                <div className="mt-2 text-3xl font-bold text-gray-900 dark:text-white">
                  {selectedResourceType === 'all' 
                    ? (checks?.length || 0)
                    : (filteredChecks?.length || 0)}
                </div>
              </div>
              <div className="p-3 bg-primary-100 dark:bg-primary-900/30 rounded-lg">
                <FileText className="text-primary-600 dark:text-primary-400" size={24} />
              </div>
            </div>
          </Card>

          <Card>
            <div className="flex items-center justify-between">
              <div>
                <div className="text-sm text-gray-600 dark:text-gray-400">Resources Found</div>
                <div className="mt-2 text-3xl font-bold text-gray-900 dark:text-white">
                  {totalResources}
                </div>
              </div>
              <div className="p-3 bg-primary-100 dark:bg-primary-900/30 rounded-lg">
                <TrendingUp className="text-primary-600 dark:text-primary-400" size={24} />
              </div>
            </div>
          </Card>

          <Card>
            <div className="flex items-center justify-between">
              <div>
                <div className="text-sm text-gray-600 dark:text-gray-400">Potential Yearly Savings</div>
                <div className="mt-2 text-3xl font-bold text-gray-900 dark:text-white">
                  ${totalSavings.toFixed(2)}
                </div>
                {savingsSummary.sharedResourceCount > 0 && (
                  <div className="mt-1 text-xs text-gray-500 dark:text-gray-400">
                    {savingsSummary.sharedResourceCount} resource
                    {savingsSummary.sharedResourceCount === 1 ? ' is' : 's are'} flagged by more
                    than one check and counted once. Adding every check separately would read
                    ${savingsSummary.rawTotal.toFixed(2)}.
                  </div>
                )}
              </div>
              <div className="p-3 bg-primary-100 dark:bg-primary-900/30 rounded-lg">
                <DollarSign className="text-primary-600 dark:text-primary-400" size={24} />
              </div>
            </div>
          </Card>

          <Card>
            <div className="flex items-center justify-between">
              <div>
                <div className="text-sm text-gray-600 dark:text-gray-400">Active Policies</div>
                <div className="mt-2 text-3xl font-bold text-gray-900 dark:text-white">
                  0
                </div>
              </div>
              <div className="p-3 bg-primary-100 dark:bg-primary-900/30 rounded-lg">
                <AlertCircle className="text-primary-600 dark:text-primary-400" size={24} />
              </div>
            </div>
          </Card>
        </div>

        {/* Checks List */}
        <Card 
          actions={
            <div className="flex items-center space-x-2">
              <Filter className="text-gray-500 dark:text-gray-400" size={18} />
              <select
                value={groupBy}
                onChange={(e) => setGroupBy(e.target.value as 'resource_type' | 'account' | 'region')}
                className="px-3 py-1.5 text-sm border border-gray-300 dark:border-gray-600 rounded-lg bg-white dark:bg-gray-800 text-gray-900 dark:text-white focus:outline-none focus:ring-2 focus:ring-primary-500 focus:border-transparent"
              >
                <option value="resource_type">Group by Resource Type</option>
                <option value="account">Group by Account</option>
                <option value="region">Group by Region</option>
              </select>
              <select
                value={sortBy}
                onChange={(e) => setSortBy(e.target.value as 'name' | 'resources' | 'savings')}
                className="px-3 py-1.5 text-sm border border-gray-300 dark:border-gray-600 rounded-lg bg-white dark:bg-gray-800 text-gray-900 dark:text-white focus:outline-none focus:ring-2 focus:ring-primary-500 focus:border-transparent"
              >
                <option value="name">Name</option>
                <option value="resources">Resources</option>
                <option value="savings">Potential Savings</option>
              </select>
            </div>
          }
        >
          {checksLoading ? (
            <div className="text-center py-12 text-gray-500 dark:text-gray-400">
              <Loader2 className="animate-spin mx-auto mb-4" size={32} />
              Loading checks...
            </div>
          ) : filteredChecks && filteredChecks.length > 0 ? (
            <div className="space-y-6">
              {groupedChecks.map((group) => (
                <div key={group.key} className="rounded-lg border border-gray-200 dark:border-gray-700">
                  <div className="grid w-full grid-cols-1 items-center gap-4 px-4 py-4 hover:bg-gray-50 md:grid-cols-[minmax(220px,1.4fr)_minmax(150px,1fr)_minmax(120px,0.8fr)_minmax(160px,0.9fr)_auto] dark:hover:bg-gray-800/50">
                    <button
                      type="button"
                      onClick={() => handleGroupHeaderClick(group.key, group.iconType)}
                      className="flex items-center gap-3 text-left"
                    >
                      {expandedGroups.has(group.key) ? (
                        <ChevronDown className="text-gray-500 dark:text-gray-400" size={18} />
                      ) : (
                        <ChevronRight className="text-gray-500 dark:text-gray-400" size={18} />
                      )}
                      <div>
                        <div className="flex items-center gap-2 text-sm font-semibold uppercase tracking-wide text-gray-700 dark:text-gray-200">
                          {group.iconType && <ResourceTypeIcon resourceType={group.iconType} size={48} />}
                          <span>{group.label}</span>
                        </div>
                        <div className="mt-1 text-xs text-gray-500 dark:text-gray-400">
                          {group.items.length} check{group.items.length === 1 ? '' : 's'}
                        </div>
                      </div>
                    </button>
                    <div className="hidden md:flex md:justify-center">
                      <SavingsSparkline values={group.trend} labels={group.trendLabels} />
                    </div>
                    <div className="text-left md:text-center">
                      <div className="text-xs text-gray-500 dark:text-gray-400">Resources</div>
                      <div className="font-semibold text-gray-900 dark:text-white">{group.resourcesFound}</div>
                    </div>
                    <div className="text-left md:text-center">
                      <div className="text-xs text-gray-500 dark:text-gray-400">Potential Savings</div>
                      <div className="font-semibold text-gray-900 dark:text-white">
                        ${group.savings.toFixed(2)}
                      </div>
                    </div>
                    {groupBy === 'resource_type' && (
                      <div className="flex justify-start md:justify-end">
                        <Button
                          variant="secondary"
                          size="sm"
                          onClick={() => handleRunResourceTypeChecks(group.iconType || group.label, group.items)}
                          disabled={
                            bulkRunScope !== null ||
                            !group.items.some(
                              (check) => !inactiveCheckIds.has(check.check_id) && !runningChecks.has(check.check_id)
                            )
                          }
                        >
                          <span className="flex items-center gap-2 whitespace-nowrap">
                            {bulkRunScope === `resource_type:${group.iconType || group.label}` ? (
                              <Loader2 className="animate-spin" size={16} />
                            ) : (
                              <Play size={16} />
                            )}
                            <span>
                              {bulkRunScope === `resource_type:${group.iconType || group.label}`
                                ? 'Running...'
                                : 'Run scans'}
                            </span>
                          </span>
                        </Button>
                      </div>
                    )}
                  </div>

                  {expandedGroups.has(group.key) && (
                    <div className="space-y-4 border-t border-gray-200 px-4 py-4 dark:border-gray-700">
                      {group.items.map((check) => {
                        const state = checkStates.get(check.check_id);
                        const displayState = state
                          ? {
                              ...state,
                              resources_found: adjustOptimizationCount(state.resources_found, profile),
                              potential_savings_yearly: adjustOptimizationSavings(
                                state.potential_savings_yearly,
                                profile
                              ),
                            }
                          : state;
                        const isRunning = runningChecks.has(check.check_id);
                        const matchingPolicy = policies?.find((policy) => policy.check_id === check.check_id);
                        const isInactive = matchingPolicy?.status === 'inactive';
                        const testActionsEnabled = !!featureFlags?.test_action;
                        const savingsHistory = normalizeSavingsHistoryEntry(savingsHistories?.[check.check_id]);

                        return (
                          <CheckCard
                            key={check.check_id}
                            check={check}
                            state={displayState}
                            savingsTrend={savingsHistory.values.map((value) =>
                              adjustOptimizationSavings(value, profile)
                            )}
                            savingsTrendLabels={savingsHistory.labels}
                            isRunning={isRunning}
                            disableRun={!!isInactive || bulkRunScope !== null}
                            onRunCheck={handleRunCheck}
                            onClick={(check, state) => {
                              if (state?.last_run || state?.status !== 'idle') {
                                navigate(`/dashboard/checks/${check.check_id}`);
                              }
                            }}
                            onTestClick={testActionsEnabled ? () => setTestCheck(check) : undefined}
                          />
                        );
                      })}
                    </div>
                  )}
                </div>
              ))}
            </div>
          ) : (
            <div className="text-center py-12 text-gray-500 dark:text-gray-400">
              {selectedResourceType === 'all' 
                ? 'No checks available.'
                : `No checks found for resource type "${selectedResourceType}".`}
            </div>
          )}
        </Card>
      </div>

      {testCheck && (
        <TestActionDialog
          isOpen={true}
          onClose={() => setTestCheck(null)}
          check={testCheck}
          actions={getActionsForCheck(testCheck)}
          defaultAccountId={userSettings?.account}
          defaultRegion={userSettings?.region}
          onRun={(payload) => checksApi.executeAction(testCheck.check_id, payload)}
        />
      )}

    </Layout>
  );
};
  const getResourceOverviewRoute = (resourceType: string): string | null => {
    const normalized = resourceType.trim().toLowerCase();
    if (normalized === 'ec2') return '/dashboard/resources/ec2';
    if (normalized === 'asg' || normalized === 'auto_scaling_group' || normalized === 'autoscaling_group' || normalized === 'auto-scaling-group') return '/dashboard/resources/asg';
    if (normalized === 'dynamodb' || normalized === 'dynamodb_table' || normalized === 'dynamodb_gsi') return '/dashboard/resources/dynamodb';
    if (normalized === 'ebs') return '/dashboard/resources/ebs';
    if (normalized === 's3') return '/dashboard/resources/s3';
    if (normalized === 'elasticache' || normalized === 'elasticache_cluster' || normalized === 'elasticache_replication_group') return '/dashboard/resources/elasticache';
    if (normalized === 'rds' || normalized === 'rds_instance') return '/dashboard/resources/rds';
    return null;
  };
