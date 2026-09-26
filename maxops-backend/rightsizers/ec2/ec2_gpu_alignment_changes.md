# EC2 GPU Rightsizing — Alignment Decisions

This document records the decisions that align the EC2 rightsizer with the GPU
demand and telemetry rules in `ec2_rightsizer_spec.md`.

## 1. GPU capability is catalog data, not a family-name guess

Rule: carry addressable device count, fractional status, model, and per-device
VRAM from `GpuInfo` into `EC2CatalogEntry` and `InstanceShape`.

Reason: candidate gating must compare what the catalog verifies. Fractional
`g6f`/`gr6f` rows with catalog count zero are one addressable slice and reuse the
single shared parser.

Reason codes introduced: `GPU_MODEL_MISMATCH`.

## 2. GPU sources are discovered and device-scoped

Rule: the decision scan joins GPU utilization, VRAM occupancy, and optional VRAM
capacity to the existing regional `ListMetrics` sweep, using the `index`
dimension and paginated `Average` queries. `memory_used` is validated as finite,
non-negative MiB; memory-bandwidth utilization and `memory_total` are not used as
occupancy.

Reason: absent is not zero, and a guessed or unit-misclassified series can turn
a busy accelerator into an unsafe resize.

Reason codes introduced: `ACCELERATOR_TELEMETRY_UNAVAILABLE`,
`ACCELERATOR_TELEMETRY_INCOMPLETE`.

## 3. Demand uses aligned full-device timestamps

Rule: retain only timestamps where every observed device reports. Busy-device
demand is the maximum simultaneous count, using configurable utilization and
VRAM-floor thresholds. VRAM demand is the maximum per-device occupancy with tier
headroom; GPU utilization magnitude never sizes a target.

Reason: scheduled or bursty jobs are safety-critical. A nightly eight-GPU job can
have p95 of zero while still requiring eight devices. Dropped samples are
persisted as aggregation evidence.

Reason codes introduced: `GPU_DEVICE_DEMAND_USES_WINDOW_MAXIMUM`.

## 4. GPU telemetry gates before candidate generation

Rule: a GPU source with unavailable status defers, and an observed device-count
mismatch defers. "Observed" means devices with **both** a utilization and a
VRAM series in the window — the count that actually feeds the demand math —
not the number of series discovery found. Both responses preserve the
underlying telemetry reason and observed count.

Reason: uncertainty resolves to no recommendation; six apparently idle devices
must not be inferred from a broken agent on two of eight devices. Comparing the
discovery count instead would let a device with a missing VRAM series drop
silently out of the demand math while the guard still passed.

Reason codes introduced: `ACCELERATOR_TELEMETRY_UNAVAILABLE`,
`ACCELERATOR_TELEMETRY_INCOMPLETE`.

## 5. Same-model capacity is the V1 candidate boundary

Rule: GPU targets need the source model, enough devices, and enough VRAM per
device. A non-GPU target is permitted only when full-window required device
demand is zero and is `CONDITIONAL`.

Reason: V1 has no GPU performance model and cannot compare utilization magnitude
or performance across models. Family changes are safe only when the GPU model
matches (for example `p4de` to `p4d`).

Reason codes introduced: `GPU_MODEL_MISMATCH`, `GPU_UNUSED_FULL_WINDOW`.

## 5a. The family gate still applies to GPU sources

Rule: the GPU capability gate is additive to the target-side family gate, not a
replacement. The single relaxation is that a same-GPU-model target may cross a
family token (`p4de → p4d`). Burstable `t*` targets stay gated for a GPU
source, and other accelerator classes (`inf*`, `trn*`, `f*`, …) are not
"non-GPU targets" — they are a different gated class and reject as
`FAMILY_NOT_ELIGIBLE`.

Reason: the family gate exists so savings-first ranking cannot surface an
unsuitable cheap type. Bypassing it for GPU sources reintroduced exactly that
failure: an unused-GPU `g5` was offered `t3.2xlarge` and `inf2.xlarge` with no
rejection recorded. Found in review of the first implementation.

Reason codes introduced: none; `GPU_DEVICE_COUNT_REQUIREMENT_NOT_MET` and
`GPU_VRAM_REQUIREMENT_NOT_MET` are named here as the GPU gate's own rejection
diagnostics, alongside `GPU_MODEL_MISMATCH`.

## 6. Thin GPU history is a conditional cap

Rule: GPU recommendations with fewer than the GPU policy minimum samples or
observation window are capped at `CONDITIONAL` and disclose
`GPU_OBSERVATION_WINDOW_TOO_SHORT`.

Reason: device-count demand is a window maximum and short history can miss a
monthly job. This is deliberately stricter than EC2 CPU/memory, where thin
telemetry is disclosure-only, because accelerator capability removal or device
reduction has asymmetric failure cost.

Reason codes introduced: `GPU_OBSERVATION_WINDOW_TOO_SHORT`.

## 7. V2 exclusions remain explicit

Rule: cross-GPU-model recommendations, GPU utilization-magnitude sizing, and
GPU model performance comparisons are V2. This release does not recommend an
A100 to A10G move, and does not attach a savings figure to an unverified model
translation.

Reason: those decisions require a GPU workload/performance model that does not
exist yet.

Reason codes introduced: none beyond the V1 mismatch diagnostic.
