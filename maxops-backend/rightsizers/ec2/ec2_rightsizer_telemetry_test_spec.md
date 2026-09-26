# EC2 Rightsizer Live Telemetry Test Specification

Status: implementation contract for a future live AWS test harness. This file
does not authorize a normal unit-test run to create AWS resources.

## 1. Purpose and boundary

This test proves that the production EC2 telemetry collectors send the intended
CloudWatch API requests and can parse recently published datapoints from a real
EC2 instance. It validates metric namespace, name, dimensions, statistic,
period, request pagination, response status, timestamp/value pairing, and the
raw inputs used by local normalization.

The live fixture is deliberately short lived. It does not prove 14-, 30-, or
60-day percentile behavior, sparse-history policy, 15-month trend correctness,
or recommendation quality. Those contracts remain covered by offline unit and
payload tests.

The implementation must be explicitly gated as an AWS integration tool. It
must never be included in broad pytest discovery.

## 2. Implemented entrypoints and defaults

The EC2 folder owns three entrypoints backed by shared helpers under
`rightsizers/common`:

```text
python rightsizers/ec2/create_telemetry_test.py --apply [--run-id ID]
python rightsizers/ec2/validate_telemetry_test.py --state PATH [--window-minutes 30]
python rightsizers/ec2/cleanup_telemetry_test.py --state PATH
```

Defaults are the repository AWS profile `default`, region `us-east-1`, and a
subnet in the default VPC. The create command records the STS account ID and
caller ARN before mutation and prints the account, region, resource plan, and
cleanup command. It must require an explicit apply/confirmation flag. It must
not silently select a different profile, region, VPC, or account.

Creation waits for the instance, EC2 status checks, SSM, and CloudWatch Agent
to become ready. It then submits the bounded workload and returns without
waiting for CloudWatch publication. Validation is one shot: it does not sleep,
poll, retry, or clean up.

IAM reporting an instance profile does not prove that EC2 can consume it yet.
Treat `Invalid IAM Instance Profile name` from `RunInstances` as eventual
consistency and retry for a bounded two-minute window before failing.

The CloudWatch Agent `ethtool` block inherits the agent's global 60-second
collection interval. Do not set `metrics_collection_interval` inside that
block; the current agent schema rejects that property.

Exit/result states are:

| Result | Meaning |
| --- | --- |
| `PASS` | Resource is ready and every applicable request/data assertion passed |
| `NOT_READY` | Instance, agent, or workload is not ready; rerun validation later |
| `FAIL` | Request contract, API response, applicability, or datapoint assertion failed |

`NOT_READY` is not used to hide a metric mismatch once the instance, agent, and
workload are ready.

## 3. Fixture

Create one current-generation, Nitro-based, EBS-burst-capable T-family Linux
instance. The default should be the lowest-cost type in `us-east-1` that the
preflight proves supports the required architecture, SSM image, ENA, and
instance-level EBS metrics. Enable detailed monitoring and use a small EBS root
volume. If no compatible type or default-VPC subnet is available, fail preflight
before creating anything.

Create and track the minimum supporting resources:

- IAM role and instance profile for SSM and CloudWatch Agent publication;
- the EC2 instance and its owned EBS volumes;
- any security group created by the harness;
- SSM command IDs for agent configuration and workload execution.

Use an AWS-documented CloudWatch Agent configuration:

- namespace `CWAgent`;
- `mem_used_percent` with an exact `InstanceId` rollup;
- the Linux `ethtool` inputs `bw_in_allowance_exceeded`,
  `bw_out_allowance_exceeded`, `pps_allowance_exceeded`, and
  `conntrack_allowance_exceeded`;
- no custom rename that is designed to match the application.

AWS documents that Linux `ethtool` metrics are published with an `ethtool_`
prefix. The validator must list the metrics actually published for the
instance and diagnose an official-name-versus-production-name mismatch. It
must not make the fixture emit nonstandard names merely to pass the test. See
[Collect network performance metrics](https://docs.aws.amazon.com/AmazonCloudWatch/latest/monitoring/CloudWatch-Agent-network-performance.html).

The workload is bounded and non-destructive: moderate CPU work, reads and
writes to a dedicated temporary file on the root volume, and ordinary network
traffic using endpoints already required for SSM/CloudWatch. It must not try to
exhaust EBS, bandwidth, PPS, or connection-tracking allowances. Workload files
are disposable with the instance.

Every owned resource receives these tags where supported:

```text
maxops:test-purpose = rightsizer-telemetry
maxops:rightsizer = ec2
maxops:run-id = <run_id>
maxops:expires-at = <UTC timestamp>
```

Tags are recovery evidence only; cleanup authorization comes from state.

## 4. Decision-scan request contract

Invoke `AWSAdapter.get_ec2_rightsizing_metrics` with the production code, a
300-second period, the state instance ID, and the requested recent window. A
delegating boto3 client records requests and responses without replacing the
real CloudWatch client.

Except for discovered memory, every query has dimension
`InstanceId=<state.instance_id>`. Memory must use the complete namespace, name,
and dimension set returned by production discovery. `ReturnData` is true,
`ScanBy` is `TimestampAscending`, and `MaxDatapoints` is `100800`.

| Alias | Namespace | Metric name | Stat | Applicability and pass criterion |
| --- | --- | --- | --- | --- |
| `cpu_percent` | `AWS/EC2` | `CPUUtilization` | Average | Required; at least one finite value in `[0,100]` |
| `network_in_bytes` | `AWS/EC2` | `NetworkIn` | Sum | Required; finite and nonnegative |
| `network_out_bytes` | `AWS/EC2` | `NetworkOut` | Sum | Required; finite and nonnegative |
| `network_packets_in` | `AWS/EC2` | `NetworkPacketsIn` | Sum | Required; finite and nonnegative |
| `network_packets_out` | `AWS/EC2` | `NetworkPacketsOut` | Sum | Required; finite and nonnegative |
| `ebs_read_operations` | `AWS/EC2` | `EBSReadOps` | Sum | Required on the Nitro fixture; finite and nonnegative |
| `ebs_write_operations` | `AWS/EC2` | `EBSWriteOps` | Sum | Required on the Nitro fixture; finite and nonnegative |
| `ebs_read_bytes` | `AWS/EC2` | `EBSReadBytes` | Sum | Required on the Nitro fixture; finite and nonnegative |
| `ebs_write_bytes` | `AWS/EC2` | `EBSWriteBytes` | Sum | Required on the Nitro fixture; finite and nonnegative |
| `instance_ebs_iops_exceeded` | `AWS/EC2` | `InstanceEBSIOPSExceededCheck` | Maximum | Required on non-bare-metal Nitro; zero is valid |
| `instance_ebs_throughput_exceeded` | `AWS/EC2` | `InstanceEBSThroughputExceededCheck` | Maximum | Required on non-bare-metal Nitro; zero is valid |
| `ebs_io_balance_percent` | `AWS/EC2` | `EBSIOBalance%` | Minimum | Required only when preflight proves the chosen size publishes it; `[0,100]` |
| `ebs_byte_balance_percent` | `AWS/EC2` | `EBSByteBalance%` | Minimum | Required only when preflight proves the chosen size publishes it; `[0,100]` |
| `memory_percent` | discovered | discovered percentage metric | Average | Required because the fixture installs the agent; finite `[0,100]`; exact source persisted |
| `bw_in_allowance_exceeded` | `CWAgent` | `ethtool_bw_in_allowance_exceeded` | Sum | Required from the Linux agent; zero valid |
| `bw_out_allowance_exceeded` | `CWAgent` | `ethtool_bw_out_allowance_exceeded` | Sum | Required from the Linux agent; zero valid |
| `pps_allowance_exceeded` | `CWAgent` | `ethtool_pps_allowance_exceeded` | Sum | Required from the Linux agent; zero valid |
| `conntrack_allowance_exceeded` | `CWAgent` | `ethtool_conntrack_allowance_exceeded` | Sum | Required from the Linux agent; zero valid |

The table is the oracle. If production changes its query set, the live test
fails until the implementation and this specification are deliberately
reconciled.

## 5. Memory discovery and parsing assertions

Record every paginated `ListMetrics` call. Assert that discovery:

- uses the fixture region and associates candidates with its `InstanceId`;
- preserves all dimensions returned for the selected metric;
- selects a finite percentage series and persists its source metadata;
- tries a lower-ranked candidate only when a preferred candidate has no usable
  values;
- never treats an empty, NaN, infinite, negative, or over-100 value as usable.

For every `GetMetricData` response, assert `StatusCode=Complete` for returned
series and fail on response messages that indicate an invalid query. Follow all
`NextToken` values. Merge by query ID, preserve timestamp/value pairing, sort
ascending, and do not synthesize zero for missing timestamps. A legitimate
numeric zero is a datapoint and must remain present.

Pass the returned payload through the production EC2 normalization path and
assert that sample counts are positive for required series, byte/operation
counters are converted using the requested 300-second period, and no normalized
value is derived from an absent raw point. This is a smoke assertion, not a
long-window percentile assertion.

## 6. Confidence-trend smoke contract

Invoke `get_ec2_confidence_trend` using the selected memory source. Record and
assert the request shape for CPU and memory:

- daily period `86400`, stats `Maximum`, `p99`, and `p95`;
- bucket periods of 30, 90, 120, 180, 365, and 455 days, stats `p99` and `p95`;
- CPU uses `AWS/EC2/CPUUtilization` plus `InstanceId`;
- memory reuses the exact discovered source;
- network and EBS metrics are absent.

New resources need not return complete daily or multi-day values. A successful
API response and exact request shape pass the trend smoke section even when its
series are empty.

## 7. State and cleanup

The default state path is
`rightsizers/ec2/.telemetry_test_state/<run_id>.json` and must be gitignored.
State uses the shared schema and contains at least:

```text
schema_version, run_id, rightsizer, profile, account_id, caller_arn, region
created_at, expires_at, phase
resources[]: logical_name, service, type, id_or_arn, ownership, dependencies,
             create_status, ready_status, delete_status
workload_commands[]: command_id, target_ids, submitted_at, terminal_status
validation_runs[]: started_at, window, result, report_path
cleanup: started_at, completed_at, result, errors[]
```

Write state atomically immediately after every successful create API response,
before invoking its waiter, and again after readiness transitions. Never write
AWS credentials, user-data secrets, session tokens, or instance credentials.

Cleanup terminates the instance first, waits for termination, then removes
owned profiles, roles, policies, security groups, and any surviving owned
volumes in dependency-safe order. Reused default-VPC resources are never
deleted. `NotFound` is success. Retain the state file with deletion tombstones
and return nonzero on partial failure. If creation fails, invoke this same
cleanup implementation automatically and preserve its report.

## 8. Acceptance

The live run passes only when:

1. the recorded production request set exactly matches Section 4;
2. all applicable decision series contain valid recent datapoints;
3. memory discovery and source reuse are exact;
4. parsing and normalization smoke assertions pass;
5. trend request shapes match Section 6; and
6. the report names every alias as `PASS`, `NOT_APPLICABLE`, or a specific
   failure—never as an ambiguous empty value.

The likely Linux `ethtool_*` mismatch is expected to be exposed by the first
implementation of this test. It is not an allowed permanent exception.
