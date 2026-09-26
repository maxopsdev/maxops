# ElastiCache Rightsizer V1 Specification

This spec deliberately reuses the EC2 rightsizer's decision architecture
(`rightsizers/ec2/ec2_rightsizer_spec.md`) so users get the same experience:
exact normalization, hard constraints separated from warnings, per-dimension
risk, ACTIONABLE/CONDITIONAL/REJECTED/DEFERRED classification, tiered options,
the 60-day decision scan, and the 15-month confidence trend. Sections below
state what carries over unchanged and specify only what is genuinely
ElastiCache-specific.

---

# 1. Engine and resource scope

V1 evaluates **Redis and Valkey** replication groups only.

| Context | Reason code | Classification |
| --- | --- | --- |
| Memcached cluster | `UNSUPPORTED_ENGINE` | DEFERRED |
| Serverless cache | `SERVERLESS_CACHE` | DEFERRED |
| Global Datastore member (primary or secondary) | `MANAGED_BY_GLOBAL_DATASTORE` | DEFERRED |
| Application Auto Scaling target attached (replicas or shards) | `MANAGED_BY_AUTO_SCALING` | DEFERRED |
| Member cache cluster of a replication group (legacy inventory row; Section 2) | `MEMBER_OF_REPLICATION_GROUP` | DEFERRED |
| Replication group status not `available` | `RESOURCE_NOT_AVAILABLE` | DEFERRED |

These gates run **before candidate generation**, exactly like EC2's Section 24
scope guard. Memcached is deferred because its vertical scale path replaces
the cluster and discards all cached data — a different user promise than the
online `ModifyReplicationGroup` path this product implies. Auto-scaled groups
are deferred because a standalone resize fights the scaling policy.

The auto-scaling gate **fails closed on evidence, never open on absence**:
only a confirmed attachment (`auto_scaling_attached is True`) defers. When
the check itself could not run (AccessDenied on
`application-autoscaling:DescribeScalableTargets`, an API error, or a scan
predating the check), `auto_scaling_attached` is `None`; evaluation proceeds
but every candidate carries the `AUTO_SCALING_STATE_UNKNOWN` warning and
classification is capped at CONDITIONAL — an unverified scope gate can never
support ACTIONABLE. The onboarding IAM policy must include
`application-autoscaling:DescribeScalableTargets` so the confirmed path is
the norm, not the exception.

Application Auto Scaling identifiers are normalized before the scope join:
`replication-group/<id>` maps to the canonical replication-group id and
`cache-cluster/<id>` maps to a standalone cluster. A matching target in any
supported ElastiCache scalable dimension (`NodeGroups`, `Replicas`, or cache
cluster `Nodes`) sets `auto_scaling_attached = True`. `False` is valid only
after every response page was consumed successfully; an incomplete paginated
call is `None`, never a partial negative.

The scanner persists into `ElasticacheInventory.metadata_json`:

```text
engine, engine_version, cluster_mode_enabled, automatic_failover,
multi_az, num_node_groups (shards), replicas_per_node_group,
member_cluster_ids, member_roles (per member: primary | replica | unknown,
with node-group id), member_created_at, global_datastore_member,
auto_scaling_attached (True | False | None when the check errored),
data_tiering_enabled, snapshot_retention_limit,
allowed_scale_up_types / allowed_scale_down_types
(from ListAllowedNodeTypeModifications; null when the call errored),
reserved_memory_percent / reserved_memory_bytes
(from the effective parameter group, Section 6.1; null when unresolved)
```

`INSUFFICIENT_DATA` remains reserved. Thin telemetry is disclosed via
`observed_days` (Section 15) and does not change **node-type** classification.
Replica removal has the narrower Section 11.3 exception: insufficient read
coverage/history prevents ACTIONABLE because observed zeroes cannot prove an
unused replica across an inadequately observed window.

---

# 2. Unit of evaluation: the replication group

The recommendation unit is the **replication group** (for a standalone
cluster that belongs to no replication group, the cluster itself). All
member nodes share one node type; metrics are per node (`CacheClusterId`
dimension).

**Inventory identity rule.** The scanner persists exactly one inventory row
per replication group. A `describe_cache_clusters` result whose
`ReplicationGroupId` is set is a member node of a group, not an independent
resource: it is never persisted as its own `elasticache` inventory row, and
the rightsizer defers any legacy member-cluster row it encounters with
`MEMBER_OF_REPLICATION_GROUP` (Section 1), so a group can never be
evaluated — or counted in savings — more than once. Only a cluster with no
`ReplicationGroupId` is retained as an independent unit.

This rule binds **every** persistence path, not just initial inventory
collection: the check/finding phase re-persists resources returned by
checks, and the ElastiCache checks fetch replication groups and member
clusters directly from the adapter. One shared member-cluster predicate is
applied at every point that writes `elasticache` inventory, so member rows
cannot re-enter through a side door.

Checks may still inspect member clusters when their evidence is node-scoped,
but they must return a **canonical replication-group payload** for persistence
and finding attachment. In particular, `elasticache_low_item_count` fetches
`CurrItems`/`KeyCount` by `CacheClusterId`; it never queries a fabricated
`ReplicationGroupId` CloudWatch dimension. At each timestamp its group value is
the sum, across shards, of the maximum available member value in each shard
(replicas duplicate a shard's keyspace and must not be added together). A
timestamp missing every member for any shard is discarded, never zero-filled.
The check evaluates that canonical group series and attaches at most one
finding to the replication-group row. The non-Graviton check evaluates the
group row directly because node type and engine are group properties.

Aggregation rule — **the hottest node binds each dimension independently**:

```python
group_engine_cpu_p99  = max(node.engine_cpu_p99  for node in members)
group_memory_p99      = max(node.memory_p99      for node in members)
group_network_in_p99  = max(node.network_in_p99  for node in members)
group_network_out_p99 = max(node.network_out_p99 for node in members)
```

The primary typically binds write CPU and memory; any replica may bind
network or read CPU. Dimensions are never averaged across nodes — averaging
would let idle replicas hide a saturated primary.

Savings are priced per group:

```python
monthly_savings = (
    (current_node_monthly_price - target_node_monthly_price)
    * total_node_count
)
```

The one-cent floor, `min_monthly_savings`, decimal-safe eligibility
arithmetic, and float display rounding carry over from EC2 Section 11
unchanged.

---

# 3. Candidate generation

Candidate generation is **cross-family and cross-architecture**, restricted
to Redis/Valkey-supported `cache.*` node types with regional pricing.

## 3.1 Graviton is first-class

Unlike EC2, arm64 targets are ordinary candidates. ElastiCache is a managed
service: clients speak the wire protocol and never observe the instruction
set, and `cache.m5 → cache.m7g` is a normal modify operation. There is no
OPPORTUNITY classification and no Graviton savings preview in this product.

Architecture is replaced as a constraint by **engine-version compatibility**,
which is a hard constraint:

```text
The target node type must be supported by the group's current engine and
engine version. A target unsupported at the current engine version is
rejected with ENGINE_VERSION_INCOMPATIBLE. V1 never recommends an engine
upgrade as part of a resize.
```

The **primary source is AWS itself**: the scanner calls
`elasticache:ListAllowedNodeTypeModifications` per replication group and
persists `allowed_scale_up_types` / `allowed_scale_down_types` (Section 1).
That is per-group runtime truth — it already accounts for engine, engine
version, region, and the online vertical-scaling engine floor — and
supersedes any hand-maintained table. When the persisted sets are present, a
candidate absent from both is rejected with `ENGINE_VERSION_INCOMPATIBLE`.

A **static fallback table** (versioned policy) is consulted only when the
API result is unavailable (AccessDenied, stale scan). It uses three-part
semantic versions — AWS floors such as Redis 5.0.6 are not expressible as
major.minor — and is keyed by family *and size* where AWS differentiates
(e.g. `t4g.micro` has a different floor than `t4g.small`/`t4g.medium`).
When neither source can determine support for a target, the target is
skipped and counted as `ENGINE_VERSION_SUPPORT_UNKNOWN` (a rejection tally,
not a DEFERRED) — support is never guessed.

## 3.2 Family gates

Mirroring EC2 Section 1's gated classes:

```text
Burstable        cache.t*     (credit-limited CPU — could throttle a
                               sustained workload)

Data tiering     cache.r*gd   (memory + SSD tier; capacity semantics are
                               not comparable to pure-memory types)
```

A gated-class target is allowed only when the current node type shares that
class (`cache.t3 → cache.t4g` is allowed; `cache.m5 → cache.t4g` is not).
The data-tiering gate applies in **both directions**: V1 never recommends
into or out of a `*gd` family, because usable capacity on a tiered node
depends on the workload's hot/cold split, which telemetry cannot prove.
Gated targets are skipped and counted as `FAMILY_NOT_ELIGIBLE`. The gated
set is configurable policy.

## 3.3 No Availability Zone or capacity validation

Carried over from EC2 Section 1 verbatim: V1 does not validate per-AZ node
type offerings or launch capacity, and displays the same general note:

```text
Node type availability and modification capacity are not validated by this
recommendation. Confirm that the target type is available before applying
the change.
```

---

# 4. Metric normalization

Exact, deterministic, threshold-free — EC2 Section 3's philosophy applies
unchanged. Uncertainty enters during capacity interpretation, never during
unit conversion.

**Single statistics path (EC2 parity).** Every series is collected as raw
per-period points via `GetMetricData`: gauges as `Average` per 300 s period,
counters as `Sum` per period. Percentiles are computed **locally, per node,
over the collected window** — never requested server-side. A server-side p99
of five-minute periods is a percentile of percentiles and is forbidden.
Decision demand per dimension is the **maximum p99 across the 14-, 30-, and
60-day windows**, computed from the same raw series, exactly as EC2 does —
a recently grown workload is not diluted by a long quiet history.

## 4.1 CPU

`EngineCPUUtilization` and `CPUUtilization` are percentage gauges from the
`AWS/ElastiCache` namespace with the `CacheClusterId` dimension. No unit
conversion; percentiles are computed per node after collection, then
aggregated per Section 2.

## 4.2 Memory

`DatabaseMemoryUsagePercentage` is a percentage of the node's `maxmemory`
(not raw RAM). `BytesUsedForCache` is collected alongside as the absolute
series used for projecting demand onto a target (Section 6).

## 4.3 Network

`NetworkBytesIn` and `NetworkBytesOut` are period byte totals, identical in
shape to EC2's `NetworkIn`/`NetworkOut`:

```python
network_mbps = network_bytes_sum / period_seconds * 8 / 1_000_000
```

Decimal Mbps; each five-minute point converted independently before
percentiles; inbound and outbound never summed (full-duplex shaping).
EC2 Section 3.1's rules apply verbatim.

## 4.4 Cache-health series

Collected for the warning layer (Section 8), normalized but never
threshold-adjusted at this stage:

```text
Evictions            Sum per period            (count)
SwapUsage            gauge                     (bytes)
ReplicationLag       gauge, replicas only      (seconds)
CurrConnections      gauge                     (count)
GetTypeCmds /
SetTypeCmds          Sum per period -> ops/s   (per node; replica-reduction
                                                evidence, Section 11)
```

Missing samples are never converted to zero (EC2 Section 3 rule).

---

# 5. CPU model

## 5.1 Signals

Redis/Valkey command execution is effectively single-threaded, so host
`CPUUtilization` understates saturation on multi-vCPU nodes. V1 uses two
gates:

```text
Primary (saturation):   EngineCPUUtilization p99 vs the tier target
                        utilization. EngineCPU does not scale with vCPU
                        count, so the projection onto a target is identity:
                        projected_engine_cpu = observed_engine_cpu_p99.

Secondary (background): CPUUtilization p99, projected by vCPU ratio:
                        projected_host_cpu = host_cpu_p99
                                             * current_vcpus / target_vcpus
```

Both projected values must be `<= tier_ratio * 100` for the candidate to
pass the tier's compute gate. CoreMark is not used for ElastiCache — the
performance-delta presentation of EC2 Section 25.5 is omitted.

## 5.2 Small-target conservatism

On nodes with fewer than 4 vCPUs, `EngineCPUUtilization` and host
`CPUUtilization` converge and enhanced I/O threads are unavailable, so a
downsize to a **≤2 vCPU target** additionally requires:

```text
projected_host_cpu <= tier_ratio * 100 * small_target_cpu_multiplier
```

with `small_target_cpu_multiplier` defaulting to `0.85` (i.e. the host-CPU
gate tightens by 15%). This is configurable policy. When
`EngineCPUUtilization` is unavailable (very old engine versions), the
candidate set may still be generated from host CPU alone, but every
candidate carries `ENGINE_CPU_METRIC_UNAVAILABLE`, compute risk is at least
MEDIUM, and classification caps at CONDITIONAL.

---

# 6. Memory model

## 6.1 Usable memory, not node RAM

Targets are compared on **usable memory**:

```python
# percent form (reserved-memory-percent)
usable_memory_bytes = target_maxmemory_bytes * (1 - reserved_memory_percent)

# absolute form (legacy reserved-memory, bytes) — a fixed reserve is a
# LARGER fraction of a smaller target, so it is applied per target:
usable_memory_bytes = target_maxmemory_bytes - reserved_memory_bytes
```

`target_maxmemory_bytes` comes from the catalog (AWS-published per-node-type
`maxmemory` parameter default), which already excludes engine overhead.

The reservation is read from the group's **effective parameter group**
(`elasticache:DescribeCacheParameters` at scan time). AWS supports two
mutually exclusive parameters — `reserved-memory-percent` and the legacy
absolute `reserved-memory` — and both must be handled; older default
parameter groups reserve **zero** bytes. The `0.25` default applies only
when the reservation was resolved to a modern default parameter group whose
default is 25%. When the effective value cannot be resolved (AccessDenied,
parameter fetch failed, unparseable custom configuration), **never silently
assume 0.25**: target usable memory is UNKNOWN, memory-reducing candidates
are excluded by the capacity-retention rule (every candidate must retain or
grow raw `maxmemory`), and remaining candidates carry
`RESERVED_MEMORY_UNKNOWN` with memory risk at least MEDIUM and a CONDITIONAL
cap. Using raw node RAM anywhere in the memory comparison is an error.

## 6.2 Demand and projection

```python
observed_used_bytes_p99 = bytes_used_for_cache_p99      # hottest node

projected_memory_util = (
    observed_used_bytes_p99
    / target_usable_memory_bytes
)
```

The tier gate is `projected_memory_util <= tier_ratio`, same shape as EC2
Section 25.2.

## 6.3 The eviction floor

A cache under memory pressure evicts instead of failing, so a high-eviction
cache can report deceptively stable memory percentages. Hard rule:

```text
Any nonzero Evictions sum in the 60-day decision window forces the memory
floor to current capacity: every candidate must have usable memory >= the
current node type's usable memory. Memory-reducing candidates are rejected
with MEMORY_PRESSURE_EVICTIONS_PRESENT.

Candidates that retain or grow memory remain eligible but carry the
EVICTIONS_PRESENT warning and memory risk = HIGH.
```

An evicting cache is never downsized on memory, whatever
`DatabaseMemoryUsagePercentage` says. Deliberate-eviction workloads (caches
run intentionally full with an LRU policy) are the reason this is a floor on
*memory-reducing* candidates rather than a scope gate: such workloads can
still receive same-memory cross-family or Graviton savings.

## 6.4 Native telemetry — no discovery, no missing-memory machinery

Memory telemetry needs no agent, so EC2's memory-metric discovery
(Section 19), the unmeasured-memory capacity floor, and the
`MEMORY_METRIC_MISSING_DOWNSIZE` savings preview have **no equivalent** here
and must not be ported. A group whose memory series is absent (telemetry
gap) is handled by the generic rule: unmeasured dimension → candidates must
retain current capacity in that dimension, disclosed via `observed_days`.

---

# 7. Hard constraints vs warnings

EC2 Section 4 carries over unchanged: every resource dimension produces a
`ResourceEvaluation` with independent `hard_constraint_status`
(PASS/FAIL/UNKNOWN/NOT_APPLICABLE) and `risk_level` (LOW/MEDIUM/HIGH).

ElastiCache hard failures:

```text
Engine/engine-version does not support the target node type
(ENGINE_VERSION_INCOMPATIBLE).

Observed sustained memory demand (p99 BytesUsedForCache) exceeds the
target's usable memory (MEMORY_REQUIREMENT_NOT_MET).

Eviction floor violated (MEMORY_PRESSURE_EVICTIONS_PRESENT, Section 6.3).

Observed sustained network demand exceeds a reliable documented target
maximum (NETWORK_RELIABLE_MAX_EXCEEDED).

Network allowance-exceeded events recorded and the target has equal, lower,
or unknown network capacity (EC2 Section 7 rule, unchanged).

Data-tiering / burstable family gate (FAMILY_NOT_ELIGIBLE, Section 3.2).

Traffic management observed in-window and the candidate reduces CPU or
memory capacity (TRAFFIC_MANAGEMENT_ACTIVE, Section 8) — AWS throttled the
node because demand exceeded capacity; a capacity-reducing move is never
recommended on that evidence.

Replica-reduction safety floors (Section 11.2).
```

Do not hard-block on approximate signals — swap, lag, connection counts,
p99 near an "up to" bandwidth value, unknown baselines. Those are warnings.

---

# 8. Cache-health warning layer

This layer plays the role EBS operational signals play for EC2 (Section 10):
it raises risk and forces CONDITIONAL, never rejects on its own.

```text
Evictions > 0 in window:
    EVICTIONS_PRESENT · memory risk = HIGH
    (plus the Section 6.3 floor on memory-reducing candidates)

SwapUsage p99 > swap_warning_bytes (default 50 MiB):
    SWAP_USAGE_REVIEW_REQUIRED · memory risk = HIGH

ReplicationLag p99 > replication_lag_warning_seconds (default 1.0 s):
    REPLICATION_LAG_REVIEW_REQUIRED · risk = HIGH on the affected dimension
    (compute) — a downsize that slows replication risks failover data loss

CurrConnections p99 > 0.70 * 65_000:
    HIGH_CONNECTION_COUNT_REVIEW_REQUIRED · compute risk = MEDIUM
    (maxclients is 65,000 on every node type, so connections are a review
    signal, not a sizing dimension)

TrafficManagementActive nonzero at any point in the window:
    TRAFFIC_MANAGEMENT_DETECTED · compute risk = HIGH
    (plus the Section 7 hard floor on CPU- or memory-reducing candidates —
    AWS documents any positive point as possible evidence of an undersized
    node)

Current type is burstable and CPUCreditBalance minimum falls below
cpu_credit_balance_low_watermark x the node size's maximum accrued credits
(AWS-published per size: t4g.micro/t3.micro 288, t4g.small/medium and
t3.small/medium 576):
    CPU_CREDITS_REVIEW_REQUIRED · compute risk = HIGH
    (a credit-exhausted node is running at throttled baseline, so its
    observed utilization cannot validate a smaller burstable target)

Current type is burstable and credit telemetry is missing — always true
for cache.t2, which publishes no CPUCreditBalance/CPUCreditUsage:
    CPU_CREDIT_TELEMETRY_UNAVAILABLE · compute risk >= MEDIUM ·
    burstable-target candidates cap at CONDITIONAL
    (an unmeasured credit posture never silently supports an unqualified
    burstable recommendation)
```

`FreeableMemory` is collected as interpretive context for `SwapUsage` and
disclosed in evidence (a node can swap while memory is free when the kernel
pages out cold allocations); it raises no warning of its own.

Thresholds are configurable policy (Section 13). Any of these warnings
routes an otherwise-eligible candidate to CONDITIONAL.

---

# 9. Network model

EC2 Sections 5–7 apply **verbatim**, with one mapping rule:

```text
A cache node type's baseline and peak bandwidth are those of its EC2
equivalent: cache.<family>.<size> -> <family>.<size> in the EC2 catalog's
NetworkInfo (BaselineBandwidthInGbps / PeakBandwidthInGbps, summed across
cards).
```

Carried over unchanged:

* Three-band model on p99 vs baseline and reliable peak (40%/70% default
  ratios, `NETWORK_SUSTAINED_ABOVE_BASELINE`, `NETWORK_RELIABLE_MAX_EXCEEDED`).
* Assumed baseline for "up to"-only types (size-proportional within the exact
  cache family's EC2 equivalent, floored, `NETWORK_BASELINE_ASSUMED`,
  never hard-rejects, never supports ACTIONABLE).
* Allowance metrics — ElastiCache publishes them natively in
  `AWS/ElastiCache`:
  `NetworkBandwidthInAllowanceExceeded`, `NetworkBandwidthOutAllowanceExceeded`,
  `NetworkPacketsPerSecondAllowanceExceeded`,
  `NetworkConntrackAllowanceExceeded` — EC2 Section 7 rules unchanged,
  including `NETWORK_ALLOWANCE_METRICS_MISSING` being non-blocking.
* In/out evaluated separately, never summed; greater directional ratio drives
  the verdict.

When no EC2 equivalent exists in the catalog for a cache type, the target is
`capacity_kind = UNKNOWN` with `RESOURCE_BASELINE_CAPACITY_UNKNOWN` — shown,
CONDITIONAL, never faked.

There is no EBS dimension. `storage` in the risk structure is reported as
NOT_APPLICABLE / LOW for pure-memory types.

---

# 10. Classification

The EC2 Section 11 decision table carries over with `storage` replaced by
the cache-health dimension:

| Hard status (network & memory & engine) | Network risk | Memory/health risk | Blocking warnings | Classification |
| --- | --- | --- | --- | --- |
| Any FAIL | any | any | any | REJECTED |
| PASS or NOT_APPLICABLE, any UNKNOWN | any | any | any | CONDITIONAL |
| PASS or NOT_APPLICABLE | LOW | LOW | none | ACTIONABLE |
| PASS or NOT_APPLICABLE | MEDIUM or HIGH | any | any | CONDITIONAL |
| PASS or NOT_APPLICABLE | any | MEDIUM or HIGH | any | CONDITIONAL |

Strict LOW/LOW for ACTIONABLE, same as EC2 — tunable policy, not law.
Classification is driven only by the table's columns; telemetry risk and the
disclosure dimensions never change classification.

**There is no OPPORTUNITY class in the ElastiCache rightsizer.** Graviton
moves are ordinary candidates (Section 3.1). The classification set is:

```text
ACTIONABLE · CONDITIONAL · REJECTED · DEFERRED
```

Risk structure (EC2 Section 12 shape, storage renamed):

```python
@dataclass(frozen=True)
class RiskAssessment:
    telemetry: str
    compute: str          # EngineCPU + host CPU
    memory: str           # usable-memory fit + evictions/swap
    network: str
    cache_health: str     # lag, connections, swap disclosure
    compatibility: str    # engine-version support
    migration: str        # replica-reduction operational risk
    overall: str
    reason_codes: tuple[str, ...]
```

---

# 11. Replica-count reduction (second recommendation kind)

V1 emits up to two **independent** recommendation kinds per replication
group:

```text
NODE_TYPE_CHANGE          vertical resize; Sections 3–10, tiers apply
REPLICA_COUNT_REDUCTION   fewer read replicas; this section, no tiers
```

They are never composed. When both exist, both are returned and the user
picks one; the next scan recomputes the survivor's figures.

## 11.1 Evidence and load-transfer model

Replicas exist for HA and read scaling, so removal requires **read-traffic
evidence**, not just idle CPU.

**Roles.** Removable nodes are replicas only. Roles come from the
**per-node `IsMaster` metric** (1 = primary, 0 = replica), which AWS
publishes for every Redis/Valkey node and which the decision scan collects
for all members — this covers cluster-mode-enabled groups, where the API
reports no `CurrentRole`. The latest window samples decide; where the API
does provide `NodeGroupMembers[].CurrentRole` (cluster mode disabled) it is
cross-checked. Roles are unresolved — and the group receives no replica
recommendation (`REPLICA_ROLES_UNRESOLVED`) — when `IsMaster` is missing
for any member, when any shard does not resolve to exactly one primary, or
when the metric and the API disagree. Roles are never inferred from traffic
patterns.

**Inputs**, all over the 60-day window:

```python
replica_read_ops_p99   # GetTypeCmds/s p99, per replica
replica_read_ops_max   # maximum observed 5-minute GetTypeCmds/s point
replica_read_ops_sum   # total observed GetTypeCmds commands (raw Sum points)
replica_read_coverage_ratio
replica_read_observed_days
replica_read_latest_sample_age_seconds
replica_engine_cpu_p99
group_read_ops_p99     # timestamp-aligned sum of GetTypeCmds/s across all
                       # members, then p99; a timestamp missing any member's
                       # sample is dropped from the sum, never zero-filled
```

`replica_read_coverage_ratio` is `sample_count / expected_sample_count`, where
the denominator is the number of five-minute periods in the lesser of that
member's age (`member_created_at`) and the 60-day decision window.
`replica_read_observed_days` remains `sample_count * 300 / 86400`, and
`replica_read_latest_sample_age_seconds` is measured from scan end. Neither
calculation fills a missing point with zero. Missing creation time or a latest
sample older than `replica_actionable_max_sample_age_seconds` prevents
ACTIONABLE but may still support a CONDITIONAL projection.

**Per-op CPU cost is measured, not assumed.** The conversion from read
ops/s to EngineCPU% is derived from the group's own telemetry:

```python
cpu_per_read_op = max(
    node.engine_cpu_p99 / node.read_ops_p99
    for node in members
    if node.read_ops_p99 >= policy.min_read_ops_for_cpu_rate  # default 50 ops/s
)
```

Taking the max across members biases the estimate upward, **but it is a
heuristic, not a bound**: the two p99s are not timestamp-aligned, and
GetTypeCmds mixes commands of very different costs. This is exactly why a
projection-justified removal is never ACTIONABLE (Section 11.3). When no
member clears the ops floor, the group's read traffic is immaterial and
redistribution cannot bind; removal is then governed solely by the idle
threshold and health signals **for candidate generation**. Classification
still follows Section 11.3: any positive observed read traffic, even below the
idle threshold, prevents ACTIONABLE.

**Survivor projection — worst-case concentration.** The removed replicas'
read load is assumed to land **entirely on the busiest survivor**, not to
spread evenly: connection pooling and reader-endpoint routing can
concentrate redirected clients on one node, so the model plans for that
case rather than the average one.

```python
removed_read_ops = sum(r.read_ops_p99 for r in removed_replicas)
busiest = max(surviving_replicas, key=lambda n: n.engine_cpu_p99)
projected_survivor_engine_cpu = (
    busiest.engine_cpu_p99 + removed_read_ops * cpu_per_read_op
)
# require projected_survivor_engine_cpu <= 70.0
```

AND ReplicationLag shows no sustained lag (Section 8 threshold).

The recommendation removes the **minimum-read-traffic replicas first** and
reduces by the largest count that keeps the projection within target.

**Fail closed on missing evidence.** Any of the following yields *no*
replica recommendation (reason recorded, never a CONDITIONAL guess):
`GetTypeCmds` missing for any member, `EngineCPUUtilization` missing for
any survivor, `ReplicationLag` missing while replicas exist, or unresolved
roles — reason codes `REPLICA_EVIDENCE_INCOMPLETE` /
`REPLICA_ROLES_UNRESOLVED`. Node-type evaluation is unaffected.

## 11.2 Safety floors (hard constraints)

```text
Never recommend below 1 replica when automatic failover or Multi-AZ is
enabled (REPLICA_FLOOR_AUTOMATIC_FAILOVER).

Never recommend to 0 replicas at all in V1 — removing the last replica is
an availability-posture change, not a rightsize
(REPLICA_FLOOR_LAST_REPLICA).

Cluster mode enabled: the reduction applies per node group via
replicas_per_node_group; never below the floor in any shard. Uneven
per-shard replica layouts are not evaluated in V1
(UNEVEN_REPLICA_LAYOUT -> no replica recommendation for the group).
```

## 11.3 Presentation

Single recommendation (no tiers): current replica count, target count,
per-replica read-traffic evidence, projected survivor load, savings
(`removed_count * node_monthly_price`), and its own classification/risk.
**ACTIONABLE requires observed-zero read traffic on every removed replica**:
every observed five-minute `GetTypeCmds` Sum point is zero
(`read_ops_max == 0` and `read_ops_sum == 0`), coverage is at least
`replica_actionable_min_coverage_ratio` (default `0.95`), observed time is at
least `replica_actionable_min_observed_days` (default `30`), and health signals
are clean. The member creation time must be known and its latest read sample
must be no older than `replica_actionable_max_sample_age_seconds` (default
`600`). P99 is never accepted as proof of zero traffic because it can hide the
busiest one percent of samples. A candidate
below the broader
`idle_replica_read_ops_threshold` (default 5 ops/s) may still be shown, but it
is CONDITIONAL unless the observed-zero and coverage requirements also hold.
A removal justified by the survivor projection is likewise **always capped at
CONDITIONAL**: the empirical per-op model is a heuristic (Section 11.1) and can
only gate which reductions are shown for review, never certify one as safe to
apply. Positive observed reads add `REPLICA_READ_TRAFFIC_OBSERVED`; a failed
coverage/history/age/freshness requirement adds
`REPLICA_READ_COVERAGE_INSUFFICIENT`; projection use adds
`REPLICA_LOAD_PROJECTION_REQUIRES_REVIEW`. Replica reduction is also at most
CONDITIONAL when any cache-health warning is present. `migration` risk is at
least MEDIUM —
removal is a topology change with a brief failover-capacity reduction.
Every replica recommendation displays a redundancy disclosure: reducing the
replica count reduces read redundancy and failover headroom even when the
Section 11.2 floors hold — the floors preserve *minimum* posture, not
*current* posture.

---

# 12. Tiered options (NODE_TYPE_CHANGE only)

EC2 Section 25 carries over with these substitutions:

* `projected_util = max(projected_engine_cpu/100, projected_host_cpu/100,
  projected_memory_util)` — coremark replaced by the Section 5 projections.
* Tiers: Conservative 0.55 / Balanced 0.70 / Aggressive 0.85, generation at
  the Aggressive gate, cheapest-fit per tier, nesting, dedup/collapse,
  single-option fallback, per-tier independent classification — all
  unchanged.
* `binding_dimension` over `{cpu, memory, network}` (no storage).
* No `performance_ratio` / `performance_change_pct` — CoreMark does not
  apply; the headroom story (`projected_util`) leads for downsizes, and
  same-price newer-generation swaps are explained by generation, not a
  benchmark delta.
* Balanced remains the default anchor and the candidate-limit reservation
  rule is unchanged.

Replica-count recommendations do not participate in tiers, ranking, or
`candidate_limit` (they are a separate response section).

---

# 13. Configurable policy

```python
@dataclass(frozen=True)
class ElastiCachePerformanceWarningPolicy:
    network_medium_ratio: float                 # 0.40
    network_high_ratio: float                   # 0.70
    memory_medium_ratio: float                  # 0.40
    memory_high_ratio: float                    # 0.70

    swap_warning_bytes: int                     # 52_428_800 (50 MiB)
    replication_lag_warning_seconds: float      # 1.0
    connection_warning_ratio: float             # 0.70 (of 65_000)

    small_target_cpu_multiplier: float          # 0.85 (Section 5.2)
    idle_replica_read_ops_threshold: float      # 5.0 ops/s
    replica_actionable_min_coverage_ratio: float       # 0.95
    replica_actionable_min_observed_days: float        # 30.0
    replica_actionable_max_sample_age_seconds: float   # 600.0
    min_read_ops_for_cpu_rate: float            # 50.0 ops/s (Section 11.1)
    cpu_credit_balance_low_watermark: float     # 0.10 of the node size's max
                                                # accrued credits (AWS table;
                                                # t2 publishes no credit metrics
                                                # -> §8 telemetry-unavailable rule)

    reserved_memory_percent_default: float      # 0.25 — resolved default
                                                # parameter groups only (§6.1)

    network_assumed_baseline_enabled: bool      # true
    network_assumed_baseline_multiplier: float  # 1.0
    network_assumed_baseline_floor_mbps: float  # 100.0

    unknown_baseline_risk: str                  # HIGH
    burst_dependent_risk: str                   # HIGH

    gated_families: tuple[str, ...]             # ("cache.t", "cache.r*gd")
    trend_primary_context_limit: int            # 8; per metric, >= 0
    policy_version: str
```

Thresholds affect warnings, risk, and ACTIONABLE-vs-CONDITIONAL. They must
not affect raw normalization, memory/CPU requirements, engine compatibility,
the eviction floor, or replica safety floors (EC2 Section 13 rule).

---

# 14. Required customer-facing warnings

Carried over verbatim where applicable: `HIGH_NETWORK_USAGE_REVIEW_REQUIRED`,
`NETWORK_SUSTAINED_ABOVE_BASELINE`, `NETWORK_BASELINE_ASSUMED`,
`RESOURCE_BASELINE_CAPACITY_UNKNOWN`.

New ElastiCache warnings:

```text
EVICTIONS_PRESENT

The cache evicted keys during the observation window, which means memory
demand exceeds capacity regardless of the reported memory percentage.
Memory-reducing targets are excluded; review whether evictions are
intentional (LRU cache pattern) before applying any change.
```

```text
SWAP_USAGE_REVIEW_REQUIRED

The node is using swap, which indicates memory pressure and can severely
degrade cache latency. Review memory sizing before applying this
recommendation.
```

```text
REPLICATION_LAG_REVIEW_REQUIRED

Replicas show sustained replication lag. A smaller node type or fewer
replicas could increase lag and widen the data-loss window on failover.
Review replication health before applying this recommendation.
```

```text
HIGH_CONNECTION_COUNT_REVIEW_REQUIRED

Connection count approaches the fixed 65,000 per-node limit. Node sizing
does not change this limit; review client connection pooling.
```

```text
ENGINE_CPU_METRIC_UNAVAILABLE

EngineCPUUtilization is not available for this engine version. The
recommendation was computed from host CPU only, which can understate
engine saturation on multi-vCPU nodes.
```

```text
TRAFFIC_MANAGEMENT_DETECTED

ElastiCache actively throttled or managed traffic on this node during the
observation window, which AWS documents as possible evidence the node is
undersized. Capacity-reducing targets are excluded; review workload
saturation before applying any change.
```

```text
CPU_CREDITS_REVIEW_REQUIRED

This burstable node's CPU credit balance approached exhaustion during the
observation window, so observed utilization reflects throttled baseline
performance. Review credit history before applying a burstable target.
```

```text
CPU_CREDIT_TELEMETRY_UNAVAILABLE

CPU credit telemetry is not published for this node type (cache.t2) or was
not available during the observation window, so burst-credit exhaustion
cannot be ruled out. Burstable targets require manual review of workload
burst behavior before applying.
```

```text
AUTO_SCALING_STATE_UNKNOWN

Application Auto Scaling attachment could not be verified (missing
permission or API error). If this group is managed by an auto scaling
policy, a manual resize will conflict with it. Verify before applying.
```

```text
RESERVED_MEMORY_UNKNOWN

The group's reserved-memory configuration could not be resolved, so target
usable memory is uncertain. Memory-reducing targets are excluded; confirm
the parameter group's reserved-memory settings before applying.
```

```text
REPLICA_READ_TRAFFIC_OBSERVED

At least one replica proposed for removal served observed read commands. The
reduction is based on a load-transfer projection and requires workload review;
it is not certified as an unused-replica removal.
```

```text
REPLICA_READ_COVERAGE_INSUFFICIENT

Read telemetry for at least one replica proposed for removal did not meet the
minimum history, coverage, creation-time, or freshness requirements. Observed
zeroes are therefore insufficient to classify the reduction as ACTIONABLE.
```

```text
REPLICA_LOAD_PROJECTION_REQUIRES_REVIEW

This reduction depends on an empirical read-load transfer model. The model
concentrates removed load on the busiest survivor but is still a heuristic;
validate client routing and workload behavior before changing topology.
```

Every recommendation also displays the operational note:

```text
Applying a node type change uses ElastiCache online vertical scaling,
which fails over each shard during the modification. Expect brief
write interruption per shard; validate client retry behavior.
```

---

# 15. Telemetry

EC2 Sections 18–23 carry over with these substitutions.

## 15.1 Decision scan

Period 300 s, 60-day raw window, per member node (`CacheClusterId`
dimension). Every series is fetched as raw per-period points — gauges as
`Average`, counters as `Sum`, `TrafficManagementActive` as `Maximum` — and
p95/p99/max/average are computed **locally** per node over the 14-, 30-,
and 60-day windows (Section 4), then aggregated per Section 2:

```text
EngineCPUUtilization, CPUUtilization        gauges, no conversion
DatabaseMemoryUsagePercentage               gauge
BytesUsedForCache                           gauge (bytes)
FreeableMemory                              gauge (bytes; swap context)
NetworkBytesIn / NetworkBytesOut            Sum -> Mbps per point (local math)
Evictions, GetTypeCmds, SetTypeCmds         Sum -> events/s, ops/s
SwapUsage, ReplicationLag, CurrConnections  gauges
IsMaster                                    gauge (0/1; role resolution, §11.1)
TrafficManagementActive                     Maximum per period (0/1)
CPUCreditBalance, CPUCreditUsage            gauges (T3/T4g current only; AWS
                                            publishes neither for cache.t2 —
                                            §8 telemetry-unavailable rule)
Network allowance-exceeded metrics          Sum (events)
```

Per-node collection multiplies the query count by node count. This is an
explicit, accepted fan-out: at GetMetricData rates a 10-node group costs on
the order of ten instances' worth of queries — still fractions of a cent per
scan. Document actual query counts in the implementation as EC2 Section 21
does.

## 15.2 Confidence trend (15-month)

Daily-Maximum headline series for **EngineCPUUtilization and
DatabaseMemoryUsagePercentage** — both native gauges, so unlike EC2 the
memory trend is always available and needs no discovered source. Network is
excluded from the 15-month horizon for the same Sum-counter rollup-smearing
reason as EC2 Section 20; the network reassurance figure remains the 60-day
decision-window peak per direction.

**Node set and response contract.** The EngineCPU-hottest and
memory-hottest nodes may differ. For each trend metric independently, fetch
the hottest node by decision-scan p99 plus the top
`trend_primary_context_limit` shard primaries ranked by that same metric's
decision-scan p99 (default `8`), breaking ties by `cache_cluster_id` ascending.
A limit of zero returns only the hottest series. A cluster-mode-enabled group
has one primary per shard; there is no singular group primary. Deduplicate by
`cache_cluster_id`. When the hottest node is a primary, emit one series with
the hottest selection reason rather than a duplicate context series; its
`role: "primary"` still supplies the topology context.
The response is organized per metric, and every series is labeled. It also
reports the total, returned, and omitted primary counts so clients disclose
truncation rather than implying complete shard coverage:

```python
{
  "metrics": {
    "engine_cpu": [ { "cache_cluster_id", "node_group_id", "role",
                      "selection_reason",   # engine_cpu list:
                                            # hottest_engine_cpu | primary
                                            # memory list:
                                            # hottest_memory | primary
                      "buckets": [...] }, ... ],
    "memory":     [ ...same shape... ],
  },
  "selection_summary": {
    "engine_cpu": { "primary_total", "primary_returned", "primary_omitted",
                    "primary_context_limit": 8 },
    "memory":     { "primary_total", "primary_returned", "primary_omitted",
                    "primary_context_limit": 8 },
  },
  "bucket_semantics": {...},
}
```

`primary_total` is the number of unique shard primaries in inventory.
`primary_returned` is the number of those primary identities visible in the
metric's returned series, including a hottest series whose role is primary;
`primary_omitted = primary_total - primary_returned`. Thus a zero context limit
can still report one returned primary when the independently required hottest
node is primary. Standalone clusters report zero for all three primary counts.
The inherited EC2 trend shape submits 15 metric-stat query IDs per returned
series (three daily-Maximum queries plus p99 and p95 for each of six windows).
At the default limit the worst case is therefore 135 query IDs per metric and
270 total, versus unbounded growth with shard count; `NextToken` pagination
does not add billed metric-stat queries.

The UI rescales evidence for a dimension using the unique series whose
`selection_reason` matches that dimension (`hottest_engine_cpu` for CPU
headroom, `hottest_memory` for memory headroom); all `primary` series are
the capped, highest-p99 per-shard context for that metric. It shows “Showing N
of M shard primaries” whenever `primary_omitted > 0`. Bucket semantics,
statistic rules (daily Max headline, epoch-aligned percentile caveats), and
`bucket_semantics` disclosure carry over unchanged.

## 15.3 Disclosure

`observed_days = sample_count * period_seconds / 86400` per metric per
hottest node; disclosure, not a gate. Example:

```text
Considered: EngineCPU ~58 days · Memory ~58 days · Network In/Out ~58 days
15-month peak EngineCPU 31% (p99 18%) — target leaves ~2.2x headroom
15-month peak memory 54% of usable (p99 47%) — target leaves ~1.5x headroom
60-day peak Network Out 310 Mbps (p99 120) — well under 750 Mbps baseline
Evictions: 0 in 60 days · Swap: none · Replication lag p99 0.2s
```

---

# 16. Module boundaries

Mirrors EC2 Section 15; exact math and approximate judgment never share a
module.

```text
elasticache_rightsizer/
├── normalization/
│   ├── cpu.py            # EngineCPU + host CPU series
│   ├── memory.py         # DatabaseMemoryUsagePercentage, BytesUsedForCache
│   ├── network.py        # bytes -> Mbps (shared logic with EC2 where practical)
│   └── health.py         # evictions, swap, lag, connections, read/write ops
├── requirements/
│   ├── compute.py        # Section 5 projections
│   ├── memory.py         # Section 6 usable-memory demand
│   └── network.py
├── constraints/
│   ├── engine_compatibility.py
│   ├── memory_hard_constraints.py    # incl. eviction floor
│   ├── network_hard_constraints.py
│   └── replica_floors.py
├── warnings/
│   ├── cache_health_warnings.py
│   └── network_warnings.py
├── risk/
│   └── risk_assembly.py
└── selection/
    ├── node_type_classification.py   # tiers, decision table
    └── replica_reduction.py          # Section 11
```

Where the EC2 implementation already has reusable pure functions (network
normalization, three-band evaluation, assumed baseline, tier bucketing),
prefer importing/sharing over copying — divergence between the two products'
network math is a bug.

---

# 17. Required tests

## Scope gating

```text
Memcached, serverless, Global Datastore members, auto-scaling-attached
groups, member-cluster rows, and non-available groups each return DEFERRED
with their specific reason code and produce no candidates.

Unknown auto-scaling state (check errored) proceeds but carries
AUTO_SCALING_STATE_UNKNOWN and caps at CONDITIONAL.

A plain available Redis/Valkey replication group proceeds.
```

## Normalization

```text
NetworkBytesIn/Out conversion is exact for 1-minute and 5-minute data and
directions are never summed.

Evictions/GetTypeCmds Sums convert to per-second rates exactly.

Missing samples are never treated as zero.
```

## Aggregation

```text
Each dimension is bound by the hottest node independently: a group where
the primary binds CPU and a replica binds network reports both.

Dimensions are never averaged across members.
```

## CPU model

```text
EngineCPU projects identity across node sizes; host CPU projects by vCPU
ratio.

A candidate passing EngineCPU but failing the vCPU-normalized host-CPU
gate is excluded.

A ≤2-vCPU target applies the small_target_cpu_multiplier tightening.

Missing EngineCPU yields ENGINE_CPU_METRIC_UNAVAILABLE, ≥MEDIUM compute
risk, and caps at CONDITIONAL.
```

## Memory model

```text
Comparison uses usable memory derived from the effective parameter group,
in both the percent and absolute reserved-memory forms, never raw node RAM;
an unresolved reservation excludes memory-reducing candidates and caps at
CONDITIONAL with RESERVED_MEMORY_UNKNOWN.

Nonzero Evictions rejects every memory-reducing candidate with
MEMORY_PRESSURE_EVICTIONS_PRESENT while same-or-larger-memory candidates
remain eligible with EVICTIONS_PRESENT and HIGH memory risk.

p99 BytesUsedForCache above target usable memory is a hard
MEMORY_REQUIREMENT_NOT_MET rejection.
```

## Saturation floors

```text
Any nonzero TrafficManagementActive point in the window rejects every CPU-
or memory-reducing candidate (TRAFFIC_MANAGEMENT_ACTIVE); non-reducing
candidates carry TRAFFIC_MANAGEMENT_DETECTED with HIGH compute risk.

A burstable group whose credit balance falls below the low watermark caps
burstable-target candidates at CONDITIONAL with CPU_CREDITS_REVIEW_REQUIRED;
a balance above the watermark produces no warning.

A burstable current with missing credit telemetry (cache.t2 always) caps
burstable targets at CONDITIONAL with CPU_CREDIT_TELEMETRY_UNAVAILABLE —
never a silent unqualified recommendation.
```

## Graviton / engine compatibility

```text
An arm64 target supported at the current engine version is an ordinary
candidate and may be ACTIONABLE.

A target absent from the group's persisted ListAllowedNodeTypeModifications
sets is rejected with ENGINE_VERSION_INCOMPATIBLE; when no API result
exists, the static fallback table (three-part versions, size-aware keys)
decides; support determinable from neither source is tallied as
ENGINE_VERSION_SUPPORT_UNKNOWN.

cache.m5 -> cache.t4g is FAMILY_NOT_ELIGIBLE; cache.t3 -> cache.t4g is
allowed. r6gd is never recommended into or out of.
```

## Network

```text
The three-band model, assumed baseline, allowance-exceeded rules, and
threshold-isolation tests carry over from EC2 Section 16, run against
cache node types mapped to EC2 equivalents.

A cache type with no EC2 equivalent gets capacity_kind = UNKNOWN and
CONDITIONAL, never a fabricated ratio.
```

## Replica reduction

```text
Automatic-failover groups never receive a below-1-replica recommendation;
no group ever receives a 0-replica recommendation.

A replica serving material read traffic is not removable even at idle CPU.
P99 below the idle threshold is insufficient for ACTIONABLE: every observed
GetTypeCmds Sum point must be zero and the observed-days/coverage floors must
both pass. Otherwise a qualifying reduction is at most CONDITIONAL.

Projected survivor EngineCPU above 70% blocks the reduction.

Cluster-mode groups reduce per node group and skip uneven layouts; roles
come from IsMaster, and a missing or inconsistent IsMaster (no unique
primary per shard, or disagreement with CurrentRole) yields
REPLICA_ROLES_UNRESOLVED.

Missing GetTypeCmds, EngineCPU, or ReplicationLag evidence yields no
replica recommendation (REPLICA_EVIDENCE_INCOMPLETE, fail closed) while
node-type evaluation proceeds.

A projection-justified removal is never ACTIONABLE; the survivor projection
concentrates the entire removed read load on the busiest survivor.

NODE_TYPE_CHANGE and REPLICA_COUNT_REDUCTION are independent: both may be
returned; neither composes with the other; replica recommendations never
appear in tiers or candidate_limit.
```

## Tiers and classification

```text
Tier bucketing, nesting, dedup/collapse, Balanced-anchor, and
per-tier-independent-classification tests carry over from EC2 Section 25.8
with projected_util built from the Section 5/6 projections.

The decision table yields exactly the classifications in Section 10;
cache-health warnings force CONDITIONAL but never REJECTED.

No code path can emit OPPORTUNITY.
```

## Module isolation

```text
Changing cache-health thresholds cannot change normalized demand, memory
requirements, engine compatibility, or pricing.

Changing network thresholds cannot change CPU or memory eligibility.

Changing tier ratios cannot change hard-constraint outcomes or replica
safety floors.
```

---

# 18. V1 product guarantee

```text
V1 does not claim to prove application latency or hit-rate outcomes.

It normalizes observed engine, memory, and network telemetry consistently;
blocks candidates with known engine, memory, family, or maximum-capacity
incompatibilities; never reduces memory on an evicting cache or capacity on
a traffic-managed one; never removes the last replica or breaches the
automatic-failover replica floor (replica reduction still reduces
redundancy, and is always disclosed as such with migration risk at least
MEDIUM); and flags candidates with burst-dependent, approximate, or
unhealthy signals for customer review.

Recommendations requiring review remain visible as CONDITIONAL rather than
being silently removed.
```
