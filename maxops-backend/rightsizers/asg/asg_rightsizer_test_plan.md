# ASG Rightsizer Offline Test Plan

Status: authoritative V2 test guide for `asg_rightsizer_spec.md`. The existing
V1 matrix remains the mandatory flag-off regression baseline.

This plan tests exact ASG target-instance/min/desired recommendation behavior
without creating, updating, or querying production Auto Scaling Groups.
Collection adapters are tested with synthetic AWS responses in separate named
unit files.

Sections 1–10 retain the implemented V1 scenario IDs and safe-execution record.
Run those scenarios with `instance_optimization_enabled=False`; their existing
pytest node mappings must remain unchanged. Section 11 adds the normative V2
matrix for flag-on combined optimization and overrides V1-only assertions when
the target type changes. This versioning preserves earlier review citations
instead of silently assigning their IDs new meanings.

## 1. Test boundaries

### 1.1 Recommendation boundary

The primary system under test is:

```python
ASGRightsizer.get_recommendation(inventory_id)
```

Recommendation scenarios exercise:

- Scope gating.
- Telemetry sufficiency.
- Required-capacity calculation.
- Availability floors.
- Tier assembly and deduplication.
- Pricing and savings.
- Operational warnings, risk, and classification.
- Missing-memory preview behavior.
- Response evidence and deterministic ordering.
- EC2 target candidate eligibility and rejection evidence.
- Instance-only and combined type/count optimization.

They do not call AWS, CloudWatch, or Auto Scaling APIs.

### 1.2 Collection boundary

Separate adapter/scan tests exercise:

- Paginated ASG discovery.
- The exact allowed `GetMetricData` queries.
- `NextToken` pagination.
- Period/statistic/dimension selection.
- Timestamp joining and missing-point behavior.
- Memory discovery reuse.
- Window normalization and persistence.

Use fake clients and captured payloads only. No AWS credentials or network are
permitted.

### 1.3 Excluded systems

- Applying min/desired changes.
- IAM policy deployment.
- Live regional capacity.
- ASG creation or deletion.
- Integration tests.
- Existing legacy ASG check output.
- Frontend rendering.
- Applying a target instance type to a launch definition.

## 2. Offline scenario harness

Implement a reusable helper:

```python
def run_asg_recommendation_scenario(
    *,
    current_min: int,
    current_desired: int,
    current_max: int,
    instance_type: str = "m6i.large",
    region: str = "us-east-1",
    platform: str = "linux",
    availability_zones: tuple[str, ...] = (
        "us-east-1a",
        "us-east-1b",
        "us-east-1c",
    ),
    normalized_windows: dict[str, object] | None = None,
    memory_available: bool = True,
    target_instance_types: tuple[str, ...] | None = None,
    metadata: dict[str, object] | None = None,
    capacity_policy: ASGCapacityPolicy | None = None,
    scope_policy: ASGScopePolicy | None = None,
    candidate_policy: EC2CandidatePolicy | None = None,
    instance_optimization_enabled: bool = False,
    candidate_limit: int = 10,
    min_monthly_savings: float = 0.0,
) -> dict[str, object]:
    ...
```

The helper:

- Creates an in-memory SQLAlchemy database.
- Inserts one `AsgInventory` row.
- Injects a catalog backed by a real packaged EC2 entry by default.
- Uses multiple real catalog entries when instance optimization is enabled.
- Writes already-normalized 14d/30d/60d telemetry into `metadata_json`.
- Invokes the real service.
- Fails if the service attempts to construct an AWS adapter.

### 2.1 Catalog fidelity

Use a real catalog entry from `maxops_pricing.db` for instance specs and
regional price. Exact price-boundary tests may replace only `monthly_usd` and
must label the fixture `REAL_CATALOG_PRICE_OVERRIDE`.

Before connecting with SQLite, assert that the database path is a file. The
failure message points to:

```bash
./.venv/bin/python staging_pricing/unpack_pricing_db.py
```

This prevents `sqlite3.connect()` from silently creating an empty gitignored
database on a fresh clone.

### 2.2 Synthetic telemetry

Workload telemetry is deterministic and synthetic. Build timestamped load
series first, then run the production normalizer where a test targets
normalization. Recommendation-only tests may provide persisted summaries that
could have resulted from those series.

Neutral fixtures use:

```text
current min=6, desired=10, max=20
three Availability Zones
CPU and memory present
>= 7 observed days
pairing coverage=1.0
no scope blockers
CPU target tracking
no activity warnings
```

Each scenario changes only named inputs.

All existing CAP/BND/AZ/TIER/COST/TEL/PRV/SCOPE/CLS/ENV/MET/INV/TRD scenarios
set `instance_optimization_enabled=False`. This is explicit even if the
production policy default becomes true, so the V1 baseline cannot drift with a
default change.

Use these concrete V1 anchors instead of leaving qualitative rows to fixture
interpretation:

| Scenario | Exact input | Exact expected decision |
| --- | --- | --- |
| CAP-001 | current `(min=6, desired=10, max=20)`; tier `(p50,p99)` requirements Conservative `(4,9)`, Balanced `(3,7)`, Aggressive `(3,6)`; three AZs | targets `(4,9,20)`, `(3,7,20)`, `(3,6,20)` and Balanced default |
| CAP-007 | current desired `6`; Balanced raw p99 requirement `7` | no increase and no Balanced recommendation |
| CAP-008 | current `(6,10,20)`; calculated `(min=5, desired=10)` | no option and no monetized saving |
| F2 clamp fixture | current `(6,10,20)`; calculated `(min=7, desired=8)` | returned `(6,8,20)` |
| AZ-002 | three unique AZs; calculated minimum `1`, desired `5` | target `(3,5,current_max)` |
| TIER-002 | all tiers calculate `(3,5,20)` | one returned configuration labeled with all three tiers |
| TEL-004 | CPU observed days `6.999`, policy minimum `7.0` | INSUFFICIENT_DATA and exact `messages[]` value below |
| PRV-001 | current `(6,10,20)`, same type, memory absent, CPU Balanced `(3,7)` | no normal recommendation; memory preview Balanced `(3,7,20)` |
| COST-005 | raw current/target cost difference exactly `Decimal("0.01")` | included |

Rows that test below/equal/above boundaries use the exact values already listed
in their boundary table. A scenario may override an anchor only when the row
states the replacement values explicitly.

### 2.3 Compact projection

Avoid full-response snapshots. Assert a stable projection:

```python
def decision_projection(result):
    return {
        "classification": result.get("classification"),
        "targets": [
            (
                item["target_min_size"],
                item["target_desired_capacity"],
                item["target_max_size"],
            )
            for item in result.get("recommendations", [])
        ],
        "savings": [
            item["monthly_savings"]
            for item in result.get("recommendations", [])
        ],
        "tiers": {
            name: (
                option["target_min_size"],
                option["target_desired_capacity"],
            ) if option else None
            for name, option in result.get("tiers", {}).items()
            if name != "default"
        },
        "tier_default": result.get("tiers", {}).get("default"),
        "previews": {
            item["kind"]: {
                "classification": item["classification"],
                "blockers": tuple(item["blockers"]),
                "targets": {
                    tier: (
                        option["target_min_size"],
                        option["target_desired_capacity"],
                        option["target_max_size"],
                    ) if option else None
                    for tier, option in item["options"].items()
                    if tier != "default"
                },
                "default": item["options"]["default"],
            }
            for item in result.get("savings_previews", [])
        },
        "blocking_reasons": result.get("blocking_reasons", []),
        "deferred_reasons": result.get("deferred_reason_codes", []),
    }
```

Assert detailed evidence only in the scenario that owns it.

For V2 scenarios extend, rather than replace, that projection:

```python
def v2_decision_projection(result):
    base = decision_projection(result)
    base.update({
        "targets": [
            (
                item["target_instance_type"],
                item["target_min_size"],
                item["target_desired_capacity"],
                item["target_max_size"],
            )
            for item in result.get("recommendations", [])
        ],
        "optimization_kinds": [
            item["optimization_kind"]
            for item in result.get("recommendations", [])
        ],
        "tier_targets": {
            name: (
                option["target_instance_type"],
                option["target_min_size"],
                option["target_desired_capacity"],
                option["target_max_size"],
            ) if option else None
            for name, option in result.get("tiers", {}).items()
            if name != "default"
        },
        "rejection_summary": result.get("rejection_summary", {}),
        "previews": {
            item["kind"]: item.get("target_instance_type")
            for item in result.get("savings_previews", [])
        },
    })
    return base
```

Performance, capacity-basis, aggregate-capacity, and catalog evidence are
asserted in their owning cases rather than snapshotted globally.

## 3. Required scenario matrix

### 3.1 Capacity calculation

| ID | Scenario | Required assertions |
| --- | --- | --- |
| CAP-001 | Clean Balanced reduction | p50 drives min, p99 drives desired, max unchanged |
| CAP-002 | CPU binding | CPU required count and binding dimension are exact |
| CAP-003 | Memory binding | Memory produces the larger required count |
| CAP-004 | Equal CPU/memory pressure | deterministic binding tie rule |
| CAP-005 | Maximum across 14/30/60 | largest valid window requirement wins |
| CAP-006 | Missing one window | remaining valid windows decide |
| CAP-007 | Current desired below calculated need | no increase and no downsize option |
| CAP-008 | Min-only calculated reduction | not returned without desired savings |
| CAP-009 | Desired target bounded by target min | `min <= desired` always |
| CAP-010 | Current max preserved | every tier has byte-identical current max |

For per-timestamp arithmetic, include unequal group counts so tests would fail if
the implementation multiplied independent CPU and capacity percentiles.
Also pin the asymmetric clamp case: calculated minimum above current minimum,
calculated desired below current desired. The minimum stays current while the
desired-capacity reduction remains eligible.

### 3.2 Numeric boundaries

Test below, equal, and above every threshold:

| ID | Boundary |
| --- | --- |
| BND-001 | CPU/memory value 0 and 100 accepted; below 0 and above 100 rejected |
| BND-002 | In-service count 1 accepted; 0 insufficient |
| BND-003 | Pairing ratio 0.8999, 0.90, 0.9001 |
| BND-004 | Observed days 6.999, 7.0, 7.001 |
| BND-005 | Tier utilization 0.55, 0.70, 0.85 equality passes |
| BND-006 | One-cent savings 0.0099, 0.01, 0.0101 |
| BND-007 | `min_monthly_savings` below, equal, and above raw savings |

Nearest-rank p50/p99 tests include odd/even sample counts, duplicates, one
sample, and unsorted input.

### 3.3 Availability floor

| ID | Scenario | Required assertions |
| --- | --- | --- |
| AZ-001 | One AZ | floor is one |
| AZ-002 | Three AZs, calculated min one | target min is three |
| AZ-003 | Four AZs, desired calculation two | both min and desired become four |
| AZ-004 | Duplicate/empty AZ values | unique non-empty zones only |
| AZ-005 | No zones persisted | conservative fallback floor one plus warning |
| AZ-006 | Current capacity below AZ floor | no downsize; no hidden increase |

These tests do not call or model instance-type offering APIs.

### 3.4 Tier behavior

| ID | Scenario | Required assertions |
| --- | --- | --- |
| TIER-001 | Three distinct configurations | Conservative >= Balanced >= Aggressive |
| TIER-002 | All tiers collapse | one recommendation with three labels |
| TIER-003 | Conservative and Balanced collapse | shared reference; Aggressive distinct |
| TIER-004 | Conservative no savings | Conservative null; Balanced/Aggressive valid |
| TIER-005 | Aggressive only | default is null, never Aggressive |
| TIER-006 | Balanced present | default is Balanced |
| TIER-007 | No tier saves | all tiers and default null |
| TIER-008 | Tier reference classification | reference preserves the configuration classification |
| TIER-009 | Deterministic ties | stable configuration ordering |

### 3.5 Pricing and savings

| ID | Scenario | Required assertions |
| --- | --- | --- |
| COST-001 | Desired decreases by N | monthly savings = N times regional unit price |
| COST-002 | Min changes | no separate or double-counted savings |
| COST-003 | Max unchanged | max contributes no savings |
| COST-004 | Missing regional price/spec | blocking reason, no options |
| COST-005 | Exactly one cent | included using Decimal-from-string |
| COST-006 | Minimum savings equality | included |
| COST-007 | Display values | existing rounding convention; yearly consistent |
| COST-008 | Savings disclosure | initial desired delta and scaling-policy caveat present |

Include the float-hazard pair `$10.00` and `$9.99`.

### 3.6 Telemetry sufficiency and missing data

| ID | Scenario | Required result |
| --- | --- | --- |
| TEL-001 | CPU absent | INSUFFICIENT_DATA |
| TEL-002 | In-service absent | INSUFFICIENT_DATA plus group-metrics remediation |
| TEL-003 | Desired history absent, current desired known | CONDITIONAL option with reason and group-metrics remediation |
| TEL-004 | Short window | INSUFFICIENT_DATA plus required/observed-day message |
| TEL-005 | CPU/in-service pairing below threshold | INSUFFICIENT_DATA with CPU pairing code |
| TEL-006 | Memory unavailable, below pairing, or younger than seven days | preview only with the matching missing, pairing, or short-window blocker |
| TEL-007 | Missing timestamps | omitted, never zero |
| TEL-008 | One valid window | recommendation may proceed |
| TEL-009 | Non-finite values | removed and coverage reduced |
| TEL-010 | Current desired/in-service mismatch | CONDITIONAL; zero current in-service is INSUFFICIENT_DATA with `ASG_CURRENT_IN_SERVICE_CAPACITY_ZERO` |

TEL-002, TEL-003, and TEL-004 pin message routing, not merely message meaning:

```python
assert "message" not in result

# TEL-002
assert result["messages"] == [
    "Historical ASG capacity is unavailable. Enable Auto Scaling group metrics "
    "collection to provide GroupInServiceInstances for capacity assessment."
]

# TEL-003
assert result["messages"] == [
    "Historical desired capacity is unavailable. Enable Auto Scaling group "
    "metrics collection to provide GroupDesiredCapacity; the current desired "
    "value remains available from the group configuration."
]

# TEL-004 with observed_days=6.999 and policy minimum 7
assert result["messages"] == [
    "At least 7 observed days are required; 6.999 are available."
]
```

The desired-history case must assert both the reason code on the option and the
exact remediation in `messages[]`. Any implementation that writes a singular
top-level `message` fails F7 even when the text itself is correct.

### 3.7 Missing-memory preview

| ID | Scenario | Required assertions |
| --- | --- | --- |
| PRV-001 | Memory absent, CPU saves | no recommendations; one memory preview |
| PRV-002 | Preview exact shape and values | EC2-compatible fields/blocker; CPU-only equations are correct |
| PRV-003 | Preview tier collapse | dedup inside preview is deterministic |
| PRV-004 | Memory present | memory preview absent |
| PRV-005 | CPU-only no savings | preview absent |
| PRV-006 | Preview flag disabled | output matches flag-off contract |
| PRV-007 | Scope deferred | no preview generated |
| PRV-008 | Pricing missing | no monetary preview |

PRV-001/002 use blocker `MEMORY_METRIC_NOT_ENABLED`. TEL-006 uses
`ASG_MEMORY_CAPACITY_PAIRING_INSUFFICIENT` for poor pairing and
`ASG_MEMORY_TELEMETRY_WINDOW_TOO_SHORT` for a usable series younger than seven
days. Each result contains at most one preview of this kind, with all tier
options nested inside it. PRV-002 also
asserts `kind`, `classification`, `blockers`, `message`, `options`, and
`evidence`, and verifies that every non-null tier embeds a complete
configuration.

Preview options never appear in normal tiers and never make the top-level
classification ACTIONABLE or CONDITIONAL.

### 3.8 Scope and deferral

One test per reason code:

```text
SCOPE-001 MIXED_INSTANCES_POLICY_UNSUPPORTED
SCOPE-002 WEIGHTED_CAPACITY_UNSUPPORTED
SCOPE-003 WARM_POOL_REQUIRES_SEPARATE_OPTIMIZATION
SCOPE-004 SCHEDULED_SCALING_REQUIRES_SEPARATE_OPTIMIZATION
SCOPE-005 PREDICTIVE_SCALING_REQUIRES_SEPARATE_OPTIMIZATION
SCOPE-006 SCALE_TO_ZERO_UNSUPPORTED
SCOPE-007 HETEROGENEOUS_INSTANCE_TYPES_UNSUPPORTED
SCOPE-008 INSTANCE_REFRESH_IN_PROGRESS
SCOPE-009 SCALE_IN_PROTECTION_ACTIVE
SCOPE-010 SCALING_PROCESS_SUSPENDED
SCOPE-011 UNSUPPORTED_INSTANCE_LIFECYCLE
```

Assert scope returns before catalog lookup and telemetry calculation. A supported
homogeneous three-AZ group proceeds as the positive control.

### 3.9 Operational classification

| ID | Scenario | Classification |
| --- | --- | --- |
| CLS-001 | Complete CPU-driven evidence | ACTIONABLE |
| CLS-002 | Non-CPU scaling signal | CONDITIONAL |
| CLS-003 | No dynamic policy | CONDITIONAL |
| CLS-004 | Recent failed activity | CONDITIONAL |
| CLS-005 | Capacity shortage evidence | CONDITIONAL |
| CLS-006 | Capacity rebalance | CONDITIONAL |
| CLS-007 | Desired history missing | CONDITIONAL |
| CLS-008 | Missing memory | PREVIEW only |
| CLS-009 | Required metric absent | INSUFFICIENT_DATA |
| CLS-010 | Unsupported configuration | DEFERRED |

Risk assertions verify the named dimension and reason code, not only the top-
level classification.

### 3.10 Response and shared-UI contracts

| ID | Scenario | Required assertions |
| --- | --- | --- |
| ENV-001 | Complete envelope | state, current cost, pricing source, current-capacity evidence, and policy objects are present |
| ENV-002 | Telemetry disclosure | every metric has present/observed-days/thin-data; CPU and memory expose pairing ratios |
| ENV-003 | Memory disclosure states | exact persisted source plus usable/unavailable/insufficient-pairing status |
| ENV-004 | Top-level classification | always present on ASG paths; OPPORTUNITY is never emitted |
| ENV-005 | Shared risk panel | network/storage/migration are null, operations is present, overall ignores nulls |
| ENV-006 | Policy naming | ASG returns capacity_policy and does not alias it as compute_policy |
| ENV-007 | Pricing parity | current monthly cost and regional pricing evidence support savings-percent display |

These tests pin deliberate ASG extensions and divergences rather than asserting
that the normal EC2 envelope has the same top-level classification. UI contract
tests render null risk components as `n/a`, not LOW, and render ASG thin-data
insufficiency with the required wait-for-coverage message.

## 4. Collection tests

### 4.1 Exact query set

Assert one assessment submits no metric beyond:

```text
CPUUtilization
GroupDesiredCapacity
GroupInServiceInstances
selected memory metric when available
```

Verify namespace, complete dimensions, period, and statistic. A mutation adding
network, EBS, group min/max, pending, terminating, total, warm-pool, or forecast
metrics must fail this test.

### 4.2 Pagination and ordering

| ID | Scenario |
| --- | --- |
| MET-001 | `GetMetricData` follows all `NextToken` pages |
| MET-002 | Results from pages merge by query ID |
| MET-003 | Returned timestamps sort ascending |
| MET-004 | Duplicate timestamps resolve deterministically without double count |
| MET-005 | Empty page followed by token does not stop pagination |

### 4.3 Timestamp normalization

| ID | Scenario |
| --- | --- |
| MET-006 | CPU and capacity exact timestamp join |
| MET-007 | CPU-only timestamp omitted when capacity absent |
| MET-008 | Capacity-only timestamp omitted when CPU absent |
| MET-009 | Missing memory timestamp affects only memory coverage |
| MET-010 | Gauge Maximum and utilization Average are requested |
| MET-011 | 14d/30d/60d windows filter correctly at cutoff equality |
| MET-012 | Required counts calculated before percentiles |
| MET-013 | No interpolation or forward fill |

### 4.4 Discovery and inventory

| ID | Scenario |
| --- | --- |
| INV-001 | ASG pagination complete |
| INV-002 | Single launch-template type resolves |
| INV-003 | In-service type mismatch marks heterogeneous |
| INV-004 | Mixed/weighted metadata detected |
| INV-005 | Schedule/predictive/warm-pool metadata detected |
| INV-006 | Critical suspended process detected |
| INV-007 | Active refresh and scale-in protection detected |
| INV-008 | Per-group failure does not abort other groups |
| INV-009 | Inventory upsert stable by resource ID |
| INV-010 | Same ASG name in different account/region scopes persists as distinct rows |

INV-005 includes a configured warm pool whose current `Instances` list is empty;
`WarmPoolConfiguration` alone must set `warm_pool_present: true`.
INV-010 also asserts non-null ASG account/region columns in both the ORM and
migration contract—specifically Alembic revision
`20260715_000001_add_asg_inventory.py`—while the pre-existing nullable shared
finding columns stay unchanged. This is a frozen V1 schema assertion, not a V2
table migration.

### 4.5 Memory discovery compatibility

Run the existing EC2 memory discovery cases against the shared helper plus ASG
association cases:

- Pagination.
- Name normalization and exclusions.
- Candidate ranking.
- Invalid-value fallback.
- Complete-dimension persistence.
- Group-associated source preference.
- `ListMetrics` requests include both `Name=AutoScalingGroupName` and the exact
  group `Value`, follow every page, and never issue a value-less regional scan
  once per group.
- If implementation chooses a broad regional `ListMetrics` inventory instead,
  assert one scan-scoped cache fill per account/region/client, exact client-side
  group-value filtering, no cross-scope cache reuse, and bounded call count as
  group count grows. Either strategy must pin F9 explicitly in
  `test_asg_list_metrics_filter_or_cache_is_scope_bounded`.
- Partial historical membership rejected.
- Group source preferred over a valid stable-member aggregate.
- Stable-member aggregate requires constant capacity, old members, no membership
  activity, compatible sources, and complete timestamp intersections.
- EC2 selected source/output unchanged after helper extraction.

## 5. Confidence trend tests

| ID | Scenario |
| --- | --- |
| TRD-001 | CPU daily Maximum headline |
| TRD-002 | persisted memory source reused |
| TRD-003 | memory omitted when unavailable |
| TRD-004 | no network/storage query |
| TRD-005 | 30/90/120/180/365/455 buckets |
| TRD-006 | pagination complete |
| TRD-007 | cache isolated by account/region/group/source/schema, including a memory-source change |
| TRD-008 | trend never changes recommendation |

## 6. Generated invariants

Generate deterministic synthetic series across group sizes, utilization values,
zones, prices, and tier policies. Assert:

1. Max never changes.
2. `availability_floor <= min <= desired <= max`.
3. No target min or desired exceeds current values.
4. Every returned option reduces desired and has positive eligible savings.
5. Tier counts nest Conservative >= Balanced >= Aggressive.
6. Increasing CPU or memory demand cannot reduce a target count.
7. Increasing demand cannot improve classification.
8. Removing metric points cannot improve telemetry risk.
9. Missing memory cannot create a normal recommendation.
10. A preview configuration never appears in normal tiers.
11. Unsupported scope never reaches pricing or telemetry evaluation.
12. Reordering raw points does not change output.
13. Repeated inputs produce identical decision projections.
14. Desired savings equal count delta times unit price.
15. Min and max changes never add savings.
16. Required capacity uses the union of independently capacity-paired CPU and
    usable-memory timestamps; neither dimension can erase a spike observed only
    in the other series, and an absent value is never treated as zero.
17. All tier references identify a returned complete configuration.
18. Collector query names are a subset of the four-series allowlist.
19. Every response retains all telemetry-summary metric keys even when absent.
20. Memory absent, pairing-insufficient, and short-window previews use distinct
    blockers.
21. Null risk dimensions do not affect overall risk.
22. ASG output never emits OPPORTUNITY.

Use fixed seeds and report the seed on failure.

## 7. Traceability

Maintain a machine-checked map from every frozen V1 scenario ID in Sections
3–5 to one explicit pytest node ID. Section 11.10 defines the V2 manifest
extension and its enablement gate. At minimum for V1:

| Spec section | Scenario groups |
| --- | --- |
| §2 Scope | SCOPE-001–011 |
| §3 Shared contracts | MET-001–013, BND-001–007 |
| §4 Inventory | INV-001–010 |
| §5–6 Telemetry | MET-001–013, TEL-001–010, ENV-002–003 |
| §7 Capacity model | CAP-001–010, AZ-001–006 |
| §8 Tiers | TIER-001–009 |
| §9 Pricing | COST-001–008 |
| §10 Scope gates | SCOPE-001–011 |
| §11 Sufficiency | TEL-001–010 |
| §12 Preview | PRV-001–008 |
| §13–15 Classification/risk | CLS-001–010 |
| §16 Response | CAP, TIER, COST projections, ENV-001–007 |
| §18 Trend | TRD-001–008 |
| §22 Invariants | generated invariant test |

The current manifest test parses the frozen V1 prefixes, verifies the expected
ID set and mapped functions, and fails on undocumented or unmapped V1 IDs. The
V2 implementation extends the parser and node map before V2 can be enabled.

## 8. Coverage and mutation targets

Require branch and condition coverage for:

- Scope gates.
- Pairing/sufficiency boundaries.
- p50/p99 nearest-rank calculation.
- `ceil` and equality at tier ratios.
- Availability floor.
- No-increase bounds.
- Tier dedup/default selection.
- Decimal one-cent and minimum savings comparisons.
- Missing-memory preview separation.
- Classification caps.

Periodic mutation testing should target:

```text
>= versus > at tier equality
ceil versus floor/round
max versus min across windows
p50 versus p99 selection
availability floor max
independently paired timestamp union versus CPU/memory intersection
missing point versus zero
Decimal(str(x)) versus Decimal(x)
current max preservation
preview versus recommendation destination
```

Mutation testing is periodic, not a per-commit gate.

## 9. Safe execution

On a fresh clone, unpack the catalog before catalog-backed tests:

```bash
.venv/bin/python staging_pricing/unpack_pricing_db.py
```

Run only explicitly named files:

```bash
.venv/bin/python -m pytest tests/test_asg_rightsizer.py -q
.venv/bin/python -m pytest tests/test_asg_rightsizer_scenarios.py -q
.venv/bin/python -m pytest tests/test_asg_metric_collection.py -q
.venv/bin/python -m pytest tests/test_asg_memory_metric_discovery.py -q
.venv/bin/python -m pytest tests/test_ec2_rightsizer_v1.py tests/test_ec2_memory_metric_discovery.py -q
```

Never run broad or keyword discovery. Files under `tests/integration/` can
provision real, billable AWS resources.

## 10. Acceptance

The frozen V1 suite is complete when:

- Every scenario ID maps to an existing named unit test.
- Every spec invariant has a direct or generated assertion.
- Collection tests prove the four-series allowlist.
- Normal recommendation tests use real catalog pricing/specs by default.
- All below/equal/above boundaries pass.
- Missing-memory tests prove preview-only behavior.
- Route tests prove case-insensitive classification filtering, HTTP 422 for an
  unsupported classification, and UTC `Z` inventory timestamp serialization.
- Pairing tests prove CPU failure and memory-preview paths use distinct explicit
  reason codes.
- Response tests pin telemetry disclosure, EC2-compatible preview fields,
  envelope parity fields, deliberate policy/classification differences, and
  null-risk UI behavior.
- Existing named EC2 tests remain green.
- Tests require no AWS credentials, network, running ASG, or production data.
- Results are deterministic locally and in CI.

V2 has the additional completion gate in Section 11.10; satisfying this V1 list
alone never authorizes enabling instance optimization.

## 11. V2 combined-optimization matrix

### 11.1 V1 and shared-helper parity

| ID | Scenario | Required assertions |
| --- | --- | --- |
| BASE2-001 | ASG instance optimization off | complete V1 decision projection is byte-for-byte unchanged |
| BASE2-002 | Current type inside V2 candidate set | its per-tier counts/evidence equal the V1 calculation |
| BASE2-003 | EC2 shared-helper extraction | named EC2 metrics-present/missing/tiering/family/preview projections unchanged |
| BASE2-004 | Existing V1 review regressions | timestamp union, clamp, warm pool, schema, activities, remediation, UTC, and classification tests remain green |

BASE2-001 runs metrics-present, missing-memory, desired-history-missing,
DEFERRED, and INSUFFICIENT_DATA fixtures. It compares decision fields, not only
the selected target.

Mechanize invariant 25 before extracting the first shared helper:

1. Generate the existing `decision_projection()` for those five fixtures plus
   candidate-limit/tier-collapse and operational-CONDITIONAL fixtures.
2. Serialize the mapping as canonical UTF-8 JSON with sorted keys, compact
   separators, and one trailing newline.
3. Commit the bytes at
   `tests/fixtures/asg_rightsizer/v1_decision_projection_golden.json` in the
   helper-extraction commit's parent state.
4. After extraction, tests load that file read-only, regenerate the projection,
   serialize it identically, and compare the byte strings.

"Byte-for-byte" means these canonical decision-projection bytes, not the full
response. Volatile timestamps, additive evidence, and unrelated envelope fields
remain outside the golden, consistent with the no-full-response-snapshot rule.
There is no automatic update mode in pytest. Any intentional golden change must
be generated only with:

```bash
.venv/bin/python tests/fixtures/asg_rightsizer/capture_v1_decision_golden.py --write
```

The script refuses to overwrite without `--write`; its default mode compares
current canonical bytes to the committed file. Review the fixture diff and
justify it as a V1 contract change rather than extraction fallout.

### 11.2 Catalog candidates and compatibility

| ID | Scenario | Required assertions |
| --- | --- | --- |
| CND2-001 | Same-architecture same-family type | evaluated normally |
| CND2-002 | Same-architecture cross-family type | evaluated normally; `family_changed=true` |
| CND2-003 | Missing architecture evidence | rejected `ARCHITECTURE_INCOMPATIBLE` |
| CND2-004 | x86 to arm64 | excluded from normal recommendations; eligible only for Graviton preview |
| CND2-005 | General-purpose to burstable/specialized | rejected `FAMILY_NOT_ELIGIBLE` |
| CND2-006 | Same gated class, such as t3 to t3a | eligible when architecture overlaps |
| CND2-007 | Changed `.metal` target | excluded; unchanged current `.metal` remains capacity-only eligible |
| CND2-008 | Missing target regional price/spec | target skipped and rejection tallied |
| CND2-009 | Catalog order shuffled | identical targets, ranking, tiers, and rejection tallies |
| CND2-010 | Regional pricing/global specification | price follows ASG region/platform; specification is global |

Use real packaged specifications for at least one general-purpose, one compute,
one memory, one burstable, and one arm64 type. Price overrides are permitted
only when the scenario owns an exact economic boundary.

### 11.3 Target-independent demand and capacity math

| ID | Scenario | Required assertions |
| --- | --- | --- |
| DEM2-001 | Both CoreMark values available | one consistent CoreMark basis drives target counts |
| DEM2-002 | Either CoreMark unavailable | both sides use vCPU fallback |
| DEM2-003 | No comparable CPU basis | `CPU_CAPABILITY_UNKNOWN` rejection |
| DEM2-004 | CPU spike without memory timestamp | spike remains in target requirement |
| DEM2-005 | Memory spike without CPU timestamp | spike remains in target requirement |
| DEM2-006 | Independent percentiles mutation | multiplying/combining independent percentiles fails the expected count |
| DEM2-007 | Current target | exact V1 p50/min and p99/desired result |
| DEM2-008 | Raw minimum exceeds current, desired fits | minimum clamps; option survives |
| DEM2-009 | Raw desired equals/exceeds current | equality is no count reduction; above is `TARGET_REQUIRES_CAPACITY_INCREASE` |
| DEM2-010 | Current target overflow, no alternative | top-level `CURRENT_CAPACITY_NOT_OVERPROVISIONED` |
| DEM2-011 | Current target overflow, alternative survives | valid alternative returned; no top-level no-option reason |
| DEM2-012 | V1 telemetry schema only | no type candidates; V1 output retained during rollout |
| DEM2-013 | Changed smaller target needs more members while another target survives | smaller target tallied `TARGET_REQUIRES_CAPACITY_INCREASE`; alternative returned; no top-level no-option reason |

Instrument DEM2 cases to prove normalization/alignment happens once per ASG,
not once per target. Do not assert an invalid percentile optimization: the
per-timestamp maximum remains inside each target calculation.

DEM2-013 uses real `m6i.xlarge`, `m6i.large`, and `m6i.2xlarge` specifications
with labeled price overrides. Current is `m6i.xlarge`, `(min=3, desired=10,
max=20)`, and Balanced p99 CPU demand is `4.2` current-instance equivalents.
Expected raw Balanced desired counts are `12` for `m6i.large`, `6` for the
unchanged type, and `3` for `m6i.2xlarge`. Set monthly prices to `$80`, `$45`,
and `$150` respectively. The medium target is rejected even though its clamped
10-count cost would appear cheap; the xlarge target `(3,3,20)` costs `$450`,
saves `$350`, survives, and prevents top-level
`CURRENT_CAPACITY_NOT_OVERPROVISIONED`. The rejection summary contains at least
one `TARGET_REQUIRES_CAPACITY_INCREASE`.

### 11.4 Optimization kind and total economics

| ID | Scenario | Required assertions |
| --- | --- | --- |
| ECO2-001 | Same type, desired lower | `CAPACITY_ONLY` |
| ECO2-002 | Type changes, desired unchanged | `INSTANCE_ONLY` |
| ECO2-003 | Type and desired both change | `COMBINED` |
| ECO2-004 | Higher target unit price, lower total cost | eligible with exact complete-configuration savings |
| ECO2-005 | Lower unit price, nonpositive total saving | excluded |
| ECO2-006 | Exactly $0.01 total saving | included using Decimal-from-string |
| ECO2-007 | Request minimum below/equal/above raw total saving | inclusive equality only |
| ECO2-008 | Double-count mutation | no separately added instance/count savings; displayed total matches cost delta |

Include the concrete 10-at-$80 versus 4-at-$150 fixture and a float-hazard
fixture. `target_monthly_cost + monthly_savings == current_monthly_cost` uses
the raw Decimal eligibility values before display rounding.

### 11.5 Missing memory and architecture previews

| ID | Scenario | Required assertions |
| --- | --- | --- |
| MEM2-001 | Missing memory, same-type count reduction | preview only |
| MEM2-002 | Larger-memory target retains min and desired aggregate memory | normal CONDITIONAL recommendation with retention code |
| MEM2-003 | Desired floor passes, minimum floor fails | preview only |
| MEM2-004 | Minimum floor passes, desired floor fails | preview only |
| MEM2-005 | Exact aggregate-memory equality | retention passes |
| MEM2-006 | Unknown current or target memory | fails closed |
| MEM2-007 | Retained fallback dimension | excluded from projected utilization and binding dimension |
| MEM2-008 | Missing/poor-pairing/young sources | correct distinct blocker with identical retention logic |
| PRW2-001 | Memory preview pool has multiple targets | one highest-total-savings preview, deterministic tie |
| PRW2-002 | Graviton retains raw aggregate vCPU/memory at min and desired | one OPPORTUNITY preview |
| PRW2-003 | Graviton fails one retention floor | no preview |
| PRW2-004 | Graviton CoreMark evidence | projected CPU/performance fields null |
| PRW2-005 | Preview flags off | preview kinds absent without normal-output changes |
| PRW2-006 | Preview isolation | previews never affect normal tiers, limiting, classification, or rejection tallies |

### 11.6 Tier selection, limiting, and evidence

| ID | Scenario | Required assertions |
| --- | --- | --- |
| TIR2-001 | Three tiers choose different types | every reference resolves by complete type/count key |
| TIR2-002 | Same type/config satisfies multiple tiers | one recommendation with all labels |
| TIR2-003 | Counts across types | no invalid raw-count nesting assertion across different per-instance capacities |
| TIR2-004 | Deterministic economic tie | target cost, desired, min, then lexical type tie-break |
| TIR2-005 | Balanced displaced by savings-only truncation | shared limiter reserves full-set Balanced target |
| TIR2-006 | Candidate limit one | reserved target may satisfy Balanced/Aggressive; other tiers null |
| TIR2-007 | Rejection tallies versus limit | tallies identical for small and large limits |
| TIR2-008 | Invalid candidate limit | nonpositive value rejected with HTTP 422/service validation |

### 11.7 Classification and response contract

| ID | Scenario | Required assertions |
| --- | --- | --- |
| ENV2-001 | Capacity-only clean option | retains V1 ACTIONABLE behavior |
| ENV2-002 | Same-architecture type change | CONDITIONAL with network/storage validation reason |
| ENV2-003 | Type-change risk | network/storage null, compatibility MEDIUM, overall ignores nulls |
| ENV2-004 | Performance evidence available | same-architecture ratio/change exact; positive may be headlined |
| ENV2-005 | Performance evidence unavailable | null without blocking candidate |
| ENV2-006 | Complete option | type, kind, family, architecture, basis, prices, total cost, and aggregate capacity evidence present |
| ENV2-007 | Policy evidence | separate capacity, scope, and candidate policies exposed |
| ENV2-008 | Availability note | EC2-compatible note present; no launch-capacity claim |
| ENV2-009 | Top-level OPPORTUNITY | never used for normal output; allowed only inside Graviton preview |
| ENV2-010 | Full configuration identity | dedup/tier/UI projection includes type plus min/desired/max |

### 11.8 V2 generated invariants

Generate fixed-seed current/target catalog fixtures and timestamped workloads.
In addition to the flag-off invariants in Section 6, assert:

1. Every normal changed target has architecture overlap, passes family policy,
   and is not `.metal`.
2. No target increases min or desired; max remains numerically unchanged.
3. Every recommendation changes type or lowers desired and has positive exact
   total savings.
4. For a single target type, increasing demand cannot lower its required count
   or improve classification.
5. Missing memory cannot reduce aggregate min or desired memory normally.
6. Type-changing normal options are never ACTIONABLE.
7. Candidate order and limit do not alter rejection tallies.
8. Every tier references a returned complete configuration.
9. No CPU comparison mixes CoreMark with vCPU.
10. The current-type candidate equals V1 under the same evidence.
11. Flag-off output equals the frozen V1 projection across generated inputs.
12. Current cost minus target cost equals authoritative savings exactly once.

Pin invariant 28 with a numeric p99 commutation counterexample of exactly 100
aligned demand points:

```text
t1:       cpu_used_capacity=10, memory_used_capacity=0
t2:       cpu_used_capacity=0,  memory_used_capacity=10
t3..t100: cpu_used_capacity=0,  memory_used_capacity=0

nearest-rank p99(per_timestamp_max) = 10
nearest-rank p99(cpu) = 0
nearest-rank p99(memory) = 0
max(independent p99s) = 0
```

The production path must select 10 before applying the target/tier denominator.
A mutation that computes independent percentiles and then takes their maximum
must fail this fixture.

Mutation targets add: cheaper-unit-price filtering, missing either memory floor,
raw desired clamp instead of rejection, CoreMark/vCPU mixing, type-less dedup,
type-change ACTIONABLE, full-set tier selection after truncation, and percentile
combination before per-timestamp maximum.

### 11.9 Deployment and catalog gates

| ID | Scenario | Required assertions |
| --- | --- | --- |
| ROL2-001 | Deployment setting false, policy true, V2 telemetry present | flag-off V1 output; deployment-off wins |
| ROL2-002 | Deployment true, policy false | flag-off V1 output |
| ROL2-003 | Both switches true, V1 telemetry only | flag-off V1 output and upgrade coverage event |
| ROL2-004 | CoreMark coverage 0.6999, 0.70, 0.7001 | below remains disabled; equality and above pass |
| ROL2-005 | Empty CoreMark denominator | disabled with visible coverage error; no division by zero |
| ROL2-006 | Coverage metadata | numerator, denominator, ratio, catalog schema, and generated timestamp exact |

Patch settings rather than process-global environment where practical. Assert
the effective formula includes deployment setting, policy, V2 telemetry, and
catalog gate and that no request parameter can override a false term.

### 11.10 V2 manifest, traceability, and named files

Extend `SCENARIO_NODE_MAP` and its parser to recognize every BASE2/CND2/DEM2/
ECO2/MEM2/PRW2/TIR2/ENV2/ROL2 ID. Until the V2 tests land, the existing manifest
continues to enforce only the frozen V1 IDs; V2 may not be enabled in that
state. Once implementation begins, no V2 ID may map to a generic placeholder
node merely to satisfy the manifest.

| Spec section | V2 scenario groups |
| --- | --- |
| §2–3 scope/shared reuse | BASE2, CND2 |
| §4–6 inventory/telemetry | BASE2-004, DEM2-004–006, DEM2-012 |
| §7 capacity | DEM2, MEM2 |
| §8 tiers | TIR2 |
| §9 candidates/pricing | CND2, ECO2 |
| §10 scope | BASE2-004 plus frozen SCOPE/INV warm-pool cases |
| §11–12 sufficiency/previews | MEM2, PRW2 |
| §13–15 classification/risk | ENV2-001–005, ENV2-009 |
| §16–17 response/policy | ENV2-006–010, TIR2 |
| §21 rollout | BASE2-001–004, DEM2-012, ROL2 |
| §22 invariants | Section 11.8 generated tests |

Add only:

```text
tests/test_asg_rightsizer_v2.py
tests/test_asg_rightsizer_v2_properties.py
```

Run them individually along with the four frozen V1 ASG files and the named EC2
regression files. Never run broad discovery. V2 acceptance requires every old
and new manifest entry, all 28 spec invariants, catalog-backed scenarios, and
flag-off/EC2 parity to pass without AWS credentials or network access.
