# RDS Rightsizer V1 — UI/UX Contract

## 0. Purpose

The RDS rightsizer uses the same decision experience as EC2 and ElastiCache:
show the outcome and savings first, make tier/headroom tradeoffs comparable,
expose telemetry and risk, and turn uncertainty into explicit review work.

This document consumes `rds_rightsizer_spec.md`. Where it is silent, reuse the
EC2 rightsizer's interaction and accessibility behavior. RDS-specific behavior
is deliberate:

- instance class and storage configuration are independent recommendations;
- allocated storage is never reduced;
- Database Insights / Performance Insights may resolve ambiguous evidence;
- enabling telemetry is a user action, never an automatic MaxOps action;
- every instance-class change includes outage/reconnection guidance.

## 1. Product surfaces and requests

### 1.1 Fleet surface

Add rightsizer state to the existing RDS inventory page. Fetch:

```text
GET /recommendations/rds/rightsize
  ?account_id=&region=&engine=&state=available
  &classification=&min_monthly_savings=&candidate_limit=
```

Render one row per RDS inventory ID. Do not render Multi-AZ standbys as rows and
do not merge a source DB with its read replicas.

The URL stores server-side filters and current sort. Search and purely visual
filters may be client-side.

### 1.2 Resource detail surface

Fetch in parallel:

```text
GET /recommendations/rds/rightsize/{inventory_id}
GET /recommendations/rds/rightsize/{inventory_id}/trend
```

Recommendation success is independent of trend success. A trend error creates
a local evidence-panel error and never hides the recommendation.

Place **Rightsizing recommendation** after headline database metrics and before
general findings/runbook actions. Do not put Apply, Modify, Enable, Stop, or
Delete controls inside the rightsizer in V1.

## 2. Typed frontend contract

Shared classification, tier, risk, warning, policy, and trend types should be
generalized from EC2 rather than copied.

```ts
// REJECTED exists only on evaluated candidates in diagnostics/tallies; the
// top-level response classification never carries it (core spec §15/§16.1).
type RdsClassification =
  | 'ACTIONABLE'
  | 'CONDITIONAL'
  | 'DEFERRED'
  | 'INSUFFICIENT_DATA';

type RdsEvaluationStatus =
  | 'RECOMMENDED'
  | 'NO_RECOMMENDATION'
  | 'INSUFFICIENT_DATA'
  | 'NOT_APPLICABLE'
  | 'DEFERRED';

type RdsRecommendationKind =
  | 'DB_INSTANCE_CLASS_CHANGE'
  | 'STORAGE_CONFIGURATION_CHANGE';

type RdsBindingDimension =
  | 'cpu'
  | 'memory'
  | 'db_load_cpu'
  | 'storage_iops'
  | 'storage_throughput'
  | 'network';

interface RdsTierOption {
  target_db_instance_class: string;
  target_vcpus: number;
  target_memory_gib: number;
  projected_util: number | null;
  binding_dimension: RdsBindingDimension | null;
  target_monthly_cost: number;
  monthly_savings: number;
  yearly_savings: number;
  classification: 'ACTIONABLE' | 'CONDITIONAL';
  satisfied_tiers: Array<'conservative' | 'balanced' | 'aggressive'>;
  risk_assessment: Record<string, unknown>;
  reason_codes: string[];
  warning_details: Array<{ code: string; message: string }>;
  evidence: Record<string, unknown>;
}

interface RdsInstanceClassRecommendation {
  kind: 'DB_INSTANCE_CLASS_CHANGE';
  classification: 'ACTIONABLE' | 'CONDITIONAL';
  tiers: {
    default: 'balanced' | 'conservative' | 'aggressive' | null;
    conservative: RdsTierOption | null;
    balanced: RdsTierOption | null;
    aggressive: RdsTierOption | null;
  };
  candidates: RdsTierOption[];
}

interface RdsStorageRecommendation {
  kind: 'STORAGE_CONFIGURATION_CHANGE';
  classification: 'ACTIONABLE' | 'CONDITIONAL';
  current_storage: {
    storage_type: string;
    allocated_storage_gib: number;
    iops: number | null;
    throughput_mibps: number | null;
  };
  target_storage: {
    storage_type: string;
    allocated_storage_gib: number;
    iops: number | null;
    throughput_mibps: number | null;
  };
  monthly_savings: number;
  yearly_savings: number;
  evidence: Record<string, unknown>;
  risk_assessment: Record<string, unknown>;
  reason_codes: string[];
  warning_details: Array<{ code: string; message: string }>;
}

interface RdsDbLoadAttribution {
  status:
    | 'AVAILABLE'
    | 'DISABLED'
    | 'UNSUPPORTED'
    | 'ACCESS_DENIED'
    | 'ERROR'
    | 'NOT_NEEDED';
  required: boolean;
  observed_days: number | null;
  total_load: Record<string, number | null> | null;
  cpu_load: Record<string, number | null> | null;
  non_cpu_load: Record<string, number | null> | null;
  unattributed_load: Record<string, number | null> | null;
  wait_type_shares: Array<{
    name: string;
    share: number;
    aas_p99: number | null;
  }>;
  enablement_prompt: {
    title: string;
    message: string;
    documentation_url: string;
    causes_downtime: false;
    recommended_mode: 'standard';
    sufficient_retention_days: 7;
  } | null;
}

interface RdsRightsizerResponse {
  inventory_id: number;
  resource_id: string;
  resource_name: string | null;
  account_id: string | null;
  region: string;
  state: string;
  engine: string;
  engine_version: string;
  license_model: string;
  multi_az: boolean;
  read_replica: boolean;
  current: Record<string, unknown>;
  classification: RdsClassification | null;
  evaluation_status: {
    instance_class: RdsEvaluationStatus;
    storage_configuration: RdsEvaluationStatus;
  };
  evaluation_reason_codes: {
    instance_class: string[];
    storage_configuration: string[];
  };
  deferred_reason_codes?: string[];
  recommendations: Array<
    RdsInstanceClassRecommendation | RdsStorageRecommendation
  >;
  telemetry_summary: Record<string, unknown>;
  database_load_attribution: RdsDbLoadAttribution;
  policy: Record<string, unknown>;
  pricing_scope: Record<string, unknown>;
  operational_note: string;
  availability_note: string;
}
```

The UI must discriminate recommendation unions by `kind`; it must not assume
every response has tiers.

## 3. Fleet behavior

### 3.1 Columns

Default columns:

```text
Database
Engine
Deployment
Current class
Recommended class
Binding dimension
Instance savings / month
Storage opportunity
Observed days
DB load evidence
```

`Observed days` renders both CPU and freeable-memory coverage compactly. When
both exist, its headline is their lesser observed-days value. When one is
absent, show `CPU missing` or `Memory missing` rather than treating null as zero.
Below seven days it uses the RDS thin-data treatment (amber flag); it is
disclosure, not a distinct "confidence" score — no derived confidence metric
exists in the backend contract.

`Deployment` is `Single-AZ`, `Multi-AZ`, or `Read replica`. Show a read replica's
source in secondary text when known.

`DB load evidence` is one of:

```text
Available · Not needed · Enable for confidence · Unsupported · Unavailable
```

Do not label disabled Performance Insights as an error.

### 3.2 Savings totals

Never add class and storage savings for the same database. Fleet totals choose
the default class option when present, otherwise the highest-savings
capacity-retaining class candidate when present, otherwise storage. Show:

```text
Potential monthly savings (independent recommendations; not additive)
```

The storage cell may still show its separate amount.

### 3.3 Sorting and filtering

Default sort:

1. classification order: `ACTIONABLE`, `CONDITIONAL`, `INSUFFICIENT_DATA`,
   `DEFERRED`, then `null`;
2. larger selected monthly savings first within a classification (missing
   savings sorts below numeric savings);
3. resource name/id ascending.

`null` is deliberately last: it means evaluation completed and found no saving,
not that review or remediation is pending.

Filters: engine, region, account, deployment, classification, recommendation
kind, DB-load status, and minimum savings.

## 4. Detail layout

Render these blocks in order:

1. outcome header;
2. instance-class tier comparison, when present;
3. storage-configuration recommendation, when present;
4. capacity evidence;
5. database-load attribution;
6. risk and required review;
7. long-range confidence trend;
8. pricing and operational disclosures.

### 4.1 Outcome header

Lead with one sentence:

```text
Balanced: db.m6g.xlarge -> db.m7g.large, saving about $X/month.
```

or:

```text
Conditional: a smaller class fits standard metrics, but database-load
attribution is needed before applying it.
```

If only storage exists:

```text
Storage: keep 500 GiB and reduce gp3 provisioned performance to X IOPS / Y
MiB/s, saving about $Z/month.
```

When `classification` is `null`, show:

```text
No cost-saving RDS rightsizing recommendation was found under the current
policy.
```

Use the per-kind statuses to distinguish a complete no-opportunity result from
an evaluation that could not run.

Never describe a `CONDITIONAL` option as safe, approved, or ready to apply.

### 4.2 Tier cards

Use the shared Conservative/Balanced/Aggressive selector. Each card shows:

```text
target class
vCPU and memory
maximum projected utilization
binding dimension
monthly and annual savings
classification and highest risk
```

Balanced is selected by default when present. Explain collapsed tiers using
the shared EC2 behavior; never duplicate an identical target into three cards.

`maximum projected utilization` is compute-only (CPU, memory, and CPU AAS
when attribution ran — core spec §14), the same meaning as EC2 tier cards.
Tier cards deliberately carry no performance-delta badge: the backend
declines cross-class performance modeling (core spec §8), so there is no RDS
analog of EC2's CoreMark `performance_change_pct`. Do not fabricate one.

`binding_dimension` is broader: it names the highest known limiting ratio
across compute, memory, network, and storage. It may therefore say `network` or
`storage throughput` without changing the compute-only utilization figure or
tier. When every comparable ratio is unavailable it is omitted.

When both CPU and FreeableMemory are absent, every tier is `null`; do not render
the tier selector. Capacity-retaining candidates may still appear as a
`CONDITIONAL` savings list with `projected_util: null`, an empty
`satisfied_tiers` array, and an explicit unmeasured-capacity explanation. The
fleet Recommended class and savings cells use the highest-savings such candidate
when no default tier exists, matching the backend headline order.

### 4.3 Storage card

Use a before/after comparison:

| Setting | Current | Recommended |
| --- | --- | --- |
| Type | gp3 | gp3 |
| Allocated storage | 500 GiB | 500 GiB (unchanged) |
| IOPS | 12,000 | 6,000 |
| Throughput | 500 MiB/s | 250 MiB/s |

Always render allocated storage even though it is unchanged. Adjacent copy:

```text
RDS doesn't support reducing allocated storage. This option changes only paid
storage performance and/or storage type.
```

Class and storage cards must not offer a combined total or combined apply path.

## 5. Capacity evidence

### 5.1 Headroom claim and chart

CPU inherits the EC2 pattern (EC2 UI spec §4.2/§5.1): the selected tier's
headroom sentence sits directly above a target-scaled daily-maximum trend
chart in the same card, so the claim is visually checkable rather than
asserted. CPU is a 0–100 gauge, so the EC2 target-mode transform applies
unchanged, including the 40%/70% bands, the 100% target-ceiling line, and
the always-visible peak callout.

Freeable memory is an absolute, lower-is-worse GiB series, so the EC2 percent
transform does not apply. Project each historical daily-minimum point onto the
target class using the same working-set model as the decision:

```python
estimated_used_memory_t = current_memory - current_freeable_memory_t
projected_target_freeable_t = target_memory - estimated_used_memory_t
```

Do not clamp a negative projected value; it truthfully shows that the modeled
working set would not fit. Plot `projected_target_freeable_t` as the headline
series, optionally plot current FreeableMemory as lighter context, and draw the
absolute policy free-memory floor as a horizontal reference line. The gap
between the projected-target series and the floor is the target's historical
modeled headroom. The sentence states selected-window projected free GiB and
projected used percent. Tooltips show both current free GiB and projected target
free GiB for the hovered day.

When FreeableMemory is absent, omit this chart and show that current memory was
retained as an unmeasured-dimension capacity floor.

### 5.2 Evidence table

Show the selected tier's evidence in a compact table:

| Dimension | Observed | Projected on target | Target/headroom | Status |
| --- | --- | --- | --- | --- |
| CPU | p99 and max | projected % | tier target | Low/Review/Fail |
| Memory | p01 free, min free | projected free and used % | tier target + absolute floor | ... |
| Storage IOPS | combined p99/max | target ratio | baseline/peak | ... |
| Storage throughput | combined p99/max | target ratio | baseline/peak | ... |
| Network | in/out p99/max | separate ratios | baseline/peak | ... |
| Connections | p99/max | no fabricated target | configured limit if known | ... |

Use `p01 free` wording for memory. Do not relabel it as p99 usage unless the
backend explicitly returns that derived value.

Below the table show actual observed days and coverage per required metric.
Requested 60 days must never be presented as observed 60 days.

Latency and queue depth appear as health context beneath storage capacity, not
as comparable percentages.

When a target class's network or EBS baseline is unknown, render that row in
an unknown-capacity style with its unknown-capacity warning; never
fabricate a ratio or bar fill. RDS has no assumed-baseline estimate (core
spec §11), so EC2's dashed "estimated baseline" treatment does not exist
here — the only states are documented capacity and unknown capacity.

## 6. Database-load attribution panel

### 6.1 Available

Title: **What the database was waiting on**.

Show:

- DB load p99/max in average active sessions;
- CPU AAS p99 against current and target vCPUs;
- CPU, I/O, lock/concurrency, and other wait shares;
- unattributed share when nonzero;
- actual observed days.

A horizontal stacked bar is permitted for wait shares. It must include text
labels and values and must not rely on color alone. Do not show SQL statements,
database users, or client hosts.

Explanatory copy:

```text
DB load measures active sessions running on CPU or waiting for a resource. A
large non-CPU share can indicate I/O, locks, or query behavior that a larger
instance alone may not fix.
```

### 6.2 Disabled and required

Render a yellow review panel:

```text
Enable Database Insights for stronger confidence

The standard metrics are close or contradictory. Enable Database Insights
Standard with Performance Insights, keep the default 7-day retention, and
rescan so MaxOps can distinguish CPU work from I/O and lock waits.

Enabling Performance Insights doesn't require a reboot, failover, or database
outage. MaxOps will not enable it automatically.
```

Provide only an external AWS documentation link in V1. Do not provide a button
that calls `ModifyDBInstance`.

### 6.3 Disabled but not needed

Show a quiet status:

```text
Database-load attribution wasn't required because standard CPU, memory,
storage, network, and connection evidence all had ample headroom.
```

Do not nag the user to enable telemetry.

### 6.4 Unsupported or inaccessible

For unsupported engines/classes, say manual review is required and omit enable
instructions. For access denied, show the missing `pi:GetResourceMetrics`
permission. For transient errors, offer Retry and preserve the conditional
recommendation.

## 7. Risk and warnings

Show overall risk plus component risks:

```text
Telemetry · Compute · Memory · DB load · Storage · Network · Connections ·
Compatibility · Operations
```

Group reason codes into plain-language actions. Preserve stable reason codes in
details/copy affordances for supportability.

The following warnings are always prominent:

- class modification outage;
- Multi-AZ failover/reconnection and DNS behavior;
- current swap or storage-credit depletion;
- DB-load attribution required but absent;
- non-CPU wait dominance;
- storage latency/queue pressure;
- unknown target EBS/network baseline;
- replica lag on a read replica.

## 8. Trend behavior

The default chart contains:

- CPU daily Maximum;
- FreeableMemory daily Minimum;
- connections daily Maximum as optional context;
- free storage daily Minimum as optional context.

CPU and memory use separate axes and truthful units. Freeable memory is GiB,
not percent, unless the backend returns a derived percent series.

Bucket chips: 30d, 90d, 120d, 180d, 365d, 455d. Labels explicitly say
`daily max` or `daily min`. The 60-day decision p99/max for IOPS, throughput,
latency, and network appears in evidence cards, not on a misleading 15-month
peak chart.

If an additional DB-load series exists, label it with its actual retained
horizon. A seven-day line must never visually span or imply 15 months.

## 9. Deferred and insufficient states

`DEFERRED` shows the exact unsupported context and no savings estimate.
Examples:

```text
Aurora needs a cluster-level rightsizer because writer and readers share
cluster storage and topology.
```

```text
This database has a pending class/storage modification. Rescan after it
finishes.
```

An absent CPU or FreeableMemory series is not an empty state. Show a
`CONDITIONAL` capacity-retaining recommendation when one survives, name the
unmeasured dimension, and explain that the target retains current vCPU or
memory capacity. When both are absent, the tier selector is absent and only
capacity-retaining savings candidates can be shown.

`INSUFFICIENT_DATA` appears only when collection/validation failed and no usable
fallback exists. Render it per recommendation kind. If class evaluation is
insufficient but storage is recommended, keep the storage card and use its
classification as the resource headline. A resource-level insufficient empty
state appears only when no recommendation exists and at least one applicable
kind could not run.

Per-kind status presentation:

| Status | UI behavior |
| --- | --- |
| `RECOMMENDED` | Render that kind's recommendation card. |
| `NO_RECOMMENDATION` | Omit the card; expose its stable blocker/reason in evaluation details. |
| `INSUFFICIENT_DATA` | Render a local unavailable state and remediation without hiding the other kind. |
| `NOT_APPLICABLE` | Omit the card and state the configuration reason when useful, such as multi-volume storage. |
| `DEFERRED` | Render the resource-level deferral; both kinds must agree. |

Short or sparse telemetry is also not an empty state: it renders a
`CONDITIONAL` recommendation with `OBSERVATION_WINDOW_TOO_SHORT` for short
history or `OBSERVATION_COVERAGE_TOO_LOW` for sparse coverage. None of these
states suggests Performance Insights as a replacement for CPU or
FreeableMemory; PI is attribution, not the base sizing source.

## 10. Loading, errors, and stale data

- Fleet skeletons preserve column widths.
- Detail recommendation and trend have independent loading states.
- Keep the previous successful recommendation visible during a refresh and
  label it with `generated_at`.
- A failed PI request preserves a conditional base recommendation.
- A failed orderable-options or pricing request doesn't display a guessed
  target.
- A missing recommendation is never rendered as `$0 savings`.

## 11. Accessibility and formatting

- Classification and risk use text/icon plus color.
- All charts have a table/text equivalent.
- Keyboard focus order follows the visual order.
- Currency includes ISO code when account/region context can vary.
- CPU/memory percentages show at most one decimal; GiB and MiB/s use binary
  units; network uses decimal Mbps/Gbps; latency displays milliseconds.
- AAS is defined on first use as average active sessions.
- Tooltips never contain the only copy of a warning.

## 12. Required frontend tests

- Fleet renders one row per inventory ID and no Multi-AZ standby row.
- Source and read replica remain separate.
- Class and storage union variants render independently.
- Fleet savings never adds both variants for one database.
- Balanced selection and tier collapse match backend fixtures.
- Allocated storage displays unchanged in every storage option.
- `CONDITIONAL` never shows safe/ready-to-apply language.
- Disabled-and-required PI renders Standard mode, seven days, no-downtime copy,
  a docs link, and no mutation button.
- Disabled-and-not-needed PI doesn't show an enable CTA.
- Unsupported PI omits enable instructions.
- Wait-share visuals have text equivalents and render unattributed load.
- SQL/user/host fields are absent from types and fixtures.
- Trend failure doesn't hide a recommendation.
- Deferred responses show no guessed savings. An insufficient kind shows no
  savings for that kind but does not hide an independently recommended kind.
- Thin telemetry renders a `CONDITIONAL` recommendation with the thin-data
  banner, not an `INSUFFICIENT_DATA` empty state.
- Short history and sparse coverage render their distinct reason codes.
- Missing CPU retains current vCPU; missing FreeableMemory retains current
  memory; either yields `CONDITIONAL` capacity-retaining candidates.
- Both missing metrics produce null tiers and no tier selector, while eligible
  capacity-retaining candidates remain visible.
- An insufficient class evaluation does not hide a valid storage option, and
  vice versa.
- Null top-level classification renders a complete no-opportunity state, not an
  error or `$0` recommendation.
- Tier-card utilization figures match compute-only projection fixtures;
  changing a fixture's network/EBS ratio changes classification, not tier.
- Unknown-baseline rows render the unknown-capacity style with no fabricated
  ratio.
- The CPU headroom sentence's numbers match the target-scaled chart, and the
  memory chart projects every daily point onto the target and draws the floor
  instead of comparing current free memory to a static target line.
- Outage and Multi-AZ reconnection warnings are present when applicable.
- Unit conversions and observed-day labels match backend fixtures.
