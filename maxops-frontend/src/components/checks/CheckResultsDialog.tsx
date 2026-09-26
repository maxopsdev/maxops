import React, { useState, useEffect, useMemo } from 'react';
import { Modal } from '@/components/common/Modal';
import { Button } from '@/components/common/Button';
import { Card } from '@/components/common/Card';
import { Input } from '@/components/common/Input';
import { 
  Download, 
  FileText, 
  DollarSign, 
  TrendingUp, 
  Tag, 
  BarChart3, 
  BookOpen,
  Zap,
  CheckCircle,
  XCircle,
  Loader2
} from 'lucide-react';
import { LineChart, Line, XAxis, YAxis, CartesianGrid, Tooltip, Legend, ResponsiveContainer } from 'recharts';
import { checksApi } from '@/services/checks';
import { settingsApi } from '@/services/settings';
import type { CheckMetadata } from '@/services/checks';
import { SnoozeDialog } from './SnoozeDialog';
import { getActionsForCheck } from './checkActions';
import { useQuery, useMutation, useQueryClient } from 'react-query';
import { Clock, Shield, ShieldOff } from 'lucide-react';
import {
  getElasticacheNodeTypeOptions,
  getPreferredElasticacheNodeType,
} from './elasticacheNodeTypes';
import {
  formatActionLabelWithImpact,
  getActionImpact,
  getActionImpactStyles,
} from './actionImpact';
import { STATUS_COLORS, chartAxisLineColor, chartGridColor } from '@/styles/chartColors';
import { useTheme } from '@/contexts/ThemeContext';

interface CheckResultsDialogProps {
  isOpen: boolean;
  onClose: () => void;
  check: CheckMetadata;
  checkState?: {
    resources_found: number;
    potential_savings_yearly: number;
    last_run?: string;
    status?: string;
  };
}

export const CheckResultsDialog: React.FC<CheckResultsDialogProps> = ({
  isOpen,
  onClose,
  check,
  checkState,
}) => {
  const [selectedTab, setSelectedTab] = useState<'overview' | 'resources' | 'metrics' | 'trends' | 'docs'>('overview');
  const [snoozeResource, setSnoozeResource] = useState<{ resourceId: string } | null>(null);
  const [resourceExemptions, setResourceExemptions] = useState<Record<string, {
    exempted: boolean;
    snoozed_until: string | null;
    snooze_days: number | null;
  }>>({});
  const [cloudwatchAgentStatuses, setCloudwatchAgentStatuses] = useState<Record<string, {
    installed: boolean | null;
    status: 'installed' | 'not_installed' | 'unknown' | 'installing' | 'error';
  }>>({});
  const [selectedResources, setSelectedResources] = useState<Set<string>>(new Set());
  const [resourceActions, setResourceActions] = useState<Record<string, string>>({});
  const [bulkAction, setBulkAction] = useState('');
  const [actionDialog, setActionDialog] = useState<{ action: string; resourceIds: string[] } | null>(null);
  const [actionResult, setActionResult] = useState<string | null>(null);
  const [executingResources, setExecutingResources] = useState<Set<string>>(new Set());
  const [impactAcknowledged, setImpactAcknowledged] = useState(false);
  const [targetInstanceClass, setTargetInstanceClass] = useState('');
  const [applyImmediately, setApplyImmediately] = useState(true);
  const [archivalDays, setArchivalDays] = useState('30');
  const [expirationDays, setExpirationDays] = useState('90');
  const [noncurrentExpirationDays, setNoncurrentExpirationDays] = useState('30');
  const [provisionedReadCapacity, setProvisionedReadCapacity] = useState('');
  const [provisionedWriteCapacity, setProvisionedWriteCapacity] = useState('');
  const [reduceRcuValue, setReduceRcuValue] = useState('');
  const [reduceWcuValue, setReduceWcuValue] = useState('');
  const [elasticacheTargetNodeType, setElasticacheTargetNodeType] = useState('');
  const [elasticacheNodeCount, setElasticacheNodeCount] = useState('');
  const [elasticacheEngineVersion, setElasticacheEngineVersion] = useState('');
  const [elasticacheTargetReplicationGroupId, setElasticacheTargetReplicationGroupId] = useState('');
  const [elasticacheTransitEncryptionEnabled, setElasticacheTransitEncryptionEnabled] = useState(true);
  const [ebsDownsizeIops, setEbsDownsizeIops] = useState('');
  const [ebsDownsizeThroughput, setEbsDownsizeThroughput] = useState('');
  const [ebsDownsizeVolumeType, setEbsDownsizeVolumeType] = useState('');
  const [ebsReduceIops, setEbsReduceIops] = useState('');
  const [ebsLifecyclePolicyName, setEbsLifecyclePolicyName] = useState('');
  const [ebsLifecycleIntervalHours, setEbsLifecycleIntervalHours] = useState('24');
  const [ebsLifecycleRetainCount, setEbsLifecycleRetainCount] = useState('7');
  const [ebsLifecycleTagKey, setEbsLifecycleTagKey] = useState('maxops_policy');
  const [ebsLifecycleTagValue, setEbsLifecycleTagValue] = useState('');
  const [ebsLifecycleRoleArn, setEbsLifecycleRoleArn] = useState('');
  const [s3TransitionDays, setS3TransitionDays] = useState('30');
  const [s3TransitionStorageClass, setS3TransitionStorageClass] = useState('GLACIER');
  const [s3ExpirationDays, setS3ExpirationDays] = useState('365');
  const [s3AbortMpuDays, setS3AbortMpuDays] = useState('7');
  const [efsTransitionToIA, setEfsTransitionToIA] = useState('AFTER_30_DAYS');
  const [efsPerformanceMode, setEfsPerformanceMode] = useState('maxIO');
  const [efsThroughputMode, setEfsThroughputMode] = useState('provisioned');
  const [efsProvisionedThroughput, setEfsProvisionedThroughput] = useState('');
  const [efsCopyData, setEfsCopyData] = useState(false);
  const [efsDeleteOld, setEfsDeleteOld] = useState(false);
  const [lambdaMemorySize, setLambdaMemorySize] = useState('');
  const [lambdaProvisionedConcurrency, setLambdaProvisionedConcurrency] = useState('');
  const [lambdaLogRetentionDays, setLambdaLogRetentionDays] = useState('7');
  const [efsMigrationStatus, setEfsMigrationStatus] = useState<{
    isPolling: boolean;
    resourceId?: string;
    taskExecutionArn?: string;
    status?: string;
    progress?: {
      bytes_transferred?: number;
      files_transferred?: number;
      files_skipped?: number;
    };
    error?: string;
    oldFileSystemId?: string;
    newFileSystemId?: string;
    accountId?: string;
    region?: string;
    manualInstructions?: string[];
  } | null>(null);
  const queryClient = useQueryClient();
  const { theme } = useTheme();

  const actionOptions = getActionsForCheck(check);
  const bulkActionImpact = useMemo(() => getActionImpact(bulkAction), [bulkAction]);
  const actionDialogImpact = useMemo(
    () => (actionDialog ? getActionImpact(actionDialog.action) : null),
    [actionDialog]
  );
  const actionDialogImpactStyles = useMemo(
    () => (actionDialogImpact ? getActionImpactStyles(actionDialogImpact.level) : null),
    [actionDialogImpact]
  );
  const isOnDemandBilling = (metadata?: Record<string, any>): boolean => {
    if (!metadata) return false;
    const mode = metadata.billing_mode || metadata.BillingMode;
    return typeof mode === 'string' && mode.toUpperCase() === 'PAY_PER_REQUEST';
  };
  const filterActionOptions = (
    options: string[],
    resourcesToCheck: Array<{ metadata?: Record<string, any> }>
  ): string[] => {
    const checksRequiringBillingFilter = new Set([
      'dynamodb_underutilized_tables',
      'dynamodb_best_fit_on_demand',
    ]);
    if (!checksRequiringBillingFilter.has(check.check_id)) {
      return options;
    }
    const hasOnDemand = resourcesToCheck.some((resource) => isOnDemandBilling(resource.metadata));
    if (!hasOnDemand) return options;
    return options.filter((option) => option !== 'Switch to On-Demand billing');
  };

  // Fetch stored resource details from the last run
  // Derived from the service rather than restated: this copy had already
  // drifted, missing the `tags` field the API sends and the CSV export reads.
  const { data: storedResources, isLoading } = useQuery<
    Awaited<ReturnType<typeof checksApi.getCheckResources>>
  >(
    ['check-resources', check.check_id],
    () => checksApi.getCheckResources(check.check_id),
    {
      enabled: isOpen, // Always fetch latest stored resources for this check
      staleTime: 30000, // Cache for 30 seconds
      refetchOnWindowFocus: false,
    }
  );

  const { data: userSettings } = useQuery(
    ['user-settings', check.check_id],
    () => settingsApi.getSettings(),
    { retry: false }
  );

  const resources = storedResources?.resources || [];
  const resourceLookup = useMemo(
    () =>
      new Map(
        resources
          .filter((resource) => resource.resource_id)
          .map((resource) => [resource.resource_id!, resource])
      ),
    [resources]
  );
  const primaryActionResourceId = actionDialog?.resourceIds?.[0];
  const primaryActionResource = primaryActionResourceId
    ? resourceLookup.get(primaryActionResourceId)
    : undefined;
  const primaryActionMetadata = primaryActionResource?.metadata || {};
  const isLambdaRightsizeAction =
    actionDialog?.action === 'Rightsize memory using duration vs memory analysis';
  const recommendedLambdaMemoryMb =
    Number(
      primaryActionMetadata?.recommended_memory_mb ??
      primaryActionMetadata?.recommended_memory ??
      primaryActionMetadata?.recommended_mb
    ) || 0;

  const rdsTargetResourceId = actionDialog?.resourceIds?.[0];
  const rdsTargetResource = rdsTargetResourceId ? resourceLookup.get(rdsTargetResourceId) : undefined;
  const rdsTargetRegion = rdsTargetResource?.region || userSettings?.region;
  const shouldLoadRdsClasses =
    actionDialog?.action === 'Migrate to Graviton' && !!rdsTargetResourceId && !!rdsTargetRegion;
  const { data: rdsClassOptions, isLoading: rdsClassLoading } = useQuery(
    ['rds-instance-classes', check.check_id, rdsTargetResourceId, rdsTargetRegion],
    () => checksApi.getRdsInstanceClasses(check.check_id, rdsTargetResourceId!, rdsTargetRegion!),
    { enabled: shouldLoadRdsClasses }
  );

  const elasticacheTargetResourceId = actionDialog?.resourceIds?.[0];
  const elasticacheTargetResource = elasticacheTargetResourceId
    ? resourceLookup.get(elasticacheTargetResourceId)
    : undefined;
  const elasticacheCurrentNodeType =
    elasticacheTargetResource?.metadata?.CacheNodeType ||
    elasticacheTargetResource?.metadata?.cache_node_type;
  const elasticacheCurrentNodeCount = useMemo(() => {
    const metadata = elasticacheTargetResource?.metadata;
    if (!metadata) return null;
    if (Array.isArray(metadata.MemberClusters) && metadata.MemberClusters.length > 0) {
      return metadata.MemberClusters.length;
    }
    if (metadata.NumCacheNodes !== undefined) {
      return Number(metadata.NumCacheNodes);
    }
    return null;
  }, [elasticacheTargetResource]);
  const elasticacheNodeTypeOptions = useMemo(
    () => getElasticacheNodeTypeOptions(actionDialog?.action),
    [actionDialog?.action]
  );

  useEffect(() => {
    if (!actionDialog) return;
    setActionResult(null);
    setImpactAcknowledged(false);
    setTargetInstanceClass('');
    setApplyImmediately(true);
    setProvisionedReadCapacity('');
    setProvisionedWriteCapacity('');
    setReduceRcuValue('');
    setReduceWcuValue('');
    setElasticacheTargetNodeType('');
    setElasticacheNodeCount('');
    setElasticacheEngineVersion('');
    setElasticacheTargetReplicationGroupId('');
    setElasticacheTransitEncryptionEnabled(true);
    setEbsDownsizeIops('');
    setEbsDownsizeThroughput('');
    setEbsDownsizeVolumeType('');
    setEbsReduceIops('');
    setEbsLifecyclePolicyName('');
    setEbsLifecycleIntervalHours('24');
    setEbsLifecycleRetainCount('7');
    setEbsLifecycleTagKey('maxops_policy');
    setEbsLifecycleTagValue('');
    setEbsLifecycleRoleArn('');
    setS3TransitionDays('30');
    setS3TransitionStorageClass('GLACIER');
    setS3ExpirationDays('365');
    setS3AbortMpuDays('7');
    setEfsTransitionToIA('AFTER_30_DAYS');
    if (actionDialog.action === 'lifecycle policy') {
      const resourceId = actionDialog.resourceIds?.[0];
      const defaultName = resourceId ? `maxops-ebs-${resourceId}` : 'maxops-ebs-policy';
      setEbsLifecyclePolicyName(defaultName);
      setEbsLifecycleTagValue(defaultName);
    }
    if (actionDialog.action === 'Modify volume type to gp3; tune IOPS/throughput') {
      setEbsDownsizeVolumeType('gp3');
    }
    if (
      actionDialog.action === 'Reduce provisioned IOPS' ||
      actionDialog.action === 'Reduce provisioned IOPS or migrate to gp3'
    ) {
      const suggestedIops = getSuggestedEbsIops(primaryActionMetadata);
      if (suggestedIops && suggestedIops > 0) {
        setEbsReduceIops(String(suggestedIops));
      }
    }
    if (
      actionDialog.action === 'Downsize volume' ||
      actionDialog.action === 'Modify volume type to gp3; tune IOPS/throughput' ||
      actionDialog.action === 'Downsize volume; consider filesystem resize + snapshot/restore'
    ) {
      const suggestedIops = getSuggestedEbsIops(primaryActionMetadata);
      const suggestedThroughput = getSuggestedEbsThroughput(primaryActionMetadata);
      if (suggestedIops && suggestedIops > 0) {
        setEbsDownsizeIops(String(suggestedIops));
      }
      if (suggestedThroughput && suggestedThroughput > 0) {
        setEbsDownsizeThroughput(String(suggestedThroughput));
      }
    }
  }, [actionDialog]);

  useEffect(() => {
    if (!actionDialog || !isLambdaRightsizeAction) return;
    if (recommendedLambdaMemoryMb > 0) {
      setLambdaMemorySize(String(recommendedLambdaMemoryMb));
    }
  }, [actionDialog, isLambdaRightsizeAction, recommendedLambdaMemoryMb]);

  useEffect(() => {
    if (!actionDialog) return;
    if (
      actionDialog.action !== 'Downsize' &&
      actionDialog.action !== 'Migrate to Graviton node type'
    ) {
      return;
    }
    if (elasticacheTargetNodeType) return;
    const preferred = getPreferredElasticacheNodeType(
      actionDialog.action,
      elasticacheCurrentNodeType,
      elasticacheNodeTypeOptions
    );
    if (preferred) {
      setElasticacheTargetNodeType(preferred);
    }
  }, [
    actionDialog,
    elasticacheCurrentNodeType,
    elasticacheTargetNodeType,
    elasticacheNodeTypeOptions,
  ]);

  useEffect(() => {
    if (!shouldLoadRdsClasses) return;
    if (!rdsClassOptions?.graviton_instance_classes?.length) return;
    if (targetInstanceClass) return;

    const currentClass = rdsClassOptions.current_instance_class;
    const options = rdsClassOptions.graviton_instance_classes;
    let preferred = options[0];
    if (currentClass) {
      const parts = currentClass.split('.');
      if (parts.length >= 3) {
        const size = parts.slice(2).join('.');
        const sizeMatch = options.find((cls) => cls.endsWith(`.${size}`));
        if (sizeMatch) {
          preferred = sizeMatch;
        }
      }
    }
    setTargetInstanceClass(preferred);
  }, [shouldLoadRdsClasses, rdsClassOptions, targetInstanceClass]);

  // Calculate efficiency score based on potential savings
  const calculateEfficiencyScore = (resources: Array<{ metadata?: Record<string, any> }>): number => {
    if (!resources || resources.length === 0) return 0;
    
    // Simple efficiency score based on potential savings
    // Higher savings = higher efficiency score
    const totalSavings = resources.reduce((sum, r) => {
      return sum + (r.metadata?.potential_savings_yearly || 
        (r.metadata?.potential_savings_monthly ? r.metadata.potential_savings_monthly * 12 : 0));
    }, 0);
    
    // Normalize to 0-100 scale (assuming max savings of $120,000/year = 100)
    return Math.min(100, Math.round((totalSavings / 120000) * 100));
  };

  const isSnoozedActive = (resourceId?: string): boolean => {
    if (!resourceId) return false;
    const exemption = resourceExemptions[resourceId];
    if (!exemption || !exemption.snoozed_until) return false;
    const snoozedUntil = new Date(exemption.snoozed_until);
    if (isNaN(snoozedUntil.getTime())) return false;
    return snoozedUntil > new Date();
  };

  const getCloudWatchAgentLabel = (status?: string): string => {
    switch (status) {
      case 'installed':
        return 'Installed';
      case 'not_installed':
        return 'Not installed';
      case 'installing':
        return 'Installing...';
      case 'error':
        return 'Error';
      case 'unknown':
      default:
        return 'Unknown';
    }
  };

  const toggleSelectedResource = (resourceId: string) => {
    setSelectedResources(prev => {
      const next = new Set(prev);
      if (next.has(resourceId)) {
        next.delete(resourceId);
      } else {
        next.add(resourceId);
      }
      return next;
    });
  };

  const openActionDialog = async (resourceIdsOverride?: string[], actionOverride?: string) => {
    const resourceIds = resourceIdsOverride && resourceIdsOverride.length > 0
      ? resourceIdsOverride
      : selectedResources.size > 0
      ? Array.from(selectedResources)
      : resources.map((resource) => resource.resource_id!).filter(Boolean);
    const actionToUse = actionOverride || bulkAction || (resourceIds.length === 1 ? resourceActions[resourceIds[0]] : '');
    if (!actionToUse) {
      alert('Please select an action to execute.');
      return;
    }
    
    // Clear migration status if it's for a different resource
    if (efsMigrationStatus && efsMigrationStatus.resourceId && 
        resourceIds.length === 1 && efsMigrationStatus.resourceId !== resourceIds[0]) {
      setEfsMigrationStatus(null);
    }
    
    // Check for existing action status for the first resource (single resource actions)
    if (resourceIds.length === 1) {
      try {
        const actionStatus = await checksApi.getActionStatus(check.check_id, resourceIds[0]);
        if (actionStatus.status !== 'none' && actionStatus.status !== 'completed' && actionStatus.status !== 'failed') {
          // There's a running action - show its status
          setActionResult(
            `Action '${actionStatus.action}' is already running for this resource.\n` +
            `Status: ${actionStatus.status}\n` +
            `Started: ${actionStatus.started_at ? new Date(actionStatus.started_at).toLocaleString() : 'Unknown'}\n` +
            (actionStatus.message ? `Message: ${actionStatus.message}` : '')
          );
          // Set migration status if it requires polling
          if (actionStatus.requires_polling && actionStatus.polling_identifier) {
            const details = actionStatus.details || {};
            setEfsMigrationStatus({
              isPolling: true,
              resourceId: resourceIds[0],
              taskExecutionArn: actionStatus.polling_identifier,
              status: 'TRANSFERRING',
              oldFileSystemId: details.old_file_system_id,
              newFileSystemId: details.new_file_system_id,
              accountId: resourceLookup.get(resourceIds[0])?.account_id || userSettings?.account,
              region: resourceLookup.get(resourceIds[0])?.region || userSettings?.region,
            });
          }
        }
      } catch (error) {
        // Ignore errors when checking action status
        console.error('Failed to check action status:', error);
      }
    }
    
    setActionResult(null);
    setActionDialog({ action: actionToUse, resourceIds });
  };

  const reduceRcuCapacities = useMemo(() => {
    if (actionDialog?.action !== 'Reduce RCU') return null;
    const values = (actionDialog.resourceIds || [])
      .map((id) => {
        const metadata = resourceLookup.get(id)?.metadata;
        return (
          metadata?.read_capacity_units ??
          metadata?.ProvisionedThroughput?.ReadCapacityUnits ??
          null
        );
      })
      .filter((value) => typeof value === 'number' && Number.isFinite(value));
    if (values.length === 0) {
      return { values: [], min: null, max: null };
    }
    return {
      values,
      min: Math.min(...values),
      max: Math.max(...values),
    };
  }, [actionDialog, resourceLookup]);

  const reduceWcuCapacities = useMemo(() => {
    if (actionDialog?.action !== 'Reduce WCU') return null;
    const values = (actionDialog.resourceIds || [])
      .map((id) => {
        const metadata = resourceLookup.get(id)?.metadata;
        return (
          metadata?.write_capacity_units ??
          metadata?.ProvisionedThroughput?.WriteCapacityUnits ??
          null
        );
      })
      .filter((value) => typeof value === 'number' && Number.isFinite(value));
    if (values.length === 0) {
      return { values: [], min: null, max: null };
    }
    return {
      values,
      min: Math.min(...values),
      max: Math.max(...values),
    };
  }, [actionDialog, resourceLookup]);

  const ebsProvisionedIops = useMemo(() => {
    if (
      actionDialog?.action !== 'Reduce provisioned IOPS' &&
      actionDialog?.action !== 'Reduce provisioned IOPS or migrate to gp3' &&
      actionDialog?.action !== 'Downsize volume' &&
      actionDialog?.action !== 'Modify volume type to gp3; tune IOPS/throughput' &&
      actionDialog?.action !== 'Downsize volume; consider filesystem resize + snapshot/restore'
    ) {
      return null;
    }
    const values = (actionDialog.resourceIds || [])
      .map((id) => resourceLookup.get(id)?.metadata?.iops ?? null)
      .filter((value) => typeof value === 'number' && Number.isFinite(value));
    if (values.length === 0) {
      return { values: [], min: null, max: null };
    }
    return {
      values,
      min: Math.min(...values),
      max: Math.max(...values),
    };
  }, [actionDialog, resourceLookup]);

  const ebsProvisionedThroughput = useMemo(() => {
    if (
      actionDialog?.action !== 'Downsize volume' &&
      actionDialog?.action !== 'Modify volume type to gp3; tune IOPS/throughput' &&
      actionDialog?.action !== 'Downsize volume; consider filesystem resize + snapshot/restore' &&
      actionDialog?.action !== 'Reduce provisioned IOPS or migrate to gp3'
    ) {
      return null;
    }
    const values = (actionDialog.resourceIds || [])
      .map((id) => resourceLookup.get(id)?.metadata?.throughput ?? null)
      .filter((value) => typeof value === 'number' && Number.isFinite(value));
    if (values.length === 0) {
      return { values: [], min: null, max: null };
    }
    return {
      values,
      min: Math.min(...values),
      max: Math.max(...values),
    };
  }, [actionDialog, resourceLookup]);

  const executeAction = async () => {
    if (!actionDialog) return;
    if (!actionDialog.resourceIds || actionDialog.resourceIds.length === 0) {
      setActionResult('No resources selected for this action.');
      return;
    }
    if (actionDialog.action === 'Migrate to Graviton' && !targetInstanceClass) {
      setActionResult('Please select a target instance class.');
      return;
    }
    if (actionDialog.action === 'Add policy - Add archival transition' && !archivalDays.trim()) {
      setActionResult('Please enter archival days.');
      return;
    }
    if (actionDialog.action === 'Add policy - Add expiration for old objects' && !expirationDays.trim()) {
      setActionResult('Please enter expiration days.');
      return;
    }
    if (actionDialog.action === 'Add policy - Expire noncurrent versions' && !noncurrentExpirationDays.trim()) {
      setActionResult('Please enter noncurrent expiration days.');
      return;
    }
    if (actionDialog.action === 'Add policy - Add noncurrent archival transition' && !archivalDays.trim()) {
      setActionResult('Please enter transition days.');
      return;
    }
    if (actionDialog.action === 'Reduce RCU') {
      const rcu = Number(reduceRcuValue);
      if (!reduceRcuValue.trim()) {
        setActionResult('Please enter read capacity units.');
        return;
      }
      if (!Number.isFinite(rcu) || rcu <= 0) {
        setActionResult('Read capacity must be a positive number.');
        return;
      }
      if (reduceRcuCapacities?.min !== null && reduceRcuCapacities?.min !== undefined && rcu > reduceRcuCapacities.min) {
        setActionResult('Read capacity cannot exceed the current read capacity.');
        return;
      }
    }
    if (actionDialog.action === 'Reduce WCU') {
      const wcu = Number(reduceWcuValue);
      if (!reduceWcuValue.trim()) {
        setActionResult('Please enter write capacity units.');
        return;
      }
      if (!Number.isFinite(wcu) || wcu <= 0) {
        setActionResult('Write capacity must be a positive number.');
        return;
      }
      if (reduceWcuCapacities?.min !== null && reduceWcuCapacities?.min !== undefined && wcu > reduceWcuCapacities.min) {
        setActionResult('Write capacity cannot exceed the current write capacity.');
        return;
      }
    }
    if (
      actionDialog.action === 'Downsize' ||
      actionDialog.action === 'Migrate to Graviton node type'
    ) {
      if (!elasticacheTargetNodeType.trim()) {
        setActionResult('Please enter a target node type.');
        return;
      }
      if (elasticacheNodeCount.trim()) {
        const nodeCount = Number(elasticacheNodeCount);
        if (!Number.isFinite(nodeCount) || nodeCount <= 0) {
          setActionResult('Node count must be a positive number.');
          return;
        }
        if (
          actionDialog.action === 'Downsize' &&
          elasticacheCurrentNodeCount !== null &&
          nodeCount > elasticacheCurrentNodeCount
        ) {
          setActionResult('Node count cannot exceed the current node count when downsizing.');
          return;
        }
      }
    }
    if (actionDialog.action === 'Upgrade to Valkey' && elasticacheEngineVersion.trim()) {
      if (!/^[0-9]+(\\.[0-9]+)*$/.test(elasticacheEngineVersion.trim())) {
        setActionResult('Engine version must be a dot-separated number (e.g., 7.1).');
        return;
      }
    }
    if (
      actionDialog.action === 'Reduce provisioned IOPS' ||
      actionDialog.action === 'Reduce provisioned IOPS or migrate to gp3'
    ) {
      const iops = Number(ebsReduceIops);
      if (!ebsReduceIops.trim()) {
        setActionResult('Please enter IOPS.');
        return;
      }
      if (!Number.isFinite(iops) || iops <= 0) {
        setActionResult('IOPS must be a positive number.');
        return;
      }
      if (ebsProvisionedIops?.min !== null && ebsProvisionedIops?.min !== undefined && iops > ebsProvisionedIops.min) {
        setActionResult('IOPS cannot exceed current provisioned IOPS.');
        return;
      }
    }
    if (
      actionDialog.action === 'Downsize volume' ||
      actionDialog.action === 'Modify volume type to gp3; tune IOPS/throughput' ||
      actionDialog.action === 'Downsize volume; consider filesystem resize + snapshot/restore'
    ) {
      const iopsValue = ebsDownsizeIops.trim();
      const throughputValue = ebsDownsizeThroughput.trim();
      const volumeTypeValue = ebsDownsizeVolumeType.trim();
      if (!iopsValue && !throughputValue && !volumeTypeValue) {
        setActionResult('Provide IOPS, throughput, or volume type to downsize.');
        return;
      }
      if (iopsValue) {
        const iops = Number(iopsValue);
        if (!Number.isFinite(iops) || iops <= 0) {
          setActionResult('IOPS must be a positive number.');
          return;
        }
        if (ebsProvisionedIops?.min !== null && ebsProvisionedIops?.min !== undefined && iops > ebsProvisionedIops.min) {
          setActionResult('IOPS cannot exceed current provisioned IOPS.');
          return;
        }
      }
      if (throughputValue) {
        const throughput = Number(throughputValue);
        if (!Number.isFinite(throughput) || throughput <= 0) {
          setActionResult('Throughput must be a positive number.');
          return;
        }
        if (
          ebsProvisionedThroughput?.min !== null &&
          ebsProvisionedThroughput?.min !== undefined &&
          throughput > ebsProvisionedThroughput.min
        ) {
          setActionResult('Throughput cannot exceed current provisioned throughput.');
          return;
        }
      }
    }
    if (actionDialog.action === 'lifecycle policy') {
      const interval = Number(ebsLifecycleIntervalHours);
      const retain = Number(ebsLifecycleRetainCount);
      if (!ebsLifecyclePolicyName.trim()) {
        setActionResult('Please enter a policy name.');
        return;
      }
      if (!ebsLifecycleRoleArn.trim()) {
        setActionResult('Please enter a role ARN.');
        return;
      }
      if (!Number.isFinite(interval) || interval <= 0) {
        setActionResult('Interval hours must be a positive number.');
        return;
      }
      if (!Number.isFinite(retain) || retain <= 0) {
        setActionResult('Retain count must be a positive number.');
        return;
      }
      if (!ebsLifecycleTagKey.trim() || !ebsLifecycleTagValue.trim()) {
        setActionResult('Please enter both tag key and tag value.');
        return;
      }
    }
    if (actionDialog.action === 'Use Provisioned Capacity') {
      const rcu = Number(provisionedReadCapacity);
      const wcu = Number(provisionedWriteCapacity);
      if (!provisionedReadCapacity.trim() || !provisionedWriteCapacity.trim()) {
        setActionResult('Please enter read and write capacity units.');
        return;
      }
      if (!Number.isFinite(rcu) || !Number.isFinite(wcu) || rcu <= 0 || wcu <= 0) {
        setActionResult('Capacity units must be positive numbers.');
        return;
      }
    }
    if (actionDialog.action === 'Modify throughput mode' && efsThroughputMode === 'provisioned') {
      if (!efsProvisionedThroughput.trim()) {
        setActionResult('Please enter provisioned throughput in MiB/s.');
        return;
      }
      const throughput = Number(efsProvisionedThroughput);
      if (!Number.isFinite(throughput) || throughput < 1) {
        setActionResult('Provisioned throughput must be at least 1 MiB/s.');
        return;
      }
    }
    if (
      actionDialog.action === 'Update memory configuration' ||
      actionDialog.action === 'Update memory' ||
      actionDialog.action === 'Rightsize memory using duration vs memory analysis'
    ) {
      const selectedMemory = Number(lambdaMemorySize);
      const fallbackMemory =
        recommendedLambdaMemoryMb > 0
          ? recommendedLambdaMemoryMb
          : Number(primaryActionMetadata?.MemorySize || 0);
      const effectiveMemory =
        Number.isFinite(selectedMemory) && selectedMemory > 0 ? selectedMemory : fallbackMemory;
      if (!Number.isFinite(effectiveMemory) || effectiveMemory < 128 || effectiveMemory > 10240) {
        setActionResult('Unable to determine a valid memory size for this Lambda action.');
        return;
      }
    }
    // Mark resources as executing
    setExecutingResources(new Set(actionDialog.resourceIds));
    try {
      const resourceMap = new Map(
        resources
          .filter((resource) => resource.resource_id)
          .map((resource) => [resource.resource_id!, resource])
      );
      const results = await Promise.all(
        actionDialog.resourceIds.map(async (resourceId) => {
          const resource = resourceMap.get(resourceId);
          const accountId = resource?.account_id || userSettings?.account;
          const region = resource?.region || userSettings?.region;
          if (!accountId || !region) {
            return { resourceId, status: 'error', message: 'Missing account_id or region.' };
          }
          try {
            const response = await checksApi.executeAction(check.check_id, {
              action: actionDialog.action,
              account_id: accountId,
              region,
              resource_id: resourceId,
              parameters:
                actionDialog.action === 'Migrate to Graviton'
                  ? {
                      target_instance_class: targetInstanceClass,
                      apply_immediately: applyImmediately,
                    }
                  : actionDialog.action === 'Add policy - Add archival transition'
                  ? {
                      archival_days: Number(archivalDays),
                    }
                  : actionDialog.action === 'Add policy - Add expiration for old objects'
                  ? {
                      expiration_days: Number(expirationDays),
                    }
                  : actionDialog.action === 'Add policy - Expire noncurrent versions'
                  ? {
                      noncurrent_expiration_days: Number(noncurrentExpirationDays),
                    }
                  : actionDialog.action === 'Add policy - Add noncurrent archival transition'
                  ? {
                      noncurrent_transition_days: Number(archivalDays),
                    }
                  : actionDialog.action === 'Use Provisioned Capacity'
                  ? {
                      read_capacity_units: Number(provisionedReadCapacity),
                      write_capacity_units: Number(provisionedWriteCapacity),
                    }
                  : actionDialog.action === 'Reduce RCU'
                  ? {
                      read_capacity_units: Number(reduceRcuValue),
                    }
                  : actionDialog.action === 'Reduce WCU'
                  ? {
                      write_capacity_units: Number(reduceWcuValue),
                    }
                  : actionDialog.action === 'Downsize' || actionDialog.action === 'Migrate to Graviton node type'
                  ? {
                      target_node_type: elasticacheTargetNodeType.trim(),
                      num_cache_nodes: elasticacheNodeCount.trim()
                        ? Number(elasticacheNodeCount)
                        : undefined,
                    }
                  : actionDialog.action === 'Upgrade to Valkey'
                  ? {
                      target_engine_version: elasticacheEngineVersion.trim() || undefined,
                    target_replication_group_id: elasticacheTargetReplicationGroupId.trim() || undefined,
                    transit_encryption_enabled: elasticacheTransitEncryptionEnabled,
                    }
                  : (
                      actionDialog.action === 'Reduce provisioned IOPS' ||
                      actionDialog.action === 'Reduce provisioned IOPS or migrate to gp3'
                    )
                  ? {
                      iops: Number(ebsReduceIops),
                    }
                  : (
                      actionDialog.action === 'Downsize volume' ||
                      actionDialog.action === 'Modify volume type to gp3; tune IOPS/throughput' ||
                      actionDialog.action === 'Downsize volume; consider filesystem resize + snapshot/restore'
                    )
                  ? {
                      iops: ebsDownsizeIops.trim() ? Number(ebsDownsizeIops) : undefined,
                      throughput: ebsDownsizeThroughput.trim() ? Number(ebsDownsizeThroughput) : undefined,
                      volume_type: ebsDownsizeVolumeType.trim() || undefined,
                    }
                  : actionDialog.action === 'lifecycle policy'
                  ? {
                      policy_name: ebsLifecyclePolicyName.trim(),
                      interval_hours: Number(ebsLifecycleIntervalHours),
                      retain_count: Number(ebsLifecycleRetainCount),
                      tag_key: ebsLifecycleTagKey.trim(),
                      tag_value: ebsLifecycleTagValue.trim(),
                      role_arn: ebsLifecycleRoleArn.trim(),
                    }
                  : actionDialog.action === 'Modify performance mode'
                  ? {
                      performance_mode: efsPerformanceMode,
                      copy_data: efsCopyData,
                      delete_old_after_copy: efsDeleteOld,
                    }
                  : actionDialog.action === 'Modify throughput mode'
                  ? {
                      throughput_mode: efsThroughputMode,
                      provisioned_throughput_in_mibps: efsThroughputMode === 'provisioned' && efsProvisionedThroughput.trim() 
                        ? Number(efsProvisionedThroughput) 
                        : undefined,
                    }
                  : actionDialog.action === 'Add lifecycle policy' && check.resource_type === 's3'
                  ? {
                      transition_days: Number(s3TransitionDays),
                      transition_storage_class: s3TransitionStorageClass,
                      expiration_days: Number(s3ExpirationDays),
                      abort_mpu_days: Number(s3AbortMpuDays),
                    }
                  : actionDialog.action === 'Add lifecycle policy' && check.resource_type === 'efs'
                  ? {
                      transition_to_ia: efsTransitionToIA,
                    }
                  : (
                      actionDialog.action === 'Update memory configuration' ||
                      actionDialog.action === 'Update memory' ||
                      actionDialog.action === 'Rightsize memory using duration vs memory analysis'
                    )
                  ? {
                      memory_size: Number(lambdaMemorySize) > 0
                        ? Number(lambdaMemorySize)
                        : (recommendedLambdaMemoryMb > 0
                          ? recommendedLambdaMemoryMb
                          : Number(primaryActionMetadata?.MemorySize || 128)),
                    }
                  : (
                      actionDialog.action === 'Reduce provisioned concurrency' ||
                      actionDialog.action === 'Update provisioned concurrency' ||
                      actionDialog.action === 'Reduce/disable provisioned concurrency'
                    )
                  ? {
                      provisioned_concurrent_executions: Number(lambdaProvisionedConcurrency),
                      qualifier: primaryActionMetadata?.ProvisionedConcurrencyConfig?.Qualifier,
                    }
                  : (
                      actionDialog.action === 'Reduce log verbosity and set retention' ||
                      actionDialog.action === 'Set log retention' ||
                      actionDialog.action === 'Reduce log verbosity; set retention'
                    )
                  ? {
                      retention_days: Number(lambdaLogRetentionDays),
                    }
                  : undefined,
            });
            
            // Check if this is an EFS migration with data copy that requires polling
            if (
              actionDialog.action === 'Modify performance mode' &&
              response.details?.requires_polling
            ) {
              // Start polling for DataSync status (if DataSync task exists) or show manual instructions
              if (response.details?.datasync_task_execution_arn) {
                setEfsMigrationStatus({
                  isPolling: true,
                  resourceId: resourceId,
                  taskExecutionArn: response.details.datasync_task_execution_arn,
                  status: 'INITIALIZING',
                  oldFileSystemId: response.details.old_file_system_id,
                  newFileSystemId: response.details.new_file_system_id,
                  accountId: accountId,
                  region: region,
                });
              } else if (response.details?.copy_instructions) {
                // DataSync failed but copy_data was requested - show manual instructions and keep dialog open
                setEfsMigrationStatus({
                  isPolling: false,
                  resourceId: resourceId,
                  taskExecutionArn: undefined,
                  status: 'MANUAL_REQUIRED',
                  oldFileSystemId: response.details.old_file_system_id,
                  newFileSystemId: response.details.new_file_system_id,
                  accountId: accountId,
                  region: region,
                  manualInstructions: response.details.copy_instructions,
                });
              }
              // Don't return yet - we'll poll in useEffect or show manual instructions
            }
            
            return { resourceId, status: response.status, message: response.message, details: response.details };
          } catch (error: any) {
            const message = error?.response?.data?.detail || error?.message || 'Failed to execute action.';
            console.error('Execute action failed:', {
              action: actionDialog.action,
              resourceId,
              message,
              error,
            });
            return { resourceId, status: 'error', message };
          }
        })
      );
      
      // If no migration polling started, show results normally
      if (!efsMigrationStatus?.isPolling) {
        const summary = results
          .map((item) => `${item.resourceId}: ${item.status} - ${item.message}`)
          .join('\n');
        setActionResult(summary);
        // Clear executing status for these resources
        setExecutingResources(prev => {
          const next = new Set(prev);
          actionDialog.resourceIds.forEach(id => next.delete(id));
          return next;
        });
      } else {
        // Keep dialog open and polling - execution will be cleared when migration completes
        setActionResult('Migration started. Monitoring progress...');
      }
    } catch (error: any) {
      const message = error?.message || 'Failed to execute action.';
      console.error('Execute action failed:', error);
      setActionResult(message);
      // Clear executing status on error
      setExecutingResources(prev => {
        const next = new Set(prev);
        actionDialog.resourceIds.forEach(id => next.delete(id));
        return next;
      });
      setEfsMigrationStatus(null);
    }
  };

  const loggingTargetBuckets = actionDialog?.action === 'Disable Logging Report and delete the log bucket'
    ? Array.from(
        new Set(
          (actionDialog.resourceIds || [])
            .map((id) => resourceLookup.get(id)?.metadata?.logging_target_bucket)
            .filter(Boolean)
        )
      )
    : [];
const inventoryTargetBuckets = actionDialog?.action === 'Disable Inventory Report and delete the log bucket'
  ? Array.from(
      new Set(
        (actionDialog.resourceIds || [])
          .flatMap((id) => resourceLookup.get(id)?.metadata?.inventory_target_buckets || [])
      )
    )
  : [];
const replicationTargetBuckets = actionDialog?.action === 'Disable replication and delete the replicate bucket'
  ? Array.from(
      new Set(
        (actionDialog.resourceIds || [])
          .flatMap((id) => resourceLookup.get(id)?.metadata?.replication_target_buckets || [])
      )
    )
  : [];

  const isExcludedFromSavings = (resourceId?: string): boolean => {
    if (!resourceId) return false;
    const exemption = resourceExemptions[resourceId];
    if (!exemption) return false;
    if (exemption.exempted) return true;
    return isSnoozedActive(resourceId);
  };

  const resourcesForSavings = resources.filter(
    (resource) => !isExcludedFromSavings(resource.resource_id)
  );

  const efficiencyScore = resourcesForSavings.length > 0
    ? calculateEfficiencyScore(resourcesForSavings)
    : 0;

  // Fetch exemption status for all resources
  const fetchExemptions = React.useCallback(async () => {
    if (resources.length === 0) return;
    const exemptions: Record<string, any> = {};
    await Promise.all(resources.map(async (resource) => {
      if (resource.resource_id) {
        try {
          const exemption = await checksApi.getResourceExemption(check.check_id, resource.resource_id);
          exemptions[resource.resource_id] = exemption;
        } catch (error) {
          // Resource may not have exemption record yet
          exemptions[resource.resource_id] = {
            exempted: false,
            snoozed_until: null,
            snooze_days: null
          };
        }
      }
    }));
    setResourceExemptions(exemptions);
  }, [resources, check.check_id]);

  const fetchCloudWatchAgentStatuses = React.useCallback(async () => {
    if (resources.length === 0) return;
    const statuses: Record<string, {
      installed: boolean | null;
      status: 'installed' | 'not_installed' | 'unknown' | 'installing' | 'error';
    }> = {};

    await Promise.all(resources.map(async (resource) => {
      if (resource.resource_id && resource.resource_type?.toLowerCase() === 'ec2') {
        try {
          const status = await checksApi.getCloudWatchAgentStatus(
            check.check_id,
            resource.resource_id,
            resource.region
          );
          statuses[resource.resource_id] = status;
        } catch (error) {
          statuses[resource.resource_id] = {
            installed: null,
            status: 'error'
          };
        }
      }
    }));
    setCloudwatchAgentStatuses(statuses);
  }, [resources, check.check_id]);

  React.useEffect(() => {
    if (selectedTab === 'resources') {
      fetchExemptions();
      fetchCloudWatchAgentStatuses();
    }
  }, [selectedTab, fetchExemptions, fetchCloudWatchAgentStatuses]);

  // Poll for DataSync task status when migration is in progress
  React.useEffect(() => {
    if (!efsMigrationStatus?.isPolling) {
      return;
    }
    
    // If manual instructions are required (no DataSync task), don't poll
    if (efsMigrationStatus.status === 'MANUAL_REQUIRED' || !efsMigrationStatus.taskExecutionArn) {
      return;
    }

    const pollInterval = setInterval(async () => {
      try {
        const status = await checksApi.getDatasyncStatus(
          check.check_id,
          efsMigrationStatus.taskExecutionArn!,
          efsMigrationStatus.region!,
          efsMigrationStatus.accountId!
        );

        setEfsMigrationStatus(prev => prev ? {
          ...prev,
          status: status.status,
          progress: {
            bytes_transferred: status.bytes_transferred,
            files_transferred: status.files_transferred,
            files_skipped: status.files_skipped,
          },
          error: status.error_code || status.error_detail,
        } : null);

        // If task completed successfully, delete old file system if requested
        if (status.status === 'SUCCESS') {
          clearInterval(pollInterval);
          setEfsMigrationStatus(prev => prev ? { ...prev, isPolling: false } : null);
          
          // Check if delete_old_after_copy was requested
          const deleteOld = actionDialog?.action === 'Modify performance mode' && efsDeleteOld;
          if (deleteOld && efsMigrationStatus.oldFileSystemId) {
            try {
              // Delete the old file system
              await checksApi.executeAction(check.check_id, {
                action: 'Delete file system',
                account_id: efsMigrationStatus.accountId!,
                region: efsMigrationStatus.region!,
                resource_id: efsMigrationStatus.oldFileSystemId,
              });
              
              setActionResult(
                `Migration completed successfully!\n` +
                `Data copied: ${status.files_transferred || 0} files, ${formatBytes(status.bytes_transferred || 0)}\n` +
                `Old file system ${efsMigrationStatus.oldFileSystemId} deleted.\n` +
                `New file system: ${efsMigrationStatus.newFileSystemId}`
              );
            } catch (deleteError: any) {
              const deleteMessage = deleteError?.response?.data?.detail || deleteError?.message || 'Failed to delete old file system';
              setActionResult(
                `Migration completed successfully!\n` +
                `Data copied: ${status.files_transferred || 0} files, ${formatBytes(status.bytes_transferred || 0)}\n` +
                `âš ï¸ Failed to delete old file system: ${deleteMessage}\n` +
                `Please delete ${efsMigrationStatus.oldFileSystemId} manually.\n` +
                `New file system: ${efsMigrationStatus.newFileSystemId}`
              );
            }
          } else {
            setActionResult(
              `Migration completed successfully!\n` +
              `Data copied: ${status.files_transferred || 0} files, ${formatBytes(status.bytes_transferred || 0)}\n` +
              `New file system: ${efsMigrationStatus.newFileSystemId}\n` +
              `Old file system: ${efsMigrationStatus.oldFileSystemId} (not deleted - delete manually if needed)`
            );
          }
          // Clear executing status for this resource
          if (efsMigrationStatus.resourceId) {
            setExecutingResources(prev => {
              const next = new Set(prev);
              next.delete(efsMigrationStatus.resourceId!);
              return next;
            });
          }
        } else if (status.status === 'ERROR') {
          clearInterval(pollInterval);
          setEfsMigrationStatus(prev => prev ? { ...prev, isPolling: false } : null);
          setActionResult(
            `Migration failed!\n` +
            `Error: ${status.error_code || status.error_detail || 'Unknown error'}\n` +
            `Task execution ARN: ${efsMigrationStatus.taskExecutionArn}\n` +
            `Please check AWS DataSync console for details.`
          );
          // Clear executing status for this resource
          if (efsMigrationStatus.resourceId) {
            setExecutingResources(prev => {
              const next = new Set(prev);
              next.delete(efsMigrationStatus.resourceId!);
              return next;
            });
          }
        }
      } catch (error: any) {
        console.error('Failed to poll DataSync status:', error);
        // Continue polling on error (might be temporary)
      }
    }, 5000); // Poll every 5 seconds

    return () => clearInterval(pollInterval);
  }, [efsMigrationStatus?.isPolling, efsMigrationStatus?.taskExecutionArn, check.check_id, efsDeleteOld, actionDialog?.action]);

  // Helper function to format bytes
  const formatBytes = (bytes: number): string => {
    if (bytes === 0) return '0 B';
    const k = 1024;
    const sizes = ['B', 'KB', 'MB', 'GB', 'TB'];
    const i = Math.floor(Math.log(bytes) / Math.log(k));
    return `${(bytes / Math.pow(k, i)).toFixed(2)} ${sizes[i]}`;
  };

  const formatUsd = (value?: number): string => {
    if (value === undefined || value === null || Number.isNaN(Number(value))) {
      return '$0.00';
    }
    const numericValue = Number(value);
    if (numericValue === 0) return '$0.00';
    if (Math.abs(numericValue) < 0.01) {
      return `$${numericValue.toFixed(6)}`;
    }
    return `$${numericValue.toFixed(2)}`;
  };

  const getSuggestedEbsIops = (metadata: Record<string, any>): number | null => {
    const avgIops = Number(metadata?.avg_iops ?? 0);
    const volumeType = String(metadata?.volume_type || metadata?.VolumeType || '').toLowerCase();
    const minIops = volumeType === 'gp3' ? 3000 : 100;
    const provisionedIops = Number(
      metadata?.iops ??
      metadata?.Iops ??
      metadata?.provisioned_iops ??
      0
    );

    if (avgIops > 0) {
      // 2x headroom above observed average for an "idle-safe" target.
      const basedOnUsage = Math.ceil(avgIops * 2);
      const baseline = Math.max(minIops, basedOnUsage);
      if (provisionedIops > 0) {
        return Math.min(provisionedIops, baseline);
      }
      return baseline;
    }

    if (provisionedIops > 0) {
      // Fallback when usage metrics are missing.
      return Math.max(minIops, Math.floor(provisionedIops * 0.5));
    }
    return null;
  };

  const getSuggestedEbsThroughput = (metadata: Record<string, any>): number | null => {
    const avgThroughput = Number(metadata?.avg_throughput_mb ?? 0);
    const volumeType = String(metadata?.volume_type || metadata?.VolumeType || '').toLowerCase();
    const minThroughput = volumeType === 'gp3' ? 125 : 1;
    if (avgThroughput > 0) {
      // 2x headroom above observed average throughput.
      return Math.max(minThroughput, Math.ceil(avgThroughput * 2));
    }
    const currentThroughput = Number(metadata?.throughput ?? 0);
    if (currentThroughput > 0) {
      return Math.max(minThroughput, Math.floor(currentThroughput * 0.5));
    }
    return null;
  };

  // Mutations for snooze and exempt
  const snoozeMutation = useMutation(
    ({ resourceId, days }: { resourceId: string; days: number }) =>
      checksApi.snoozeResource(check.check_id, resourceId, days),
    {
      onSuccess: async () => {
        queryClient.invalidateQueries(['check-resources', check.check_id]);
        const latestResults = await checksApi.getLatestResults();
        queryClient.setQueryData('latest-check-results', latestResults);
        // Refresh exemption status
        await fetchExemptions();
      }
    }
  );

  const exemptMutation = useMutation(
    ({ resourceId, exempted }: { resourceId: string; exempted: boolean }) =>
      checksApi.exemptResource(check.check_id, resourceId, exempted),
    {
      onSuccess: async () => {
        queryClient.invalidateQueries(['check-resources', check.check_id]);
        const latestResults = await checksApi.getLatestResults();
        queryClient.setQueryData('latest-check-results', latestResults);
        // Refresh exemption status
        await fetchExemptions();
      }
    }
  );

  const cancelSnoozeMutation = useMutation(
    ({ resourceId }: { resourceId: string }) =>
      checksApi.cancelSnoozeResource(check.check_id, resourceId),
    {
      onSuccess: async () => {
        queryClient.invalidateQueries(['check-resources', check.check_id]);
        const latestResults = await checksApi.getLatestResults();
        queryClient.setQueryData('latest-check-results', latestResults);
        // Refresh exemption status
        await fetchExemptions();
      }
    }
  );

  const installCloudWatchAgentMutation = useMutation(
    ({ resourceId, region }: { resourceId: string; region?: string }) =>
      checksApi.installCloudWatchAgent(check.check_id, resourceId, region),
    {
      onMutate: ({ resourceId }) => {
        setCloudwatchAgentStatuses(prev => ({
          ...prev,
          [resourceId]: {
            installed: false,
            status: 'installing'
          }
        }));
      },
      onSuccess: async () => {
        await fetchCloudWatchAgentStatuses();
      },
      onError: (_error, variables) => {
        setCloudwatchAgentStatuses(prev => ({
          ...prev,
          [variables.resourceId]: {
            installed: null,
            status: 'error'
          }
        }));
      }
    }
  );

  // Fetch historical savings data for trend chart
  const { data: savingsHistory } = useQuery<{
    check_id: string;
    data_points: Array<{
      timestamp: string;
      savings: number;
      resources_found: number;
    }>;
  }>(
    ['check-savings-history', check.check_id],
    () => checksApi.getCheckSavingsHistory(check.check_id),
    {
      enabled: isOpen, // Fetch history if available; API returns empty when none exists
      staleTime: 30000, // Cache for 30 seconds
      refetchOnWindowFocus: false,
    }
  );

  // Format data for chart
  const chartData = savingsHistory?.data_points.map(point => ({
    date: new Date(point.timestamp).toLocaleDateString('en-US', { month: 'short', day: 'numeric' }),
    timestamp: point.timestamp,
    savings: point.savings,
    resources: point.resources_found,
  })) || [];

  // Check-specific best practices
  const getBestPractices = (checkId: string): string[] => {
    const practices: Record<string, string[]> = {
      'ec2_unused_instances': [
        'Review stopped instances regularly to identify ones that can be safely terminated',
        'Before terminating, verify the instance is not needed for disaster recovery or compliance',
        'Take a final snapshot of EBS volumes before termination if data might be needed later',
        'Use AWS Systems Manager to automate instance lifecycle management',
        'Consider using AWS Instance Scheduler to automatically stop/start instances on a schedule',
        'Tag instances with owner and purpose to make cleanup decisions easier'
      ],
      'ec2_idle_instances': [
        'Monitor CPU and network utilization over time to identify truly idle instances',
        'Consider right-sizing idle instances to smaller instance types before stopping',
        'Review application logs to confirm the instance is not performing background tasks',
        'Use AWS Cost Explorer to analyze costs before and after optimization',
        'Implement automated policies to stop idle instances during off-hours',
        'Set up CloudWatch alarms to alert when instances become idle'
      ],
      'rds_idle_databases': [
        'Verify the database is not used for reporting or batch jobs before stopping',
        'Consider creating a final snapshot before stopping for safety',
        'Review database connections and query logs to confirm inactivity',
        'Use RDS automated backups to ensure data recovery options',
        'Consider right-sizing to a smaller instance class if the database is needed but underutilized',
        'Implement read replicas for production databases before stopping primary instances'
      ],
      'rds_non_graviton_instance_class': [
        'Graviton instances offer up to 20% better price-performance for most workloads',
        'Test your application with Graviton instances in a staging environment first',
        'Ensure your database engine version supports Graviton (most modern versions do)',
        'Plan for a maintenance window during instance class change',
        'Monitor performance metrics after migration to ensure no degradation',
        'Use AWS Database Migration Service for zero-downtime migrations if needed'
      ],
      'ebs_unattached_volumes': [
        'Verify volumes are not needed for future instances before deletion',
        'Create a snapshot of important data before deleting volumes',
        'Review volume tags to identify ownership and purpose',
        'Check if volumes are part of a backup or disaster recovery strategy',
        'Consider moving data to cheaper storage (S3 Glacier) if retention is required',
        'Set up automated policies to delete unattached volumes after a grace period'
      ],
      'snapshot_old_snapshots': [
        'Review snapshot retention policies and compliance requirements',
        'Keep snapshots needed for disaster recovery in a separate lifecycle policy',
        'Use AWS Backup for centralized snapshot management',
        'Tag snapshots with creation date and purpose for easier management',
        'Consider moving old snapshots to S3 Glacier for long-term archival',
        'Implement automated snapshot lifecycle policies to prevent accumulation'
      ],
      'dynamodb_best_fit_on_demand': [
        'On-demand billing is ideal for unpredictable or spiky workloads',
        'Monitor your traffic patterns to determine if spikes are temporary or permanent',
        'Consider on-demand for new tables until traffic patterns are established',
        'Use AWS Cost Explorer to compare provisioned vs on-demand costs',
        'On-demand eliminates the need to manage capacity units and throttling',
        'Switch back to provisioned if traffic becomes predictable and steady'
      ],
      'dynamodb_best_fit_provisioned': [
        'Provisioned billing is more cost-effective for steady, predictable workloads',
        'Use auto-scaling to adjust capacity based on actual usage patterns',
        'Monitor utilization metrics to optimize read and write capacity units',
        'Set up CloudWatch alarms for throttling events to trigger auto-scaling',
        'Consider reserved capacity for long-term predictable workloads',
        'Review capacity settings monthly to ensure optimal cost-performance balance'
      ],
      'dynamodb_gsi_unused': [
        'Review query patterns to confirm the GSI is truly unused',
        'Check if the GSI is used for infrequent but important queries',
        'Monitor GSI metrics over a longer period before deletion',
        'Consider the cost of recreating the GSI if needed later',
        'Document the original purpose of the GSI before deletion',
        'Test application functionality after GSI deletion in a staging environment'
      ],
      'cloudwatch_high_volume_alerts_triggered': [
        'Review alarm configurations to ensure thresholds are appropriate',
        'Investigate root causes of frequent alarms to address underlying issues',
        'Consider consolidating similar alarms to reduce noise',
        'Use alarm actions (SNS, Lambda) to automate responses',
        'Implement alarm state change notifications for critical alerts',
        'Review and tune alarm thresholds based on historical data'
      ],
      'cloudwatch_duplicate_alarms': [
        'Consolidate duplicate alarms to reduce management overhead',
        'Use CloudWatch Composite Alarms to combine related metrics',
        'Review alarm naming conventions to prevent duplicates',
        'Implement alarm tagging for better organization',
        'Use AWS Config to detect and prevent duplicate alarm creation',
        'Document alarm purposes to avoid recreating duplicates'
      ],
      'cloudwatch_log_groups_high_ingest_bytes': [
        'Review log retention policies to reduce storage costs',
        'Consider filtering unnecessary log data at the source',
        'Use log group expiration policies to automatically archive old logs',
        'Move historical logs to S3 Glacier for long-term storage',
        'Implement log sampling for high-volume applications',
        'Review application logging levels to reduce verbosity'
      ],
      'cloudwatch_log_groups_no_recent_ingestion': [
        'Verify log groups are not needed for compliance or auditing',
        'Delete unused log groups to reduce costs',
        'Review application deployments to confirm log groups are obsolete',
        'Check if log groups are part of a disaster recovery plan',
        'Use AWS CloudTrail to track log group usage',
        'Implement automated policies to delete inactive log groups after a grace period'
      ],
      's3_no_expiration_policy': [
        'Implement lifecycle policies to automatically transition objects to cheaper storage',
        'Set expiration rules for temporary or test data',
        'Use S3 Intelligent-Tiering for automatic cost optimization',
        'Review object access patterns to determine appropriate storage classes',
        'Consider S3 Glacier for long-term archival needs',
        'Use versioning with lifecycle policies to manage object versions'
      ],
      's3_no_lifecycle_policy': [
        'Create lifecycle policies to automate object transitions',
        'Move infrequently accessed data to S3 Standard-IA or Glacier',
        'Set up automatic deletion of temporary files and logs',
        'Use S3 Analytics to identify optimization opportunities',
        'Implement policies based on object age and access patterns',
        'Review and update lifecycle policies quarterly'
      ],
      'vpc_flow_logs_enabled': [
        'Enable VPC Flow Logs for security monitoring and troubleshooting',
        'Send flow logs to CloudWatch Logs for centralized analysis',
        'Use flow logs to detect unusual network traffic patterns',
        'Implement log retention policies to manage costs',
        'Review flow logs regularly for security incidents',
        'Consider using VPC Flow Logs Insights for advanced querying'
      ],
      'vpc_no_s3_vpc_endpoint': [
        'Create VPC endpoints for S3 to reduce data transfer costs',
        'VPC endpoints improve security by keeping traffic within AWS network',
        'Use Gateway endpoints for S3 (free) or Interface endpoints for other services',
        'Review data transfer costs before and after endpoint creation',
        'Ensure endpoint policies allow necessary S3 bucket access',
        'Monitor endpoint usage to optimize costs'
      ],
      'vpc_no_dynamodb_vpc_endpoint': [
        'Create VPC endpoints for DynamoDB to reduce data transfer costs',
        'VPC endpoints improve security and reduce latency',
        'Use Gateway endpoints for DynamoDB (free)',
        'Review data transfer costs before and after endpoint creation',
        'Ensure endpoint policies allow necessary DynamoDB table access',
        'Monitor endpoint usage and performance metrics'
      ]
    };

    return practices[checkId] || [
      'Regularly review and optimize resources identified by this check',
      'Consider implementing automated policies to prevent future waste',
      'Review resource tags to ensure proper cost allocation',
      'Monitor efficiency scores over time to track improvement'
    ];
  };

  // Export to CSV
  const handleExportCSV = () => {
    if (!resources || resources.length === 0) return;

    const headers = ['Resource ID', 'Resource Type', 'Resource Name', 'Potential Savings ($/year)', 'Region', 'Account ID', 'Tags'];
    const rows = resources.map(resource => {
    const tagsSource = resource.metadata?.tags || resource.tags;
    const tags = tagsSource ? Object.entries(tagsSource).map(([k, v]) => `${k}=${v}`).join('; ') : '';
      const savings = resource.metadata?.potential_savings_yearly || 
        (resource.metadata?.potential_savings_monthly ? resource.metadata.potential_savings_monthly * 12 : 0);
      return [
        resource.resource_id || '',
        resource.resource_type || '',
        resource.resource_name || '',
        savings.toFixed(2),
        resource.region || '',
        resource.account_id || '',
        tags
      ];
    });

    const csvContent = [
      headers.join(','),
      ...rows.map(row => row.map(cell => `"${cell}"`).join(','))
    ].join('\n');

    const blob = new Blob([csvContent], { type: 'text/csv;charset=utf-8;' });
    const link = document.createElement('a');
    const url = URL.createObjectURL(blob);
    link.setAttribute('href', url);
    link.setAttribute('download', `${check.check_id}_results_${new Date().toISOString().split('T')[0]}.csv`);
    link.style.visibility = 'hidden';
    document.body.appendChild(link);
    link.click();
    document.body.removeChild(link);
  };

  const resourceCount = storedResources?.resources_found || checkState?.resources_found || resources.length;
  const totalSavings = checkState?.potential_savings_yearly ||
    resourcesForSavings.reduce((sum, r) => {
      return sum + (r.metadata?.potential_savings_yearly ||
        (r.metadata?.potential_savings_monthly ? r.metadata.potential_savings_monthly * 12 : 0));
    }, 0);

  return (
    <>
    <Modal
      isOpen={isOpen}
      onClose={onClose}
      title={check.name}
      size="xl"
      closeOnOverlayClick={false}
    >
      <div className="space-y-6">
        {/* Tabs */}
        <div className="border-b border-gray-200 dark:border-gray-700">
          <nav className="flex space-x-4">
            {['overview', 'resources', 'metrics', 'trends', 'docs'].map((tab) => (
              <button
                key={tab}
                onClick={() => setSelectedTab(tab as any)}
                className={`pb-3 px-1 border-b-2 font-medium text-sm transition-colors ${
                  selectedTab === tab
                    ? 'border-primary-500 text-primary-600 dark:text-primary-400'
                    : 'border-transparent text-gray-500 hover:text-gray-700 hover:border-gray-300 dark:text-gray-400 dark:hover:text-gray-300'
                }`}
              >
                {tab.charAt(0).toUpperCase() + tab.slice(1)}
              </button>
            ))}
          </nav>
        </div>

        {/* Loading State */}
        {isLoading && (
          <div className="flex items-center justify-center py-12">
            <Loader2 className="animate-spin text-primary-600" size={32} />
            <span className="ml-3 text-gray-600 dark:text-gray-400">Loading check results...</span>
          </div>
        )}

        {/* Content */}
        {!isLoading && (
          <>
            {/* Overview Tab */}
            {selectedTab === 'overview' && (
              <div className="space-y-6">
                {/* Summary Cards */}
                <div className="grid grid-cols-1 md:grid-cols-3 gap-4">
                  <Card>
                    <div className="p-4">
                      <div className="flex items-center justify-between mb-2">
                        <span className="text-sm text-gray-600 dark:text-gray-400">Resource Count</span>
                        <FileText className="text-primary-600 dark:text-primary-400" size={20} />
                      </div>
                      <div className="text-2xl font-bold text-gray-900 dark:text-white">
                        {resourceCount}
                      </div>
                    </div>
                  </Card>

                  <Card>
                    <div className="p-4">
                      <div className="flex items-center justify-between mb-2">
                        <span className="text-sm text-gray-600 dark:text-gray-400">Total Potential Savings</span>
                        <DollarSign className="text-success-600 dark:text-success-400" size={20} />
                      </div>
                      <div className="text-2xl font-bold text-success-600 dark:text-success-400">
                        {formatUsd(totalSavings)}/yr
                      </div>
                    </div>
                  </Card>

                  <Card>
                    <div className="p-4">
                      <div className="flex items-center justify-between mb-2">
                        <span className="text-sm text-gray-600 dark:text-gray-400">Efficiency Score</span>
                        <TrendingUp className="text-primary-600 dark:text-primary-400" size={20} />
                      </div>
                      <div className="text-2xl font-bold text-gray-900 dark:text-white">
                        {efficiencyScore}/100
                      </div>
                    </div>
                  </Card>
                </div>

                {/* Check Status */}
                <Card>
                  <div className="p-4">
                    <div className="flex items-center space-x-3">
                      {checkState?.status === 'completed' ? (
                        <CheckCircle className="text-success-600 dark:text-success-400" size={24} />
                      ) : checkState?.status === 'failed' ? (
                        <XCircle className="text-danger-600 dark:text-danger-400" size={24} />
                      ) : null}
                      <div>
                        <div className="font-semibold text-gray-900 dark:text-white">
                          Check Status: {checkState?.status?.toUpperCase() || 'UNKNOWN'}
                        </div>
                        {checkState?.last_run && (
                          <div className="text-sm text-gray-600 dark:text-gray-400 mt-1">
                            Last run: {new Date(checkState.last_run).toLocaleString()}
                          </div>
                        )}
                      </div>
                    </div>
                  </div>
                </Card>

                {/* Export Button */}
                {resources.length > 0 && (
                  <div className="flex justify-end">
                    <Button
                      onClick={handleExportCSV}
                      variant="secondary"
                      size="sm"
                    >
                      <Download size={16} className="mr-2" />
                      Export to CSV
                    </Button>
                  </div>
                )}
              </div>
            )}

            {/* Resources Tab */}
            {selectedTab === 'resources' && (
              <div className="space-y-4">
                <div className="flex items-center justify-between">
                  <h3 className="text-lg font-semibold text-gray-900 dark:text-white">
                    Resources ({resourceCount})
                  </h3>
                  <div className="flex items-center space-x-2">
                    {resources.length > 0 && (
                      <>
                        <Button
                          onClick={handleExportCSV}
                          variant="secondary"
                          size="sm"
                        >
                          <Download size={16} className="mr-2" />
                          Export CSV
                        </Button>
                        {actionOptions.length > 0 && (
                          <>
                            {(() => {
                              const selectedList = selectedResources.size
                                ? resources.filter((resource) => selectedResources.has(resource.resource_id!))
                                : resources;
                              const bulkOptions = filterActionOptions(actionOptions, selectedList);
                              return (
                                <div className="space-y-2">
                                  <select
                                    value={bulkAction}
                                    onChange={(e) => setBulkAction(e.target.value)}
                                    className="input w-60"
                                  >
                                    <option value="">Select action</option>
                                    {bulkOptions.map((option) => (
                                      <option key={option} value={option}>
                                        {formatActionLabelWithImpact(option)}
                                      </option>
                                    ))}
                                  </select>
                                  {bulkAction && (
                                    <div className="text-xs text-gray-600 dark:text-gray-400">
                                      <span className="font-semibold">{bulkActionImpact.tag}:</span>{' '}
                                      {bulkActionImpact.summary}
                                    </div>
                                  )}
                                </div>
                              );
                            })()}
                            {(() => {
                              const selectedList = selectedResources.size > 0
                                ? Array.from(selectedResources)
                                : resources.map(r => r.resource_id!).filter(Boolean);
                              const anyExecuting = selectedList.some(id => executingResources.has(id));
                              return (
                                <Button
                                  variant="primary"
                                  size="sm"
                                  onClick={() => openActionDialog()}
                                  disabled={!bulkAction || anyExecuting}
                                >
                                  {anyExecuting ? 'Executing...' : 'Execute'}
                                </Button>
                              );
                            })()}
                          </>
                        )}
                      </>
                    )}
                  </div>
                </div>

                {resources.length === 0 ? (
                  <div className="text-center py-12 text-gray-500 dark:text-gray-400">
                    No resources found for this check.
                  </div>
                ) : (
                  <div className="space-y-3 max-h-[500px] overflow-y-auto">
                    {resources.map((resource, index) => {
                      const metadata = resource.metadata || {};
                      const pricing = (resource as any).pricing || metadata.pricing;
                      const resourceTags = metadata.tags || resource.tags;
                      const resourceType = resource.resource_type?.toLowerCase() || '';
                      const isExempted = resourceExemptions[resource.resource_id!]?.exempted || false;
                      const isSnoozed = isSnoozedActive(resource.resource_id);
                      const cloudwatchAgentStatus = resource.resource_id
                        ? cloudwatchAgentStatuses[resource.resource_id]
                        : undefined;
                      const cloudwatchAgentLabel = getCloudWatchAgentLabel(cloudwatchAgentStatus?.status);
                      const canInstallCloudWatchAgent = cloudwatchAgentStatus?.status === 'not_installed';
                      const isInstallingCloudWatchAgent = cloudwatchAgentStatus?.status === 'installing';
                      const selectedAction = resource.resource_id ? (resourceActions[resource.resource_id] || '') : '';
                      
                      return (
                        <Card 
                          key={resource.resource_id || index} 
                          className={`p-4 ${
                            isExempted
                              ? 'opacity-60 border-warning-300 dark:border-warning-700 bg-warning-50/50 dark:bg-warning-900/10' 
                              : isSnoozed
                              ? 'opacity-75 border-warning-200 dark:border-warning-800'
                              : ''
                          }`}
                        >
                          <div className="space-y-3">
                            {/* Resource ID and Type */}
                            <div className="flex items-center justify-between">
                              <div>
                                <div className="font-semibold text-gray-900 dark:text-white text-lg">
                                  <div className="flex items-center space-x-2">
                                    <input
                                      type="checkbox"
                                      checked={selectedResources.has(resource.resource_id!)}
                                      onChange={() => toggleSelectedResource(resource.resource_id!)}
                                      className="w-4 h-4 text-primary-600 border-gray-300 rounded focus:ring-primary-500"
                                    />
                                    <span>{resource.resource_id}</span>
                                  </div>
                                </div>
                                {resource.resource_name && resource.resource_name !== resource.resource_id && (
                                  <div className="text-sm text-gray-600 dark:text-gray-400 mt-1">
                                    {resource.resource_name}
                                  </div>
                                )}
                              </div>
                              <span className="text-xs px-2 py-1 bg-gray-100 dark:bg-gray-700 rounded">
                                {resource.resource_type}
                              </span>
                            </div>

                            {/* Resource Type Specific Details */}
                            <div className="grid grid-cols-2 gap-3 text-sm">
                              {resource.region && (
                                <div>
                                  <span className="text-gray-600 dark:text-gray-400">Region:</span>{' '}
                                  <span className="font-medium text-gray-900 dark:text-white">{resource.region}</span>
                                </div>
                              )}
                              
                              {resource.account_id && (
                                <div>
                                  <span className="text-gray-600 dark:text-gray-400">Account:</span>{' '}
                                  <span className="font-medium text-gray-900 dark:text-white">{resource.account_id}</span>
                                </div>
                              )}

                              {/* EC2 Specific */}
                              {resourceType === 'ec2' && (
                                <>
                                  {metadata.instance_type && (
                                    <div>
                                      <span className="text-gray-600 dark:text-gray-400">Instance Type:</span>{' '}
                                      <span className="font-medium text-gray-900 dark:text-white">{metadata.instance_type}</span>
                                    </div>
                                  )}
                                  {metadata.state && (
                                    <div>
                                      <span className="text-gray-600 dark:text-gray-400">State:</span>{' '}
                                      <span className="font-medium text-gray-900 dark:text-white capitalize">{metadata.state}</span>
                                    </div>
                                  )}
                                  {metadata.vpc_id && (
                                    <div>
                                      <span className="text-gray-600 dark:text-gray-400">VPC:</span>{' '}
                                      <span className="font-medium text-gray-900 dark:text-white">{metadata.vpc_id}</span>
                                    </div>
                                  )}
                                  {metadata.subnet_id && (
                                    <div>
                                      <span className="text-gray-600 dark:text-gray-400">Subnet:</span>{' '}
                                      <span className="font-medium text-gray-900 dark:text-white">{metadata.subnet_id}</span>
                                    </div>
                                  )}
                                  <div className="col-span-2 flex items-center justify-between">
                                    <div>
                                      <span className="text-gray-600 dark:text-gray-400">CloudWatch Agent:</span>{' '}
                                      <span className="font-medium text-gray-900 dark:text-white">{cloudwatchAgentLabel}</span>
                                    </div>
                                    {canInstallCloudWatchAgent && (
                                      <Button
                                        variant="secondary"
                                        size="sm"
                                        onClick={() => {
                                          if (resource.resource_id) {
                                            installCloudWatchAgentMutation.mutate({
                                              resourceId: resource.resource_id,
                                              region: resource.region
                                            });
                                          }
                                        }}
                                        disabled={isInstallingCloudWatchAgent || installCloudWatchAgentMutation.isLoading}
                                      >
                                        Install CloudWatch Agent
                                      </Button>
                                    )}
                                  </div>
                                </>
                              )}

                              {/* RDS Specific */}
                              {resourceType === 'rds' && (
                                <>
                                  {metadata.db_instance_class && (
                                    <div>
                                      <span className="text-gray-600 dark:text-gray-400">Instance Class:</span>{' '}
                                      <span className="font-medium text-gray-900 dark:text-white">{metadata.db_instance_class}</span>
                                    </div>
                                  )}
                                  {metadata.engine && (
                                    <div>
                                      <span className="text-gray-600 dark:text-gray-400">Engine:</span>{' '}
                                      <span className="font-medium text-gray-900 dark:text-white">{metadata.engine}</span>
                                    </div>
                                  )}
                                  {metadata.db_instance_status && (
                                    <div>
                                      <span className="text-gray-600 dark:text-gray-400">Status:</span>{' '}
                                      <span className="font-medium text-gray-900 dark:text-white">{metadata.db_instance_status}</span>
                                    </div>
                                  )}
                                </>
                              )}

                              {/* EBS Specific */}
                              {resourceType.startsWith('ebs') && (
                                <>
                                  {(pricing?.price_per_unit !== undefined) && (
                                    <div>
                                      <span className="text-gray-600 dark:text-gray-400">Price per unit:</span>{' '}
                                      <span className="font-medium text-gray-900 dark:text-white">
                                        ${Number(pricing.price_per_unit).toFixed(4)} {pricing.unit}
                                      </span>
                                    </div>
                                  )}
                                  {metadata.estimated_monthly_cost !== undefined && (
                                    <div>
                                      <span className="text-gray-600 dark:text-gray-400">Estimated Monthly Cost:</span>{' '}
                                      <span className="font-medium text-gray-900 dark:text-white">
                                        ${Number(metadata.estimated_monthly_cost).toFixed(2)}
                                      </span>
                                    </div>
                                  )}
                                  {metadata.size && (
                                    <div>
                                      <span className="text-gray-600 dark:text-gray-400">Size:</span>{' '}
                                      <span className="font-medium text-gray-900 dark:text-white">{metadata.size} GB</span>
                                    </div>
                                  )}
                                  {metadata.volume_type && (
                                    <div>
                                      <span className="text-gray-600 dark:text-gray-400">Volume Type:</span>{' '}
                                      <span className="font-medium text-gray-900 dark:text-white">{metadata.volume_type}</span>
                                    </div>
                                  )}
                                  {metadata.iops !== undefined && (
                                    <div>
                                      <span className="text-gray-600 dark:text-gray-400">Provisioned IOPS:</span>{' '}
                                      <span className="font-medium text-gray-900 dark:text-white">{metadata.iops}</span>
                                    </div>
                                  )}
                                  {metadata.throughput !== undefined && (
                                    <div>
                                      <span className="text-gray-600 dark:text-gray-400">Throughput:</span>{' '}
                                      <span className="font-medium text-gray-900 dark:text-white">
                                        {metadata.throughput} MB/s
                                      </span>
                                    </div>
                                  )}
                                  {metadata.state && (
                                    <div>
                                      <span className="text-gray-600 dark:text-gray-400">State:</span>{' '}
                                      <span className="font-medium text-gray-900 dark:text-white capitalize">{metadata.state}</span>
                                    </div>
                                  )}
                                  {metadata.avg_iops !== undefined && (
                                    <div>
                                      <span className="text-gray-600 dark:text-gray-400">Avg IOPS:</span>{' '}
                                      <span className="font-medium text-gray-900 dark:text-white">{metadata.avg_iops}</span>
                                    </div>
                                  )}
                                  {metadata.avg_throughput_mb !== undefined && (
                                    <div>
                                      <span className="text-gray-600 dark:text-gray-400">Avg Throughput:</span>{' '}
                                      <span className="font-medium text-gray-900 dark:text-white">
                                        {metadata.avg_throughput_mb} MB/s
                                      </span>
                                    </div>
                                  )}
                                  {metadata.provisioned_iops !== undefined && (
                                    <div>
                                      <span className="text-gray-600 dark:text-gray-400">Provisioned IOPS:</span>{' '}
                                      <span className="font-medium text-gray-900 dark:text-white">
                                        {metadata.provisioned_iops}
                                      </span>
                                    </div>
                                  )}
                                  {metadata.iops_utilization_pct !== undefined && (
                                    <div>
                                      <span className="text-gray-600 dark:text-gray-400">IOPS Utilization:</span>{' '}
                                      <span className="font-medium text-gray-900 dark:text-white">
                                        {metadata.iops_utilization_pct}%
                                      </span>
                                    </div>
                                  )}
                                  {metadata.iops_utilization_threshold_pct !== undefined && (
                                    <div>
                                      <span className="text-gray-600 dark:text-gray-400">IOPS Threshold:</span>{' '}
                                      <span className="font-medium text-gray-900 dark:text-white">
                                        {metadata.iops_utilization_threshold_pct}%
                                      </span>
                                    </div>
                                  )}
                                </>
                              )}

                              {/* Snapshot Specific */}
                              {resourceType === 'snapshot' && (
                                <>
                                  {metadata.size && (
                                    <div>
                                      <span className="text-gray-600 dark:text-gray-400">Size:</span>{' '}
                                      <span className="font-medium text-gray-900 dark:text-white">{metadata.size} GB</span>
                                    </div>
                                  )}
                                  {metadata.start_time && (
                                    <div>
                                      <span className="text-gray-600 dark:text-gray-400">Created:</span>{' '}
                                      <span className="font-medium text-gray-900 dark:text-white">
                                        {new Date(metadata.start_time).toLocaleDateString()}
                                      </span>
                                    </div>
                                  )}
                                </>
                              )}

                              {/* DynamoDB Table Specific */}
                              {(resourceType === 'dynamodb' || resourceType === 'dynamodb_table') && (
                                <>
                                  {(metadata.billing_mode || metadata.BillingMode) && (
                                    <div>
                                      <span className="text-gray-600 dark:text-gray-400">Billing Mode:</span>{' '}
                                      <span className="font-medium text-gray-900 dark:text-white">
                                        {metadata.billing_mode || metadata.BillingMode}
                                      </span>
                                    </div>
                                  )}
                                  {metadata.read_capacity_units && (
                                    <div>
                                      <span className="text-gray-600 dark:text-gray-400">Read Capacity:</span>{' '}
                                      <span className="font-medium text-gray-900 dark:text-white">{metadata.read_capacity_units}</span>
                                    </div>
                                  )}
                                  {metadata.write_capacity_units && (
                                    <div>
                                      <span className="text-gray-600 dark:text-gray-400">Write Capacity:</span>{' '}
                                      <span className="font-medium text-gray-900 dark:text-white">{metadata.write_capacity_units}</span>
                                    </div>
                                  )}
                                  {metadata.ProvisionedThroughput?.ReadCapacityUnits && !metadata.read_capacity_units && (
                                    <div>
                                      <span className="text-gray-600 dark:text-gray-400">Read Capacity:</span>{' '}
                                      <span className="font-medium text-gray-900 dark:text-white">
                                        {metadata.ProvisionedThroughput.ReadCapacityUnits}
                                      </span>
                                    </div>
                                  )}
                                  {metadata.ProvisionedThroughput?.WriteCapacityUnits && !metadata.write_capacity_units && (
                                    <div>
                                      <span className="text-gray-600 dark:text-gray-400">Write Capacity:</span>{' '}
                                      <span className="font-medium text-gray-900 dark:text-white">
                                        {metadata.ProvisionedThroughput.WriteCapacityUnits}
                                      </span>
                                    </div>
                                  )}
                                  {metadata.avg_consumed_rcu !== undefined && (
                                    <div>
                                      <span className="text-gray-600 dark:text-gray-400">Avg Consumed RCU:</span>{' '}
                                      <span className="font-medium text-gray-900 dark:text-white">{metadata.avg_consumed_rcu}</span>
                                    </div>
                                  )}
                                  {metadata.avg_provisioned_rcu !== undefined && (
                                    <div>
                                      <span className="text-gray-600 dark:text-gray-400">Avg Provisioned RCU:</span>{' '}
                                      <span className="font-medium text-gray-900 dark:text-white">
                                        {metadata.avg_provisioned_rcu}
                                      </span>
                                    </div>
                                  )}
                                  {metadata.rcu_utilization_pct !== undefined && (
                                    <div>
                                      <span className="text-gray-600 dark:text-gray-400">RCU Utilization:</span>{' '}
                                      <span className="font-medium text-gray-900 dark:text-white">
                                        {metadata.rcu_utilization_pct}%
                                      </span>
                                    </div>
                                  )}
                                  {metadata.rcu_utilization_threshold !== undefined && (
                                    <div>
                                      <span className="text-gray-600 dark:text-gray-400">RCU Threshold:</span>{' '}
                                      <span className="font-medium text-gray-900 dark:text-white">
                                        {metadata.rcu_utilization_threshold}%
                                      </span>
                                    </div>
                                  )}
                                  {metadata.avg_item_count !== undefined && (
                                    <div>
                                      <span className="text-gray-600 dark:text-gray-400">Avg Item Count:</span>{' '}
                                      <span className="font-medium text-gray-900 dark:text-white">{metadata.avg_item_count}</span>
                                    </div>
                                  )}
                                  {metadata.avg_consumed_rcu !== undefined && (
                                    <div>
                                      <span className="text-gray-600 dark:text-gray-400">Avg Consumed RCU:</span>{' '}
                                      <span className="font-medium text-gray-900 dark:text-white">{metadata.avg_consumed_rcu}</span>
                                    </div>
                                  )}
                                  {metadata.avg_consumed_wcu !== undefined && (
                                    <div>
                                      <span className="text-gray-600 dark:text-gray-400">Avg Consumed WCU:</span>{' '}
                                      <span className="font-medium text-gray-900 dark:text-white">{metadata.avg_consumed_wcu}</span>
                                    </div>
                                  )}
                                  {metadata.p95_consumed_rcu !== undefined && (
                                    <div>
                                      <span className="text-gray-600 dark:text-gray-400">P95 Consumed RCU:</span>{' '}
                                      <span className="font-medium text-gray-900 dark:text-white">{metadata.p95_consumed_rcu}</span>
                                    </div>
                                  )}
                                  {metadata.p95_consumed_wcu !== undefined && (
                                    <div>
                                      <span className="text-gray-600 dark:text-gray-400">P95 Consumed WCU:</span>{' '}
                                      <span className="font-medium text-gray-900 dark:text-white">{metadata.p95_consumed_wcu}</span>
                                    </div>
                                  )}
                                  {metadata.spike_ratio_rcu_p95_over_avg !== undefined && (
                                    <div>
                                      <span className="text-gray-600 dark:text-gray-400">RCU Spike Ratio:</span>{' '}
                                      <span className="font-medium text-gray-900 dark:text-white">
                                        {metadata.spike_ratio_rcu_p95_over_avg}
                                      </span>
                                    </div>
                                  )}
                                  {metadata.spike_ratio_wcu_p95_over_avg !== undefined && (
                                    <div>
                                      <span className="text-gray-600 dark:text-gray-400">WCU Spike Ratio:</span>{' '}
                                      <span className="font-medium text-gray-900 dark:text-white">
                                        {metadata.spike_ratio_wcu_p95_over_avg}
                                      </span>
                                    </div>
                                  )}
                                  {metadata.avg_provisioned_rcu !== undefined && (
                                    <div>
                                      <span className="text-gray-600 dark:text-gray-400">Avg Provisioned RCU:</span>{' '}
                                      <span className="font-medium text-gray-900 dark:text-white">
                                        {metadata.avg_provisioned_rcu}
                                      </span>
                                    </div>
                                  )}
                                  {metadata.avg_provisioned_wcu !== undefined && (
                                    <div>
                                      <span className="text-gray-600 dark:text-gray-400">Avg Provisioned WCU:</span>{' '}
                                      <span className="font-medium text-gray-900 dark:text-white">
                                        {metadata.avg_provisioned_wcu}
                                      </span>
                                    </div>
                                  )}
                                  {metadata.avg_rcu_utilization_pct !== undefined && (
                                    <div>
                                      <span className="text-gray-600 dark:text-gray-400">Avg RCU Utilization:</span>{' '}
                                      <span className="font-medium text-gray-900 dark:text-white">
                                        {metadata.avg_rcu_utilization_pct}%
                                      </span>
                                    </div>
                                  )}
                                  {metadata.avg_wcu_utilization_pct !== undefined && (
                                    <div>
                                      <span className="text-gray-600 dark:text-gray-400">Avg WCU Utilization:</span>{' '}
                                      <span className="font-medium text-gray-900 dark:text-white">
                                        {metadata.avg_wcu_utilization_pct}%
                                      </span>
                                    </div>
                                  )}
                                  {metadata.avg_read_throttle_events !== undefined && (
                                    <div>
                                      <span className="text-gray-600 dark:text-gray-400">Avg Read Throttles:</span>{' '}
                                      <span className="font-medium text-gray-900 dark:text-white">
                                        {metadata.avg_read_throttle_events}
                                      </span>
                                    </div>
                                  )}
                                  {metadata.avg_write_throttle_events !== undefined && (
                                    <div>
                                      <span className="text-gray-600 dark:text-gray-400">Avg Write Throttles:</span>{' '}
                                      <span className="font-medium text-gray-900 dark:text-white">
                                        {metadata.avg_write_throttle_events}
                                      </span>
                                    </div>
                                  )}
                                  {metadata.avg_consumed_wcu !== undefined && (
                                    <div>
                                      <span className="text-gray-600 dark:text-gray-400">Avg Consumed WCU:</span>{' '}
                                      <span className="font-medium text-gray-900 dark:text-white">{metadata.avg_consumed_wcu}</span>
                                    </div>
                                  )}
                                  {metadata.avg_provisioned_wcu !== undefined && (
                                    <div>
                                      <span className="text-gray-600 dark:text-gray-400">Avg Provisioned WCU:</span>{' '}
                                      <span className="font-medium text-gray-900 dark:text-white">
                                        {metadata.avg_provisioned_wcu}
                                      </span>
                                    </div>
                                  )}
                                  {metadata.wcu_utilization_pct !== undefined && (
                                    <div>
                                      <span className="text-gray-600 dark:text-gray-400">WCU Utilization:</span>{' '}
                                      <span className="font-medium text-gray-900 dark:text-white">
                                        {metadata.wcu_utilization_pct}%
                                      </span>
                                    </div>
                                  )}
                                  {metadata.wcu_utilization_threshold !== undefined && (
                                    <div>
                                      <span className="text-gray-600 dark:text-gray-400">WCU Threshold:</span>{' '}
                                      <span className="font-medium text-gray-900 dark:text-white">
                                        {metadata.wcu_utilization_threshold}%
                                      </span>
                                    </div>
                                  )}
                                  {metadata.item_count_threshold !== undefined && (
                                    <div>
                                      <span className="text-gray-600 dark:text-gray-400">Item Count Threshold:</span>{' '}
                                      <span className="font-medium text-gray-900 dark:text-white">
                                        {metadata.item_count_threshold}
                                      </span>
                                    </div>
                                  )}
                                  {metadata.lookback_days !== undefined && (
                                    <div>
                                      <span className="text-gray-600 dark:text-gray-400">Lookback Days:</span>{' '}
                                      <span className="font-medium text-gray-900 dark:text-white">
                                        {metadata.lookback_days}
                                      </span>
                                    </div>
                                  )}
                                  {metadata.current_monthly_cost !== undefined && (
                                    <div>
                                      <span className="text-gray-600 dark:text-gray-400">Current Monthly Cost:</span>{' '}
                                      <span className="font-medium text-gray-900 dark:text-white">
                                        ${Number(metadata.current_monthly_cost).toFixed(2)}
                                      </span>
                                    </div>
                                  )}
                                  {metadata.recommended_rcu !== undefined && (
                                    <div>
                                      <span className="text-gray-600 dark:text-gray-400">Recommended RCU:</span>{' '}
                                      <span className="font-medium text-gray-900 dark:text-white">
                                        {metadata.recommended_rcu}
                                      </span>
                                    </div>
                                  )}
                                  {metadata.recommended_wcu !== undefined && (
                                    <div>
                                      <span className="text-gray-600 dark:text-gray-400">Recommended WCU:</span>{' '}
                                      <span className="font-medium text-gray-900 dark:text-white">
                                        {metadata.recommended_wcu}
                                      </span>
                                    </div>
                                  )}
                                  {metadata.recommended_monthly_cost !== undefined && (
                                    <div>
                                      <span className="text-gray-600 dark:text-gray-400">Recommended Monthly Cost:</span>{' '}
                                      <span className="font-medium text-gray-900 dark:text-white">
                                        ${Number(metadata.recommended_monthly_cost).toFixed(2)}
                                      </span>
                                    </div>
                                  )}
                                  {metadata.potential_savings_monthly !== undefined && (
                                    <div>
                                      <span className="text-gray-600 dark:text-gray-400">Potential Savings (Monthly):</span>{' '}
                                      <span className="font-medium text-success-600 dark:text-success-400">
                                        ${Number(metadata.potential_savings_monthly).toFixed(2)}
                                      </span>
                                    </div>
                                  )}
                                  {metadata.potential_savings_yearly !== undefined && (
                                    <div>
                                      <span className="text-gray-600 dark:text-gray-400">Potential Savings (Yearly):</span>{' '}
                                      <span className="font-medium text-success-600 dark:text-success-400">
                                        ${Number(metadata.potential_savings_yearly).toFixed(2)}
                                      </span>
                                    </div>
                                  )}
                                  {metadata.note && (
                                    <div className="col-span-2">
                                      <span className="text-gray-600 dark:text-gray-400">Note:</span>{' '}
                                      <span className="font-medium text-warning-600 dark:text-warning-400">
                                        {metadata.note}
                                      </span>
                                    </div>
                                  )}
                                </>
                              )}

                              {/* DynamoDB GSI Specific */}
                              {resourceType === 'dynamodb_gsi' && (
                                <>
                                  {(metadata.table_name || metadata.TableName) && (
                                    <div>
                                      <span className="text-gray-600 dark:text-gray-400">Table:</span>{' '}
                                      <span className="font-medium text-gray-900 dark:text-white">
                                        {metadata.table_name || metadata.TableName}
                                      </span>
                                    </div>
                                  )}
                                  {(metadata.index_name || metadata.IndexName) && (
                                    <div>
                                      <span className="text-gray-600 dark:text-gray-400">Index:</span>{' '}
                                      <span className="font-medium text-gray-900 dark:text-white">
                                        {metadata.index_name || metadata.IndexName}
                                      </span>
                                    </div>
                                  )}
                                  {metadata.IndexStatus && (
                                    <div>
                                      <span className="text-gray-600 dark:text-gray-400">Status:</span>{' '}
                                      <span className="font-medium text-gray-900 dark:text-white">{metadata.IndexStatus}</span>
                                    </div>
                                  )}
                                  {metadata.read_capacity_units && (
                                    <div>
                                      <span className="text-gray-600 dark:text-gray-400">Read Capacity:</span>{' '}
                                      <span className="font-medium text-gray-900 dark:text-white">{metadata.read_capacity_units}</span>
                                    </div>
                                  )}
                                  {metadata.write_capacity_units && (
                                    <div>
                                      <span className="text-gray-600 dark:text-gray-400">Write Capacity:</span>{' '}
                                      <span className="font-medium text-gray-900 dark:text-white">{metadata.write_capacity_units}</span>
                                    </div>
                                  )}
                                  {metadata.ProvisionedThroughput?.ReadCapacityUnits && !metadata.read_capacity_units && (
                                    <div>
                                      <span className="text-gray-600 dark:text-gray-400">Read Capacity:</span>{' '}
                                      <span className="font-medium text-gray-900 dark:text-white">
                                        {metadata.ProvisionedThroughput.ReadCapacityUnits}
                                      </span>
                                    </div>
                                  )}
                                  {metadata.ProvisionedThroughput?.WriteCapacityUnits && !metadata.write_capacity_units && (
                                    <div>
                                      <span className="text-gray-600 dark:text-gray-400">Write Capacity:</span>{' '}
                                      <span className="font-medium text-gray-900 dark:text-white">
                                        {metadata.ProvisionedThroughput.WriteCapacityUnits}
                                      </span>
                                    </div>
                                  )}
                                  {metadata.billing_mode && (
                                    <div>
                                      <span className="text-gray-600 dark:text-gray-400">Billing Mode:</span>{' '}
                                      <span className="font-medium text-gray-900 dark:text-white">
                                        {metadata.billing_mode}
                                      </span>
                                    </div>
                                  )}
                                  {metadata.current_monthly_cost !== undefined && (
                                    <div>
                                      <span className="text-gray-600 dark:text-gray-400">Estimated Monthly Cost:</span>{' '}
                                      <span className="font-medium text-gray-900 dark:text-white">
                                        ${Number(metadata.current_monthly_cost).toFixed(2)}
                                      </span>
                                    </div>
                                  )}
                                  {metadata.note && (
                                    <div className="col-span-2">
                                      <span className="text-gray-600 dark:text-gray-400">Note:</span>{' '}
                                      <span className="font-medium text-warning-600 dark:text-warning-400">
                                        {metadata.note}
                                      </span>
                                    </div>
                                  )}
                                </>
                              )}

                              {/* ElastiCache Specific */}
                              {resourceType.startsWith('elasticache') && (
                                <>
                                  {(metadata.Engine || metadata.engine) && (
                                    <div>
                                      <span className="text-gray-600 dark:text-gray-400">Engine:</span>{' '}
                                      <span className="font-medium text-gray-900 dark:text-white">
                                        {metadata.Engine || metadata.engine}
                                      </span>
                                    </div>
                                  )}
                                  {(metadata.EngineVersion || metadata.engine_version) && (
                                    <div>
                                      <span className="text-gray-600 dark:text-gray-400">Engine Version:</span>{' '}
                                      <span className="font-medium text-gray-900 dark:text-white">
                                        {metadata.EngineVersion || metadata.engine_version}
                                      </span>
                                    </div>
                                  )}
                                  {metadata.CacheNodeType && (
                                    <div>
                                      <span className="text-gray-600 dark:text-gray-400">Node Type:</span>{' '}
                                      <span className="font-medium text-gray-900 dark:text-white">{metadata.CacheNodeType}</span>
                                    </div>
                                  )}
                                  {(metadata.Status || metadata.CacheClusterStatus) && (
                                    <div>
                                      <span className="text-gray-600 dark:text-gray-400">Status:</span>{' '}
                                      <span className="font-medium text-gray-900 dark:text-white">
                                        {metadata.Status || metadata.CacheClusterStatus}
                                      </span>
                                    </div>
                                  )}
                                  {metadata.NumCacheNodes !== undefined && (
                                    <div>
                                      <span className="text-gray-600 dark:text-gray-400">Node Count:</span>{' '}
                                      <span className="font-medium text-gray-900 dark:text-white">{metadata.NumCacheNodes}</span>
                                    </div>
                                  )}
                                  {metadata.NumNodeGroups !== undefined && (
                                    <div>
                                      <span className="text-gray-600 dark:text-gray-400">Node Groups:</span>{' '}
                                      <span className="font-medium text-gray-900 dark:text-white">{metadata.NumNodeGroups}</span>
                                    </div>
                                  )}
                                  {metadata.ReplicasPerNodeGroup !== undefined && (
                                    <div>
                                      <span className="text-gray-600 dark:text-gray-400">Replicas/Group:</span>{' '}
                                      <span className="font-medium text-gray-900 dark:text-white">
                                        {metadata.ReplicasPerNodeGroup}
                                      </span>
                                    </div>
                                  )}
                                  {Array.isArray(metadata.MemberClusters) && metadata.MemberClusters.length > 0 && (
                                    <div className="col-span-2">
                                      <span className="text-gray-600 dark:text-gray-400">Member Clusters:</span>{' '}
                                      <span className="font-medium text-gray-900 dark:text-white">
                                        {metadata.MemberClusters.join(', ')}
                                      </span>
                                    </div>
                                  )}
                                  {metadata.avg_item_count !== undefined && (
                                    <div>
                                      <span className="text-gray-600 dark:text-gray-400">Avg Item Count:</span>{' '}
                                      <span className="font-medium text-gray-900 dark:text-white">{metadata.avg_item_count}</span>
                                    </div>
                                  )}
                                  {metadata.max_item_count !== undefined && (
                                    <div>
                                      <span className="text-gray-600 dark:text-gray-400">Max Item Count:</span>{' '}
                                      <span className="font-medium text-gray-900 dark:text-white">{metadata.max_item_count}</span>
                                    </div>
                                  )}
                                  {metadata.low_item_threshold !== undefined && (
                                    <div>
                                      <span className="text-gray-600 dark:text-gray-400">Item Threshold:</span>{' '}
                                      <span className="font-medium text-gray-900 dark:text-white">{metadata.low_item_threshold}</span>
                                    </div>
                                  )}
                                  {metadata.estimated_monthly_cost !== undefined && (
                                    <div>
                                      <span className="text-gray-600 dark:text-gray-400">Estimated Monthly Cost:</span>{' '}
                                      <span className="font-medium text-gray-900 dark:text-white">
                                        ${Number(metadata.estimated_monthly_cost).toFixed(2)}
                                      </span>
                                    </div>
                                  )}
                                  {metadata.potential_savings_monthly !== undefined && (
                                    <div>
                                      <span className="text-gray-600 dark:text-gray-400">Potential Savings (Monthly):</span>{' '}
                                      <span className="font-medium text-success-600 dark:text-success-400">
                                        ${Number(metadata.potential_savings_monthly).toFixed(2)}
                                      </span>
                                    </div>
                                  )}
                                  {metadata.potential_savings_yearly !== undefined && (
                                    <div>
                                      <span className="text-gray-600 dark:text-gray-400">Potential Savings (Yearly):</span>{' '}
                                      <span className="font-medium text-success-600 dark:text-success-400">
                                        ${Number(metadata.potential_savings_yearly).toFixed(2)}
                                      </span>
                                    </div>
                                  )}
                                  {metadata.savings_note && (
                                    <div className="col-span-2">
                                      <div className="rounded-md border border-success-200 bg-success-50 px-3 py-2 text-sm text-success-800 dark:border-success-700/60 dark:bg-success-950/30 dark:text-success-100">
                                        {metadata.savings_note}
                                      </div>
                                    </div>
                                  )}
                                </>
                              )}

                              {/* EFS Specific */}
                              {resourceType === 'efs' && (
                                <>
                                  {(pricing?.price_per_unit !== undefined) && (
                                    <div>
                                      <span className="text-gray-600 dark:text-gray-400">Price per unit:</span>{' '}
                                      <span className="font-medium text-gray-900 dark:text-white">
                                        ${Number(pricing.price_per_unit).toFixed(4)} {pricing.unit}
                                      </span>
                                    </div>
                                  )}
                                  {metadata.estimated_monthly_cost !== undefined && (
                                    <div>
                                      <span className="text-gray-600 dark:text-gray-400">Estimated Monthly Cost:</span>{' '}
                                      <span className="font-medium text-gray-900 dark:text-white">
                                        ${Number(metadata.estimated_monthly_cost).toFixed(2)}
                                      </span>
                                    </div>
                                  )}
                                  {metadata.size_gb !== undefined && (
                                    <div>
                                      <span className="text-gray-600 dark:text-gray-400">Size:</span>{' '}
                                      <span className="font-medium text-gray-900 dark:text-white">{metadata.size_gb} GB</span>
                                    </div>
                                  )}
                                  {metadata.PerformanceMode && (
                                    <div>
                                      <span className="text-gray-600 dark:text-gray-400">Performance Mode:</span>{' '}
                                      <span className="font-medium text-gray-900 dark:text-white">{metadata.PerformanceMode}</span>
                                    </div>
                                  )}
                                  {metadata.ThroughputMode && (
                                    <div>
                                      <span className="text-gray-600 dark:text-gray-400">Throughput Mode:</span>{' '}
                                      <span className="font-medium text-gray-900 dark:text-white">{metadata.ThroughputMode}</span>
                                    </div>
                                  )}
                                  {metadata.LifeCycleState && (
                                    <div>
                                      <span className="text-gray-600 dark:text-gray-400">Lifecycle State:</span>{' '}
                                      <span className="font-medium text-gray-900 dark:text-white">{metadata.LifeCycleState}</span>
                                    </div>
                                  )}
                                  {metadata.potential_savings_monthly !== undefined && metadata.potential_savings_monthly > 0 && (
                                    <div>
                                      <span className="text-gray-600 dark:text-gray-400">Potential Savings (Monthly):</span>{' '}
                                      <span className="font-medium text-success-600 dark:text-success-400">
                                        ${Number(metadata.potential_savings_monthly).toFixed(2)}
                                      </span>
                                    </div>
                                  )}
                                  {metadata.potential_savings_yearly !== undefined && metadata.potential_savings_yearly > 0 && (
                                    <div>
                                      <span className="text-gray-600 dark:text-gray-400">Potential Savings (Yearly):</span>{' '}
                                      <span className="font-medium text-success-600 dark:text-success-400">
                                        ${Number(metadata.potential_savings_yearly).toFixed(2)}
                                      </span>
                                    </div>
                                  )}
                                  {metadata.pricing_note && (
                                    <div className="col-span-2">
                                      <div className="rounded-md border border-primary-200 bg-primary-50 px-3 py-2 text-sm text-primary-800 dark:border-primary-700/60 dark:bg-primary-950/30 dark:text-primary-100">
                                        <div className="font-medium mb-1">ðŸ’° Pricing Note</div>
                                        {metadata.pricing_note}
                                      </div>
                                    </div>
                                  )}
                                </>
                              )}

                              {/* Lambda Specific */}
                              {resourceType === 'lambda' && (
                                <>
                                  {metadata.MemorySize !== undefined && (
                                    <div>
                                      <span className="text-gray-600 dark:text-gray-400">Allocated Memory:</span>{' '}
                                      <span className="font-medium text-gray-900 dark:text-white">
                                        {metadata.MemorySize} MB
                                      </span>
                                    </div>
                                  )}
                                  {metadata.avg_memory_used_mb !== undefined && (
                                    <div>
                                      <span className="text-gray-600 dark:text-gray-400">Avg Memory Used:</span>{' '}
                                      <span className="font-medium text-gray-900 dark:text-white">
                                        {Number(metadata.avg_memory_used_mb).toFixed(2)} MB
                                      </span>
                                    </div>
                                  )}
                                  {metadata.max_memory_used_mb !== undefined && (
                                    <div>
                                      <span className="text-gray-600 dark:text-gray-400">Max Memory Used:</span>{' '}
                                      <span className="font-medium text-gray-900 dark:text-white">
                                        {Number(metadata.max_memory_used_mb).toFixed(2)} MB
                                      </span>
                                    </div>
                                  )}
                                  {metadata.memory_utilization_pct !== undefined && (
                                    <div>
                                      <span className="text-gray-600 dark:text-gray-400">Memory Utilization:</span>{' '}
                                      <span className="font-medium text-gray-900 dark:text-white">
                                        {Number(metadata.memory_utilization_pct).toFixed(2)}%
                                      </span>
                                    </div>
                                  )}
                                  {metadata.current_monthly_cost !== undefined && (
                                    <div>
                                      <span className="text-gray-600 dark:text-gray-400">Current Monthly Cost:</span>{' '}
                                      <span className="font-medium text-gray-900 dark:text-white">
                                        {formatUsd(metadata.current_monthly_cost)}
                                      </span>
                                    </div>
                                  )}
                                  {metadata.current_log_cost_monthly !== undefined && (
                                    <div>
                                      <span className="text-gray-600 dark:text-gray-400">Current Log Cost (Monthly):</span>{' '}
                                      <span className="font-medium text-gray-900 dark:text-white">
                                        {formatUsd(metadata.current_log_cost_monthly)}
                                      </span>
                                    </div>
                                  )}
                                  {metadata.optimized_monthly_cost !== undefined && (
                                    <div>
                                      <span className="text-gray-600 dark:text-gray-400">Optimized Monthly Cost:</span>{' '}
                                      <span className="font-medium text-gray-900 dark:text-white">
                                        {formatUsd(metadata.optimized_monthly_cost)}
                                      </span>
                                    </div>
                                  )}
                                  {metadata.optimized_log_cost_monthly !== undefined && (
                                    <div>
                                      <span className="text-gray-600 dark:text-gray-400">Optimized Log Cost (Monthly):</span>{' '}
                                      <span className="font-medium text-gray-900 dark:text-white">
                                        {formatUsd(metadata.optimized_log_cost_monthly)}
                                      </span>
                                    </div>
                                  )}
                                  {metadata.potential_savings_monthly !== undefined && (
                                    <div>
                                      <span className="text-gray-600 dark:text-gray-400">Potential Savings (Monthly):</span>{' '}
                                      <span className="font-medium text-success-600 dark:text-success-400">
                                        {formatUsd(metadata.potential_savings_monthly)}
                                      </span>
                                    </div>
                                  )}
                                  {metadata.potential_savings_yearly !== undefined && (
                                    <div>
                                      <span className="text-gray-600 dark:text-gray-400">Potential Savings (Yearly):</span>{' '}
                                      <span className="font-medium text-success-600 dark:text-success-400">
                                        {formatUsd(metadata.potential_savings_yearly)}
                                      </span>
                                    </div>
                                  )}
                                </>
                              )}

                              {/* S3 Specific */}
                              {resourceType === 's3' && (
                                <>
                                  {metadata.bucket_size_gb !== undefined && metadata.bucket_size_gb > 0 && (
                                    <div>
                                      <span className="text-gray-600 dark:text-gray-400">Bucket Size:</span>{' '}
                                      <span className="font-medium text-gray-900 dark:text-white">
                                        {Number(metadata.bucket_size_gb).toFixed(2)} GB
                                      </span>
                                    </div>
                                  )}
                                  {metadata.object_count !== undefined && metadata.object_count > 0 && (
                                    <div>
                                      <span className="text-gray-600 dark:text-gray-400">Object Count:</span>{' '}
                                      <span className="font-medium text-gray-900 dark:text-white">
                                        {Number(metadata.object_count).toLocaleString()}
                                      </span>
                                    </div>
                                  )}
                                  {metadata.estimated_monthly_cost !== undefined && metadata.estimated_monthly_cost > 0 && (
                                    <div>
                                      <span className="text-gray-600 dark:text-gray-400">Estimated Monthly Cost:</span>{' '}
                                      <span className="font-medium text-gray-900 dark:text-white">
                                        ${Number(metadata.estimated_monthly_cost).toFixed(2)}
                                      </span>
                                    </div>
                                  )}
                                  {metadata.potential_savings_monthly !== undefined && metadata.potential_savings_monthly > 0 && (
                                    <div>
                                      <span className="text-gray-600 dark:text-gray-400">Potential Savings (Monthly):</span>{' '}
                                      <span className="font-medium text-success-600 dark:text-success-400">
                                        ${Number(metadata.potential_savings_monthly).toFixed(2)}
                                      </span>
                                    </div>
                                  )}
                                  {metadata.potential_savings_yearly !== undefined && metadata.potential_savings_yearly > 0 && (
                                    <div>
                                      <span className="text-gray-600 dark:text-gray-400">Potential Savings (Yearly):</span>{' '}
                                      <span className="font-medium text-success-600 dark:text-success-400">
                                        ${Number(metadata.potential_savings_yearly).toFixed(2)}
                                      </span>
                                    </div>
                                  )}
                                  {metadata.creation_date && (
                                    <div>
                                      <span className="text-gray-600 dark:text-gray-400">Created:</span>{' '}
                                      <span className="font-medium text-gray-900 dark:text-white">
                                        {new Date(metadata.creation_date).toLocaleDateString()}
                                      </span>
                                    </div>
                                  )}
                                  {Array.isArray(metadata.lifecycle_rules) && (
                                    <div>
                                      <span className="text-gray-600 dark:text-gray-400">Lifecycle Rules:</span>{' '}
                                      <span className="font-medium text-gray-900 dark:text-white">
                                        {metadata.lifecycle_rules.length}
                                      </span>
                                    </div>
                                  )}
                                  {Array.isArray(metadata.archival_classes_checked) && (
                                    <div className="col-span-2">
                                      <span className="text-gray-600 dark:text-gray-400">Archival Classes:</span>{' '}
                                      <span className="font-medium text-gray-900 dark:text-white">
                                        {metadata.archival_classes_checked.join(', ')}
                                      </span>
                                    </div>
                                  )}
                                  {metadata.rule_count !== undefined && (
                                    <div>
                                      <span className="text-gray-600 dark:text-gray-400">Lifecycle Rule Count:</span>{' '}
                                      <span className="font-medium text-gray-900 dark:text-white">
                                        {metadata.rule_count}
                                      </span>
                                    </div>
                                  )}
                                  {Array.isArray(metadata.inventory_target_buckets) && (
                                    <div className="col-span-2">
                                      <span className="text-gray-600 dark:text-gray-400">Inventory Targets:</span>{' '}
                                      <span className="font-medium text-gray-900 dark:text-white">
                                        {metadata.inventory_target_buckets.join(', ')}
                                      </span>
                                    </div>
                                  )}
                                  {metadata.log_bucket_reason && (
                                    <div className="col-span-2">
                                      <span className="text-gray-600 dark:text-gray-400">Log Bucket Reason:</span>{' '}
                                      <span className="font-medium text-gray-900 dark:text-white">
                                        {metadata.log_bucket_reason}
                                      </span>
                                    </div>
                                  )}
                                  {metadata.lifecycle_rule_count !== undefined && (
                                    <div>
                                      <span className="text-gray-600 dark:text-gray-400">Lifecycle Rule Count:</span>{' '}
                                      <span className="font-medium text-gray-900 dark:text-white">
                                        {metadata.lifecycle_rule_count}
                                      </span>
                                    </div>
                                  )}
                                  {Array.isArray(metadata.allowed_storage_classes) && (
                                    <div className="col-span-2">
                                      <span className="text-gray-600 dark:text-gray-400">Allowed Storage Classes:</span>{' '}
                                      <span className="font-medium text-gray-900 dark:text-white">
                                        {metadata.allowed_storage_classes.join(', ')}
                                      </span>
                                    </div>
                                  )}
                                  {metadata.versioning_status && (
                                    <div>
                                      <span className="text-gray-600 dark:text-gray-400">Versioning:</span>{' '}
                                      <span className="font-medium text-gray-900 dark:text-white">
                                        {metadata.versioning_status}
                                      </span>
                                    </div>
                                  )}
                                  {metadata.rule_count !== undefined && (
                                    <div>
                                      <span className="text-gray-600 dark:text-gray-400">Lifecycle Rule Count:</span>{' '}
                                      <span className="font-medium text-gray-900 dark:text-white">
                                        {metadata.rule_count}
                                      </span>
                                    </div>
                                  )}
                                  {metadata.replication_rule_count !== undefined && (
                                    <div>
                                      <span className="text-gray-600 dark:text-gray-400">Replication Rules:</span>{' '}
                                      <span className="font-medium text-gray-900 dark:text-white">
                                        {metadata.replication_rule_count}
                                      </span>
                                    </div>
                                  )}
                                  {metadata.replication_enabled_rule_count !== undefined && (
                                    <div>
                                      <span className="text-gray-600 dark:text-gray-400">Enabled Replication Rules:</span>{' '}
                                      <span className="font-medium text-gray-900 dark:text-white">
                                        {metadata.replication_enabled_rule_count}
                                      </span>
                                    </div>
                                  )}
                                  {Array.isArray(metadata.replication_target_buckets) && (
                                    <div className="col-span-2">
                                      <span className="text-gray-600 dark:text-gray-400">Replication Targets:</span>{' '}
                                      <span className="font-medium text-gray-900 dark:text-white">
                                        {metadata.replication_target_buckets.join(', ')}
                                      </span>
                                    </div>
                                  )}
                                  {metadata.cost_note && (
                                    <div className="col-span-2">
                                      <div className="rounded-md border border-primary-200 bg-primary-50 px-3 py-2 text-sm text-primary-800 dark:border-primary-700/60 dark:bg-primary-950/30 dark:text-primary-100">
                                        <div className="font-medium mb-1">ðŸ’° Cost Impact</div>
                                        {metadata.cost_note}
                                      </div>
                                    </div>
                                  )}
                                </>
                              )}
                            </div>
                          
                            {/* Potential Savings */}
                            {(metadata.potential_savings_yearly !== undefined || metadata.potential_savings_monthly !== undefined) && (
                              <div className="p-3 bg-success-50 dark:bg-success-900/20 rounded-lg">
                                <div className="text-sm font-medium text-success-800 dark:text-success-300">
                                  Potential Yearly Savings
                                </div>
                                <div className="text-xl font-bold text-success-600 dark:text-success-400">
                                  {formatUsd(
                                    metadata.potential_savings_yearly !== undefined
                                      ? Number(metadata.potential_savings_yearly)
                                      : (metadata.potential_savings_monthly !== undefined ? Number(metadata.potential_savings_monthly) * 12 : 0)
                                  )}/year
                                </div>
                              </div>
                            )}

                            {/* Tags */}
                            {resourceTags && Object.keys(resourceTags).length > 0 && (
                              <div>
                                <div className="text-sm font-medium text-gray-700 dark:text-gray-300 mb-2">Tags</div>
                                <div className="flex flex-wrap gap-2">
                                  {Object.entries(resourceTags).map(([key, value]) => (
                                    <span
                                      key={key}
                                      className="inline-flex items-center px-2 py-1 rounded text-xs bg-primary-100 dark:bg-primary-900/30 text-primary-800 dark:text-primary-300"
                                    >
                                      <Tag size={12} className="mr-1" />
                                      {key}: {String(value)}
                                    </span>
                                  ))}
                                </div>
                              </div>
                            )}

                            {/* Additional Metadata (Collapsible) */}
                            {metadata && Object.keys(metadata).length > 0 && (
                              <details className="mt-2">
                                <summary className="text-sm text-gray-600 dark:text-gray-400 cursor-pointer hover:text-gray-900 dark:hover:text-gray-200 font-medium">
                                  View All Metadata
                                </summary>
                                <div className="mt-2 p-3 bg-gray-50 dark:bg-gray-800 rounded text-xs font-mono max-h-48 overflow-y-auto">
                                  <pre className="whitespace-pre-wrap">
                                    {JSON.stringify(metadata, null, 2)}
                                  </pre>
                                </div>
                              </details>
                            )}

                            {/* Actions */}
                            <div className="flex flex-col space-y-3 pt-2 border-t border-gray-200 dark:border-gray-700">
                              <div className="flex items-center space-x-2">
                                {actionOptions.length > 0 && (
                                  <>
                                    {(() => {
                                      const perResourceOptions = filterActionOptions(actionOptions, [resource]);
                                      return (
                                        <div className="space-y-2">
                                          <select
                                            value={selectedAction}
                                            onChange={(e) => {
                                              if (resource.resource_id) {
                                                setResourceActions(prev => ({
                                                  ...prev,
                                                  [resource.resource_id!]: e.target.value
                                                }));
                                              }
                                            }}
                                            className="input w-56"
                                          >
                                            <option value="">Select action</option>
                                            {perResourceOptions.map((option) => (
                                              <option key={option} value={option}>
                                                {formatActionLabelWithImpact(option)}
                                              </option>
                                            ))}
                                          </select>
                                          {selectedAction && (
                                            <div className="text-xs text-gray-600 dark:text-gray-400">
                                              <span className="font-semibold">
                                                {getActionImpact(selectedAction).tag}:
                                              </span>{' '}
                                              {getActionImpact(selectedAction).summary}
                                            </div>
                                          )}
                                        </div>
                                      );
                                    })()}
                                    <Button
                                      variant="primary"
                                      size="sm"
                                      onClick={() => openActionDialog([resource.resource_id!], selectedAction)}
                                      disabled={!selectedAction || executingResources.has(resource.resource_id!)}
                                    >
                                      {executingResources.has(resource.resource_id!) ? 'Executing...' : 'Execute'}
                                    </Button>
                                  </>
                                )}
                              </div>
                              <div className="flex items-center space-x-2">
                                {/* Exempt Checkbox */}
                                <label className="flex items-center space-x-2 cursor-pointer">
                                  <input
                                    type="checkbox"
                                    checked={isExempted}
                                    onChange={(e) => {
                                      exemptMutation.mutate({
                                        resourceId: resource.resource_id!,
                                        exempted: e.target.checked
                                      });
                                    }}
                                    className="w-4 h-4 text-primary-600 border-gray-300 rounded focus:ring-primary-500"
                                  />
                                  <span className="text-sm text-gray-700 dark:text-gray-300 flex items-center">
                                    {isExempted ? (
                                      <>
                                        <Shield size={14} className="mr-1 text-warning-600 dark:text-warning-400" />
                                        Exempt
                                      </>
                                    ) : (
                                      <>
                                        <ShieldOff size={14} className="mr-1 text-gray-400" />
                                        Exempt
                                      </>
                                    )}
                                  </span>
                                </label>

                                {/* Snooze Button */}
                                {isSnoozed ? (
                                  <Button
                                    variant="secondary"
                                    size="sm"
                                    onClick={() => {
                                      if (resource.resource_id) {
                                        cancelSnoozeMutation.mutate({ resourceId: resource.resource_id });
                                      }
                                    }}
                                    disabled={isExempted || cancelSnoozeMutation.isLoading}
                                  >
                                    <XCircle size={14} className="mr-2" />
                                    Cancel Snooze
                                  </Button>
                                ) : (
                                  <Button
                                    variant="secondary"
                                    size="sm"
                                    onClick={() => setSnoozeResource({ resourceId: resource.resource_id! })}
                                    disabled={isExempted}
                                  >
                                    <Clock size={14} className="mr-2" />
                                    Snooze
                                  </Button>
                                )}
                              </div>
                            </div>

                            {/* Exemption/Snooze Status Indicators */}
                            {(isExempted || isSnoozed) && (
                              <div className="mt-2 p-2 bg-warning-50 dark:bg-warning-900/20 rounded-lg border border-warning-200 dark:border-warning-800">
                                {isExempted ? (
                                  <div className="flex items-center text-sm text-warning-800 dark:text-warning-300">
                                    <Shield size={14} className="mr-2" />
                                    This resource is permanently exempted from this check
                                  </div>
                                ) : isSnoozed && (
                                  <div className="flex items-center text-sm text-warning-800 dark:text-warning-300">
                                    <Clock size={14} className="mr-2" />
                                    Snoozed until {new Date(resourceExemptions[resource.resource_id!].snoozed_until!).toLocaleDateString()}
                                  </div>
                                )}
                              </div>
                            )}
                          </div>
                        </Card>
                      );
                    })}
                  </div>
                )}
              </div>
            )}

            {/* Metrics Tab */}
            {selectedTab === 'metrics' && (
              <div className="space-y-4">
                <h3 className="text-lg font-semibold text-gray-900 dark:text-white">Metrics</h3>
                <Card>
                  <div className="p-4 space-y-4">
                    <div>
                      <div className="text-sm font-medium text-gray-700 dark:text-gray-300 mb-2">
                        Resource Count
                      </div>
                      <div className="text-2xl font-bold text-gray-900 dark:text-white">
                        {resourceCount}
                      </div>
                    </div>
                    <div>
                      <div className="text-sm font-medium text-gray-700 dark:text-gray-300 mb-2">
                        Total Potential Yearly Savings
                      </div>
                      <div className="text-2xl font-bold text-success-600 dark:text-success-400">
                        ${totalSavings.toFixed(2)}
                      </div>
                    </div>
                    <div>
                      <div className="text-sm font-medium text-gray-700 dark:text-gray-300 mb-2">
                        Efficiency Score
                      </div>
                      <div className="flex items-center space-x-2">
                        <div className="flex-1 bg-gray-200 dark:bg-gray-700 rounded-full h-4">
                          <div
                            className="bg-primary-600 dark:bg-primary-400 h-4 rounded-full transition-all"
                            style={{ width: `${efficiencyScore}%` }}
                          />
                        </div>
                        <span className="text-sm font-semibold text-gray-900 dark:text-white">
                          {efficiencyScore}%
                        </span>
                      </div>
                    </div>
                    {checkState?.last_run && (
                      <div>
                        <div className="text-sm font-medium text-gray-700 dark:text-gray-300 mb-2">
                          Last Execution
                        </div>
                        <div className="text-sm text-gray-600 dark:text-gray-400">
                          {new Date(checkState.last_run).toLocaleString()}
                        </div>
                      </div>
                    )}
                  </div>
                </Card>
              </div>
            )}

            {/* Trends Tab */}
            {selectedTab === 'trends' && (
              <div className="space-y-4">
                <h3 className="text-lg font-semibold text-gray-900 dark:text-white">Potential Savings Trend</h3>
                <Card>
                  <div className="p-4">
                    {chartData.length === 0 ? (
                      <div className="text-center py-12 text-gray-500 dark:text-gray-400">
                        <TrendingUp className="mx-auto mb-4 text-gray-400" size={48} />
                        <p>No historical data available</p>
                        <p className="text-sm mt-2">Run this check multiple times to see potential savings trends</p>
                      </div>
                    ) : chartData.length === 1 ? (
                      <div className="text-center py-12 text-gray-500 dark:text-gray-400">
                        <TrendingUp className="mx-auto mb-4 text-gray-400" size={48} />
                        <p>Insufficient data for trend analysis</p>
                        <p className="text-sm mt-2">Run this check at least one more time to see potential savings trends</p>
                      </div>
                    ) : (
                      <div className="space-y-4">
                        <div className="h-80">
                          <ResponsiveContainer width="100%" height="100%">
                            <LineChart data={chartData} margin={{ top: 5, right: 30, left: 20, bottom: 5 }}>
                              <CartesianGrid strokeDasharray="3 3" stroke={chartGridColor(theme)} />
                              <XAxis
                                dataKey="date"
                                className="text-xs"
                                stroke={chartAxisLineColor(theme)}
                              />
                              <YAxis
                                className="text-xs"
                                stroke={chartAxisLineColor(theme)}
                                tickFormatter={(value) => `$${value.toLocaleString()}`}
                              />
                              <Tooltip
                                contentStyle={{
                                  backgroundColor: theme === 'dark' ? 'rgba(14, 24, 48, 0.95)' : 'rgba(255, 255, 255, 0.95)',
                                  border: `1px solid ${chartGridColor(theme)}`,
                                  borderRadius: '8px',
                                  padding: '12px',
                                }}
                                formatter={(value: number | undefined, name: string | undefined) => {
                                  if (name === 'savings') {
                                    return [`$${(value ?? 0).toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 })}/yr`, 'Potential Savings'];
                                  }
                                  return [value ?? 0, 'Resources'];
                                }}
                                labelFormatter={(label) => `Date: ${label}`}
                              />
                              <Legend
                                wrapperStyle={{ paddingTop: '20px' }}
                                formatter={(value) => {
                                  if (value === 'savings') return 'Potential Savings ($/yr)';
                                  if (value === 'resources') return 'Resources Found';
                                  return value;
                                }}
                              />
                              <Line
                                type="monotone"
                                dataKey="savings"
                                stroke={STATUS_COLORS.healthy}
                                strokeWidth={2}
                                dot={{ fill: STATUS_COLORS.healthy, r: 4 }}
                                activeDot={{ r: 6 }}
                                name="savings"
                              />
                            </LineChart>
                          </ResponsiveContainer>
                        </div>
                      </div>
                    )}
                  </div>
                </Card>
              </div>
            )}

            {/* Documentation Tab */}
            {selectedTab === 'docs' && (
              <div className="space-y-4">
                <h3 className="text-lg font-semibold text-gray-900 dark:text-white">Documentation</h3>
                <Card>
                  <div className="p-4 space-y-4">
                    <div>
                      <div className="flex items-center space-x-2 mb-2">
                        <BookOpen className="text-primary-600 dark:text-primary-400" size={20} />
                        <h4 className="font-semibold text-gray-900 dark:text-white">Check Description</h4>
                      </div>
                      <p className="text-sm text-gray-600 dark:text-gray-400">
                        {check.description}
                      </p>
                    </div>

                    <div>
                      <div className="flex items-center space-x-2 mb-2">
                        <BarChart3 className="text-primary-600 dark:text-primary-400" size={20} />
                        <h4 className="font-semibold text-gray-900 dark:text-white">Resource Type</h4>
                      </div>
                      <p className="text-sm text-gray-600 dark:text-gray-400">
                        {check.resource_type}
                      </p>
                    </div>

                    <div>
                      <div className="flex items-center space-x-2 mb-2">
                        <Zap className="text-primary-600 dark:text-primary-400" size={20} />
                        <h4 className="font-semibold text-gray-900 dark:text-white">Default Action</h4>
                      </div>
                      <p className="text-sm text-gray-600 dark:text-gray-400">
                        {check.default_action}
                      </p>
                    </div>

                    <div>
                      <h4 className="font-semibold text-gray-900 dark:text-white mb-2">Best Practices</h4>
                      <div className="text-sm text-gray-600 dark:text-gray-400 space-y-2">
                        {getBestPractices(check.check_id).map((practice, index) => (
                          <p key={index}>
                            â€¢ {practice}
                          </p>
                        ))}
                      </div>
                    </div>
                  </div>
                </Card>
              </div>
            )}
          </>
        )}

      </div>
    </Modal>

    {/* Snooze Dialog */}
    {snoozeResource && (
      <SnoozeDialog
        isOpen={!!snoozeResource}
        onClose={() => setSnoozeResource(null)}
        onConfirm={(days) => {
          if (snoozeResource) {
            snoozeMutation.mutate({
              resourceId: snoozeResource.resourceId,
              days
            });
          }
        }}
        resourceId={snoozeResource.resourceId}
      />
    )}

    {actionDialog && (
      <Modal
        isOpen={true}
        onClose={() => setActionDialog(null)}
        title="Execute Action"
        size="lg"
        closeOnOverlayClick={false}
      >
        <div className="space-y-4">
          <div className="text-sm text-gray-700 dark:text-gray-300">
            Action: <span className="font-semibold">{actionDialog.action}</span>
          </div>
          {actionDialogImpact && actionDialogImpactStyles && (
            <div className={`rounded-lg border p-3 text-sm ${actionDialogImpactStyles.panel}`}>
              <div className={`font-semibold ${actionDialogImpactStyles.title}`}>
                {actionDialogImpact.tag}
              </div>
              <div className={`mt-1 ${actionDialogImpactStyles.helper}`}>
                {actionDialogImpact.summary}
              </div>
              {actionDialogImpact.warning && (
                <div className={`mt-2 ${actionDialogImpactStyles.helper}`}>
                  Warning: {actionDialogImpact.warning}
                </div>
              )}
              {actionDialogImpact.acknowledgmentRequired && (
                <label className={`mt-3 flex items-start gap-2 text-sm ${actionDialogImpactStyles.checkbox}`}>
                  <input
                    type="checkbox"
                    className="mt-0.5 h-4 w-4"
                    checked={impactAcknowledged}
                    onChange={(e) => setImpactAcknowledged(e.target.checked)}
                  />
                  <span>I understand the impact and want to continue with this action.</span>
                </label>
              )}
            </div>
          )}
          <div className="text-sm text-gray-700 dark:text-gray-300">
            Target resources: {actionDialog.resourceIds.length}
          </div>
          <div className="text-xs text-gray-600 dark:text-gray-400">
            {actionDialog.resourceIds.join(', ')}
          </div>

          {loggingTargetBuckets.length > 0 && (
            <div className="rounded-lg border border-gray-200 dark:border-gray-700 p-3 text-sm">
              <div className="font-medium text-gray-900 dark:text-white">Logging target bucket(s)</div>
              <div className="text-gray-700 dark:text-gray-300 mt-1">
                {loggingTargetBuckets.join(', ')}
              </div>
            </div>
          )}
          {inventoryTargetBuckets.length > 0 && (
            <div className="rounded-lg border border-gray-200 dark:border-gray-700 p-3 text-sm">
              <div className="font-medium text-gray-900 dark:text-white">Inventory target bucket(s)</div>
              <div className="text-gray-700 dark:text-gray-300 mt-1">
                {inventoryTargetBuckets.join(', ')}
              </div>
            </div>
          )}
          {replicationTargetBuckets.length > 0 && (
            <div className="rounded-lg border border-gray-200 dark:border-gray-700 p-3 text-sm">
              <div className="font-medium text-gray-900 dark:text-white">Replication target bucket(s)</div>
              <div className="text-gray-700 dark:text-gray-300 mt-1">
                {replicationTargetBuckets.join(', ')}
              </div>
            </div>
          )}

          {actionDialog.action === 'Migrate to Graviton' && (
            <div className="space-y-3">
              <div>
                <label className="block text-sm font-medium text-gray-700 dark:text-gray-300 mb-2">
                  Target instance class
                </label>
                <select
                  className="input"
                  value={targetInstanceClass}
                  onChange={(e) => setTargetInstanceClass(e.target.value)}
                  disabled={rdsClassLoading}
                >
                  <option value="">Select class</option>
                  {rdsClassOptions?.graviton_instance_classes?.map((cls) => (
                    <option key={cls} value={cls}>
                      {cls}
                    </option>
                  ))}
                </select>
              </div>
              <div>
                <label className="block text-sm font-medium text-gray-700 dark:text-gray-300 mb-2">
                  Apply timing
                </label>
                <select
                  className="input"
                  value={applyImmediately ? 'immediate' : 'maintenance'}
                  onChange={(e) => setApplyImmediately(e.target.value === 'immediate')}
                >
                  <option value="immediate">Apply immediately</option>
                  <option value="maintenance">Apply in next maintenance window</option>
                </select>
              </div>
            </div>
          )}
          {actionDialog.action === 'Add policy - Add archival transition' && (
            <div className="space-y-3">
              <div>
                <label className="block text-sm font-medium text-gray-700 dark:text-gray-300 mb-2">
                  Transition after days
                </label>
                <Input
                  value={archivalDays}
                  onChange={(e) => setArchivalDays(e.target.value)}
                  type="number"
                  min="1"
                />
              </div>
            </div>
          )}
          {actionDialog.action === 'Add policy - Add expiration for old objects' && (
            <div className="space-y-3">
              <div>
                <label className="block text-sm font-medium text-gray-700 dark:text-gray-300 mb-2">
                  Expire after days
                </label>
                <Input
                  value={expirationDays}
                  onChange={(e) => setExpirationDays(e.target.value)}
                  type="number"
                  min="1"
                />
              </div>
            </div>
          )}
          {actionDialog.action === 'Add policy - Expire noncurrent versions' && (
            <div className="space-y-3">
              <div>
                <label className="block text-sm font-medium text-gray-700 dark:text-gray-300 mb-2">
                  Noncurrent expiration days
                </label>
                <Input
                  value={noncurrentExpirationDays}
                  onChange={(e) => setNoncurrentExpirationDays(e.target.value)}
                  type="number"
                  min="1"
                />
              </div>
            </div>
          )}
          {actionDialog.action === 'Add policy - Add noncurrent archival transition' && (
            <div className="space-y-3">
              <div>
                <label className="block text-sm font-medium text-gray-700 dark:text-gray-300 mb-2">
                  Transition after days
                </label>
                <Input
                  value={archivalDays}
                  onChange={(e) => setArchivalDays(e.target.value)}
                  type="number"
                  min="1"
                />
              </div>
            </div>
          )}
          {actionDialog.action === 'Reduce RCU' && (
            <div className="space-y-3">
              {reduceRcuCapacities && reduceRcuCapacities.values.length > 0 && (
                <div className="text-sm text-gray-600 dark:text-gray-400">
                  Current read capacity:{' '}
                  {reduceRcuCapacities.min === reduceRcuCapacities.max
                    ? reduceRcuCapacities.min
                    : `${reduceRcuCapacities.min} - ${reduceRcuCapacities.max}`}
                </div>
              )}
              <div>
                <label className="block text-sm font-medium text-gray-700 dark:text-gray-300 mb-2">
                  New read capacity units
                </label>
                <Input
                  value={reduceRcuValue}
                  onChange={(e) => setReduceRcuValue(e.target.value)}
                  type="number"
                  min="1"
                />
              </div>
            </div>
          )}
          {actionDialog.action === 'Reduce WCU' && (
            <div className="space-y-3">
              {reduceWcuCapacities && reduceWcuCapacities.values.length > 0 && (
                <div className="text-sm text-gray-600 dark:text-gray-400">
                  Current write capacity:{' '}
                  {reduceWcuCapacities.min === reduceWcuCapacities.max
                    ? reduceWcuCapacities.min
                    : `${reduceWcuCapacities.min} - ${reduceWcuCapacities.max}`}
                </div>
              )}
              <div>
                <label className="block text-sm font-medium text-gray-700 dark:text-gray-300 mb-2">
                  New write capacity units
                </label>
                <Input
                  value={reduceWcuValue}
                  onChange={(e) => setReduceWcuValue(e.target.value)}
                  type="number"
                  min="1"
                />
              </div>
            </div>
          )}
          {(actionDialog.action === 'Downsize' ||
            actionDialog.action === 'Migrate to Graviton node type') && (
            <div className="space-y-3">
              <div>
                <label className="block text-sm font-medium text-gray-700 dark:text-gray-300 mb-2">
                  Target node type
                </label>
                <select
                  className="input"
                  value={elasticacheTargetNodeType}
                  onChange={(e) => setElasticacheTargetNodeType(e.target.value)}
                >
                  <option value="">Select node type</option>
                  {elasticacheNodeTypeOptions.map((nodeType) => (
                    <option key={nodeType} value={nodeType}>
                      {nodeType}
                    </option>
                  ))}
                </select>
              </div>
              <div>
                <label className="block text-sm font-medium text-gray-700 dark:text-gray-300 mb-2">
                  Node count (optional)
                </label>
                <Input
                  value={elasticacheNodeCount}
                  onChange={(e) => setElasticacheNodeCount(e.target.value)}
                  type="number"
                  min="1"
                />
              </div>
            </div>
          )}
          {actionDialog.action === 'Upgrade to Valkey' && (
            <div className="space-y-3">
              <div className="text-sm text-gray-600 dark:text-gray-400">
                Snapshot-based migration can take several minutes. Validate the new Valkey group before deleting the
                original Redis group.
              </div>
              {elasticacheTargetResource?.resource_type === 'elasticache_replication_group' && (
                <div className="rounded-md border border-warning-200 bg-warning-50 px-3 py-2 text-sm text-warning-800 dark:border-warning-700/60 dark:bg-warning-950/30 dark:text-warning-100">
                  This resource is a replication group. Upgrading to Valkey will create a new replication group from a
                  snapshot. The original Redis group will remain until you delete it.
                </div>
              )}
              <div>
                <label className="block text-sm font-medium text-gray-700 dark:text-gray-300 mb-2">
                  Target engine version (optional, defaults to latest)
                </label>
                <Input
                  value={elasticacheEngineVersion}
                  onChange={(e) => setElasticacheEngineVersion(e.target.value)}
                  placeholder="7.1"
                />
              </div>
              <div>
                <label className="block text-sm font-medium text-gray-700 dark:text-gray-300 mb-2">
                  Target Valkey replication group ID (optional)
                </label>
                <Input
                  value={elasticacheTargetReplicationGroupId}
                  onChange={(e) => setElasticacheTargetReplicationGroupId(e.target.value)}
                  placeholder="maxops-test-elasticache-redis-valkey-valkey"
                />
              </div>
              <label className="flex items-center gap-2 text-sm text-gray-700 dark:text-gray-300">
                <input
                  type="checkbox"
                  className="h-4 w-4"
                  checked={elasticacheTransitEncryptionEnabled}
                  onChange={(e) => setElasticacheTransitEncryptionEnabled(e.target.checked)}
                />
                Transit encryption enabled
              </label>
            </div>
          )}
          {(actionDialog.action === 'Reduce provisioned IOPS' || actionDialog.action === 'Reduce provisioned IOPS or migrate to gp3') && (
            <div className="space-y-3">
              {ebsProvisionedIops && ebsProvisionedIops.values.length > 0 && (
                <div className="text-sm text-gray-600 dark:text-gray-400">
                  Current IOPS:{' '}
                  {ebsProvisionedIops.min === ebsProvisionedIops.max
                    ? ebsProvisionedIops.min
                    : `${ebsProvisionedIops.min} - ${ebsProvisionedIops.max}`}
                </div>
              )}
              <div>
                <label className="block text-sm font-medium text-gray-700 dark:text-gray-300 mb-2">
                  New IOPS
                </label>
                <Input
                  value={ebsReduceIops}
                  onChange={(e) => setEbsReduceIops(e.target.value)}
                  type="number"
                  min="1"
                />
                {getSuggestedEbsIops(primaryActionMetadata) && (
                  <div className="text-xs text-primary-600 dark:text-primary-400 mt-1">
                    Suggested IOPS: {getSuggestedEbsIops(primaryActionMetadata)}
                  </div>
                )}
              </div>
            </div>
          )}
          {(
            actionDialog.action === 'Downsize volume' ||
            actionDialog.action === 'Modify volume type to gp3; tune IOPS/throughput' ||
            actionDialog.action === 'Downsize volume; consider filesystem resize + snapshot/restore'
          ) && (
            <div className="space-y-3">
              {ebsProvisionedIops && ebsProvisionedIops.values.length > 0 && (
                <div className="text-sm text-gray-600 dark:text-gray-400">
                  Current IOPS:{' '}
                  {ebsProvisionedIops.min === ebsProvisionedIops.max
                    ? ebsProvisionedIops.min
                    : `${ebsProvisionedIops.min} - ${ebsProvisionedIops.max}`}
                </div>
              )}
              {ebsProvisionedThroughput && ebsProvisionedThroughput.values.length > 0 && (
                <div className="text-sm text-gray-600 dark:text-gray-400">
                  Current throughput:{' '}
                  {ebsProvisionedThroughput.min === ebsProvisionedThroughput.max
                    ? ebsProvisionedThroughput.min
                    : `${ebsProvisionedThroughput.min} - ${ebsProvisionedThroughput.max}`} MB/s
                </div>
              )}
              <div className="grid grid-cols-3 gap-4">
                <div>
                  <label className="block text-sm font-medium text-gray-700 dark:text-gray-300 mb-2">
                    Target IOPS
                  </label>
                  <Input
                    value={ebsDownsizeIops}
                    onChange={(e) => setEbsDownsizeIops(e.target.value)}
                    type="number"
                    min="1"
                  />
                  {getSuggestedEbsIops(primaryActionMetadata) && (
                    <div className="text-xs text-primary-600 dark:text-primary-400 mt-1">
                      Suggested: {getSuggestedEbsIops(primaryActionMetadata)}
                    </div>
                  )}
                </div>
                <div>
                  <label className="block text-sm font-medium text-gray-700 dark:text-gray-300 mb-2">
                    Target throughput (MB/s)
                  </label>
                  <Input
                    value={ebsDownsizeThroughput}
                    onChange={(e) => setEbsDownsizeThroughput(e.target.value)}
                    type="number"
                    min="1"
                  />
                  {getSuggestedEbsThroughput(primaryActionMetadata) && (
                    <div className="text-xs text-primary-600 dark:text-primary-400 mt-1">
                      Suggested: {getSuggestedEbsThroughput(primaryActionMetadata)} MB/s
                    </div>
                  )}
                </div>
                <div>
                  <label className="block text-sm font-medium text-gray-700 dark:text-gray-300 mb-2">
                    Target volume type
                  </label>
                  <Input
                    value={ebsDownsizeVolumeType}
                    onChange={(e) => setEbsDownsizeVolumeType(e.target.value)}
                    placeholder="gp3"
                    readOnly={actionDialog.action === 'Modify volume type to gp3; tune IOPS/throughput'}
                  />
                  {actionDialog.action === 'Modify volume type to gp3; tune IOPS/throughput' && (
                    <div className="text-xs text-gray-500 dark:text-gray-400 mt-1">
                      Target volume type is fixed to gp3 for this action.
                    </div>
                  )}
                </div>
              </div>
            </div>
          )}
          {actionDialog.action === 'lifecycle policy' && (
            <div className="space-y-3">
              <div className="grid grid-cols-2 gap-4">
                <div>
                  <label className="block text-sm font-medium text-gray-700 dark:text-gray-300 mb-2">
                    Policy name
                  </label>
                  <Input
                    value={ebsLifecyclePolicyName}
                    onChange={(e) => setEbsLifecyclePolicyName(e.target.value)}
                  />
                </div>
                <div>
                  <label className="block text-sm font-medium text-gray-700 dark:text-gray-300 mb-2">
                    Interval (hours)
                  </label>
                  <Input
                    value={ebsLifecycleIntervalHours}
                    onChange={(e) => setEbsLifecycleIntervalHours(e.target.value)}
                    type="number"
                    min="1"
                  />
                </div>
              </div>
              <div className="grid grid-cols-2 gap-4">
                <div>
                  <label className="block text-sm font-medium text-gray-700 dark:text-gray-300 mb-2">
                    Retain count
                  </label>
                  <Input
                    value={ebsLifecycleRetainCount}
                    onChange={(e) => setEbsLifecycleRetainCount(e.target.value)}
                    type="number"
                    min="1"
                  />
                </div>
                <div>
                  <label className="block text-sm font-medium text-gray-700 dark:text-gray-300 mb-2">
                    Tag key
                  </label>
                  <Input
                    value={ebsLifecycleTagKey}
                    onChange={(e) => setEbsLifecycleTagKey(e.target.value)}
                  />
                </div>
              </div>
              <div>
                <label className="block text-sm font-medium text-gray-700 dark:text-gray-300 mb-2">
                  Tag value
                </label>
                <Input
                  value={ebsLifecycleTagValue}
                  onChange={(e) => setEbsLifecycleTagValue(e.target.value)}
                />
              </div>
              <div>
                <label className="block text-sm font-medium text-gray-700 dark:text-gray-300 mb-2">
                  DLM execution role ARN
                </label>
                <Input
                  value={ebsLifecycleRoleArn}
                  onChange={(e) => setEbsLifecycleRoleArn(e.target.value)}
                  placeholder="arn:aws:iam::123456789012:role/YourDlmRole"
                />
              </div>
            </div>
          )}
          {actionDialog.action === 'Add lifecycle policy' && check.resource_type === 's3' && (
            <div className="space-y-3">
              <div className="text-sm text-gray-600 dark:text-gray-400 mb-3">
                Configure S3 lifecycle policy to automatically transition objects to cheaper storage classes and expire old objects.
              </div>
              <div className="grid grid-cols-2 gap-4">
                <div>
                  <label className="block text-sm font-medium text-gray-700 dark:text-gray-300 mb-2">
                    Transition to archive (days)
                  </label>
                  <Input
                    value={s3TransitionDays}
                    onChange={(e) => setS3TransitionDays(e.target.value)}
                    type="number"
                    min="30"
                    placeholder="30"
                  />
                  <div className="text-xs text-gray-500 dark:text-gray-400 mt-1">
                    Minimum 30 days for Glacier
                  </div>
                </div>
                <div>
                  <label className="block text-sm font-medium text-gray-700 dark:text-gray-300 mb-2">
                    Storage class
                  </label>
                  <select
                    value={s3TransitionStorageClass}
                    onChange={(e) => setS3TransitionStorageClass(e.target.value)}
                    className="input"
                  >
                    <option value="GLACIER">Glacier (~$0.004/GB/month)</option>
                    <option value="GLACIER_IR">Glacier Instant Retrieval (~$0.004/GB/month)</option>
                    <option value="DEEP_ARCHIVE">Deep Archive (~$0.00099/GB/month)</option>
                    <option value="INTELLIGENT_TIERING">Intelligent-Tiering (automatic)</option>
                  </select>
                </div>
              </div>
              <div className="grid grid-cols-2 gap-4">
                <div>
                  <label className="block text-sm font-medium text-gray-700 dark:text-gray-300 mb-2">
                    Delete after (days)
                  </label>
                  <Input
                    value={s3ExpirationDays}
                    onChange={(e) => setS3ExpirationDays(e.target.value)}
                    type="number"
                    min="0"
                    placeholder="365"
                  />
                  <div className="text-xs text-gray-500 dark:text-gray-400 mt-1">
                    Set to 0 to disable expiration
                  </div>
                </div>
                <div>
                  <label className="block text-sm font-medium text-gray-700 dark:text-gray-300 mb-2">
                    Abort incomplete uploads (days)
                  </label>
                  <Input
                    value={s3AbortMpuDays}
                    onChange={(e) => setS3AbortMpuDays(e.target.value)}
                    type="number"
                    min="1"
                    placeholder="7"
                  />
                  <div className="text-xs text-gray-500 dark:text-gray-400 mt-1">
                    Cleans up failed multipart uploads
                  </div>
                </div>
              </div>
            </div>
          )}
          {actionDialog.action === 'Add lifecycle policy' && check.resource_type === 'efs' && (
            <div className="space-y-3">
              <div className="text-sm text-gray-600 dark:text-gray-400 mb-3">
                Configure EFS lifecycle policy to automatically move infrequently accessed files to lower-cost IA storage (saves ~85%).
              </div>
              <div>
                <label className="block text-sm font-medium text-gray-700 dark:text-gray-300 mb-2">
                  Transition to Infrequent Access after
                </label>
                <select
                  value={efsTransitionToIA}
                  onChange={(e) => setEfsTransitionToIA(e.target.value)}
                  className="input"
                >
                  <option value="AFTER_7_DAYS">7 days (aggressive savings)</option>
                  <option value="AFTER_14_DAYS">14 days (recommended)</option>
                  <option value="AFTER_30_DAYS">30 days (balanced)</option>
                  <option value="AFTER_60_DAYS">60 days (conservative)</option>
                  <option value="AFTER_90_DAYS">90 days (very conservative)</option>
                </select>
                <div className="text-xs text-gray-500 dark:text-gray-400 mt-1">
                  Files not accessed for this period will be moved to IA storage ($0.025/GB vs $0.30/GB)
                </div>
              </div>
            </div>
          )}
          {(
            actionDialog.action === 'Update memory configuration' ||
            actionDialog.action === 'Update memory' ||
            actionDialog.action === 'Rightsize memory using duration vs memory analysis'
          ) && (
            <div className="space-y-3">
              <div className="text-sm text-gray-600 dark:text-gray-400 mb-3">
                Adjust Lambda function memory allocation. AWS also scales CPU proportionally with memory.
              </div>
              {isLambdaRightsizeAction && recommendedLambdaMemoryMb > 0 && (
                <div className="rounded-md border border-primary-200 bg-primary-50 px-3 py-2 text-sm text-primary-800 dark:border-primary-700/60 dark:bg-primary-950/30 dark:text-primary-100">
                  Optimal memory from duration vs memory analysis: <span className="font-semibold">{recommendedLambdaMemoryMb} MB</span>
                </div>
              )}
              <div>
                <label className="block text-sm font-medium text-gray-700 dark:text-gray-300 mb-2">
                  {isLambdaRightsizeAction ? 'Optimal Memory Size (MB)' : 'Memory Size (MB)'}
                </label>
                <Input
                  value={lambdaMemorySize}
                  onChange={(e) => setLambdaMemorySize(e.target.value)}
                  type="number"
                  min="128"
                  max="10240"
                  step="64"
                  disabled={isLambdaRightsizeAction}
                  placeholder={String(recommendedLambdaMemoryMb || 256)}
                />
                <div className="text-xs text-gray-500 dark:text-gray-400 mt-1">
                  {isLambdaRightsizeAction
                    ? 'This value is auto-selected from analysis results.'
                    : 'Must be between 128 MB and 10,240 MB (in 1 MB increments)'}
                  {recommendedLambdaMemoryMb > 0 && (
                    <span className="block mt-1 text-primary-600 dark:text-primary-400">
                      Recommended: {recommendedLambdaMemoryMb} MB
                    </span>
                  )}
                </div>
              </div>
            </div>
          )}
          {(
            actionDialog.action === 'Reduce provisioned concurrency' ||
            actionDialog.action === 'Update provisioned concurrency' ||
            actionDialog.action === 'Reduce/disable provisioned concurrency'
          ) && (
            <div className="space-y-3">
              <div className="text-sm text-gray-600 dark:text-gray-400 mb-3">
                Set provisioned concurrency to keep Lambda functions warm. Set to 0 to disable and use on-demand only.
              </div>
              <div>
                <label className="block text-sm font-medium text-gray-700 dark:text-gray-300 mb-2">
                  Provisioned Concurrent Executions
                </label>
                <Input
                  value={lambdaProvisionedConcurrency}
                  onChange={(e) => setLambdaProvisionedConcurrency(e.target.value)}
                  type="number"
                  min="0"
                  placeholder={primaryActionMetadata?.recommended_provisioned_concurrency?.toString() || '0'}
                />
                <div className="text-xs text-gray-500 dark:text-gray-400 mt-1">
                  Set to 0 to disable provisioned concurrency (recommended for low-traffic functions)
                  {primaryActionMetadata?.recommended_provisioned_concurrency !== undefined && (
                    <span className="block mt-1 text-primary-600 dark:text-primary-400">
                      Recommended: {primaryActionMetadata.recommended_provisioned_concurrency} units
                    </span>
                  )}
                </div>
              </div>
            </div>
          )}
          {(
            actionDialog.action === 'Reduce log verbosity and set retention' ||
            actionDialog.action === 'Set log retention' ||
            actionDialog.action === 'Reduce log verbosity; set retention'
          ) && (
            <div className="space-y-3">
              <div className="text-sm text-gray-600 dark:text-gray-400 mb-3">
                Set CloudWatch Logs retention period. Shorter retention reduces storage costs. Consider also reducing log verbosity in your function code.
              </div>
              <div>
                <label className="block text-sm font-medium text-gray-700 dark:text-gray-300 mb-2">
                  Log Retention (days)
                </label>
                <select
                  value={lambdaLogRetentionDays}
                  onChange={(e) => setLambdaLogRetentionDays(e.target.value)}
                  className="input"
                >
                  <option value="1">1 day (minimal retention)</option>
                  <option value="3">3 days</option>
                  <option value="5">5 days</option>
                  <option value="7">7 days (recommended for dev)</option>
                  <option value="14">14 days</option>
                  <option value="30">30 days (recommended for prod)</option>
                  <option value="60">60 days</option>
                  <option value="90">90 days</option>
                  <option value="365">1 year</option>
                </select>
                <div className="text-xs text-gray-500 dark:text-gray-400 mt-1">
                  Logs older than this period will be automatically deleted
                  {primaryActionMetadata?.log_group_name && (
                    <span className="block mt-1 text-gray-600 dark:text-gray-400">
                      Log group: {primaryActionMetadata.log_group_name}
                    </span>
                  )}
                </div>
              </div>
            </div>
          )}
          {actionDialog.action === 'Use Provisioned Capacity' && (
            <div className="space-y-3">
              <div className="grid grid-cols-2 gap-4">
                <div>
                  <label className="block text-sm font-medium text-gray-700 dark:text-gray-300 mb-2">
                    Read capacity units
                  </label>
                  <Input
                    value={provisionedReadCapacity}
                    onChange={(e) => setProvisionedReadCapacity(e.target.value)}
                    type="number"
                    min="1"
                  />
                </div>
                <div>
                  <label className="block text-sm font-medium text-gray-700 dark:text-gray-300 mb-2">
                    Write capacity units
                  </label>
                  <Input
                    value={provisionedWriteCapacity}
                    onChange={(e) => setProvisionedWriteCapacity(e.target.value)}
                    type="number"
                    min="1"
                  />
                </div>
              </div>
            </div>
          )}
          {actionDialog.action === 'Modify performance mode' && (
            <div className="space-y-3">
              <div className="rounded-md border border-primary-200 bg-primary-50 px-3 py-2 text-sm text-primary-800 dark:border-primary-700/60 dark:bg-primary-950/30 dark:text-primary-100">
                <div className="font-medium mb-1">Note: Performance mode cannot be changed after creation</div>
                <div>This action will create a new file system with the desired performance mode. You can optionally copy data from the old file system to the new one.</div>
              </div>
              <div>
                <label className="block text-sm font-medium text-gray-700 dark:text-gray-300 mb-2">
                  Desired Performance Mode
                </label>
                <select
                  className="input"
                  value={efsPerformanceMode}
                  onChange={(e) => setEfsPerformanceMode(e.target.value)}
                >
                  <option value="generalPurpose">General Purpose (default, recommended for most workloads)</option>
                  <option value="maxIO">Max I/O (for workloads with high I/O requirements)</option>
                </select>
              </div>
              <div className="space-y-2">
                <label className="flex items-center gap-2 text-sm text-gray-700 dark:text-gray-300">
                  <input
                    type="checkbox"
                    className="h-4 w-4"
                    checked={efsCopyData}
                    onChange={(e) => setEfsCopyData(e.target.checked)}
                  />
                  Copy data from old file system to new file system
                </label>
                {efsCopyData && (
                  <label className="flex items-center gap-2 text-sm text-gray-700 dark:text-gray-300 ml-6">
                    <input
                      type="checkbox"
                      className="h-4 w-4"
                      checked={efsDeleteOld}
                      onChange={(e) => setEfsDeleteOld(e.target.checked)}
                    />
                    Delete old file system after copy completes
                  </label>
                )}
                {efsCopyData && (
                  <div className="text-xs text-gray-600 dark:text-gray-400 ml-6">
                    Data will be copied using AWS DataSync or rsync. This may take some time depending on the amount of data.
                  </div>
                )}
              </div>
            </div>
          )}
          {actionDialog.action === 'Modify throughput mode' && (
            <div className="space-y-3">
              <div>
                <label className="block text-sm font-medium text-gray-700 dark:text-gray-300 mb-2">
                  Throughput Mode
                </label>
                <select
                  className="input"
                  value={efsThroughputMode}
                  onChange={(e) => setEfsThroughputMode(e.target.value)}
                >
                  <option value="bursting">Bursting (default, scales with file system size)</option>
                  <option value="provisioned">Provisioned (for consistent performance requirements)</option>
                </select>
              </div>
              {efsThroughputMode === 'provisioned' && (
                <div>
                  <label className="block text-sm font-medium text-gray-700 dark:text-gray-300 mb-2">
                    Provisioned Throughput (MiB/s)
                  </label>
                  <Input
                    value={efsProvisionedThroughput}
                    onChange={(e) => setEfsProvisionedThroughput(e.target.value)}
                    type="number"
                    min="1"
                    placeholder="e.g., 100"
                  />
                  <div className="text-xs text-gray-600 dark:text-gray-400 mt-1">
                    Required when using provisioned throughput mode. Minimum is 1 MiB/s.
                  </div>
                </div>
              )}
            </div>
          )}

          {/* EFS Migration Progress */}
          {(efsMigrationStatus && (efsMigrationStatus.isPolling || efsMigrationStatus.status === 'MANUAL_REQUIRED') && 
            efsMigrationStatus.resourceId === actionDialog?.resourceIds?.[0]) && (
            <div className="rounded-lg border border-primary-200 bg-primary-50 dark:border-primary-700/60 dark:bg-primary-950/30 p-4">
              <div className="flex items-center gap-3 mb-3">
                {efsMigrationStatus.status !== 'MANUAL_REQUIRED' && (
                  <div className="animate-spin rounded-full h-5 w-5 border-b-2 border-primary-600 dark:border-primary-400"></div>
                )}
                <div className="font-semibold text-primary-900 dark:text-primary-100">
                  {efsMigrationStatus.status === 'INITIALIZING' && 'Initializing data copy...'}
                  {efsMigrationStatus.status === 'LAUNCHING' && 'Launching DataSync task...'}
                  {efsMigrationStatus.status === 'PREPARING' && 'Preparing data transfer...'}
                  {efsMigrationStatus.status === 'TRANSFERRING' && 'Copying data...'}
                  {efsMigrationStatus.status === 'VERIFYING' && 'Verifying data integrity...'}
                  {efsMigrationStatus.status === 'SUCCESS' && 'Migration completed!'}
                  {efsMigrationStatus.status === 'ERROR' && 'Migration failed'}
                  {efsMigrationStatus.status === 'MANUAL_REQUIRED' && 'Manual data copy required'}
                  {!efsMigrationStatus.status && 'Monitoring migration...'}
                </div>
              </div>
              {efsMigrationStatus.status === 'MANUAL_REQUIRED' && efsMigrationStatus.manualInstructions && (
                <div className="text-sm text-primary-800 dark:text-primary-200 space-y-2">
                  <div className="font-medium">Please follow these steps to copy data manually:</div>
                  <ol className="list-decimal list-inside space-y-1 ml-2">
                    {efsMigrationStatus.manualInstructions.map((instruction: string, idx: number) => (
                      <li key={idx}>{instruction}</li>
                    ))}
                  </ol>
                  {efsMigrationStatus.newFileSystemId && (
                    <div className="mt-2 pt-2 border-t border-primary-300 dark:border-primary-700">
                      <div className="font-medium">New file system ID: {efsMigrationStatus.newFileSystemId}</div>
                      <div className="text-xs mt-1">Old file system ID: {efsMigrationStatus.oldFileSystemId}</div>
                    </div>
                  )}
                </div>
              )}
              {efsMigrationStatus.progress && (
                <div className="text-sm text-primary-800 dark:text-primary-200 space-y-1">
                  {efsMigrationStatus.progress.files_transferred !== undefined && (
                    <div>Files transferred: {efsMigrationStatus.progress.files_transferred.toLocaleString()}</div>
                  )}
                  {efsMigrationStatus.progress.bytes_transferred !== undefined && (
                    <div>Data transferred: {formatBytes(efsMigrationStatus.progress.bytes_transferred)}</div>
                  )}
                  {efsMigrationStatus.progress.files_skipped !== undefined && efsMigrationStatus.progress.files_skipped > 0 && (
                    <div>Files skipped: {efsMigrationStatus.progress.files_skipped.toLocaleString()}</div>
                  )}
                </div>
              )}
              {efsMigrationStatus.newFileSystemId && efsMigrationStatus.status !== 'MANUAL_REQUIRED' && (
                <div className="text-xs text-primary-700 dark:text-primary-300 mt-2">
                  New file system: {efsMigrationStatus.newFileSystemId}
                </div>
              )}
              {efsMigrationStatus.error && (
                <div className="text-sm text-danger-600 dark:text-danger-400 mt-2">
                  Error: {efsMigrationStatus.error}
                </div>
              )}
            </div>
          )}

          {actionResult && (
            <div className="rounded-lg border border-gray-200 dark:border-gray-700 p-3 text-sm whitespace-pre-wrap">
              {actionResult}
            </div>
          )}

          <div className="flex justify-end space-x-2">
            <Button 
              variant="secondary" 
              onClick={() => {
                setActionDialog(null);
                // Only clear migration status if it's for this resource
                if (efsMigrationStatus?.resourceId === actionDialog?.resourceIds?.[0]) {
                  setEfsMigrationStatus(null);
                }
              }}
              disabled={efsMigrationStatus?.isPolling && efsMigrationStatus?.resourceId === actionDialog?.resourceIds?.[0]}
            >
              {(efsMigrationStatus?.isPolling && efsMigrationStatus?.resourceId === actionDialog?.resourceIds?.[0]) ? 'Migration in progress...' : 'Close'}
            </Button>
            {!(efsMigrationStatus?.isPolling && efsMigrationStatus?.resourceId === actionDialog?.resourceIds?.[0]) && (() => {
              const isCurrentlyExecuting = actionDialog?.resourceIds?.some(id => executingResources.has(id));
              return (
                <Button 
                  variant="primary" 
                  onClick={executeAction} 
                  disabled={
                    !actionDialog?.action ||
                    isCurrentlyExecuting ||
                    (actionDialog?.resourceIds?.length === 1 && actionResult?.includes('already running')) ||
                    Boolean(actionDialogImpact?.acknowledgmentRequired && !impactAcknowledged)
                  }
                >
                  {isCurrentlyExecuting ? 'Executing...' : 'Execute'}
                </Button>
              );
            })()}
          </div>
        </div>
      </Modal>
    )}
    </>
  );
};
