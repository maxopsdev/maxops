# ASG Rightsizer Specification

Status: authoritative V2 design contract. V2 adds EC2 instance-type optimization
while preserving the currently implemented V1 capacity-only path; V2 is not
considered implemented until its implementation and test plans pass.

This document defines an Auto Scaling Group rightsizer that detects
overprovisioned group floors, current capacity, and per-instance compute shape.
It recommends an exact target EC2 instance type together with exact `MinSize`
and `DesiredCapacity` values. A result may optimize capacity only, instance type
only, or both dimensions together. It never changes numeric `MaxSize`.

The EC2 rightsizer remains the source of truth for shared catalog access,
instance candidate eligibility, architecture and family gates, CPU performance
modeling, classification vocabulary, tier ratios, savings arithmetic, trend
semantics, previews, and conservative missing-metric behavior. Those rules must
be implemented as shared pure helpers and reused by both services. The ASG
rightsizer adds only the group-level demand and target-count calculation.

## 1. Goal and user promise

For a supported ASG, answer:

```text
Which EC2 instance type, minimum capacity, and desired capacity provide the
lowest safe regional cost while preserving explicit workload headroom?
```

The tier summary presents up to three combined configurations; the
savings-ranked recommendation list may contain additional candidates up to the
configured limit:

```text
Current:       m6i.large   min=6, desired=10, max=20
Conservative:  m7i.large   min=4, desired=9,  max=20 unchanged
Balanced:      m7i.large   min=3, desired=7,  max=20 unchanged
Aggressive:    c7i.large   min=3, desired=6,  max=20 unchanged
```

Every option explains its target type, optimization kind, utilization target,
binding dimension, telemetry coverage, capacity floor, compatibility evidence,
classification, and estimated savings. Total configuration savings are
authoritative; instance and count savings are never added independently. The
service is read-only and never updates an ASG, its launch definition, or
CloudWatch metric settings.

## 2. V2 scope

### 2.1 In scope

- Homogeneous, instance-count-based ASGs with one effective EC2 instance type.
- Existing Linux or supported platform pricing in the regional EC2 catalog.
- Same-architecture, cross-family EC2 instance candidates using the EC2
  rightsizer's candidate policy.
- Exact recommendations for lower `MinSize` and `DesiredCapacity`.
- Exact recommendations for a different target instance type, with or without
  a count reduction.
- `MaxSize` displayed unchanged.
- Aggregate CPU pressure.
- Aggregate memory pressure when a usable discovered memory metric exists.
- Current configuration, capacity history, scaling activity safety signals,
  and policy context.
- Conservative, Balanced, and Aggressive options.
- CPU-only savings previews when memory is unavailable.
- Graviton migration previews using the EC2 rightsizer contract.
- Read-only list, detail, and on-demand confidence-trend endpoints.

### 2.2 Out of scope

- Changing `MaxSize`.
- Applying launch-template or launch-configuration changes.
- Changing AMIs, architecture, operating system, user data, purchase option,
  or application binaries.
- Mixed-instances policies, weighted capacity, or instance-type overrides.
- Spot or mixed purchase-option optimization.
- Warm-pool optimization.
- Scheduled or predictive scaling optimization.
- Rewriting target-tracking, step, or simple scaling policies.
- Scaling-to-zero recommendations.
- Network, EBS, packet, connection, queue-depth, request-rate, latency, or
  application-SLO-driven sizing.
- Applying recommendations or enabling ASG metrics.
- Regional capacity or Availability Zone offering validation.

Existing ASG checks may continue to report separate findings such as warm-pool
oversizing, old launch templates, purchase mix, or schedules. They do not feed
this V2 rightsizer unless explicitly named in this specification.

## 3. Shared EC2 contracts

The following rules match `rightsizers/ec2/ec2_rightsizer_spec.md`:

- Decision metrics use `GetMetricData` with a 300-second period.
- The requested horizon is 60 days, with derived 14-, 30-, and 60-day windows.
- Raw timestamped values are normalized before local percentiles.
- Missing timestamps are absent, never zero.
- Pagination follows every `NextToken`.
- Decision summaries contain p95, p99, maximum, and sample count.
- `observed_days = sample_count * period_seconds / 86400`.
- Memory uses the same discovery, validation, fallback, ranking, and persisted
  source as the EC2 rightsizer.
- Tier target ratios are 0.55, 0.70, and 0.85.
- Balanced is the default and policy values are included in the response.
- Savings use decimal-safe eligibility with an inclusive one-cent floor and
  inclusive `min_monthly_savings` comparison.
- Instance specifications come from the global `EC2InstanceCatalog`; prices
  remain regional and platform-specific.
- Same-architecture cross-family eligibility, gated family classes, `.metal`
  exclusion, deterministic type tie-breaking, CoreMark/vCPU fallback, positive
  performance evidence, and Graviton previews use the EC2 candidate contract.
- Classifications use the shared enum: ACTIONABLE, CONDITIONAL, PREVIEW,
  REJECTED, DEFERRED, INSUFFICIENT_DATA, and OPPORTUNITY. ASG emits OPPORTUNITY
  only for a display-only architecture-migration preview.
- The long-range confidence trend is on demand, not a decision input; it uses
  daily Maximum as its headline and never substitutes Average for Maximum.

ASG-specific differences are explicit below. They must not silently change the
EC2 behavior.

### 3.1 Required code reuse boundary

The implementation must not copy the EC2 candidate loop into the ASG service
or invoke `EC2Rightsizer` once per member. Extract and reuse product-neutral,
pure decision helpers from the current EC2 implementation for:

```text
EC2CandidatePolicy validation and evidence
architecture overlap and ARCHITECTURE_INCOMPATIBLE
family_class and FAMILY_NOT_ELIGIBLE
regional catalog enumeration and deterministic type ordering
CoreMark-or-vCPU capacity basis and performance delta
Decimal(str(...)) savings eligibility
Balanced-reserving candidate limiting
Graviton preview eligibility and raw-capacity evidence
```

`EC2Rightsizer` keeps ownership of standalone-instance network/EBS evaluation,
inventory shaping, and its response contract. `ASGRightsizer` keeps ownership
of group demand, minimum/desired count selection, operational signals, and its
response contract. Shared extraction is accepted only when the named EC2
regression tests prove byte-for-byte equivalent EC2 recommendation behavior.

The shared compatibility helper returns an eligibility result and reason code;
it does not assume that a target's unit price is lower. Standalone EC2 requires
a cheaper target instance. ASG evaluates total configuration cost, so a more
expensive but larger target instance can be valid when fewer instances make the
combined configuration cheaper.

## 4. Required inventory

Persist one ASG inventory row per account, region, and group name. At minimum it
contains:

```text
inventory_id
resource_id / auto_scaling_group_name
resource_name
account_id
region
state
min_size
desired_capacity
max_size
instance_type
platform_normalized
availability_zones
instance_ids
in_service_instance_ids
mixed_instances_policy_present
capacity_unit_kind                  # "instances" in supported V2
warm_pool_present
scheduled_actions_present
predictive_scaling_present
dynamic_policy_kinds
dynamic_policy_metrics
default_instance_warmup
default_cooldown
capacity_rebalance
scale_in_protection_present
suspended_processes
generated_at
metadata_json
aws_payload_json
```

The database identity is the composite `(account_id, region, resource_id)`;
`resource_id` is the ASG name and is not globally unique. `inventory_id` remains
unique within `asg_inventory` and is the API identifier. Both identity scope
columns are non-null. The scan orchestrator supplies `account_id` from the
validated onboarding scope and the regional adapter supplies `region`; missing
identity is a visible collection/import error rather than a nullable row.

`DescribeAutoScalingGroups` is authoritative for the current min, desired, max,
member state, zones, and launch configuration. The effective instance type is
resolved from the launch template/configuration and verified against in-service
members. If one type cannot be proven, the group is unsupported.

The scanner also persists paginated scaling activities, scheduled-action
presence, scaling-policy summaries, warm-pool presence, and active instance
refresh state. Full raw payloads may be retained in `aws_payload_json`, while
the recommendation service reads normalized fields from `metadata_json`.

`warm_pool_present` is true whenever `DescribeWarmPool` returns a
`WarmPoolConfiguration`, even when its `Instances` list is empty. The current
warm-instance count is evidence only and never determines whether the warm-pool
scope gate applies.

## 5. Minimal decision telemetry

V2 collects only the series required for combined instance/count rightsizing.

| Normalized name | AWS source | Dimension | Statistic | Required |
| --- | --- | --- | --- | --- |
| `cpu_percent` | `AWS/EC2/CPUUtilization` | `AutoScalingGroupName` | Average | yes |
| `desired_capacity` | `AWS/AutoScaling/GroupDesiredCapacity` | `AutoScalingGroupName` | Maximum | recommended |
| `in_service_instances` | `AWS/AutoScaling/GroupInServiceInstances` | `AutoScalingGroupName` | Maximum | yes |
| `memory_percent` | discovered percentage metric | persisted exact dimensions matching ASG members | Average | optional |

The collector attempts these standard inputs for every assessment. It does not
collect `GroupMinSize`, `GroupMaxSize`, pending, standby, terminating, total,
warm-pool, predictive forecast, network, EBS, or allowance metrics for this
rightsizer. Instance-type changes therefore carry the explicit network/storage
review contract in Sections 14 and 15. Current min/max values come from
`DescribeAutoScalingGroups`.

Current desired capacity from `DescribeAutoScalingGroups` is required for target
and savings calculations. Historical `GroupDesiredCapacity` is collected for
every assessment but is not a workload input: its absence caps a complete
recommendation at CONDITIONAL rather than making the calculation impossible.
`GroupInServiceInstances` remains required because it converts utilization into
used-instance demand.

Some accounts may not expose one of the CloudWatch series. In particular, the
two `AWS/AutoScaling` group series require Auto Scaling group metrics collection
to be enabled. Absence is a coverage result, not zero demand, and follows
Section 11. The rightsizer reports the remediation but never enables metrics.

### 5.1 Period alignment

All four series are requested at 300 seconds and sorted ascending. Capacity
gauges use Maximum within the five-minute bucket so a transition cannot
understate the number of instances carrying the observed Average utilization.

Only timestamps with both CPU and a positive in-service count produce a CPU
load point. Memory produces a load point only when a valid memory value and the
same in-service timestamp are present. Desired capacity is joined separately as
cost and configuration evidence; it is not workload demand.

No interpolation, forward fill, or implicit zero is permitted.

### 5.2 Memory discovery

Reuse the EC2 memory discovery contract without a second ASG-specific naming
heuristic:

- Paginate `ListMetrics`.
- Require an `InstanceId` or `AutoScalingGroupName` association.
- Match percentage utilization names and exclude bytes/free/cache/swap/total
  metrics exactly as the EC2 discovery does.
- Preserve every returned dimension.
- Prefer standard used-percentage metrics, then utilization aliases; use
  `CWAgent` as the deterministic tie-breaker.
- Add `CWAgent/mem_used_percent` as the fallback candidate.
- Reject non-finite or out-of-range values and try the next candidate when the
  preferred series is empty.
- Persist the selected source and reuse it for the decision scan and trend.

For member-dimensioned custom metrics, aggregate only members belonging to the
ASG at that timestamp when membership evidence is available. If a trustworthy
group memory series cannot be assembled, memory is unavailable; do not average
only today's surviving members across historical periods and present it as full
group history.

Prefer an exact `AutoScalingGroupName` source. A member aggregate is usable only
when every current in-service member predates the complete 60-day window,
desired and in-service histories remain constant, no launch/termination/
replacement activity occurs, every member exposes the same namespace and metric
name, and every aggregate timestamp contains all members. Persist its source as
`kind: stable_member_aggregate` with the ordered member sources and
`aggregation: mean_complete_member_intersection`. Otherwise memory is
unavailable and Section 12 applies.

## 6. Persisted telemetry shape

Persist normalized windows in `metadata_json["rightsizing_metrics"]`:

```yaml
14d:
  lookback_days: 14
  period_seconds: 300
  normalized:
    cpu_percent: {p95, p99, maximum, sample_count}
    memory_percent: {p95, p99, maximum, sample_count}
    desired_capacity: {p50, p95, p99, maximum, sample_count}
    in_service_instances: {p50, p95, p99, maximum, sample_count}
    required_capacity:
      conservative: {p50, p99, maximum, sample_count}
      balanced: {p50, p99, maximum, sample_count}
      aggressive: {p50, p99, maximum, sample_count}
  coverage:
    cpu_in_service_pairing_ratio: 0.0
    memory_in_service_pairing_ratio: 0.0 | null
  signals:
    scaling_failure_seen: false
    capacity_shortage_seen: false
    desired_in_service_mismatch_seen: false
  normalization_version: asg-v1-exact
30d: ...
60d: ...
```

Required-capacity summaries are derived from timestamped load points, never by
multiplying independent percentile summaries.

V2 additionally persists one target-independent series outside the duplicated
window summaries:

```yaml
metadata_json["rightsizing_demand"]:
  normalization_version: asg-v2-target-independent-demand
  period_seconds: 300
  start: <UTC ISO-8601>
  end: <UTC ISO-8601>
  points:
    - timestamp: <UTC ISO-8601>
      cpu_used_instance_equivalents: <float or null>
      memory_used_instance_equivalents: <float or null>
```

Each dimension is paired independently with in-service capacity and the point
set is their timestamp union when memory is usable. The same 60-day point array
is filtered into 14/30/60-day windows during candidate evaluation; it is never
copied into every window. The service converts instance equivalents to current
CoreMark/vCPU units and MiB once after loading the current specification.

Existing `asg-v1-exact` rows remain valid for the flag-off capacity-only path
but cannot produce type candidates. During rollout, record
`ASG_INSTANCE_OPTIMIZATION_TELEMETRY_UPGRADE_REQUIRED` as coverage telemetry,
not as a mutation of the V1 response. Enable V2 output only for rows carrying
the target-independent schema.

### 6.1 Telemetry disclosure

Every response returns the following stable `telemetry_summary` shape. An
absent metric retains its key with `present: false`, zero observed days, and
null source or pairing fields as applicable:

```yaml
telemetry_summary:
  cpu_percent:
    present: true
    observed_days: 59.8
    thin_data: false
    pairing_ratio: 0.99
  memory_percent:
    present: true
    observed_days: 59.7
    thin_data: false
    pairing_ratio: 0.98
    source:
      namespace: CWAgent
      metric_name: mem_used_percent
      dimensions:
        - {Name: AutoScalingGroupName, Value: api-production}
    status: usable
  desired_capacity:
    present: true
    observed_days: 60.0
    thin_data: false
  in_service_instances:
    present: true
    observed_days: 60.0
    thin_data: false
```

`observed_days` uses the formula in Section 3. `thin_data` is true below seven
observed days even though ASG V2 also hard-gates such telemetry. CPU and memory
pairing ratios are their timestamp intersections divided by the corresponding
valid utilization timestamp count. Memory `status` is one of `usable`,
`unavailable`, or `insufficient_pairing`; its `source` is the exact persisted
discovered source or null. This shape deliberately extends the EC2 telemetry
disclosure with pairing ratios.

## 7. Capacity model

### 7.1 Per-timestamp load

For each target instance type, choose one CPU capacity basis and use it on both
sides of the ratio:

```python
if current.coremark > 0 and target.coremark > 0:
    current_cpu_units = current.coremark
    target_cpu_units = target.coremark
    cpu_capacity_basis = "coremark"
elif current.vcpus > 0 and target.vcpus > 0:
    current_cpu_units = current.vcpus
    target_cpu_units = target.vcpus
    cpu_capacity_basis = "vcpu_fallback"
else:
    reject target with CPU_CAPABILITY_UNKNOWN
```

CoreMark is a modeled whole-instance throughput score. It is used only for
same-architecture comparisons. The fallback never compares current CoreMark to
target vCPU or vice versa.

For timestamp `t` paired independently with in-service capacity:

```python
cpu_used_capacity_t = (
    in_service_instances_t * cpu_percent_t / 100.0 * current_cpu_units
)

memory_used_mib_t = (
    in_service_instances_t * memory_percent_t / 100.0 * current_memory_mib
)  # only when memory exists
```

Values outside 0–100 are invalid. An in-service count less than one is invalid
for V2. Supported homogeneous groups use instance count, not weighted capacity.

For target instance type `target` and tier ratio `r`:

```python
cpu_required_instances_t = ceil(
    cpu_used_capacity_t / (target_cpu_units * r)
)
memory_required_instances_t = ceil(
    memory_used_mib_t / (target_memory_mib * r)
)  # only when memory exists

required_instances_t = max(
    cpu_required_instances_t,
    memory_required_instances_t,
)
```

The binding dimension is whichever measured dimension produced the selected
desired-capacity requirement. An exact CPU/memory tie is reported as
`cpu_and_memory`; it is never resolved by iteration order. Equality is
conservative and inclusive: a projected ratio exactly equal to the tier ratio
passes that tier.

The current instance type is always evaluated as a target. That candidate
reproduces the V1 capacity-only calculation because its current/target CPU and
memory capacity ratios are one.

Normalize and align `cpu_used_capacity_t` and `memory_used_mib_t` once per ASG,
then reuse those immutable demand series for every target type and tier. Do not
re-fetch or re-normalize telemetry inside the candidate loop. The per-timestamp
`max(cpu_required_instances_t, memory_required_instances_t)` must still be
calculated before window percentiles; moving `max()` outside the timestamp loop
or combining independent CPU and memory percentiles can understate demand.

### 7.2 Window summaries

For each tier and each valid 14-, 30-, and 60-day window:

- `p50_required` is the nearest-rank 50th percentile of timestamped required
  instance counts.
- `p99_required` is the nearest-rank 99th percentile.
- `maximum_required` is evidence only.

Take the maximum available `p50_required` across windows for the minimum-size
calculation and the maximum available `p99_required` across windows for the
desired-capacity calculation, independently for every target instance type.
This mirrors EC2's conservative maximum-across-windows decision rule while
preserving the different meanings of minimum and desired capacity.

### 7.3 Availability floor

The V2 availability floor is:

```python
availability_floor = max(1, number_of_enabled_availability_zones)
```

This is a group-resilience floor, not instance-type offering validation. The
rightsizer does not claim that ASG balancing guarantees one healthy member in
every zone at every instant. It simply never recommends a configured minimum
below the number of zones the group is intended to span.

If the current min, desired, or max is already below the availability floor,
V2 returns no downsize and records `CURRENT_CAPACITY_BELOW_AVAILABILITY_FLOOR`;
it does not use a rightsizing endpoint to recommend an increase. If no usable
zone list is persisted, use a floor of one, add
`ASG_AVAILABILITY_ZONES_UNAVAILABLE_FLOOR_ONE`, and cap any otherwise valid
option at CONDITIONAL.

### 7.4 Exact target values

For each target instance type and tier:

```python
calculated_min = max(
    availability_floor,
    max_valid_window_p50_required,
)

calculated_desired = max(
    calculated_min,
    max_valid_window_p99_required,
)

target_min = min(current_min, calculated_min)
target_desired = min(current_desired, calculated_desired)
target_desired = max(target_min, target_desired)
target_max = current_max
```

V2 never recommends an increase in min or desired count. A target type may be
larger or more expensive per instance, but its total target configuration must
still be cheaper. If observed demand indicates that current capacity is
inadequate, return no downsize and disclose
`CURRENT_CAPACITY_NOT_OVERPROVISIONED` or a stronger operational warning.

An option is eligible when either the target type changes or
`target_desired < current_desired`, and when total monthly savings meet Section
9. A min-only change with the same type and desired count is not monetized or
returned; it may remain a separate ASG finding.

`target_desired` and `target_min` must not exceed current values even when a
smaller target instance would mathematically require more members. If the raw
calculated desired count exceeds current desired, reject that target/tier with
`TARGET_REQUIRES_CAPACITY_INCREASE`. A raw calculated minimum above current
minimum is clamped to the current minimum under Section 7.4 and is not by itself
a rejection when the desired requirement still fits. This preserves the V1
clamping contract while preventing an undersized desired recommendation. The
service does not trade a larger group for smaller instances in V2.

For the unchanged current-type candidate, the same desired-overflow rejection
is retained as candidate evidence. If no alternative target configuration
survives, the response exposes top-level
`CURRENT_CAPACITY_NOT_OVERPROVISIONED` and no recommendation. If another target
survives, that valid recommendation remains visible and the top-level no-option
reason is not emitted; `TARGET_REQUIRES_CAPACITY_INCREASE` remains only in the
rejection summary.

### 7.5 Optimization kinds

Every returned option has exactly one derived `optimization_kind`:

| Kind | Target type | Desired count |
| --- | --- | --- |
| `CAPACITY_ONLY` | unchanged | lower |
| `INSTANCE_ONLY` | changed | unchanged |
| `COMBINED` | changed | lower |

The kind is presentation evidence, not a separate scoring path. All three use
the same target-configuration cost and safety rules.

## 8. Tier behavior

| Tier | Ratio | Meaning |
| --- | ---: | --- |
| Conservative | 0.55 | 45% modeled headroom |
| Balanced | 0.70 | 30% modeled headroom; default |
| Aggressive | 0.85 | 15% modeled headroom |

For one target instance type, target counts must nest:

```text
Conservative min     >= Balanced min     >= Aggressive min
Conservative desired >= Balanced desired >= Aggressive desired
```

Options with the same `(target_instance_type, target_min, target_desired,
target_max)` are deduplicated and labeled with every tier they satisfy. Counts
are not compared across different target types because per-instance capacity
differs. Risk and classification belong to the configuration, not the label.

For each tier, choose the eligible full configuration with the greatest exact
monthly savings. Break ties by lower target monthly cost, lower desired count,
lower minimum count, then lexical target instance type. Candidate generation
uses the Aggressive ratio; tier selection is a projection over the resulting
configurations and never re-runs telemetry collection.

If Balanced is present, `tiers.default = "balanced"`. If only Aggressive is
eligible, it may be shown but `tiers.default = null`; V2 never silently defaults
to the least-cushioned option. If no tier saves at least one cent, all tiers and
the default are null.

The savings-ranked list uses the EC2 Balanced-reserving limiter with a positive
`candidate_limit` and default 10. Tier selection occurs only from the returned
list, and every non-null tier references one returned recommendation. At limit
one, the reserved Balanced configuration may also satisfy Aggressive; tiers
without a returned qualifying configuration are null.

## 9. Pricing and savings

Resolve the current and target instance types in the global EC2 specification
catalog and the group region/platform in regional pricing. A missing current
price or specification blocks monetary output with
`CURRENT_INSTANCE_PRICING_OR_SPEC_MISSING`. A missing target price/specification
skips that target and increments `TARGET_INSTANCE_PRICING_OR_SPEC_MISSING`.

For an option:

```python
current_monthly_cost = current_desired * current_unit_monthly_price
target_monthly_cost = target_desired * target_unit_monthly_price
monthly_savings = current_monthly_cost - target_monthly_cost
yearly_savings = monthly_savings * 12
```

This is immediate full-configuration savings, not a promise that the ASG will
remain at that capacity. Scaling policies may subsequently change desired
capacity within the unchanged min/max bounds. The total is calculated once;
the UI must not add an instance-type saving to a count saving.

Savings eligibility uses `Decimal(str(value))` for prices, the `$0.01` floor,
and `min_monthly_savings`. Equality is included. Display values retain the
existing response rounding convention.

The response discloses:

```text
savings_basis: "initial_target_configuration"
current_unit_monthly_price
target_unit_monthly_price
current_desired
target_desired
scaling_policy_may_change_realized_savings: true
```

### 9.1 Instance candidate generation

Start with the current instance type, then enumerate every non-metal type with
a regional price for the current platform. Do not require a lower unit price;
Section 9 applies the saving floor to the full target configuration.

Normal candidates must have explicit architecture overlap with the current
type. Missing or incompatible evidence produces `ARCHITECTURE_INCOMPATIBLE`.
Cross-family moves are allowed. Reuse the EC2 gated-family policy exactly:

```text
Burstable:  t
Specialized: p, g, inf, trn, dl, f, vt, hpc, u
```

A gated target is eligible only when current and target share that family
class; otherwise record `FAMILY_NOT_ELIGIBLE`. Examples: `t3 -> t3a` is valid,
while general-purpose `m6i -> t3` is not. Architecture migration never enters
normal recommendations.

The catalog does not validate immediate regional launch capacity. Include the
same availability note as EC2. Launch-template compatibility, AMI support, and
application validation remain apply-time work.

### 9.2 Target capability evaluation

CPU and memory determine counts using Section 7. The option also discloses:

```text
family_changed
architecture_overlap
cpu_capacity_basis
performance_ratio
performance_change_pct
current and target vCPU/memory/CoreMark
current and target aggregate capacity at min, desired, and max
```

Performance ratio and change use CoreMark only when both values exist and
architectures overlap; otherwise they are null. Positive changes may be
headlined as modeled CPU throughput evidence. Negative changes remain detail
evidence behind projected headroom, matching the EC2 presentation contract.

V2 deliberately does not add network/EBS CloudWatch queries to the minimal ASG
collector. Therefore an instance-type-changing option cannot use missing group
I/O telemetry to claim network or storage safety. It carries
`INSTANCE_TYPE_CHANGE_NETWORK_STORAGE_VALIDATION_REQUIRED`, sets network and
storage risk to null, and is capped at CONDITIONAL. Catalog network/EBS fields
are still disclosed for side-by-side review but are not converted into a
numeric workload verdict. Capacity-only options retain the existing V1
classification behavior.

This is the sole intentional difference from a fully evaluated standalone EC2
resize. A later version may aggregate member I/O telemetry, but it must define
historical membership alignment before removing the classification cap.

### 9.3 Candidate evaluation order and rejection evidence

Evaluate the full effective V2 flag before the scope gate; this reads only the
cached regional catalog and does not evaluate pricing candidates or telemetry.

Evaluate deterministically in this order:

1. Add the current type as the capacity-only candidate.
2. Load regional/platform prices and global specifications.
3. Exclude changed targets ending in `.metal`, incompatible architecture, and
   disallowed family classes. The unchanged current type remains eligible for
   capacity-only evaluation.
4. Establish one comparable CPU capacity basis.
5. Calculate per-tier target counts from time-aligned CPU and usable memory.
6. Apply missing-memory aggregate retention when needed.
7. Reject raw desired requirements above current desired and enforce the
   availability/max bounds.
8. Apply exact total-configuration savings eligibility.
9. Add operational and instance-change review signals.
10. Deduplicate, rank, limit while reserving Balanced, and assemble tiers.

The response tallies at least these target rejection reasons when applicable:

```text
TARGET_INSTANCE_PRICING_OR_SPEC_MISSING
ARCHITECTURE_INCOMPATIBLE
FAMILY_NOT_ELIGIBLE
CPU_CAPABILITY_UNKNOWN
CPU_REQUIREMENT_NOT_MET
MEMORY_REQUIREMENT_NOT_MET
TARGET_REQUIRES_CAPACITY_INCREASE
TOTAL_CONFIGURATION_SAVINGS_BELOW_MINIMUM
```

Candidates that feed a preview retain their ordinary rejection tally. Rejection
counts do not depend on `candidate_limit`, and target iteration order cannot
change the returned configurations or tallies.

## 10. Scope gates

Return `DEFERRED` before telemetry calculation for:

| Context | Reason code |
| --- | --- |
| Mixed instances or overrides | `MIXED_INSTANCES_POLICY_UNSUPPORTED` |
| Weighted capacity units | `WEIGHTED_CAPACITY_UNSUPPORTED` |
| Warm pool, including configured but currently empty | `WARM_POOL_REQUIRES_SEPARATE_OPTIMIZATION` |
| Scheduled actions | `SCHEDULED_SCALING_REQUIRES_SEPARATE_OPTIMIZATION` |
| Predictive scaling | `PREDICTIVE_SCALING_REQUIRES_SEPARATE_OPTIMIZATION` |
| Scale to zero (`min=0` or `desired=0`) | `SCALE_TO_ZERO_UNSUPPORTED` |
| More than one effective instance type | `HETEROGENEOUS_INSTANCE_TYPES_UNSUPPORTED` |
| Active instance refresh | `INSTANCE_REFRESH_IN_PROGRESS` |
| Scale-in protection prevents reduction | `SCALE_IN_PROTECTION_ACTIVE` |
| Critical scaling process suspended | `SCALING_PROCESS_SUSPENDED` |
| Unsupported lifecycle/purchase configuration | `UNSUPPORTED_INSTANCE_LIFECYCLE` |

Critical suspended processes are `Launch`, `Terminate`, `AlarmNotification`, or
`AZRebalance`. Unsupported groups produce no recommendations or previews.

## 11. Telemetry sufficiency

CPU and in-service count are required series. Historical desired capacity is a
recommended context series; the authoritative current desired value comes from
`DescribeAutoScalingGroups`.

For a normal recommendation:

- CPU/in-service timestamps have at least 90% pairing coverage.
- At least seven observed days are available in one decision window.
- Memory is present with at least 90% memory/in-service pairing coverage, or
  the target configuration passes the explicit aggregate memory-retention
  floors in Section 12.

ACTIONABLE additionally requires desired-capacity history and usable measured
memory. The documented desired-history and retained-memory exceptions remain
normal recommendations but cap classification at CONDITIONAL.

If CPU or in-service capacity is absent, return `INSUFFICIENT_DATA` with:

```text
ASG_CPU_METRIC_UNAVAILABLE
ASG_IN_SERVICE_CAPACITY_METRIC_UNAVAILABLE
```

When `GroupInServiceInstances` is absent, include this required remediation:

```text
Historical ASG capacity is unavailable. Enable Auto Scaling group metrics
collection to provide GroupInServiceInstances for capacity assessment.
```

The service must not claim that group metrics are free and must not enable them.

If CPU/in-service pairing is below the inclusive 0.90 threshold, return
`INSUFFICIENT_DATA / ASG_CPU_CAPACITY_PAIRING_INSUFFICIENT`. If a discovered
memory series is present but memory/in-service pairing is below the threshold,
treat memory as unusable with `status: insufficient_pairing`; apply the same
aggregate retention rule as missing memory. Configurations that reduce
aggregate memory remain preview-only and use blocker
`ASG_MEMORY_CAPACITY_PAIRING_INSUFFICIENT`.

If desired-capacity history is absent but the current desired value is known,
the service may still calculate current-price savings, but classification is
capped at CONDITIONAL and includes
`ASG_DESIRED_CAPACITY_HISTORY_UNAVAILABLE`.
Include this remediation:

```text
Historical desired capacity is unavailable. Enable Auto Scaling group metrics
collection to provide GroupDesiredCapacity; the current desired value remains
available from the group configuration.
```

If fewer than seven observed days exist, return `INSUFFICIENT_DATA /
ASG_TELEMETRY_WINDOW_TOO_SHORT`. This is intentionally stricter than standalone
EC2 thin-data disclosure because an ASG count reduction cannot retain the
unmeasured aggregate capacity. Include a message explaining that at least seven
observed days are required and how many were available; the shared UI should not
render this as an ordinary EC2 thin-data warning.

## 12. Missing memory, capacity retention, and savings preview

Missing memory uses the EC2 contract: retain current capacity in the unmeasured
dimension without adding the tier ratio as artificial utilization. For ASG the
capacity is aggregate, so a configuration may remain a normal recommendation
only when both floors pass:

```python
target_min * target.memory_mib >= current_min * current.memory_mib
target_desired * target.memory_mib >= current_desired * current.memory_mib
```

The fallback memory dimension is excluded from `projected_util` and cannot be
the binding dimension. Attach
`MEMORY_METRIC_UNAVAILABLE_CURRENT_CAPACITY_RETAINED`. A same-type count
reduction necessarily fails the desired floor, preserving V1 preview behavior;
a larger-memory target type may safely reduce count while retaining aggregate
memory. If either current or target memory capacity is unknown, the target fails
the floor rather than treating unknown as unlimited.

Until V2 is effectively enabled, the flag-off path retains the V1 preview
message as part of the invariant-25 decision contract.

When CPU and capacity history are usable but a candidate fails only this memory
floor:

- Calculate its exact CPU-only tier min/desired values.
- Put it in the memory preview pool.
- Return at most one `savings_previews` object of kind
  `MEMORY_METRIC_MISSING_CAPACITY_REDUCTION`, selecting the highest total
  configuration saving with deterministic target-type tie-breaking.
- Use the EC2-compatible field names `kind`, `classification`, `blockers`,
  `message`, and `evidence`, with ASG tier configurations nested in `options`.
- Classification is PREVIEW.
- Include the CPU-only options, target instance type, estimated savings,
  aggregate memory shortfall, and telemetry evidence.
- Do not add it to `recommendations` or normal `tiers`.
- Keep the ordinary rejection summary unchanged; the preview is derived from
  the rejected candidate.

Required message:

```text
CPU history indicates that this instance-type and capacity configuration may
save money, but it reduces aggregate memory while total memory demand is
unknown. Enable or repair memory metrics to verify the change.
```

The object contract is:

```yaml
kind: MEMORY_METRIC_MISSING_CAPACITY_REDUCTION
classification: PREVIEW
blockers: [MEMORY_METRIC_NOT_ENABLED]
message: <required message above>
options:
  conservative: <embedded configuration or null>
  balanced: <embedded configuration or null>
  aggressive: <embedded configuration or null>
  default: balanced | null
evidence: {}
```

Use `MEMORY_METRIC_NOT_ENABLED` when no usable memory series was discovered and
`ASG_MEMORY_CAPACITY_PAIRING_INSUFFICIENT` when a series exists but cannot be
paired reliably. Use `ASG_MEMORY_TELEMETRY_WINDOW_TOO_SHORT` when the discovered
series is valid and sufficiently paired but has fewer than seven observed days;
this tells the user to wait for coverage rather than enable an already-enabled
metric. Preview configurations use the same configuration, pricing,
deduplication, and ordering fields as normal ASG options. Each non-null tier
embeds its complete configuration; collapsed tiers serialize the same
configuration and `satisfied_tiers` values.

The preview is controlled by the shared default-on memory preview flag and is
omitted when no CPU-only tier has positive total-configuration savings.

An independently eligible cross-architecture candidate may also produce one
`GRAVITON_MIGRATION` preview under the EC2 rules. It must retain raw aggregate
vCPU and memory at both current min and desired counts, null CoreMark-derived
cross-architecture evidence, use OPPORTUNITY, and never enter recommendations
or normal tiers.

## 13. Operational signals

Use paginated `DescribeScalingActivities` and policy metadata without collecting
additional CloudWatch series.

The following prevent ACTIONABLE but keep an otherwise valid option visible as
CONDITIONAL:

```text
RECENT_SCALING_FAILURE_REQUIRES_REVIEW
RECENT_CAPACITY_SHORTAGE_REQUIRES_REVIEW
DESIRED_IN_SERVICE_MISMATCH_REQUIRES_REVIEW
NON_CPU_SCALING_SIGNAL_REQUIRES_REVIEW
NO_DYNAMIC_SCALING_POLICY_REQUIRES_REVIEW
CAPACITY_REBALANCE_REQUIRES_REVIEW
```

A current desired/in-service mismatch is a transient operational signal unless
the in-service count is zero, which is `INSUFFICIENT_DATA` with
`ASG_CURRENT_IN_SERVICE_CAPACITY_ZERO`. Failed activities do not become zero
demand. Activity text may support evidence, but classification must use
structured status fields wherever AWS supplies them.

Activity warnings describe current operational state, not a fixed historical
lookback. Categorize activities by operation and inspect the newest activity in
each category. A newer successful activity clears an older failure of that
operation. Capacity-shortage evidence requires the newest launch activity to be
failed and to carry capacity-failure evidence.

## 14. Classification

### ACTIONABLE

Allowed only when:

- The group passes every scope gate.
- CPU, memory, desired, and in-service evidence are usable.
- At least one exact tier reduces desired capacity and meets the savings floor.
- No recent scaling failure, shortage, or persistent desired/in-service mismatch
  is present.
- The group has a CPU target-tracking policy, or equivalent supported CPU-driven
  policy whose metric is explicitly identified.
- The option respects the availability floor.
- The target instance type is unchanged. V2 does not label a type-changing
  option ACTIONABLE until network/storage workload validation is available.

### CONDITIONAL

Use when the calculation is complete and no hard/scope failure exists, but a
review signal remains, including an instance-type change, non-CPU scaling
logic, desired-history gaps, capacity rebalance, or recent recoverable activity
failures.

Missing memory is CONDITIONAL only for an option that passes both aggregate
memory-retention floors. A candidate that fails either floor uses PREVIEW.

### PREVIEW

CPU-derived exact configurations that fail the missing-memory capacity floor.
A Graviton OPPORTUNITY is also preview-only. Previews are awareness, not
executable recommendations.

### INSUFFICIENT_DATA

Required CPU/in-service telemetry is absent, time alignment is unusable, or the
minimum observed window is too short.

### DEFERRED

The group configuration is outside V2 scope. Scope is checked before telemetry
and pricing so unsupported groups do not spend collection work unnecessarily.

### REJECTED

Reserved for evaluated configurations that fail a known hard rule. Most ASG V2
failures produce no option, DEFERRED, or INSUFFICIENT_DATA rather than a visible
REJECTED target.

## 15. Risk and reason evidence

Each option carries:

```yaml
risk_assessment:
  telemetry: LOW | MEDIUM | HIGH
  compute: LOW | MEDIUM | HIGH
  memory: LOW | MEDIUM | HIGH
  network: null
  storage: null
  compatibility: LOW | MEDIUM | HIGH
  migration: null
  operations: LOW | MEDIUM | HIGH
  overall: LOW | MEDIUM | HIGH
  reason_codes: []
```

The ASG envelope retains the shared EC2 risk keys. Network and storage remain
null because V2 does not collect their group workload demand, and the shared UI
renders null as `n/a`; it does not omit the chips or translate null to LOW.
Same-architecture target changes set compatibility to MEDIUM and carry the
validation reason from Section 9.2; Graviton stays outside normal risk as an
OPPORTUNITY preview. `operations` is the ASG-specific extension. `overall` is
the highest non-null component risk. Classification and risk are related but
not interchangeable: a complete non-CPU scaling policy may be CONDITIONAL even
when telemetry risk is LOW.

Capacity evidence includes:

```text
decision_statistic
lookback_windows
observed_days per metric
pairing coverage
selected memory metric namespace, name, dimensions, and status
current min/desired/max
current and target instance type and family
target min/desired/max
optimization kind
availability floor and AZ count
CPU and memory p99
CPU and memory used-instance p99
p50 and p99 required counts per window
selected window maxima
projected CPU/memory ratio at target desired
aggregate current/target CPU and memory at min, desired, and max
CPU capacity basis and modeled performance change
binding dimension
scaling policy kind and metric
activity signal summary
```

## 16. Response contract

### 16.1 Detail response

```yaml
inventory_id: 123
resource_id: api-production
resource_name: api-production
account_id: "123456789012"
region: us-east-1
state: active
classification: ACTIONABLE | CONDITIONAL | PREVIEW | DEFERRED | INSUFFICIENT_DATA | null
current_monthly_cost: 876.00
pricing_source: street_pricing_sqlite
current_capacity_evidence:
  min_size: 6
  desired_capacity: 10
  max_size: 20
  in_service_instances: 10
  availability_zone_count: 3
  inventory_generated_at: "2026-07-15T00:00:00Z"
current_configuration:
  instance_type: m6i.large
  min_size: 6
  desired_capacity: 10
  max_size: 20
  availability_zones: [us-east-1a, us-east-1b, us-east-1c]
recommendations:
  - target_instance_type: m7i.large
    optimization_kind: COMBINED
    family_changed: false
    target_min_size: 3
    target_desired_capacity: 7
    target_max_size: 20
    max_size_changed: false
    satisfied_tiers: [balanced, aggressive]
    projected_cpu_util: 0.61
    projected_memory_util: 0.66
    projected_util: 0.66
    binding_dimension: memory
    cpu_capacity_basis: coremark
    performance_ratio: 1.12
    performance_change_pct: 12.0
    current_unit_monthly_price: 87.60
    target_unit_monthly_price: 78.84
    target_monthly_cost: 551.88
    monthly_savings: 324.12
    yearly_savings: 3889.44
    classification: CONDITIONAL
    risk_assessment: {}
    reason_codes: [INSTANCE_TYPE_CHANGE_NETWORK_STORAGE_VALIDATION_REQUIRED]
    evidence: {}
tiers:
  conservative: <option reference or null>
  balanced: <option reference or null>
  aggressive: <option reference or null>
  default: balanced | null
savings_previews: []
blocking_reasons: []
deferred_reason_codes: []
messages: []
telemetry_summary: <Section 6.1 shape>
capacity_policy: {}
scope_policy: {}
candidate_policy: {}
pricing_evidence: {}
rejection_summary: {}
availability_note: <same note as EC2>
```

The always-present top-level `classification` is an intentional ASG extension;
normal EC2 envelopes do not currently guarantee that field. Shared list UI code
must read each product contract rather than assuming EC2 parity. ASG never emits
OPPORTUNITY for a normal recommendation; it may appear only inside a Graviton
savings preview.

Top-level `classification` is PREVIEW when only a savings preview exists. For
normal recommendations it is the Balanced option's classification; when only
Aggressive is present it is that option's classification. A fully evaluated
group with no eligible reduction has `classification: null` and
`CURRENT_CAPACITY_NOT_OVERPROVISIONED`. DEFERRED and INSUFFICIENT_DATA retain
their explicit top-level classifications.

The recommendation list is ordered by exact monthly savings descending, then
target monthly cost, `target_desired_capacity`, `target_min_size`, and lexical
`target_instance_type`. V2 accepts a bounded `candidate_limit` because multiple
types produce multiple configurations; it uses the shared EC2
Balanced-reserving behavior.

Tier objects reference returned recommendations by the complete configuration,
not merely desired capacity:

```text
(target_instance_type, target_min_size, target_desired_capacity, target_max_size)
```

`current_monthly_cost`, `pricing_source`, and `current_capacity_evidence` mirror
the EC2 envelope so shared current-cost and savings-percentage UI can be reused.
`inventory_generated_at` is serialized as an explicit UTC ISO-8601 value ending
in `Z`; timezone information lost during a SQLite round-trip is interpreted as
UTC rather than local time.
`pricing_evidence` contains both regional unit prices and the global
specification source. `capacity_policy` owns group count math.
`candidate_policy` is the shared EC2 instance-candidate policy exposed with the
same gated-family evidence. They are separate because changing one must not
silently change the other.
Fields stay present on early DEFERRED or INSUFFICIENT_DATA paths; values that
would require a skipped pricing or telemetry lookup are null.

### 16.2 List response

The list endpoint returns one envelope per persisted ASG, ordered by inventory
ID after account/region/state filtering. DEFERRED and INSUFFICIENT_DATA envelopes
remain visible so the UI can explain coverage and scope; an optional
`classification` filter may narrow the list.

## 17. Policy objects

```python
ASGCapacityPolicy(
    tier_ratios=(
        ("conservative", 0.55),
        ("balanced", 0.70),
        ("aggressive", 0.85),
    ),
    default_tier="balanced",
    minimum_statistic="p50",
    desired_statistic="p99",
    lookback_days=(14, 30, 60),
    period_seconds=300,
    minimum_observed_days=7.0,
    minimum_pairing_ratio=0.90,
    availability_floor_per_az=1,
    candidate_limit=10,
    instance_optimization_enabled=True,
    policy_version="asg-v2-combined",
)
```

```python
EC2CandidatePolicy(
    gated_family_classes=("t", "p", "g", "inf", "trn", "dl", "f", "vt", "hpc", "u"),
    memory_preview_enabled=True,
    graviton_preview_enabled=True,
)
```

```python
ASGScopePolicy(
    allow_mixed_instances=False,
    allow_weighted_capacity=False,
    allow_warm_pool=False,
    allow_scheduled_scaling=False,
    allow_predictive_scaling=False,
    allow_scale_to_zero=False,
)
```

V2 exposes policy values in every response. Request-scoped overrides for the
out-of-scope gates are not supported until their behavior is separately
specified and tested.

## 18. Confidence trend

The detail UI may request an ASG confidence trend. It follows the EC2 trend
contract and does not affect the recommendation:

- Up to 455 days at daily granularity.
- CPU always requested using the ASG dimension.
- Memory included only with the persisted discovered source.
- Daily Maximum is the headline.
- Buckets are 30d, 90d, 120d, 180d, 365d, and 455d.
- Maximum uses trailing daily maxima; p95/p99 use the latest complete aligned
  bucket semantics already documented by EC2.
- Network and storage are omitted.
- Cache key includes account, region, ASG name, memory source identity, and
  schema version `asg-trend-v1`.

Including memory source identity is an intentional improvement over the current
EC2 trend cache key, not a claim of byte-for-byte parity. It prevents stale ASG
memory trends after discovery selects a different source and is a candidate for
a later EC2 backport.

## 19. API contract

Read-only endpoints:

```text
GET /recommendations/asg/rightsize
GET /recommendations/asg/rightsize/{inventory_id}
GET /recommendations/asg/rightsize/{inventory_id}/trend
```

List query parameters:

```text
account_id
region
state=active
classification
min_monthly_savings=0.0
candidate_limit=10
```

`classification` is case-insensitive and restricted to the ASG top-level
classification vocabulary. Unsupported values return HTTP 422 rather than a
silent empty list.

Detail accepts `min_monthly_savings` and `candidate_limit`. Tier ratios are
configured by service policy in V2 rather than arbitrary public query
parameters. Missing inventory
returns HTTP 404. AWS failures in the on-demand trend return HTTP 502; persisted
recommendation evaluation performs no AWS calls.

## 20. Permissions and cost discipline

Reuse existing read permissions and add only missing describe permissions:

```text
autoscaling:DescribeAutoScalingGroups
autoscaling:DescribeInstanceRefreshes
autoscaling:DescribeLaunchConfigurations
autoscaling:DescribePolicies
autoscaling:DescribeScalingActivities
autoscaling:DescribeScheduledActions
autoscaling:DescribeWarmPool
cloudwatch:GetMetricData
cloudwatch:ListMetrics
ec2:DescribeLaunchTemplateVersions
```

Do not require `autoscaling:EnableMetricsCollection` for the recommendation
service. The collector requests only the four series in Section 5 and batches
queries within CloudWatch limits. Different statistics or periods are separate
metric queries and must be described honestly in cost estimates.

## 21. Compatibility and rollout

- Add a dedicated ASG inventory model/table; do not overload `Ec2Inventory` or
  finding rows.
- Extract shared EC2 candidate helpers before enabling ASG instance
  optimization. The extraction must not change standalone EC2 output.
- With `instance_optimization_enabled=False`, the recommendation, tier,
  preview, reason, and classification decision fields are byte-for-byte the V1
  ASG capacity-only output for the same input.
- With the flag enabled, the current-instance candidate must produce the same
  capacity-only configuration and classification as V1; new target types are
  additive competitors.
- Existing ASG checks and pricing behavior remain unchanged until explicitly
  migrated.
- Existing EC2-managed-by-ASG instances continue to return
  `DEFERRED / MANAGED_BY_ASG`; the new ASG envelope becomes their group-level
  optimization surface.
- Missing ASG inventory produces no recommendation, never a synthetic default
  group.
- Deploy collection before enabling the endpoints so persisted inputs exist.
- Roll out candidate generation behind the default-off deployment setting
  `ASG_INSTANCE_OPTIMIZATION_ENABLED`. Deployment-off always wins over the
  policy's default-on `instance_optimization_enabled`; neither a request nor a
  policy override may bypass it. Effective enablement additionally requires V2
  target-independent telemetry and the implementation plan's CoreMark catalog
  coverage gate. Compare shadow V1/V2 capacity-only decisions before enabling.
- Monitor optimization-kind counts, candidate rejection codes, ACTIONABLE,
  CONDITIONAL, PREVIEW, DEFERRED, and INSUFFICIENT_DATA plus metric/catalog
  coverage ratios.

## 22. Acceptance invariants

1. `target_max_size` always equals current `MaxSize`.
2. `availability_floor <= target_min <= target_desired <= current_max`.
3. V2 never recommends increasing min or desired count.
4. Every normal recommendation changes the type or reduces desired capacity,
   and saves at least one cent under decimal-safe total-configuration
   comparison.
5. For one target type, Conservative counts are never below Balanced and
   Balanced counts are never below Aggressive.
6. For one target type, increasing any time-aligned demand point cannot lower a
   recommended count or improve classification.
7. Missing timestamps never become zero demand.
8. Independent metric percentiles are never multiplied to derive demand.
9. Missing memory never produces a normal configuration whose aggregate memory
   at min or desired is below the corresponding current aggregate capacity.
10. A preview never enters recommendations or normal tiers.
11. Unsupported scope returns before pricing and telemetry evaluation.
12. Identical inputs produce byte-for-byte identical decision fields.
13. Savings equal current desired times current regional price minus target
    desired times target regional price; no component saving is double-counted.
14. The service performs no AWS calls while reading a persisted recommendation.
15. Collection requests no metric outside Section 5 for ASG V2.
16. Every response exposes the complete telemetry-summary keys and explicit
    memory source/status contract from Section 6.1.
17. CPU pairing below policy returns its explicit insufficient-data code;
    insufficient memory pairing follows the same retention/preview split as
    absent memory.
18. Null network/storage/migration risks never contribute to `overall`.
19. A normal type-changing recommendation has explicit architecture overlap,
    passes the shared family gate, and is never `.metal`.
20. A type-changing normal recommendation is CONDITIONAL and carries
    `INSTANCE_TYPE_CHANGE_NETWORK_STORAGE_VALIDATION_REQUIRED`.
21. Every non-null tier references a returned recommendation by its complete
    type-and-count configuration.
22. Candidate limiting never displaces the full-set Balanced selection.
23. A CoreMark/vCPU fallback never mixes unlike CPU capacity units.
24. A more expensive unit-price target is eligible only when its complete
    target configuration still meets the savings floor.
25. Flag-off ASG decision projection, including its effective V1
    `capacity_policy` view, and standalone EC2 output remain byte-for-byte
    compatible with their pre-extraction regression fixtures.
26. A returned `WarmPoolConfiguration` always triggers the warm-pool scope gate,
    regardless of the current warm-instance count.
27. A current-type desired overflow produces top-level
    `CURRENT_CAPACITY_NOT_OVERPROVISIONED` only when no alternative target
    survives.
28. Demand series are normalized once and reused, but required-count `max()` is
    evaluated per timestamp before any percentile.

## 23. Explicit V2 examples

### Clean Balanced recommendation

```text
Three-AZ homogeneous group
Current min=6, desired=10, max=20
Balanced p50 required=2, p99 required=7
Availability floor=3

Target min=max(3, 2)=3
Target desired=max(3, 7)=7
Target max=20 unchanged
```

### No reduction

```text
Current desired=6
Balanced p99 required=7

V2 does not recommend an increase and emits no Balanced capacity-only downsize.
```

### Missing memory

```text
Current and target type are the same.
CPU supports min=3, desired=7
Memory is unavailable

Normal recommendations=[]
savings_previews=[MEMORY_METRIC_MISSING_CAPACITY_REDUCTION]
```

### Instance-only optimization

```text
Current: m6i.large, min=3, desired=6, max=12
Target:  m7i.large, min=3, desired=6, max=12
Target provides sufficient modeled CPU and memory capacity.
The target configuration is cheaper in the regional catalog.

optimization_kind=INSTANCE_ONLY
classification=CONDITIONAL
reason=INSTANCE_TYPE_CHANGE_NETWORK_STORAGE_VALIDATION_REQUIRED
```

### Combined optimization with a higher unit price

```text
Current: 10 x m6i.large at $80 = $800/month
Target:   4 x m6i.xlarge at $150 = $600/month

The target unit price is higher, but the safe target count is lower.
optimization_kind=COMBINED
monthly_savings=$200 (calculated once from complete configurations)
```

### Missing memory retained by a larger target

```text
Current: 10 x 8 GiB = 80 GiB aggregate desired memory
Target:   5 x 16 GiB = 80 GiB aggregate desired memory
The corresponding aggregate minimum-memory floor also passes.

Memory is unavailable, but the unmeasured dimension is retained byte-for-byte.
The option may be a normal CONDITIONAL recommendation with
MEMORY_METRIC_UNAVAILABLE_CURRENT_CAPACITY_RETAINED.
```

### Graviton migration

```text
Current architecture: x86_64
Target architecture: arm64

The target is never a normal recommendation. When raw aggregate vCPU and memory
retention plus total savings pass, return one GRAVITON_MIGRATION OPPORTUNITY
preview with ARCHITECTURE_MIGRATION_REQUIRED.
```

### Tier collapse

```text
Conservative, Balanced, and Aggressive all calculate min=3, desired=5.

Return one configuration labeled with all three tiers.
```

### Availability floor

```text
Four enabled AZs; calculated min=2.

Target min=4. Target desired must be at least 4.
```
