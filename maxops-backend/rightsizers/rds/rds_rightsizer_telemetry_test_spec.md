# RDS Rightsizer Live Telemetry Test Specification

Status: implementation contract for a future live AWS test harness. This file
does not authorize a normal unit-test run to create AWS resources.

## 1. Purpose and boundary

This test proves that the production RDS collectors issue the intended
CloudWatch and Performance Insights requests against a real primary/read-replica
topology. It validates all decision metric names, dimensions, statistics,
role/class/storage applicability, parsing, local normalization inputs,
DB-load request identity, and confidence-trend request shapes.

The short-lived fixture does not prove 30/60-day evidence gates, long-window
percentiles, storage or class recommendation quality, savings, failover, or
15-month trend values. Those remain offline-test contracts.

The harness must be explicitly gated as an AWS integration tool and excluded
from broad pytest discovery.

## 2. Implemented entrypoints and defaults

The RDS folder owns:

```text
python rightsizers/rds/create_telemetry_test.py --apply [--run-id ID]
python rightsizers/rds/validate_telemetry_test.py --state PATH [--window-minutes 30]
python rightsizers/rds/cleanup_telemetry_test.py --state PATH
```

Shared state, identity checks, request recording, reports, and cleanup helpers
live under `rightsizers/common`. Defaults are AWS profile `default`, region
`us-east-1`, and the default VPC. Creation records and displays STS account ID
and caller ARN, prints the billable topology and cleanup command, and requires
explicit apply confirmation. It never falls back to another account, region,
VPC, engine, or DB class.

Creation waits for the primary and replica to be `available`, for the workload
client and SSM to be ready, and for the bounded SQL workload to be submitted.
It returns without waiting for CloudWatch or Performance Insights publication.
Validation is one shot and performs no sleep, polling, retry, or cleanup.

| Result | Meaning |
| --- | --- |
| `PASS` | Topology is ready and all applicable telemetry assertions passed |
| `NOT_READY` | DB, replica, workload client, SSM, or workload is not ready |
| `FAIL` | Request, API, applicability, identifier, or datapoint contract failed |

## 3. Fixture and workload

Create a current MySQL primary and read replica using `db.t4g.medium`, the
lowest-cost burstable MySQL class in `us-east-1` that supports Performance
Insights. `db.t4g.micro` and `db.t4g.small` are explicitly unsupported. Use
Single-AZ, 20 GiB gp2 storage, backup retention sufficient for
replica creation, deletion protection disabled, and Performance Insights with
the minimum supported retention. Preflight engine/version/class/orderability,
PI support, gp2 support, and default-VPC subnet coverage before mutation.

Create and track:

- DB subnet group and an owned DB security group;
- primary identifier, ARN, and `DbiResourceId`;
- replica identifier, ARN, and `DbiResourceId`;
- one small SSM-managed EC2 workload client and its IAM/profile/security group;
- one SSM Parameter Store `SecureString` containing the generated DB password;
- SSM workload command IDs.

State stores only the SecureString parameter name, never the password. The
workload reads it at execution time and performs bounded table creation,
inserts/updates, indexed reads, and short-lived connections against the
primary. It must not stress storage, consume CPU credits intentionally, create
large replica lag, or modify schema outside its run-ID database/table. Workload
objects are disposable with the DB.

Tag every supported owned resource:

```text
maxops:test-purpose = rightsizer-telemetry
maxops:rightsizer = rds
maxops:run-id = <run_id>
maxops:expires-at = <UTC timestamp>
```

## 4. CloudWatch decision-scan contract

Invoke `AWSAdapter.get_rds_rightsizing_metrics` through a delegating recorder
once for the primary and once for the replica with `period_seconds=300`. Every
query uses namespace `AWS/RDS`, exact dimension
`DBInstanceIdentifier=<current identifier>`, `ReturnData=true`, ascending scan
order, and `MaxDatapoints=100800`.

| Alias | Metric name | Stat | Applicability and pass criterion |
| --- | --- | --- | --- |
| `cpu_percent` | `CPUUtilization` | Average | Required on both; finite `[0,100]` |
| `freeable_memory_bytes` | `FreeableMemory` | Average | Required on both; finite nonnegative |
| `swap_bytes` | `SwapUsage` | Average | Required for MySQL; zero valid |
| `connections` | `DatabaseConnections` | Average | Required and finite nonnegative on both; workload must create a positive primary observation, while an idle replica may validly remain zero |
| `read_iops` | `ReadIOPS` | Average | Required; finite nonnegative |
| `write_iops` | `WriteIOPS` | Average | Required; finite nonnegative |
| `read_throughput_bps` | `ReadThroughput` | Average | Required; finite nonnegative |
| `write_throughput_bps` | `WriteThroughput` | Average | Required; finite nonnegative |
| `read_latency_seconds` | `ReadLatency` | Average | Required; finite nonnegative |
| `write_latency_seconds` | `WriteLatency` | Average | Required; finite nonnegative |
| `disk_queue_depth` | `DiskQueueDepth` | Average | Required; finite nonnegative |
| `free_storage_bytes` | `FreeStorageSpace` | Minimum | Required; finite positive |
| `network_rx_bps` | `NetworkReceiveThroughput` | Average | Required; finite nonnegative |
| `network_tx_bps` | `NetworkTransmitThroughput` | Average | Required; finite nonnegative |
| `cpu_credit_balance` | `CPUCreditBalance` | Minimum | Required on selected burstable class; finite nonnegative |
| `cpu_credit_usage` | `CPUCreditUsage` | Sum | Required on selected burstable class; zero valid |
| `burst_balance_percent` | `BurstBalance` | Minimum | Required on gp2; finite `[0,100]` |
| `ebs_io_balance_percent` | `EBSIOBalance%` | Minimum | Required only when selected class supports it; `[0,100]` |
| `ebs_byte_balance_percent` | `EBSByteBalance%` | Minimum | Required only when selected class supports it; `[0,100]` |
| `replica_lag_seconds` | `ReplicaLag` | Maximum | Required on replica; `NOT_APPLICABLE` on primary |

The applicability manifest is derived from the actual engine, role, class, and
storage before evaluating results. An applicable empty series is not
`NOT_APPLICABLE`. AWS definitions and applicability are documented in
[Amazon CloudWatch metrics for RDS](https://docs.aws.amazon.com/AmazonRDS/latest/UserGuide/rds-metrics.html).

## 5. Performance Insights contract

Invoke `get_rds_performance_insights_metrics` for the PI-enabled primary. The
request must use:

```text
API: GetResourceMetrics
ServiceType: RDS
Identifier: <primary DbiResourceId>
PeriodInSeconds: 300
MaxResults: 25
MetricQueries:
  - Metric: db.load.avg
  - Metric: db.load.avg
    GroupBy: {Group: db.wait_event_type, Limit: 25}
```

The identifier must not be the DB instance identifier or ARN. AWS rejects
`MaxResults` above 25. Follow all `NextToken` pages. No query may request SQL,
database, user, host, tokenized SQL,
or other sensitive dimensions. After the workload is complete, require a
finite nonnegative ungrouped DB-load value. A grouped response may be empty when
there was no measurable wait; that is a valid completed response, not a reason
to invent zero attribution.

## 6. Parsing and normalization assertions

Record every CloudWatch and PI request/response. For CloudWatch, require
`StatusCode=Complete`, fail actionable query messages, follow every token,
merge by query ID, preserve timestamp/value pairing, sort ascending, and keep
legitimate numeric zeros. Missing points remain absent.

Pass primary and replica raw data through production RDS normalization and
assert:

- IOPS and throughput gauges are already rates and are not divided by 300;
- read/write values combine only at matching timestamps;
- one-sided observations remain lower bounds rather than implicit zero;
- Minimum/Maximum balance and lag semantics survive parsing;
- role-specific telemetry status is distinct between primary and replica;
- actual short-window sample counts are disclosed without claiming long
  coverage.

These assertions verify input wiring, not storage evidence sufficiency or
recommendation output.

## 7. Confidence-trend smoke contract

Invoke `get_rds_confidence_trend` for the primary and record the exact request
shape:

| Response key | Metric | Daily period/stat | Bucket stats |
| --- | --- | --- | --- |
| `cpu` | `CPUUtilization` | `86400` / Maximum | `p99`, `p95` |
| `freeable_memory` | `FreeableMemory` | `86400` / Minimum | `p01`, `p05` |
| `connections` | `DatabaseConnections` | `86400` / Maximum | `p99`, `p95` |
| `free_storage` | `FreeStorageSpace` | `86400` / Minimum | `p01`, `p05` |

Each bucket statistic is requested at 30-, 90-, 120-, 180-, 365-, and 455-day
periods using the same exact DB identifier dimension. No PI, network, latency,
IOPS, or replica-lag metric belongs in this trend call. New resources need not
return complete daily or multi-day points; successful API responses and exact
requests pass the smoke section.

## 8. State and cleanup

The default state path is
`rightsizers/rds/.telemetry_test_state/<run_id>.json` and must be gitignored.
Use the shared schema:

```text
schema_version, run_id, rightsizer, profile, account_id, caller_arn, region
created_at, expires_at, phase
resources[]: logical_name, service, type, id_or_arn, ownership, dependencies,
             create_status, ready_status, delete_status
workload_commands[]: command_id, target_ids, submitted_at, terminal_status
validation_runs[]: started_at, window, result, report_path
cleanup: started_at, completed_at, result, errors[]
```

Checkpoint atomically after each create response and before each waiter. Do not
persist the database password, decrypted SecureString value, AWS credentials,
or session tokens.

Cleanup order is strict:

1. delete the read replica with `SkipFinalSnapshot=true` and wait;
2. delete the primary with `SkipFinalSnapshot=true` and wait;
3. terminate the workload client and wait;
4. delete the SecureString parameter;
5. delete owned DB subnet/security and client IAM/network resources.

No final snapshot is created, deletion protection must be false, and reused
default-VPC resources are never deleted. Missing resources count as success.
Retain state tombstones and return nonzero on partial failure. Provisioning
failure invokes the same cleanup implementation automatically.

## 9. Acceptance

Pass requires the exact 20-query matrix for both resources, role/class/storage
applicability, valid applicable datapoints, `DbiResourceId` PI requests without
sensitive dimensions, parsing/normalization smoke assertions, and exact trend
queries. The report lists every alias for primary and replica as `PASS`,
`NOT_APPLICABLE`, or a specific failure; valid zero is never confused with
missing data.
