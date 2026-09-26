# ElastiCache Rightsizer V1 — UI/UX Contract

## 0. Purpose and relationship to EC2

The ElastiCache rightsizer must give a FinOps or cloud engineer the same core
decision experience as the EC2 rightsizer: understand the recommendation and
savings, compare Conservative/Balanced/Aggressive headroom, inspect evidence and
risk, and see uncertainty without it being hidden.

This document consumes the API contract in
`elasticache_rightsizer_impl_plan.md` D6 and the trend contract in backend spec
§15.2. Where this document is silent, use the corresponding behavior in
`rightsizers/ec2/ec2_rightsizer_ui_spec.md`. ElastiCache-specific differences
are deliberate:

- node type replaces EC2 instance type;
- memory telemetry is native and always requested;
- there is no EBS/storage decision lane and no CoreMark performance delta;
- a separate replica-count recommendation may coexist with node-type options;
- there are no savings previews or one-click rightsizer apply actions in V1.

The recommendation is advisory. The page must never imply that availability or
application performance is guaranteed.

## 1. Product surfaces and API calls

### 1.1 Fleet/list surface

Add rightsizer state to the existing ElastiCache overview rather than creating
a second inventory. Fetch:

```text
GET /recommendations/elasticache/rightsize
  ?account_id=&region=&state=available
  &min_monthly_savings=&candidate_limit=
  &network_medium_ratio=&network_high_ratio=
  &memory_medium_ratio=&memory_high_ratio=
```

The selected account, region, and state filters are sent to the API; search and
presentation-only filters may remain client-side. Query state is represented in
the URL so a filtered view is shareable.

Render exactly one row per `inventory_id`. A replication group and its member
clusters must never appear as separate rightsizer rows. A standalone cluster
without `ReplicationGroupId` remains one row.

### 1.2 Resource detail surface

On `ElasticacheResourceDetails.tsx`, fetch in parallel:

```text
GET /recommendations/elasticache/rightsize/{inventory_id}
GET /recommendations/elasticache/rightsize/{inventory_id}/trend
```

Insert a **Rightsizing recommendation** section after the four headline metric
cards and before the existing topology/optimization panels. The existing
finding and Execute controls remain in their current **Runbook action** panel;
they are not moved into the rightsizer.

The trend failure state is local to the evidence area. A trend 404/502 must not
hide a valid recommendation response.

## 2. Typed frontend contract

Create the types below in the frontend recommendation service. Shared policy,
evidence, risk, tier, and candidate types should be imported from or generalized
with the EC2 rightsizer types rather than copied.

```ts
type RightsizerClassification =
  | 'ACTIONABLE'
  | 'CONDITIONAL'
  | 'REJECTED'
  | 'DEFERRED';

type ElastiCacheRecommendationKind =
  | 'NODE_TYPE_CHANGE'
  | 'REPLICA_COUNT_REDUCTION';

interface ElastiCacheNodeCandidate {
  kind: 'NODE_TYPE_CHANGE';
  target_node_type: string;
  target_monthly_cost: number;
  monthly_savings: number;
  yearly_savings: number;
  projected_util: number | null;
  binding_dimension: 'cpu' | 'memory' | 'network' | null;
  classification: Exclude<RightsizerClassification, 'DEFERRED'>;
  satisfied_tiers: Array<'conservative' | 'balanced' | 'aggressive'>;
  risk_assessment: Record<string, unknown>;
  reason_codes: string[];
  warning_details: Array<{ code: string; message: string }>;
  compute_evidence: Record<string, unknown>;
  network_evaluation: Record<string, unknown>;
  memory_evaluation: Record<string, unknown>;
  cache_health: Record<string, unknown>;
  constraint_coverage: Record<string, unknown>;
  required_review: boolean;
}

interface ReplicaMemberEvidence {
  cache_cluster_id: string;
  node_group_id: string;
  role: 'replica';
  removed: boolean;
  member_created_at: string | null;
  read_ops_p99: number;
  read_ops_max: number;
  read_ops_sum: number;
  read_coverage_ratio: number | null;
  read_observed_days: number;
  read_latest_sample_age_seconds: number | null;
}

interface ElastiCacheReplicaRecommendation {
  kind: 'REPLICA_COUNT_REDUCTION';
  classification: 'ACTIONABLE' | 'CONDITIONAL';
  current_replicas_per_node_group: number;
  target_replicas_per_node_group: number;
  affected_node_group_count: number;
  removed_node_count: number;
  projected_survivor_engine_cpu: number;
  monthly_savings: number;
  yearly_savings: number;
  risk_assessment: Record<string, unknown>;
  reason_codes: string[];
  redundancy_disclosure: string;
  member_evidence: ReplicaMemberEvidence[];
}

interface ElastiCacheTierOption {
  target_node_type: string;
  projected_util: number | null;
  binding_dimension: 'cpu' | 'memory' | 'network' | null;
  monthly_savings: number;
  yearly_savings: number;
  classification: 'ACTIONABLE' | 'CONDITIONAL';
  risk_assessment: Record<string, unknown>;
  satisfied_tiers: Array<'conservative' | 'balanced' | 'aggressive'>;
}

interface ElastiCacheRightsizerResponse {
  inventory_id: number;
  resource_id: string;
  resource_name: string | null;
  account_id: string | null;
  region: string;
  state: string;
  current_monthly_cost?: number;
  pricing_source?: string;
  classification?: 'DEFERRED';
  deferred_reason_codes?: string[];
  policy?: Record<string, unknown>;
  compute_policy: Record<string, unknown>;
  candidate_policy: Record<string, unknown>;
  scope_policy: Record<string, unknown>;
  current_capacity_evidence?: Record<string, unknown>;
  recommendations: ElastiCacheNodeCandidate[];
  tiers: {
    default: 'conservative' | 'balanced' | 'aggressive' | null;
    conservative: ElastiCacheTierOption | null;
    balanced: ElastiCacheTierOption | null;
    aggressive: ElastiCacheTierOption | null;
  };
  rejection_summary?: Record<string, number>;
  availability_note: string;
  availability_validated: false;
  capability_catalog: Record<string, unknown>;
  telemetry_summary: Record<string, unknown>;
  replication_group_id: string | null;
  engine: 'redis' | 'valkey' | string;
  engine_version: string | null;
  current_node_type: string | null;
  node_count: number;
  cluster_mode_enabled: boolean;
  num_node_groups: number;
  replica_recommendation: ElastiCacheReplicaRecommendation | null;
  operational_note: string;
}

type TrendSelectionReason =
  | 'hottest_engine_cpu'
  | 'hottest_memory'
  | 'primary';

interface ElastiCacheTrendSeries {
  cache_cluster_id: string;
  node_group_id: string;
  role: 'primary' | 'replica';
  selection_reason: TrendSelectionReason;
  buckets: Array<Record<string, unknown>>;
}

interface TrendSelectionSummary {
  primary_total: number;
  primary_returned: number;
  primary_omitted: number;
  primary_context_limit: number;
}

interface ElastiCacheTrendResponse {
  metrics: {
    engine_cpu: ElastiCacheTrendSeries[];
    memory: ElastiCacheTrendSeries[];
  };
  selection_summary: {
    engine_cpu: TrendSelectionSummary;
    memory: TrendSelectionSummary;
  };
  bucket_semantics: Record<string, unknown>;
}
```

Normal and deferred fixtures from backend test MOD-109 are the contract-test
source of truth. `savings_previews` is intentionally absent; the frontend type
must not require or synthesize it.

## 3. Fleet/list behavior

Each row shows resource name/id, account, region, engine/version, current node
type, topology summary, classification, selected headline recommendation, and
monthly/yearly savings.

Headline selection is deterministic:

1. Use the Balanced node-type candidate when present.
2. If no Balanced candidate exists, use the replica recommendation.
3. If both exist, display “2 alternatives” and use the greater of their monthly
   savings only as the row's **potential** value.
4. Never add node-type and replica savings. They are mutually exclusive.
5. DEFERRED and no-candidate rows show zero potential savings and their reason,
   not a green `$0` opportunity.

Fleet savings aggregation uses the same per-row potential rule, so alternatives
are never double-counted. Label the total **potential monthly savings**, not
committed or guaranteed savings.

Required filters: account, region, state, classification, recommendation kind,
and “has recommendation.” Required sorts: potential savings, monthly cost,
classification, name, region, and node count. Search covers resource name/id,
engine, node type, and reason codes.

## 4. Detail information architecture

The section answers, in order:

1. What can change and what might it save?
2. What evidence supports it?
3. What remains uncertain or operationally risky?

### 4.1 Decision header

Show resource name/id, region/state, engine/version, current node type, node and
shard counts, current monthly cost, and an advisory label. Classification is the
most prominent badge for the selected alternative.

### 4.2 Alternative selector and mutual exclusion

When both recommendation kinds exist, render two labeled groups:

- **Change node type** — the tier cards from §4.3.
- **Reduce replicas** — the single card from §4.4.

Maintain one selection state: `node:<target_node_type>` or `replica`. Balanced
is selected by default when it exists; otherwise select the replica card.
Selecting either group deselects the other. Show the persistent message:

> Alternatives are evaluated independently and are not combined. After making
> one change, run a new scan before considering the other.

Every savings headline, classification badge, warning list, evidence panel, and
risk panel reflects only the selected alternative. Never show combined savings,
a combined target, checkboxes, or language such as “apply both.”

### 4.3 Node-type tier cards

Match the EC2 interaction: Conservative / Balanced / Aggressive with Balanced
visually marked **Recommended** and selected by default. Each card shows target
node type, monthly/yearly savings, projected utilization, binding dimension,
classification, and satisfied-tier labels.

When tiers collapse to one target, render one card with all satisfied labels.
A null tier says “No cost-saving option at this headroom.” There is no
performance-change badge because ElastiCache has no CoreMark analogue.

Changing tiers updates all selected-option evidence. CPU headroom uses the trend
series with `selection_reason: hottest_engine_cpu`; memory uses
`hottest_memory`. The UI must find exactly one matching series per dimension.
Series labeled `primary` are shard context and are never substituted for a
missing hottest series. Evidence values are rescaled using the selected
candidate's target capacity; raw values remain available in tooltips. Primary
context is capped per metric by the backend policy and contains the primaries
with the highest decision-scan p99 for that metric. When
`primary_omitted > 0`, disclose “Showing N of M shard primaries,” using
`primary_returned` and `primary_total`; do not imply that omitted shards were
rendered.

### 4.4 Replica-reduction card

Show current → target replicas **per shard**, affected shard count, total nodes
removed, monthly/yearly savings, classification, projected busiest-survivor
EngineCPU, and redundancy disclosure.

The evidence table has one row per replica and shows shard, retained/removed,
p99 read ops, maximum read ops, observed command total, coverage percentage,
observed days, and sample freshness. For ACTIONABLE, explicitly state that every removed replica
had zero observed reads and passed both coverage floors. For CONDITIONAL, name
the exact reason: positive observed reads, insufficient coverage/history,
projection model, or health warning.

Replica selection does not rescale CPU or memory trends to a new node capacity,
because node type is unchanged. Show raw trend context and the survivor
projection separately. Always show that removal reduces read redundancy and
failover headroom.

### 4.5 Evidence and charts

For node-type selection, mirror the EC2 evidence layout with these lanes:

- 15-month EngineCPU trend from `hottest_engine_cpu`;
- 15-month memory trend from `hottest_memory`;
- 60-day CPU, memory, Network In, and Network Out p99/max versus target;
- cache health: evictions, swap, replication lag, connections, traffic
  management, CPU credits, and network allowance events.

Use Daily Maximum as the headline trend and p99/p95 as supporting lines. Draw
target-scaled CPU and memory on separate 0–100% plots; never use dual axes.
Network remains a 60-day decision-window bar, with separate In/Out lanes and
the EC2 baseline/burst visual language. Do not fabricate a ratio when target
capacity is unknown.

Keep the metric's hottest series visually dominant. Primary-context series are
secondary and may be hidden behind a “Shard primary context” toggle to keep the
chart legible. Never issue follow-up requests for or draw primaries omitted by
the backend; render the `selection_summary` truncation disclosure alongside
the toggle or chart legend.

For every metric, show `observed_days`; fewer than seven days is visibly thin
but is disclosure, not a frontend rejection rule. If the trend request fails,
show “Long-term trend unavailable” and retain 60-day decision evidence.

### 4.6 Warnings, risk, and coverage

Render warning code plus user-facing message, grouped by compute, memory,
network, compatibility, migration, and telemetry. CONDITIONAL shows a prominent
“Review required before making a change” banner.

Risk chips use LOW/MEDIUM/HIGH and match EC2 colors. Storage is omitted, not
shown as a misleading LOW. Coverage discloses catalog sources, assumed/unknown
network capacity, unresolved reserved memory, auto-scaling verification, the
availability note, and `availability_validated: false`.

## 5. Deferred, no-candidate, loading, and error states

- **DEFERRED:** show the first `deferred_reason_codes` message prominently and
  all codes in details. Do not render target cards or savings. Keep current
  topology and available raw telemetry as context.
- **No candidate:** explain that no cheaper compatible target passed the hard
  constraints. Show `rejection_summary`; do not label the resource healthy.
- **Replica evidence incomplete:** node-type options remain visible. Explain why
  replica analysis was unavailable using `REPLICA_EVIDENCE_INCOMPLETE` or
  `REPLICA_ROLES_UNRESOLVED`.
- **`AUTO_SCALING_STATE_UNKNOWN`:** render a scope-verification warning and the
  CONDITIONAL cap; never present it as a confirmed absence of auto scaling.
- **404:** “ElastiCache inventory resource not found.”
- **502 trend error:** local trend error only; recommendation remains usable.
- **Loading:** preserve layout with labeled skeletons; do not flash `$0` or an
  ACTIONABLE badge.

Reason-code copy must come from a centralized mapping with an explicit fallback
that displays the unknown code. Unknown codes are never silently dropped.

## 6. Separation from existing Execute controls

The rightsizer is read-only in V1:

- no Apply, Execute, Resize, or “one click” button appears in the rightsizer;
- the existing Runbook action selector/button remains visually separate below
  the recommendation section;
- add the note above that panel: “Runbook actions are separate from the
  rightsizing recommendation and do not automatically apply the selected
  option”;
- selecting a tier or replica alternative does not preselect, mutate, enable,
  or invoke any runbook action;
- the existing confirmation and permission behavior for Execute is unchanged.

This separation must hold even when an older check's recommended action is
`elasticache_downsize`.

## 7. Accessibility and responsive behavior

- All selection cards are keyboard-operable radio choices with a visible focus
  state and an accessible group label.
- Classification and risk never rely on color alone; text labels are required.
- Charts provide a text summary/table and accessible names.
- Warning copy meets contrast requirements in light and dark themes.
- At narrow widths, cards stack and tables scroll inside their containers; the
  page body must not scroll horizontally.
- Currency, percentages, node counts, and timestamps use locale-aware formatters.

## 8. Frontend acceptance and contract tests

Required tests:

1. Parse normal, deferred, and no-candidate MOD-109 fixtures with the TypeScript
   contract; assert `savings_previews` is neither required nor rendered.
2. Balanced is selected by default; collapsed and null tiers render exactly as
   specified.
3. Selecting replica deselects node type and vice versa; displayed savings are
   never summed.
4. Fleet aggregation uses one canonical row and the maximum alternative
   savings, never group-plus-member or node-plus-replica totals.
5. CPU and memory select their matching hottest trend series; returned shard
   primaries are secondary context; a hottest primary is not duplicated.
6. DEFERRED, no-candidate, incomplete-replica, unknown-auto-scaling, trend 502,
   and unknown-reason-code states render explicit copy.
7. Rightsizer selection never changes or invokes the existing Execute control.
8. Keyboard selection, focus order, accessible names, and the chart text
   alternative pass the frontend accessibility test harness.
9. A 100-shard trend fixture renders no more than nine series per metric at the
   default limit and discloses the returned and omitted primary counts.

The UI contract is accepted only when these tests use the backend contract
fixtures rather than independently maintained lookalike JSON.
