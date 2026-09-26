# ElastiCache Rightsizer Offline Recommendation Test Plan

Status: authoritative test guide for ElastiCache recommendation behavior.

This document defines how to test the ElastiCache rightsizer without creating,
modifying, or querying live ElastiCache resources. The source of truth for
product behavior is `elasticache_rightsizer_spec.md`; scenario identifiers
below provide traceability from that specification to offline tests. It
follows the conventions of the EC2 plan
(`rightsizers/ec2/ec2_rightsizer_test_plan.md`); shared conventions are
referenced, not restated — the equality convention, coverage standard,
mutation targets, and change procedure apply verbatim unless amended here.

## 1. Test boundary

The system under test is:

```python
ElastiCacheRightsizer.get_recommendation(inventory_id)
```

Tests exercise the real recommendation service: scope gates, catalog-backed
candidate generation, engine-version compatibility, compute/memory/network
evaluation, cache-health warnings, risk construction, classification, tier
selection, and replica-count reduction.

Each scenario supplies only the inputs the engine consumes:

- One synthetic `ElasticacheInventory` row (current group), with topology in
  `metadata_json` (engine, engine_version, node type, node counts,
  `automatic_failover`, `multi_az`, `cluster_mode_enabled`,
  `num_node_groups`, `replicas_per_node_group`, `member_cluster_ids`,
  scope-gate markers).
- Persisted normalized decision-scan telemetry in
  `metadata_json["rightsizing_metrics"]`, including the `per_node` block for
  replica scenarios.
- An injected `ElastiCacheNodeCatalog` whose `list_region()` returns the
  current entry plus candidates.
- Optional compute, warning, scope, and candidate policy overrides.
- Optional `min_monthly_savings` and `candidate_limit` request values.

Outside the boundary for *scenario* tests (exactly as in the EC2 plan): AWS
calls and credentials, CloudWatch collection/pagination/aggregation, HTTP
transport, pricing enrichment jobs, availability validation, confidence-trend
collection and caching, and applying a recommendation. Unlike EC2, the
collection, enrichment, inventory-identity, trend, and route code is all
greenfield here, so those surfaces are covered by the §3.11 companion module
tests (stubbed clients, still no network) rather than left untested.

## 2. Offline scenario harness

### 2.1 Harness contract

```python
def run_elasticache_scenario(
    *,
    current: ElastiCacheCatalogEntry,
    candidates: tuple[ElastiCacheCatalogEntry, ...],
    topology: dict[str, object] | None = None,       # merged into metadata_json
    normalized_metrics: dict[str, dict[str, float]] | None = None,
    per_node_metrics: dict[str, dict[str, float]] | None = None,
    signals: dict[str, bool] | None = None,          # allowance events etc.
    warning_policy: ElastiCachePerformanceWarningPolicy | None = None,
    compute_policy: ElastiCacheComputePolicy | None = None,
    scope_policy: ElastiCacheScopePolicy | None = None,
    candidate_policy: ElastiCacheCandidatePolicy | None = None,
    min_monthly_savings: float = 0.0,
    candidate_limit: int = 10,
) -> dict[str, object]:
    ...
```

In-memory SQLAlchemy database, one inserted inventory row, injected catalog,
real `get_recommendation()`. No AWS adapter is constructed or mocked; a
scenario attempting network access is incorrectly scoped.

Topology defaults (overridable per scenario): Redis 7.1, cluster mode
disabled, 1 node group, 2 replicas with roles resolved (`IsMaster` = 1 on
the primary and 0 on the replicas, consistent with `member_roles` /
`CurrentRole`), automatic failover enabled, multi-AZ enabled,
status `available`, no Global Datastore, auto scaling confirmed absent
(`False`, not `None`), `allowed_scale_up/down_types` present and containing
every scenario candidate, `reserved_memory_percent = 0.25` resolved from the
parameter group (scenarios exercising the unresolved path set it to null
explicitly).

### 2.2 Catalog data-fidelity model

The four fidelity modes of the EC2 plan (§2.2) apply unchanged:
`REAL_CATALOG` (default), `REAL_CATALOG_PRICE_OVERRIDE` (only `monthly_usd`
replaced), `SYNTHETIC_CAPABILITY_OVERRIDE` (named fields only, with a
regression assertion that untouched fields still match the packaged entry),
and `FULLY_SYNTHETIC` (last resort, justified in the test).

The catalog fixture loads entries through `ElastiCacheNodeCatalog` from the
packaged `maxops_pricing.db` (`street_pricing_elasticache` +
`ec2_instance_specs` for network mapping). The fixture asserts the extracted
database exists before connecting and points to
`./.venv/bin/python staging_pricing/unpack_pricing_db.py` on failure — same
rationale as the EC2 plan (silent empty-DB creation must not masquerade as a
`no such table` error later).

Default scenario dimensions: `us-east-1`, engine `redis`. Required
representative node types are fixture contracts (fail, never skip, when
missing from the packaged DB):

```text
cache.m6g.large / cache.m6g.xlarge      (Graviton general-purpose)
cache.m5.large / cache.m5.xlarge        (x86 general-purpose)
cache.r6g.large / cache.r7g.large       (Graviton memory-optimized)
cache.r5.large                          (x86 memory-optimized)
cache.t3.medium / cache.t4g.medium      (burstable, both architectures)
cache.r6gd.xlarge                       (data tiering)
cache.c7gn.xlarge                       (network-optimized, numeric EC2 peak)
```

A catalog-conformance test verifies each resolves with vcpus, memory,
maxmemory (or the disclosed `memory_attribute` fallback), architecture, and —
for the types whose EC2 equivalent carries structured `NetworkInfo` — a
numeric network baseline or peak.

### 2.3 Synthetic telemetry derived from real capacity

As in the EC2 plan (§2.3): telemetry is synthetic (it describes the
hypothetical workload) but ratio/boundary inputs are **derived from the real
target capability**, never from a substituted capability:

```python
bytes_used_p99   = target.usable_memory_bytes * desired_ratio
engine_cpu_p99   = desired_projected_util * 100          # identity projection
host_cpu_p99     = desired_projected_util * 100 * target.vcpus / current.vcpus
network_in_p99   = target.network_baseline_mbps * desired_ratio
read_ops_p99     = policy.idle_replica_read_ops_threshold * factor
read_ops_max     = scenario value (zero only for ACTIONABLE fixtures)
read_ops_sum     = scenario value (zero only for ACTIONABLE fixtures)
read_coverage_ratio = scenario value (default 1.0)
read_observed_days  = scenario value (default 60.0)
read_latest_sample_age_seconds = scenario value (default 300)
```

Assert the prerequisite capability is present before computing the input.
Exact price boundaries use `REAL_CATALOG_PRICE_OVERRIDE`.

### 2.4 Neutral defaults

The scenario builder starts from a neutral candidate that creates no
unrelated warnings:

- Ungated family (general-purpose `m`), engine-version supported.
- Positive monthly savings; structured capability source.
- Known network baseline and reliable peak (EC2-mappable type).
- Low EngineCPU, host CPU, memory, and network demand.
- Zero evictions, no swap, no replication lag, low connections.
- All allowance signals collected and false.
- Healthy topology per §2.1 defaults.

Each scenario changes only the inputs named in its setup.

### 2.5 Compact decision projection

Reuse the EC2 `decision_projection` shape (targets, savings, classifications,
reason codes, warnings, tiers, tier_default, rejection_summary) extended with:

```python
"replica": {
    "recommended": bool,
    "target_replica_count": int | None,
    "classification": str | None,
    "reason_codes": list[str],
}
```

Individual scenarios additionally assert only the evidence they prove
(`projected_util`, `binding_dimension`, network ratios, per-node read-ops
evidence). Do not snapshot timestamps, disclosure text, or full evidence
envelopes.

## 3. Required recommendation scenarios

Scenario ID and specification columns form the required traceability map.
Equality convention: identical to the EC2 plan §3 — conservative at equality
(0.40 escalates, demand at baseline triggers sustained-above-baseline, demand
at the reliable peak rejects, `>= usable memory` rejects) with 0.70 remaining
MEDIUM; tier gates use `<=`. Freezing these semantics happens when the rows
land; later changes are versioned policy changes.

### 3.1 Scope and deferral (spec §1)

Each row asserts candidate generation did not run, `recommendations` is
empty, and no replica recommendation is produced.

| ID | Spec | Setup | Required result |
| --- | --- | --- | --- |
| `SCOPE-101` | §1 | Engine memcached | `DEFERRED / UNSUPPORTED_ENGINE` |
| `SCOPE-102` | §1 | Serverless cache (no node type) | `DEFERRED / SERVERLESS_CACHE` |
| `SCOPE-103` | §1 | Global Datastore member (primary and secondary variants) | `DEFERRED / MANAGED_BY_GLOBAL_DATASTORE` |
| `SCOPE-104` | §1 | Application Auto Scaling target attached (`True`) | `DEFERRED / MANAGED_BY_AUTO_SCALING` |
| `SCOPE-105` | §1 | Auto-scaling state unknown (`None`, check errored) | Evaluation proceeds; every candidate carries `AUTO_SCALING_STATE_UNKNOWN`; nothing is `ACTIONABLE` (CONDITIONAL cap); unknown state does not defer |
| `SCOPE-106` | §1 | Status `modifying` | `DEFERRED / RESOURCE_NOT_AVAILABLE` |
| `SCOPE-107` | §1 | Plain available Redis and Valkey groups | Evaluation proceeds for both engines |
| `SCOPE-108` | §1, §2 | Legacy member-cluster inventory row (`ReplicationGroupId` set) | `DEFERRED / MEMBER_OF_REPLICATION_GROUP`; no candidates, no replica output |

### 3.2 Base selection, pricing, and candidate eligibility (spec §2, §3, §12)

| ID | Spec | Setup | Required result |
| --- | --- | --- | --- |
| `REC-101` | §2, §10 | Cheaper compatible target; all dimensions low and known | `ACTIONABLE` with savings = per-node delta × node count (3-node group) |
| `REC-102` | §2 | Target price equals / exceeds current | Target absent (no positive savings) |
| `REC-103` | §2 | Savings just below, exactly at, and just above `min_monthly_savings`; group savings (not per-node) compared | Below excluded; equality and above included |
| `REC-104` | §2 | Float-hazard price pair (e.g. per-node $10.00 → $9.99 on a 1-node group) | Decimal-safe one-cent floor: just-below excluded, at/above included |
| `REC-105` | §3.1 | x86 current (`cache.m5.large`), cheaper Graviton target (`cache.m6g.large`), Redis 7.1 | Target is an ordinary candidate; may be `ACTIONABLE`; no OPPORTUNITY class anywhere in the response |
| `REC-106` | §3.1 | Target absent from the persisted `allowed_scale_up/down_types`; variant: no API result and engine version below the fallback-table floor (e.g. Redis 5.0 → `cache.m7g`) | Rejected `ENGINE_VERSION_INCOMPATIBLE` in both variants; the allowed-sets verdict wins over the table when both are present |
| `REC-107` | §3.1 | No API result, target absent from the fallback table, version unparseable | Skipped; tallied `ENGINE_VERSION_SUPPORT_UNKNOWN` (not DEFERRED) |
| `REC-116` | §3.1 | Fallback-table three-part boundary: current Redis 5.0.5 vs 5.0.6 against a `(5, 0, 6)` floor | 5.0.5 rejected, 5.0.6 eligible — major.minor truncation would get this wrong |
| `REC-108` | §3.2 | General-purpose current, burstable target (`cache.m5 → cache.t4g`) | Absent as `FAMILY_NOT_ELIGIBLE` |
| `REC-109` | §3.2 | Burstable-to-burstable (`cache.t3.medium → cache.t4g.medium`) | Eligible |
| `REC-110` | §3.2 | Data tiering both directions (`cache.r6g → cache.r6gd`, `cache.r6gd → cache.r6g`) | Both absent as `FAMILY_NOT_ELIGIBLE` |
| `REC-111` | §3.2 | Family class removed from `gated_family_classes` via policy override | Gated target becomes eligible; default policy still rejects it |
| `REC-112` | §2 | More eligible candidates than `candidate_limit`; equal-savings tie | Limit respected; savings-ranked; deterministic node-type tie-break |
| `REC-113` | §2 | Current node type absent from catalog | No recommendations; blocking reason `CURRENT_NODE_PRICING_OR_SPEC_MISSING` |
| `REC-114` | §2, §12 | `candidate_limit=1` with a Balanced qualifier displaced by higher-savings Aggressive-only candidates | Balanced target holds the reserved slot; Balanced and Aggressive tiers reference it; only Conservative may be null |
| `REC-115` | §10 | Multiple candidates each rejected for a different hard reason | `recommendations` empty; `rejection_summary` tallies every code with correct counts |

### 3.3 Compute (spec §5)

| ID | Spec | Setup | Required result |
| --- | --- | --- | --- |
| `CPU-101` | §5.1 | EngineCPU p99 present | Engine projection is identity: `projected_engine_cpu == engine_cpu_p99` regardless of target size |
| `CPU-102` | §5.1 | Host CPU p99 present; target with more and with fewer vCPUs | Host projection scales by `current_vcpus / target_vcpus` |
| `CPU-103` | §5.1 | Engine projection passes 0.70 gate but host projection fails it | Candidate excluded by the compute gate |
| `CPU-104` | §5.2 | Target with 2 vCPUs at a host projection between `0.70 * 0.85` and `0.70` | Excluded by the small-target tightening; a 4-vCPU target at the same projection passes |
| `CPU-105` | §5.2 | `EngineCPUUtilization` absent | Candidates generated from host CPU alone; every candidate carries `ENGINE_CPU_METRIC_UNAVAILABLE`, compute risk ≥ MEDIUM, classification capped at `CONDITIONAL` |
| `CPU-106` | §5, §12 | Boundary sweep at each tier ratio (0.55 / 0.70 / 0.85) on `projected_util` | Below/equal qualify; above does not (`<=` gate) |

### 3.4 Memory (spec §6)

| ID | Spec | Setup | Required result |
| --- | --- | --- | --- |
| `MEM-101` | §6.1 | Resolved `reserved_memory_percent = 0.10` vs resolved default `0.25` | Usable memory uses the resolved value; raw node RAM is never used |
| `MEM-102` | §6.2 | `bytes_used_p99` just below, exactly at, and just above target usable memory | Below passes; at and above reject `MEMORY_REQUIREMENT_NOT_MET` |
| `MEM-103` | §6.3 | Nonzero `Evictions` sum; memory-reducing target | Rejected `MEMORY_PRESSURE_EVICTIONS_PRESENT` |
| `MEM-104` | §6.3 | Nonzero `Evictions`; same-or-larger-memory cheaper target (Graviton swap) | Eligible; carries `EVICTIONS_PRESENT`; memory risk HIGH; `CONDITIONAL` |
| `MEM-105` | §6.4 | Memory series absent (telemetry gap) | Memory-reducing candidates excluded (capacity retained rule); retention disclosed; no discovery machinery invoked |
| `MEM-106` | §6.1 | Catalog entry using the `memory_attribute` maxmemory fallback | Comparison still uses the derived usable value; the weaker source is disclosed in evidence |
| `MEM-107` | §6.1 | Legacy absolute `reserved-memory` (bytes) resolved for the group | Usable memory = `maxmemory - reserved_bytes` per target; the fixed reserve consumes a larger fraction of the smaller target |
| `MEM-108` | §6.1 | Reservation unresolved (null; AccessDenied path) | Memory-reducing candidates excluded (capacity retention on raw maxmemory); survivors carry `RESERVED_MEMORY_UNKNOWN`, memory risk ≥ MEDIUM, CONDITIONAL cap; no silent 0.25 |

### 3.5 Network (spec §9)

The EC2 network rows apply through the shared modules; re-run them here at the
service boundary with cache node types to prove the wiring, not the math.
Directions stay separate in every scenario; the larger directional ratio
drives the verdict.

| ID | Spec | Setup | Required result |
| --- | --- | --- | --- |
| `NET-101` | §9 | Mapped baseline; ratio below 0.40 / at 0.40 / just above 0.70 | LOW / MEDIUM / HIGH risk with the matching warnings; MEDIUM+ is never `ACTIONABLE` |
| `NET-102` | §9 | Demand at baseline, between baseline and peak | `NETWORK_SUSTAINED_ABOVE_BASELINE`; `CONDITIONAL` |
| `NET-103` | §9 | Demand exactly at and above the reliable peak | Rejected `NETWORK_RELIABLE_MAX_EXCEEDED` |
| `NET-104` | §9 | Target whose EC2 equivalent has a numeric peak but no baseline | Assumed baseline (exact EC2-equivalent family denominator); `NETWORK_BASELINE_ASSUMED`; never `ACTIONABLE`, never hard-fails |
| `NET-105` | §9 | Cache type with no EC2 equivalent in `ec2_instance_specs` | `capacity_kind = UNKNOWN`; `RESOURCE_BASELINE_CAPACITY_UNKNOWN`; `CONDITIONAL`; no fabricated ratio |
| `NET-106` | §9 | Bandwidth allowance event with lower/equal/unknown target capacity, then with clearly higher capacity | Lower/equal/unknown rejects; higher remains `CONDITIONAL` with HIGH warning |
| `NET-107` | §9 | PPS / conntrack allowance events | Matching warning; network risk HIGH |
| `NET-108` | §9 | Allowance metrics absent | `NETWORK_ALLOWANCE_METRICS_MISSING`; non-blocking |
| `NET-109` | §4.3, §9 | High inbound + low outbound, then reversed | High direction drives the verdict; both ratios preserved; never summed |

### 3.6 Cache health (spec §8)

| ID | Spec | Setup | Required result |
| --- | --- | --- | --- |
| `HLT-101` | §8 | SwapUsage p99 just below and just above `swap_warning_bytes` | Below: no warning; above: `SWAP_USAGE_REVIEW_REQUIRED`, memory risk HIGH, `CONDITIONAL` |
| `HLT-102` | §8 | ReplicationLag p99 above `replication_lag_warning_seconds` | `REPLICATION_LAG_REVIEW_REQUIRED`; `CONDITIONAL`; also blocks replica reduction (`REP-105`) |
| `HLT-103` | §8 | CurrConnections p99 above `0.70 * 65_000` | `HIGH_CONNECTION_COUNT_REVIEW_REQUIRED`; compute risk MEDIUM; `CONDITIONAL` |
| `HLT-104` | §8, §13 | Same demand, thresholds moved via policy override | Warnings/classification change; normalized metrics and hard-constraint outcomes do not |
| `HLT-105` | §7, §8 | Single nonzero `TrafficManagementActive` point; one CPU/memory-reducing target and one same-capacity cheaper target | Reducing target rejected `TRAFFIC_MANAGEMENT_ACTIVE`; same-capacity target carries `TRAFFIC_MANAGEMENT_DETECTED`, compute risk HIGH, `CONDITIONAL` |
| `HLT-106` | §8 | Burstable current (`cache.t3.medium`, max accrual 576), credit-balance minimum below `0.10 × 576`, burstable target | `CPU_CREDITS_REVIEW_REQUIRED`; compute risk HIGH; `CONDITIONAL`; a balance above the watermark produces no warning |
| `HLT-107` | §8 | `cache.t2` current (AWS publishes no credit metrics), burstable target | `CPU_CREDIT_TELEMETRY_UNAVAILABLE`; burstable target capped at `CONDITIONAL`; no error, no silent unqualified recommendation |

### 3.7 Hottest-node aggregation (spec §2)

| ID | Spec | Setup | Required result |
| --- | --- | --- | --- |
| `AGG-101` | §2 | Primary binds EngineCPU; a replica binds network (per-node metrics differ) | Group verdicts use the max per dimension from different nodes; evidence names both |
| `AGG-102` | §2 | Idle replicas alongside a saturated primary | Group memory/CPU demand equals the primary's, not the mean |

### 3.8 Replica-count reduction (spec §11)

| ID | Spec | Setup | Required result |
| --- | --- | --- | --- |
| `REP-101` | §11.2 | Automatic failover enabled, 1 replica | No reduction; `REPLICA_FLOOR_AUTOMATIC_FAILOVER` recorded |
| `REP-102` | §11.2 | Failover disabled, 1 replica | No reduction to 0; `REPLICA_FLOOR_LAST_REPLICA` |
| `REP-103` | §11.1, §11.3 | 3 replicas; two removed replicas have every observed GetTypeCmds Sum point equal to zero, total read sum zero, coverage ≥ `replica_actionable_min_coverage_ratio`, observed days ≥ `replica_actionable_min_observed_days`, known creation time, latest sample age ≤ `replica_actionable_max_sample_age_seconds`, health clean | Reduction to 1 recommended; `ACTIONABLE`; savings = 2 × node price; migration risk ≥ MEDIUM |
| `REP-104` | §11.1 | A low-CPU replica serving material read traffic | That replica is not removable; reduction limited to the truly idle replicas or absent |
| `REP-105` | §11.1 | Idle replicas but sustained ReplicationLag | No `ACTIONABLE` reduction; blocked or `CONDITIONAL` per §11.3 |
| `REP-106` | §11.1 | Removal projection pushes busiest survivor's EngineCPU above 70% | Reduction stops at the count that keeps the projection within target |
| `REP-107` | §11.2 | Cluster mode enabled, even layout (2 shards × 2 replicas), one idle replica per shard | Per-node-group reduction respecting per-shard floors |
| `REP-108` | §11.2 | Cluster mode with uneven replica layout | No replica recommendation; `UNEVEN_REPLICA_LAYOUT` |
| `REP-109` | §11 | Both a node-type candidate and a replica reduction qualify | Both returned; neither composed; replica recommendation absent from `recommendations`, tiers, and `candidate_limit` |
| `REP-110` | §11.1 | `GetTypeCmds` series missing for one member | No replica recommendation; `REPLICA_EVIDENCE_INCOMPLETE`; node-type output unaffected |
| `REP-111` | §11.1 | Cluster mode enabled; `IsMaster` identifies exactly one primary per shard | Roles resolved from `IsMaster`; reduction proceeds per shard |
| `REP-112` | §11.1 | `IsMaster` missing for one member; variants: two `IsMaster = 1` in one shard; `IsMaster` contradicting `CurrentRole` | No replica recommendation; `REPLICA_ROLES_UNRESOLVED` in every variant; roles never inferred from traffic |
| `REP-113` | §11.1 | Members clearing the ops floor with differing `engine_cpu/read_ops` ratios | `cpu_per_read_op` equals the maximum observed ratio, and the survivor projection uses it |
| `REP-114` | §11.1, §11.3 | A non-idle replica removable only via the survivor projection; health clean | Reduction returned with `REPLICA_LOAD_PROJECTION_REQUIRES_REVIEW` and capped at `CONDITIONAL` — never `ACTIONABLE`; the projection adds the **full** removed read load to the busiest survivor (assert the worst-case arithmetic, not an even split) |
| `REP-115` | §11.1, §11.3 | Replica p99 below the idle threshold but one or more high read bursts occur in the top 1% of five-minute points (`read_ops_max > 0`, `read_ops_sum > 0`) | Reduction may be returned but is `CONDITIONAL` with `REPLICA_READ_TRAFFIC_OBSERVED`, never `ACTIONABLE`; p99 cannot certify zero reads |
| `REP-116` | §11.3 | All observed read points are zero; variants: coverage just below `replica_actionable_min_coverage_ratio`, observed days just below `replica_actionable_min_observed_days`, missing member creation time, latest sample just above `replica_actionable_max_sample_age_seconds` | Reduction may be returned but is `CONDITIONAL` with `REPLICA_READ_COVERAGE_INSUFFICIENT`; equality at all three numeric policy boundaries with known creation time permits the ACTIONABLE path |

### 3.9 Tiers and classification (spec §10, §12)

| ID | Spec | Setup | Required result |
| --- | --- | --- | --- |
| `TIER-101` | §12 | Three candidates spanning the three ratios | Three distinct tier targets; Balanced default |
| `TIER-102` | §12 | All present tiers resolve to one target | Satisfied-tier labels; collapse detectable |
| `TIER-103` | §12 | No cushioned cost-saving Conservative option | Conservative null; others selected normally |
| `TIER-104` | §12 | Aggressive target has HIGH network risk; Conservative clean | Aggressive `CONDITIONAL`, Conservative independently `ACTIONABLE` |
| `TIER-105` | §12 | Every non-null tier option | References a returned recommendation; projection, savings, classification, risk match that candidate; no coremark/performance fields exist |
| `CLS-101` | §10 | All PASS, all risks LOW, no blocking warnings | `ACTIONABLE` |
| `CLS-102` | §10 | Network or memory/health risk MEDIUM or HIGH; hard statuses PASS | `CONDITIONAL` |
| `CLS-103` | §10 | Any hard status UNKNOWN | Not `ACTIONABLE` |
| `CLS-104` | §10 | Any hard FAIL | `REJECTED` |
| `CLS-105` | §10 | Full response sweep across all scenarios | No code path emits `OPPORTUNITY` |

### 3.10 Telemetry sufficiency (spec §15.3)

| ID | Spec | Setup | Required result |
| --- | --- | --- | --- |
| `TEL-101` | §15.3, §1 | Short observation history, otherwise clean, vs identical full-history run; replica recommendation disabled so only node-type output is compared | `observed_days` reflects actual span; node-type classification, tiers, and targets are identical; `INSUFFICIENT_DATA` never returned |
| `TEL-102` | §4, §15.1 | Demand high in the last 14 days, low across the full 60 | Decision demand is the max p99 across the 14/30/60-day windows — the recent surge binds; a 60-day-only p99 would understate it |

### 3.11 Frontend/UI contract (`elasticache_rightsizer_ui_spec.md`)

These run in the frontend suite against serialized backend
MOD-109/MOD-108/MOD-113 fixtures; independently maintained lookalike JSON is
forbidden.

| ID | Contract | Setup | Required result |
| --- | --- | --- | --- |
| `UI-101` | UI spec §2, §5 | Normal, deferred, and no-candidate MOD-109 fixtures | TypeScript decoder accepts the exact envelopes; absent `savings_previews` is not synthesized; reason and rejection states render |
| `UI-102` | UI spec §3, §4.2 | Group with three node tiers plus replica alternative | Balanced defaults; selecting replica deselects node type; detail and fleet savings never add mutually exclusive alternatives |
| `UI-103` | UI spec §4.3–§4.5 | MOD-108 two-shard trend fixture with a hottest primary | CPU/memory choose their matching hottest series; all returned shard primaries render as secondary context; hottest primary is not duplicated |
| `UI-104` | UI spec §5–§6 | DEFERRED, no-candidate, unknown-auto-scaling, incomplete-replica, trend-502, and unknown reason code fixtures | Explicit honest states render; recommendation survives trend failure; rightsizer selection never mutates or invokes Execute |
| `UI-105` | UI spec §7 | Keyboard and screen-reader interaction over tier/replica alternatives and chart summaries | Radio semantics, focus order, names, text alternatives, contrast, and responsive overflow meet the frontend accessibility harness |
| `UI-106` | UI spec §4.3–§4.5, §8 | MOD-113 100-shard fixture at the default primary-context limit | At most nine series render per metric; the hottest line remains dominant; returned/total primary counts and omitted count are disclosed; no follow-up request is issued for an omitted primary and none is drawn |

### 3.12 Companion module-level tests

Explicit, documented exceptions to the service boundary:

| ID | Spec | Boundary | Setup | Required result |
| --- | --- | --- | --- | --- |
| `MOD-101` | §4 | normalization package | 1-minute and 5-minute Sum series for NetworkBytes and Evictions | Exact per-second/Mbps conversion; missing samples never zero |
| `MOD-102` | Impl plan A2/A3 | catalog | attributes_json with multiple `redis*-maxmemory` keys; families in and absent from the support table | Max-across-keys maxmemory; support returns True/False/None per contract |
| `MOD-103` | §9 | shared network modules | Identical inputs through the EC2 and ElastiCache wiring | Identical band verdicts and warnings (shared-module regression guard) |
| `MOD-104` | Impl plan C1 | adapter (stubbed CloudWatch client) | Metric query construction: statistics per series (`Average`/`Sum`/`Maximum`), `CacheClusterId` dimensions per member, 500-query chunking, pagination | Queries match the spec §15.1 table exactly; no server-side percentiles requested; every member covered |
| `MOD-105` | Impl plan C2 | enrichment | Missing-node and partial series; timestamp-aligned group read-ops summation; member creation times; 14/30/60-day window derivation | Missing samples never zero-filled; timestamps lacking any member dropped from the group sum; demand is the max p99 across windows; `per_node` includes roles plus exact read max/sum, age-based coverage, observed days, and latest-sample age |
| `MOD-106` | Impl plan C0 | **full scan** (stubbed adapter), inventory phase AND finding phase with the ElastiCache checks (`low_items_count`, `non_graviton`) active | One two-shard replication group + member clusters + one standalone cluster; member metrics prove a low group item count and non-Graviton type | After the complete scan, exactly one row per group plus the standalone cluster; the expected low-item and non-Graviton findings attach to the group inventory id; shard values are summed once and replicas are not double-counted; member clusters never re-enter through check-result persistence |
| `MOD-107` | Impl plan C0 | adapter (stubbed clients) | AccessDenied on `DescribeScalableTargets`, `ListAllowedNodeTypeModifications`, and `DescribeCacheParameters` independently | Each degrades to null independently; no exception escapes; nulls flow into the engine's unknown-state rules |
| `MOD-108` | Impl plan F, spec §15.2 | trend cache + route | Cache-key uniqueness per (resource type, inventory, node); a two-shard group whose EngineCPU-hottest and memory-hottest nodes differ and where one hottest node is a primary; 404/upstream-error responses | Keys never collide with EC2 entries; each metric contains its unique hottest series plus its returned primary-context series; the hottest-primary is emitted once with the hottest selection reason; every series has `cache_cluster_id`, `node_group_id`, `role`, `selection_reason`; `selection_summary` counts are exact; route returns the documented error envelope |
| `MOD-109` | Impl plan D6 | route serialization | Full response with node and replica alternatives, deferred response, and no-candidate response | Field-for-field match with D6 and the UI §2 contract: shared base uses EC2 names (`policy`, `capability_catalog`, `deferred_reason_codes`); node candidates use `yearly_savings`, `risk_assessment`, and named evidence blocks; replica object includes observed-zero/coverage evidence; `savings_previews` absent; extension fields present |
| `MOD-110` | Impl plan C0 | IAM policy definitions | Frontend constant (`iamReadOnlyPolicy.ts`) and backend onboarding policy (`iam_onboarding_service.py`) | Both contain every action the ElastiCache collection path calls (`DescribeScalableTargets`, `ListAllowedNodeTypeModifications`, `DescribeCacheParameters`, `DescribeCacheParameterGroups`, existing describes) — parity guard |
| `MOD-111` | Impl plan C0 | adapter (stubbed clients) | Multi-page responses: `DescribeScalableTargets` (`NextToken`, 50/page), `DescribeCacheParameters` / `describe_replication_groups` / `describe_cache_clusters` (`Marker`) | Every page consumed; a group or scalable target on page 2 is never silently missed |
| `MOD-112` | Impl plan C0 | adapter (stubbed Application Auto Scaling client) | Targets named `replication-group/rg-a` for both `NodeGroups` and `Replicas`, `cache-cluster/cache-a` for `Nodes`, unrelated targets, and an error after page 1 | Prefixes normalize to canonical inventory ids; every supported matching dimension yields `True`; unrelated complete results yield `False`; any incomplete/error result yields `None`, never a partial false negative |
| `MOD-113` | Impl plan F1, spec §15.2 | trend adapter + route | A 100-shard group with distinct CPU and memory p99 rankings; policy limits `8`, `0`, and `100`; the hottest node is a primary in one variant | Each metric selects primaries by that metric's own descending decision-scan p99 (cache-cluster id breaks ties), returns at most hottest + limit series after deduplication, and reports exact total/returned/omitted/limit counts; limit `8` emits no more than nine series/135 metric-stat query IDs per metric (18/270 total), limit `0` returns hottest only, limit `100` truncates nothing, and a hottest primary is emitted once with the hottest reason; assert exact `15 * returned_series_count` query IDs for every variant |

## 4. Generated invariants

EC2 invariants 1–4, 7, 8, 11 (monotonic demand), 12, and 13 apply with the
obvious substitutions (no storage dimension, no coremark, no previews). New or
amended:

1. No response ever contains `OPPORTUNITY`, a savings preview, or a
   memory-metric-discovery artifact.
2. Nonzero evictions ⇒ no returned recommendation reduces usable memory.
3. Replica recommendations never appear in `recommendations`, tiers, or
   `candidate_limit`; node-type and replica outputs are independent (either,
   both, or neither may be present for the same input).
4. A replica recommendation never targets a count below 1, never below the
   automatic-failover floor, and never violates a per-shard floor.
5. For any group, raising any per-node demand series (holding measurement
   coverage fixed) cannot improve the outcome under the EC2 partial order,
   extended with: replica reduction outcome `absent < CONDITIONAL <
   ACTIONABLE` and target replica count (higher retained count is never
   "better" evidence of the same demand).
6. An engine-version-incompatible or family-gated target never appears, under
   any policy override except the explicit gate-list override.
7. Group savings always equal per-node savings × affected node count at cent
   precision.
8. Nonzero `TrafficManagementActive` ⇒ no returned recommendation reduces
   CPU or memory capacity.
9. Unresolved reserved-memory or unknown auto-scaling state ⇒ nothing in the
   response is `ACTIONABLE`.
10. A replica recommendation exists only when roles are resolved from
    IsMaster and every §11.1 evidence series is present (fail closed, never
    a degraded guess).
11. A replica recommendation is ACTIONABLE only when every removed replica has
    zero maximum and total observed reads and meets both coverage floors. Any
    positive observed read point, insufficient coverage/time, unknown member
    age, or stale latest sample makes it non-ACTIONABLE regardless of p99.

Generated values must be finite and non-negative; concentrate on policy
boundaries; pairwise combinations, not a full Cartesian product.

## 5. Coverage, mutation, and maintenance

The EC2 plan's coverage standard (§5.1), mutation targets (§5.2), and change
procedure (§5.3) apply unchanged, with these additional mutation targets:

- Identity vs vCPU-ratio projection wiring (EngineCPU vs host CPU swapped).
- The `<=` in the small-target tightening and the 2-vCPU boundary.
- `max` vs mean in hottest-node aggregation.
- Eviction floor trigger (`> 0` vs `>= threshold`).
- Replica floor comparisons and the survivor-projection inequality.
- Per-node vs group savings multiplication.
- Percent vs absolute reserved-memory application, and applying the absolute
  form per target rather than once.
- The nonzero trigger on `TrafficManagementActive` and the reducing vs
  non-reducing candidate split it drives.
- `max` vs `min`/mean in the empirical `cpu_per_read_op` derivation.
- Worst-case concentration vs even split in the survivor projection (the
  full removed load must hit the busiest survivor).
- The observed-zero maximum/sum checks and both coverage comparisons on
  ACTIONABLE replica reductions.
- Three-part vs truncated two-part version comparison in the fallback table.

## 6. Safe execution

```bash
.venv/bin/python staging_pricing/unpack_pricing_db.py   # fresh checkout only
.venv/bin/python -m pytest tests/test_elasticache_rightsizer_v1.py -q
.venv/bin/python -m pytest tests/test_elasticache_rightsizer_scenarios.py -q
```

When a shared module is extracted from the EC2 package, also run:

```bash
.venv/bin/python -m pytest tests/test_ec2_rightsizer_v1.py tests/test_ec2_rightsizer_scenarios.py -q
```

Never run broad or keyword discovery (`pytest tests/`, `pytest -k cache`,
repository-wide coverage). Files under `tests/integration/` — including
`test_elasticache_*` integration files — provision real, billable AWS
resources.

## 7. Acceptance criteria

The offline suite is complete when:

- Every service-boundary scenario declares a catalog fidelity mode;
  `REAL_CATALOG` is the default and capability overrides assert untouched
  fields still match the packaged entry.
- A catalog-conformance test resolves every named representative node type
  from the packaged database (fail, never skip) with the §2.2 field
  requirements, including EC2-mapped network capability where specified.
- Ratio and capacity boundaries use telemetry derived from the loaded target
  capability; price boundaries override only `monthly_usd`.
- Every scenario ID maps to at least one named test or parametrized case, and
  every applicable spec clause maps back to a scenario ID.
- Boundary scenarios assert below/equality/above behavior against active
  policy values.
- Generated invariants pass across a deterministic seed set; critical
  comparison mutations are killed by the named suite.
- The shared-module regression guard (`MOD-103`) proves EC2 and ElastiCache
  network verdicts cannot drift.
- Tests require no AWS credentials, network access, or live resources; the
  versioned pricing artifact is permitted and required test data; results are
  deterministic across repeated local and CI runs.
