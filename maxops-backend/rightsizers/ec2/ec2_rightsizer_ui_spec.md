# EC2 Rightsizer — Mock UI Spec (for a self-contained HTML artifact)

## 0. Goal & audience

Build a **single, self-contained mock page** that presents an EC2 rightsizing
recommendation with enough clear, honest detail that a cloud/FinOps engineer can
**make the resize decision from this page alone**. The centerpiece is the
evidence: utilization metric plots (a 15-month trend plus a recent decision
window) shown next to the target's capacity, so "is it safe to downsize?" is
answerable at a glance.

This is a **mock** — use the dummy data in §7. Do not call any backend.

## 1. Deliverable & hard constraints

- One HTML file, rendered as an artifact. **Strict CSP: no external anything** —
  no CDN scripts, fonts, or chart libraries. Inline all CSS/JS. **Charts must be
  hand-rolled inline SVG** (or `<canvas>`); do not import Chart.js/D3/etc.
- **Theme-aware** (light + dark) and **responsive**; wide content (tables,
  charts) scrolls inside its own container, never the page body.
- Load the **dataviz** and **artifact-design** skills before writing chart or
  layout code. Follow the dataviz palette/validator for all chart colors and the
  semantic color mapping in §6.
- Synthetic series (the 455-day daily line) should be generated in-page from a
  **seeded PRNG** (e.g. mulberry32 with a fixed seed) so the mock is stable
  across reloads — do not use bare `Math.random()` for the trend shape.

## 2. Mental model (what the page must convey)

Three questions, answered top to bottom:
1. **What's recommended and what does it save?** current → target, $/mo and $/yr,
   classification.
2. **Is it safe?** the headroom claim shown *and drawn* in one place (§4.2), what
   still needs review (§4.3), risk by resource dimension (§4.4), and the
   supporting utilization evidence (§4.5).
3. **How much do we actually know?** telemetry disclosure (days of data),
   constraint coverage, and scope/availability caveats.

The page must never overclaim. A CONDITIONAL or DEFERRED result is a first-class
outcome shown with its reason, not hidden.

## 3. Information architecture

Two-pane layout (stack to one column below ~900px):

- **Left rail — instance list (master).** One row per dummy instance: name,
  instance id, current type, a classification badge, and headline monthly
  savings (or the deferral reason). Selecting a row loads the detail pane.
- **Right pane — recommendation detail (§4).**

Top of page: a compact header with product name and a one-line honest framing
("Recommendations are advisory; availability and exact app performance are not
guaranteed — see notes").

## 4. Detail pane — sections (in order)

### 4.1 Decision header
- Instance name, id, region, state.
- **Classification badge** (see §6) — the single most prominent element.
- current type → target type, with vCPU/memory deltas.
- **Savings**: `$X/mo` and `$Y/yr`, and `% reduction`. If DEFERRED/INSUFFICIENT_DATA,
  replace savings with the reason.

### 4.1a Recommendation options — tiers (backend spec §25)
Up to three sizing options let the user choose savings vs. headroom. Render as a
row of cards; the rest of the detail pane (§4.2 onward) describes the **selected**
option, defaulting to Balanced.

- **Three tiers:** Conservative / Balanced / Aggressive (0.55 / 0.70 / 0.85 target
  utilization). **Balanced is visually highlighted as the default/recommended.**
  Aggressive = most savings/least cushion; Conservative = most cushion/least
  savings.
- Each card shows: target type, `$X/mo` savings, **projected utilization**,
  a **performance delta** (see below), **binding dimension** ("limited by memory"),
  and the option's **own classification badge** (an Aggressive card may be
  CONDITIONAL while Conservative is ACTIONABLE — show each honestly, never
  averaged).
- **Performance delta (`performance_change_pct`, backend spec §25.5):** coremark
  is a whole-instance CPU-throughput score. When the value is **positive**, show a
  prominent **"~+X% CPU performance"** confidence badge (e.g. a same-size
  newer-gen swap that is cheaper *and* faster). When **negative** (normal
  downsize), **do not** show a red "−X%" — let projected utilization carry the
  "still comfortable" story, and keep the raw delta in the detail view only. Show
  nothing (not a fabricated number) when `performance_change_pct` is null
  (coremark missing). Caveat: CPU integer throughput only, not application
  performance; render directionally ("~").
- **Dedup / collapse:** when tiers resolve to the same target, show one card
  labeled with all satisfied tiers (`Balanced · Aggressive`). When all present
  tiers collapse to one target, render a **single recommendation** and omit the
  tier row entirely. A `null` tier (no cost-saving option at that headroom) shows
  a muted "no option at this headroom," not an empty card.
- Selecting a card re-scopes §4.2–§4.5 to that option (Chart A's target-scaled
  view, the network/EBS bars, warnings, and risk all reflect the chosen tier).
- Data: `tiers.{conservative,balanced,aggressive}` + `tiers.default` from the
  recommendation envelope; each option's fields come from its candidate in
  `recommendations` (`projected_util`, `performance_ratio`,
  `performance_change_pct`, `binding_dimension`, `satisfied_tiers`,
  classification).

### 4.2 Why this recommendation — summary + evidence chart (merged)
This section is one unit: the headroom claim and the chart that proves it sit
together, so the reader never has to hold a number in their head while scrolling
to find the plot. It describes the **selected tier** (default Balanced).

- The **headroom sentence** (the key persuasion element), immediately followed by
  **Chart A** (§5.1) rendered in the same card, e.g.:
  > "Over the last 15 months, max CPU was **22%** (p99 **9%**).
  > The recommended **m5.large** leaves ~**4×** headroom against even that peak."
  >
  > *[Chart A renders directly below, on the same capacity scale the sentence
  > just described, so the claim is visually checkable, not just asserted.]*
- Chart A's whole point in this position is to let the reader visually compare
  **historical usage against the target's headroom** in one glance — see §5.1 for
  how the chart itself encodes that comparison (it does not just repeat the
  current instance's raw history next to a caption; the plotted values and the
  sentence must agree, from the same underlying numbers).
- **Memory** gets the same treatment via Chart A′ (§5.1a) rendered beside Chart A,
  with its own headroom sentence when a memory series exists (e.g. "Over the last
  ~40 days of memory data, max was **61%** (p99 **54%**); the target leaves ~1.3×
  headroom"). When no memory series exists, omit the sentence and show the
  empty-state chart.
- **Network** carries a **60-day** reassurance line (not 15-month — see backend
  §20), both directions separate, next to Chart C's network bars, e.g.:
  > "Over the last 60 days, Network In peaked at **640 Mbps** (p99 **180**) —
  > about **24%** of the target's **750 Mbps** baseline, with burst to 10 Gbps
  > available. Ample headroom."
- For CONDITIONAL: state exactly which dimension needs review and why (e.g.
  "Network capacity is burst/'up to' rated — sustained demand can't be proven").
  This note renders below the chart, still inside this card.
- For OPPORTUNITY (Graviton): state that this is an architecture migration with
  required validation, not a drop-in resize, followed by the validation checklist.
  Still inside this card.
- For DEFERRED: no target was evaluated, so there is nothing to show headroom
  against. The chart still renders (for context — see §5.1's no-target mode) but
  the sentence is replaced with the deferral explanation.

### 4.3 Warnings & required review steps
Placed immediately after the why/evidence card — once the reader has seen the
claim and the proof, the very next thing they need is what still requires their
judgment, before the risk breakdown or supporting evidence.
- List each warning as `{code}` + human message (§7 provides messages). Group by
  dimension.
- A prominent "Review required before applying" banner when classification =
  CONDITIONAL.

### 4.4 Risk by dimension
A compact 7-cell panel (chips): **telemetry, compute, memory, network, storage,
compatibility, migration**, each LOW/MEDIUM/HIGH, plus an **overall** chip. Use
the §6 risk colors. Make explicit that overall = max(dimensions) and that a HIGH
dimension routes to CONDITIONAL, not rejection. Show `reason_codes` as small tags.

### 4.5 Supporting utilization evidence — CHARTS B & C
Chart A already carried the headline evidence in §4.2. This section is the
supporting detail underneath it:
- **B. Lookback buckets** (CPU): 30d / 90d / 120d / 6mo / 12mo / 15mo summary.
- **C. Decision-window utilization** (last 60 days): CPU %, network in/out Mbps,
  EBS IOPS & throughput — observed p99 vs the target's capacity, as threshold bars.

### 4.6 Telemetry disclosure
- Per always-on metric (CPU, network in, network out): **present?** and
  **"~N days considered"**. Flag `thin_data` (< 7 days) prominently (amber). This
  is disclosure, not a gate — the recommendation still stands; the user decides.

### 4.7 Coverage & caveats (collapsible / secondary)
- **Constraint coverage**: which target capabilities were known vs assumed
  (numeric network baseline? reliable network max? EBS attachment limit?
  capability source = structured vs pricing-attributes).
- **Scope/availability notes**: the availability disclaimer; scope policy in
  effect (e.g. instance-store default-defer); for DEFERRED, the deferral reason.

## 5. Chart specifications

General: label axes and units; provide a legend; on hover show a tooltip with the
exact value + date; degrade gracefully when a series is `null` (see the 15-month
percentile note). Respect theme colors. Keep the headline series visually dominant.

### 5.1 Chart A — 15-month CPU confidence trend, scaled to the target (line)

This chart has two modes, chosen by whether a target exists. **Never render both
a raw scale and a target scale on two y-axes at once** — that is a dual-axis
chart and it's the #1 chart-honesty mistake. Pick one scale per render.

**Target mode** (ACTIONABLE / CONDITIONAL / OPPORTUNITY — a `target_headroom_x`
exists):
- Re-express the whole 15-month series as **% of the target's capacity**, not
  the current instance's. This is what actually answers "is it safe": one
  consistent scale where **100% is the target's ceiling**, so the reader compares
  history directly against the thing they're deciding about, in the same plot.
- Scale factor: `target_peak_pct = 100 / target_headroom_x` (the value the
  headroom sentence already states); `scale = target_peak_pct / observed_peak_max`
  where `observed_peak_max` is this series' own all-time daily maximum. Multiply
  every plotted value (`maximum`, `p99`, `p95`) by `scale`. By construction the
  scaled peak always lands exactly at `target_peak_pct` and never exceeds 100 —
  this is what makes the transform safe to compute from any `target_headroom_x`
  without producing an off-chart or nonsensical value.
- Y-axis fixed **0–100**, with light background bands at the same 40%/70%
  thresholds used elsewhere (LOW/MEDIUM/HIGH), and a bold reference line at
  **100% labeled "target capacity ceiling."** The gap between the plotted peak
  and that line **is** the headroom, drawn, not just stated.
- Tooltip on hover shows **both** numbers for the hovered day: the target-scaled
  value (primary, bold) and the raw observed value as % of the *current*
  instance (secondary) — the transform must never hide the original number.
- Axis subtitle: "% of target capacity — historical usage rescaled to
  `<target_type>`."

**Raw mode** (DEFERRED, or any case with no target): no rescaling is possible or
meaningful. Render the series as **% of the current instance's capacity**, y-axis
auto-fit to the observed max (never clipped), no ceiling line, no bands. Axis
subtitle: "% of current instance capacity — no target evaluated."

Both modes keep:
- **Three series**, with **Maximum as the bold headline** line; p99 and p95
  lighter/thinner. Daily **Max** is the honest ceiling ("even the worst hour").
  Never show daily *Average* as a headline.
- An annotated all-time peak point. In **target mode** this is a two-line,
  **always-visible callout** (not hover-only) showing the translation itself:
  the current instance's raw peak on one line ("current peak: 22%") and the
  target-scaled result on the next ("→ 24% on target"). The reader must see how
  the current peak becomes the new peak without hovering for it — the hover
  tooltip (§ above) still repeats both numbers for every other day, but the peak
  itself is the one point called out by default. In **raw mode** it's a single
  label ("peak: 22%") since there's nothing to translate to.
- Data shape per point: `{ date, maximum, p99, p95 }` (raw, pre-scale — the scale
  factor is applied at render time, never mutates the stored series).

### 5.1a Chart A′ — 15-month memory confidence trend (sibling of Chart A)

Memory renders as a **separate sibling chart beside Chart A**, not overlaid on it
— two capacity scales on one plot is the dual-axis honesty mistake §5.1 warns
against, and a sibling makes it obvious when memory data is short or absent.

- Same construction as Chart A (target vs raw mode, Maximum headline with p99/p95
  lighter, 40%/70% bands, target ceiling line, annotated peak). Memory is a
  percentage gauge, so daily Max is an honest ceiling exactly like CPU.
- The series comes from the **discovered** memory source (backend §20); the chart
  labels which metric/namespace it plotted.
- **Absent/short data is expected and shown honestly:** when no memory source was
  discovered, render an empty-state ("no memory telemetry — CloudWatch agent
  required") rather than a blank axis. When the agent ran for less than the
  horizon, plot only the available span and let the `observed_days` disclosure
  (§4.2 / backend §22) state it — do not pad to 15 months.

### 5.2 Chart B — lookback buckets (small multiples or grouped bars)
- Six buckets: `30d, 90d, 120d, 6mo (180d), 12mo (365d), 15mo (455d)`.
- Each shows **max** (from the trailing daily series) and **p99/p95** (latest
  complete aligned window). Render `null` percentiles honestly as "n/a — window
  incomplete" (this legitimately happens at 12mo/15mo). Include a footnote with the
  `bucket_semantics`: *max = trailing window; percentiles = latest complete aligned window.*

### 5.3 Chart C — decision-window utilization vs capacity (threshold bars)
- Last 60 days. One horizontal bar per resource lane: **CPU%**, **Network In (Mbps)**,
  **Network Out (Mbps)**, **EBS IOPS**, **EBS throughput (MiB/s)**.
- Each bar = observed **p99** as a fraction of the **target capacity** (baseline
  where known). Draw the **40%** and **70%** threshold markers (LOW/MED/HIGH bands
  from the policy). Color the bar by the resulting risk band (§6).

**Network lanes are baseline-anchored with a burst zone.** The two network lanes
encode the three-band model from backend spec §6.1 in the bar geometry itself:
- Fixed anchors: **baseline** sits at a fixed reference position (~70% of bar
  width), **peak** ("up to") at the far right. The axis is baseline-relative, not
  raw Mbps and not peak-normalized — baseline is the decision line.
- Two marks per bar: **p99** as the solid bar (the sustained demand that matters),
  **max** as a lighter tick/◆ (secondary — noisier, like Max under p99 in Chart A).
- Bands: left of baseline = LOW/safe (with the 40%/70%-of-baseline markers inside
  it — the reassurance zone); baseline→peak = amber **burst zone** ("sustained
  above baseline, relying on time-limited credits"); at/near peak = red.
- Three data cases, one layout:
  - **Numeric baseline + peak** → full bar as above.
  - **Assumed baseline** (backend §6.3) → same bar, baseline marker drawn
    **dashed/"estimated"** and labeled `NETWORK_BASELINE_ASSUMED`; never shown as
    authoritative.
  - **Nothing known** (no numeric peak) → "capacity approximate" style; do not
    fake a ratio.
- In and Out stay separate lanes; never sum them.
- *Optional:* a small 60-day daily-max network sparkline per direction may sit
  beside each bar (honest inside the 63-day 5-minute window). The bar carries the
  decision; the sparkline is nice-to-have, not required.

- Where a non-network target baseline is unknown (burst/"up to"), show the bar in
  a "capacity approximate" style and label it — do not fake a ratio.

## 6. Visual language

Use dataviz-skill accessible colors; map by **semantic intent**, consistent in
light/dark:
- **Classification badges:** ACTIONABLE = positive/success; CONDITIONAL = caution/amber;
  OPPORTUNITY = informational/violet; DEFERRED = neutral/grey; INSUFFICIENT_DATA =
  neutral/amber; REJECTED = critical/red (only in rejection summaries).
- **Risk chips:** LOW = success, MEDIUM = caution, HIGH = critical, plus a distinct
  "n/a" for unknown.
- Savings figures in a calm positive tone; never make caution/critical states look
  celebratory.

## 7. Dummy data set

Eighteen instances span six behavior groups, with three instances in each
group. Sections 7.1–7.6 define the detailed rendering template for each group;
the mock's machine-readable `INSTANCE_GROUPS` data source adds two catalog-backed
variants per template. Each has recommendation fields + metric
data. For the 455-day daily CPU series, generate from the described **profile**
(seeded), and use the given **buckets** and **decision-window** values verbatim.
The companion `MOCK_CATALOG` map carries the real packaged vCPU, memory,
architecture, price, network, EBS, and instance-store facts consumed by those
records; a named unit test checks every tuple against `maxops_pricing.db`.

New fields every fixture carries (added for the network baseline/burst + memory
work):
- Each network lane in `decision_window` gets `max` (alongside `p99`),
  `target_baseline`, `target_peak`, and `capacity_kind`
  (`BASELINE` | `ASSUMED_BASELINE` | `BURST_OR_UP_TO` | `UNKNOWN`).
- A `memory_percent` entry in `telemetry_summary`, a `memory_trend_profile`, and
  a `memory_buckets` block (same shape as CPU `buckets`) so Chart A′ renders. A
  fixture with no memory agent sets `memory_percent.present: false` and omits the
  trend/buckets, exercising the empty-state.

The six groups and their required catalog-backed variants are:

| Behavior group | Detailed template | Additional instances |
| --- | --- | --- |
| ACTIONABLE clean resize | `web-prod-01` (`m5.xlarge → m5.large`) | `m6i.2xlarge → m6i.xlarge`; `r6i.2xlarge → r6i.xlarge` |
| CONDITIONAL network review | `api-prod-02` (`c5.2xlarge → c5.xlarge`) | `c6i.2xlarge → c6i.xlarge`; `m6i.2xlarge → m6i.xlarge` |
| OPPORTUNITY Graviton migration | `batch-03` (`r5.2xlarge → r6g.2xlarge`) | `m5.2xlarge → m7g.2xlarge`; `c5.2xlarge → c7g.2xlarge` |
| DEFERRED managed resource | `asg-worker-04` (`m5.large`, ASG) | `c6i.xlarge`, ECS; `m6i.2xlarge`, Kubernetes/EKS |
| CONDITIONAL thin telemetry | `new-svc-05` (`t3.large → t3.medium`) | `t3a.xlarge → t3a.large`; `m6i.xlarge → m6i.large` |
| DEFERRED instance store | `data-06` (`i3.xlarge`) | `i3.2xlarge`; `d3.xlarge` |

Instance vCPU, memory, architecture, instance-store capacity, and the displayed
`us-east-1` Linux price relationships come from the packaged catalog. Workload
telemetry remains deterministic mock data inherited from the behavior template.
Each record exposes `catalogMode` (`REAL_CATALOG` or
`REAL_CATALOG_PRICE_OVERRIDE`) in Coverage & caveats. The UI must group the rail
by behavior and calculate the count from the data source rather than hardcoding
six or eighteen.

### 7.1 `web-prod-01` — ACTIONABLE (clean downsize)
```yaml
instance_id: i-0a1web
region: us-east-1
state: running
current_type: m5.xlarge   # 4 vCPU / 16 GiB
target_type:  m5.large    # 2 vCPU / 8 GiB
classification: ACTIONABLE
monthly_savings: 70.00
yearly_savings: 840.00
savings_percent: 50
risk: { telemetry: LOW, compute: LOW, memory: LOW, network: LOW, storage: LOW, compatibility: LOW, migration: LOW, overall: LOW }
reason_codes: []
telemetry_summary:
  cpu_percent:     { present: true, observed_days: 58.2, thin_data: false }
  memory_percent:  { present: true, observed_days: 41.0, thin_data: false, source: "CWAgent/mem_used_percent" }
  network_in_mbps: { present: true, observed_days: 58.2, thin_data: false }
  network_out_mbps:{ present: true, observed_days: 58.2, thin_data: false }
decision_window:   # p99 observed vs target capacity
  cpu_percent:      { p99: 14, max: 22, target_headroom_x: 4.1 }
  memory_percent:   { p99: 54, max: 61, target_headroom_x: 1.3 }
  # numeric baseline + peak → BASELINE case; p99 well under baseline (~14%)
  network_in_mbps:  { p99: 180, max: 640, target_baseline: 1250, target_peak: 10000, capacity_kind: BASELINE }
  network_out_mbps: { p99: 95,  max: 210, target_baseline: 1250, target_peak: 10000, capacity_kind: BASELINE }
  ebs_iops:         { p99: 320, target_baseline: 8000 }
  ebs_mibps:        { p99: 24,  target_baseline: 250 }
cpu_trend_profile: "flat 8–12%, rare spikes to ~22%; no upward trend"
memory_trend_profile: "steady 45–55%, occasional ~61% peaks; ~41 days of agent data"
buckets:
  "30d":  { maximum: 22, p99: 9,  p95: 7 }
  "90d":  { maximum: 24, p99: 10, p95: 7 }
  "120d": { maximum: 24, p99: 10, p95: 8 }
  "6mo":  { maximum: 26, p99: 11, p95: 8 }
  "12mo": { maximum: 28, p99: 11, p95: 8 }
  "15mo": { maximum: 28, p99: null, p95: null }   # window incomplete
memory_buckets:   # only ~41 days of data → longer windows null
  "30d":  { maximum: 61, p99: 54, p95: 50 }
  "90d":  { maximum: 61, p99: null, p95: null }
  "120d": { maximum: null, p99: null, p95: null }
  "6mo":  { maximum: null, p99: null, p95: null }
  "12mo": { maximum: null, p99: null, p95: null }
  "15mo": { maximum: null, p99: null, p95: null }
warnings: []
constraint_coverage: { numeric_network_baseline: true, reliable_network_maximum: true, ebs_attachment_limit: true, capability_source: describe_instance_types }
```

### 7.2 `api-prod-02` — CONDITIONAL (network review required)
```yaml
instance_id: i-0b2api
region: us-east-1
state: running
current_type: c5.2xlarge  # 8 vCPU / 16 GiB
target_type:  c5.xlarge   # 4 vCPU / 8 GiB
classification: CONDITIONAL
monthly_savings: 120.00
yearly_savings: 1440.00
savings_percent: 50
risk: { telemetry: LOW, compute: LOW, memory: LOW, network: HIGH, storage: LOW, compatibility: LOW, migration: LOW, overall: HIGH }
reason_codes: [NETWORK_SUSTAINED_ABOVE_BASELINE, NETWORK_BASELINE_ASSUMED]
telemetry_summary:
  cpu_percent:      { present: true, observed_days: 60.0, thin_data: false }
  memory_percent:   { present: false }   # no CloudWatch agent → Chart A′ empty-state
  network_in_mbps:  { present: true, observed_days: 60.0, thin_data: false }
  network_out_mbps: { present: true, observed_days: 60.0, thin_data: false }
decision_window:
  cpu_percent:      { p99: 38, max: 55, target_headroom_x: 1.6 }
  # no published baseline → assumed from size*peak; p99 sits ABOVE it → burst zone
  network_in_mbps:  { p99: 640, max: 1180, target_baseline: 590, target_peak: 10000, capacity_kind: ASSUMED_BASELINE }  # "Up to 10 Gbps"
  network_out_mbps: { p99: 410, max: 760,  target_baseline: 590, target_peak: 10000, capacity_kind: ASSUMED_BASELINE }
  ebs_iops:         { p99: 1200, target_baseline: 8000 }
  ebs_mibps:        { p99: 60,   target_baseline: 250 }
cpu_trend_profile: "diurnal 20–40%, weekday peaks ~55%, weekends low"
buckets:
  "30d":  { maximum: 55, p99: 40, p95: 34 }
  "90d":  { maximum: 58, p99: 41, p95: 35 }
  "120d": { maximum: 60, p99: 42, p95: 35 }
  "6mo":  { maximum: 62, p99: 43, p95: 36 }
  "12mo": { maximum: 64, p99: null, p95: null }
  "15mo": { maximum: 66, p99: null, p95: null }
warnings:
  - code: NETWORK_SUSTAINED_ABOVE_BASELINE
    message: "Sustained network demand (p99 640 Mbps) exceeds the target's estimated baseline (~590 Mbps). The instance would rely on time-limited burst capacity. Review before applying."
  - code: NETWORK_BASELINE_ASSUMED
    message: "No published baseline for this target; baseline estimated from instance size and peak bandwidth. Treat the comparison as approximate."
required_review: true
constraint_coverage: { numeric_network_baseline: false, reliable_network_maximum: true, ebs_attachment_limit: true, capability_source: describe_instance_types }
```

### 7.3 `batch-03` — OPPORTUNITY (Graviton migration)
```yaml
instance_id: i-0c3bat
region: us-west-2
state: running
current_type: r5.2xlarge   # x86, 8 vCPU / 64 GiB
target_type:  r6g.2xlarge  # Graviton, 8 vCPU / 64 GiB
classification: OPPORTUNITY
architecture_change_required: true
monthly_savings: 180.00
yearly_savings: 2160.00
savings_percent: 20
risk: { telemetry: LOW, compute: LOW, memory: LOW, network: MEDIUM, storage: LOW, compatibility: HIGH, migration: HIGH, overall: HIGH }
reason_codes: [ARCHITECTURE_CHANGE_REQUIRED, APPLICATION_ARM_COMPATIBILITY_UNVERIFIED, AMI_OR_IMAGE_REBUILD_REQUIRED]
telemetry_summary:
  cpu_percent:      { present: true, observed_days: 60.0, thin_data: false }
  network_in_mbps:  { present: true, observed_days: 60.0, thin_data: false }
  network_out_mbps: { present: true, observed_days: 60.0, thin_data: false }
decision_window:
  cpu_percent:      { p99: 46, max: 61, target_headroom_x: 1.6 }
  network_in_mbps:  { p99: 520, target_baseline: 2500 }
  network_out_mbps: { p99: 300, target_baseline: 2500 }
  ebs_iops:         { p99: 3400, target_baseline: 13333 }
  ebs_mibps:        { p99: 180,  target_baseline: 500 }
cpu_trend_profile: "steady 35–50%, periodic batch spikes to ~61%"
buckets:
  "30d":  { maximum: 61, p99: 47, p95: 42 }
  "90d":  { maximum: 63, p99: 48, p95: 43 }
  "120d": { maximum: 64, p99: 48, p95: 43 }
  "6mo":  { maximum: 66, p99: 49, p95: 44 }
  "12mo": { maximum: 68, p99: null, p95: null }
  "15mo": { maximum: 70, p99: null, p95: null }
validation_steps:   # render as a checklist
  - Validate ARM application binaries and native libraries
  - Rebuild or select an ARM64 AMI / container image
  - Validate agents (security, backup, monitoring)
  - Validate licensing
  - Canary test; compare latency/errors/CPU/mem/net/storage
  - Keep x86 rollback available
constraint_coverage: { numeric_network_baseline: true, reliable_network_maximum: true, ebs_attachment_limit: true, capability_source: describe_instance_types }
```

### 7.4 `asg-worker-04` — DEFERRED (managed by ASG)
```yaml
instance_id: i-0d4asg
region: us-east-1
state: running
current_type: m5.large
classification: DEFERRED
deferred_reason_codes: [MANAGED_BY_ASG]
management_context: asg
telemetry_summary:
  cpu_percent:      { present: true, observed_days: 60.0, thin_data: false }
  network_in_mbps:  { present: true, observed_days: 60.0, thin_data: false }
  network_out_mbps: { present: true, observed_days: 60.0, thin_data: false }
note: "Managed by an Auto Scaling group — resize via the ASG launch template, not per-instance. No standalone recommendation produced."
# still show CPU trend so the user sees the utilization context, but no target/savings.
cpu_trend_profile: "spiky autoscaled workload 10–70%"
buckets:
  "30d":  { maximum: 70, p99: 55, p95: 44 }
  "90d":  { maximum: 74, p99: 57, p95: 45 }
  "120d": { maximum: 76, p99: 58, p95: 46 }
  "6mo":  { maximum: 78, p99: 60, p95: 47 }
  "12mo": { maximum: 80, p99: null, p95: null }
  "15mo": { maximum: 82, p99: null, p95: null }
```

### 7.5 `new-svc-05` — INSUFFICIENT_DATA / thin telemetry
```yaml
instance_id: i-0e5new
region: eu-west-1
state: running
current_type: t3.large
target_type:  t3.medium
classification: CONDITIONAL     # visible, but flagged thin
monthly_savings: 30.00
yearly_savings: 360.00
savings_percent: 50
risk: { telemetry: HIGH, compute: MEDIUM, memory: LOW, network: LOW, storage: LOW, compatibility: LOW, migration: LOW, overall: HIGH }
reason_codes: [OBSERVATION_WINDOW_TOO_SHORT]
telemetry_summary:
  cpu_percent:      { present: true, observed_days: 4.3, thin_data: true }
  network_in_mbps:  { present: true, observed_days: 4.3, thin_data: true }
  network_out_mbps: { present: true, observed_days: 4.3, thin_data: true }
decision_window:
  cpu_percent:      { p99: 12, max: 19, target_headroom_x: 2.0 }
  network_in_mbps:  { p99: 40, target_baseline: 640, capacity_kind: BURST_OR_UP_TO }
  network_out_mbps: { p99: 25, target_baseline: 640, capacity_kind: BURST_OR_UP_TO }
  ebs_iops:         { p99: 90, target_baseline: 4000 }
  ebs_mibps:        { p99: 6,  target_baseline: 125 }
cpu_trend_profile: "only ~4 days of data available; short flat series ~8–19%"
buckets:
  "30d":  { maximum: 19, p99: 12, p95: 10 }   # computed over only ~4 days of data
  "90d":  { maximum: null, p99: null, p95: null }
  "120d": { maximum: null, p99: null, p95: null }
  "6mo":  { maximum: null, p99: null, p95: null }
  "12mo": { maximum: null, p99: null, p95: null }
  "15mo": { maximum: null, p99: null, p95: null }
warnings:
  - code: OBSERVATION_WINDOW_TOO_SHORT
    message: "Only ~4 days of telemetry are available. The recommendation is shown for transparency but should be revisited after a full observation window."
required_review: true
```

### 7.6 `data-06` — DEFERRED (instance store, usage unknown)
```yaml
instance_id: i-0f6dat
region: us-east-1
state: running
current_type: i3.xlarge
classification: DEFERRED
deferred_reason_codes: [INSTANCE_STORE_USAGE_UNKNOWN]
instance_store_present: true
scope_policy: { allow_unknown_instance_store_usage: false }
note: "This type has local NVMe instance-store. Its use/durability can't be proven from inventory, so a resize is deferred. Set allow_unknown_instance_store_usage to evaluate anyway (result would be CONDITIONAL with HIGH compatibility risk)."
cpu_trend_profile: "storage-bound service, CPU 20–40%"
buckets:
  "30d":  { maximum: 44, p99: 33, p95: 28 }
  "90d":  { maximum: 46, p99: 34, p95: 29 }
  "120d": { maximum: 47, p99: 35, p95: 29 }
  "6mo":  { maximum: 49, p99: 36, p95: 30 }
  "12mo": { maximum: 51, p99: null, p95: null }
  "15mo": { maximum: 52, p99: null, p95: null }
```

## 8. States & empty handling
- **Selected DEFERRED**: hide savings/target and the decision-window "vs capacity"
  bars; keep the CPU trend + buckets (context is still useful) and show the reason
  banner + remediation note.
- **Thin telemetry**: amber banner at the top of the detail pane; still render
  everything; long buckets render "n/a — insufficient history".
- **Null percentile buckets**: render "n/a" with the footnote, not 0.
- Default selection on load: `web-prod-01` (the clean ACTIONABLE case).

## 9. Acceptance checklist
- [ ] All eighteen instances are selectable and grouped as three records in each
  of the six behavior groups; each renders its correct classification badge
  and state-specific layout.
- [ ] Section order in the detail pane is: decision header → why + evidence chart
      (§4.2) → warnings (§4.3) → risk by dimension (§4.4) → supporting evidence
      B/C (§4.5) → telemetry (§4.6) → coverage (§4.7).
- [ ] Chart A lives inside the same card as the headroom sentence, directly below
      it — not in a separate "utilization evidence" section.
- [ ] Chart A is in target mode for ACTIONABLE/CONDITIONAL/OPPORTUNITY: y-axis
      0–100, a bold 100% "target capacity ceiling" line, 40/70 background bands,
      and the scaled peak lands exactly at `100/target_headroom_x`.
- [ ] Chart A is in raw mode for DEFERRED: % of current instance capacity, no
      ceiling line, auto-fit y-axis.
- [ ] Chart A's tooltip shows both the scaled and the raw observed value per day
      in target mode.
- [ ] Chart A's peak point shows an always-visible two-line callout in target
      mode ("current peak: X%" → "Y% on target") — not hover-only.
- [ ] The headroom sentence says "max CPU", not "worst".
- [ ] Chart A shows Max as the dominant series over ~455 daily points, seeded/stable.
- [ ] Chart C draws the 40%/70% markers and colors bars by band; burst/unknown
      capacity shown as "approximate", not a fabricated ratio.
- [ ] Buckets show max always, percentiles as n/a where null, with the semantics
      footnote.
- [ ] Telemetry disclosure shows "~N days" and flags thin data.
- [ ] Headroom sentence present on ACTIONABLE/CONDITIONAL/OPPORTUNITY, and its
      stated peak/headroom numbers match what Chart A plots.
- [ ] Works in light and dark; no horizontal body scroll; no external requests.
