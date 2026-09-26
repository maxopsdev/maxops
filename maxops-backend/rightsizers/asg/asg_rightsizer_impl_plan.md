# ASG Rightsizer Implementation Plan

Status: authoritative V2 implementation handoff for
`asg_rightsizer_spec.md`. The V1 capacity-only backend is implemented and is
the frozen flag-off regression baseline.

Extend the read-only, inventory-backed ASG rightsizer so it can recommend an EC2
instance type and exact `MinSize`/`DesiredCapacity` together. The existing V1
capacity-only output must remain byte-for-byte identical when instance
optimization is disabled. Modify the EC2 rightsizer only through shared pure-
helper extraction guarded by its named regression suite.

Run only explicitly named unit files. Never use broad pytest discovery because
repository integration tests can create billable AWS resources.

## 1. Plan versioning and delivery sequence

Sections 2–8 record the implemented V1 foundation and its safe execution
contract. They are retained so earlier review findings and test-node references
remain auditable. Section 9 is the normative V2 delta and overrides any V1-only
instruction in Sections 2–8, including the former prohibition on EC2 candidate
helper reuse. Do not reinterpret an old V1 section citation as a V2 requirement.

V2 implementation proceeds in this order:

1. Prove the named V1 baseline is green with no xfails.
2. Extract shared EC2 candidate/savings/CPU-capacity helpers with EC2 parity.
3. Persist target-independent ASG demand points under a versioned V2 schema.
4. Implement type candidate generation and combined count calculation.
5. Add V2 pricing, tiers, previews, response evidence, and API parameters.
6. Implement the V2 scenario/manifest suites and shadow parity gate.
7. Enable instance optimization only after V2 telemetry coverage is populated.

No new AWS metric, permission, inventory table, or write endpoint is required.
The V2 telemetry schema must land before instance candidates are enabled.
Recommendation evaluation continues to operate only on persisted data.

### 1.1 V1 baseline gate

The earlier review findings are fixed in the current implementation and become
non-negotiable regression fixtures. Use this stable finding-to-node map; do not
replace a focused node with a broad-file assertion:

The inclusive exact-tier boundary fix supersedes the previously frozen
binary-float over-count in V1 required-capacity decisions.

| Finding | Baseline contract | Required pytest node(s) |
| --- | --- | --- |
| F1 | independently capacity-paired CPU/memory timestamp union | `tests/test_asg_metric_collection.py::test_usable_memory_does_not_erase_cpu_spike_without_memory_timestamp`; `::test_usable_memory_spike_survives_missing_cpu_timestamp` |
| F2 | minimum overflow clamps while a valid desired reduction survives | `tests/test_asg_rightsizer_scenarios.py::test_required_min_above_current_is_clamped_while_desired_still_reduces` |
| F3 | configured-but-empty warm pool is detected and deferred | `tests/test_asg_metric_collection.py::test_asg_discovery_paginates_resolves_launch_and_isolates_group_failures`; `tests/test_asg_rightsizer_scenarios.py::test_scope_gates[warm_pool_present-True-WARM_POOL_REQUIRES_SEPARATE_OPTIMIZATION]` |
| F4 | shared `MaxOpsInventory.account_id/region` retain their migrated nullable contract | `tests/test_asg_metric_collection.py::test_inventory_model_nullability_matches_existing_and_new_migrations` |
| F5 | ASG account/region are non-null in ORM and migration and collection supplies both | the same nullability node plus `tests/test_asg_rightsizer.py::test_same_asg_name_is_scoped_by_account_and_region` |
| F6 | zero current in-service capacity is INSUFFICIENT_DATA | `tests/test_asg_rightsizer.py::test_zero_current_in_service_is_insufficient_even_with_healthy_history` |
| F7 | every required remediation is appended to `messages[]`; singular `message` is absent | `tests/test_asg_rightsizer.py::test_missing_required_metric_is_insufficient[in_service_instances-ASG_IN_SERVICE_CAPACITY_METRIC_UNAVAILABLE]`; `::test_desired_history_missing_caps_conditional`; `tests/test_asg_rightsizer_scenarios.py::test_short_window_boundary` |
| F8 | usable-but-young memory uses its distinct short-window blocker | `tests/test_asg_rightsizer_scenarios.py::test_short_memory_window_uses_documented_preview_blocker` |
| F9 | `ListMetrics` uses an exact `AutoScalingGroupName` Name+Value filter and full pagination; a broad regional listing, if ever used, is scan-scoped cached and filtered client-side | `tests/test_asg_memory_metric_discovery.py::test_group_memory_discovery_paginates_filters_and_ranks`; add `::test_asg_list_metrics_filter_or_cache_is_scope_bounded` before extraction |
| F10 | stable-member lookback and pairing proof derive from `ASGCapacityPolicy` | `tests/test_asg_metric_collection.py::test_stable_member_proof_uses_capacity_policy_window_and_ratio` |

F4/F5 include Alembic revision
`20260715_000001_add_asg_inventory.py`: the V1 baseline introduced the dedicated
ASG table and its non-null composite identity while deliberately leaving the
older shared inventory columns nullable. This migration/model parity is V1
baseline work, not a V2 schema change and not covered by Section 9's statement
that V2 adds no new table.

If any baseline assertion fails, fix it before shared extraction. V2 must not
encode a failing result into its flag-off parity fixture.

## 2. V1 baseline record: values and inventory

### 2.1 Shared values

Add an ASG package under `rightsizers/asg/asg_rightsizer/` with immutable values
for:

- `ASGCapacityPolicy` using the exact defaults in spec Section 17.
- `ASGScopePolicy` using the exact default-off scope overrides.
- Operational reason codes and warning messages.
- Capacity option and preview helpers.

Reuse `RecommendationClassification`, `RiskLevel`, `RiskAssessment` patterns,
and percentile helpers from the EC2 package where their semantics are identical.
Do not import EC2 candidate-family or network/EBS evaluators.

The shared classification enum includes OPPORTUNITY, but ASG V1 never emits it.
Add no ASG-specific enum fork.

Validate policy construction:

- Unique tier names and ratios in `(0, 1]`.
- Balanced exists and equals the default 0.70 target.
- Lookback windows are positive and include 14, 30, and 60 by default.
- Period is positive.
- Minimum observed days is non-negative.
- Pairing ratio is in `[0, 1]`.
- Availability floor per AZ is at least one.

### 2.2 Inventory model

Add `AsgInventory` to `app/models/inventory.py` and `RESOURCE_MODELS`. Use a
dedicated `asg_inventory` table with the fields listed in spec Section 4. Keep
large or evolving AWS structures in JSON columns and promote only filter and
decision fields to typed columns.

Use `(account_id, region, resource_id)` as the database uniqueness constraint;
ASG names can repeat across accounts and regions. Keep `inventory_id` unique for
API lookup and add the table through the next Alembic migration. Declare
`account_id` and `region` non-null in both ORM and migration; reject scan/import
rows with missing identity before persistence.

Required indexed columns:

```text
inventory_id unique
resource_id indexed; unique only with account_id and region
account_id
region
state
instance_type
generated_at
```

Required typed capacity columns:

```text
min_size
desired_capacity
max_size
```

Update the synthetic inventory importer and scan persistence paths so ASG rows
round-trip without using `MaxOpsInventory` as the recommendation source.

## 3. V1 baseline record: ASG discovery and scope metadata

### 3.1 Adapter collection

Add paginated adapter methods for:

```text
DescribeAutoScalingGroups
DescribeScalingActivities
DescribeScheduledActions
DescribePolicies
DescribeWarmPool
DescribeInstanceRefreshes
DescribeLaunchConfigurations when a group uses one
DescribeLaunchTemplateVersions when needed to resolve instance type/platform
```

Reuse the cached adapter conventions. Collection exceptions must be isolated per
group and recorded as coverage errors; one malformed group must not abort the
regional scan.

### 3.2 Normalize the group

For every ASG:

- Resolve current capacity and member lifecycle states.
- Resolve one effective instance type from the launch definition and verify it
  against in-service members.
- Record enabled Availability Zones and current in-service IDs.
- Detect mixed policy, weights, warm pool, schedules, predictive policies,
  active refresh, scale-in protection, Spot lifecycle, and critical suspended
  processes.
- Summarize policy kind and scaling metric without interpreting arbitrary metric
  math in V1.
- Store structured activity statuses and a bounded evidence summary; do not use
  free-text parsing when a structured status exists.

Evaluate scope gates before metric enrichment where the scan orchestration can
do so safely. Still persist the inventory envelope for DEFERRED responses.

### 3.3 Permissions

Extend onboarding IAM only for missing read operations named in spec Section 20.
Do not add `EnableMetricsCollection`, mutation, update, or termination actions.

## 4. V1 baseline record: minimal metric collection

### 4.1 Adapter interface

Add a focused method, conceptually:

```python
get_asg_rightsizing_metrics(
    asg_name,
    start_date,
    end_date,
    *,
    region,
    period_seconds=300,
    memory_source=None,
) -> dict
```

It submits only:

```text
AWS/EC2 CPUUtilization / AutoScalingGroupName / Average
AWS/AutoScaling GroupDesiredCapacity / AutoScalingGroupName / Maximum
AWS/AutoScaling GroupInServiceInstances / AutoScalingGroupName / Maximum
one selected memory query / Average, when available
```

Always attempt `GroupDesiredCapacity` for context. Its absence is a CONDITIONAL
coverage result because current desired capacity comes from inventory.
`GroupInServiceInstances` is required for the demand calculation. Neither the
collector nor the service may call `EnableMetricsCollection`.

Follow every `NextToken`, use `TimestampAscending`, and retain timestamp/value
pairs. Batch safely within CloudWatch query/datapoint limits. Do not add network,
EBS, warm-pool, pending, terminating, forecast, or group min/max queries.

### 4.2 Memory reuse

Extract or extend EC2 memory discovery so ASG and EC2 use the same name filter,
ranking, valid-value checks, fallback, and source shape. Preserve current EC2
tests byte-for-byte.

For ASG aggregation:

- Prefer a trustworthy group-associated metric source.
- Otherwise assemble exact member sources only when membership association for
  the timestamp is available.
- Reject partial historical membership aggregation rather than treating it as
  complete group memory.
- Persist the selected source identity or explicit unavailable status.
- Prefer the group-dimensioned source, then allow the spec Section 5.2 stable-
  member aggregate only when every proof condition and complete timestamp
  intersection succeeds.

### 4.3 Normalization

Add an ASG enrichment function beside the EC2 enrichment path:

- Request 60 days at 300 seconds.
- Build 14d, 30d, and 60d windows from the returned raw series.
- Join CPU and memory independently to positive in-service timestamps.
- CPU remains the required base series. When memory is usable, calculate over
  the union of CPU/in-service and memory/in-service timestamps. At each point,
  evaluate every measured dimension without treating a missing dimension as
  zero, so neither a CPU nor memory spike is erased by a gap in the other series.
- Calculate pairing coverage.
- Calculate used-instance and required-instance series before percentiles.
- Use the existing nearest-rank helper.
- Persist the schema in spec Section 6 with
  `normalization_version="asg-v1-exact"`.
- Build the complete Section 6.1 telemetry disclosure for every metric,
  including `present`, `observed_days`, `thin_data`, CPU/memory pairing ratios,
  and persisted memory `source`/`status`.
- Mark a discovered memory source below the pairing threshold as
  `insufficient_pairing`, not absent or usable.

Missing points remain absent. Do not forward fill group size, interpolate, or
substitute the current desired count into historical buckets.

### 4.4 Operational signals

Persist, per assessment:

- Failed or cancelled scaling activity seen.
- Capacity shortage or failed launch evidence.
- Current desired/in-service mismatch.
- Scaling-policy kind and primary metric.
- Capacity rebalance flag.

For failures and shortages, retain only the newest outcome per categorized
operation as the decision signal. A later success clears the earlier failure;
the full bounded activity evidence may remain persisted for disclosure.

These are metadata, not new CloudWatch queries.

## 5. V1 baseline record: recommendation service

### 5.1 Service boundary

Create `app/services/asg_rightsizer.py` with:

```python
class ASGRightsizer:
    list_recommendations(...)
    get_recommendation(inventory_id, *, min_monthly_savings=0.0)
    get_confidence_trend(inventory_id, adapter=None, end_date=None)
```

The constructor accepts injected pricing catalog, capacity policy, and scope
policy. Normal recommendation methods must never construct an AWS adapter.

### 5.2 Evaluation order

For one inventory row:

1. Build the response identity and policy evidence.
2. Apply scope gates and return DEFERRED immediately if one matches.
3. Resolve regional/platform instance pricing and global instance specs.
4. Validate required CPU/in-service telemetry, observed days, and pairing.
5. Apply desired-history and memory coverage independently: missing desired
   history caps normal output at CONDITIONAL; absent or insufficiently paired
   memory calculates only the CPU preview.
6. Calculate all three tier configurations from persisted required-capacity
   summaries.
7. Apply availability floor and no-increase bounds.
8. Remove options without desired reduction or eligible savings.
9. Add operational warnings and classify each unique configuration.
10. Deduplicate configurations, assemble tier references, and return.

Do not recompute raw telemetry in the service. The persisted normalization is
the testable boundary between scan collection and recommendation evaluation.

The sufficiency branches are explicit:

- Missing CPU: `INSUFFICIENT_DATA / ASG_CPU_METRIC_UNAVAILABLE`.
- Missing in-service history: `INSUFFICIENT_DATA /
  ASG_IN_SERVICE_CAPACITY_METRIC_UNAVAILABLE` plus the required group-metrics
  remediation from spec Section 11.
- Zero current in-service members: `INSUFFICIENT_DATA /
  ASG_CURRENT_IN_SERVICE_CAPACITY_ZERO`, even when historical series are usable.
- CPU/in-service pairing below policy: `INSUFFICIENT_DATA /
  ASG_CPU_CAPACITY_PAIRING_INSUFFICIENT`.
- Fewer than seven observed days: `INSUFFICIENT_DATA /
  ASG_TELEMETRY_WINDOW_TOO_SHORT` plus observed/required-day copy.
- Missing desired history with current desired inventory: continue with
  `ASG_DESIRED_CAPACITY_HISTORY_UNAVAILABLE`, the group-metrics remediation from
  spec Section 11, and a CONDITIONAL cap.
- Missing memory: preview blocker `MEMORY_METRIC_NOT_ENABLED`.
- Memory/in-service pairing below policy: preview blocker
  `ASG_MEMORY_CAPACITY_PAIRING_INSUFFICIENT`.
- Usable memory with fewer than seven observed days: preview blocker
  `ASG_MEMORY_TELEMETRY_WINDOW_TOO_SHORT`.

### 5.3 Capacity calculation

Implement the equations and nearest-rank rules from spec Section 7 exactly.
Use integer counts after `ceil`. Take maximum p50/p99 requirements across valid
windows. Record all source window values in evidence.

Apply bounds in this order:

```text
availability floor
calculated minimum
calculated desired >= calculated minimum
no increase above current min/desired
target desired >= target min
max unchanged
```

The no-increase bounds are clamps, not tier rejection gates:

```text
target_min = min(current_min, calculated_min)
target_desired = min(current_desired, calculated_desired)
target_desired = max(target_min, target_desired)
```

A calculated minimum above the current minimum can therefore still produce a
valid desired-capacity reduction. A calculated desired at or above the current
desired produces no downsize.

If current capacity is below calculated need, do not return an increase from a
downsize endpoint.

Before applying no-increase bounds, reject downsize output when current min,
desired, or max is below the availability floor. When zones are unavailable,
use floor one, attach the specified warning, and cap normal options at
CONDITIONAL.

### 5.4 Tier assembly

Key unique options by `(target_min, target_desired, current_max)`. Preserve tier
nesting and attach all satisfied labels. Balanced is default only when present;
Aggressive-only results have a null default.

Set the top-level classification using spec Section 16: Balanced when present,
otherwise Aggressive, PREVIEW for preview-only output, explicit scope/data
classification for those paths, and null for a valid no-reduction result.

Sort unique recommendations by:

```text
monthly_savings descending
target_desired ascending
target_min ascending
```

### 5.5 Pricing

Use `EC2InstanceCatalog` for the resolved instance type, region, and platform.
The instance specification remains global/us-east-1-sourced; the price remains
regional. Use Decimal-from-string for both the one-cent floor and request
minimum comparison. Preserve existing display rounding.

### 5.6 Classification and preview

Implement the matrix in spec Sections 12–14:

- Complete supported CPU-driven groups may be ACTIONABLE.
- Review signals cap otherwise valid configurations at CONDITIONAL.
- Missing memory produces one default-on PREVIEW object with CPU-only tier
  configurations.
- The preview uses `kind`, `classification`, `blockers`, `message`, `options`,
  and `evidence`; `options` contains embedded Conservative/Balanced/Aggressive
  configurations plus `default`, and there is at most one preview of this kind.
- Missing required CPU/in-service data or short windows produce
  INSUFFICIENT_DATA.
- Scope failures produce DEFERRED.

A preview never enters normal recommendations or tiers and never alters normal
classification tallies.

Return the complete spec Section 16 envelope: `state`,
`current_monthly_cost`, `pricing_source`, `current_capacity_evidence`, concrete
`telemetry_summary`, and `pricing_evidence` are present on every path. Top-level
`classification` is an intentional ASG extension and is always present; never
emit OPPORTUNITY. Keep the ASG policy name `capacity_policy` rather than
aliasing EC2's `compute_policy`.

Risk output retains the shared EC2 keys. Set network, storage, and migration to
null, add the ASG `operations` component, and calculate overall from non-null
components only.

## 6. V1 baseline record: API and trend

### 6.1 Routes

Add the three routes from spec Section 19 to the existing recommendations
router. Follow EC2 error behavior: 404 for missing inventory and 502 only for
on-demand AWS trend failures.

List filters are account, region, state, classification, and minimum savings.
Normalize classification case-insensitively and reject values outside the ASG
top-level vocabulary with HTTP 422. Serialize persisted inventory timestamps as
explicit UTC `Z` values so SQLite timezone loss cannot make the UI interpret
them as local time.
Do not expose arbitrary tier-ratio query parameters in V1.

### 6.2 Trend

Implement an ASG trend cache isolated from EC2 with schema `asg-trend-v1`.
Reuse EC2 bucket semantics and query helpers, parameterized by group dimension
and persisted memory source. Request only CPU and optional memory. Include the
memory source identity in the cache key so source changes cannot reuse stale
data.

This source-aware key is an intentional improvement over the current EC2 cache,
not byte-for-byte reuse. Keep it covered so a later EC2 backport can adopt it
without weakening ASG isolation.

## 7. V1 baseline record: tests and verification

Create explicitly named files:

```text
tests/test_asg_rightsizer.py
tests/test_asg_rightsizer_scenarios.py
tests/test_asg_metric_collection.py
tests/test_asg_memory_metric_discovery.py
```

Implement every scenario and invariant in `asg_rightsizer_test_plan.md`. Reuse
the packaged EC2 catalog for real instance pricing/spec fixtures and guard for a
missing extracted `maxops_pricing.db` before SQLite can create an empty file.

Run only:

```bash
.venv/bin/python -m pytest tests/test_asg_rightsizer.py -q
.venv/bin/python -m pytest tests/test_asg_rightsizer_scenarios.py -q
.venv/bin/python -m pytest tests/test_asg_metric_collection.py -q
.venv/bin/python -m pytest tests/test_asg_memory_metric_discovery.py -q
.venv/bin/python -m pytest tests/test_ec2_rightsizer_v1.py tests/test_ec2_memory_metric_discovery.py -q
```

Never run `pytest tests/`, `pytest -k asg`, repository-wide coverage, or broad
discovery.

## 8. V1 baseline record: rollout and acceptance

Deployment order:

1. Add schema/model and importer compatibility.
2. Deploy read-only discovery and metric persistence.
3. Observe coverage without exposing recommendations.
4. Enable list/detail endpoints.
5. Enable on-demand trend.

Track:

```text
groups discovered
scope-deferral reasons
CPU/desired/in-service/memory coverage
pairing coverage
ACTIONABLE/CONDITIONAL/PREVIEW/INSUFFICIENT_DATA counts
estimated savings totals by classification
```

Done means:

- All spec Section 22 invariants pass.
- The collector requests only the four allowed metric series.
- A normal recommendation has exact min/desired values and unchanged max.
- Missing memory cannot create a normal downsize.
- Missing/insufficient pairing follows the exact CPU and memory branches and
  reason codes in spec Section 11.
- Every envelope includes the pinned telemetry, pricing, current-cost, state,
  current-capacity, policy, and risk contracts.
- Unsupported groups return before pricing/telemetry evaluation.
- EC2 rightsizer outputs and named regression tests remain unchanged.
- No recommendation or list call performs an AWS request.

## 9. V2 combined instance and capacity optimization

### 9.1 Shared EC2 helper extraction

Move product-neutral logic out of `app/services/ec2_rightsizer.py` into a small
shared module consumed by EC2 and ASG. Reuse the existing
`EC2InstanceCatalog`; do not create an ASG catalog or duplicate its SQL.

The shared surface owns:

```text
EC2CandidatePolicy and gated-family evidence
instance family/class parsing
architecture overlap result and reason
changed-target .metal exclusion
CoreMark-or-vCPU comparable capacity basis
same-architecture performance ratio/change
Decimal-from-string savings comparison
deterministic target-type ordering
Balanced-reserving candidate limiting
Graviton raw-capacity eligibility primitives
```

Keep group count math, ASG operational warnings, and ASG response shaping out of
the shared module. Keep standalone network/EBS evaluation and EC2 inventory
logic in EC2. `ASGRightsizer` must not instantiate `EC2Rightsizer` or evaluate
members one by one.

After each extraction step, run the explicitly named EC2 regression files. The
pre-extraction EC2 decision projection must be byte-for-byte equal for metrics-
present, missing-memory, missing-CPU, tier-limit, family-gate, architecture,
network, EBS, and preview fixtures.

### 9.2 Policies and compatibility switch

Extend `ASGCapacityPolicy` with:

```python
candidate_limit: int = 10
instance_optimization_enabled: bool = True
policy_version: str = "asg-v2-combined"
```

Require a positive candidate limit. Inject the shared `EC2CandidatePolicy`
into `ASGRightsizer` and expose its public gated-family evidence separately as
`candidate_policy`. Preview switches remain internal, matching EC2.

Name the deployment switch `ASG_INSTANCE_OPTIMIZATION_ENABLED`; its default is
false during rollout. Effective enablement is:

```python
effective_instance_optimization = (
    settings.ASG_INSTANCE_OPTIMIZATION_ENABLED
    and capacity_policy.instance_optimization_enabled
    and telemetry_schema_is_v2
    and coremark_catalog_gate_passes
)
```

Deployment-off always wins. No request parameter or policy object can override
it. Flag-off execution uses the existing V1 code path before candidate
enumeration and produces the exact V1 recommendation, tier, preview, reason,
and classification fields. Additive V2 response fields must not leak into that
projection.

### 9.3 Versioned target-independent telemetry

The V1 persisted `required_capacity` summaries are tied to the current instance
shape and cannot safely size a different target. Extend normalization to persist
exactly the spec Section 6 object—do not introduce top-level `demand_points` or
rename `points`:

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

Each value is derived from its own exact utilization/in-service timestamp
intersection. The point set is their union when memory is usable. Missing
dimensions stay null. Do not duplicate the same point array inside 14/30/60-day
windows.

At recommendation time, load the current global specification once and convert
the persisted series once:

```python
cpu_used_capacity_t = cpu_used_instance_equivalents_t * current_cpu_units
memory_used_mib_t = memory_used_instance_equivalents_t * current_memory_mib
```

Reuse the resulting immutable arrays for every target and tier. Required counts
still evaluate the per-timestamp CPU/memory maximum before nearest-rank p50/p99;
percentiles cannot be scaled or combined independently.

Rows with only `asg-v1-exact` telemetry continue through the flag-off
capacity-only path. They must not receive type candidates. During shadow rollout
record `ASG_INSTANCE_OPTIMIZATION_TELEMETRY_UPGRADE_REQUIRED` in coverage logs,
not as a change to the V1 response. Enable V2 output only after the row has the
V2 schema.

### 9.4 Candidate generation

For each supported homogeneous group:

1. Add the unchanged current type so capacity-only output competes normally.
2. Enumerate global specifications with regional/platform prices.
3. Exclude changed `.metal` targets.
4. Apply the shared architecture and gated-family decisions.
5. Establish one comparable CPU basis; never mix CoreMark and vCPU.
6. Calculate Conservative/Balanced/Aggressive counts for the target.
7. Apply availability, current-count, maximum, and missing-memory floors.
8. Apply exact total-configuration savings.
9. Add operational and type-change classification evidence.

Do not require a cheaper unit price. Do require:

```python
current_cost = current_desired * current_price
target_cost = target_desired * target_price
eligible = current_cost - target_cost >= Decimal("0.01")
```

Apply `min_monthly_savings` to the same unrounded Decimal result. The target may
be larger or costlier per unit only when its complete safe configuration is
cheaper. Iterate targets in deterministic lexical order and maintain rejection
counts independently of `candidate_limit`.

### 9.5 Per-target capacity calculation

For a target, select CoreMark only when both sides have a positive value and
architecture overlaps; otherwise select vCPU only when both sides have a
positive value. Reject `CPU_CAPABILITY_UNKNOWN` when neither basis exists.

For every tier ratio and demand point calculate CPU and usable-memory required
counts, take their per-timestamp maximum, then derive nearest-rank p50/p99 for
each lookback. Use the longest lookback as deterministic evidence when equal
requirements tie. Apply the current V1 minimum/desired equations, including:

- minimum overflow clamps to current minimum and may survive;
- raw desired overflow rejects that target/tier with
  `TARGET_REQUIRES_CAPACITY_INCREASE`;
- no target increases min or desired;
- numeric max remains unchanged.

If the unchanged current candidate overflows desired and no alternative target
survives, return top-level `CURRENT_CAPACITY_NOT_OVERPROVISIONED`. If an
alternative survives, keep only the target rejection tally and do not emit that
top-level no-option reason.

Derive `CAPACITY_ONLY`, `INSTANCE_ONLY`, or `COMBINED` from the final type and
desired count. Do not create separate scoring paths for the three labels.

### 9.6 Missing memory and previews

When memory is absent, insufficiently paired, or too young, evaluate both
aggregate retention floors before allowing a normal configuration:

```python
target_min * target_memory_mib >= current_min * current_memory_mib
target_desired * target_memory_mib >= current_desired * current_memory_mib
```

Unknown current/target memory fails closed. A passing configuration excludes
the fallback dimension from projected utilization, attaches
`MEMORY_METRIC_UNAVAILABLE_CURRENT_CAPACITY_RETAINED`, and is capped at
CONDITIONAL. A candidate failing only memory enters the preview pool and keeps
its ordinary `MEMORY_REQUIREMENT_NOT_MET` rejection tally.

Build at most one highest-total-savings memory preview, with one target type and
its embedded tier configurations. Build at most one Graviton OPPORTUNITY using
shared architecture/family gates and raw aggregate vCPU/memory retention at
both min and desired. Null all cross-architecture CoreMark evidence. Neither
preview enters recommendations, tiers, limiting, or normal classification.

### 9.7 Tiering, ranking, and classification

Deduplicate by the complete key:

```text
(target_instance_type, target_min_size, target_desired_capacity, target_max_size)
```

For each tier, select greatest exact savings, then lower target cost, desired,
minimum, and lexical instance type. Apply the shared Balanced-reserving limiter
to the savings-ranked list and select tier references only from returned
recommendations. Every tier reference must resolve by the complete key.

Capacity-only configurations retain V1 classification. Every normal
type-changing configuration is capped at CONDITIONAL with
`INSTANCE_TYPE_CHANGE_NETWORK_STORAGE_VALIDATION_REQUIRED`; network/storage
risk stays null because no group I/O workload series is collected. Catalog
capabilities are disclosure only. OPPORTUNITY appears only in a Graviton
preview.

### 9.8 Response and API delta

Add the spec Section 16 V2 fields without renaming existing ASG envelope fields:

```text
target_instance_type
optimization_kind
family_changed and architecture evidence
CPU capacity basis and performance evidence
current/target unit prices and target total cost
aggregate min/desired/max capacity evidence
candidate_policy
rejection_summary
availability_note
```

Tier references include type plus counts. List/detail accept a validated
positive `candidate_limit`; tier ratios remain service policy. Trend behavior,
cache schema, collection permissions, inventory identity, scope gates, and the
four-series metric allowlist do not change.

The mock/UI data contract must eventually display the target type and
optimization kind and must not claim ASG is capacity-only. Frontend work remains
outside this backend implementation change.

### 9.9 V2 tests and safe execution

Add only these new files:

```text
tests/test_asg_rightsizer_v2.py
tests/test_asg_rightsizer_v2_properties.py
```

Extend the existing Markdown manifest to map every V2 scenario ID before the
feature flag is enabled. Use real packaged catalog specifications for current
and target types; override prices only in explicitly labeled boundary fixtures.

Run the four V1 ASG files, the two V2 files, and the named EC2 regression files
individually. Never use broad or keyword discovery.

### 9.10 V2 rollout and done signal

1. Land pure-helper extraction with EC2 and V1 ASG parity.
2. Land V2 telemetry persistence while serving V1 output.
3. Backfill/recollect active supported ASGs.
4. Measure CoreMark catalog coverage and keep the deployment switch off unless
   the gate below passes.
5. Shadow V2 and compare the unchanged-current candidate to V1 byte-for-byte.
6. Set `ASG_INSTANCE_OPTIMIZATION_ENABLED=true` only for rows with the new
   normalization version.
7. Monitor candidate rejection, optimization kind, catalog coverage, and
   conditional-validation reason counts.

Define CoreMark catalog coverage as:

```python
eligible = regional_platform_priced_nonmetal_types_with_valid_vcpu
covered = [entry for entry in eligible if entry.coremark is finite and entry.coremark > 0]
coremark_coverage = len(covered) / len(eligible)
```

Deduplicate global specifications by instance type before counting, but require
that each denominator type has a price in at least one enabled region/platform
cohort. Architecture-incompatible and gated-family decisions do not remove a
type from this packaging-quality denominator. The release/startup gate is
`coremark_coverage >= 0.70`; equality passes. If the denominator is empty or
coverage is below 70%, `ASG_INSTANCE_OPTIMIZATION_ENABLED` remains effectively
false even when configured true, startup emits a visible coverage error, and
ASG continues serving V1. Record numerator, denominator, ratio, catalog schema,
and generation timestamp in release metadata and rollout telemetry. vCPU
fallback still handles individual uncovered candidates after the catalog-level
gate passes.

V2 is done only when all 28 spec invariants, every V1 scenario ID, every V2
scenario ID, the manifest, and both EC2 parity files pass; flag-off behavior is
unchanged; no new AWS metric or permission exists; and all type-changing output
is CONDITIONAL or preview-only as specified.
