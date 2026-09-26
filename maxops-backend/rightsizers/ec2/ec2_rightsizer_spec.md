# EC2 Rightsizer V1 Amendment: Simplified Availability, Network, and EBS Validation

## 1. Remove Availability Zone offering validation from V1

V1 does not need `DescribeInstanceTypeOfferings` or Availability Zone–specific candidate validation.

The recommendation engine should assume that commonly available instance types can generally be launched in the customer’s Region.

V1 should not:

* Query instance offerings per Availability Zone.
* Store Availability Zone IDs for rightsizing purposes.
* Reject a target because Availability Zone offering data is unavailable.
* Make claims about immediate EC2 capacity availability.

The product should display the following general note:

```text
Instance availability and launch capacity are not validated by this
recommendation. Confirm that the target type is available before
applying the change.
```

Candidate generation should still use:

```text
Current Region
Operating system
Architecture
Instance family
Instance capabilities
Pricing availability
```

Region-level pricing availability may be used as a practical indication that the type is relevant to that Region, but it is not a launch-capacity guarantee.

Availability validation can be added later as part of an execution or migration-planning workflow.

### Cross-family generation (same architecture)

Candidate generation is **cross-family but same-architecture**. Every cheaper,
region-priced type whose architecture overlaps the current instance's is eligible
(for example `m5.xlarge → c6i.large`). "Instance family" above is an input to
capability evaluation, not a restriction to the current family. Architecture
migrations (for example x86 → Graviton) remain out of scope for
*recommendations* and route through `ARCHITECTURE_INCOMPATIBLE`; the
OPPORTUNITY classification (Section 11) stays reserved for those future
architecture changes, not same-architecture family moves, which classify as
ordinary resizes. (Section 27 additionally surfaces a display-only Graviton
*savings preview* derived from that rejected set; it is not a recommendation
and does not change this scope rule.)

**Family gate.** Because V1 does not model CPU-credit behavior or specialized
accelerator semantics, two family groups are excluded from candidate generation
unless the current instance is already in that same class:

```text
Burstable        t*                       (credit-limited CPU — could throttle
                                            a sustained workload)
Specialized /    p*, g*, inf*, trn*, dl*, (accelerator, FPGA, media, HPC, and
accelerator      f*, vt*, hpc*, u*         high-memory bare-metal families)
```

A gated-class target is allowed only when the current instance shares that class
and architecture (for example `t3 → t3a`, `p3 → p4`). Otherwise the target is skipped and counted
as `FAMILY_NOT_ELIGIBLE`. The gated-class set is configurable policy. This keeps
savings-first ranking from surfacing an unsuitable cheap type ahead of a sound
same-family or general-purpose downsize.

For an accelerator source, the source-side gate is stricter than the target-side
family gate. A target must have the same GPU model (`GpuInfo.Gpus[].Name`), at
least the required number of addressable devices, and at least the required VRAM
per device. The family may differ when the model matches (`p4de → p4d`,
`g5.48xlarge → g5.12xlarge`). Cross-GPU-model moves are V2: this version has no
performance model that can compare them. A target without a GPU is allowed only
when `required_devices == 0` over the full decision window; that capability
removal is `CONDITIONAL` and carries `GPU_UNUSED_FULL_WINDOW` so the user sees
why it is being proposed.

The source-side GPU gate is applied **in addition to** the target-side family
gate above, never instead of it. The only relaxation a GPU source earns is that
a target with the **same GPU model** may come from a different family token
(`p4de → p4d`). Every other gated class stays gated: an unused-GPU source must
not be offered a burstable `t*` target, and an Inferentia/Trainium/FPGA target
(`inf*`, `trn*`, `f*`, …) is not "a non-GPU target" — it is a different
accelerator class and is rejected as `FAMILY_NOT_ELIGIBLE` exactly as it would
be for an `m5` source. Rejections specific to the GPU gate are counted as
`GPU_MODEL_MISMATCH`, `GPU_DEVICE_COUNT_REQUIREMENT_NOT_MET`, and
`GPU_VRAM_REQUIREMENT_NOT_MET` alongside `FAMILY_NOT_ELIGIBLE` in candidate
diagnostics.

The assumed-baseline denominator (Section 6.3) is always computed from the
**target's own exact family** (e.g. `c6i`), never widened by cross-family
generation.

---

# 2. Network and EBS validation philosophy

Network and EBS sizing should use:

```text
Exact metric normalization
+ approximate capacity evaluation
+ conservative warnings
```

V1 should avoid pretending that it can prove exact network or EBS performance from five-minute or one-minute CloudWatch metrics.

Published EC2 performance may include:

* Baseline capacity
* Temporary burst capacity
* “Up to” maximum capacity
* Credit-based behavior
* Workload-dependent IOPS and throughput interaction
* Short microbursts that CloudWatch aggregation may not show

Therefore, network and EBS throughput should usually affect:

```text
Warnings
Risk classification
Recommendation classification
Required review steps
```

rather than automatically rejecting every candidate whose estimated demand approaches a published limit.

---

# 3. Metric normalization remains exact

The following conversions are still required.

## 3.1 Network bytes to Mbps

`NetworkIn` and `NetworkOut` are period totals.

```python
bytes_per_second = (
    network_bytes_sum
    / period_seconds
)

network_mbps = (
    bytes_per_second
    * 8
    / 1_000_000
)
```

Use decimal megabits per second.

Examples:

```text
300,000,000 bytes over 300 seconds = 8 Mbps
60,000,000 bytes over 60 seconds   = 8 Mbps
```

Inbound and outbound traffic must remain separate.

Do not add inbound and outbound rates together when comparing them with an instance’s documented bandwidth.

## 3.2 Network packets to PPS

```python
packets_per_second = (
    packet_count_sum
    / period_seconds
)
```

## 3.3 EBS operations to IOPS

```python
read_iops = (
    ebs_read_operations_sum
    / period_seconds
)

write_iops = (
    ebs_write_operations_sum
    / period_seconds
)
```

## 3.4 EBS bytes to MiB/s

```python
read_mibps = (
    ebs_read_bytes_sum
    / period_seconds
    / 1_048_576
)

write_mibps = (
    ebs_write_bytes_sum
    / period_seconds
    / 1_048_576
)
```

The conversion and aggregation logic must be fully deterministic and independently tested.

V1 collects `EBSReadOps`, `EBSWriteOps`, `EBSReadBytes`, and
`EBSWriteBytes` from the `AWS/EC2` namespace with the `InstanceId`
dimension. These counters are already instance-level aggregates across the
attached EBS volumes. V1 therefore normalizes and combines these instance-level
series by timestamp before calculating percentiles; it does not perform a
second per-volume aggregation pass.

Read and write series are combined by timestamp before percentiles. When only
one direction is present for a timestamp, V1 retains the observed direction as
a lower bound, records incomplete directional coverage, and adds a review
warning. It does not claim that the missing direction was zero.

Uncertainty should enter during capacity interpretation, not during unit conversion.

---

# 4. Separate hard restrictions from performance warnings

Network and EBS evaluation must produce two independent results:

```python
@dataclass(frozen=True)
class ResourceEvaluation:
    hard_constraint_status: str
    risk_level: str
    warnings: tuple[str, ...]
    evidence: Mapping[str, object]
```

Allowed hard-constraint statuses:

```text
PASS
FAIL
UNKNOWN
NOT_APPLICABLE
```

Allowed risk levels:

```text
LOW
MEDIUM
HIGH
```

A candidate may pass its hard restrictions while still receiving a high network or EBS risk.

---

# 5. Network hard restrictions

V1 should hard-block a network candidate only when an objectively incompatible condition exists.

Hard failures include:

```text
Target ENI limit is lower than the number of currently attached ENIs.

The workload requires EFA and the target does not support EFA.

The observed sustained network demand is above a reliable documented
target maximum.

The target does not support an explicitly required networking feature.

The current workload has recorded bandwidth or PPS allowance-exceeded
events and the target has clearly lower documented network capacity.
```

Do not hard-block merely because:

```text
Observed p99 is close to a published “up to” value.

The exact sustainable baseline is unknown.

Allowance-exceeded metrics were not collected.

Five-minute metrics may hide microbursts.

The target has the same qualitative bandwidth description as the
current instance.
```

These should create warnings.

---

# 6. Network warning model

## 6.1 Network utilization ratios (baseline and peak)

EC2 network capacity has two published numbers: a **sustained baseline** and a
higher **peak** ("up to") the instance can burst to on network credits for a
limited, unguaranteed time. V1 evaluates demand against **both**.

Inbound and outbound are kept separate (see Section 3.1); EC2 shaping is
full-duplex, so the two directions are never summed. The greater directional
ratio drives the verdict.

```python
baseline_util = (               # "am I within guaranteed sustained capacity?"
    observed_network_p99_mbps
    / target_network_baseline_mbps
)

peak_util = (                   # "how close to the ceiling even with burst?"
    observed_network_p99_mbps
    / target_network_peak_mbps  # reliable_max; None when peak is unknown
)

network_utilization_ratio = max(
    baseline_util_in,
    baseline_util_out,
)
```

An external convention (for example the marbot monitoring guide) sums
`NetworkIn + NetworkOut` into a single throughput figure as a conservative
single-alarm heuristic. V1 does **not** sum them: EC2 publishes bandwidth that
is available to each direction independently, so summing double-counts and
overstates demand.

Thresholds must be configurable. The decision uses the **p99** statistic; p95
and max are collected and displayed for context but do not drive eligibility.
Changing the decision percentile is a versioned policy change.

### Three-band model

| Condition | Meaning | Network risk | Classification effect |
| --- | --- | --- | --- |
| `p99 < baseline` (util < high ratio) | Fits inside guaranteed sustained capacity | LOW / MEDIUM per 40%/70% of baseline | ACTIONABLE-eligible |
| `baseline ≤ p99 < peak` | Sustained **above baseline** — relies on time-limited burst credits AWS does not guarantee | HIGH | `NETWORK_SUSTAINED_ABOVE_BASELINE`, CONDITIONAL |
| `p99 ≥ reliable peak` | Sustained demand exceeds the target's absolute documented ceiling | HIGH | Hard fail `NETWORK_RELIABLE_MAX_EXCEEDED` → REJECTED (Section 5) |

Recommended balanced defaults, applied to `baseline_util`:

```text
Less than 40% of target baseline:
    LOW network risk

40%–70% of target baseline:
    MEDIUM network risk

Greater than 70% of target baseline:
    HIGH network risk and review required
```

The threshold does not change the normalized demand. It changes only the warning
and risk classification. The top band is the one existing objective hard fail
(Section 5): when a **reliable numeric peak** is known and sustained p99 exceeds
it, the target cannot serve the workload and is rejected. This applies only to a
reliable peak — a mere qualitative "up to N Gbps" with no numeric peak is not a
reliable maximum and never triggers this hard fail; it routes through the
burst/assumed-baseline warnings instead.

## 6.2 “Up to” or burst-only target capacity

When the target is documented only as:

```text
Up to N Gbps
```

and no reliable numeric sustainable baseline is stored, do not use the peak value as a guaranteed capacity constraint.

Instead:

```text
hard_constraint_status = UNKNOWN
risk_level = HIGH
warning = NETWORK_BURST_CAPACITY_REQUIRES_REVIEW
```

The candidate may still be shown.

Its recommendation classification should normally become:

```text
CONDITIONAL
```

The explanation should state:

```text
The target is documented with burst or “up to” network performance.
Available telemetry does not prove that sustained or short-duration
network demand will remain within the target’s baseline capacity.
Review network behavior before applying this recommendation.
```

### Baseline known but exceeded

When a numeric baseline **is** available and observed p99 sits above it but
below peak (the middle band of Section 6.1), the workload is sustaining above
the guaranteed baseline and depending on burst credits. This is distinct from
the unknown-baseline case above:

```text
hard_constraint_status = PASS
risk_level = HIGH
warning = NETWORK_SUSTAINED_ABOVE_BASELINE
classification = CONDITIONAL
```

The candidate remains visible. It is not rejected — only a reliable peak
violation or a Section 7 allowance-exceeded event can reject it.

## 6.3 Assumed baseline for "up to" types

AWS publishes numeric `BaselineBandwidthInGbps` and `PeakBandwidthInGbps` only
for newer Nitro instance types. Older or smaller types expose a peak (an "up to"
value) but no sustained baseline. Rather than fall back to coarse qualitative
class comparison, V1 estimates a baseline from the instance's size share of its
family, matching AWS's size-proportional shaping:

```python
assumed_baseline_mbps = (
    target_network_peak_mbps
    * min(target_vcpus / family_max_vcpus, 1.0)
)
assumed_baseline_mbps = max(
    assumed_baseline_mbps,
    network_assumed_baseline_floor_mbps,
)
```

`family_max_vcpus` is the largest non-metal size in the target's **exact**
instance family present in the catalog — the family+generation token (e.g. `m5`,
`c6i`), not the bare letter class (`m`, `c`); the classes must not be pooled. A
configurable multiplier
(`network_assumed_baseline_multiplier`) may scale the result; when uncertain,
bias **low**, because a lower assumed baseline raises the utilization ratio and
errs toward warning — the intended conservative direction.

When this path is used:

```text
capacity_kind = ASSUMED_BASELINE
warning = NETWORK_BASELINE_ASSUMED   (always attached)
```

An assumed baseline may drive warnings and route a candidate to CONDITIONAL. It
must **never** produce a hard reject and must **never** support an ACTIONABLE
"network is fine" claim — an estimated number cannot carry that confidence.

Do not derive exact Mbps from display strings unless the catalog explicitly
identifies the number as a sustained baseline. When neither a published nor an
assumable baseline exists (no numeric peak either), leave the ratio undisplayed
and mark the target `capacity_kind = UNKNOWN`; do not fake a ratio.

This assumed-baseline path replaces the former qualitative-class comparison
(`NETWORK_CAPACITY_APPROXIMATE` / `NETWORK_CAPABILITY_REDUCTION` /
`NETWORK_CAPABILITY_UNKNOWN`) as the primary fallback.

---

# 7. Network allowance metrics

When available, collect:

```text
bw_in_allowance_exceeded
bw_out_allowance_exceeded
pps_allowance_exceeded
conntrack_allowance_exceeded
```

These metrics identify packets queued or dropped when EC2 networking allowances are exceeded.

Rules:

```text
Any bandwidth allowance event:
    NETWORK_ALLOWANCE_EXCEEDED warning
    network risk = HIGH

Any PPS allowance event:
    PPS_ALLOWANCE_EXCEEDED warning
    network risk = HIGH

Any connection-tracking allowance event:
    CONNTRACK_ALLOWANCE_EXCEEDED warning
    network risk = HIGH
```

A positive event does not automatically reject every candidate.

Instead:

```text
Target clearly has greater network capacity:
    allow as CONDITIONAL

Target has equal, lower, or unknown network capacity:
    reject or mark as not recommended
```

When allowance metrics are missing:

```text
NETWORK_ALLOWANCE_METRICS_MISSING
```

This should increase uncertainty but should not by itself block a recommendation.

---

# 8. EBS hard restrictions

Keep the following as hard constraints because they are configuration facts rather than approximate performance estimates:

```text
Target does not support EBS.

Target attachment limit is lower than the number of attached volumes.

Target is incompatible with a required EBS volume feature.

Target documented maximum IOPS is lower than clearly observed
sustained IOPS.

Target documented maximum throughput is lower than clearly observed
sustained throughput.

Target cannot support the provisioned IOPS or throughput configuration
required by attached volumes.
```

The following should remain hard regardless of selected headroom:

```text
EBS attachment count
Required volume features
EBS support
Known maximum-capacity incompatibility
```

---

# 9. EBS performance warning model

EBS performance depends on both:

```text
Attached volume capabilities
EC2 instance EBS capabilities
```

The delivered performance is constrained by the lower effective limit.

AWS also documents that certain instance types can use maximum EBS performance temporarily before returning to baseline, so maximum capacity should not automatically be treated as sustained capacity.

## 9.1 Baseline utilization ratio

When reliable baseline data exists:

```python
ebs_iops_utilization_ratio = (
    observed_combined_iops_p99
    / target_ebs_baseline_iops
)

ebs_throughput_utilization_ratio = (
    observed_combined_throughput_p99_mibps
    / target_ebs_baseline_throughput_mibps
)

ebs_utilization_ratio = max(
    ebs_iops_utilization_ratio,
    ebs_throughput_utilization_ratio,
)
```

Capacity decisions use the p99 statistic (as in the ratios above). p95 and max are collected and displayed for context but do not drive eligibility. Changing the decision percentile is a versioned policy change.

Recommended balanced defaults:

```text
Less than 40% of target baseline:
    LOW EBS risk

40%–70% of target baseline:
    MEDIUM EBS risk

Greater than 70% of target baseline:
    HIGH EBS risk and review required
```

## 9.2 Burst or “up to” EBS capacity

When target performance relies on temporary maximum capacity:

```text
EBS_BURST_CAPACITY_REQUIRES_REVIEW
```

The candidate can remain visible but should normally be:

```text
classification = CONDITIONAL
storage risk = HIGH
```

## 9.3 Missing baseline capability

When only maximum or qualitative capacity is available:

```text
hard_constraint_status = UNKNOWN
storage risk = HIGH
warning = EBS_BASELINE_CAPABILITY_UNKNOWN
```

The candidate should not be described as storage-safe.

It may still be shown when:

* CPU and memory checks pass.
* Attachment and volume-feature restrictions pass.
* No known maximum limit is violated.
* The warning is prominently displayed.

---

# 10. EBS operational signals

The following should increase storage risk:

```text
High queue length
High read or write latency
EBS throttling
Instance EBS exceeded checks
Volume burst balance depletion
Sustained demand close to documented limits
```

AWS notes that EBS behavior depends on I/O size, sequential versus random access, volume type, queue length, provisioned performance, and instance-level capacity. This makes a single IOPS or throughput comparison insufficient to guarantee application performance.

Rules:

```text
Throttling or exceeded events with target capacity clearly higher:
    allow as CONDITIONAL
    storage risk = HIGH

Throttling or exceeded events with target capacity equal, lower, or
unknown:
    do not recommend that target

High queue length without throttling:
    allow as CONDITIONAL
    EBS_QUEUE_REVIEW_REQUIRED
```

---

# 11. Revised recommendation classification

A technically eligible candidate should be classified as follows.

Candidate savings eligibility uses decimal-safe arithmetic. Raw monthly
savings must be at least `$0.01` and must meet `min_monthly_savings`; equality
with either threshold is included. Construct decimal values from the catalog
price representations (for example `Decimal(str(price))`), not from their
binary float expansion. The legacy float difference remains the source for
displayed monthly/yearly savings so existing rounding does not change.

## ACTIONABLE

Allowed only when:

```text
CPU and memory pass.

No hard network or EBS restrictions fail.

Network demand is low relative to known target capacity.

EBS demand is low relative to known target capacity.

No allowance-exceeded or throttling events exist.

No important network or EBS capability is unknown.
```

MEDIUM network or EBS risk does not qualify as ACTIONABLE. Any **network or
storage** risk dimension at MEDIUM or HIGH, any UNKNOWN hard status, or any
blocking warning routes the candidate to CONDITIONAL. This strict LOW/LOW rule
matches the shipped classifier and V1's "high-confidence over coverage" goal;
it is a tunable policy, not a fixed law.

Classification is driven only by the CPU/memory pass status, the network and
EBS hard statuses and risk levels, and blocking warnings — exactly the columns
of the decision table below. The other risk dimensions in Section 12
(telemetry, compute, memory, compatibility) are assembled after classification
and are disclosure only; they never change the classification. In particular,
HIGH telemetry risk from a missing CPU metric does not block ACTIONABLE (a
missing memory metric does not raise telemetry risk; it is disclosed through
the `MEMORY_METRIC_UNAVAILABLE_CURRENT_CAPACITY_RETAINED` reason code
instead), because missing telemetry is already handled by a stronger
mechanism: the capacity floor requires the target to retain comparable current
capacity for any unmeasured dimension (Section 19), so such a recommendation
never reduces the capacity that could not be measured. Scope-override
compatibility review is the one exception and is applied as a separate
post-classification cap: it downgrades a non-REJECTED candidate to CONDITIONAL
(Section 24).

### Classification decision table

| Hard status (network & EBS) | Network risk | Storage risk | Blocking warnings | Classification |
| --- | --- | --- | --- | --- |
| Any FAIL | any | any | any | REJECTED |
| PASS or NOT_APPLICABLE, any UNKNOWN | any | any | any | CONDITIONAL |
| PASS or NOT_APPLICABLE | LOW | LOW | none | ACTIONABLE |
| PASS or NOT_APPLICABLE | MEDIUM or HIGH | any | any | CONDITIONAL |
| PASS or NOT_APPLICABLE | any | MEDIUM or HIGH | any | CONDITIONAL |

`NETWORK_ALLOWANCE_METRICS_MISSING` is non-blocking and does not by itself force CONDITIONAL.

GPU recommendations are additionally capped at `CONDITIONAL` when the observed
window is thinner than the GPU policy minimum. Device demand is a window maximum,
so a short observation can miss a monthly or nightly job; this is deliberately
stricter than the EC2 CPU/memory disclosure-only rule and carries
`GPU_OBSERVATION_WINDOW_TOO_SHORT`.

## CONDITIONAL

Use when:

```text
CPU and memory pass.

No hard network or EBS incompatibility is known.

Network or EBS capacity is approximate, burst-based, near a limit, or
missing a reliable baseline.

A workload-specific review is required.

GPU targets that remove unused accelerator capability are also `CONDITIONAL`
with `GPU_UNUSED_FULL_WINDOW`. Every GPU recommendation discloses
`GPU_DEVICE_DEMAND_USES_WINDOW_MAXIMUM`.
```

## OPPORTUNITY

Continue to use for:

```text
Graviton migrations
Architecture changes
Other application-level migrations
```

A Graviton recommendation can simultaneously contain:

```text
architecture migration risk = HIGH
network risk = MEDIUM
storage risk = HIGH
```

---

# 12. Revised risk structure

```python
@dataclass(frozen=True)
class RiskAssessment:
    telemetry: str
    compute: str
    memory: str

    network: str
    storage: str

    compatibility: str
    migration: str

    overall: str
    reason_codes: tuple[str, ...]
```

Do not automatically set the whole recommendation to `HIGH` merely because a throughput limit is approximate.

Instead:

```text
Network risk = HIGH
Storage risk = LOW
Compute risk = LOW
Memory risk = LOW
Overall technical risk = HIGH
Classification = CONDITIONAL
```

The product UI should explain which resource requires review.

---

# 13. Configurable warning thresholds

Add the following policy:

```python
@dataclass(frozen=True)
class PerformanceWarningPolicy:
    network_medium_ratio: float
    network_high_ratio: float

    ebs_medium_ratio: float
    ebs_high_ratio: float

    require_network_allowance_metrics_for_actionable: bool
    require_ebs_exceeded_metrics_for_actionable: bool

    unknown_baseline_risk: str
    burst_dependent_risk: str

    network_assumed_baseline_enabled: bool
    network_assumed_baseline_multiplier: float
    network_assumed_baseline_floor_mbps: float

    policy_version: str
```

Balanced defaults:

```yaml
network_medium_ratio: 0.40
network_high_ratio: 0.70

ebs_medium_ratio: 0.40
ebs_high_ratio: 0.70

require_network_allowance_metrics_for_actionable: false
require_ebs_exceeded_metrics_for_actionable: false

unknown_baseline_risk: HIGH
burst_dependent_risk: HIGH

network_assumed_baseline_enabled: true
network_assumed_baseline_multiplier: 1.0    # scales size-proportional estimate; <1 warns sooner
network_assumed_baseline_floor_mbps: 100.0
```

These thresholds affect:

```text
Warnings
Risk
ACTIONABLE versus CONDITIONAL classification
```

They must not affect:

```text
Raw metric normalization
CPU requirements
Memory requirements
Architecture compatibility
Attachment count
Volume compatibility
```

---

# 14. Required customer-facing warnings

## GPU decisions

```text
GPU_UNUSED_FULL_WINDOW

No GPU device was observed busy for the full decision window. Removing GPU
capability is conditional; confirm that a scheduled or infrequent accelerator
job is not absent from this observation.

GPU_DEVICE_DEMAND_USES_WINDOW_MAXIMUM

GPU device demand is sized from the maximum simultaneous busy-device count in
the observation window. This protects bursty and scheduled multi-GPU jobs; it
does not compare utilization magnitude across GPU models.

GPU_OBSERVATION_WINDOW_TOO_SHORT

GPU telemetry covers less than the policy minimum observation window. The
recommendation is conditional because a short window can miss a scheduled job.
```

## Network review

```text
HIGH_NETWORK_USAGE_REVIEW_REQUIRED

The workload shows material network demand relative to the target’s
documented capacity. EC2 network performance may use baseline and
burst behavior, and aggregated CloudWatch metrics may not capture
short microbursts. Review detailed network and ENA allowance metrics
before applying this recommendation.
```

## Sustained above baseline

```text
NETWORK_SUSTAINED_ABOVE_BASELINE

Sustained network demand (p99) exceeds the target’s guaranteed baseline
bandwidth. The instance would rely on EC2 burst capacity, which is
time-limited and not guaranteed, so network may throttle under sustained
load. Review network behavior before applying this recommendation.
```

## Assumed baseline

```text
NETWORK_BASELINE_ASSUMED

No published sustained baseline is available for this target, so the
baseline was estimated from the instance’s size and peak bandwidth.
Treat the network comparison as approximate and validate before applying
this recommendation.
```

## Non-default bandwidth weighting

When `NetworkPerformanceOptions.BandwidthWeighting` is not the default, the
published baseline and peak do not reliably describe the instance's effective
network allocation. In that case V1 **suppresses the precise band verdict** of
Section 6.1 and falls back to the existing review warning rather than asserting a
numeric ratio:

```text
NON_DEFAULT_NETWORK_BANDWIDTH_WEIGHTING_REQUIRES_REVIEW
```

The published default baseline is retained as evidence, not asserted as the
effective capacity.

## EBS review

```text
HIGH_EBS_USAGE_REVIEW_REQUIRED

The workload shows material EBS IOPS or throughput demand relative to
the target’s documented capacity. Actual EBS performance depends on
volume configuration, I/O size, access pattern, instance limits, and
burst behavior. Review EBS queue, latency, throttling, and exceeded
metrics before applying this recommendation.
```

## Unknown baseline

```text
RESOURCE_BASELINE_CAPACITY_UNKNOWN

A reliable sustained baseline was not available for this target.
The recommendation passed known compatibility checks but requires
performance validation.
```

---

# 15. Module boundaries

Keep exact calculations and approximate decisions in different modules.

```text
normalization/
    Converts CloudWatch data into Mbps, PPS, IOPS, and MiB/s.
    Contains no rightsizing thresholds.

requirements/
    Produces observed demand and policy-adjusted demand.
    Contains no candidate ranking.

constraints/
    Evaluates objective incompatibilities.
    Attachment counts, required features, and known maximum limits.

warnings/
    Evaluates approximate throughput pressure.
    Produces review warnings.

risk/
    Converts constraints, warnings, telemetry quality, and uncertainty
    into risk dimensions.

selection/
    Selects among candidates after constraints and risk are complete.
```

Suggested structure:

```text
ec2_rightsizer/
├── normalization/
│   ├── network.py
│   ├── ebs.py
│   ├── cpu.py
│   └── memory.py
├── requirements/
│   ├── network.py
│   └── ebs.py
├── constraints/
│   ├── network_hard_constraints.py
│   └── ebs_hard_constraints.py
├── warnings/
│   ├── network_warnings.py
│   └── ebs_warnings.py
├── risk/
│   ├── network_risk.py
│   └── storage_risk.py
└── selection/
    └── recommendation_classification.py
```

---

# 16. Required tests

The scenario harness must cover: same-model device-count gating; same-model VRAM
gating with headroom; cross-family same-model `p4de → p4d`; cross-model rejection;
full-window unused GPU capability removal; unavailable and incomplete GPU
telemetry deferrals; thin-window conditional capping; fractional GPU slices;
and unchanged non-GPU behavior. Unit coverage must also test GPU-memory matcher
accept/reject semantics, all-device timestamp alignment, maximum (not p95)
device demand, and MiB validation without percent limits.

## Normalization tests

```text
Network conversion remains exact for one-minute and five-minute data.

EBS conversion remains exact for one-minute and five-minute data.

Inbound and outbound network rates remain separate.

Missing data is not converted to zero.

EBS instance-level read and write data is combined by timestamp before percentiles.
```

## Hard-constraint tests

```text
Too many attached volumes always reject the target.

Unsupported EBS feature always rejects the target.

Observed demand above a reliable target maximum rejects the target.

High usage below the target maximum does not automatically reject it.
```

## Warning tests

```text
Network usage above the configured high ratio creates a high warning.

EBS usage above the configured high ratio creates a high warning.

An unknown baseline creates a review warning.

A burst-dependent target creates a review warning.

Sustained p99 above a known baseline but below peak yields
NETWORK_SUSTAINED_ABOVE_BASELINE, HIGH network risk, and CONDITIONAL.

A target with no published baseline gets a size-proportional assumed baseline
(peak * min(vCPU / family_max_vCPU, 1.0), floored) and NETWORK_BASELINE_ASSUMED.

An assumed baseline can route to CONDITIONAL but never yields ACTIONABLE and
never hard-rejects.

Non-default bandwidth weighting suppresses the numeric band verdict and reports
NON_DEFAULT_NETWORK_BANDWIDTH_WEIGHTING_REQUIRES_REVIEW instead of a ratio.

Inbound and outbound are evaluated separately and never summed.

Changing warning thresholds does not change normalized metrics.
```

## Classification tests

```text
A candidate with no warnings may be ACTIONABLE.

A candidate with an unknown network baseline is CONDITIONAL.

A candidate with high EBS usage is CONDITIONAL.

A candidate with an attachment-count failure is rejected.

A CONDITIONAL candidate remains visible to the user.
```

## Module-isolation tests

```text
Changing network warning thresholds cannot change:
- CPU eligibility
- Memory eligibility
- EBS normalized demand
- CoreMark calculations

Changing EBS warning thresholds cannot change:
- Network normalized demand
- Architecture checks
- CPU requirements
- Pricing

Removing Availability Zone validation cannot change:
- Capacity calculations
- Risk formulas
- Benchmark data
- Candidate family generation
```

---

# 17. V1 product guarantee

```text
V1 does not claim to prove exact application network or storage
performance.

It normalizes observed metrics consistently, blocks candidates with
known configuration or maximum-capacity incompatibilities, and flags
candidates with high, burst-dependent, or uncertain network and EBS
requirements for customer review.

Recommendations requiring throughput review remain visible and are
classified as CONDITIONAL rather than being silently removed.
```

---

# 18. Telemetry collection model

Telemetry is collected on two independent paths. They must not be conflated.

| Path | Purpose | Period | Horizon | Statistics | When |
| --- | --- | --- | --- | --- | --- |
| Decision scan | Drives the recommendation | 5-minute (300s) | up to 60 days | p95, p99, max | Every scan |
| Confidence trend | User-facing evidence | daily (86400s) | up to 15 months | max, p99, p95 | On-demand (detail view) |

For the decision scan, GPU compute utilization, GPU VRAM occupancy, and GPU VRAM
capacity are discovered per device and queried only when their `index`-dimension
sources are found. They are `utilization_gpu`, `memory_used`, and
`memory_total`, respectively. No GPU source is assumed from the instance type.

The decision window is widened from 30 to 60 days, staying under CloudWatch's 63-day retention for 5-minute data.

---

# 19. Decision-scan telemetry

* Period 300s, 60-day window; statistics p95, p99, max, average.
* Percentiles are computed after normalization. Metrics that require per-timestamp math (EBS read + write summed per timestamp before the percentile, and fixed-period network rates) are pulled as raw per-timestamp series and aggregated locally. CPU and memory are examples that do not require cross-series per-timestamp math.
* Missing samples are never zero (see Section 3). One-sided EBS timestamps retain the observed component as a lower bound and raise `EBS_DIRECTIONAL_METRICS_INCOMPLETE`.

### Memory metric discovery

EC2 memory utilization is discovered per region with CloudWatch `ListMetrics`,
filtering for metrics that contain an `InstanceId` dimension. The returned
dimension value assigns the metric to its EC2 inventory row; all returned
dimensions are retained when the metric is queried. Names are matched
case-insensitively after separator and camel-case normalization. Exact `mem`
and `memory` aliases are accepted, as are names containing `mem` or `memory`
with `percent`, `percentage`, `pct`, or `utilization`. Metrics containing
`bytes`, `free`, `available`, `cache`, `swap`, or `total` are excluded.

Candidates are ranked by standard `mem_used_percent`, utilization names,
exact aliases, and then other matches; `CWAgent` breaks ties. A lower-ranked
candidate is tried when the preferred candidate has no finite values in the
0–100 range. If discovery is unavailable or no candidate has usable data, the
existing `CWAgent/mem_used_percent` query remains the fallback. The selected
series is used by regular EC2 scan utilization, the 300-second decision scan,
and inventory memory averages/history. The selected namespace, metric name,
and dimensions are persisted as memory metric source metadata.

### GPU metric discovery and demand

The same regional `ListMetrics` sweep discovers, per `index` device,
`nvidia_smi_utilization_gpu`, `nvidia_smi_memory_used`, and, when published,
`nvidia_smi_memory_total`. The GPU-memory matcher accepts `memory_used` and
`GPUMemoryUsed`-style aliases, but rejects `memory_total` and
`utilization_memory`: the latter is memory-bandwidth activity, not VRAM
occupancy. The decision scan queries `Average` values with the derived period
and paginates each discovered device query. `memory_used` is non-negative MiB,
not a percentage; `memory_total` is retained as consistency evidence and a
mismatch with catalog VRAM is not itself a failure.

For each window, align the per-device series by timestamp and retain only
timestamps where every discovered device reported. `required_devices` is the
maximum, over the window, of the number of devices busy at one timestamp, where
busy means `utilization_gpu > busy_threshold` OR `memory_used > vram_floor_mib`.
The default policy is `busy_threshold = 5.0` percent and
`vram_floor_mib = 512`. `required_vram_mib` is the maximum `memory_used` on any
device over the window multiplied by the tier's VRAM headroom; device count is
already a maximum and receives no second headroom multiplier. A four-hour
nightly job that uses all eight GPUs therefore requires eight even if p95 says
zero. Dropped timestamps and aligned counts are recorded as GPU aggregation
evidence; incomplete or absent GPU evidence never creates a recommendation.

A device **counts as reporting only when both** its `utilization_gpu` and its
`memory_used` series returned datapoints in the window. A device with one
series and not the other is neither idle nor busy — it is unknown — and it
must not be silently dropped from the demand math while the instance is still
treated as fully observed. The observed device count that the Section 24
completeness guard compares against the catalog is therefore the number of
devices contributing to the aligned demand series, not the number of series
discovery found.

VRAM headroom reuses the tier's memory margin: `required_vram_mib` is the
window maximum of per-device `memory_used` multiplied by
`1 + (1 − memory_target_ratio)` from the active compute policy, so a tier that
keeps 30% memory headroom keeps 30% VRAM headroom. Device count receives no
multiplier — it is already a maximum and one device is not a fraction.

### Balanced compute and network evidence

The EC2 rightsizer intentionally retains the MaxOps balanced decision policy:
CPU and memory use the existing Average samples, capacity uses the conservative
maximum p99 across the 14-, 30-, and 60-day windows, and both compute dimensions
target 70% utilization (30% headroom). This differs from the AWS Compute
Optimizer default of five-minute maximum points, CPU P99.5, 20% CPU and memory
headroom, and a 14-day default lookback. The difference is explicit in the
additive `compute_policy` response object.

For memory, estimated used capacity is `current_memory_mib * memory_p99 / 100`
and required capacity is estimated usage divided by `0.70`. When no valid memory
series is available, required memory remains equal to current memory. Candidate
`compute_evidence` records the observed p99, estimated used MiB, required and
target capacity, projected target memory percentage, selected metric source,
and whether the conservative fallback was used. Memory is also included in
`telemetry_summary` with coverage, status, and source.

CloudWatch `NetworkIn` and `NetworkOut` are byte sums. Each five-minute point is
converted independently to decimal Mbps as `bytes * 8 / 300 / 1_000_000` before
percentiles. Inbound and outbound are never added: EC2 publishes bandwidth that
is available to both directions simultaneously. Current and candidate evidence
records separate **p99** rates and, for context, separate **max** rates per
direction over the 60-day decision window.

The decision compares demand against **both** the baseline and the peak from
`DescribeInstanceTypes.NetworkInfo.NetworkCards`
(`BaselineBandwidthInGbps`, `PeakBandwidthInGbps`, summed across cards and
converted to Mbps), not the baseline alone — this drives the three-band model of
Section 6.1. When AWS exposes no numeric baseline for a target, the assumed
baseline of Section 6.3 is used and `NETWORK_BASELINE_ASSUMED` is attached. The
greater directional ratio drives the 40%/70% warning thresholds; crossing the
baseline remains conditional, while only a reliable peak violation or another
hard constraint can reject a candidate.

`NetworkPerformanceOptions.BandwidthWeighting` is persisted from inventory.
Because AWS does not expose an exact effective per-instance baseline adjustment
in the inventory response, any non-default weighting **suppresses the numeric
band verdict** and is reported as conditional with
`NON_DEFAULT_NETWORK_BANDWIDTH_WEIGHTING_REQUIRES_REVIEW`; the published default
instance-type baseline is retained as evidence, not asserted as the effective
capacity.

---

# 20. Confidence trend (15-month history)

Purpose: give the user the evidence to accept or reject the recommendation. The trend does not gate any decision.

* V1 contains `CPUUtilization` and, when a usable memory series exists, memory
  utilization. Both are percentage gauges, so a daily `Maximum` survives
  CloudWatch long-term rollup as an honest ceiling. CPU requires no CloudWatch
  agent; memory is included only when the agent published a usable series.
* The memory trend must reuse the **discovered** memory metric source
  (namespace, metric name, and dimensions from Section 19's memory-metric
  discovery), not re-guess it. When no memory source was discovered, the trend
  omits memory rather than substituting a default.
* When GPU telemetry is discoverable, the trend also includes per-device GPU
  utilization and VRAM peak using daily `Maximum` from the same discovered
  sources as the decision scan. GPU trend evidence is never used for sizing and
  does not compare GPU models.
* Because the CloudWatch agent has usually not run for the full horizon, memory
  often has far fewer than 15 months of data. This is disclosed through
  `observed_days` (Section 22), not hidden — a short memory line is expected.
* **Network is deliberately excluded from the 15-month trend.** `NetworkIn` and
  `NetworkOut` are `Sum` byte counters; beyond CloudWatch's 63-day 5-minute
  retention the daily `Maximum` is computed from 1-hour sums, which smears short
  bursts and understates the peak. An honest long-range network peak is not
  recoverable, so network peak evidence is limited to the 60-day decision window
  (Section 22). Disk trend series remain excluded.
* Horizon and granularity: up to 15 months at daily granularity for the plotted
  CPU and memory lines.
* Statistic rule (mandatory): the headline series is Maximum. Daily Max preserves each day's true peak and survives CloudWatch long-term rollup, so it is an honest ceiling. Daily Average must never be the headline series. p99 and p95 may be shown alongside Max.
* Summary buckets: 30d, 90d, 120d, 180d, 365d, 455d. Each bucket's headline Maximum is computed from the daily Maximum series over the trailing window. This avoids CloudWatch's epoch-aligned, partially open large-period bucket. p99 and p95 remain server-side large-period queries, but only the latest complete aligned datapoint is used; if no complete datapoint is available the value is `null`. These percentiles describe the latest complete epoch-aligned window and may be slightly stale, not a true trailing-window percentile.
* The response exposes `bucket_semantics`: Maximum is labeled `trailing_window_from_daily_maximum`, while p99/p95 are labeled `latest_complete_epoch_aligned_window` so consumers do not present them as true trailing-window percentiles.
* CPU utilization is returned in Percent and needs no unit conversion.
* Fetched on-demand when a user opens the detail view; not stored per scan.

---

# 21. Metric API and cost discipline

* Use `GetMetricData` for both paths. Billing follows the submitted metric-stat queries; statistics requested at different periods are separate queries and must not be described as one metric request.
* Keep the confidence statistic set to {p99, p95, max}. The trend currently submits three daily queries plus two percentile queries for each of six windows (15 query IDs total).
* `GetMetricData` is billed per metric requested regardless of datapoint count or `NextToken` pagination, so `NextToken` pagination is cost-neutral. The 60-day decision scan may paginate freely; it does not re-bill.
* Server-side aggregates (large `Period`, one datapoint) are optional and used only to cut round-trips/latency where per-timestamp math is not required. They must never be used for EBS-combined IOPS/throughput or exact fixed-period network normalization, which require local per-timestamp math.
* Reference cost (US East, per full run, 10,000 instances): decision scan about $1.40; confidence trend cost is negligible because it is on-demand and per-metric billing ignores the 15-month depth.

---

# 22. Telemetry disclosure

The recommendation surfaces, per always-on metric, whether it is present and how many days of data were actually considered — not the requested window.

```python
observed_days = (
    sample_count
    * period_seconds
    / 86400
)
```

* `observed_days` is computed from data already returned, so it self-corrects for young instances and sparse data. An instance launched five days ago shows about five days, not 60.
* This is disclosure, not a gate. Thin data still produces a recommendation; the user decides. The UI should emphasize low values (for example, flag fewer than seven days) but must not block.

For network, the reassurance figure is the **60-day** decision-window peak, not a
15-month value (see Section 20). Both directions are reported separately as
`max` and `p99` in Mbps/Gbps against the target's baseline and peak:

```text
Network In:  p99 180 Mbps · max 640 Mbps — about 24% of the target's
             750 Mbps baseline (burst to 10 Gbps available). Ample headroom.
```

Example surfaced to the user:

```text
Considered: CPU ~58 days · Memory ~40 days · Network In ~58 days · Network Out ~58 days
15-month peak CPU 22% (p99 9%) — target leaves about 4x headroom
15-month peak memory 61% (p99 54%) — target leaves about 1.3x headroom
60-day peak Network In 640 Mbps (p99 180) · Out 210 Mbps (p99 95) — well under 750 Mbps baseline
```

---

# 23. Telemetry tests

```text
observed_days equals sample_count * period / 86400 and reflects actual,
not requested, span for a young instance.

The decision scan uses a 60-day, 5-minute window and changing it does not
alter the confidence-trend path.

Bucket Maximum comes from the trailing daily-Max series. Bucket percentiles
come only from the latest complete epoch-aligned per-bucket query result; a
partial final bucket is never displayed as a full window.

The confidence trend requests CPUUtilization and, when a memory source was
discovered, the discovered memory series; it never requests network for the
15-month horizon.

The memory trend queries the discovered namespace/metric/dimensions, not a
hardcoded default, and omits memory when no source was discovered.

Network max and p99 per direction are available for the 60-day decision window
and are not extended to the 15-month horizon.

Daily Max over a window containing a known spike returns the spike;
daily Average does not.
```

---

# 24. Out-of-scope contexts return DEFERRED

Add `DEFERRED` and `INSUFFICIENT_DATA` to the recommendation classification set. A resource in any of the following contexts must never be evaluated for a standalone resize; it returns `DEFERRED` with a specific reason code before candidate generation.

| Context | Reason code |
| --- | --- |
| Auto Scaling group member | `MANAGED_BY_ASG` |
| ECS container instance | `MANAGED_BY_ECS` |
| EKS or Kubernetes node | `MANAGED_BY_KUBERNETES` |
| Non-default tenancy (dedicated or host) | `UNSUPPORTED_TENANCY` |
| Spot or non-standard lifecycle | `UNSUPPORTED_LIFECYCLE` |
| Instance store present, usage unknown or active | `INSTANCE_STORE_USAGE_UNKNOWN` |
| GPU catalog capability but no usable GPU telemetry | `ACCELERATOR_TELEMETRY_UNAVAILABLE` |
| GPU catalog device count differs from observed telemetry | `ACCELERATOR_TELEMETRY_INCOMPLETE` |

The scanner persists these fields before the guard runs. From the existing `DescribeInstances` response it uses:

```text
Placement.Tenancy                     -> tenancy
InstanceLifecycle (spot / scheduled)  -> lifecycle
Tags aws:autoscaling:groupName,
     aws:eks:cluster-name, ECS cluster -> management context
BlockDeviceMappings + instance-type
     instance-store device count       -> instance-store presence
```

Persist these into `Ec2Inventory.metadata_json` (for example `management_context`, `tenancy`, `lifecycle`, `instance_store_present`). The guard is then a single early check in `EC2Rightsizer._recommend`.

Instance-store handling is controlled by
`EC2ScopePolicy.allow_unknown_instance_store_usage`. It defaults to `false`.
With the default policy, known instance-store presence or an unresolved
capability returns `DEFERRED / INSTANCE_STORE_USAGE_UNKNOWN`. A request may
set `allow_unknown_instance_store_usage=true` on either recommendation
endpoint to continue evaluation. That override records HIGH compatibility
risk, surfaces `INSTANCE_STORE_USAGE_UNKNOWN`, and forces every otherwise
eligible candidate to `CONDITIONAL`; it can never produce `ACTIONABLE`.

Existing inventory rows use the current instance type's structured catalog
capability as a fallback. If neither persisted metadata nor the catalog proves
that instance store is absent, the default policy defers safely.

Deployment note: populate the global `ec2_instance_specs` table once from
`DescribeInstanceTypes` in `us-east-1` before enabling the default policy.
Instance specifications are keyed globally by instance type while pricing stays
regional. Types absent from the `us-east-1` response remain explicit coverage
gaps and use assumed/unknown capability paths; no secondary regional fetch is
performed. Without structured specs or a persisted
`instance_store_present=false`, all affected inventory rows safely collapse to
`DEFERRED / INSTANCE_STORE_USAGE_UNKNOWN`. Operators may temporarily use
`allow_unknown_instance_store_usage=true`, accepting CONDITIONAL-only results,
while capability coverage is being populated. Monitor
`capability_catalog.coverage_ratio` after deployment.

`INSUFFICIENT_DATA` remains reserved. Thin telemetry is disclosed but does not
change classification.

The accelerator rows are evaluated before candidate generation. A catalog GPU
source with no candidates, discovery failure, query failure, or no datapoints
returns `DEFERRED / ACCELERATOR_TELEMETRY_UNAVAILABLE`. A mismatch between the
catalog device count and the number of devices **contributing to the aligned
demand series** (both `utilization_gpu` and `memory_used` present — see
Section 19) returns `DEFERRED / ACCELERATOR_TELEMETRY_INCOMPLETE`; a broken
agent on two of eight devices must not look like six idle devices, and neither
must two devices whose VRAM series is missing. Both responses retain the underlying telemetry
reason and observed count as evidence.

## 24.1 Scope-gating tests

```text
An ASG-tagged instance returns DEFERRED with MANAGED_BY_ASG and produces
no candidates.

Non-default tenancy returns DEFERRED with UNSUPPORTED_TENANCY.

Instance store present with unknown usage returns DEFERRED; instance store
absent proceeds normally.

Legacy or unresolved instance-store capability returns DEFERRED under the
default policy. With the request-scoped override it proceeds with HIGH
compatibility risk, includes INSTANCE_STORE_USAGE_UNKNOWN, and is restricted
to CONDITIONAL.
```

---

# 25. Tiered instance recommendations (multiple options)

V1 presents up to three sizing options per instance so the user chooses the
tradeoff between savings and headroom, instead of a single take-it-or-leave-it
target.

## 25.1 The three tiers

| Tier | Target utilization | Meaning |
| --- | --- | --- |
| Conservative | 0.55 (45% headroom) | Larger target, least savings, most cushion |
| Balanced | 0.70 (30% headroom) | The shipped V1 policy; the default/recommended option |
| Aggressive | 0.85 (15% headroom) | Smallest target, most savings, least cushion |

The ratios are configurable policy. **Balanced equals the current V1 compute
policy**, so the Balanced option is exactly the single recommendation V1 produces
today — nothing regresses. Balanced is the default; Aggressive and Conservative
are alternatives on either side.

## 25.2 Tiering is a selection layer, not a re-run

Every candidate already carries the data to place it in a tier. Projected
utilization is derived from the same coremark and memory figures the compute gate
already uses:

```python
projected_cpu_util = (
    cpu_p99 / 100.0
    * current.coremark / target.coremark        # None -> fall back to vCPU ratio
)
projected_memory_util = (
    estimated_used_memory_mib / target.memory_mib
)   # estimated_used_memory_mib = current.memory_mib * memory_p99 / 100
    # (conservative fallback retains current memory when no memory series)

projected_util = max(projected_cpu_util, projected_memory_util)
```

This is the same relationship the existing gate encodes: a candidate passes at
ratio `r` exactly when `projected_util <= r`. Tiering therefore computes
`projected_util` once per candidate and buckets it — it does not run selection
three times.

## 25.3 Candidate generation and tier selection

* Candidate **generation** uses the **loosest (Aggressive) ratio** as the CPU and
  memory gate, so smaller options that only fit at Aggressive are included in the
  set. All existing constraints (architecture, family gate, network, EBS, scope,
  positive savings) still apply unchanged.
* For each tier, the chosen option is the **cheapest candidate with
  `projected_util <= tier_ratio`**. Because cheaper means smaller means higher
  utilization, the picks nest by size: Conservative ⊇ Balanced ⊇ Aggressive.
* A tier with no qualifying cost-saving candidate is `null` (for example, no
  cushioned Conservative option that still saves money).
* If both CPU and memory metrics are absent, candidates that retain current CPU
  and memory capacity remain in the savings-ranked `recommendations` list, but
  `projected_util` is unavailable, every tier is `null`, and `tiers.default` is
  `null`; V1 does not invent a Balanced tier without measured utilization.

## 25.4 Presentation rules

* **Dedup and label.** When tiers resolve to the same target, present one option
  labeled with every tier it satisfies (for example `Balanced · Aggressive`).
  Always anchor on Balanced.
* **Single-option fallback.** When all present tiers collapse to one target,
  return a single recommendation and omit the tier UI — tiers that do not create
  a real choice are noise.
* **Per-tier classification is independent.** A smaller Aggressive target has a
  smaller network baseline and EBS capability, so it may be CONDITIONAL while
  Conservative is ACTIONABLE. Never average risk or classification across tiers.

## 25.5 Performance delta (coremark)

The catalog stores a **multi-thread CoreMark score scaled to the instance's total
vCPUs** (a whole-instance throughput figure). Each option reports the compute
delta versus the current instance as a simple percentage:

```python
performance_ratio = target.coremark / current.coremark
performance_change_pct = (target.coremark - current.coremark) / current.coremark * 100
```

Example: current 220,000, target 260,000 → `+18%`.

### When to surface it

The sign depends on the move, and the two cases are presented differently:

* **Positive** (`performance_change_pct > 0`) — a same-size newer-generation or
  cross-family swap whose target has greater total throughput (for example
  `m5.xlarge → c6i.xlarge`, 59k → 70k). **Surface it prominently** as a confidence
  signal, for example "**+18% CPU performance** — and cheaper." This is the
  awareness win: the recommendation is not just cheaper, it is faster.
* **Negative or flat** (`performance_change_pct <= 0`) — the normal capacity
  reduction of a downsize (fewer vCPUs). **Do not headline the negative
  percentage.** A downsize showing "−40% performance" reads as a downgrade even
  though it is correct. Lead instead with the headroom story already available
  (`projected_util`, for example "runs at ~52% at peak — comfortable headroom");
  the raw compute-capacity delta may appear in the detail view, never as a
  headline red number.

### Framing and coverage

* Report directionally with a `~` — the scores are **modeled**, not measured, so
  present "~+18%", never a false-precision figure.
* CoreMark is a **CPU integer-throughput benchmark only** — not memory bandwidth,
  I/O, or single-thread latency under contention. Never label it "application
  performance."
* Coverage is partial (roughly three quarters of types). When `coremark` is null
  for either the current or target instance, report the performance delta as
  **unavailable** — never fabricate it — and do not block the recommendation;
  tier bucketing and the memory floor are unaffected (see §25.2–§25.3).
* Comparisons are within-architecture only (candidate generation is
  same-architecture, §1), which is the reliable regime for CoreMark; do not
  extend the percentage to cross-architecture (Graviton) moves without
  revalidating the score across architectures.

## 25.6 Binding dimension

Each option reports the **binding dimension** — the dimension whose limiting
ratio is highest among `{cpu, memory, network, storage}`. This explains why a
collapsed or single result cannot go smaller (for example "limited by memory"),
preempting the "did it really try?" doubt.

## 25.7 Response shape

The recommendation envelope adds a `tiers` object alongside the existing
`recommendations` list:

```text
tiers:
  conservative: <option> | null
  balanced:     <option> | null   # the default/recommended
  aggressive:   <option> | null
  default: "balanced"
```

Each `<option>` references a candidate by `target_instance_type` and carries its
`projected_util`, `performance_ratio`, `performance_change_pct` (null when either
coremark is null), `binding_dimension`, savings, and its own classification/risk.
The `recommendations` list remains savings-ranked (Option A
— tiers are a selection over it, ranking is unchanged); note the list widens
because generation now uses the Aggressive gate. The Balanced option is
byte-for-byte the target V1 produced before tiering.

Candidate limiting preserves that guarantee. Select the legacy Balanced target
over the full eligible set, reserve it one returned-list slot when it would
otherwise be displaced by higher-savings Aggressive-only candidates, fill the
remaining slots by savings, re-sort for display, and select tiers only from the
limited returned list. The list never exceeds `candidate_limit`; when the limit
is one, the reserved target satisfies both Balanced and Aggressive because the
tier gates are nested. Conservative may be null.

## 25.8 Tiering tests

```text
projected_cpu_util equals cpu_p99/100 * current.coremark / target.coremark, and a
candidate is placed in a tier exactly when projected_util <= that tier ratio.

Three distinct targets across a wide size range yield three distinct tiers;
a workload with one viable downsize collapses to a single recommendation.

Conservative is null when no cushioned option still saves money.

The Balanced option equals the pre-tiering single recommendation (no regression).

performance_change_pct equals (target.coremark - current.coremark)/current.coremark
* 100, is positive for a higher-throughput target and negative for a downsize, and
is reported unavailable (null) when either coremark is null — without breaking tier
assignment. A positive delta is surfaced prominently; a negative delta is not
headlined (the headroom story leads instead).

binding_dimension reports the highest limiting ratio dimension.

An Aggressive option may be CONDITIONAL while Conservative is ACTIONABLE for the
same instance; classification is never averaged across tiers.
```

# 27. Savings previews

Status: **implemented.** This section is additive; offline coverage is in test
plan Section 3.10.

## 27.1 Purpose and placement

The engine sometimes *knows* savings may exist but cannot verify them: memory
telemetry is missing, or a cheaper arm64 (Graviton) type exists but the
workload runs x86. Those candidates remain rejected from recommendations
(`MEMORY_REQUIREMENT_NOT_MET`, `ARCHITECTURE_INCOMPATIBLE`) and the user never
learns what enabling the memory metric or planning a migration could be worth
unless the corresponding preview is enabled.

V1.1 surfaces them in a response section separate from `recommendations`:

```text
savings_previews: list of preview objects, each with
    kind                     MEMORY_METRIC_MISSING_DOWNSIZE | GRAVITON_MIGRATION
    target_instance_type
    monthly_savings, yearly_savings   (same rounding rules as recommendations)
    classification           PREVIEW (memory kind) | OPPORTUNITY (Graviton kind)
    blockers                 what must change before this could become real
    evidence                 same evidence envelope style as recommendations
```

Hard rules that keep previews from weakening the core product:

```text
A preview never appears in `recommendations`, never participates in tiers,
ranking, candidate_limit, or rejection_summary changes, and is never
ACTIONABLE or CONDITIONAL.

Every invariant on `recommendations` (capacity floors, hard constraints,
savings floor, classification rules) is completely unaffected by previews.

Previews are display-only: applying one is not supported in V1.1.

At most one preview per kind is returned: the highest-savings qualifying
candidate. Both kinds are independently enabled by policy flags
(default on).
```

## 27.2 MEMORY_METRIC_MISSING_DOWNSIZE

Shown only when the memory metric is absent. Candidates that were rejected
**solely** because they reduce memory below the current-capacity floor
(Section 19) are re-examined:

```text
Eligible for preview when the candidate passes every other gate:
architecture overlap, family gate, one-cent savings floor,
min_monthly_savings, CPU projection (measured CPU, or CPU capacity retained
when CPU is also missing), and network and EBS hard constraints.
```

The preview selects the highest-savings such candidate and must carry the
disclosure:

```text
blockers = ["MEMORY_METRIC_NOT_ENABLED"]

"This target has less memory than the current instance. Actual memory usage
is unknown because the CloudWatch agent memory metric is not enabled. Enable
memory metrics to verify whether this saving is achievable."
```

When the memory metric IS present, this preview kind is never emitted — real
evaluation governs, and a memory-reducing candidate is either a genuine
recommendation or a genuine rejection.

## 27.3 GRAVITON_MIGRATION

Shown when the current instance has no arm64 architecture support and the
catalog contains region-priced arm64 candidates. Selection is conservative
because cross-architecture CoreMark comparison is not reliable evidence:

```text
Eligible arm64 candidates must retain at least the current vCPU count and at
least the current memory, pass network and EBS hard constraints, and offer
positive savings under the same one-cent floor and `min_monthly_savings`.

Pick the highest-savings eligible candidate, with the standard deterministic
instance-type tie-breaker. Same-size analogues in matching Graviton families
(m -> m*g, c -> c*g, r -> r*g) normally win naturally when they offer the
greatest savings; no separate analogue override is applied.
```

Classification is `OPPORTUNITY`, consistent with Section 11's reservation of
that class for architecture migrations. The gated-family rule of Section 1 is
not bypassed: burstable/specialized arm64 families remain excluded unless the
current instance already shares the class. Required disclosure:

```text
blockers = ["ARCHITECTURE_MIGRATION_REQUIRED"]

"This target uses the arm64 (Graviton) architecture. Realizing this saving
requires validating and migrating the application to arm64 (rebuilt
binaries/images and compatible dependencies). Compatibility is not validated
by this recommendation."
```

`ARCHITECTURE_INCOMPATIBLE` rejection tallies in `rejection_summary` are
unchanged; the preview is derived from that rejected set, not a reclassification
of it.
