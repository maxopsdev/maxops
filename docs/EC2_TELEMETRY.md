# EC2 Telemetry

How EC2 CloudWatch telemetry is collected, what each consumer is allowed to
assume about it, and which invariants must not be broken.

This document is cross-cutting on purpose. EC2 telemetry is consumed by two
independent subsystems that live in different trees — `maxops-backend/rightsizers/`
and `maxops-backend/app/checks/` — and neither one owns the contract.

For rightsizer semantics (tier placement, constraint evaluation, warning
vocabulary), `rightsizers/ec2/ec2_rightsizer_spec.md` remains the family
authority. This document covers collection and the shape of what is handed
downstream.

---

## 1. Three collection paths

They are independent. Do not conflate them.

| Path | Purpose | Period | Horizon | Entry point | When |
| --- | --- | --- | --- | --- | --- |
| Decision scan | Drives rightsizer recommendations | 300s | up to 60 days | `AWSAdapter.get_ec2_rightsizing_metrics` | every scan |
| Check scan | Drives check findings | 300s | 7–14 days | `AWSAdapter.get_resource_utilization` → `_get_ec2_utilization` | every scan |
| Confidence trend | User-facing evidence only | 86400s | up to 15 months | `AWSAdapter.get_ec2_confidence_trend` | on demand |

The horizons follow CloudWatch retention: 5-minute data is kept 63 days, 1-hour
data 455 days. Sixty days is the widest decision window that stays inside
5-minute retention; 15 months is reachable only at coarse granularity.

**The confidence trend never gates a decision.** It exists so a user can accept
or reject a recommendation on evidence. A daily `Maximum` on a percentage gauge
survives CloudWatch's long-term rollup as an honest ceiling, which is what makes
it suitable for a chart and unsuitable for a threshold.

---

## 2. Where each path runs

Both scan paths execute inside `app/services/scan_service.py::run_inventory_scan`,
in phase order:

1. **`inventory` phase** — `_enrich_ec2_rightsizing_metrics` calls
   `get_ec2_rightsizing_metrics` and writes normalized telemetry into
   `resource["metadata"]`. Persisted by `_store_inventory_resource` onto the
   typed `EC2Inventory` row.

2. **`policies` phase** — `check_registry.execute_check` runs the checks in
   `app/checks/ec2/`. They call `get_resource_utilization` through the adapter
   they are handed. Their output is persisted by `_store_maxops_finding` onto
   `MaxOpsInventory` — a **different table**.

The two phases do not share telemetry. See §7.

---

## 3. What the decision scan collects

`get_ec2_rightsizing_metrics` submits 18 query IDs at 300s:

| Key | Metric | Statistic |
| --- | --- | --- |
| `cpu_percent` | `CPUUtilization` | Average |
| `network_in_bytes` / `network_out_bytes` | `NetworkIn` / `NetworkOut` | Sum |
| `network_packets_in` / `_out` | `NetworkPacketsIn` / `Out` | Sum |
| `ebs_read_operations` / `ebs_write_operations` | `EBSReadOps` / `EBSWriteOps` | Sum |
| `ebs_read_bytes` / `ebs_write_bytes` | `EBSReadBytes` / `EBSWriteBytes` | Sum |
| `ebs_io_balance_percent` / `ebs_byte_balance_percent` | `EBSIOBalance%` / `EBSByteBalance%` | Minimum |
| `instance_ebs_iops_exceeded` / `..._throughput_exceeded` | corresponding `…ExceededCheck` | Maximum |
| `memory_percent` | discovered source (see §5) | Average |
| 4 × allowance signals | `CWAgent ethtool_*_allowance_exceeded` | Sum |

Counter metrics use `Sum` deliberately. Capacity interpretation happens in the
rightsizer, never in the adapter.

`_enrich_ec2_rightsizing_metrics` then writes to `resource["metadata"]`:

- `rightsizing_metrics` — `{"14d", "30d", "60d"}`, each with `lookback_days`,
  `period_seconds`, `normalized` (rate-converted series summaries), `signals`,
  `aggregation_evidence`, and `normalization_version: "ec2-v1-exact"`.
- `avg_memory_utilization`, `metric_history["memoryutilization"]`,
  `memory_metric_status`, `memory_metric_source`.

Each entry under `normalized` is a `_rate_summary`:

```python
{"p95": float|None, "p99": float|None, "maximum": float|None, "sample_count": int}
```

**The raw point series is not persisted.** Only these summaries survive. Any
statistic not computed here cannot be recovered downstream.

---

## 4. What the check scan collects

`_get_ec2_utilization` submits 6 query IDs — `CPUUtilization`, `NetworkIn`,
`NetworkOut`, each at `Average` and `Maximum` — plus the memory queries and,
when discovered, one GPU `Average` query per device.

It returns two distinct structures, and the difference between them is
load-bearing:

- **`metric_history[alias]`** — the raw per-period series
  (`timestamps`, `average`, `maximum`). The `p90`/`p95`/`p99` keys still exist
  and are **always empty**; they are retained only so the persisted
  `metric_history_json` column shape stays stable. Never gate on them.

- **`metric_summary[alias]`** — window-wide statistics, and **the only valid
  source for a gating decision**:

```python
{
    "average": float|None,      # mean of the series
    "maximum": float|None,      # max of the per-period Maximum series
    "p90": float|None,          # numpy.percentile over the whole window
    "p95": float|None,
    "p99": float|None,
    "sample_count": int,
    "period_seconds": int,
}
```

For `gpuutilization`, percentiles are over the across-device busiest series,
while `average` is over the across-device mean series. This preserves the
conservative busiest-device gate without giving the common `average` alias a
different meaning from CPU and memory.

### Why percentiles are computed locally

CloudWatch's `p90` statistic returns the 90th percentile *within each period
bucket*. At 300s with basic monitoring each bucket holds one datapoint, so a
per-bucket p90 equals that bucket's maximum — and taking `max()` across the
series yields the window peak, not the window p90.

That makes `p90`, `p95`, `p99` and `maximum` all return the same number, which
silently collapses four independent gates into one and replaces the operator's
configured peak tolerance with the strictest percentile threshold. A single
brief spike then disqualifies an otherwise idle instance.

So the adapter requests only `Average`/`Maximum` and computes percentiles across
the full window with `numpy.percentile`. `maximum` still comes from CloudWatch's
per-bucket `Maximum` series, where `max()` across buckets is genuinely the
window maximum.

**Known approximation:** with detailed (1-minute) monitoring a 300s bucket holds
five raw datapoints and `Average` is their mean, so percentiles are computed over
slightly smoothed values. Accepted for V1; far smaller than the error it replaces.

---

## 5. Memory metric discovery

Memory is not an `AWS/EC2` metric. `_ec2_memory_metric_candidates` discovers a
published series (namespace, metric name, dimensions) and both scan paths reuse
the **discovered** source rather than re-guessing it.

`memory_metric_status` is the authority on whether memory is usable:

| Value | Meaning |
| --- | --- |
| `"usable"` | a series was discovered and returned datapoints |
| `"unavailable"` | no series, or no datapoints in the window |

A numeric memory field may still be present when status is `"unavailable"`; it is
a backward-compatibility placeholder and **must not be read as an observation**.

## GPU telemetry

GPU identity comes from the global `ec2_instance_specs` catalog, independently
of regional street-pricing coverage. When CloudWatch agent `nvidia_smi` metrics
are discovered, each GPU is queried separately. The history has one
`gpu_devices[device_index]` entry per `index` dimension, with that device's
Average series. The top-level GPU `average` is the mean across devices at each
timestamp; `maximum` is the maximum of those per-device Averages at that
timestamp. CPU and memory `maximum` instead comes from CloudWatch's Maximum
statistic for the period. The derivations differ, but both represent the
busiest signal a consumer must respect at that moment.

This is intentionally different from SageMaker's native `GPUUtilization`,
which is summed across GPUs. CloudWatch agent GPU values are per-device
percentages from 0–100, so an instance with one busy GPU out of eight remains
busy even when the across-device mean is low.

Fractional GPU catalog rows (for example `g6f.*` and `gr6f.*`) report a summed
GPU count of zero because they are slices. MaxOps treats each slice as one
addressable device and sets `fractional=True`; its utilization still uses the
same 0–100 per-device scale.

`gpu_metric_status` is `"usable"` only when at least one discovered device
returned datapoints. Otherwise it is `"unavailable"`, with
`gpu_metric_unavailable_reason` set to one of these:

| Reason | Meaning |
| --- | --- |
| `"no_candidates"` | No NVIDIA metric was discovered. |
| `"discovery_unavailable"` | The ListMetrics sweep failed. |
| `"no_datapoints"` | A successful query returned no device datapoints. |
| `"query_failed"` | The GetMetricData query raised. |
| `"gpu_count_unknown"` | The resource type could not be resolved to an addressable GPU count. |

GPU discovery has no fallback source: inventing one could make a busy training
job look idle.

For gating, `metric_summary["gpuutilization"]` computes p95/p99 over the
across-device `maximum` series. Thus p95 means the busiest device was idle 95%
of the window. A GPU instance without usable GPU telemetry is never considered
idle.

---

## 6. Invariants

These hold across every path. Breaking one is a correctness bug, not a style
preference.

1. **Absent is not zero.** A missing or empty series yields `None`, never `0.0`.
   An empty list read as `0.0` satisfies every `< threshold` gate and
   manufactures a finding from data that does not exist. Use
   `app/checks/base.py::get_metric_history_statistic`, which returns
   `Optional[float]`.

2. **A missing signal must not produce a finding.** Checks skip the resource and
   log the reason. Never flag on an unknown.

3. **Unmeasured memory is disclosed, not assumed.** Per the EC2 rightsizer spec,
   EC2 uses disclosure rather than a hard cap: a recommendation may stand on
   CPU/network evidence alone, but the absence must be recorded
   (`memory_metric_status` plus a note in `check_reason`), and
   `avg_memory_utilization` must be `None` rather than a fabricated `0.0`.

4. **Gate on `metric_summary`, never on `metric_history` percentiles.** See §4.

5. **Periods are derived, never hardcoded.** `derive_ec2_metric_period` picks the
   largest valid CloudWatch period that is ≤ the requested period and ≤ the
   window, so a period can never exceed the range it covers.

6. **Every `GetMetricData` call paginates.** A response caps at 100,800
   datapoints. At 300s over a configurable window this cap is reachable, and a
   dropped `NextToken` is silent truncation. Accumulate `Timestamps`/`Values`
   **per result `Id` across pages** before parsing — a `{item["Id"]: item}` dict
   comprehension keeps only the last page.

7. **A GPU instance without usable GPU telemetry is never idle.** GPU
   utilization is optional at the AWS account level, but it is mandatory before
   MaxOps can emit an idle or rightsizing finding for a catalog GPU instance.

---

## 7. The per-scan cache

`CachedAWSAdapter` (`app/services/scan_service.py`) memoizes expensive reads for
the life of one scan. It is what checks receive as `aws_adapter`, so it
intercepts without any check signature change. It is constructed per region per
scan via `adapter_cache` in `run_inventory_scan`, so entries cannot outlive a
scan.

`get_resource_utilization` is keyed on:

```python
("get_resource_utilization", resource_id, resource_type, region, window_days)
```

**The end timestamp is deliberately excluded.** Each check captures its own
`datetime.utcnow()` *once, before* iterating instances, so the two EC2 checks'
timestamps differ by however long the first check ran — minutes on a large
account. Including the timestamp, even quantized to a coarse bucket, makes the
cache miss for every instance whenever that gap exceeds the bucket, which is
exactly the large-account case where the savings matter. Do not add it back.

The window length stays in the key: a 7-day and a 14-day request are different
data and must not share an entry.

---

## 8. TODO — unify collection across checks and rightsizers

**Status: open. Not scheduled.**

The `inventory` and `policies` phases still fetch overlapping CloudWatch data for
the same instance in the same scan. `CachedAWSAdapter` cannot bridge them: they
are different methods returning different shapes, so no key makes one satisfy the
other.

The check window is a strict subset of the decision window, so in principle the
checks could read what the enrichment already collected. Four things block it
today:

1. **The raw series is discarded.** `_rate_summary` persists only
   `{p95, p99, maximum, sample_count}`. Nothing downstream can recompute a
   statistic that was not already computed.
2. **Two required statistics are missing.** Checks gate on `average`, `maximum`,
   `p90`, `p95`, `p99`. The summary has no `p90` and no `average`.
3. **Windows do not align.** The enrichment stores 14d/30d/60d, while
   `idle_instances` defaults to **7 days**, which is not a stored window.
4. **Network statistics and units diverge.** The enrichment requests `Sum` and
   normalizes to Mbps/pps. The checks request `Average`/`Maximum` in raw bytes
   and compare against `network_threshold`. Not convertible without the raw
   series that (1) discards.

There is a consequence beyond cost: network is `Sum`-derived on the
`EC2Inventory` row and `Average`-derived on the `MaxOpsInventory` finding. Both
can surface in the UI for the same instance and will legitimately disagree,
because they answer different questions under similar labels.

Sketch of the work:

1. Extend `_rate_summary` with `p90` and `average`; add a 7-day window, or make
   check lookbacks configurable to the stored set.
2. Reconcile the network statistic, or persist both representations under
   unambiguous names.
3. Route enriched telemetry into `check_registry.execute_check`, which today
   receives only `aws_adapter`. **This is the significant step** — it changes a
   signature shared by every check across every resource type, not just EC2, and
   should be designed against the non-EC2 checks before being attempted.

Step 3 is the reason this is filed rather than done. Steps 1 and 2 are
independently useful and could land first.
