export interface IamPolicyStatement {
  Sid: string;
  Effect: 'Allow';
  Action: string[];
  Resource: string | string[];
}

export interface IamPolicyDocument {
  Version: '2012-10-17';
  Statement: IamPolicyStatement[];
}

export const READ_ONLY_IAM_POLICY_NAME = 'MaxOpsReadOnlyScanPolicy';
export const READ_ONLY_IAM_POLICY_FILENAME = 'maxops-read-only-scan-policy.json';
export const READ_ONLY_IAM_ROLE_NAME = 'MaxOpsReadOnlyRole';

export const readOnlyIamPolicy: IamPolicyDocument = {
  Version: '2012-10-17',
  Statement: [
    {
      Sid: 'IdentityAndPricingRead',
      Effect: 'Allow',
      Action: [
        'pricing:GetProducts',
        'sts:GetCallerIdentity',
      ],
      Resource: '*',
    },
    {
      Sid: 'Ec2EbsVpcRead',
      Effect: 'Allow',
      Action: [
        'ec2:DescribeAddresses',
        'ec2:DescribeFlowLogs',
        'ec2:DescribeInstances',
        'ec2:DescribeSnapshots',
        'ec2:DescribeVolumes',
        'ec2:DescribeVpcEndpoints',
        'ec2:DescribeVpcs',
      ],
      Resource: '*',
    },
    {
      Sid: 'CloudWatchAndLogsRead',
      Effect: 'Allow',
      Action: [
        'cloudwatch:DescribeAlarmHistory',
        'cloudwatch:DescribeAlarms',
        'cloudwatch:GetMetricData',
        'cloudwatch:GetMetricStatistics',
        'cloudwatch:ListMetrics',
        'logs:DescribeLogGroups',
        'logs:FilterLogEvents',
      ],
      Resource: '*',
    },
    {
      Sid: 'DatabaseRead',
      Effect: 'Allow',
      Action: [
        'rds:DescribeDBClusterSnapshots',
        'rds:DescribeDBClusters',
        'rds:DescribeDBInstances',
        'rds:DescribeDBParameters',
        'rds:DescribeEvents',
        'rds:DescribeGlobalClusters',
        'rds:DescribeOrderableDBInstanceOptions',
        'rds:DescribePendingMaintenanceActions',
        'rds:DescribeValidDBInstanceModifications',
        'rds:ListTagsForResource',
      ],
      Resource: '*',
    },
    {
      Sid: 'PerformanceInsightsOptionalRead',
      Effect: 'Allow',
      Action: ['pi:GetResourceMetrics'],
      Resource: '*',
    },
    {
      Sid: 'S3AccountBucketList',
      Effect: 'Allow',
      Action: [
        's3:ListAllMyBuckets',
      ],
      Resource: '*',
    },
    {
      Sid: 'S3BucketConfigurationRead',
      Effect: 'Allow',
      Action: [
        's3:GetBucketLocation',
        's3:GetBucketLogging',
        's3:GetBucketTagging',
        's3:GetBucketVersioning',
        's3:GetInventoryConfiguration',
        's3:GetLifecycleConfiguration',
        's3:GetReplicationConfiguration',
        's3:ListBucket',
      ],
      Resource: 'arn:aws:s3:::*',
    },
    {
      Sid: 'ComputeInventoryRead',
      Effect: 'Allow',
      Action: [
        'autoscaling:DescribeAutoScalingGroups',
        'autoscaling:DescribeInstanceRefreshes',
        'autoscaling:DescribeLaunchConfigurations',
        'autoscaling:DescribePolicies',
        'autoscaling:DescribeScalingActivities',
        'autoscaling:DescribeScheduledActions',
        'autoscaling:DescribeWarmPool',
        'ec2:DescribeLaunchTemplateVersions',
        'ecs:DescribeClusters',
        'ecs:DescribeServices',
        'ecs:DescribeTaskDefinition',
        'ecs:ListClusters',
        'ecs:ListServices',
        'lambda:GetProvisionedConcurrencyConfig',
        'lambda:ListAliases',
        'lambda:ListFunctions',
        'lambda:ListTags',
      ],
      Resource: '*',
    },
    {
      Sid: 'DataServicesRead',
      Effect: 'Allow',
      Action: [
        'athena:GetWorkGroup',
        'athena:ListWorkGroups',
        'dynamodb:DescribeTable',
        'dynamodb:ListTables',
        'firehose:DescribeDeliveryStream',
        'firehose:ListDeliveryStreams',
        'glue:GetDatabases',
        'glue:GetJob',
        'glue:GetJobRuns',
        'glue:GetJobs',
        'glue:GetPartitions',
        'glue:GetTables',
        'glue:ListJobs',
        'kinesis:DescribeStreamSummary',
        'kinesis:ListStreams',
      ],
      Resource: '*',
    },
    {
      Sid: 'AnalyticsAndStorageRead',
      Effect: 'Allow',
      Action: [
        'elasticache:DescribeCacheClusters',
        'elasticache:DescribeCacheParameterGroups',
        'elasticache:DescribeCacheParameters',
        'elasticache:DescribeReplicationGroups',
        'elasticache:ListAllowedNodeTypeModifications',
        'application-autoscaling:DescribeScalableTargets',
        'elasticfilesystem:DescribeFileSystems',
        'elasticfilesystem:DescribeLifecycleConfiguration',
        'elasticfilesystem:DescribeMountTargets',
        'elasticfilesystem:DescribeTags',
        'elasticmapreduce:DescribeCluster',
        'elasticmapreduce:ListClusters',
        'elasticmapreduce:ListInstanceFleets',
        'elasticmapreduce:ListInstanceGroups',
        'redshift:DescribeClusterSnapshots',
        'redshift:DescribeClusters',
      ],
      Resource: '*',
    },
    {
      Sid: 'OpenSearchRead',
      Effect: 'Allow',
      Action: [
        'es:DescribeDomain',
        'es:ESHttpGet',
        'es:ListDomainNames',
      ],
      Resource: '*',
    },
  ],
};

export const readOnlyIamPolicyJson = JSON.stringify(readOnlyIamPolicy, null, 2);

export const readOnlyIamPermissionGroups = readOnlyIamPolicy.Statement.map((statement) => ({
  sid: statement.Sid,
  actions: statement.Action,
  resource: statement.Resource,
}));

export const readOnlyIamActionCount = new Set(
  readOnlyIamPolicy.Statement.flatMap((statement) => statement.Action)
).size;
