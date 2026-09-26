# EC2 Rightsizer §27 Savings Previews — Implementation Plan

Status: completed/historical. The shipped contract lives in spec §27 and the
offline scenarios live in test-plan Section 3.10.

Implements spec §27 (`ec2_rightsizer_spec.md`): the `savings_previews`
response section with the `MEMORY_METRIC_MISSING_DOWNSIZE` and
`GRAVITON_MIGRATION` preview kinds. Test expectations are pinned in
`ec2_rightsizer_test_plan.md` Section 3.10 (`PRV-001`–`PRV-008`) and
generated invariant 15.

Sequencing: implement **after** the §26 fixes
(`ec2_rightsizer_fix_plan.md`) land — the preview savings floor reuses the
decimal-safe one-cent comparison introduced there. This is a separate PR from
§26.

The governing constraint, restated: previews are display-only and must leave
`recommendations`, tiers, ranks, `candidate_limit` handling, and
`rejection_summary` byte-for-byte unchanged for every input. If any existing
test changes its expected output, the implementation is wrong.

## 1. Model and policy changes

- `rightsizers/ec2/ec2_rightsizer/models.py`: add `PREVIEW = "PREVIEW"` to
  `RecommendationClassification` (line ~31). `OPPORTUNITY` already exists.
- `app/services/ec2_rightsizer.py`: add two flags to `EC2CandidatePolicy`
  (line ~79), both defaulting to `True`:
  `memory_preview_enabled: bool`, `graviton_preview_enabled: bool`.

## 2. Response envelope

Add `"savings_previews": [...]` to the success envelope built at
`app/services/ec2_rightsizer.py` (~line 777). The DEFERRED early-return
envelope (~line 678) gets `"savings_previews": []` — deferral means no
evaluation ran, so no previews either.

Preview object shape (spec §27.1):

```python
{
    "kind": "MEMORY_METRIC_MISSING_DOWNSIZE" | "GRAVITON_MIGRATION",
    "target_instance_type": str,
    "monthly_savings": float,   # same legacy-float + round(., 2) as recommendations
    "yearly_savings": float,
    "classification": "PREVIEW" | "OPPORTUNITY",
    "blockers": [str, ...],     # non-empty
    "message": str,             # §27.2/§27.3 disclosure text
    "evidence": {...},          # same envelope style as recommendations
}
```

At most one entry per kind (highest savings; tie-break by
`target_instance_type` like everything else). List order: memory preview
first, then Graviton.

## 3. Memory preview (spec §27.2)

Anchor: the candidate loop in `_recommend()` rejects memory-reducing targets
at ~line 745 (`MEMORY_REQUIREMENT_NOT_MET`) and `continue`s before
`_evaluate_candidate` runs.

- Only when `not memory_metric_available` and the policy flag is on: when a
  target fails **only** `memory_passes` (CPU passed, and it already survived
  architecture, family gate, and both savings thresholds), stash it in a
  `memory_preview_pool` list instead of merely tallying. Keep the
  `rejected["MEMORY_REQUIREMENT_NOT_MET"]` tally exactly as today
  (`PRV-001` asserts it).
- After the main loop: for pool candidates in descending-savings order, run
  `_evaluate_candidate(..., cpu_passes=cpu_passes, memory_passes=True, ...)`
  — with the memory metric absent, `projected_memory` is `None`, so
  `memory_passes=True` is consistent and lets the network/EBS evaluation run.
  Accept the first candidate whose result is not `REJECTED`
  (i.e. network and EBS hard constraints pass, `PRV-002`); stop there —
  only one preview is emitted.
- Build the preview from that evaluation: `classification="PREVIEW"`
  (override whatever `_evaluate_candidate` classified — previews are never
  ACTIONABLE/CONDITIONAL), `blockers=["MEMORY_METRIC_NOT_ENABLED"]`, the
  §27.2 disclosure text, savings via the legacy float path.
- When `memory_metric_available` is true, the pool logic is skipped entirely
  (`PRV-003`).

## 4. Graviton preview (spec §27.3)

Anchor: architecture-incompatible targets are tallied and skipped at ~line
713, before any other gate.

- Only when the current instance has no `arm64` support, the policy flag is
  on, and the target supports `arm64`: instead of merely tallying
  `ARCHITECTURE_INCOMPATIBLE`, also consider the target for
  `graviton_preview_pool` when ALL of:
  - the §1 family gate passes (same rule as the main loop — gated classes
    excluded unless shared, `PRV-006`);
  - capacity retention: target vCPUs >= current vCPUs AND target memory >=
    current memory (`PRV-005`) — use the same capacity helpers the missing-
    metric floors use (`_retains_current_cpu_capacity` uses CoreMark when
    available; for cross-architecture do NOT use CoreMark, compare raw vCPU
    counts and memory only, per spec §27.3);
  - both savings thresholds pass (one-cent decimal floor and
    `min_monthly_savings`).
  The `rejected["ARCHITECTURE_INCOMPATIBLE"]` tally stays exactly as today
  (`PRV-004`).
- After the main loop: evaluate pool candidates in descending-savings order
  with `_evaluate_candidate(..., cpu_passes=True, memory_passes=True, ...)`;
  accept the first non-REJECTED result (network/EBS hard constraints pass).
- In the preview evidence, null out the CoreMark-derived fields
  (`performance_change_pct`, CPU projection) — cross-architecture CoreMark
  comparison is not reliable evidence (spec §27.3); vCPU/memory retention is
  the stated basis.
- `classification="OPPORTUNITY"`,
  `blockers=["ARCHITECTURE_MIGRATION_REQUIRED"]`, §27.3 disclosure text.
- Family-analogue preference (`m -> m*g` etc.) from the spec is satisfied
  naturally when analogues exist because they are typically the
  highest-savings capacity-retaining option; do not implement special-case
  matching logic — highest savings with the constraints above is the rule.

## 5. What must NOT change

- `_recommendation_tiers`, `classify_recommendation`, ranking, truncation
  (§26.1 semantics), and all rejection tallies.
- No preview candidate enters the `candidates` list; `candidate_limit` never
  applies to previews.
- Existing tests must pass unmodified. Invariant 15's flag test is the
  strongest check: run the same input with both flags off and both on —
  everything except `savings_previews` must be identical.

## 6. Tests

Implement test plan Section 3.10 rows `PRV-001` through `PRV-008` in the
same change, plus generated invariant 15, in
`tests/test_ec2_rightsizer_v1.py` (or the consolidated scenario file). Use
the harness conventions of test-plan §2; extend the decision projection with
a `previews` key (`kind -> target_instance_type`) for these scenarios only.

Run only the named test file:

```bash
.venv/bin/python -m pytest tests/test_ec2_rightsizer_v1.py -q
```

Never run broad pytest discovery — `tests/integration/` provisions real,
billable AWS resources.

## 7. Documentation updates in the same change

1. Spec §27: change the status line from "specified, not yet implemented" to
   implemented (reference the change).
2. Test plan Section 3.10: remove the "feature not yet implemented" preamble
   sentence; the rows become ordinary required scenarios.
3. Generated invariant 15: remove the "(With spec §27, once implemented.)"
   qualifier.
4. Mark this plan completed/historical or delete it, as with
   `ec2_rightsizer_fix_plan.md`.

## 8. Acceptance

- All `PRV-` rows and invariant 15 pass.
- All pre-existing tests pass unmodified.
- With both preview flags disabled, output is byte-for-byte identical to the
  pre-change service for every input.
- With flags enabled, only `savings_previews` differs from the pre-change
  output.
