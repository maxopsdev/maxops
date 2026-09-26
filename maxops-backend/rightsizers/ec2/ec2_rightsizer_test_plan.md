# EC2 Rightsizer Offline Recommendation Test Plan

Status: authoritative test guide for EC2 recommendation behavior.

Implementation status: the scenario matrix and traceability are complete. The
catalog-backed data-fidelity model specified in Sections 2.2–2.4 is the next
fixture migration: current scenarios still construct their capability values
directly. The reusable offline harness and consolidated service-boundary
scenarios live in `tests/test_ec2_rightsizer_scenarios.py`; focused
compatibility, tiering, and preview regressions remain in
`tests/test_ec2_rightsizer_v1.py`.

This document defines how to test the EC2 rightsizer without launching, stopping,
resizing, or querying live EC2 instances. The source of truth for product behavior
remains `ec2_rightsizer_spec.md`; the scenario identifiers below provide
traceability from that specification to offline tests.

## 1. Test boundary

The system under test is:

```python
EC2Rightsizer.get_recommendation(inventory_id)
```

Tests exercise the real recommendation service, constraint evaluators, warning
evaluators, risk construction, classification, ranking, and tier selection.

Each scenario supplies only the inputs the recommendation engine consumes:

- One synthetic `Ec2Inventory` row representing the current instance.
- Persisted normalized telemetry and signals in `metadata_json`.
- Zero or more attached `EbsInventory` rows when storage behavior matters.
- An injected catalog of `EC2CatalogEntry` objects. By default, capability
  fields come unchanged from the repository's packaged pricing/specification
  database; regional prices also come from that database unless the scenario
  explicitly tests a price boundary.
- Optional compute, warning, scope, and candidate policy overrides.
- Optional minimum-savings and candidate-limit request values.

The following are explicitly outside this test boundary:

- AWS API calls and credentials.
- CloudWatch collection, pagination, aggregation, and metric discovery.
- HTTP route behavior and response transport.
- Running pricing or instance-spec enrichment. Reading the already-packaged,
  local catalog as deterministic test input is in scope.
- Regional instance availability or launch capacity.
- Confidence-trend collection and caching.
- Applying a recommendation to an instance.

Those systems may have their own tests, but they must not be mixed into the
recommendation scenarios in this document.

## 2. Offline scenario harness

### 2.1 Harness contract

Implement or consolidate a reusable helper with this conceptual interface:

```python
def run_recommendation_scenario(
    *,
    current: EC2CatalogEntry,
    candidates: tuple[EC2CatalogEntry, ...],
    normalized_metrics: dict[str, dict[str, float]] | None = None,
    signals: dict[str, bool] | None = None,
    metadata: dict[str, object] | None = None,
    attached_volumes: tuple[EbsInventory, ...] = (),
    warning_policy: PerformanceWarningPolicy | None = None,
    compute_policy: EC2ComputePolicy | None = None,
    scope_policy: EC2ScopePolicy | None = None,
    candidate_policy: EC2CandidatePolicy | None = None,
    min_monthly_savings: float = 0.0,
    candidate_limit: int = 10,
) -> dict[str, object]:
    ...
```

The implementation uses an in-memory SQLAlchemy database, inserts the inventory
and optional volume rows, injects a catalog whose `list_region()` returns the
current entry plus candidates, and invokes `get_recommendation()`.

The service does not discover volumes from `EbsInventory` rows: EBS evaluation
is driven by `metadata_json["attached_volume_count"]`, and provisioned-capacity
checks read `metadata_json["attached_volume_ids"]`. The harness must therefore
derive both fields from the `attached_volumes` argument automatically —
setting the count and the volume-id list whenever volumes are supplied — so a
scenario cannot silently skip EBS evaluation by inserting rows the service
never reads.

No AWS adapter should be constructed or mocked. If a recommendation scenario
attempts network access, the test harness is incorrectly scoped.

### 2.2 Catalog data-fidelity model

Recommendation scenarios use a hybrid model. Real catalog specifications are
the default; synthetic telemetry and narrowly scoped overrides create the
condition under test.

Every scenario declares one of these modes:

| Mode | Catalog data | Allowed use |
| --- | --- | --- |
| `REAL_CATALOG` | The complete `EC2CatalogEntry` is loaded from the packaged database and is not modified | Default for ordinary selection, compute, network, EBS, family, architecture, and preview scenarios |
| `REAL_CATALOG_PRICE_OVERRIDE` | All capability fields remain unchanged; only `monthly_usd` may be replaced | Exact savings floors, equal-price ties, deterministic ranking, or a case where current regional prices do not produce the required cheaper/more-expensive relationship |
| `SYNTHETIC_CAPABILITY_OVERRIDE` | Start from a real entry and replace only the capability fields named by the scenario | Missing/unknown evidence, exact hard-capacity equality when telemetry derivation is insufficient, or capability combinations the packaged catalog cannot represent |
| `FULLY_SYNTHETIC` | The whole entry is constructed in the test | Last resort for malformed/missing catalog evidence and module-level cases; the test must explain why no real entry can represent the condition |

Use the narrowest mode that can express the scenario. A test must not overwrite
vCPU, memory, architecture, CoreMark, network, EBS, ENI, EFA, or instance-store
fields merely to make expected output convenient.

The catalog fixture loads entries through `EC2InstanceCatalog` from the local
repository artifact (`maxops_pricing.db`, extracted from the versioned
`pricing_artifacts/maxops_pricing.db.xz`). It does not call AWS or an enrichment
job. Instance specifications are the global `us-east-1`-sourced records in
`ec2_instance_specs`; price selection remains regional and platform-specific.
Use `us-east-1` and `linux` as the default scenario price dimensions unless the
scenario explicitly tests another supported price dimension.

The fixture must assert that `maxops_pricing.db` exists before opening it with
SQLite. The failure message must point to
`./.venv/bin/python staging_pricing/unpack_pricing_db.py`; otherwise
`sqlite3.connect()` can silently create an empty, gitignored database and hide
the actual setup failure behind a later `no such table` error. Any CI job that
runs catalog-backed rightsizer tests must perform the unpack step first.

Conceptual helpers:

```python
def real_catalog_entry(
    instance_type: str,
    *,
    region: str = "us-east-1",
    platform: str = "linux",
) -> EC2CatalogEntry:
    entry = packaged_catalog.get(region, instance_type, platform)
    assert entry is not None
    assert entry.capability_source == "describe_instance_types"
    return entry


def with_test_price(entry: EC2CatalogEntry, monthly_usd: float) -> EC2CatalogEntry:
    # REAL_CATALOG_PRICE_OVERRIDE: no capability field may change.
    return dataclasses.replace(entry, monthly_usd=monthly_usd)
```

Required representative families include general-purpose, compute-optimized,
memory-optimized, burstable, and Graviton types. Named types are fixture
contracts: if a required type or its structured specification is missing from
the packaged database, the test fails instead of skipping. This turns catalog
coverage loss into a visible packaging regression.

Each parametrized case or traceability entry records its mode. Test output
should make the source visible, for example `REC-001[REAL_CATALOG]` or
`REC-016[REAL_CATALOG_PRICE_OVERRIDE]`.

### 2.3 Synthetic telemetry derived from real capacity

Inventory rows, normalized telemetry, signals, and attached-volume demand remain
synthetic because they describe the hypothetical workload. Whenever a scenario
tests a ratio or capacity boundary, derive the telemetry from the real target
capability instead of replacing that capability:

```python
network_in_p99 = target.network_baseline_mbps * desired_ratio
network_at_peak = target.network_reliable_max_mbps
ebs_iops_p99 = target.ebs_baseline_iops * desired_ratio
cpu_p99 = desired_projected_cpu * 100 * target.coremark / current.coremark
memory_p99 = desired_projected_memory * 100 * target.memory_mib / current.memory_mib
```

Use the same method for below/equal/above cases. Assert the prerequisite
capacity is present before calculating the input. Do not silently substitute a
made-up baseline or maximum into a `REAL_CATALOG` case.

Exact price comparisons are the exception: catalog prices cannot be expected to
differ by exactly one cent or tie. Those rows use
`REAL_CATALOG_PRICE_OVERRIDE`, preserving all real specification fields while
controlling only price.

Missing-evidence scenarios intentionally cannot use an unchanged real structured
entry. They use `SYNTHETIC_CAPABILITY_OVERRIDE` and set only the field under
test to `None` or the required boundary value. Their assertions must verify that
unrelated capability fields still equal the source catalog entry.

### 2.4 Neutral defaults

The scenario builder should start from a neutral candidate that does not create
unrelated warnings:

- Same architecture as the current instance.
- Ungated general-purpose family.
- Positive monthly savings.
- Structured capability source.
- No instance store and `instance_store_present=false`.
- Adequate ENI and EBS attachment limits.
- Known network baseline and reliable peak.
- Known EBS baseline and maximum.
- Low CPU, memory, network, and EBS demand.
- All allowance/exceeded signals collected and false.
- No attached volumes unless the scenario tests EBS.

Each scenario changes only the inputs named in its setup. This prevents a test
for one dimension from failing because an unrelated baseline is unknown.

### 2.5 Compact decision projection

Tests should assert a stable projection of the result rather than snapshotting
the full response:

```python
def decision_projection(result):
    recommendations = result.get("recommendations", [])
    return {
        "targets": [item["target_instance_type"] for item in recommendations],
        "savings": [item["monthly_savings"] for item in recommendations],
        "classifications": {
            item["target_instance_type"]: item["classification"]
            for item in recommendations
        },
        "reason_codes": {
            item["target_instance_type"]: item["reason_codes"]
            for item in recommendations
        },
        "warnings": {
            item["target_instance_type"]: [
                detail["code"] for detail in item["warning_details"]
            ]
            for item in recommendations
        },
        "tiers": {
            name: option["target_instance_type"] if option else None
            for name, option in result.get("tiers", {}).items()
            if name != "default"
        },
        "tier_default": result.get("tiers", {}).get("default"),
        "rejection_summary": result.get("rejection_summary", {}),
    }
```

Individual scenarios additionally assert only the evidence they are intended to
prove, such as `projected_util`, `binding_dimension`, network ratios, or
`performance_change_pct`. Do not snapshot timestamps, disclosure text, the full
risk object, or the complete evidence envelope.

### 2.6 Mock-UI fixture projection

The catalog-backed fixture pool also supplies the self-contained mock UI in
`ec2_rightsizer_ui_mock.html`. Keep UI display records separate from pytest
implementation details: the UI consumes a machine-readable `INSTANCE_GROUPS`
projection, not test functions or an in-memory SQLAlchemy session.
Its compact `MOCK_CATALOG` rows are generated from the packaged database and
validated field-for-field by the named recommendation scenario suite.

Provide at least three representative instances for each UI behavior group:

1. ACTIONABLE clean resize.
2. CONDITIONAL network review.
3. OPPORTUNITY Graviton migration.
4. DEFERRED managed resource (ASG, ECS, and Kubernetes/EKS).
5. CONDITIONAL thin telemetry.
6. DEFERRED instance-store review.

The minimum mock data set is therefore eighteen instances. Each UI record keeps
the source scenario ID or behavior template, catalog fidelity mode, region,
platform, current/target types, real vCPU and memory labels, classification,
savings, risk, warnings, telemetry summary, tier projection, and coverage. UI
trend points may remain seeded synthetic data. The rail groups records by
behavior and derives all counts from the data source.

When a packaged-catalog fixture changes, update the UI projection in the same
change and assert that every UI current/target type exists in the catalog. Do
not copy arbitrary EC2-looking names into the mock. Exact mock savings may use
`REAL_CATALOG_PRICE_OVERRIDE`, but the record must disclose that mode.

## 3. Required recommendation scenarios

The scenario ID and specification columns form the required traceability map.
Every row must be represented by a test or an explicit parametrized case.

### Equality convention

Most boundaries in this plan are conservative at equality: a ratio exactly at
0.40 escalates to MEDIUM, demand exactly at baseline triggers the
sustained-above-baseline path, and demand exactly at the reliable network peak
is a hard rejection. The 0.70 warning boundary is the deliberate exception: a
ratio of exactly 0.70 remains MEDIUM because HIGH uses `> 0.70`.

Verified against the shipped code (2026-07-14): the warning evaluators use
`ratio >= medium_ratio` for MEDIUM and `ratio > high_ratio` for HIGH in both
the network and EBS packages, matching `NET-002`/`NET-003` and
`EBS-005`/`EBS-006`. Also verified: tier gates use `projected_util <= ratio`
(equality qualifies, `TIER-001`–`003`); ENI and EBS attachment limits reject
only strictly-greater counts (equality passes, `NET-019`/`EBS-003`); the
network reliable peak rejects at `>=` (equality rejects, `NET-008`) while the
EBS reliable maximum rejects at `>` (equality passes, `EBS-007`) — that
network/EBS asymmetry at the hard ceiling is shipped behavior and the rows
assert it as such; and the savings filter includes equality with
`min_monthly_savings` (`REC-004`). Once these rows land, the equality
semantics are frozen — changing them later is a versioned policy change.

### 3.1 Base selection, pricing, and candidate eligibility

| ID | Spec | Setup | Required result |
| --- | --- | --- | --- |
| `REC-001` | §11, §17 | Cheaper compatible target; all dimensions low and known | Target is returned as `ACTIONABLE` with correct monthly/yearly savings |
| `REC-002` | §11 | Target price equals current price | Target is absent because savings are not positive |
| `REC-003` | §11 | Target is more expensive | Target is absent |
| `REC-004` | §11 | Savings just below, exactly at, and just above `min_monthly_savings` | Below is excluded; equality and above are included provided savings also meet the raw $0.01 floor (use `min_monthly_savings >= 0.01` in these cases so the two thresholds do not interact) |
| `REC-005` | §1 | Cheaper same-architecture target in a different family | Target is evaluated normally and records `family_changed=true` |
| `REC-006` | §1 | Architectures do not overlap | Target is absent; rejection tally contains `ARCHITECTURE_INCOMPATIBLE` |
| `REC-007` | §1 | Current or target architecture evidence is empty | Target is absent as `ARCHITECTURE_INCOMPATIBLE` |
| `REC-008` | §1 | General-purpose current with burstable or specialized target | Target is absent as `FAMILY_NOT_ELIGIBLE` |
| `REC-009` | §1 | Current and target share the same gated family class | Target remains eligible when architecture also overlaps |
| `REC-010` | §11, §25.7 | More eligible candidates than `candidate_limit` | Output length respects the limit and retained candidates remain savings-ranked |
| `REC-011` | §11, §25.7 | Two candidates have equal savings | Tie breaks deterministically by target instance type |
| `REC-012` | §11 | Current type is absent from the catalog | No recommendations; blocking reason is `CURRENT_INSTANCE_PRICING_OR_SPEC_MISSING` |
| `REC-013` | §11, §25.7 | `candidate_limit` of 1 and of fewer than the number of qualifying tiers (the request contract requires `candidate_limit >= 1`; do not test 0) | Output never exceeds the limit. The legacy Balanced target holds the reserved slot, remaining slots fill by savings, and tiers select only from the returned set — so with `candidate_limit=1` the list is the Balanced target alone, Balanced *and* Aggressive both reference it (a Balanced qualifier's `projected_util <= 0.70` always also satisfies the `<= 0.85` Aggressive gate), and only Conservative may be null (unless the target's `projected_util <= 0.55`) |
| `REC-014` | §11 | Multiple candidates, each rejected for a different hard reason | `recommendations` is empty; `rejection_summary` tallies every rejection code with correct counts |
| `REC-015` | §25.3, §25.7 | More high-savings candidates than `candidate_limit`, all qualifying only for Aggressive, plus one lower-savings candidate that is the sole Balanced qualifier | The Balanced qualifier keeps its reserved slot, the list still respects `candidate_limit`, and the Balanced tier selects it |
| `REC-016` | §11 | Targets priced so raw monthly savings is just below $0.01 (e.g. $0.009), exactly $0.01, and just above $0.01. The exactly-at case must use a float-hazard price pair such as current $10.00 / target $9.99 (whose float subtraction yields 0.009999999999999787) to prove the comparison is decimal-safe | Raw monthly savings must be at least one cent: just-below is excluded — even though $0.009 would display as `0.01` after rounding — while exactly-at and just-above are included |
| `REC-017` | §1 | A family class removed from `EC2CandidatePolicy.gated_family_classes` via the candidate-policy override | A target in that class becomes eligible and is evaluated normally; with the default policy the same target is rejected as `FAMILY_NOT_ELIGIBLE` |

### 3.2 Compute, memory, and missing metrics

| ID | Spec | Setup | Required result |
| --- | --- | --- | --- |
| `CMP-001` | §19, §25.2 | CPU p99 and CoreMark are present | `projected_cpu_util = cpu_p99 / 100 * current_coremark / target_coremark` |
| `CMP-002` | §19, §25.2 | Either CoreMark is unavailable but vCPU values exist | CPU projection uses the vCPU ratio; recommendation is not blocked solely by missing CoreMark |
| `CMP-003` | §19 | Memory p99 is present | Estimated used memory and target memory projection use current memory times p99 |
| `CMP-004` | §19 | Memory metric absent; target retains current memory | Target remains eligible and includes `MEMORY_METRIC_UNAVAILABLE_CURRENT_CAPACITY_RETAINED` |
| `CMP-005` | §19 | Memory metric absent; target has less memory | Target is absent as `MEMORY_REQUIREMENT_NOT_MET` |
| `CMP-006` | §19 | CPU metric absent; target retains current CoreMark or vCPU capacity | Target remains eligible; CPU is excluded from projected utilization and binding |
| `CMP-007` | §19 | CPU metric absent; target reduces comparable CPU capacity | Target is absent as `CPU_REQUIREMENT_NOT_MET` |
| `CMP-008` | §19, §25.3 | CPU and memory metrics both absent; target retains both capacities | Candidate remains in `recommendations`; both projections and `projected_util` are null; all tiers and default are null |
| `CMP-009` | §25.2 | One metric is absent and the other is present | Tiering and binding use only the measured dimension; the fallback dimension is not fabricated as 100% |
| `CMP-010` | §25.5 | Target CoreMark exceeds current | Ratio is greater than 1 and `performance_change_pct` is positive |
| `CMP-011` | §25.5 | Target CoreMark is below current | Ratio is below 1 and `performance_change_pct` is negative without affecting eligibility |
| `CMP-012` | §25.5 | Either CoreMark is null or current CoreMark is zero | Performance ratio/change are unavailable and never block the recommendation |

### 3.3 Network behavior

Inbound and outbound values must remain separate in every network scenario. Use
the larger directional ratio as the verdict driver; never sum directions.

| ID | Spec | Setup | Required result |
| --- | --- | --- | --- |
| `NET-001` | §6.1, §13 | Reliable baseline; maximum directional ratio below 0.40 | Network risk is `LOW`; no ratio warning |
| `NET-002` | §6.1, §11, §13 | Ratio exactly 0.40 and between 0.40 and 0.70 | Network risk is `MEDIUM` with `NETWORK_USAGE_REVIEW_REQUIRED`; candidate is not `ACTIONABLE` |
| `NET-003` | §6.1, §13 | Ratio exactly 0.70 | Network risk remains `MEDIUM` because HIGH uses `> 0.70` |
| `NET-004` | §6.1, §11, §13 | Ratio just above 0.70 but below baseline | Network risk is `HIGH` with `HIGH_NETWORK_USAGE_REVIEW_REQUIRED`; candidate is not `ACTIONABLE` |
| `NET-005` | §6.1, §14 | Demand exactly at baseline and below reliable peak | `NETWORK_SUSTAINED_ABOVE_BASELINE`; candidate is `CONDITIONAL` |
| `NET-006` | §6.1 | Demand between baseline and reliable peak | Candidate remains visible as `CONDITIONAL` |
| `NET-007` | §5, §6.1 | Demand just below reliable peak | No hard rejection from peak capacity |
| `NET-008` | §5, §6.1 | Demand exactly at reliable peak | Candidate is rejected with `NETWORK_RELIABLE_MAX_EXCEEDED` |
| `NET-009` | §5, §6.1 | Demand above reliable peak | Candidate is rejected with `NETWORK_RELIABLE_MAX_EXCEEDED` |
| `NET-010` | §6.3, §14 | Published baseline absent; numeric peak and family denominator available. Include variants where the catalog also contains same-letter-class entries from *other* families and `.metal` sizes of the target family | Effective baseline follows the configured formula/floor; the denominator comes only from the target's exact family (never pooled by bare letter class) and excludes `.metal` sizes; evidence says `ASSUMED_BASELINE`; warning includes `NETWORK_BASELINE_ASSUMED`; result is never `ACTIONABLE` |
| `NET-011` | §6.2 | Baseline absent and target is burst/up-to; assumed-baseline derivation prevented (no numeric peak/family denominator inputs, or assumed-baseline policy disabled) — otherwise the assumed-baseline path (`NET-010`) fires first | Candidate is `CONDITIONAL` with burst/unknown-baseline warnings |
| `NET-012` | §6.2 | No numeric baseline, peak, or comparable qualitative capability | Candidate is `CONDITIONAL` with explicit unknown-capability evidence |
| `NET-013` | §6.1, §19 | High inbound and low outbound, then the reverse | The high direction drives risk; evidence preserves both ratios |
| `NET-014` | §6.1, §14 | Non-default bandwidth weighting | Numeric bands are suppressed; `NON_DEFAULT_NETWORK_BANDWIDTH_WEIGHTING_REQUIRES_REVIEW` is emitted |
| `NET-015` | §7 | Bandwidth allowance event and target capacity is lower/equal/unknown | Candidate is rejected with `NETWORK_ALLOWANCE_EVENTS_WITHOUT_CAPACITY_INCREASE` |
| `NET-016` | §7 | Bandwidth allowance event and target capacity increases | No allowance hard failure; HIGH warning remains |
| `NET-017` | §7 | PPS or conntrack allowance event | Corresponding warning is present and network risk is HIGH |
| `NET-018` | §7, §13 | Allowance metrics absent with policy requirement disabled/enabled | Disabled does not alone block ACTIONABLE; enabled forces review warning/classification |
| `NET-019` | §5 | Attached ENIs equal target limit, then exceed it | Equality passes; exceeding rejects with `TARGET_ENI_LIMIT_TOO_LOW` |
| `NET-020` | §5 | EFA required with support true, false, and unknown | True passes; false rejects; unknown cannot be ACTIONABLE |
| `NET-021` | §6.1, §7, §14 | Non-default bandwidth weighting combined with a bandwidth allowance event and no target capacity increase | Numeric-band suppression (`NET-014`) does not suppress allowance hard checks: candidate is still rejected with `NETWORK_ALLOWANCE_EVENTS_WITHOUT_CAPACITY_INCREASE` alongside the weighting review warning |

### 3.4 EBS behavior

Attach at least one synthetic EBS volume in every scenario below except
`EBS-001`; otherwise the service correctly marks storage as not applicable and
does not invoke EBS evaluation. Note that attachment is metadata-driven — the
harness must set `attached_volume_count` (and `attached_volume_ids` for
provisioned-capacity scenarios such as `EBS-009`) per §2.1; database volume
rows alone do not trigger EBS evaluation.

| ID | Spec | Setup | Required result |
| --- | --- | --- | --- |
| `EBS-001` | §8, §9 | No attached volumes | Storage evaluation is not applicable and does not create warnings |
| `EBS-002` | §8 | Target explicitly does not support EBS | Candidate is rejected with `TARGET_DOES_NOT_SUPPORT_EBS` |
| `EBS-003` | §8 | Attached volume count equal to, then greater than effective limit | Equality passes; greater rejects with `TARGET_EBS_ATTACHMENT_LIMIT_TOO_LOW` |
| `EBS-004` | §9.1, §13 | IOPS/throughput ratio below 0.40 | Storage risk is LOW |
| `EBS-005` | §9.1, §11, §13 | Ratio from exactly 0.40 through exactly 0.70 | Storage risk is MEDIUM with `EBS_USAGE_REVIEW_REQUIRED`; candidate is not `ACTIONABLE` |
| `EBS-006` | §9.1, §11, §13 | Ratio just above 0.70 | Storage risk is HIGH with `HIGH_EBS_USAGE_REVIEW_REQUIRED`; candidate is not `ACTIONABLE` |
| `EBS-007` | §8 | Observed IOPS or throughput exactly equals reliable maximum | Equality passes the hard maximum |
| `EBS-008` | §8 | Observed IOPS or throughput exceeds reliable maximum | Candidate is rejected with the matching maximum-exceeded code |
| `EBS-009` | §8 | Provisioned IOPS/throughput equals, then exceeds target maximum | Equality passes; greater rejects with the matching provisioned-capacity code |
| `EBS-010` | §9.3 | Baseline capability unknown | Candidate remains visible as `CONDITIONAL` with `EBS_BASELINE_CAPABILITY_UNKNOWN` |
| `EBS-011` | §10 | Exceeded/throttled signal without a capacity increase | Candidate is rejected with `EBS_EXCEEDED_EVENTS_WITHOUT_CAPACITY_INCREASE` |
| `EBS-012` | §10 | Queue, latency, or depleted burst-balance signal | Corresponding warning is emitted and storage risk is HIGH |
| `EBS-013` | §10 | Only one directional EBS component is present | Lower-bound demand is retained and `EBS_DIRECTIONAL_METRICS_INCOMPLETE` forces review |
| `EBS-014` | §10, §13 | Exceeded metrics absent with policy requirement disabled/enabled | Policy-disabled behavior is unchanged; enabled adds required review evidence |

Spec §9.2 (burst-only EBS baseline) is currently **not applicable at this
service boundary**: the catalog maps EBS capacity only to BASELINE or UNKNOWN,
so a burst-only input cannot be constructed through `get_recommendation()`.
It is covered by `MOD-001` in Section 3.9 instead.

### 3.5 Scope and deferral

Each deferral scenario must assert that candidate generation did not run and that
`recommendations` is empty.

| ID | Spec | Setup | Required result |
| --- | --- | --- | --- |
| `SCOPE-001` | §24 | ASG management context | `DEFERRED / MANAGED_BY_ASG` |
| `SCOPE-002` | §24 | ECS management context | `DEFERRED / MANAGED_BY_ECS` |
| `SCOPE-003` | §24 | Kubernetes/EKS management context | `DEFERRED / MANAGED_BY_KUBERNETES` |
| `SCOPE-004` | §24 | Dedicated or host tenancy | `DEFERRED / UNSUPPORTED_TENANCY` |
| `SCOPE-005` | §24 | Spot or other non-standard lifecycle | `DEFERRED / UNSUPPORTED_LIFECYCLE` |
| `SCOPE-006` | §24 | Instance store known present or capability unresolved | Default policy returns `DEFERRED / INSTANCE_STORE_USAGE_UNKNOWN` |
| `SCOPE-007` | §24 | Instance store explicitly absent | Recommendation evaluation proceeds |
| `SCOPE-008` | §24 | Unknown instance store with request override | Evaluation proceeds; compatibility risk is HIGH; candidates are at most `CONDITIONAL` |

### 3.6 Tier assembly and presentation data

| ID | Spec | Setup | Required result |
| --- | --- | --- | --- |
| `TIER-001` | §25.1–§25.3 | Projected utilization just below, equal to, and just above 0.55 | Below/equal qualify for Conservative; above does not |
| `TIER-002` | §25.1–§25.3 | Just below, equal to, and just above 0.70 | Below/equal qualify for Balanced; above does not |
| `TIER-003` | §25.1–§25.3 | Just below, equal to, and just above 0.85 | Below/equal qualify for Aggressive; above is excluded during generation |
| `TIER-004` | §25.3 | Three candidates span the three ratios | Three distinct targets are selected; Balanced is default |
| `TIER-005` | §25.4 | All present tiers select one target | Candidate has all satisfied-tier labels; response exposes identical target references for collapse |
| `TIER-006` | §25.3 | No cost-saving candidate meets Conservative | Conservative is null; remaining tiers are selected normally |
| `TIER-007` | §25.3 | CPU and memory projections both unavailable after floor checks | All tiers/default are null while candidate remains savings-ranked |
| `TIER-008` | §25.4 | Aggressive target has HIGH network/EBS risk; Conservative target is clean | Aggressive is `CONDITIONAL`; Conservative is independently `ACTIONABLE` |
| `TIER-009` | §25.7 | Multiple candidates meet one tier | Tier chooses the greatest monthly savings, then target name for an exact tie |
| `TIER-010` | §25.7 | Default policy with the same inputs as the pre-tiering 0.70 algorithm | Balanced target exactly matches the legacy selection with metrics present and with memory absent. Migration anchor: remove this row and generated invariant 9 when the legacy 0.70 path is deleted, rather than letting them constrain future tier changes |
| `TIER-011` | §25.6 | CPU, memory, network, and storage ratios vary | `binding_dimension` names the highest measured limiting ratio; absent dimensions are excluded |
| `TIER-012` | §25.7 | Every non-null tier option | Its target exists in `recommendations` and its projection, savings, classification, risk, performance, and satisfied-tier data match that candidate |

### 3.7 Classification matrix

The strict LOW/LOW rule (§11) is a tunable policy, not a fixed law. Encode it
as the named scenarios below — easy to amend when the policy changes — not as
a generated invariant, which would claim universality.

Classification consumes only the CPU/memory pass status and the network and
storage evaluations (`classify_recommendation`), per spec §11. Telemetry,
compute, memory, and compatibility risk are assembled into the risk object
*after* classification and are disclosure only — they never change the
classification (the sole exception is the scope-override compatibility cap of
§24, covered by `SCOPE-008`). Scenarios below therefore scope "risk" to the
network and storage evaluations only.

| ID | Spec | Setup | Required result |
| --- | --- | --- | --- |
| `CLS-001` | §11 | All hard statuses PASS; network and storage risk both LOW; no blocking warnings | `ACTIONABLE` |
| `CLS-002` | §11 | Network risk MEDIUM; storage risk LOW; hard statuses PASS | `CONDITIONAL` — MEDIUM network/storage risk never qualifies as ACTIONABLE |
| `CLS-003` | §11 | Storage risk MEDIUM; network risk LOW; hard statuses PASS | `CONDITIONAL` |
| `CLS-004` | §11 | Network or storage risk HIGH; the other LOW; hard statuses PASS | `CONDITIONAL` |
| `CLS-005` | §11 | Network or storage hard status UNKNOWN; both risks LOW | Not `ACTIONABLE` |
| `CLS-006` | §11 | Blocking warning present; both risks LOW; hard statuses PASS | `CONDITIONAL` |
| `CLS-007` | §11, §19 | CPU metric absent (telemetry risk HIGH); target retains comparable capacity; network and storage LOW/PASS | Candidate is `ACTIONABLE`; the risk object discloses HIGH telemetry risk. Missing telemetry never blocks ACTIONABLE because the §19 capacity floor already requires the target to retain the unmeasured capacity |

### 3.8 Telemetry sufficiency

| ID | Spec | Setup | Required result |
| --- | --- | --- | --- |
| `TEL-001` | §22, §24 | Short observation history (thin telemetry) with otherwise-clean metrics, compared against the identical scenario with full history | Disclosure evidence reflects the short history; classification, tiers, and targets are identical to the full-history run; `INSUFFICIENT_DATA` is never returned (it remains reserved per §24) |

### 3.9 Companion module-level tests

These scenarios cover spec clauses that cannot be exercised through the
`get_recommendation()` boundary defined in Section 1. They test the underlying
modules directly and are the explicit, documented exception to that boundary —
list them here rather than silently omitting the spec clause from
traceability.

| ID | Spec | Boundary | Setup | Required result |
| --- | --- | --- | --- | --- |
| `MOD-001` | §9.2 | EBS warnings package (`rightsizers/ec2/ec2_rightsizer/warnings`) | Burst-only EBS baseline capability — not representable in the service catalog, which maps EBS capacity only to BASELINE or UNKNOWN | Burst-capacity warning is emitted and the evaluation routes to review. Promote this to a service-level `EBS-` scenario when the catalog models burst-only capacity |

### 3.10 Savings previews (spec §27)

For these scenarios, extend the decision projection with a `previews` key
mapping preview kind to target instance type.

| ID | Spec | Setup | Required result |
| --- | --- | --- | --- |
| `PRV-001` | §27.2 | Memory metric absent; a memory-reducing candidate passes every other gate (architecture, family, savings floors, CPU projection, network/EBS hard constraints) | `savings_previews` contains one `MEMORY_METRIC_MISSING_DOWNSIZE` entry for the highest-savings such candidate with `blockers=["MEMORY_METRIC_NOT_ENABLED"]`; the candidate does not appear in `recommendations`; `rejection_summary` still tallies `MEMORY_REQUIREMENT_NOT_MET` |
| `PRV-002` | §27.2 | Memory metric absent; the memory-reducing candidate fails a network or EBS hard constraint | No memory preview — previews never bypass non-memory hard constraints |
| `PRV-003` | §27.2 | Memory metric present; a candidate reduces memory below measured need | No memory preview; the candidate is an ordinary `MEMORY_REQUIREMENT_NOT_MET` rejection |
| `PRV-004` | §27.3 | x86-only current instance; region-priced arm64 analogue retains vCPU and memory with positive savings | `savings_previews` contains one `GRAVITON_MIGRATION` entry classified `OPPORTUNITY` with `blockers=["ARCHITECTURE_MIGRATION_REQUIRED"]`; `recommendations` and `ARCHITECTURE_INCOMPATIBLE` tallies unchanged |
| `PRV-005` | §27.3 | Only arm64 candidates that reduce vCPU or memory, or that fail hard constraints | No Graviton preview — cross-architecture selection is capacity-retaining only |
| `PRV-006` | §27.3 | arm64 candidate exists only in a gated family class the current instance does not share | No Graviton preview — the §1 family gate is not bypassed |
| `PRV-007` | §27.1 | Both preview kinds qualify simultaneously; multiple qualifying candidates per kind | At most one preview per kind, each the highest-savings qualifier; previews never affect `recommendations`, tiers, ranks, or `candidate_limit` |
| `PRV-008` | §27.1 | Preview policy flags disabled | `savings_previews` is empty; everything else identical to the flags-enabled run except the previews |

## 4. Generated invariants

In addition to named examples, use bounded generated cases or parametrized input
grids to assert properties that must hold for all recommendations:

1. Every returned recommendation has strictly positive savings and satisfies the
   request's minimum-savings threshold. Generate prices at cent precision so
   "strictly positive" and the two-decimal display rounding agree; `REC-016`
   separately pins the decimal-safe one-cent boundary.
2. Recommendations are ordered by descending monthly savings and then target
   instance type; the same input always returns the same order.
3. Architecture-incompatible and family-gated candidates never appear.
4. A candidate with a failed network or EBS hard constraint never appears.
5. When CPU or memory telemetry is absent, a candidate cannot reduce the
   corresponding comparable current capacity.
6. An unmeasured CPU or memory dimension never contributes a fabricated ratio to
   `projected_util` or `binding_dimension`.
7. For a fixed candidate, Conservative eligibility implies Balanced and
   Aggressive eligibility; Balanced implies Aggressive.
8. Every non-null tier references a returned recommendation and uses that
   candidate's own classification and evidence.
9. With the default policy, the Balanced target equals the target selected by the
   legacy 0.70 compute/memory gate, including under candidate-limit pressure.
   (Migration anchor — remove together with `TIER-010` when the legacy path is
   deleted.)
10. An assumed network baseline never yields `ACTIONABLE` and never replaces the
    reliable peak used by hard constraints.
11. Raising observed CPU, memory, network, or EBS demand — while holding all
    other inputs fixed, including which dimensions are measured — cannot improve
    the candidate outcome. "Improve" is defined by this partial order, compared
    componentwise (any component becoming better while none becomes worse is an
    improvement):
    - Risk levels: `LOW < MEDIUM < HIGH` (lower is better).
    - Tier eligibility: the set of satisfied tiers, compared by inclusion — a
      strict superset is better; incomparable sets are neither better nor worse.
    - Outcome: `absent from recommendations / REJECTED < CONDITIONAL <
      ACTIONABLE` (higher is better).
    Changing a dimension from unmeasured to measured is a different
    transformation and is outside this invariant.
12. Increasing a reliable capacity while holding demand fixed cannot create a new
    hard-capacity failure.
13. Candidate limits never change the relative order of retained candidates.
14. `performance_change_pct` is additive evidence only and never changes
    candidate eligibility or tier placement.
15. `savings_previews` never influences
    `recommendations`: for any input, the recommendations list, tiers, ranks,
    classifications, and rejection tallies are identical with previews enabled
    and disabled; no preview target appears in `recommendations`; every
    preview has positive savings meeting the one-cent floor and a non-empty
    `blockers` list; no preview is `ACTIONABLE` or `CONDITIONAL`.

Generated values must be finite and non-negative. Concentrate generation around
policy boundaries and use pairwise combinations across dimensions; do not build
an unmaintainable full Cartesian product.

## 5. Coverage, mutation, and maintenance

### 5.1 Coverage standard

Recommendation tests should achieve branch and compound-condition coverage for:

- Candidate filtering and scope gates.
- Missing-metric capacity floors.
- Network and EBS hard constraints.
- Network and EBS warning bands.
- Classification and risk escalation.
- Tier selection, defaulting, deduplication, and ranking.

Line coverage alone is insufficient. Boundary rows in the scenario matrix are
required even when a generated test happens to execute the same line.

### 5.2 Mutation targets

Mutation testing should specifically challenge:

- `<`, `<=`, `>`, and `>=` at savings, tier, warning, baseline, and reliable-max
  boundaries.
- `and`/`or` conditions in architecture and family gates.
- Missing-metric fallbacks and capacity comparisons.
- Savings sort direction and deterministic tie-breakers.
- `max` versus sum for directional network demand.
- Assumed-baseline versus reliable-peak wiring.

A surviving mutation in one of these comparisons indicates a missing scenario or
assertion, even if coverage is nominally complete.

Scope mutation runs to the recommendation modules only (constraints, warnings,
risk, classification, tier selection) against the named suite, and run them as
a periodic job rather than a per-commit CI gate — repository-wide mutation runs
are too slow to survive in practice.

### 5.3 Change procedure

When recommendation behavior changes:

1. Update the product specification first.
2. Add or amend a scenario row here with a stable ID.
3. Add the corresponding test and its compact assertions.
4. Update generated invariants only when the product contract changes.
5. Keep historical regression scenarios unless the old behavior is explicitly
   removed from the specification.

## 6. Safe execution

First install the versioned pricing artifact into the local, gitignored
database. This is also a required setup step in any CI job that runs the
catalog-backed suite:

```bash
.venv/bin/python staging_pricing/unpack_pricing_db.py
```

The unpack helper refuses to replace an existing database unless explicitly
forced, so this command is required only on a fresh checkout or clean runner.
Then run only explicitly named unit files. The current recommendation suites
are:

```bash
.venv/bin/python -m pytest tests/test_ec2_rightsizer_v1.py -q
.venv/bin/python -m pytest tests/test_ec2_rightsizer_scenarios.py -q
```

Never run broad or keyword discovery such as `pytest tests/`,
`pytest -k rightsizer`, or repository-wide coverage discovery. Files under
`tests/integration/` can provision real, billable AWS resources.

## 7. Acceptance criteria

The offline recommendation test suite is complete when:

- Every service-boundary scenario declares one catalog fidelity mode from
  Section 2.2. Direct evaluator scenarios such as `CLS-001` and `MOD-001`
  declare `NOT_APPLICABLE` instead of inventing an instance.
- `REAL_CATALOG` is the default. Capability overrides are limited to fields
  named by the scenario, and a regression assertion proves all other
  capability fields still match the packaged entry.
- A catalog-conformance test verifies the packaged database exists, contains a
  non-empty global `ec2_instance_specs` table, and resolves every named
  representative instance type with `capability_source =
  "describe_instance_types"`. Missing fixtures fail; they are never skipped.
- Catalog-backed tests check for the extracted database before connecting and
  report the unpack command when it is absent; CI installs the bundled artifact
  before running those tests.
- Ratio and capacity boundaries use telemetry derived from the loaded target
  specification. Price-boundary scenarios preserve real specifications and
  override only `monthly_usd`.
- Every scenario ID in this document maps to at least one explicitly named test
  or parametrized case.
- Every applicable EC2 rightsizer specification clause maps back to a scenario
  ID in this document.
- All boundary scenarios assert below, equality, and above behavior using the
  active policy values.
- The generated invariants pass across a deterministic seed set.
- Critical comparison mutations are killed by the named suite.
- Tests require no AWS credentials, network access, running instances, or live
  customer/production inventory. The versioned local pricing/specification
  artifact is permitted and required test data.
- Results are deterministic across repeated local and CI runs.
- Existing recommendation tests remain valid while consolidation proceeds.
