# ElastiCache Rightsizer Live Telemetry Test Specification

Status: implementation contract for a future live AWS test harness. This file
does not authorize a normal unit-test run to create AWS resources.

## 1. Purpose and boundary

This test proves that the production ElastiCache collector submits the exact
per-node CloudWatch queries and parses native telemetry from a real replicated
Redis/Valkey topology. It validates role-aware applicability, optional
burstable CPU credits, bounded workload signals, local normalization inputs,
and confidence-trend request shapes.

It does not prove 14/30/60-day percentile policy, replica-removal modeling,
candidate sizing, savings, failover behavior, or 15-month trend values. Those
remain offline tests.

The harness must be explicitly gated as an AWS integration tool and excluded
from broad pytest discovery.

## 2. Implemented entrypoints and defaults

The ElastiCache folder owns:

```text
python rightsizers/elasticache/create_telemetry_test.py --apply [--run-id ID]
python rightsizers/elasticache/validate_telemetry_test.py --state PATH [--window-minutes 30]
python rightsizers/elasticache/cleanup_telemetry_test.py --state PATH
```

Shared state, identity checks, request recording, reports, and cleanup helpers
live under `rightsizers/common`. Defaults are AWS profile `default`, region
`us-east-1`, and the default VPC. Creation records and displays the STS account
and caller, prints the billable resource plan and cleanup command, and requires
explicit apply confirmation. It never falls back to a different account,
region, VPC, engine, or topology.

Creation waits for the replication group, both member clusters, the workload
client, SSM, and workload dependencies to be ready. It submits bounded traffic
and returns without waiting for CloudWatch. Validation checks once and does not
sleep, poll, retry, or clean up.

| Result | Meaning |
| --- | --- |
| `PASS` | Topology is ready and applicable request/data assertions passed |
| `NOT_READY` | Cache, client, SSM, or workload is not ready |
| `FAIL` | Request shape, API response, applicability, role, or data is wrong |

## 3. Fixture and workload

Create one cluster-mode-disabled Redis/Valkey replication group with one shard,
one primary, and one replica on the lowest-cost available T4g cache node that
publishes CPU-credit metrics. Use a current engine version supported in
`us-east-1`. Preflight engine/node availability before mutation.

Create an ElastiCache subnet group when the default VPC has sufficient subnets,
an owned security group allowing the workload client only, and one small
SSM-managed EC2 workload client. Track the replication group, member
`CacheClusterId` values and roles, subnet group, owned security groups, client
instance/profile/role, and SSM command IDs.

The bounded workload connects to the primary endpoint and performs a modest
mix of SET and GET commands over multiple connections. Data keys carry the run
ID and are disposable with the cluster. Do not attempt to fill memory, trigger
evictions, exhaust traffic allowances, activate traffic management, or create
large replication lag. Zero event counters are valid telemetry.

Tag every supported owned resource:

```text
maxops:test-purpose = rightsizer-telemetry
maxops:rightsizer = elasticache
maxops:run-id = <run_id>
maxops:expires-at = <UTC timestamp>
```

## 4. Decision-scan request contract

Invoke `AWSAdapter.get_elasticache_rightsizing_metrics` through a delegating
recorder with both member cluster IDs, `period_seconds=300`, and
`include_cpu_credits=true`. Every query uses namespace `AWS/ElastiCache`, exact
dimension `CacheClusterId=<member id>`, `ReturnData=true`, ascending scan order,
and `MaxDatapoints=100800`. The full matrix is repeated for each node.

| Alias | Metric name | Stat | Applicability and pass criterion |
| --- | --- | --- | --- |
| `engine_cpu_percent` | `EngineCPUUtilization` | Average | Required on both; finite `[0,100]` |
| `host_cpu_percent` | `CPUUtilization` | Average | Required on both; finite `[0,100]` |
| `memory_percent` | `DatabaseMemoryUsagePercentage` | Average | Required on both; finite `[0,100]` |
| `bytes_used_for_cache` | `BytesUsedForCache` | Average | Required; finite and nonnegative |
| `freeable_memory_bytes` | `FreeableMemory` | Average | Required; finite and nonnegative |
| `network_in_bytes` | `NetworkBytesIn` | Sum | Required; finite and nonnegative |
| `network_out_bytes` | `NetworkBytesOut` | Sum | Required; finite and nonnegative |
| `evictions` | `Evictions` | Sum | Required; zero is valid |
| `get_type_cmds` | `GetTypeCmds` | Sum | Required with a nonzero point on the primary; `NOT_APPLICABLE` on a replica when reads target only the primary and AWS does not publish the replica series |
| `set_type_cmds` | `SetTypeCmds` | Sum | Required on both; workload should produce a nonzero primary point; replica zero is valid |
| `swap_usage_bytes` | `SwapUsage` | Average | Required; zero is valid |
| `replication_lag_seconds` | `ReplicationLag` | Average | Required on replica; `NOT_APPLICABLE` on primary |
| `curr_connections` | `CurrConnections` | Average | Required; finite and nonnegative |
| `is_master` | `IsMaster` | Average | Required when published for chosen engine; role value must agree with inventory |
| `traffic_management_active` | `TrafficManagementActive` | Maximum | Required on supported engine; zero is valid |
| `network_bw_in_allowance_exceeded` | `NetworkBandwidthInAllowanceExceeded` | Sum | Required on supported T4g; zero valid |
| `network_bw_out_allowance_exceeded` | `NetworkBandwidthOutAllowanceExceeded` | Sum | Required on supported T4g; zero valid |
| `network_packets_per_second_allowance_exceeded` | `NetworkPacketsPerSecondAllowanceExceeded` | Sum | Required on supported T4g; zero valid |
| `network_conntrack_allowance_exceeded` | `NetworkConntrackAllowanceExceeded` | Sum | Required on supported T4g; zero valid |
| `cpu_credit_balance` | `CPUCreditBalance` | Average | Required because fixture is T4g; finite and nonnegative |
| `cpu_credit_usage` | `CPUCreditUsage` | Sum | Required because fixture is T4g; zero valid |

AWS publishes host metrics per cache node at one-minute intervals; see
[ElastiCache host-level metrics](https://docs.aws.amazon.com/AmazonElastiCache/latest/dg/CacheMetrics.HostLevel.html).
The validator must use the chosen engine/version and node type to produce an
applicability manifest before evaluating results. It must not call a missing
applicable metric `NOT_APPLICABLE` merely because the response was empty.

## 5. Parsing, role, and normalization assertions

Record every `GetMetricData` request and response. Assert batching at no more
than 500 query IDs, complete pagination for every batch, `StatusCode=Complete`
for returned series, no actionable query messages, exact query-ID-to-node/alias
mapping, timestamp/value pairing, and ascending output. Numeric zeros survive;
missing points are never synthesized or copied between nodes.

Determine primary/replica roles from ElastiCache inventory and compare them
with `IsMaster` when the metric applies. Do not infer a primary from query
ordering. Require replica lag only for the replica. A primary empty lag series
is `NOT_APPLICABLE`, while a replica empty lag series is a failure after the
resource and workload are ready.

Pass the raw result through production ElastiCache normalization and assert:

- counters use their 300-second `Sum` semantics before local rate conversion;
- gauges are not divided by the period;
- each member retains independent sample counts and windows;
- group summaries use the intended hottest/summed topology semantics;
- no missing counter is treated as zero.

These are short-window smoke assertions, not expected long-window percentiles.

## 6. Confidence-trend smoke contract

Invoke `get_elasticache_confidence_trend` with topology descriptors selected by
the production service for both `engine_cpu` and `memory`. For every selected
series assert:

- descriptor cache-cluster ID and persisted role, with
  `selection_reason=telemetry_fixture_full_topology`;

- namespace `AWS/ElastiCache` and exact `CacheClusterId`;
- metric `EngineCPUUtilization` or `DatabaseMemoryUsagePercentage`;
- daily period `86400` with `Maximum`, `p99`, and `p95`;
- 30-, 90-, 120-, 180-, 365-, and 455-day periods with `p99` and `p95`;
- at most 500 query IDs per request batch;
- no network, credit, command, or replica-lag trend query.

New resources need not return complete daily/multi-day values. Exact request
shape and successful API responses pass the trend smoke section.

## 7. State and cleanup

The default state path is
`rightsizers/elasticache/.telemetry_test_state/<run_id>.json` and must be
gitignored. Use the shared schema:

```text
schema_version, run_id, rightsizer, profile, account_id, caller_arn, region
created_at, expires_at, phase
resources[]: logical_name, service, type, id_or_arn, ownership, dependencies,
             create_status, ready_status, delete_status
workload_commands[]: command_id, target_ids, submitted_at, terminal_status
validation_runs[]: started_at, window, result, report_path
cleanup: started_at, completed_at, result, errors[]
```

Atomically checkpoint immediately after each create response and before its
waiter. Do not store cache credentials, AWS credentials, or session tokens.

Cleanup deletes the replication group without a final snapshot, waits for all
member clusters to disappear, then terminates the workload client and deletes
owned subnet/security/IAM resources in dependency order. Never delete reused
default-VPC subnets or route resources. `NotFound` is success. Retain state
tombstones and return nonzero on partial failure. Provisioning failures invoke
the same cleanup implementation automatically.

## 8. Acceptance

Pass requires the exact per-node query matrix in Section 4, role-correct
applicability, valid datapoints for every applicable alias, successful parsing
and normalization smoke assertions, and exact trend requests. The report lists
all 21 possible aliases per node as `PASS`, `NOT_APPLICABLE`, or a specific
failure. Valid zero counters pass; ambiguous empty values do not.
