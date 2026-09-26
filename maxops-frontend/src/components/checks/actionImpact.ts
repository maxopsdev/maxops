export type ActionImpactLevel = 'safe' | 'low' | 'caution' | 'high';

export interface ActionImpactInfo {
  level: ActionImpactLevel;
  tag: string;
  summary: string;
  warning?: string;
  acknowledgmentRequired: boolean;
}

const ACTION_IMPACT_MAP: Record<string, ActionImpactInfo> = {
  'Stop': {
    level: 'caution',
    tag: 'Caution',
    summary: 'Temporarily interrupts the workload until it is started again.',
    warning: 'Confirm the instance or database is not serving active traffic before stopping it.',
    acknowledgmentRequired: true,
  },
  'Terminate': {
    level: 'high',
    tag: 'High Impact',
    summary: 'Deletes the compute resource and can permanently remove service capacity.',
    warning: 'This can cause service outage and may be difficult to recover without backups.',
    acknowledgmentRequired: true,
  },
  'Terminate with snapshot': {
    level: 'high',
    tag: 'High Impact',
    summary: 'Creates a recovery snapshot, then deletes the running resource.',
    warning: 'The snapshot helps recovery, but the running resource will still be terminated.',
    acknowledgmentRequired: true,
  },
  'Terminate but leave the volume': {
    level: 'high',
    tag: 'High Impact',
    summary: 'Deletes the instance but keeps the underlying volume for later recovery.',
    warning: 'Compute is removed immediately even though the volume is preserved.',
    acknowledgmentRequired: true,
  },
  'Delete table': {
    level: 'high',
    tag: 'High Impact',
    summary: 'Permanently removes the DynamoDB table and its data.',
    warning: 'Do not proceed unless the table is fully retired or recoverable from backup.',
    acknowledgmentRequired: true,
  },
  'Backup and delete table': {
    level: 'high',
    tag: 'High Impact',
    summary: 'Creates a backup, then deletes the DynamoDB table.',
    warning: 'A backup exists, but production reads and writes will still stop after deletion.',
    acknowledgmentRequired: true,
  },
  'Switch to On-Demand billing': {
    level: 'low',
    tag: 'Low Impact',
    summary: 'Changes billing mode with little direct availability risk.',
    warning: 'Costs can increase if traffic is steady and predictable.',
    acknowledgmentRequired: false,
  },
  'Reduce RCU': {
    level: 'caution',
    tag: 'Caution',
    summary: 'Reduces read capacity and may throttle read-heavy workloads.',
    warning: 'Validate read traffic patterns and autoscaling coverage before reducing capacity.',
    acknowledgmentRequired: true,
  },
  'Reduce WCU': {
    level: 'caution',
    tag: 'Caution',
    summary: 'Reduces write capacity and may throttle write-heavy workloads.',
    warning: 'Validate write peaks and retry behavior before reducing capacity.',
    acknowledgmentRequired: true,
  },
  'Enable autoscaling': {
    level: 'safe',
    tag: 'Safe',
    summary: 'Adds adaptive scaling to reduce throttling risk as usage changes.',
    acknowledgmentRequired: false,
  },
  'Use Provisioned Capacity': {
    level: 'caution',
    tag: 'Caution',
    summary: 'Changes billing and requires selecting enough throughput for the workload.',
    warning: 'Under-sizing provisioned capacity can introduce throttling.',
    acknowledgmentRequired: true,
  },
  'Delete GSI': {
    level: 'high',
    tag: 'High Impact',
    summary: 'Removes the index and breaks queries that depend on it.',
    warning: 'Confirm the index is unused by all applications, jobs, and dashboards.',
    acknowledgmentRequired: true,
  },
  'Downsize': {
    level: 'caution',
    tag: 'Caution',
    summary: 'Reduces cache node size or count to lower cost.',
    warning: 'Validate memory headroom, failover posture, and eviction risk before downsizing.',
    acknowledgmentRequired: true,
  },
  'Delete instance': {
    level: 'high',
    tag: 'High Impact',
    summary: 'Deletes the cache resource and removes in-memory data capacity.',
    warning: 'This can disrupt applications immediately if the cache is still in use.',
    acknowledgmentRequired: true,
  },
  'Upgrade to Valkey': {
    level: 'caution',
    tag: 'Caution',
    summary: 'Performs an engine migration that may change behavior and cutover flow.',
    warning: 'Validate client compatibility, snapshots, and rollback steps before migrating.',
    acknowledgmentRequired: true,
  },
  'Migrate to Graviton node type': {
    level: 'caution',
    tag: 'Caution',
    summary: 'Moves the cache workload to a different instance family.',
    warning: 'Confirm engine support, parameter compatibility, and maintenance expectations.',
    acknowledgmentRequired: true,
  },
  'Add lifecycle policy': {
    level: 'low',
    tag: 'Low Impact',
    summary: 'Adds an automated retention or tiering policy to reduce storage cost.',
    warning: 'Review retention timing so data is not archived or expired earlier than intended.',
    acknowledgmentRequired: false,
  },
  'Add policy - Abort incomplete MPUs': {
    level: 'safe',
    tag: 'Safe',
    summary: 'Cleans up incomplete multipart uploads with minimal application risk.',
    acknowledgmentRequired: false,
  },
  'Add policy - Expire noncurrent versions': {
    level: 'low',
    tag: 'Low Impact',
    summary: 'Deletes older object versions after a retention window.',
    warning: 'Confirm version retention requirements before expiring historical copies.',
    acknowledgmentRequired: false,
  },
  'Add policy - Add expiration for old objects': {
    level: 'caution',
    tag: 'Caution',
    summary: 'Automatically deletes older objects after the configured age.',
    warning: 'Make sure no compliance, audit, or recovery requirement depends on long-lived objects.',
    acknowledgmentRequired: true,
  },
  'Add policy - Add archival transition': {
    level: 'low',
    tag: 'Low Impact',
    summary: 'Moves older objects to colder storage to reduce cost.',
    warning: 'Retrieval can become slower and more expensive after transition.',
    acknowledgmentRequired: false,
  },
  'Add policy - Enable delete-marker cleanup': {
    level: 'safe',
    tag: 'Safe',
    summary: 'Removes expired delete markers with low operational risk.',
    acknowledgmentRequired: false,
  },
  'Disable Inventory Report and delete the log bucket': {
    level: 'high',
    tag: 'High Impact',
    summary: 'Turns off inventory reporting and removes the destination bucket.',
    warning: 'Verify no audit, reporting, or downstream process depends on the inventory output.',
    acknowledgmentRequired: true,
  },
  'Disable Logging Report and delete the log bucket': {
    level: 'high',
    tag: 'High Impact',
    summary: 'Turns off S3 access logging and removes the destination bucket.',
    warning: 'This removes future access logs and may affect security or audit workflows.',
    acknowledgmentRequired: true,
  },
  'Disable replication and delete the replicate bucket': {
    level: 'high',
    tag: 'High Impact',
    summary: 'Disables replication and removes the replicated bucket.',
    warning: 'Confirm disaster recovery and cross-region access requirements before proceeding.',
    acknowledgmentRequired: true,
  },
  'Add policy - log retention lifecycle': {
    level: 'low',
    tag: 'Low Impact',
    summary: 'Adds retention rules to log storage.',
    warning: 'Make sure the retention period still satisfies audit requirements.',
    acknowledgmentRequired: false,
  },
  'Create S3 Gateway Endpoint': {
    level: 'low',
    tag: 'Low Impact',
    summary: 'Adds a private network path to S3 inside the VPC.',
    warning: 'Review route table scope and endpoint policy before rollout.',
    acknowledgmentRequired: false,
  },
  'Create DynamoDB Gateway Endpoint': {
    level: 'low',
    tag: 'Low Impact',
    summary: 'Adds a private network path to DynamoDB inside the VPC.',
    warning: 'Review route table scope and endpoint policy before rollout.',
    acknowledgmentRequired: false,
  },
  'Disable flow logs and delete bucket': {
    level: 'high',
    tag: 'High Impact',
    summary: 'Stops network flow logging and removes stored log data.',
    warning: 'This can reduce forensic visibility and may violate security monitoring requirements.',
    acknowledgmentRequired: true,
  },
  'Tune/dedupe noisy alarms': {
    level: 'low',
    tag: 'Low Impact',
    summary: 'Refines alarm behavior to reduce alert noise.',
    warning: 'Confirm the change will not hide a needed signal.',
    acknowledgmentRequired: false,
  },
  'Reduce log ingest': {
    level: 'caution',
    tag: 'Caution',
    summary: 'Reduces log volume to save cost.',
    warning: 'Lower log volume can reduce troubleshooting and audit detail.',
    acknowledgmentRequired: true,
  },
  'Consolidate duplicates': {
    level: 'low',
    tag: 'Low Impact',
    summary: 'Removes redundant alarms or alert paths.',
    warning: 'Verify that at least one valid alert path remains.',
    acknowledgmentRequired: false,
  },
  'Reduce retention or delete': {
    level: 'caution',
    tag: 'Caution',
    summary: 'Shortens retention or removes an unused log group.',
    warning: 'Deleting logs can remove historical evidence needed for debugging or compliance.',
    acknowledgmentRequired: true,
  },
  'Upgrade GlueVersion': {
    level: 'caution',
    tag: 'Caution',
    summary: 'Changes the Glue runtime version.',
    warning: 'Validate job compatibility and test scripts before upgrading.',
    acknowledgmentRequired: true,
  },
  'Snapshot and Terminate': {
    level: 'high',
    tag: 'High Impact',
    summary: 'Creates a snapshot, then deletes the volume resource.',
    warning: 'The snapshot preserves data, but the live volume will be removed.',
    acknowledgmentRequired: true,
  },
  'lifecycle policy': {
    level: 'low',
    tag: 'Low Impact',
    summary: 'Creates an automated snapshot lifecycle policy.',
    warning: 'Review retention settings and IAM role scope before enabling it.',
    acknowledgmentRequired: false,
  },
  'Downsize volume': {
    level: 'caution',
    tag: 'Caution',
    summary: 'Reduces storage performance or capacity assumptions to cut cost.',
    warning: 'Volume and filesystem resizing can affect performance and recovery workflow.',
    acknowledgmentRequired: true,
  },
  'Reduce provisioned IOPS': {
    level: 'caution',
    tag: 'Caution',
    summary: 'Lowers provisioned disk performance to save cost.',
    warning: 'Confirm latency and throughput headroom before reducing IOPS.',
    acknowledgmentRequired: true,
  },
  'Modify volume type to gp3; tune IOPS/throughput': {
    level: 'caution',
    tag: 'Caution',
    summary: 'Changes volume type and performance settings.',
    warning: 'Review performance baselines and maintenance expectations before changing storage settings.',
    acknowledgmentRequired: true,
  },
  'Reduce provisioned IOPS or migrate to gp3': {
    level: 'caution',
    tag: 'Caution',
    summary: 'Lowers disk performance or changes the backing volume type.',
    warning: 'Validate workload performance and storage compatibility before proceeding.',
    acknowledgmentRequired: true,
  },
  'Downsize volume; consider filesystem resize + snapshot/restore': {
    level: 'high',
    tag: 'High Impact',
    summary: 'May require filesystem work plus snapshot and restore steps to complete safely.',
    warning: 'This needs careful planning because a storage resize mistake can cause data loss or downtime.',
    acknowledgmentRequired: true,
  },
  'Migrate to Graviton': {
    level: 'caution',
    tag: 'Caution',
    summary: 'Moves the workload to a different compute architecture or instance family.',
    warning: 'Validate compatibility, maintenance behavior, and rollback steps before migration.',
    acknowledgmentRequired: true,
  },
  'Delete snapshot': {
    level: 'high',
    tag: 'High Impact',
    summary: 'Permanently removes a recovery snapshot.',
    warning: 'Do not delete the snapshot unless other recovery points are confirmed.',
    acknowledgmentRequired: true,
  },
  'Delete file system': {
    level: 'high',
    tag: 'High Impact',
    summary: 'Permanently removes the file system and its data path.',
    warning: 'Confirm all mounts, applications, and backup requirements are cleared first.',
    acknowledgmentRequired: true,
  },
  'Modify performance mode': {
    level: 'high',
    tag: 'High Impact',
    summary: 'Creates a replacement EFS file system and may require data migration.',
    warning: 'This is a multi-step migration that can affect access paths and cutover timing.',
    acknowledgmentRequired: true,
  },
  'Modify throughput mode': {
    level: 'caution',
    tag: 'Caution',
    summary: 'Changes EFS throughput behavior and cost profile.',
    warning: 'Provisioned settings that are too low can affect throughput-sensitive workloads.',
    acknowledgmentRequired: true,
  },
  'Rightsize memory using duration vs memory analysis': {
    level: 'low',
    tag: 'Low Impact',
    summary: 'Adjusts Lambda memory using observed execution data.',
    warning: 'Retest latency-sensitive paths because memory changes also affect CPU allocation.',
    acknowledgmentRequired: false,
  },
  'Reduce/disable provisioned concurrency': {
    level: 'caution',
    tag: 'Caution',
    summary: 'Reduces warm capacity and may increase cold starts.',
    warning: 'Confirm acceptable latency before lowering provisioned concurrency.',
    acknowledgmentRequired: true,
  },
  'Reduce log verbosity; set retention': {
    level: 'caution',
    tag: 'Caution',
    summary: 'Reduces logging detail and retention to save cost.',
    warning: 'Make sure you are not removing operational or audit evidence your teams still need.',
    acknowledgmentRequired: true,
  },
  'Delete service; clean up ALB/TG/log groups/roles': {
    level: 'high',
    tag: 'High Impact',
    summary: 'Removes the ECS service and associated supporting resources.',
    warning: 'This can cause immediate outage if any workload still depends on the service path.',
    acknowledgmentRequired: true,
  },
  'Rightsize task definition based on utilization': {
    level: 'caution',
    tag: 'Caution',
    summary: 'Changes CPU and memory reservations for the ECS task.',
    warning: 'Under-sizing task reservations can cause throttling, OOM kills, or scaling instability.',
    acknowledgmentRequired: true,
  },
  'Delete cluster after verifying no dependencies': {
    level: 'high',
    tag: 'High Impact',
    summary: 'Deletes the ECS cluster after dependency cleanup.',
    warning: 'Proceed only after confirming no active services, tasks, capacity providers, or automation depend on it.',
    acknowledgmentRequired: true,
  },
};

const IMPACT_STYLES: Record<
  ActionImpactLevel,
  {
    badge: string;
    panel: string;
    title: string;
    helper: string;
    checkbox: string;
  }
> = {
  safe: {
    badge: 'border-success-200 bg-success-50 text-success-700 dark:border-success-700/60 dark:bg-success-950/30 dark:text-success-200',
    panel: 'border-success-200 bg-success-50 dark:border-success-700/60 dark:bg-success-950/30',
    title: 'text-success-900 dark:text-success-100',
    helper: 'text-success-800 dark:text-success-200',
    checkbox: 'text-success-800 dark:text-success-200',
  },
  low: {
    badge: 'border-primary-200 bg-primary-50 text-primary-700 dark:border-primary-700/60 dark:bg-primary-950/30 dark:text-primary-200',
    panel: 'border-primary-200 bg-primary-50 dark:border-primary-700/60 dark:bg-primary-950/30',
    title: 'text-primary-900 dark:text-primary-100',
    helper: 'text-primary-800 dark:text-primary-200',
    checkbox: 'text-primary-800 dark:text-primary-200',
  },
  caution: {
    badge: 'border-warning-200 bg-warning-50 text-warning-700 dark:border-warning-700/60 dark:bg-warning-950/30 dark:text-warning-200',
    panel: 'border-warning-200 bg-warning-50 dark:border-warning-700/60 dark:bg-warning-950/30',
    title: 'text-warning-900 dark:text-warning-100',
    helper: 'text-warning-800 dark:text-warning-200',
    checkbox: 'text-warning-900 dark:text-warning-100',
  },
  high: {
    badge: 'border-danger-200 bg-danger-50 text-danger-700 dark:border-danger-700/60 dark:bg-danger-950/30 dark:text-danger-200',
    panel: 'border-danger-200 bg-danger-50 dark:border-danger-700/60 dark:bg-danger-950/30',
    title: 'text-danger-900 dark:text-danger-100',
    helper: 'text-danger-800 dark:text-danger-200',
    checkbox: 'text-danger-900 dark:text-danger-100',
  },
};

const DEFAULT_IMPACT: ActionImpactInfo = {
  level: 'caution',
  tag: 'Caution',
  summary: 'This action changes live cloud configuration and should be reviewed before execution.',
  warning: 'Confirm dependencies, rollback options, and blast radius before proceeding.',
  acknowledgmentRequired: true,
};

export const getActionImpact = (action?: string | null): ActionImpactInfo => {
  if (!action) {
    return DEFAULT_IMPACT;
  }
  return ACTION_IMPACT_MAP[action] || DEFAULT_IMPACT;
};

export const formatActionLabelWithImpact = (action: string): string => {
  const impact = getActionImpact(action);
  return `${action} [${impact.tag}]`;
};

export const getActionImpactStyles = (level: ActionImpactLevel) => IMPACT_STYLES[level];
