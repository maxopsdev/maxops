import type { CheckState } from '@/services/checks';

export interface DedupedSavings {
  /** Total with each resource counted once. */
  total: number;
  /** Sum of the per-check totals, which counts shared resources repeatedly. */
  rawTotal: number;
  /** How many resources were flagged by more than one check. */
  sharedResourceCount: number;
}

/**
 * Total yearly savings across checks, counting each resource once.
 *
 * Several checks can flag the same resource, and each reports the saving
 * available from fixing that resource -- not an additional saving on top of
 * the others. Summing per-check totals therefore multiplies one opportunity
 * by however many checks noticed it. All nine ASG checks, for instance, share
 * a single flat heuristic, so an ASG caught by three of them reported the
 * identical figure three times.
 *
 * The largest estimate per resource wins. Checks with no per-resource
 * breakdown -- imported summaries, which carry only a total -- cannot take
 * part and are added as they are.
 */
export const summariseYearlySavings = (
  states: Array<CheckState | undefined>,
): DedupedSavings => {
  const bestByResource = new Map<string, number>();
  const checksPerResource = new Map<string, number>();
  let unattributed = 0;
  let rawTotal = 0;

  states.forEach((state) => {
    if (!state) return;
    rawTotal += state.potential_savings_yearly || 0;

    const byResource = state.savings_by_resource;
    if (byResource && Object.keys(byResource).length > 0) {
      Object.entries(byResource).forEach(([resourceId, yearly]) => {
        const current = bestByResource.get(resourceId) ?? 0;
        if (yearly > current) bestByResource.set(resourceId, yearly);
        checksPerResource.set(resourceId, (checksPerResource.get(resourceId) ?? 0) + 1);
      });
      return;
    }

    unattributed += state.potential_savings_yearly || 0;
  });

  let total = unattributed;
  bestByResource.forEach((yearly) => {
    total += yearly;
  });

  let sharedResourceCount = 0;
  checksPerResource.forEach((count) => {
    if (count > 1) sharedResourceCount += 1;
  });

  return { total, rawTotal, sharedResourceCount };
};

/** Convenience wrapper for callers that only need the figure. */
export const dedupedYearlySavings = (states: Array<CheckState | undefined>): number =>
  summariseYearlySavings(states).total;

export type AsgSavingsSource = 'rightsizer' | 'heuristic' | 'none';

export interface AsgSavings {
  value: number;
  source: AsgSavingsSource;
  /** The figure the other model produced, when both exist and disagree. */
  alternative: number | null;
}

/**
 * Reconcile the two ASG savings models so a page can explain which it shows.
 *
 * The rightsizer prices a specific target configuration against the current
 * one. The checks apply a flat share of the ASG's cost. Both are "potential
 * yearly savings" and they rarely agree, so showing one without naming it
 * leaves the dashboard and this page silently contradicting each other.
 */
export const resolveAsgSavings = (
  rightsizerYearly: number | null | undefined,
  checkYearly: number | null | undefined,
): AsgSavings => {
  const refined = rightsizerYearly ?? null;
  const heuristic = checkYearly ?? null;

  if (refined !== null) {
    return {
      value: refined,
      source: 'rightsizer',
      alternative: heuristic !== null && heuristic !== refined ? heuristic : null,
    };
  }
  if (heuristic !== null) {
    return { value: heuristic, source: 'heuristic', alternative: null };
  }
  return { value: 0, source: 'none', alternative: null };
};
