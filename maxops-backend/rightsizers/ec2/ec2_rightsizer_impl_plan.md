# EC2 Rightsizer — Implementation Plan (cross-family candidates + network baseline/burst + memory 15-month trend + global specs)

Executor: coding agent. This plan implements the spec additions in
`rightsizers/ec2/ec2_rightsizer_spec.md` §6, §13, §14, §19, §20, §22 and the UI
additions in `rightsizers/ec2/ec2_rightsizer_ui_spec.md` §4.2, §5.1a, §5.3, §7.
It follows the prior §18–§24 phase (scope gating, 60-day window, observed_days
disclosure, CPU confidence trend), which is already shipped — see the spec and
`git log` for that work. Do not redo it.

Run `python -m pytest tests/test_ec2_rightsizer_v1.py tests/test_ec2_memory_metric_discovery.py -q`
after each workstream; all existing tests must stay green.

> **Do not run broad/keyword pytest** (`pytest tests/ -k ...`). Files under
> `tests/integration/` provision real billable AWS resources. Run the named unit
> files only. The `-m "not integration"` default in `pytest.ini` is a safety
> gate — never remove it.

## Goal

Turn the collected network telemetry into a "network may be an issue" signal by
comparing observed demand against **both** the instance-type baseline and peak,
and give memory the same 15-month confidence trend CPU already has. Accuracy is
not the goal — a conservative, honest warning is. Classification-only (Option A):
a burst-covered target is labeled CONDITIONAL; candidate selection is **not**
reordered.

## Context (current code, verified)

- Catalog: `EC2CatalogEntry` + `EC2InstanceCatalog._entry` in
  `app/services/ec2_instance_catalog.py` already read
  `NetworkCards[].BaselineBandwidthInGbps` / `PeakBandwidthInGbps` into
  `network_baseline_mbps` / `network_reliable_max_mbps`, and set
  `network_capacity_kind` = `BASELINE` | `BURST_OR_UP_TO` | `UNKNOWN`.
- **Data gap (verified):** the catalog reads baseline/peak from the
  `ec2_instance_specs` table (`spec_json.NetworkInfo.NetworkCards`), populated by
  `staging_pricing/enrich_ec2_instance_specs.py` via `describe_instance_types`.
  The **shipped** `pricing_artifacts/maxops_pricing.db.xz` does **not** contain
  that table — it has only the `street_pricing_*` tables. So today every target
  resolves to `BURST_OR_UP_TO` / `UNKNOWN` and there is no authoritative
  baseline/peak in production. The feature is inert until Workstream G runs.
  `_load_specs` already degrades safely (`{}`) when the table is absent.
- Warning model: `evaluate_network_warnings` in
  `rightsizers/ec2/ec2_rightsizer/warnings/network_warnings.py` divides p99 by
  `target_baseline_mbps` only (no peak ratio) and, when baseline is missing,
  falls back to `BURST_OR_UP_TO` warnings or qualitative class comparison.
- Hard constraints: `evaluate_network_constraints` in
  `constraints/network_hard_constraints.py` already hard-fails
  `NETWORK_RELIABLE_MAX_EXCEEDED` when p99 > `target_reliable_max_mbps` (peak).
  Change this comparison to `>=` so the top band of spec §6.1 includes exact
  equality.
- Values: `models.py` has `CapacityKind` (add `ASSUMED_BASELINE`) and
  `PerformanceWarningPolicy` (add 3 fields). `RiskLevel`, `ResourceEvaluation`,
  `classify_recommendation` are unchanged in shape.
- Engine wiring: `EC2Rightsizer._evaluate_candidate` /
  `_current_capacity_evidence` in `app/services/ec2_rightsizer.py` build
  `NetworkWarningInput` / `NetworkConstraintInput` and the `network` evidence
  dict (~L163-192, ~L513-636).
- Telemetry: `AWSAdapter.get_ec2_rightsizing_metrics` (60-day, 5-min) collects
  `network_in_bytes` / `network_out_bytes` as `Sum`; `scan_service._enrich_*`
  normalizes to `network_in_mbps` / `network_out_mbps` p99/max.
  `AWSAdapter.get_ec2_confidence_trend` is **CPU-only** and hardcodes
  `AWS/EC2/CPUUtilization`. Memory discovery lives in
  `_ec2_memory_metric_candidates` / `_get_ec2_memory_utilization`.

Item reconciliation resolved in the spec this plan targets: `p99 ≥ reliable
numeric peak` is a hard fail (existing `NETWORK_RELIABLE_MAX_EXCEEDED`); only the
`baseline ≤ p99 < peak` **burst zone** is the new CONDITIONAL case. A qualitative
"up to N Gbps" with no numeric peak is not a reliable maximum and never
hard-fails.

---

## Workstream A — Values (spec §13, §6.3)

**A1. `CapacityKind`** — `rightsizers/ec2/ec2_rightsizer/models.py`
Add `ASSUMED_BASELINE = "ASSUMED_BASELINE"` to the enum (the `QUALITATIVE`
member stays for back-compat but is no longer produced as the primary fallback).

**A2. `PerformanceWarningPolicy`** — same file. Add fields with defaults:
```python
    network_assumed_baseline_enabled: bool = True
    network_assumed_baseline_multiplier: float = 1.0
    network_assumed_baseline_floor_mbps: float = 100.0
```
Extend `__post_init__` validation: `multiplier > 0`, `floor_mbps >= 0`. Do not
put these in the 0–1 ratio loop.

---

## Workstream B — Assumed baseline for "up to" types (spec §6.3)

Compute in the engine, where both the region catalog (for family-max vCPU) and
the policy are available — not in `_entry`, which sees one row at a time.

**B1.** Add a module-level helper in `app/services/ec2_rightsizer.py`:
```python
def assumed_network_baseline_mbps(
    entry: EC2CatalogEntry,
    family_max_vcpus: float | None,
    policy: PerformanceWarningPolicy,
) -> float | None:
    if not policy.network_assumed_baseline_enabled:
        return None
    if entry.network_baseline_mbps is not None:      # already authoritative
        return None
    if entry.network_reliable_max_mbps is None or not family_max_vcpus:
        return None                                   # no numeric peak → UNKNOWN
    if entry.vcpus is None:
        return None
    share = min(entry.vcpus / family_max_vcpus, 1.0)
    value = entry.network_reliable_max_mbps * share * policy.network_assumed_baseline_multiplier
    return max(value, policy.network_assumed_baseline_floor_mbps)
```

**B2.** In `_recommend` (where the region `entries` dict is already loaded), build
`family_max_vcpus: dict[str, float]` = max `vcpus` per **`instance_family(instance_type)`**
(the exact family+generation, e.g. `m5`, `c6i`), **not** `family_class` (which
returns the bare letter class `m` and would wrongly pool `m5`/`m6`/`m7`). Exclude
`.metal` sizes from the max. Key the assumed-baseline denominator on the
**target's own** exact family — cross-family candidate generation (Workstream H)
must not broaden this denominator. Pass the map into candidate evaluation.

**B3.** In `_evaluate_candidate` / `_current_capacity_evidence`, when an entry's
`network_baseline_mbps` is `None`, derive the assumed value and use it as the
effective baseline for the warning model. Track that it was assumed:
- effective baseline used for ratios = `assumed_network_baseline_mbps(...)`
- `capacity_kind = ASSUMED_BASELINE` when an assumed value was produced
- record `assumed_baseline: true` and `assumed_baseline_mbps` in network evidence.

Never feed an assumed baseline into `NetworkConstraintInput.target_reliable_max_mbps`
(that stays the true numeric peak) — assumed baseline drives warnings only, never
a hard fail.

---

## Workstream C — Two-ratio band warning model (spec §6.1, §6.2, §14)

**C1. `NetworkWarningInput`** — `warnings/network_warnings.py`. Add fields:
```python
    target_peak_mbps: float | None = None
    baseline_is_assumed: bool = False
    bandwidth_weighting_default: bool = True
```

**C2. `evaluate_network_warnings`** — rework the baseline branch:
- Keep the existing 40%/70% LOW/MEDIUM/HIGH logic for `p99 < baseline`.
- **New middle band:** when `target_baseline_mbps` is known and
  `max(in_p99, out_p99) >= target_baseline_mbps` **and** (peak unknown or
  `p99 < target_peak_mbps`) → `risk = HIGH`, append
  `NETWORK_SUSTAINED_ABOVE_BASELINE`. (The `p99 ≥ peak` case is left to the hard
  constraint `NETWORK_RELIABLE_MAX_EXCEEDED`; do not also warn-and-pass it.)
- When `baseline_is_assumed` → always append `NETWORK_BASELINE_ASSUMED`; the
  assumed baseline still drives the bands above.
- **Non-default weighting:** when `not bandwidth_weighting_default`, **skip the
  numeric band verdict entirely** (no ratio-driven risk/warnings) and instead set
  `risk = HIGH`, append `NON_DEFAULT_NETWORK_BANDWIDTH_WEIGHTING_REQUIRES_REVIEW`.
  Still evaluate the allowance-event checks below.
- Retire the qualitative-class fallback (`NETWORK_CAPABILITY_*`,
  `NETWORK_CAPACITY_APPROXIMATE`) as the primary path: reach it only when there is
  no baseline, no assumable baseline, and no numeric peak. Keep the code for the
  genuinely-unknown case but it should rarely fire now.
- Evidence: add `network_in_peak_ratio`, `network_out_peak_ratio`,
  `baseline_is_assumed`, and the effective `baseline_mbps` used.

**C3. Messages** — `warnings/messages.py`. Add two entries verbatim from spec §14:
```python
    "NETWORK_SUSTAINED_ABOVE_BASELINE": (
        "Sustained network demand (p99) exceeds the target's guaranteed baseline "
        "bandwidth. The instance would rely on EC2 burst capacity, which is "
        "time-limited and not guaranteed, so network may throttle under sustained "
        "load. Review network behavior before applying this recommendation."
    ),
    "NETWORK_BASELINE_ASSUMED": (
        "No published sustained baseline is available for this target, so the "
        "baseline was estimated from the instance's size and peak bandwidth. Treat "
        "the network comparison as approximate and validate before applying this "
        "recommendation."
    ),
```
`NON_DEFAULT_NETWORK_BANDWIDTH_WEIGHTING_REQUIRES_REVIEW` already exists — reuse it.

---

## Workstream D — Engine wiring + evidence (spec §19, §6.1 no-sum)

`app/services/ec2_rightsizer.py`, `_evaluate_candidate` /
`_current_capacity_evidence`:

**D1.** Populate the new `NetworkWarningInput` fields:
`target_peak_mbps=target.network_reliable_max_mbps`,
`baseline_is_assumed=<from Workstream B>`,
`bandwidth_weighting_default=(str(metadata.get("network_bandwidth_weighting") or "default") == "default")`.
Use the effective (possibly assumed) baseline as `target_baseline_mbps`.

**D2.** Keep inbound and outbound **separate** — never sum. The verdict driver
stays `max(in_ratio, out_ratio)`.

**D3.** Network evidence dict — add `observed_in_max_mbps`, `observed_out_max_mbps`
(60-day max, alongside the existing p99), `target_peak_mbps`, `baseline_mbps`
(effective), `capacity_kind`, `baseline_is_assumed`, and the peak ratios. The
`max` values come from the normalized decision-window summary that scan already
persists (see D4).

**D4.** `scan_service._enrich_ec2_rightsizing_metrics` — confirm `network_in_mbps`
/ `network_out_mbps` summaries persist `max` as well as `p99` (they persist the
full percentile set; expose `max` on the normalized payload the engine reads via
`_normalized_metric`, adding a `_normalized_metric_max` accessor if needed).

---

## Workstream E — Memory 15-month confidence trend (spec §20, §22)

**E1.** Parametrize `AWSAdapter.get_ec2_confidence_trend`
(`app/adapters/aws/adapter.py`) to accept the metric(s) to plot instead of
hardcoding CPU:
- Always include `CPUUtilization` (AWS/EC2, Percent), unchanged.
- Discover the memory source via the existing `_ec2_memory_metric_candidates`
  (reuse — do **not** re-guess). When a usable source exists, add a second metric
  group using its `namespace` / `metric_name` / `dimensions`, with the same
  `{Maximum, p99, p95}` daily queries and the same 6 lookback buckets.
- When no memory source is discovered, omit memory entirely (do not substitute a
  default namespace).
- Bump the trend schema version so cached CPU-only results are not reused (the
  prior plan used `cpu-v2`; use e.g. `trend-v3`).

**E2.** Return shape: keep `{daily, buckets}` for CPU and add a parallel
`memory` block (or a `{cpu: {...}, memory: {...}|null}` envelope — pick one and
document it in the docstring). Memory is a percentage gauge, so `Maximum` stays
the honest headline exactly like CPU; do not use Average.

**E3.** Service/route — `EC2Rightsizer.get_confidence_trend` and
`GET /ec2/rightsize/{inventory_id}/trend` already exist (prior plan D3). Extend
the envelope to carry the memory block. `observed_days` disclosure (spec §22)
already self-corrects for short memory history — no gate.

**E4.** **Network stays out of the 15-month trend.** Do not add NetworkIn/Out to
`get_ec2_confidence_trend`. Network peak evidence is the 60-day decision-window
`max`/`p99` from Workstream D only.

---

## Workstream F — Tests (`tests/test_ec2_rightsizer_v1.py`, `tests/test_ec2_memory_metric_discovery.py`)

Warning/model (spec §16):
- p99 above a known baseline but below peak → `NETWORK_SUSTAINED_ABOVE_BASELINE`,
  network risk HIGH, classification CONDITIONAL (not REJECTED).
- p99 equal to or above a **reliable numeric peak** → `NETWORK_RELIABLE_MAX_EXCEEDED`,
  REJECTED (existing behavior preserved).
- No published baseline + numeric peak + vCPU/family → assumed baseline
  `peak * min(vCPU/family_max_vCPU, 1) * multiplier`, floored; `ASSUMED_BASELINE`
  capacity_kind + `NETWORK_BASELINE_ASSUMED`.
- Assumed baseline routes to CONDITIONAL, never ACTIONABLE, never hard-fails.
- Non-default `network_bandwidth_weighting` → suppresses the numeric verdict,
  emits `NON_DEFAULT_NETWORK_BANDWIDTH_WEIGHTING_REQUIRES_REVIEW`, no ratio-driven
  warning.
- Inbound and outbound evaluated separately; a high inbound + low outbound drives
  the verdict off inbound and the two are never summed.
- Changing `network_assumed_baseline_multiplier` changes warnings but not the
  normalized network metrics.

Trend (spec §23):
- Trend requests CPUUtilization and, when the fake client exposes a memory metric,
  the discovered memory series; never network. ≤ 5 stats per metric.
- Memory omitted when no source discovered.
- Given an injected daily spike, memory `Maximum` returns the spike (Max preserves
  peaks); Average does not.

---

## Workstream G — Populate `ec2_instance_specs` baseline/peak data (prerequisite)

Without this, the whole feature runs on assumed/unknown values (see the Context
data gap). Treat instance specifications as global product data keyed by
`instance_type`; regional prices remain in `street_pricing_ec2`.

**G1. Global schema and transactional replacement.** Redefine
`ec2_instance_specs` as `(instance_type PRIMARY KEY, spec_json, source_region,
schema_version, generated_at)`. Fetch the complete `DescribeInstanceTypes`
pagination before opening the replacement transaction, filter it to instance
types priced anywhere in the DB, and atomically replace the derived table. A
failed AWS fetch must preserve the previous table. Catalog loading also accepts
the prior regional schema, preferring `us-east-1` when duplicate rows exist.

**G2. Fetch once from `us-east-1`.** The enrichment CLI has no multi-region
option and always creates one EC2 client in `us-east-1`:
```bash
python -m staging_pricing.enrich_ec2_instance_specs \
    --database maxops_pricing.db
```
Requires AWS credentials with `ec2:DescribeInstanceTypes`. Types absent from the
`us-east-1` response remain coverage gaps and route through assumed/unknown
capability behavior; do not query a secondary region.

**G3. Verify global coverage.** Release metadata records `source_region:
us-east-1`, total structured rows, and the counts with baseline/peak on any
`NetworkCard` (not only card zero). Low baseline coverage is expected for older
families; nonzero peak coverage proves enrichment ran.

**G4. Repackage and release the pricing DB** via the existing
`staging_pricing/package_pricing_release.py` flow so
`pricing_artifacts/maxops_pricing.db.xz` ships **with** the `ec2_instance_specs`
table. Confirm `tests/test_pricing_release_artifacts.py` still passes; if it
asserts a table allow-list, add `ec2_instance_specs` to it.

---

## Workstream H — Cross-family candidate generation with family gate (spec §1, §11)

Widen candidate generation beyond the current same-family-only filter, but gate
which *families* are eligible so savings-first ranking cannot surface unsuitable
types.

**H1. Remove the exact-family filter** in `EC2Rightsizer._recommend`
(`app/services/ec2_rightsizer.py` ~L427-430). Today it skips any target whose
`instance_family` differs from the current. Replace the family check with the
architecture-overlap check that already follows it (keep that), so every cheaper,
region-priced, architecture-compatible type is a candidate. Architecture
migrations remain out of scope: a target with no architecture overlap, or unknown
architecture evidence, is skipped and counted under `ARCHITECTURE_INCOMPATIBLE`.

**H2. Family gate (deny-list with same-class exception).** Add a helper and apply
it right after the architecture check:
```python
BURSTABLE_CLASSES = frozenset({"t"})
SPECIALIZED_CLASSES = frozenset({"p", "g", "inf", "trn", "dl", "f", "vt", "hpc", "u"})
GATED_CLASSES = BURSTABLE_CLASSES | SPECIALIZED_CLASSES   # configurable

def _family_gate_blocks(current_type: str, target_type: str) -> bool:
    tgt = family_class(target_type)          # bare letter class: "t", "p", "m", ...
    if tgt not in GATED_CLASSES:
        return False                          # general-purpose/compute/mem → allowed
    return family_class(current_type) != tgt  # allow only if current is same class
```
Rationale: the engine has **no CPU-credit awareness**, so a burstable `t3` can
pass coremark/memory yet throttle under sustained load; accelerator/specialized
families are niche and must not be proposed as generic downsizes. A target in a
gated class is allowed only when the current instance is already in that same
class and architecture (e.g. `t3 → t3a`, `p3 → p4`). Record skips as
`FAMILY_NOT_ELIGIBLE` in the
rejection tally. Expose `GATED_CLASSES` as configurable policy.

**H3.** Ranking stays savings-first and `candidate_limit` is unchanged
(Option A — no selection steering). Cross-family same-arch moves classify as
normal resizes (ACTIONABLE/CONDITIONAL); they are **not** OPPORTUNITY (that stays
reserved for architecture migrations, still out of scope). Optionally record
`family_changed: true` in candidate evidence when
`instance_family(target) != instance_family(current)`.

**H4. Tests** (`tests/test_ec2_rightsizer_v1.py`):
- A cheaper same-arch different-family type (e.g. `m5 → c6i`) is evaluated.
- A burstable target (`c5 → t3`) is gated out with `FAMILY_NOT_ELIGIBLE`; a
  burstable-to-burstable move (`t3.xlarge → t3.large`) is allowed.
- An accelerator target for a general current type is gated out; same-class
  (`p3 → p4`) is allowed.
- An architecture-incompatible target is still skipped as
  `ARCHITECTURE_INCOMPATIBLE`.
- The assumed-baseline denominator uses the target's exact family, not the letter
  class (a `c6i` target does not pool `c5`/`c7i` vCPU maxima).

---

## Workstream I — Tiered recommendations (spec §25) — NEW, builds on shipped A–H

Workstreams A–H are already implemented and shipped. This workstream is the new
scope: present up to three sizing options (Conservative / Balanced / Aggressive)
per instance. It is a **selection layer over the existing ranked candidates** — no
re-run of selection.

> **Note for the implementer (§25.5 rewrite — coremark performance delta).**
> The base tiering (I1–I6) plus the `performance_ratio` field and the
> missing-metric floor fix are already implemented. The remaining delta from this
> update is **one new field and its presentation rule**:
> - Add **`performance_change_pct = (target.coremark - current.coremark) /
>   current.coremark * 100`** to each candidate/tier option, right beside the
>   existing `performance_ratio` (in `_evaluate_candidate` / `_tier_option`).
>   `None` when either coremark is null.
> - coremark is a **multi-thread score scaled to total vCPUs** (whole-instance
>   throughput), so this percentage is the total-throughput delta — positive for a
>   higher-throughput target, negative for a downsize.
> - **Presentation (spec §25.5):** surface it prominently **only when positive**
>   ("~+18% CPU performance"); when negative, do **not** headline it — the
>   `projected_util` headroom story leads and the raw delta stays in the detail
>   view. Never fabricate when null; never block the recommendation.
> - No change to `projected_util`, tier bucketing, or the Balanced-equals-pre-
>   tiering guarantee — this is additive.

**I1. Tier policy** — `app/services/ec2_rightsizer.py`, `EC2ComputePolicy` (has
`cpu_target_ratio=0.70`, `memory_target_ratio=0.70`). Add:
```python
    tier_ratios: tuple[tuple[str, float], ...] = (
        ("conservative", 0.55),
        ("balanced", 0.70),
        ("aggressive", 0.85),
    )
```
Validate each ratio in (0, 1]; the `balanced` ratio must equal `cpu_target_ratio`
so the Balanced option matches today's single recommendation. Keep it
configurable.

**I2. Loosen the generation gate to the Aggressive ratio.** In `_recommend`, the
CPU/memory candidate gate currently uses `required_coremark` / `required_memory`
computed at `cpu_target_ratio` (0.70) via `_cpu_memory_requirements`. Compute the
generation gate at the **max tier ratio** (Aggressive, 0.85) so smaller options
are included. Add an optional `target_ratio` override to `_cpu_memory_requirements`
(default keeps 0.70 for existing callers) or compute the loosened requirement
inline. All other filters (architecture, family gate, network, EBS, scope,
positive savings) are unchanged.

**I3. Per-candidate tiering fields** — in `_evaluate_candidate`, add to the
returned candidate:
```python
projected_cpu_util = (cpu_p99/100) * (current.coremark / target.coremark)   # None coremark -> vCPU ratio
projected_memory_util = (current.memory_mib * memory_p99/100) / target.memory_mib
projected_util = max(x for x in (projected_cpu_util, projected_memory_util) if x is not None)
performance_ratio = target.coremark / current.coremark            # None when either coremark is None
performance_change_pct = (target.coremark - current.coremark) / current.coremark * 100   # None when either is None
binding_dimension = argmax over {"cpu": projected_cpu_util, "memory": projected_memory_util,
                                 "network": network baseline util, "storage": ebs util}
```
`coremark` is a multi-thread score scaled to total vCPUs (whole-instance
throughput). `performance_change_pct` is the total-throughput delta (spec §25.5):
surface it prominently only when **positive**; when negative (normal downsize) do
not headline it — the headroom (`projected_util`) story leads. Both fields are
`None` when either coremark is null and must not block the recommendation.
Reuse `cpu_p99 = _normalized_metric(metadata,"cpu_percent")` and
`memory_p99 = _normalized_metric(metadata,"memory_percent")` (same source the gate
uses). The network/storage limiting ratios are already in the network/EBS
evidence dicts. Record all four fields in the candidate.

**I4. Tier assembly** — in `_recommend`, after the candidate list is built and
savings-ranked, construct the `tiers` object:
```python
def _pick_tier(candidates, ratio):
    eligible = [c for c in candidates if c["projected_util"] is not None
                and c["projected_util"] <= ratio]
    return min(eligible, key=lambda c: (-c["monthly_savings"], c["target_instance_type"]), default=None)
    # cheapest == most savings == smallest that still clears the headroom bar
```
Build `{name: _pick_tier(candidates, ratio) for name, ratio in policy.tier_ratios}`.
Dedup by `target_instance_type` for presentation (label each distinct target with
the tiers it satisfies — a `satisfied_tiers` list on the option). `default` =
`"balanced"` when the balanced pick exists, else the most conservative present
tier. When all present tiers resolve to one target, the caller should render a
single recommendation (expose enough for the UI to detect this: identical
`target_instance_type` across tiers).

**I5. Response envelope** — add to the `_recommend` return dict:
```python
"tiers": {
    "conservative": <option-or-None>,
    "balanced": <option-or-None>,
    "aggressive": <option-or-None>,
    "default": "balanced",
},
```
Keep `recommendations` as-is (savings-ranked; it widens because generation now
uses the Aggressive gate). Each tier value references a candidate already in
`recommendations` (by `target_instance_type`) — do not duplicate the full object
if the UI can look it up; carrying `target_instance_type` + the tiering fields is
enough.

**I6. Tests** (`tests/test_ec2_rightsizer_v1.py`):
- `projected_cpu_util` exactness and tier placement at the ratio boundary.
- Three distinct targets across a wide coremark range → three distinct tiers;
  a single-viable-downsize workload collapses to one target across all tiers.
- Conservative is `null` when no cushioned option still saves money.
- The Balanced option equals the pre-tiering single recommendation (regression
  guard: run with the default policy and assert the Balanced target equals the
  top-savings candidate under the old 0.70 gate).
- `performance_ratio` and `performance_change_pct` computed from coremark; `null`
  when either coremark is `null` without breaking tier assignment (falls back to
  vCPU ratio for cpu util). `performance_change_pct` is positive for a
  higher-throughput target, negative for a downsize.
- `binding_dimension` returns the highest limiting-ratio dimension.
- An Aggressive option is CONDITIONAL while Conservative is ACTIONABLE for the
  same instance (independent per-tier classification).

**I7. UI** — `ec2_rightsizer_ui_spec.md` §4.1a / mock: render up to three tier
cards (Balanced highlighted as default), each showing target, savings, projected
utilization, binding dimension, and per-tier classification. When
`performance_change_pct > 0`, show a prominent "~+X% CPU performance" confidence
badge (CPU-throughput-only caveat); when negative, suppress it / keep it in the
detail view and let the headroom story lead. Collapse to one card when tiers
resolve to the same target. Track as a UI task.

---

## Suggested order & dependencies

**A–H are implemented and shipped** (network baseline/burst, assumed baseline,
memory 15-month trend, cross-family + family gate, global specs). Their order is
retained below for reference. **Workstream I (tiering) is the new work.**

1. **A** (values) — shipped.
2. **B** (assumed baseline, exact-family denominator) — shipped.
3. **C** (warning model) — shipped.
4. **D** (engine wiring + evidence) — shipped.
5. **H** (cross-family generation + family gate) — shipped.
6. **E** (memory trend) — shipped.
7. **G** (populate global specs) — shipped.
8. **F** (tests for A–H) — shipped, green.
9. **I** (tiered recommendations) — **new**; builds on the shipped `_recommend`
   candidate loop. I2 (loosen gate to Aggressive) and I4 (tier assembly) are the
   core; I3 adds per-candidate fields; I6 tests; I7 UI. Land I3→I2→I4→I5→I6.

## Out of scope (not this plan)
- Active selection steering (Option B). Burst-covered targets are CONDITIONAL;
  candidate ranking is unchanged.
- **Architecture migrations** (e.g. x86 → Graviton). Candidate generation is
  cross-family but **same-architecture only** (Workstream H); Graviton/OPPORTUNITY
  remains roadmap.
- CPU-credit / burstable-suitability modeling. Instead of modeling it, burstable
  and specialized families are gated out of candidate generation (H2).
- A 15-month **network** trend / peak reconstruction (dishonest past 63-day
  5-minute retention — see spec §20).
- A standalone `DescribeInstanceTypes` → CSV export. Redundant with the
  `ec2_instance_specs` table populated in Workstream G; coverage is the G2 query.
- Frontend: the mock in `ec2_rightsizer_ui_mock.html` implements
  `ec2_rightsizer_ui_spec.md` §5.1a (memory sibling chart), §5.3 (baseline-anchored
  network bars with burst zone), §4.2 captions, and §7 fixtures. Track as a UI task.

## Acceptance
- Existing `tests/test_ec2_rightsizer_v1.py` and
  `tests/test_ec2_memory_metric_discovery.py` stay green, plus the new tests in F.
- A candidate whose sustained p99 sits between the target's baseline and peak is
  CONDITIONAL with `NETWORK_SUSTAINED_ABOVE_BASELINE`; one equal to or above a reliable peak is
  REJECTED; an "up to" target with no numeric baseline gets a flagged
  `ASSUMED_BASELINE` and is never ACTIONABLE.
- Network evidence carries separate in/out p99 **and** max, effective baseline,
  peak, capacity_kind, and the assumed flag.
- `GET /ec2/rightsize/{id}/trend` returns CPU **and** (when available) memory
  15-month series from the discovered memory source, with network absent.
- The released `pricing_artifacts/maxops_pricing.db.xz` contains a populated
  global `ec2_instance_specs` table sourced from `us-east-1`, and release metadata
  shows non-zero `with_network_peak` coverage — so supported targets resolve to
  `BASELINE` rather than every target degrading to assumed/unknown.
- Cross-family same-architecture candidates are evaluated (e.g. `m5 → c6i`), while
  burstable and specialized families are gated out unless the current instance is
  already that class; the assumed-baseline denominator uses the target's exact
  family, not the letter class.
- **(Workstream I)** The recommendation envelope carries a `tiers` object with
  Conservative / Balanced / Aggressive options (0.55 / 0.70 / 0.85), Balanced as
  default; the Balanced option is byte-for-byte the pre-tiering single
  recommendation; tiers dedup and collapse to a single recommendation when they
  resolve to the same target; each option reports `projected_util`,
  `performance_ratio` (coremark, CPU-only caveat), and `binding_dimension`.
