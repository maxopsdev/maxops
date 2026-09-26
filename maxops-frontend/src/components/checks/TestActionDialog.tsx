import React, { useEffect, useMemo, useState } from 'react';
import { useQuery } from 'react-query';
import { Modal } from '@/components/common/Modal';
import { Button } from '@/components/common/Button';
import { Input } from '@/components/common/Input';
import { checksApi } from '@/services/checks';
import type { CheckMetadata } from '@/services/checks';
import { getElasticacheNodeTypeOptions } from './elasticacheNodeTypes';
import {
  formatActionLabelWithImpact,
  getActionImpact,
  getActionImpactStyles,
} from './actionImpact';

interface TestActionDialogProps {
  isOpen: boolean;
  onClose: () => void;
  check: CheckMetadata;
  actions: string[];
  defaultAccountId?: string;
  defaultRegion?: string;
  initialResourceId?: string;
  initialAccountId?: string;
  initialRegion?: string;
  initialAction?: string;
  resourceIdReadOnly?: boolean;
  hideTargetInputs?: boolean;
  resourceSummary?: React.ReactNode;
  bulkResourceIds?: string[];
  title?: string;
  runLabel?: string;
  onRun: (payload: {
    action: string;
    account_id: string;
    region: string;
    resource_id: string;
    parameters?: Record<string, any>;
  }) => Promise<{ status: string; message: string; details?: Record<string, any> }>;
  onRunBulk?: (payload: {
    action: string;
    resource_ids: string[];
    parameters?: Record<string, any>;
  }) => Promise<{ status: string; message: string; details?: Record<string, any> }>;
}

export const TestActionDialog: React.FC<TestActionDialogProps> = ({
  isOpen,
  onClose,
  check,
  actions,
  defaultAccountId,
  defaultRegion,
  initialResourceId,
  initialAccountId,
  initialRegion,
  initialAction,
  resourceIdReadOnly = false,
  hideTargetInputs = false,
  resourceSummary,
  bulkResourceIds,
  title = 'Test Action',
  runLabel = 'Run Test',
  onRun,
  onRunBulk,
}) => {
  const actionOptions = useMemo(
    () => actions.map((value) => ({ value, label: formatActionLabelWithImpact(value) })),
    [actions]
  );
  const [action, setAction] = useState(actionOptions[0]?.value || '');
  const actionImpact = useMemo(() => getActionImpact(action), [action]);
  const actionImpactStyles = useMemo(
    () => getActionImpactStyles(actionImpact.level),
    [actionImpact.level]
  );
  const elasticacheNodeTypeOptions = useMemo(
    () => getElasticacheNodeTypeOptions(action),
    [action]
  );
  const [accountId, setAccountId] = useState('');
  const [region, setRegion] = useState('');
  const [resourceId, setResourceId] = useState('');
  const [isSubmitting, setIsSubmitting] = useState(false);
  const [targetInstanceClass, setTargetInstanceClass] = useState('');
  const [applyImmediately, setApplyImmediately] = useState(true);
  const [archivalDays, setArchivalDays] = useState('30');
  const [expirationDays, setExpirationDays] = useState('90');
  const [noncurrentExpirationDays, setNoncurrentExpirationDays] = useState('30');
  const [provisionedReadCapacity, setProvisionedReadCapacity] = useState('');
  const [provisionedWriteCapacity, setProvisionedWriteCapacity] = useState('');
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
  const [efsPerformanceMode, setEfsPerformanceMode] = useState('maxIO');
  const [efsThroughputMode, setEfsThroughputMode] = useState('provisioned');
  const [efsProvisionedThroughput, setEfsProvisionedThroughput] = useState('');
  const [efsCopyData, setEfsCopyData] = useState(false);
  const [efsDeleteOld, setEfsDeleteOld] = useState(false);
  const [impactAcknowledged, setImpactAcknowledged] = useState(false);
  const [result, setResult] = useState<{ status: string; message: string; details?: Record<string, any> } | null>(null);
  const trimmedResourceId = resourceId.trim();
  const trimmedRegion = region.trim();

  useEffect(() => {
    setAction(initialAction || actionOptions[0]?.value || '');
  }, [actionOptions, initialAction]);

  const shouldLoadRdsClasses =
    action === 'Migrate to Graviton' && !!trimmedResourceId && !!trimmedRegion;
  const { data: rdsClassOptions, isLoading: rdsClassLoading } = useQuery(
    ['rds-instance-classes', check.check_id, trimmedResourceId, trimmedRegion],
    () => checksApi.getRdsInstanceClasses(check.check_id, trimmedResourceId, trimmedRegion),
    { enabled: shouldLoadRdsClasses, retry: false }
  );

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

  useEffect(() => {
    if (!isOpen) return;
    setResourceId(initialResourceId || '');
    setAccountId(initialAccountId || defaultAccountId || '');
    setRegion(initialRegion || defaultRegion || '');
    setResult(null);
    setImpactAcknowledged(false);
  }, [isOpen, initialResourceId, initialAccountId, initialRegion, defaultAccountId, defaultRegion]);

  useEffect(() => {
    setImpactAcknowledged(false);
  }, [action]);

  useEffect(() => {
    if (action === 'lifecycle policy') {
      const defaultName = trimmedResourceId ? `maxops-ebs-${trimmedResourceId}` : 'maxops-ebs-policy';
      setEbsLifecyclePolicyName(defaultName);
      setEbsLifecycleTagValue(defaultName);
    }
  }, [action, trimmedResourceId]);

  const canSubmit = hideTargetInputs
    ? Boolean(action && bulkResourceIds && bulkResourceIds.length > 0)
    : Boolean(action && accountId.trim() && region.trim() && resourceId.trim());

  const buildParameters = () =>
    action === 'Migrate to Graviton'
      ? {
          target_instance_class: targetInstanceClass,
          apply_immediately: applyImmediately,
        }
      : action === 'Add policy - Add archival transition'
      ? {
          archival_days: Number(archivalDays),
        }
      : action === 'Add policy - Add expiration for old objects'
      ? {
          expiration_days: Number(expirationDays),
        }
      : action === 'Add policy - Expire noncurrent versions'
      ? {
          noncurrent_expiration_days: Number(noncurrentExpirationDays),
        }
      : action === 'Add policy - Add noncurrent archival transition'
      ? {
          noncurrent_transition_days: Number(archivalDays),
        }
      : action === 'Use Provisioned Capacity'
      ? {
          read_capacity_units: Number(provisionedReadCapacity),
          write_capacity_units: Number(provisionedWriteCapacity),
        }
      : action === 'Reduce provisioned IOPS' || action === 'Reduce provisioned IOPS or migrate to gp3'
      ? {
          iops: Number(ebsReduceIops),
        }
      : action === 'Downsize volume' ||
        action === 'Modify volume type to gp3; tune IOPS/throughput' ||
        action === 'Downsize volume; consider filesystem resize + snapshot/restore'
      ? {
          iops: ebsDownsizeIops.trim() ? Number(ebsDownsizeIops) : undefined,
          throughput: ebsDownsizeThroughput.trim() ? Number(ebsDownsizeThroughput) : undefined,
          volume_type: ebsDownsizeVolumeType.trim() || undefined,
        }
      : action === 'Downsize' || action === 'Migrate to Graviton node type'
      ? {
          target_node_type: elasticacheTargetNodeType.trim(),
          num_cache_nodes: elasticacheNodeCount.trim() ? Number(elasticacheNodeCount) : undefined,
        }
      : action === 'Upgrade to Valkey'
      ? {
          target_engine_version: elasticacheEngineVersion.trim() || undefined,
          target_replication_group_id: elasticacheTargetReplicationGroupId.trim() || undefined,
          transit_encryption_enabled: elasticacheTransitEncryptionEnabled,
        }
      : action === 'lifecycle policy'
      ? {
          policy_name: ebsLifecyclePolicyName.trim(),
          interval_hours: Number(ebsLifecycleIntervalHours),
          retain_count: Number(ebsLifecycleRetainCount),
          tag_key: ebsLifecycleTagKey.trim(),
          tag_value: ebsLifecycleTagValue.trim(),
          role_arn: ebsLifecycleRoleArn.trim(),
        }
      : action === 'Modify performance mode'
      ? {
          performance_mode: efsPerformanceMode,
          copy_data: efsCopyData,
          delete_old: efsDeleteOld,
        }
      : action === 'Modify throughput mode'
      ? {
          throughput_mode: efsThroughputMode,
          provisioned_throughput: efsThroughputMode === 'provisioned' ? Number(efsProvisionedThroughput) : undefined,
        }
      : undefined;

  const handleRun = async () => {
    if (!canSubmit) return;
    if (action === 'Add policy - Add archival transition' && !archivalDays.trim()) {
      setResult({ status: 'error', message: 'Please enter archival days.' });
      return;
    }
    if (action === 'Add policy - Add expiration for old objects' && !expirationDays.trim()) {
      setResult({ status: 'error', message: 'Please enter expiration days.' });
      return;
    }
    if (action === 'Add policy - Expire noncurrent versions' && !noncurrentExpirationDays.trim()) {
      setResult({ status: 'error', message: 'Please enter noncurrent expiration days.' });
      return;
    }
    if (action === 'Add policy - Add noncurrent archival transition' && !archivalDays.trim()) {
      setResult({ status: 'error', message: 'Please enter transition days.' });
      return;
    }
    if (action === 'Migrate to Graviton' && !targetInstanceClass) {
      setResult({ status: 'error', message: 'Please select a target instance class.' });
      return;
    }
    if (action === 'Use Provisioned Capacity') {
      const rcu = Number(provisionedReadCapacity);
      const wcu = Number(provisionedWriteCapacity);
      if (!provisionedReadCapacity.trim() || !provisionedWriteCapacity.trim()) {
        setResult({ status: 'error', message: 'Please enter read and write capacity units.' });
        return;
      }
      if (!Number.isFinite(rcu) || !Number.isFinite(wcu) || rcu <= 0 || wcu <= 0) {
        setResult({ status: 'error', message: 'Capacity units must be positive numbers.' });
        return;
      }
    }
    if (action === 'Reduce provisioned IOPS' || action === 'Reduce provisioned IOPS or migrate to gp3') {
      const iops = Number(ebsReduceIops);
      if (!ebsReduceIops.trim()) {
        setResult({ status: 'error', message: 'Please enter IOPS.' });
        return;
      }
      if (!Number.isFinite(iops) || iops <= 0) {
        setResult({ status: 'error', message: 'IOPS must be a positive number.' });
        return;
      }
    }
    if (
      action === 'Downsize volume' ||
      action === 'Modify volume type to gp3; tune IOPS/throughput' ||
      action === 'Downsize volume; consider filesystem resize + snapshot/restore'
    ) {
      if (!ebsDownsizeIops.trim() && !ebsDownsizeThroughput.trim() && !ebsDownsizeVolumeType.trim()) {
        setResult({ status: 'error', message: 'Provide IOPS, throughput, or volume type to downsize.' });
        return;
      }
    }
    if (action === 'lifecycle policy') {
      const interval = Number(ebsLifecycleIntervalHours);
      const retain = Number(ebsLifecycleRetainCount);
      if (!ebsLifecyclePolicyName.trim()) {
        setResult({ status: 'error', message: 'Please enter a policy name.' });
        return;
      }
      if (!ebsLifecycleRoleArn.trim()) {
        setResult({ status: 'error', message: 'Please enter a role ARN.' });
        return;
      }
      if (!Number.isFinite(interval) || interval <= 0) {
        setResult({ status: 'error', message: 'Interval hours must be a positive number.' });
        return;
      }
      if (!Number.isFinite(retain) || retain <= 0) {
        setResult({ status: 'error', message: 'Retain count must be a positive number.' });
        return;
      }
      if (!ebsLifecycleTagKey.trim() || !ebsLifecycleTagValue.trim()) {
        setResult({ status: 'error', message: 'Please enter both tag key and tag value.' });
        return;
      }
    }
    if (action === 'Downsize' || action === 'Migrate to Graviton node type') {
      if (!elasticacheTargetNodeType.trim()) {
        setResult({ status: 'error', message: 'Please enter a target node type.' });
        return;
      }
      if (elasticacheNodeCount.trim()) {
        const nodeCount = Number(elasticacheNodeCount);
        if (!Number.isFinite(nodeCount) || nodeCount <= 0) {
          setResult({ status: 'error', message: 'Node count must be a positive number.' });
          return;
        }
      }
    }
    if (action === 'Upgrade to Valkey' && elasticacheEngineVersion.trim()) {
      if (!/^[0-9]+(\\.[0-9]+)*$/.test(elasticacheEngineVersion.trim())) {
        setResult({ status: 'error', message: 'Engine version must be a dot-separated number (e.g., 7.1).' });
        return;
      }
    }
    if (action === 'Modify throughput mode' && efsThroughputMode === 'provisioned') {
      if (!efsProvisionedThroughput.trim()) {
        setResult({ status: 'error', message: 'Please enter provisioned throughput in MiB/s.' });
        return;
      }
      const throughput = Number(efsProvisionedThroughput);
      if (!Number.isFinite(throughput) || throughput < 1) {
        setResult({ status: 'error', message: 'Provisioned throughput must be at least 1 MiB/s.' });
        return;
      }
    }
    setIsSubmitting(true);
    setResult(null);
    try {
      const parameters = buildParameters();
      const response =
        hideTargetInputs && onRunBulk && bulkResourceIds && bulkResourceIds.length > 0
          ? await onRunBulk({
              action,
              resource_ids: bulkResourceIds,
              parameters,
            })
          : await onRun({
              action,
              account_id: accountId.trim(),
              region: trimmedRegion,
              resource_id: trimmedResourceId,
              parameters,
            });
      setResult(response);
    } catch (error: any) {
      const message = error?.response?.data?.detail || error?.message || 'Failed to run test action.';
      console.error('Test action failed:', {
        action,
        accountId: accountId.trim(),
        region: region.trim(),
        resourceId: resourceId.trim(),
        message,
        error,
      });
      setResult({ status: 'error', message });
    } finally {
      setIsSubmitting(false);
    }
  };

  return (
    <Modal isOpen={isOpen} onClose={onClose} title={title} size="lg">
      <div className="space-y-4">
        {actionOptions.length === 0 ? (
          <div className="text-sm text-gray-600 dark:text-gray-400">
            No test actions are available for this check.
          </div>
        ) : (
          <>
            {resourceSummary && (
              <div className="rounded-lg border border-gray-200 dark:border-gray-700 p-3 text-sm">
                {resourceSummary}
              </div>
            )}
            <div>
              <label className="block text-sm font-medium text-gray-700 dark:text-gray-300 mb-2">
                Action
              </label>
              <select
                value={action}
                onChange={(e) => setAction(e.target.value)}
                className="input"
              >
                {actionOptions.map((item) => (
                  <option key={item.value} value={item.value}>
                    {item.label}
                  </option>
                ))}
              </select>
            </div>
            <div className={`rounded-lg border p-3 text-sm ${actionImpactStyles.panel}`}>
              <div className={`font-semibold ${actionImpactStyles.title}`}>
                {actionImpact.tag}
              </div>
              <div className={`mt-1 ${actionImpactStyles.helper}`}>
                {actionImpact.summary}
              </div>
              {actionImpact.warning && (
                <div className={`mt-2 ${actionImpactStyles.helper}`}>
                  Warning: {actionImpact.warning}
                </div>
              )}
              {actionImpact.acknowledgmentRequired && (
                <label className={`mt-3 flex items-start gap-2 text-sm ${actionImpactStyles.checkbox}`}>
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

            {!hideTargetInputs && (
              <>
                <div className="grid grid-cols-2 gap-4">
                  <div>
                    <label className="block text-sm font-medium text-gray-700 dark:text-gray-300 mb-2">
                      Account ID
                    </label>
                    <Input value={accountId} onChange={(e) => setAccountId(e.target.value)} />
                  </div>
                  <div>
                    <label className="block text-sm font-medium text-gray-700 dark:text-gray-300 mb-2">
                      Region
                    </label>
                    <Input value={region} onChange={(e) => setRegion(e.target.value)} />
                  </div>
                </div>

                <div>
                  <label className="block text-sm font-medium text-gray-700 dark:text-gray-300 mb-2">
                    {check.resource_type === 'ec2' ? 'Instance ID' : 'Resource ID'}
                  </label>
                  <Input
                    value={resourceId}
                    onChange={(e) => setResourceId(e.target.value)}
                    disabled={resourceIdReadOnly}
                  />
                </div>
              </>
            )}
            {action === 'Migrate to Graviton' && (
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
                  {shouldLoadRdsClasses &&
                    !rdsClassLoading &&
                    rdsClassOptions?.graviton_instance_classes?.length === 0 && (
                      <div className="text-xs text-gray-600 dark:text-gray-400 mt-2">
                        No Graviton classes returned for this instance.
                      </div>
                    )}
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
            {action === 'Add policy - Add archival transition' && (
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
            {action === 'Add policy - Add expiration for old objects' && (
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
            {action === 'Add policy - Expire noncurrent versions' && (
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
            {action === 'Add policy - Add noncurrent archival transition' && (
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
            {action === 'Use Provisioned Capacity' && (
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
            {(action === 'Reduce provisioned IOPS' || action === 'Reduce provisioned IOPS or migrate to gp3') && (
              <div className="space-y-3">
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
                </div>
              </div>
            )}
            {(
              action === 'Downsize volume' ||
              action === 'Modify volume type to gp3; tune IOPS/throughput' ||
              action === 'Downsize volume; consider filesystem resize + snapshot/restore'
            ) && (
              <div className="space-y-3">
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
                  </div>
                  <div>
                    <label className="block text-sm font-medium text-gray-700 dark:text-gray-300 mb-2">
                      Target volume type
                    </label>
                    <Input
                      value={ebsDownsizeVolumeType}
                      onChange={(e) => setEbsDownsizeVolumeType(e.target.value)}
                      placeholder="gp3"
                    />
                  </div>
                </div>
              </div>
            )}
            {action === 'lifecycle policy' && (
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
            {(action === 'Downsize' || action === 'Migrate to Graviton node type') && (
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
            {action === 'Upgrade to Valkey' && (
              <div className="space-y-3">
                <div className="text-sm text-gray-600 dark:text-gray-400">
                  Snapshot-based migration can take several minutes. Validate the new Valkey group before deleting the
                  original Redis group.
                </div>
                <div className="rounded-md border border-warning-200 bg-warning-50 px-3 py-2 text-sm text-warning-800 dark:border-warning-700/60 dark:bg-warning-950/30 dark:text-warning-100">
                  If the resource is a replication group, upgrading to Valkey creates a new replication group from a
                  snapshot. The original Redis group remains until you delete it.
                </div>
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
            {action === 'Modify performance mode' && (
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
            {action === 'Modify throughput mode' && (
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
          </>
        )}

        {result && (
          <div className="rounded-lg border border-gray-200 dark:border-gray-700 p-3 text-sm">
            <div className="font-semibold text-gray-900 dark:text-white">Result</div>
            <div className="text-gray-700 dark:text-gray-300 mt-1">{result.message}</div>
            {result.details && (
              <pre className="mt-2 text-xs text-gray-600 dark:text-gray-400 whitespace-pre-wrap">
                {JSON.stringify(result.details, null, 2)}
              </pre>
            )}
          </div>
        )}

        <div className="flex justify-end space-x-2 pt-2">
          <Button variant="secondary" onClick={onClose}>
            Close
          </Button>
          {actionOptions.length > 0 && (
            <Button
              variant="primary"
              onClick={handleRun}
              disabled={!canSubmit || isSubmitting || (actionImpact.acknowledgmentRequired && !impactAcknowledged)}
            >
              {isSubmitting ? 'Running...' : runLabel}
            </Button>
          )}
        </div>
      </div>
    </Modal>
  );
};
