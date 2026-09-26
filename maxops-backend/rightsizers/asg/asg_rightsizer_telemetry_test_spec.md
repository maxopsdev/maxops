# Auto Scaling Group Rightsizer Live Telemetry Test Specification

Status: implementation contract for a future live AWS test harness. This file
does not authorize a normal unit-test run to create AWS resources.

## 1. Purpose and boundary

This test proves the production ASG collector's exact CloudWatch query contract
against a real two-member Auto Scaling group. It covers group CPU, desired and
in-service capacity, group memory discovery, response parsing, local demand
normalization inputs, and the CPU/memory confidence-trend request shape.

The short-lived group cannot establish the 60-day membership stability needed
for `stable_member_aggregate`. That fallback, historical pairing, long-window
percentiles, recommendation candidates, and savings remain offline-test
responsibilities.

The implementation must be explicitly gated as an AWS integration tool and
must not participate in broad pytest discovery.

## 2. Implemented entrypoints and defaults

The ASG folder owns:

```text
python rightsizers/asg/create_telemetry_test.py --apply [--run-id ID]
python rightsizers/asg/validate_telemetry_test.py --state PATH [--window-minutes 30]
python rightsizers/asg/cleanup_telemetry_test.py --state PATH
```

Shared request recording, state, reports, AWS identity checks, and idempotent
cleanup live under `rightsizers/common`. Defaults are AWS profile `default`,
region `us-east-1`, and the default VPC. Creation records and displays the STS
account ID/caller ARN and requires an explicit apply/confirmation flag. It must
not fall back to another account, region, or VPC.

Creation waits for the ASG to have two healthy in-service instances, for EC2
status checks and SSM on both members, and for CloudWatch Agent to be running.
It submits a bounded workload and returns without waiting for metrics.
Validation performs one readiness check and no sleep, polling, retry, or
cleanup.

IAM reporting an instance profile does not prove that Auto Scaling can consume
it yet. Treat launch-template validation failures containing `Invalid IAM
Instance Profile name` as eventual consistency and retry group creation for a
bounded two-minute window before failing.

The CloudWatch Agent `ethtool` block inherits the agent's global 60-second
collection interval. Do not set `metrics_collection_interval` inside that
block; the current agent schema rejects that property.

| Result | Meaning |
| --- | --- |
| `PASS` | Group is ready and applicable request/data assertions passed |
| `NOT_READY` | Group membership, SSM, agent, or workload is not ready |
| `FAIL` | The live collector violates the request, API, or datapoint contract |

## 3. Fixture

Create one launch template and one ASG with desired/min/max capacity `2/2/2`.
Use the lowest-cost compatible current-generation T-family Linux type, detailed
monitoring, a small EBS root volume, and an instance profile for SSM and
CloudWatch Agent. Do not attach scaling policies, scheduled actions, warm
pools, mixed-instance policies, lifecycle hooks, or instance refreshes.

Explicitly call `EnableMetricsCollection` with one-minute granularity for:

```text
GroupDesiredCapacity
GroupInServiceInstances
```

These `AWS/AutoScaling` metrics are not assumed to be enabled by default. See
[Amazon EC2 Auto Scaling metrics](https://docs.aws.amazon.com/autoscaling/ec2/userguide/ec2-auto-scaling-metrics.html).

Configure `CWAgent/mem_used_percent` with both exact rollups:

```text
["InstanceId"]
["AutoScalingGroupName"]
```

The group rollup is the required live memory source. Per-member sources may be
listed for diagnostics but must not be presented as a stable historical group
aggregate. Submit the same bounded CPU and temporary-file workload to both
members. Do not deliberately cause a scale event or replace a member.

Track the launch template and version, ASG, IAM role/profile/policies, any owned
security group, discovered member instance IDs, and SSM command IDs. Instances
are owned through the ASG and are not independently deleted before ASG scale-in.

Apply these tags where supported:

```text
maxops:test-purpose = rightsizer-telemetry
maxops:rightsizer = asg
maxops:run-id = <run_id>
maxops:expires-at = <UTC timestamp>
```

## 4. Decision-scan request contract

Invoke `AWSAdapter.get_asg_rightsizing_metrics` through a delegating recorder
with `period_seconds=300`. Every standard query has dimension
`AutoScalingGroupName=<state.asg_name>`, `ReturnData=true`, ascending scan order,
and `MaxDatapoints=100800`. Memory uses the complete discovered source.

| Alias | Namespace | Metric name | Stat | Applicability and pass criterion |
| --- | --- | --- | --- | --- |
| `cpu_percent` | `AWS/EC2` | `CPUUtilization` | Average | Required; finite values in `[0,100]` |
| `desired_capacity` | `AWS/AutoScaling` | `GroupDesiredCapacity` | Maximum | Required by this enabled fixture; finite value `2` while stable |
| `in_service_instances` | `AWS/AutoScaling` | `GroupInServiceInstances` | Maximum | Required; finite positive value, expected `2` while stable |
| `memory_percent` | discovered, normally `CWAgent` | discovered percentage metric | Average | Required; exact `AutoScalingGroupName` source and finite `[0,100]` values |

No decision request may include group min/max, total, pending, terminating,
standby, warm-pool, predictive-scaling, network, EBS, or allowance metrics. An
addition is a contract failure even if AWS accepts it.

## 5. Discovery, parsing, and normalization

Record every paginated `ListMetrics` and `GetMetricData` call and response.
Assert that group memory discovery:

- filters by the exact ASG name;
- accepts the exact group rollup and preserves every returned dimension;
- selects a finite percentage utilization series rather than bytes/free/swap;
- records `kind: group` and the selected namespace/name/dimensions;
- does not claim the short-lived members satisfy stable-member history.

For each metric-data result, require `StatusCode=Complete`, fail actionable
response messages, follow all `NextToken` pages, merge results by ID, deduplicate
timestamps deterministically, sort ascending, and preserve numeric zero. Never
forward-fill or synthesize capacity or utilization.

Pass the raw payload through production ASG normalization and assert:

- CPU load points exist only where CPU and positive in-service capacity share a
  timestamp;
- desired capacity remains contextual evidence, not workload demand;
- memory pairing uses the same in-service timestamp;
- required-capacity inputs and sample counts are positive for the live window;
- no 14/30/60-day coverage claim exceeds the actual short observation period.

This verifies wiring and timestamp joins, not long-window percentile values.

## 6. Confidence-trend smoke contract

Invoke `get_asg_confidence_trend` with the persisted exact group memory source.
For both group CPU and memory, assert:

- daily `86400` queries with `Maximum`, `p99`, and `p95`;
- 30-, 90-, 120-, 180-, 365-, and 455-day periods with `p99` and `p95`;
- CPU uses `AWS/EC2/CPUUtilization` and the exact ASG dimension;
- memory reuses the decision-scan source;
- no capacity, network, EBS, or unrelated group query appears.

Recent resources need not return complete daily or multi-day points. Exact
requests plus successful API responses are sufficient for this smoke section.

## 7. State and cleanup

The default state path is
`rightsizers/asg/.telemetry_test_state/<run_id>.json` and must be gitignored.
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

Checkpoint atomically after every create response and before each waiter. Never
persist credentials or instance-role tokens.

Cleanup updates ASG desired/min/max to zero when necessary, deletes the ASG and
waits for member termination, then deletes launch-template versions/template,
owned security groups, and IAM resources in dependency order. Do not terminate
members independently while the ASG can replace them. Reused VPC resources are
never deleted. Missing resources count as success; retain tombstones and return
nonzero on partial failure. Provisioning failure invokes this same cleanup path
automatically.

## 8. Acceptance

The live run passes only when the four decision aliases match Section 4
exactly, all are present for this fixture, memory is an exact group source,
normalization joins are valid, and trend request shapes match Section 6. Every
alias receives a named result. No empty series is interpreted as zero.
