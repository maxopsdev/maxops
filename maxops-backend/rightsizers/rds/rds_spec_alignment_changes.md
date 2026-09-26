# RDS Rightsizer Spec — Alignment Resolution

Scope: `rds_rightsizer_spec.md` and `rds_rightsizer_ui_spec.md` were reviewed
against the EC2 and ElastiCache contracts. This file records the final decisions
from that review. No RDS implementation code exists yet.

## 1. Thin telemetry is visible and RDS-specific

Short or sparse CPU/FreeableMemory history is not a hard gate. Candidate math
runs on available points, but RDS caps the result at `CONDITIONAL` because a DB
instance class change causes an outage.

This is intentionally stricter than the authoritative EC2 backend contract,
where thin history is disclosure-only. The previous text incorrectly described
the conditional cap as exact EC2 behavior.

Distinct reason codes prevent a sparse long window from being called short:

```text
OBSERVATION_WINDOW_TOO_SHORT
OBSERVATION_COVERAGE_TOO_LOW
```

Both set telemetry risk HIGH. The storage recommendation retains its 30-day /
95% hard evidence requirement because reducing provisioned IOPS or throughput
has no unmeasured-dimension capacity floor and RDS can block another storage
change for six hours after modification begins.

## 2. Missing native metrics use capacity retention

A successfully completed CloudWatch query with no usable CPU points is not zero
CPU demand. Instance-class candidates must retain current vCPU capacity. Empty
FreeableMemory similarly requires retaining current memory capacity.

```text
RDS_CPU_METRIC_UNAVAILABLE_CURRENT_CAPACITY_RETAINED
RDS_MEMORY_METRIC_UNAVAILABLE_CURRENT_CAPACITY_RETAINED
```

Either condition sets telemetry risk HIGH and caps class candidates at
`CONDITIONAL`. If both metrics are absent, only candidates retaining both
capacities survive; `projected_util` and every tier are null, while the
savings-ranked capacity-retaining candidate list may remain visible.

`INSUFFICIENT_DATA` is now reserved for an actual collection/validation failure
such as AccessDenied or an API error with no persisted fallback. Persisted
telemetry distinguishes `EMPTY` from `ACCESS_DENIED`, `ERROR`, and `INVALID`.

## 3. Independent recommendation-kind status

The response reports evaluation status separately for:

```text
instance_class
storage_configuration
```

Each is one of `RECOMMENDED`, `NO_RECOMMENDATION`, `INSUFFICIENT_DATA`,
`NOT_APPLICABLE`, or `DEFERRED`. A failed class evaluation never suppresses a
complete storage recommendation, or vice versa.

Top-level classification comes from the headline returned recommendation. It
is `INSUFFICIENT_DATA` only when no recommendation exists and an applicable kind
could not run; a complete no-savings evaluation has a null classification.

The core spec now maps known blockers deterministically. Multi-volume or
dedicated-log storage is `NOT_APPLICABLE` for storage configuration while class
evaluation continues. Short/incomplete storage evidence is
`NO_RECOMMENDATION` with a stable blocker. Collection/API failure is
`INSUFFICIENT_DATA` only for the affected kind.

## 4. Tier placement remains compute-only

`projected_util` uses CPU, memory, and CPU AAS when those values are available.
Network and EBS affect constraints, risk, and classification, never tier
placement.

`binding_dimension` remains an overall limiting-dimension explanation and can
therefore be network or storage even though the tier figure is compute-only. It
is null when no comparable ratio exists.

## 5. Unknown RDS baselines are not estimated

RDS does not inherit EC2's family-shaped assumed network baseline across the
`db.` boundary. Missing network/EBS capacity remains unknown and
`CONDITIONAL`. UI language says **unknown capacity**, not approximate or
estimated capacity, and never fabricates a utilization ratio.

## 6. Trend semantics remain aligned

CPU and connections use daily Maximum headlines; FreeableMemory uses daily
Minimum. Bucket p99/p95 or p01/p05 values use the latest complete epoch-aligned
window and return null when unavailable. `bucket_semantics` prevents clients
from presenting them as true trailing-window percentiles.

## 7. Memory evidence chart projects every point

The UI no longer compares current FreeableMemory against a static projected
target line. It derives a target-projected daily series:

```python
estimated_used_memory_t = current_memory - current_freeable_memory_t
projected_target_freeable_t = target_memory - estimated_used_memory_t
```

That series is compared with the absolute free-memory floor. Negative projected
values are not clamped. When memory is absent, the chart is omitted and the UI
shows the current-memory capacity-retention rule.

## 8. Other resolved UI contracts

- Fleet `Confidence` is now `Observed days`; missing CPU/memory is named rather
  than displayed as zero days.
- Top-level classification excludes `REJECTED`; rejected targets remain in
  candidate diagnostics/tallies.
- `projected_util` and `binding_dimension` are nullable.
- Tier cards deliberately omit an EC2 CoreMark performance badge.
- Class and storage statuses render independently.
- Performance Insights remains conditional attribution and never substitutes
  for CPU or FreeableMemory sizing telemetry.
- Fleet sort order is `ACTIONABLE`, `CONDITIONAL`, `INSUFFICIENT_DATA`,
  `DEFERRED`, then null; complete no-savings rows are last.
- Scan-time failures persist structured status and produce per-kind outcomes on
  later reads. Live request-time failures use the shared error envelope when no
  safe persisted or partial response exists.
