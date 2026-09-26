# EC2 Rightsizer §26 Fix Plan

Status: completed/historical. The shipped contracts now live in spec §11
(decimal-safe savings floor) and §25.7 (Balanced-reserving candidate limiting).

Implementation plan for the two known deviations documented in
`ec2_rightsizer_spec.md` §26. Scope is exactly these two fixes plus their
regression tests and the documentation cleanup — no other behavior changes.

Both fixes live in `app/services/ec2_rightsizer.py` inside `_recommend()`.
Read spec §26.1/§26.2 first; they are the authoritative contracts. Test
expectations are pinned in `ec2_rightsizer_test_plan.md` rows `REC-013`,
`REC-015`, `REC-016`, and generated invariants 1 and 9.

## Fix 1 — Balanced-reserving candidate limiting (spec §26.1)

### Current behavior

`_recommend()` sorts candidates by `(-monthly_savings, target_instance_type)`,
truncates with `candidates = candidates[:limit]`, assigns ranks, and only then
calls `_recommendation_tiers(candidates, self.compute_policy)`. When more than
`limit` higher-savings candidates qualify only for the Aggressive gate, the
sole Balanced qualifier is truncated away and the Balanced tier returns null,
violating §25.7's guarantee that Balanced equals the legacy pre-tiering
selection.

### Required behavior

1. After sorting the full candidate list, compute the **reserved Balanced
   target**: the same selection `_recommendation_tiers` would make for the
   `balanced` ratio, but over the *full* pre-truncation list — i.e. among
   candidates with `projected_util is not None and projected_util <=
   balanced_ratio`, pick min by `(-monthly_savings, target_instance_type)`.
   Get `balanced_ratio` from `self.compute_policy.tier_ratios` (the
   `"balanced"` entry); do not hardcode 0.70.
2. Build the returned list: if a reserved target exists and is not already in
   the top `limit` by savings order, take the reserved target plus the top
   `limit - 1` other candidates; otherwise plain truncation. Re-sort the
   final list by `(-monthly_savings, target_instance_type)` so display order
   stays savings-ranked, then assign ranks 1..n.
3. Call `_recommendation_tiers` on the final (limited) list only. Do not pass
   the full list to tiering — tiers must reference returned candidates
   (invariant 8).

### Notes and edge cases

- `len(candidates) <= limit`: no reservation logic needed, behavior unchanged.
- No Balanced qualifier exists (`projected_util` null everywhere or all above
  the ratio): plain truncation, Balanced tier null — unchanged.
- Because tier gates are nested, the reserved Balanced target also satisfies
  the Aggressive gate; with `limit=1` both Balanced and Aggressive resolve to
  the reserved target and only Conservative may be null (see `REC-013`).
- The returned list never exceeds `limit`; it may be shorter.
- Rejection tallies (`rejection_summary`) are unaffected — reservation only
  reorders which *eligible* candidates are retained.
- `_recommendation_tiers` itself does not change.

## Fix 2 — Decimal-safe one-cent savings floor (spec §26.2)

### Current behavior

```python
savings = current.monthly_usd - target.monthly_usd
if savings < min_savings or savings <= 0:
    continue
```

Any positive float difference passes, so a raw saving below $0.005 is
returned and displayed as `0.00` monthly savings.

### Required behavior

Require raw monthly savings of at least one cent, computed with decimal-safe
arithmetic. Float subtraction must not be used for the threshold comparison:
`10.00 - 9.99 == 0.009999999999999787` and
`0.03 - 0.02 == 0.009999999999999998`, so a naive `savings >= 0.01` on floats
rejects mathematically exact cents.

Implementation sketch — Decimal is used **only for the eligibility
comparisons**; the legacy float difference is retained for all response
calculations:

```python
from decimal import Decimal

savings = current.monthly_usd - target.monthly_usd
savings_exact = (
    Decimal(str(current.monthly_usd))
    - Decimal(str(target.monthly_usd))
)
if (
    savings_exact < Decimal("0.01")
    or savings_exact < Decimal(str(min_savings))
):
    continue
```

- Do NOT replace `savings` with `float(savings_exact)` for display: the two
  values can round differently at half-cent boundaries and that would break
  the byte-for-byte guarantee for retained candidates. Example:
  legacy `0.017 - 0.002 == 0.015000000000000001` → `round(..., 2) == 0.02`,
  while `float(Decimal("0.015")) == 0.015` → `round(..., 2) == 0.01`.
  The legacy float `savings` continues to feed `monthly_savings`,
  `yearly_savings`, and sort keys unchanged.
- Both comparisons (the one-cent floor and `min_monthly_savings`) use Decimal
  so the two thresholds behave consistently (`REC-004` requires equality with
  `min_monthly_savings` to be included).
- `Decimal(str(x))` — not `Decimal(x)` — so the value reflects the shortest
  decimal representation of the stored float, not its binary expansion.
- A genuine $0.009 saving is excluded even though it would display as `0.01`
  after rounding; the contract is on the raw value (spec §26.2).
- Downstream `round(savings, 2)` display logic is unchanged.

## Tests

Add to `tests/test_ec2_rightsizer_v1.py` (or the consolidated scenario file if
it exists by then), using the plan's harness conventions (§2 of
`ec2_rightsizer_test_plan.md`):

- `REC-015`: more high-savings Aggressive-only candidates than
  `candidate_limit`, plus one lower-savings sole Balanced qualifier → the
  Balanced qualifier is retained and selected; list length never exceeds the
  limit.
- `REC-013`: `candidate_limit=1` with three distinct tier qualifiers → list is
  the Balanced target alone; Balanced and Aggressive both reference it;
  Conservative null unless its ratio is met.
- `REC-016`: raw savings just below $0.01 (e.g. $0.009 via sub-cent prices) is
  excluded; exactly $0.01 **using the float-hazard pair current $10.00 /
  target $9.99** is included; just above is included. Write all three as
  plain assertions (no xfail — the fix lands with this change).
- No-limit-pressure regression: a fixed scenario with several candidates,
  `candidate_limit` comfortably above the candidate count, and savings well
  clear of both thresholds. Assert the exact ordering, ranks, tier targets,
  and `monthly_savings`/`yearly_savings` values against hand-computed
  expectations (via the decision projection — not a full-response snapshot).
  Include one candidate priced so the legacy float difference carries an
  *upward* half-cent residue: use exactly current $0.017 / target $0.002
  (`0.017 - 0.002 == 0.015000000000000001` → displays `0.02`, whereas the
  Decimal-derived value would display `0.01`), so the test fails if display
  savings are ever switched to the Decimal-derived value. Caution: the
  residue direction depends on the exact operands — $10.017 − $10.002 rounds
  *down* and would not catch the regression — so if the prices are changed,
  verify with `repr(current - target)` that the residue still rounds upward. This is the executable form of the acceptance
  guarantee below.
- Re-run the existing suite to confirm no regressions: the reservation logic
  must not change any result where limit pressure is absent, and the savings
  floor must not exclude any existing fixture with savings ≥ $0.01.

Run **only** the named test file:

```bash
.venv/bin/python -m pytest tests/test_ec2_rightsizer_v1.py -q
```

Never run broad discovery (`pytest tests/`, `pytest -k ...` variants) —
`tests/integration/` provisions real billable AWS resources.

## Documentation cleanup (same change, per test-plan §5.3)

After both fixes pass:

1. Delete §26 from `ec2_rightsizer_spec.md` and fold the repair semantics into
   the sections they amend: the reservation rule into §25.7, the one-cent
   decimal-safe floor into §11's savings language.
2. In `ec2_rightsizer_test_plan.md`: remove the KNOWN ISSUE sentences and
   xfail instructions from `REC-015` and `REC-016`; remove the TEMPORARY
   GENERATOR CONSTRAINT paragraph from generated invariant 9; update the
   `§26.x` references in the Spec columns of `REC-004`, `REC-013`, `REC-015`,
   `REC-016` to point at the amended sections.
3. Do not change any other scenario row, threshold, or invariant.
4. Mark this fix plan itself as completed/historical: add a status line at the
   top pointing to the amended spec sections (§11 savings floor, §25.7
   reservation semantics), since the §26 references in this document become
   dangling once §26 is deleted. Alternatively delete this file in the same
   change.

## Acceptance

- `REC-013`, `REC-015`, `REC-016` pass without xfail marks.
- All pre-existing tests in `tests/test_ec2_rightsizer_v1.py` still pass.
- Output for any input without limit pressure is byte-for-byte identical to
  before the change (ordering, ranks, tiers, savings values), with two
  intended eligibility exceptions at the thresholds: candidates with raw
  savings below one cent are now excluded, and a candidate exactly at
  `min_monthly_savings` that the old float comparison wrongly excluded may
  now be included (Decimal equality is the specified behavior). For every
  candidate returned both before and after, all savings values are
  byte-for-byte unchanged.
- No references to spec §26 remain in the spec or test plan.
