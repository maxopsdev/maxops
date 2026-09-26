# SageMaker Checks — V1 Specification

Status: implementation contract. This document is the authority for the
`sagemaker_*` checks; `docs/EC2_TELEMETRY.md` §6 supplies the telemetry
invariants they inherit, and `rightsizers/ec2/ec2_rightsizer_spec.md` remains
the authority for anything the EC2 rightsizer does with GPU demand.

SageMaker is greenfield in MaxOps: no adapter methods, inventory model, registry
entries, checks, actions, or payload fixtures exist. V1 therefore ships the
vertical slice once — discovery → inventory → telemetry → checks → payload
harness — and six checks chosen to exercise every data class that slice must
support.

---

# 1. Scope

## 1.1 V1 checks

| Check ID | Resource | Data class | Action |
| --- | --- | --- | --- |
| `sagemaker_endpoint_idle` | endpoint | native | delete endpoint |
| `sagemaker_endpoint_overprovisioned` | endpoint | native | reduce instance count |
| `sagemaker_endpoint_gpu_underutilized` | endpoint | native | move to CPU or smaller GPU type |
| `sagemaker_notebook_no_auto_stop` | notebook instance | config | attach idle auto-stop lifecycle config |
| `sagemaker_notebook_idle` (with GPU variant) | notebook instance | agent | stop notebook |
| `sagemaker_training_no_managed_spot` | training job (recurring) | config | enable managed spot + checkpointing |

Data classes, which set the difficulty and the failure mode:

* **config** — answered from Describe/List calls. Never "unknown".
* **native** — SageMaker publishes the metric itself. Known namespace and
  dimensions. Available for endpoints and jobs only.
* **agent** — requires the CloudWatch agent on the instance. Discovered, never
  assumed. Notebook instances and Studio apps publish **nothing** natively.

## 1.2 V2 (named so the list is not rediscovered)

| Check | Signal | Data |
| --- | --- | --- |
| `sagemaker_notebook_stopped_long` | Stopped > N days; EBS volume still bills | config |
| `sagemaker_endpoint_underutilized` | low CPU/memory with invocations present → smaller type | native |
| `sagemaker_endpoint_no_autoscaling` | multi-instance variant, no Application Auto Scaling policy | config |
| `sagemaker_endpoint_serverless_candidate` | sporadic invocations, no GPU → serverless inference (OPPORTUNITY class) | native |
| `sagemaker_training_gpu_underutilized` | recurring jobs, `GPUUtilization ÷ count` low → smaller type | native |
| `sagemaker_job_stuck` | InProgress far beyond the job family's history | config |
| `sagemaker_studio_app_idle` (+GPU) | KernelGateway/JupyterServer InService, no activity | agent |
| `sagemaker_model_unreferenced`, `sagemaker_endpoint_config_orphaned` | housekeeping | config |
| `sagemaker_feature_group_online_store_unread` | zero `ConsumedReadRequestsUnits` | native |

---

# 2. Cross-cutting rules

## 2.1 Absent is not zero

Inherited verbatim from `docs/EC2_TELEMETRY.md` §6. A missing or empty series is
`None`; a check never produces a finding from an unknown signal; a resource
skipped for missing telemetry is logged with the reason. This matters more here
than for EC2: notebooks and Studio have no native metrics, so "no data" is the
*common* case, not the edge case, and treating it as idle would recommend
stopping every notebook without an agent.

## 2.2 Instance type resolution

SageMaker types are `ml.<family>.<size>`. The `ml.` prefix says nothing about
GPUs. Resolution order:

1. Strip `ml.` and look the remainder up in `ec2_instance_specs` via
   `app/utils/ec2_gpu_info.py` (`gpu_info_for_instance_type`) — this yields GPU
   count, model, and memory for every current family.
2. If absent, consult the **seeded table** `app/utils/sagemaker_instance_types.py`
   — the 20 GPU types from the SageMaker docs (Studio Classic notebook table),
   with explicit GPU counts. This exists because `p3.{2,8,16}xlarge` are no
   longer returned by `DescribeInstanceTypes` yet still run in accounts.
3. Otherwise: unknown. **Never infer GPU-ness from the family letter.**

Fractional GPU types follow the EC2 rule: one addressable device.

## 2.3 GPU utilization is summed — normalize before comparing

SageMaker's native `GPUUtilization` and `GPUMemoryUtilization` are **summed
across devices**: an 8-GPU instance ranges 0–800%. Every threshold in this
document is per-device. Divide by `device_count` from §2.2 before gating; if
`device_count` is unknown, the GPU signal is `unavailable` and the check skips
with reason `gpu_count_unknown`.

Consequence: only the **mean** per device is recoverable from a summed metric.
Per-device series exist only when the endpoint has `EnableEnhancedMetrics=True`
(dimension `AcceleratorId`). V1 gates on the mean and discloses
`GPU_AGGREGATION_MEAN_ONLY`; when `AcceleratorId` series are discovered, gate on
the busiest device instead and omit the disclosure. `GPUUtilizationNormalized`
(0–100, inference-component endpoints only) is preferred when present.

This is the opposite of the EC2 CloudWatch-agent shape (one series per device,
0–100 each). Do not import either normalization into the other path.

## 2.4 Windows and periods

Native endpoint/job metrics are 1-minute. Checks request `Average` and
`Maximum` at the derived period (`derive_ec2_metric_period` — rename or alias to
`derive_cloudwatch_period`; it is not EC2-specific) over the check's lookback,
with per-`Id` pagination. Window-wide percentiles are computed locally exactly
as `docs/EC2_TELEMETRY.md` §4 describes; never gate on CloudWatch's per-bucket
percentiles.

## 2.5 Telemetry status vocabulary

Every telemetry-consuming check writes, per signal:

```text
<signal>_metric_status            "usable" | "unavailable"
<signal>_metric_unavailable_reason  "no_candidates" | "discovery_unavailable"
                                    | "query_failed" | "no_datapoints"
                                    | "gpu_count_unknown"
```

Same words as the EC2 path. New reasons are added to `docs/EC2_TELEMETRY.md`
§GPU when introduced.

---

# 3. Discovery and inventory

## 3.1 Resource types

| `resource_type` | AWS calls | Identity |
| --- | --- | --- |
| `sagemaker_notebook` | `ListNotebookInstances` (+ `DescribeNotebookInstance` per item for lifecycle config, volume size, platform) | `NotebookInstanceName` |
| `sagemaker_endpoint` | `ListEndpoints` + `DescribeEndpoint` + `DescribeEndpointConfig` (variants, instance type/count, serverless config); `application-autoscaling:DescribeScalingPolicies` for the variant | `EndpointName` (+ `VariantName`) |
| `sagemaker_training_job` | `ListTrainingJobs` (CreationTimeAfter = now − `job_history_days`, default 30) + `DescribeTrainingJob` | `TrainingJobName` |

Canonical type `sagemaker` in `_canonical_resource_type`; `_inventory_call_types`
returns the three subtypes. Region-scoped like everything else.

## 3.2 Inventory model

`SageMakerInventory` (`sagemaker_inventory`), mirroring `EbsInventory`:
`inventory_id`, `resource_id` (the AWS name), `resource_subtype`
(`notebook|endpoint|training_job`), `resource_name`, `account_id`, `region`,
`instance_type`, `instance_count`, `status`, `created_at`, `metadata_json`,
`metric_history_json`, `generated_at`. Add to `INVENTORY_MODELS`, `database.py`
column backfill, and the resource-type list the UI reads.

`metadata_json` carries per subtype:

* notebook: `notebook_status`, `lifecycle_config_name`, `lifecycle_config_has_auto_stop`
  (see §5.4), `volume_size_gb`, `platform_identifier`, `direct_internet_access`,
  `last_modified_time`, GPU fields from §2.2, telemetry status fields.
* endpoint: `endpoint_status`, `endpoint_config_name`, `variants[]` each with
  `variant_name`, `instance_type`, `initial_instance_count`, `current_instance_count`,
  `serverless` (bool), `autoscaling_policy_present`, `enhanced_metrics_enabled`;
  GPU fields; telemetry status fields.
* training job: `training_job_status`, `instance_type`, `instance_count`,
  `enable_managed_spot_training`, `checkpoint_config_present`,
  `max_wait_time_seconds`, `training_time_seconds`, `billable_time_seconds`,
  `job_family` (§5.6), `creation_time`.

## 3.3 Telemetry sources

| Subtype | Namespace | Dimensions | Metrics |
| --- | --- | --- | --- |
| endpoint | `AWS/SageMaker` | `EndpointName, VariantName` | `Invocations` (Sum), `InvocationsPerInstance` (Sum), `ModelLatency` |
| endpoint | `/aws/sagemaker/Endpoints` | `EndpointName, VariantName` (+ `InstanceId`, `AcceleratorId` if enhanced) | `CPUUtilization`, `MemoryUtilization`, `GPUUtilization`, `GPUMemoryUtilization`, `GPUUtilizationNormalized` |
| training job | `/aws/sagemaker/TrainingJobs` | `Host = <job>/algo-<n>` | `CPUUtilization`, `MemoryUtilization`, `GPUUtilization`, `GPUMemoryUtilization` |
| notebook | **discovered** | see below | `mem_used_percent`, `nvidia_smi_utilization_gpu`, `nvidia_smi_memory_used`, CPU via agent `cpu_usage_*` |

**Notebook discovery generalizes the EC2 sweep.** `_discover_ec2_metric_candidates`
filters `ListMetrics` on the `InstanceId` dimension. Notebook agents installed
through the AWS-documented lifecycle configuration publish with dimension
`NotebookInstanceName` (namespace commonly `/aws/sagemaker/NotebookInstances`).
Generalize the sweep to take the identity dimension name as a parameter
(`InstanceId` for EC2, `NotebookInstanceName` for notebooks), cache per
`(region, identity_dimension)`, and reuse the same signal table. This is a
refactor of the existing function, not a copy of it.

A notebook with no discovered series is `unavailable / no_candidates`. That is
the expected state for most notebooks and it is why `no_auto_stop` (§5.4) is in
V1: it fixes the idle problem without needing the signal the idle check lacks.

---

# 4. Pricing and savings

`street_pricing_*` tables contain no `ml.*` rows. V1 attempts
`AwsPricingCacheService` with service code `AmazonSageMaker` (product family
"ML Instance", component by check: `Hosting`, `Notebook`, `Training`). When a
price resolves, `potential_savings_monthly` is computed; when it does not,
`potential_savings_monthly` is `None` and `savings_disclosure` says the price
was unavailable. **No EC2-price-times-uplift estimates.** The implementation
must report which components resolved so the gap is visible.

---

# 5. Checks

Common to all: `region` parameter; resources outside selected regions skipped;
`check_reason` via `create_check_reason`; metadata keys are additive and
stable. Each check logs every skip with resource id and reason.

## 5.1 `sagemaker_endpoint_idle` — native

* Candidates: endpoints `InService`, non-serverless variants.
* Signal: `AWS/SageMaker Invocations` Sum per variant over `lookback_days` (14).
* Flag when Sum == 0 for **every** variant **and** `invocations_metric_status ==
  "usable"` with `sample_count ≥ minimum_samples` (the metric only emits on
  traffic, so an empty result set is ambiguous: require that the endpoint has
  existed for the full window — `creation_time ≤ now − lookback_days` — before
  treating zero as idle; otherwise skip with reason `endpoint_younger_than_window`).
* Metadata: `invocations_total`, `lookback_days`, `variants[]` with per-variant
  totals, `instance_type`, `instance_count`, `endpoint_age_days`.
* `recommended_action: "delete_endpoint"`; `recommended_actions:
  ["delete_endpoint"]`.
* Savings: full endpoint cost (all variants × count × hourly × 730) per §4.

## 5.2 `sagemaker_endpoint_overprovisioned` — native

* Candidates: endpoints `InService`, a variant with `current_instance_count > 1`,
  **not** `sagemaker_endpoint_idle` (mutually exclusive: idle owns zero-traffic).
* Signals over `lookback_days` (14): `InvocationsPerInstance` Sum; `CPUUtilization`
  (÷ vCPU count from the resolved EC2 type — SageMaker CPU is summed per core,
  §2.3 applies to CPU too); `GPUUtilization` ÷ device count when GPU.
* Flag when per-instance p95 CPU < `cpu_threshold` (20.0) **and** (no GPU or
  per-device mean GPU p95 < `gpu_threshold` 10.0) **and** `autoscaling_policy_present
  == False`. With a scaling policy present, skip with reason
  `autoscaling_manages_count` — the policy owns instance count.
* Recommend `target_instance_count = max(1, ceil(current × p95_utilization /
  target_utilization))` with `target_utilization` 0.6; disclose the formula.
* `recommended_action: "reduce_endpoint_instance_count"`.
* Savings: (current − target) × hourly × 730 per §4.

## 5.3 `sagemaker_endpoint_gpu_underutilized` — native

* Candidates: endpoints `InService` whose variant type resolves to GPU (§2.2),
  with invocations present (Sum > 0 — otherwise `idle` owns it).
* Signal: `GPUUtilization` ÷ device count (or `GPUUtilizationNormalized`, or
  busiest `AcceleratorId` series when enhanced metrics are on), plus
  `GPUMemoryUtilization` ÷ count.
* Skip with the §2.5 reason when the GPU signal is unavailable or
  `gpu_count_unknown`. **A GPU endpoint without GPU telemetry is never flagged.**
* Flag when per-device GPU p95 < `gpu_threshold` (5.0) **and** per-device GPU
  memory p95 < `gpu_memory_threshold` (10.0). The memory gate prevents flagging
  a model that is resident but sparsely invoked as "not using the GPU".
* Metadata: `gpu_device_count`, `gpu_p95_per_device`, `gpu_memory_p95_per_device`,
  `gpu_aggregation` (`"mean_of_sum" | "busiest_accelerator" | "normalized"`),
  `GPU_AGGREGATION_MEAN_ONLY` disclosure when applicable.
* `recommended_action: "rightsize"`, no target type (the rightsizer owns
  targets); reason text states GPUs are idle while the endpoint serves traffic.
* Savings: full instance cost with the same disclosure the EC2
  `gpu_underutilized` check uses.

## 5.4 `sagemaker_notebook_no_auto_stop` — config

* Candidates: notebooks `InService`.
* Signal: `DescribeNotebookInstance.NotebookInstanceLifecycleConfigName`, then
  `DescribeNotebookInstanceLifecycleConfig.OnStart[].Content` (base64). A config
  **has auto-stop** when the decoded content matches any of the configurable
  patterns (default: `autostop`, `auto-stop`, `idle`, `stop-notebook-instance`)
  — the AWS sample scripts all match. Record `lifecycle_config_has_auto_stop`
  and the matched pattern.
* Flag when no lifecycle config is attached, or the attached config has no
  auto-stop match. Never flag on a Describe failure — skip with reason.
* `recommended_action: "attach_auto_stop_lifecycle_config"`; parameter
  `lifecycle_config_name` for the action. V1 action is **advisory** (finding
  only) unless the operator supplies an existing config name.
* Savings: `None` (behavioural fix), `savings_disclosure` explains.

## 5.5 `sagemaker_notebook_idle` — agent (+ GPU variant)

* Candidates: notebooks `InService`.
* Signals: discovered per §3.3 — CPU (`cpu_usage_active` or `cpu_usage_user`
  from the agent; matcher accepts both), memory (`mem_used_percent`), GPU
  (`nvidia_smi_utilization_gpu` per device) when the type is GPU.
* Skip when CPU is unavailable (reason logged). For a GPU type, additionally
  require usable GPU telemetry — **a GPU notebook without GPU telemetry is
  never idle**; skip with the reason.
* Flag when window-wide CPU p95 < `cpu_threshold` (5.0), CPU max < `cpu_max_threshold`
  (15.0), and — GPU types — busiest-device GPU p95 < `gpu_threshold` (5.0), max <
  `gpu_max_threshold` (15.0). Same two-threshold shape as EC2.
* Metadata: EC2 idle-check fields plus `gpu_*` evidence from `gpu_utils`
  (reuse, do not copy), `lifecycle_config_has_auto_stop` (so the UI can say
  "and it has no auto-stop").
* `recommended_action: "stop_notebook"`; `recommended_actions:
  ["stop_notebook", "attach_auto_stop_lifecycle_config"]`.
* Savings: instance hourly × 730 per §4 (volume cost continues; disclose).

## 5.6 `sagemaker_training_no_managed_spot` — config

* Candidates: training jobs in the last `job_history_days` (30) with
  `TrainingJobStatus == Completed`, grouped by **job family** — the job name with
  its trailing timestamp/run suffix removed (`[-_]\d{4,}.*$` and
  `[-_](\d{4}-\d{2}-\d{2}.*)$`, configurable). A family with fewer than
  `minimum_runs` (3) completed runs is not recurring and is skipped.
* Flag a family when every run has `EnableManagedSpotTraining == False`.
  Report `runs_observed`, `median_training_time_seconds`, `instance_type`,
  `instance_count`, `checkpoint_config_present` (spot without checkpoints risks
  losing progress — the finding says so).
* `recommended_action: "enable_managed_spot_training"` — advisory only in V1;
  the change applies to the *next* run's request, not an existing resource.
* Savings: `median_training_time × instance_count × hourly × runs_per_month ×
  spot_discount` with `spot_discount` a configurable estimate (default 0.6) and
  a disclosure that spot savings vary. Only when §4 resolves a price.

---

# 6. Actions

V1 implements two handlers in `app/actions/handlers_sagemaker.py`, registered in
`action_registry` by `action_key`:

* `stop_notebook` — `StopNotebookInstance`; idempotent on already-stopped.
* `delete_endpoint` — `DeleteEndpoint`; **does not** delete the endpoint config
  or model (they may be shared); the response says so.

`reduce_endpoint_instance_count` (`UpdateEndpointWeightsAndCapacities`),
`attach_auto_stop_lifecycle_config`, and `enable_managed_spot_training` are
advisory in V1 and listed in the V2 table with their API calls.

Action captures follow the `tests_generator` action contract (§8).

---

# 7. Registry, catalog, UI

* Register each check with `resource_type="sagemaker"` and the parameters and
  defaults above.
* Add rows to `docs/maxops_checks_catalog.csv` and actions to
  `docs/maxops_actions_catalog.csv`.
* Add `sagemaker` to the resource-type lists the frontend reads (inventory,
  checks page); a subtype badge (`notebook | endpoint | training_job`) is
  enough for V1. No new pages.

---

# 8. Testing

Three layers, each with a distinct job. All are offline; none touch AWS unless
run deliberately.

## 8.1 Unit tests — logic, free, immediate

`tests/test_sagemaker_checks.py`, stub-adapter style (as `test_ec2_gpu_telemetry.py`).
Required cases, at minimum one per rule above:

* `ml.` resolution: `ml.g5.xlarge` → catalog GPU info; `ml.p3.2xlarge` → seeded
  table; `ml.m5.large` → not GPU; `ml.zz9.large` → unknown.
* summed-GPU normalization: 8-device endpoint at `GPUUtilization = 240` →
  per-device 30; `device_count` unknown → status `unavailable / gpu_count_unknown`
  and **not flagged**.
* `endpoint_idle`: zero invocations + old enough → flagged; zero invocations +
  younger than window → skipped; serverless variant → not a candidate.
* `overprovisioned`: scaling policy present → skipped with reason; target count
  formula with a worked example; mutual exclusion with `idle`.
* `gpu_underutilized`: GPU idle + memory resident → **not** flagged (memory
  gate); GPU telemetry unavailable → not flagged; enhanced metrics present →
  busiest-device aggregation chosen.
* `no_auto_stop`: no config → flagged; config with `autostop` in OnStart →
  passed; Describe raises → skipped, not flagged.
* `notebook_idle`: CPU idle, no agent GPU data on a GPU type → **not** flagged
  (name the test for what it prevents); CPU idle on a CPU type → flagged.
* `no_managed_spot`: family grouping on `train-2026-09-01-1200` style names;
  two runs → skipped; three runs all on-demand → flagged; one spot run → passed.

## 8.2 Payload-backed tests — real AWS shapes, captured once

`tests_generator/sagemaker/` per `tests_generator/PAYLOAD_GENERATION.md`:
`resource_config.json`, `checks.json`, `actions.json`,
`sagemaker_resource_creation.py`, `sagemaker_payloads_generator.py`,
`sagemaker_resource_cleanup.py`, and `sagemaker` entries in
`payload_check_map.json`. Add `StaticSageMakerClient` (and an
application-autoscaling stub) to `tests/payload_helpers.py` and a
`build_sagemaker_payload_adapter(check_id, scenario)`.

Capture resources — few, cheap, reused across scenarios:

| Alias | Resource | Cost note |
| --- | --- | --- |
| `notebook_cpu` | `ml.t3.medium` notebook, no lifecycle config | ~$0.05/h |
| `notebook_cpu_autostop` | same, with an auto-stop lifecycle config attached | ~$0.05/h |
| `endpoint_cpu` | `ml.m5.large`, 1 instance, tiny built-in model (e.g. XGBoost) | ~$0.12/h |
| `endpoint_cpu_x2` | same config, 2 instances, for `overprovisioned` | ~$0.23/h |
| `endpoint_gpu` | `ml.g4dn.xlarge`, 1 instance, **created last, deleted first**, kept only long enough for one `GetMetricData` capture | ~$0.74/h |
| `training_cpu` | 3 runs of a tiny built-in-algorithm job, same family name with a timestamp suffix, on-demand; 1 further run with managed spot | minutes each |

Scenarios per check (`pass_*` = flagged as expected, `fail_*` = correctly not
flagged, `edge_*` = skip/unknown paths), with `metric_overrides` allowed only
where PAYLOAD_GENERATION permits (narrow value overrides on a real
`GetMetricData` response — e.g. forcing `Invocations` to zero, or
`GPUUtilization` to a chosen summed value). Structural responses (Describe*,
List*, lifecycle config content) are captured as-is.

**Cost gate.** Capture provisions real SageMaker resources. The generator lives
behind the same two gates as `tests/integration/` — `-m integration`-style
marker semantics do not apply to scripts, so: the scripts refuse to run unless
`MAXOPS_RUN_AWS_INTEGRATION_TESTS=1`, print the resource plan and estimated
hourly cost, and require `--apply`. Cleanup runs from `finally`, and the GPU
endpoint is deleted first. **Codex must deliver the generator but must not
execute it.** Capture is run by an operator, deliberately, once.

Until fixtures exist, the payload-backed tests **skip** with the message
`"sagemaker payload fixtures not captured; run tests_generator/sagemaker"` — they
must not fail and must not be marked xfail. When fixtures land they activate
without code change.

## 8.3 What is deliberately not simulated

There is no rightsizer for SageMaker in V1, so there is no scenario harness of
the `test_ec2_rightsizer_scenarios.py` kind. The checks' decision logic is
fully covered by §8.1; the AWS shapes by §8.2. If a SageMaker rightsizer is
added later, it gets its own scenario harness per the EC2 test plan §2.

---

# 9. Module layout

```text
app/checks/sagemaker/
  __init__.py
  sagemaker_checks_spec.md          (this file)
  common.py                         resolution, normalization, telemetry status helpers
  endpoint_idle.py
  endpoint_overprovisioned.py
  endpoint_gpu_underutilized.py
  notebook_no_auto_stop.py
  notebook_idle.py
  training_no_managed_spot.py
app/utils/sagemaker_instance_types.py  seeded ml.* GPU table
app/actions/handlers_sagemaker.py
app/models/inventory.py               SageMakerInventory
tests/test_sagemaker_checks.py
tests/test_sagemaker_payload_checks.py (skips until captured)
tests_generator/sagemaker/
```

Reuse, do not copy: `derive_*_period`, the discovery sweep (generalized on the
identity dimension), `gpu_utils`, `ec2_gpu_info`, `get_metric_history_statistic`,
`_valid_percent_value` / `_valid_non_negative_value`, the pagination helper.

---

# 10. TODO — open items after V1 implementation

**Status: open. Owner: operator / next contributor.**

1. **Capture real payload fixtures** (§8.2). Run `tests_generator/sagemaker/`
   deliberately with `MAXOPS_RUN_AWS_INTEGRATION_TESTS=1` and `--apply`
   (estimated < $5 with cleanup; the GPU endpoint is created last and deleted
   first). This activates `tests/test_sagemaker_payload_checks.py` and the
   action replay tests with no code change. Until then, adapter correctness
   against real AWS responses rests on the botocore shape-validated tests in
   `tests/test_sagemaker_adapter_parsing.py`, which check field names and
   types but not real values.
2. **Pricing.** `AwsPricingCacheService` resolved no `AmazonSageMaker` price for
   Hosting, Notebook, or Training in the offline environment, so findings ship
   `potential_savings_monthly = None` with a disclosure. Confirm the product
   family / attribute filters against the Price List API and add a test with a
   captured price-list page.
3. **Advisory actions → handlers** (§6): `reduce_endpoint_instance_count`
   (`UpdateEndpointWeightsAndCapacities`), `attach_auto_stop_lifecycle_config`,
   `enable_managed_spot_training`. Each needs an action-capture scenario in
   `actions.json` before it ships.
4. **Legacy generator gate retrofit** — tracked in `tests_generator/PAYLOAD_GENERATION.md`
   once landed; all 16 pre-existing generators must call the shared
   `require_capture_gate`.
