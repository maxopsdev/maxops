# RDS Rightsizer V1 Specification

Status: authoritative V1 product and service contract.

This specification follows the EC2 and ElastiCache rightsizer architecture:
exact metric normalization, hard constraints separated from warnings, local
window statistics, Conservative/Balanced/Aggressive instance-class options,
explicit uncertainty, independent recommendation kinds, and a long-range
confidence view. It is deliberately read-only.

The word **Performance Insights** in API names remains correct. AWS is moving
the console experience to CloudWatch Database Insights, but the Performance
Insights API and its configuration parameters continue to exist. User-facing
copy should say **Database Insights / Performance Insights** during that
transition.

---

## 1. Goal and user promise

For a supported RDS DB instance, answer:

```text
Can this database use a less expensive DB instance class or storage
configuration while retaining explicit CPU, memory, I/O, throughput, and
network headroom?
```

V1 may emit two independent recommendation kinds:

```text
DB_INSTANCE_CLASS_CHANGE       compute/memory class change; tiered
STORAGE_CONFIGURATION_CHANGE  storage type, IOPS, or throughput; one option
```

They are never composed. If both are available, both are shown and priced
independently. The next scan recomputes the other recommendation after a user
applies one.

The service never modifies a database, enables telemetry, changes an engine
version, changes availability posture, or claims that a resize is outage-free.

## 2. V1 scope

### 2.1 Supported resources

V1 evaluates provisioned, non-Aurora RDS DB instances returned by
`DescribeDBInstances`, including:

- Single-AZ DB instances;
- conventional Multi-AZ DB instance deployments with one non-readable standby;
- source DB instances and read replicas, each as its own inventory row;
- MySQL, MariaDB, PostgreSQL, Oracle, SQL Server, and Db2 when the runtime
  orderable-options catalog exposes compatible targets.

The evaluation unit is one DB instance (`DBInstanceIdentifier`). A Multi-AZ
standby is not a separate inventory row. Preserve the current `MultiAZ` value
and price the complete deployment.

Read replicas are sized independently from their source because their CPU,
memory, connections, and read traffic can differ. V1 never recommends removing
a read replica or changing replication topology.

### 2.2 Scope gates

Return `DEFERRED` before telemetry or pricing work for:

| Context | Reason code |
| --- | --- |
| Aurora member or cluster | `AURORA_REQUIRES_CLUSTER_RIGHTSIZER` |
| Multi-AZ DB cluster member | `MULTI_AZ_CLUSTER_REQUIRES_CLUSTER_RIGHTSIZER` |
| RDS Custom | `RDS_CUSTOM_UNSUPPORTED` |
| DB instance not `available` | `RESOURCE_NOT_AVAILABLE` |
| Pending class, engine, storage, or processor modification | `PENDING_MODIFICATION` |
| Storage optimization in progress | `STORAGE_OPTIMIZATION_IN_PROGRESS` |
| Non-default Oracle/SQL Server processor features | `CUSTOM_PROCESSOR_CONFIGURATION_UNSUPPORTED` |
| Unknown engine, engine version, license model, or class | `INVENTORY_CONFIGURATION_INCOMPLETE` |

Db2 can be evaluated from standard CloudWatch telemetry. Because Performance
Insights isn't available for Db2, any candidate that requires load attribution
under Section 12 is `CONDITIONAL`; the user is not asked to enable an
unsupported feature.

Dedicated log volumes and additional storage volumes do not block instance
class analysis, but they disable `STORAGE_CONFIGURATION_CHANGE` in V1 with
`MULTI_VOLUME_STORAGE_OPTIMIZATION_UNSUPPORTED`. Instance-class candidates are
at most `CONDITIONAL` unless all volume demand and the target class's combined
EBS capacity can be proved.

### 2.3 Explicitly out of scope

- Aurora provisioned and Serverless clusters;
- Multi-AZ DB clusters with readable standbys;
- changing engine, engine version, edition, license model, parameter groups,
  option groups, character sets, or architecture configuration;
- changing Single-AZ/Multi-AZ posture;
- replica creation or removal;
- allocated-storage reduction (RDS permits only increases);
- storage-autoscaling threshold changes;
- magnetic-storage recommendations;
- stopping, deleting, snapshotting, or scheduling a database;
- query, index, schema, lock, or connection-pool tuning;
- automatic application of any recommendation.

The existing idle-database and non-Graviton checks may remain separate. Their
findings do not substitute for this rightsizer's evidence.

## 3. Required inventory

Persist one `RdsInventory` row per DB instance. Existing columns remain valid;
the normalized fields below belong in `metadata_json` until promoted to typed
columns:

```text
DBInstanceIdentifier, DBInstanceArn, DbiResourceId
DBInstanceStatus, DBInstanceClass
Engine, EngineVersion, LicenseModel
AvailabilityZone, SecondaryAvailabilityZone, MultiAZ
DBClusterIdentifier, ReadReplicaSourceDBInstanceIdentifier
ReadReplicaDBInstanceIdentifiers
InstanceCreateTime
StorageType, AllocatedStorage, Iops, StorageThroughput
MaxAllocatedStorage, StorageEncrypted
DedicatedLogVolume, AdditionalStorageVolumes
PerformanceInsightsEnabled, PerformanceInsightsRetentionPeriod
DatabaseInsightsMode, MonitoringInterval
ProcessorFeatures, PendingModifiedValues
DBParameterGroups, OptionGroupMemberships
NetworkType, DBSubnetGroup, CustomerOwnedIpEnabled
DeletionProtection, AutoMinorVersionUpgrade
```

The scanner also persists:

```text
orderable_option_key
orderable_target_classes
valid_storage_options
class_capability_catalog_version
storage_capability_policy_version
pricing_catalog_version
last_rds_event_at
recent_event_categories
pending_maintenance_actions
```

`DbiResourceId`, not `DBInstanceIdentifier`, is the identifier supplied to the
Performance Insights API. Inventory collection must retain both.

All paginated RDS, CloudWatch, Performance Insights, and pricing calls consume
every page. A partially completed paginated call is unknown, never a valid
negative result.

## 4. Candidate generation and compatibility

### 4.1 Runtime orderable options are authoritative

Build the target set from paginated `DescribeOrderableDBInstanceOptions` using
the instance's exact:

```text
engine
engine_version
license_model
region
VPC mode
```

An option must preserve the current storage type and support the current
features:

```text
MultiAZCapable                 when MultiAZ is true
SupportsStorageEncryption     when encrypted
SupportsIops                  when provisioned IOPS are configured
SupportsStorageThroughput     when gp3 throughput is configured
SupportsPerformanceInsights   when Performance Insights is enabled
SupportedNetworkTypes         includes the current network type
```

The exact engine version match is mandatory. V1 never proposes an engine
upgrade to unlock a class. An absent runtime option is
`ENGINE_CLASS_COMBINATION_UNAVAILABLE`, not a guess based on a family map.

### 4.2 Capability catalog

Each class candidate needs these normalized capabilities:

```text
vcpus
memory_gib
network_baseline_mbps | null
network_peak_mbps | null
ebs_baseline_iops | null
ebs_peak_iops | null
ebs_baseline_mbps | null
ebs_peak_mbps | null
architecture
burstable
local_nvme
current_generation
```

Use a versioned RDS class-capability catalog generated from AWS's RDS DB
instance class tables. The Price List product attributes may enrich the
catalog, but pricing metadata is not the compatibility authority. Never infer
memory from family ratios or strip `db.` and assume the EC2 equivalent is
identical.

If vCPU or memory is unknown, the target is rejected with
`TARGET_COMPUTE_CAPACITY_UNKNOWN`. If only a network or EBS baseline is unknown,
the candidate may remain visible as `CONDITIONAL` with the corresponding
unknown-capacity warning, but it cannot be `ACTIONABLE`.

### 4.3 Family gates

These target classes require matching current-class semantics:

| Class | Gate |
| --- | --- |
| `db.t*` burstable | current class must also be burstable |
| local-NVMe classes such as `*d`/`*gd` | current class must have the same local-storage behavior |
| deprecated/previous-generation policy set | target allowed only when current class is also in the set |

Graviton targets are ordinary candidates when runtime orderable options expose
them. RDS supplies the database engine binary and clients don't observe the
host architecture. `db.m5 -> db.m7g`, for example, is therefore a normal
instance-class option, not a special opportunity class.

### 4.4 Availability and capacity

The runtime orderable option must include the current Availability Zone or its
AZ group when AWS returns that data. This validates orderability, not live
capacity. The required disclosure is:

```text
This recommendation validates the target against current RDS orderable
options. It does not reserve capacity or guarantee that a modification will
succeed at apply time.
```

## 5. Pricing and savings

Use the regional AWS Price List catalog for exact on-demand RDS pricing,
matching engine, edition/license model, deployment option, class, and region.
Do not apply a generic hourly class dictionary or a fixed Graviton discount.

For `DB_INSTANCE_CLASS_CHANGE`:

```python
monthly_cost = hourly_price * monthly_hours
monthly_savings = current_monthly_cost - target_monthly_cost
```

The price dimension must already represent Single-AZ versus Multi-AZ. Never
blindly multiply a Multi-AZ price by two.

For `STORAGE_CONFIGURATION_CHANGE`, price allocated GiB, provisioned IOPS, and
provisioned throughput using the storage type's regional dimensions. Allocated
GiB is unchanged in both current and target cost.

Eligibility uses `Decimal`, the EC2 inclusive one-cent floor, and inclusive
`min_monthly_savings`. Floats are display-only. Missing exact pricing prevents
that candidate from becoming a recommendation and records
`TARGET_PRICING_UNAVAILABLE`.

Reserved Instances, Savings Plans, enterprise discounts, support, backup,
snapshot, data-transfer, Performance Insights/Database Insights, and license
BYOL economics are not modeled. Surface the pricing scope.

## 6. Telemetry collection model

Telemetry has three independent paths:

| Path | Purpose | Period | Horizon | When |
| --- | --- | --- | --- | --- |
| CloudWatch decision scan | sizes every candidate | 300 s | up to 60 days | every scan |
| DB-load attribution | resolves ambiguous load | 300 s | up to available retention, default 7 days | conditional during scan |
| Confidence trend | user evidence | daily | up to 15 months | on demand |

The CloudWatch path works without Performance Insights. The attribution path
uses the Performance Insights API when enabled and needed. The confidence path
never changes a recommendation already calculated by the decision scan.

### 6.1 Standard CloudWatch series

Namespace `AWS/RDS`, dimension
`DBInstanceIdentifier=<current identifier>`:

| Normalized name | AWS metric | Per-period statistic | Use |
| --- | --- | --- | --- |
| `cpu_percent` | `CPUUtilization` | Average | CPU sizing |
| `freeable_memory_bytes` | `FreeableMemory` | Average | memory sizing |
| `swap_bytes` | `SwapUsage` | Average | memory pressure |
| `connections` | `DatabaseConnections` | Average | concurrency warning |
| `read_iops` | `ReadIOPS` | Average | storage demand |
| `write_iops` | `WriteIOPS` | Average | storage demand |
| `read_throughput_bps` | `ReadThroughput` | Average | storage demand |
| `write_throughput_bps` | `WriteThroughput` | Average | storage demand |
| `read_latency_seconds` | `ReadLatency` | Average | storage health |
| `write_latency_seconds` | `WriteLatency` | Average | storage health |
| `disk_queue_depth` | `DiskQueueDepth` | Average | storage health |
| `free_storage_bytes` | `FreeStorageSpace` | Minimum | capacity warning |
| `network_rx_bps` | `NetworkReceiveThroughput` | Average | class network |
| `network_tx_bps` | `NetworkTransmitThroughput` | Average | class network |
| `cpu_credit_balance` | `CPUCreditBalance` | Minimum | T classes only |
| `cpu_credit_usage` | `CPUCreditUsage` | Sum | T classes only |
| `burst_balance_percent` | `BurstBalance` | Minimum | gp2 only |
| `ebs_io_balance_percent` | `EBSIOBalance%` | Minimum | class EBS burst context |
| `ebs_byte_balance_percent` | `EBSByteBalance%` | Minimum | class EBS burst context |
| `replica_lag_seconds` | `ReplicaLag` | Maximum | replicas only |

Metrics that don't apply to the current engine, class, storage, or role are
`NOT_APPLICABLE`, not missing. Required metrics that apply but return no usable
points are `MISSING`.

Every applicable metric also records collection state. `PRESENT`, `EMPTY`, and
`INVALID` are assigned only after the complete paginated request succeeds;
`EMPTY` means no datapoints were returned and `INVALID` means datapoints existed
but none survived validation. `ACCESS_DENIED` and `ERROR` are request-level
states and must never be collapsed into `EMPTY`. Section 7 defines the persisted
shape and Section 16 maps these states to evaluation status.

### 6.2 Exact normalization

Fetch raw timestamped five-minute points with `GetMetricData`. Missing points
are absent and are never zero-filled, interpolated, or forward-filled.

RDS IOPS and throughput metrics are already rates. Do not divide them by the
period. At each timestamp:

```python
total_iops_t = read_iops_t + write_iops_t
total_throughput_bps_t = read_throughput_bps_t + write_throughput_bps_t
```

Only timestamps containing both directions enter the exact combined series.
If one direction is systematically absent, retain the observed direction as a
lower bound, set `STORAGE_DIRECTIONAL_METRICS_INCOMPLETE`, and prevent
`ACTIONABLE` storage or class reductions whose EBS capacity matters.

Network receive and transmit are evaluated independently; they are not added.
Bytes per second convert to decimal Mbps exactly:

```python
mbps = bytes_per_second * 8 / 1_000_000
```

Memory bytes convert to GiB with `1024 ** 3`. Latency remains seconds in the
service response; the UI may display milliseconds.

Reject NaN, infinity, negative counts/rates, CPU outside 0-100, and balance
percentages outside 0-100. Invalid samples are absent and counted.

### 6.3 Window statistics

Derive trailing 14-, 30-, and 60-day windows from the one 60-day fetch. Compute
statistics locally after normalization:

```text
ordinary demand:       average, p50, p95, p99, maximum, sample_count
freeable memory:       minimum, p01, p05, average, sample_count
free storage/balances: minimum, p01, p05, average, sample_count
```

Percentiles use the shared EC2 nearest-rank/interpolation convention and a
single implementation. The decision value for ordinary demand is the maximum
window p99. The decision value for freeable memory and balance is the minimum
window p01. Maxima/minima remain warning and disclosure evidence.

`observed_days = sample_count * 300 / 86400`. Also report first timestamp,
last timestamp, expected samples based on the lesser of resource age and the
window, coverage ratio, and invalid sample count.

### 6.4 Telemetry sufficiency and thin data

Like EC2 and ElastiCache, observed days and coverage are not hard gates: short
or sparse telemetry still produces recommendations. RDS deliberately applies a
stricter classification policy than those sibling rightsizers because changing
a DB instance class causes an outage: thin evidence caps the result at
`CONDITIONAL` instead of being disclosure-only.

- CPU or freeable memory below `min_observed_days` observed days in every
  window adds `OBSERVATION_WINDOW_TOO_SHORT`.
- CPU or freeable memory below `min_required_coverage_ratio` in its selected
  window adds `OBSERVATION_COVERAGE_TOO_LOW`.
- Either condition sets telemetry risk HIGH and caps every candidate at
  `CONDITIONAL`. Candidate math still runs on the samples that exist.
- Both storage directions must have usable evidence when storage applies
  (Section 6.2).
- Inventory and target capabilities must be complete (Section 4).

A successfully completed CloudWatch request that returns no usable points for a
native metric is an unmeasured dimension, not zero demand:

```python
if cpu_missing:
    required_vcpus = current_vcpus
    projected_cpu_percent = None

if freeable_memory_missing:
    required_memory_bytes = current_memory_bytes
    projected_memory_util = None
```

Candidates may still be generated, but they must retain or grow capacity in
every unmeasured dimension. Missing CPU adds
`RDS_CPU_METRIC_UNAVAILABLE_CURRENT_CAPACITY_RETAINED`; missing FreeableMemory
adds `RDS_MEMORY_METRIC_UNAVAILABLE_CURRENT_CAPACITY_RETAINED`. Either sets
telemetry risk HIGH and caps instance-class candidates at `CONDITIONAL`.

When both CPU and FreeableMemory are absent, only candidates retaining both
current vCPU and memory remain eligible. `projected_util` is `null`, every tier
is `null`, and the surviving candidates remain savings-ranked outside the tier
selector. This is a capacity-retaining modernization/pricing result, not a
measured downsize.

`INSUFFICIENT_DATA` is reserved for an evaluation path that could not collect
or validate the telemetry needed to run at all, such as an AccessDenied or
failed `GetMetricData` request with no usable persisted fallback. It is recorded
per recommendation kind (Section 16); it is never inferred merely from an empty
metric series. A failed instance-class evaluation does not suppress an
independently complete storage evaluation.

Connections, latency, queue depth, balance, network, and swap are warning or
constraint evidence. An applicable missing series caps a candidate at
`CONDITIONAL` when that dimension could bind; it is not silently treated as
healthy.

## 7. Persisted decision telemetry

Persist normalized summaries in
`metadata_json["rightsizing_metrics"]["rds_v1"]`:

```yaml
generated_at: timestamp
period_seconds: 300
collection:
  status: SUCCESS | ACCESS_DENIED | ERROR
  reason_code: null | RDS_CLOUDWATCH_COLLECTION_FAILED
windows:
  14d:
    cpu_percent: {status: PRESENT | EMPTY | INVALID, p50, p95, p99, maximum, sample_count, observed_days, coverage_ratio}
    freeable_memory_bytes: {status: PRESENT | EMPTY | INVALID, minimum, p01, p05, average, sample_count, observed_days, coverage_ratio}
    swap_bytes: {status: PRESENT | EMPTY | INVALID | NOT_APPLICABLE, p95, p99, maximum, sample_count}
    connections: {status: PRESENT | EMPTY | INVALID, p95, p99, maximum, sample_count}
    read_iops: {status: PRESENT | EMPTY | INVALID, p95, p99, maximum, sample_count}
    write_iops: {status: PRESENT | EMPTY | INVALID, p95, p99, maximum, sample_count}
    total_iops: {status: PRESENT | EMPTY | INVALID, p95, p99, maximum, sample_count, pairing_ratio}
    read_throughput_bps: {status: PRESENT | EMPTY | INVALID, p95, p99, maximum, sample_count}
    write_throughput_bps: {status: PRESENT | EMPTY | INVALID, p95, p99, maximum, sample_count}
    total_throughput_bps: {status: PRESENT | EMPTY | INVALID, p95, p99, maximum, sample_count, pairing_ratio}
    read_latency_seconds: {status: PRESENT | EMPTY | INVALID, p95, p99, maximum, sample_count}
    write_latency_seconds: {status: PRESENT | EMPTY | INVALID, p95, p99, maximum, sample_count}
    disk_queue_depth: {status: PRESENT | EMPTY | INVALID, p95, p99, maximum, sample_count}
    network_rx_mbps: {status: PRESENT | EMPTY | INVALID, p95, p99, maximum, sample_count}
    network_tx_mbps: {status: PRESENT | EMPTY | INVALID, p95, p99, maximum, sample_count}
    free_storage_bytes: {status: PRESENT | EMPTY | INVALID, minimum, p01, p05, average, sample_count}
    cpu_credit_balance: {status: PRESENT | EMPTY | INVALID | NOT_APPLICABLE, minimum, p01, p05, sample_count}
    cpu_credit_usage: {status: PRESENT | EMPTY | INVALID | NOT_APPLICABLE, p95, p99, maximum, sample_count}
    burst_balance_percent: {status: PRESENT | EMPTY | INVALID | NOT_APPLICABLE, minimum, p01, p05, sample_count}
    ebs_io_balance_percent: {status: PRESENT | EMPTY | INVALID | NOT_APPLICABLE, minimum, p01, p05, sample_count}
    ebs_byte_balance_percent: {status: PRESENT | EMPTY | INVALID | NOT_APPLICABLE, minimum, p01, p05, sample_count}
    replica_lag_seconds: {status: PRESENT | EMPTY | INVALID | NOT_APPLICABLE, ...}
  30d: ...
  60d: ...
selected_decision_values: {...}
performance_insights:
  status: AVAILABLE | DISABLED | UNSUPPORTED | ACCESS_DENIED | ERROR | NOT_NEEDED
  requested_days: 7
  observed_days: null
  summaries: null
  wait_type_summary: null
  truncation: null
normalization_version: rds-v1-exact
```

`EMPTY` is valid evidence that the request completed but the dimension was
unmeasured and therefore invokes a capacity floor. `ACCESS_DENIED` or `ERROR`
means the collection path did not complete and can produce per-kind
`INSUFFICIENT_DATA`. `INVALID` means points were returned but none survived
validation; treat it as a collection/validation failure unless another window
or persisted fallback contains usable points.

Persist summaries and bounded wait-type labels, never SQL text, user names,
client hosts, or raw one-second Performance Insights samples.

## 8. CPU model

For each candidate and tier target `u` (`0.55`, `0.70`, `0.85`):

```python
cpu_used_vcpus = current_vcpus * cpu_percent_p99 / 100
required_vcpus = cpu_used_vcpus / u
projected_cpu_percent = cpu_used_vcpus / target_vcpus * 100
```

The maximum result across 14/30/60-day windows binds. A candidate must satisfy
`target_vcpus >= required_vcpus` for that tier.

When the CPU series is `EMPTY`, the utilization formula above is not evaluated.
Use the conservative capacity floor instead:

```python
cpu_used_vcpus = None
required_vcpus = current_vcpus
projected_cpu_percent = None
cpu_capacity_pass = target_vcpus >= current_vcpus
```

The candidate has no CPU component in `projected_util`, carries
`RDS_CPU_METRIC_UNAVAILABLE_CURRENT_CAPACITY_RETAINED`, and is capped at
`CONDITIONAL` per Section 6.4. `ACCESS_DENIED`, `ERROR`, or wholly invalid CPU
telemetry does not use this fallback; it makes the instance-class evaluation
`INSUFFICIENT_DATA` unless a usable persisted fallback exists.

This is a utilization capacity model, not a cross-processor benchmark. A
different generation or architecture is not assumed to be faster. When a
reliable normalized performance score is later available it may be additive;
V1 never invents one.

For burstable current classes, any credit depletion or sustained CPU above the
documented baseline adds `BURSTABLE_CPU_REQUIRES_REVIEW`. For burstable targets,
the current class must also be burstable and projected sustained utilization
must remain below the target baseline with the policy margin. Otherwise the
target is rejected with `BURSTABLE_CPU_BASELINE_EXCEEDED`.

## 9. Memory model

RDS publishes native `FreeableMemory`; no customer agent discovery is needed.
For Linux-based engines AWS defines it from available memory, which includes
memory the OS can reclaim. Use the low tail, not the average:

```python
memory_demand_bytes = max(
    current_memory_bytes - freeable_memory_p01_by_window
    for window in usable_windows
)
projected_memory_util = memory_demand_bytes / target_memory_bytes
projected_freeable_bytes = target_memory_bytes - memory_demand_bytes
required_memory_bytes = memory_demand_bytes / tier_target_util
```

A target must have positive projected freeable memory and meet its tier ratio.
Memory demand is clamped to `[0, current_memory_bytes]`; a value outside that
range is invalid telemetry, not an excuse to clamp an arbitrary number.

When the FreeableMemory series is `EMPTY`, do not calculate a working set or
projected free memory. Retain raw current memory capacity:

```python
memory_demand_bytes = None
required_memory_bytes = current_memory_bytes
projected_memory_util = None
projected_freeable_bytes = None
memory_capacity_pass = target_memory_bytes >= current_memory_bytes
```

The candidate has no memory component in `projected_util`, carries
`RDS_MEMORY_METRIC_UNAVAILABLE_CURRENT_CAPACITY_RETAINED`, and is capped at
`CONDITIONAL`. `ACCESS_DENIED`, `ERROR`, or wholly invalid FreeableMemory does
not use this fallback; it makes the instance-class evaluation
`INSUFFICIENT_DATA` unless a usable persisted fallback exists.

Any material swap usage in the selected window prevents memory reduction from
being `ACTIONABLE`. Repeated swap or `projected_freeable_bytes` below the
absolute policy floor rejects the candidate.

SQL Server memory reporting and engine buffer behavior can make a literal
working-set projection less certain. SQL Server class reductions therefore
require a larger configurable free-memory floor and are at least
`CONDITIONAL` when the target is memory-binding.

## 10. Storage and instance I/O model

### 10.1 Class-level EBS constraints

Changing DB instance class can lower the host's EBS bandwidth or IOPS envelope.
Evaluate timestamp-aligned total IOPS and total throughput against the target
class's baseline and reliable peak.

Use the EC2-style three-band interpretation:

```text
LOW     p99 < 40% of target baseline
MEDIUM  40% <= p99 < 70% of target baseline
HIGH    p99 >= 70% of baseline, or sustained demand depends on burst
FAIL    p99 or observed max exceeds a documented reliable target maximum
UNKNOWN target capacity unavailable
```

`EBSIOBalance%` and `EBSByteBalance%` apply to the DB instance class burst
bucket and are distinct from gp2 `BurstBalance`. Depletion in any applicable
balance metric prevents `ACTIONABLE` downsizing.

Latency and queue depth are health signals, not capacity units. Elevated
latency/queue plus high IOPS/throughput is storage-pressure evidence. Elevated
latency/queue with low storage utilization is ambiguous and invokes DB-load
attribution (Section 12); it must never be interpreted as spare storage.

### 10.2 Storage-configuration candidates

V1 may propose:

```text
gp3: lower paid IOPS and/or throughput while preserving storage type
gp2: migrate to gp3 when exact regional pricing is lower and gp3 capacity fits
io1: migrate to io2 only when runtime support and exact pricing prove savings
```

The candidate always keeps `AllocatedStorage` unchanged. It also preserves
encryption, Multi-AZ, autoscaling maximum, and every unrelated setting.

For gp3:

```python
required_iops = total_iops_p99 / storage_target_util
required_throughput_mibps = (
    total_throughput_bps_p99 / 1024**2 / storage_target_util
)

target_iops = round_up_to_valid_runtime_value(
    max(required_iops, engine_storage_min_iops)
)
target_throughput = round_up_to_valid_runtime_value(
    max(required_throughput_mibps, engine_storage_min_throughput)
)
```

Call `DescribeValidDBInstanceModifications` for the current DB instance and use
its `ValidStorageOptions` as the primary modification contract. It supplies the
valid storage types and ranges for storage size, provisioned IOPS, IOPS/GiB,
provisioned throughput, and throughput/IOPS. Cross-check the selected class's
paginated orderable option and a versioned policy for included baseline
performance and billing boundaries. A value must satisfy every applicable
runtime range; static policy can narrow runtime truth but never widen it. The
API response distinguishes included from paid IOPS/throughput.

The storage recommendation is a single Balanced option using the default 70%
target. There are no storage tiers in V1 because the valid billing boundaries
and engine-specific floors commonly collapse them.

No storage recommendation is emitted when:

- directional I/O pairing is incomplete;
- less than 30 observed days or 95% coverage is available;
- current latency, queue depth, or a balance metric is unhealthy;
- storage autoscaling is actively modifying storage;
- dedicated/additional volumes exist;
- runtime storage constraints or exact pricing are unknown;
- savings don't meet the floor.

## 11. Network, connections, and database-health evidence

Evaluate receive and transmit p99 independently against documented target class
baseline and peak bandwidth. Use the same 40%/70% three-band model as EC2.
Unknown baseline, burst dependence, or an exceeded reliable peak follows the
same `CONDITIONAL`/`REJECTED` behavior as class-level EBS.

EC2's assumed-baseline fallback (EC2 spec Section 6.3, a size-proportional
estimate tagged `NETWORK_BASELINE_ASSUMED`) is deliberately not adopted for
RDS in V1: it would borrow EC2 family shaping assumptions across the `db.`
boundary, which Section 4.2 forbids. An RDS class without a documented
baseline therefore stays `CONDITIONAL` with `TARGET_NETWORK_CAPACITY_UNKNOWN`
or `TARGET_EBS_CAPACITY_UNKNOWN`, and the UI shows an unknown-capacity
treatment, never an estimated ratio. Older classes with sparse documentation
will yield more `CONDITIONAL` results; that is the intended conservative
behavior, not a gap.

`DatabaseConnections` does not have one universal class limit. Engine
parameters, custom parameter groups, reserved administrative sessions, and
per-connection memory all matter. V1 therefore:

- reports p99/max connections and current effective `max_connections` when it
  can be resolved safely;
- warns when p99 exceeds the configured policy ratio;
- never fabricates a target connection limit from RAM alone;
- invokes DB-load attribution when connections are high or spiky;
- caps at `CONDITIONAL` when connection headroom on a smaller target cannot be
  established.

Replica lag, recent RDS failure/recovery events, low free storage, swap,
depleted credits, and pending maintenance are operational warnings. A
read replica with unhealthy lag is never an `ACTIONABLE` class reduction.

## 12. Database load attribution

### 12.1 Why and when it runs

CloudWatch CPU says how busy the host is; it doesn't explain whether database
sessions are running on CPU or waiting on I/O, locks, latches, or concurrency.
Performance Insights `db.load.avg` expresses average active sessions (AAS) and
can split them by wait-event type.

Load attribution runs only when at least one candidate is otherwise viable and
one of these ambiguity triggers is true:

```text
projected CPU is at or above the tier's medium band
CPU, latency, queue depth, IOPS, or throughput signals disagree
connections are high or bursty
swap or memory pressure is near a warning threshold
the target depends on burst EBS/network capacity
an applicable standard metric is missing
```

For a clearly underutilized database with ample CPU, memory, storage, network,
and connection headroom, absence of Performance Insights alone does not prevent
`ACTIONABLE`.

### 12.2 Collection contract

When `PerformanceInsightsEnabled` is true, call `GetResourceMetrics` with:

```text
ServiceType: RDS
Identifier: DbiResourceId
Metric: db.load.avg
PeriodInSeconds: 300
StartTime: min(scan_end - configured attribution window, retained start)
EndTime: scan_end
```

Submit one ungrouped total-load query and one query grouped by
`db.wait_event_type` / `db.wait_event_type.name` with the maximum supported
group limit of 25. Follow every `NextToken`.
The default requested window is seven days because that matches default
retention. Longer configured retention may be used up to 60 days, but the
response always discloses actual observed days.

At each timestamp:

```python
db_load_total = ungrouped db.load.avg
db_load_cpu = grouped value whose wait-event type is CPU
db_load_known_non_cpu = sum(other returned wait-event types)
db_load_unattributed = max(db_load_total - grouped_sum, 0)
```

Missing CPU group means zero only when the grouped response is complete and
total load exists for that timestamp. Top-N truncation or pagination failure
makes the remainder `unattributed`, never zero.

Compute p95, p99, max, observed days, and time-weighted wait shares locally.
Persist bounded wait-event **type** labels. V1 does not call
`DescribeDimensionKeys` and does not collect optional wait-event names, SQL,
users, databases, applications, or hosts. This keeps the diagnostic surface
bounded to the two `GetResourceMetrics` queries above.

### 12.3 Decision use

For each target:

```python
projected_cpu_aas_ratio = db_load_cpu_p99 / target_vcpus
projected_total_aas_ratio = db_load_total_p99 / target_vcpus
```

- CPU AAS above the tier's CPU target rejects that tier with
  `DB_LOAD_CPU_EXCEEDS_TARGET` even if coarse CloudWatch CPU would pass.
- Total AAS above target vCPUs with mainly non-CPU waits adds
  `NON_CPU_DB_LOAD_REQUIRES_REVIEW` and caps the result at `CONDITIONAL`; adding
  vCPUs is not automatically the remedy for I/O or lock waits.
- Material I/O waits plus storage pressure reject a storage reduction and cap
  class reduction at `CONDITIONAL` unless the target's I/O headroom is LOW.
- Material lock/concurrency waits are a query/application issue. They don't
  create a larger-class recommendation and cap a downsize at `CONDITIONAL`.
- A material unattributed share adds `DB_LOAD_ATTRIBUTION_INCOMPLETE`.

Performance Insights refines risk; it never relaxes a failed CloudWatch hard
constraint.

### 12.4 Disabled, unsupported, or inaccessible

If attribution is required and Performance Insights is disabled:

```text
PERFORMANCE_INSIGHTS_REQUIRED_FOR_ATTRIBUTION
```

The otherwise valid candidate remains visible as `CONDITIONAL`. The response
includes a read-only enablement prompt:

```text
The standard metrics are close or contradictory, so database-load attribution
would improve this recommendation. Enable Database Insights Standard with
Performance Insights (7-day retention is sufficient), then rescan. Enabling
Performance Insights does not require a reboot, failover, or database outage.
```

The product must not enable it automatically. `AccessDenied` or API error uses
`PERFORMANCE_INSIGHTS_TELEMETRY_UNAVAILABLE`, also `CONDITIONAL`, and does not
claim the feature is disabled.

When the engine/class doesn't support Performance Insights, use
`PERFORMANCE_INSIGHTS_UNSUPPORTED`; show manual review guidance, not an enable
button.

## 13. Hard constraints and warnings

Hard constraints reject only the affected candidate:

```text
ENGINE_CLASS_COMBINATION_UNAVAILABLE
TARGET_COMPUTE_CAPACITY_UNKNOWN
FAMILY_NOT_ELIGIBLE
CPU_CAPACITY_EXCEEDED
MEMORY_CAPACITY_EXCEEDED
MEMORY_ABSOLUTE_FLOOR_VIOLATED
DB_LOAD_CPU_EXCEEDS_TARGET
BURSTABLE_CPU_BASELINE_EXCEEDED
NETWORK_RELIABLE_MAX_EXCEEDED
EBS_RELIABLE_MAX_EXCEEDED
STORAGE_RUNTIME_CONSTRAINT_VIOLATED
CURRENT_MEMORY_PRESSURE_BLOCKS_DOWNSIZE
```

Warnings keep a viable target visible and normally cap it at `CONDITIONAL`:

```text
OBSERVATION_WINDOW_TOO_SHORT
OBSERVATION_COVERAGE_TOO_LOW
RDS_CPU_METRIC_UNAVAILABLE_CURRENT_CAPACITY_RETAINED
RDS_MEMORY_METRIC_UNAVAILABLE_CURRENT_CAPACITY_RETAINED
PERFORMANCE_INSIGHTS_REQUIRED_FOR_ATTRIBUTION
PERFORMANCE_INSIGHTS_TELEMETRY_UNAVAILABLE
PERFORMANCE_INSIGHTS_UNSUPPORTED
DB_LOAD_ATTRIBUTION_INCOMPLETE
NON_CPU_DB_LOAD_REQUIRES_REVIEW
STORAGE_LATENCY_REQUIRES_REVIEW
STORAGE_QUEUE_REQUIRES_REVIEW
STORAGE_DIRECTIONAL_METRICS_INCOMPLETE
NETWORK_DIRECTIONAL_METRICS_INCOMPLETE
EBS_BURST_DEPENDENCE_REQUIRES_REVIEW
NETWORK_BURST_DEPENDENCE_REQUIRES_REVIEW
TARGET_NETWORK_CAPACITY_UNKNOWN
TARGET_EBS_CAPACITY_UNKNOWN
CONNECTION_HEADROOM_REQUIRES_REVIEW
SWAP_USAGE_REQUIRES_REVIEW
CPU_CREDIT_DEPLETION_REQUIRES_REVIEW
STORAGE_CREDIT_DEPLETION_REQUIRES_REVIEW
REPLICA_LAG_REQUIRES_REVIEW
RECENT_RDS_EVENT_REQUIRES_REVIEW
SQLSERVER_MEMORY_PROJECTION_REQUIRES_REVIEW
```

`RDS_CLOUDWATCH_COLLECTION_FAILED` is an evaluation-level reason that produces
per-kind `INSUFFICIENT_DATA`; it is not a candidate warning.

Completed storage evaluations with status `NO_RECOMMENDATION` use stable
blocker codes in `evaluation_reason_codes.storage_configuration`, including:

```text
STORAGE_OBSERVATION_WINDOW_TOO_SHORT
STORAGE_OBSERVATION_COVERAGE_TOO_LOW
STORAGE_DIRECTIONAL_METRICS_INCOMPLETE
```

Storage evaluations with status `NOT_APPLICABLE` instead use configuration
reason codes such as `MULTI_VOLUME_STORAGE_OPTIMIZATION_UNSUPPORTED` or
`STORAGE_CONFIGURATION_KIND_NOT_APPLICABLE`. These are applicability reasons,
not blockers from a completed target evaluation.

Thresholds create warnings; they never alter normalized telemetry.

## 14. Tiered instance-class options

`DB_INSTANCE_CLASS_CHANGE` uses the shared tier targets:

```text
Conservative  55% maximum projected utilization
Balanced      70% maximum projected utilization (default)
Aggressive    85% maximum projected utilization
```

Tier placement uses compute-and-memory projected utilization only, matching
EC2 spec Section 25.2, plus the CPU-AAS ratio when attribution ran — CPU AAS
is a CPU capacity measure, so including it preserves EC2's meaning of the
tier figure as "compute headroom on the target":

```python
projected_components = [
    value
    for value in (
        projected_cpu_percent / 100 if projected_cpu_percent is not None else None,
        projected_memory_util,
        projected_cpu_aas_ratio_when_available,
    )
    if value is not None
]
projected_util = max(projected_components) if projected_components else None
```

If no projected compute/memory value exists, `projected_util` is `null` and the
candidate participates only in the savings-ranked recommendation list; it does
not enter a tier and its `satisfied_tiers` is empty. A metric's
capacity-retention floor still applies before this selection layer.

Network and EBS ratios never move a candidate between tiers; as in EC2 they
drive risk bands, warnings, and classification (Sections 10, 11, and 15). A
tier card's "maximum projected utilization" therefore means the same thing in
the RDS and EC2 products.

Generate at the Aggressive boundary, then select the cheapest compatible
candidate per tier. Reuse EC2's nesting, deduplication, tier collapse, savings
floor, candidate limit reservation, and deterministic tie breakers. Balanced
is the default when present.

Binding dimension is one of:

```text
cpu | memory | db_load_cpu | storage_iops | storage_throughput | network
```

`projected_util` describes compute/memory tier placement. `binding_dimension`
describes the highest known limiting ratio across the entire candidate, so it
may be network or storage even though those dimensions never move tier
placement. It is `null` when all comparable ratios are unavailable. Health-only
warnings such as locks or latency can change classification but do not
masquerade as a capacity binding dimension.

## 15. Classification

Candidate classification uses:

```text
ACTIONABLE · CONDITIONAL · REJECTED
```

Resource-level classification uses `ACTIONABLE`, `CONDITIONAL`, `DEFERRED`,
`INSUFFICIENT_DATA`, or `null` (evaluated successfully with no cost-saving
recommendation). Section 16 defines its derivation from the independent
recommendation kinds.

### ACTIONABLE

Allowed only when the resource passes scope gates; required telemetry is
sufficient; all hard constraints pass; CPU, memory, storage/EBS, and network
risk are LOW; pricing and runtime compatibility are exact; and no blocking
warning remains. Performance Insights may be `NOT_NEEDED` or may have resolved
an ambiguity cleanly.

### CONDITIONAL

The target math and pricing are complete and no hard constraint fails, but a
review signal remains. This includes required DB-load attribution being
disabled/unavailable, non-CPU waits, unknown baselines, burst dependence,
connection uncertainty, SQL Server memory uncertainty, or operational health
warnings.

### REJECTED

The specific evaluated target fails a hard capacity, compatibility, runtime
storage, pricing-eligibility, or savings rule. Rejections appear in candidate
diagnostics/tallies, not as the default recommendation card.

### DEFERRED

The database configuration is outside V1 scope. Scope is evaluated before
telemetry collection.

### INSUFFICIENT_DATA

The database is in scope, but one or more recommendation-kind evaluations could
not run because collection/validation failed and no usable persisted fallback
exists. It is not caused by a successfully queried but empty CPU or
FreeableMemory series; those use the capacity-retention rules in Section 6.4.
Short or sparse telemetry likewise produces `CONDITIONAL` candidates.

There is no `OPPORTUNITY` or `PREVIEW` classification in RDS V1.

## 16. Risk and response contract

Each option carries:

```yaml
risk_assessment:
  telemetry: LOW | MEDIUM | HIGH
  compute: LOW | MEDIUM | HIGH
  memory: LOW | MEDIUM | HIGH
  db_load: LOW | MEDIUM | HIGH | NOT_AVAILABLE | NOT_NEEDED
  storage: LOW | MEDIUM | HIGH
  network: LOW | MEDIUM | HIGH
  connections: LOW | MEDIUM | HIGH
  compatibility: LOW | MEDIUM | HIGH
  operations: LOW | MEDIUM | HIGH
  overall: LOW | MEDIUM | HIGH
  reason_codes: []
```

`overall` is the highest applicable component. Classification is determined by
Section 15, not inferred from `overall` alone.

Each recommendation kind also reports an evaluation status:

```text
RECOMMENDED       at least one recommendation of this kind is returned
NO_RECOMMENDATION evaluation completed but no eligible saving exists
INSUFFICIENT_DATA collection/validation failure prevented this evaluation
NOT_APPLICABLE    the kind does not apply to this configuration
DEFERRED          the resource-level scope gate prevented all evaluation
```

Status mapping is deterministic:

| Condition | Instance class | Storage configuration |
| --- | --- | --- |
| Resource-level scope gate in Section 2.2 | `DEFERRED` | `DEFERRED` |
| Complete evaluation with one or more returned options | `RECOMMENDED` | `RECOMMENDED` |
| Complete evaluation, but no target passes constraints, pricing, savings, or policy | `NO_RECOMMENDATION` | `NO_RECOMMENDATION` |
| CloudWatch/RDS/pricing collection or validation failure prevents this kind from running and no persisted fallback exists | `INSUFFICIENT_DATA` | `INSUFFICIENT_DATA` |
| Dedicated log/additional volumes (`MULTI_VOLUME_STORAGE_OPTIMIZATION_UNSUPPORTED`) | evaluate normally; at most `CONDITIONAL` when combined EBS demand is unproved | `NOT_APPLICABLE` |
| Current storage type has no V1 storage recommendation kind, including magnetic | evaluate normally | `NOT_APPLICABLE` |
| Storage history/coverage below its hard floor | unaffected | `NO_RECOMMENDATION` with `STORAGE_OBSERVATION_WINDOW_TOO_SHORT` or `STORAGE_OBSERVATION_COVERAGE_TOO_LOW` |
| Directional storage evidence incomplete or current storage health is unsafe | evaluate class under Sections 10–15 | `NO_RECOMMENDATION` with the applicable storage blocker |

`NOT_APPLICABLE` means the recommendation kind is outside its own V1 lane while
the resource remains in scope; it never defers the other kind. A runtime or
pricing API failure is `INSUFFICIENT_DATA`, not `NOT_APPLICABLE`. A known target
that fails a constraint is a candidate rejection inside a completed evaluation,
so the kind is `NO_RECOMMENDATION` when no other target survives.

Top-level `classification` is derived as follows:

1. A scope gate returns `DEFERRED` and both kind statuses are `DEFERRED`.
2. When recommendations exist, use the classification of the headline option:
   the default tiered class option, otherwise the highest-savings
   capacity-retaining class candidate, otherwise the storage option.
3. When no recommendation exists and any applicable kind is
   `INSUFFICIENT_DATA`, return `INSUFFICIENT_DATA`.
4. When every applicable kind completed without a recommendation, return
   `null`.

Thus an actionable storage option remains top-level `ACTIONABLE` even when the
instance-class evaluation is `INSUFFICIENT_DATA`.

### 16.1 Detail response

```yaml
inventory_id: 123
resource_id: database-1
account_id: "123456789012"
region: us-east-1
engine: postgres
engine_version: "16.3"
license_model: postgresql-license
multi_az: true
current:
  db_instance_class: db.m6g.xlarge
  vcpus: 4
  memory_gib: 16
  storage_type: gp3
  allocated_storage_gib: 500
  iops: 12000
  storage_throughput_mibps: 500
  monthly_cost: 0
classification: ACTIONABLE | CONDITIONAL | DEFERRED | INSUFFICIENT_DATA | null
evaluation_status:
  instance_class: RECOMMENDED | NO_RECOMMENDATION | INSUFFICIENT_DATA | NOT_APPLICABLE | DEFERRED
  storage_configuration: RECOMMENDED | NO_RECOMMENDATION | INSUFFICIENT_DATA | NOT_APPLICABLE | DEFERRED
evaluation_reason_codes:
  instance_class: []
  storage_configuration: []
recommendations:
  - kind: DB_INSTANCE_CLASS_CHANGE
    classification: ACTIONABLE
    tiers:
      default: balanced
      conservative: {...} | null
      balanced: {...} | null
      aggressive: {...} | null
    candidates: [...]
  - kind: STORAGE_CONFIGURATION_CHANGE
    classification: ACTIONABLE | CONDITIONAL
    target_storage: {...}
    monthly_savings: 0
    evidence: {...}
telemetry_summary: {...}
database_load_attribution:
  status: AVAILABLE | DISABLED | UNSUPPORTED | ACCESS_DENIED | ERROR | NOT_NEEDED
  required: true | false
  enablement_prompt: null | {...}
  total_load: null | {...}
  cpu_load: null | {...}
  wait_type_shares: []
policy: {...}
pricing_scope: {...}
operational_note: string
availability_note: string
```

The literal `monthly_cost: 0` values above are shape placeholders, not defaults.
JSON numeric values contain calculated prices or `null` where explicitly
allowed.

### 16.2 Fleet response

The list endpoint returns one compact row per `inventory_id`:

```text
identity, engine, deployment, current class, default target class,
instance-class classification/savings, storage target/savings,
binding dimension, overall risk, PI/DB-load status, observed days,
reason codes, generated_at
```

Fleet totals must not add both independent recommendation savings for the same
database. Use the default instance-class recommendation when present, otherwise
the highest-savings capacity-retaining class candidate when present, otherwise
the storage recommendation. This is the same headline selection order as
Section 16. Label the total as non-composable.

## 17. Confidence trend

The on-demand detail trend uses `GetMetricData` for up to 15 months:

- CPU: daily `Maximum` headline;
- freeable memory: daily `Minimum` headline;
- connections: daily `Maximum` context;
- free storage: daily `Minimum` context.

Do not show a 15-month IOPS, throughput, latency, or network peak. Past
CloudWatch retention boundaries, coarse rollups smear bursts and can't preserve
an honest five-minute peak. Those dimensions show the 60-day decision p99/max.

Buckets are 30d, 90d, 120d, 180d, 365d, and 455d. CPU/connection/free-memory
headline buckets are computed from daily series. Return explicit semantics:

```text
cpu maximum: trailing_window_from_daily_maximum
freeable memory minimum: trailing_window_from_daily_minimum
connections maximum: trailing_window_from_daily_maximum
```

Bucket percentiles follow the EC2 mechanism: p99/p95 for maximum-headline
series (CPU, connections) and p01/p05 for the freeable-memory minimum
headline are server-side large-period queries that use only the latest
complete epoch-aligned datapoint, returning `null` when no complete
datapoint exists. The response labels them
`latest_complete_epoch_aligned_window` in `bucket_semantics` so consumers
never present them as true trailing-window percentiles.

When Performance Insights is enabled, the evidence panel may request an
additional DB-load trend only up to actual configured retention. It is labeled
separately and is never presented as a 15-month series unless 15 months truly
exist.

## 18. Configurable policy

```python
@dataclass(frozen=True)
class RdsRightsizerPolicy:
    conservative_target_util: float = 0.55
    balanced_target_util: float = 0.70
    aggressive_target_util: float = 0.85

    min_observed_days: float = 7.0
    min_required_coverage_ratio: float = 0.90
    storage_min_observed_days: float = 30.0
    storage_min_coverage_ratio: float = 0.95

    network_medium_ratio: float = 0.40
    network_high_ratio: float = 0.70
    ebs_medium_ratio: float = 0.40
    ebs_high_ratio: float = 0.70
    connection_warning_ratio: float = 0.70

    memory_absolute_free_floor_gib: float = 1.0
    sqlserver_memory_absolute_free_floor_gib: float = 2.0
    swap_warning_bytes: int = 52_428_800
    free_storage_warning_ratio: float = 0.15
    replica_lag_warning_seconds: float = 30.0

    latency_warning_seconds_by_engine: dict = field(
        default_factory=lambda: {
            **{engine: 0.020 for engine in SUPPORTED_ENGINES},
            "*": 0.020,
        }
    )
    queue_depth_warning_by_storage: dict = field(
        default_factory=lambda: {kind: 5.0 for kind in ("gp2", "gp3", "io1", "io2")}
    )
    pi_cpu_trigger_percent: float = 35.0
    pi_latency_trigger_seconds: float = 0.020
    pi_queue_depth_trigger: float = 5.0
    pi_connections_trigger: float = 100.0
    db_load_unattributed_warning_ratio: float = 0.10
    attribution_default_days: int = 7
    attribution_max_days: int = 60

    monthly_hours: Decimal = Decimal("730")
    min_monthly_savings: Decimal = Decimal("0.01")
    gated_previous_generation_families: tuple[str, ...] = ()
    policy_version: str = "rds-v1"
```

`min_observed_days` and `min_required_coverage_ratio` are RDS-specific thin-data
review thresholds (Section 6.4) that cap candidates at `CONDITIONAL`; they are
not hard gates. `storage_min_observed_days` and
`storage_min_coverage_ratio` remain hard emission requirements for the storage
recommendation only. Unlike an unmeasured compute dimension, reduced provisioned
IOPS/throughput has no capacity-retention fallback; RDS can also block another
storage change for six hours after modification starts. V1 therefore requires
the stronger history before it emits a storage-performance reduction.

Engine latency thresholds are policy because acceptable latency is workload-
specific; they create review warnings, never rewrite raw latency. Every response
returns the effective policy and version.

## 19. API contract

```text
GET /recommendations/rds/rightsize
GET /recommendations/rds/rightsize/{inventory_id}
GET /recommendations/rds/rightsize/{inventory_id}/trend
```

List filters:

```text
account_id, region, engine, state, classification,
min_monthly_savings, candidate_limit
```

Unknown inventory IDs return 404. An inventory row of another resource type
also returns 404. AWS permission/transport errors map to structured telemetry
status and reason codes where a partial result is safe; otherwise return the
shared service error envelope.

The timing boundary is mandatory:

- **Scan-time failure:** persist `ACCESS_DENIED`, `ERROR`, or `INVALID` plus the
  stable reason code. Later list/detail reads return HTTP 200 and map only the
  affected recommendation kind to `INSUFFICIENT_DATA`; a complete independent
  kind remains visible.
- **Live request-time failure:** on-demand trend calls and any detail/list call
  that must contact AWS because no usable persisted assessment exists use the
  shared error envelope. Do not manufacture a new domain classification from a
  transient request failure. When a safe persisted/partial response exists,
  return it with the affected live subdocument's structured error status.

## 20. Permissions, privacy, and cost discipline

Minimum read permissions:

```text
rds:DescribeDBInstances
rds:DescribeOrderableDBInstanceOptions
rds:DescribeValidDBInstanceModifications
rds:DescribeDBParameters
rds:DescribePendingMaintenanceActions
rds:DescribeEvents
rds:ListTagsForResource
cloudwatch:GetMetricData
pi:GetResourceMetrics
pricing:GetProducts
```

`pi:GetResourceMetrics` is optional for a base assessment but required to
resolve Section 12 ambiguity. `DescribeDBParameters` is used only for
connection-limit context; inability to read it does not block basic CPU/memory
sizing.

Use `GetMetricData`, batch queries under service limits, paginate every token,
and cache the regional orderable-options, class capability, storage policy, and
pricing catalogs. Performance Insights `GetResourceMetrics` accepts at most 15
metric queries per call; this design normally uses two.

No SQL text is requested or stored. This is both a privacy boundary and a
product boundary: the rightsizer sizes infrastructure; it doesn't inspect
customer queries.

## 21. Required customer-facing messages

Every class recommendation:

```text
Changing an RDS DB instance class causes an outage during the change. Schedule
the modification for a maintenance window, test connection recovery, and
review all pending modifications before applying it.
```

Every Multi-AZ recommendation additionally:

```text
A Multi-AZ modification can trigger failover. Existing connections must
reconnect and DNS caching must allow the application to resolve the current
RDS endpoint.
```

Every storage recommendation:

```text
Allocated storage is unchanged because RDS storage can't be reduced. This
recommendation changes only storage type and/or provisioned performance.
Storage modification can temporarily degrade performance and RDS can block
another storage change for six hours after it begins.
```

The conditional Performance Insights message is defined in Section 12.4.

## 22. Module boundaries

```text
rds_rightsizer/
├── models.py
├── inventory.py
├── telemetry/
│   ├── cloudwatch.py
│   ├── performance_insights.py
│   └── trend.py
├── normalization/
│   ├── statistics.py
│   ├── memory.py
│   ├── storage.py
│   └── network.py
├── catalogs/
│   ├── orderable_options.py
│   ├── class_capabilities.py
│   ├── storage_capabilities.py
│   └── pricing.py
├── requirements/
│   ├── compute.py
│   ├── memory.py
│   └── storage.py
├── constraints/
│   ├── compatibility.py
│   ├── compute.py
│   ├── storage.py
│   └── network.py
├── warnings/
│   ├── database_health.py
│   └── messages.py
├── selection/
│   ├── instance_class.py
│   └── storage_configuration.py
└── risk/
    └── assessment.py
```

Exact normalization modules contain no policy thresholds. DB-load attribution
can increase risk or reject on explicit CPU AAS capacity, but it cannot mutate
CloudWatch samples.

## 23. Required tests and acceptance invariants

### Scope and inventory

- Aurora, Multi-AZ clusters, RDS Custom, pending modifications, and custom
  processor configurations defer before telemetry.
- Standard Multi-AZ DB instances remain one row and preserve deployment type.
- Read replicas are independent rows; no topology reduction is emitted.
- `DbiResourceId` is used for Performance Insights, never the DB identifier.

### Candidate compatibility

- Every target exists in fully paginated orderable options for exact engine
  version/license/region.
- Current encryption, Multi-AZ, network type, storage, IOPS, throughput, and PI
  support are preserved.
- Missing target-class vCPU/memory capability rejects; missing network/EBS
  baseline capacity cannot be actionable.
- Graviton targets are ordinary candidates only when returned by AWS.

### Normalization and statistics

- IOPS/throughput rates are not divided by 300.
- Read/write storage rates combine per timestamp before percentiles.
- Network directions remain separate and convert bytes/s to decimal Mbps.
- Missing points are absent; invalid values are counted and excluded.
- p99/p01 are local over normalized 14/30/60-day windows.
- Observed days and coverage use actual samples/resource age.
- A successful empty metric result persists `EMPTY`; AccessDenied/API failure
  persists collection failure and is never mistaken for an empty series.

### CPU and memory

- CPU used-vCPU math and target projection are exact.
- Memory demand uses the lowest selected p01 FreeableMemory window.
- Swap and the absolute free-memory floor prevent unsafe downsizes.
- Burstable targets cannot hide sustained CPU above baseline.
- A successfully queried empty CPU series retains current vCPU capacity; an
  empty FreeableMemory series retains current memory capacity.
- Missing one dimension caps class candidates at `CONDITIONAL`; missing both
  yields a null `projected_util` and null tiers.
- AccessDenied/error/invalid telemetry uses no capacity fallback and maps only
  the affected kind to `INSUFFICIENT_DATA` when no persisted fallback exists.

### Storage and network

- Target class EBS and network capacities are checked independently.
- Reliable peak violations reject; unknown/burst capacities cannot be
  actionable.
- Allocated storage never decreases in any response.
- gp3 target IOPS/throughput round upward and satisfy every runtime ratio/floor.
- Storage values satisfy `DescribeValidDBInstanceModifications`; static policy
  never widens its returned ranges.
- Storage recommendations require 30 days/95% evidence and clean health.
- Savings use exact regional storage dimensions.

### Performance Insights

- It is queried only after an ambiguity trigger and with `DbiResourceId`.
- Total load and grouped wait types are separate queries and paginate fully.
- CPU/non-CPU/unattributed load is derived per timestamp before statistics.
- Top-N truncation becomes unattributed load, never zero.
- CPU AAS can reject a target but can never relax a CloudWatch failure.
- Disabled PI on an ambiguous candidate yields `CONDITIONAL` plus enablement
  guidance; a clearly underutilized candidate doesn't require PI.
- Unsupported PI never offers an enable action.
- No query requests or persists SQL/user/host dimensions.

### Classification and pricing

- `ACTIONABLE` requires LOW risk in every capacity dimension and no blocking
  warning.
- Telemetry below `min_observed_days` yields `CONDITIONAL` with
  `OBSERVATION_WINDOW_TOO_SHORT`; low coverage yields `CONDITIONAL` with
  `OBSERVATION_COVERAGE_TOO_LOW`. Neither yields `INSUFFICIENT_DATA` or a
  silent `ACTIONABLE`.
- Missing CPU retains current vCPU capacity; missing FreeableMemory retains
  current memory capacity. Either is HIGH telemetry risk and `CONDITIONAL`.
- With both metrics absent, capacity-retaining candidates can remain in the
  savings-ranked list, but `projected_util` and every tier are `null`.
- A collection/validation failure is `INSUFFICIENT_DATA` only for the affected
  recommendation kind and never suppresses an independently complete kind.
- Multi-volume storage is `NOT_APPLICABLE` only for storage configuration;
  class evaluation continues. Thin/incomplete storage evidence is
  `NO_RECOMMENDATION`, not a resource-level deferral.
- Top-level classification follows the headline returned recommendation;
  `INSUFFICIENT_DATA` is used only when no recommendation exists and an
  applicable kind could not run. A complete no-savings result is `null`.
- Tier placement uses only CPU, memory, and CPU-AAS projections; changing a
  network or EBS ratio can change classification but never a candidate's tier.
- Independent class/storage savings are never added for fleet totals.
- Missing exact pricing emits no recommendation.
- Tier selection is deterministic and Balanced is the default.
- Scan-time failures persist per-kind status for later HTTP 200 reads; live
  request-time failures use the shared error envelope when no safe persisted or
  partial response exists.

### Confidence trend

- CPU uses daily Maximum and freeable memory daily Minimum.
- Long-range I/O/network peaks are not claimed.
- Trend failure never hides a valid recommendation.

## 24. V1 product guarantee

An `ACTIONABLE` RDS recommendation guarantees only that, under the stated
policy and observed telemetry:

- AWS currently lists the target for the exact database configuration;
- normalized CPU, memory, storage/EBS, and network demand fits the target with
  the selected headroom;
- applicable health and burst signals are clean;
- exact catalog pricing shows savings; and
- no unresolved DB-load attribution question remains.

It does not guarantee query latency, future workload behavior, live capacity,
application reconnection, modification duration, zero downtime, or successful
application of the change.

## 25. AWS source notes

The following primary references anchor facts that should be revalidated when
the capability-policy version changes:

- [Amazon RDS CloudWatch metrics](https://docs.aws.amazon.com/AmazonRDS/latest/UserGuide/rds-metrics.html)
- [Performance Insights API: GetResourceMetrics](https://docs.aws.amazon.com/performance-insights/latest/APIReference/API_GetResourceMetrics.html)
- [Performance Insights dimensions](https://docs.aws.amazon.com/performance-insights/latest/APIReference/API_DimensionGroup.html)
- [Database load and average active sessions](https://docs.aws.amazon.com/AmazonRDS/latest/UserGuide/USER_PerfInsights.Overview.ActiveSessions.html)
- [Performance Insights / Database Insights transition](https://docs.aws.amazon.com/AmazonRDS/latest/UserGuide/USER_PerfInsights.Overview.html)
- [Turning Performance Insights on and off](https://docs.aws.amazon.com/AmazonRDS/latest/UserGuide/USER_PerfInsights.Enabling.html)
- [RDS DB instance class hardware](https://docs.aws.amazon.com/AmazonRDS/latest/UserGuide/Concepts.DBInstanceClass.Summary.html)
- [Orderable DB instance options](https://docs.aws.amazon.com/AmazonRDS/latest/APIReference/API_DescribeOrderableDBInstanceOptions.html)
- [Valid DB instance modifications](https://docs.aws.amazon.com/AmazonRDS/latest/APIReference/API_DescribeValidDBInstanceModifications.html)
- [DB instance modification settings](https://docs.aws.amazon.com/AmazonRDS/latest/UserGuide/USER_ModifyInstance.Settings.html)
- [ModifyDBInstance outage behavior](https://docs.aws.amazon.com/AmazonRDS/latest/APIReference/API_ModifyDBInstance.html)
