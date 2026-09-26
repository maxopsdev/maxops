# ElastiCache Rightsizer — Implementation Plan (V1: node-type change + replica reduction)

Executor: coding agent. This plan implements
`rightsizers/elasticache/elasticache_rightsizer_spec.md` in full (greenfield —
there is no existing ElastiCache rightsizer). It deliberately mirrors the EC2
rightsizer's architecture (`app/services/ec2_rightsizer.py` +
`rightsizers/ec2/ec2_rightsizer/`) so shared concepts stay byte-compatible in
behavior; where a pure function already exists in the EC2 package, **import or
extract it — do not copy it**.

Run `python -m pytest tests/test_elasticache_rightsizer_v1.py -q` (created in
Workstream H) after each workstream; existing EC2 suites
(`tests/test_ec2_rightsizer_v1.py`, `tests/test_ec2_rightsizer_scenarios.py`)
must stay green whenever a shared module is extracted.

> **Do not run broad/keyword pytest** (`pytest tests/ -k ...`). Files under
> `tests/integration/` provision real billable AWS resources. Run the named unit
> files only. The `-m "not integration"` default in `pytest.ini` is a safety
> gate — never remove it.

## Goal

Produce spec-compliant recommendations for Redis/Valkey replication groups:
tiered node-type changes (Conservative/Balanced/Aggressive) with Graviton as
first-class candidates, plus an independent replica-count-reduction
recommendation — driven by a 60-day per-node decision scan, honest network
banding reused from EC2, the eviction memory floor, and replica safety floors.

## Context (current code, verified 2026-07-14)

- **Pricing/catalog data already ships.** The packaged
  `pricing_artifacts/maxops_pricing.db.xz` contains `street_pricing_elasticache`
  (2,895 rows: `region_code`, `instance_type`, `cache_engine`/`engine_normalized`
  ∈ {redis, valkey, memcached}, `hourly_usd`, `monthly_usd`, `attributes_json`,
  `pricing_json`). Crucially, `attributes_json` includes **`vcpu`**, **`memory`**
  (GiB), **`max_clients`**, **`networkPerformance`** (qualitative string), and
  per-engine **`maxmemory` in bytes** (e.g. `redis6.x-maxmemory`). No new AWS
  enrichment job is required for the cache catalog.
- The same DB contains the global `ec2_instance_specs` table (us-east-1
  `DescribeInstanceTypes`), which provides numeric
  `NetworkInfo.NetworkCards[].BaselineBandwidthInGbps` / `PeakBandwidthInGbps`
  for the EC2 equivalents of cache node types (`cache.m6g.large → m6g.large`).
- `StreetPricingService._lookup_elasticache` (`app/services/street_pricing.py`)
  already resolves per-engine prices; `_elasticache_engine_candidates`
  normalizes engine aliases. `app/pricing/pricing_elasticache.py` has node-type
  parsing helpers (`_parse_elasticache_node_type`, `_family_base`,
  `_family_generation`, `_find_nearest_graviton_node_type`) — reuse the parsing,
  ignore the nearest-Graviton heuristic (candidate generation supersedes it).
- **Telemetry gap:** `AWSAdapter._get_elasticache_utilization`
  (`app/adapters/aws/adapter.py` ~L2854) collects only hourly-average
  `CurrItems`/`KeyCount` via `get_metric_statistics`. There is no ElastiCache
  equivalent of `get_ec2_rightsizing_metrics` (60-day 5-min `GetMetricData`) or
  of the confidence trend. Both must be built (Workstreams C, F).
- Inventory: `ElasticacheInventory` (`app/models/inventory.py` L141) has
  `engine`, `engine_version`, `cache_node_type`, `metric_history_json`,
  `metadata_json`. `AWSAdapter._get_elasticache_resources` (~L1054) fetches both
  `elasticache_replication_group` and `elasticache_cluster`, and `scan_service`
  persists both under the canonical `elasticache` type — today a 3-node group
  produces **four** inventory rows. C0 must exclude member clusters (rows whose
  `ReplicationGroupId` is set) so exactly one unit per group persists; the
  engine additionally defers legacy member rows
  (`MEMBER_OF_REPLICATION_GROUP`, spec §1/§2). Topology fields required by
  spec §1 (`num_node_groups`, `member_cluster_ids`, per-member roles and shard
  membership, `automatic_failover`, `multi_az`, `cluster_mode_enabled`, Global
  Datastore and auto-scaling markers, `data_tiering_enabled`, allowed
  node-type modifications, reserved-memory parameters) must be persisted into
  `metadata_json` (Workstream C0).
- EC2 rightsizer surfaces to mirror/reuse: `EC2ComputePolicy` (tier ratios),
  `_limit_candidates_preserving_balanced`, `_recommendation_tiers`,
  `_binding_dimension`, `classify_recommendation`, the network
  warnings/constraints modules under `rightsizers/ec2/ec2_rightsizer/`
  (`warnings/network_warnings.py`, `constraints/network_hard_constraints.py`),
  `assumed_network_baseline_mbps`, and the trend cache pattern
  (`app/services/ec2_trend_cache.py`).
- **Engine-version node support is NOT in the pricing attributes.** Spec §3.1's
  primary source is `elasticache:ListAllowedNodeTypeModifications`, collected
  per group at scan time (C0) — per-group runtime truth from AWS. A static
  fallback table in code (Workstream A3; three-part semantic versions,
  size-aware keys) covers missing API results, with conservative unknown
  handling (`ENGINE_VERSION_SUPPORT_UNKNOWN`).

---

## Workstream A — Cache node catalog

New: `app/services/elasticache_node_catalog.py`.

**A1. `ElastiCacheCatalogEntry`** (frozen dataclass), populated from
`street_pricing_elasticache` + `ec2_instance_specs`:

```python
node_type: str                    # cache.m6g.large
engine: str                       # redis | valkey (normalized)
region: str
hourly_usd / monthly_usd: float
vcpus: int | None                 # attributes_json["vcpu"]
memory_gib: float | None          # attributes_json["memory"]
maxmemory_bytes: int | None       # max over redis*-maxmemory keys (A2)
max_clients: int | None           # attributes_json["max_clients"]
family: str                       # m6g   (exact family+generation token)
family_class: str                 # m     (bare letter; gates)
architecture: str                 # arm64 | x86_64, derived from family token
data_tiering: bool                # family endswith "gd"
burstable: bool                   # family_class == "t"
network_baseline_mbps: float | None       # from EC2 equivalent (A4)
network_reliable_max_mbps: float | None
network_capacity_kind: str        # BASELINE | BURST_OR_UP_TO | UNKNOWN
network_performance_label: str | None     # qualitative string, evidence only
capability_source: str            # "street_pricing_attributes"
```

**A2. `maxmemory_bytes` resolution.** `attributes_json` carries versioned keys
(`redis6.x-maxmemory`, older `redis4.x-…`, etc.). Take the **max across all
`redis*-maxmemory` keys** (they agree in practice; max is the stable choice) for
Redis and Valkey alike — Valkey rows carry the same Redis-derived attribute set
(verified in the packaged rows). When absent, fall back to
`memory_gib * (1 GiB)` and mark `maxmemory_source = "memory_attribute"` so the
usable-memory computation (spec §6.1) can disclose the weaker source. Never fall
back silently.

**A3. Engine-version support (fallback only).** The primary compatibility
source is the per-group `allowed_scale_up_types` / `allowed_scale_down_types`
persisted by C0 from `ListAllowedNodeTypeModifications` — when present, a
candidate absent from both sets is `ENGINE_VERSION_INCOMPATIBLE` and the
static table is not consulted. The fallback table uses **three-part semantic
versions** (AWS floors like Redis 5.0.6 are not expressible as major.minor)
keyed by family and, where AWS differentiates, size:

```python
MIN_ENGINE_VERSION_BY_NODE: dict[str, tuple[int, int, int]] = {
    # Minimum Redis OSS version, from the AWS supported-node-types table:
    # docs.aws.amazon.com/AmazonElastiCache/latest/dg/CacheNodes.SupportedTypes.html
    # Retrieved 2026-07-15. Versioned policy — keep the URL + retrieval date
    # in the module docstring and re-verify when AWS adds families.
    "m7g": (6, 2, 0), "r7g": (6, 2, 0), "c7gn": (6, 2, 0),
    "m6g": (5, 0, 6), "r6g": (5, 0, 6),
    "r6gd": (6, 2, 0),
    "t4g.micro": (3, 2, 4), "t4g.small": (5, 0, 6), "t4g.medium": (5, 0, 6),
    "m5": (3, 2, 4), "m4": (3, 2, 4), "r5": (3, 2, 4), "r4": (3, 2, 4),
    "t3": (3, 2, 4), "t2": (3, 2, 4),
}
def engine_supports_node_type(engine: str, engine_version: str, node_type: str) -> bool | None:
    # lookup: exact "family.size" key first, then bare family key
    ...  # None when no key matches OR the version is unparseable
```

Return `False` → `ENGINE_VERSION_INCOMPATIBLE` (hard reject); `None` →
skip + tally `ENGINE_VERSION_SUPPORT_UNKNOWN` (spec §3.1). **Valkey:** every
floor in the table is ≤ 6.2 and Valkey ships from 7.2, so any parseable
Valkey version clears every listed family — a Valkey group is supported on
any family present in the table and `None` (unknown) on any family absent
from it; no separate Valkey floors are encoded. Representative tests: the
6.2 floors (m7g/r7g/c7gn/r6gd), the 5.0.5-vs-5.0.6 three-part boundary, the
t4g.micro-vs-t4g.small size split, and Valkey-on-m7g.

**A3b. Burstable credit capacity table** — module-level versioned policy,
from the same AWS supported-node-types page (Burstable Performance
Instances table, retrieved 2026-07-15):

```python
MAX_ACCRUED_CPU_CREDITS: dict[str, int] = {
    "t4g.micro": 288, "t4g.small": 576, "t4g.medium": 576,
    "t3.micro": 288, "t3.small": 576, "t3.medium": 576,
    "t2.micro": 144, "t2.small": 288, "t2.medium": 576,
}
```

The spec §8 watermark is `cpu_credit_balance_low_watermark ×
MAX_ACCRUED_CPU_CREDITS[size]`. `CPUCreditBalance`/`CPUCreditUsage` are
published **only for T3/T4g, never for T2** (AWS host-level metrics doc) —
a burstable current with missing credit telemetry gets
`CPU_CREDIT_TELEMETRY_UNAVAILABLE` and burstable targets cap at CONDITIONAL
(spec §8); it never silently produces an unqualified burstable
recommendation.

**A4. EC2 network mapping.** `ec2_equivalent(node_type)` strips the `cache.`
prefix and looks up `ec2_instance_specs` (reuse `EC2InstanceCatalog._load_specs`
— extract it into a shared helper rather than re-implementing the JSON walk).
Sum baseline/peak across `NetworkCards`, convert Gbps → Mbps. Missing
equivalent → `network_capacity_kind = UNKNOWN` (spec §9). Keep the qualitative
`networkPerformance` string as evidence only — never parse Mbps out of it.

**A5. `ElastiCacheNodeCatalog`** — mirrors `EC2InstanceCatalog`: `__init__`
resolves the packaged DB path, `list_region(region, engine)` returns
`dict[node_type, entry]` for Redis/Valkey rows priced in that region, `get()`
for a single entry. Same missing-DB assertion + unpack-command error message as
the EC2 catalog fixture demands.

---

## Workstream B — Rightsizer package `rightsizers/elasticache/elasticache_rightsizer/`

Mirror the EC2 package layout (spec §16). Modules and their EC2 reuse:

**B1. `models.py`** — `RiskLevel`, `HardConstraintStatus`, `ResourceEvaluation`,
`CapacityKind`: **import from the EC2 package** (move to a shared
`rightsizers/common/` if cross-package import is awkward; do it as a mechanical
extraction commit that keeps EC2 tests green). New here:
`ElastiCachePerformanceWarningPolicy` (spec §13 fields + `__post_init__`
validation: ratios in (0,1], coverage ratio in (0,1], observed-day and
byte/second/sample-age thresholds ≥ 0, trend primary context limit is an
integer ≥ 0, multiplier > 0),
`RiskAssessment` with the `cache_health` dimension (spec §10).

**B2. `normalization/`** — pure, threshold-free:
- `network.py`: re-export/wrap the EC2 bytes→Mbps conversion (same math, spec §4.3).
- `cpu.py`, `memory.py`: percentage gauges — percentile helpers only.
- `health.py`: Sum→rate conversion for `Evictions`, `GetTypeCmds`, `SetTypeCmds`;
  gauges pass through. Missing samples never become zero.

**B3. `requirements/compute.py`** (spec §5):
```python
projected_engine_cpu(engine_cpu_p99) -> identity
projected_host_cpu(host_cpu_p99, current_vcpus, target_vcpus)
small_target_gate(target_vcpus, policy)   # <=2 vCPU tightening, §5.2
```

**B4. `requirements/memory.py`** (spec §6): `usable_memory_bytes(entry,
reserved_memory_percent)`, `projected_memory_util(bytes_used_p99, target_usable)`.

**B5. `constraints/`**:
- `engine_compatibility.py` → wraps A3.
- `memory_hard_constraints.py` → `MEMORY_REQUIREMENT_NOT_MET` (p99 used bytes ≥
  target usable, equality rejects — conservative like the EC2 network peak) and
  the eviction floor `MEMORY_PRESSURE_EVICTIONS_PRESENT` (any evictions +
  memory-reducing target, §6.3).
- `network_hard_constraints.py` → **reuse the EC2 evaluator** with a
  `NOT_APPLICABLE` ENI/EFA path (cache nodes expose none of those inputs); the
  reliable-max and allowance-event rules are identical.
- `replica_floors.py` → spec §11.2 (`REPLICA_FLOOR_AUTOMATIC_FAILOVER`,
  `REPLICA_FLOOR_LAST_REPLICA`, per-shard floors, `UNEVEN_REPLICA_LAYOUT`).

**B6. `warnings/`**:
- `network_warnings.py` → reuse the EC2 evaluator (three bands, assumed
  baseline, `NETWORK_SUSTAINED_ABOVE_BASELINE`, allowance warnings). The
  bandwidth-weighting input is always default for cache nodes.
- `cache_health_warnings.py` → spec §8: `EVICTIONS_PRESENT`,
  `SWAP_USAGE_REVIEW_REQUIRED`, `REPLICATION_LAG_REVIEW_REQUIRED`,
  `HIGH_CONNECTION_COUNT_REVIEW_REQUIRED`, plus
  `ENGINE_CPU_METRIC_UNAVAILABLE` (spec §5.2).
- `messages.py` → the §14 texts verbatim, including the online-vertical-scaling
  operational note attached to every node-type recommendation.

**B7. `risk/risk_assembly.py`** — assemble the §10 `RiskAssessment`;
classification consumes only the decision-table columns (network + memory/health
evaluations + blocking warnings), disclosure dimensions assembled after — same
discipline as EC2 §11.

**B8. `selection/`**:
- `node_type_classification.py` → reuse `classify_recommendation` (the table is
  isomorphic: storage ↦ memory/health evaluation) and the tier machinery:
  extract `_recommendation_tiers`, `_tier_option`,
  `_limit_candidates_preserving_balanced`, `_binding_dimension` from
  `app/services/ec2_rightsizer.py` into a shared module parameterized by the
  projected-util inputs, so EC2 keeps its behavior and ElastiCache supplies
  `{cpu, memory, network}` (no storage, no coremark fields).
- `replica_reduction.py` → Workstream E.

---

## Workstream C — Telemetry collection and persistence

**C0. Inventory identity + topology fields (spec §1, §2).**

- **One row per group, enforced end to end.** A single shared predicate
  (`is_member_cluster(resource)` — cluster-type payload with
  `ReplicationGroupId` set) is applied at **every** path that writes
  `elasticache` inventory, not just initial collection:
  1. `_get_elasticache_resources` drops member clusters from the
     `elasticache_cluster` result (standalone clusters persist normally);
  2. the check/finding phase — `_store_inventory_resource` at
     `scan_service.py` ~L1296 re-persists every resource a check returns,
     and the ElastiCache checks (`app/checks/elasticache/low_items_count.py`,
     `non_graviton.py`) fetch groups **and** clusters directly from the
     adapter, so without this the member rows return on the first scan with
     active checks. `non_graviton.py` evaluates group rows and standalone
     clusters directly. `low_items_count.py` must continue fetching member
     clusters because its metrics are node-scoped: query each member by
     `CacheClusterId`, form a canonical group series by summing the maximum
     member value per shard at each timestamp (replicas duplicate a shard's
     keyspace), discard timestamps missing an entire shard, and return the
     replication-group payload so the finding attaches to the group row.
     Neither check returns a member-cluster payload. Make
     `_store_inventory_resource` refuse member-cluster payloads as a final
     backstop;
  3. the engine defers any legacy member row already in the DB with
     `MEMBER_OF_REPLICATION_GROUP` (D2).
  A full-scan regression with active ElastiCache checks (test plan MOD-106)
  asserts the end state: one row per group plus standalone clusters only.
- Extend the replication-group payload → `ElasticacheInventory.metadata_json`:
  `cluster_mode_enabled`, `automatic_failover`, `multi_az`, `num_node_groups`,
  `replicas_per_node_group` (per-shard list), `member_cluster_ids`,
  `member_roles` (per member: `primary`/`replica`/`unknown` + node-group id,
  from `NodeGroups[].NodeGroupMembers[].CurrentRole` where the API provides
  it; cluster-mode-enabled groups report no roles — persist `unknown`, with
  authoritative resolution from the `IsMaster` metric at enrichment time
  (C2) per spec §11.1), `member_created_at` per member (from the member cache
  cluster creation timestamp, used by the replica coverage denominator),
  `global_datastore_member` (from
  `GlobalReplicationGroupInfo`),
  `data_tiering_enabled`, `snapshot_retention_limit`, `status`.
- `auto_scaling_attached`: one
  `application-autoscaling:DescribeScalableTargets` call per region for the
  `elasticache` namespace. Normalize AWS target `ResourceId` values before
  joining: `replication-group/<id>` maps to the replication-group inventory
  id and `cache-cluster/<id>` maps to a standalone cluster. Treat any matching
  supported dimension (`elasticache:replication-group:NodeGroups`,
  `elasticache:replication-group:Replicas`, or
  `elasticache:cache-cluster:Nodes`) as attached. Set `False` only after every
  page was read successfully; use `None` on AccessDenied or any incomplete
  call, never a partial negative.
  Only a confirmed `True` defers; `None` proceeds with
  `AUTO_SCALING_STATE_UNKNOWN` and a CONDITIONAL cap (spec §1) — never
  silently, never ACTIONABLE.
- `allowed_scale_up_types` / `allowed_scale_down_types`: one
  `elasticache:ListAllowedNodeTypeModifications` call per group; `None` on
  error (engine falls back to the A3 table).
- Reserved memory: resolve the group's effective parameter group via
  `describe_cache_clusters` (`CacheParameterGroup`) +
  `elasticache:DescribeCacheParameters`; persist `reserved_memory_percent`
  **or** `reserved_memory_bytes` (mutually exclusive AWS forms — handle
  both). On AccessDenied/unresolved, persist null — the engine then applies
  spec §6.1's unknown-reservation rule (capacity retention + CONDITIONAL),
  not a silent 0.25.
- **IAM — both policies:** add `application-autoscaling:DescribeScalableTargets`,
  `elasticache:ListAllowedNodeTypeModifications`, and
  `elasticache:DescribeCacheParameters` (+
  `elasticache:DescribeCacheParameterGroups`) to **both** onboarding policy
  definitions in the same change: the frontend constant
  (`maxops-frontend/src/constants/iamReadOnlyPolicy.ts`) and the backend
  policy in `app/services/iam_onboarding_service.py` (~L143,
  `AnalyticsAndStorageRead` statement). Add a parity test asserting both
  policies contain every action the ElastiCache collection path calls
  (test plan MOD-110).
- **Pagination — mandatory on every collection call:**
  `DescribeScalableTargets` returns at most 50 results per page
  (`NextToken`); `DescribeCacheParameters` and the existing
  `describe_replication_groups` / `describe_cache_clusters` paginate via
  `Marker` (the current adapter reads only the first page of both — fix
  this while touching the function). Unpaginated collection silently
  misses groups, auto-scaling attachments, and parameters (test plan
  MOD-111).

**C1. `AWSAdapter.get_elasticache_rightsizing_metrics`** — mirror
`get_ec2_rightsizing_metrics` **exactly in statistics semantics**:
`GetMetricData`, period 300, 60-day window, **per member node**
(`CacheClusterId` dimension), for the spec §15.1 metric set (EngineCPU, host
CPU, `DatabaseMemoryUsagePercentage`, `BytesUsedForCache`, `FreeableMemory`,
`NetworkBytesIn/Out`, `Evictions`, `GetTypeCmds`, `SetTypeCmds`, `SwapUsage`,
`ReplicationLag`, `CurrConnections`, `IsMaster`, `TrafficManagementActive`,
`CPUCreditBalance`/`CPUCreditUsage` when the current type is T3/T4g (AWS
publishes neither for T2 — the missing series triggers spec §8's
`CPU_CREDIT_TELEMETRY_UNAVAILABLE`, never an error), and
the four network allowance metrics). **All series come back as raw
per-timestamp points** — gauges as `Average` per period, counters as `Sum`,
`TrafficManagementActive` as `Maximum`; **no server-side percentiles ever**
(spec §4: a p99 of five-minute p99s is a percentile of percentiles).
Percentiles are computed locally in C2. Chunk query batches to the 500-query
GetMetricData limit; per-node fan-out is accepted (spec §15.1).

**C2. `scan_service._enrich_elasticache_rightsizing_metrics`** — mirror the EC2
enrichment: normalize, compute per-node percentiles **locally over the 14-,
30-, and 60-day windows and take the max p99 per dimension** (EC2 parity,
spec §4), aggregate per spec §2 (hottest node per dimension, plus the
per-node read-ops/write-ops series retained for replica evidence and
empirical role resolution), persist a `rightsizing_metrics` block into
`metadata_json` with the same normalized-metric shape `_normalized_metric`
reads (`{p99, p95, max, avg, sample_count, period_seconds}` per metric), plus
`observed_days` inputs, allowance-event and traffic-management booleans, and
`per_node: {cache_cluster_id: {role, node_group_id, engine_cpu_p99,
read_ops_p99, read_ops_max, read_ops_sum, read_coverage_ratio,
read_observed_days, read_latest_sample_age_seconds, set_ops_p99, ...}}`.
`read_ops_sum` is calculated from raw
CloudWatch Sum points before rate conversion; missing points are never treated
as zero. Coverage uses the lesser of member age and 60 days as its denominator;
unknown member age is preserved as unknown, not assumed. Role resolution
happens here per spec
§11.1: `role` comes from the latest `IsMaster` samples, cross-checked
against C0's `CurrentRole` where present; missing/ambiguous/conflicting →
`unknown` (the engine then fails closed with `REPLICA_ROLES_UNRESOLVED`).
Timestamp-aligned group read-ops summation (spec §11.1) also happens here:
sum only at timestamps where every member has a sample; never zero-fill.

---

## Workstream D — Engine service `app/services/elasticache_rightsizer.py`

Mirror `EC2Rightsizer` end to end.

**D1. Policies.** `ElastiCacheComputePolicy` (tier_ratios identical to EC2's,
`engine_cpu_target_ratio=0.70`, `host_cpu_target_ratio=0.70`,
`memory_target_ratio=0.70`, `small_target_cpu_multiplier=0.85`,
`reserved_memory_percent_default=0.25`), `ElastiCacheScopePolicy`,
`ElastiCacheCandidatePolicy` (`gated_family_classes = {"t"}`,
`gated_data_tiering=True`, configurable).

**D2. Scope gate (spec §1)** — first thing in `_recommend`:
memcached → `UNSUPPORTED_ENGINE`; serverless (no `cache_node_type`) →
`SERVERLESS_CACHE`; legacy member-cluster row (`ReplicationGroupId` set on a
cluster-type row) → `MEMBER_OF_REPLICATION_GROUP`; `global_datastore_member`
→ `MANAGED_BY_GLOBAL_DATASTORE`; `auto_scaling_attached is True` →
`MANAGED_BY_AUTO_SCALING` (a `None` state proceeds with
`AUTO_SCALING_STATE_UNKNOWN` + CONDITIONAL cap); status not `available` →
`RESOURCE_NOT_AVAILABLE`. Same `_deferred_response` envelope as EC2.

**D3. Candidate generation (spec §3).** From
`catalog.list_region(region, engine)`: cheaper than current (per-group savings
≥ floors, reuse `_exact_savings`/`_savings_is_eligible`), **no architecture
filter** (Graviton first-class), engine-version gate (A3), family gates
(burstable + data-tiering both directions), compute/memory generation gate at
the **Aggressive** ratio.

**D4. Candidate evaluation.** Per candidate: §5 projections (engine CPU
identity, host CPU vCPU-ratio, ≤2-vCPU tightening), §6 usable-memory projection
(both reserved-memory forms; unresolved reservation → capacity retention +
`RESERVED_MEMORY_UNKNOWN` + CONDITIONAL cap) + eviction floor + the
traffic-management floor (`TRAFFIC_MANAGEMENT_ACTIVE` hard-rejects CPU- or
memory-reducing targets; others carry `TRAFFIC_MANAGEMENT_DETECTED`, HIGH
compute risk), network evaluation via the reused EC2 modules with mapped
baseline/peak (+ assumed baseline keyed on the **cache family's EC2-equivalent
exact family**), cache-health warnings (incl. the CPU-credit watermark when
the current type is burstable), risk assembly, classification,
`projected_util = max(engine_cpu, host_cpu, memory projections)`,
`binding_dimension` over `{cpu, memory, network}`. Evidence envelope mirrors
EC2 naming: every returned candidate has `kind: "NODE_TYPE_CHANGE"`,
`target_node_type`, `target_monthly_cost`, `monthly_savings`,
`yearly_savings`, projection fields, `classification`, `risk_assessment`,
`reason_codes`, `warning_details`, `compute_evidence`, `network_evaluation`,
`memory_evaluation`, `cache_health`, `constraint_coverage`, and
`required_review`.

**D5. Tiers + limiting** — via the shared selection module (B8): Balanced
reservation, savings ranking, dedup/collapse, `tiers.default = "balanced"`.

**D6. Response envelope — shared base + service extensions.** The base is
the **implemented** EC2 contract (`app/services/ec2_rightsizer.py` ~L1004),
using EC2's actual field names — `policy` (not `warning_policy`),
`capability_catalog` (not a new coinage), `deferred_reason_codes` (a list,
not a scalar `reason_code`):

```python
# ---- shared base (same names and shapes as EC2) ----
{
  "inventory_id", "resource_id", "resource_name",
  "account_id", "region", "state",
  "current_monthly_cost", "pricing_source",
  "policy": {...},                 # warning policy, asdict
  "compute_policy": {...}, "candidate_policy": {...}, "scope_policy": {...},
  "current_capacity_evidence": {...},
  "recommendations": [...],        # NODE_TYPE_CHANGE candidates, savings-ranked
  "tiers": {...},
  "rejection_summary": {...},
  "availability_note": "...", "availability_validated": False,
  "capability_catalog": {...},     # maxmemory source, network mapping kind (§6/§9)
  "telemetry_summary": {...},      # incl. observed_days, windows used
  # ---- ElastiCache extensions ----
  "replication_group_id", "engine", "engine_version",
  "current_node_type",             # the analog of EC2's current_instance_type
  "node_count", "cluster_mode_enabled", "num_node_groups",
  "replica_recommendation": {       # exact shape; None when not available
    "kind": "REPLICA_COUNT_REDUCTION",
    "classification": "ACTIONABLE" | "CONDITIONAL",
    "current_replicas_per_node_group", "target_replicas_per_node_group",
    "affected_node_group_count", "removed_node_count",
    "projected_survivor_engine_cpu",
    "monthly_savings", "yearly_savings",
    "risk_assessment": {...}, "reason_codes": [...],
    "redundancy_disclosure": "...",
    "member_evidence": [{
      "cache_cluster_id", "node_group_id", "role", "removed",
      "member_created_at",
      "read_ops_p99", "read_ops_max", "read_ops_sum",
      "read_coverage_ratio", "read_observed_days",
      "read_latest_sample_age_seconds",
    }],
  } | None,
  "operational_note": "...",       # spec §14 online-vertical-scaling text
}
```

Deliberate divergence, documented here: **no `savings_previews`** — the spec
(§10, test invariant 1) forbids previews in this product, so the key is
absent, not empty. Deferred responses mirror EC2's `_deferred_response`
(~L1331) field-for-field — `classification: "DEFERRED"`,
`deferred_reason_codes: [reason]`, empty `recommendations`/`tiers`, the
policy/capability/telemetry blocks — plus the ElastiCache extension fields.
This envelope **is** the UI/API contract; the UI spec consumes it, it does
not renegotiate it (MOD-109 guards it).

**D7. Savings** — `(current_monthly - target_monthly) * node_count`, decimal
eligibility + float display (reuse EC2 helpers).

---

## Workstream E — Replica-count reduction (spec §11)

`selection/replica_reduction.py`, invoked from `_recommend` after (and
independent of) node-type evaluation:

**E1.** Inputs: per-node `member_created_at`, `read_ops_p99`, `read_ops_max`, raw
`read_ops_sum`, `read_coverage_ratio`, `read_observed_days`,
`read_latest_sample_age_seconds`
(`GetTypeCmds`), `set_ops_p99`, `engine_cpu_p99`, timestamp-aligned
`group_read_ops_p99` (C2), group `ReplicationLag` p99, per-member roles +
shard membership (C0/C2), policy.

**E2.** Evidence gate + safety floors first: unresolved roles →
`REPLICA_ROLES_UNRESOLVED`; missing `GetTypeCmds`/`EngineCPU`/`ReplicationLag`
per spec §11.1 → `REPLICA_EVIDENCE_INCOMPLETE`; then B5 `replica_floors.py`.
Any hit → no replica recommendation, reason recorded, node-type evaluation
unaffected (fail closed, never a CONDITIONAL guess).

**E3.** Greedy reduction per spec §11.1's load-transfer model: derive the
empirical `cpu_per_read_op = max(engine_cpu_p99 / read_ops_p99)` over members
clearing `min_read_ops_for_cpu_rate` (no member clears it → redistribution
cannot bind; only the idle-threshold path applies). The projection is
**worst-case concentration** — the entire removed read load lands on the
busiest survivor, never an even split (pooling/reader-endpoint routing can
concentrate redirected clients). Sort replicas ascending by `read_ops_p99`;
remove while:
```python
removed_read_ops = sum(r.read_ops_p99 for r in removed_replicas)
projected = busiest_survivor.engine_cpu_p99 + removed_read_ops * cpu_per_read_op
# require projected <= 70.0
```
Cluster mode: roles from C2's IsMaster resolution (missing/ambiguous/
conflicting → `REPLICA_ROLES_UNRESOLVED`); apply per node group to
`replicas_per_node_group`; skip uneven layouts (`UNEVEN_REPLICA_LAYOUT`).

**E4.** Classification: ACTIONABLE **only** when every removed replica has
`read_ops_max == 0`, `read_ops_sum == 0`, `read_coverage_ratio >= 0.95`,
`read_observed_days >= 30`, known member creation time, latest read sample no
older than `policy.replica_actionable_max_sample_age_seconds`, and no
cache-health warning exists. P99 below the idle threshold alone never proves
zero reads and is at most CONDITIONAL. A projection-justified removal is also
always capped at CONDITIONAL — the per-op model is a heuristic, not a bound
(spec §11.1/§11.3). Emit the exact Section 11.3 reason codes for positive
reads, insufficient coverage, and projection use. `migration` risk ≥ MEDIUM
always;
the spec §11.3 redundancy disclosure is attached to every replica
recommendation. Savings = `removed_count * node_monthly_price` (per-shard
aware in cluster mode). No tiers, no participation in `candidate_limit`.

---

## Workstream F — Confidence trend (spec §15.2)

**F1.** `AWSAdapter.get_elasticache_confidence_trend` — clone the parametrized
EC2 trend shape (daily Maximum headline + p99/p95, six lookback buckets,
`bucket_semantics`): metrics `EngineCPUUtilization` and
`DatabaseMemoryUsagePercentage`, dimension `CacheClusterId`. Node set and
response contract per spec §15.2: for each metric, the hottest node plus
the top `policy.trend_primary_context_limit` shard primaries ranked by that
same metric's decision-scan p99 (cache-cluster id ascending breaks ties),
deduplicated by cache-cluster id. If a hottest node is a primary, retain the
hottest selection reason and emit it once. The response is per-metric with
every series labeled
`{cache_cluster_id, node_group_id, role, selection_reason ∈
{hottest_engine_cpu, hottest_memory, primary}, buckets}` so the UI picks the
series matching the evidence dimension it rescales. Include per-metric
`selection_summary` values for `primary_total`, `primary_returned`,
`primary_omitted`, and `primary_context_limit`, using the count semantics in
spec §15.2 (including a hottest primary in `primary_returned`). At the default
limit, fan-out is at most nine series per metric (one hottest plus eight
primary-context series after deduplication), or 18 series total. The EC2 shape
uses 15 metric-stat query IDs per returned series, so the default worst case is
135 IDs per metric and 270 total; document this formula and the actual emitted
count beside the adapter implementation. Never network. Both are native gauges
— no discovery step.

**F2.** Cache via the `ec2_trend_cache.py` pattern (generalize the cache key by
resource type rather than duplicating the module). Schema version
`elasticache-trend-v1`.

**F3.** Route `GET /elasticache/rightsize/{inventory_id}/trend`.

---

## Workstream G — API routes

Mirror the EC2 rightsizer routes (`app/api/routes/recommendations.py` ~L41)
**including the full parameter surface**: `GET
/elasticache/rightsize/{inventory_id}` and `GET /elasticache/rightsize`
(list) with `account_id`, `region`, `state` filters, `min_monthly_savings`,
`candidate_limit`, and the warning-threshold Query params EC2 exposes
(`network_medium_ratio`, `network_high_ratio`, plus the ElastiCache analogs
`memory_medium_ratio`/`memory_high_ratio` in place of the EBS ratios); wire
`ElastiCacheRightsizer` with the catalog and policies. Register in
`app/api/routes/`. No apply/execute endpoint in V1 (display only — the
actions handlers in `app/actions/handlers_elasticache.py` are a separate
surface and out of scope).

---

## Workstream H — Tests

`tests/test_elasticache_rightsizer_v1.py` (focused regressions) +
`tests/test_elasticache_rightsizer_scenarios.py` (offline harness). Follow
`rightsizers/elasticache/elasticache_rightsizer_test_plan.md` — the scenario
matrix and harness contract live there; do not restate them here. Minimum bar
for this plan: every workstream's acceptance bullet below has a named test, and
the shared-module extractions (B1, B8, F2) keep the EC2 suites green.

---

## Workstream I — UI/UX contract (`elasticache_rightsizer_ui_spec.md`)

The UI contract now exists and is a **gate on backend acceptance**. Keep it in
lockstep with D6 and F1; a backend envelope or trend-schema change is incomplete
until the UI types, fixtures, and contract tests are updated in the same change.
It specifies:

- Node-type tier-card selection behavior and the Balanced default (reuses
  the EC2 mock pattern; enumerate any deltas explicitly).
- The replica-reduction card — the one genuinely new shape — and the
  mutual-exclusion interaction: both recommendation kinds shown, user picks
  one, the other is visibly not composed with it. This contract is now a
  precondition for Workstream E.
- Selected-option evidence mapping: which trend series
  (`selection_reason`) rescales which evidence dimension when the user
  switches tiers.
- DEFERRED and no-candidate rendering (reason codes → copy), including the
  `AUTO_SCALING_STATE_UNKNOWN` CONDITIONAL-cap presentation.
- Interaction with the existing ElastiCache **Execute** action UI
  (`maxops-frontend/src/pages/ElasticacheResourceDetails.tsx`): the
  rightsizer is display-only in V1; define how the read-only recommendation
  coexists with the action controls without implying one-click apply.
- List view: filtering (`account_id`/`region`/`state`), sorting, savings
  aggregation, and duplicate suppression (one row per replication group).
- Typed frontend API contract (TS types for the D6 envelope + trend
  response) with contract tests against MOD-109's fixtures.

---

## Suggested order & dependencies

1. **A** (catalog) — everything reads it.
2. **B1** shared-values extraction (EC2 tests must stay green in the same commit).
3. **B2–B7** (pure modules) with unit tests as they land.
4. **C0** (topology persistence) → **C1/C2** (decision scan + enrichment).
5. **D** (engine service) + **B8** shared tier extraction.
6. **E** (replica reduction).
7. **F** (trend) and **G** (routes) — parallel after D.
8. **I** (UI contract) — already delivered; validate its types and fixtures as
   D6/F1 land, before E or the routes ship.
9. **H** throughout; scenario suite completes last.

## Out of scope (not this plan)

- Applying recommendations (`ModifyReplicationGroup` execution) and the actions
  pipeline.
- Memcached, serverless, Global Datastore evaluation (DEFERRED by design).
- Shard-count (horizontal) recommendations and combined node+replica moves.
- Reserved-node/RI-aware pricing (on-demand `monthly_usd` only, as EC2 V1).
- Engine-version upgrade recommendations.
- A 15-month network trend (dishonest past 63-day retention — same as EC2).
- UI *implementation*. The UI/API contract is NOT out of scope: D6 pins the
  envelope (shared EC2 base + extensions) and Workstream I provides
  `elasticache_rightsizer_ui_spec.md` as a gate on backend acceptance, with
  the replica-card spec landing before Workstream E ships.

## Acceptance

- Catalog: every Redis/Valkey `street_pricing_elasticache` row in a region
  resolves to an entry with vcpus, memory, maxmemory (or disclosed fallback),
  architecture, family gates, and EC2-mapped network baseline/peak where the
  equivalent exists; no AWS calls.
- A cheaper same-capacity Graviton target for an x86 group on a supported
  engine version is an ordinary candidate and can be ACTIONABLE; on an old
  version it is `ENGINE_VERSION_INCOMPATIBLE`.
- Any evictions in-window: every memory-reducing candidate rejected
  (`MEMORY_PRESSURE_EVICTIONS_PRESENT`); same-or-larger-memory candidates carry
  `EVICTIONS_PRESENT` + HIGH memory risk.
- Hottest-node aggregation: a group whose primary binds CPU and whose replica
  binds network reports both, never averaged.
- Network: three-band verdicts, assumed baseline, and allowance-event rules
  behave identically to EC2 for equivalent inputs (shared-module guarantee);
  unmappable node types get `capacity_kind = UNKNOWN` and cap at CONDITIONAL.
- Tiers: Balanced equals the 0.70 single-target selection; dedup/collapse and
  candidate-limit reservation match EC2 semantics; `binding_dimension` spans
  `{cpu, memory, network}`.
- Replica reduction: never below 1 replica with automatic failover, never to
  0, per-shard floors respected, observed-zero-plus-coverage ACTIONABLE path,
  p99-hidden-burst CONDITIONAL path, and read-traffic-blocked path all covered;
  independent of node-type output.
- Scope gates return DEFERRED with the §1 reason codes and no candidates;
  unknown auto-scaling state proceeds capped at CONDITIONAL; member-cluster
  rows are neither persisted anew nor evaluated (one unit per group).
- Statistics parity: every series collected as raw points (gauges `Average`,
  counters `Sum`), percentiles computed locally, demand = max p99 across the
  14/30/60-day windows — the same semantics as EC2.
- Nonzero `TrafficManagementActive` rejects every CPU/memory-reducing
  candidate; unresolved reserved-memory excludes memory-reducing candidates
  and caps at CONDITIONAL.
- Replica reduction fails closed on missing roles or evidence
  (`REPLICA_ROLES_UNRESOLVED` / `REPLICA_EVIDENCE_INCOMPLETE`) while
  node-type output is unaffected; roles come from IsMaster; a
  projection-justified removal is never ACTIONABLE; the survivor projection
  concentrates the full removed load on the busiest survivor.
- A full scan with active ElastiCache checks still yields one inventory row
  per replication group (plus standalone clusters) — the member-cluster
  predicate holds on every persistence path while node-scoped low-item
  telemetry still produces a canonical group finding.
- **Both** onboarding IAM policies (frontend constant + backend service)
  include the new read permissions, with a parity test; every collection
  call paginates.
- The UI spec (Workstream I) exists and matches the D6 envelope and F1
  trend contract, including read-only separation from Execute controls.
- Trend endpoint returns EngineCPU + memory 15-month series, network absent.
- EC2 suites stay green after every shared-module extraction.
